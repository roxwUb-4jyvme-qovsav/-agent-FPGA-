"""SSE 首连与重连；失效凭据在下一批读取前终止连接。"""

import asyncio
import json
import time

from fastapi import APIRouter, Request
from fastapi.responses import StreamingResponse
from sqlalchemy.exc import SQLAlchemyError
from starlette.concurrency import run_in_threadpool

from ..errors import ApiError
from ..services import events

router = APIRouter(prefix="/api/v1/projects/{project_id}")


@router.get("/events")
async def stream_events(request: Request, project_id: str, follow: bool = True):
    cursor = events.parse_cursor(request.headers.get("last-event-id"))
    # 在发送 200 头之前验证身份、项目与游标，保证过期游标确实返回 HTTP 410。
    first = await run_in_threadpool(events.read_batch, request, project_id, cursor)

    async def generate():
        batch = first
        heartbeat_at = time.monotonic()
        yield ": connected\n\n"
        while True:
            for value in batch.events:
                yield events.event_frame(value)
            # id-only 检查点允许跨过不可见/旧格式事件，不泄露资源引用或正文。
            yield f"id: {batch.cursor}\n\n"
            if not follow or await request.is_disconnected():
                return
            if time.monotonic() - heartbeat_at >= events.HEARTBEAT_SECONDS:
                yield ": heartbeat\n\n"
                heartbeat_at = time.monotonic()
            await asyncio.sleep(events.POLL_SECONDS)
            try:
                # 首连已经确定高水位；连续在线期间不因该水位变老而触发重连循环。
                batch = await run_in_threadpool(events.read_batch, request, project_id,
                                                batch.cursor, validate_cursor=False)
            except ApiError as exc:
                control = "resync" if exc.status == 410 else "access_denied"
                yield f"event: {control}\ndata: {json.dumps(exc.body(request.state.request_id))}\n\n"
                return
            except SQLAlchemyError:
                yield "event: unavailable\ndata: {}\n\n"
                return

    return StreamingResponse(generate(), media_type="text/event-stream", headers={
        "Cache-Control": "no-cache, no-transform", "X-Accel-Buffering": "no",
    })
