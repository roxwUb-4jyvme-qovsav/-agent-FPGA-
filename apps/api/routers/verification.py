"""验收服务唯一写入口；人类与生成 Worker 只能按项目权限读取。"""

from copy import deepcopy

from fastapi import APIRouter, Request

from ..models import ResourceRecord, utcnow
from ..security import project_role
from ..services.common import event, get_resource, history, iso, mutate, read
from ..services.service_identity import verifier_auth
from ..services.verification import check_submission, project_validity, task_result

router = APIRouter(prefix="/api/v1")


@router.post("/verification/evidence")
async def ingest(request: Request):
    def operation(db, principal, body):
        task, existing, _keys = check_submission(db, request, principal, body)
        if existing:
            return existing.internal["ingest_result"], 200, {}
        record = ResourceRecord(
            id=body["id"],
            kind="evidence",
            project_id=task.project_id,
            state="IMMUTABLE",
            payload=deepcopy(body),
            internal={},
        )
        db.add(record)
        db.flush()
        projection = project_validity(db, request.app.state.settings, record, task.revision_id)
        task.result = task_result(body, projection)
        task.state = "SUCCEEDED" if projection["effective_status"] == "PASS" else "FAILED"
        task.attempts = [
            *task.attempts[:-1],
            {
                **task.attempts[-1],
                "state": task.state,
                "finished_at": iso(utcnow()),
                "result_artifact_id": body["report"]["id"],
            },
        ]
        task.lease_expires_at = None
        run = get_resource(db, "runs", task.run_id, task.project_id, lock=True)
        history(db, run)
        run.state = "BLOCKED"
        emitted = event(db, principal.id, "verification.evidence_ingested", record)
        result = {
            "resource_id": record.id,
            "state": record.state,
            "run_id": task.run_id,
            "approval_id": None,
            "event_cursor": emitted.id,
        }
        record.internal = {"ingest_result": result}
        return result, 200, {}

    return await mutate(
        request,
        operation,
        authenticator=verifier_auth,
        preauthorize=lambda db, principal, body: check_submission(db, request, principal, body),
    )


@router.get("/projects/{project_id}/evidence/{resource_id}/validity")
async def validity(request: Request, project_id: str, resource_id: str):
    def operation(db, user):
        project_role(db, user, project_id)
        record = get_resource(db, "evidence", resource_id, project_id)
        revision_id = request.query_params.get("revision_id", record.payload["revision_id"])
        return project_validity(db, request.app.state.settings, record, revision_id)

    return await read(request, operation)
