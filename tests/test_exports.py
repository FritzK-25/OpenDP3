import json
import zipfile
import pytest
from conftest import add
from opendp3.exporting import export_evidence
from opendp3.storage import read_db

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
    from opendp3.demo import make_demo
    path=make_demo(tmp_path/"demo.sqlite",seconds=395)
    with read_db(path) as db:
        iid=db.execute("SELECT id FROM incidents").fetchone()[0]
    report,_=export_evidence(path,tmp_path/"export",incident_id=iid)
    text=report.read_text("utf-8")
    assert "SYNTHETIC DATA" in text
    assert "Pre/post coverage complete: False" in text

