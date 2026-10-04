"""Single-writer SQLite evidence store; raw frames commit before interpretation."""
from contextlib import contextmanager
import json
import math
from pathlib import Path
import shutil
import sqlite3
import time
import uuid

import portalocker

from . import DECODER_VERSION
from .events import COLLECTOR_PIN_KINDS

SCHEMA = """
CREATE TABLE IF NOT EXISTS sessions(
 id TEXT PRIMARY KEY, start_utc_ns INTEGER NOT NULL, start_mono_ns INTEGER NOT NULL,
 end_t REAL, status TEXT NOT NULL, synthetic INTEGER NOT NULL, firmware TEXT NOT NULL,
 conditions TEXT NOT NULL, decoder TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS frames(
 id INTEGER PRIMARY KEY, session_id TEXT NOT NULL REFERENCES sessions(id), segment INTEGER NOT NULL,
 utc_ns INTEGER NOT NULL, mono_ns INTEGER NOT NULL, t REAL NOT NULL, retention_ns INTEGER NOT NULL,
 raw BLOB NOT NULL, seq TEXT, source_timestamp TEXT, status TEXT NOT NULL DEFAULT 'pending',
 decoded TEXT NOT NULL DEFAULT '{}', size INTEGER NOT NULL);
CREATE INDEX IF NOT EXISTS frame_session_time ON frames(session_id,t);
CREATE INDEX IF NOT EXISTS frame_retention ON frames(retention_ns);
CREATE TABLE IF NOT EXISTS measurements(
 frame_id INTEGER NOT NULL REFERENCES frames(id) ON DELETE CASCADE, key TEXT NOT NULL,
 value REAL NOT NULL, quality TEXT NOT NULL, PRIMARY KEY(frame_id,key));
CREATE INDEX IF NOT EXISTS measurement_key ON measurements(key,frame_id);
CREATE TABLE IF NOT EXISTS events(
 id INTEGER PRIMARY KEY, session_id TEXT NOT NULL REFERENCES sessions(id),
 t REAL NOT NULL, utc_ns INTEGER NOT NULL, kind TEXT NOT NULL, detail TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS incidents(
 id INTEGER PRIMARY KEY, session_id TEXT NOT NULL REFERENCES sessions(id),
 start_t REAL NOT NULL, end_t REAL NOT NULL, pre_missing INTEGER NOT NULL DEFAULT 0,
 reasons TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS compaction_progress(
 policy TEXT NOT NULL, session_id TEXT NOT NULL REFERENCES sessions(id),
 next_t REAL NOT NULL, PRIMARY KEY(policy,session_id));
CREATE INDEX IF NOT EXISTS compaction_session ON compaction_progress(session_id);
CREATE INDEX IF NOT EXISTS events_session_time ON events(session_id,t);
CREATE INDEX IF NOT EXISTS incidents_session_time ON incidents(session_id,start_t,end_t);
PRAGMA user_version=1;
"""
PIN = """EXISTS (SELECT 1 FROM incidents i WHERE i.session_id=frames.session_id
 AND frames.t BETWEEN i.start_t AND i.end_t)"""
EVENT_PIN = PIN.replace("frames.", "events.")
# Sorts before/after every real key: where a maintenance cursor starts, and
# how it marks a stable timestamp as completely measured.
FIRST_KEY = -2**63
LAST_KEY = 2**63 - 1

# Where maintain() resumes, kept in the file so a restart does not start over.
# Additive, like compaction_progress, so the schema version is unchanged and an
# older release still opens the database. Triggers keep it true across every
# frame insert, resize and delete, event insert and incident change, whoever
# writes (nothing rewrites a stored frame's or event's time or session):
#  * everything before the frame and event cursors was found pinned; a row
#    recorded behind a cursor moves that cursor back to it;
#  * ordinary_bytes is the size of every unpinned frame up to counted_id;
#  * any incident change sets pins_changed and the next pass starts over,
#    unless Store.incident() accounted for it already. Releasing protection can
#    unpin rows behind every cursor, so only starting over is safe for that.
MAINTENANCE_SCHEMA = f"""
CREATE INDEX IF NOT EXISTS events_retention ON events(utc_ns);
CREATE TABLE IF NOT EXISTS maintenance_progress(
 id INTEGER PRIMARY KEY CHECK(id=1),
 frame_retention_ns INTEGER NOT NULL, frame_id INTEGER NOT NULL,
 event_utc_ns INTEGER NOT NULL, event_id INTEGER NOT NULL,
 counted_id INTEGER NOT NULL, ordinary_bytes INTEGER NOT NULL, pins_changed INTEGER NOT NULL);
INSERT OR IGNORE INTO maintenance_progress VALUES(1,{FIRST_KEY},{FIRST_KEY},{FIRST_KEY},{FIRST_KEY},0,0,0);
CREATE TRIGGER IF NOT EXISTS maintenance_frame_behind AFTER INSERT ON frames
 WHEN (NEW.retention_ns,NEW.id)<(SELECT frame_retention_ns,frame_id FROM maintenance_progress)
 BEGIN UPDATE maintenance_progress SET frame_retention_ns=NEW.retention_ns,frame_id=NEW.id; END;
CREATE TRIGGER IF NOT EXISTS maintenance_frame_reused AFTER INSERT ON frames
 WHEN NEW.id<=(SELECT counted_id FROM maintenance_progress)
 BEGIN UPDATE maintenance_progress SET ordinary_bytes=ordinary_bytes+NEW.size
  WHERE NOT {PIN.replace("frames.", "NEW.")}; END;
CREATE TRIGGER IF NOT EXISTS maintenance_frame_resized AFTER UPDATE OF size ON frames
 WHEN NEW.id<=(SELECT counted_id FROM maintenance_progress)
 BEGIN UPDATE maintenance_progress SET ordinary_bytes=ordinary_bytes+NEW.size-OLD.size
  WHERE NOT {PIN.replace("frames.", "NEW.")}; END;
CREATE TRIGGER IF NOT EXISTS maintenance_frame_deleted AFTER DELETE ON frames
 WHEN OLD.id<=(SELECT counted_id FROM maintenance_progress)
 BEGIN UPDATE maintenance_progress SET ordinary_bytes=ordinary_bytes-OLD.size
  WHERE NOT {PIN.replace("frames.", "OLD.")}; END;
CREATE TRIGGER IF NOT EXISTS maintenance_event_behind AFTER INSERT ON events
 WHEN (NEW.utc_ns,NEW.id)<(SELECT event_utc_ns,event_id FROM maintenance_progress)
 BEGIN UPDATE maintenance_progress SET event_utc_ns=NEW.utc_ns,event_id=NEW.id; END;
CREATE TRIGGER IF NOT EXISTS maintenance_pin_added AFTER INSERT ON incidents
 BEGIN UPDATE maintenance_progress SET pins_changed=1; END;
CREATE TRIGGER IF NOT EXISTS maintenance_pin_moved AFTER UPDATE OF session_id,start_t,end_t ON incidents
 BEGIN UPDATE maintenance_progress SET pins_changed=1; END;
CREATE TRIGGER IF NOT EXISTS maintenance_pin_removed AFTER DELETE ON incidents
 BEGIN UPDATE maintenance_progress SET pins_changed=1; END;
"""
# Why the collector last stopped delivering telemetry: one of runtime.REASONS,
# "none" once a session delivers measurements. The bridge publishes it. One row
# replaced in place, so writing it never grows the file -- it is written below
# the free-space reserve too, where a collector that cannot start still has to
# say why. Additive, like maintenance_progress: the schema version is
# unchanged, and an older release ignores it.
REASON_SCHEMA = """CREATE TABLE IF NOT EXISTS collector_reason(
 id INTEGER PRIMARY KEY CHECK(id=1), reason TEXT NOT NULL, utc_ns INTEGER NOT NULL)"""
# Rows per maintenance step. A time-budgeted pass still takes one step of each
# kind, so every kind of work progresses however small the budget; a minute of
# DP3 frames (about 140) fits in one step, so routine passes keep up.
MAINTENANCE_BATCH = 256

# Collector pins lapse (see maintain()). A pin_lapse row names an incident whose
# every reason is one of events.COLLECTOR_PIN_KINDS, where its window ends in
# retention time, and the record bytes it keeps, counted a batch at a time from
# (measured_t, measured_id). Growing collector windows keep that cursor and count
# only the prefix old enough that no more rows should arrive; a settled window
# ends with a NULL cursor. Any writer's change to the incident deletes its row,
# so a window an older release extended or marked is kept until the rule is
# applied to it again: by Store.incident() when it merges, or when a Store next
# opens the database, which judges every incident without a row.
# compaction_redo lists lapsed windows the downsampler had
# already passed, so they are thinned like the history around them. Additive,
# like maintenance_progress: the schema version is unchanged.
LAPSE_SCHEMA = (
    """CREATE TABLE IF NOT EXISTS pin_lapse(
     incident_id INTEGER PRIMARY KEY, end_ns INTEGER NOT NULL, bytes INTEGER NOT NULL DEFAULT 0,
     measured_t REAL, measured_id INTEGER)""",
    "CREATE INDEX IF NOT EXISTS pin_lapse_age ON pin_lapse(end_ns,incident_id)",
    "CREATE INDEX IF NOT EXISTS pin_lapse_unmeasured ON pin_lapse(end_ns) WHERE measured_t IS NOT NULL",
    """CREATE TRIGGER IF NOT EXISTS pin_lapse_changed AFTER UPDATE ON incidents
     BEGIN DELETE FROM pin_lapse WHERE incident_id=OLD.id; END""",
    """CREATE TRIGGER IF NOT EXISTS pin_lapse_removed AFTER DELETE ON incidents
     BEGIN DELETE FROM pin_lapse WHERE incident_id=OLD.id; END""",
    """CREATE TABLE IF NOT EXISTS compaction_redo(
     id INTEGER PRIMARY KEY, session_id TEXT NOT NULL, start_t REAL NOT NULL, end_t REAL NOT NULL)""",
)
# How many frames each session holds and the id of its newest, which both
# bridges read on every publish. frame_session_time orders a session's frames
# by elapsed time, not receipt, so without this, finding the newest sorted the
# whole session and counting it read every entry. Kept by triggers, like
# maintenance_progress, so it is true whoever writes, an older release
# included, and whatever changes a frame's session or id. Additive: the schema
# version is unchanged. Writing a frame, or removing any but its session's
# newest, is a seek. Removing the newest reads the rest of the session's index
# entries to find the next. Retention does that to a session's last frames,
# when little of it is left; the recorder does it only to an authentication
# packet, which the transport already filters out.
SESSION_FRAMES_TABLE = """CREATE TABLE session_frames(
 session_id TEXT PRIMARY KEY, frames INTEGER NOT NULL, last_id INTEGER) WITHOUT ROWID"""
# A frame leaving its session, and one joining a session. The insert has no
# conflict clause, so a writer's own (INSERT OR REPLACE, say) cannot become one
# here and reset a session's count.
FRAME_LEAVES = """UPDATE session_frames SET frames=frames-1,last_id=CASE WHEN last_id=OLD.id
  THEN (SELECT MAX(id) FROM frames WHERE session_id=OLD.session_id) ELSE last_id END
  WHERE session_id=OLD.session_id;"""
FRAME_JOINS = """INSERT INTO session_frames SELECT NEW.session_id,0,NULL
  WHERE NOT EXISTS (SELECT 1 FROM session_frames WHERE session_id=NEW.session_id);
 UPDATE session_frames SET frames=frames+1,last_id=MAX(COALESCE(last_id,NEW.id),NEW.id)
  WHERE session_id=NEW.session_id;"""
SESSION_FRAMES_TRIGGERS = (
    f"CREATE TRIGGER IF NOT EXISTS session_frames_added AFTER INSERT ON frames BEGIN {FRAME_JOINS} END",
    f"CREATE TRIGGER IF NOT EXISTS session_frames_removed AFTER DELETE ON frames BEGIN {FRAME_LEAVES} END",
    f"""CREATE TRIGGER IF NOT EXISTS session_frames_moved AFTER UPDATE OF session_id,id ON frames
     WHEN NEW.session_id<>OLD.session_id OR NEW.id<>OLD.id BEGIN {FRAME_LEAVES} {FRAME_JOINS} END""",
)
# How long a collector pin protects its window, and how much record data all of
# them together may keep; past either, the oldest lapse first.
COLLECTOR_PIN_DAYS = 30
COLLECTOR_PIN_BYTES = 1_000_000_000
# A collector pin is measured once nothing more can land in its window: its
# frames have arrived by its end, and an event can merge into it 300 s later.
PIN_SETTLE_SECONDS = 600
# Free pages kept for new frames to reuse. Shrinking the file for the next
# minute's frames to grow it again would move pages for nothing.
VACUUM_SLACK_BYTES = 16 * 1024 * 1024
# Pages one incremental-vacuum step gives back: 1 MiB at the default page size.
VACUUM_STEP = 256
RESERVE_REACHED = "Storage reserve reached. Recording stopped; existing evidence is intact."

class StorageError(Exception):
    pass

def lapses(reasons):
    """Whether an incident's protection lapses: every reason is a collector kind."""
    try:
        kinds = {reason["kind"] for reason in reasons}
    except (TypeError, KeyError):
        return False
    return bool(kinds) and kinds <= COLLECTOR_PIN_KINDS

def frame_tally(conn, sid):
    """How many frames session ``sid`` holds, and the id of its newest: one seek.

    Newest means received last, the highest id. A database no Store of this
    release has opened yet has no session_frames; it is counted instead, which
    reads the session's index entries but never sorts its frames.
    """
    if conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='session_frames'").fetchone():
        row = conn.execute("SELECT frames,last_id FROM session_frames WHERE session_id=?", (sid,)).fetchone()
        return (row[0], row[1]) if row else (0, None)
    frames, last_id = conn.execute("SELECT COUNT(*),MAX(id) FROM frames WHERE session_id=?", (sid,)).fetchone()
    return frames, last_id

def connect_reader(path: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(path.resolve().as_uri() + "?mode=ro", uri=True, timeout=2)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA query_only=ON")
    return conn

@contextmanager
def read_db(path: Path):
    conn = connect_reader(path)
    try:
        # A single consistent snapshot across the caller's queries.
        conn.execute("BEGIN")
        yield conn
    finally:
        conn.close()

class Store:
    def __init__(self, path: Path, *, reserve_bytes=128 * 1024 * 1024):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.reserve_bytes = reserve_bytes
        self.lock = portalocker.Lock(str(self.path) + ".writer.lock", timeout=0)
        try:
            self.lock.acquire()
        except portalocker.exceptions.LockException:
            raise StorageError("Another recorder is writing this database.") from None
        self.conn = None
        # Opened below the free-space reserve too: nothing new is recorded
        # there (session() and add_raw() refuse), but maintenance can still
        # prune what the retention policy allows and give the space back.
        try:
            self.conn = sqlite3.connect(self.path, timeout=3)
            self.conn.row_factory = sqlite3.Row
            version = self.conn.execute("PRAGMA user_version").fetchone()[0]
            if version not in (0, 1):
                raise StorageError("This database was created by a newer OpenPowerstation version.")
            self.conn.execute("PRAGMA auto_vacuum=INCREMENTAL")
            self.conn.execute("PRAGMA journal_mode=WAL")
            # Every commit is synced unless _derived() says otherwise. See
            # the durability notes in the README.
            self.conn.execute("PRAGMA synchronous=FULL")
            self.conn.execute("PRAGMA foreign_keys=ON")
            self.conn.executescript(SCHEMA)
            self.conn.executescript(MAINTENANCE_SCHEMA)
            self.conn.execute(REASON_SCHEMA)
            self._lapse_schema()
            self._session_frames_schema()
            with self.conn:
                # Only the exclusive writer recovers abandoned sessions.
                self.conn.execute("""UPDATE sessions SET status='interrupted',
                 end_t=COALESCE((SELECT MAX(t) FROM frames WHERE session_id=sessions.id),0)
                 WHERE status='recording'""")
        except Exception as exc:
            self.close()
            # A disk too full even to open the database on is the reserve.
            if isinstance(exc, sqlite3.OperationalError) and self.space_low():
                raise StorageError(RESERVE_REACHED) from exc
            raise

    def _lapse_schema(self):
        """Create the lapse bookkeeping and judge every incident it does not cover.

        That is each incident on a database from before collector pins could
        lapse, and afterwards only the ones an older release pinned or changed
        in between; evidence windows are read and left as they are.
        """
        with self.conn:
            self.conn.execute("BEGIN")
            for statement in LAPSE_SCHEMA:
                self.conn.execute(statement)
            for row in self.conn.execute(
                    "SELECT id,session_id,start_t,end_t,reasons FROM incidents WHERE NOT EXISTS "
                    "(SELECT 1 FROM pin_lapse WHERE incident_id=incidents.id)").fetchall():
                try:
                    reasons = json.loads(row["reasons"])
                except ValueError:
                    continue
                if lapses(reasons):
                    self._lapse_later(row["id"], row["session_id"], row["start_t"], row["end_t"])

    def _session_frames_schema(self):
        """Create the per-session frame tally, counting what is already stored once.

        The count reads frame_session_time rather than the frames themselves,
        and shares its transaction with the triggers, so no frame is missed or
        counted twice.
        """
        with self.conn:
            self.conn.execute("BEGIN")
            if not self.conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' "
                                     "AND name='session_frames'").fetchone():
                self.conn.execute(SESSION_FRAMES_TABLE)
                self.conn.execute("INSERT INTO session_frames "
                                  "SELECT session_id,COUNT(*),MAX(id) FROM frames GROUP BY session_id")
            for statement in SESSION_FRAMES_TRIGGERS:
                self.conn.execute(statement)

    def _lapse_later(self, incident_id, sid, start, end, *, previous=None, old_end=None):
        # end_ns is in retention time, like a frame's retention_ns: session start plus elapsed.
        if previous is None:
            self.conn.execute("""INSERT OR REPLACE INTO pin_lapse(
             incident_id,end_ns,bytes,measured_t,measured_id)
             SELECT ?,start_utc_ns+?,0,?,? FROM sessions WHERE id=?""",
                              (incident_id, int(end*1e9), start, FIRST_KEY, sid))
            return
        measured_t, measured_id = previous["measured_t"], previous["measured_id"]
        # A window that had settled is complete only through its old end. Reopen
        # its cursor there when the same collector incident grows forward.
        if measured_t is None and old_end is not None and end > old_end:
            measured_t, measured_id = old_end, LAST_KEY
        self.conn.execute("""INSERT OR REPLACE INTO pin_lapse(
         incident_id,end_ns,bytes,measured_t,measured_id)
         SELECT ?,start_utc_ns+?,?,?,? FROM sessions WHERE id=?""",
                          (incident_id, int(end*1e9), previous["bytes"],
                           measured_t, measured_id, sid))

    def space_low(self):
        return shutil.disk_usage(self.path.parent).free < self.reserve_bytes

    def check_space(self):
        if self.space_low():
            raise StorageError(RESERVE_REACHED)

    def release_space(self):
        """Give every free page back and fold the WAL into the file.

        For a store below its reserve: a routine pass keeps some free pages
        for new frames to reuse, and its checkpoint does not wait for readers.
        Still a step at a time, because a disk this full has no room for the
        WAL that one unbounded vacuum writes before its checkpoint.
        """
        self._vacuum(lambda: False, {"pages_freed": 0}, slack_bytes=0)
        self._step("PRAGMA wal_checkpoint(TRUNCATE)")

    def _step(self, sql):
        # Stepped to the end without keeping the rows: incremental_vacuum
        # frees one page per row, and a statement left unstepped frees one.
        for _ in self.conn.execute(sql):
            pass

    @contextmanager
    def _derived(self):
        """A transaction holding only what the committed raw frame can derive again.

        It commits at synchronous=NORMAL: not synced by itself, it becomes
        durable with the next FULL commit, the next raw frame's. WAL keeps
        commits in order, so a power loss in between can leave the newest
        frame pending, never a torn or reordered history.
        """
        if self.conn.in_transaction:
            # The level cannot change inside a transaction; the caller's commit decides.
            with self.conn:
                yield
            return
        self.conn.execute("PRAGMA synchronous=NORMAL")
        try:
            with self.conn:
                yield
        finally:
            if self.conn.in_transaction:
                # Only a failed commit leaves one open, and the level cannot
                # go back to FULL until it is gone.
                self.conn.rollback()
            self.conn.execute("PRAGMA synchronous=FULL")

    def session(self, *, utc_ns=None, mono_ns=None, synthetic=False, firmware="", conditions=""):
        # A recording never starts below the reserve.
        self.check_space()
        sid = uuid.uuid4().hex
        utc_ns = time.time_ns() if utc_ns is None else utc_ns
        mono_ns = time.monotonic_ns() if mono_ns is None else mono_ns
        with self.conn:
            self.conn.execute("INSERT INTO sessions VALUES(?,?,?,NULL,'recording',?,?,?,?)",
                              (sid, utc_ns, mono_ns, int(synthetic), firmware, conditions, DECODER_VERSION))
        return sid

    def finish(self, sid, t, status="stopped"):
        with self.conn:
            self.conn.execute("UPDATE sessions SET status=?,end_t=? WHERE id=?", (status, t, sid))

    def set_reason(self, reason, utc_ns=None):
        """Replace the collector's reason (REASON_SCHEMA). Not refused below the reserve."""
        utc_ns = time.time_ns() if utc_ns is None else utc_ns
        with self.conn:
            self.conn.execute("INSERT OR REPLACE INTO collector_reason VALUES(1,?,?)", (reason, utc_ns))

    def add_raw(self, sid, segment, utc_ns, mono_ns, t, raw, retention_ns):
        self.check_space()
        with self.conn:
            cur = self.conn.execute("""INSERT INTO frames(session_id,segment,utc_ns,mono_ns,t,
             retention_ns,raw,size) VALUES(?,?,?,?,?,?,?,?)""",
                                    (sid, segment, utc_ns, mono_ns, t, retention_ns, raw, len(raw) + 256))
            # Imported/reordered observations can arrive behind a saved watermark.
            self.conn.execute("UPDATE compaction_progress SET next_t=MIN(next_t,?) WHERE session_id=?",
                              (t, sid))
        return cur.lastrowid

    def interpret(self, frame_id, packet, decoded, duplicate=False):
        status = "repeated_unverified" if duplicate else decoded.status
        doc = json.dumps(decoded.fields, ensure_ascii=False, allow_nan=False)
        seq = packet.seq.hex() if packet else None
        with self._derived():
            self.conn.execute("UPDATE frames SET seq=?,status=?,decoded=?,size=size+? WHERE id=?",
                              (seq, status, doc, len(doc.encode()) + len(decoded.measurements)*150, frame_id))
            self.conn.executemany("INSERT INTO measurements VALUES(?,?,?,?)",
                                 [(frame_id, key, val, "repeated_unverified" if duplicate else decoded.quality[key])
                                  for key, val in decoded.measurements.items()])

    def interpret_observation(self, frame_id, fields, measurements, quality="ble_observed"):
        """Store a non-EcoFlow observation (for example a Jackery cloud snapshot)."""
        doc = json.dumps(fields, ensure_ascii=False, allow_nan=False)
        with self._derived():
            self.conn.execute("UPDATE frames SET status='decoded',decoded=?,size=size+? WHERE id=?",
                              (doc, len(doc.encode()) + len(measurements) * 150, frame_id))
            self.conn.executemany("INSERT INTO measurements VALUES(?,?,?,?)",
                                 [(frame_id, key, value, quality) for key, value in measurements.items()])

    def invalid(self, frame_id):
        with self.conn:
            self.conn.execute("UPDATE frames SET status='invalid_packet' WHERE id=?", (frame_id,))

    def event(self, sid, t, utc_ns, kind, detail):
        with self.conn:
            self.conn.execute("INSERT INTO events(session_id,t,utc_ns,kind,detail) VALUES(?,?,?,?,?)",
                              (sid, t, utc_ns, kind, detail))

    def incident(self, sid, t, reason, before=300, after=300):
        start, end = t-before, t+after
        with self.conn:
            pending = self.conn.execute("SELECT pins_changed FROM maintenance_progress").fetchone()[0]
            # Counted frames this window newly covers stop being ordinary bytes.
            # Everything else the merge below covers was pinned already, so the
            # cost is the requested window, never the merged incident.
            self.conn.execute(f"""UPDATE maintenance_progress SET ordinary_bytes=ordinary_bytes-(
             SELECT COALESCE(SUM(size),0) FROM frames WHERE session_id=? AND t BETWEEN ? AND ?
             AND id<=maintenance_progress.counted_id AND NOT {PIN})""", (sid, start, end))
            overlaps = self.conn.execute(
                "SELECT * FROM incidents WHERE session_id=? AND end_t>=? AND start_t<=?",
                (sid, start, end)).fetchall()
            # Preserve byte-accounting progress when one lapseable collector
            # incident only grows forward. The UPDATE trigger removes pin_lapse,
            # so capture its cursor before changing the incident and restore it
            # below instead of restarting the count from the beginning.
            prior_lapse = None
            prior_end = None
            if len(overlaps) == 1:
                prior_end = overlaps[0]["end_t"]
                prior_lapse = self.conn.execute(
                    "SELECT * FROM pin_lapse WHERE incident_id=?", (overlaps[0]["id"],)).fetchone()
            earliest = self.conn.execute("SELECT MIN(t) FROM frames WHERE session_id=?", (sid,)).fetchone()[0]
            reasons = [{"t": t, "kind": reason}]
            pre_missing = earliest is None or earliest > start
            original_start = overlaps[0]["start_t"] if len(overlaps) == 1 else None
            for row in overlaps:
                start, end = min(start, row["start_t"]), max(end, row["end_t"])
                reasons.extend(json.loads(row["reasons"]))
                pre_missing |= bool(row["pre_missing"])
            if overlaps:
                keep = overlaps[0]["id"]
                self.conn.execute("UPDATE incidents SET start_t=?,end_t=?,pre_missing=?,reasons=? WHERE id=?",
                                  (start, end, int(pre_missing), json.dumps(reasons), keep))
                self.conn.executemany("DELETE FROM incidents WHERE id=?", [(r["id"],) for r in overlaps[1:]])
            else:
                keep = self.conn.execute(
                    "INSERT INTO incidents(session_id,start_t,end_t,pre_missing,reasons) VALUES(?,?,?,?,?)",
                    (sid, start, end, int(pre_missing), json.dumps(reasons))).lastrowid
            # The triggers dropped any lapse the merged windows had; one reason
            # of another kind keeps the whole merged window.
            if lapses(reasons):
                preserve = (prior_lapse if len(overlaps) == 1 and start == original_start
                            and end >= prior_end else None)
                self._lapse_later(keep, sid, start, end, previous=preserve, old_end=prior_end)
            if not pending:
                # Pinning more only removes ordinary bytes, accounted above; the
                # cursors stay valid. A change already pending still restarts.
                self.conn.execute("UPDATE maintenance_progress SET pins_changed=0")

    def delete_incident(self, incident_id):
        # Frames behind maintain()'s cursors may now be unpinned. The delete
        # sets pins_changed, so the next pass recounts and rewalks from the start.
        with self.conn:
            self.conn.execute("DELETE FROM compaction_progress WHERE session_id IN "
                              "(SELECT session_id FROM incidents WHERE id=?)", (incident_id,))
            self.conn.execute("DELETE FROM incidents WHERE id=?", (incident_id,))

    def maintain(self, now_ns, *, days=7, ordinary_bytes=5_000_000_000, pin_days=COLLECTOR_PIN_DAYS,
                 pin_bytes=COLLECTOR_PIN_BYTES, time_budget=None, stop_at_reserve=True):
        """Prune ordinary history and lapse collector pins; evidence pins are never touched.

        ``days=None`` keeps history indefinitely.  The Jackery path uses that and
        thins old frames with ``downsample`` instead, because deleting by age
        would silently undo the thinning and lose the minute-level record.

        A pin whose every reason is a collector kind (events.COLLECTOR_PIN_KINDS)
        protects its window for ``pin_days`` after the window ends, and all of
        them together keep at most ``pin_bytes`` of record data; past either,
        the oldest lapse first. A lapsed window is ordinary history from then
        on, so what it holds beyond ``days`` goes at once. ``None`` lifts either
        limit. Every other pin -- manual, device error, suspect telemetry, an
        unmapped change -- is evidence and never lapses.

        A pass costs what changed since the previous one, not what is stored:
        with the durable cursors of MAINTENANCE_SCHEMA a new frame is counted
        once and expired pinned history is walked past once. The size cap waits
        until every frame is counted. ``time_budget`` bounds a pass the way it
        bounds ``downsample``; work left over resumes on the next pass, and the
        returned summary's ``complete`` says whether any remains. Free pages
        beyond VACUUM_SLACK_BYTES go back to the filesystem, at least one step
        a pass. Unless ``stop_at_reserve`` is false, a pass that leaves free
        space below the reserve raises StorageError, which stops recording.
        """
        # perf_counter is monotonic too, and finer than the 15.6 ms steps of
        # time.monotonic() on Windows.
        began = time.perf_counter()
        deadline = None if time_budget is None else began + time_budget
        summary = {"counted": 0, "examined": 0, "deleted": 0, "events_examined": 0,
                   "events_deleted": 0, "pins_lapsed": 0, "pages_freed": 0, "complete": True}

        def spent():
            if deadline is not None and time.perf_counter() >= deadline:
                summary["complete"] = False
                return True
            return False

        progress = self.conn.execute("SELECT * FROM maintenance_progress").fetchone()
        if progress["pins_changed"]:
            with self.conn:
                self.conn.execute("""UPDATE maintenance_progress SET frame_retention_ns=?,frame_id=?,
                 event_utc_ns=?,event_id=?,counted_id=0,ordinary_bytes=0,pins_changed=0""", (FIRST_KEY,)*4)
            progress = self.conn.execute("SELECT * FROM maintenance_progress").fetchone()
        cutoff = None if days is None else now_ns - int(days*86400*1e9)
        # Age first: it is what bounds the disk, so a counting backlog after an
        # upgrade or a released incident must not starve it.
        self._prune_frames((progress["frame_retention_ns"], progress["frame_id"]),
                           cutoff, None, spent, summary)
        self._lapse_pins(now_ns, cutoff, pin_days, pin_bytes, spent, summary)
        # Both steps keep their cursors in the table, and a lapse can move them back.
        progress = self.conn.execute("SELECT * FROM maintenance_progress").fetchone()
        if self._count(progress["counted_id"], spent, summary):
            self._prune_frames((progress["frame_retention_ns"], progress["frame_id"]),
                               cutoff, ordinary_bytes, spent, summary)
        else:
            summary["complete"] = False
        if cutoff is not None:
            self._prune_events((progress["event_utc_ns"], progress["event_id"]), cutoff, spent, summary)
        self._vacuum(spent, summary)
        # After the vacuum: a checkpoint that completes truncates the file.
        self.conn.execute("PRAGMA wal_checkpoint(PASSIVE)")
        if stop_at_reserve:
            self.check_space()
        summary["seconds"] = time.perf_counter() - began
        return summary

    def _vacuum(self, spent, summary, *, slack_bytes=None):
        """Give free pages beyond ``slack_bytes`` (VACUUM_SLACK_BYTES) back, a step at a time.

        Each step is its own transaction, so SQLite's automatic checkpoint
        after it can keep the WAL near its usual 1,000 pages however much the
        steps move in total.
        """
        slack_bytes = VACUUM_SLACK_BYTES if slack_bytes is None else slack_bytes
        keep = slack_bytes // self.conn.execute("PRAGMA page_size").fetchone()[0]
        free = self.conn.execute("PRAGMA freelist_count").fetchone()[0]
        while free > keep:
            self._step(f"PRAGMA incremental_vacuum({min(free - keep, VACUUM_STEP)})")
            left = self.conn.execute("PRAGMA freelist_count").fetchone()[0]
            summary["pages_freed"] += free - left
            # Nothing freed: a database without incremental auto-vacuum.
            if left >= free or spent():
                return
            free = left

    def _lapse_pins(self, now_ns, cutoff, pin_days, pin_bytes, spent, summary):
        """Lift collector pins past their age or over their byte cap, oldest first."""
        expiry = None if pin_days is None else now_ns - int(pin_days*86400*1e9)
        if pin_bytes is not None:
            self._measure_pins(expiry, now_ns - PIN_SETTLE_SECONDS * 10**9, spent)
        first = True
        while True:
            row = self.conn.execute(
                "SELECT l.incident_id,l.end_ns,l.measured_t,i.session_id,i.start_t,i.end_t,i.reasons "
                "FROM pin_lapse l LEFT JOIN incidents i ON i.id=l.incident_id "
                "ORDER BY l.end_ns,l.incident_id LIMIT 1").fetchone()
            if row is None:
                return
            aged = expiry is not None and row["end_ns"] < expiry
            # A growing window may already have a stable, measured prefix.
            # Once measured collector bytes exceed the cap, releasing the oldest
            # whole window is safe even if its newest tail is not settled yet.
            measured_bytes = self.conn.execute(
                "SELECT COALESCE(SUM(bytes),0) FROM pin_lapse").fetchone()[0]
            over = pin_bytes is not None and measured_bytes > pin_bytes
            if not (aged or over) or (not first and spent()):
                return
            first = False
            try:
                reasons = json.loads(row["reasons"])
            except (TypeError, ValueError):
                reasons = None
            if not lapses(reasons):
                # Unreachable while the triggers hold; a window is kept when in doubt.
                with self.conn:
                    self.conn.execute("DELETE FROM pin_lapse WHERE incident_id=?", (row["incident_id"],))
                continue
            if not self._release(row, cutoff, spent, summary):
                summary["complete"] = False
                return
            summary["pins_lapsed"] += 1

    def _measure_pins(self, expiry, settled_ns, spent):
        """Count stable collector-pin bytes a batch at a time.

        A window need not finish before it starts counting. For a growing
        incident, only the prefix at least PIN_SETTLE_SECONDS behind now is
        eligible; once the whole window is stable its cursor becomes NULL.
        Pins already past expiry lapse whatever they keep, so they are not
        counted.
        """
        while True:
            row = self.conn.execute(
                "SELECT l.incident_id,l.end_ns,l.measured_t,l.measured_id,"
                "i.session_id,i.end_t,s.start_utc_ns "
                "FROM pin_lapse l JOIN incidents i ON i.id=l.incident_id "
                "JOIN sessions s ON s.id=i.session_id "
                "WHERE l.measured_t IS NOT NULL AND l.end_ns>=? "
                "AND s.start_utc_ns+CAST(l.measured_t*1000000000 AS INTEGER)<=MIN(l.end_ns,?) "
                "AND (s.start_utc_ns+CAST(l.measured_t*1000000000 AS INTEGER)<MIN(l.end_ns,?) "
                "OR l.measured_id<?) ORDER BY l.end_ns LIMIT 1",
                (FIRST_KEY if expiry is None else expiry, settled_ns, settled_ns, LAST_KEY)).fetchone()
            if row is None:
                return
            stable_end_ns = min(row["end_ns"], settled_ns)
            stable_end_t = (stable_end_ns - row["start_utc_ns"]) / 1e9
            # The cursor is spelled out as in _prune_frames, for the same reason.
            frames = self.conn.execute(
                "SELECT id,t,size FROM frames WHERE session_id=? AND t>=? AND t<=? AND (t>? OR id>=?) "
                "ORDER BY t,id LIMIT ?", (row["session_id"], row["measured_t"], stable_end_t,
                                          row["measured_t"], row["measured_id"], MAINTENANCE_BATCH)).fetchall()
            if len(frames) == MAINTENANCE_BATCH:
                cursor = (frames[-1]["t"], frames[-1]["id"] + 1)
            elif stable_end_ns >= row["end_ns"]:
                cursor = (None, None)
            else:
                # No more rows can land in this stable prefix. Mark the whole
                # prefix consumed so an always-growing incident resumes later.
                cursor = (stable_end_t, LAST_KEY)
            with self.conn:
                self.conn.execute("UPDATE pin_lapse SET bytes=bytes+?,measured_t=?,measured_id=? "
                                  "WHERE incident_id=?", (sum(f["size"] for f in frames), *cursor,
                                                          row["incident_id"]))
            if spent():
                return

    def _release(self, row, cutoff, spent, summary):
        """Lift one collector pin; False while its expired rows are still being deleted.

        What the window holds past the age cutoff goes first, a batch at a
        time and while the pin still covers it, so the ordinary total never
        counts it. The rest then becomes ordinary history: counted, put back in
        front of the retention cursors if they have passed it, and queued for
        thinning if the downsampler has.
        """
        iid, sid, start, end = row["incident_id"], row["session_id"], row["start_t"], row["end_t"]
        # Kept by another incident: merging stops windows overlapping, but an
        # older writer may not have merged. The unary plus keeps the seek on
        # the session index rather than every expired row.
        only_this = ("NOT EXISTS (SELECT 1 FROM incidents o WHERE o.session_id={0}.session_id "
                     "AND {0}.t BETWEEN o.start_t AND o.end_t AND o.id<>?)")
        while cutoff is not None:
            doomed = self.conn.execute(
                f"SELECT id FROM frames WHERE session_id=? AND t>=? AND t<=? AND +retention_ns<? "
                f"AND {only_this.format('frames')} LIMIT ?",
                (sid, start, end, cutoff, iid, MAINTENANCE_BATCH)).fetchall()
            gone = self.conn.execute(
                f"SELECT id FROM events WHERE session_id=? AND t>=? AND t<=? AND +utc_ns<? "
                f"AND {only_this.format('events')} LIMIT ?",
                (sid, start, end, cutoff, iid, MAINTENANCE_BATCH)).fetchall()
            if doomed or gone:
                with self.conn:
                    self.conn.executemany("DELETE FROM frames WHERE id=?", doomed)
                    self.conn.executemany("DELETE FROM events WHERE id=?", gone)
                summary["deleted"] += len(doomed)
                summary["events_deleted"] += len(gone)
            if len(doomed) < MAINTENANCE_BATCH and len(gone) < MAINTENANCE_BATCH:
                break
            if spent():
                return False
        window = (sid, start, end)
        with self.conn:
            pending = self.conn.execute("SELECT pins_changed FROM maintenance_progress").fetchone()[0]
            self.conn.execute("DELETE FROM incidents WHERE id=?", (iid,))
            self.conn.execute(f"""UPDATE maintenance_progress SET ordinary_bytes=ordinary_bytes+(
             SELECT COALESCE(SUM(size),0) FROM frames WHERE session_id=? AND t BETWEEN ? AND ?
             AND id<=maintenance_progress.counted_id AND NOT {PIN})""", window)
            first = self.conn.execute(f"SELECT MIN(+retention_ns) FROM frames WHERE session_id=? "
                                      f"AND t BETWEEN ? AND ? AND NOT {PIN}", window).fetchone()[0]
            if first is not None:
                self.conn.execute("UPDATE maintenance_progress SET frame_retention_ns=?,frame_id=? "
                                  "WHERE (frame_retention_ns,frame_id)>(?,?)", (first, FIRST_KEY) * 2)
            first = self.conn.execute(f"SELECT MIN(+utc_ns) FROM events WHERE session_id=? "
                                      f"AND t BETWEEN ? AND ? AND NOT {EVENT_PIN}", window).fetchone()[0]
            if first is not None:
                self.conn.execute("UPDATE maintenance_progress SET event_utc_ns=?,event_id=? "
                                  "WHERE (event_utc_ns,event_id)>(?,?)", (first, FIRST_KEY) * 2)
            self.conn.execute("INSERT INTO compaction_redo(session_id,start_t,end_t) SELECT ?,?,? WHERE EXISTS "
                              "(SELECT 1 FROM compaction_progress WHERE session_id=? AND next_t>?)",
                              (*window, sid, start))
            if not pending:
                # Accounted for above, as Store.incident() accounts for a new pin.
                self.conn.execute("UPDATE maintenance_progress SET pins_changed=0")
        return True

    # Each maintenance step below takes at least one batch, then stops when its
    # work is done or ``spent()`` says the pass's budget is used up.

    def _count(self, counted, spent, summary):
        """Add frames recorded since ``counted`` to the ordinary total; True once all are."""
        while True:
            rows = self.conn.execute(f"SELECT id,size,{PIN} AS pinned FROM frames WHERE id>? "
                                     "ORDER BY id LIMIT ?", (counted, MAINTENANCE_BATCH)).fetchall()
            if rows:
                counted = rows[-1]["id"]
                with self.conn:
                    self.conn.execute("UPDATE maintenance_progress SET counted_id=?,"
                                      "ordinary_bytes=ordinary_bytes+?",
                                      (counted, sum(r["size"] for r in rows if not r["pinned"])))
                summary["counted"] += len(rows)
            if len(rows) < MAINTENANCE_BATCH:
                return True
            if spent():
                return False

    def _prune_frames(self, cursor, cutoff, cap, spent, summary):
        """Walk retention order from ``cursor``, deleting unpinned frames while
        they are expired or, given a ``cap``, while ordinary bytes exceed it."""
        while True:
            size = self.conn.execute("SELECT ordinary_bytes FROM maintenance_progress").fetchone()[0]
            doomed, steps, stopped = [], 0, False
            # Stepped lazily: a pass with nothing due reads one row, not a batch.
            # The cursor is spelled out rather than as a row value, so the seek
            # on frame_retention does not depend on the SQLite version.
            rows = self.conn.execute(
                f"SELECT id,retention_ns,size,{PIN} AS pinned FROM frames "
                "WHERE retention_ns>=? AND (retention_ns>? OR id>=?) ORDER BY retention_ns,id LIMIT ?",
                (cursor[0], *cursor, MAINTENANCE_BATCH))
            for row in rows:
                expired = cutoff is not None and row["retention_ns"] < cutoff
                if not expired and not (cap is not None and size > cap):
                    stopped = True
                    break
                if not row["pinned"]:
                    doomed.append((row["id"],))
                    # With a cap every frame is counted, as the delete trigger assumes.
                    size -= row["size"]
                cursor = row["retention_ns"], row["id"] + 1
                steps += 1
            rows.close()
            if steps:
                with self.conn:
                    self.conn.executemany("DELETE FROM frames WHERE id=?", doomed)
                    self.conn.execute("UPDATE maintenance_progress SET frame_retention_ns=?,frame_id=?",
                                      cursor)
                summary["examined"] += steps
                summary["deleted"] += len(doomed)
            if stopped or steps < MAINTENANCE_BATCH or spent():
                return cursor

    def _prune_events(self, cursor, cutoff, spent, summary):
        """Walk receipt-time order from ``cursor``, deleting unpinned events before ``cutoff``.

        Events have no retention time of their own; they age by receipt UTC,
        which a clock change can put out of insertion order.
        """
        while True:
            doomed, steps, stopped = [], 0, False
            rows = self.conn.execute(
                f"SELECT id,utc_ns,{EVENT_PIN} AS pinned FROM events "
                "WHERE utc_ns>=? AND (utc_ns>? OR id>=?) ORDER BY utc_ns,id LIMIT ?",
                (cursor[0], *cursor, MAINTENANCE_BATCH))
            for row in rows:
                if row["utc_ns"] >= cutoff:
                    stopped = True
                    break
                if not row["pinned"]:
                    doomed.append((row["id"],))
                cursor = row["utc_ns"], row["id"] + 1
                steps += 1
            rows.close()
            if steps:
                with self.conn:
                    self.conn.executemany("DELETE FROM events WHERE id=?", doomed)
                    self.conn.execute("UPDATE maintenance_progress SET event_utc_ns=?,event_id=?", cursor)
                summary["events_examined"] += steps
                summary["events_deleted"] += len(doomed)
            if stopped or steps < MAINTENANCE_BATCH or spent():
                return

    def downsample(self, now_ns, *, grace_seconds=172_800, bucket_seconds=60,
                   peak_keys=(), dry_run=False, batch=500, max_buckets=None,
                   time_budget=None):
        """Compact completed buckets with indexed seeks and a durable watermark.

        Decisions and progress commit together. A later pass resumes after the
        completed buckets, including after restart. Late frames and removal of
        incident protection invalidate progress. Grace-straddling buckets wait
        until their entire time window is eligible, preserving a single first
        frame and the true extrema. Automatic callers bound work per cycle.
        A collector pin that lapses behind the watermark queues its window in
        compaction_redo; those buckets are thinned first, and only they.
        """
        if not math.isfinite(bucket_seconds) or bucket_seconds <= 0:
            raise ValueError("Compaction bucket must be a positive finite number.")
        if not math.isfinite(grace_seconds) or grace_seconds < 0:
            raise ValueError("Compaction grace must be a nonnegative finite number.")
        cutoff = now_ns - int(grace_seconds * 1e9)
        policy = json.dumps([float(bucket_seconds), sorted(peak_keys)])
        summary = {"scanned": 0, "kept": 0, "deleted": 0, "buckets": 0, "pinned_skipped": 0}
        deadline = None if time_budget is None else time.monotonic() + time_budget

        def exhausted():
            return ((max_buckets is not None and summary["buckets"] >= max_buckets)
                    or (deadline is not None and time.monotonic() >= deadline))

        def thin(sid, start):
            """Frames to delete from the bucket at ``start``; None while any is inside the grace."""
            end = start + bucket_seconds
            rows = self.conn.execute(
                f"SELECT id,t,retention_ns,{PIN} AS pinned FROM frames "
                "WHERE session_id=? AND t>=? AND t<? ORDER BY t,id",
                (sid, start, end)).fetchall()
            # Retention is monotonic-derived; do not substitute wall-clock UTC.
            if any(r["retention_ns"] + int((end-r["t"]) * 1e9) > cutoff for r in rows):
                return None
            keep = {rows[0]["id"]}
            pinned = {r["id"] for r in rows if r["pinned"]}
            keep.update(pinned)
            if peak_keys:
                extremes = {}
                query = ("SELECT m.frame_id,m.key,m.value FROM frames f "
                         "JOIN measurements m ON m.frame_id=f.id "
                         "WHERE f.session_id=? AND f.t>=? AND f.t<? AND m.key IN (" +
                         ",".join("?" for _ in peak_keys) + ") ORDER BY f.t,f.id")
                for frame_id, key, value in self.conn.execute(query, (sid,start,end,*peak_keys)):
                    low, high = extremes.get(key, ((frame_id,value), (frame_id,value)))
                    if value < low[1]: low = (frame_id,value)
                    if value > high[1]: high = (frame_id,value)
                    extremes[key] = low, high
                for low, high in extremes.values():
                    keep.update((low[0], high[0]))
            doomed = [(r["id"],) for r in rows if r["id"] not in keep]
            summary["scanned"] += len(rows)
            summary["kept"] += len(rows) - len(doomed)
            summary["deleted"] += len(doomed)
            summary["buckets"] += 1
            summary["pinned_skipped"] += len(pinned)
            return doomed

        for redo in self.conn.execute("SELECT * FROM compaction_redo ORDER BY id").fetchall():
            sid = redo["session_id"]
            progress = self.conn.execute(
                "SELECT next_t FROM compaction_progress WHERE policy=? AND session_id=?",
                (policy, sid)).fetchone()
            cursor, done = redo["start_t"], progress is None
            while not done:
                if exhausted():
                    return summary
                first = self.conn.execute(
                    "SELECT t FROM frames WHERE session_id=? AND t>=? AND t<=? ORDER BY t LIMIT 1",
                    (sid, cursor, redo["end_t"])).fetchone()
                start = None if first is None else math.floor(first[0] / bucket_seconds) * bucket_seconds
                # Buckets from the watermark on are the walk below's.
                if start is None or start >= progress[0]:
                    done = True
                    break
                doomed = thin(sid, start)
                if doomed is None:
                    break
                cursor = start + bucket_seconds
                if not dry_run:
                    with self.conn:
                        self.conn.executemany("DELETE FROM frames WHERE id=?", doomed)
                        self.conn.execute("UPDATE compaction_redo SET start_t=? WHERE id=?", (cursor, redo["id"]))
            if done and not dry_run:
                with self.conn:
                    self.conn.execute("DELETE FROM compaction_redo WHERE id=?", (redo["id"],))

        sessions = self.conn.execute("SELECT id FROM sessions ORDER BY start_utc_ns,id").fetchall()
        for (sid,) in sessions:
            progress = self.conn.execute(
                "SELECT next_t FROM compaction_progress WHERE policy=? AND session_id=?",
                (policy, sid)).fetchone()
            cursor = math.floor(progress[0] / bucket_seconds) * bucket_seconds if progress else -1e300
            while True:
                if exhausted():
                    return summary
                first = self.conn.execute(
                    "SELECT t FROM frames WHERE session_id=? AND t>=? ORDER BY t LIMIT 1",
                    (sid, cursor)).fetchone()
                if first is None:
                    break
                start = math.floor(first[0] / bucket_seconds) * bucket_seconds
                end = start + bucket_seconds
                doomed = thin(sid, start)
                if doomed is None:
                    break
                if not dry_run:
                    with self.conn:
                        self.conn.executemany("DELETE FROM frames WHERE id=?", doomed)
                        self.conn.execute("INSERT INTO compaction_progress VALUES(?,?,?) "
                                          "ON CONFLICT(policy,session_id) DO UPDATE SET next_t=excluded.next_t",
                                          (policy, sid, end))
                cursor = end
        return summary

    def close(self):
        if getattr(self, "conn", None):
            self.conn.close()
            self.conn = None
        if getattr(self, "lock", None):
            self.lock.release()

    def __enter__(self):
        return self

    def __exit__(self, *_):
        self.close()
