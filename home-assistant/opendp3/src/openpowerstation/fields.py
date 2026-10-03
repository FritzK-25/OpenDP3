"""Known presentation/export fields; protocol decoders keep their own allowlists."""
from .decoder import FIELDS as DP3_FIELDS, Field, UNAVAILABLE
from .jackery_fields import JACKERY_FIELDS


def _group(unit):
    return {"°C": "temperature", "W": "power", "%": "soc", "V": "voltage",
            "Hz": "frequency", "h": "duration"}.get(unit, "state")


JACKERY_OBSERVATIONS = tuple(
    Field(key, label, unit, _group(unit))
    for key, label, unit, component, device_class, state_class, diagnostic in JACKERY_FIELDS
    # This string-valued selector is derived for MQTT, not a stored measurement.
    if key != "jackery_battery_save"
)


def is_jackery(session):
    # Compatibility with recordings made before explicit device metadata existed.
    return session.get("firmware", "").startswith("Jackery")


def fields_for_session(session):
    return JACKERY_OBSERVATIONS if is_jackery(session) else DP3_FIELDS


def unavailable_for_session(session):
    return () if is_jackery(session) else UNAVAILABLE
