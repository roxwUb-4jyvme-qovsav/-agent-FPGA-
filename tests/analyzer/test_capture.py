import struct

import pytest


@pytest.mark.parametrize("channels,mask,raw,stored", [(8, 0x05, 0xFF, 5), (16, 0x8001, 0xFFFF, 0x8001)])
def test_software_tick_depth_mask_and_frozen_result(channels, mask, raw, stored):
    from products.analyzer.capture import CaptureConfig, CaptureEngine, CaptureState

    engine = CaptureEngine(channels)
    engine.configure(CaptureConfig(channel_mask=mask, depth=16))
    capture_id = engine.arm()
    for _ in range(9):
        assert not engine.step(raw)
    assert engine.sample_count == 0
    assert engine.step(raw)
    assert engine.samples == (stored,)
    for _ in range(150):
        engine.step(raw)
    assert engine.state == CaptureState.DONE
    assert engine.sample_count == 16
    assert engine.read_capture(capture_id, 0, 16) == stored.to_bytes(channels // 8, "little") * 16
    for _ in range(30):
        engine.step(0)
    engine.abort()
    assert engine.sample_count == 16 and engine.state == CaptureState.DONE


def test_two_register_latency_and_arm_compares_pre_edge_synchronized_value():
    from products.analyzer.capture import CaptureConfig, CaptureEngine

    engine = CaptureEngine(8)
    engine.configure(CaptureConfig(depth=16))
    engine.arm()
    for _ in range(8):
        engine.step(0)
    engine.step(1)
    engine.step(1)  # tick 10 sees sync stage 2 from BEFORE this edge: still zero
    assert engine.samples == (0,)
    for _ in range(10):
        engine.step(1)
    assert engine.samples == (0, 1)


def test_rising_channel_15_trigger_tick_is_sample_zero():
    from products.analyzer.capture import CaptureConfig, CaptureEngine, CaptureState

    engine = CaptureEngine(16)
    engine.configure(CaptureConfig(trigger_mode=1, trigger_channel=15, channel_mask=0x8000, depth=16))
    engine.arm()
    for _ in range(10):
        engine.step(0)
    assert engine.state == CaptureState.ARMED
    for _ in range(10):
        engine.step(0x8000)
    assert engine.samples == (0x8000,)
    assert engine.state == CaptureState.CAPTURING


def test_abort_wins_over_tick_and_reset_wins_over_abort():
    from products.analyzer.capture import CaptureConfig, CaptureEngine, CaptureError, CaptureState

    engine = CaptureEngine()
    engine.configure(CaptureConfig(depth=16))
    current = engine.arm()
    for _ in range(9):
        engine.step(1)
    engine.step(1, abort=True)
    assert engine.state == CaptureState.ABORTED and engine.sample_count == 0
    with pytest.raises(CaptureError) as error:
        engine.read_capture(current, 0, 1)
    assert error.value.code == 10
    engine.step(1, abort=True, reset=True)
    assert engine.state == CaptureState.IDLE and engine.capture_id == 0


@pytest.mark.parametrize("config", [
    {"channel_mask": 0}, {"channel_mask": 256}, {"divider": 11}, {"depth": 15},
    {"depth": 17}, {"depth": 8192}, {"trigger_mode": 2}, {"trigger_channel": 8},
    {"trigger_mode": 1, "trigger_channel": 2, "channel_mask": 1},
])
def test_config_rejection_is_atomic(config):
    from products.analyzer.capture import CaptureConfig, CaptureEngine, CaptureError

    engine = CaptureEngine()
    old = engine.config
    with pytest.raises(CaptureError) as error:
        engine.configure(CaptureConfig(**config))
    assert error.value.code == 4
    assert engine.config == old


def test_duplicate_arm_precedes_busy_and_sequence_conflict_preserves_cache():
    from products.analyzer.capture import CaptureEngine
    from products.analyzer.protocol import Frame

    engine = CaptureEngine()
    arm = Frame(3, 65535)
    first = engine.handle_frame(arm)
    assert first.type == 0x83 and first.payload == b"\x01\x00\x00\x00"
    assert engine.handle_frame(arm) == first
    conflict = engine.handle_frame(Frame(6, 65535))
    assert conflict.payload == b"\x06\x09\x00"
    assert engine.handle_frame(arm) == first and engine.capture_id == 1
    assert engine.handle_frame(Frame(5, 0)).type == 0x85
    busy = engine.handle_frame(Frame(3, 1))
    assert busy.payload == b"\x03\x06\x00"
    assert engine.handle_frame(Frame(3, 1)) == busy
    engine.reset()
    assert engine.handle_frame(arm) == first


def test_capture_id_stale_precedence_wrap_and_configuration_invalidation():
    from products.analyzer.capture import CaptureConfig, CaptureEngine, CaptureError

    engine = CaptureEngine()
    engine.capture_id = 0xFFFFFFFF
    current = engine.arm()
    assert current == 1
    with pytest.raises(CaptureError) as error:
        engine.read_capture(2, 0, 1)
    assert error.value.code == 5
    with pytest.raises(CaptureError) as error:
        engine.read_capture(current, 0, 1)
    assert error.value.code == 7
    engine.abort()
    engine.configure(CaptureConfig(depth=16))
    assert engine.capture_id == current
    with pytest.raises(CaptureError) as error:
        engine.read_capture(current, 0, 1)
    assert error.value.code == 5


def test_protocol_semantic_error_precedence_and_caps():
    from products.analyzer.capture import CaptureEngine
    from products.analyzer.protocol import Frame

    engine = CaptureEngine(16)
    assert engine.handle_frame(Frame(1, 1)).payload.hex() == "100200e1f5050010000403000000"
    assert engine.handle_frame(Frame(3, 2, major=2)).payload == b"\x03\x02\x00"
    assert engine.handle_frame(Frame(3, 3, flags=1)).payload == b"\x03\x03\x00"
    assert engine.handle_frame(Frame(3, 4, b"x")).payload == b"\x03\x01\x00"
    assert engine.handle_frame(Frame(9, 5)).payload == b"\x09\x03\x00"
    assert engine.capture_id == 0


def test_read_range_chunk_capacity_and_little_endian():
    from products.analyzer.capture import CaptureConfig, CaptureEngine, CaptureError
    from products.analyzer.protocol import Frame

    engine = CaptureEngine(16)
    engine.configure(CaptureConfig(channel_mask=0xFFFF, depth=1024))
    current = engine.arm()
    for _ in range(10240):
        engine.step(0x8001)
    assert engine.read_capture(current, 0, 506) == b"\x01\x80" * 506
    for offset, count in [(0, 507), (0, 0), (1023, 2), (-1, 1)]:
        with pytest.raises(CaptureError) as error:
            engine.read_capture(current, offset, count)
        assert error.value.code == 8
    response = engine.handle_frame(Frame(4, 1, struct.pack("<IIH", current, 10, 2)))
    assert response.payload.hex() == "010000000a0000000200020001800180"


def test_divider_100_and_non_tick_short_pulse_are_not_false_edges():
    from products.analyzer.capture import CaptureConfig, CaptureEngine

    engine = CaptureEngine()
    engine.configure(CaptureConfig(trigger_mode=1, divider=100, depth=16))
    engine.arm()
    for index in range(1, 101):
        tick = engine.step(1 if 20 <= index < 25 else 0)
        assert tick == (index == 100)
    assert engine.sample_count == 0
    for _ in range(100):
        engine.step(1)
    assert engine.samples == (1,)


def test_all_read_chunks_reassemble_exact_4096_samples_for_both_widths():
    from products.analyzer.capture import CaptureEngine

    for channels, count in ((8, 1012), (16, 506)):
        engine = CaptureEngine(channels)
        capture_id = engine.arm()
        for index in range(40960):
            engine.step((index // 10) & ((1 << channels) - 1))
        chunks = [engine.read_capture(capture_id, offset, min(count, 4096-offset)) for offset in range(0, 4096, count)]
        assert len(b"".join(chunks)) == 4096 * (channels // 8)
        assert b"".join(chunks) == b"".join(value.to_bytes(channels // 8, "little") for value in engine.samples)
