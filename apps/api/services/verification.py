"""可信任务预期、签名原文与有效性投影分开保存。"""

import base64
import json
from copy import deepcopy
from typing import Any

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey
from fastapi import Request
from sqlalchemy import select
from sqlalchemy.orm import Session

from acceptance_service.signing import canonical_bytes
from acceptance_service.validation import timestamp
from acceptance_service.verification import (
    ApprovedKey,
    EvidenceError,
    evaluate_validity,
    verify_evidence,
)
from packages.contracts.validation import validate_payload

from ..config import Settings
from ..errors import ApiError
from ..models import ResourceRecord, SystemValue, TaskRecord, utcnow
from .artifacts import artifact_bytes
from .common import get_resource, sha
from .service_identity import ServicePrincipal, _uuid, authorize_task

ASSIGNMENT_FIELDS = {
    "test_id",
    "suite_sha256",
    "environment_sha256",
    "tool_version",
    "template_id",
    "seed",
    "mode",
}


def register_assignment(db: Session, task: TaskRecord, expected: dict[str, Any]) -> None:
    """仅供可信调度器调用，无客户端写入口；隔离后端未登记时不得创建验收任务。"""
    if (
        task.capability != "acceptance.verify"
        or task.state != "QUEUED"
        or task.envelope.get("output_schema") != "urn:fpga:contracts:VerificationEvidence:1.0"
        or set(expected) != ASSIGNMENT_FIELDS
    ):
        raise ApiError(422, "INVALID_VERIFICATION_ASSIGNMENT", "验收任务或固定预期不完整")
    validate_payload("TaskEnvelope", task.envelope)
    run = get_resource(db, "runs", task.run_id, task.project_id)
    get_resource(db, "revisions", task.revision_id, task.project_id)
    if (
        run.payload["revision_id"] != task.revision_id
        or any(
            task.envelope[k] != getattr(task, k) for k in ("run_id", "project_id", "revision_id")
        )
        or task.envelope["task_id"] != task.id
    ):
        raise ApiError(422, "INVALID_VERIFICATION_ASSIGNMENT", "任务归属与冻结信封不符")
    value = {**deepcopy(expected), "input_sha256": sha(task.envelope["input_artifacts"])}
    key = "verification-task:" + task.id
    existing = db.get(SystemValue, key)
    if existing and existing.value != value:
        raise ApiError(409, "ASSIGNMENT_IMMUTABLE", "验收预期不可改写，请创建新任务")
    if not existing:
        db.add(SystemValue(key=key, value=value))


def expected_binding(db: Session, task: TaskRecord) -> dict:
    assignment = db.get(SystemValue, "verification-task:" + task.id)
    if not assignment:
        raise ApiError(409, "VERIFICATION_NOT_REGISTERED", "可信调度器尚未登记验收预期")
    return {
        **assignment.value,
        "task_id": task.id,
        "project_id": task.project_id,
        "revision_id": task.revision_id,
    }


def approved_keys(settings: Settings) -> list[ApprovedKey]:
    try:
        path = settings.verifier_public_keys
        if path is None or path.stat().st_size > 1024 * 1024:
            raise ValueError("keys unavailable")
        document = json.loads(path.read_text(encoding="utf-8"))
        if set(document) != {"schema_version", "keys"} or document["schema_version"] != "1.0":
            raise ValueError("invalid key registry")
        if not isinstance(document["keys"], list) or len(document["keys"]) > 1000:
            raise ValueError("invalid key list")
        result, identities = [], set()
        for item in document["keys"]:
            if set(item) != {"issuer_id", "key_id", "public_key_base64", "revoked_at"}:
                raise ValueError("invalid key fields")
            identity = (_uuid(item["issuer_id"]), item["key_id"])
            if not isinstance(identity[1], str) or not identity[1] or identity in identities:
                raise ValueError("invalid or duplicate key")
            identities.add(identity)
            if item["revoked_at"] is not None:
                timestamp(item["revoked_at"])
            key = Ed25519PublicKey.from_public_bytes(
                base64.b64decode(item["public_key_base64"], validate=True)
            )
            result.append(ApprovedKey(*identity, key, item["revoked_at"]))
        return result
    except (OSError, ValueError, TypeError, KeyError, AttributeError) as exc:
        raise ApiError(503, "VERIFIER_KEYS_UNAVAILABLE", "受信任公钥登记不可用") from exc


def check_report(db: Session, settings: Settings, task: TaskRecord, evidence: dict) -> None:
    report = get_resource(db, "artifacts", evidence["report"]["id"], task.project_id)
    if (
        report.payload != evidence["report"]
        or report.payload["kind"] != "verification.report"
        or report.internal.get("task_id") != task.id
        or report.internal.get("worker_id") != task.worker_id
        or report.internal.get("attempt") != task.attempt
        or report.internal.get("fencing_token") != task.fencing_token
        or report.internal.get("provenance") != "verifier_worker"
    ):
        raise ApiError(422, "REPORT_SCOPE_MISMATCH", "验收报告不属于本任务当前受控上传")
    artifact_bytes(settings, report, max_bytes=1024 * 1024)


def check_submission(
    db: Session, request: Request, principal: ServicePrincipal, body: dict
) -> tuple[TaskRecord, ResourceRecord | None, list[ApprovedKey]]:
    """幂等缓存前也执行；签名、角色、报告与撤销不能被历史响应绕过。"""
    from ..routers.jobs import current_lease, deadline_elapsed
    from ..routers.worker_artifacts import lease_headers

    validate_payload("VerificationEvidence", body)
    task = db.get(TaskRecord, body["task_id"])
    if not task:
        raise ApiError(404, "NOT_FOUND", "验收任务不存在")
    authorize_task(principal, task)
    # 与取消入口统一 run -> task 锁序；等待后重读任务，不能使用锁前身份映射中的旧状态。
    run = get_resource(db, "runs", task.run_id, task.project_id, lock=True)
    task = db.scalar(
        select(TaskRecord)
        .where(TaskRecord.id == task.id)
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    authorize_task(principal, task)
    if task.capability != "acceptance.verify" or principal.issuer_id != body["issuer_id"]:
        raise ApiError(403, "VERIFIER_SCOPE_DENIED", "签发者或任务不是登记的独立验收身份")
    headers = lease_headers(request)
    if (
        headers["worker_id"] != task.worker_id
        or headers["attempt"] != task.attempt
        or headers["fencing_token"] != task.fencing_token
    ):
        raise ApiError(409, "STALE_LEASE", "证据提交与任务当前租约不一致")
    keys = approved_keys(request.app.state.settings)
    matched = [
        k
        for k in keys
        if k.issuer_id == body["issuer_id"] and k.key_id == body["auth_tag"]["key_id"]
    ]
    if len(matched) == 1 and matched[0].revoked_at is not None:
        raise ApiError(403, "VERIFIER_KEY_REVOKED", "已撤销密钥不能提交新证据或重放入库请求")
    expected = expected_binding(db, task)
    if expected["input_sha256"] != sha(task.envelope["input_artifacts"]):
        raise ApiError(409, "VERIFICATION_INPUT_CHANGED", "任务输入与登记预期不符")
    try:
        verify_evidence(body, keys, expected)
    except EvidenceError as exc:
        raise ApiError(422, "INVALID_VERIFICATION_EVIDENCE", str(exc)) from exc
    existing = db.get(ResourceRecord, body["id"])
    if existing:
        if (
            existing.kind != "evidence"
            or existing.project_id != task.project_id
            or canonical_bytes(existing.payload) != canonical_bytes(body)
        ):
            raise ApiError(409, "EVIDENCE_ID_CONFLICT", "同一证据ID不能对应不同内容")
    else:
        current_lease(db, task.id, headers)
        if (
            task.state == "CANCEL_REQUESTED"
            or run.state in ("CANCEL_REQUESTED", "CANCELLED")
            or deadline_elapsed(task)
        ):
            raise ApiError(409, "VERIFICATION_INTERRUPTED", "取消或截止后不能提升验收证据")
        started, finished = timestamp(body["started_at"]), timestamp(body["finished_at"])
        if started < timestamp(task.attempts[-1]["started_at"]) or finished > utcnow():
            raise ApiError(
                422, "EVIDENCE_TIME_MISMATCH", "验收时间必须在本次租约开始之后且不晚于服务端时间"
            )
    check_report(db, request.app.state.settings, task, body)
    return task, existing, keys


def project_validity(
    db: Session, settings: Settings, record: ResourceRecord, revision_id: str
) -> dict:
    evidence = record.payload
    get_resource(db, "revisions", revision_id, record.project_id)
    task = db.get(TaskRecord, evidence["task_id"])
    try:
        keys = approved_keys(settings)
    except ApiError:
        keys = []
    reason = None
    try:
        if not task or task.project_id != record.project_id:
            raise ApiError(409, "TASK_UNAVAILABLE", "验收任务缺失")
        expected = {
            **expected_binding(db, task),
            "input_sha256": sha(task.envelope["input_artifacts"]),
        }
        check_report(db, settings, task, evidence)
    except ApiError as exc:
        expected, reason = {}, exc.code
    validity = evaluate_validity(evidence, keys, current_revision_id=revision_id, expected=expected)
    if reason and validity["signature_valid"]:
        validity.update(effective_status="INDETERMINATE", stale_reason=reason)
    # 投影单独存放，绝不更新 evidence 的 payload、版本或原始 PASS。
    key = f"evidence-validity:{record.id}:{revision_id}"
    projection = db.get(SystemValue, key)
    if projection:
        projection.value = validity
    else:
        db.add(SystemValue(key=key, value=validity))
    return validity


def task_result(evidence: dict, validity: dict) -> dict:
    result = {
        k: evidence[k]
        for k in (
            "schema_version",
            "task_id",
            "revision_id",
            "tool_version",
            "input_sha256",
            "started_at",
            "finished_at",
            "exit_code",
        )
    }
    result.update(
        call_id=evidence["id"],
        artifact_refs=[evidence["report"]],
        log_ref=None,
        status="passed"
        if validity["effective_status"] == "PASS"
        else "not_run"
        if evidence["status"] == "NOT_RUN"
        else "failed",
        summary="独立验收证据 " + evidence["id"] + "；有效性 " + validity["effective_status"],
    )
    validate_payload("ToolResult", result)
    return result
