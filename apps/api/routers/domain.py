"""领域查询和治理入口，复用会话、CSRF、幂等及同一数据库事务。"""

import os
from typing import Any
from uuid import NAMESPACE_URL, uuid5

from cryptography.fernet import Fernet
from fastapi import APIRouter, Request
from sqlalchemy import select

from packages.contracts.validation import validate_payload
from packages.knowledge.index import KnowledgeIndex

from ..errors import ApiError
from ..models import EventRecord, Member, ResourceHistory, ResourceRecord, SecretRecord, uid, utcnow
from ..security import authenticate, project_role, require_admin
from ..services.common import (
    check_match,
    event,
    get_resource,
    history,
    iso,
    mutate,
    read,
)

router = APIRouter(prefix="/api/v1")


@router.post("/projects/{project_id}/knowledge/search")
async def knowledge_search(request: Request, project_id: str):
    try:
        body = await request.json()
    except (ValueError, UnicodeDecodeError) as exc:
        raise ApiError(400, "INVALID_JSON", "请求必须为有效 JSON") from exc
    if not request.headers.get("idempotency-key"):
        raise ApiError(400, "IDEMPOTENCY_KEY_REQUIRED", "搜索请求需要 Idempotency-Key")

    def operation(db: Any, user: Any):
        authenticate(db, request, write=True)
        project_role(db, user, project_id)
        validate_payload("SearchRequest", body)
        membership = db.scalar(
            select(Member).where(Member.project_id == project_id, Member.user_id == user.id)
        )
        collections = []
        for identifier in body["collection_ids"]:
            collection = get_resource(db, "collections", identifier, project_id)
            permitted = collection.payload.get("acl_member_ids", [])
            if user.id in permitted or membership.id in permitted:
                collections.append(collection.id)
        # SQL 首先限定项目、发布态和已通过 ACL 的 collection；排名器无法见到私有内容。
        records = db.scalars(
            select(ResourceRecord).where(
                ResourceRecord.kind == "documents",
                ResourceRecord.project_id == project_id,
                ResourceRecord.state == "PUBLISHED",
            )
        ).all()
        documents = []
        for record in records:
            if record.payload.get("collection_id") not in collections:
                continue
            parsed = record.internal.get("parsed", {})
            documents.append(
                {
                    "id": record.id,
                    "source_id": record.id,
                    "project_id": project_id,
                    "collection_id": record.payload["collection_id"],
                    "version_id": record.internal["version_id"],
                    "status": record.state,
                    "allowed_user_ids": [user.id],
                    "license": record.payload["license"],
                    "source_uri": parsed["source_uri"],
                    "chunks": parsed["chunks"],
                }
            )
        index = KnowledgeIndex(documents, generation="db-keyword-v1")
        result = index.search(
            {"project_id": project_id, "user_id": user.id, "member": True},
            body["query"],
            collections,
            body["document_version_ids"],
            body["top_k"],
        )
        result["warnings"].append("KEYWORD_ONLY_VECTOR_NOT_CONFIGURED")
        return result, 200, {}

    # 搜索不产生外部副作用，每次都重查 ACL；不能复用撤权前的幂等响应缓存。
    return await read(request, operation)


@router.get("/system/resources/{resource_id}/versions")
async def resource_versions(request: Request, resource_id: str):
    def operation(db: Any, user: Any):
        record = db.get(ResourceRecord, resource_id)
        if not record:
            raise ApiError(404, "NOT_FOUND", "资源不存在")
        if record.project_id:
            project_role(db, user, record.project_id)
        else:
            require_admin(user)
        rows = db.scalars(
            select(ResourceHistory)
            .where(ResourceHistory.resource_id == record.id)
            .order_by(ResourceHistory.version)
        ).all()
        return {
            "items": [
                {
                    "id": row.id,
                    "version": row.version,
                    "state": row.state,
                    "payload": row.payload,
                    "created_at": iso(row.created_at),
                }
                for row in rows
            ],
            "current_version": record.version,
        }

    return await read(request, operation)


def _cipher() -> Fernet:
    key = os.getenv("FPGA_SECRET_KEY")
    if not key:
        raise ApiError(409, "SECRET_KEY_NOT_CONFIGURED", "请通过进程环境登记本机加密主密钥")
    try:
        return Fernet(key.encode())
    except (ValueError, TypeError) as exc:
        raise ApiError(503, "SECRET_KEY_INVALID", "加密主密钥格式错误") from exc


def _secret_meta(record: SecretRecord, metadata: ResourceRecord) -> dict[str, Any]:
    return {
        "id": record.id,
        "label": record.name,
        "created_at": iso(record.created_at),
        "rotated_at": metadata.internal.get("rotated_at"),
        "configured": True,
    }


@router.post("/admin/secrets")
async def create_secret(request: Request):
    def operation(db: Any, user: Any, body: dict[str, Any]):
        require_admin(user)
        validate_payload("SecretInput", body)
        identifier = uid()
        record = SecretRecord(
            id=identifier,
            name=body["label"],
            ciphertext=_cipher().encrypt(body["value"].encode()).decode(),
        )
        metadata = ResourceRecord(
            id=identifier,
            kind="secret-meta",
            state="CONFIGURED",
            payload={"label": body["label"]},
            internal={},
        )
        db.add_all([record, metadata])
        db.flush()
        event(
            db,
            user.id,
            "secret.created",
            metadata,
            payload={"request_id": request.state.request_id},
        )
        return _secret_meta(record, metadata), 201, {"ETag": f'"{record.id}:{record.version}"'}

    return await mutate(request, operation)


@router.get("/admin/secrets/{resource_id}")
async def get_secret(request: Request, resource_id: str):
    def operation(db: Any, user: Any):
        require_admin(user)
        record = db.get(SecretRecord, resource_id)
        metadata = get_resource(db, "secret-meta", resource_id)
        if not record:
            raise ApiError(404, "NOT_FOUND", "凭证不存在")
        return _secret_meta(record, metadata), 200, {"ETag": f'"{record.id}:{record.version}"'}

    return await read(request, operation)


def _revoke_connections(db: Any, secret_id: str) -> None:
    records = db.scalars(
        select(ResourceRecord)
        .where(
            ResourceRecord.kind.in_(
                ("model-profiles", "mcp-connections", "knowledge-connections", "quote-connections")
            )
        )
        .with_for_update()
    ).all()
    for record in records:
        if record.payload.get("secret_ref") == secret_id:
            history(db, record)
            record.state = "DISABLED"
            record.internal = {
                **record.internal,
                "health": {"status": "BLOCKED", "code": "SECRET_CHANGED"},
            }
            if "enabled" in record.payload:
                record.payload = {**record.payload, "enabled": False}


@router.put("/admin/secrets/{resource_id}")
async def rotate_secret(request: Request, resource_id: str):
    def operation(db: Any, user: Any, body: dict[str, Any]):
        require_admin(user)
        validate_payload("SecretInput", body)
        metadata = get_resource(db, "secret-meta", resource_id, lock=True)
        check_match(request, metadata)
        record = db.get(SecretRecord, resource_id)
        if not record:
            raise ApiError(404, "NOT_FOUND", "凭证不存在")
        encrypted = _cipher().encrypt(body["value"].encode()).decode()
        history(db, metadata)
        metadata.payload, metadata.internal = (
            {"label": body["label"]},
            {"rotated_at": iso(utcnow())},
        )
        record.name, record.ciphertext, record.version = (
            body["label"],
            encrypted,
            record.version + 1,
        )
        _revoke_connections(db, resource_id)
        event(
            db,
            user.id,
            "secret.rotated",
            metadata,
            payload={"request_id": request.state.request_id},
        )
        return _secret_meta(record, metadata), 200, {"ETag": f'"{record.id}:{record.version}"'}

    return await mutate(request, operation, key_required=False)


@router.delete("/admin/secrets/{resource_id}")
async def delete_secret(request: Request, resource_id: str):
    def operation(db: Any, user: Any, body: dict[str, Any]):
        require_admin(user)
        metadata = get_resource(db, "secret-meta", resource_id, lock=True)
        check_match(request, metadata)
        record = db.get(SecretRecord, resource_id)
        if not record:
            raise ApiError(404, "NOT_FOUND", "凭证不存在")
        _revoke_connections(db, resource_id)
        db.delete(record)
        history(db, metadata)
        metadata.state = "DELETED"
        emitted = event(
            db,
            user.id,
            "secret.deleted",
            metadata,
            payload={"request_id": request.state.request_id},
        )
        return (
            {
                "resource_id": resource_id,
                "state": "DELETED",
                "run_id": None,
                "approval_id": None,
                "event_cursor": emitted.id,
            },
            200,
            {},
        )

    return await mutate(request, operation, key_required=False)


@router.get("/admin/audit")
async def audit_log(request: Request):
    def operation(db: Any, user: Any):
        require_admin(user)
        try:
            limit = int(request.query_params.get("limit", "20"))
            cursor = int(request.query_params.get("cursor", "0"))
            if not 1 <= limit <= 100 or cursor < 0:
                raise ValueError()
        except ValueError:
            raise ApiError(422, "INVALID_CURSOR", "审计分页参数无效")
        rows = db.scalars(
            select(EventRecord)
            .where(EventRecord.id > cursor)
            .order_by(EventRecord.id)
            .limit(limit + 1)
        ).all()
        return {
            "items": [
                {
                    "schema_version": "1.0",
                    "id": str(uuid5(NAMESPACE_URL, "audit:" + str(row.id))),
                    "sequence": row.id,
                    "project_id": row.project_id,
                    "audit_scope": "project" if row.project_id else "system",
                    "actor_id": row.actor_id,
                    "action": row.type,
                    "target_id": row.resource_id,
                    "target_version": None,
                    "occurred_at": iso(row.created_at),
                    "request_id": row.payload.get(
                        "request_id", "00000000-0000-0000-0000-000000000000"
                    ),
                    "summary": row.type,
                }
                for row in rows[:limit]
            ],
            "next_cursor": str(rows[limit - 1].id) if len(rows) > limit else None,
        }

    return await read(request, operation)


@router.post("/admin/backups")
async def create_backup(request: Request):
    def operation(db: Any, user: Any, body: dict[str, Any]):
        require_admin(user)
        validate_payload("BackupRequest", body)
        raise ApiError(409, "BACKUP_NOT_CONFIGURED", "尚未配置数据库和加密工件的协调备份后端")

    return await mutate(request, operation)


@router.post("/admin/restores")
async def restore_backup(request: Request):
    def operation(db: Any, user: Any, body: dict[str, Any]):
        require_admin(user)
        validate_payload("RestoreRequest", body)
        raise ApiError(
            409, "RESTORE_NOT_CONFIGURED", "尚未实现包含删除与撤权重放的恢复演练，禁止声明恢复成功"
        )

    return await mutate(request, operation)
