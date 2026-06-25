"""Config-layer tests (PGN 0xEF00) exercised against the simulator. No hardware.

Run with:  python -m pytest tests/      (or: python tests/test_config.py)
"""

import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from sick_ahm36 import Ahm36Encoder, Param  # noqa: E402
from sick_ahm36 import config as configmsg  # noqa: E402
from sick_ahm36.config import ConfigError  # noqa: E402
from sick_ahm36.protocol import ConfigMsgId  # noqa: E402


def _enc():
    e = Ahm36Encoder({"interface": "sim", "sim_speed_rpm": 90.0})
    e.start_background()
    time.sleep(0.03)
    return e


def test_config_message_roundtrip():
    frame = configmsg.build_request(ConfigMsgId.WRITE, int(Param.SPEED_FORMAT),
                                    source_address=0xF9, destination_address=0xE0,
                                    length=4, value=4)
    msg_id, index, length, err, value = configmsg.parse_message(frame.data)
    assert msg_id == int(ConfigMsgId.WRITE)
    assert index == int(Param.SPEED_FORMAT)
    assert length == 4
    assert err == 0
    assert value == 4


def test_read_known_defaults():
    with _enc() as enc:
        assert enc.read_param(Param.BAUD_RATE) == 250
        assert enc.read_param(Param.NODE_ADDRESS) == 224
        assert enc.read_param(Param.SPEED_FORMAT) == 3


def test_write_then_readback():
    with _enc() as enc:
        enc.write_param(Param.SPEED_FORMAT, 4)
        assert enc.read_param(Param.SPEED_FORMAT) == 4
        # encoder's local decode unit should track the device
        assert enc._speed_format.value == 4  # noqa: SLF001 - intentional check


def test_read_all_skips_write_only():
    with _enc() as enc:
        params = enc.read_all_params()
        assert Param.BAUD_RATE in params
        assert Param.PRESET not in params
        assert Param.FACTORY_RESET not in params


def test_read_write_only_param_rejected():
    with _enc() as enc:
        try:
            enc.read_param(Param.PRESET)
            assert False, "expected ConfigError reading a write-only param"
        except ConfigError as exc:
            assert exc.response.error_code == 0x05


def test_preset_moves_position():
    with _enc() as enc:
        enc.preset(1_000_000)
        time.sleep(0.05)
        pos = enc.get_position()
        assert pos is not None and pos >= 1_000_000


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
