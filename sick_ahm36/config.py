"""PGN 0xEF00 configuration message framing (phase 2).

Configuration is point-to-point (PDU1). Each message is exactly 8 bytes::

    Byte 1  Message ID     0=param data (response), 1=read request, 2=write request
    Byte 2  Parameter Index
    Byte 3  Parameter Length   number of valid data bytes in the value field
    Byte 4  Error Code         0x00 = success (meaningful in responses)
    Bytes 5-8  Parameter Value  little-endian, left-aligned

The encoder always answers a request with Message ID = 0 and an error code.
This module only builds/parses those frames; the send/receive lives in encoder.
"""

from __future__ import annotations

import struct
from dataclasses import dataclass

from . import j1939, protocol
from .protocol import ConfigMsgId
from .transport import Frame


@dataclass(frozen=True)
class ConfigResponse:
    """Decoded encoder reply to a 0xEF00 request."""

    param_index: int
    length: int
    error_code: int
    value: int

    @property
    def ok(self) -> bool:
        return self.error_code == 0x00

    @property
    def error_text(self) -> str:
        return protocol.CONFIG_ERROR_CODES.get(self.error_code, f"0x{self.error_code:02X}")


class ConfigError(Exception):
    """Raised when the encoder rejects a configuration request."""

    def __init__(self, response: ConfigResponse):
        self.response = response
        super().__init__(
            f"param {response.param_index}: {response.error_text} "
            f"(error 0x{response.error_code:02X})"
        )


def _mask_to_length(value: int, length: int) -> int:
    if length == 1:
        return value & 0xFF
    if length == 2:
        return value & 0xFFFF
    return value & 0xFFFFFFFF


def build_request(msg_id: ConfigMsgId, param_index: int, *,
                  source_address: int, destination_address: int,
                  length: int = 0, value: int = 0, priority: int = 6) -> Frame:
    """Build a 0xEF00 request frame addressed to the encoder."""
    data = bytearray(8)
    data[0] = int(msg_id) & 0xFF
    data[1] = param_index & 0xFF
    data[2] = length & 0xFF
    data[3] = 0x00  # error code is zero in a request
    struct.pack_into("<I", data, 4, value & 0xFFFFFFFF)
    arb = j1939.build_id(protocol.PGN_CONFIG, source_address=source_address,
                         priority=priority, destination_address=destination_address)
    return Frame(arbitration_id=arb, data=bytes(data))


def build_response(param_index: int, *, source_address: int, destination_address: int,
                   length: int = 0, value: int = 0, error_code: int = 0x00,
                   priority: int = 6) -> Frame:
    """Build a 0xEF00 response frame (Message ID = 0). Used by the simulator."""
    data = bytearray(8)
    data[0] = int(ConfigMsgId.PARAM_DATA)
    data[1] = param_index & 0xFF
    data[2] = length & 0xFF
    data[3] = error_code & 0xFF
    struct.pack_into("<I", data, 4, value & 0xFFFFFFFF)
    arb = j1939.build_id(protocol.PGN_CONFIG, source_address=source_address,
                         priority=priority, destination_address=destination_address)
    return Frame(arbitration_id=arb, data=bytes(data))


def parse_message(data: bytes) -> tuple[int, int, int, int, int]:
    """Return (msg_id, param_index, length, error_code, value) from 8 bytes."""
    if len(data) < 8:
        raise ValueError(f"0xEF00 message needs 8 bytes, got {len(data)}")
    msg_id, param_index, length, error_code = data[0], data[1], data[2], data[3]
    value = struct.unpack_from("<I", data, 4)[0]
    return msg_id, param_index, length, error_code, _mask_to_length(value, length)


def parse_response(data: bytes) -> ConfigResponse:
    """Parse an encoder reply (Message ID = 0) into a :class:`ConfigResponse`."""
    _msg_id, param_index, length, error_code, value = parse_message(data)
    return ConfigResponse(param_index=param_index, length=length,
                          error_code=error_code, value=value)
