"""SICK AHM36A SAE J1939 protocol constants.

Pure data, no I/O. Single source of truth transcribed from the SICK
"Operating Instructions AHS/AHM36 SAE J1939" (doc 8027379) as captured in the
project design document. Everything that is sensor- or spec-specific lives here
so the rest of the library has exactly one place to look things up.
"""

from __future__ import annotations

from enum import IntEnum

# --- Bus defaults -----------------------------------------------------------

DEFAULT_BITRATE = 250_000          # 125k / 250k / 500k supported; 250k default
DEFAULT_SOURCE_ADDRESS = 0xE0      # 224, the encoder's factory node address

# --- Process-data PGNs (broadcast, PDU2) ------------------------------------
# All three share an 8-byte little-endian layout. Only PGN_FFE0 is enabled by
# default (10 ms cycle); the other two are off until configured.

PGN_FFE0 = 0xFFE0  # 65504: Position (UINT32) | Speed (INT16)       | Status (UINT16)
PGN_FFE1 = 0xFFE1  # 65505: Position (UINT32) | Temperature (INT16) | Status (UINT16)
PGN_FFE2 = 0xFFE2  # 65506: Speed   (INT32)   | Temperature (INT16) | Status (UINT16)

# --- Configuration PGN (point-to-point, PDU1) -------------------------------

PGN_CONFIG = 0xEF00  # 61184: read/write parameters; encoder replies on same PGN

# Resolution (from the device nameplate / part number 014X12)
STEPS_PER_REV = 16_384            # 14-bit singleturn
NUM_REVOLUTIONS = 4_096           # 12-bit multiturn
TOTAL_RANGE = STEPS_PER_REV * NUM_REVOLUTIONS  # 67,108,864 counts (2**26)


class SpeedFormat(IntEnum):
    """Param index 132 values: unit used for the broadcast speed field."""

    CPS = 0          # counts per second
    CP100MS = 1      # counts per 100 ms
    CP10MS = 2       # counts per 10 ms
    RPM = 3          # revolutions per minute (default)
    RPS = 4          # revolutions per second


SPEED_UNIT_LABELS = {
    SpeedFormat.CPS: "cps",
    SpeedFormat.CP100MS: "counts/100ms",
    SpeedFormat.CP10MS: "counts/10ms",
    SpeedFormat.RPM: "rpm",
    SpeedFormat.RPS: "rps",
}

DEFAULT_SPEED_FORMAT = SpeedFormat.RPM


# --- Status word ------------------------------------------------------------
# Bytes 7-8 of every transmit PGN. 0x0000 means healthy. Each set bit maps to a
# diagnostic message below; anything non-zero should be logged.

STATUS_BITS = {
    15: "Memory error - invalid EEPROM checksum on initialization",
    13: "Sync multi counter error - speed exceeded 12,500 rpm or excessive singleturn errors",
    11: "Position error - invalid sync between singleturn and multiturn counters",
    10: "Position error - singleturn position incorrect",
    9: "Position error - vector length error in multiturn stage",
    8: "Position error - vector length error in singleturn stage",
    7: "Position and memory error - I2C communication failure",
    6: "Position error - amplitude error in singleturn stage",
    5: "Speed warning - value outside min/max limit",
    4: "Position error - amplitude error in multiturn stage",
    3: "Voltage warning - supply voltage outside limit",
    1: "Temperature warning - value outside limit",
    0: "General start-up warning at power-on",
}


# --- Configuration parameter table (phase 2) --------------------------------
# Sent via PGN_CONFIG (0xEF00). Kept here so configuration support can be added
# in encoder.py without re-deriving anything. Value bytes are little-endian.

class ParamType(IntEnum):
    UINT8 = 1
    UINT16 = 2
    UINT32 = 4


class Param(IntEnum):
    """Parameter indexes (byte 2 of a 0xEF00 message)."""

    BAUD_RATE = 7
    COUNTING_DIRECTION = 129
    STEPS_PER_REV = 130
    TOTAL_MEASURING_RANGE = 128   # READ index: the effective range (read-only).
                                  # WRITE goes to index 131 - see WRITE_INDEX_OVERRIDE.
    SPEED_FORMAT = 132
    UPDATE_TIME_T1 = 133
    CYCLE_TIME_FFE0 = 137
    CYCLE_TIME_FFE1 = 140
    CYCLE_TIME_FFE2 = 144
    NODE_ADDRESS = 149
    PRESET = 200          # write-only
    POWER_CYCLE = 254     # write-only
    FACTORY_RESET = 255   # write-only


# index -> (type, human description). Defaults are noted in the design doc.
PARAM_INFO = {
    Param.BAUD_RATE: (ParamType.UINT16, "Baud rate (125/250/500 kbit/s)"),
    Param.COUNTING_DIRECTION: (ParamType.UINT8, "Counting direction (0=CW, 1=CCW)"),
    Param.STEPS_PER_REV: (ParamType.UINT32, "Steps per revolution (1..16384)"),
    Param.TOTAL_MEASURING_RANGE: (ParamType.UINT32, "Total measuring range"),
    Param.SPEED_FORMAT: (ParamType.UINT32, "Speed format (see SpeedFormat)"),
    Param.UPDATE_TIME_T1: (ParamType.UINT32, "Update time T1 in ms (1..50)"),
    Param.CYCLE_TIME_FFE0: (ParamType.UINT32, "Cycle time PGN 0xFFE0 in ms (0=off, 10..10000)"),
    Param.CYCLE_TIME_FFE1: (ParamType.UINT32, "Cycle time PGN 0xFFE1 in ms (0=off, 10..10000)"),
    Param.CYCLE_TIME_FFE2: (ParamType.UINT32, "Cycle time PGN 0xFFE2 in ms (0=off, 10..10000)"),
    Param.NODE_ADDRESS: (ParamType.UINT8, "Node address"),
    Param.PRESET: (ParamType.UINT32, "Preset value (write-only)"),
    Param.POWER_CYCLE: (ParamType.UINT8, "Power cycle / reset (write-only)"),
    Param.FACTORY_RESET: (ParamType.UINT8, "Factory reset (write-only)"),
}


# The SICK total measuring range is asymmetric (verified live on an AHM36A): the
# *effective* range is read read-only at index 128, but a new range is *written*
# to index 131 ("total measuring range modified"). Writing 131 updates the value
# read back at 128 and rescales the broadcast position into the new range. Any
# param not listed here is written to its own index.
WRITE_INDEX_OVERRIDE = {Param.TOTAL_MEASURING_RANGE: 131}


class ConfigMsgId(IntEnum):
    """Byte 1 of a 0xEF00 message."""

    PARAM_DATA = 0    # encoder response carrying parameter data
    READ = 1          # read request
    WRITE = 2         # write request


# Byte 4 error codes in a 0xEF00 encoder response.
CONFIG_ERROR_CODES = {
    0x00: "success",
    0x01: "parameter not available",
    0x02: "parameter length incorrect",
    0x03: "value out of range",
    0x04: "parameter value not supported",
    0x05: "attempted read of write-only parameter",
    0x06: "attempted write of read-only parameter",
    0x07: "message ID not supported",
    0xFF: "unknown error",
}
