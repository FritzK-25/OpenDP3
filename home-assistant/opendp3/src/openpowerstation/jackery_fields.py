"""Jackery device property names, shared by every Jackery transport.

The station returns the same property keys over local BLE that Jackery's own
cloud API returned, so this mapping is device schema rather than transport
detail.  It lives on its own so the MQTT bridge can import it without pulling
in a Bluetooth stack.
"""
import math

JACKERY_FIELDS = (
    ("bms_batt_soc", "Main battery SOC", "%", "sensor", "battery", "measurement", False),
    ("cms_batt_soc", "System SOC", "%", "sensor", "battery", "measurement", False),
    ("cms_batt_temp", "CMS battery temperature", "°C", "sensor", "temperature", "measurement", False),
    ("pow_in_sum_w", "Total input", "W", "sensor", "power", "measurement", False),
    ("pow_out_sum_w", "Total output", "W", "sensor", "power", "measurement", False),
    ("jackery_ac_input_w", "AC input", "W", "sensor", "power", "measurement", False),
    ("jackery_ac_voltage_v", "AC output voltage", "V", "sensor", "voltage", "measurement", False),
    ("jackery_ac_frequency_hz", "AC output frequency", "Hz", "sensor", "frequency", "measurement", False),
    ("jackery_charge_time_h", "Estimated charging time", "h", "sensor", "duration", "measurement", True),
    ("jackery_output_time_h", "Estimated output time", "h", "sensor", "duration", "measurement", False),
    ("jackery_ac_output", "AC output", "", "switch", "power", "", False),
    ("jackery_dc_output", "DC output", "", "switch", "power", "", False),
    ("jackery_emergency_charge", "Emergency charge mode", "", "sensor", "", "", True),
    ("jackery_charge_mode", "Charge mode", "", "sensor", "", "", True),
    ("jackery_low_power_mode", "Low-power mode", "", "sensor", "", "", True),
    ("jackery_battery_save", "Battery saving mode", "", "select", "", "", False),
    ("jackery_screen_timeout", "Screen timeout", "", "select", "", "", False),
    ("jackery_energy_saving_mode", "Energy-saving mode", "", "sensor", "", "", True),
    ("jackery_auto_off", "Auto-off setting", "", "sensor", "", "", True),
    ("jackery_light_mode", "Light mode", "", "sensor", "", "", True),
    ("errcode", "Device error (raw code)", "", "sensor", "", "", True),
)


# Raw property names map_properties understands.  Anything else the station
# sends is unmapped: its units are unverified so it is never published, but a
# value never seen before is still worth flagging.
MAPPED_PROPERTIES = frozenset({
    "rb", "bt", "op", "ip", "ec", "acip", "acov", "acohz", "it", "ot",
    "sfc", "cs", "lps", "pm", "ast", "lm", "oac", "odc", "sltb",
})


# Fields whose extremes inside a bucket are worth keeping when history is
# thinned: a peak draw or a temperature spike is the reason a minute mattered.
PEAK_KEYS = ("pow_out_sum_w", "pow_in_sum_w", "cms_batt_temp")


def map_properties(properties: dict) -> dict:
    """Map only stable, unit-known Jackery properties into dashboard fields."""
    if not isinstance(properties, dict):
        return {}
    measurements = {}

    def numeric(value, minimum=None, maximum=None):
        return (isinstance(value, (int, float)) and not isinstance(value, bool)
                and math.isfinite(value)
                and (minimum is None or value >= minimum)
                and (maximum is None or value <= maximum))

    if numeric(properties.get("rb"), 0, 100):
        measurements["bms_batt_soc"] = float(properties["rb"])
        measurements["cms_batt_soc"] = float(properties["rb"])
    if numeric(properties.get("bt"), -1000, 3000):
        measurements["cms_batt_temp"] = float(properties["bt"]) / 10
    if numeric(properties.get("op"), 0):
        measurements["pow_out_sum_w"] = float(properties["op"])
    if numeric(properties.get("ip"), 0):
        measurements["pow_in_sum_w"] = float(properties["ip"])
    if numeric(properties.get("ec"), 0):
        measurements["errcode"] = float(properties["ec"])
    sltb = properties.get("sltb")
    if type(sltb) is int and sltb in {1, 2, 3}:
        # The Explorer reports the selected screen preset as an integer enum
        # rather than echoing the command's minute count: 1=no timeout, 2=2m,
        # 3=2h. Reject floats and booleans so malformed status fails closed.
        measurements["jackery_screen_timeout"] = float(sltb)
    for source, key, scale in (("acip", "jackery_ac_input_w", 1),
                               ("acov", "jackery_ac_voltage_v", 0.1),
                               ("acohz", "jackery_ac_frequency_hz", 1),
                               ("it", "jackery_charge_time_h", 0.01),
                               ("ot", "jackery_output_time_h", 0.1),
                               ("sfc", "jackery_emergency_charge", 1),
                               ("cs", "jackery_charge_mode", 1),
                               ("lps", "jackery_low_power_mode", 1),
                               ("pm", "jackery_energy_saving_mode", 1),
                               ("ast", "jackery_auto_off", 1),
                               ("lm", "jackery_light_mode", 1)):
        if numeric(properties.get(source), 0):
            measurements[key] = float(properties[source]) * scale
    for source, key in (("oac", "jackery_ac_output"), ("odc", "jackery_dc_output")):
        if numeric(properties.get(source), 0):
            measurements[key] = float(bool(properties[source]))
    return measurements
