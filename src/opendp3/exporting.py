"""Shareable, allowlisted evidence exports and a separate private raw archive."""
import csv
from datetime import datetime, timezone
import hashlib
import html
import json
from pathlib import Path
import zipfile

from .fields import fields_for_session, is_jackery
from .events import SAFE_EVENT_KINDS, contains_gaps
from .queries import plot_arrays
from .storage import read_db

QUALIFICATION = (
    "Times are host receipt times, not verified device measurement times. "
    "Ordering and spacing do not establish device-side causation. Missing fields are unknown. "
    "A disconnect is not a confirmed reboot; a raw error code is not an Error 036 mapping. "
    "This recording alone cannot establish faulty firmware."
)

def utc_text(ns):
    return datetime.fromtimestamp(ns/1e9, timezone.utc).isoformat(timespec="milliseconds")

def export_evidence(database: Path, destination: Path, *, sid=None, incident_id=None, include_raw=False):
    database, destination = Path(database), Path(destination)
    # Never overwrite an earlier evidence bundle.
    destination.mkdir(parents=True, exist_ok=False)
    with read_db(database) as db:
        if sid is None:
            row = db.execute("SELECT id FROM sessions ORDER BY start_utc_ns DESC LIMIT 1").fetchone()
            if not row:
                raise ValueError("Recording is empty.")
            sid = row[0]
        session = dict(db.execute("SELECT * FROM sessions WHERE id=?", (sid,)).fetchone())
        field_map = {field.key: field for field in fields_for_session(session)}
        bounds = db.execute("SELECT MIN(t),MAX(t) FROM frames WHERE session_id=?", (sid,)).fetchone()
        if bounds[0] is None:
            raise ValueError("No telemetry available to export.")
        start, end = bounds
        incident = None
        if incident_id is not None:
            row = db.execute("SELECT * FROM incidents WHERE id=? AND session_id=?", (incident_id,sid)).fetchone()
            if not row:
                raise ValueError("Incident does not belong to the selected session.")
            incident = dict(row)
            start, end = incident["start_t"], incident["end_t"]
        # Source snapshot held for the duration of all database reads.
        with (destination/"telemetry.csv").open("w", newline="", encoding="utf-8") as file:
            writer = csv.writer(file)
            writer.writerow(["receipt_utc","elapsed_s","segment","field","value","unit","quality"])
            points = {}
            query = """SELECT f.utc_ns,f.t,f.segment,m.key,m.value,m.quality FROM frames f
             JOIN measurements m ON m.frame_id=f.id WHERE f.session_id=? AND f.t BETWEEN ? AND ?
             ORDER BY f.id"""
            for row in db.execute(query, (sid,start,end)):
                key = row["key"]
                if key not in field_map:
                    continue
                writer.writerow([utc_text(row["utc_ns"]),row["t"],row["segment"],key,row["value"],
                                 field_map[key].unit,row["quality"]])
                points.setdefault(key,[]).append(dict(row))
        events = [dict(r) for r in db.execute(
            "SELECT t,utc_ns,kind FROM events WHERE session_id=? AND t BETWEEN ? AND ? ORDER BY t,id",
            (sid,start,end))]
        with (destination/"events.csv").open("w",newline="",encoding="utf-8") as file:
            writer = csv.writer(file)
            writer.writerow(["receipt_utc","elapsed_s","kind"])
            for event in events:
                # Do not export free-text notes, exception messages, or arbitrary event details.
                if event['kind'] not in SAFE_EVENT_KINDS:
                    event['kind'] = 'unrecognized_event'
                writer.writerow([utc_text(event["utc_ns"]),event["t"],event["kind"]])
        counts = dict(db.execute("""SELECT status,COUNT(*) FROM frames
          WHERE session_id=? AND t BETWEEN ? AND ? GROUP BY status""", (sid,start,end)).fetchall())
        raw_file = None
        if include_raw:
            raw_file = destination.parent / (destination.name + "-PRIVATE-raw.zip")
            with zipfile.ZipFile(raw_file, "x", compression=zipfile.ZIP_DEFLATED) as archive:
                manifest = {"warning":"PRIVATE: opaque payloads may contain identifiers. Do not share without review.",
                            "decoder":session["decoder"],"frames":[],"synthetic":bool(session["synthetic"])}
                for row in db.execute("SELECT * FROM frames WHERE session_id=? AND t BETWEEN ? AND ? ORDER BY id",
                                      (sid,start,end)):
                    name = f"frames/{row['id']:010d}.bin"
                    raw = bytes(row["raw"])
                    archive.writestr(name,raw)
                    manifest["frames"].append({"file":name,"receipt_utc":utc_text(row["utc_ns"]),
                        "monotonic_ns":row["mono_ns"],"elapsed_s":row["t"],"segment":row["segment"],
                        "seq":row["seq"],"source_timestamp":row["source_timestamp"],
                        "status":row["status"],"sha256":hashlib.sha256(raw).hexdigest()})
                archive.writestr("manifest.json",json.dumps(manifest,indent=2))
        synthetic = bool(session["synthetic"])
        complete = incident is None or (not incident["pre_missing"] and bounds[1]>=end)
        gaps = contains_gaps(events, start, end)
        metadata = {"synthetic":synthetic,"decoder":session["decoder"],"elapsed_start_s":start,
                    "elapsed_end_s":end,"incident_window_complete":complete,"contains_gaps":gaps,
                    "frame_status_counts":counts,"qualification":QUALIFICATION,
                    "omitted":"Account/device identifiers, free-text notes, firmware text, raw payloads and unknown fields."}
        (destination/"summary.json").write_text(json.dumps(metadata,indent=2),encoding="utf-8")
    # Static scientific plots for portable, offline reports. matplotlib is in
    # the optional `gui` extra, and the rest of this module runs without it, so
    # a headless install gets here before anything says which package is
    # missing. Omitting the plots instead would quietly change what an evidence
    # export contains, so this fails with the name of the extra to install.
    try:
        from matplotlib.figure import Figure
        from matplotlib.backends.backend_agg import FigureCanvasAgg
    except ModuleNotFoundError as exc:
        raise ValueError(
            f"Evidence charts need the '{exc.name}' package, which is not "
            "installed. Install the chart extra: pip install 'opendp3[charts]'"
        ) from None
    groups = [("temperature","Temperature (°C)"),("power","Power (W)"),("soc","SOC / SOH (%)"),("state","Raw state / error")]
    if is_jackery(session):
        groups += [("voltage", "Voltage (V)"), ("frequency", "Frequency (Hz)"), ("duration", "Estimated time (h)")]
    figure = Figure(figsize=(12, 2.25 * len(groups)), layout="constrained", facecolor="#f7f9fc")
    FigureCanvasAgg(figure)
    axes = figure.subplots(len(groups),1,sharex=True)
    for axis,(group,label) in zip(axes,groups):
        for key,rows in points.items():
            if field_map[key].group != group:
                continue
            x,y = plot_arrays(rows)
            axis.plot(x,y,lw=1,label=field_map[key].label)
        axis.set_ylabel(label)
        axis.grid(alpha=.2)
        if axis.lines:
            axis.legend(fontsize=6,loc="upper left",ncols=2)
        else:
            axis.text(.5,.5,"Not observed",ha="center",transform=axis.transAxes)
        for event in events:
            if event["kind"] in {"suspect_telemetry","device_error","manual"}:
                axis.axvline(event["t"],color="#cb6122",lw=.6,alpha=.45)
    axes[-1].set_xlabel("Elapsed seconds from recording start (host monotonic receipt time)")
    figure.suptitle("SYNTHETIC DEMONSTRATION — NOT DEVICE EVIDENCE" if synthetic else "OpenPowerstation telemetry evidence")
    figure.savefig(destination/"charts.png",dpi=150)
    rows_html = "".join(f"<tr><td>{html.escape(f.label)}</td><td>{'Observed' if f.key in points else 'Not observed'}</td></tr>"
                        for f in field_map.values())
    mapping_note = ("Jackery voltage, frequency, output states and estimated times use the known local property mapping."
                    if is_jackery(session) else
                    "Pack and PV voltage/current are not mapped: configured charging limits are not measurements. "
                    "Extra-battery temperatures use an unverified community mapping.")
    report = f"""<!doctype html><html lang="en"><meta charset="utf-8">
<title>OpenPowerstation incident report</title><style>
body{{font:16px system-ui;max-width:1100px;margin:40px auto;padding:0 24px;color:#183142}}
h1{{margin-bottom:8px}} .notice{{background:#fff1da;padding:16px;border-left:4px solid #c47712}}
img{{width:100%}}td{{padding:6px 20px;border-bottom:1px solid #ddd}}small{{color:#526676}}
</style><h1>OpenPowerstation · {'Synthetic demonstration' if synthetic else 'Telemetry report'}</h1>
<p class="notice">{'SYNTHETIC DATA — not a recording from your battery. ' if synthetic else ''}{html.escape(QUALIFICATION)}</p>
<p>Requested window: {start:.3f}–{end:.3f} elapsed seconds.
Pre/post coverage complete: {complete}. Communication or capture gaps: {gaps}.</p>
<p>Decoder: {html.escape(session['decoder'])}. Recorded at native receipt cadence.</p>
<img src="charts.png" alt="Aligned telemetry charts with gaps and incident markers">
<h2>Coverage</h2><table>{rows_html}</table>
<p>{mapping_note}</p>
<p>Free-text notes, arbitrary device fields and identifying metadata are excluded.
CSV files contain only observed, mapped numeric fields and event categories.
Repeated identical packets are marked unverified and do not imply fresh device measurements.</p>
</html>"""
    (destination/"report.html").write_text(report,encoding="utf-8")
    return destination/"report.html", raw_file
