"""资源目录驱动的 CRUD；动作由各领域服务处理，拒绝任意 payload 和状态跳转。"""

from fastapi import APIRouter, Request
from sqlalchemy import func, select

from packages.contracts.validation import reference_json, validate_payload

from ..errors import ApiError
from ..models import Member, ResourceRecord, SystemValue, User
from ..security import project_role, require_admin
from ..services.common import (
    check_match,
    event,
    get_resource,
    history,
    list_page,
    mutate,
    read,
    response,
)

router = APIRouter(prefix="/api/v1")
CATALOG = reference_json("api/resource-catalog.json")
EDIT = ("developer", "reviewer", "admin")


def authorize(db, user, kind, project_id, *, write=False):
    if CATALOG[kind]["scope"] == "admin":
        require_admin(user)
    elif kind != "projects":
        project_role(
            db,
            user,
            project_id,
            ("admin",) if kind == "members" and write else EDIT if write else None,
        )


def validate_references(db, kind, payload, project_id):
    if kind == "revisions":
        if payload["parent_id"]:
            get_resource(db, "revisions", payload["parent_id"], project_id)
        for key in ("requirement_artifact_id", "contract_artifact_id", "acceptance_artifact_id"):
            if payload.get(key):
                get_resource(db, "artifacts", payload[key], project_id)
    if "revision_id" in payload:
        get_resource(db, "revisions", payload["revision_id"], project_id)
    if kind == "members" and db.get(User, payload["user_id"]) is None:
        raise ApiError(422, "INVALID_REFERENCE", "用户不存在")


def create_record(db, user, kind, payload, project_id, settings):
    validate_payload(CATALOG[kind]["payload_schema"], payload)
    authorize(db, user, kind, project_id, write=True)
    validate_references(db, kind, payload, project_id)
    if kind == "projects":
        if payload["team_id"] != db.get(SystemValue, "local_team").value["id"]:
            raise ApiError(403, "CAPABILITY_DENIED", "当前用户不属于该团队")
    if kind in ("runs", "device-sessions", "approvals", "changes"):
        from ..services.workflow import prepare_create

        return prepare_create(db, user, kind, payload, project_id, settings)
    record = ResourceRecord(kind=kind, project_id=project_id, payload=payload)
    if kind == "users":
        user_record = User(
            username=payload["username"],
            global_role=payload["global_role"],
            enabled=payload["enabled"],
        )
        db.add(user_record)
        db.flush()
        record.id = user_record.id
        record.state = "ENABLED" if payload["enabled"] else "DISABLED"
    db.add(record)
    db.flush()
    if kind == "projects":
        db.add(Member(project_id=record.id, user_id=user.id, role="admin"))
    if kind == "members":
        db.add(
            Member(
                id=record.id,
                project_id=project_id,
                user_id=payload["user_id"],
                role=payload["role"],
            )
        )
    event(
        db, user.id, kind + ".created", record, project_id=record.id if kind == "projects" else None
    )
    return record


def register_collection(kind: str):
    spec = CATALOG[kind]
    prefix = "/admin" if spec["scope"] == "admin" else "/projects/{project_id}"
    base = "/projects" if kind == "projects" else prefix + "/" + kind

    async def collection_get(request: Request, project_id: str | None = None):
        def operation(db, user):
            authorize(db, user, kind, project_id)
            query = None
            if kind == "projects":
                visible = select(Member.project_id).where(Member.user_id == user.id)
                query = select(ResourceRecord).where(
                    ResourceRecord.kind == kind, ResourceRecord.id.in_(visible)
                )
            elif kind in ("collections", "documents", "artifacts"):
                from ..services.visibility import visible_ids

                identifiers = visible_ids(db, user, kind, project_id)
                query = select(ResourceRecord).where(
                    ResourceRecord.kind == kind,
                    ResourceRecord.project_id == project_id,
                    ResourceRecord.id.in_(identifiers),
                    ResourceRecord.state != "DELETED",
                )
            return list_page(db, request, kind, project_id, user.id, query)

        return await read(request, operation)

    router.add_api_route(base, collection_get, methods=["GET"], name=kind + "_list")
    if spec["create"]:

        async def collection_post(request: Request, project_id: str | None = None):
            return await mutate(
                request,
                lambda db, user, body: response(
                    create_record(db, user, kind, body, project_id, request.app.state.settings), 201
                ),
            )

        router.add_api_route(base, collection_post, methods=["POST"], name=kind + "_create")

    item = "/projects/{resource_id}" if kind == "projects" else base + "/{resource_id}"

    async def item_get(request: Request, resource_id: str, project_id: str | None = None):
        def operation(db, user):
            authorize(db, user, kind, project_id)
            if kind == "projects":
                project_role(db, user, resource_id)
            record = get_resource(db, kind, resource_id, project_id)
            from ..services.visibility import resource_visible

            if not resource_visible(db, user, record):
                raise ApiError(404, "NOT_FOUND", "资源不存在")
            return response(record)

        return await read(request, operation)

    router.add_api_route(item, item_get, methods=["GET"], name=kind + "_get")

    if spec["replace_draft_only"]:

        async def item_put(request: Request, resource_id: str, project_id: str | None = None):
            def operation(db, user, body):
                authorize(db, user, kind, project_id, write=True)
                record = get_resource(db, kind, resource_id, project_id, lock=True)
                check_match(request, record)
                validate_payload(spec["payload_schema"], body)
                validate_references(db, kind, body, project_id)
                if record.state in ("ENABLED", "PUBLISHED", "APPROVED"):
                    raise ApiError(409, "RESOURCE_IN_USE", "请先停用，再创建新的配置草案版本")
                if kind == "members":
                    synchronize_member(db, record, body)
                if kind == "users":
                    if record.id == user.id and not body["enabled"]:
                        raise ApiError(409, "LAST_ADMIN", "不能停用当前管理员会话")
                    target = db.get(User, record.id)
                    target.username, target.global_role, target.enabled = (
                        body["username"],
                        body["global_role"],
                        body["enabled"],
                    )
                history(db, record)
                record.payload = body
                db.flush()
                event(db, user.id, kind + ".updated", record)
                return response(record)

            return await mutate(request, operation, key_required=False)

        router.add_api_route(item, item_put, methods=["PUT"], name=kind + "_replace")

    if spec["delete_if_unreferenced"]:

        async def item_delete(request: Request, resource_id: str, project_id: str | None = None):
            def operation(db, user, body):
                authorize(db, user, kind, project_id, write=True)
                record = get_resource(db, kind, resource_id, project_id, lock=True)
                check_match(request, record)
                if kind == "members":
                    synchronize_member(db, record, None)
                elif kind == "users":
                    if record.id == user.id:
                        raise ApiError(409, "RESOURCE_IN_USE", "不能删除当前账号")
                    db.get(User, record.id).enabled = False
                history(db, record)
                record.state = "DELETED"
                event(db, user.id, kind + ".deleted", record)
                return {}, 204, {}

            return await mutate(request, operation, key_required=False)

        router.add_api_route(item, item_delete, methods=["DELETE"], name=kind + "_delete")

    if spec["commands"]:

        async def command(request: Request, resource_id: str, project_id: str | None = None):
            from ..services.workflow import execute_command

            def operation(db, user, body):
                authorize(db, user, kind, project_id, write=True)
                record = get_resource(db, kind, resource_id, project_id, lock=True)
                return execute_command(db, user, request, record, body)

            return await mutate(request, operation)

        router.add_api_route(item + "/commands", command, methods=["POST"], name=kind + "_command")


def synchronize_member(db, record, body):
    member = db.get(Member, record.id)
    if member.role == "admin" and (
        body is None or body["role"] != "admin" or body["user_id"] != member.user_id
    ):
        count = db.scalar(
            select(func.count())
            .select_from(Member)
            .where(Member.project_id == record.project_id, Member.role == "admin")
        )
        if count <= 1:
            raise ApiError(409, "LAST_ADMIN", "不能移除项目最后一个管理员")
    if body:
        member.user_id, member.role = body["user_id"], body["role"]
    else:
        db.delete(member)


@router.patch("/projects/{project_id}")
async def project_patch(request: Request, project_id: str):
    def operation(db, user, body):
        project_role(db, user, project_id, EDIT)
        record = get_resource(db, "projects", project_id, lock=True)
        check_match(request, record)
        validate_payload("ProjectPatch", body)
        history(db, record)
        record.payload = {**record.payload, **body}
        db.flush()
        event(db, user.id, "projects.updated", record, project_id)
        return response(record)

    return await mutate(request, operation, key_required=False)


for resource_kind in CATALOG:
    register_collection(resource_kind)
