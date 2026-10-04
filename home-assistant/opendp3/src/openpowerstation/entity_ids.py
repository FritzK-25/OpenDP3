"""The Home Assistant entity ID each discovered entity is created under.

Home Assistant finds an MQTT entity by unique_id, but chooses its entity_id
once, when it creates the registry entry, from the discovery payload's
default_entity_id. Seeding a different ID is harmless only while the registry
entry lives. When one is created again -- the device deleted in Home
Assistant, the MQTT integration removed and re-added, a rebuilt instance --
the entity lands on the seeded ID, and Home Assistant 2026.9 also renames a
deleted entity that is rediscovered to the seeded ID. Every template reading
the old ID then goes unavailable.

By default every entity keeps the bridge's own scheme,
<component>.opendp3_<key> or <component>.jackery_<key>. An installation whose
entities were created under other IDs (name-derived ones, from before the
bridges seeded any) keeps them by listing them in a JSON file named by the
OPENDP3_ENTITY_IDS environment variable; the Home Assistant app sets it when
its configuration folder holds entity_ids.json:

    {"dp3": {"bms_batt_soc": "sensor.my_battery_soc"},
     "jackery": {"bms_batt_soc": "sensor.my_explorer_soc"}}

Keys are discovery keys (the fourth discovery topic segment). Never "tidy" a
value there: default_entity_id is only a creation-time hint, so changing it
renames nothing that exists and moves every later re-creation away from the
ID the installation's dashboards and automations read.
"""
import json
import os
from pathlib import Path
import re

OVERRIDES_ENV = "OPENDP3_ENTITY_IDS"
DEVICES = ("dp3", "jackery")
ENTITY_ID = re.compile(r"[a-z_]+\.[a-z0-9_]+")


class EntityIdOverrideError(ValueError):
    """The overrides file is unreadable or names an invalid entity ID."""


def load_overrides(path) -> dict[str, dict[str, str]]:
    """``{device: {key: entity_id}}`` from one overrides file, validated."""
    try:
        raw = json.loads(Path(path).read_text("utf-8"))
    except (OSError, ValueError) as exc:
        raise EntityIdOverrideError(f"entity ID overrides unreadable ({type(exc).__name__})") from None
    if not isinstance(raw, dict) or set(raw) - set(DEVICES):
        raise EntityIdOverrideError("entity ID overrides must be an object keyed by " + " and ".join(DEVICES))
    tables = {}
    for device in DEVICES:
        table = raw.get(device, {})
        if not isinstance(table, dict) or not all(
                isinstance(key, str) and isinstance(value, str) and ENTITY_ID.fullmatch(value)
                for key, value in table.items()):
            raise EntityIdOverrideError(f"entity ID overrides for {device} must map keys to entity IDs")
        tables[device] = dict(table)
    return tables


def _from_environment() -> dict[str, dict[str, str]]:
    path = os.environ.get(OVERRIDES_ENV)
    return load_overrides(path) if path else {device: {} for device in DEVICES}


_OVERRIDES = _from_environment()
# The DELTA Pro 3 and the Explorer 1000 v2, keyed by discovery key.
DP3_ENTITY_IDS = _OVERRIDES["dp3"]
JACKERY_ENTITY_IDS = _OVERRIDES["jackery"]


def seeded_ids(table, key, scheme):
    """``(object_id, default_entity_id)`` for one discovered entity.

    ``scheme`` is the bridge's own ``<component>.<object_id>`` for a key that
    the overrides do not name. object_id is the older hint for the same ID;
    deriving both from one value means they can never name different entities.
    """
    entity_id = table.get(key, scheme)
    return entity_id.split(".", 1)[1], entity_id
