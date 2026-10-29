"""Clock-edge reference model. This is a mock, never RTL/hardware evidence."""
from dataclasses import dataclass
from enum import IntEnum
from typing import NoReturn

from .protocol import Frame, encode_frame


class CaptureState(IntEnum):
    IDLE = 0
    ARMED = 1
    CAPTURING = 2
    DONE = 3
    ABORTED = 4
    ERROR = 5


class CaptureError(ValueError):
    def __init__(self, code: int, message: str) -> None:
        super().__init__(message)
        self.code = code


@dataclass(frozen=True)
class CaptureConfig:
    trigger_mode: int = 0
    trigger_channel: int = 0
    divider: int = 10
    channel_mask: int = 0xFF
    depth: int = 4096


class CaptureEngine:
    mode = "mock"

    def __init__(self, channels: int = 8, clock_hz: int = 100_000_000) -> None:
        if channels not in (8, 16) or clock_hz <= 0:
            raise ValueError("channels must be 8/16 and clock_hz positive")
        self.channels = channels
        self.clock_hz = clock_hz
        self.sample_bytes = channels // 8
        self.reset()

    def reset(self) -> None:
        self.config = CaptureConfig(channel_mask=(1 << self.channels) - 1)
        self.state = CaptureState.IDLE
        self.capture_id = 0
        self.error_code = 0
        self.overflow = False
        self._valid_capture = False
        self._samples: list[int] = []
        self._phase = 0
        self._sync1 = self._sync2 = self._previous = 0
        self._cached_request: Frame | None = None
        self._cached_response: Frame | None = None

    @property
    def sample_count(self) -> int:
        return len(self._samples)

    @property
    def samples(self) -> tuple[int, ...]:
        return tuple(self._samples)

    def _fail(self, code: int, message: str) -> NoReturn:
        self.error_code = code
        raise CaptureError(code, message)

    def configure(self, config: CaptureConfig) -> None:
        values = (config.trigger_mode, config.trigger_channel, config.divider, config.channel_mask, config.depth)
        if any(type(value) is not int for value in values):
            self._fail(4, "configuration fields must be integers")
        valid = (config.trigger_mode in (0, 1) and 0 <= config.trigger_channel < self.channels
                 and config.divider in (10, 100) and 0 < config.channel_mask < (1 << self.channels)
                 and 16 <= config.depth <= 4096 and config.depth & (config.depth - 1) == 0)
        if not valid or (config.trigger_mode == 1 and not config.channel_mask & (1 << config.trigger_channel)):
            self._fail(4, "INVALID_CONFIG")
        # 参数校验先于状态准入；全部成功后一次提交，不能部分覆盖旧配置。
        if self.state in (CaptureState.ARMED, CaptureState.CAPTURING):
            self._fail(6, "BUSY")
        self.config = config
        self.state = CaptureState.IDLE
        self._samples.clear()
        self._valid_capture = False
        self.error_code = 0
        self.overflow = False

    def arm(self) -> int:
        if self.state in (CaptureState.ARMED, CaptureState.CAPTURING):
            self._fail(6, "BUSY")
        self.capture_id = (self.capture_id + 1) & 0xFFFFFFFF
        if self.capture_id == 0:
            self.capture_id = 1
        self._samples.clear()
        self._phase = 0
        self._previous = self._sync2
        self._valid_capture = True
        self.state = CaptureState.ARMED
        return self.capture_id

    def abort(self) -> int:
        if self.state in (CaptureState.ARMED, CaptureState.CAPTURING):
            self.state = CaptureState.ABORTED
        return self.capture_id

    def step(self, raw_inputs: int, *, arm: bool = False, abort: bool = False, reset: bool = False) -> bool:
        """Advance one system rising edge; return whether an active sample tick occurred."""
        if reset:
            self.reset()
            return False
        if type(raw_inputs) is not int or not 0 <= raw_inputs < (1 << self.channels):
            raise ValueError("raw_inputs outside channel width")
        vector = self._sync2
        # 模拟非阻塞寄存器：核心消费沿前的第二级值，更新在这个沿后才生效。
        self._sync2, self._sync1 = self._sync1, raw_inputs
        if abort:
            self.abort()
            return False
        if arm:
            # ARM 的前值也必须使用沿前向量，而非刚更新后的同步寄存器。
            self.arm()
            self._previous = vector
            return False
        if self.state not in (CaptureState.ARMED, CaptureState.CAPTURING):
            return False
        self._phase += 1
        if self._phase < self.config.divider:
            return False
        self._phase = 0
        bit = 1 << self.config.trigger_channel
        triggered = self.config.trigger_mode == 0 or (not self._previous & bit and bool(vector & bit))
        self._previous = vector
        if self.state == CaptureState.ARMED and not triggered:
            return True
        self._samples.append(vector & self.config.channel_mask)
        self.state = CaptureState.DONE if self.sample_count == self.config.depth else CaptureState.CAPTURING
        return True

    def read_capture(self, capture_id: int, offset: int, count: int) -> bytes:
        if not self._valid_capture or capture_id != self.capture_id:
            self._fail(5, "STALE_CAPTURE")
        if self.state == CaptureState.ABORTED:
            self._fail(10, "CAPTURE_ABORTED")
        if self.state != CaptureState.DONE:
            self._fail(7, "NOT_READY")
        capacity = (1024 - 12) // self.sample_bytes
        if type(offset) is not int or type(count) is not int or offset < 0 or not 0 < count <= capacity or offset + count > self.sample_count:
            self._fail(8, "RANGE")
        return b"".join(sample.to_bytes(self.sample_bytes, "little") for sample in self._samples[offset:offset + count])

    def handle_frame(self, request: Frame) -> Frame:
        """Process a CRC-validated frame; raw UART must pass FrameDecoder first."""
        from .commands import dispatch

        encode_frame(request)  # 拒绝不可能来自合法物理帧的字段和超长负载。
        if self._cached_request is not None and request.sequence == self._cached_request.sequence:
            if request == self._cached_request:
                assert self._cached_response is not None
                return self._cached_response
            self.error_code = 9
            return Frame(0xFF, request.sequence, bytes([request.type, 9, 0]))
        try:
            response = Frame(request.type | 0x80, request.sequence, dispatch(self, request))
        except CaptureError as exc:
            self.error_code = exc.code
            response = Frame(0xFF, request.sequence, bytes([request.type]) + exc.code.to_bytes(2, "little"))
        # 可信新请求的语义错误也进入缓存；序号冲突不覆盖上一次请求。
        self._cached_request, self._cached_response = request, response
        return response

    def snapshot(self) -> dict[str, object]:
        from .snapshot import dump_snapshot

        return dump_snapshot(self)

    @classmethod
    def from_snapshot(cls, snapshot: dict[str, object]) -> "CaptureEngine":
        from .snapshot import restore_snapshot

        return restore_snapshot(snapshot)
