import json
import sqlite3
import pytest
from conftest import add
from opendp3.decoder import decode_raw
from opendp3.protocol import auth_packet
from opendp3.queries import snapshot,plot_arrays
from opendp3.storage import Store,StorageError,read_db
from opendp3.validation import verify_recording
from opendp3.vendor.packet import Packet

def test_presence_zero_and_partial_packets(packet):
    decoded = decode_raw(packet(bms_max_cell_temp=0,cms_batt_soc=0))
    assert decoded.measurements["bms_max_cell_temp"] == 0
    assert "bms_min_cell_temp" not in decoded.fields
    assert "bms_max_cell_temp" not in decode_raw(packet(seq=2,pow_get_ac_in=400)).measurements

def test_only_decoded_measurements_renew_the_session_lease(evidence,packet):
    """The lease must track evidence, not activity.

    A retransmit or an undecodable frame keeps the transport busy while telling
    us nothing new about the battery, so neither may hold a stalled session open.
    """
    _,rec=evidence
    raw=packet(seq=1,bms_max_cell_temp=23)
    assert rec.ingest(raw) is True
    assert rec.ingest(raw) is False
    assert rec.ingest(b"not a protocol frame") is False

def test_unknown_message_and_field_retained(evidence,packet):
    store,rec=evidence
    from google.protobuf.internal.encoder import _VarintBytes
    from opendp3.protocol import parse_packet
    p = parse_packet(packet(bms_max_cell_temp=23))
    p.payload += _VarintBytes((8000<<3)|0)+_VarintBytes(777)
    raw=p.to_bytes(); add(rec,raw,0)
    unknown=Packet(2,0x21,0xDD,0x45,b"future telemetry").to_bytes()
    add(rec,unknown,1)
    rows=store.conn.execute("SELECT raw,status FROM frames ORDER BY id").fetchall()
    assert bytes(rows[0]["raw"]) == raw
    assert rows[1]["status"] == "unknown_message"
    assert verify_recording(store.path)["mismatches"] == 0

def test_jump_error_incidents_and_reconnect(evidence,packet):
    store,rec=evidence
    add(rec,packet(seq=1,bms_max_cell_temp=23,errcode=0),0)
    add(rec,packet(seq=2,bms_max_cell_temp=0,errcode=999),1)
    kinds=[r[0] for r in store.conn.execute("SELECT kind FROM events")]
    assert kinds.count("suspect_telemetry")==1
    assert kinds.count("device_error")==1
    assert store.conn.execute("SELECT COUNT(*) FROM incidents").fetchone()[0]==1
    rec.event("disconnected","Not a reboot",rec.start_utc+2*10**9,2*10**9)
    add(rec,packet(seq=3,bms_max_cell_temp=23),3)
    assert store.conn.execute("SELECT COUNT(*) FROM events WHERE kind='suspect_telemetry'").fetchone()[0]==1
    assert snapshot(store.path)["incidents"][0]["gaps"]
    assert not snapshot(store.path)["incidents"][0]["complete"]

def test_duplicates_do_not_refresh_age_or_trigger(evidence,packet):
    store,rec=evidence
    raw=packet(seq=1,bms_max_cell_temp=23)
    add(rec,raw,0); add(rec,raw,1)
    assert store.conn.execute("SELECT status FROM frames ORDER BY id DESC").fetchone()[0]=="repeated_unverified"
    c=next(c for c in snapshot(store.path)["coverage"] if c["key"]=="bms_max_cell_temp")
    assert c["last_t"]==0
    assert store.conn.execute("SELECT COUNT(*) FROM incidents").fetchone()[0]==0

def test_clock_jump_uses_monotonic_for_anomaly(evidence,packet):
    store,rec=evidence
    add(rec,packet(seq=1,bms_max_cell_temp=23),0)
    add(rec,packet(seq=2,bms_max_cell_temp=0),1,wall_offset=-3600)
    assert store.conn.execute("SELECT COUNT(*) FROM events WHERE kind='clock_change'").fetchone()[0]==1
    assert store.conn.execute("SELECT COUNT(*) FROM events WHERE kind='suspect_telemetry'").fetchone()[0]==1
    rows=store.conn.execute("SELECT retention_ns FROM frames ORDER BY id").fetchall()
    assert rows[1][0]-rows[0][0]==10**9

def test_zeros_not_categorical_anomaly_and_gap_window(evidence,packet):
    store,rec=evidence
    add(rec,packet(seq=1,bms_max_cell_temp=0),0)
    add(rec,packet(seq=2,bms_max_cell_temp=1),1)
    add(rec,packet(seq=3,bms_max_cell_temp=23),100)
    assert store.conn.execute("SELECT COUNT(*) FROM incidents").fetchone()[0]==0

def test_nonfinite_is_preserved_not_normalized(evidence,packet):
    store,rec=evidence
    add(rec,packet(bms_batt_soc=float("nan")),0)
    row=store.conn.execute("SELECT decoded FROM frames").fetchone()[0]
    assert json.loads(row)["bms_batt_soc"]=={"nonfinite":"nan"}
    assert store.conn.execute("SELECT COUNT(*) FROM measurements").fetchone()[0]==0

def test_raw_committed_even_if_decoder_crashes(evidence,packet,monkeypatch):
    store,rec=evidence
    monkeypatch.setattr("opendp3.recorder.decode",lambda _:(_ for _ in ()).throw(RuntimeError("decoder fault")))
    raw=packet(bms_max_cell_temp=23)
    with pytest.raises(RuntimeError): add(rec,raw,0)
    with read_db(store.path) as reader:
        row=reader.execute("SELECT raw,status FROM frames").fetchone()
        assert bytes(row["raw"])==raw
        assert row["status"]=="pending"

def test_auth_never_touches_database_or_wal(evidence):
    store,rec=evidence
    secret=b"SECRET_AUTH_CREDENTIAL_STRING_123"
    raw=auth_packet(0x86,secret).to_bytes()
    add(rec,raw,0)
    assert store.conn.execute("SELECT COUNT(*) FROM frames").fetchone()[0]==0
    for p in store.path.parent.glob("recording.sqlite*"):
        if p.suffix != '.lock':
            assert secret not in p.read_bytes()

def test_retention_preserves_pinned_raw_and_measurements(evidence,packet):
    store,rec=evidence
    for t in (0,400,800,1200):
        add(rec,packet(seq=t+1,bms_max_cell_temp=23),t)
    store.incident(rec.sid,400,"manual",before=20,after=20)
    store.maintain(rec.start_utc+10*86400*10**9,ordinary_bytes=0)
    assert [r[0] for r in store.conn.execute("SELECT t FROM frames")]==[400]
    assert store.conn.execute("SELECT COUNT(*) FROM measurements").fetchone()[0]==1
    iid=store.conn.execute("SELECT id FROM incidents").fetchone()[0]
    store.delete_incident(iid)
    store.maintain(rec.start_utc+10*86400*10**9,ordinary_bytes=0)
    assert store.conn.execute("SELECT COUNT(*) FROM frames").fetchone()[0]==0

def test_ingest_respects_unlimited_retention(tmp_path,packet):
    from opendp3.recorder import Recorder
    with Store(tmp_path/"retain.sqlite",reserve_bytes=0) as store:
        rec=Recorder(store,utc_ns=1_000_000_000_000_000_000,mono_ns=0,retain_days=None)
        add(rec,packet(seq=1,bms_max_cell_temp=23),0)
        add(rec,packet(seq=2,bms_max_cell_temp=24),8*86400)
        assert [r[0] for r in store.conn.execute("SELECT t FROM frames ORDER BY t")]==[0,8*86400]

def test_overlapping_windows_merge_and_extend(evidence):
    store,rec=evidence
    store.incident(rec.sid,400,"first")
    store.incident(rec.sid,900,"second")
    row=store.conn.execute("SELECT * FROM incidents").fetchone()
    assert (row["start_t"],row["end_t"])==(100,1200)
    assert len(json.loads(row["reasons"]))==2

def test_exclusive_writer_and_recovery(tmp_path,packet):
    path=tmp_path/"a.sqlite"
    from opendp3.recorder import Recorder
    with Store(path,reserve_bytes=0) as s:
        r=Recorder(s,utc_ns=1_000_000_000_000_000_000,mono_ns=0)
        add(r,packet(bms_max_cell_temp=23),0)
        with pytest.raises(StorageError): Store(path,reserve_bytes=0)
    with Store(path,reserve_bytes=0) as s:
        assert s.conn.execute("SELECT status FROM sessions").fetchone()[0]=="interrupted"

def test_disk_exhaustion_explicit(evidence,packet,monkeypatch):
    store,rec=evidence
    from types import SimpleNamespace
    store.reserve_bytes=128
    monkeypatch.setattr("opendp3.storage.shutil.disk_usage",lambda _:SimpleNamespace(free=0))
    with pytest.raises(StorageError): add(rec,packet(bms_max_cell_temp=22),0)
    assert store.conn.execute("SELECT COUNT(*) FROM frames").fetchone()[0]==0

def test_plot_segments_break():
    x,y=plot_arrays([{"t":0,"segment":1,"value":23,"quality":"observed"},
                    {"t":1,"segment":2,"value":0,"quality":"observed"}])
    import math
    assert math.isnan(y[1])

@pytest.mark.parametrize('flag',[None,0,1])
def test_extra_battery_slots_require_present_connection_flag(packet,flag):
    values={'plug_in_info_4p8_1_resv':{'resv_info':[0]*15}}
    if flag is not None:
        values['plug_in_info_4p8_1_in_flag']=flag
    decoded=decode_raw(packet(**values))
    assert ('extra1_temperature' in decoded.measurements) == bool(flag)
    if flag:
        assert decoded.measurements['extra1_temperature']==0
        assert decoded.quality['extra1_temperature']=='unverified'
