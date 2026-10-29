"""运行上下文以不可变 JSON 字节和 SHA256 固定。"""
import hashlib
import json
from dataclasses import dataclass
from typing import Any, Mapping


@dataclass(frozen=True)
class ContextSnapshot:
    payload_json: str
    sha256: str

    def to_dict(self) -> dict[str, Any]:
        return json.loads(self.payload_json)


def freeze_snapshot(payload: Mapping[str, Any]) -> ContextSnapshot:
    # 禁止 NaN，避免不同 JSON 实现对同一快照产生不同含义。
    encoded = json.dumps(dict(payload), ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)
    return ContextSnapshot(encoded, hashlib.sha256(encoded.encode("utf-8")).hexdigest())
