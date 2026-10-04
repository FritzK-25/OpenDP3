"""Read-only view models shared by desktop, CLI, and reports."""
from bisect import bisect_right
import json
from pathlib import Path
import sqlite3
import statistics
import time

from .decoder import FIELDS
from .fields import fields_for_session, unavailable_for_session
from .events import contains_gaps
from .storage import frame_tally, read_db

# Receipts of one field further apart than this, inside one connection
# segment, are a gap: what happened between them is unknown, so a chart breaks
# its line there and the inspector does not reach across.
GAP_SECONDS = 30.0

# The longest a running collector goes without writing a frame or an event.
# The Explorer's search reports a failed attach at most once per
# cli.ATTACH_REPORT_SECONDS (600 s), and an attempt -- a minute's search, the
# radio lease's queue and hold -- takes a few minutes at most; the DP3's
# reconnect backoff tops out at a minute, its frame lease at 75 s. A collector
# polling slower than this is allowed its own cadence on top (writer_live).
# Only a writer opening the file finalizes a session that a crash or a power
# loss left 'recording', so a viewer goes by this instead: quieter than this,
# the process that wrote the session is gone.
WRITER_QUIET_LIMIT = 900.0

def sessions(path):
    with read_db(Path(path)) as db:
        return [dict(r) for r in db.execute("SELECT * FROM sessions ORDER BY start_utc_ns DESC")]

def snapshot(path, sid=None, *, end_t=None, span=None):
    with read_db(Path(path)) as db:
        if not sid:
            row = db.execute("SELECT id FROM sessions ORDER BY start_utc_ns DESC LIMIT 1").fetchone()
            if not row:
                return {}
            sid = row[0]
        session = dict(db.execute("SELECT * FROM sessions WHERE id=?", (sid,)).fetchone())
        count, last_id = frame_tally(db, sid)
        # Read in the same transaction as everything below, so a probe that
        # finds a different one knows this snapshot is out of date.
        changes = revision(db, sid)
        # One aggregate each, so each is read from an end of the index.
        earliest = float(db.execute("SELECT MIN(t) FROM frames WHERE session_id=?", (sid,)).fetchone()[0] or 0)
        latest = float(db.execute("SELECT MAX(t) FROM frames WHERE session_id=?", (sid,)).fetchone()[0] or 0)
        right = latest if end_t is None else min(float(end_t), latest)
        left = earliest if span is None else max(earliest, right-span)
        # Fetch only the visible window; no interpolation or forward filling.
        rows = db.execute("""SELECT f.id,f.t,f.utc_ns,f.segment,f.status,m.key,m.value,m.quality
            FROM frames f JOIN measurements m ON f.id=m.frame_id
            WHERE f.session_id=? AND f.t BETWEEN ? AND ? ORDER BY f.id""", (sid, left, right)).fetchall()
        series = {}
        for r in rows:
            series.setdefault(r["key"], []).append(dict(r))
        coverage = []
        for field in fields_for_session(session):
            points = [r for r in series.get(field.key, []) if r["quality"] != "repeated_unverified"]
            intervals = [b["t"]-a["t"] for a,b in zip(points,points[1:])
                         if a["segment"] == b["segment"] and b["t"]>a["t"]]
            # These rows already arrived in receipt/id order. Reuse the latest
            # comparable observation instead of rescanning the session per field.
            # For a field absent from this window, start with its key index so
            # missing fields don't each scan every frame in a long recording.
            latest_row = points[-1] if points else db.execute("""SELECT m.value,m.quality,f.t
              FROM measurements m CROSS JOIN frames f ON f.id=m.frame_id
              WHERE m.key=? AND f.session_id=? AND f.t<=?
              AND m.quality!='repeated_unverified' ORDER BY m.frame_id DESC LIMIT 1""",
                                    (field.key,sid,right)).fetchone()
            coverage.append({"key": field.key, "label": field.label, "unit": field.unit,
                             "status": latest_row["quality"] if latest_row else "not observed",
                             "value": latest_row["value"] if latest_row else None,
                             "last_t": latest_row["t"] if latest_row else None,
                             "median_interval": statistics.median(intervals) if intervals else None})
        for name in unavailable_for_session(session):
            coverage.append({"key": "", "label": name, "unit": "", "status": "not mapped; no verified measured field",
                             "value": None, "last_t": None, "median_interval": None})
        events = [dict(r) for r in db.execute("SELECT * FROM events WHERE session_id=? AND t<=? ORDER BY t,id", (sid,right))]
        incidents = [dict(r) for r in db.execute("SELECT * FROM incidents WHERE session_id=? ORDER BY start_t", (sid,))]
        for i in incidents:
            i["reasons"] = json.loads(i["reasons"])
            i["complete"] = not i["pre_missing"] and latest >= i["end_t"]
            i["gaps"] = contains_gaps(events, i["start_t"], i["end_t"])
        counts = dict(db.execute("SELECT status,COUNT(*) FROM frames WHERE session_id=? GROUP BY status", (sid,)).fetchall())
        # The newest frame received: when the session's writer last stored
        # one, and the anchor for its elapsed time now (elapsed_now).
        newest = None if last_id is None else db.execute(
            "SELECT id,t,utc_ns,segment,status,decoded FROM frames WHERE id=?", (last_id,)).fetchone()
        # The writer's own cadence: the spacing of its newest two frames of
        # one connection segment, wide for a collector that polls slowly.
        before = None if newest is None else db.execute(
            "SELECT t,segment FROM frames WHERE session_id=? AND t<=? AND id<>? ORDER BY t DESC LIMIT 1",
            (sid, newest["t"], newest["id"])).fetchone()
        cadence = (newest["t"] - before["t"]
                   if before is not None and before["segment"] == newest["segment"] else None)
        # The newest frame received by the right edge: the session's newest
        # unless the view has moved back past it. Then the highest id among the
        # index entries up to the edge, still without sorting the frames.
        raw = newest
        if raw is not None and raw["t"] > right:
            raw = db.execute("""SELECT id,t,utc_ns,status,decoded FROM frames
              WHERE id=(SELECT MAX(id) FROM frames WHERE session_id=? AND t<=?)""", (sid,right)).fetchone()
        # Whatever the writer stored last -- the session itself, a frame or an
        # event -- says when it was last seen alive (writer_live).
        event = db.execute("SELECT utc_ns FROM events WHERE session_id=? ORDER BY t DESC,id DESC LIMIT 1",
                           (sid,)).fetchone()
        activity = max([session["start_utc_ns"]] + [row["utc_ns"] for row in (newest, event) if row])
        return {"session":session,"series":series,"coverage":coverage,"events":events,"incidents":incidents,
                "count":count,"counts":counts,"earliest":earliest,
                "latest":latest,"left":left,"right":right,
                "raw":dict(raw) if raw else None,
                "newest":{"t":newest["t"],"utc_ns":newest["utc_ns"]} if newest else None,
                "activity_utc_ns":activity,"cadence":cadence,"revision":changes,
                "thinning":thinning(db, sid, incidents)}

def revision(db, sid):
    """What a writer can change about session ``sid``, without reading its frames.

    The session row (its status and end), the frame tally (which moves with
    every frame written and every one thinned or pruned), the events, the
    incident windows and the thinning watermark. While all of them stay the
    same, so does every snapshot of the session: the desktop asks for this
    instead of querying and drawing a saved session again. A few rows and the
    session's event and incident index entries are read, never a frame or a
    measurement, however long the session.
    """
    session = db.execute("SELECT status,end_t FROM sessions WHERE id=?", (sid,)).fetchone()
    events = db.execute("SELECT COUNT(*),MAX(id) FROM events WHERE session_id=?", (sid,)).fetchone()
    incidents = db.execute("""SELECT COUNT(*),MAX(id),TOTAL(start_t),TOTAL(end_t),TOTAL(pre_missing)
      FROM incidents WHERE session_id=?""", (sid,)).fetchone()
    try:
        watermark = db.execute("SELECT MAX(next_t) FROM compaction_progress WHERE session_id=?",
                               (sid,)).fetchone()[0]
    except sqlite3.OperationalError as exc:
        # A database no collector of a thinning release has opened.
        if "no such table" not in str(exc):
            raise
        watermark = None
    return (tuple(session) if session else None, frame_tally(db, sid), tuple(events),
            tuple(incidents), watermark)

def session_revision(path, sid):
    """revision() of session ``sid`` in the recording at ``path``."""
    with read_db(Path(path)) as db:
        return revision(db, sid)

def thinning(db, sid, incidents):
    """Where the downsampler has thinned this session's history, or None.

    The recorded fact, not a guess: compaction_progress holds each policy's
    bucket and the end of the last bucket it finished. Before ``before_t``
    ordinary history keeps the first frame of each bucket and its extremes,
    so neighbouring frames there are up to a bucket apart with nothing
    missing. Incident windows (``pinned``, sorted and disjoint) are never
    thinned. A late frame or a released incident moves the watermark back, and
    the history behind it reads as unthinned until the next pass walks it.
    """
    try:
        rows = db.execute("SELECT policy,next_t FROM compaction_progress WHERE session_id=?",
                          (sid,)).fetchall()
    except sqlite3.OperationalError as exc:
        # A database no collector of a thinning release has opened.
        if "no such table" not in str(exc):
            raise
        rows = []
    policies = []
    for row in rows:
        try:
            bucket, peak_keys = json.loads(row["policy"])
            policies.append((float(bucket), row["next_t"], tuple(peak_keys)))
        except (ValueError, TypeError):
            continue
    if not policies:
        return None
    return {"before_t": max(next_t for _, next_t, _ in policies),
            "bucket_seconds": max(bucket for bucket, _, _ in policies),
            "peak_keys": sorted({key for _, _, keys in policies for key in keys}),
            "pinned": sorted((i["start_t"], i["end_t"]) for i in incidents)}

def gap(previous, point, thinning=None):
    """Whether ``point`` must not be joined to ``previous`` on a chart.

    A new connection segment always breaks, and so do receipts more than
    GAP_SECONDS apart. In thinned history (see thinning()) the downsampler
    kept one frame per bucket on purpose, so there they may be a bucket more
    apart -- unless both lie inside one incident window, which is kept at
    native cadence. A real outage in thinned history is still a segment of
    its own: the Explorer's capture_gap and the DP3's silence split one.
    """
    if point["segment"] != previous["segment"]:
        return True
    allowed = GAP_SECONDS
    if thinning and previous["t"] < thinning["before_t"]:
        pinned = thinning["pinned"]
        window = bisect_right(pinned, previous["t"], key=lambda w: w[0]) - 1
        if window < 0 or point["t"] > pinned[window][1]:
            allowed += thinning["bucket_seconds"]
    return point["t"] - previous["t"] > allowed

def plot_arrays(points, thinning=None):
    """NaN breaks separate connection segments, corrupt gaps and uncertain repeats."""
    x, y = [], []
    previous = None
    for p in points:
        if previous and gap(previous, p, thinning):
            x.append(p["t"]); y.append(float("nan"))
        x.append(p["t"])
        y.append(float("nan") if p["quality"] == "repeated_unverified" else p["value"])
        previous = p
    return x, y

def writer_live(snap, now_ns=None):
    """Whether another process is still writing the session in ``snap``.

    The 'recording' status alone is not that: a crash or a power loss leaves
    it, and only the next writer to open the file finalizes the session as
    interrupted. A live writer also stores something at least every
    WRITER_QUIET_LIMIT seconds, or one poll interval more for a collector
    polling slower than that (``jackery-record --interval``), which its own
    newest frames space out.
    """
    session = snap.get("session") or {}
    if session.get("status") != "recording":
        return False
    now_ns = time.time_ns() if now_ns is None else now_ns
    limit = WRITER_QUIET_LIMIT + max(0.0, snap.get("cadence") or 0.0)
    return now_ns - snap["activity_utc_ns"] <= limit * 1e9

def elapsed_now(snap, now_ns=None):
    """The session's elapsed time now, for a session another process writes.

    Elapsed times come from the writer's monotonic clock, which no other
    process can read, and which restarts with every boot. The newest frame
    pairs one with a wall-clock receipt time, so now is its elapsed time plus
    the wall-clock time since it arrived; before any frame, the session start.
    """
    now_ns = time.time_ns() if now_ns is None else now_ns
    anchor = snap.get("newest") or {"t": 0.0, "utc_ns": snap["session"]["start_utc_ns"]}
    return anchor["t"] + (now_ns - anchor["utc_ns"]) / 1e9

def latest(path, sid=None, *, keys=None):
    """Newest observation per field plus session liveness, for the MQTT bridge.

    Receipt order (the highest frame ``id``) is canonical for session liveness.
    Replay/import callers may supply wall-clock or elapsed timestamps out of order,
    so ``last_id``, ``last_utc_ns`` and ``last_t`` must always come from one row.
    Incident completion is different: it tracks the furthest elapsed session time
    reached, so replaying an older observation cannot reopen a completed window.
    Deliberately narrower than ``snapshot``: no series, no plot arrays, no window.
    Every read is a seek, so a publish costs the same however long the session
    has run: the frame count and newest frame come from the session_frames
    tally, the furthest elapsed time from the end of ``frame_session_time``,
    and each field from one reverse seek on ``measurement_key`` starting at the
    newest frame.
    """
    with read_db(Path(path)) as db:
        row = (db.execute("SELECT * FROM sessions WHERE id=?", (sid,)).fetchone() if sid else
               db.execute("SELECT * FROM sessions ORDER BY start_utc_ns DESC LIMIT 1").fetchone())
        if not row:
            return {}
        session = dict(row)
        sid = session["id"]
        count, last_id = frame_tally(db, sid)
        last = None if last_id is None else db.execute(
            "SELECT id,utc_ns,t FROM frames WHERE id=?", (last_id,)).fetchone()
        # MAX(t) alone, so SQLite reads it from the end of the index; beside
        # another aggregate it would read every entry of the session.
        progress_t = db.execute("SELECT MAX(t) FROM frames WHERE session_id=?", (sid,)).fetchone()[0]
        if last is None:
            last_id = last_utc_ns = last_t = None
        else:
            last_id, last_utc_ns, last_t = last["id"], last["utc_ns"], last["t"]
        values = {}
        field_keys = [field.key for field in FIELDS] if keys is None else list(keys)
        for field_key in field_keys if last_id is not None else []:
            # Uncertain repeats are not observations; exclude them exactly as
            # snapshot() does, so the bridge never publishes an unverified retransmit.
            observation = db.execute("""SELECT m.value,m.quality,f.t,f.utc_ns FROM measurements m
              JOIN frames f ON f.id=m.frame_id
              WHERE m.key=? AND m.frame_id<=? AND f.session_id=?
              AND m.quality!='repeated_unverified' ORDER BY m.frame_id DESC LIMIT 1""",
                                     (field_key, last_id, sid)).fetchone()
            if observation:
                values[field_key] = dict(observation)
        # When the newest charted measurement arrived, as opposed to when the
        # newest frame of any kind did.
        #
        # last_utc_ns counts every recorded frame, including device messages the
        # decoder maps to no field at all. A DP3 that has stopped uploading
        # telemetry still emits those, so last_utc_ns keeps resetting while every
        # field ages out -- which reads as a healthy link serving old data and
        # hides a total telemetry outage. Reporting both separates "the stream
        # stopped" from "one property aged out while the rest kept arriving".
        #
        # Taken from the per-field rows already fetched above, so this costs no
        # extra query and inherits their exclusion of unverified retransmits.
        # It therefore means "newest observation among the fields this call
        # asked for": a caller that narrows ``keys`` narrows this too, and a
        # caller passing ``keys=[]`` gets None. Callers that act on it -- the
        # bridges -- request every field.
        arrivals = [v["utc_ns"] for v in values.values() if v.get("utc_ns") is not None]
        elapsed = [v["t"] for v in values.values() if v.get("t") is not None]
        event = db.execute("SELECT kind,detail,t,utc_ns FROM events WHERE session_id=? ORDER BY t DESC,id DESC LIMIT 1",
                           (sid,)).fetchone()
        # Incident progress follows the furthest elapsed time, not newest receipt time.
        active = db.execute("SELECT COUNT(*) FROM incidents WHERE session_id=? AND end_t>=?",
                            (sid, progress_t if progress_t is not None else 0)).fetchone()[0]
        # The collector's, not this session's: why telemetry last stopped,
        # including a start the reserve refused, which leaves no session.
        try:
            reason = db.execute("SELECT reason FROM collector_reason WHERE id=1").fetchone()
        except sqlite3.OperationalError as exc:
            # No collector of this release has opened the database yet.
            if "no such table" not in str(exc):
                raise
            reason = None
        return {"session": session, "values": values, "count": count, "last_id": last_id,
                "last_t": last_t, "last_utc_ns": last_utc_ns,
                "last_telemetry_utc_ns": max(arrivals) if arrivals else None,
                "last_telemetry_t": max(elapsed) if elapsed else None,
                "event": dict(event) if event else None,
                "incidents": active,
                "reason": reason[0] if reason else None}
