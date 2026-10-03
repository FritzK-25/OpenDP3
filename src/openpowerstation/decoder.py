"""Presence-aware decoding. No last-known-value merge takes place here."""
from dataclasses import dataclass
import math
import struct

from google.protobuf.message import DecodeError, Message
from .vendor.pb.mr521_pb2 import DisplayPropertyUpload
from .protocol import Packet, parse_packet

@dataclass(frozen=True)
class Field:
    key: str
    label: str
    unit: str
    group: str

FIELDS = [
    Field("bms_max_cell_temp", "BMS maximum cell temperature", "°C", "temperature"),
    Field("bms_min_cell_temp", "BMS minimum cell temperature", "°C", "temperature"),
    Field("bms_max_mos_temp", "BMS maximum MOS temperature", "°C", "temperature"),
    Field("bms_min_mos_temp", "BMS minimum MOS temperature", "°C", "temperature"),
    Field("cms_batt_temp", "CMS battery temperature", "°C", "temperature"),
    Field("bms_batt_soc", "Main battery SOC", "%", "soc"),
    Field("cms_batt_soc", "System SOC", "%", "soc"),
    Field("cms_batt_soh", "System SOH", "%", "soc"),
    Field("bms_batt_soh", "Main battery SOH", "%", "soc"),
    Field("pow_in_sum_w", "Total input", "W", "power"),
    Field("pow_out_sum_w", "Total output", "W", "power"),
    Field("pow_get_ac_in", "AC input", "W", "power"),
    Field("pow_get_ac_lv_out", "AC LV output (raw sign)", "W", "power"),
    Field("pow_get_ac_hv_out", "AC HV output (raw sign)", "W", "power"),
    Field("pow_get_pv_h", "PV HV input", "W", "power"),
    Field("pow_get_pv_l", "PV LV input", "W", "power"),
    Field("pow_get_bms", "Battery power (raw sign)", "W", "power"),
    # The DP3's own output-state bitmasks. These are deliberately kept raw in
    # evidence storage; the Home Assistant bridge applies the same low-bit
    # interpretation as the pinned ha-ef-ble DP3 implementation.
    Field("flow_info_ac_hv_out", "AC HV output state (raw bitmask)", "", "state"),
    Field("flow_info_ac_lv_out", "AC LV output state (raw bitmask)", "", "state"),
    Field("cms_bms_run_state", "BMS run state (raw)", "", "state"),
    Field("cms_chg_dsg_state", "Charge/discharge state (raw)", "", "state"),
    Field("plug_in_info_ac_charger_flag", "AC connected", "", "state"),
    Field("plug_in_info_pv_h_type", "PV HV source type (raw)", "", "state"),
    Field("plug_in_info_pv_l_type", "PV LV source type (raw)", "", "state"),
    Field("errcode", "Device error (raw code)", "", "state"),
    Field("bms_err_code", "BMS error (raw code)", "", "state"),
    Field("mppt_err_code", "MPPT error (raw code)", "", "state"),
    Field("inv_err_code", "Inverter error (raw code)", "", "state"),
]
# Do not substitute configured charge limits for measured volts/amps.
UNAVAILABLE = ["Pack voltage", "Pack current", "PV HV voltage", "PV HV current",
               "PV LV voltage", "PV LV current"]
for n in (1, 2):
    FIELDS += [Field(f"extra{n}_soc", f"Extra battery {n} SOC (community mapping)", "%", "soc"),
               Field(f"extra{n}_temperature", f"Extra battery {n} temperature (unverified)", "°C", "temperature")]
FIELD_MAP = {f.key: f for f in FIELDS}
for key in ['pd_err_code','llc_err_code','llc_inv_err_code','dcdc_err_code',
            'plug_in_info_5p8_err_code','plug_in_info_acp_err_code',
            'plug_in_info_4p8_1_err_code','plug_in_info_4p8_2_err_code',
            'plug_in_info_dcp_err_code','plug_in_info_dcp2_err_code']:
    field = Field(key,key.replace('_',' ')+' (raw)','', 'state')
    FIELDS.append(field)
    FIELD_MAP[key] = field

@dataclass
class Decoded:
    fields: dict
    measurements: dict
    quality: dict
    status: str

def present_fields(msg: Message) -> dict:
    result = {}
    for descriptor, value in msg.ListFields():
        if descriptor.is_repeated:
            result[descriptor.name] = [
                present_fields(v) if descriptor.type == descriptor.TYPE_MESSAGE else v
                for v in value
            ]
        elif descriptor.type == descriptor.TYPE_MESSAGE:
            result[descriptor.name] = present_fields(value)
        elif descriptor.type == descriptor.TYPE_BYTES:
            result[descriptor.name] = {"bytes_hex": value.hex()}
        elif isinstance(value, float) and not math.isfinite(value):
            result[descriptor.name] = {"nonfinite": str(value)}
        else:
            result[descriptor.name] = value
    return result

def decode(packet: Packet) -> Decoded:
    if (packet.src, packet.cmd_set, packet.cmd_id) != (2, 0xFE, 0x15):
        return Decoded({}, {}, {}, "unknown_message")
    msg = DisplayPropertyUpload()
    try:
        msg.ParseFromString(packet.payload)
    except DecodeError:
        return Decoded({}, {}, {}, "invalid_protobuf")
    fields = present_fields(msg)
    values, quality = {}, {}
    for key in FIELD_MAP:
        value = fields.get(key)
        if isinstance(value, (float, int)) and math.isfinite(value):
            values[key] = value
            quality[key] = "observed"
    for n in (1, 2):
        # Disconnected slots still publish zero-filled reserved data. Do not
        # manufacture batteries or temperatures from it. A partial packet must
        # establish connection in the same observation before using this heuristic.
        if not fields.get(f'plug_in_info_4p8_{n}_in_flag'):
            continue
        resv = fields.get(f"plug_in_info_4p8_{n}_resv", {}).get("resv_info", [])
        if len(resv) < 15:
            continue
        soc = struct.unpack("<f", struct.pack("<I", resv[0] & 0xFFFFFFFF))[0]
        if math.isfinite(soc):
            values[f"extra{n}_soc"] = soc
            quality[f"extra{n}_soc"] = "community_mapping"
        b0, b1 = resv[13] & 255, (resv[13] >> 8) & 255
        values[f"extra{n}_temperature"] = b1 if b1 >= 25 else b0
        quality[f"extra{n}_temperature"] = "unverified"
    return Decoded(fields, values, quality, "decoded")

def decode_raw(raw: bytes) -> Decoded:
    return decode(parse_packet(raw))
