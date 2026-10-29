from fastapi import APIRouter, Request

from packages.contracts.validation import reference_json, validate_payload

from ..errors import ApiError
from ..models import ResourceRecord, uid
from ..security import project_role
from ..services.artifacts import store_json
from ..services.common import (
    check_version,
    event,
    get_resource,
    history,
    mutate,
    read,
    response,
    serialize,
)
from ..services.workflow import target_digest

router = APIRouter(prefix="/api/v1")


@router.get("/system/examples")
async def examples(request: Request):
    def operation(db, user):
        return {"schema_version": "1.0", "examples": [example(ch) for ch in (8, 16)]}

    return await read(request, operation)


def example(ch):
    return {
        "id": f"analyzer-{ch}ch",
        "name": f"{ch} 通道仿真采集仪",
        "channels": ch,
        "requirement": reference_json(
            "examples/requirement.json" if ch == 8 else "examples/requirement-16ch.json"
        ),
        "contract": reference_json(f"examples/system-contract-{ch}ch.json"),
        "acceptance": reference_json(
            "examples/acceptance.json" if ch == 8 else "examples/acceptance-16ch.json"
        ),
        "board": reference_json("examples/board-virtual.json"),
    }


@router.post("/projects/{project_id}/examples/{example_id}/import", status_code=201)
async def import_example(request: Request, project_id: str, example_id: str):
    def operation(db, user, body):
        project_role(db, user, project_id, ("developer", "reviewer", "admin"))
        if body or example_id not in ("analyzer-8ch", "analyzer-16ch"):
            raise ApiError(422, "INVALID_EXAMPLE", "选择一个已登记的 8 或 16 通道样例")
        data = example(8 if example_id == "analyzer-8ch" else 16)
        revision_id = uid()
        for kind in ("requirement", "contract", "acceptance"):
            data[kind]["project_id"], data[kind]["revision_id"] = project_id, revision_id
        settings = request.app.state.settings
        requirement = store_json(
            db,
            settings,
            project_id,
            data["requirement"],
            "requirement",
            provenance="specification_example_unapproved",
        )
        acceptance = store_json(
            db,
            settings,
            project_id,
            data["acceptance"],
            "acceptance",
            provenance="specification_example_unapproved",
        )
        data["contract"]["requirement_sha256"] = requirement.payload["sha256"]
        data["contract"]["acceptance_sha256"] = acceptance.payload["sha256"]
        contract = store_json(
            db,
            settings,
            project_id,
            data["contract"],
            "system_contract",
            provenance="specification_example_unapproved",
        )
        record = ResourceRecord(
            id=revision_id,
            kind="revisions",
            project_id=project_id,
            state="DRAFT",
            payload={
                "parent_id": None,
                "requirement_artifact_id": requirement.id,
                "acceptance_artifact_id": acceptance.id,
                "contract_artifact_id": contract.id,
            },
        )
        db.add(record)
        db.flush()
        event(db, user.id, "example.imported", record)
        return (
            {
                "revision": serialize(record),
                "artifacts": [r.payload for r in (requirement, acceptance, contract)],
                "target_sha256": target_digest(record),
            },
            201,
            {},
        )

    return await mutate(request, operation)


@router.get("/projects/{project_id}/revisions/{resource_id}/approval-target")
async def approval_target(request: Request, project_id: str, resource_id: str):
    def operation(db, user):
        project_role(db, user, project_id)
        record = get_resource(db, "revisions", resource_id, project_id)
        return {
            "action": "freeze",
            "target_sha256": target_digest(record),
            "revision_id": record.id,
        }

    return await read(request, operation)


@router.post("/projects/{project_id}/approvals/{resource_id}/decision")
async def decide(request: Request, project_id: str, resource_id: str):
    def operation(db, user, body):
        project_role(db, user, project_id, ("reviewer", "admin"))
        validate_payload("ApprovalDecision", body)
        record = get_resource(db, "approvals", resource_id, project_id, lock=True)
        check_version(body, record)
        if record.state != "PENDING":
            raise ApiError(409, "INVALID_STATE", "此批准已经处理")
        target = db.get(ResourceRecord, record.payload["target_id"])
        if not target or target_digest(target) != record.payload["target_sha256"]:
            raise ApiError(409, "APPROVAL_TARGET_CHANGED", "批准目标发生变化")
        history(db, record)
        record.state = "APPROVED" if body["decision"] == "approve" else "REJECTED"
        record.internal = {**record.internal, "actor_id": user.id, "reason": body["reason"]}
        db.flush()
        event(db, user.id, "approval.decided", record)
        return response(record)

    return await mutate(request, operation)
