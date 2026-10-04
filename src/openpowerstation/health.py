"""Anomaly rules shared by the DP3 and Jackery recorders, kept pure to be tested.

Whatever a rule here flags pins an incident window, and a pinned window is
neither thinned (the Jackery) nor aged out after seven days (the DP3). The bias
jackery_health.py describes is device-agnostic -- a false positive keeps a few
hundred kilobytes, a false negative destroys the only recording of a fault --
so both recorders run these rules rather than one copy each. They drifted when
there were two: the DP3 had only the temperature jump, so a DP3 cell
temperature at a rail or a BMS recalibration step pinned nothing.

Each device names which of its keys play which part (Roles); the rules know
nothing else about either. Before the DP3 took the band and the step, both were
replayed over 186,655 recorded DP3 observations in 39 sessions (cell and MOS
temperatures 20..29 °C, SoC 30..80 %): neither fired once.
"""
from dataclasses import dataclass


@dataclass(frozen=True)
class Thresholds:
    # Battery temperature: a jump between two close samples, plus an absolute
    # band that catches a sensor failing to a rail instead of stepping.
    # config.json's temperature_jump and temperature_window set the jump for
    # both devices (configured()).
    temperature_jump: float = 10.0
    temperature_window: float = 5.0
    temperature_min: float = -20.0
    temperature_max: float = 60.0
    # A ~1 kWh station discharging at its rated output moves roughly 1.5 %/min,
    # and the 4 kWh DP3 less, so several percent within a few seconds is a
    # discontinuity, not use. BMS recalibration produces exactly this, and is
    # worth keeping in full.
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


def configured(config) -> Thresholds:
    """The thresholds a Config sets; the rest keep their defaults."""
    return Thresholds(temperature_jump=config.temperature_jump,
                      temperature_window=config.temperature_window)


@dataclass(frozen=True)
class Roles:
    """Which of one device's measurement keys each shared rule reads."""
    # The absolute band and the jump rule.
    battery_temperatures: tuple = ()
    # The jump rule only. A MOSFET runs hotter than a cell under load, so the
    # cell band would flag its ordinary operation.
    temperatures: tuple = ()
    # The step rule.
    socs: tuple = ()


# Every observed DP3 temperature and SoC (decoder.FIELDS). The extra-battery
# values are heuristic, community-mapped readings and stay out of every rule.
DP3_ROLES = Roles(
    battery_temperatures=("bms_max_cell_temp", "bms_min_cell_temp", "cms_batt_temp"),
    temperatures=("bms_max_mos_temp", "bms_min_mos_temp"),
    socs=("bms_batt_soc", "cms_batt_soc"),
)
JACKERY_ROLES = Roles(battery_temperatures=("cms_batt_temp",), socs=("cms_batt_soc",))


def comparable(previous, key):
    """The previous ``(t, value)`` for ``key`` when it holds a number, else None."""
    entry = previous.get(key)
    return entry if entry and isinstance(entry[1], (int, float)) else None


def measurement_findings(roles, measurements, previous, t, thresholds):
    """``(kind, detail)`` for every shared rule one observation breaks.

    ``previous`` is the recorder's ``{key: (t, value)}`` from before this
    observation, which a segment boundary clears; ``t`` is its receipt time.
    """
    limits = thresholds
    found = []
    for key in roles.battery_temperatures + roles.temperatures:
        value = measurements.get(key)
        if value is None:
            continue
        old = comparable(previous, key)
        # Once per value: a sensor stuck at a rail repeats one number, about
        # every second on a DP3, and an event for each would never stop.
        if (key in roles.battery_temperatures
                and not limits.temperature_min <= value <= limits.temperature_max
                and (old is None or old[1] != value)):
            found.append(("suspect_telemetry", f"{key} {value:g} °C is outside "
                          f"{limits.temperature_min:g}..{limits.temperature_max:g} °C."))
        if (old and 0 < t-old[0] <= limits.temperature_window
                and abs(value-old[1]) >= limits.temperature_jump):
            found.append(("suspect_telemetry", f"{key}: {old[1]:g} -> {value:g} °C "
                          f"in {t-old[0]:.3f}s receipt time."))
    for key in roles.socs:
        value = measurements.get(key)
        old = comparable(previous, key)
        if (value is not None and old and 0 < t-old[0] <= limits.soc_window
                and abs(value-old[1]) >= limits.soc_step):
            found.append(("suspect_telemetry", f"{key}: {old[1]:g} -> {value:g} % "
                          f"in {t-old[0]:.3f}s receipt time."))
    return found
