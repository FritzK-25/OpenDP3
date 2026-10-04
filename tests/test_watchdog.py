"""Recovery must never turn an unrelated outage into a recording gap."""
import importlib.util
from pathlib import Path
import sqlite3
from unittest.mock import Mock

import pytest

from openpowerstation.config import Config, save_config

spec = importlib.util.spec_from_file_location("opendp3_watchdog", Path(__file__).parents[1] / "scripts/watchdog.py")
watchdog = importlib.util.module_from_spec(spec)
spec.loader.exec_module(watchdog)


@pytest.fixture
def healthy(monkeypatch):
    monkeypatch.setattr(watchdog.time, "time_ns", lambda: 1_000_000_000_000)
    monkeypatch.setattr(watchdog, "newest_frame", lambda path: 1_000_000_000_000)
    monkeypatch.setattr(watchdog, "lock_held", lambda path: True)
    monkeypatch.setattr(watchdog, "verify_broker_state", lambda *a, **k: {
        "DP3": {"availability": "online", "telemetry": "online"},
        "Jackery": {"availability": "online", "telemetry": "online"},
    })
    monkeypatch.setattr(watchdog, "configured_devices", lambda data: {"DP3": True, "Jackery": True})


def test_an_unconfigured_device_is_neither_checked_nor_started(tmp_path, monkeypatch, healthy):
    monkeypatch.setattr(watchdog, "configured_devices", lambda data: {"DP3": True, "Jackery": False})
    monkeypatch.setattr(watchdog, "newest_frame",
                        lambda p: None if p.name == "jackery.sqlite" else 1_000_000_000_000)
    monkeypatch.setattr(watchdog, "lock_held", lambda p: not p.name.startswith("jackery"))
    asked = {}

    def broker(data, **kwargs):
        asked.update(kwargs)
        return {"DP3": {"availability": "online", "telemetry": "online"}}

    monkeypatch.setattr(watchdog, "verify_broker_state", broker)
    assert watchdog.health(tmp_path) == (True, "healthy", {})
    assert asked["jackery"] is False


def test_configured_devices_follow_the_launcher(tmp_path):
    assert watchdog.configured_devices(tmp_path) == {"DP3": False, "Jackery": False}
    save_config(Config("AA:BB:CC:DD:EE:FF", "MR51123456789012", "123456"), tmp_path / "config.json")
    assert watchdog.configured_devices(tmp_path) == {"DP3": True, "Jackery": False}
    save_config(Config("AA:BB:CC:DD:EE:FF", "MR51123456789012", "123456", jackery_serial="123456789012345"),
                tmp_path / "config.json")
    assert watchdog.configured_devices(tmp_path) == {"DP3": True, "Jackery": True}


def test_nothing_configured_is_reported_without_actions(tmp_path, monkeypatch, healthy):
    monkeypatch.setattr(watchdog, "configured_devices", lambda data: {"DP3": False, "Jackery": False})
    assert watchdog.health(tmp_path) == (False, "no device configured", {})


def test_broker_outage_does_not_restart_any_owned_worker(tmp_path, monkeypatch, healthy):
    monkeypatch.setattr(watchdog, "verify_broker_state", lambda *a, **k: {
        name: {"availability": None, "telemetry": None} for name in ("DP3", "Jackery")})
    ok, _, actions = watchdog.health(tmp_path)
    assert not ok
    assert actions == {}


def test_powered_off_jackery_does_not_interrupt_dp3_or_reset_reconnecting_collector(tmp_path, monkeypatch, healthy):
    monkeypatch.setattr(watchdog, "newest_frame", lambda p: 0 if p.name == "jackery.sqlite" else 1_000_000_000_000)
    monkeypatch.setattr(watchdog, "verify_broker_state", lambda *a, **k: {
        "DP3": {"availability": "online", "telemetry": "online"},
        "Jackery": {"availability": "online", "telemetry": "offline"},
    })
    assert watchdog.health(tmp_path)[2] == {}


def test_dead_collector_only_starts_that_collector(tmp_path, monkeypatch, healthy):
    monkeypatch.setattr(watchdog, "lock_held", lambda p: p.name != "jackery.sqlite.writer.lock")
    assert watchdog.health(tmp_path)[2] == {"Jackery recorder": False}


def test_stuck_bridge_only_restarts_that_bridge(tmp_path, monkeypatch, healthy):
    monkeypatch.setattr(watchdog, "verify_broker_state", lambda *a, **k: {
        "DP3": {"availability": "offline", "telemetry": "online"},
        "Jackery": {"availability": "online", "telemetry": "online"},
    })
    assert watchdog.health(tmp_path)[2] == {"DP3 bridge": True}
    launched = []
    monkeypatch.setattr(watchdog, "lock_held", lambda p: False)
    monkeypatch.setattr(watchdog.start_all, "start_bridge", lambda *a: launched.append(a) or "started")
    assert watchdog.recover_worker(tmp_path, "DP3 bridge", True)
    assert len(launched) == 1
    assert {p.name for p in tmp_path.glob("*.stop")} == {"bridge.stop"}


def test_collector_restart_requires_evidence_beyond_stale_frames(tmp_path, healthy):
    with pytest.raises(ValueError):
        watchdog.recover_worker(tmp_path, "DP3 recorder", True)
    assert not list(tmp_path.glob("*.stop"))


class EndTest(BaseException):
    pass


def test_locked_database_is_unhealthy_instead_of_crashing(tmp_path, monkeypatch):
    database = tmp_path / "recordings.sqlite"
    database.touch()
    monkeypatch.setattr(watchdog, "read_db", Mock(side_effect=sqlite3.OperationalError("locked")))
    assert watchdog.newest_frame(database) is None


def test_probe_exception_does_not_end_supervision(tmp_path, monkeypatch):
    probe = Mock(side_effect=[RuntimeError("probe failed"), (True, "healthy", {})])
    monkeypatch.setattr(watchdog, "health", probe)
    monkeypatch.setattr(watchdog.time, "sleep", Mock(side_effect=[None, EndTest()]))
    with pytest.raises(EndTest):
        watchdog.run(tmp_path)
    assert probe.call_count == 2


def test_recovery_exception_does_not_end_supervision(tmp_path, monkeypatch):
    probe = Mock(side_effect=[(False, "offline", {"DP3 bridge": True}), (True, "healthy", {})])
    recovery = Mock(side_effect=OSError("transient"))
    monkeypatch.setattr(watchdog, "health", probe)
    monkeypatch.setattr(watchdog, "recover_worker", recovery)
    monkeypatch.setattr(watchdog.time, "sleep", Mock(side_effect=[None, EndTest()]))
    with pytest.raises(EndTest):
        watchdog.run(tmp_path)
    assert probe.call_count == 2
    recovery.assert_called_once_with(tmp_path, "DP3 bridge", True)


def test_a_viewer_install_is_never_supervised(tmp_path, monkeypatch):
    # A scheduled task left registered on a PC made a viewer must start
    # nothing: supervising here is starting collectors.
    from openpowerstation.config import Config, save_config
    save_config(Config("AA:BB:CC:DD:EE:FF", "MR51123456789012", "123456", role="viewer"),
                tmp_path / "config.json")
    monkeypatch.setattr(watchdog, "run", Mock(side_effect=AssertionError("supervised a viewer")))
    monkeypatch.setattr(watchdog.logging, "basicConfig", lambda **_: None)
    monkeypatch.setattr(watchdog.sys, "argv", ["watchdog.py", "--data-dir", str(tmp_path)])
    assert watchdog.main() == 0
