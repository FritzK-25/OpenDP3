"""Thinning ordinary history must never cost evidence.

Every test here is really one question: after the downsampler runs, can the
surviving rows still be re-derived from bytes the station actually sent? That is
why nothing is aggregated and no frame is ever synthesized.
"""
import json

import pytest

from opendp3.jackery_fields import PEAK_KEYS, map_properties
from opendp3.recorder import Recorder
from opendp3.storage import Store
from opendp3.validation import verify_recording

SERIAL = "123456789012345"
DAY_NS = 86_400 * 10**9


def base_properties(**overrides):
    properties = {"acip": 0, "acohz": 0, "acov": 0, "acpsp": 0, "acpss": 0, "ast": 120,
                  "bs": 0, "bt": 290, "cip": 0, "cs": 0, "ec": 0, "ip": 0, "it": 0,
                  "lm": 0, "lps": 0, "oac": 0, "odc": 0, "op": 100, "ot": 999,
                  "pal": 0, "pm": 720, "pmb": 0, "rb": 100, "sfc": 0, "sltb": 1, "ta": 0}
    properties.update(overrides)
    return properties


def observe(recorder, t, **overrides):
    properties = base_properties(**overrides)
    fields = {"device": {"serial": SERIAL, "model": "Explorer 1000 v2", "transport": "ble"},
              "properties": properties}
    raw = json.dumps(fields, ensure_ascii=False, sort_keys=True).encode()
    recorder.ingest_observation(raw, fields, map_properties(properties),
                                utc_ns=recorder.start_utc + int(t*1e9),
                                mono_ns=recorder.start_mono + int(t*1e9),
                                quality="ble_observed")


@pytest.fixture
def jackery(tmp_path):
    with Store(tmp_path/"jackery.sqlite", reserve_bytes=0) as store:
        yield store, Recorder(store, utc_ns=1_000_000_000_000_000_000, mono_ns=0,
                              firmware=f"Jackery Explorer 1000 v2 {SERIAL}",
                              conditions="Jackery local BLE transport",
                              expected_interval=3, retain_days=None)


def frame_times(store):
    return [round(r[0], 3) for r in store.conn.execute("SELECT t FROM frames ORDER BY t")]


def test_ordinary_history_thins_to_one_sample_a_minute(jackery):
    store, rec = jackery
    for step in range(60):            # three minutes of 3s polling
        observe(rec, step*3)
    assert len(frame_times(store)) == 60
    summary = store.downsample(rec.start_utc + 5*DAY_NS, bucket_seconds=60)
    assert summary["buckets"] == 3
    assert frame_times(store) == [0.0, 60.0, 120.0]
    assert summary["deleted"] == 57


def test_a_flagged_window_is_never_thinned(jackery):
    store, rec = jackery
    for step in range(60):
        observe(rec, step*3)
    # A fault anywhere in the second minute pins 300s either side of it.
    store.incident(rec.sid, 90, "device_error", before=30, after=30)
    store.downsample(rec.start_utc + 5*DAY_NS, bucket_seconds=60)
    survivors = frame_times(store)
    # Everything inside 60..120 survives at full 3s fidelity.
    assert [t for t in survivors if 60 <= t <= 120] == [float(x) for x in range(60, 121, 3)]
    assert 0.0 in survivors


def test_the_grace_period_protects_recent_history(jackery):
    store, rec = jackery
    for step in range(60):
        observe(rec, step*3)
    # Nothing is older than the default 48h grace, so nothing may be touched.
    summary = store.downsample(rec.start_utc + 60*10**9, bucket_seconds=60)
    assert summary == {"scanned": 0, "kept": 0, "deleted": 0, "buckets": 0, "pinned_skipped": 0}
    assert len(frame_times(store)) == 60


def test_peaks_survive_so_a_transient_is_not_lost(jackery):
    store, rec = jackery
    for step in range(20):            # one minute
        observe(rec, step*3, op=100)
    observe(rec, 30, op=1500)         # a spike inside that minute
    store.downsample(rec.start_utc + 5*DAY_NS, bucket_seconds=60, peak_keys=PEAK_KEYS)
    kept = dict(store.conn.execute(
        "SELECT f.t, m.value FROM frames f JOIN measurements m ON m.frame_id=f.id "
        "WHERE m.key='pow_out_sum_w' ORDER BY f.t"))
    assert 1500.0 in kept.values(), "the peak draw was thinned away"


def test_thinning_never_orphans_a_measurement(jackery):
    store, rec = jackery
    for step in range(60):
        observe(rec, step*3)
    store.downsample(rec.start_utc + 5*DAY_NS, bucket_seconds=60, peak_keys=PEAK_KEYS)
    orphans = store.conn.execute(
        "SELECT COUNT(*) FROM measurements WHERE frame_id NOT IN (SELECT id FROM frames)").fetchone()[0]
    assert orphans == 0


def test_evidence_still_verifies_after_thinning(jackery, tmp_path):
    store, rec = jackery
    for step in range(60):
        observe(rec, step*3)
    before = verify_recording(tmp_path/"jackery.sqlite")
    assert before["mismatches"] == 0 and before["observations"] == 60
    store.downsample(rec.start_utc + 5*DAY_NS, bucket_seconds=60, peak_keys=PEAK_KEYS)
    after = verify_recording(tmp_path/"jackery.sqlite")
    assert after["mismatches"] == 0, "thinning broke replay --verify"
    assert after["observations"] < before["observations"]


def test_dry_run_reports_without_deleting(jackery):
    store, rec = jackery
    for step in range(60):
        observe(rec, step*3)
    planned = store.downsample(rec.start_utc + 5*DAY_NS, bucket_seconds=60, dry_run=True)
    assert len(frame_times(store)) == 60, "dry run deleted frames"
    applied = store.downsample(rec.start_utc + 5*DAY_NS, bucket_seconds=60)
    assert planned["deleted"] == applied["deleted"]


def test_indefinite_retention_keeps_what_the_seven_day_rule_would_delete(jackery):
    store, rec = jackery
    for step in range(10):
        observe(rec, step*3)
    store.maintain(rec.start_utc + 30*DAY_NS, days=None)
    assert len(frame_times(store)) == 10
    store.maintain(rec.start_utc + 30*DAY_NS, days=7)
    assert frame_times(store) == []


def test_compaction_watermark_survives_restart_and_does_no_repeat_work(tmp_path):
    path = tmp_path / "jackery.sqlite"
    with Store(path, reserve_bytes=0) as store:
        rec = Recorder(store, utc_ns=1_000_000_000_000_000_000, mono_ns=0)
        for t in range(0, 180, 3):
            observe(rec, t)
        now = rec.start_utc + 5*DAY_NS
        summary = store.downsample(now, max_buckets=1)
        assert summary["buckets"] == 1
    with Store(path, reserve_bytes=0) as store:
        summary = store.downsample(now)
        assert summary["buckets"] == 2
        assert store.downsample(now)["scanned"] == 0
        assert frame_times(store) == [0.0, 60.0, 120.0]


def test_grace_boundary_waits_for_whole_bucket_and_preserves_true_peak(jackery):
    store, rec = jackery
    for t in range(0, 60, 3):
        observe(rec, t, op=1500 if t == 57 else 100)
    first = store.downsample(rec.start_utc + int(172_800+30)*10**9, peak_keys=PEAK_KEYS)
    assert first["scanned"] == 0
    final = store.downsample(rec.start_utc + int(172_800+60)*10**9, peak_keys=PEAK_KEYS)
    assert final["deleted"] == 18
    assert frame_times(store) == [0.0, 57.0]


def test_late_observation_reopens_already_compacted_bucket(jackery):
    store, rec = jackery
    for t in (0, 3, 6):
        observe(rec, t)
    now = rec.start_utc + 5*DAY_NS
    store.downsample(now, peak_keys=PEAK_KEYS)
    observe(rec, 9, op=1500)
    observe(rec, 12, op=100)
    assert store.downsample(now, peak_keys=PEAK_KEYS)["scanned"] == 3
    assert frame_times(store) == [0.0, 9.0]


def test_unpinning_reopens_protected_history(jackery):
    store, rec = jackery
    for t in (0, 3, 6):
        observe(rec, t)
    store.incident(rec.sid, 3, "manual", before=3, after=3)
    now = rec.start_utc + 5*DAY_NS
    store.downsample(now)
    assert len(frame_times(store)) == 3
    incident = store.conn.execute("SELECT id FROM incidents").fetchone()[0]
    store.delete_incident(incident)
    store.downsample(now)
    assert frame_times(store) == [0.0]


def test_dry_run_does_not_advance_compaction_watermark(jackery):
    store, rec = jackery
    for t in (0, 3, 6):
        observe(rec, t)
    now = rec.start_utc + 5*DAY_NS
    store.downsample(now, dry_run=True)
    assert store.conn.execute("SELECT COUNT(*) FROM compaction_progress").fetchone()[0] == 0
    assert store.downsample(now)["deleted"] == 2
