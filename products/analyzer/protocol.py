"""Bounded capture/1.0 framing; semantic checks belong to the device."""
import struct
import time
from dataclasses import dataclass

SYNC = b"\xa5\x5a"
MAX_PAYLOAD = 1024
HEADER = struct.Struct("<BBBBHH")


class FrameError(ValueError):
    """Untrusted physical frame; do not produce an ERROR reply."""


@dataclass(frozen=True)
class Frame:
    type: int
    sequence: int
    payload: bytes = b""
    major: int = 1
    minor: int = 0
    flags: int = 0


def crc16(data: bytes) -> int:
    """CRC16 CCITT FALSE, unreflected, init FFFF, xorout 0."""
    crc = 0xFFFF
    for byte in data:
        crc ^= byte << 8
        for _ in range(8):
            # 只保留 16 位，多项式不包含隐含的 x^16 项。
            crc = ((crc << 1) ^ (0x1021 if crc & 0x8000 else 0)) & 0xFFFF
    return crc


def encode_frame(frame: Frame) -> bytes:
    if not isinstance(frame.payload, bytes) or len(frame.payload) > MAX_PAYLOAD:
        raise FrameError("payload must be bytes with length <= 1024")
    try:
        header = HEADER.pack(frame.major, frame.minor, frame.type, frame.flags, frame.sequence, len(frame.payload))
    except struct.error as exc:
        raise FrameError("header field outside wire range") from exc
    covered = header + frame.payload
    return SYNC + covered + struct.pack("<H", crc16(covered))


def decode_frame(raw: bytes) -> Frame:
    if len(raw) < 12 or raw[:2] != SYNC:
        raise FrameError("short frame or missing sync")
    major, minor, message_type, flags, sequence, length = HEADER.unpack(raw[2:10])
    if length > MAX_PAYLOAD or len(raw) != length + 12:
        raise FrameError("invalid frame length")
    if crc16(raw[2:-2]) != int.from_bytes(raw[-2:], "little"):
        raise FrameError("CRC mismatch")
    return Frame(message_type, sequence, raw[10:-2], major, minor, flags)


class FrameDecoder:
    """Streaming parser; legal payload sync bytes never start a nested frame."""

    def __init__(self, inter_byte_timeout: float = 0.1) -> None:
        if inter_byte_timeout <= 0:
            raise ValueError("inter_byte_timeout must be positive")
        self.inter_byte_timeout = inter_byte_timeout
        self._buffer = bytearray()
        self._last_byte_at: float | None = None

    @property
    def buffered_bytes(self) -> int:
        return len(self._buffer)

    def expire(self, now: float | None = None) -> None:
        current = time.monotonic() if now is None else now
        if self._last_byte_at is not None and current - self._last_byte_at > self.inter_byte_timeout:
            self._buffer.clear()
            self._last_byte_at = None

    def feed(self, data: bytes, now: float | None = None) -> list[Frame]:
        current = time.monotonic() if now is None else now
        self.expire(current)
        frames: list[Frame] = []
        # 逐字节加入使持久缓冲始终有界，不随一次 feed 的噪声大小增长。
        for byte in data:
            self._buffer.append(byte)
            self._last_byte_at = current
            self._drain(frames)
        return frames

    def _drain(self, frames: list[Frame]) -> None:
        while self._buffer:
            start = self._buffer.find(SYNC)
            if start < 0:
                self._buffer[:] = b"\xa5" if self._buffer[-1] == 0xA5 else b""
                return
            if start:
                del self._buffer[:start]
            if len(self._buffer) < 10:
                return
            length = int.from_bytes(self._buffer[8:10], "little")
            if length > MAX_PAYLOAD:
                del self._buffer[0]  # 非法候选从起点后一字节重同步。
                continue
            size = length + 12
            if len(self._buffer) < size:
                return  # 已有合法长度，先收满再判 CRC，不抢认负载中的同步字。
            try:
                frame = decode_frame(bytes(self._buffer[:size]))
            except FrameError:
                del self._buffer[0]
                continue
            frames.append(frame)
            del self._buffer[:size]
