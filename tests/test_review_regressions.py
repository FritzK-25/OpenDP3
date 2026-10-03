"""Cross-component regressions from the September broad review. No live I/O."""
import asyncio
import csv
import json
from types import SimpleNamespace

import pytest

from opendp3.bridge import Bridge, state_payload, discovery_payloads
from opendp3.cli import _jackery_ble_loop
from opendp3.config import Config, save_config
from opendp3.exporting import export_evidence
from opendp3.jackery_bridge import JackeryBridge, state_payload as jackery_payload
from opendp3.jackery_fields import map_properties
from opendp3.queries import latest, snapshot
from opendp3.recorder import Recorder
from opendp3.storage import Store

SERIAL = "123456789012345"
UTC = 1_800_000_000_000_000_000


def config(allow=False):
    return Config("AA:BB:CC:DD:EE:FF", "MR51ABCDEFGHIJKL", "123", allow_control=allow)


class Client:
    def __init__(self):
        self.states = []

    def publish(self, topic, payload=None, **_):
        if topic.endswith("/state"):
            self.states.append(json.loads(payload))


def observe(recorder, t, **properties):
    fields = {"device": {"serial": SERIAL}, "properties": properties}
    recorder.ingest_observation(json.dumps(fields).encode(), fields, map_properties(properties),
                                utc_ns=recorder.start_utc + int(t*1e9),
                                mono_ns=recorder.start_mono + int(t*1e9))


@pytest.mark.parametrize("bridge_type,key,folder", [
    (Bridge, "cfg_hv_ac_out_open", "commands"),
    (JackeryBridge, "jackery_ac_output", "jackery-commands"),
])
def test_retained_commands_never_acquire_a_fresh_lease(tmp_path, bridge_type, key, folder):
    extra = (SERIAL,) if bridge_type is JackeryBridge else ()
    bridge = bridge_type(config(True), tmp_path / "test.sqlite", *extra)
    message = SimpleNamespace(topic=f"{bridge.base}/control/{key}/set", payload=b"ON", retain=True)
    bridge.on_message(None, None, message)
    assert not list((tmp_path / folder).glob("*.json"))
    message.retain = False
    bridge.on_message(None, None, message)
    assert len(list((tmp_path / folder).glob("*.json"))) == 1


@pytest.mark.parametrize("policy", ["disabled", "missing", "invalid", "revoked", "enabled"])
def test_jackery_checks_policy_when_executing_each_request(tmp_path, monkeypatch, policy):
    setup = tmp_path / "config.json"
    if policy != "missing":
        save_config(config(policy in {"revoked", "enabled"}), setup)
    if policy == "invalid":
        setup.write_text("{broken", encoding="utf-8")
    commands = tmp_path / "jackery-commands"
    commands.mkdir()
    (commands / "request.json").write_text(
        json.dumps({"control": "jackery_ac_output", "value": True}), encoding="utf-8")
    stop = tmp_path / "stop"

    class Reader:
        identity = SimpleNamespace(serial=SERIAL)
        sent = []

        async def read(self, **_):
            if policy == "revoked":
                save_config(config(False), setup)
            stop.touch()
            return {"rb": 50, "oac": 1}

        async def send_control(self, *args):
            self.sent.append(args)

        async def close(self):
            pass

    reader = Reader()

    async def discover(*_):
        return reader

    monkeypatch.setattr("opendp3.jackery.discover_reader", discover)
    with Store(tmp_path / "jackery.sqlite", reserve_bytes=0) as store:
        asyncio.run(_jackery_ble_loop(store, 3, None, SERIAL, stop, config_path=setup))
        kinds = [row[0] for row in store.conn.execute("SELECT kind FROM events")]
    assert reader.sent == ([("jackery_ac_output", True)] if policy == "enabled" else [])
    assert ("control" if policy == "enabled" else "control_refused") in kinds
    assert not list(commands.glob("*.json"))


def test_bridge_follows_restart_even_if_new_session_first_frame_is_delayed(tmp_path, monkeypatch):
    monkeypatch.setattr("opendp3.jackery_bridge.time.time_ns", lambda: UTC + 10**9)
    client = Client()
    path = tmp_path / "jackery.sqlite"
    bridge = JackeryBridge(config(), path, serial=SERIAL, client=client)
    with Store(path, reserve_bytes=0) as store:
        first = Recorder(store, utc_ns=UTC, mono_ns=0, firmware="Jackery test")
        observe(first, 0, rb=40)
        bridge.publish_once()
        first.finish(mono_ns=0)
        second = Recorder(store, utc_ns=UTC+1, mono_ns=0, firmware="Jackery test")
        bridge.publish_once()  # New session exists but has no decoded frame yet.
        observe(second, 0, rb=80)
        bridge.publish_once()
        assert bridge._session_id == second.sid
        assert client.states[-1]["bms_batt_soc"] == 80
        assert client.states[-1]["collector_state"] == "recording"


@pytest.mark.parametrize("publish", [state_payload, jackery_payload])
def test_field_expiry_does_not_depend_on_other_packets(tmp_path, publish):
    with Store(tmp_path / "test.sqlite", reserve_bytes=0) as store:
        recorder = Recorder(store, utc_ns=UTC, mono_ns=0)
        observe(recorder, 0, bt=290, rb=50)
        observe(recorder, 50, rb=49)
        payload, live = publish(latest(store.path), now_ns=UTC + 50*10**9)
        assert live
        assert payload["cms_batt_temp"] is None
        assert payload["bms_batt_soc"] == 49


def test_field_discovery_marks_null_unavailable_including_diagnostic_measurements():
    configs = {p["object_id"]: p for p in discovery_payloads("test").values()}
    for key in ("cms_batt_temp", "extra1_temperature", "errcode"):
        entry = configs["opendp3_" + key]
        assert "availability_topic" not in entry
        assert entry["availability_mode"] == "all"
        state = next(a for a in entry["availability"] if a["topic"].endswith("/state"))
        assert f"value_json.{key} is not none" in state["value_template"]
        assert "else 'offline'" in state["value_template"]


def test_jackery_backfill_preserves_field_age(tmp_path, monkeypatch):
    monkeypatch.setattr("opendp3.jackery_bridge.time.time_ns", lambda: UTC + 50*10**9)
    path = tmp_path / "test.sqlite"
    client = Client()
    with Store(path, reserve_bytes=0) as store:
        recorder = Recorder(store, utc_ns=UTC, mono_ns=0, firmware="Jackery test")
        observe(recorder, 0, bt=290, acov=1200)
        observe(recorder, 50, rb=50)
        bridge = JackeryBridge(config(), path, serial=SERIAL, client=client)
        bridge.publish_once()
        assert client.states[-1]["jackery_ac_voltage_v"] is None
        assert client.states[-1]["cms_batt_temp"] is None
        assert client.states[-1]["bms_batt_soc"] == 50


def test_jackery_incident_export_contains_its_measurements_and_gap(tmp_path, monkeypatch):
    from matplotlib.figure import Figure
    saved_axes = []
    original_save = Figure.savefig

    def save(figure, *args, **kwargs):
        saved_axes.extend(axis.get_ylabel() for axis in figure.axes)
        return original_save(figure, *args, **kwargs)

    monkeypatch.setattr(Figure, "savefig", save)
    path = tmp_path / "test.sqlite"
    with Store(path, reserve_bytes=0) as store:
        recorder = Recorder(store, utc_ns=UTC, mono_ns=0, firmware="Jackery test", expected_interval=3)
        observe(recorder, 0, rb=50, acov=1200, acohz=60, oac=1, novel=0)
        observe(recorder, 20, rb=50, acov=1100, acohz=59, oac=1, novel=1)
        snap = snapshot(path)
        coverage = {row["key"]: row for row in snap["coverage"]}
        assert coverage["jackery_ac_voltage_v"]["value"] == 110
        assert "bms_max_cell_temp" not in coverage
        assert snap["incidents"][0]["gaps"]
        assert snap["series"]["cms_batt_soc"][0]["segment"] != snap["series"]["cms_batt_soc"][1]["segment"]
        report, _ = export_evidence(path, tmp_path / "export")
    summary = json.loads((report.parent / "summary.json").read_text())
    assert summary["contains_gaps"]
    with (report.parent / "telemetry.csv").open(encoding="utf-8", newline="") as f:
        rows = list(csv.DictReader(f))
    fields = {r["field"] for r in rows}
    assert {"jackery_ac_voltage_v", "jackery_ac_frequency_hz", "jackery_ac_output"} <= fields
    assert "novel" not in fields
    with (report.parent / "events.csv").open(encoding="utf-8", newline="") as f:
        kinds = {r["kind"] for r in csv.DictReader(f)}
    assert {"capture_gap", "unmapped_change"} <= kinds
    assert "unrecognized_event" not in kinds
    assert {"Voltage (V)", "Frequency (Hz)", "Estimated time (h)"} <= set(saved_axes)


def test_an_unmapped_jackery_read_does_not_kill_the_collector(tmp_path, monkeypatch):
    """One unmapped status frame must not cost a process restart.

    The collector used to raise on the first read that mapped to nothing, which
    ended the process; the supervisor then waited its retry before a fresh
    rediscovery, a far longer outage than the read was worth. The EcoFlow
    collector has always recovered in-process instead.
    """
    stop = tmp_path / "stop"
    closes = []

    class Reader:
        identity = SimpleNamespace(serial=SERIAL)

        def __init__(self):
            self.reads = 0

        async def read(self, **_):
            self.reads += 1
            if self.reads == 1:
                return {"zz": 1}          # nothing dashboard-mapped
            stop.touch()
            return {"rb": 50, "oac": 1}   # a real reading right after

        async def close(self):
            closes.append(1)

    reader = Reader()

    async def discover(*_):
        return reader

    monkeypatch.setattr("opendp3.jackery.discover_reader", discover)
    with Store(tmp_path / "jackery.sqlite", reserve_bytes=0) as store:
        # No exception: the loop rides out the unmapped read and records the
        # next good one.
        asyncio.run(_jackery_ble_loop(store, 1, None, SERIAL, stop))
        frames = store.conn.execute("SELECT COUNT(*) FROM frames").fetchone()[0]
        values = [row[0] for row in store.conn.execute("SELECT DISTINCT key FROM measurements")]
    assert reader.reads == 2, "the loop stopped at the unmapped read"
    assert frames == 1, "the good reading that followed was not recorded"
    assert values, "no measurements were stored"
    # The link was never dropped over a single unmapped read.
    assert closes == [1], closes


def test_repeated_unmapped_jackery_reads_reacquire_the_link(tmp_path, monkeypatch):
    """Persistent nonsense is a bad session, and is recovered as one."""
    from opendp3.cli import UNMAPPED_READ_LIMIT

    stop = tmp_path / "stop"
    attaches = []

    class Reader:
        """One good reading, then nothing mappable ever again."""

        identity = SimpleNamespace(serial=SERIAL)

        def __init__(self):
            self.reads = 0

        async def read(self, **_):
            self.reads += 1
            if self.reads == 1:
                return {"rb": 50, "oac": 1}
            return {"zz": self.reads}

        async def close(self):
            pass

    class GoodReader(Reader):
        async def read(self, **_):
            stop.touch()
            return {"rb": 51, "oac": 1}

    async def discover(*_):
        attaches.append(1)
        # The first session goes bad and stays bad; the replacement works,
        # which is exactly what a reattach is for.
        return Reader() if len(attaches) == 1 else GoodReader()

    monkeypatch.setattr("opendp3.jackery.discover_reader", discover)
    with Store(tmp_path / "jackery.sqlite", reserve_bytes=0) as store:
        asyncio.run(_jackery_ble_loop(store, 1, None, SERIAL, stop))
        rows = list(store.conn.execute("SELECT kind FROM events"))
        kinds = [row[0] for row in rows]
        frames = list(store.conn.execute("SELECT segment FROM frames ORDER BY id"))
    assert len(attaches) == 2, "the dead session was never replaced"
    # Every unmapped read is preserved as evidence, not just the last one.
    assert kinds.count("suspect_telemetry") == UNMAPPED_READ_LIMIT, kinds
    # The reattach is a break in capture, so the readings either side are not
    # contiguous and must not share a segment.
    assert "disconnected" in kinds, kinds
    assert len(frames) == 2, frames
    assert frames[0][0] != frames[1][0], "the reattach did not start a new segment"


def test_an_unmapped_read_before_any_reading_still_recovers(tmp_path, monkeypatch):
    """No recorder exists yet, so there is nowhere to write an event.

    The loop must still not raise. Nothing is recorded because the session is
    only opened by the first real reading; the run log carries the reason.
    """
    stop = tmp_path / "stop"

    class Reader:
        identity = SimpleNamespace(serial=SERIAL)

        def __init__(self):
            self.reads = 0

        async def read(self, **_):
            self.reads += 1
            if self.reads == 1:
                return {"zz": 1}
            stop.touch()
            return {"rb": 50, "oac": 1}

        async def close(self):
            pass

    reader = Reader()

    async def discover(*_):
        return reader

    monkeypatch.setattr("opendp3.jackery.discover_reader", discover)
    with Store(tmp_path / "jackery.sqlite", reserve_bytes=0) as store:
        asyncio.run(_jackery_ble_loop(store, 1, None, SERIAL, stop))
        frames = store.conn.execute("SELECT COUNT(*) FROM frames").fetchone()[0]
    assert reader.reads == 2
    assert frames == 1
