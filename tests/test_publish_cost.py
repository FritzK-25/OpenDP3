"""A bridge publish must cost the same however long its session has run.

Both bridges read the newest session every few seconds. Finding its newest
frame sorted every frame of the session, because the only index on a session's
frames orders them by elapsed time, not receipt, and counting its frames read
every one of them. So each publish cost the whole session: 1.6 s on a desktop
for a session of 1.5 million frames, and a session ends only when its collector
restarts. These tests pin every statement a publish reads to an index seek, its
cost to what it reads rather than what is stored, and the per-session tally
that makes that possible to an exact recount, whoever writes or removes frames.
"""
import json
import sqlite3
import time

from conftest import add
from test_jackery_bridge import SERIAL, FakeClient, published_state, record_ble_observation
from test_maintenance import DAY, NS, START, history, unindexed, vm_steps
from openpowerstation import jackery_bridge, storage
from openpowerstation.config import Config
from openpowerstation.jackery_bridge import JackeryBridge
from openpowerstation.queries import latest, snapshot
from openpowerstation.storage import Store
from openpowerstation.vendor.packet import Packet

AUTHENTICATION = Packet(0x35, 0x21, 0x35, 0x86, b"\0").to_bytes()


def reads(monkeypatch, work):
    """What the read-only connections ``work`` opens run: statements, and VM steps in hundreds.

    The bridges open their own connections, so both are gathered where every
    reader is opened. Steps are deterministic, unlike wall-clock time.
    """
    statements, steps = [], [0]
    connect = storage.connect_reader

    def tick():
        steps[0] += 1
        return 0

    def watched(path):
        conn = connect(path)
        conn.set_trace_callback(statements.append)
        conn.set_progress_handler(tick, 100)
        return conn

    with monkeypatch.context() as patch:
        patch.setattr(storage, "connect_reader", watched)
        work()
    return statements, steps[0]


def history_reads(path, statements):
    """The statements that read frames, and any of them that sort or scan history.

    Choosing the newest session sorts the session rows, one per collector
    start; that is not history, and a publish reads no more than one.
    """
    reading = [s for s in statements if "FROM sessions" not in s]
    conn = sqlite3.connect(path)
    try:
        return [s for s in reading if "FROM frames" in s], unindexed(conn, reading)
    finally:
        conn.close()


def dp3_session(path, frames):
    """A session ``frames`` long, each frame carrying one observation."""
    with Store(path, reserve_bytes=0) as store:
        sid = store.session(utc_ns=START, mono_ns=0)
        history(store, sid, 0, frames, 1)
        with store.conn:
            store.conn.execute("INSERT INTO measurements SELECT id,'bms_batt_soc',50,'measured' "
                               "FROM frames WHERE session_id=?", (sid,))
    return sid


def test_every_read_of_a_dp3_publish_is_a_seek(tmp_path, monkeypatch):
    path = tmp_path / "plans.sqlite"
    dp3_session(path, 500)
    statements, _ = reads(monkeypatch, lambda: latest(path))
    frames, bad = history_reads(path, statements)
    assert frames, "latest() read no frames"
    assert bad == {}


def test_a_dp3_publish_costs_the_same_however_long_the_session(tmp_path, monkeypatch):
    def cost(name, frames):
        path = tmp_path / name
        dp3_session(path, frames)
        reading = {}
        _, steps = reads(monkeypatch, lambda: reading.update(latest(path)))
        assert (reading["count"], reading["last_t"]) == (frames, frames - 1)
        assert reading["values"]["bms_batt_soc"]["t"] == frames - 1
        return steps

    small, large = cost("small.sqlite", 200), cost("large.sqlite", 4_000)
    # Twenty times the session must not make a publish measurably dearer.
    assert large <= small * 1.5 + 10, (small, large)


def grow(store, sid, count):
    """Repeat the session's first observation ``count`` more times, a second apart."""
    first = store.conn.execute("SELECT * FROM frames WHERE session_id=?", (sid,)).fetchone()
    with store.conn:
        for index in range(1, count + 1):
            frame = store.conn.execute(
                "INSERT INTO frames(session_id,segment,utc_ns,mono_ns,t,retention_ns,raw,status,"
                "decoded,size) VALUES(?,?,?,?,?,?,?,?,?,?)",
                (sid, first["segment"], first["utc_ns"] + index * NS, first["mono_ns"] + index * NS,
                 first["t"] + index, first["retention_ns"] + index * NS, first["raw"],
                 first["status"], first["decoded"], first["size"])).lastrowid
            store.conn.execute("INSERT INTO measurements SELECT ?,key,value,quality FROM measurements "
                               "WHERE frame_id=?", (frame, first["id"]))


def jackery_publish(path, frames, monkeypatch):
    """Statements and cost of a new bridge's first publish of a session ``frames`` long.

    A new bridge looks for its session first, so that read is covered too.
    """
    with record_ble_observation(path) as store:
        sid = store.conn.execute("SELECT id FROM sessions").fetchone()[0]
        grow(store, sid, frames - 1)
    client = FakeClient()
    bridge = JackeryBridge(Config(address="AA:BB:CC:DD:EE:FF", serial=SERIAL, user_id="0",
                                  mqtt_host="broker.invalid"), path, serial=SERIAL, client=client)
    statements, steps = reads(monkeypatch, bridge.publish_once)
    assert bridge._session_id == sid
    state = next(payload for topic, payload, _ in client.published if topic.endswith("/state"))
    assert json.loads(state)["frame_count"] == frames
    return statements, steps


def test_every_read_of_a_jackery_publish_is_a_seek(tmp_path, monkeypatch):
    path = tmp_path / "jackery.sqlite"
    statements, _ = jackery_publish(path, 300, monkeypatch)
    frames, bad = history_reads(path, statements)
    assert any("f.decoded" in s for s in frames), "the backfill read no frame"
    assert bad == {}


def test_a_jackery_publish_costs_the_same_however_long_the_session(tmp_path, monkeypatch):
    _, small = jackery_publish(tmp_path / "small.sqlite", 100, monkeypatch)
    _, large = jackery_publish(tmp_path / "large.sqlite", 2_000, monkeypatch)
    assert large <= small * 1.5 + 10, (small, large)


def test_the_jackery_backfill_reads_the_frame_latest_read(tmp_path, monkeypatch):
    """A reply recorded between the bridge's two reads must not mix into its reading."""
    path = tmp_path / "jackery.sqlite"
    with record_ble_observation(path) as store:
        first = store.conn.execute("SELECT * FROM frames").fetchone()
    newer = json.loads(first["decoded"])
    newer["properties"]["rb"] = 55
    read = jackery_bridge.latest

    def then_recorded(*args, **kwargs):
        reading = read(*args, **kwargs)
        conn = sqlite3.connect(path)
        try:
            with conn:
                now = time.time_ns()
                conn.execute("INSERT INTO frames(session_id,segment,utc_ns,mono_ns,t,retention_ns,raw,"
                             "status,decoded,size) VALUES(?,?,?,?,?,?,x'00','decoded',?,257)",
                             (first["session_id"], first["segment"], now, first["mono_ns"] + NS,
                              first["t"] + 1, first["retention_ns"] + NS, json.dumps(newer)))
        finally:
            conn.close()
        return reading

    monkeypatch.setattr(jackery_bridge, "latest", then_recorded)
    payload, _ = published_state(path)
    # The reading latest() took has the recorded 100 %; the later reply's 55 is the next publish's.
    assert payload["bms_batt_soc"] == 100.0


def replayed(path):
    """A session whose receipt order is not its elapsed order, as a replay or import writes."""
    with Store(path, reserve_bytes=0) as store:
        sid = store.session(utc_ns=START, mono_ns=0)
        for t in [10.0, 30.0, 20.0, 5.0, 40.0, 15.0]:
            store.add_raw(sid, 0, START + int(t * NS), int(t * NS), t, b"frame", START + int(t * NS))
        return sid, store.conn.execute("SELECT id,t FROM frames ORDER BY id").fetchall()


def test_snapshot_shows_the_newest_frame_received_within_its_view(tmp_path, monkeypatch):
    path = tmp_path / "view.sqlite"
    sid, frames = replayed(path)
    for end_t in [None, 50.0, 40.0, 35.0, 25.0, 12.0, 5.0, 4.0]:
        right = 40.0 if end_t is None else min(end_t, 40.0)
        within = [f["id"] for f in frames if f["t"] <= right]
        views = []
        statements, _ = reads(monkeypatch, lambda: views.append(snapshot(path, sid, end_t=end_t)))
        view = views[0]
        assert (view["raw"]["id"] if view["raw"] else None) == (max(within) if within else None), end_t
        assert (view["count"], view["earliest"], view["latest"]) == (len(frames), 5.0, 40.0)
        raw = [s for s in statements if "decoded FROM frames" in s]
        assert raw, "snapshot() read no raw frame"
        assert history_reads(path, raw)[1] == {}, end_t


def test_latest_reads_the_newest_receipt_not_the_furthest_time(tmp_path):
    path = tmp_path / "replay.sqlite"
    sid, frames = replayed(path)
    reading = latest(path, sid)
    assert reading["count"] == len(frames)
    assert reading["last_id"] == frames[-1]["id"]
    assert (reading["last_t"], reading["last_utc_ns"]) == (15.0, START + 15 * NS)


# The tally a publish reads in place of counting and sorting the session.

def tally(store):
    """Each session's frames and newest frame id, as kept and as a recount finds them."""
    sessions = [row[0] for row in store.conn.execute("SELECT id FROM sessions")]
    kept = {row[0]: (row[1], row[2]) for row in store.conn.execute("SELECT * FROM session_frames")}
    counted = {row[0]: (row[1], row[2]) for row in store.conn.execute(
        "SELECT session_id,COUNT(*),MAX(id) FROM frames GROUP BY session_id")}
    return ({sid: kept.get(sid, (0, None)) for sid in sessions},
            {sid: counted.get(sid, (0, None)) for sid in sessions})


def test_the_tally_stays_exact_through_every_write_path(evidence, packet):
    store, rec = evidence
    for seq in range(1, 31):
        add(rec, packet(seq=seq, bms_max_cell_temp=23), seq * 20)
    kept, counted = tally(store)
    assert kept == counted and kept[rec.sid][0] == 30
    other = store.session(utc_ns=rec.start_utc, mono_ns=0)

    def newest():
        return store.conn.execute("SELECT MAX(id) FROM frames").fetchone()[0]

    def raw(t):
        # Another writer, such as an older release or an import.
        store.conn.execute("INSERT INTO frames(session_id,segment,utc_ns,mono_ns,t,retention_ns,raw,size) "
                           "VALUES(?,0,?,0,?,?,x'00',257)", (other, rec.start_utc, t, rec.start_utc))

    checks = [
        # The recorder drops an authentication packet it has already stored.
        lambda: add(rec, AUTHENTICATION, 610),
        lambda: add(rec, packet(seq=40, bms_max_cell_temp=24), 620),
        lambda: raw(1.0),
        lambda: store.conn.execute("DELETE FROM frames WHERE id=?", (newest(),)),
        # SQLite hands the deleted id straight back to the next frame.
        lambda: raw(2.0),
        lambda: raw(3.0),
        lambda: store.conn.execute("UPDATE frames SET id=? WHERE t=2.0", (newest() + 10,)),
        lambda: store.conn.execute("UPDATE frames SET session_id=? WHERE t=600", (other,)),
        lambda: store.conn.execute("UPDATE frames SET session_id=? WHERE t=3.0", (rec.sid,)),
        lambda: store.downsample(rec.start_utc + 3 * DAY * NS, bucket_seconds=120),
        lambda: store.maintain(rec.start_utc + (7 * DAY + 300) * NS),
        lambda: store.conn.execute("DELETE FROM frames WHERE session_id=?", (other,)),
        lambda: store.maintain(rec.start_utc + 30 * DAY * NS),
    ]
    for index, check in enumerate(checks):
        with store.conn:
            check()
        kept, counted = tally(store)
        assert kept == counted, index
    assert kept[rec.sid] == kept[other] == (0, None)


def test_a_database_from_an_earlier_release_is_tallied_once_and_read_meanwhile(tmp_path, monkeypatch):
    """Additive, like maintenance_progress: no new schema version, counted on first open."""
    path = tmp_path / "old.sqlite"
    with Store(path, reserve_bytes=0) as store:
        old = store.session(utc_ns=START, mono_ns=0)
        history(store, old, 0, 40, 60)
    conn = sqlite3.connect(path)
    try:
        with conn:
            for (name,) in conn.execute("SELECT name FROM sqlite_master WHERE type='trigger' "
                                        "AND name LIKE 'session_frames%'").fetchall():
                conn.execute(f'DROP TRIGGER "{name}"')
            conn.execute("DROP TABLE session_frames")
            # What the earlier release goes on recording, uncounted.
            conn.execute("INSERT INTO sessions VALUES('new',?,0,24.0,'stopped',0,'','','')",
                         (START + DAY * NS,))
            conn.executemany("INSERT INTO frames(session_id,segment,utc_ns,mono_ns,t,retention_ns,raw,size) "
                             "VALUES('new',0,?,0,?,?,x'00',257)",
                             [(START + (DAY + t) * NS, float(t), START + (DAY + t) * NS) for t in range(25)])
    finally:
        conn.close()
    # Until a collector opens it, a reader counts the session, still without sorting it.
    before = {}
    statements, _ = reads(monkeypatch, lambda: before.update(latest(path)))
    assert (before["count"], before["last_t"]) == (25, 24.0)
    assert history_reads(path, statements)[1] == {}
    with Store(path, reserve_bytes=0) as store:
        kept, counted = tally(store)
        assert kept == counted and kept["new"][0] == 25 and kept[old][0] == 40
        assert store.conn.execute("PRAGMA user_version").fetchone()[0] == 1
    assert latest(path) == before


def test_the_tally_costs_a_seek_per_frame_written_or_removed(tmp_path):
    """Writing a frame, or removing one that is not its session's newest, never recounts."""
    def cost(name, frames):
        with Store(tmp_path / name, reserve_bytes=0) as store:
            sid = store.session(utc_ns=START, mono_ns=0)
            history(store, sid, 0, frames, 1)

            def work():
                store.add_raw(sid, 0, START + frames * NS, frames * NS, float(frames), b"x",
                              START + frames * NS)
                with store.conn:
                    store.conn.execute("DELETE FROM frames WHERE id=(SELECT MIN(id) FROM frames)")
            return vm_steps(store.conn, work)

    small, large = cost("small.sqlite", 200), cost("large.sqlite", 4_000)
    assert large <= small * 1.5 + 10, (small, large)
