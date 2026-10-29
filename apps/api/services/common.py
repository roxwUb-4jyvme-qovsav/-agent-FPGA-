"""资源包装、乐观并发和幂等与业务事实位于同一数据库事务。"""

import base64
import hashlib
import json
from typing import Callable

from fastapi import Request
from fastapi.responses import JSONResponse, Response
from sqlalchemy import select
from starlette.concurrency import run_in_threadpool

from ..errors import ApiError
from ..models import EventRecord, IdempotencyRecord, ResourceHistory, ResourceRecord, utcnow
from ..request_context import request_id_context
from ..security import authenticate, project_role, require_admin


def iso(value) -> str:
    from ..security import aware

    return aware(value).isoformat().replace("+00:00", "Z")


def canonical(value) -> bytes:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()


def sha(value) -> str:
    return hashlib.sha256(canonical(value)).hexdigest()


def etag(record: ResourceRecord) -> str:
    return f'"{record.id}:{record.version}"'


def serialize(record: ResourceRecord) -> dict:
    return {
        "id": record.id,
        "project_id": record.project_id,
        "version": record.version,
        "etag": etag(record),
        "state": record.state,
        "payload": record.payload,
        "created_at": iso(record.created_at),
        "updated_at": iso(record.updated_at),
    }


def get_resource(db, kind: str, resource_id: str, project_id=None, *, lock=False):
    query = select(ResourceRecord).where(
        ResourceRecord.id == resource_id,
        ResourceRecord.kind == kind,
        ResourceRecord.project_id == project_id,
    )
    record = db.scalar(query.with_for_update() if lock else query)
    if record is None or record.state == "DELETED":
        raise ApiError(404, "NOT_FOUND", "资源不存在")
    return record


def check_match(request: Request, record: ResourceRecord) -> None:
    supplied = request.headers.get("if-match")
    if not supplied:
        raise ApiError(428, "PRECONDITION_REQUIRED", "修改前请携带 If-Match")
    if supplied != etag(record):
        raise ApiError(412, "VERSION_CONFLICT", "资源已更新，请重新读取")


def check_version(payload: dict, record: ResourceRecord) -> None:
    if payload.get("expected_version") != record.version:
        raise ApiError(412, "VERSION_CONFLICT", "命令版本已过期")


def history(db, record: ResourceRecord) -> None:
    db.add(
        ResourceHistory(
            resource_id=record.id,
            version=record.version,
            state=record.state,
            payload=record.payload,
        )
    )
    record.version += 1
    record.updated_at = utcnow()


def lock_event_projects(db, project_ids) -> None:
    if db.bind.dialect.name != "postgresql":
        return
    from sqlalchemy import text

    held = db.info.setdefault("event_project_locks", set())
    for project_id in sorted(set(project_ids) - held):
        # 多项目事务在首次事件之前一次按统一顺序取锁；锁持有直到提交。
        db.execute(
            text("SELECT pg_advisory_xact_lock(:key)"),
            {"key": -int(sha(["project-events", project_id])[:15], 16)},
        )
        held.add(project_id)


def event(
    db,
    actor_id: str,
    kind: str,
    record: ResourceRecord | None = None,
    project_id: str | None = None,
    payload=None,
) -> EventRecord:
    scope = project_id or (record.project_id if record else None)
    if scope:
        lock_event_projects(db, [scope])
    db.flush()
    data = dict(payload or {})
    request_id = data.get("request_id") or request_id_context.get()
    if scope and record is None and data.get("task_id"):
        from ..models import TaskRecord

        task = db.get(TaskRecord, data["task_id"])
        if task and task.project_id == scope:
            record = db.get(ResourceRecord, task.run_id)
    if scope and record and request_id:
        # 冻结发生时的版本和请求标识；投递端不读取正文或未来版本填补历史。
        data["_stream"] = {
            "schema_version": "1.0",
            "project_id": scope,
            "type": kind,
            "resource_id": record.id,
            "resource_version": record.version,
            "occurred_at": iso(utcnow()),
            "request_id": request_id,
            "summary": kind,
        }
    result = EventRecord(
        project_id=scope,
        actor_id=actor_id,
        type=kind,
        resource_id=record.id if record else None,
        payload=data,
    )
    db.add(result)
    db.flush()
    return result


def response(record: ResourceRecord, status=200) -> tuple[dict, int, dict]:
    return serialize(record), status, {"ETag": etag(record)}


def authorize_cached_response(db, request, user, body, cached_response=None) -> None:
    """幂等记录保存历史响应，不保存旧权限；撤权必须立即影响重复请求。"""
    path = request.url.path.removeprefix("/api/v1")
    if path == "/worker/jobs/claim" and cached_response and cached_response.get("job_id"):
        from ..models import TaskRecord
        from .service_identity import authorize_task

        task = db.get(TaskRecord, cached_response["job_id"])
        if task is None:
            raise ApiError(404, "NOT_FOUND", "缓存中的任务已不存在")
        authorize_task(user, task)
    if path.startswith("/admin/") or path.startswith("/system/users/"):
        require_admin(user)
    project_id = request.path_params.get("project_id")
    if not project_id:
        return
    roles = ("developer", "reviewer", "admin")
    if "/members" in path:
        roles = ("admin",)
    elif path.endswith("/decision") or body.get("action") in (
        "freeze",
        "release",
        "rollback",
        "publish",
    ):
        roles = ("reviewer", "admin")
    project_role(db, user, project_id, roles)
    resource_id = request.path_params.get("resource_id")
    if resource_id:
        record = db.get(ResourceRecord, resource_id)
        if record and record.project_id == project_id:
            from .visibility import resource_visible

            if not resource_visible(db, user, record):
                raise ApiError(404, "NOT_FOUND", "资源不存在")


async def read(request: Request, operation: Callable):
    def execute():
        with request.app.state.db.transaction() as db:
            user = authenticate(db, request)
            result = operation(db, user)
            if isinstance(result, tuple):
                body, status, headers = result
                return JSONResponse(body, status_code=status, headers=headers)
            return result

    return await run_in_threadpool(execute)


async def mutate(
    request: Request,
    operation: Callable,
    *,
    key_required=True,
    authenticator=authenticate,
    preauthorize=None,
):
    try:
        body = await request.json() if await request.body() else {}
    except (ValueError, UnicodeDecodeError):
        raise ApiError(400, "INVALID_JSON", "请求必须为有效 JSON")

    def execute():
        with request.app.state.db.transaction() as db:
            user = authenticator(db, request, write=True)
            if preauthorize is not None:
                preauthorize(db, user, body)
            key = request.headers.get("idempotency-key", "")
            if key_required and (not key or len(key) > 200):
                raise ApiError(400, "IDEMPOTENCY_KEY_REQUIRED", "POST 需要有效 Idempotency-Key")
            identity = sha([user.id, request.method, request.url.path, key])
            request_hash = sha(body)
            if key:
                # PostgreSQL 的事务级建议锁将同一幂等键串行，提交失败也不会留下半条响应。
                if db.bind.dialect.name == "postgresql":
                    from sqlalchemy import text

                    db.execute(
                        text("SELECT pg_advisory_xact_lock(:key)"), {"key": int(identity[:15], 16)}
                    )
                existing = db.get(IdempotencyRecord, identity)
                if existing:
                    authorize_cached_response(db, request, user, body, existing.response)
                    if existing.body_sha256 != request_hash:
                        raise ApiError(409, "IDEMPOTENCY_CONFLICT", "相同幂等键对应不同请求内容")
                    return JSONResponse(
                        existing.response,
                        status_code=existing.status_code,
                        headers=existing.headers,
                    )
            value, status, headers = operation(db, user, body)
            db.flush()
            if key:
                db.add(
                    IdempotencyRecord(
                        id=identity,
                        body_sha256=request_hash,
                        response=value,
                        status_code=status,
                        headers=headers,
                    )
                )
            if status == 204:
                return Response(status_code=204, headers=headers)
            return JSONResponse(value, status_code=status, headers=headers)

    return await run_in_threadpool(execute)


def list_page(db, request, kind: str, project_id: str | None, user_id: str, query=None) -> dict:
    try:
        limit = int(request.query_params.get("limit", "20"))
        if not 1 <= limit <= 100:
            raise ValueError()
    except ValueError:
        raise ApiError(422, "INVALID_LIMIT", "limit 应为 1 至 100")
    query = (
        query
        if query is not None
        else select(ResourceRecord).where(
            ResourceRecord.kind == kind,
            ResourceRecord.project_id == project_id,
            ResourceRecord.state != "DELETED",
        )
    )
    scope = sha([kind, project_id, user_id])
    cursor = request.query_params.get("cursor")
    if cursor:
        try:
            decoded = json.loads(base64.urlsafe_b64decode(cursor.encode()))
            if decoded["scope"] != scope:
                raise ValueError()
            from datetime import datetime

            after_time = datetime.fromisoformat(decoded["created_at"])
            query = query.where(
                (ResourceRecord.created_at > after_time)
                | ((ResourceRecord.created_at == after_time) & (ResourceRecord.id > decoded["id"]))
            )
        except (ValueError, KeyError, TypeError):
            raise ApiError(400, "INVALID_CURSOR", "分页游标无效或不属于当前查询")
    records = db.scalars(
        query.order_by(ResourceRecord.created_at, ResourceRecord.id).limit(limit + 1)
    ).all()
    next_cursor = None
    if len(records) > limit:
        last = records[limit - 1]
        next_cursor = base64.urlsafe_b64encode(
            canonical({"scope": scope, "created_at": iso(last.created_at), "id": last.id})
        ).decode()
    return {"items": [serialize(r) for r in records[:limit]], "next_cursor": next_cursor}
