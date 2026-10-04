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


# Chart groups in display order. Every chart of a session -- the desktop's and
# the evidence export's -- draws one panel per group its fields use, so a field
# the registry gains is charted wherever the session is shown. State rows go
# last, under the shared time axis.
CHART_GROUPS = ("temperature", "power", "soc", "voltage", "frequency", "duration", "state")


def is_jackery(session):
    # Compatibility with recordings made before explicit device metadata existed.
    return session.get("firmware", "").startswith("Jackery")


def device(session):
    """``jackery`` or ``dp3``: which registry, headline readings and labels a session gets."""
    return "jackery" if is_jackery(session) else "dp3"


def fields_for_session(session):
    return JACKERY_OBSERVATIONS if is_jackery(session) else DP3_FIELDS


def chart_groups(session):
    used = {field.group for field in fields_for_session(session)}
    return tuple(group for group in CHART_GROUPS if group in used)


def unavailable_for_session(session):
    return () if is_jackery(session) else UNAVAILABLE
