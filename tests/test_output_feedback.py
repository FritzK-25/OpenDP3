"""DP3 output-state telemetry used to back the Home Assistant switches."""
from opendp3.decoder import FIELD_MAP, decode_raw


def test_ac_output_flow_fields_are_normalized(packet):
    decoded = decode_raw(packet(flow_info_ac_hv_out=0, flow_info_ac_lv_out=2))
    assert decoded.measurements["flow_info_ac_hv_out"] == 0
    assert decoded.measurements["flow_info_ac_lv_out"] == 2
    assert decoded.quality["flow_info_ac_hv_out"] == "observed"
    assert decoded.quality["flow_info_ac_lv_out"] == "observed"


def test_ac_output_flow_fields_remain_raw_state_bitmasks():
    assert FIELD_MAP["flow_info_ac_hv_out"].group == "state"
    assert FIELD_MAP["flow_info_ac_lv_out"].group == "state"
    assert not FIELD_MAP["flow_info_ac_hv_out"].unit
    assert not FIELD_MAP["flow_info_ac_lv_out"].unit
