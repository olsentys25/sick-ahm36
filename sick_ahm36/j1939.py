"""SAE J1939 29-bit CAN identifier helpers.

Generic J1939 framing only - nothing here knows about the SICK encoder. The
29-bit extended identifier is laid out as:

    bits 26-28  Priority (3 bits)
    bit  25     Extended Data Page (EDP / reserved) - 0 for standard J1939
    bit  24     Data Page (DP)
    bits 16-23  PF  (PDU Format)
    bits 8-15   PS  (PDU Specific) -> Destination Address (PDU1) or Group Ext (PDU2)
    bits 0-7    SA  (Source Address)

A PGN is PDU1 (point-to-point) when PF < 0xF0; then PS carries the destination
address and is NOT part of the PGN. When PF >= 0xF0 it is PDU2 (broadcast) and
PS is the Group Extension, which IS part of the PGN.
"""

from __future__ import annotations

from dataclasses import dataclass

PDU1_PF_LIMIT = 0xF0  # PF below this is PDU1 (point-to-point)


@dataclass(frozen=True)
class J1939Id:
    """Decoded fields of a 29-bit J1939 identifier."""

    priority: int
    pgn: int
    source_address: int
    pdu_format: int
    pdu_specific: int

    @property
    def is_pdu1(self) -> bool:
        return self.pdu_format < PDU1_PF_LIMIT

    @property
    def destination_address(self) -> int | None:
        """Destination address for PDU1 messages, else None (PDU2 is broadcast)."""
        return self.pdu_specific if self.is_pdu1 else None


def pgn_from_id(can_id: int) -> int:
    """Extract the 18-bit PGN from a 29-bit J1939 identifier."""
    dp = (can_id >> 24) & 0x1
    pf = (can_id >> 16) & 0xFF
    ps = (can_id >> 8) & 0xFF
    if pf < PDU1_PF_LIMIT:
        return (dp << 16) | (pf << 8)
    return (dp << 16) | (pf << 8) | ps


def parse_id(can_id: int) -> J1939Id:
    """Fully decode a 29-bit J1939 identifier into its fields."""
    priority = (can_id >> 26) & 0x7
    dp = (can_id >> 24) & 0x1
    pf = (can_id >> 16) & 0xFF
    ps = (can_id >> 8) & 0xFF
    sa = can_id & 0xFF
    if pf < PDU1_PF_LIMIT:
        pgn = (dp << 16) | (pf << 8)
    else:
        pgn = (dp << 16) | (pf << 8) | ps
    return J1939Id(priority=priority, pgn=pgn, source_address=sa,
                   pdu_format=pf, pdu_specific=ps)


def build_id(pgn: int, source_address: int, *, priority: int = 6,
             destination_address: int | None = None) -> int:
    """Build a 29-bit J1939 identifier.

    For PDU1 PGNs (PF < 0xF0) a ``destination_address`` is required; the PGN's
    low byte is ignored and replaced by the DA. For PDU2 PGNs the DA is ignored.
    """
    dp = (pgn >> 16) & 0x1
    pf = (pgn >> 8) & 0xFF
    if pf < PDU1_PF_LIMIT:
        if destination_address is None:
            raise ValueError(f"PDU1 PGN 0x{pgn:04X} requires a destination_address")
        ps = destination_address & 0xFF
    else:
        ps = pgn & 0xFF
    return ((priority & 0x7) << 26 | (dp & 0x1) << 24 | (pf & 0xFF) << 16
            | (ps & 0xFF) << 8 | (source_address & 0xFF))
