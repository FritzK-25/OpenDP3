"""Fault detection for Jackery observations, kept pure so it can be tested.

The downsampler thins ordinary history, so this module is what decides "ordinary"
means.  Anything flagged here pins an incident window, and pinned windows are
never thinned.  The bias is deliberate and asymmetric: a false positive costs a
few hundred kilobytes of frames kept forever, a false negative destroys the only
recording of a fault.  When a rule is uncertain, it errs toward flagging.

Every threshold is region- and model-agnostic where it can be.  The AC rules in
particular avoid assuming a 120 V or 230 V mains, because guessing wrong would
either flag every reading or none of them.
"""
from dataclasses import dataclass

from .jackery_fields import MAPPED_PROPERTIES


@dataclass(frozen=True)
class Thresholds:
    # Battery temperature: mirrors the DP3 recorder's own jump detector, plus an
    # absolute band that catches a sensor failing to a rail instead of stepping.
    temperature_jump: float = 10.0
    temperature_window: float = 5.0
    temperature_min: float = -20.0
    temperature_max: float = 60.0
    # A ~1 kWh station discharging at its rated output moves roughly 1.5 %/min,
    # so several percent within a few seconds is a discontinuity, not use. BMS
    # recalibration produces exactly this, and is worth keeping in full.
    soc_step: float = 5.0
    soc_window: float = 10.0
    # Deliberately wide: any mains in the world sits inside this. Outside it,
    # while the inverter is actually on, is a real fault rather than a region.
    ac_voltage_min: float = 90.0
    ac_voltage_max: float = 260.0
    ac_hz_min: float = 45.0
    ac_hz_max: float = 65.0
    # Nominal is unknown, so judge AC output by how fast it moves instead.
    ac_voltage_step: float = 0.15
    # A missing sample is evidence too. Scaled to the poll rate, with a floor so
    # a fast poll rate does not flag ordinary scheduling jitter.
    gap_factor: float = 4.0
    gap_floor: float = 15.0


# An unmapped field that keeps producing new values is a measurement, not a
# state code. Stop treating its changes as novel once it has shown this many,
# or a counter-like field would pin every window forever.
UNMAPPED_STATE_LIMIT = 32


def _previous(previous, key):
    entry = previous.get(key)
    return entry if entry and isinstance(entry[1], (int, float)) else None


def findings(measurements, properties, previous, unmapped_seen, t, *,
             expected_interval=None, last_t=None, thresholds=Thresholds()):
    """Return ``(kind, detail, pin)`` for everything wrong with one observation.

    ``previous`` is the recorder's ``{key: (t, value)}`` map from before this
    observation; ``unmapped_seen`` is a session-scoped ``{key: set(values)}``
    that this function updates in place.
    """
    found = []
    limits = thresholds

    def flag(kind, detail):
        found.append((kind, detail, True))

    # A gap in capture, judged against the rate we expect to be polling at.
    if expected_interval and last_t is not None:
        allowed = max(limits.gap_factor * expected_interval, limits.gap_floor)
        if t - last_t > allowed:
            flag("capture_gap", f"No observation for {t-last_t:.1f}s; expected one every "
                                f"{expected_interval:g}s.")

    errcode = measurements.get("errcode")
    old = _previous(previous, "errcode")
    if errcode is not None and errcode != 0 and (not old or old[1] != errcode):
        flag("device_error", f"errcode reported raw code {errcode:g}; meaning is unverified.")

    temperature = measurements.get("cms_batt_temp")
    if temperature is not None:
        if not limits.temperature_min <= temperature <= limits.temperature_max:
            flag("suspect_telemetry", f"cms_batt_temp {temperature:g} °C is outside "
                                      f"{limits.temperature_min:g}..{limits.temperature_max:g} °C.")
        old = _previous(previous, "cms_batt_temp")
        if (old and 0 < t-old[0] <= limits.temperature_window
                and abs(temperature-old[1]) >= limits.temperature_jump):
            flag("suspect_telemetry", f"cms_batt_temp: {old[1]:g} -> {temperature:g} °C in {t-old[0]:.3f}s.")

    soc = measurements.get("cms_batt_soc")
    old = _previous(previous, "cms_batt_soc")
    if (soc is not None and old and 0 < t-old[0] <= limits.soc_window
            and abs(soc-old[1]) >= limits.soc_step):
        flag("suspect_telemetry", f"cms_batt_soc: {old[1]:g} -> {soc:g} % in {t-old[0]:.3f}s.")

    # AC rules only apply while the inverter is on; 0 V with the output off is
    # correct, not a fault.
    if measurements.get("jackery_ac_output"):
        voltage = measurements.get("jackery_ac_voltage_v")
        if voltage is not None and not limits.ac_voltage_min <= voltage <= limits.ac_voltage_max:
            flag("suspect_telemetry", f"AC output on but voltage is {voltage:g} V.")
        hertz = measurements.get("jackery_ac_frequency_hz")
        if hertz is not None and not limits.ac_hz_min <= hertz <= limits.ac_hz_max:
            flag("suspect_telemetry", f"AC output on but frequency is {hertz:g} Hz.")
        old = _previous(previous, "jackery_ac_voltage_v")
        if (voltage and old and old[1]
                and abs(voltage-old[1])/old[1] >= limits.ac_voltage_step):
            flag("suspect_telemetry", f"AC output voltage moved {old[1]:g} -> {voltage:g} V "
                                      f"while the inverter stayed on.")

    # Unknown unknowns: a property whose meaning we have never verified taking a
    # value this session has not seen before.
    for key, value in sorted(properties.items()):
        if key in MAPPED_PROPERTIES or not isinstance(value, (int, float)):
            continue
        seen = unmapped_seen.setdefault(key, set())
        if len(seen) >= UNMAPPED_STATE_LIMIT:
            continue
        if value not in seen:
            if seen:
                flag("unmapped_change", f"Unmapped property {key} took new value {value!r}; "
                                        f"its meaning is unverified.")
            seen.add(value)
            if len(seen) >= UNMAPPED_STATE_LIMIT:
                found.append(("unmapped_change",
                              f"Unmapped property {key} has shown {UNMAPPED_STATE_LIMIT} "
                              f"distinct values; treating it as a measurement, not a state.",
                              False))
    return found
