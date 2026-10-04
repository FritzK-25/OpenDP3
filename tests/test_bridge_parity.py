"""Both MQTT bridges must meet the same failure the same way.

The DP3 and Jackery bridges are two copies of one lifecycle, and fixes kept
landing in one copy only. The Jackery bridge took no single-instance lock on
the path the add-on runs, crashed on a database error the DP3 bridge absorbed,
and re-sent its retained discovery every minute, connected or not. The DP3
bridge absorbed that error so quietly that its retained collector_state went
on reading "recording". Each test here runs both bridges through one scenario,
so a fix made to one copy alone fails for the other.

No broker and no radio: the client is a stand-in that records what would be sent.
"""
import contextlib
from dataclasses import dataclass
import itertools
import json
from pathlib import Path
import sqlite3
from types import SimpleNamespace
from typing import Callable

import portalocker
import pytest

from conftest import add
from test_bridge import make_config
from test_jackery_bridge import SERIAL as JACKERY_SERIAL, record_ble_observation
from openpowerstation import bridge, cli, jackery_bridge
from openpowerstation.config import Config, save_config
from openpowerstation.recorder import Recorder
from openpowerstation.storage import Store
from openpowerstation.vendor.packet import Packet
from openpowerstation.vendor.pb.mr521_pb2 import DisplayPropertyUpload


class FakeClient:
    """Records publishes, and which of them the caller waited to be delivered."""

    def __init__(self):
        self.published = []
        self.waited = []
        self.connected = None
        self.on_publish = None

    def publish(self, topic, payload=None, qos=0, retain=False):
        if self.on_publish:
            self.on_publish(topic, payload)
        self.published.append((topic, payload, retain))
        return SimpleNamespace(wait_for_publish=lambda timeout=None: self.waited.append(topic))

    def subscribe(self, topic, qos=0):
        pass

    def connect(self, host, port, keepalive=60):
        self.connected = (host, port)

    def loop_start(self):
        pass

    def loop_stop(self):
        pass

    def disconnect(self):
        pass

    def last(self, suffix):
        for topic, payload, _ in reversed(self.published):
            if topic.endswith(suffix):
                return payload
        return None

    def discovery(self):
        return [topic for topic, _, _ in self.published
                if topic.startswith("homeassistant/") and topic != bridge.STATUS_TOPIC]


@contextlib.contextmanager
def dp3_recording(path):
    """A DP3 session whose one frame arrived just now, so it reads as live."""
    frame = Packet(2, 0x21, 0xFE, 0x15,
                   DisplayPropertyUpload(bms_batt_soc=80, pow_out_sum_w=120).SerializeToString(),
                   seq=(1).to_bytes(4, "little")).to_bytes()
    with Store(path, reserve_bytes=0) as store:
        add(Recorder(store), frame, 0)
        yield


@dataclass(frozen=True)
class Device:
    name: str
    database: str
    # The lock file start_all.py and watchdog.py test to see whether it is up.
    lock: str
    # Its lines in the transition log start with this.
    log: str
    # The module whose latest() the bridge reads its recording through.
    module: object
    record: Callable
    make: Callable
    command: tuple

    def publisher(self, directory, client):
        return self.make(Path(directory) / self.database, client)


DEVICES = [
    Device("dp3", "recordings.sqlite", "bridge.lock", "", bridge, dp3_recording,
           lambda database, client: bridge.Bridge(make_config(), database, client=client),
           ("bridge",)),
    Device("jackery", "jackery.sqlite", "jackery-bridge.lock", "jackery ", jackery_bridge,
           record_ble_observation,
           lambda database, client: jackery_bridge.JackeryBridge(
               make_config(), database, serial=JACKERY_SERIAL, client=client),
           ("jackery-bridge", "--serial", JACKERY_SERIAL)),
]


@pytest.fixture(params=DEVICES, ids=[device.name for device in DEVICES])
def device(request):
    return request.param


# --------------------------------------------------------------------------- unreadable recording

def test_an_unreadable_recording_is_published_as_unreadable(device, tmp_path, capsys):
    """Neither frozen at "recording" nor fatal.

    The DP3 bridge treated an unreadable database as no recording: it published
    no state, so the retained collector_state kept reading "recording" over
    values it could no longer see, and the cause was never logged. The Jackery
    bridge let the error out of run(): the process exited, took every entity
    offline, and was restarted 15 seconds later to fail again.
    """
    (tmp_path / device.database).write_bytes(b"not a database " * 512)
    client = FakeClient()
    device.publisher(tmp_path, client).run(once=True)
    state = client.last("/state")
    assert state is not None, "nothing replaced the retained state"
    state = json.loads(state)
    assert state["collector_state"] == "unreadable"
    assert [key for key, value in state.items() if value is not None] == ["collector_state"]
    assert client.last("/telemetry") == "offline"
    log = capsys.readouterr().out
    assert (device.log + "database read error: none -> DatabaseError: file is not a database"
            in log), log


def test_a_read_failure_is_logged_once_and_clears_on_its_own(device, tmp_path, monkeypatch, capsys):
    """One line when reads start failing and one when they recover, none in between."""
    cause = "OperationalError: database is locked"
    with device.record(tmp_path / device.database):
        client = FakeClient()
        publisher = device.publisher(tmp_path, client)
        publisher.publish_once()
        assert json.loads(client.last("/state"))["collector_state"] == "recording"
        capsys.readouterr()

        read = device.module.latest

        def locked(*args, **kwargs):
            raise sqlite3.OperationalError("database is locked")

        monkeypatch.setattr(device.module, "latest", locked)
        publisher.publish_once()
        failed = capsys.readouterr().out
        assert json.loads(client.last("/state"))["collector_state"] == "unreadable"
        assert client.last("/telemetry") == "offline"
        publisher.publish_once()
        publisher.publish_once()
        assert capsys.readouterr().out == "", "a failure that persisted was logged again"

        monkeypatch.setattr(device.module, "latest", read)
        publisher.publish_once()
        recovered = capsys.readouterr().out
    assert json.loads(client.last("/state"))["collector_state"] == "recording"
    assert client.last("/telemetry") == "online"
    assert f"{device.log}database read error: none -> {cause}" in failed, failed
    assert f"{device.log}collector_state: recording -> unreadable" in failed, failed
    assert f"{device.log}database read error: {cause} -> none" in recovered, recovered
    assert f"{device.log}collector_state: unreadable -> recording" in recovered, recovered


# --------------------------------------------------------------------------- one instance

def test_a_second_bridge_is_refused_before_it_connects(device, tmp_path):
    """Two bridges for one device share an MQTT client ID.

    The broker drops the first when the second connects, and both reconnect in
    turn for as long as they run. The DP3 bridge was locked only by its launch
    command; the Jackery bridge only by the Windows launcher, never on the path
    the add-on runs.
    """
    held = portalocker.Lock(str(tmp_path / device.lock), timeout=0)
    held.acquire()
    try:
        client = FakeClient()
        with pytest.raises(ValueError, match="already publishing"):
            device.publisher(tmp_path, client).run(once=True)
    finally:
        held.release()
    assert client.connected is None, "the second bridge connected anyway"
    assert client.published == []


def test_the_lock_is_held_until_offline_is_published(device, tmp_path):
    """Released any earlier, a successor's "online" could be overwritten by it."""
    client = FakeClient()
    held_at_offline = []

    def probe(topic, payload):
        if topic.endswith("/availability") and payload == "offline":
            lock = portalocker.Lock(str(tmp_path / device.lock), timeout=0)
            try:
                lock.acquire()
            except portalocker.exceptions.LockException:
                held_at_offline.append(True)
            else:
                lock.release()
                held_at_offline.append(False)

    client.on_publish = probe
    device.publisher(tmp_path, client).run(once=True)
    assert held_at_offline == [True]
    # And released after, for the next run.
    successor = portalocker.Lock(str(tmp_path / device.lock), timeout=0)
    successor.acquire()
    successor.release()


def test_the_launch_command_the_app_runs_is_refused_too(device, tmp_path, monkeypatch, capsys):
    """The add-on starts both bridges through the CLI, so that path must be covered."""
    save_config(Config(address="AA:BB:CC:DD:EE:FF", serial="MR51ABCDEFGHIJKL", user_id="0",
                       mqtt_host="broker.invalid"), tmp_path / "config.json")
    clients = []
    for cls in (bridge.Bridge, jackery_bridge.JackeryBridge):
        monkeypatch.setattr(cls, "build_client",
                            lambda self: clients.append(FakeClient()) or clients[-1])
    held = portalocker.Lock(str(tmp_path / device.lock), timeout=0)
    held.acquire()
    try:
        status = cli.main(["--data-dir", str(tmp_path), *device.command, "--once"])
    finally:
        held.release()
    assert status == 1
    assert "already publishing" in capsys.readouterr().err
    assert clients == [], "the refused bridge built a client"


# --------------------------------------------------------------------------- discovery

def test_discovery_is_resent_only_on_connect_and_birth(device, tmp_path, monkeypatch):
    """Retained discovery is re-sent when the broker or Home Assistant may have lost it.

    The Jackery bridge also re-sent all of it every minute, connected or not:
    about 40,000 messages a day that Home Assistant parses and ignores, and,
    while the broker is down, a queue paho never bounds -- 1.5 MB an hour --
    replayed in full on reconnect.
    """
    client = FakeClient()
    publisher = device.publisher(tmp_path, client)
    # Six hours between publishes: longer than any refresh timer would wait.
    clock = itertools.count(start=1_000.0, step=6 * 3600.0)
    monkeypatch.setattr(device.module.time, "monotonic", lambda: next(clock))
    publisher.on_connect(client, None, None, 0)
    publisher.publish_once()
    once = len(client.discovery())
    assert once, "nothing was announced"
    for _ in range(3):
        publisher.publish_once()
    assert len(client.discovery()) == once, "discovery was re-sent on a timer"

    publisher.on_disconnect(client, None, None, 7, None)
    for _ in range(3):
        publisher.publish_once()
    assert len(client.discovery()) == once, "discovery was queued while disconnected"

    publisher.on_connect(client, None, None, 0)
    publisher.publish_once()
    assert len(client.discovery()) == 2 * once, "a reconnect was not re-announced to"
    publisher.on_message(client, None, SimpleNamespace(
        topic=bridge.STATUS_TOPIC, payload=b"online", retain=False))
    publisher.publish_once()
    publisher.publish_once()
    assert len(client.discovery()) == 3 * once, "a restarted Home Assistant was not re-announced to"


# --------------------------------------------------------------------------- shutdown

def test_shutdown_waits_for_offline_to_reach_the_broker(device, tmp_path):
    """Stopped before it is sent, "offline" is lost and the retained "online" stays.

    A clean disconnect does not fire the last will, so nothing else would
    correct it, and Home Assistant would keep the device's last values on show.
    """
    client = FakeClient()
    publisher = device.publisher(tmp_path, client)
    publisher.run(once=True)
    assert publisher.base + "/availability" in client.waited
