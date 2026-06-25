"""Reach-stacker yaw profile: angle math + config apply/verify (sim, no hardware).

Run with:  python -m pytest tests/      (or: python tests/test_profile.py)
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from sick_ahm36 import Ahm36Encoder, profile  # noqa: E402
from sick_ahm36.protocol import Param  # noqa: E402


# --- scaling constants mirror the firmware ---------------------------------

def test_scale_constants_match_firmware():
    assert profile.ENC_RES_PER_REV == 16384
    assert profile.GEAR_RATIO == 16
    assert profile.ENC_SCALE == 262144          # ROTARY_ENC_SCALE
    assert profile.HALF_SCALE == 131072         # ROTARY_ENC_HALF_SCALE
    assert abs(profile.COUNTS_PER_DEGREE - 728.18) < 0.01


# --- tophandler-angle conversion mirrors calculateAngleWithWraparound -------

def test_angle_zero_and_quarter_turn():
    assert profile.tophandler_angle_deg(0) == 0.0
    # a quarter tophandler turn = 262144/4 counts -> +90 deg
    assert abs(profile.tophandler_angle_deg(profile.ENC_SCALE // 4) - 90.0) < 1e-6


def test_angle_folds_past_180():
    # just past +180 deg should fold to a small negative angle (matches firmware)
    just_over = profile.HALF_SCALE + 1
    assert profile.tophandler_angle_deg(just_over) < 0.0
    # exactly HALF_SCALE is +180 (firmware uses strict > for the fold)
    assert abs(profile.tophandler_angle_deg(profile.HALF_SCALE) - 180.0) < 1e-6


def test_angle_with_zero_offset():
    # raw == offset -> 0 deg regardless of where the offset sits
    assert profile.tophandler_angle_deg(50_000, zero_offset=50_000) == 0.0
    # offset that would underflow gets the single +SCALE wrap correction
    ang = profile.tophandler_angle_deg(10, zero_offset=profile.ENC_SCALE - 10)
    assert abs(ang - (20 / profile.COUNTS_PER_DEGREE)) < 1e-6


# --- apply / verify against the simulator ----------------------------------

def test_apply_then_verify_clean():
    with Ahm36Encoder({"interface": "sim"}) as enc:
        enc.start_background()
        writes = profile.apply(enc)
        assert all(w.ok for w in writes)
        checks = profile.verify(enc)
        assert checks, "verify should return one entry per required param"
        assert all(c.ok for c in checks), \
            [(c.param.name, c.actual, c.expected) for c in checks if not c.ok]
        # the value that actually differs from the sim's factory default
        ranges = {c.param: c.actual for c in checks}
        assert ranges[Param.TOTAL_MEASURING_RANGE] == profile.ENC_SCALE


def test_apply_skips_already_correct():
    with Ahm36Encoder({"interface": "sim"}) as enc:
        enc.start_background()
        profile.apply(enc)                 # first pass writes
        again = profile.apply(enc)         # second pass should skip everything
        assert all(w.ok for w in again)
        assert all(w.skipped for w in again), \
            [w.param.name for w in again if not w.skipped]


def test_apply_optional_counting_direction():
    with Ahm36Encoder({"interface": "sim"}) as enc:
        enc.start_background()
        writes = profile.apply(enc, counting_direction=1)
        assert any(w.param == Param.COUNTING_DIRECTION for w in writes)
        assert enc.read_param(Param.COUNTING_DIRECTION) == 1


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
