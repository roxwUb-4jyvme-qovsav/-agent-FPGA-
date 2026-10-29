"""Explicit versioned JSON state, never pickle or executable deserialization."""
from dataclasses import asdict
from typing import Any, Mapping

from .capture import CaptureConfig, CaptureEngine, CaptureState
from .protocol import Frame, decode_frame, encode_frame

FIELDS = {"schema_version", "mode", "channels", "clock_hz", "config", "state", "capture_id",
          "error_code", "overflow", "valid_capture", "samples", "phase", "sync1", "sync2",
          "previous", "cached_request", "cached_response"}


def dump_snapshot(engine: CaptureEngine) -> dict[str, Any]:
    # 只列出协议/时序需要的事实，绝不序列化任意对象属性或文件句柄。
    return {"schema_version": "1.0", "mode": "mock", "channels": engine.channels,
            "clock_hz": engine.clock_hz, "config": asdict(engine.config), "state": int(engine.state),
            "capture_id": engine.capture_id, "error_code": engine.error_code, "overflow": engine.overflow,
            "valid_capture": engine._valid_capture, "samples": list(engine.samples), "phase": engine._phase,
            "sync1": engine._sync1, "sync2": engine._sync2, "previous": engine._previous,
            "cached_request": encode_frame(engine._cached_request).hex() if engine._cached_request else None,
            "cached_response": encode_frame(engine._cached_response).hex() if engine._cached_response else None}


def _integer(value: Any, minimum: int, maximum: int, name: str) -> int:
    if type(value) is not int or not minimum <= value <= maximum:
        raise ValueError(f"invalid snapshot {name}")
    return value


def _frame(value: Any) -> Frame | None:
    if value is None:
        return None
    if not isinstance(value, str) or len(value) > 2072:
        raise ValueError("invalid snapshot cached frame")
    return decode_frame(bytes.fromhex(value))


def restore_snapshot(snapshot: Mapping[str, Any]) -> CaptureEngine:
    if not isinstance(snapshot, Mapping) or set(snapshot) != FIELDS:
        raise ValueError("snapshot fields do not match schema")
    if snapshot["schema_version"] != "1.0" or snapshot["mode"] != "mock":
        raise ValueError("unsupported snapshot version/mode")
    channels = _integer(snapshot["channels"], 8, 16, "channels")
    engine = CaptureEngine(channels, _integer(snapshot["clock_hz"], 1, 0xFFFFFFFF, "clock_hz"))
    config = snapshot["config"]
    if not isinstance(config, dict) or set(config) != set(asdict(engine.config)):
        raise ValueError("invalid snapshot configuration fields")
    engine.configure(CaptureConfig(**config))
    state = CaptureState(_integer(snapshot["state"], 0, 5, "state"))
    capture_id = _integer(snapshot["capture_id"], 0, 0xFFFFFFFF, "capture_id")
    error = _integer(snapshot["error_code"], 0, 10, "error_code")
    for name in ("overflow", "valid_capture"):
        if type(snapshot[name]) is not bool:
            raise ValueError(f"invalid snapshot {name}")
    samples = snapshot["samples"]
    if not isinstance(samples, list) or len(samples) > engine.config.depth:
        raise ValueError("invalid snapshot samples")
    for sample in samples:
        _integer(sample, 0, (1 << channels) - 1, "sample")
        if sample & ~engine.config.channel_mask:
            raise ValueError("snapshot sample contains disabled channel")
    valid = snapshot["valid_capture"]
    # 检查跨字段状态，不能把残缺数据恢复为 DONE 或把无效 ID 恢复为完整采集。
    if state == CaptureState.IDLE and (samples or valid):
        raise ValueError("IDLE snapshot contains active capture")
    if state in (CaptureState.ARMED, CaptureState.CAPTURING, CaptureState.DONE, CaptureState.ABORTED) and (not valid or capture_id == 0):
        raise ValueError("active snapshot lacks capture identity")
    if state == CaptureState.ARMED and samples:
        raise ValueError("ARMED snapshot already contains samples")
    if state == CaptureState.CAPTURING and not 0 < len(samples) < engine.config.depth:
        raise ValueError("CAPTURING snapshot sample count invalid")
    if state == CaptureState.DONE and len(samples) != engine.config.depth:
        raise ValueError("DONE snapshot is incomplete")
    if state == CaptureState.ABORTED and len(samples) >= engine.config.depth:
        raise ValueError("ABORTED snapshot cannot contain complete data")
    request, response = _frame(snapshot["cached_request"]), _frame(snapshot["cached_response"])
    if (request is None) != (response is None):
        raise ValueError("snapshot command cache is incomplete")
    if request is not None and response is not None:
        if request.sequence != response.sequence or response.type not in (0xFF, request.type | 0x80):
            raise ValueError("snapshot cached response does not match request")
    engine.state, engine.capture_id, engine.error_code = state, capture_id, error
    engine.overflow, engine._valid_capture = snapshot["overflow"], valid
    engine._samples = list(samples)
    engine._phase = _integer(snapshot["phase"], 0, engine.config.divider - 1, "phase")
    engine._sync1 = _integer(snapshot["sync1"], 0, (1 << channels) - 1, "sync1")
    engine._sync2 = _integer(snapshot["sync2"], 0, (1 << channels) - 1, "sync2")
    engine._previous = _integer(snapshot["previous"], 0, (1 << channels) - 1, "previous")
    engine._cached_request, engine._cached_response = request, response
    return engine
