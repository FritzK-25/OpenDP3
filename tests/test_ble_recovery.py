"""Recovery from BLE sessions that stay alive after telemetry has stopped.

Every hang here is modelled with an operation that never returns, because that
is the failure the collectors actually hit: BlueZ keeps the link Connected, no
exception is ever raised, and the worker stays alive with nothing arriving. A
test that raises immediately would pass against the unfixed code.
"""
import asyncio
import time
from types import SimpleNamespace

import pytest

from openpowerstation import ble
from openpowerstation import radio
from openpowerstation.ble import Session
from openpowerstation.cli import _jackery_ble_loop
from openpowerstation.config import Config
from openpowerstation.jackery import Identity as JackeryIdentity
from openpowerstation.jackery import LocalReader
from openpowerstation.protocol import Identity
from openpowerstation.runtime import Service
from openpowerstation.storage import Store, read_db


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
    assert count >= 1
    assert not service.is_alive()


def test_adapter_selection_is_explicit_and_scopes_the_radio_lock(tmp_path, monkeypatch):
    monkeypatch.setenv(radio.ADAPTER_ENV, "hci1")
    assert radio.configured_adapter() == "hci1"
    assert radio.radio_lock_path(tmp_path).name == "radio-hci1.lock"
    monkeypatch.setattr(radio.sys, "platform", "linux")
    assert radio.bleak_adapter_kwargs() == {"adapter": "hci1"}
    monkeypatch.setenv(radio.ADAPTER_ENV, "not-an-adapter")
    with pytest.raises(ValueError, match="expected hci"):
        radio.configured_adapter()


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

    with pytest.raises(TimeoutError) as failure:
        await jackery_reader(WorkingBleak()).read(timeout=0.01)

    detail = str(failure.value)
    assert "backend=Bleak/" in detail
    assert "operation=notification_wait" in detail
    assert "exception=TimeoutError" in detail
    assert "transport_error=none_observed" in detail


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

    async def discover_reader(_timeout, _serial=None):
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
