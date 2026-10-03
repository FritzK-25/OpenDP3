"""Identifiers that existing installs have already stored, pinned as literals.

The project's display name may change; these must not. Home Assistant keeps
the app slug, MQTT topics, entity unique_ids, entity ids and device identifiers
from the first install onwards, so renaming any of them orphans a user's
entities, history and automations, or makes them reinstall the app. Branch
protection requires the CI check name. If one of these tests fails, the change
needs a migration plan, not a test update.
"""
from pathlib import Path

from opendp3.bridge import (HA_ID_PREFIX, Bridge, control_topic,
                            discovery_payloads)
from opendp3.config import Config

ROOT = Path(__file__).resolve().parents[1]
DEV_ID = "0123456789ab"


def test_ha_id_prefix_is_frozen():
    assert HA_ID_PREFIX == "opendp3"


def test_ha_app_slug_is_frozen():
    config = (ROOT / "home-assistant" / "opendp3" / "config.yaml").read_text("utf-8")
    assert "\nslug: opendp3\n" in config


def test_discovery_identifiers_are_frozen():
    payloads = discovery_payloads(DEV_ID, control=True)
    battery = payloads["homeassistant/sensor/opendp3_0123456789ab/cms_batt_soc/config"]
    assert battery["unique_id"] == "opendp3_0123456789ab_cms_batt_soc"
    assert battery["object_id"] == "opendp3_cms_batt_soc"
    assert battery["default_entity_id"] == "sensor.opendp3_cms_batt_soc"
    assert battery["state_topic"] == "opendp3/0123456789ab/state"
    assert battery["device"]["identifiers"] == ["opendp3_0123456789ab"]
    switch = payloads["homeassistant/switch/opendp3_0123456789ab/cfg_hv_ac_out_open/config"]
    assert switch["unique_id"] == "opendp3_0123456789ab_cfg_hv_ac_out_open"
    assert switch["default_entity_id"] == "switch.opendp3_cfg_hv_ac_out_open"
    assert switch["command_topic"] == "opendp3/0123456789ab/control/cfg_hv_ac_out_open/set"


def test_control_topic_is_frozen():
    assert control_topic(DEV_ID, "cfg_hv_ac_out_open") == \
        "opendp3/0123456789ab/control/cfg_hv_ac_out_open/set"


def test_bridge_topic_base_is_frozen(tmp_path):
    config = Config(address="", serial="MR51ABCDEFGHIJKL", user_id="")
    bridge = Bridge(config, tmp_path / "telemetry.sqlite")
    assert bridge.base == "opendp3/" + bridge.dev_id


def test_required_ci_check_name_is_frozen():
    workflow = (ROOT / ".github" / "workflows" / "ci.yml").read_text("utf-8")
    assert "\n  home-assistant-app:\n" in workflow
