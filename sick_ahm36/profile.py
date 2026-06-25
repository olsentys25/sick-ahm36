"""Reach-stacker tophandler-yaw application profile.

This is the bridge between the generic AHM36A library and the *specific* way the
reach-stacker VCU firmware uses the encoder. It is the single source of truth on
the Python side, mirroring two things in the firmware repo:

  * the encoder parameters the firmware *assumes* (``SICK_YAW_ENCODER_MIGRATION.md``
    §4), and
  * the tophandler-angle math the firmware *performs*
    (``TopHandlerTypes.hpp`` constants + ``RtchVsDataBridge::calculateAngleWithWraparound``).

Keeping those numbers here lets this bench tool (a) configure / verify a real
encoder to match what the vehicle expects and (b) display the same yaw angle the
VCU will compute, so a reading on the bench agrees with the reading on the truck.

Nothing generic depends on this module; it depends on the generic layers.
"""

from __future__ import annotations

from .protocol import Param

# --- Mechanical / scaling constants ----------------------------------------
# Mirror TopHandlerTypes.hpp exactly. Do not edit one side without the other.

ENC_RES_PER_REV = 16384                          # ROTARY_ENC_RES_PER_REV (14-bit)
GEAR_RATIO = 16                                  # GEAR_RATIO (rotate axis -> encoder)
ENC_SCALE = ENC_RES_PER_REV * GEAR_RATIO         # ROTARY_ENC_SCALE = 262144 (wrap period)
HALF_SCALE = ENC_SCALE // 2                      # ROTARY_ENC_HALF_SCALE = 131072 (+/-180 deg fold)
COUNTS_PER_DEGREE = ENC_SCALE / 360.0            # ROTARY_COUNTS_PER_DEGREE ~= 728.18


def tophandler_angle_deg(raw_value: float, zero_offset: float = 0.0) -> float:
    """Counts on the wire -> tophandler yaw angle in degrees.

    Faithful port of ``RtchVsDataBridge::calculateAngleWithWraparound`` (which
    returns radians; we return degrees, the natural unit for a readout). The
    firmware applies a *single* wrap correction around +/-HALF_SCALE - it assumes
    both the raw value and the zero offset already lie within one revolution
    (0 .. ENC_SCALE), so ``delta`` is in (-ENC_SCALE, ENC_SCALE) and one fold is
    enough. We replicate that rather than a full modulo so the result matches the
    vehicle bit-for-bit within the operating range.
    """
    delta = raw_value - zero_offset
    if delta > HALF_SCALE:
        delta -= ENC_SCALE
    elif delta < -HALF_SCALE:
        delta += ENC_SCALE
    return delta / COUNTS_PER_DEGREE


# --- Required encoder parameters (migration §4) ----------------------------
# Values the firmware math assumes. Ordered so the disruptive writes
# (node address, baud) happen last - see apply(). Counting direction is
# deliberately NOT here: the migration leaves its polarity TBD, so forcing a
# value could silently invert yaw. It is handled as an explicit, opt-in arg to
# apply() and called out in MANUAL_NOTES instead.

REQUIRED_PARAMS: dict[Param, int] = {
    Param.STEPS_PER_REV: ENC_RES_PER_REV,        # 16384
    Param.TOTAL_MEASURING_RANGE: ENC_SCALE,      # 262144 (+ round-axis, see notes)
    Param.CYCLE_TIME_FFE0: 10,                   # 10 ms, matches GenMsgCycleTime
    Param.BAUD_RATE: 250,                         # 250 kbit/s (bus default)
    Param.NODE_ADDRESS: 224,                      # 0xE0, matches the DBC message ID
}

MANUAL_NOTES = (
    "Manual items the bus cannot fully verify:\n"
    "  - Counting direction (param 129): polarity is TBD in the migration; confirm\n"
    "    CW/CCW so yaw sign matches the previous sensor. Pass it to 'Apply' only\n"
    "    once verified.\n"
    "  - Total measuring range needs the encoder's round-axis / scaling function\n"
    "    enabled so hardware rollover lands on 262144; the value write alone may\n"
    "    not toggle that flag on every firmware revision - check the SICK tool.\n"
    "  - Re-calibrate the rotate zero offset after install (it is raw counts and\n"
    "    does not carry over from the old sensor)."
)


def summary() -> str:
    """One-block human description of what apply() will write."""
    lines = ["Reach-stacker yaw profile (writes over PGN 0xEF00):"]
    for param, value in REQUIRED_PARAMS.items():
        lines.append(f"  {param.name} ({int(param)}) = {value}")
    lines.append(f"  scale={ENC_SCALE}, half={HALF_SCALE}, "
                 f"counts/deg={COUNTS_PER_DEGREE:.2f}")
    return "\n".join(lines)


# --- Apply / verify ---------------------------------------------------------
# Thin reach-stacker-specific wrappers over the generic encoder methods.

def verify(encoder) -> list:
    """Read the required params back and compare. Returns list[ParamCheck]."""
    return encoder.verify_params(REQUIRED_PARAMS)


def apply(encoder, *, counting_direction: int | None = None,
          skip_if_equal: bool = True) -> list:
    """Write the required params to the encoder. Returns list[ParamWrite].

    ``counting_direction`` (0=CW, 1=CCW) is written only if given; leave it None
    until the polarity has been confirmed. ``skip_if_equal`` avoids re-writing
    (and re-disrupting the bus with) a parameter already at its target value.
    """
    values = dict(REQUIRED_PARAMS)
    if counting_direction is not None:
        # put direction first; it is harmless and good to set before the risky tail
        values = {Param.COUNTING_DIRECTION: int(counting_direction), **values}
    return encoder.apply_params(values, skip_if_equal=skip_if_equal)


# --- Report formatting (used by the GUI; kept here so the GUI stays thin) ----

def format_check(check) -> str:
    if check.actual is None:
        return f"  ? {check.param.name} ({int(check.param)}): unreadable - {check.note}"
    mark = "OK " if check.ok else "XX "
    suffix = "" if check.ok else f"  (expected {check.expected})"
    return f"  {mark}{check.param.name} ({int(check.param)}) = {check.actual}{suffix}"


def format_write(write) -> str:
    if not write.ok:
        return f"  XX {write.param.name} ({int(write.param)}) = {write.value}: {write.error}"
    if write.skipped:
        return f"  -- {write.param.name} ({int(write.param)}) already {write.value}"
    return f"  OK {write.param.name} ({int(write.param)}) = {write.value}"
