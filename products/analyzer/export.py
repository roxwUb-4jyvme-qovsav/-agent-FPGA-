"""Lossless exports and an offline, sample-based UART 8N1 decoder."""
import hashlib
import json
from dataclasses import dataclass
from decimal import Decimal
from fractions import Fraction
from typing import Any, Iterable, Mapping


def _samples(samples: Iterable[int], channels: int) -> tuple[int, ...]:
    if channels not in (8, 16):
        raise ValueError("channels must be 8 or 16")
    values = tuple(samples)
    if any(type(value) is not int or not 0 <= value < (1 << channels) for value in values):
        raise ValueError("sample outside channel width")
    return values


def csv_text(samples: Iterable[int], channels: int, divider: int, clock_hz: int) -> str:
    values = _samples(samples, channels)
    if divider not in (10, 100) or clock_hz <= 0:
        raise ValueError("invalid sampling timebase")
    rows = ["time_s," + ",".join(f"ch{channel}" for channel in range(channels))]
    for index, value in enumerate(values):
        # 从整数样本号独立计算时间，避免浮点累加导致长记录漂移。
        seconds = format(Decimal(index * divider) / Decimal(clock_hz), "f")
        if "." in seconds:
            seconds = seconds.rstrip("0").rstrip(".")
        rows.append(seconds + "," + ",".join(str((value >> bit) & 1) for bit in range(channels)))
    return "\n".join(rows) + "\n"


def export_bundle(samples: Iterable[int], metadata: Mapping[str, Any]) -> dict[str, bytes]:
    """Return archive members; caller owns ACL, storage and immutable CaptureRecord."""
    required = {"protocol", "contract_sha256", "revision_id", "mode", "channels", "divider", "clock_hz", "capture_id", "captured_at"}
    if not required <= metadata.keys():
        raise ValueError("missing capture metadata")
    if metadata["protocol"] != "capture/1.0" or metadata["mode"] not in ("mock", "replay", "rtl_simulation", "hardware"):
        raise ValueError("invalid protocol or mode")
    channels = metadata["channels"]
    values = _samples(samples, channels)
    width = channels // 8
    binary = b"".join(sample.to_bytes(width, "little") for sample in values)
    final = dict(metadata)
    final.update(sample_count=len(values), sample_bytes=width, data_sha256=hashlib.sha256(binary).hexdigest())
    members = {"samples.bin": binary, "metadata.json": json.dumps(final, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False).encode("utf-8")}
    members["SHA256SUMS"] = "".join(f"{hashlib.sha256(data).hexdigest()}  {name}\n" for name, data in sorted(members.items())).encode("ascii")
    return members


@dataclass(frozen=True)
class UARTFrame:
    byte: int | None
    start_sample: int
    end_sample: int
    frame_error: bool
    uncertain: bool


def decode_uart(samples: Iterable[int], channel: int, sample_rate: int | float, baud: int = 115200) -> list[UARTFrame]:
    """Decode idle-high 8N1; incomplete words retain uncertainty, never zero-fill."""
    if type(channel) is not int or not 0 <= channel < 16 or baud <= 0 or sample_rate <= 0:
        raise ValueError("invalid UART configuration")
    period = Fraction(str(sample_rate)) / baud
    if period < 8:
        raise ValueError("采样不足: UART requires sample_rate >= 8 * baud")
    values = tuple(samples)
    if any(type(value) is not int or not 0 <= value <= 0xFFFF for value in values):
        raise ValueError("invalid logic sample")
    bits = tuple((value >> channel) & 1 for value in values)
    result: list[UARTFrame] = []
    index = 1
    while index < len(bits):
        if bits[index - 1] != 1 or bits[index] != 0:
            index += 1
            continue
        start = index
        start_center = start + round(period / 2)
        if start_center >= len(bits):
            result.append(UARTFrame(None, start, len(bits) - 1, False, True))
            break
        if bits[start_center] != 0:
            index += 1  # 起始低电平在中心已消失，作为毛刺丢弃。
            continue
        centers = [start + round(period * Fraction(3 + 2 * bit, 2)) for bit in range(9)]
        end = start + int(period * 10) - 1
        if centers[-1] >= len(bits) or end >= len(bits):
            result.append(UARTFrame(None, start, len(bits) - 1, False, True))
            break
        byte = sum(bits[center] << bit for bit, center in enumerate(centers[:8]))
        result.append(UARTFrame(byte, start, end, bits[centers[8]] != 1, False))
        index = end + 1
    return result
