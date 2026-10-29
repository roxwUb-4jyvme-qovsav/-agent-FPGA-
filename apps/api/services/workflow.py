"""批准、版本与运行状态的唯一事务入口。"""

from datetime import datetime, timedelta

from sqlalchemy import select

from packages.contracts.validation import validate_api_schema, validate_payload

from ..errors import ApiError
from ..models import ResourceRecord, SystemValue, TaskRecord, uid, utcnow
from ..security import aware, project_role
from .artifacts import artifact_json
from .common import check_version, event, get_resource, history, iso, sha


def target_digest(record):
    return sha(
        {
            "id": record.id,
            "kind": record.kind,
            "project_id": record.project_id,
            "payload": record.payload,
        }
    )


def prepare_create(db, user, kind, payload, project_id):
    record = ResourceRecord(kind=kind, project_id=project_id, payload=payload, internal={})
    if kind == "approvals":
        target = db.get(ResourceRecord, payload["target_id"])
        if not target or target.project_id != project_id:
            raise ApiError(404, "NOT_FOUND", "批准目标不存在")
        if payload["target_sha256"] != target_digest(target):
            raise ApiError(409, "APPROVAL_TARGET_CHANGED", "批准摘要与当前对象不一致")
        expiry = datetime.fromisoformat(payload["expires_at"].replace("Z", "+00:00"))
        if aware(expiry) <= utcnow() or aware(expiry) > utcnow() + timedelta(hours=24):
            raise ApiError(422, "INVALID_EXPIRY", "批准期限必须在未来 24 小时内")
        record.state = "PENDING"
        record.internal = {
            "requester_id": user.id,
            "revision_id": target.id,
            "actor_id": None,
            "used_execution_keys": [],
        }
        if target.kind == "revisions" and payload["action"] == "freeze":
            if target.state != "DRAFT":
                raise ApiError(409, "INVALID_STATE", "只有草案可请求冻结")
            history(db, target)
            target.state = "WAITING_APPROVAL"
    elif kind == "runs":
        revision = get_resource(db, "revisions", payload["revision_id"], project_id, lock=True)
        if revision.state not in ("FROZEN", "FAILED"):
            raise ApiError(409, "REVISION_NOT_FROZEN", "先冻结需求与契约，再创建运行")
        if payload["max_cost_cny"] != "0":
            raise ApiError(
                409, "BUDGET_NOT_CONFIGURED", "尚未登记真实模型额度，当前仅允许零费用静态检查"
            )
        if payload["mode"] != "simulate":
            raise ApiError(409, "WORKFLOW_NOT_READY", "变更和发布运行仍需完整下游配置")
        record.state = "QUEUED"
        db.add(record)
        db.flush()
        make_static_task(db, record, revision)
        history(db, revision)
        revision.state = "BUILDING"
    elif kind == "device-sessions":
        if payload["mode"] != "mock":
            raise ApiError(
                409,
                "HARDWARE_NOT_BOUND"
                if payload["mode"] == "hardware"
                else "CAPTURE_SOURCE_REQUIRED",
                "当前仅配置了明确标记的 mock 采集模型",
            )
        revision = get_resource(db, "revisions", payload["revision_id"], project_id)
        if not revision.payload.get("contract_artifact_id"):
            raise ApiError(409, "CONTRACT_REQUIRED", "采集会话需要统一契约")
        record.state = "OPEN"
    elif kind == "changes":
        record.state = "DRAFT"
    db.add(record)
    db.flush()
    event(db, user.id, kind + ".created", record)
    return record


def builtin_profile(db):
    config = db.get(SystemValue, "static_profile")
    if config:
        return config.value["id"]
    identifier = uid()
    # 工具任务不调用模型；冻结一个明确的 test_only 配置，不能冒充真实 Provider。
    profile = {
        "schema_version": "1.0",
        "id": identifier,
        "display_name": "离线契约检查 无模型调用",
        "provider_kind": "mock",
        "base_url": "https://example.invalid",
        "model_id": "no-model",
        "runtime_origin": "test_only",
        "secret_ref": None,
        "capabilities": ["text"],
        "context_limit": 1,
        "max_output_tokens": 1,
        "timeout_seconds": 120,
        "price_input_per_million_cny": None,
        "price_output_per_million_cny": None,
        "daily_budget_cny": "0",
        "fallback_profile_ids": [],
        "enabled": False,
    }
    db.add(ResourceRecord(id=identifier, kind="model-profiles", payload=profile, state="DISABLED"))
    db.add(SystemValue(key="static_profile", value={"id": identifier}))
    db.flush()
    return identifier


def make_static_task(db, run, revision):
    refs = [
        get_resource(db, "artifacts", identifier, run.project_id).payload
        for key, identifier in revision.payload.items()
        if key.endswith("_artifact_id") and identifier
    ]
    deadline = iso(utcnow() + timedelta(minutes=30))
    profile = builtin_profile(db)
    task = TaskRecord(
        id=uid(),
        run_id=run.id,
        project_id=run.project_id,
        revision_id=revision.id,
        state="QUEUED",
        capability="contract.validate",
        envelope={},
    )
    task.envelope = {
        "schema_version": "1.0",
        "task_id": task.id,
        "run_id": run.id,
        "project_id": run.project_id,
        "revision_id": revision.id,
        "attempt": 1,
        "fencing_token": 1,
        "agent_role": "contract_validation",
        "goal": "检查冻结契约结构与引用，不执行 EDA 或模型",
        "input_artifacts": refs,
        "output_schema": "urn:fpga:contracts:ToolResult:1.0",
        "model_profile_version_id": profile,
        "extension_version_ids": [],
        "skill_version_ids": [],
        "allowed_tools": ["contract.validate"],
        "workspace_relpath": f"runs/{run.id}/{task.id}",
        "deadline": deadline,
        "max_cost_cny": "0",
        "approval_ids": [],
        "idempotency_key": task.id,
    }
    validate_payload("TaskEnvelope", task.envelope)
    acceptance = next(r for r in refs if r["id"] == revision.payload["acceptance_artifact_id"])
    snapshot = {
        "schema_version": "1.0",
        "run_id": run.id,
        "project_id": run.project_id,
        "revision_id": revision.id,
        "input_artifacts": refs,
        "model_profile_version_ids": [profile],
        "skill_version_ids": [],
        "extension_version_ids": [],
        "acceptance_sha256": acceptance["sha256"],
        "toolchain_sha256": sha({"tool": "contract-validator", "mode": "static"}),
        "generator_sha256": sha({"generator": "static-validator-v1"}),
        "price_version_id": None,
        "deadline": deadline,
        "max_cost_cny": "0",
        "created_at": iso(utcnow()),
    }
    validate_payload("RunSnapshot", snapshot)
    run.internal = {
        "snapshot": snapshot,
        "blockers": ["MODEL_NOT_CONFIGURED", "EDA_NOT_VERIFIED", "CAD_NOT_VERIFIED"],
    }
    db.add(task)


def consume_approval(db, user, record, action, approval_id):
    if not approval_id:
        raise ApiError(409, "APPROVAL_REQUIRED", "该动作需要绑定对象摘要的人工批准")
    approval = get_resource(db, "approvals", approval_id, record.project_id, lock=True)
    payload = approval.payload
    if (
        approval.state != "APPROVED"
        or payload["action"] != action
        or payload["target_id"] != record.id
        or payload["target_sha256"] != target_digest(record)
        or aware(datetime.fromisoformat(payload["expires_at"].replace("Z", "+00:00"))) <= utcnow()
    ):
        raise ApiError(409, "APPROVAL_INVALID", "批准已过期、被撤销或摘要发生变化")
    project_role(db, user, record.project_id, ("reviewer", "admin"))
    executions = list(approval.internal.get("used_execution_keys", []))
    execution_key = f"{record.id}:{action}:{record.version}"
    if execution_key in executions:
        raise ApiError(409, "APPROVAL_ALREADY_USED", "该批准已执行")
    approval.internal = {**approval.internal, "used_execution_keys": executions + [execution_key]}


def execute_command(db, user, request, record, body):
    from ..routers.resources import CATALOG

    name = CATALOG[record.kind]["resource_schema"] + "Command"
    validate_api_schema(name, body)
    check_version(body, record)
    action = body["action"]
    params = body["parameters"]
    if record.kind == "revisions":
        if action == "freeze":
            if record.state not in ("DRAFT", "WAITING_APPROVAL"):
                raise ApiError(409, "INVALID_STATE", "当前版本不能再次冻结")
            validate_revision(db, request.app.state.settings, record)
            consume_approval(db, user, record, "freeze", params["approval_id"])
            history(db, record)
            record.state = "FROZEN"
        elif action == "validate":
            validate_revision(db, request.app.state.settings, record)
        elif action in ("release", "rollback"):
            raise ApiError(
                409, "EVIDENCE_REQUIRED", "尚未具备完整独立验证证据，禁止发布或回滚未发布版本"
            )
    elif record.kind == "runs":
        if action == "cancel":
            history(db, record)
            pending = False
            tasks = db.scalars(
                select(TaskRecord).where(TaskRecord.run_id == record.id).with_for_update()
            ).all()
            for task in tasks:
                if task.state in ("QUEUED", "WAITING_INPUT"):
                    task.state = "CANCELLED"
                elif task.state in ("LEASED", "RUNNING", "LOST"):
                    task.state = "CANCEL_REQUESTED"
                    pending = True
            record.state = "CANCEL_REQUESTED" if pending else "CANCELLED"
        else:
            raise ApiError(409, "RECONCILIATION_REQUIRED", "重试前必须对账旧进程和外部副作用")
    elif record.kind == "device-sessions" and action == "close":
        history(db, record)
        record.state = "CLOSED"
    else:
        from .management import management_command

        return management_command(db, user, request, record, body)
    emitted = event(db, user.id, record.kind + "." + action, record)
    db.flush()
    return (
        {
            "resource_id": record.id,
            "state": record.state,
            "run_id": record.id if record.kind == "runs" else None,
            "approval_id": params.get("approval_id"),
            "event_cursor": emitted.id,
        },
        202,
        {},
    )


def validate_revision(db, settings, record):
    payload = record.payload
    docs = {}
    for key, schema in [
        ("requirement_artifact_id", "RequirementSpec"),
        ("acceptance_artifact_id", "AcceptanceSpec"),
        ("contract_artifact_id", "SystemContract"),
    ]:
        if not payload.get(key):
            raise ApiError(409, "CONTRACT_REQUIRED", "冻结需要需求、契约和验收三个工件")
        artifact = get_resource(db, "artifacts", payload[key], record.project_id)
        document = artifact_json(settings, artifact)
        validate_payload(schema, document)
        if document["project_id"] != record.project_id or document["revision_id"] != record.id:
            raise ApiError(422, "REFERENCE_SCOPE_MISMATCH", "工件中的项目或版本与当前对象不一致")
        docs[schema] = document
    if any(
        q.get("blocking") and q.get("answer") is None for q in docs["RequirementSpec"]["questions"]
    ):
        raise ApiError(409, "NEEDS_INPUT", "仍有未解决的关键问题")
    contract = docs["SystemContract"]
    if contract["sample_bytes"] * 8 != contract["channels"]:
        raise ApiError(422, "INVALID_CONTRACT", "通道数与样本字宽不匹配")
    for key, hash_key in [
        ("requirement_artifact_id", "requirement_sha256"),
        ("acceptance_artifact_id", "acceptance_sha256"),
    ]:
        if (
            get_resource(db, "artifacts", payload[key], record.project_id).payload["sha256"]
            != contract[hash_key]
        ):
            raise ApiError(422, "CONTRACT_HASH_MISMATCH", "统一契约引用的输入哈希不一致")
