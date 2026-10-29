"""设备接口：每次按项目与会话鉴权，写入按会话行锁串行。"""

from fastapi import APIRouter, Request
from sqlalchemy import select

from ..errors import ApiError
from ..models import ResourceRecord
from ..security import project_role
from ..services.common import etag, get_resource, list_page, mutate, read
from ..services.devices import advance_mock, execute_device

router = APIRouter(prefix="/api/v1/projects/{project_id}/device-sessions")
EDIT = ("developer", "reviewer", "admin")


@router.post("/{resource_id}/device-commands")
async def device_command(request: Request, project_id: str, resource_id: str):
    def operation(db, user, body):
        project_role(db, user, project_id, EDIT)
        record = get_resource(db, "device-sessions", resource_id, project_id, lock=True)
        accepted, command = execute_device(db, record, body, user.id, request.state.request_id)
        return accepted, 202, {"ETag": etag(command)}

    return await mutate(request, operation)


@router.post("/{resource_id}/mock-inputs")
async def mock_inputs(request: Request, project_id: str, resource_id: str):
    def operation(db, user, body):
        project_role(db, user, project_id, EDIT)
        record = get_resource(db, "device-sessions", resource_id, project_id, lock=True)
        result = advance_mock(db, request.app.state.settings, record, body, user.id,
                              request.state.request_id)
        db.flush()
        return result, 200, {"ETag": etag(record)}

    return await mutate(request, operation)


def scoped_record(db, user, project_id: str, session_id: str, kind: str,
                  identifier: str) -> ResourceRecord:
    project_role(db, user, project_id)
    get_resource(db, "device-sessions", session_id, project_id)
    record = get_resource(db, kind, identifier, project_id)
    if record.payload["session_id"] != session_id:
        raise ApiError(404, "NOT_FOUND", "资源不存在")
    return record


@router.get("/{session_id}/commands/{command_id}")
async def command_result(request: Request, project_id: str, session_id: str, command_id: str):
    def operation(db, user):
        record = scoped_record(db, user, project_id, session_id, "device-command-results", command_id)
        return record.payload, 200, {"ETag": etag(record)}

    return await read(request, operation)


@router.get("/{session_id}/captures")
async def captures(request: Request, project_id: str, session_id: str):
    def operation(db, user):
        project_role(db, user, project_id)
        get_resource(db, "device-sessions", session_id, project_id)
        query = select(ResourceRecord).where(
            ResourceRecord.kind == "captures", ResourceRecord.project_id == project_id,
            ResourceRecord.internal["session_id"].as_string() == session_id,
        )
        # 将会话放入游标 scope，防止把另一会话的游标拿来跳过结果。
        result = list_page(db, request, "captures:" + session_id, project_id, user.id, query)
        result["items"] = [row["payload"] for row in result["items"]]
        return result

    return await read(request, operation)


@router.get("/{session_id}/captures/{resource_id}")
async def capture_detail(request: Request, project_id: str, session_id: str, resource_id: str):
    def operation(db, user):
        record = scoped_record(db, user, project_id, session_id, "captures", resource_id)
        return record.payload, 200, {"ETag": etag(record)}

    return await read(request, operation)
