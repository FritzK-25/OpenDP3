"""Storage maintenance must cost what it does, not what is already stored.

maintain() runs inside the capture callback. When each pass re-read the whole
frames table to total its bytes, and re-walked every expired frame an incident
kept, its cost grew with retained history until the collector stopped running
for seconds every minute. These tests pin the work of a pass to the frames and
events that arrived, expired or changed since the previous one, and guard the
query plans of everything a captured frame costs.
"""
import re
import shutil
import sqlite3

import pytest

from conftest import add
from openpowerstation import recorder
from openpowerstation.storage import PIN, Store

START = 1_000_000_000_000_000_000
DAY = 86_400
NS = 10**9


def history(store, sid, first, count, step, size=1_000):
    """Insert ``count`` stored frames ``step`` seconds apart, in one transaction."""
    rows = []
    for index in range(first, first + count):
        t = index * step
        rows.append((sid, 0, START + int(t * NS), int(t * NS), t, START + int(t * NS),
                     b"x" * 16, "decoded", size))
    with store.conn:
        store.conn.executemany(
            "INSERT INTO frames(session_id,segment,utc_ns,mono_ns,t,retention_ns,raw,status,size) "
            "VALUES(?,?,?,?,?,?,?,?,?)", rows)


def recorded_events(store, sid, times, *, clock_offset=0):
    """Events at session times ``times``; ``clock_offset`` skews their wall-clock stamp."""
    with store.conn:
        store.conn.executemany(
            "INSERT INTO events(session_id,t,utc_ns,kind,detail) VALUES(?,?,?,'state_change','')",
            [(sid, t, START + int((t + clock_offset) * NS)) for t in times])


def frame_ids(store):
    return [r[0] for r in store.conn.execute("SELECT id FROM frames ORDER BY id")]


def event_ids(store):
    return [r[0] for r in store.conn.execute("SELECT id FROM events ORDER BY id")]


def vm_steps(conn, work):
    """SQLite virtual-machine steps, in hundreds, spent running ``work``.

    Deterministic, unlike wall-clock time, so a cost bound cannot flake.
    """
    steps = [0]

    def tick():
        steps[0] += 1
        return 0

    conn.set_progress_handler(tick, 100)
    try:
        work()
    finally:
        conn.set_progress_handler(None, 0)
    return steps[0]


def steady_pass_cost(path, frames):
    """Cost of one routine pass over ``frames`` of history, most of it pinned.

    Mirrors production: eight days recorded, an incident keeping the expired
    first day and most of the rest, and a minute of new frames per pass.
    """
    span = 8 * DAY
    step = span / frames
    with Store(path, reserve_bytes=0) as store:
        sid = store.session(utc_ns=START, mono_ns=0)
        history(store, sid, 0, frames, step)
        store.incident(sid, 0.45 * span, "manual", before=0.45 * span, after=0.4 * span)
        now = START + span * NS
        store.maintain(now)
        history(store, sid, frames, 60, step)
        return vm_steps(store.conn, lambda: store.maintain(now + 60 * NS))


def test_a_routine_pass_costs_the_same_however_much_history_is_kept(tmp_path):
    small = steady_pass_cost(tmp_path / "small.sqlite", 2_000)
    large = steady_pass_cost(tmp_path / "large.sqlite", 8_000)
    # Four times the history must not make a routine pass measurably dearer.
    assert large <= small * 1.5 + 10, (small, large)


FULL_SCAN = re.compile(r"^(SCAN (TABLE )?(frames|events|measurements)\b|USE TEMP B-TREE)")


def unindexed(conn, statements):
    """Statements whose query plan reads a whole history table or sorts one."""
    found = {}
    for sql in statements:
        if not sql.lstrip().upper().startswith(("SELECT", "INSERT", "UPDATE", "DELETE")):
            continue
        plan = [row[3] for row in conn.execute("EXPLAIN QUERY PLAN " + sql)]
        bad = [step for step in plan if FULL_SCAN.match(step)]
        if bad:
            found[sql[:160]] = bad
    return found


def traced(conn, work):
    """Every statement ``work`` runs, with its parameters expanded."""
    statements = []
    conn.set_trace_callback(statements.append)
    try:
        work()
    finally:
        conn.set_trace_callback(None)
    return statements


def test_maintenance_never_reads_a_whole_history_table(tmp_path):
    with Store(tmp_path / "plans.sqlite", reserve_bytes=0) as store:
        sid = store.session(utc_ns=START, mono_ns=0)
        history(store, sid, 0, 400, 30 * 60)
        recorded_events(store, sid, [h * 3600 for h in range(0, 200, 5)])
        store.incident(sid, 2 * 3600, "manual", before=3600, after=3600)
        now = START + 8 * DAY * NS

        def passes():
            store.maintain(now)
            history(store, sid, 400, 5, 30 * 60)
            store.maintain(now + 60 * NS, ordinary_bytes=1)
            store.maintain(now + 120 * NS, days=None)

        statements = traced(store.conn, passes)
        assert any("FROM frames" in s for s in statements)
        assert any("FROM events" in s for s in statements)
        assert unindexed(store.conn, statements) == {}


def test_capture_path_never_reads_a_whole_history_table(evidence, packet):
    """Guard: every statement a frame, event, pin or maintenance pass costs is index-driven."""
    store, rec = evidence

    def capture():
        for seq in range(1, 6):
            add(rec, packet(seq=seq, bms_max_cell_temp=20 + seq), seq)
        rec.event("disconnected", "test", rec.start_utc + 7 * NS, 7 * NS)
        add(rec, packet(seq=9, bms_max_cell_temp=23, errcode=5), 8)
        # Past the one-minute mark, so the recorder runs a maintenance pass.
        add(rec, packet(seq=10, bms_max_cell_temp=24), 70)

    statements = traced(store.conn, capture)
    assert any(s.startswith("INSERT INTO frames") for s in statements)
    # Only a maintenance pass checkpoints; a routine one has no free pages to return.
    assert any(s.startswith("PRAGMA wal_checkpoint") for s in statements), "no maintenance pass ran"
    assert unindexed(store.conn, statements) == {}


def test_expired_pinned_history_is_examined_once(tmp_path):
    with Store(tmp_path / "once.sqlite", reserve_bytes=0) as store:
        sid = store.session(utc_ns=START, mono_ns=0)
        history(store, sid, 0, 1_000, 60)
        store.incident(sid, 0, "manual", before=0, after=500 * 60)
        now = START + (7 * DAY + 1_000 * 60) * NS
        first = store.maintain(now)
        assert first["examined"] == 1_000 and first["deleted"] == 499
        # A minute later exactly one more frame has expired; that is all the
        # pass may look at, not the 501 pinned frames it already walked past.
        history(store, sid, 1_000, 1, 60)
        second = store.maintain(now + 60 * NS)
        assert second["examined"] == 1 and second["deleted"] == 1
        assert store.maintain(now + 60 * NS)["examined"] == 0


def build(path):
    """A store with expired ordinary, expired pinned and live history, and events."""
    with Store(path, reserve_bytes=0) as store:
        sid = store.session(utc_ns=START, mono_ns=0)
        history(store, sid, 0, 3_000, 300, size=2_000)
        recorded_events(store, sid, range(0, 3_000 * 300, 600))
        store.incident(sid, 100 * 300, "manual", before=0, after=400 * 300)
        store.incident(sid, 2_500 * 300, "manual", before=100 * 300, after=100 * 300)
    return START + (7 * DAY + 3_000 * 300 // 2) * NS


def test_budgeted_passes_reach_exactly_the_unbounded_result(tmp_path):
    whole, budgeted = tmp_path / "whole.sqlite", tmp_path / "budgeted.sqlite"
    now = build(whole)
    shutil.copy(whole, budgeted)
    cap = 2_000 * 700
    with Store(whole, reserve_bytes=0) as store:
        assert store.maintain(now, ordinary_bytes=cap)["complete"]
        expected = frame_ids(store), event_ids(store)
    with Store(budgeted, reserve_bytes=0) as store:
        passes = 0
        while not store.maintain(now, ordinary_bytes=cap, time_budget=0)["complete"]:
            passes += 1
            assert passes < 200
        assert passes > 3, "a zero budget should have split the backlog across passes"
        assert (frame_ids(store), event_ids(store)) == expected
    assert expected[0] and expected[1]


def tracked(store):
    return store.conn.execute("SELECT ordinary_bytes FROM maintenance_progress").fetchone()[0]


def recount(store):
    """The whole-table total the tracked one replaces, over the frames counted so far."""
    counted = store.conn.execute("SELECT counted_id FROM maintenance_progress").fetchone()[0]
    return store.conn.execute(
        f"SELECT COALESCE(SUM(size),0) FROM frames WHERE id<=? AND NOT {PIN}", (counted,)).fetchone()[0]


def test_ordinary_byte_total_stays_exact_through_every_write_path(evidence, packet):
    store, rec = evidence
    for seq in range(1, 31):
        add(rec, packet(seq=seq, bms_max_cell_temp=23), seq * 20)
    store.maintain(rec.start_utc + 700 * NS, days=None)
    assert tracked(store) == recount(store) > 0
    checks = [
        lambda: store.incident(rec.sid, 100, "manual", before=30, after=30),
        lambda: store.incident(rec.sid, 150, "manual", before=30, after=30),   # merges
        lambda: store.incident(rec.sid, 500, "manual", before=1000, after=10),  # swallows
        lambda: store.conn.execute("DELETE FROM frames WHERE t=580"),
        lambda: add(rec, packet(seq=40, bms_max_cell_temp=24), 610),
        lambda: store.maintain(rec.start_utc + 700 * NS, days=None),
    ]
    for check in checks:
        with store.conn:
            check()
        assert tracked(store) == recount(store)
    newest = store.conn.execute("SELECT MAX(id) FROM frames").fetchone()[0]
    with store.conn:
        store.conn.execute("DELETE FROM frames WHERE id=?", (newest,))
    # SQLite hands the deleted id straight back to the next frame.
    add(rec, packet(seq=41, bms_max_cell_temp=25), 620)
    assert store.conn.execute("SELECT MAX(id) FROM frames").fetchone()[0] == newest
    assert tracked(store) == recount(store)
    incident = store.conn.execute("SELECT id FROM incidents").fetchone()[0]
    store.delete_incident(incident)
    store.maintain(rec.start_utc + 700 * NS, days=None)
    assert tracked(store) == recount(store)
    assert store.conn.execute("SELECT counted_id FROM maintenance_progress").fetchone()[0] == \
        store.conn.execute("SELECT MAX(id) FROM frames").fetchone()[0]


def test_size_cap_spares_pinned_frames_and_waits_for_a_complete_count(tmp_path):
    with Store(tmp_path / "cap.sqlite", reserve_bytes=0) as store:
        sid = store.session(utc_ns=START, mono_ns=0)
        history(store, sid, 0, 2_000, 1, size=100)
        store.incident(sid, 0, "manual", before=0, after=999)
        now = START + 2_000 * NS
        partial = store.maintain(now, ordinary_bytes=100 * 500, time_budget=0)
        assert not partial["complete"] and partial["deleted"] == 0
        while not store.maintain(now, ordinary_bytes=100 * 500, time_budget=0)["complete"]:
            pass
        times = [r[0] for r in store.conn.execute("SELECT t FROM frames ORDER BY t")]
        assert times == list(range(0, 1_000)) + list(range(1_500, 2_000))
        assert tracked(store) == recount(store) == 100 * 500


@pytest.mark.parametrize("release", ["delete_incident", "direct"])
def test_releasing_protection_reopens_expired_history(tmp_path, release):
    with Store(tmp_path / "release.sqlite", reserve_bytes=0) as store:
        sid = store.session(utc_ns=START, mono_ns=0)
        history(store, sid, 0, 600, 60)
        recorded_events(store, sid, range(0, 600 * 60, 600))
        store.incident(sid, 0, "manual", before=0, after=600 * 60)
        now = START + (8 * DAY) * NS
        store.maintain(now)
        assert len(frame_ids(store)) == 600 and len(event_ids(store)) == 60
        incident = store.conn.execute("SELECT id FROM incidents").fetchone()[0]
        if release == "delete_incident":
            store.delete_incident(incident)
        else:
            # A writer that knows nothing of maintenance, such as an older release.
            with store.conn:
                store.conn.execute("DELETE FROM incidents WHERE id=?", (incident,))
        store.maintain(now)
        assert frame_ids(store) == [] and event_ids(store) == []


def test_frame_recorded_behind_the_cursor_is_still_pruned(tmp_path):
    with Store(tmp_path / "late.sqlite", reserve_bytes=0) as store:
        sid = store.session(utc_ns=START, mono_ns=0)
        history(store, sid, 0, 100, 60)
        now = START + 8 * DAY * NS
        store.maintain(now)
        assert frame_ids(store) == []
        # An imported or replayed observation arriving long after its time.
        store.add_raw(sid, 0, START, 0, 5.0, b"late", START + 5 * NS)
        store.maintain(now + 60 * NS)
        assert frame_ids(store) == []


def test_expired_events_go_unless_an_incident_keeps_them(tmp_path):
    with Store(tmp_path / "events.sqlite", reserve_bytes=0) as store:
        sid = store.session(utc_ns=START, mono_ns=0)
        recorded_events(store, sid, [0, 60, 120, 7 * DAY + 60])
        store.incident(sid, 60, "manual", before=1, after=1)
        store.maintain(START + (7 * DAY + 100) * NS)
        assert [r[0] for r in store.conn.execute("SELECT t FROM events ORDER BY t")] == \
            [60, 120, 7 * DAY + 60]


def test_events_age_by_their_own_stamp_whatever_order_they_were_written(tmp_path):
    """Events age by wall-clock receipt time, which is not in insertion order.

    A clock that ran a day ahead writes stamps from the future; an import writes
    old stamps late. Neither may hold back, or escape, the seven-day limit.
    """
    with Store(tmp_path / "skew.sqlite", reserve_bytes=0) as store:
        sid = store.session(utc_ns=START, mono_ns=0)
        recorded_events(store, sid, [0], clock_offset=DAY)
        recorded_events(store, sid, [60, 120])
        now = START + (7 * DAY + 200) * NS
        store.maintain(now)
        assert [r[0] for r in store.conn.execute("SELECT t FROM events")] == [0]
        recorded_events(store, sid, [30])
        store.maintain(now + 60 * NS)
        assert [r[0] for r in store.conn.execute("SELECT t FROM events")] == [0]


def test_database_from_an_earlier_release_is_counted_without_a_new_schema_version(tmp_path):
    """Additive, like compaction_progress, so an older OpenPowerstation can still open it."""
    path = tmp_path / "old.sqlite"
    with Store(path, reserve_bytes=0) as store:
        sid = store.session(utc_ns=START, mono_ns=0)
        history(store, sid, 0, 50, 60)
    conn = sqlite3.connect(path)
    try:
        with conn:
            for (name,) in conn.execute("SELECT name FROM sqlite_master WHERE type='trigger'").fetchall():
                conn.execute(f'DROP TRIGGER "{name}"')
            conn.execute("DROP TABLE maintenance_progress")
            conn.execute("DROP INDEX events_retention")
    finally:
        conn.close()
    with Store(path, reserve_bytes=0) as store:
        store.maintain(START + 60 * 60 * NS, days=None)
        assert tracked(store) == recount(store) == 50 * 1_000
        assert store.conn.execute("PRAGMA user_version").fetchone()[0] == 1


def test_recorder_bounds_every_maintenance_pass(evidence, packet, monkeypatch, capsys):
    store, rec = evidence
    calls = []
    real = store.maintain

    def spy(*args, **kwargs):
        calls.append(kwargs)
        return real(*args, **kwargs)

    monkeypatch.setattr(store, "maintain", spy)
    add(rec, packet(seq=1, bms_max_cell_temp=23), 0)
    add(rec, packet(seq=2, bms_max_cell_temp=23), 61)
    raw = b'{"device":{"serial":"1"}}'
    rec.ingest_observation(raw, {"properties": {}}, {"cms_batt_soc": 50.0},
                           utc_ns=rec.start_utc + 125 * NS, mono_ns=rec.start_mono + 125 * NS)
    assert len(calls) == 2
    assert all(call.get("time_budget") is not None for call in calls), calls
    assert all(call["time_budget"] == recorder.MAINTENANCE_BUDGET for call in calls), calls
    assert 0 < recorder.MAINTENANCE_BUDGET <= 0.1
    assert rec.last_maintenance_seconds is not None
    # Routine passes are quiet in the app log.
    assert "[maintenance]" not in capsys.readouterr().out


def test_a_slow_maintenance_pass_is_named_in_the_log(evidence, packet, monkeypatch, capsys):
    store, rec = evidence
    monkeypatch.setattr(store, "maintain", lambda *_, **__: {
        "seconds": 2.5, "deleted": 7, "events_deleted": 1, "complete": False})
    add(rec, packet(seq=1, bms_max_cell_temp=23), 61)
    out = capsys.readouterr().out
    assert "[maintenance] recording.sqlite" in out and "2.5s" in out, out
    assert rec.last_maintenance_seconds == 2.5
