"""Wire command payloads, separate from the clocked acquisition model."""
import struct

from .capture import CaptureConfig, CaptureEngine, CaptureError
from .protocol import Frame

LENGTHS = {1: 0, 2: 10, 3: 0, 4: 10, 5: 0, 6: 0}


def dispatch(engine: CaptureEngine, request: Frame) -> bytes:
    if (request.major, request.minor) != (1, 0):
        raise CaptureError(2, "UNSUPPORTED_VERSION")
    if request.flags or request.type not in LENGTHS:
        raise CaptureError(3, "UNKNOWN_TYPE")
    if len(request.payload) != LENGTHS[request.type]:
        raise CaptureError(1, "BAD_LENGTH")
    if request.type == 1:
        return struct.pack("<BBIHHI", engine.channels, engine.sample_bytes, engine.clock_hz, 4096, 1024, 3)
    if request.type == 2:
        mode, channel, divider, mask, depth = struct.unpack("<BBHHI", request.payload)
        engine.configure(CaptureConfig(mode, channel, divider, mask, depth))
        return b""
    if request.type == 3:
        return struct.pack("<I", engine.arm())
    if request.type == 4:
        capture_id, offset, count = struct.unpack("<IIH", request.payload)
        samples = engine.read_capture(capture_id, offset, count)
        return struct.pack("<IIHBB", capture_id, offset, count, engine.sample_bytes, 0) + samples
    if request.type == 5:
        return struct.pack("<BBBBIIHH", int(engine.state), engine.channels, engine.config.trigger_mode,
                           int(engine.overflow), engine.capture_id, engine.sample_count, engine.error_code, 0)
    return struct.pack("<I", engine.abort())
