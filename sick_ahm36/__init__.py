"""Rudimentary modular library for the SICK AHM36A-BDJC014X12 absolute encoder.

The encoder speaks SAE J1939 over CAN and *broadcasts* position + speed by
default (PGN 0xFFE0 @ 10 ms), so reading is listen-and-decode. Start here::

    from sick_ahm36 import Ahm36Encoder

    with Ahm36Encoder({"interface": "sim"}) as enc:   # "sim" needs no hardware
        data = enc.read_process_data()
        print(data)

Layers (each independently usable):
    protocol   - constants / spec (PGNs, status bits, parameter table)
    j1939      - generic 29-bit identifier encode/decode
    decode     - raw bytes -> ProcessData
    transport  - python-can wrapper (backend chosen by config)
    sim        - offline simulator
    encoder    - high-level Ahm36Encoder client
    profile    - reach-stacker yaw application profile (firmware-matching
                 config + tophandler-angle conversion)
"""

from . import config, j1939, profile, protocol
from .config import ConfigError, ConfigResponse
from .decode import ProcessData, decode_ffe0, decode_status, encode_ffe0
from .encoder import Ahm36Encoder, ParamCheck, ParamWrite
from .protocol import Param, SpeedFormat
from .transport import Frame, open_transport

__all__ = [
    "Ahm36Encoder",
    "ParamCheck",
    "ParamWrite",
    "ProcessData",
    "SpeedFormat",
    "Param",
    "ConfigError",
    "ConfigResponse",
    "Frame",
    "decode_ffe0",
    "decode_status",
    "encode_ffe0",
    "open_transport",
    "protocol",
    "j1939",
    "config",
    "profile",
]

__version__ = "0.1.0"
