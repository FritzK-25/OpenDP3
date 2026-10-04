"""Collector pins lapse; evidence pins are kept.

Every disconnect, corrupt transport run and host suspend pinned ten minutes of
full-rate history outside both retention limits, and nothing ever released it,
so an always-on collector's database grew for as long as its link misbehaved.
Those pins describe the collector's own link or host, not the battery, so their
protection now lapses: thirty days after the window, or sooner, oldest first,
once collector pins hold more than their byte cap. A manual marker, a device
error or suspect telemetry is evidence and stays until a person removes it.
"""
import json
import shutil
import sqlite3

from openpowerstation import events
from openpowerstation.recorder import Recorder
from openpowerstation.storage import Store
from test_maintenance import recount, traced, tracked, unindexed

START = 1_000_000_000_000_000_000
DAY = 86_400
NS = 10**9
# What a pinned window around a trigger holds in these tests: a frame every
# 30 s, 300 s either side, each accounted at 1,000 bytes.
PINNED = 21 * 1_000


def window(store, sid, t, *, step=30, span=600, size=1_000):
    """Frames every ``step`` s, ``span`` s either side of ``t``."""
    rows = [(sid, 0, START + u * NS, u * NS, u, START + u * NS, b"x" * 16, "decoded", size)
            for u in range(t - span, t + span + 1, step)]
    with store.conn:
        store.conn.executemany(
            "INSERT INTO frames(session_id,segment,utc_ns,mono_ns,t,retention_ns,raw,status,size) "
            "VALUES(?,?,?,?,?,?,?,?,?)", rows)


def pin(store, sid, t, kind):
    """What Recorder.event does for a pinning kind: record it, then protect its window."""
    store.event(sid, t, START + t * NS, kind, "")
    store.incident(sid, t, kind)


def frame_times(store):
    return [r[0] for r in store.conn.execute("SELECT t FROM frames ORDER BY t")]


def reason_kinds(store):
    return sorted(sorted({r["kind"] for r in json.loads(row[0])})
                  for row in store.conn.execute("SELECT reasons FROM incidents"))


def test_collector_pins_lapse_after_thirty_days_and_evidence_is_kept(tmp_path):
    kinds = ["disconnected", "manual", "corrupt_transport", "device_error",
             "host_suspend", "suspect_telemetry", "capture_gap"]
    with Store(tmp_path / "lapse.sqlite", reserve_bytes=0) as store:
        sid = store.session(utc_ns=START, mono_ns=0)
        at = {kind: (i + 1) * DAY for i, kind in enumerate(kinds)}
        for kind, t in at.items():
            window(store, sid, t)
            pin(store, sid, t, kind)
        # A collector window someone then marked is evidence as a whole.
        marked = 9 * DAY
        window(store, sid, marked)
        pin(store, sid, marked, "disconnected")
        pin(store, sid, marked + 60, "manual")
        # So is one an older release merged a marker into, knowing nothing of lapsing.
        merged = 10 * DAY
        window(store, sid, merged)
        pin(store, sid, merged, "host_suspend")
        row = store.conn.execute("SELECT id,reasons FROM incidents WHERE start_t=?", (merged - 300,)).fetchone()
        with store.conn:
            store.conn.execute("UPDATE incidents SET reasons=? WHERE id=?",
                               (json.dumps(json.loads(row[1]) + [{"t": merged, "kind": "manual"}]), row[0]))

        summary = store.maintain(START + (merged + 31 * DAY) * NS)

        kept = [(at[k] - 300, at[k] + 300) for k in ("manual", "device_error", "suspect_telemetry")]
        kept += [(marked - 300, marked + 360), (merged - 300, merged + 300)]
        assert frame_times(store) == [u for lo, hi in sorted(kept) for u in range(lo, hi + 1, 30)]
        assert reason_kinds(store) == [["device_error"], ["disconnected", "manual"], ["host_suspend", "manual"],
                                       ["manual"], ["suspect_telemetry"]]
        recorded = sorted(r[0] for r in store.conn.execute("SELECT kind FROM events"))
        assert recorded == sorted(["manual", "device_error", "suspect_telemetry",
                                   "disconnected", "manual", "host_suspend"])
        assert summary["pins_lapsed"] == 4 and summary["complete"]
        assert tracked(store) == recount(store)
    # The kinds that lapse are named once, beside the ones that pin at all.
    assert events.COLLECTOR_PIN_KINDS == {"disconnected", "corrupt_transport", "host_suspend", "capture_gap"}


def test_a_collector_pin_protects_its_window_for_thirty_days(tmp_path):
    with Store(tmp_path / "boundary.sqlite", reserve_bytes=0) as store:
        sid = store.session(utc_ns=START, mono_ns=0)
        window(store, sid, DAY)
        pin(store, sid, DAY, "disconnected")
        end = DAY + 300
        early = store.maintain(START + (end + 29 * DAY) * NS)
        assert frame_times(store) == list(range(DAY - 300, end + 1, 30))
        late = store.maintain(START + (end + 31 * DAY) * NS)
        assert frame_times(store) == []
        assert store.conn.execute("SELECT COUNT(*) FROM incidents").fetchone()[0] == 0
        assert (early["pins_lapsed"], late["pins_lapsed"]) == (0, 1)


def test_the_recorder_lapses_collector_pins_and_names_them_in_the_log(tmp_path, packet, capsys):
    path = tmp_path / "recording.sqlite"
    with Store(path, reserve_bytes=0) as store:
        sid = store.session(utc_ns=START, mono_ns=0)
        window(store, sid, DAY)
        pin(store, sid, DAY, "disconnected")
    with Store(path, reserve_bytes=0) as store:
        rec = Recorder(store, utc_ns=START + 40 * DAY * NS, mono_ns=0)
        rec.ingest(packet(seq=1, bms_max_cell_temp=23), rec.start_utc + 61 * NS, 61 * NS)
        assert store.conn.execute("SELECT COUNT(*) FROM incidents").fetchone()[0] == 0
        assert store.conn.execute("SELECT COUNT(*) FROM frames WHERE session_id=?", (sid,)).fetchone()[0] == 0
    out = capsys.readouterr().out
    assert "[maintenance] recording.sqlite: protection lapsed on 1 collector incident window" in out, out


def test_collector_pins_over_their_byte_cap_lapse_oldest_first(tmp_path):
    with Store(tmp_path / "cap.sqlite", reserve_bytes=0) as store:
        sid = store.session(utc_ns=START, mono_ns=0)
        times = [DAY + hour * 3_600 for hour in range(6)]
        for t in times:
            window(store, sid, t)
            pin(store, sid, t, "disconnected")
        # Evidence neither counts toward the cap nor lapses under it.
        evidence = DAY + 10 * 3_600
        window(store, sid, evidence, span=1_800)
        pin(store, sid, evidence, "device_error")
        frames = len(frame_times(store))
        # A day on nothing has aged out; only the cap can release anything.
        now = START + 2 * DAY * NS
        summary = store.maintain(now, pin_bytes=2 * PINNED)
        assert summary["pins_lapsed"] == 4
        assert sorted(json.loads(r[0])[0]["t"] for r in store.conn.execute("SELECT reasons FROM incidents")) == \
            times[-2:] + [evidence]
        # Released, not deleted: those frames are ordinary history now, counted exactly...
        assert len(frame_times(store)) == frames
        assert tracked(store) == recount(store)
        # ...so ordinary retention may take them, and the pinned windows stay.
        store.maintain(now, ordinary_bytes=0, pin_bytes=2 * PINNED)
        assert frame_times(store) == [u for lo, hi in sorted((t - 300, t + 300) for t in times[-2:] + [evidence])
                                      for u in range(lo, hi + 1, 30)]


def test_recurring_disconnects_cannot_bypass_the_pin_byte_cap(tmp_path):
    """A continuously extended collector incident still accrues capped bytes."""
    with Store(tmp_path / "recurring-cap.sqlite", reserve_bytes=0) as store:
        sid = store.session(utc_ns=START, mono_ns=0)
        first = DAY
        last = DAY + 3_600
        rows = [
            (sid, 0, START + t * NS, t * NS, t, START + t * NS,
             b"x" * 16, "decoded", 1_000)
            for t in range(first - 300, last + 301, 60)
        ]
        with store.conn:
            store.conn.executemany(
                "INSERT INTO frames(session_id,segment,utc_ns,mono_ns,t,retention_ns,raw,status,size) "
                "VALUES(?,?,?,?,?,?,?,?,?)", rows)

        lapsed = 0
        for t in range(first, last + 1, 300):
            pin(store, sid, t, "disconnected")
            summary = store.maintain(
                START + t * NS, days=None, pin_days=None, pin_bytes=1_000)
            lapsed += summary["pins_lapsed"]
            measured = store.conn.execute(
                "SELECT COALESCE(SUM(bytes),0) FROM pin_lapse").fetchone()[0]
            assert measured <= 1_000

        assert lapsed > 0
        # Every event is less than ten minutes from the previous one, so the
        # pre-fix implementation kept extending one unsettled incident forever.
        assert store.conn.execute("SELECT COUNT(*) FROM incidents").fetchone()[0] < 13


def test_a_lapsed_window_in_thinned_history_is_thinned_too(tmp_path):
    """The Jackery keeps history forever but thins it; a lapsed window must not stay at full rate."""
    with Store(tmp_path / "thin.sqlite", reserve_bytes=0) as store:
        sid = store.session(utc_ns=START, mono_ns=0)
        t = DAY
        window(store, sid, t, step=3)
        pin(store, sid, t, "capture_gap")
        store.downsample(START + 4 * DAY * NS, bucket_seconds=60)
        inside = [u for u in frame_times(store) if t - 300 <= u <= t + 300]
        assert inside == list(range(t - 300, t + 301, 3))
        now = START + (t + 300 + 31 * DAY) * NS
        store.maintain(now, days=None)
        store.downsample(now, bucket_seconds=60)
        buckets = {}
        for u in frame_times(store):
            buckets[u // 60] = buckets.get(u // 60, 0) + 1
        assert buckets == {b: 1 for b in range((t - 600) // 60, (t + 600) // 60 + 1)}


def test_collector_incidents_an_earlier_release_recorded_lapse_too(tmp_path):
    path = tmp_path / "old.sqlite"
    with Store(path, reserve_bytes=0) as store:
        sid = store.session(utc_ns=START, mono_ns=0)
        for t, kind in ((DAY, "host_suspend"), (2 * DAY, "manual"), (3 * DAY, "disconnected")):
            window(store, sid, t)
            pin(store, sid, t, kind)
    # A database written before collector pins could lapse has none of the
    # bookkeeping; its incidents are judged by their reasons on open.
    conn = sqlite3.connect(path)
    try:
        with conn:
            for (name,) in conn.execute("SELECT name FROM sqlite_master WHERE type='trigger' "
                                        "AND name LIKE 'pin_lapse%'").fetchall():
                conn.execute(f'DROP TRIGGER "{name}"')
            conn.execute("DROP TABLE IF EXISTS pin_lapse")
    finally:
        conn.close()
    with Store(path, reserve_bytes=0) as store:
        store.maintain(START + 40 * DAY * NS)
        assert reason_kinds(store) == [["manual"]]
        assert frame_times(store) == list(range(2 * DAY - 300, 2 * DAY + 301, 30))


def test_collector_incidents_an_older_release_pins_in_between_lapse_too(tmp_path):
    """A rollback runs a release that knows nothing of lapsing against the same file.

    It pins without the bookkeeping, and the triggers it inherits drop the
    bookkeeping of every window it extends. Neither may keep a collector window
    forever once this release runs again; a window it marked stays evidence.
    """
    path = tmp_path / "rollback.sqlite"
    with Store(path, reserve_bytes=0) as store:
        sid = store.session(utc_ns=START, mono_ns=0)
        for t in (DAY, 2 * DAY, 3 * DAY, 4 * DAY):
            window(store, sid, t)
        pin(store, sid, DAY, "disconnected")
        pin(store, sid, 4 * DAY, "corrupt_transport")
    older = sqlite3.connect(path)
    try:
        with older:
            # What the older Store.incident() writes for a new window...
            for t, kind in ((2 * DAY, "host_suspend"), (3 * DAY, "manual")):
                older.execute("INSERT INTO incidents(session_id,start_t,end_t,pre_missing,reasons) "
                              "VALUES(?,?,?,0,?)", (sid, t - 300, t + 300, json.dumps([{"t": t, "kind": kind}])))
            # ...and for one that merges into a window this release pinned.
            for t, kind in ((DAY, "corrupt_transport"), (4 * DAY, "manual")):
                row = older.execute("SELECT id,reasons FROM incidents WHERE start_t=?", (t - 300,)).fetchone()
                older.execute("UPDATE incidents SET end_t=?,reasons=? WHERE id=?",
                              (t + 360, json.dumps(json.loads(row[1]) + [{"t": t + 60, "kind": kind}]), row[0]))
    finally:
        older.close()
    with Store(path, reserve_bytes=0) as store:
        summary = store.maintain(START + 40 * DAY * NS)
        assert summary["pins_lapsed"] == 2
        assert reason_kinds(store) == [["corrupt_transport", "manual"], ["manual"]]
        assert frame_times(store) == list(range(3 * DAY - 300, 3 * DAY + 301, 30)) + \
            list(range(4 * DAY - 300, 4 * DAY + 361, 30))


def build(path):
    """Collector windows old, recent and over the cap, around evidence."""
    with Store(path, reserve_bytes=0) as store:
        sid = store.session(utc_ns=START, mono_ns=0)
        for index in range(24):
            t = DAY + index * 2 * 3_600
            window(store, sid, t, step=3)
            pin(store, sid, t, "manual" if index % 7 == 3 else "disconnected")
    # Windows from the first day are past thirty days; the cap takes more.
    return START + (DAY + 12 * 3_600 + 30 * DAY) * NS


def test_budgeted_passes_lapse_exactly_what_one_unbounded_pass_does(tmp_path):
    whole, budgeted = tmp_path / "whole.sqlite", tmp_path / "budgeted.sqlite"
    now = build(whole)
    shutil.copy(whole, budgeted)
    cap = 4 * 201 * 1_000

    def state(store):
        return (frame_times(store), [r[0] for r in store.conn.execute("SELECT id FROM events ORDER BY id")],
                [r[0] for r in store.conn.execute("SELECT id FROM incidents ORDER BY id")])

    with Store(whole, reserve_bytes=0) as store:
        summary = store.maintain(now, pin_bytes=cap)
        assert summary["complete"] and summary["pins_lapsed"] > 6
        expected = state(store)
    with Store(budgeted, reserve_bytes=0) as store:
        passes = 0
        while not store.maintain(now, pin_bytes=cap, time_budget=0)["complete"]:
            passes += 1
            assert passes < 500
        assert passes > 3, "a zero budget should have split the lapsing across passes"
        assert state(store) == expected
        assert tracked(store) == recount(store)


def test_lapsing_never_reads_a_whole_history_table(tmp_path):
    with Store(tmp_path / "plans.sqlite", reserve_bytes=0) as store:
        sid = store.session(utc_ns=START, mono_ns=0)
        for index, kind in enumerate(["disconnected", "capture_gap", "host_suspend", "manual"]):
            t = DAY + index * 3_600
            window(store, sid, t, step=10)
            pin(store, sid, t, kind)
        store.downsample(START + 4 * DAY * NS, bucket_seconds=60)

        def passes():
            store.maintain(START + 3 * DAY * NS, days=None, pin_bytes=PINNED)
            store.downsample(START + 4 * DAY * NS, bucket_seconds=60)
            store.maintain(START + 40 * DAY * NS)

        statements = traced(store.conn, passes)
        assert any("pin_lapse" in s for s in statements)
        assert any("compaction_redo" in s for s in statements)
        # downsample() orders the handful of session rows; that sort is not history.
        assert unindexed(store.conn, [s for s in statements if "FROM sessions" not in s]) == {}
