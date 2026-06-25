"""Transport config plumbing - pure, no hardware and no python-can needed.

Run with:  python -m pytest tests/      (or: python tests/test_transport.py)
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from sick_ahm36 import protocol  # noqa: E402
from sick_ahm36.transport import bus_kwargs  # noqa: E402


def test_higher_layer_keys_are_stripped():
    # A config as the encoder/GUI would build it: backend keys + library keys.
    iface, cfg = bus_kwargs({
        "interface": "pcan", "channel": "PCAN_USBBUS1", "bitrate": 250000,
        "source_address": 0xE0, "client_address": 0xF9,
        "speed_format": 3, "steps_per_rev": 16384, "config_timeout": 1.0,
    })
    assert iface == "pcan"
    assert cfg == {"channel": "PCAN_USBBUS1", "bitrate": 250000}


def test_default_bitrate_applied():
    _iface, cfg = bus_kwargs({"interface": "socketcan", "channel": "can0"})
    assert cfg["bitrate"] == protocol.DEFAULT_BITRATE


def test_vector_channel_coerced_to_int_and_app_name_none():
    iface, cfg = bus_kwargs({"interface": "vector", "channel": "0",
                             "bitrate": 250000, "source_address": 0xE0})
    assert iface == "vector"
    assert cfg["channel"] == 0 and isinstance(cfg["channel"], int)
    assert cfg["app_name"] is None
    assert "source_address" not in cfg


def test_vector_app_name_and_serial_passthrough():
    # an explicit app_name/serial from the caller is preserved
    _iface, cfg = bus_kwargs({"interface": "vector", "channel": 1,
                              "app_name": "MyApp", "serial": 12345})
    assert cfg["app_name"] == "MyApp"
    assert cfg["serial"] == 12345
    assert cfg["channel"] == 1


def test_non_vector_channel_left_as_is():
    _iface, cfg = bus_kwargs({"interface": "kvaser", "channel": "0"})
    assert cfg["channel"] == "0"        # not coerced; kvaser accepts its own form
    assert "app_name" not in cfg


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
