import asyncio
import json
import os
import sqlite3
import threading
from types import SimpleNamespace
import time

import portalocker
import pytest
from openpowerstation.config import Config,save_config,load_config,resolve_user_id
from openpowerstation.protocol import Identity,AuthenticationError
from openpowerstation.runtime import Service
from openpowerstation.storage import read_db

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
    monkeypatch.setattr("openpowerstation.runtime.ble.scan",scan)
    monkeypatch.setattr("openpowerstation.runtime.ble.Session",Session)
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


class ShiftedTime:
    """The runtime's view of the time module, with the monotonic clock moved by hand."""

    def __init__(self):
        self.offset = 0.0

    def monotonic(self):
        return time.monotonic() + self.offset

    def __getattr__(self, name):
        return getattr(time, name)


def record_across_gap(tmp_path, monkeypatch, packet, *, stalled=0.0, slept=0.0):
    """Record one frame either side of a gap; return (events, incidents, segments).

    ``stalled`` is time the host was awake while the collector's loop did not
    run; ``slept`` is time the host spent suspended. On Linux the monotonic
    clock counts only the first, so a suspend never moves it.
    """
    clock = ShiftedTime()
    asleep = [0.0]
    monkeypatch.setattr("openpowerstation.runtime.time", clock)
    # raising=False: the collector did not always read a second clock, and this
    # test has to show what that older loop recorded, not fail to patch it.
    monkeypatch.setattr("openpowerstation.runtime.host_clocks",
                        lambda: (clock.monotonic, lambda: clock.monotonic() + asleep[0]),
                        raising=False)

    async def scan(*_):
        return [(Identity(cfg().address, cfg().serial, 0, 0x13), object())]

    class Session:
        def __init__(self, identity, device, uid, on_frame, on_event, allow_control=False):
            self.on_frame, self.on_event = on_frame, on_event

        async def run(self):
            self.on_event("connected", "local session", time.time_ns(), time.monotonic_ns())
            self.on_frame(packet(seq=1, bms_max_cell_temp=23), time.time_ns(), time.monotonic_ns())
            await asyncio.sleep(0.5)
            clock.offset += stalled
            asleep[0] += slept
            await asyncio.sleep(0.6)
            self.on_frame(packet(seq=2, bms_max_cell_temp=23), time.time_ns(), time.monotonic_ns())
            await asyncio.Event().wait()

    monkeypatch.setattr("openpowerstation.runtime.ble.scan", scan)
    monkeypatch.setattr("openpowerstation.runtime.ble.Session", Session)
    service = Service(cfg(), tmp_path / "gap.sqlite", lock_dir=tmp_path / "locks")
    service.start()

    def frames():
        # The file appears before the recorder has created its schema.
        with read_db(service.database) as db:
            try:
                return db.execute("SELECT COUNT(*) FROM frames").fetchone()[0]
            except sqlite3.OperationalError:
                return 0

    try:
        wait_for(lambda: service.database.exists() and frames() == 2)
    finally:
        service.stop()
        service.join(5)
    assert not service.is_alive()
    assert not service.error
    with read_db(service.database) as db:
        events = [(r[0], r[1]) for r in db.execute("SELECT kind,detail FROM events ORDER BY id")]
        incidents = db.execute("SELECT COUNT(*) FROM incidents").fetchone()[0]
        segments = [r[0] for r in db.execute("SELECT segment FROM frames ORDER BY id")]
    return events, incidents, segments


def test_collector_stall_while_awake_is_not_recorded_as_a_suspend(tmp_path, monkeypatch, packet):
    """A 12 s stall of an awake collector used to pin a host_suspend incident.

    On the Pi that happened about once a minute: the incident flag stayed on and
    every pinned window escaped retention. A stall loses no frames -- they queue
    and are stamped late -- so it is a diagnostic, not a gap.
    """
    events, incidents, segments = record_across_gap(tmp_path, monkeypatch, packet, stalled=12)
    kinds = [kind for kind, _ in events]
    assert "host_suspend" not in kinds, events
    assert kinds.count("loop_stall") == 1, events
    detail = dict(events)["loop_stall"]
    assert "12." in detail and "awake" in detail
    assert incidents == 0
    assert segments[0] == segments[1]


def test_host_suspend_is_still_recorded_and_pinned(tmp_path, monkeypatch, packet):
    """A real suspend stays a gap, including on Linux, where monotonic time hides it."""
    events, incidents, segments = record_across_gap(tmp_path, monkeypatch, packet, slept=30)
    kinds = [kind for kind, _ in events]
    assert kinds.count("host_suspend") == 1, events
    assert "loop_stall" not in kinds
    assert "30." in dict(events)["host_suspend"]
    assert incidents == 1
    assert segments[1] == segments[0] + 1


def test_a_neighbouring_dp3_is_never_attached(tmp_path, monkeypatch):
    """Only the configured serial AT the configured address gets a session.

    Every other runtime test scans exactly the configured unit, so the match
    itself was never exercised. Attaching to whichever DP3 advertises first
    would either fail authentication, which stops the collector, or, on the
    same account, record that unit as this one and send it this unit's AC
    commands. Each neighbour here matches one half of the identity, so
    dropping either check attaches to it.
    """
    scans, sessions = [], []
    neighbours = [
        (Identity("11:22:33:44:55:66", cfg().serial, 0, 0x13), object()),
        (Identity(cfg().address, "MR51ABCDEFGHIJKL", 0, 0x13), object()),
    ]

    async def scan(*_):
        scans.append(1)
        return neighbours

    class Session:
        def __init__(self, identity, *args, **kwargs):
            sessions.append(identity)

        async def run(self):
            await asyncio.Event().wait()

    monkeypatch.setattr("openpowerstation.runtime.ble.scan", scan)
    monkeypatch.setattr("openpowerstation.runtime.ble.Session", Session)
    s = Service(cfg(), tmp_path / "live.sqlite", lock_dir=tmp_path / "locks")
    s.start()
    try:
        # "authenticating" is set just before a session is built, so waiting on
        # it too makes a wrongly attached neighbour fail below, not time out.
        wait_for(lambda: s.state in ("reconnecting", "authenticating"))
    finally:
        s.stop(); s.join(5)
    assert not s.is_alive()
    assert scans
    assert sessions == [], f"attached to a DP3 that is not the configured one: {sessions}"


def test_authentication_failure_stops_no_retries(tmp_path,monkeypatch):
    attempts=[]
    async def scan(*_):
        attempts.append(1)
        raise AuthenticationError("Wrong user ID.")
    monkeypatch.setattr("openpowerstation.runtime.ble.scan",scan)
    s=Service(cfg(),tmp_path/"live.sqlite",lock_dir=tmp_path/"locks")
    s.start(); s.join(4)
    assert not s.is_alive()
    assert s.state=="error"
    assert len(attempts)==1


def collector_reason(database):
    with read_db(database) as db:
        row = db.execute("SELECT reason FROM collector_reason").fetchone()
    return row[0] if row else None


@pytest.mark.parametrize("cause", ["not_advertising", "radio_busy", "bluetooth_error"])
def test_a_failed_attempt_says_why_and_pins_nothing(tmp_path, monkeypatch, cause):
    """An attempt that never connected lost no link, and its cause is kept.

    Every such failure was recorded as disconnected, "Bluetooth transport
    failed; device state is unknown.": the exception's text discarded, the
    add-on log saying only "Retry in 2s", and a new segment and a pinned
    incident each time, as though a live session had dropped. A DP3 slow to
    come back -- 11 minutes after the nightly backup -- could not be put down
    to the other collector holding the radio, a DP3 not advertising, or BlueZ.
    """
    from openpowerstation import radio
    shown = {"not_advertising": "not advertising", "radio_busy": "lease unavailable",
             "bluetooth_error": "orphaned DP3 link"}[cause]
    retries = []

    async def scan(*_):
        if cause == "bluetooth_error":
            # What the add-on's BlueZ orphan cleanup raises before a scan.
            raise ConnectionError("BlueZ could not release the orphaned DP3 link")
        return []

    monkeypatch.setattr("openpowerstation.runtime.ble.scan", scan)
    monkeypatch.setattr(radio, "RADIO_LOCK_TIMEOUT", 0.05)
    other_collector = portalocker.Lock(str(radio.radio_lock_path(tmp_path)), timeout=0)
    if cause == "radio_busy":
        other_collector.acquire()
    s = Service(cfg(), tmp_path / "live.sqlite", lock_dir=tmp_path / "locks",
                notify=lambda update: update["state"] == "reconnecting"
                and retries.append(update["detail"]))
    try:
        s.start()
        wait_for(lambda: retries)
    finally:
        s.stop(); s.join(5)
        if cause == "radio_busy":
            other_collector.release()
    assert not s.is_alive()
    with read_db(s.database) as db:
        events = [(r[0], r[1]) for r in db.execute("SELECT kind,detail FROM events ORDER BY id")]
        incidents = db.execute("SELECT COUNT(*) FROM incidents").fetchone()[0]
    assert "disconnected" not in [kind for kind, _ in events], events
    failures = [detail for kind, detail in events if kind == "connection_failed"]
    assert failures, events
    assert f"reason={cause}" in failures[0] and shown in failures[0], failures[0]
    assert "operation=connect" in failures[0] and "stage=scanning" in failures[0]
    assert incidents == 0
    assert f"({cause})" in retries[0], retries
    assert collector_reason(s.database) == cause


def test_a_lost_link_keeps_its_incident_and_data_clears_the_reason(tmp_path, monkeypatch, packet):
    """A session that had connected did lose a link: still a pinned disconnected.

    The reason stays until a session delivers measurements again, not merely
    authenticates: that is the 09-19 shape, connected and saying nothing.
    """
    starts, deliver = [], threading.Event()

    async def scan(*_):
        return [(Identity(cfg().address, cfg().serial, 0, 0x13), object())]

    class Session:
        def __init__(self, identity, device, uid, on_frame, on_event, allow_control=False):
            self.on_frame, self.on_event = on_frame, on_event

        async def run(self):
            starts.append(1)
            self.on_event("connected", "local session", time.time_ns(), time.monotonic_ns())
            if len(starts) == 1:
                self.on_frame(packet(seq=1, bms_max_cell_temp=23), time.time_ns(), time.monotonic_ns())
                raise ConnectionError("Bluetooth disconnected.")
            while not deliver.is_set():
                await asyncio.sleep(0.01)
            self.on_frame(packet(seq=2, bms_max_cell_temp=23), time.time_ns(), time.monotonic_ns())
            await asyncio.Event().wait()

    monkeypatch.setattr("openpowerstation.runtime.ble.scan", scan)
    monkeypatch.setattr("openpowerstation.runtime.ble.Session", Session)
    monkeypatch.setattr("openpowerstation.runtime.RECONNECT_DELAY", 0.1)
    s = Service(cfg(), tmp_path / "live.sqlite", lock_dir=tmp_path / "locks")
    s.start()
    try:
        wait_for(lambda: len(starts) == 2 and s.state == "recording")
        connected_but_quiet = collector_reason(s.database)
        deliver.set()
        wait_for(lambda: collector_reason(s.database) == "none")
    finally:
        s.stop(); s.join(5)
    assert connected_but_quiet == "link_lost"
    with read_db(s.database) as db:
        drops = [r[0] for r in db.execute("SELECT detail FROM events WHERE kind='disconnected'")]
        incidents = db.execute("SELECT COUNT(*) FROM incidents").fetchone()[0]
    assert len(drops) == 1 and drops[0].startswith("reason=link_lost; "), drops
    assert incidents == 1


@pytest.mark.parametrize("message,reason", [
    ("Device rejected authentication. Verify account binding and user ID.",
     "authentication_rejected"),
    ("Unsupported advertised DP3 protocol; no fallback attempted.", "protocol_unsupported"),
])
def test_a_refused_handshake_stops_collection_and_says_which_refusal(
        tmp_path, monkeypatch, message, reason):
    """A rebound DP3 needs new credentials; a firmware change needs a decoder update.

    Both stopped collection as "error", and the add-on restarted the collector
    every 15 seconds; Home Assistant could not tell which had happened.
    """
    from openpowerstation.protocol import AuthenticationError, CredentialsRejected
    refusal = CredentialsRejected if reason == "authentication_rejected" else AuthenticationError

    async def scan(*_):
        return [(Identity(cfg().address, cfg().serial, 0, 0x13), object())]

    class Session:
        def __init__(self, identity, device, uid, on_frame, on_event, allow_control=False):
            pass

        async def run(self):
            raise refusal(message)

    monkeypatch.setattr("openpowerstation.runtime.ble.scan", scan)
    monkeypatch.setattr("openpowerstation.runtime.ble.Session", Session)
    s = Service(cfg(), tmp_path / "live.sqlite", lock_dir=tmp_path / "locks")
    s.start(); s.join(4)
    assert not s.is_alive()
    assert s.state == "error" and s.error == message
    with read_db(s.database) as db:
        failures = [r[0] for r in db.execute("SELECT detail FROM events WHERE kind='connection_failed'")]
    assert failures == [f"reason={reason}; {message}"]
    assert collector_reason(s.database) == reason


def test_a_start_below_the_storage_reserve_leaves_its_reason(tmp_path, monkeypatch):
    """No recording opens below the reserve, so nothing else would say why.

    The collector exits, the add-on starts it again every 15 seconds, and Home
    Assistant went on showing the last session's status.
    """
    s = Service(cfg(), tmp_path / "live.sqlite", lock_dir=tmp_path / "locks")
    monkeypatch.setattr("openpowerstation.storage.shutil.disk_usage", lambda _: SimpleNamespace(free=0))
    s.start(); s.join(4)
    assert not s.is_alive()
    assert "Storage reserve reached" in s.error
    assert collector_reason(s.database) == "storage_reserve"


def test_every_reason_is_documented_for_the_operator():
    """The reference's table is the runbook, so it names exactly the codes in use."""
    from pathlib import Path
    import re
    from openpowerstation.runtime import REASONS
    readme = (Path(__file__).resolve().parents[1] / "docs" / "REFERENCE.md").read_text(encoding="utf-8")
    section = readme.split("#### `collector_reason`", 1)[1].split("\n#", 1)[0]
    documented = re.findall(r"^\| `([a-z_]+)` \|", section, flags=re.MULTILINE)
    assert sorted(documented) == sorted(REASONS)


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

    monkeypatch.setattr("openpowerstation.runtime.ble.scan", scan)
    monkeypatch.setattr("openpowerstation.runtime.ble.Session", Session)
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
           "allow_control","jackery_serial","role"}

def test_setup_persists_only_permitted_config(tmp_path):
    c=cfg(); path=tmp_path/"config.json"
    save_config(c,path)
    assert load_config(path)==c
    stored=json.loads(path.read_text())
    # An allowlist, so no future field can quietly start persisting an account secret.
    # Where DPAPI exists the user ID is kept in its protected form as well.
    assert set(stored)==PERSISTED|({"user_id_dpapi"} if os.name=="nt" else set())
    assert "token" not in path.read_text()
    # No EcoFlow account password is ever a stored field, and the broker
    # credential the bridge needs stays empty until it is deliberately set.
    assert stored["mqtt_password"]==""

@pytest.mark.skipif(os.name != "nt", reason="DPAPI is Windows-only")
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
    from openpowerstation import ble

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

    monkeypatch.setattr("openpowerstation.runtime.ble.scan", scan)
    monkeypatch.setattr("openpowerstation.runtime.ble.Session", Session)
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
