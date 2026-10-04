"""What counts as a fault decides what the downsampler is allowed to delete.

A detector that stays quiet through a real fault destroys the only recording of
it, so each rule is tested for firing AND for staying quiet during ordinary use.
"""
from openpowerstation.jackery_fields import map_properties
from openpowerstation.jackery_health import UNMAPPED_STATE_LIMIT, findings


def properties(**overrides):
    base = {"acip": 0, "acohz": 0, "acov": 0, "acpsp": 0, "acpss": 0, "ast": 120,
            "bs": 0, "bt": 290, "cip": 0, "cs": 0, "ec": 0, "ip": 0, "it": 0,
            "lm": 0, "lps": 0, "oac": 0, "odc": 0, "op": 100, "ot": 999,
            "pal": 0, "pm": 720, "pmb": 0, "rb": 100, "sfc": 0, "sltb": 1, "ta": 0}
    base.update(overrides)
    return base


def check(t=10.0, previous=None, seen=None, **overrides):
    props = properties(**overrides)
    return findings(map_properties(props), props, previous or {}, seen if seen is not None else {}, t)


def kinds(found):
    return {kind for kind, _, _ in found}


def pinned(found):
    return {kind for kind, _, pin in found if pin}


def test_ordinary_operation_is_not_flagged():
    seen = {}
    previous = {"cms_batt_temp": (7.0, 29.0), "cms_batt_soc": (7.0, 100.0)}
    assert check(previous=previous, seen=seen) == []
    # Repeating the same reading must stay quiet, or nothing would ever thin.
    assert check(t=13.0, previous=previous, seen=seen) == []


def test_a_nonzero_error_code_pins():
    found = check(ec=42)
    assert "device_error" in pinned(found)


def test_a_temperature_outside_the_band_pins():
    assert "suspect_telemetry" in pinned(check(bt=850))     # 85.0 °C
    assert "suspect_telemetry" in pinned(check(bt=-400))    # -40.0 °C


def test_a_temperature_jump_pins():
    found = check(t=10.0, previous={"cms_batt_temp": (7.0, 29.0)}, bt=450)  # 45 °C in 3s
    assert "suspect_telemetry" in pinned(found)


def test_a_soc_discontinuity_pins():
    found = check(t=10.0, previous={"cms_batt_soc": (7.0, 100.0)}, rb=80)
    assert "suspect_telemetry" in pinned(found)


def test_soc_drifting_normally_is_not_flagged():
    found = check(t=10.0, previous={"cms_batt_soc": (7.0, 100.0)}, rb=99)
    assert found == []


def test_ac_faults_only_count_while_the_inverter_is_on():
    # 0 V with the output off is correct, not a fault.
    assert check(oac=0, acov=0) == []
    assert "suspect_telemetry" in pinned(check(oac=1, acov=0))
    assert "suspect_telemetry" in pinned(check(oac=1, acov=1200, acohz=250))


def test_a_healthy_ac_output_is_not_flagged():
    # Neither 120 V nor 230 V mains may be assumed to be the faulty one.
    assert check(oac=1, acov=1200, acohz=60) == []
    assert check(oac=1, acov=2300, acohz=50) == []


def test_an_ac_voltage_step_pins_even_without_knowing_nominal():
    found = check(t=10.0, previous={"jackery_ac_voltage_v": (7.0, 120.0)},
                  oac=1, acov=950, acohz=60)
    assert "suspect_telemetry" in pinned(found)


def test_a_new_value_for_an_unverified_property_pins():
    seen = {}
    # pmb remains intentionally unmapped. sltb is now a verified screen-timeout
    # preset and therefore no longer belongs in this unknown-state sentinel test.
    assert check(seen=seen, pmb=0) == []          # first sight establishes the baseline
    found = check(seen=seen, pmb=1)
    assert "unmapped_change" in pinned(found)
    assert check(seen=seen, pmb=1) == []          # already known, stays quiet


def test_a_counter_like_property_stops_pinning():
    """A field that keeps producing new values is a measurement, not a state.

    Without this the first counter-shaped property would pin every window and
    the downsampler would never delete anything at all.
    """
    seen = {}
    for value in range(UNMAPPED_STATE_LIMIT + 5):
        found = check(seen=seen, ta=value)
    assert pinned(found) == set()
    assert check(seen=seen, ta=9999) == []


def test_a_missing_sample_pins():
    props = properties()
    found = findings(map_properties(props), props, {}, {}, 120.0,
                     expected_interval=3, last_t=10.0)
    assert "capture_gap" in pinned(found)


def test_ordinary_jitter_is_not_a_gap():
    props = properties()
    found = findings(map_properties(props), props, {}, {}, 14.0,
                     expected_interval=3, last_t=10.0)
    assert found == []


def test_the_poll_rate_may_now_be_seconds_not_minutes():
    """Local BLE reads take about half a second, so the old 15s floor is wrong.

    The floor still has to reject zero and negative intervals, which would spin
    the recorder against the station's single BLE client slot.
    """
    import pytest
    from openpowerstation.cli import do_jackery_compact, do_jackery_record
    for bad in (0, 0.5, -1, 3601):
        with pytest.raises(ValueError, match="between 1 and 3600"):
            do_jackery_record(None, None, interval=bad)
    with pytest.raises(ValueError, match="at least one hour"):
        do_jackery_compact(None, None, grace_hours=0)
