"""History queries must remain presence-aware when using the fast coverage path."""
import math

import pytest

from openpowerstation.fields import CHART_GROUPS, DP3_FIELDS, JACKERY_OBSERVATIONS
from openpowerstation.queries import (GAP_SECONDS, WRITER_QUIET_LIMIT, elapsed_now, gap, plot_arrays,
                             session_revision, snapshot, writer_live)
from openpowerstation.recorder import Recorder
from conftest import add, compacted_jackery

NS = 10**9


def drawable(y):
    """Line segments a chart can draw: neighbouring finite values."""
    return sum(1 for a, b in zip(y, y[1:]) if math.isfinite(a) and math.isfinite(b))


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


# --- whether a session is still being written -------------------------------

def test_a_recording_session_is_live_only_while_its_writer_keeps_writing(evidence, packet):
    """A crash leaves 'recording' behind; only the writer's own writes say it is alive."""
    store, recorder = evidence
    add(recorder, packet(seq=1, bms_max_cell_temp=24), 99)
    add(recorder, packet(seq=2, bms_max_cell_temp=25), 100)
    snap = snapshot(store.path, recorder.sid)
    newest = recorder.start_utc + 100 * NS
    assert snap["session"]["status"] == "recording"
    assert snap["activity_utc_ns"] == newest
    # Its newest two frames, a second apart: the limit and one second more.
    assert snap["cadence"] == 1
    assert writer_live(snap, now_ns=newest + 60 * NS)
    assert writer_live(snap, now_ns=newest + int(WRITER_QUIET_LIMIT + 1) * NS)
    assert not writer_live(snap, now_ns=newest + int(WRITER_QUIET_LIMIT + 2) * NS)
    # An event is a write too: a collector searching for its station writes nothing else.
    recorder.event("connection_failed", "not advertising", newest + 700 * NS, recorder.start_mono + 800 * NS)
    snap = snapshot(store.path, recorder.sid)
    assert snap["activity_utc_ns"] == newest + 700 * NS
    assert writer_live(snap, now_ns=newest + int(WRITER_QUIET_LIMIT + 2) * NS)
    recorder.finish()
    assert not writer_live(snapshot(store.path, recorder.sid), now_ns=newest)


def test_the_quiet_limit_outlasts_every_silence_of_a_running_collector():
    """The limit is only right while no live collector stays quieter than it."""
    from openpowerstation.cli import ATTACH_REPORT_SECONDS
    from openpowerstation.radio import RADIO_HOLD_LIMIT, RADIO_LOCK_TIMEOUT
    from openpowerstation.runtime import RECONNECT_DELAY_MAX, SESSION_FRAME_LEASE
    # The Explorer's search: one report per interval, then an attempt -- a
    # minute's search, the radio lease's queue and its hold. Its poll
    # interval comes on top, from its own cadence (see the next test).
    assert WRITER_QUIET_LIMIT >= ATTACH_REPORT_SECONDS + 60 + RADIO_LOCK_TIMEOUT + RADIO_HOLD_LIMIT
    # The DP3: the longest backoff, an attempt under the lease, then the frame lease.
    assert WRITER_QUIET_LIMIT >= (RECONNECT_DELAY_MAX + RADIO_LOCK_TIMEOUT + RADIO_HOLD_LIMIT
                                  + SESSION_FRAME_LEASE)


def test_a_collector_polling_slowly_is_allowed_its_own_cadence(tmp_path):
    """jackery-record --interval runs up to an hour between polls, and searches as slowly."""
    from openpowerstation.storage import Store
    from conftest import jackery_session
    with Store(tmp_path / "slow.sqlite", reserve_bytes=0) as store:
        recorder = jackery_session(store, seconds=3 * 3600, step=3600, finished=False)
    snap = snapshot(tmp_path / "slow.sqlite")
    newest = recorder.start_utc + 2 * 3600 * NS
    assert snap["cadence"] == 3600
    assert snap["activity_utc_ns"] == newest
    assert writer_live(snap, now_ns=newest + int(WRITER_QUIET_LIMIT + 3600) * NS)
    assert not writer_live(snap, now_ns=newest + int(WRITER_QUIET_LIMIT + 3601) * NS)


def test_the_revision_moves_with_every_change_a_writer_makes_and_only_then(evidence, packet):
    """What the desktop probes before querying and drawing a saved session again."""
    store, recorder = evidence
    add(recorder, packet(seq=1, bms_max_cell_temp=24), 0)
    snap = snapshot(store.path, recorder.sid)
    assert session_revision(store.path, recorder.sid) == snap["revision"]
    other = Recorder(store, utc_ns=recorder.start_utc + 10**12, mono_ns=0)
    add(other, packet(seq=1, bms_max_cell_temp=30), 0)
    other.event("manual", "another session's incident")
    assert session_revision(store.path, recorder.sid) == snap["revision"]
    incident = {}

    def mark():
        recorder.event("manual", "private note", recorder.start_utc + 5 * NS, recorder.start_mono + 5 * NS)
        incident["id"] = snapshot(store.path, recorder.sid)["incidents"][0]["id"]

    seen = [snap["revision"]]
    for change in (lambda: add(recorder, packet(seq=2, bms_max_cell_temp=25), 10),
                   lambda: recorder.event("connection_failed", "not advertising"),
                   mark,
                   lambda: store.incident(recorder.sid, 8, "manual"),
                   lambda: store.delete_incident(incident["id"]),
                   # A collector thinning it, once nothing pins it any more.
                   lambda: store.downsample(recorder.start_utc + 10 * 86_400 * NS, bucket_seconds=60),
                   lambda: recorder.finish()):
        change()
        current = session_revision(store.path, recorder.sid)
        assert current not in seen
        seen.append(current)


def test_elapsed_now_is_timed_by_the_wall_clock_across_boots(evidence, packet):
    """Another process's monotonic clock, from an earlier boot, means nothing here."""
    store, recorder = evidence
    add(recorder, packet(seq=1, bms_max_cell_temp=24), 0)
    add(recorder, packet(seq=2, bms_max_cell_temp=25), 50)
    snap = snapshot(store.path, recorder.sid)
    assert snap["newest"] == {"t": 50.0, "utc_ns": recorder.start_utc + 50 * NS}
    assert elapsed_now(snap, now_ns=recorder.start_utc + 250 * NS) == pytest.approx(250)
    empty = snapshot(store.path, Recorder(store, utc_ns=recorder.start_utc, mono_ns=10**15).sid)
    assert empty["newest"] is None
    assert elapsed_now(empty, now_ns=recorder.start_utc + 30 * NS) == pytest.approx(30)


# --- thinned history ---------------------------------------------------------

def test_every_registered_field_has_a_chart_group():
    """A field whose group no chart draws is recorded and never shown."""
    for field in (*DP3_FIELDS, *JACKERY_OBSERVATIONS):
        assert field.group in CHART_GROUPS, field


def test_thinned_history_still_draws_lines(tmp_path):
    """One kept frame a minute is not a gap: every point used to sit between two breaks."""
    path = compacted_jackery(tmp_path / "jackery.sqlite")
    snap = snapshot(path)
    thinning = snap["thinning"]
    assert thinning["bucket_seconds"] == 60
    assert thinning["before_t"] >= snap["latest"]
    for key in ("pow_out_sum_w", "bms_batt_soc", "jackery_ac_voltage_v"):
        points = snap["series"][key]
        assert len(points) >= 170
        x, y = plot_arrays(points, thinning)
        assert drawable(y) == len(points) - 1, key
        # The native rule alone breaks between every pair of them.
        assert drawable(plot_arrays(points)[1]) == 0


def test_the_native_gap_rule_still_holds_inside_and_after_thinned_history():
    thinning = {"before_t": 1000.0, "bucket_seconds": 60.0, "peak_keys": [],
                "pinned": [(100.0, 200.0)]}

    def point(t, segment=0):
        return {"t": t, "segment": segment, "value": 1.0, "quality": "ble_observed"}

    # Thinned history outside any incident window: a bucket apart is no gap.
    assert not gap(point(300), point(370), thinning)
    assert gap(point(300), point(300 + GAP_SECONDS + 61), thinning)
    # Inside an incident window nothing was thinned, so the native rule holds.
    assert gap(point(120), point(160), thinning)
    # Leaving a window crosses thinned history.
    assert not gap(point(190), point(250), thinning)
    # After the watermark, native cadence again; a segment always breaks.
    assert gap(point(1000), point(1040), thinning)
    assert gap(point(300), point(301, segment=1), thinning)
