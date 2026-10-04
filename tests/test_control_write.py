"""A DP3 control write that fails must leave a record and must not end capture.

The service loop awaits the write inline. Before the single GATT write site
classified its failures, anything outside the four exception types the loop
caught -- the EOFError dbus_fast hands a call pending on a closed bus, an
AssertionError from Bleak's own ``assert self._bus`` -- escaped the loop. The
collector then stopped with 'Collection stopped (<type>)', the recording ended
as 'error', and the command had no record at all.

Only the Bluetooth client is faked here. The session, its outbound gate, the
command path and the recorder are the real ones.
"""
import asyncio
import time
from types import SimpleNamespace

from bleak.exc import BleakError
import pytest

from openpowerstation import ble
from openpowerstation.config import Config, save_config
from openpowerstation.protocol import Identity, ProtocolError
from openpowerstation.runtime import Service
from openpowerstation.storage import read_db
from openpowerstation.vendor.frame_assembler import PassthroughAssembler

SERIAL = "MR51123456789012"


def settings():
    return Config("AA:BB:CC:DD:EE:FF", SERIAL, "123456", mqtt_host="broker.invalid",
                  allow_control=True)


def wait_for(predicate, timeout=5.0):
    deadline = time.monotonic() + timeout
    while not predicate():
        assert time.monotonic() < deadline, "Timed out"
        time.sleep(.02)


def authenticated(session, client):
    """Give ``session`` the state Session.authenticate() leaves it in."""
    session.client = client
    session.write_char = SimpleNamespace(properties=["write"], max_write_without_response_size=20)
    session.codec = PassthroughAssembler(None)
    session.authenticated = True
    session.gate.stage = "closed"
    return session


def new_session(client):
    session = ble.Session(Identity("AA:BB:CC:DD:EE:FF", SERIAL, 0, 0x13), object(), "123456",
                          lambda *_: None, lambda *_: None, allow_control=True)
    return authenticated(session, client)


class FailingClient:
    def __init__(self, failure):
        self.failure, self.writes = failure, 0

    async def write_gatt_char(self, char, data, response):
        self.writes += 1
        raise self.failure


def record_control(tmp_path, monkeypatch, client, *, authenticate=True):
    """Queue one guarded LV-off in a recording collector; return its outcome.

    Returns the service, once it has recorded the command and been stopped,
    and the session's final status and events.
    """
    config = settings()
    save_config(config, tmp_path / "config.json")

    class Session(ble.Session):
        async def run(self):
            if authenticate:
                authenticated(self, client)
            self.on_event("connected", "test", time.time_ns(), time.monotonic_ns())
            await asyncio.Event().wait()

    async def scan(*_):
        return [(Identity(config.address, config.serial, 0, 0x13), object())]

    def recorded():
        with read_db(service.database) as db:
            return db.execute(
                "SELECT COUNT(*) FROM events WHERE kind LIKE 'control%'").fetchone()[0]

    monkeypatch.setattr("openpowerstation.runtime.ble.scan", scan)
    monkeypatch.setattr("openpowerstation.runtime.ble.Session", Session)
    service = Service(config, tmp_path / "recordings.sqlite", config_path=tmp_path / "config.json",
                      lock_dir=tmp_path / "locks")
    service.start()
    try:
        wait_for(lambda: service.state == "recording")
        service.command("control", "cfg_lv_ac_out_open=off")
        # The collector's own verdict, if it dies over the write, lands first.
        wait_for(lambda: not service.is_alive() or recorded())
        alive = service.is_alive()
    finally:
        service.stop()
        service.join(5)
    with read_db(service.database) as db:
        status = db.execute("SELECT status FROM sessions").fetchone()[0]
        events = [tuple(row) for row in db.execute(
            "SELECT kind, detail FROM events WHERE kind LIKE 'control%' ORDER BY id")]
    return alive, service, status, events


@pytest.mark.parametrize("failure", [
    EOFError(),
    AssertionError(),
    AttributeError("'NoneType' object has no attribute 'error_name'"),
    OSError("D-Bus transport closed"),
    RuntimeError("unexpected backend state"),
    BrokenPipeError(),
    BleakError("org.bluez.Error.Failed"),
])
def test_a_failed_control_write_is_recorded_unverified_and_capture_continues(
        tmp_path, monkeypatch, failure):
    """The write may have reached the DP3 before it failed, so the outcome is unknown.

    Recording it as refused would claim nothing changed; recording nothing lost
    the one command most worth an audit trail. Either way the collector keeps
    recording: a write is not a reason to give up the session.
    """
    client = FailingClient(failure)
    alive, service, status, events = record_control(tmp_path, monkeypatch, client)
    assert client.writes == 1
    assert alive, f"the collector stopped over a control write: {service.error!r}"
    assert not service.error
    assert status == "stopped"
    [(kind, detail)] = events
    assert kind == "control_unverified"
    assert detail.startswith("cfg_lv_ac_out_open=off: outcome unknown; ")
    assert "operation=write" in detail
    assert f"exception={type(failure).__name__}" in detail


def test_a_control_refused_before_the_radio_is_still_recorded_as_refused(tmp_path, monkeypatch):
    """No authenticated session means nothing was written: that is a refusal."""
    client = FailingClient(EOFError())
    alive, service, status, events = record_control(tmp_path, monkeypatch, client,
                                                    authenticate=False)
    assert client.writes == 0
    assert alive and not service.error
    assert events == [("control_refused",
                       "cfg_lv_ac_out_open=off: No authenticated session for control.")]


async def test_every_write_failure_reaches_the_caller_as_a_connection_error():
    """One exception family out of the write site, naming the backend's own failure."""
    with pytest.raises(ConnectionError) as failure:
        await new_session(FailingClient(EOFError())).send_control("cfg_lv_ac_out_open", False)
    assert str(failure.value) == "backend=Bleak; operation=write; exception=EOFError"


async def test_a_refusal_at_the_gate_is_not_mistaken_for_a_write_failure():
    session = new_session(FailingClient(EOFError()))
    session.gate.stage = "login"
    with pytest.raises(Exception) as failure:
        await session.send_control("cfg_lv_ac_out_open", False)
    assert not isinstance(failure.value, ConnectionError)
    assert session.client.writes == 0


async def test_a_dp3_write_that_never_returns_is_bounded(monkeypatch):
    """A write the backend never answers ends at the write deadline, named as such.

    The service loop awaits control writes inline, so an unanswered one used to
    hold the loop -- stop requests, reconnects and every other command -- for
    as long as the backend kept the call pending.
    """
    cancelled = asyncio.Event()

    class HangingClient:
        async def write_gatt_char(self, *_args, **_kwargs):
            try:
                await asyncio.Event().wait()
            finally:
                cancelled.set()

    monkeypatch.setattr(ble, "WRITE_TIMEOUT", 0.05, raising=False)
    with pytest.raises(TimeoutError) as failure:
        await asyncio.wait_for(
            new_session(HangingClient()).send_control("cfg_lv_ac_out_open", False), 2)
    assert cancelled.is_set()
    detail = str(failure.value)
    assert "backend=Bleak; operation=write; exception=TimeoutError" in detail
    assert "timeout=0.05s" in detail


def test_the_write_deadline_outlasts_bluez_own_att_timeout():
    """Cancelling a write BlueZ may still complete would make 'unknown' more common, not less."""
    assert ble.WRITE_TIMEOUT > 30.0


async def test_a_cancelled_write_stays_a_cancellation():
    """The radio lease and the session teardown cancel writes; that is not a failure."""
    started = asyncio.Event()

    class HangingClient:
        async def write_gatt_char(self, *_args, **_kwargs):
            started.set()
            await asyncio.Event().wait()

    task = asyncio.create_task(new_session(HangingClient()).send_control("cfg_lv_ac_out_open", False))
    await started.wait()
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task


async def test_an_authentication_write_failure_is_classified_too():
    """The same site carries the handshake, so its failures arrive the same way."""
    session = new_session(FailingClient(AssertionError()))
    session.authenticated = False
    session.gate.stage = "ecdh"
    with pytest.raises(ConnectionError, match="operation=write; exception=AssertionError"):
        await session._write(command=b"\x01\x00" + bytes(40))


def test_protocol_error_is_what_an_unauthenticated_control_raises():
    """Pins the pre-radio refusal the runtime relies on to tell refused from unverified."""
    session = ble.Session(Identity("AA:BB:CC:DD:EE:FF", SERIAL, 0, 0x13), object(), "123456",
                          lambda *_: None, lambda *_: None, allow_control=True)
    with pytest.raises(ProtocolError):
        asyncio.run(session.send_control("cfg_lv_ac_out_open", False))
