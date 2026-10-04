"""Every long-running command stops on a known file, and every copy of its name agrees.

Stop-OpenPowerstation.cmd stops the Windows workers by writing these files, the
watchdog writes one to restart a single worker, start_all.py clears and
passes them, and the HAOS app clears them at start. A run started by hand
without --stop-file used to watch nothing, so nothing could stop it and it
went on holding the recording's writer lock (jackery.sqlite.writer.lock
incident, 2026-09-04). No test drove that default, or the Jackery bridge's
own stop check, so either could be reverted with every suite green.
"""
import importlib.util
from pathlib import Path
import re
from types import SimpleNamespace

from test_bridge_parity import FakeClient
from openpowerstation import cli, jackery_bridge, runtime
from openpowerstation.config import Config, save_config
from openpowerstation.storage import read_db

ROOT = Path(__file__).resolve().parents[1]
SERIAL = "856199990000000"


def load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def configure(path):
    save_config(Config("AA:BB:CC:DD:EE:FF", "MR51ABCDEFGH1234", "1234567",
                       mqtt_host="broker.invalid"), path)


def test_a_manual_jackery_record_stops_on_its_standard_stop_file(tmp_path):
    stop = tmp_path / "jackery.stop"
    attempts = []

    async def discover(timeout, *, serial=None, lease=None):
        attempts.append(serial)
        if len(attempts) > 1:
            raise RuntimeError("the collector ignored its stop file")
        stop.write_text("stop", encoding="ascii")
        raise ConnectionError("Explorer not advertising")

    status = cli.main(["--data-dir", str(tmp_path), "jackery-record", "--serial", SERIAL],
                      discover=discover)

    assert status == 0
    assert attempts == [SERIAL]
    with read_db(tmp_path / "jackery.sqlite") as db:
        assert [row[0] for row in db.execute("SELECT status FROM sessions")] == ["stopped"]


def test_a_manual_record_stops_on_its_standard_stop_file(tmp_path, monkeypatch):
    configure(tmp_path / "config.json")
    # The collector's device lock lives in the user's data directory.
    monkeypatch.setattr(runtime, "data_dir", lambda: tmp_path / "home")
    stop = tmp_path / "collector.stop"
    scans = []

    async def scan(*_, **__):
        scans.append(True)
        if len(scans) > 1:
            # Ends the collector's thread, so an ignored stop cannot hang the test.
            raise SystemExit("the collector ignored its stop file")
        stop.write_text("stop", encoding="ascii")
        return []

    assert cli.main(["--data-dir", str(tmp_path), "record"], scan=scan) == 0
    assert scans == [True]


def test_a_manual_jackery_bridge_stops_on_its_standard_stop_file(tmp_path, monkeypatch):
    configure(tmp_path / "config.json")
    client = FakeClient()
    monkeypatch.setattr(jackery_bridge.JackeryBridge, "build_client", lambda self: client)
    stop = tmp_path / "jackery-bridge.stop"
    publishes = []
    publish_once = jackery_bridge.JackeryBridge.publish_once

    def publish_then_stop(self):
        publishes.append(True)
        if len(publishes) > 1:
            raise RuntimeError("the bridge ignored its stop file")
        publish_once(self)
        stop.write_text("stop", encoding="ascii")

    monkeypatch.setattr(jackery_bridge.JackeryBridge, "publish_once", publish_then_stop)
    status = cli.main(["--data-dir", str(tmp_path), "jackery-bridge", "--serial", SERIAL,
                       "--interval", "1"])

    assert status == 0
    assert publishes == [True]
    assert client.last("/availability") == "offline"


def test_the_jackery_bridge_stops_on_the_stop_file_it_is_given(tmp_path):
    """start_all.py runs the bridge in its own process and relies on this check."""
    config = Config("AA:BB:CC:DD:EE:FF", "MR51ABCDEFGH1234", "1234567",
                    mqtt_host="broker.invalid")
    client = FakeClient()
    worker = jackery_bridge.JackeryBridge(config, tmp_path / "jackery.sqlite", serial=SERIAL,
                                          interval=1, client=client)
    stop = tmp_path / "chosen.stop"
    publishes = []

    def publish_then_stop():
        publishes.append(True)
        if len(publishes) > 1:
            raise RuntimeError("the bridge ignored its stop file")
        stop.write_text("stop", encoding="ascii")

    worker.publish_once = publish_then_stop
    worker.run(stop_file=stop)

    assert publishes == [True]
    assert client.last("/availability") == "offline"


def test_every_command_with_a_stop_file_option_has_a_standard_one():
    parser = cli.parser()
    commands = parser._subparsers._group_actions[0].choices
    with_option = {name for name, sub in commands.items()
                   if any("--stop-file" in action.option_strings for action in sub._actions)}
    assert with_option == {"record", "jackery-record", "jackery-bridge"}
    assert with_option <= set(cli.STOP_FILES)


def test_every_copy_of_the_stop_file_names_agrees(tmp_path, monkeypatch):
    """The copies that cannot import cli.STOP_FILES are held to it here."""
    names = set(cli.STOP_FILES.values())

    # Stop-OpenPowerstation.cmd writes one for every worker.
    stopper = (ROOT / "Stop-OpenPowerstation.cmd").read_text(encoding="utf-8")
    assert set(re.findall(r'echo stop>"%~dp0data\\([^"]+)"', stopper)) == names

    # The Windows watchdog stops one worker through it.
    watchdog = load("opendp3_watchdog_stop_files", ROOT / "scripts" / "watchdog.py")
    assert {stop for _, stop, _ in watchdog.WORKERS.values()} == names

    # start_all.py clears each before it starts a worker, and passes the
    # collectors theirs.
    start_all = watchdog.start_all
    spawned = []
    monkeypatch.setattr(start_all, "held", lambda path: False)
    monkeypatch.setattr(start_all, "spawn",
                        lambda args, log: spawned.append(args) or SimpleNamespace(poll=lambda: 0))
    data = tmp_path / "windows"
    data.mkdir()
    # The Jackery recorder starts only for the Explorer config.json names.
    save_config(Config("AA:BB:CC:DD:EE:FF", "MR51ABCDEFGH1234", "1234567",
                       jackery_serial=SERIAL), data / "config.json")
    for name in names:
        (data / name).write_text("stop", encoding="ascii")
    for launch in (start_all.start_collector, start_all.start_bridge,
                   start_all.start_jackery_recorder, start_all.start_jackery_bridge):
        launch(data, Path("python"))
    assert [path.name for path in data.glob("*.stop")] == []
    passed = {Path(args[args.index("--stop-file") + 1]).name: args[args.index("--data-dir") + 2]
              for args in spawned if "--stop-file" in args}
    assert passed == {cli.STOP_FILES["record"]: "record",
                      cli.STOP_FILES["jackery-record"]: "jackery-record"}

    # The HAOS app clears the ones its children watch, or a collector left
    # one behind would refuse every restart: "Stop file already exists".
    run = load("app_run_stop_files", ROOT / "home-assistant" / "opendp3" / "run.py")
    app = tmp_path / "app"
    app.mkdir()
    configure(tmp_path / "import.json")
    for name in names:
        (app / name).write_text("stop", encoding="ascii")
    run.prepare(app, tmp_path, {})
    watched = {cli.STOP_FILES[command] for command in ("record", "jackery-record", "jackery-bridge")}
    assert not watched & {path.name for path in app.glob("*.stop")}
