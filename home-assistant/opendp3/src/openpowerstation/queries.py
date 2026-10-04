"""Read-only view models shared by desktop, CLI, and reports."""
import json
from pathlib import Path
import statistics

from .decoder import FIELDS
from .fields import fields_for_session, unavailable_for_session
from .events import contains_gaps
from .storage import read_db

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
        bounds = db.execute("SELECT MIN(t),MAX(t),COUNT(*) FROM frames WHERE session_id=?", (sid,)).fetchone()
        latest = float(bounds[1] or 0)
        right = latest if end_t is None else min(float(end_t), latest)
        left = float(bounds[0] or 0) if span is None else max(float(bounds[0] or 0), right-span)
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
        raw = db.execute("SELECT id,t,status,decoded FROM frames WHERE session_id=? AND t<=? ORDER BY id DESC LIMIT 1", (sid,right)).fetchone()
        return {"session":session,"series":series,"coverage":coverage,"events":events,"incidents":incidents,
                "count":bounds[2],"counts":counts,"earliest":float(bounds[0] or 0),
                "latest":latest,"left":left,"right":right,
                "raw":dict(raw) if raw else None}

def plot_arrays(points):
    """NaN breaks separate connection segments, corrupt gaps and uncertain repeats."""
    x, y = [], []
    previous = None
    for p in points:
        if previous and (p["segment"] != previous["segment"] or p["t"]-previous["t"]>30):
            x.append(p["t"]); y.append(float("nan"))
        x.append(p["t"])
        y.append(float("nan") if p["quality"] == "repeated_unverified" else p["value"])
        previous = p
    return x, y

def latest(path, sid=None, *, keys=None):
    """Newest observation per field plus session liveness, for the MQTT bridge.

    Receipt order (the highest frame ``id``) is canonical for session liveness.
    Replay/import callers may supply wall-clock or elapsed timestamps out of order,
    so ``last_id``, ``last_utc_ns`` and ``last_t`` must always come from one row.
    Incident completion is different: it tracks the furthest elapsed session time
    reached, so replaying an older observation cannot reopen a completed window.
    Deliberately narrower than ``snapshot``: no series, no plot arrays, no window.
    One reverse seek per field against the ``measurement_key`` index, bounded to
    this session's highest frame id so a long recording does not rescan.
    """
    with read_db(Path(path)) as db:
        row = (db.execute("SELECT * FROM sessions WHERE id=?", (sid,)).fetchone() if sid else
               db.execute("SELECT * FROM sessions ORDER BY start_utc_ns DESC LIMIT 1").fetchone())
        if not row:
            return {}
        session = dict(row)
        sid = session["id"]
        last = db.execute(
            "SELECT id,utc_ns,t FROM frames WHERE session_id=? ORDER BY id DESC LIMIT 1",
            (sid,)).fetchone()
        progress_t, count = db.execute(
            "SELECT MAX(t),COUNT(*) FROM frames WHERE session_id=?", (sid,)).fetchone()
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
        return {"session": session, "values": values, "count": count, "last_t": last_t,
                "last_utc_ns": last_utc_ns,
                "last_telemetry_utc_ns": max(arrivals) if arrivals else None,
                "last_telemetry_t": max(elapsed) if elapsed else None,
                "event": dict(event) if event else None,
                "incidents": active}
