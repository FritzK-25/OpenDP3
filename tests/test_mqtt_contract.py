"""The MQTT discovery identity Home Assistant keys every entity on. Frozen.

Home Assistant finds an MQTT entity in its registry by unique_id. Change one
and the old entity is orphaned -- its history, its entity ID and every
dashboard card and automation naming it -- while a new one appears beside it.
So the discovery topics and unique_id values of both devices are frozen. The other bridge tests derive their
expectations from the same code they check, so a different hash length, a
reshaped unique_id or a renamed field key passed every one of them.

This test compares what both bridges discover for fixed serials with the
checked-in record in contract/mqtt_identity.json. Only identity is recorded:
names, icons, units and templates can change without touching it. A new
entity is an addition and orphans nothing; a changed or removed row does.
To change the record on purpose, regenerate it from the repository root, with the
project's environment active, and review the diff like any other change:

    python tests/test_mqtt_contract.py --update
"""
import json
from pathlib import Path
import re
import sys

import pytest

from openpowerstation import bridge, entity_ids, jackery_bridge

CONTRACT = Path(__file__).with_name("contract") / "mqtt_identity.json"
# Made up, so the record identifies no real unit. Only their hashes are
# published, and recording those pins the hash itself.
SERIALS = {"dp3": "MR51ABCDEFGHIJKL", "jackery": "JKCONTRACT000001"}
STATE_KEY = re.compile(r"value_json\.(\w+)")
CHANGED = ("MQTT contract change. These identities are what Home Assistant keys "
           "every installation's entities on.")


def identity(topic, config, *, control_only):
    """The fields of one discovery message that an entity's identity rests on."""
    _, component, _, key, _ = topic.split("/")
    availability = config.get("availability") or (
        [{"topic": config["availability_topic"]}] if "availability_topic" in config else [])
    return {
        "discovery_topic": topic,
        "component": component,
        "key": key,
        "unique_id": config["unique_id"],
        "object_id": config["object_id"],
        "default_entity_id": config["default_entity_id"],
        "device_identifiers": config["device"]["identifiers"],
        "state_topic": config["state_topic"],
        # The state-payload key the entity reads, which for a DP3 switch is
        # its feedback field rather than its own key.
        "state_key": STATE_KEY.search(config["value_template"]).group(1),
        "command_topic": config.get("command_topic"),
        "availability_topics": sorted({entry["topic"] for entry in availability}),
        # Discovered only while allow_control is on; cleared when it is off.
        "control_only": control_only,
    }


def device(serial, with_control, without_control):
    return {
        "serial": serial,
        "device_id": bridge.device_id(serial),
        "entities": [identity(topic, with_control[topic], control_only=topic not in without_control)
                     for topic in sorted(with_control)],
    }


def surface():
    """Both bridges' discovery identity for the fixed serials, as recorded."""
    dp3_id = bridge.device_id(SERIALS["dp3"])
    return {
        "dp3": device(SERIALS["dp3"], bridge.discovery_payloads(dp3_id, control=True),
                      bridge.discovery_payloads(dp3_id, control=False)),
        # The Jackery discovers its controls whatever the policy, so one call
        # is both sides.
        "jackery": device(SERIALS["jackery"],
                          jackery_bridge.discovery_payloads(SERIALS["jackery"]),
                          jackery_bridge.discovery_payloads(SERIALS["jackery"])),
    }


def render(data):
    return json.dumps(data, indent=2, ensure_ascii=False) + "\n"


def recorded():
    return json.loads(CONTRACT.read_text(encoding="utf-8"))


def differences(before, after):
    """Readable lines naming each entity that was added, removed or changed."""
    lines = []
    for name in sorted(set(before) | set(after)):
        old, new = before.get(name, {}), after.get(name, {})
        if old.get("device_id") != new.get("device_id"):
            lines.append(f"{name}: device_id {old.get('device_id')} -> {new.get('device_id')} "
                         "(changes every topic and unique_id of the device)")
        old_rows = {row["key"] + " " + row["component"]: row for row in old.get("entities", [])}
        new_rows = {row["key"] + " " + row["component"]: row for row in new.get("entities", [])}
        for entity in sorted(set(old_rows) - set(new_rows)):
            lines.append(f"{name}: REMOVED {entity} (orphans the entity on every installation)")
        for entity in sorted(set(new_rows) - set(old_rows)):
            lines.append(f"{name}: added {entity}")
        for entity in sorted(set(old_rows) & set(new_rows)):
            for field, value in old_rows[entity].items():
                if new_rows[entity].get(field) != value:
                    lines.append(f"{name}: CHANGED {entity} {field}: {value!r} -> "
                                 f"{new_rows[entity].get(field)!r}")
    return lines


def test_the_mqtt_discovery_identity_matches_the_frozen_contract():
    before, after = recorded(), surface()
    if before != after:
        detail = differences(before, after) or ["the record differs in a field not compared above"]
        if len(detail) > 40:
            # A new hash length changes every row; the first few say enough.
            detail = detail[:40] + [f"... and {len(detail) - 40} more differences"]
        raise AssertionError("\n".join([CHANGED, *detail, "Regenerate deliberately with: "
                                        "python tests/test_mqtt_contract.py --update"]))


def seeded(payloads):
    """``{key: (default_entity_id, object_id)}`` for one device's discovery."""
    return {topic.split("/")[3]: (config["default_entity_id"], config["object_id"])
            for topic, config in payloads.items()}


def test_without_overrides_every_entity_is_seeded_in_the_bridges_scheme():
    """The recorded default entity IDs are the product's own, for any install."""
    assert entity_ids.DP3_ENTITY_IDS == {} and entity_ids.JACKERY_ENTITY_IDS == {}
    schemes = {"dp3": lambda key: bridge.HA_ID_PREFIX + "_" + key,
               # The Jackery controls already carry their public prefix.
               "jackery": lambda key: key if key in jackery_bridge.JACKERY_CONTROLS else "jackery_" + key}
    for name, device in surface().items():
        for row in device["entities"]:
            assert row["default_entity_id"] == f"{row['component']}.{schemes[name](row['key'])}", row


def test_overrides_seed_the_ids_an_installation_already_holds(tmp_path, monkeypatch):
    """Home Assistant creates an MQTT entity under its default_entity_id.

    An installation whose entities were created under name-derived IDs, before
    the bridges seeded any, lists them in an override file. Seeding another ID
    would move an entity away from them whenever Home Assistant creates it
    again, and rename a deleted entity that is rediscovered.
    """
    path = tmp_path / "entity_ids.json"
    path.write_text(json.dumps({
        "dp3": {"bms_batt_soc": "sensor.home_battery_soc"},
        "jackery": {"jackery_ac_output": "switch.garage_explorer_ac"},
    }), "utf-8")
    overrides = entity_ids.load_overrides(path)
    monkeypatch.setattr(bridge, "DP3_ENTITY_IDS", overrides["dp3"])
    monkeypatch.setattr(jackery_bridge, "JACKERY_ENTITY_IDS", overrides["jackery"])
    published = {"dp3": seeded(bridge.discovery_payloads("abc", control=True)),
                 "jackery": seeded(jackery_bridge.discovery_payloads(SERIALS["jackery"]))}
    assert published["dp3"]["bms_batt_soc"][0] == "sensor.home_battery_soc"
    assert published["jackery"]["jackery_ac_output"][0] == "switch.garage_explorer_ac"
    # Keys the file does not name keep the bridges' scheme.
    assert published["dp3"]["pow_get_bms"][0] == "sensor.opendp3_pow_get_bms"
    assert published["jackery"]["bms_batt_soc"][0] == "sensor.jackery_bms_batt_soc"
    # object_id is the older hint for the same ID. Derived from one value, the
    # two cannot name different entities on any Home Assistant release.
    for name, device in published.items():
        for key, (entity_id, object_id) in device.items():
            assert object_id == entity_id.split(".", 1)[1], (name, key, object_id, entity_id)


@pytest.mark.parametrize("content", [
    "not json",
    '["dp3"]',
    '{"ecoflow": {}}',
    '{"dp3": {"bms_batt_soc": "Sensor With Spaces"}}',
    '{"jackery": {"bms_batt_soc": 5}}',
])
def test_an_invalid_override_file_is_refused(tmp_path, content):
    path = tmp_path / "entity_ids.json"
    path.write_text(content, "utf-8")
    with pytest.raises(entity_ids.EntityIdOverrideError):
        entity_ids.load_overrides(path)


def test_no_two_entities_of_either_bridge_are_seeded_with_one_id():
    """Home Assistant would create the second under a _2 suffix."""
    ids = [row["default_entity_id"] for device in surface().values() for row in device["entities"]]
    assert sorted({entity_id for entity_id in ids if ids.count(entity_id) > 1}) == []


def test_every_recorded_state_key_is_in_the_state_payload():
    """Regenerating the record cannot bless an entity that reads nothing.

    A template reading a key the state payload does not carry renders empty,
    so the entity sits unknown with no error anywhere. The test above passes
    again as soon as the record is regenerated; this one keeps failing.
    """
    dp3, _ = bridge.state_payload({"session": {"synthetic": True, "status": "recording"}})
    jackery, _ = jackery_bridge.state_payload({})
    for name, payload in (("dp3", dp3), ("jackery", jackery)):
        for row in recorded()[name]["entities"]:
            assert row["state_key"] in payload, (
                f"{row['discovery_topic']} reads value_json.{row['state_key']}, "
                f"which the {name} state payload never carries")


if __name__ == "__main__":
    if sys.argv[1:] != ["--update"]:
        raise SystemExit("usage: python tests/test_mqtt_contract.py --update")
    CONTRACT.parent.mkdir(exist_ok=True)
    # Written with LF on every platform, so regenerating an unchanged record
    # on Windows is not a whole-file diff.
    with open(CONTRACT, "w", encoding="utf-8", newline="\n") as handle:
        handle.write(render(surface()))
    print("Updated tests/contract/mqtt_identity.json; review the diff before committing.")
