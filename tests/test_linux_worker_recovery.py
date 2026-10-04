"""Regression coverage for HAOS BlueZ orphan recovery."""
import asyncio
import importlib.util
from pathlib import Path
import sys
from types import ModuleType, SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

ROOT = Path(__file__).resolve().parents[1]
APP = ROOT / "home-assistant" / "opendp3"


def load_module(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


run = load_module("app_run_bluez_test", APP / "run.py")
worker = load_module("linux_worker_bluez_test", APP / "linux_worker.py")
SERIAL = "123456789012345"
OTHER_SERIAL = "987654321098765"


def bluez_modules():
    return {
        "dbus_fast": SimpleNamespace(
            BusType=SimpleNamespace(SYSTEM=0),
            Message=lambda **kwargs: SimpleNamespace(**kwargs),
            MessageType=SimpleNamespace(ERROR="error"),
        ),
        "dbus_fast.aio": SimpleNamespace(MessageBus=None),
    }


def device_props(*, address, connected, name=None):
    values = {
        "Address": SimpleNamespace(value=address),
        "Connected": SimpleNamespace(value=connected),
    }
    if name is not None:
        values["Name"] = SimpleNamespace(value=name)
    return {"org.bluez.Device1": values}


def fake_worker_modules(reader, cli_calls=2):
    fake_package = ModuleType("openpowerstation")
    fake_ble = ModuleType("openpowerstation.ble")
    fake_jackery = ModuleType("openpowerstation.jackery")
    fake_cli = ModuleType("openpowerstation.cli")
    fake_config = ModuleType("openpowerstation.config")

    original_discover = AsyncMock(return_value=reader)
    fake_jackery.discover_reader = original_discover
    fake_package.ble = fake_ble
    fake_package.jackery = fake_jackery
    fake_config.load_config = MagicMock()

    def cli_main(args):
        assert args[-3:] == ["jackery-record", "--serial", SERIAL]
        for _ in range(cli_calls):
            discovered = asyncio.run(fake_jackery.discover_reader(60))
            assert discovered is reader
        return 0

    fake_cli.main = cli_main
    return {
        "openpowerstation": fake_package,
        "openpowerstation.ble": fake_ble,
        "openpowerstation.jackery": fake_jackery,
        "openpowerstation.cli": fake_cli,
        "openpowerstation.config": fake_config,
    }, original_discover


def test_jackery_collector_runs_through_recovery_worker():
    jobs = run.commands(
        {"ecoflow_enabled": True, "jackery_enabled": True, "jackery_serial": SERIAL},
        Path("/data"),
    )
    command = jobs["jackery-collector"]
    assert "/opt/opendp3/linux_worker.py" in command
    assert command[-3:] == ["jackery-record", "--serial", SERIAL]


def test_verified_jackery_address_round_trips_by_serial(tmp_path):
    state = tmp_path / worker.JACKERY_ADDRESS_STATE
    worker.save_known_jackery_address(state, SERIAL, "11:22:33:44:55:66")
    assert worker.load_known_jackery_address(state, SERIAL) == "11:22:33:44:55:66"
    assert worker.load_known_jackery_address(state, OTHER_SERIAL) is None


@pytest.mark.asyncio
async def test_serial_verified_jackery_candidate_without_name_is_released_before_scan():
    bus = MagicMock()
    bus.connect = AsyncMock(return_value=bus)
    bus.call = AsyncMock(side_effect=[
        SimpleNamespace(message_type="ok", body=[{
            "/org/bluez/hci0/ecoflow": device_props(
                address="AA:BB:CC:DD:EE:FF", connected=True, name="DELTA Pro 3"
            ),
            "/org/bluez/hci0/jackery": device_props(
                address="11:22:33:44:55:66", connected=True
            ),
        }]),
        SimpleNamespace(message_type="ok"),
    ])

    def resolve(values):
        return SERIAL if values.get("Address") == "11:22:33:44:55:66" else None

    with patch.dict(sys.modules, bluez_modules()), patch.object(
        worker.asyncio, "sleep", new=AsyncMock()
    ):
        await worker.disconnect_jackery_orphan(
            SERIAL,
            serial_resolver=resolve,
            bus_factory=lambda: bus,
        )

    assert bus.call.await_count == 2
    disconnect = bus.call.await_args_list[1].args[0]
    assert disconnect.member == "Disconnect"
    assert disconnect.path == "/org/bluez/hci0/jackery"
    bus.disconnect.assert_called_once()


@pytest.mark.asyncio
async def test_different_connected_jackery_is_not_disconnected():
    bus = MagicMock()
    bus.connect = AsyncMock(return_value=bus)
    bus.call = AsyncMock(return_value=SimpleNamespace(message_type="ok", body=[{
        "/org/bluez/hci0/other": device_props(
            address="11:22:33:44:55:66", connected=True, name="HT-Other"
        ),
    }]))

    with patch.dict(sys.modules, bluez_modules()):
        await worker.disconnect_jackery_orphan(
            SERIAL,
            serial_resolver=lambda values: OTHER_SERIAL,
            bus_factory=lambda: bus,
        )

    assert bus.call.await_count == 1
    bus.disconnect.assert_called_once()


@pytest.mark.asyncio
async def test_previously_verified_address_can_recover_without_cached_advertisement():
    bus = MagicMock()
    bus.connect = AsyncMock(return_value=bus)
    bus.call = AsyncMock(side_effect=[
        SimpleNamespace(message_type="ok", body=[{
            "/org/bluez/hci0/jackery": device_props(
                address="11:22:33:44:55:66", connected=True, name="HT-Explorer"
            ),
        }]),
        SimpleNamespace(message_type="ok"),
    ])

    with patch.dict(sys.modules, bluez_modules()), patch.object(
        worker.asyncio, "sleep", new=AsyncMock()
    ):
        await worker.disconnect_jackery_orphan(
            SERIAL,
            known_address="11:22:33:44:55:66",
            serial_resolver=lambda values: None,
            bus_factory=lambda: bus,
        )

    assert bus.call.await_count == 2
    assert bus.call.await_args_list[1].args[0].path == "/org/bluez/hci0/jackery"


@pytest.mark.asyncio
async def test_multiple_verified_jackery_candidates_fail_closed():
    bus = MagicMock()
    bus.connect = AsyncMock(return_value=bus)
    bus.call = AsyncMock(return_value=SimpleNamespace(message_type="ok", body=[{
        "/org/bluez/hci0/jackery1": device_props(
            address="11:22:33:44:55:66", connected=True, name="HT-One"
        ),
        "/org/bluez/hci0/jackery2": device_props(
            address="22:33:44:55:66:77", connected=True, name="Jackery Two"
        ),
    }]))

    with patch.dict(sys.modules, bluez_modules()):
        with pytest.raises(ConnectionError, match="Multiple connected Jackery"):
            await worker.disconnect_jackery_orphan(
                SERIAL,
                serial_resolver=lambda values: SERIAL,
                bus_factory=lambda: bus,
            )

    assert bus.call.await_count == 1
    bus.disconnect.assert_called_once()


def test_jackery_worker_recovers_before_every_discovery_attempt(tmp_path):
    reader = SimpleNamespace(identity=SimpleNamespace(
        serial=SERIAL, address="11:22:33:44:55:66"
    ))
    modules, original_discover = fake_worker_modules(reader, cli_calls=2)
    recovery = AsyncMock()
    argv = [
        "linux_worker.py", "--data-dir", str(tmp_path),
        "jackery-record", "--serial", SERIAL,
    ]
    with patch.dict(sys.modules, modules), patch.object(worker.sys, "argv", argv), patch.object(
        worker, "disconnect_jackery_orphan", new=recovery
    ):
        assert worker.main() == 0

    assert recovery.await_count == 2
    assert original_discover.await_count == 2
    state = tmp_path / worker.JACKERY_ADDRESS_STATE
    assert worker.load_known_jackery_address(state, SERIAL) == "11:22:33:44:55:66"


def test_wrong_discovered_serial_is_never_persisted_as_configured_station(tmp_path):
    reader = SimpleNamespace(identity=SimpleNamespace(
        serial=OTHER_SERIAL, address="22:33:44:55:66:77"
    ))
    modules, original_discover = fake_worker_modules(reader, cli_calls=1)
    recovery = AsyncMock()
    argv = [
        "linux_worker.py", "--data-dir", str(tmp_path),
        "jackery-record", "--serial", SERIAL,
    ]
    with patch.dict(sys.modules, modules), patch.object(worker.sys, "argv", argv), patch.object(
        worker, "disconnect_jackery_orphan", new=recovery
    ):
        assert worker.main() == 0

    assert recovery.await_count == 1
    assert original_discover.await_count == 1
    state = tmp_path / worker.JACKERY_ADDRESS_STATE
    assert not state.exists()


def test_address_persistence_failure_keeps_active_reader(tmp_path):
    reader = SimpleNamespace(
        identity=SimpleNamespace(serial=SERIAL, address="11:22:33:44:55:66"),
        close=AsyncMock(),
    )
    modules, original_discover = fake_worker_modules(reader, cli_calls=1)
    recovery = AsyncMock()
    argv = [
        "linux_worker.py", "--data-dir", str(tmp_path),
        "jackery-record", "--serial", SERIAL,
    ]
    with patch.dict(sys.modules, modules), patch.object(worker.sys, "argv", argv), patch.object(
        worker, "disconnect_jackery_orphan", new=recovery
    ), patch.object(
        worker, "save_known_jackery_address", side_effect=OSError("read-only filesystem")
    ):
        assert worker.main() == 0

    assert recovery.await_count == 1
    assert original_discover.await_count == 1
    reader.close.assert_not_awaited()


class FakeClock:
    def __init__(self):
        self.now = 0.0

    def monotonic(self):
        return self.now

    def monotonic_ns(self):
        return int(self.now * 1e9)


class ClockedStop:
    """A stop event whose waits advance the fake clock, ending after ``limit`` seconds."""

    def __init__(self, clock, limit):
        self.clock, self.limit = clock, limit

    def is_set(self):
        return self.clock.now >= self.limit

    def wait(self, seconds):
        self.clock.now += seconds
        return self.is_set()


class FakeProcess:
    def __init__(self, *_args, **_kwargs):
        self.returncode = None
        self.signals = 0

    def poll(self):
        return self.returncode

    def send_signal(self, _signal):
        self.signals += 1
        self.returncode = -2

    def wait(self, timeout=None):
        return self.returncode

    def kill(self):
        self.returncode = -9


def run_supervisor(monkeypatch, frames, *, limit, retry=5):
    """Supervise one collector for ``limit`` fake seconds; return its processes."""
    clock = FakeClock()
    spawned = []

    def popen(*args, **kwargs):
        spawned.append(FakeProcess())
        return spawned[-1]

    monkeypatch.setattr(run, "time", SimpleNamespace(monotonic=clock.monotonic,
                                                     monotonic_ns=clock.monotonic_ns))
    monkeypatch.setattr(run.subprocess, "Popen", popen)
    monkeypatch.setattr(run, "newest_frame", lambda _database: frames(clock.now))
    policy = run.FreshnessPolicy(Path("recordings.sqlite"), 30.0, "hci0")
    run.supervise({"ecoflow-collector": ["collector"]}, ClockedStop(clock, limit),
                  stagger=0, retry=retry, grace=0, policies={"ecoflow-collector": policy},
                  freshness_check=1, recovery_spacing=0, recovery_grace=0)
    return spawned


def test_a_collector_that_never_records_is_restarted_once(monkeypatch):
    spawned = run_supervisor(monkeypatch, lambda _now: None, limit=200)

    # The first process is stopped by the start-up lease; its replacement,
    # which also records nothing (a switched-off device looks the same), is
    # left alone instead of being restarted every lease interval.
    assert len(spawned) == 2
    assert spawned[0].signals == 1
    assert spawned[1].signals == 1  # only the final shutdown


def test_a_collector_that_stops_recording_is_restarted(monkeypatch):
    def frames(now):
        # Valid frames for the first minute, then silence.
        last = min(now, 60.0)
        return int(last), int(last * 1e9)

    spawned = run_supervisor(monkeypatch, frames, limit=120)

    assert spawned[0].signals == 1
    assert len(spawned) == 2


def test_a_recording_collector_is_not_restarted(monkeypatch):
    spawned = run_supervisor(monkeypatch, lambda now: (int(now), int(now * 1e9)), limit=200)

    assert len(spawned) == 1


@pytest.mark.parametrize("options", [
    {},
    {"ecoflow_enabled": True},
    {"ecoflow_enabled": False, "jackery_enabled": True, "jackery_serial": "123456789012345"},
    {"ecoflow_enabled": True, "jackery_enabled": True, "jackery_serial": "123456789012345"},
])
def test_freshness_policies_cover_exactly_the_started_collectors(options):
    collectors = {name for name in run.commands(options, Path("/data"))
                  if name.endswith("-collector")}
    assert set(run.freshness_policies(options, Path("/data"))) == collectors
