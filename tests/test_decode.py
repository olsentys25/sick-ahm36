"""Pure unit tests - no hardware, no python-can required.

Run with:  python -m pytest tests/      (or: python tests/test_decode.py)
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from sick_ahm36 import (  # noqa: E402
    decode_ffe0,
    decode_status,
    encode_ffe0,
    j1939,
    protocol,
)


def test_ffe0_roundtrip():
    raw = encode_ffe0(position_counts=1_234_567, speed_raw=-500, status_word=0x0000)
    assert len(raw) == 8
    pd = decode_ffe0(raw)
    assert pd.position_counts == 1_234_567
    assert pd.speed_raw == -500          # signed INT16 preserved
    assert pd.speed_unit == "rpm"
    assert pd.status_word == 0x0000
    assert pd.healthy is True
    assert pd.status_messages == ()
    # 1,234,567 counts at 16,384 counts/rev
    assert abs(pd.revolutions - 75.353) < 0.01
    assert 0.0 <= pd.angle_deg < 360.0


def test_ffe0_little_endian_layout():
    # position = 0x00000001, speed = 0x0002, status = 0x0003
    raw = bytes([0x01, 0x00, 0x00, 0x00, 0x02, 0x00, 0x03, 0x00])
    pd = decode_ffe0(raw)
    assert pd.position_counts == 1
    assert pd.speed_raw == 2
    assert pd.status_word == 3


def test_status_decoding():
    # bit 15 (memory error) + bit 3 (voltage warning)
    word = (1 << 15) | (1 << 3)
    msgs = decode_status(word)
    assert any("Memory error" in m for m in msgs)
    assert any("Voltage warning" in m for m in msgs)
    assert decode_status(0) == ()


def test_max_position_wraps_within_range():
    pd = decode_ffe0(encode_ffe0(protocol.TOTAL_RANGE - 1, 0, 0))
    assert pd.position_counts == protocol.TOTAL_RANGE - 1
    assert pd.angle_deg < 360.0


def test_j1939_pdu2_broadcast_roundtrip():
    arb = j1939.build_id(protocol.PGN_FFE0, source_address=protocol.DEFAULT_SOURCE_ADDRESS)
    ident = j1939.parse_id(arb)
    assert ident.pgn == protocol.PGN_FFE0
    assert ident.source_address == protocol.DEFAULT_SOURCE_ADDRESS
    assert ident.is_pdu1 is False
    assert ident.destination_address is None
    assert j1939.pgn_from_id(arb) == protocol.PGN_FFE0


def test_j1939_pdu1_pointtopoint_roundtrip():
    arb = j1939.build_id(protocol.PGN_CONFIG, source_address=0x01,
                         destination_address=protocol.DEFAULT_SOURCE_ADDRESS)
    ident = j1939.parse_id(arb)
    assert ident.pgn == protocol.PGN_CONFIG
    assert ident.is_pdu1 is True
    assert ident.destination_address == protocol.DEFAULT_SOURCE_ADDRESS
    assert ident.source_address == 0x01


if __name__ == "__main__":
    failures = 0
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            try:
                fn()
                print(f"PASS  {name}")
            except AssertionError as exc:
                failures += 1
                print(f"FAIL  {name}: {exc}")
    sys.exit(1 if failures else 0)
