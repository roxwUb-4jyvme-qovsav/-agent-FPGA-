from copy import deepcopy

import pytest


def test_json_snapshot_restores_command_cache_without_rearming():
    import json

    from products.analyzer.capture import CaptureEngine
    from products.analyzer.protocol import Frame

    engine = CaptureEngine(16)
    first = engine.handle_frame(Frame(3, 55))
    for _ in range(21):
        engine.step(0x8001)
    restored = CaptureEngine.from_snapshot(json.loads(json.dumps(engine.snapshot())))
    assert restored.handle_frame(Frame(3, 55)) == first
    assert restored.capture_id == 1 and restored.sample_count == 2
    for _ in range(100):
        engine.step(0xFFFF)
        restored.step(0xFFFF)
    assert restored.snapshot() == engine.snapshot()


def test_done_snapshot_restores_exact_samples_and_does_not_alias():
    from products.analyzer.capture import CaptureConfig, CaptureEngine

    engine = CaptureEngine(16)
    engine.configure(CaptureConfig(depth=16, channel_mask=0x8001))
    current = engine.arm()
    for _ in range(160):
        engine.step(0xFFFF)
    serialized = engine.snapshot()
    restored = CaptureEngine.from_snapshot(serialized)
    assert restored.read_capture(current, 0, 16) == b"\x01\x80" * 16
    serialized["samples"][0] = 0
    assert restored.samples[0] == engine.samples[0] == 0x8001


@pytest.mark.parametrize("field,value", [("channels", 9), ("capture_id", -1), ("state", 99),
                                        ("samples", [256]), ("phase", 10), ("sync1", 256),
                                        ("config", {"depth": 17}), ("cached_request", "a55a"),
                                        ("unexpected", True)])
def test_snapshot_refuses_invalid_untrusted_fields(field, value):
    from products.analyzer.capture import CaptureEngine

    serialized = deepcopy(CaptureEngine().snapshot())
    serialized[field] = value
    with pytest.raises(ValueError):
        CaptureEngine.from_snapshot(serialized)
