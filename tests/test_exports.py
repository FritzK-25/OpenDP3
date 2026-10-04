import json
import math
import zipfile
import pytest
from conftest import add, compacted_jackery
from openpowerstation.exporting import export_evidence
from openpowerstation.storage import read_db

# Evidence exports render charts, so this module needs the `charts` extra. It is
# deliberately part of `[test]` rather than of `[gui]`: these assertions are the
# privacy boundary for shareable evidence, and they must run in an ordinary test
# environment rather than only where a desktop is installed.

def test_export_allowlist_excludes_private_fields_notes_and_raw(evidence,packet,tmp_path):
    store,rec=evidence
    secret="DO_NOT_EXPORT_PRIVATE_IDENTIFIER"
    add(rec,packet(seq=1,bms_max_cell_temp=0,plug_in_info_4p8_1_sn=secret),0)
    rec.event("manual",secret,rec.start_utc,0)
    with store.conn:
        store.conn.execute("UPDATE sessions SET firmware=?,conditions=?",(secret,secret))
    dest=tmp_path/"share"
    report,raw=export_evidence(store.path,dest,include_raw=True)
    assert report.exists()
    assert raw.exists()
    for path in dest.iterdir():
        assert secret.encode() not in path.read_bytes()
    assert "bms_max_cell_temp,0.0" in (dest/"telemetry.csv").read_text("utf-8")
    with zipfile.ZipFile(raw) as archive:
        m=json.loads(archive.read("manifest.json"))
        assert m["frames"]
        raw_payload=archive.read(m["frames"][0]["file"])
        assert secret.encode() in raw_payload
    assert "SECRET" not in (dest/"report.html").read_text("utf-8")
    with pytest.raises(FileExistsError): export_evidence(store.path,dest)

def test_synthetic_and_incomplete_export_marked(tmp_path):
    from openpowerstation.demo import make_demo
    path=make_demo(tmp_path/"demo.sqlite",seconds=395)
    with read_db(path) as db:
        iid=db.execute("SELECT id FROM incidents").fetchone()[0]
    report,_=export_evidence(path,tmp_path/"export",incident_id=iid)
    text=report.read_text("utf-8")
    assert "SYNTHETIC DATA" in text
    assert "Pre/post coverage complete: False" in text
    summary=json.loads((tmp_path/"export"/"summary.json").read_text("utf-8"))
    # The bundle says what it is, so the Exported files page need not guess.
    assert (summary["device"],summary["scope"])==("dp3","incident")


def charted(monkeypatch):
    """The axes of every evidence chart saved while the returned list is held."""
    from matplotlib.figure import Figure
    figures = []
    save = Figure.savefig

    def keep(figure, *args, **kwargs):
        figures.append(figure)
        return save(figure, *args, **kwargs)

    monkeypatch.setattr(Figure, "savefig", keep)
    return figures


def drawn(axis):
    """Whether anything plotted on ``axis`` is visible: a line segment or a marker."""
    for line in axis.get_lines():
        y = list(line.get_ydata())
        if line.get_marker() not in ("None", "", None) and any(map(math.isfinite, y)):
            return True
        if any(math.isfinite(a) and math.isfinite(b) for a, b in zip(y, y[1:])):
            return True
    return False


def test_compacted_jackery_export_draws_its_history_and_says_it_was_thinned(tmp_path, monkeypatch):
    """Thinned to a frame a minute, every point used to sit between two line breaks."""
    figures = charted(monkeypatch)
    path = compacted_jackery(tmp_path / "jackery.sqlite")
    report, _ = export_evidence(path, tmp_path / "export")
    axes = {axis.get_ylabel(): axis for axis in figures[0].axes}
    for label in ("Power (W)", "SOC / SOH (%)", "Voltage (V)", "Frequency (Hz)", "Estimated time (h)"):
        assert axes[label].get_lines(), label
        assert drawn(axes[label]), f"{label} is blank"
    summary = json.loads((tmp_path / "export" / "summary.json").read_text("utf-8"))
    assert summary["device"] == "jackery" and summary["scope"] == "session"
    assert summary["thinned_bucket_s"] == 60
    assert summary["thinned_before_elapsed_s"] >= summary["elapsed_end_s"]
    text = report.read_text("utf-8")
    assert "Recorded at native receipt cadence." not in text
    assert "thinned to the first frame of each 60-second bucket" in text


def test_an_unthinned_export_keeps_native_cadence_and_marks_lone_samples(evidence, packet, tmp_path, monkeypatch):
    figures = charted(monkeypatch)
    store, rec = evidence
    add(rec, packet(seq=1, bms_max_cell_temp=24), 0)
    add(rec, packet(seq=2, bms_max_cell_temp=25), 100)   # 100 s apart: no line joins them
    report, _ = export_evidence(store.path, tmp_path / "export")
    summary = json.loads((tmp_path / "export" / "summary.json").read_text("utf-8"))
    assert summary["thinned_before_elapsed_s"] is None and summary["thinned_bucket_s"] is None
    assert "Recorded at native receipt cadence." in report.read_text("utf-8")
    temperature = next(axis for axis in figures[0].axes if axis.get_ylabel() == "Temperature (°C)")
    assert drawn(temperature), "two lone samples drew nothing"

