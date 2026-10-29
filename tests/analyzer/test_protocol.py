"""Public behavior checks; golden expectations are frozen source vectors."""
import json
from pathlib import Path

import pytest

VECTORS = json.loads((Path(__file__).parents[2] / "packages/contracts/protocol/golden-vectors.json").read_text(encoding="utf-8"))["vectors"]


@pytest.mark.parametrize("vector", VECTORS, ids=lambda value: value["name"])
def test_frozen_wire_vector_encodes_and_decodes(vector):
    from products.analyzer.protocol import Frame, decode_frame, encode_frame

    frame = Frame(vector["type"], vector["sequence"], bytes.fromhex(vector["payload_hex"]))
    wire = bytes.fromhex(vector["frame_hex"])
    assert encode_frame(frame) == wire
    assert decode_frame(wire) == frame


def test_crc_known_check_and_corruption_rejection():
    from products.analyzer.protocol import FrameError, crc16, decode_frame

    assert crc16(b"123456789") == 0x29B1
    corrupted = bytearray.fromhex(VECTORS[0]["frame_hex"])
    corrupted[-1] ^= 1
    with pytest.raises(FrameError, match="CRC"):
        decode_frame(bytes(corrupted))


def test_stream_recovers_from_noise_oversize_crc_and_embedded_sync():
    from products.analyzer.protocol import Frame, FrameDecoder, encode_frame

    expected = Frame(0x84, 8, b"\xa5\x5a\x00\xff")
    good = encode_frame(expected)
    bad = bytearray.fromhex(VECTORS[0]["frame_hex"])
    bad[-1] ^= 0x80
    oversize = bytes.fromhex("a55a0100040004000104")
    stream = b"noise\xa5" + oversize + bytes(bad) + good
    decoder = FrameDecoder()
    recovered = []
    for byte in stream:
        recovered.extend(decoder.feed(bytes([byte]), now=0.0))
    assert recovered == [expected]
    assert decoder.buffered_bytes == 0


def test_half_frame_timeout_cannot_join_old_and_new_sessions():
    from products.analyzer.protocol import FrameDecoder

    good = bytes.fromhex(VECTORS[0]["frame_hex"])
    decoder = FrameDecoder(inter_byte_timeout=0.1)
    assert decoder.feed(good[:7], now=0) == []
    assert decoder.feed(good[7:], now=0.2) == []
    assert len(decoder.feed(good, now=0.21)) == 1


@pytest.mark.parametrize("wire", [b"", b"\xa5\x5a", bytes.fromhex(VECTORS[0]["frame_hex"]) + b"x"])
def test_single_frame_decoder_rejects_non_exact_lengths(wire):
    from products.analyzer.protocol import FrameError, decode_frame

    with pytest.raises(FrameError):
        decode_frame(wire)
