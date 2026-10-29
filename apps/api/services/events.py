"""从已提交 outbox 投递脱敏摘要；每批重新认证，读取不持有长事务。"""

import json
from dataclasses import dataclass
from datetime import timedelta
from typing import Any

from fastapi import Request
from sqlalchemy import func, select

from packages.contracts.validation import validate_payload

from ..errors import ApiError
from ..models import EventRecord, ResourceRecord, utcnow
from ..security import authenticate, aware, project_role
from .visibility import resource_visible

RETENTION_DAYS = 7
BATCH_SIZE = 100
POLL_SECONDS = 0.5
HEARTBEAT_SECONDS = 15.0


@dataclass(frozen=True)
class EventBatch:
    cursor: int
    events: list[dict[str, Any]]


def parse_cursor(value: str | None) -> int | None:
    if value is None:
        return None
    if not value.isascii() or not value.isdecimal() or len(value) > 16:
        raise ApiError(400, "INVALID_EVENT_CURSOR", "事件游标必须是有效整数")
    cursor = int(value)
    if cursor > 9007199254740991:
        raise ApiError(400, "INVALID_EVENT_CURSOR", "事件游标超出支持范围")
    return cursor


def read_batch(request: Request, project_id: str, cursor: int | None, *, validate_cursor: bool = True) -> EventBatch:
    with request.app.state.db.transaction() as db:
        user = authenticate(db, request)
        project_role(db, user, project_id)
        cutoff = utcnow() - timedelta(days=RETENTION_DAYS)
        latest = db.scalar(select(func.max(EventRecord.id)).where(EventRecord.project_id == project_id)) or 0
        # 首连从高水位订阅，客户端在连接建立后刷新状态；重连使用最后确认游标。
        if cursor is None:
            return EventBatch(latest, [])
        if validate_cursor and cursor:
            row = db.get(EventRecord, cursor)
            if not row or row.project_id != project_id or cursor > latest:
                raise ApiError(400, "INVALID_EVENT_CURSOR", "游标不属于当前项目事件流")
            if aware(row.created_at) < cutoff:
                raise ApiError(410, "EVENT_CURSOR_EXPIRED", "事件游标已过期，请刷新项目状态后重新订阅")
        elif validate_cursor and cursor == 0 and db.scalar(select(EventRecord.id).where(
            EventRecord.project_id == project_id, EventRecord.created_at < cutoff,
        ).limit(1)):
            raise ApiError(410, "EVENT_CURSOR_EXPIRED", "历史事件超出七天窗口，请重新同步")
        rows = db.scalars(select(EventRecord).where(
            EventRecord.project_id == project_id, EventRecord.id > cursor,
        ).order_by(EventRecord.id).limit(BATCH_SIZE)).all()
        events = []
        for row in rows:
            cursor = row.id
            frozen = row.payload.get("_stream")
            if not frozen or aware(row.created_at) < cutoff:
                continue
            record = db.get(ResourceRecord, row.resource_id)
            if not record or (record.project_id != project_id and not (
                record.kind == "projects" and record.id == project_id
            )) or not resource_visible(db, user, record):
                continue
            value = {**frozen, "id": row.id}
            validate_payload("Event", value)
            events.append(value)
        return EventBatch(cursor, events)


def event_frame(value: dict[str, Any]) -> str:
    return f"id: {value['id']}\ndata: {json.dumps(value, ensure_ascii=False, separators=(',', ':'))}\n\n"
