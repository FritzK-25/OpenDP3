"""Recovery from BLE sessions that stay alive after telemetry has stopped.

Every hang here is modelled with an operation that never returns, because that
is the failure the collectors actually hit: BlueZ keeps the link Connected, no
exception is ever raised, and the worker stays alive with nothing arriving. A
test that raises immediately would pass against the unfixed code.
"""
import asyncio
import contextlib
import json
from pathlib import Path
import subprocess
import sys
import textwrap
import time
from types import SimpleNamespace

from bleak.exc import BleakDBusError, BleakError
import portalocker
import pytest

import openpowerstation
from openpowerstation import ble
from openpowerstation import jackery
from openpowerstation import radio
from openpowerstation.ble import Session
from openpowerstation.bridge import device_id
from openpowerstation.cli import UNPRODUCTIVE_POLL_LIMIT, _jackery_ble_loop
from openpowerstation.config import Config
from openpowerstation.jackery import Identity as JackeryIdentity
from openpowerstation.jackery import (LocalReader, SALT_RC4, SERVICE_DATA_UUID, StationNotFound,
                             StationSilent, _command, _jackery_crc, _rc4_frame, _session_keys,
                             discover_reader, parse_advertisement, rc4)
from openpowerstation.jackery_bridge import JackeryBridge
from openpowerstation.jackery_fields import map_properties
from openpowerstation.protocol import Identity, WireBuffer, parse_packet
from openpowerstation.recorder import Recorder
from openpowerstation.runtime import Service
from openpowerstation.storage import Store, read_db
from openpowerstation.vendor.encryption import Type7Encryption
from openpowerstation.vendor.frame_assembler import EncPacketAssembler
from openpowerstation.vendor.packet import Packet


def dp3_session(on_event):
    return Session(
        Identity("AA:BB:CC:DD:EE:FF", "MR51123456789012", 0, 0x13),
        object(),
        "123456",
        lambda *_: None,
        on_event,
    )


async def test_repeated_dp3_silence_is_recorded_as_a_device_not_a_radio_fault(monkeypatch):
    """The teardown has to say which of the two causes it observed.

    A quiet device and a failed GATT operation both end as a reconnect. Only
    the second is evidence against the adapter, so the event must separate them.
    """
    monkeypatch.setattr(ble, "SILENCE_TIMEOUT", 0.01)
    monkeypatch.setattr(ble, "SILENCE_LIMIT", 2)
    events = []
    session = dp3_session(lambda kind, detail, *_: events.append((kind, detail)))
    calls = 0

    async def silent_packet(*, timeout):
        nonlocal calls
        calls += 1
        assert timeout == 0.01
        raise TimeoutError

    session._packet = silent_packet
    with pytest.raises(ConnectionError, match="while still connected"):
        await session._collect_authenticated()

    assert calls == 2
    assert [kind for kind, _ in events] == ["silence", "session_timeout"]
    detail = events[-1][1]
    assert "backend=Bleak" in detail
    assert "operation=notification_wait" in detail
    assert "exception=TimeoutError" in detail
    assert "consecutive_silences=2" in detail
    assert "transport_error=none_observed" in detail


async def test_a_bluez_failure_is_recorded_with_its_own_exception(monkeypatch):
    """The opposite case: something did raise, and the message must survive."""
    events = []
    session = dp3_session(lambda kind, detail, *_: events.append((kind, detail)))

    class Client:
        services = SimpleNamespace(get_characteristic=lambda uuid: None)

        def __init__(self, device, disconnected_callback=None, timeout=20, **kwargs):
            pass

        async def connect(self):
            raise RuntimeError("org.bluez.Error.Failed: le-connection-abort-by-local")

        async def disconnect(self):
            return True

    monkeypatch.setattr(ble, "BleakClient", Client)
    with pytest.raises(RuntimeError):
        await session.run()

    kinds = [kind for kind, _ in events]
    assert kinds == ["session_error"], kinds
    detail = events[0][1]
    assert "operation=session" in detail
    assert "exception=RuntimeError" in detail
    assert "org.bluez.Error.Failed" in detail
    assert "transport_error=none_observed" not in detail


async def test_silence_teardown_is_not_also_reported_as_an_unexplained_error(monkeypatch):
    """One failure, one reason. A second vaguer event would only confuse triage."""
    monkeypatch.setattr(ble, "SILENCE_TIMEOUT", 0.01)
    monkeypatch.setattr(ble, "SILENCE_LIMIT", 2)
    events = []
    session = dp3_session(lambda kind, detail, *_: events.append((kind, detail)))

    class Client:
        def __init__(self, device, disconnected_callback=None, timeout=20, **kwargs):
            self.services = SimpleNamespace(
                get_characteristic=lambda uuid: SimpleNamespace(
                    service_uuid="s", properties=["write"],
                    max_write_without_response_size=20))

        async def connect(self):
            return True

        async def start_notify(self, char, handler):
            return None

        async def disconnect(self):
            return True

    monkeypatch.setattr(ble, "BleakClient", Client)

    async def authenticate():
        session.authenticated = True

    async def never(timeout=30):
        raise TimeoutError

    monkeypatch.setattr(session, "authenticate", authenticate)
    monkeypatch.setattr(session, "_packet", never)

    with pytest.raises(ConnectionError):
        await session.run()

    kinds = [kind for kind, _ in events]
    assert kinds == ["connected", "silence", "session_timeout"], kinds


async def test_a_disconnect_that_never_returns_cannot_hold_recovery_open(monkeypatch):
    """Cleanup is the last place a stall can hide, so it is bounded too."""
    monkeypatch.setattr(ble, "DISCONNECT_TIMEOUT", 0.01)
    events = []
    session = dp3_session(lambda kind, detail, *_: events.append((kind, detail)))
    cancelled = asyncio.Event()

    class Client:
        services = SimpleNamespace(get_characteristic=lambda uuid: None)

        def __init__(self, device, disconnected_callback=None, timeout=20, **kwargs):
            pass

        async def connect(self):
            raise ConnectionError("gatt connect refused")

        async def disconnect(self):
            try:
                await asyncio.Event().wait()
            finally:
                cancelled.set()

    monkeypatch.setattr(ble, "BleakClient", Client)
    started = time.monotonic()
    with pytest.raises(ConnectionError, match="gatt connect refused"):
        await session.run()

    assert time.monotonic() - started < 2, "the hung disconnect was never abandoned"
    assert cancelled.is_set()
    kinds = [kind for kind, _ in events]
    assert kinds == ["session_error", "disconnect_error"], kinds
    assert "operation=disconnect" in events[-1][1]
    assert "exception=TimeoutError" in events[-1][1]


SESSION_KEY = Type7Encryption(b"k" * 16, b"i" * 16)


async def type7_notifications(packet, seqs, key=SESSION_KEY):
    """One EncPacket notification per DP3 upload, encrypted under ``key``."""
    codec = EncPacketAssembler(key)
    raws = [packet(seq=seq, bms_max_cell_temp=20 + seq % 10) for seq in seqs]
    return raws, [await codec.encode(parse_packet(raw)) for raw in raws]


def recording_session(recorder):
    """An authenticated type-7 session that records into ``recorder``."""
    session = Session(Identity("AA:BB:CC:DD:EE:FF", "MR51123456789012", 7, 0x13),
                      object(), "123456", recorder.ingest, recorder.event)
    session.wire, session.authenticated = WireBuffer(7, SESSION_KEY), True
    return session


async def test_notifications_already_received_are_recorded_before_a_disconnect(
        tmp_path, packet):
    """A link drop ends the session after its backlog, not in front of it.

    The disconnect flag was checked before the queue, so notifications that had
    arrived but were not yet read -- the frames around the drop, which its
    incident exists to keep -- were thrown away. The queue holds anything only
    when the loop has stalled, and production's stalls past 10 s came about
    once a minute.
    """
    with Store(tmp_path / "recording.sqlite", reserve_bytes=0) as store:
        recorder = Recorder(store)
        session = recording_session(recorder)
        raws, wires = await type7_notifications(packet, range(1, 31))
        for wire in wires:
            session.receive(None, wire)
        session.disconnected.set()
        with pytest.raises(ConnectionError, match="Bluetooth disconnected"):
            await session._collect_authenticated()
        kept = [bytes(row[0]) for row in store.conn.execute("SELECT raw FROM frames ORDER BY id")]
    assert kept == raws


async def test_a_full_receive_queue_drops_with_a_count_and_keeps_the_link(tmp_path, packet):
    """Falling behind is lost capture, not a lost link, and is recorded where it fell.

    A full queue set the disconnect flag: the session ended as "Bluetooth
    disconnected", discarding every notification it still held -- up to 2,048
    -- and blaming the radio for a process that stopped reading.
    """
    with Store(tmp_path / "recording.sqlite", reserve_bytes=0) as store:
        recorder = Recorder(store)
        session = recording_session(recorder)
        session.queue = asyncio.Queue(maxsize=4)
        raws, wires = await type7_notifications(packet, range(1, 9))
        for wire in wires[:6]:
            session.receive(None, wire)
        assert not session.disconnected.is_set(), "a full queue was taken for a disconnect"
        reading = asyncio.create_task(session._collect_authenticated())
        deadline = time.monotonic() + 5
        while not session.queue.empty() and time.monotonic() < deadline:
            await asyncio.sleep(0.01)
        for wire in wires[6:]:
            session.receive(None, wire)
        session.disconnected.set()
        with pytest.raises(ConnectionError, match="Bluetooth disconnected"):
            await asyncio.wait_for(reading, 5)
        frames = [bytes(row[0]) for row in store.conn.execute("SELECT raw FROM frames ORDER BY id")]
        segments = [row[0] for row in store.conn.execute("SELECT segment FROM frames ORDER BY id")]
        events = [tuple(row) for row in store.conn.execute("SELECT kind, detail FROM events")]
    assert frames == raws[:4] + raws[6:], events
    gaps = [detail for kind, detail in events if kind == "capture_gap"]
    assert len(gaps) == 1 and "2 dropped" in gaps[0], events
    # The four read before the hole and the two after it are not compared.
    assert segments[:4] == [segments[0]] * 4 and segments[4] == segments[5] > segments[3]


def pinned_reasons(store):
    return [reason["kind"] for row in store.conn.execute("SELECT reasons FROM incidents")
            for reason in json.loads(row[0])]


async def test_a_stale_session_key_ends_the_session_after_ten_frames(
        tmp_path, packet, monkeypatch):
    """Frames that pass the EncPacket checksum but decrypt to noise mean the key changed.

    Nothing derives the key again after authentication, so a DP3 that re-keyed
    -- after another client's session, say -- streamed undecodable frames until
    the 75-second lease ended the session: about 210 at production's rate, each
    a pinned corrupt_transport and a new segment. The pinned upstream drops the
    link after ten so that it authenticates again.
    """
    monkeypatch.setattr(ble, "SILENCE_TIMEOUT", 0.05)
    with Store(tmp_path / "recording.sqlite", reserve_bytes=0) as store:
        recorder = Recorder(store)
        session = recording_session(recorder)
        _, wires = await type7_notifications(packet, range(1, 21),
                                             key=Type7Encryption(b"K" * 16, b"i" * 16))
        for wire in wires:
            session.receive(None, wire)
        with pytest.raises(ConnectionError, match="session key"):
            await session._collect_authenticated()
        statuses = [row[0] for row in store.conn.execute("SELECT status FROM frames")]
        reasons = pinned_reasons(store)
        errors = [row[0] for row in store.conn.execute(
            "SELECT detail FROM events WHERE kind='session_error'")]
    # Each kept as evidence, but one incident reason for the run.
    assert statuses == ["invalid_packet"] * 10
    assert reasons == ["corrupt_transport"]
    assert len(errors) == 1 and "consecutive_undecodable=10" in errors[0], errors
    assert "operation=frame_decode" in errors[0]


async def test_one_decodable_frame_restarts_the_undecodable_count(tmp_path, packet):
    """Scattered corruption is not a changed key: only a run ends the session."""
    stale = Type7Encryption(b"K" * 16, b"i" * 16)
    with Store(tmp_path / "recording.sqlite", reserve_bytes=0) as store:
        recorder = Recorder(store)
        session = recording_session(recorder)
        _, first = await type7_notifications(packet, range(1, 10), key=stale)
        good, middle = await type7_notifications(packet, [10])
        _, last = await type7_notifications(packet, range(11, 20), key=stale)
        for wire in first + middle + last:
            session.receive(None, wire)
        session.disconnected.set()
        with pytest.raises(ConnectionError, match="Bluetooth disconnected"):
            await session._collect_authenticated()
        frames = store.conn.execute("SELECT COUNT(*) FROM frames").fetchone()[0]
        reasons = pinned_reasons(store)
    assert frames == 19
    # One corrupt_transport for each run, not one per frame.
    assert reasons == ["corrupt_transport", "corrupt_transport"]


async def test_each_end_a_dp3_session_chooses_names_its_reason(tmp_path, packet, monkeypatch):
    """A dropped link, a silent one and a stale key need different responses.

    All three ended as a ConnectionError the runtime recorded the same way.
    """
    from openpowerstation.runtime import failure_reason
    monkeypatch.setattr(ble, "SILENCE_TIMEOUT", 0.01)
    with Store(tmp_path / "recording.sqlite", reserve_bytes=0) as store:
        recorder = Recorder(store)
        dropped = recording_session(recorder)
        dropped.disconnected.set()
        with pytest.raises(ConnectionError) as link_lost:
            await dropped._collect_authenticated()
        with pytest.raises(ConnectionError) as silent:
            await recording_session(recorder)._collect_authenticated()
        stale = recording_session(recorder)
        _, wires = await type7_notifications(packet, range(1, 11),
                                             key=Type7Encryption(b"K" * 16, b"i" * 16))
        for wire in wires:
            stale.receive(None, wire)
        with pytest.raises(ConnectionError) as undecodable:
            await stale._collect_authenticated()
    assert [failure_reason(ended.value, connected=True)
            for ended in (link_lost, silent, undecodable)] == [
        "link_lost", "silent", "undecodable_frames"]
    # The same drop during the handshake lost no authenticated link.
    assert failure_reason(link_lost.value, connected=False) == "bluetooth_error"


async def test_drops_after_the_last_read_are_recorded_when_the_session_ends(monkeypatch):
    """No read follows them to report them, so the session's end does."""
    events = []
    session = dp3_session(lambda kind, detail, *_: events.append((kind, detail)))
    session.queue = asyncio.Queue(maxsize=2)

    class Client:
        def __init__(self, device, disconnected_callback=None, timeout=20, **kwargs):
            self.services = SimpleNamespace(
                get_characteristic=lambda uuid: SimpleNamespace(
                    service_uuid="s", properties=["write"],
                    max_write_without_response_size=20))

        async def connect(self):
            return True

        async def start_notify(self, char, handler):
            return None

        async def disconnect(self):
            return True

    async def authenticate():
        session.authenticated = True

    async def fall_behind_then_drop():
        for _ in range(5):
            session.receive(None, b"notification")
        raise ConnectionError("Bluetooth disconnected.")

    monkeypatch.setattr(ble, "BleakClient", Client)
    monkeypatch.setattr(session, "authenticate", authenticate)
    monkeypatch.setattr(session, "_collect_authenticated", fall_behind_then_drop)
    with pytest.raises(ConnectionError):
        await session.run()

    assert [kind for kind, _ in events] == ["connected", "capture_gap", "session_error"], events
    assert "3 dropped" in events[1][1]


def test_a_failed_dp3_session_reaches_the_runtime_reconnect_path(tmp_path, monkeypatch):
    settings = Config("AA:BB:CC:DD:EE:FF", "MR51123456789012", "123456")
    starts = []

    async def scan(*_args, **_kwargs):
        return [(Identity(settings.address, settings.serial, 0, 0x13), object())]

    class FailedThenLiveSession:
        def __init__(self, _identity, _device, _user_id, _on_frame, on_event,
                     allow_control=False):
            self.on_event = on_event
            self.gate = SimpleNamespace(allow_control=allow_control)

        async def run(self):
            starts.append(time.monotonic())
            self.on_event("connected", "test session", time.time_ns(), time.monotonic_ns())
            if len(starts) == 1:
                raise ConnectionError("while still connected")
            await asyncio.Event().wait()

    monkeypatch.setattr("openpowerstation.runtime.ble.scan", scan)
    monkeypatch.setattr("openpowerstation.runtime.ble.Session", FailedThenLiveSession)
    service = Service(settings, tmp_path / "recording.sqlite", lock_dir=tmp_path / "locks")
    service.start()
    try:
        # runtime.collect() waits its 2s reconnect backoff between attempts;
        # the loop exits as soon as the second start reaches recording, so a
        # wide deadline only bounds the failure case instead of slowing the pass.
        #
        # Wait for the state too, not only the start count. starts.append()
        # runs at the top of run(), before the "connected" event that moves
        # the service to "recording", so a count-only wait can return one
        # scheduling slice early and read the state mid-transition.
        deadline = time.monotonic() + 15
        while ((len(starts) < 2 or service.state != "recording")
                and time.monotonic() < deadline):
            time.sleep(0.02)
        assert len(starts) == 2
        assert service.state == "recording"
    finally:
        service.stop()
        service.join(5)

    assert not service.is_alive()
    assert not service.error


def test_a_session_with_no_valid_frames_is_cancelled_even_while_connected(tmp_path, monkeypatch):
    """The lease is the backstop for a stall the session itself cannot see.

    The session task here never returns and never raises - exactly the zombie
    shape - so only an external deadline on decoded frames can end it.
    """
    settings = Config("AA:BB:CC:DD:EE:FF", "MR51123456789012", "123456")
    starts = []

    async def scan(*_args, **_kwargs):
        return [(Identity(settings.address, settings.serial, 0, 0x13), object())]

    class SilentSession:
        def __init__(self, _identity, _device, _user_id, _on_frame, on_event,
                     allow_control=False):
            self.on_event = on_event
            self.gate = SimpleNamespace(allow_control=allow_control)

        async def run(self):
            starts.append(time.monotonic())
            self.on_event("connected", "test session", time.time_ns(), time.monotonic_ns())
            await asyncio.Event().wait()

    monkeypatch.setattr("openpowerstation.runtime.ble.scan", scan)
    monkeypatch.setattr("openpowerstation.runtime.ble.Session", SilentSession)
    monkeypatch.setattr("openpowerstation.runtime.SESSION_FRAME_LEASE", 0.02)
    monkeypatch.setattr("openpowerstation.runtime.SESSION_LEASE_CHECK", 0.005)
    service = Service(settings, tmp_path / "recording.sqlite", lock_dir=tmp_path / "locks")
    service.start()
    try:
        # runtime.collect() waits its 2s reconnect backoff between attempts;
        # the loop exits on the second start, so a wide deadline only bounds
        # the failure case instead of slowing the passing one.
        deadline = time.monotonic() + 15
        while len(starts) < 2 and time.monotonic() < deadline:
            time.sleep(0.02)
        assert len(starts) == 2
    finally:
        service.stop()
        service.join(5)

    with read_db(service.database) as database:
        count = database.execute(
            "SELECT COUNT(*) FROM events WHERE kind='session_lease_expired'"
        ).fetchone()[0]
        reason = database.execute("SELECT reason FROM collector_reason").fetchone()[0]
    assert count >= 1
    # Nothing at all arrived: a silent link, not a protocol the decoder misses.
    assert reason == "silent"
    assert not service.is_alive()


def test_frames_without_measurements_do_not_hold_the_session_open(tmp_path, monkeypatch):
    """The 2026-09-19 shape: the link up, frames flowing, none carrying a measurement.

    The lease test above uses a session that never delivers a frame, so it
    cannot tell a lease renewed by measurements from one renewed by any frame:
    with the runtime changed to renew on every frame the whole suite still
    passed. This session streams parseable DP3 frames on a route the decoder
    does not map. The expiry also has to say what the frames were, since a
    housekeeping stream, a changed key and retransmits each mean something else.
    """
    settings = Config("AA:BB:CC:DD:EE:FF", "MR51123456789012", "123456")
    starts = []

    async def scan(*_args, **_kwargs):
        return [(Identity(settings.address, settings.serial, 7, 0x13), object())]

    class ChattySession:
        def __init__(self, _identity, _device, _user_id, on_frame, on_event,
                     allow_control=False):
            self.on_frame, self.on_event = on_frame, on_event
            self.gate = SimpleNamespace(allow_control=allow_control)

        async def run(self):
            starts.append(time.monotonic())
            self.on_event("connected", "test session", time.time_ns(), time.monotonic_ns())
            seq = 0
            while True:
                seq += 1
                housekeeping = Packet(2, 0x21, 0xFE, 0x16, b"\x08\x01",
                                      seq=seq.to_bytes(4, "little")).to_bytes()
                self.on_frame(housekeeping, time.time_ns(), time.monotonic_ns())
                await asyncio.sleep(0.01)

    monkeypatch.setattr("openpowerstation.runtime.ble.scan", scan)
    monkeypatch.setattr("openpowerstation.runtime.ble.Session", ChattySession)
    monkeypatch.setattr("openpowerstation.runtime.SESSION_FRAME_LEASE", 0.3)
    monkeypatch.setattr("openpowerstation.runtime.SESSION_LEASE_CHECK", 0.02)
    monkeypatch.setattr("openpowerstation.runtime.RECONNECT_DELAY", 0.1)
    service = Service(settings, tmp_path / "recording.sqlite", lock_dir=tmp_path / "locks")
    service.start()
    try:
        deadline = time.monotonic() + 15
        while len(starts) < 2 and time.monotonic() < deadline:
            time.sleep(0.02)
    finally:
        service.stop()
        service.join(5)

    assert len(starts) >= 2, "a stream without measurements held the session open"
    with read_db(service.database) as database:
        expiries = [row[0] for row in database.execute(
            "SELECT detail FROM events WHERE kind='session_lease_expired' ORDER BY id")]
        statuses = {row[0] for row in database.execute("SELECT DISTINCT status FROM frames")}
    assert statuses == {"unknown_message"}
    assert expiries, "the lease never expired"
    assert "frames since the last measurement" in expiries[0], expiries[0]
    assert "unknown_message 02/FE/16 x" in expiries[0], expiries[0]
    # What a firmware change the decoder does not know looks like. It ended as
    # "disconnected", as a dropped link does, while collector_state stayed
    # "waiting"; Home Assistant could not tell it from the radio.
    with read_db(service.database) as database:
        drops = [row[0] for row in database.execute(
            "SELECT detail FROM events WHERE kind='disconnected' ORDER BY id")]
        reason = database.execute("SELECT reason FROM collector_reason").fetchone()[0]
    assert drops and drops[0].startswith("reason=no_measurements; "), drops
    assert reason == "no_measurements"


def test_a_dp3_handshake_that_never_answers_gives_the_adapter_back(tmp_path, monkeypatch):
    """A handshake write that never returns cannot keep the other battery off the radio.

    Nothing bounded the DP3's attempt before ``connected``: the collector sat in
    authenticating holding the adapter lease, so the Jackery could not reattach,
    and after a respawn nothing else ended it either, because the app supervisor
    arms only once a new frame arrives. Only the Bluetooth client is faked here;
    the session, its handshake and the lease are the real ones.
    """
    settings = Config("AA:BB:CC:DD:EE:FF", "MR51123456789012", "123456")
    writes, closed_under_lease = [], []

    async def scan(*_args, **_kwargs):
        return [(Identity(settings.address, settings.serial, 0, 0x13), object())]

    class UnansweredClient:
        def __init__(self, device, disconnected_callback=None, timeout=20, **kwargs):
            char = SimpleNamespace(service_uuid="s", properties=["write"],
                                   max_write_without_response_size=20)
            self.services = SimpleNamespace(get_characteristic=lambda uuid: char)

        async def connect(self):
            return True

        async def start_notify(self, char, handler):
            return None

        async def write_gatt_char(self, *_args, **_kwargs):
            writes.append(time.monotonic())
            await asyncio.Event().wait()

        async def disconnect(self):
            # The half-open link has to be closed before the lease goes, or
            # the Jackery could start connecting beside a DP3 link still open.
            probe = portalocker.Lock(str(radio.radio_lock_path(tmp_path)),
                                     timeout=0, fail_when_locked=True)
            try:
                probe.acquire()
            except portalocker.exceptions.AlreadyLocked:
                closed_under_lease.append(True)
            else:
                probe.release()
                closed_under_lease.append(False)
            return True

    monkeypatch.setattr("openpowerstation.runtime.ble.scan", scan)
    monkeypatch.setattr(ble, "BleakClient", UnansweredClient)
    monkeypatch.setattr(radio, "RADIO_HOLD_LIMIT", 0.5)
    service = Service(settings, tmp_path / "recording.sqlite", lock_dir=tmp_path / "locks")
    service.start()
    try:
        # Generous: a loaded Windows runner has taken over five seconds to
        # get here. A passing run never waits this long.
        deadline = time.monotonic() + 30
        while not writes and time.monotonic() < deadline:
            time.sleep(0.01)
        assert writes, "the handshake never reached its first write"
        assert service.state == "authenticating"
        # What the Jackery collector needs in order to reattach.
        other_collector = portalocker.Lock(str(radio.radio_lock_path(tmp_path)), timeout=5)
        started = time.monotonic()
        other_collector.acquire()
        waited = time.monotonic() - started
        other_collector.release()
    finally:
        service.stop()
        service.join(5)

    assert waited < 3, f"the adapter lease stayed held for {waited:.1f}s"
    assert closed_under_lease[:1] == [True], closed_under_lease
    assert not service.is_alive()
    with read_db(service.database) as database:
        details = [row[0] for row in database.execute(
            "SELECT detail FROM events WHERE kind='session_error' ORDER BY id")]
    assert details, "the abandoned attempt left no reason behind"
    assert "operation=connect" in details[0]
    assert "stage=authenticating" in details[0]
    assert "0.5s limit" in details[0]


def test_the_dp3_backoff_resets_on_data_not_on_authentication(tmp_path, monkeypatch, packet):
    """A DP3 that authenticates and then reports nothing must not be redialled at the floor.

    The reconnect delay reset on every ``connected`` event, so a session that
    authenticated and delivered nothing -- ended by the valid-frame lease --
    came back after the minimum delay every time: a scan and a handshake on the
    shared adapter about every 90 seconds, indefinitely. Only a frame carrying
    measurements shows the link is worth returning to quickly.
    """
    settings = Config("AA:BB:CC:DD:EE:FF", "MR51123456789012", "123456")
    starts, retries = [], []

    async def scan(*_args, **_kwargs):
        return [(Identity(settings.address, settings.serial, 0, 0x13), object())]

    class Session:
        def __init__(self, _identity, _device, _user_id, on_frame, on_event,
                     allow_control=False):
            self.on_frame, self.on_event = on_frame, on_event
            self.gate = SimpleNamespace(allow_control=allow_control)

        async def run(self):
            starts.append(time.monotonic())
            self.on_event("connected", "test session", time.time_ns(), time.monotonic_ns())
            if len(starts) == 3:
                self.on_frame(packet(seq=1, bms_max_cell_temp=23),
                              time.time_ns(), time.monotonic_ns())
                raise ConnectionError("link lost after a reading")
            # Authenticated and silent, until the valid-frame lease ends it.
            await asyncio.Event().wait()

    def notify(update):
        if update["state"] == "reconnecting":
            retries.append(update["detail"])

    monkeypatch.setattr("openpowerstation.runtime.ble.scan", scan)
    monkeypatch.setattr("openpowerstation.runtime.ble.Session", Session)
    monkeypatch.setattr("openpowerstation.runtime.SESSION_FRAME_LEASE", 0.05)
    monkeypatch.setattr("openpowerstation.runtime.SESSION_LEASE_CHECK", 0.01)
    monkeypatch.setattr("openpowerstation.runtime.RECONNECT_DELAY", 0.1)
    service = Service(settings, tmp_path / "recording.sqlite", notify=notify,
                      lock_dir=tmp_path / "locks")
    service.start()
    try:
        deadline = time.monotonic() + 20
        while len(retries) < 3 and time.monotonic() < deadline:
            time.sleep(0.02)
    finally:
        service.stop()
        service.join(5)

    delays = [float(detail.split()[2].rstrip("s.")) for detail in retries[:3]]
    # Silent, silent, then a reading: the delay grows until data arrives.
    assert delays == [0.1, 0.2, 0.1], retries


def test_adapter_selection_is_explicit_and_scopes_the_radio_lock(tmp_path, monkeypatch):
    monkeypatch.setenv(radio.ADAPTER_ENV, "hci1")
    assert radio.configured_adapter() == "hci1"
    assert radio.radio_lock_path(tmp_path).name == "radio-hci1.lock"
    monkeypatch.setattr(radio.sys, "platform", "linux")
    assert radio.bleak_adapter_kwargs() == {"adapter": "hci1"}
    monkeypatch.setenv(radio.ADAPTER_ENV, "not-an-adapter")
    with pytest.raises(ValueError, match="expected hci"):
        radio.configured_adapter()


async def test_a_lease_holder_that_never_finishes_is_cut_off_at_the_hold_limit(
        tmp_path, monkeypatch):
    """No holder keeps the adapter longer than the other collector will wait for it.

    The work under the lease is cancelled at the hold limit, whatever it is
    stuck in, and the lease goes back; the holder learns why.
    """
    monkeypatch.setattr(radio, "RADIO_HOLD_LIMIT", 0.2)
    path = radio.radio_lock_path(tmp_path)
    holding = asyncio.Event()

    async def hung_recovery():
        async with radio.RadioLease(path):
            holding.set()
            await asyncio.Event().wait()

    holder = asyncio.create_task(hung_recovery())
    await asyncio.wait_for(holding.wait(), 5)
    started = time.monotonic()
    async with radio.RadioLease(path, timeout=5):
        waited = time.monotonic() - started
    with pytest.raises(ConnectionError, match="0.2s limit"):
        await asyncio.wait_for(holder, 1)
    assert waited < 2, f"waited {waited:.1f}s for a lease limited to 0.2s"


def test_a_collector_waiting_for_the_lease_stops_without_waiting_it_out(tmp_path):
    """A stop request reaches a collector queued for the radio at once.

    The wait used to block a worker thread for the whole acquire timeout, and
    asyncio.run() joins that thread on the way out, so a collector told to
    stop while queued behind the other one could not exit until it gave up.
    """
    path = radio.radio_lock_path(tmp_path)
    other_collector = portalocker.Lock(str(path), timeout=0)
    other_collector.acquire()

    async def queued_then_stopped():
        waiter = asyncio.create_task(radio.RadioLease(path, timeout=3).__aenter__())
        await asyncio.sleep(0.3)
        waiter.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await waiter

    try:
        started = time.monotonic()
        asyncio.run(queued_then_stopped())
        took = time.monotonic() - started
    finally:
        other_collector.release()
    assert took < 1.5, f"stopping a queued collector took {took:.1f}s"


def test_every_lease_waiter_outlasts_the_longest_permitted_hold():
    """The lease budget, checked against the deadlines it is built from.

    A cancelled holder still cleans up under the lease -- a bounded DP3
    disconnect, a bounded Jackery unsubscribe and disconnect -- so a waiter has
    to outlast the hold limit by that much, or a holder inside its budget could
    still make the other collector's recovery fail. And the deadlines that are
    not the lease's own must leave the DP3 room for its handshake.
    """
    cleanup = max(ble.DISCONNECT_TIMEOUT, 2 * jackery.GATT_CLOSE_TIMEOUT)
    assert radio.RADIO_LOCK_TIMEOUT >= radio.RADIO_HOLD_LIMIT + cleanup + radio.RADIO_LOCK_POLL
    # DP3: discovery always runs its full window, then Bleak's connect deadline.
    assert ble.SCAN_TIMEOUT + ble.CONNECT_TIMEOUT <= radio.RADIO_HOLD_LIMIT / 2
    # Jackery: it searches outside the lease and holds it to connect.
    assert jackery.CONNECT_TIMEOUT <= radio.RADIO_HOLD_LIMIT / 2


def jackery_identity():
    return JackeryIdentity(
        "AA:BB:CC:DD:EE:FF",
        "HT-test",
        "123456789012345",
        8,
        50,
        -40,
        "MDEyMzQ1Njc4OWFiY2RlZg==",
    )


def jackery_reader(bleak):
    reader = LocalReader(jackery_identity())
    reader._bleak = bleak
    reader.key = b"0123456789abcdef"
    return reader


async def test_a_gatt_write_that_never_returns_is_cancelled_by_the_read_deadline():
    """The write, not only the notification wait, has to obey the deadline.

    The read timeout protected the wait for a reply. A write that never returns
    never reaches that wait, so the whole collector stopped instead.
    """
    cancelled = asyncio.Event()

    class HangingBleak:
        async def write_gatt_char(self, *_args, **_kwargs):
            try:
                await asyncio.Event().wait()
            finally:
                cancelled.set()

    with pytest.raises(TimeoutError) as failure:
        await jackery_reader(HangingBleak()).read(timeout=0.01)

    assert cancelled.is_set()
    # A write that cannot complete is the link failing, not the station
    # staying quiet, so it must not be tolerated as a silent poll.
    assert not isinstance(failure.value, StationSilent)
    detail = str(failure.value)
    assert "backend=Bleak/" in detail
    assert "operation=write:status" in detail
    assert "exception=TimeoutError" in detail
    assert "timeout=0.01s" in detail


async def test_a_gatt_write_failure_keeps_its_backend_operation_and_cause():
    class FailedBleak:
        async def write_gatt_char(self, *_args, **_kwargs):
            raise RuntimeError("org.bluez.Error.Failed: ATT request failed")

    with pytest.raises(ConnectionError) as failure:
        await jackery_reader(FailedBleak()).read(timeout=0.1)

    detail = str(failure.value)
    assert "backend=Bleak/" in detail
    assert "operation=write:status" in detail
    assert "exception=RuntimeError" in detail
    assert "org.bluez.Error.Failed" in detail


async def test_jackery_silence_is_not_mislabelled_as_a_bluez_error():
    class WorkingBleak:
        async def write_gatt_char(self, *_args, **_kwargs):
            return None

    with pytest.raises(StationSilent) as failure:
        await jackery_reader(WorkingBleak()).read(timeout=0.01)

    detail = str(failure.value)
    assert "backend=Bleak/" in detail
    assert "operation=notification_wait" in detail
    assert "exception=TimeoutError" in detail
    assert "transport_error=none_observed" in detail


async def test_notifications_that_never_decode_are_station_silence_too():
    """A reply that arrives but cannot be read is still no answer to the query."""
    reader = jackery_reader(None)

    class GarbledBleak:
        async def write_gatt_char(self, *_args, **_kwargs):
            reader._consume(b"\x00" * 12)

    reader._bleak = GarbledBleak()
    with pytest.raises(StationSilent) as failure:
        await reader.read(timeout=0.01)

    assert "operation=notification_decode" in str(failure.value)
    assert "transport_error=none_observed" in str(failure.value)


async def test_a_hung_jackery_cleanup_is_reported_instead_of_blocking_reattach():
    """close() must return: the reattach after a stall depends on it."""
    class HangingBleak:
        async def stop_notify(self, *_args, **_kwargs):
            await asyncio.Event().wait()

        async def disconnect(self):
            await asyncio.Event().wait()

    from openpowerstation import jackery
    original = jackery.GATT_CLOSE_TIMEOUT
    jackery.GATT_CLOSE_TIMEOUT = 0.01
    try:
        started = time.monotonic()
        diagnostics = await jackery_reader(HangingBleak()).close()
    finally:
        jackery.GATT_CLOSE_TIMEOUT = original

    assert time.monotonic() - started < 2, "close() never abandoned the hung operations"
    assert len(diagnostics) == 2, diagnostics
    assert "operation=stop_notify" in diagnostics[0]
    assert "operation=disconnect" in diagnostics[1]
    assert all("exception=TimeoutError" in entry for entry in diagnostics)


async def test_the_jackery_collector_records_the_failure_before_reattaching(tmp_path, monkeypatch):
    """A stall that happens mid-recording has to leave the reason behind.

    The first read succeeds so there is a recording to write to; the session
    then stalls, which is the shape of the real 30-second freshness trips.
    """
    stop_file = tmp_path / "stop"
    closed = []

    class StallingReader:
        identity = SimpleNamespace(serial="123456789012345")

        def __init__(self):
            self.reads = 0

        async def read(self, timeout):
            self.reads += 1
            if self.reads == 1:
                return {"rb": 50, "oac": 1}
            stop_file.touch()
            raise TimeoutError(
                "backend=Bleak/linux; operation=write:status; "
                "exception=TimeoutError; timeout=2s"
            )

        async def close(self):
            closed.append(True)
            return [
                "backend=Bleak/linux; operation=disconnect; "
                "exception=RuntimeError: org.bluez.Error.Failed"
            ]

    reader = StallingReader()

    async def discover_reader(_timeout, **_):
        return reader

    monkeypatch.setattr("openpowerstation.jackery.discover_reader", discover_reader)
    with Store(tmp_path / "jackery.sqlite", reserve_bytes=0) as store:
        await _jackery_ble_loop(
            store,
            interval=1,
            hours=None,
            serial="123456789012345",
            stop_file=stop_file,
        )
        row = store.conn.execute(
            "SELECT kind, detail FROM events WHERE kind='disconnected' ORDER BY id DESC LIMIT 1"
        ).fetchone()
        close_row = store.conn.execute(
            "SELECT kind, detail FROM events WHERE kind='disconnect_error' ORDER BY id DESC LIMIT 1"
        ).fetchone()

    assert closed == [True]
    assert reader.reads == 2
    assert row[0] == "disconnected"
    assert "operation=write:status" in row[1]
    assert "exception=TimeoutError" in row[1]
    assert "closing session and reattaching" in row[1]
    assert close_row[0] == "disconnect_error"
    assert "operation=disconnect" in close_row[1]
    assert "org.bluez.Error.Failed" in close_row[1]


SERIAL = "856199990000000"
NEIGHBOUR = "856100000000001"
OK = {"rb": 50, "oac": 1}
UNMAPPED = {"zz": 1}
SILENT = ("backend=Bleak/linux; operation=notification_wait; exception=TimeoutError; "
          "transport_error=none_observed; Jackery stopped answering property queries "
          "on an open GATT session.")
REFUSED = ("org.bluez.Error.Failed", ["le-connection-abort-by-remote"])


def explorer_advertisement(serial):
    """The public advertisement an Explorer 1000 v2 broadcasts, encoded as the station does."""
    manufacturer = {(ord(serial[0]) << 8) | 0x02: serial[1:].encode()}
    plain = (8).to_bytes(2, "big") + bytes(range(1, 7)) + bytes([64, 0, 0])
    mask = 0x5A
    body = bytes(value ^ mask for value in plain) + bytes([mask])
    framed = body + bytes.fromhex(_jackery_crc(body))
    service = {SERVICE_DATA_UUID: rc4(framed, (serial[:3] + serial[-5:]).encode() + SALT_RC4)}
    assert parse_advertisement(manufacturer, service).serial == serial
    return manufacturer, service


class FakeBluetooth:
    """BlueZ as the Jackery path sees it, with no radio anywhere.

    Each listed Explorer advertises, in order, as soon as a scan starts, and
    answers every write on its open session with OK under its own session key.
    ``connect_errors`` scripts the connect attempts in turn (None succeeds), and
    ``answer(session, write)`` says whether the link still carries a write:
    False fails it the way a dropped BlueZ link does. ``scans`` counts the
    scans that started.
    """

    def __init__(self, monkeypatch, *advertising, connect_errors=(), scan_error=None,
                 notify_error=None, stop_error=None):
        self.advertising = {f"AA:BB:CC:DD:EE:{index:02X}": serial
                            for index, serial in enumerate(advertising)}
        self.connect_errors = list(connect_errors)
        self.connects, self.sessions, self.closed = [], [], []
        self.scans = 0
        self.answer = lambda session, write: True
        radio = self

        class Scanner:
            def __init__(self, detection_callback, **_):
                self.callback = detection_callback

            async def start(self):
                if scan_error:
                    raise scan_error
                radio.scans += 1
                for address, serial in radio.advertising.items():
                    manufacturer, service = explorer_advertisement(serial)
                    self.callback(SimpleNamespace(name="HT-E1000V2", address=address),
                                  SimpleNamespace(manufacturer_data=manufacturer,
                                                  service_data=service, rssi=-60))

            async def stop(self):
                if stop_error:
                    raise stop_error

        class Client:
            def __init__(self, device, **_):
                self.serial = radio.advertising[device.address]
                identity = parse_advertisement(*explorer_advertisement(self.serial))
                self.key = _session_keys(identity.encryption_key)[0]
                self.writes = 0

            async def connect(self):
                radio.connects.append(self.serial)
                error = radio.connect_errors.pop(0) if radio.connect_errors else None
                if error:
                    raise error
                self.session = len(radio.sessions)
                radio.sessions.append(self)

            async def start_notify(self, _uuid, handler):
                if notify_error:
                    raise notify_error
                self.handler = handler

            async def write_gatt_char(self, _uuid, _payload, response=False):
                self.writes += 1
                if not radio.answer(self.session, self.writes):
                    raise BleakError("Not connected")
                body = json.dumps(OK, separators=(",", ":"))
                self.handler(None, _rc4_frame(_command(0xFC, 0x03, body), self.key))

            async def stop_notify(self, _uuid):
                pass

            async def disconnect(self):
                radio.closed.append(self.serial)
                return True

        async def no_windows_device_id(*_):
            return None

        # All three, or a test on Windows would reach the real adapter.
        monkeypatch.setattr(jackery, "BleakScanner", Scanner)
        monkeypatch.setattr(jackery, "BleakClient", Client)
        monkeypatch.setattr(jackery, "_winrt_device_id", no_windows_device_id)


def recording(store):
    events = [tuple(row) for row in store.conn.execute("SELECT kind, detail FROM events ORDER BY id")]
    return SimpleNamespace(
        events=events,
        kinds=[kind for kind, _ in events],
        frames=store.conn.execute("SELECT COUNT(*) FROM frames").fetchone()[0],
        statuses=[row[0] for row in store.conn.execute("SELECT status FROM sessions")],
    )


async def test_the_collector_never_connects_to_a_neighbouring_explorer(tmp_path, monkeypatch):
    """Another Explorer advertising first must not cost ours its window.

    The station accepts one BLE client. Discovery opened whichever Explorer
    advertised first and compared serials only afterwards, so a neighbour's
    unit ended the collector each time it won the race, after its only client
    slot had already been taken.
    """
    stop = tmp_path / "stop"
    radio = FakeBluetooth(monkeypatch, NEIGHBOUR, SERIAL)

    def answer(_session, _write):
        stop.touch()
        return True

    radio.answer = answer
    with Store(tmp_path / "jackery.sqlite", reserve_bytes=0) as store:
        await _jackery_ble_loop(store, interval=0.01, hours=None, serial=SERIAL, stop_file=stop)
        run = recording(store)

    assert radio.connects == [SERIAL], "a neighbouring Explorer was connected to"
    assert run.frames == 1


async def test_discovery_says_what_it_ignored_when_ours_never_advertises(monkeypatch):
    radio = FakeBluetooth(monkeypatch, NEIGHBOUR)

    with pytest.raises(StationNotFound) as failure:
        await discover_reader(0.05, serial=SERIAL)

    assert radio.connects == []
    detail = str(failure.value)
    assert SERIAL in detail
    assert "ignored 1 other Explorer" in detail
    assert NEIGHBOUR not in detail, "a neighbour's serial does not belong in our log"


async def test_the_jackery_search_leaves_the_adapter_lease_free(tmp_path, monkeypatch):
    """Waiting for an advertisement is not recovery work, and it can last for hours.

    The whole 60-second search ran under the adapter lease, released for only
    the poll interval between searches, so while the Explorer was away the
    DP3's own reconnects queued behind it and about a quarter of them ran out
    their wait, each recorded as a failed transport.
    """
    bluetooth = FakeBluetooth(monkeypatch, NEIGHBOUR)
    with Store(tmp_path / "jackery.sqlite", reserve_bytes=0) as store:
        collector = asyncio.create_task(_jackery_ble_loop(
            store, interval=0.01, hours=None, serial=SERIAL, stop_file=tmp_path / "stop"))
        try:
            deadline = time.monotonic() + 5
            while not bluetooth.scans and time.monotonic() < deadline:
                await asyncio.sleep(0.01)
            assert bluetooth.scans, "the collector never started searching"
            # What the DP3 collector needs in order to reconnect meanwhile.
            async with radio.RadioLease(radio.radio_lock_path(tmp_path), timeout=0.5):
                pass
        finally:
            collector.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await collector
    assert bluetooth.connects == []


async def test_discovery_takes_the_lease_only_to_open_its_own_explorer(monkeypatch):
    """The lease covers connecting to our station, after it advertised, and nothing else."""
    bluetooth = FakeBluetooth(monkeypatch, NEIGHBOUR, SERIAL)
    seen = []

    class Lease:
        async def __aenter__(self):
            seen.append(("enter", bluetooth.scans, list(bluetooth.connects)))
            return self

        async def __aexit__(self, *_):
            seen.append(("exit", list(bluetooth.connects), len(bluetooth.sessions)))

    reader = await discover_reader(1, serial=SERIAL, lease=Lease())
    try:
        # Taken once the search had found our Explorer and not before, and
        # given back as soon as its session was open.
        assert seen == [("enter", 1, []), ("exit", [SERIAL], 1)]
    finally:
        await reader.close()


async def test_a_jackery_connect_that_never_returns_is_abandoned_and_retried(
        tmp_path, monkeypatch):
    """The Jackery's connect is held to the same limit as any other holder.

    Bleak's own deadline normally ends a connect, but should the call never
    return, the lease cuts it off at its hold limit, and the collector treats
    that as one more failed attach to retry in process rather than a reason to
    stop -- or to keep the DP3 off the radio for as long as the call hangs.
    """
    monkeypatch.setattr(radio, "RADIO_HOLD_LIMIT", 0.2)
    bluetooth = FakeBluetooth(monkeypatch, SERIAL)

    class HungClient(jackery.BleakClient):
        async def connect(self):
            bluetooth.connects.append(self.serial)
            await asyncio.Event().wait()

    monkeypatch.setattr(jackery, "BleakClient", HungClient)
    with Store(tmp_path / "jackery.sqlite", reserve_bytes=0) as store:
        collector = asyncio.create_task(_jackery_ble_loop(
            store, interval=0.01, hours=None, serial=SERIAL, stop_file=tmp_path / "stop"))
        try:
            deadline = time.monotonic() + 5
            while len(bluetooth.connects) < 2 and time.monotonic() < deadline:
                await asyncio.sleep(0.01)
            assert len(bluetooth.connects) >= 2, "the hung connect was never abandoned"
            assert not collector.done(), "an abandoned attach ended the collector"
        finally:
            collector.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await collector
    assert bluetooth.sessions == []


@pytest.mark.parametrize("operation", ["scan_start", "connect", "start_notify", "scan_stop"])
async def test_a_bluez_failure_while_attaching_arrives_classified(monkeypatch, operation):
    """Bleak's own errors are not OSError, so unclassified they ended the collector."""
    refused = BleakDBusError(*REFUSED)
    radio = FakeBluetooth(monkeypatch, SERIAL,
                          scan_error=refused if operation == "scan_start" else None,
                          connect_errors=[refused] if operation == "connect" else (),
                          notify_error=refused if operation == "start_notify" else None,
                          stop_error=refused if operation == "scan_stop" else None)

    with pytest.raises(ConnectionError) as failure:
        await discover_reader(1)

    detail = str(failure.value)
    assert "backend=Bleak/" in detail
    assert f"operation={operation}" in detail
    assert "exception=BleakDBusError" in detail
    assert "le-connection-abort-by-remote" in detail
    # Nothing is left holding the station's only client slot, including a
    # session that opened before the scan refused to stop.
    opened = operation in {"start_notify", "scan_stop"}
    assert radio.closed == ([SERIAL] if opened else [])


async def test_a_bluez_refusal_during_reattach_is_recorded_and_retried_in_process(
        tmp_path, monkeypatch):
    """The reattach is where BlueZ refuses connections, and that ended the process.

    The collector exited with a traceback, its recording closed as an ordinary
    stop, and why the gap went on survived only in the add-on log.
    """
    stop = tmp_path / "stop"
    radio = FakeBluetooth(monkeypatch, SERIAL,
                          connect_errors=[None, BleakDBusError(*REFUSED), None])

    def answer(session, write):
        if session == 0:
            # The first read's time sync and status query; then the link drops.
            return write <= 2
        stop.touch()
        return True

    radio.answer = answer
    with Store(tmp_path / "jackery.sqlite", reserve_bytes=0) as store:
        await _jackery_ble_loop(store, interval=0.01, hours=None, serial=SERIAL, stop_file=stop)
        run = recording(store)

    assert radio.connects == [SERIAL] * 3, "the refused attach was not retried in-process"
    assert run.frames == 2
    failures = [detail for kind, detail in run.events if kind == "connection_failed"]
    assert len(failures) == 1, run.events
    assert "operation=connect" in failures[0]
    assert "exception=BleakDBusError" in failures[0]
    assert "le-connection-abort-by-remote" in failures[0]
    assert run.statuses == ["stopped"]


class ScriptedSession:
    """An open Jackery session whose reads follow a script.

    A dict answers, an exception is raised, and "stop" answers after touching
    the stop file. ``then`` repeats once the script runs out.
    """

    identity = SimpleNamespace(serial=SERIAL)

    def __init__(self, stop, *script, then=None):
        self.stop, self.script, self.then = stop, list(script), then
        self.reads = self.closes = 0

    async def read(self, timeout):
        self.reads += 1
        step = self.script.pop(0) if self.script else self.then
        if step is None:
            raise AssertionError("the collector read past the end of the script")
        if isinstance(step, BaseException):
            raise step
        if step == "stop":
            self.stop.touch()
            step = OK
        return dict(step)

    async def close(self):
        self.closes += 1
        return []


async def record_sessions(tmp_path, monkeypatch, *sessions, interval=0.01, outage=0.0):
    """Record over scripted sessions, one per attach; each reattach takes ``outage`` seconds."""
    queue = list(sessions)

    async def discover(*_, **__):
        if not queue:
            raise AssertionError("the collector reattached more often than scripted")
        if len(queue) < len(sessions):
            await asyncio.sleep(outage)
        return queue.pop(0)

    monkeypatch.setattr("openpowerstation.jackery.discover_reader", discover)
    with Store(tmp_path / "jackery.sqlite", reserve_bytes=0) as store:
        try:
            await _jackery_ble_loop(store, interval=interval, hours=None, serial=SERIAL,
                                    stop_file=tmp_path / "stop")
        finally:
            run = recording(store)
    run.attaches = len(sessions) - len(queue)
    return run


async def test_one_unanswered_jackery_poll_keeps_the_session(tmp_path, monkeypatch):
    """A missed reply costs one poll, not the session.

    Reattaching means waiting for an advertising window the station may not
    open again for hours, so one quiet poll on a link that raised nothing used
    to buy a full rediscovery.
    """
    session = ScriptedSession(tmp_path / "stop", OK, StationSilent(SILENT), "stop")
    run = await record_sessions(tmp_path, monkeypatch, session)

    assert run.attaches == 1, "one silent poll tore the session down"
    assert session.reads == 3
    assert session.closes == 1, "closed before the recording stopped"
    assert "disconnected" not in run.kinds, run.events
    assert run.kinds.count("silence") == 1
    assert run.frames == 2


async def test_a_jackery_station_that_stays_silent_is_reattached_at_the_limit(
        tmp_path, monkeypatch):
    stop = tmp_path / "stop"
    quiet = ScriptedSession(stop, OK, then=StationSilent(SILENT))
    fresh = ScriptedSession(stop, "stop")
    run = await record_sessions(tmp_path, monkeypatch, quiet, fresh)

    assert run.attaches == 2
    assert quiet.reads == 1 + UNPRODUCTIVE_POLL_LIMIT
    assert quiet.closes == 1
    # Once for the silent stretch, the way the EcoFlow collector reports it.
    assert run.kinds.count("silence") == 1, run.events
    drops = [detail for kind, detail in run.events if kind == "disconnected"]
    assert len(drops) == 1, run.events
    assert (f"unproductive for {UNPRODUCTIVE_POLL_LIMIT} polls in a row "
            f"({UNPRODUCTIVE_POLL_LIMIT} unanswered, 0 without core telemetry)") in drops[0]
    assert "transport_error=none_observed" in drops[0]
    assert run.frames == 2


async def test_unanswered_and_unmapped_polls_share_one_limit(tmp_path, monkeypatch):
    """Both are a session producing nothing, and one limit governs them.

    Counted apart, a station mixing the two would get up to twice the grace
    before anything was done about it.
    """
    stop = tmp_path / "stop"
    silent = StationSilent(SILENT)
    mixed = ScriptedSession(stop, OK, silent, UNMAPPED, silent, UNMAPPED, silent, then=silent)
    fresh = ScriptedSession(stop, "stop")
    run = await record_sessions(tmp_path, monkeypatch, mixed, fresh)

    assert run.attaches == 2
    assert mixed.reads == 1 + UNPRODUCTIVE_POLL_LIMIT
    drops = [detail for kind, detail in run.events if kind == "disconnected"]
    assert len(drops) == 1, run.events
    assert "(3 unanswered, 2 without core telemetry)" in drops[0]
    assert run.kinds.count("suspect_telemetry") == 2
    assert run.kinds.count("silence") == 1


async def test_an_answered_poll_resets_the_unproductive_count(tmp_path, monkeypatch):
    silent = StationSilent(SILENT)
    below = [silent] * (UNPRODUCTIVE_POLL_LIMIT - 1)
    session = ScriptedSession(tmp_path / "stop", OK, *below, OK, *below, "stop")
    run = await record_sessions(tmp_path, monkeypatch, session)

    assert run.attaches == 1
    assert "disconnected" not in run.kinds, run.events
    # A new silent stretch after a good reading is reported again.
    assert run.kinds.count("silence") == 2
    assert run.frames == 3


async def test_a_collector_that_fails_unexpectedly_records_an_error_not_a_stop(
        tmp_path, monkeypatch):
    """A crash is not an operator stop, and the recording must not say it was."""
    session = ScriptedSession(tmp_path / "stop", OK, RuntimeError("unclassified failure"))

    with pytest.raises(RuntimeError):
        await record_sessions(tmp_path, monkeypatch, session)

    with read_db(tmp_path / "jackery.sqlite") as database:
        statuses = [row[0] for row in database.execute("SELECT status FROM sessions")]
    assert statuses == ["error"]


class TimedSession(ScriptedSession):
    """Stamps each read as it starts; ``slow`` maps a read number to its duration."""

    def __init__(self, stop, *script, stamps, slow=None):
        super().__init__(stop, *script)
        self.stamps, self.slow = stamps, slow or {}

    async def read(self, timeout):
        self.stamps.append(time.monotonic())
        await asyncio.sleep(self.slow.get(len(self.stamps), 0))
        return await super().read(timeout)


async def test_a_reattach_does_not_replay_the_polls_it_missed(tmp_path, monkeypatch):
    """The station gets its normal cadence back, not the backlog.

    The poll deadline survived the outage, so every slot that passed while the
    link was down fired back to back once it returned: about a hundred queries
    after a five-minute gap, on the link least able to take them.
    """
    interval, stop, stamps = 0.25, tmp_path / "stop", []
    lost = ScriptedSession(stop, OK, ConnectionError("operation=write:status"))
    back = TimedSession(stop, OK, OK, OK, "stop", stamps=stamps)
    await record_sessions(tmp_path, monkeypatch, lost, back,
                          interval=interval, outage=4 * interval)

    gaps = [later - earlier for earlier, later in zip(stamps, stamps[1:])]
    assert len(gaps) == 3
    assert min(gaps) >= 0.8 * interval, gaps


async def test_a_slow_poll_is_not_followed_by_a_burst(tmp_path, monkeypatch):
    """A deadline missed on an open session is skipped, not made up."""
    interval, stop, stamps = 0.2, tmp_path / "stop", []
    session = TimedSession(stop, OK, OK, OK, OK, OK, "stop", stamps=stamps,
                           slow={2: 5 * interval})
    await record_sessions(tmp_path, monkeypatch, session, interval=interval)

    gaps = [later - earlier for earlier, later in zip(stamps, stamps[1:])]
    assert len(gaps) == 5
    assert min(gaps) >= 0.8 * interval, gaps


# What a search, a partial reply and an ordinary poll leave behind: in the
# recording, in the bridge's collector_state, and in the add-on log.

SETTINGS = {"sltb": 1, "ec": 0, "pmb": 0}
NOT_ADVERTISING = f"No usable advertisement from Jackery {SERIAL} within 60s."
REFUSED_CONNECT = ("backend=Bleak/linux; operation=connect; "
                   "exception=BleakDBusError: org.bluez.Error.Failed")


async def record_attempts(tmp_path, monkeypatch, *attempts, interval=0.01):
    """Record through scripted attach attempts: an exception fails one, a session opens."""
    queue = list(attempts)

    async def discover(*_, **__):
        if not queue:
            raise AssertionError("the collector tried to attach more often than scripted")
        step = queue.pop(0)
        if isinstance(step, BaseException):
            raise step
        return step

    monkeypatch.setattr("openpowerstation.jackery.discover_reader", discover)
    with Store(tmp_path / "jackery.sqlite", reserve_bytes=0) as store:
        await _jackery_ble_loop(store, interval=interval, hours=None, serial=SERIAL,
                                stop_file=tmp_path / "stop")
        return recording(store)


class Published:
    """The /state payloads a bridge would have sent."""

    def __init__(self):
        self.states = []

    def publish(self, topic, payload=None, **_):
        if topic.endswith("/state"):
            self.states.append(json.loads(payload))


async def test_a_settings_only_reply_is_an_unproductive_poll(tmp_path, monkeypatch):
    """Health follows the core telemetry, not whichever field the station mapped.

    A station answering only settings fields reset the unproductive count on
    every poll, so its session was never reattached while SOC and power aged
    out, and nothing was recorded about it.
    """
    stop = tmp_path / "stop"
    partial = ScriptedSession(stop, OK, *[SETTINGS] * UNPRODUCTIVE_POLL_LIMIT, "stop")
    fresh = ScriptedSession(stop, "stop")
    run = await record_sessions(tmp_path, monkeypatch, partial, fresh)

    assert run.attaches == 2, "a session answering only settings was never reattached"
    assert partial.reads == 1 + UNPRODUCTIVE_POLL_LIMIT
    drops = [detail for kind, detail in run.events if kind == "disconnected"]
    assert len(drops) == 1, run.events
    assert (f"(0 unanswered, {UNPRODUCTIVE_POLL_LIMIT} without core telemetry)"
            in drops[0]), drops
    assert run.kinds.count("suspect_telemetry") == UNPRODUCTIVE_POLL_LIMIT
    # Still kept: the settings they carried are real observations.
    assert run.frames == 2 + UNPRODUCTIVE_POLL_LIMIT


async def test_a_reply_that_maps_to_nothing_is_kept_and_pinned(tmp_path, monkeypatch):
    """The reply that explains an anomaly is the one worth keeping.

    Unmapped replies were discarded and only an unpinned text event survived,
    which thinning could strip of its context; one arriving before the first
    reading left nothing at all, because no recording existed yet.
    """
    session = ScriptedSession(tmp_path / "stop", UNMAPPED, OK, UNMAPPED, "stop")
    run = await record_sessions(tmp_path, monkeypatch, session)

    with read_db(tmp_path / "jackery.sqlite") as db:
        kept = [json.loads(row[0])["properties"] for row in db.execute(
            "SELECT decoded FROM frames f WHERE NOT EXISTS "
            "(SELECT 1 FROM measurements m WHERE m.frame_id=f.id) ORDER BY id")]
        pinned = [reason["kind"] for row in db.execute("SELECT reasons FROM incidents")
                  for reason in json.loads(row[0])]
    assert kept == [UNMAPPED, UNMAPPED], "an unmapped reply was not kept as it arrived"
    assert run.frames == 4
    assert run.kinds.count("suspect_telemetry") == 2
    # Pinned, so the downsampler never thins them or the window around them.
    assert pinned.count("suspect_telemetry") == 2, pinned


async def test_a_search_after_a_restart_is_waiting_not_stopped(tmp_path, monkeypatch):
    """A collector looking for an absent Explorer is alive, and says so.

    Its recording used to open only with the first reading. Restarted while
    the Explorer was away -- after every lease expiry -- it left nothing in the
    database for hours, and the bridge went on publishing the finished session
    before it as "stopped": a deliberate stop, a crash and a live search all
    read the same.
    """
    path, stop = tmp_path / "jackery.sqlite", tmp_path / "stop"
    with Store(path, reserve_bytes=0) as store:
        earlier = Recorder(store, firmware=f"Jackery Explorer 1000 v2 {SERIAL}")
        fields = {"device": {"serial": SERIAL}, "properties": OK}
        earlier.ingest_observation(json.dumps(fields).encode(), fields, map_properties(OK))
        earlier.finish("stopped")
    searches = []

    async def absent(*_, **__):
        searches.append(1)
        await asyncio.sleep(0)
        raise StationNotFound(NOT_ADVERTISING)

    monkeypatch.setattr("openpowerstation.jackery.discover_reader", absent)
    client = Published()
    bridge = JackeryBridge(Config("AA:BB:CC:DD:EE:FF", "MR51ABCDEFGHIJKL", "0",
                                  mqtt_host="broker.invalid"),
                           path, serial=SERIAL, client=client)
    with Store(path, reserve_bytes=0) as store:
        collector = asyncio.create_task(_jackery_ble_loop(
            store, interval=0.01, hours=None, serial=SERIAL, stop_file=stop))
        try:
            deadline = time.monotonic() + 5
            while len(searches) < 5 and time.monotonic() < deadline:
                await asyncio.sleep(0.01)
            assert len(searches) >= 5, "the collector never searched"
            bridge.publish_once()
        finally:
            stop.touch()
            await asyncio.wait_for(collector, 5)
        run = recording(store)

    assert client.states[-1]["collector_state"] == "waiting", client.states[-1]
    assert client.states[-1]["bms_batt_soc"] is None
    assert run.statuses == ["stopped", "stopped"], "the search left no recording of its own"
    failures = [detail for kind, detail in run.events if kind == "connection_failed"]
    # Recorded, but once: an absent Explorer is searched for about once a minute.
    assert len(failures) == 1, run.events
    assert "exception=StationNotFound" in failures[0]


async def test_a_long_search_is_recorded_once_per_kind_of_failure(tmp_path, monkeypatch, capsys):
    """Each new reason is recorded at once; repeats wait for the next summary.

    The attach that finally succeeds says what the search before it cost, so a
    gap can be told apart: the Explorer away, BlueZ refusing, or the lease.
    """
    absent = StationNotFound(NOT_ADVERTISING)
    run = await record_attempts(
        tmp_path, monkeypatch, absent, absent, absent, ConnectionError(REFUSED_CONNECT),
        absent, absent, ScriptedSession(tmp_path / "stop", "stop"))

    failures = [detail for kind, detail in run.events if kind == "connection_failed"]
    assert len(failures) == 2, run.events
    assert "exception=StationNotFound" in failures[0]
    assert "exception=ConnectionError" in failures[1]
    assert "operation=connect" in failures[1]
    attached = [detail for kind, detail in run.events if kind == "connected"]
    assert len(attached) == 1, run.events
    assert "after 6 failed attempts" in attached[0]
    assert "StationNotFound=5" in attached[0] and "ConnectionError=1" in attached[0]
    log = capsys.readouterr().out
    assert log.count("Explorer not reachable") == 2, log
    assert SERIAL not in log, "the serial reached the add-on log"


async def test_a_search_is_summarised_with_its_running_count(tmp_path, monkeypatch):
    monkeypatch.setattr("openpowerstation.cli.ATTACH_REPORT_SECONDS", 0)
    absent = StationNotFound(NOT_ADVERTISING)
    run = await record_attempts(tmp_path, monkeypatch, absent, absent, absent,
                                ScriptedSession(tmp_path / "stop", "stop"))

    failures = [detail for kind, detail in run.events if kind == "connection_failed"]
    assert len(failures) == 3, run.events
    for count, detail in enumerate(failures, 1):
        assert f"{count} failed attempt" in detail, detail


async def test_the_jackery_log_names_changes_not_readings(tmp_path, monkeypatch, capsys):
    """One line per change, as the bridges log, and never the serial.

    Every poll printed its whole reading with the serial: 97.6% of the add-on
    log, whose default 100-line view then held five minutes, so the few lines
    that explain an outage were gone within minutes of it.
    """
    session = ScriptedSession(tmp_path / "stop", *[OK] * 8, "stop")
    run = await record_sessions(tmp_path, monkeypatch, session)

    log = capsys.readouterr().out.splitlines()
    assert run.frames == 9
    assert SERIAL not in "\n".join(log), "the serial reached the add-on log"
    # The attach, and the telemetry it brought; nothing per poll.
    assert len(log) == 2, log
    assert device_id(SERIAL) in log[0]


def test_stopping_the_jackery_collector_is_not_a_crash(tmp_path):
    """The supervisor stops a collector with SIGINT, on every lease restart and app stop.

    asyncio.run turns that SIGINT into a cancelled task and then re-raises
    KeyboardInterrupt, which nothing caught: every ordinary stop ended in a
    traceback in the add-on log, indistinguishable from a crash.
    """
    source = Path(openpowerstation.__file__).resolve().parents[1]
    script = textwrap.dedent(f"""
        import signal
        import sys
        from types import SimpleNamespace
        sys.path.insert(0, {str(source)!r})
        from openpowerstation import cli, jackery

        class Reader:
            identity = SimpleNamespace(serial={SERIAL!r})
            reads = 0

            async def read(self, timeout):
                Reader.reads += 1
                if Reader.reads == 2:
                    signal.raise_signal(signal.SIGINT)
                return {OK!r}

            async def close(self):
                return []

        async def discover(*_, **__):
            return Reader()

        jackery.discover_reader = discover
        sys.exit(cli.main(["--data-dir", sys.argv[1], "jackery-record",
                           "--serial", {SERIAL!r}, "--interval", "1"]))
    """)
    done = subprocess.run([sys.executable, "-c", script, str(tmp_path)],
                          capture_output=True, text=True, timeout=60)

    assert "Traceback" not in done.stderr, done.stderr
    assert done.returncode == 0, (done.returncode, done.stdout, done.stderr)
    with read_db(tmp_path / "jackery.sqlite") as db:
        assert [row[0] for row in db.execute("SELECT status FROM sessions")] == ["stopped"]
