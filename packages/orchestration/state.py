"""状态迁移只判断准入，事务和当前版本证据投影由调用方提供。"""
from typing import Iterable, Mapping


class StateError(ValueError):
    pass


REVISION_TRANSITIONS: dict[str, set[str]] = {
    "DRAFT": {"WAITING_APPROVAL", "NEEDS_INPUT", "FAILED"},
    "NEEDS_INPUT": {"DRAFT", "WAITING_APPROVAL", "FAILED"},
    "WAITING_APPROVAL": {"DRAFT", "FROZEN", "NEEDS_INPUT", "FAILED"},
    "FROZEN": {"BUILDING", "FAILED"},
    "BUILDING": {"VERIFYING", "NEEDS_INPUT", "FAILED"},
    "VERIFYING": {"VERIFIED", "NEEDS_INPUT", "FAILED"},
    "VERIFIED": {"RELEASED", "FAILED"},
    "RELEASED": set(), "FAILED": set(),
}
TASK_TRANSITIONS: dict[str, set[str]] = {
    "QUEUED": {"LEASED", "CANCEL_REQUESTED"},
    "LEASED": {"RUNNING", "LOST", "CANCEL_REQUESTED", "FAILED"},
    "RUNNING": {"SUCCEEDED", "FAILED", "WAITING_INPUT", "LOST", "CANCEL_REQUESTED"},
    "WAITING_INPUT": {"QUEUED", "CANCEL_REQUESTED"},
    "CANCEL_REQUESTED": {"CANCELLED", "LOST"},
    "LOST": {"QUEUED", "CANCEL_REQUESTED", "CANCELLED"},
    "SUCCEEDED": set(), "FAILED": set(), "CANCELLED": set(),
}


def transition_revision(current: str, target: str, *, approved: bool = False,
                        blocking_evidence: Iterable[Mapping[str, object]] = (),
                        release_approved: bool = False) -> str:
    if target not in REVISION_TRANSITIONS.get(current, set()):
        raise StateError(f"INVALID_REVISION_TRANSITION: {current} -> {target}")
    if target == "FROZEN" and not approved:
        raise StateError("APPROVAL_REQUIRED")
    if target == "VERIFIED":
        evidence = list(blocking_evidence)
        if not evidence or not all(item.get("status") == "PASS" and item.get("valid") is True and item.get("independent") is True for item in evidence):
            raise StateError("VALID_INDEPENDENT_EVIDENCE_REQUIRED")
    if target == "RELEASED" and not release_approved:
        raise StateError("RELEASE_APPROVAL_REQUIRED")
    return target


def transition_task(current: str, target: str, *, process_stopped: bool = False,
                    reconciled: bool = False, safe_to_retry: bool = False,
                    blocking_questions_resolved: bool = False) -> str:
    if target not in TASK_TRANSITIONS.get(current, set()):
        raise StateError(f"INVALID_TASK_TRANSITION: {current} -> {target}")
    if target == "CANCELLED" and not process_stopped:
        raise StateError("PROCESS_STOP_UNCONFIRMED")
    # LOST 表示租约未知，不能由超时推断外部副作用没有发生。
    if current == "LOST" and target == "QUEUED" and not (reconciled and safe_to_retry):
        raise StateError("RECONCILIATION_REQUIRED")
    if current == "WAITING_INPUT" and target == "QUEUED" and not blocking_questions_resolved:
        raise StateError("BLOCKING_QUESTIONS_UNRESOLVED")
    return target
