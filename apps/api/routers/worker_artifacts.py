"""Worker 工件只绑定当前租约；网络接收期间不持有数据库事务。"""

import hashlib
from datetime import timedelta
from uuid import UUID

from fastapi import APIRouter, Request
from fastapi.responses import Response
from sqlalchemy import select

from packages.contracts.validation import validate_payload

from ..errors import ApiError
from ..models import UploadRecord, utcnow
from ..security import aware
from ..services.artifacts import artifact_bytes, atomic_bytes, store_artifact
from ..services.common import event, get_resource, iso, mutate
from ..services.service_identity import authorize_task
from .jobs import current_lease, deadline_elapsed, worker_auth

router = APIRouter(prefix="/api/v1/worker/jobs/{job_id}")
OUTPUTS = {
    "contract-validation.json": ("application/json", "tool.report"),
    "worker.log": ("text/plain", "tool.log"),
}
VERIFIER_OUTPUTS = {
    "verification-report.json": ("application/json", "verification.report"),
    "worker.log": ("text/plain", "tool.log"),
}


def outputs(task):
    return VERIFIER_OUTPUTS if task.capability == "acceptance.verify" else OUTPUTS


MAX_FILE_BYTES = 1024 * 1024
MAX_ATTEMPT_BYTES = 4 * 1024 * 1024
MAX_ATTEMPT_FILES = 10


def lease_headers(request: Request) -> dict:
    try:
        worker_id = str(UUID(request.headers["x-worker-id"]))
        attempt = int(request.headers["x-task-attempt"])
        fence = int(request.headers["x-fencing-token"])
        if min(attempt, fence) < 1 or max(attempt, fence) > 2147483647:
            raise ValueError()
    except (KeyError, ValueError, OverflowError) as exc:
        raise ApiError(
            400, "LEASE_HEADERS_REQUIRED", "需要有效 Worker、attempt 和 fencing 请求头"
        ) from exc
    return {"worker_id": worker_id, "attempt": attempt, "fencing_token": fence}


def artifact_lease(db, request: Request, job_id: str):
    task = current_lease(db, job_id, lease_headers(request))
    principal = worker_auth(db, request)
    authorize_task(principal, task)
    if task.capability == "acceptance.verify":
        from ..services.verification import expected_binding

        expected_binding(db, task)
    return task


def ticket_for(db, request: Request, task, upload_id: str):
    ticket = db.scalar(select(UploadRecord).where(UploadRecord.id == upload_id).with_for_update())
    if (
        not ticket
        or ticket.job_id != task.id
        or ticket.project_id != task.project_id
        or ticket.owner_id != task.worker_id
        or ticket.fencing_token != task.fencing_token
    ):
        raise ApiError(404, "NOT_FOUND", "任务上传票据不存在")
    if aware(ticket.expires_at) <= utcnow():
        raise ApiError(410, "UPLOAD_EXPIRED", "上传票据已过期")
    if task.state == "CANCEL_REQUESTED" and ticket.payload["filename"] != "worker.log":
        raise ApiError(409, "CANCELLATION_PENDING", "取消期间仅允许保留诊断日志")
    if deadline_elapsed(task) and ticket.payload["filename"] != "worker.log":
        raise ApiError(409, "TASK_DEADLINE_EXCEEDED", "截止时间后仅允许保留日志")
    return ticket


@router.get("/artifacts/{resource_id}/content")
def download_input(request: Request, job_id: str, resource_id: str):
    with request.app.state.db.transaction() as db:
        worker_auth(db, request)
        task = artifact_lease(db, request, job_id)
        if task.state == "CANCEL_REQUESTED":
            raise ApiError(409, "CANCELLATION_PENDING", "任务已请求取消")
        if deadline_elapsed(task):
            raise ApiError(409, "TASK_DEADLINE_EXCEEDED", "任务截止后禁止继续下载输入")
        expected = next(
            (ref for ref in task.envelope["input_artifacts"] if ref["id"] == resource_id), None
        )
        if expected is None:
            raise ApiError(404, "NOT_FOUND", "工件不在本任务冻结输入中")
        record = get_resource(db, "artifacts", resource_id, task.project_id)
        if record.payload != expected:
            raise ApiError(409, "INPUT_HASH_MISMATCH", "输入引用与冻结快照不一致")
        content = artifact_bytes(request.app.state.settings, record, max_bytes=4 * 1024 * 1024)
        return Response(
            content,
            media_type="application/octet-stream",
            headers={
                "ETag": '"' + expected["sha256"] + '"',
                "Content-Disposition": f'attachment; filename="{resource_id}.bin"',
            },
        )


@router.post("/uploads")
async def initialize_upload(request: Request, job_id: str):
    def operation(db, principal, body):
        task = artifact_lease(db, request, job_id)
        validate_payload("UploadInit", body)
        allowed = outputs(task).get(body["filename"])
        if not allowed or body["content_type"] != allowed[0]:
            raise ApiError(415, "OUTPUT_TYPE_DENIED", "该任务仅允许契约检查 JSON 与文本日志")
        if task.state == "CANCEL_REQUESTED" and body["filename"] != "worker.log":
            raise ApiError(409, "CANCELLATION_PENDING", "取消期间仅允许保留日志")
        if deadline_elapsed(task) and body["filename"] != "worker.log":
            raise ApiError(409, "TASK_DEADLINE_EXCEEDED", "截止时间后仅允许保留日志")
        existing = db.scalars(
            select(UploadRecord).where(
                UploadRecord.job_id == task.id,
                UploadRecord.fencing_token == task.fencing_token,
            )
        ).all()
        if (
            body["bytes"] > MAX_FILE_BYTES
            or len(existing) >= MAX_ATTEMPT_FILES
            or sum(row.payload["bytes"] for row in existing) + body["bytes"] > MAX_ATTEMPT_BYTES
        ):
            raise ApiError(413, "WORKER_OUTPUT_QUOTA", "任务输出超出当前 attempt 配额")
        ticket = UploadRecord(
            project_id=task.project_id,
            owner_id=task.worker_id,
            job_id=task.id,
            fencing_token=task.fencing_token,
            payload=body,
            expires_at=utcnow() + timedelta(minutes=10),
        )
        db.add(ticket)
        db.flush()
        return (
            {
                "upload_id": ticket.id,
                "put_path": f"/api/v1/worker/jobs/{job_id}/uploads/{ticket.id}/content",
                "expires_at": iso(ticket.expires_at),
            },
            201,
            {},
        )

    return await mutate(
        request,
        operation,
        authenticator=worker_auth,
        preauthorize=lambda db, *_: artifact_lease(db, request, job_id),
    )


@router.put("/uploads/{resource_id}/content")
async def upload_content(request: Request, job_id: str, resource_id: str):
    with request.app.state.db.transaction() as db:
        worker_auth(db, request)
        task = artifact_lease(db, request, job_id)
        ticket = ticket_for(db, request, task, resource_id)
        if ticket.artifact_id:
            raise ApiError(409, "UPLOAD_COMPLETED", "已完成的工件不可改写")
        maximum, digest = ticket.payload["bytes"], ticket.payload["sha256"]
    content = bytearray()
    async for chunk in request.stream():
        if len(content) + len(chunk) > maximum:
            raise ApiError(413, "FILE_TOO_LARGE", "上传超过票据大小")
        content.extend(chunk)
    if len(content) != maximum or hashlib.sha256(content).hexdigest() != digest:
        raise ApiError(422, "UPLOAD_MISMATCH", "上传大小或哈希不匹配")
    # 慢上传过程中可能失去租约；写入前再次锁定并核对，不能提升迟到输出。
    with request.app.state.db.transaction() as db:
        worker_auth(db, request)
        task = artifact_lease(db, request, job_id)
        ticket = ticket_for(db, request, task, resource_id)
        if ticket.artifact_id:
            raise ApiError(409, "UPLOAD_COMPLETED", "已完成的工件不可改写")
        atomic_bytes(
            request.app.state.settings.data_dir / "worker-uploads" / ticket.id, bytes(content)
        )
    return Response(status_code=204)


@router.post("/uploads/{resource_id}/complete")
async def complete_upload(request: Request, job_id: str, resource_id: str):
    def operation(db, principal, body):
        task = artifact_lease(db, request, job_id)
        ticket = ticket_for(db, request, task, resource_id)
        validate_payload("UploadComplete", body)
        if body["sha256"] != ticket.payload["sha256"]:
            raise ApiError(422, "UPLOAD_MISMATCH", "完成哈希与票据不一致")
        if ticket.artifact_id:
            return (
                get_resource(db, "artifacts", ticket.artifact_id, task.project_id).payload,
                200,
                {},
            )
        try:
            content = (
                request.app.state.settings.data_dir / "worker-uploads" / ticket.id
            ).read_bytes()
        except FileNotFoundError as exc:
            raise ApiError(409, "UPLOAD_INCOMPLETE", "请先上传完整内容") from exc
        if (
            len(content) != ticket.payload["bytes"]
            or hashlib.sha256(content).hexdigest() != body["sha256"]
        ):
            raise ApiError(422, "UPLOAD_MISMATCH", "工件大小或哈希不匹配")
        artifact = store_artifact(
            db,
            request.app.state.settings,
            task.project_id,
            content,
            outputs(task)[ticket.payload["filename"]][1],
            filename=ticket.payload["filename"],
            media_type=ticket.payload["content_type"],
            provenance="verifier_worker"
            if task.capability == "acceptance.verify"
            else "static_worker",
            task_id=task.id,
        )
        artifact.internal = {
            **artifact.internal,
            "worker_id": task.worker_id,
            "attempt": task.attempt,
            "fencing_token": task.fencing_token,
        }
        ticket.artifact_id = artifact.id
        event(db, principal.id, "worker.artifact_uploaded", artifact)
        return artifact.payload, 200, {}

    return await mutate(
        request,
        operation,
        authenticator=worker_auth,
        preauthorize=lambda db, *_: ticket_for(
            db, request, artifact_lease(db, request, job_id), resource_id
        ),
    )
