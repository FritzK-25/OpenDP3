"""Only manufacturer-documented Explorer 1000 v2 settings become controls."""

import json

import pytest

from opendp3.jackery import (
    JACKERY_CONTROLS,
    jackery_control_command,
    jackery_control_readback_matches,
    jackery_control_value,
)
from opendp3.jackery_bridge import discovery_payloads, state_payload


def _command_body(command: str) -> dict:
    return json.loads(bytes.fromhex(command[12:]).decode("utf-8"))


def test_charging_mode_exposes_only_documented_standard_and_quiet_states():
    spec = JACKERY_CONTROLS["jackery_charging_mode"]
    assert spec == {
        "wire": "cs", "action": 0x0A, "kind": "select",
        "values": {"standard": 0, "quiet": 1},
    }
    assert _command_body(jackery_control_command("jackery_charging_mode", "standard")) == {"cs": 0}
    assert _command_body(jackery_control_command("jackery_charging_mode", "quiet")) == {"cs": 1}
    assert jackery_control_readback_matches("jackery_charging_mode", "quiet", 1)
    with pytest.raises(ValueError):
        jackery_control_value("jackery_charging_mode", "custom")
    with pytest.raises(ValueError):
        jackery_control_value("jackery_charging_mode", "fast")


def test_auto_power_off_matches_jackery_published_presets():
    spec = JACKERY_CONTROLS["jackery_auto_power_off"]
    assert spec["wire"] == "pm"
    assert spec["action"] == 0x0C
    assert spec["values"] == {"off": 0, "2h": 120, "8h": 480, "12h": 720, "24h": 1440}
    assert _command_body(jackery_control_command("jackery_auto_power_off", "12h")) == {"pm": 720}
    assert jackery_control_readback_matches("jackery_auto_power_off", "12h", 720)


def test_undocumented_or_ambiguous_controls_stay_out_of_the_allowlist():
    assert "jackery_light_mode" not in JACKERY_CONTROLS
    assert "jackery_emergency_charge" not in JACKERY_CONTROLS
    assert "jackery_ups_mode" not in JACKERY_CONTROLS


def test_bridge_publishes_documented_selects_from_existing_raw_readback():
    now = 1_000_000_000
    reading = {
        "session": {"synthetic": False, "status": "recording"},
        "last_utc_ns": now,
        "count": 1,
        "values": {
            "jackery_charge_mode": {"value": 1.0, "quality": "ble_observed", "utc_ns": now, "t": 0.0},
            "jackery_energy_saving_mode": {"value": 720.0, "quality": "ble_observed", "utc_ns": now, "t": 0.0},
        },
    }
    payload, live = state_payload(reading, now_ns=now)
    assert live
    assert payload["jackery_charge_mode"] == 1.0
    assert payload["jackery_charging_mode"] == "quiet"
    assert payload["jackery_energy_saving_mode"] == 720.0
    assert payload["jackery_auto_power_off"] == "12h"


def test_mqtt_discovery_exposes_only_the_documented_new_options():
    controls = {
        config["object_id"]: config
        for config in discovery_payloads("123456789012345").values()
        if config.get("command_topic")
    }
    assert controls["jackery_charging_mode"]["options"] == ["standard", "quiet"]
    assert controls["jackery_auto_power_off"]["options"] == ["off", "2h", "8h", "12h", "24h"]
