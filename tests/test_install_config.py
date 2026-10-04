"""What one install's config.json decides: which Explorer it names, and
whether it may hold the batteries at all.

The owner's Explorer serial used to be the library default in five places --
both Jackery commands, do_jackery_record, JackeryBridge and start_all.py -- so
anyone else's install searched for and published the owner's station unless
told otherwise, and the Windows launcher could name no other.

Each battery accepts one Bluetooth connection, and the production Pi holds
both. Nothing stopped a desktop install from taking one: Start recording, a
manual record command or a leftover launcher retried until the Pi's link
dropped -- a nightly backup, an app restart -- and then held the battery.
A viewer install now refuses at every collector and bridge.
"""
import importlib.util
import inspect
import json
from pathlib import Path
import re

import pytest

from openpowerstation import bridge, cli, jackery_bridge, runtime
from openpowerstation.config import Config, save_config
from openpowerstation.jackery_bridge import JackeryBridge

ROOT = Path(__file__).resolve().parents[1]
SERIAL = "856199990000000"


def load_start_all():
    spec = importlib.util.spec_from_file_location(
        "start_all_install_config", ROOT / "scripts" / "start_all.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def configure(data, **overrides):
    save_config(Config("AA:BB:CC:DD:EE:FF", "MR51123456789012", "123456", **overrides),
                data / "config.json")


def test_no_shipped_code_names_a_jackery_serial():
    shipped = [path for base in ("src/openpowerstation", "scripts")
               for path in sorted((ROOT / base).rglob("*.py")) if "vendor" not in path.parts]
    named = [f"{path.relative_to(ROOT).as_posix()}:{number}"
             for path in shipped
             for number, line in enumerate(path.read_text("utf-8").splitlines(), 1)
             if re.search(r"(?<![0-9])[0-9]{15}(?![0-9])", line)]
    assert named == []


@pytest.mark.parametrize("command", ["jackery-record", "jackery-bridge"])
def test_the_jackery_commands_require_a_serial(command, capsys):
    with pytest.raises(SystemExit):
        cli.parser().parse_args([command])
    assert "--serial" in capsys.readouterr().err


def test_no_jackery_entry_point_defaults_its_serial():
    for entry in (JackeryBridge, cli.do_jackery_record):
        assert inspect.signature(entry).parameters["serial"].default is inspect.Parameter.empty


@pytest.mark.parametrize("value", ["12345", "85612408241169X", 856199990000000, None])
def test_a_malformed_jackery_serial_is_refused(tmp_path, value):
    with pytest.raises(ValueError, match="Jackery serial"):
        Config("AA:BB:CC:DD:EE:FF", "MR51123456789012", "123456", jackery_serial=value).validate()


def test_start_all_records_the_explorer_config_json_names(tmp_path, monkeypatch):
    start_all = load_start_all()
    configure(tmp_path, jackery_serial=SERIAL)
    spawned = []
    monkeypatch.setattr(start_all, "spawn", lambda args, log: spawned.append(args))
    monkeypatch.setattr(start_all, "held", lambda path: False)
    assert start_all.start_jackery_recorder(tmp_path, Path("python")) == "started"
    command = spawned[0]
    assert command[command.index("jackery-record") + 1:][:2] == ["--serial", SERIAL]


def test_start_all_leaves_the_jackery_alone_when_config_json_names_none(tmp_path, monkeypatch, capsys):
    start_all = load_start_all()
    configure(tmp_path)
    spawned = []
    monkeypatch.setattr(start_all, "spawn", lambda args, log: spawned.append(args))
    monkeypatch.setattr(start_all, "held", lambda path: False)
    assert start_all.start_jackery_recorder(tmp_path, Path("python")) == "unconfigured"
    assert start_all.jackery_bridge_worker(tmp_path) == 1
    assert "jackery_serial" in capsys.readouterr().err
    assert spawned == []


# --------------------------------------------------------------------------- role

def refusal():
    from openpowerstation.config import VIEWER_REFUSAL
    return VIEWER_REFUSAL


@pytest.fixture
def home(tmp_path, monkeypatch):
    """The user data directory the DP3 collector keeps its device lock in."""
    monkeypatch.setattr(runtime, "data_dir", lambda: tmp_path / "home")
    return tmp_path / "home"


def ignored(calls):
    """A scan that records the call and ends the collector's thread, so a
    collector that ignores its role fails here instead of searching for ever."""
    async def scan(*_, **__):
        calls.append(True)
        raise SystemExit("the collector ignored the viewer role")
    return scan


def test_a_viewer_install_never_opens_the_dp3(tmp_path, home):
    scans = []
    scan = ignored(scans)
    service = runtime.Service(Config("AA:BB:CC:DD:EE:FF", "MR51123456789012", "123456",
                                     role="viewer"),
                              tmp_path / "recordings.sqlite", scan=scan)
    service.start()
    service.join(5)
    assert not service.is_alive()
    assert service.error == refusal()
    # Refused before the radio, the device lock and the evidence store.
    assert scans == []
    assert not home.exists()
    assert not (tmp_path / "recordings.sqlite").exists()


def test_the_record_command_on_a_viewer_install_refuses(tmp_path, home, capsys):
    configure(tmp_path, role="viewer")
    scans = []
    assert cli.main(["--data-dir", str(tmp_path), "record"], scan=ignored(scans)) == 1
    assert refusal() in capsys.readouterr().err
    assert scans == []


@pytest.mark.parametrize("contents", [
    None,                                    # a viewer install
    "{not json",                             # unreadable: fails closed
    json.dumps({"role": "owner"}),           # an unknown role: fails closed
])
def test_jackery_record_on_a_viewer_install_refuses(tmp_path, capsys, contents):
    if contents is None:
        configure(tmp_path, role="viewer")
    else:
        (tmp_path / "config.json").write_text(contents, encoding="utf-8")
    searched = []

    async def discover(*_, **__):
        searched.append(True)
        # Stops a collector that ignored its role after this one search.
        (tmp_path / "jackery.stop").write_text("stop", encoding="ascii")
        raise ConnectionError("not advertising")

    status = cli.main(["--data-dir", str(tmp_path), "jackery-record", "--serial", SERIAL],
                      discover=discover)
    assert status == 1
    assert refusal() in capsys.readouterr().err
    assert searched == []
    assert not (tmp_path / "jackery.sqlite").exists()


def test_an_install_with_no_role_or_no_config_collects(tmp_path):
    # Configs written before the role existed, and a Jackery-only install with
    # no config.json at all, keep recording as they did.
    from openpowerstation.config import install_role
    assert install_role(tmp_path / "config.json") == "collector"
    configure(tmp_path)
    values = json.loads((tmp_path / "config.json").read_text("utf-8"))
    values.pop("role", None)
    (tmp_path / "config.json").write_text(json.dumps(values), encoding="utf-8")
    assert install_role(tmp_path / "config.json") == "collector"


@pytest.mark.parametrize("command", [["bridge", "--once"],
                                     ["jackery-bridge", "--serial", SERIAL, "--once"]])
def test_neither_bridge_publishes_from_a_viewer_install(tmp_path, monkeypatch, capsys, command):
    # The batteries' MQTT identity is the recording machine's too: a second
    # publisher would overwrite its retained state.
    configure(tmp_path, role="viewer", mqtt_host="broker.invalid")
    built = []
    for cls in (bridge.Bridge, jackery_bridge.JackeryBridge):
        monkeypatch.setattr(cls, "build_client", lambda self: built.append(self) or None)
    assert cli.main(["--data-dir", str(tmp_path), *command]) == 1
    assert refusal() in capsys.readouterr().err
    assert built == []


def test_start_all_starts_nothing_on_a_viewer_install(tmp_path, monkeypatch, capsys):
    start_all = load_start_all()
    configure(tmp_path, role="viewer", jackery_serial=SERIAL)
    spawned = []
    monkeypatch.setattr(start_all, "spawn", lambda args, log: spawned.append(args))
    monkeypatch.setattr(start_all, "held", lambda path: False)
    monkeypatch.setattr(start_all, "CONNECT_TIMEOUT", 0)
    assert start_all.main(["--data-dir", str(tmp_path), "--no-bridge"]) == 1
    assert refusal() in capsys.readouterr().err
    assert spawned == []

