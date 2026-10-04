"""DP3 output-state telemetry used to back the Home Assistant switches."""
from conftest import add
from openpowerstation.decoder import FIELD_MAP, decode_raw


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


def test_an_ac_output_turning_on_or_off_is_a_state_change(evidence, packet):
    """Whether a DP3 output really changed is on the timeline, not only in frames.

    A DP3 ``control`` event records that the write left the radio; the DP3
    sends no acknowledgement. Its output feedback was stored only as raw
    measurements, so a command the DP3 obeyed and one it ignored left the same
    events behind. The low two bits carry the outlet state the switches show
    (0 off, 2 and 3 on); the bits above them move without the output changing.
    """
    store, rec = evidence
    add(rec, packet(seq=1, flow_info_ac_hv_out=0, flow_info_ac_lv_out=2), 0)
    add(rec, packet(seq=2, flow_info_ac_hv_out=4, flow_info_ac_lv_out=6), 1)
    add(rec, packet(seq=3, flow_info_ac_hv_out=7, flow_info_ac_lv_out=4), 2)
    events = [tuple(row) for row in store.conn.execute(
        "SELECT t, detail FROM events WHERE kind='state_change' ORDER BY id")]
    assert events == [
        (2.0, "flow_info_ac_hv_out: 4 -> 7 (raw; output off -> on)."),
        (2.0, "flow_info_ac_lv_out: 6 -> 4 (raw; output on -> off)."),
    ]
