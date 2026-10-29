"""持久化 mock 设备；时钟仅由显式输入推进，绝不合成硬件证据。"""

import struct
from typing import Any

from sqlalchemy.orm import Session

from packages.contracts.validation import reference_json, validate_payload
from products.analyzer.capture import CaptureEngine, CaptureState
from products.analyzer.protocol import Frame, decode_frame, encode_frame

from ..config import Settings
from ..errors import ApiError
from ..models import ResourceRecord, uid, utcnow
from .artifacts import artifact_json, store_artifact
from .common import canonical, event, get_resource, history, iso

ACTIONS = dict(zip(("get_caps", "set_config", "arm", "read_capture", "status", "abort"), range(1, 7)))
ERRORS = reference_json("protocol/errors.json")


def initialize_device(db: Session, settings: Settings, record: ResourceRecord) -> None:
    revision = get_resource(db, "revisions", record.payload["revision_id"], record.project_id)
    artifact = get_resource(db, "artifacts", revision.payload["contract_artifact_id"], record.project_id)
    contract = artifact_json(settings, artifact)
    validate_payload("SystemContract", contract)
    if (contract["project_id"] != record.project_id or contract["revision_id"] != revision.id
            or contract["sample_bytes"] * 8 != contract["channels"]
            or contract["wire_protocol"] != "capture/1.0"):
        raise ApiError(422, "REFERENCE_SCOPE_MISMATCH", "设备契约的项目、版本或位宽不一致")
    # 绑定建立时的不可变契约，后续修订不能悄悄改变正在采集的设备。
    record.internal = {"engine": CaptureEngine(contract["channels"], contract["clock_hz"]).snapshot(),
                       "contract_ref": artifact.payload, "capture_record_id": None,
                       "input_events": []}


def load_engine(record: ResourceRecord) -> CaptureEngine:
    if record.state != "OPEN":
        raise ApiError(409, "SESSION_CLOSED", "采集会话已关闭")
    if record.payload["mode"] != "mock":
        raise ApiError(409, "CAPTURE_SOURCE_REQUIRED", "此入口仅支持明确标记的 mock 模型")
    if "engine" not in record.internal:
        # 旧会话没有建立时的契约快照，不能用当前草案猜测其历史身份。
        raise ApiError(409, "SESSION_SNAPSHOT_REQUIRED", "旧会话缺少快照，请建立新会话")
    try:
        return CaptureEngine.from_snapshot(record.internal["engine"])
    except (ValueError, KeyError, TypeError) as exc:
        raise ApiError(409, "DEVICE_SNAPSHOT_INVALID", "设备快照损坏，已阻止执行") from exc


def decode_reply(action: str, frame: Frame) -> dict[str, Any]:
    payload = frame.payload
    if action == "get_caps":
        values = struct.unpack("<BBIHHI", payload)
        return dict(zip(("channels", "sample_bytes", "clock_hz", "max_depth", "max_payload_bytes", "feature_bits"), values))
    if action == "status":
        state, channels, trigger, overflow, cid, count, error, _ = struct.unpack("<BBBBIIHH", payload)
        return {"state": CaptureState(state).name, "channels": channels, "trigger_mode": trigger,
                "overflow": bool(overflow), "capture_id": cid, "sample_count": count, "error_code": error}
    if action in ("arm", "abort"):
        return {"capture_id": struct.unpack("<I", payload)[0]}
    if action == "set_config":
        if payload:
            raise ValueError("unexpected config reply")
        return {}
    cid, offset, count, width, reserved = struct.unpack("<IIHBB", payload[:12])
    if reserved or width not in (1, 2) or len(payload[12:]) != count * width:
        raise ValueError("invalid capture chunk")
    return {"capture_id": cid, "offset_samples": offset, "count_samples": count,
            "sample_bytes": width, "samples_hex": payload[12:].hex()}


def execute_device(db: Session, record: ResourceRecord, body: dict[str, Any],
                   actor_id: str, request_id: str) -> tuple[dict[str, Any], ResourceRecord]:
    validate_payload("DeviceCommand", body)
    if len(body["payload_hex"]) > 2048:
        raise ApiError(422, "BAD_LENGTH", "设备负载不能超过 1024 字节")
    engine = load_engine(record)
    request = Frame(ACTIONS[body["action"]], body["sequence"], bytes.fromhex(body["payload_hex"]))
    # 使用完整编解码路径核对 CRC、序号、回复类型；模型状态和命令结果一起提交。
    wire = encode_frame(engine.handle_frame(request))
    reply = decode_frame(wire)
    if reply.sequence != request.sequence or reply.type not in (request.type | 0x80, 0xFF):
        raise ApiError(502, "DEVICE_REPLY_INVALID", "设备回复与请求不匹配")
    error, decoded = None, None
    if reply.type == 0xFF:
        original, code = struct.unpack("<BH", reply.payload)
        if original != request.type:
            raise ApiError(502, "DEVICE_REPLY_INVALID", "错误回复与请求不匹配")
        name = ERRORS.get(str(code), "DEVICE_ERROR")
        error = ApiError(409, name, name).body(request_id)
    else:
        decoded = {"action": body["action"], "data": decode_reply(body["action"], reply)}
    internal = dict(record.internal)
    if not error and body["action"] == "set_config":
        internal.update(capture_record_id=None, input_events=[])
    if not error and body["action"] == "arm" and engine.capture_id != internal["engine"]["capture_id"]:
        internal.update(capture_record_id=None, input_events=[])
    history(db, record)
    record.internal = {**internal, "engine": engine.snapshot()}
    capture_ref = None
    if not error and body["action"] == "read_capture" and internal.get("capture_record_id"):
        capture = get_resource(db, "captures", internal["capture_record_id"], record.project_id)
        capture_ref = capture.payload["samples"]
    result = {"command_id": uid(), "session_id": record.id, "action": body["action"],
              "sequence": body["sequence"], "state": "FAILED" if error else "COMPLETED",
              "reply": decoded, "error": error, "response_frame_hex": wire.hex(), "capture_ref": capture_ref}
    validate_payload("DeviceCommandResult", result)
    command = ResourceRecord(id=result["command_id"], kind="device-command-results",
                             project_id=record.project_id, state=result["state"], payload=result,
                             internal={"request": body, "mode": "mock"})
    db.add(command)
    emitted = event(db, actor_id, "device.command.completed", command,
                    payload={"request_id": request_id, "session_id": record.id, "mode": "mock"})
    return {"resource_id": command.id, "state": command.state, "run_id": None,
            "approval_id": None, "event_cursor": emitted.id}, command


def validate_segments(body: dict[str, Any], channels: int) -> list[dict[str, int]]:
    segments = body.get("segments") if isinstance(body, dict) else None
    valid = isinstance(body, dict) and set(body) == {"segments"} and isinstance(segments, list) and 1 <= len(segments) <= 256
    if not valid:
        raise ApiError(422, "INVALID_MOCK_INPUT", "需要 1 至 256 段显式 mock 输入")
    for segment in segments:
        if (not isinstance(segment, dict) or set(segment) != {"value", "cycles"}
                or type(segment["value"]) is not int or not 0 <= segment["value"] < 1 << channels
                or type(segment["cycles"]) is not int or not 1 <= segment["cycles"] <= 500000):
            raise ApiError(422, "INVALID_MOCK_INPUT", "输入位宽或时钟周期数无效")
    if sum(item["cycles"] for item in segments) > 500000:
        raise ApiError(422, "INVALID_MOCK_INPUT", "单次最多推进 500000 个系统时钟")
    return segments


def persist_capture(db: Session, settings: Settings, record: ResourceRecord,
                    engine: CaptureEngine, actor_id: str) -> str:
    identifier = uid()
    metadata = {"schema_version": "1.0", "id": identifier, "session_id": record.id,
                "project_id": record.project_id, "revision_id": record.payload["revision_id"],
                "capture_id": engine.capture_id, "mode": "mock", "channels": engine.channels,
                "sample_bytes": engine.sample_bytes, "clock_hz": engine.clock_hz,
                "divider": engine.config.divider, "sample_count": engine.sample_count,
                "contract_sha256": record.internal["contract_ref"]["sha256"], "captured_at": iso(utcnow())}
    raw = b"".join(value.to_bytes(engine.sample_bytes, "little") for value in engine.samples)
    samples = store_artifact(db, settings, record.project_id, raw, "capture.samples",
                             filename="samples.bin", provenance="mock")
    manifest = {**metadata, "samples": samples.payload, "config": engine.snapshot()["config"],
                "input_event_ids": record.internal["input_events"]}
    meta = store_artifact(db, settings, record.project_id, canonical(manifest), "capture.metadata",
                         filename="metadata.json", media_type="application/json", provenance="mock")
    payload = {**metadata, "samples": samples.payload, "metadata": meta.payload}
    validate_payload("CaptureRecord", payload)
    capture = ResourceRecord(id=identifier, kind="captures", project_id=record.project_id,
                             state="IMMUTABLE", payload=payload, internal={"session_id": record.id})
    db.add(capture)
    event(db, actor_id, "capture.completed", capture, payload={"session_id": record.id, "mode": "mock"})
    return identifier


def advance_mock(db: Session, settings: Settings, record: ResourceRecord, body: dict[str, Any],
                 actor_id: str, request_id: str) -> dict[str, Any]:
    engine = load_engine(record)
    segments = validate_segments(body, engine.channels)
    if engine.state not in (CaptureState.ARMED, CaptureState.CAPTURING):
        raise ApiError(409, "CAPTURE_NOT_ACTIVE", "请先 ARM，再注入显式 mock 输入")
    for segment in segments:
        for _ in range(segment["cycles"]):
            engine.step(segment["value"])
    history(db, record)
    emitted = event(db, actor_id, "device.mock_inputs", record,
                    payload={"request_id": request_id, "segments": segments, "mode": "mock"})
    record.internal = {**record.internal, "engine": engine.snapshot(),
                       "input_events": [*record.internal["input_events"], emitted.id]}
    if engine.state == CaptureState.DONE and not record.internal.get("capture_record_id"):
        identifier = persist_capture(db, settings, record, engine, actor_id)
        record.internal = {**record.internal, "capture_record_id": identifier}
    return {"mode": "mock", "state": engine.state.name, "sample_count": engine.sample_count,
            "capture_record_id": record.internal.get("capture_record_id")}
