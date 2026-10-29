"""Approved public keys, immutable evidence and separately evaluated validity."""
import base64
import json
from copy import deepcopy
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Iterable, Mapping

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey

from .signing import canonical_bytes, unsigned_bytes
from .validation import timestamp, validate_evidence


class EvidenceError(ValueError):
    pass


@dataclass(frozen=True)
class ApprovedKey:
    issuer_id: str
    key_id: str
    public_key: Ed25519PublicKey
    revoked_at: str | None = None


def _mismatches(evidence: Mapping[str, Any], expected: Mapping[str, Any]) -> list[str]:
    mismatches = []
    for name, value in expected.items():
        actual = evidence.get("report", {}).get("sha256") if name == "report_sha256" else evidence.get(name)
        if actual != value:
            mismatches.append(name)
    return mismatches


def verify_evidence(evidence: Mapping[str, Any], keys: Iterable[ApprovedKey], expected: Mapping[str, Any] | None = None) -> None:
    try:
        validate_evidence(evidence)
        tag = evidence["auth_tag"]
        matches = [key for key in keys if key.issuer_id == evidence["issuer_id"] and key.key_id == tag["key_id"]]
        if len(matches) != 1:
            raise EvidenceError("issuer/key is not uniquely approved")
        key = matches[0]
        # 撤销前历史运行仍可核验；撤销后完成的新运行不能继续使用此密钥。
        if key.revoked_at is not None and timestamp(evidence["finished_at"]) >= timestamp(key.revoked_at):
            raise EvidenceError("key revoked before evidence was completed")
        signature = base64.b64decode(tag["signature"] + "==", altchars=b"-_", validate=True)
        if len(signature) != 64 or base64.urlsafe_b64encode(signature).rstrip(b"=").decode() != tag["signature"]:
            raise EvidenceError("signature is not canonical unpadded base64url")
        key.public_key.verify(signature, unsigned_bytes(evidence))
        mismatch = _mismatches(evidence, expected or {})
        if mismatch:
            raise EvidenceError("evidence binding mismatch: " + ", ".join(mismatch))
    except (InvalidSignature, ValueError, KeyError, TypeError) as exc:
        if isinstance(exc, EvidenceError):
            raise
        raise EvidenceError(f"evidence verification failed: {exc}") from exc


def evaluate_validity(evidence: Mapping[str, Any], keys: Iterable[ApprovedKey], *, current_revision_id: str,
                      expected: Mapping[str, Any] | None = None, reuse_approved: bool = False,
                      evaluated_at: str | None = None) -> dict[str, Any]:
    """Project current validity; caller must persist approved reuse mappings separately."""
    valid = True
    reason: str | None = None
    status = str(evidence.get("status", "INDETERMINATE"))
    try:
        verify_evidence(evidence, keys)
    except EvidenceError as exc:
        valid, status, reason = False, "INDETERMINATE", str(exc)
    if valid:
        mismatch = _mismatches(evidence, expected or {})
        if evidence["revision_id"] != current_revision_id:
            # 跨版本复用不能只依赖一个布尔开关，必须显式核对完整依赖指纹。
            required = {"input_sha256", "suite_sha256", "environment_sha256", "tool_version", "template_id", "report_sha256"}
            if not reuse_approved or not required <= (expected or {}).keys():
                mismatch.append("revision_id/reuse_dependencies")
        if mismatch:
            status, reason = "STALE", "dependency changed: " + ", ".join(mismatch)
        elif status == "PASS" and evidence["mode"] in ("mock", "replay"):
            status, reason = "INDETERMINATE", "mock/replay cannot prove independent acceptance"
    return {"schema_version": "1.0", "evidence_id": evidence["id"], "current_revision_id": current_revision_id,
            "effective_status": status, "stale_reason": reason, "signature_valid": valid,
            "evaluated_at": evaluated_at or datetime.now(timezone.utc).isoformat()}


class EvidenceRegistry:
    """Process-local boundary helper; API database supplies durable uniqueness/ACL."""

    def __init__(self) -> None:
        self._raw: dict[str, bytes] = {}

    def register(self, evidence: Mapping[str, Any], keys: Iterable[ApprovedKey], expected: Mapping[str, Any] | None = None) -> None:
        verify_evidence(evidence, keys, expected)
        encoded = canonical_bytes(evidence)
        identity = evidence["id"]
        if identity in self._raw and self._raw[identity] != encoded:
            raise EvidenceError("same evidence id has different content")
        self._raw[identity] = encoded

    def get(self, evidence_id: str) -> dict[str, Any]:
        return deepcopy(json.loads(self._raw[evidence_id]))
