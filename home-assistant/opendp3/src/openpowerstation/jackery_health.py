"""Fault detection for Jackery observations, kept pure so it can be tested.

The downsampler thins ordinary history, so this module is what decides "ordinary"
means.  Anything flagged here pins an incident window, and pinned windows are
never thinned.  The bias is deliberate and asymmetric: a false positive costs a
few hundred kilobytes of frames kept forever, a false negative destroys the only
recording of a fault.  When a rule is uncertain, it errs toward flagging.  The
one exception to "forever" is a capture gap: it is about the collector, not the
station, so its pin lapses after 30 days, or sooner under the byte cap all such
collector pins share (events.COLLECTOR_PIN_KINDS).

Every threshold is region- and model-agnostic where it can be.  The AC rules in
particular avoid assuming a 120 V or 230 V mains, because guessing wrong would
either flag every reading or none of them.

The battery temperature and state-of-charge rules are the DP3's too, so they
live in health.py; this module adds what only the Explorer has.
"""
from .health import JACKERY_ROLES, Thresholds, comparable, measurement_findings
from .jackery_fields import MAPPED_PROPERTIES


# An unmapped field that keeps producing new values is a measurement, not a
# state code. Stop treating its changes as novel once it has shown this many,
# or a counter-like field would pin every window forever.
UNMAPPED_STATE_LIMIT = 32


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
    old = comparable(previous, "errcode")
    if errcode is not None and errcode != 0 and (not old or old[1] != errcode):
        flag("device_error", f"errcode reported raw code {errcode:g}; meaning is unverified.")

    # Battery temperature and state of charge, by the rules the DP3 runs too.
    for kind, detail in measurement_findings(JACKERY_ROLES, measurements, previous, t, limits):
        flag(kind, detail)

    # AC rules only apply while the inverter is on; 0 V with the output off is
    # correct, not a fault.
    if measurements.get("jackery_ac_output"):
        voltage = measurements.get("jackery_ac_voltage_v")
        if voltage is not None and not limits.ac_voltage_min <= voltage <= limits.ac_voltage_max:
            flag("suspect_telemetry", f"AC output on but voltage is {voltage:g} V.")
        hertz = measurements.get("jackery_ac_frequency_hz")
        if hertz is not None and not limits.ac_hz_min <= hertz <= limits.ac_hz_max:
            flag("suspect_telemetry", f"AC output on but frequency is {hertz:g} Hz.")
        old = comparable(previous, "jackery_ac_voltage_v")
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
