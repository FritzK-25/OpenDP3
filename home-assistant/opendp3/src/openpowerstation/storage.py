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

class StorageError(Exception):
    pass

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
        try:
            self.check_space()
            self.conn = sqlite3.connect(self.path, timeout=3)
            self.conn.row_factory = sqlite3.Row
            version = self.conn.execute("PRAGMA user_version").fetchone()[0]
            if version not in (0, 1):
                raise StorageError("This database was created by a newer OpenPowerstation version.")
            self.conn.execute("PRAGMA auto_vacuum=INCREMENTAL")
            self.conn.execute("PRAGMA journal_mode=WAL")
            self.conn.execute("PRAGMA synchronous=FULL")
            self.conn.execute("PRAGMA foreign_keys=ON")
            self.conn.executescript(SCHEMA)
            with self.conn:
                # Only the exclusive writer recovers abandoned sessions.
                self.conn.execute("""UPDATE sessions SET status='interrupted',
                 end_t=COALESCE((SELECT MAX(t) FROM frames WHERE session_id=sessions.id),0)
                 WHERE status='recording'""")
        except Exception:
            self.close()
            raise

    def check_space(self):
        if shutil.disk_usage(self.path.parent).free < self.reserve_bytes:
            raise StorageError("Storage reserve reached. Recording stopped; existing evidence is intact.")

    def session(self, *, utc_ns=None, mono_ns=None, synthetic=False, firmware="", conditions=""):
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
        with self.conn:
            self.conn.execute("UPDATE frames SET seq=?,status=?,decoded=?,size=size+? WHERE id=?",
                              (seq, status, doc, len(doc.encode()) + len(decoded.measurements)*150, frame_id))
            self.conn.executemany("INSERT INTO measurements VALUES(?,?,?,?)",
                                 [(frame_id, key, val, "repeated_unverified" if duplicate else decoded.quality[key])
                                  for key, val in decoded.measurements.items()])

    def interpret_observation(self, frame_id, fields, measurements, quality="ble_observed"):
        """Store a non-EcoFlow observation (for example a Jackery cloud snapshot)."""
        doc = json.dumps(fields, ensure_ascii=False, allow_nan=False)
        with self.conn:
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
            overlaps = self.conn.execute(
                "SELECT * FROM incidents WHERE session_id=? AND end_t>=? AND start_t<=?",
                (sid, start, end)).fetchall()
            earliest = self.conn.execute("SELECT MIN(t) FROM frames WHERE session_id=?", (sid,)).fetchone()[0]
            reasons = [{"t": t, "kind": reason}]
            pre_missing = earliest is None or earliest > start
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
                self.conn.execute("INSERT INTO incidents(session_id,start_t,end_t,pre_missing,reasons) VALUES(?,?,?,?,?)",
                                  (sid, start, end, int(pre_missing), json.dumps(reasons)))

    def delete_incident(self, incident_id):
        with self.conn:
            self.conn.execute("DELETE FROM compaction_progress WHERE session_id IN "
                              "(SELECT session_id FROM incidents WHERE id=?)", (incident_id,))
            self.conn.execute("DELETE FROM incidents WHERE id=?", (incident_id,))

    def maintain(self, now_ns, *, days=7, ordinary_bytes=5_000_000_000):
        """Prune ordinary history; incident windows are never touched.

        ``days=None`` keeps history indefinitely.  The Jackery path uses that and
        thins old frames with ``downsample`` instead, because deleting by age
        would silently undo the thinning and lose the minute-level record.
        """
        cutoff = now_ns - int((days or 0)*86400*1e9)
        while days is not None:
            rows = self.conn.execute(
                f"SELECT id FROM frames WHERE retention_ns<? AND NOT {PIN} LIMIT 2000", (cutoff,)).fetchall()
            if not rows:
                break
            with self.conn:
                self.conn.executemany("DELETE FROM frames WHERE id=?", [(r[0],) for r in rows])
        size = self.conn.execute(f"SELECT COALESCE(SUM(size),0) FROM frames WHERE NOT {PIN}").fetchone()[0]
        while size > ordinary_bytes:
            rows = self.conn.execute(f"SELECT id,size FROM frames WHERE NOT {PIN} ORDER BY retention_ns LIMIT 2000").fetchall()
            if not rows:
                break
            selected = []
            for row in rows:
                selected.append((row["id"],))
                size -= row["size"]
                if size <= ordinary_bytes:
                    break
            with self.conn:
                self.conn.executemany("DELETE FROM frames WHERE id=?", selected)
        if days is not None:
            with self.conn:
                self.conn.execute("""DELETE FROM events WHERE utc_ns<? AND NOT EXISTS
                 (SELECT 1 FROM incidents i WHERE i.session_id=events.session_id
                 AND events.t BETWEEN i.start_t AND i.end_t)""", (cutoff,))
        self.conn.execute("PRAGMA wal_checkpoint(PASSIVE)")
        self.conn.execute("PRAGMA incremental_vacuum(2000)")
        self.check_space()

    def downsample(self, now_ns, *, grace_seconds=172_800, bucket_seconds=60,
                   peak_keys=(), dry_run=False, batch=500, max_buckets=None,
                   time_budget=None):
        """Compact completed buckets with indexed seeks and a durable watermark.

        Decisions and progress commit together. A later pass resumes after the
        completed buckets, including after restart. Late frames and removal of
        incident protection invalidate progress. Grace-straddling buckets wait
        until their entire time window is eligible, preserving a single first
        frame and the true extrema. Automatic callers bound work per cycle.
        """
        if not math.isfinite(bucket_seconds) or bucket_seconds <= 0:
            raise ValueError("Compaction bucket must be a positive finite number.")
        if not math.isfinite(grace_seconds) or grace_seconds < 0:
            raise ValueError("Compaction grace must be a nonnegative finite number.")
        cutoff = now_ns - int(grace_seconds * 1e9)
        policy = json.dumps([float(bucket_seconds), sorted(peak_keys)])
        summary = {"scanned": 0, "kept": 0, "deleted": 0, "buckets": 0, "pinned_skipped": 0}
        deadline = None if time_budget is None else time.monotonic() + time_budget
        sessions = self.conn.execute("SELECT id FROM sessions ORDER BY start_utc_ns,id").fetchall()
        for (sid,) in sessions:
            progress = self.conn.execute(
                "SELECT next_t FROM compaction_progress WHERE policy=? AND session_id=?",
                (policy, sid)).fetchone()
            cursor = math.floor(progress[0] / bucket_seconds) * bucket_seconds if progress else -1e300
            while True:
                if ((max_buckets is not None and summary["buckets"] >= max_buckets)
                        or (deadline is not None and time.monotonic() >= deadline)):
                    return summary
                first = self.conn.execute(
                    "SELECT t FROM frames WHERE session_id=? AND t>=? ORDER BY t LIMIT 1",
                    (sid, cursor)).fetchone()
                if first is None:
                    break
                start = math.floor(first[0] / bucket_seconds) * bucket_seconds
                end = start + bucket_seconds
                rows = self.conn.execute(
                    f"SELECT id,t,retention_ns,{PIN} AS pinned FROM frames "
                    "WHERE session_id=? AND t>=? AND t<? ORDER BY t,id",
                    (sid, start, end)).fetchall()
                # Retention is monotonic-derived; do not substitute wall-clock UTC.
                if any(r["retention_ns"] + int((end-r["t"]) * 1e9) > cutoff for r in rows):
                    break
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
