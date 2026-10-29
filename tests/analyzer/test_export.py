import hashlib
import json

import pytest


def test_csv_uses_sample_zero_and_uncompressed_channel_order():
    from products.analyzer.export import csv_text

    rows = csv_text([1, 0x8000], 16, 10, 100_000_000).splitlines()
    assert rows[0] == "time_s," + ",".join(f"ch{i}" for i in range(16))
    assert rows[1] == "0,1," + ",".join(["0"] * 15)
    assert rows[2] == "0.0000001," + ",".join(["0"] * 15 + ["1"])


def test_binary_bundle_metadata_and_sha256_are_final_bytes():
    from products.analyzer.export import export_bundle

    metadata = {"protocol": "capture/1.0", "contract_sha256": "a" * 64,
                "revision_id": "rev", "mode": "mock", "channels": 16,
                "divider": 10, "clock_hz": 100_000_000, "capture_id": 7,
                "captured_at": "2026-10-29T00:00:00Z"}
    bundle = export_bundle([0x8001, 0xFFFF], metadata)
    assert bundle["samples.bin"] == b"\x01\x80\xff\xff"
    parsed = json.loads(bundle["metadata.json"])
    assert parsed["sample_count"] == 2 and parsed["sample_bytes"] == 2
    assert parsed["data_sha256"] == hashlib.sha256(b"\x01\x80\xff\xff").hexdigest()
    for line in bundle["SHA256SUMS"].decode().splitlines():
        digest, name = line.split("  ")
        assert hashlib.sha256(bundle[name]).hexdigest() == digest
    assert "data_sha256" not in metadata


def waveform(byte, stop=1):
    # 独立已知波形：每位 16 样本，前后空闲高，LSB 先行。
    bits = [1, 0] + [(byte >> bit) & 1 for bit in range(8)] + [stop, 1]
    return [bit for bit in bits for _ in range(16)]


def test_offline_uart_8n1_center_sampling_frame_error_and_rate_floor():
    from products.analyzer.export import decode_uart

    decoded = decode_uart(waveform(0xA5), channel=0, sample_rate=1_843_200)
    assert len(decoded) == 1
    assert decoded[0].byte == 0xA5 and decoded[0].start_sample == 16
    assert decoded[0].end_sample == 175
    assert not decoded[0].frame_error and not decoded[0].uncertain
    assert decode_uart(waveform(0x33, stop=0), channel=0, sample_rate=1_843_200)[0].frame_error
    assert decode_uart(waveform(0x55)[:100], channel=0, sample_rate=1_843_200)[0].uncertain
    with pytest.raises(ValueError, match="采样不足"):
        decode_uart(waveform(1), channel=0, sample_rate=500_000)
