"""Ed25519 signing over RFC8785 JCS bytes, never Python json.dumps sorting."""
import base64
from copy import deepcopy
from typing import Any, Mapping

import rfc8785
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from .validation import validate_evidence


def canonical_bytes(value: Any) -> bytes:
    try:
        return rfc8785.dumps(value)
    except (rfc8785.CanonicalizationError, TypeError, ValueError) as exc:
        raise ValueError("value cannot be represented by RFC8785 JCS") from exc


def unsigned_bytes(evidence: Mapping[str, Any]) -> bytes:
    # 移除整个 auth_tag，不只移除 signature；其余所有字段都受签名保护。
    return canonical_bytes({key: value for key, value in evidence.items() if key != "auth_tag"})


def sign_evidence(evidence: Mapping[str, Any], private_key: Ed25519PrivateKey, key_id: str) -> dict[str, Any]:
    if not key_id:
        raise ValueError("key_id cannot be empty")
    signed = deepcopy(dict(evidence))
    signature = private_key.sign(unsigned_bytes(signed))
    signed["auth_tag"] = {"algorithm": "Ed25519", "key_id": key_id,
                          "signature": base64.urlsafe_b64encode(signature).rstrip(b"=").decode("ascii")}
    validate_evidence(signed)
    return signed
