"""History queries must remain presence-aware when using the fast coverage path."""
from openpowerstation.queries import snapshot
from openpowerstation.recorder import Recorder
from conftest import add


def test_coverage_uses_visible_samples_and_falls_back_to_actual_older_receipts(evidence, packet):
    store, recorder = evidence
    add(recorder, packet(seq=1, bms_max_cell_temp=24, bms_batt_soc=80), 0)
    current = packet(seq=2, bms_batt_soc=79)
    add(recorder, current, 100)
    add(recorder, current, 101)  # An uncertain repeat must not refresh the age.
    snap = snapshot(store.path, recorder.sid, end_t=101, span=10)
    coverage = {row["key"]: row for row in snap["coverage"]}
    assert coverage["bms_batt_soc"]["value"] == 79
    assert coverage["bms_batt_soc"]["last_t"] == 100
    assert coverage["bms_max_cell_temp"]["value"] == 24
    assert coverage["bms_max_cell_temp"]["last_t"] == 0
    assert "bms_max_cell_temp" not in snap["series"]
    assert coverage["extra1_soc"]["value"] is None


def test_fallback_never_leaks_future_values_or_another_session(evidence, packet):
    store, recorder = evidence
    add(recorder, packet(seq=1, bms_max_cell_temp=24), 0)
    add(recorder, packet(seq=2, bms_batt_soc=79), 100)
    add(recorder, packet(seq=3, bms_max_cell_temp=30), 200)
    other = Recorder(store, utc_ns=recorder.start_utc + 1000, mono_ns=0, synthetic=True)
    add(other, packet(seq=4, bms_max_cell_temp=40, bms_batt_soc=90), 0)
    snap = snapshot(store.path, recorder.sid, end_t=100, span=10)
    coverage = {row["key"]: row for row in snap["coverage"]}
    assert coverage["bms_max_cell_temp"]["value"] == 24
    assert coverage["bms_max_cell_temp"]["last_t"] == 0
    assert coverage["bms_batt_soc"]["value"] == 79
