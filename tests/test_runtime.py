import asyncio
import json
from types import SimpleNamespace
import time

import portalocker
from conftest import add
from opendp3.config import Config,save_config,load_config,resolve_user_id
from opendp3.protocol import Identity,AuthenticationError
from opendp3.runtime import Service
from opendp3.storage import read_db

def cfg():
    return Config("AA:BB:CC:DD:EE:FF","MR51123456789012","123456")

def wait_for(predicate,timeout=4):
    deadline=time.monotonic()+timeout
    while not predicate():
        if time.monotonic()>deadline: raise AssertionError("Timed out")
        time.sleep(.02)

def test_release_resume_without_implicit_reconnect(tmp_path,monkeypatch,packet):
    starts=[]
    async def scan(*_):
        return [(Identity(cfg().address,cfg().serial,0,0x13),object())]
    class Session:
        def __init__(self,identity,device,uid,on_frame,on_event,allow_control=False):
            self.on_frame,self.on_event=on_frame,on_event
            self.allow_control=allow_control
        async def run(self):
            starts.append(1)
            self.on_event("connected","local session",time.time_ns(),time.monotonic_ns())
            self.on_frame(packet(seq=len(starts),bms_max_cell_temp=23),time.time_ns(),time.monotonic_ns())
            await asyncio.Event().wait()
    monkeypatch.setattr("opendp3.runtime.ble.scan",scan)
    monkeypatch.setattr("opendp3.runtime.ble.Session",Session)
    s=Service(cfg(),tmp_path/"live.sqlite",lock_dir=tmp_path/"locks")
    s.start()
    try:
        wait_for(lambda:s.state=="recording")
        s.command("release")
        wait_for(lambda:s.state=="released")
        count=len(starts)
        time.sleep(.4)
        assert len(starts)==count
        s.command("resume")
        wait_for(lambda:len(starts)==2)
        s.command("mark","private note")
    finally:
        s.stop(); s.join(5)
    assert not s.is_alive()
    assert not s.error
    with read_db(s.database) as db:
        assert db.execute("SELECT COUNT(*) FROM frames").fetchone()[0]==2

def test_authentication_failure_stops_no_retries(tmp_path,monkeypatch):
    attempts=[]
    async def scan(*_):
        attempts.append(1)
        raise AuthenticationError("Wrong user ID.")
    monkeypatch.setattr("opendp3.runtime.ble.scan",scan)
    s=Service(cfg(),tmp_path/"live.sqlite",lock_dir=tmp_path/"locks")
    s.start(); s.join(4)
    assert not s.is_alive()
    assert s.state=="error"
    assert len(attempts)==1


def test_saved_control_revocation_is_enforced_without_restarting_dp3(tmp_path, monkeypatch):
    settings = cfg()
    settings.allow_control = True
    path = tmp_path / "config.json"
    save_config(settings, path)
    sent = []

    async def scan(*_):
        return [(Identity(settings.address, settings.serial, 0, 0x13), object())]

    class Session:
        def __init__(self, identity, device, uid, on_frame, on_event, allow_control=False):
            self.on_event = on_event
            self.gate = SimpleNamespace(allow_control=allow_control)

        async def run(self):
            self.on_event("connected", "test", time.time_ns(), time.monotonic_ns())
            await asyncio.Event().wait()

        async def send_control(self, field, value):
            assert self.gate.allow_control
            sent.append((field, value))

    monkeypatch.setattr("opendp3.runtime.ble.scan", scan)
    monkeypatch.setattr("opendp3.runtime.ble.Session", Session)
    service = Service(settings, tmp_path / "test.sqlite", config_path=path, lock_dir=tmp_path / "locks")
    service.start()
    try:
        wait_for(lambda: service.state == "recording")
        service.command("control", "cfg_hv_ac_out_open=on")
        wait_for(lambda: len(sent) == 1)
        revoked = cfg()
        save_config(revoked, path)
        service.command("control", "cfg_hv_ac_out_open=off")

        def refused():
            with read_db(service.database) as db:
                return db.execute("SELECT COUNT(*) FROM events WHERE kind='control_refused'").fetchone()[0]

        wait_for(refused)
        assert sent == [("cfg_hv_ac_out_open", True)]
    finally:
        service.stop()
        service.join(5)
    assert not service.is_alive()
    assert not service.error

def test_device_lock_shared_across_database_paths(tmp_path,monkeypatch):
    import hashlib
    lock_dir=tmp_path/"locks"; lock_dir.mkdir()
    key=hashlib.sha256(cfg().serial.encode()).hexdigest()
    with portalocker.Lock(lock_dir/(key+".lock"),timeout=0):
        s=Service(cfg(),tmp_path/"different.sqlite",lock_dir=lock_dir)
        s.start(); s.join(3)
        assert "already owns" in s.error
        assert not s.database.exists()

PERSISTED={"address","serial","user_id","region","firmware","conditions",
           "temperature_jump","temperature_window","mqtt_host","mqtt_port",
           "mqtt_username","mqtt_password","mqtt_tls","mqtt_interval",
           "allow_control"}

def test_setup_persists_only_permitted_config(tmp_path):
    c=cfg(); path=tmp_path/"config.json"
    save_config(c,path)
    assert load_config(path)==c
    stored=json.loads(path.read_text())
    # An allowlist, so no future field can quietly start persisting an account secret.
    assert set(stored)==PERSISTED
    assert "token" not in path.read_text()
    # No EcoFlow account password is ever a stored field, and the broker
    # credential the bridge needs stays empty until it is deliberately set.
    assert stored["mqtt_password"]==""

def test_broker_password_is_written_to_exactly_one_field(tmp_path):
    # DPAPI-protected at rest (see config.py, docs/SECURITY.md): no field
    # holds the plaintext value, and the encrypted form lives in exactly the
    # one field meant to hold it.
    c=cfg(); c.mqtt_host="broker.local"; c.mqtt_password="BROKER_SECRET"
    path=tmp_path/"config.json"
    save_config(c,path)
    stored=json.loads(path.read_text())
    assert not [k for k,v in stored.items() if v=="BROKER_SECRET"]
    assert stored["mqtt_password"]=="" and stored["mqtt_password_dpapi"]
    assert load_config(path).mqtt_password=="BROKER_SECRET"

def test_invalid_broker_settings_never_quote_the_value(tmp_path):
    c=cfg(); c.mqtt_host="broker.local"; c.mqtt_password="BROKER_SECRET"; c.mqtt_port=0
    path=tmp_path/"config.json"
    try:
        save_config(c,path)
        raise AssertionError("accepted an invalid broker port")
    except ValueError as exc:
        assert "BROKER_SECRET" not in str(exc) and "broker.local" not in str(exc)
    assert not path.exists()

async def test_login_no_redirects_and_result_minimized(monkeypatch):
    import httpx
    requests=[]
    class Client:
        def __init__(self,**kw):
            assert kw["follow_redirects"] is False and kw["trust_env"] is False
        async def __aenter__(self): return self
        async def __aexit__(self,*_): pass
        async def post(self,url,**kw):
            requests.append(url)
            return SimpleNamespace(status_code=200,json=lambda:{"code":"0","data":{"user":{"userId":"123"},"token":"secret"}})
    monkeypatch.setattr(httpx,"AsyncClient",Client)
    assert await resolve_user_id("email@example.test","PRIVATE_PASSWORD","US")=="123"
    assert requests==["https://api.ecoflow.com/auth/login"]


def test_silent_session_is_dropped_and_reacquired(tmp_path, monkeypatch, packet):
    """A connected-but-dead DP3 link must not be waited on indefinitely.

    BlueZ can keep reporting Connected/ServicesResolved while the device stops
    uploading. ble.Session then raises after SILENCE_LIMIT silent rounds; this
    asserts the runtime treats that as a lost transport and reacquires, rather
    than leaving the zombie link in place until someone restarts the app.
    """
    from opendp3 import ble

    starts = []

    async def scan(*_):
        return [(Identity(cfg().address, cfg().serial, 0, 0x13), object())]

    class Session:
        """Never delivers telemetry, mirroring a device that has gone quiet."""

        def __init__(self, identity, device, uid, on_frame, on_event, allow_control=False):
            self.on_frame, self.on_event = on_frame, on_event

        async def run(self):
            starts.append(1)
            self.on_event("connected", "local session", time.time_ns(), time.monotonic_ns())
            # One frame per attach, so a reattach is visible in the recording.
            self.on_frame(packet(seq=len(starts), bms_max_cell_temp=23),
                          time.time_ns(), time.monotonic_ns())
            silent_rounds = 0
            while True:
                await asyncio.sleep(0)
                silent_rounds += 1
                if silent_rounds == 1:
                    self.on_event("silence", "No telemetry.", time.time_ns(), time.monotonic_ns())
                if silent_rounds >= ble.SILENCE_LIMIT:
                    raise ConnectionError("No telemetry while still connected.")

    monkeypatch.setattr("opendp3.runtime.ble.scan", scan)
    monkeypatch.setattr("opendp3.runtime.ble.Session", Session)
    s = Service(cfg(), tmp_path / "live.sqlite", lock_dir=tmp_path / "locks")
    s.start()
    try:
        # Two independent attaches prove the reconnect path ran, not just that
        # the first session ended.
        wait_for(lambda: len(starts) >= 2, timeout=8)
    finally:
        s.stop(); s.join(5)
    assert not s.error
    with read_db(s.database) as db:
        kinds = [r[0] for r in db.execute("SELECT kind FROM events ORDER BY id")]
    assert "silence" in kinds, kinds
    # The runtime records the torn-down transport and then reconnects.
    assert "disconnected" in kinds, kinds
    assert kinds.count("connected") >= 2, kinds
