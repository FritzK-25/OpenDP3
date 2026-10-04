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


class AuthError(Exception):
    """As dbus-fast's own: the system bus refused the connection. Not an OSError."""


async def never_answers(*_, **__):
    await asyncio.Event().wait()


def failing_bluez(failure):
    """dbus-fast as linux_worker imports it, failing on the way to BlueZ."""
    if failure == "dbus-fast missing":
        return {"dbus_fast": None, "dbus_fast.aio": None}
    bus = MagicMock()
    bus.connect = AsyncMock(return_value=bus)
    if failure == "bus refused":
        bus.connect = AsyncMock(side_effect=AuthError("rejected"))
    elif failure == "bus never answers":
        bus.connect = never_answers
    elif failure == "BlueZ error reply":
        bus.call = AsyncMock(return_value=SimpleNamespace(message_type="error"))
    modules = bluez_modules()
    modules["dbus_fast.aio"] = SimpleNamespace(MessageBus=lambda **_: bus)
    return modules


def device_props(*, address, connected, name=None):
    values = {
        "Address": SimpleNamespace(value=address),
        "Connected": SimpleNamespace(value=connected),
    }
    if name is not None:
        values["Name"] = SimpleNamespace(value=name)
    return {"org.bluez.Device1": values}


def fake_worker_modules(reader, cli_calls=2, **discover_kwargs):
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

    def cli_main(args, *, discover=None):
        assert args[-3:] == ["jackery-record", "--serial", SERIAL]
        # As the real cli: the package's own discovery unless one is handed in.
        discover = discover or fake_jackery.discover_reader
        for _ in range(cli_calls):
            discovered = asyncio.run(discover(60, **discover_kwargs))
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
        released = await worker.disconnect_jackery_orphan(
            SERIAL,
            serial_resolver=resolve,
            bus_factory=lambda: bus,
        )

    assert released == 1
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
        released = await worker.disconnect_jackery_orphan(
            SERIAL,
            serial_resolver=lambda values: OTHER_SERIAL,
            bus_factory=lambda: bus,
        )

    assert released == 0
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


def test_ecoflow_worker_leaves_discovery_its_own_window(tmp_path):
    """The release goes before discovery; the discovery window stays ble.scan's own.

    The shared adapter lease's hold limit is budgeted against ble.SCAN_TIMEOUT.
    This wrapper hard-coded 8 seconds instead, so the window the lease is sized
    for and the window the add-on actually scanned could drift apart.
    """
    steps = []

    async def scan(*args, **kwargs):
        steps.append(("scan", args, kwargs))
        return []

    fake_package = ModuleType("openpowerstation")
    fake_ble = ModuleType("openpowerstation.ble")
    fake_cli = ModuleType("openpowerstation.cli")
    fake_config = ModuleType("openpowerstation.config")
    fake_ble.scan = scan
    fake_package.ble = fake_ble
    fake_config.load_config = MagicMock(
        return_value=SimpleNamespace(address="AA:BB:CC:DD:EE:FF"))

    def cli_main(args, *, scan=None):
        assert args[-1] == "record"
        scan = scan or fake_ble.scan
        # How runtime.connect() calls it: with no window of its own.
        assert asyncio.run(scan()) == []
        return 0

    fake_cli.main = cli_main
    modules = {
        "openpowerstation": fake_package,
        "openpowerstation.ble": fake_ble,
        "openpowerstation.cli": fake_cli,
        "openpowerstation.config": fake_config,
    }
    recovery = AsyncMock(side_effect=lambda address: steps.append(("release", address)))
    argv = ["linux_worker.py", "--data-dir", str(tmp_path), "record"]
    with patch.dict(sys.modules, modules), patch.object(worker.sys, "argv", argv), patch.object(
        worker, "disconnect_orphan", new=recovery
    ):
        assert worker.main() == 0

    assert steps == [("release", "AA:BB:CC:DD:EE:FF"), ("scan", (), {})]


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


@pytest.mark.parametrize("caller", [{}, {"serial": SERIAL}], ids=["bare", "cli"])
def test_jackery_worker_discovers_only_the_configured_explorer(tmp_path, caller):
    """The worker exists for one Explorer, so no other may be connected to.

    Discovery used to open whichever Explorer advertised first; with a
    neighbour's unit in range that took its single client slot and cost this
    station its advertising window. The CLI passes the serial itself, and the
    worker holds it even for a caller that does not.
    """
    reader = SimpleNamespace(identity=SimpleNamespace(
        serial=SERIAL, address="11:22:33:44:55:66"
    ))
    modules, original_discover = fake_worker_modules(reader, cli_calls=2, **caller)
    argv = [
        "linux_worker.py", "--data-dir", str(tmp_path),
        "jackery-record", "--serial", SERIAL,
    ]
    with patch.dict(sys.modules, modules), patch.object(worker.sys, "argv", argv), patch.object(
        worker, "disconnect_jackery_orphan", new=AsyncMock()
    ):
        assert worker.main() == 0

    assert original_discover.await_count == 2
    for call in original_discover.await_args_list:
        assert call.kwargs == {"serial": SERIAL}, call


def test_jackery_worker_releases_and_connects_under_the_collectors_lease(tmp_path):
    """The search runs outside the adapter lease; the BlueZ operations do not.

    The collector hands discovery its lease to take once its Explorer
    advertises. This wrapper passed discovery nothing but the serial, so in
    production the connect would have run with no lease at all.
    """
    steps = []

    class Lease:
        async def __aenter__(self):
            steps.append("lease")
            return self

        async def __aexit__(self, *_):
            steps.append("released")

    lease = Lease()
    reader = SimpleNamespace(identity=SimpleNamespace(
        serial=SERIAL, address="11:22:33:44:55:66"
    ))
    modules, original_discover = fake_worker_modules(
        reader, cli_calls=1, serial=SERIAL, lease=lease)
    recovery = AsyncMock(side_effect=lambda *_, **__: steps.append("orphan release"))
    argv = [
        "linux_worker.py", "--data-dir", str(tmp_path),
        "jackery-record", "--serial", SERIAL,
    ]
    with patch.dict(sys.modules, modules), patch.object(worker.sys, "argv", argv), patch.object(
        worker, "disconnect_jackery_orphan", new=recovery
    ):
        assert worker.main() == 0

    assert steps == ["lease", "orphan release", "released"]
    assert original_discover.await_args.kwargs == {"serial": SERIAL, "lease": lease}


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
    monkeypatch.setattr(run, "newest_frame", lambda _database, _keys=None: frames(clock.now))
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
def test_the_worker_hands_its_release_over_instead_of_patching_the_package(tmp_path):
    """Neither openpowerstation.ble nor openpowerstation.jackery is changed by the worker.

    It replaced ble.scan and jackery.discover_reader at runtime, which reached
    the collectors only while they looked those names up through the module
    at call time. Moving an import to the top of cli.py or runtime.py would
    have dropped the release without failing anything.
    """
    reader = SimpleNamespace(identity=SimpleNamespace(
        serial=SERIAL, address="11:22:33:44:55:66"
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
        assert modules["openpowerstation.jackery"].discover_reader is original_discover
    assert recovery.await_count == 1

    async def scan(*_, **__):
        return []

    modules["openpowerstation.ble"].scan = scan
    modules["openpowerstation.config"].load_config = MagicMock(
        return_value=SimpleNamespace(address="AA:BB:CC:DD:EE:FF"))
    scanned = []

    def record_main(args, *, scan=None):
        scanned.append(asyncio.run((scan or modules["openpowerstation.ble"].scan)()))
        return 0

    modules["openpowerstation.cli"].main = record_main
    recovery = AsyncMock()
    argv = ["linux_worker.py", "--data-dir", str(tmp_path), "record"]
    with patch.dict(sys.modules, modules), patch.object(worker.sys, "argv", argv), patch.object(
        worker, "disconnect_orphan", new=recovery
    ):
        assert worker.main() == 0
        assert modules["openpowerstation.ble"].scan is scan
    assert scanned == [[]]
    recovery.assert_awaited_once_with("AA:BB:CC:DD:EE:FF")


@pytest.fixture
def radio(monkeypatch):
    """Stand in for Bleak and WinRT where a test runs the real collectors.

    Any use is recorded, runs ``radio.on_use`` (which can end the run) and
    raises, so a regression that reaches past a test's fakes fails on
    ``radio.used`` instead of scanning or connecting on the machine.
    """
    from openpowerstation import ble, jackery

    state = SimpleNamespace(used=[], on_use=lambda: None)

    def forbid(what):
        state.used.append(what)
        state.on_use()
        raise RuntimeError(f"a test reached the Bluetooth radio ({what})")

    class Forbidden:
        def __init__(self, *_, **__):
            forbid("a Bleak scanner or client")

        @classmethod
        async def discover(cls, *_, **__):
            forbid("BleakScanner.discover")

    async def device_id(*_):
        forbid("a WinRT device lookup")

    for module in (ble, jackery):
        monkeypatch.setattr(module, "BleakScanner", Forbidden)
        monkeypatch.setattr(module, "BleakClient", Forbidden)
    monkeypatch.setattr(jackery, "_winrt_device_id", device_id)
    return state


def test_the_real_jackery_collector_releases_through_the_worker(tmp_path, monkeypatch, radio):
    """The worker's release reaches the real collector, through the real CLI."""
    from openpowerstation import jackery

    # What a run started without --stop-file stops on, as the app's is.
    stop = tmp_path / "jackery.stop"
    radio.on_use = lambda: stop.write_text("stop", encoding="ascii")
    steps = []

    async def discover(timeout, *, serial=None, lease=None):
        if any(step[0] == "discover" for step in steps):
            raise RuntimeError("the collector ignored its stop file")
        steps.append(("discover", serial, lease is not None))
        stop.write_text("stop", encoding="ascii")
        raise ConnectionError("Explorer not advertising")

    monkeypatch.setattr(jackery, "discover_reader", discover)
    release = AsyncMock(side_effect=lambda *_, **__: steps.append(("release",)))
    argv = [
        "linux_worker.py", "--data-dir", str(tmp_path),
        "jackery-record", "--serial", SERIAL,
    ]
    with patch.object(worker.sys, "argv", argv), patch.object(
        worker, "disconnect_jackery_orphan", new=release
    ):
        assert worker.main() == 0

    assert radio.used == []
    assert steps == [("release",), ("discover", SERIAL, True)]


def test_the_real_dp3_collector_releases_through_the_worker(tmp_path, monkeypatch, radio):
    """The worker's release reaches the real DP3 collector before every scan."""
    from openpowerstation import ble, runtime
    from openpowerstation.config import Config, save_config

    save_config(Config("AA:BB:CC:DD:EE:FF", "MR51ABCDEFGH1234", "1234567"),
                tmp_path / "config.json")
    # The collector's device lock lives in the user's data directory.
    monkeypatch.setattr(runtime, "data_dir", lambda: tmp_path / "home")
    stop = tmp_path / "collector.stop"
    radio.on_use = lambda: stop.write_text("stop", encoding="ascii")
    steps = []

    async def scan(*args, **kwargs):
        if any(step[0] == "scan" for step in steps):
            # Ends the collector's thread, so a broken stop cannot hang the test.
            raise SystemExit("the collector ignored its stop file")
        steps.append(("scan", args, kwargs))
        stop.write_text("stop", encoding="ascii")
        return []

    monkeypatch.setattr(ble, "scan", scan)
    release = AsyncMock(side_effect=lambda address: steps.append(("release", address)))
    argv = ["linux_worker.py", "--data-dir", str(tmp_path), "record"]
    with patch.object(worker.sys, "argv", argv), patch.object(
        worker, "disconnect_orphan", new=release
    ):
        assert worker.main() == 0

    assert radio.used == []
    assert steps == [("release", "AA:BB:CC:DD:EE:FF"), ("scan", (), {})]


def dp3_worker_modules(scanned, attempts=2):
    """A fake package whose record command scans ``attempts`` times through the worker."""
    fake_package = ModuleType("openpowerstation")
    fake_ble = ModuleType("openpowerstation.ble")
    fake_cli = ModuleType("openpowerstation.cli")
    fake_config = ModuleType("openpowerstation.config")

    async def scan(*_, **__):
        scanned.append(True)
        return []

    fake_ble.scan = scan
    fake_package.ble = fake_ble
    fake_config.load_config = MagicMock(
        return_value=SimpleNamespace(address="AA:BB:CC:DD:EE:FF"))

    def cli_main(args, *, scan=None):
        for _ in range(attempts):
            # Bounded here so a check that never ends fails rather than hangs.
            asyncio.run(asyncio.wait_for(scan(), 5))
        return 0

    fake_cli.main = cli_main
    return {"openpowerstation": fake_package, "openpowerstation.ble": fake_ble,
            "openpowerstation.cli": fake_cli, "openpowerstation.config": fake_config}


@pytest.mark.parametrize("failure", ["dbus-fast missing", "bus refused", "BlueZ error reply"])
def test_a_failed_orphan_check_never_stops_the_dp3_scan(tmp_path, capsys, failure):
    """The release helps rediscovery; it is not a condition for it.

    It ran unguarded in front of every scan, so dbus-fast failing to import
    after a rebuild, the system bus refusing the connection, or BlueZ
    answering with an error stopped every DP3 scan for as long as it lasted.
    """
    scanned = []
    argv = ["linux_worker.py", "--data-dir", str(tmp_path), "record"]
    with patch.dict(sys.modules, {**dp3_worker_modules(scanned), **failing_bluez(failure)}), \
            patch.object(worker.sys, "argv", argv):
        assert worker.main() == 0

    assert scanned == [True, True]
    log = capsys.readouterr().out
    # Named once for the two attempts, by type alone.
    assert log.count("BlueZ orphan check for EcoFlow skipped") == 1, log
    assert "AA:BB:CC:DD:EE:FF" not in log


def test_an_orphan_check_that_never_ends_is_abandoned_for_the_scan(tmp_path, capsys):
    """Bounded: connecting to the system bus had no deadline at all."""
    scanned = []
    argv = ["linux_worker.py", "--data-dir", str(tmp_path), "record"]
    with patch.dict(sys.modules, {**dp3_worker_modules(scanned, attempts=1),
                                  **failing_bluez("bus never answers")}), \
            patch.object(worker.sys, "argv", argv), \
            patch.object(worker, "ORPHAN_CHECK_TIMEOUT", 0.05):
        assert worker.main() == 0

    assert scanned == [True]
    assert "BlueZ orphan check for EcoFlow skipped (TimeoutError)" in capsys.readouterr().out


@pytest.mark.parametrize("failure", ["dbus-fast missing", "bus refused", "BlueZ error reply"])
def test_a_failed_orphan_check_never_ends_the_jackery_collector(tmp_path, capsys, failure):
    """Anything but an OSError from the release ended the Jackery collector's process."""
    reader = SimpleNamespace(identity=SimpleNamespace(
        serial=SERIAL, address="11:22:33:44:55:66"
    ))
    modules, original_discover = fake_worker_modules(reader, cli_calls=2)
    argv = [
        "linux_worker.py", "--data-dir", str(tmp_path),
        "jackery-record", "--serial", SERIAL,
    ]
    with patch.dict(sys.modules, {**modules, **failing_bluez(failure)}), \
            patch.object(worker.sys, "argv", argv):
        assert worker.main() == 0

    assert original_discover.await_count == 2
    log = capsys.readouterr().out
    assert log.count("BlueZ orphan check for Jackery skipped") == 1, log
    assert SERIAL not in log


def test_two_connected_candidates_still_refuse_the_jackery_attempt(tmp_path):
    """The one failure that is not skipped: which link is this Explorer's is unknown."""
    bus = MagicMock()
    bus.connect = AsyncMock(return_value=bus)
    bus.call = AsyncMock(return_value=SimpleNamespace(message_type="ok", body=[{
        "/org/bluez/hci0/jackery1": device_props(address="11:22:33:44:55:66", connected=True),
        "/org/bluez/hci0/jackery2": device_props(address="22:33:44:55:66:77", connected=True),
    }]))
    modules = bluez_modules()
    modules["dbus_fast.aio"] = SimpleNamespace(MessageBus=lambda **_: bus)
    reader = SimpleNamespace(identity=SimpleNamespace(serial=SERIAL, address="11:22:33:44:55:66"))
    fakes, original_discover = fake_worker_modules(reader, cli_calls=1)
    argv = [
        "linux_worker.py", "--data-dir", str(tmp_path),
        "jackery-record", "--serial", SERIAL,
    ]
    with patch.dict(sys.modules, {**fakes, **modules}), patch.object(worker.sys, "argv", argv), \
            patch.object(worker, "_jackery_serial_from_bluez", lambda values: SERIAL), \
            pytest.raises(ConnectionError, match="Multiple connected Jackery"):
        worker.main()

    # A ConnectionError, which the collector records and retries; nothing
    # was released and nothing was opened.
    original_discover.assert_not_awaited()
    assert bus.call.await_count == 1


def test_an_orphan_check_failure_is_logged_once_until_it_works_again(capsys):
    release = AsyncMock(side_effect=[AuthError("rejected"), AuthError("rejected"), 0,
                                     ConnectionError("BlueZ device inventory unavailable")])
    check = worker.OrphanCheck(release, "EcoFlow")

    async def attempts():
        for _ in range(4):
            await check("AA:BB:CC:DD:EE:FF")

    asyncio.run(attempts())
    assert capsys.readouterr().out.splitlines() == [
        "BlueZ orphan check for EcoFlow skipped (AuthError); discovering anyway.",
        "BlueZ orphan check for EcoFlow working again.",
        "BlueZ orphan check for EcoFlow skipped (ConnectionError); discovering anyway.",
    ]


def test_the_app_passes_an_installations_entity_id_overrides_to_its_children(tmp_path, monkeypatch):
    monkeypatch.delenv("OPENDP3_ENTITY_IDS", raising=False)
    run.use_entity_id_overrides(tmp_path)
    assert "OPENDP3_ENTITY_IDS" not in run.os.environ
    overrides = tmp_path / "entity_ids.json"
    overrides.write_text('{"dp3": {"bms_batt_soc": "sensor.home_battery_soc"}}', "utf-8")
    run.use_entity_id_overrides(tmp_path)
    assert run.os.environ["OPENDP3_ENTITY_IDS"] == str(overrides)


def test_a_malformed_entity_id_override_file_stops_setup(tmp_path, monkeypatch):
    monkeypatch.delenv("OPENDP3_ENTITY_IDS", raising=False)
    (tmp_path / "entity_ids.json").write_text('{"dp3": {"bms_batt_soc": "Not An Id"}}', "utf-8")
    with pytest.raises(run.SetupError):
        run.use_entity_id_overrides(tmp_path)
    assert "OPENDP3_ENTITY_IDS" not in run.os.environ
