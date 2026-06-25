"""Decode SICK AHM36A process-data frames into physical values.

Pure functions over raw bytes - unit-testable with no hardware. Covers the
default broadcast PGN 0xFFE0 (Position + Speed + Status). The other process
PGNs share the same 8-byte little-endian shape and can be added the same way.
"""

from __future__ import annotations

import struct
from dataclasses import dataclass, field

from . import protocol
from .protocol import SpeedFormat


@dataclass(frozen=True)
class ProcessData:
    """One decoded 0xFFE0 broadcast frame."""

    position_counts: int          # raw absolute position (0 .. TOTAL_RANGE-1)
    speed_raw: int                # signed speed in the encoder's configured unit
    status_word: int              # 0x0000 = healthy

    speed_unit: str = "rpm"       # label for speed_raw
    revolutions: float = 0.0      # position expressed in full turns
    angle_deg: float = 0.0        # angle within the current turn, 0..360
    status_messages: tuple[str, ...] = field(default_factory=tuple)

    @property
    def healthy(self) -> bool:
        return self.status_word == 0x0000

    def __str__(self) -> str:
        health = "OK" if self.healthy else f"FAULT(0x{self.status_word:04X})"
        return (f"pos={self.position_counts} "
                f"({self.revolutions:.3f} rev, {self.angle_deg:.2f} deg) "
                f"speed={self.speed_raw} {self.speed_unit} [{health}]")


def decode_status(status_word: int) -> tuple[str, ...]:
    """Return human-readable messages for every set bit in the status word."""
    if status_word == 0:
        return ()
    return tuple(
        msg for bit, msg in sorted(protocol.STATUS_BITS.items(), reverse=True)
        if status_word & (1 << bit)
    )


def decode_ffe0(data: bytes,
                speed_format: SpeedFormat = protocol.DEFAULT_SPEED_FORMAT,
                steps_per_rev: int = protocol.STEPS_PER_REV) -> ProcessData:
    """Decode an 8-byte PGN 0xFFE0 payload.

    Layout (little-endian): position UINT32 | speed INT16 | status UINT16.
    """
    if len(data) < 8:
        raise ValueError(f"0xFFE0 frame needs 8 bytes, got {len(data)}")

    position, speed, status = struct.unpack_from("<IhH", data, 0)

    revolutions = position / steps_per_rev if steps_per_rev else 0.0
    angle_deg = (position % steps_per_rev) / steps_per_rev * 360.0 if steps_per_rev else 0.0

    return ProcessData(
        position_counts=position,
        speed_raw=speed,
        status_word=status,
        speed_unit=protocol.SPEED_UNIT_LABELS.get(speed_format, "?"),
        revolutions=revolutions,
        angle_deg=angle_deg,
        status_messages=decode_status(status),
    )


def encode_ffe0(position_counts: int, speed_raw: int, status_word: int = 0) -> bytes:
    """Build an 8-byte 0xFFE0 payload. Used by the simulator and tests."""
    return struct.pack("<IhH",
                       position_counts & 0xFFFFFFFF,
                       speed_raw,
                       status_word & 0xFFFF)
