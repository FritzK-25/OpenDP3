"""Cross-component regressions from the September broad review. No live I/O."""
import asyncio
import csv
import json
import time
from types import SimpleNamespace

import pytest

from openpowerstation.bridge import Bridge, state_payload, discovery_payloads
from openpowerstation.cli import _jackery_ble_loop
from openpowerstation.config import Config, save_config
from openpowerstation.control_queue import write_request
from openpowerstation.exporting import export_evidence
from openpowerstation.jackery_bridge import JackeryBridge, state_payload as jackery_payload
from openpowerstation.jackery_fields import map_properties
from openpowerstation.queries import latest, snapshot
from openpowerstation.recorder import Recorder
from openpowerstation.storage import Store, read_db

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
    (lambda *args: JackeryBridge(*args, serial=SERIAL), "jackery_ac_output", "jackery-commands"),
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
    assert write_request(commands, "jackery_ac_output", True)
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

    async def discover(*_, **__):
        return reader

    monkeypatch.setattr("openpowerstation.jackery.discover_reader", discover)
    with Store(tmp_path / "jackery.sqlite", reserve_bytes=0) as store:
        asyncio.run(_jackery_ble_loop(store, 3, None, SERIAL, stop, config_path=setup))
        kinds = [row[0] for row in store.conn.execute("SELECT kind FROM events")]
    assert reader.sent == ([("jackery_ac_output", True)] if policy == "enabled" else [])
    assert ("control" if policy == "enabled" else "control_refused") in kinds
    assert not list(commands.glob("*.json"))


class ControlReader:
    """A station that answers every read with ``readback`` and records what it is sent."""

    def __init__(self, stop, readback, serial=SERIAL):
        self.identity = SimpleNamespace(serial=serial)
        self.stop, self.readback = stop, readback
        self.sent, self.reads, self.closes = [], 0, 0

    async def read(self, **_):
        self.reads += 1
        self.stop.touch()
        return dict(self.readback)

    async def send_control(self, *args):
        self.sent.append(args)

    async def close(self):
        self.closes += 1


def run_jackery_request(tmp_path, monkeypatch, reader, *, age=0.0):
    """Queue one AC-on request ``age`` seconds old and record until the stop file.

    Control is enabled and the request is well formed, so anything that
    refuses it is the guard under test, not the policy or the payload.
    Returns the recorded ``(kind, detail)`` events.
    """
    setup = tmp_path / "config.json"
    save_config(config(True), setup)
    commands = tmp_path / "jackery-commands"
    commands.mkdir()
    # Dated inside the request, as the bridge dates it; the file's own
    # modification time is not what ages a request.
    issued = time.time_ns() - int(age * 1e9)
    (commands / "request.json").write_text(json.dumps(
        {"key": "jackery_ac_output", "value": True, "issued_utc_ns": issued}), encoding="utf-8")

    async def discover(*_, **__):
        return reader

    monkeypatch.setattr("openpowerstation.jackery.discover_reader", discover)
    with Store(tmp_path / "jackery.sqlite", reserve_bytes=0) as store:
        asyncio.run(_jackery_ble_loop(store, 3, None, SERIAL, reader.stop, config_path=setup))
        return [tuple(row) for row in store.conn.execute("SELECT kind, detail FROM events ORDER BY id")]


def test_a_stale_jackery_request_is_refused_not_sent(tmp_path, monkeypatch):
    """A press that waited past 30 s is not obeyed late.

    The bridge queues requests with no age pruning, and they wait whenever the
    station is out of range. Without this refusal an output change pressed
    minutes ago would run on reconnect, after the person has seen something
    else happen.
    """
    reader = ControlReader(tmp_path / "stop", {"rb": 50, "oac": 1})
    events = run_jackery_request(tmp_path, monkeypatch, reader, age=31)
    assert reader.sent == [], "an expired request reached the station"
    assert ("control_refused",
            "Jackery jackery_ac_output: request expired before it could be sent.") in events
    assert "control" not in [kind for kind, _ in events]


def test_a_jackery_command_the_station_did_not_apply_is_never_recorded_as_confirmed(
        tmp_path, monkeypatch):
    """The evidence record says 'confirmed' only when the readback agrees."""
    # The station accepts the write but its AC output stays off.
    reader = ControlReader(tmp_path / "stop", {"rb": 50, "oac": 0})
    events = run_jackery_request(tmp_path, monkeypatch, reader)
    assert reader.sent == [("jackery_ac_output", True)]
    kinds = [kind for kind, _ in events]
    assert "control" not in kinds, "an unapplied command was recorded as confirmed"
    assert any(kind == "control_unverified" and "readback did not confirm oac" in detail
               for kind, detail in events), events


class ReadbackReader(ControlReader):
    """Answers the poll with ``rb=50, oac=0`` and every read after a command with
    ``rb=51, oac=<after>``, and notes when the command went out."""

    def __init__(self, stop, after):
        super().__init__(stop, {"rb": 50, "oac": 0})
        self.after, self.sent_ns = after, None

    async def read(self, **_):
        self.reads += 1
        self.stop.touch()
        if self.sent_ns is None:
            return {"rb": 50, "oac": 0}
        return {"rb": 51, "oac": self.after}

    async def send_control(self, *args):
        # Room either side, so a stamp is plainly before or after the command
        # even on a coarse host clock.
        await asyncio.sleep(0.05)
        self.sent.append(args)
        self.sent_ns = time.time_ns()
        await asyncio.sleep(0.05)


@pytest.mark.parametrize("after,outcome", [(0, "control_unverified"), (1, "control")])
def test_each_jackery_reading_around_a_command_is_stamped_when_it_was_read(
        tmp_path, monkeypatch, after, outcome):
    """A reading taken before a command is never stamped after it.

    The poll's reply was stored only once the queued commands had run, at the
    time of storing. After a command the station did not apply, the evidence
    showed the output still off at a moment after the ON was sent; after one
    it did apply, the reply from before the command was merged into the
    readback and lost.
    """
    reader = ReadbackReader(tmp_path / "stop", after)
    events = run_jackery_request(tmp_path, monkeypatch, reader)
    assert reader.sent == [("jackery_ac_output", True)]
    assert outcome in [kind for kind, _ in events]
    with read_db(tmp_path / "jackery.sqlite") as db:
        frames = [(row[0], json.loads(row[1])["properties"])
                  for row in db.execute("SELECT utc_ns, raw FROM frames ORDER BY id")]
    assert [properties for _, properties in frames] == [
        {"rb": 50, "oac": 0}, {"rb": 51, "oac": after}]
    (before, _), (readback, _) = frames
    assert before < reader.sent_ns < readback


def test_a_jackery_command_refused_before_the_radio_is_recorded_as_refused(tmp_path, monkeypatch):
    """A write-site refusal sent nothing, so its outcome is known, not unverified.

    PermissionError is an OSError, so the gate's refusal used to be recorded
    as control_unverified -- "may have reached the station" -- for a command
    that provably never did.
    """
    class GatedReader(ControlReader):
        async def send_control(self, *args):
            raise PermissionError("Jackery outbound gate: command outside the allowlist blocked.")

    events = run_jackery_request(tmp_path, monkeypatch, GatedReader(tmp_path / "stop", {"rb": 50, "oac": 0}))
    assert ("control_refused", "Jackery jackery_ac_output: Jackery outbound gate: "
            "command outside the allowlist blocked.") in events
    assert "control_unverified" not in [kind for kind, _ in events]


def test_a_neighbouring_jackery_is_closed_before_any_read_or_command(tmp_path, monkeypatch):
    """Discovery opens the first Explorer advertising; only ours may be kept.

    Nothing later in the loop checks the serial again, so a neighbour kept
    here would be recorded under this station's serial and receive its
    queued output commands.
    """
    reader = ControlReader(tmp_path / "stop", {"rb": 50, "oac": 1}, serial="856100000000000")
    with pytest.raises(ValueError, match="not the requested"):
        run_jackery_request(tmp_path, monkeypatch, reader)
    assert reader.sent == [], "a queued command was sent to another station"
    assert reader.reads == 0, "the neighbour was read as though it were this station"
    assert reader.closes, "the neighbour's BLE session was left open"
    with read_db(tmp_path / "jackery.sqlite") as db:
        assert db.execute("SELECT COUNT(*) FROM frames").fetchone()[0] == 0


def test_bridge_follows_restart_even_if_new_session_first_frame_is_delayed(tmp_path, monkeypatch):
    monkeypatch.setattr("openpowerstation.jackery_bridge.time.time_ns", lambda: UTC + 10**9)
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
    configs = {topic.split("/")[3]: p for topic, p in discovery_payloads("test").items()}
    for key in ("cms_batt_temp", "extra1_temperature", "errcode"):
        entry = configs[key]
        assert "availability_topic" not in entry
        assert entry["availability_mode"] == "all"
        state = next(a for a in entry["availability"] if a["topic"].endswith("/state"))
        assert f"value_json.{key} is not none" in state["value_template"]
        assert "else 'offline'" in state["value_template"]


def test_jackery_backfill_preserves_field_age(tmp_path, monkeypatch):
    monkeypatch.setattr("openpowerstation.jackery_bridge.time.time_ns", lambda: UTC + 50*10**9)
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

    async def discover(*_, **__):
        return reader

    monkeypatch.setattr("openpowerstation.jackery.discover_reader", discover)
    with Store(tmp_path / "jackery.sqlite", reserve_bytes=0) as store:
        # No exception: the loop rides out the unmapped read and records the
        # next good one.
        asyncio.run(_jackery_ble_loop(store, 1, None, SERIAL, stop))
        # The unmapped reply is kept too, as a frame that carries no measurement.
        measured = store.conn.execute(
            "SELECT COUNT(DISTINCT frame_id) FROM measurements").fetchone()[0]
        values = [row[0] for row in store.conn.execute("SELECT DISTINCT key FROM measurements")]
    assert reader.reads == 2, "the loop stopped at the unmapped read"
    assert measured == 1, "the good reading that followed was not recorded"
    assert values, "no measurements were stored"
    # The link was never dropped over a single unmapped read.
    assert closes == [1], closes


def test_repeated_unmapped_jackery_reads_reacquire_the_link(tmp_path, monkeypatch):
    """Persistent nonsense is a bad session, and is recovered as one."""
    from openpowerstation.cli import UNPRODUCTIVE_POLL_LIMIT

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

    async def discover(*_, **__):
        attaches.append(1)
        # The first session goes bad and stays bad; the replacement works,
        # which is exactly what a reattach is for.
        return Reader() if len(attaches) == 1 else GoodReader()

    monkeypatch.setattr("openpowerstation.jackery.discover_reader", discover)
    with Store(tmp_path / "jackery.sqlite", reserve_bytes=0) as store:
        asyncio.run(_jackery_ble_loop(store, 1, None, SERIAL, stop))
        rows = list(store.conn.execute("SELECT kind FROM events"))
        kinds = [row[0] for row in rows]
        # The two readings; the unmapped replies between them are kept as well.
        frames = list(store.conn.execute(
            "SELECT segment FROM frames f WHERE EXISTS "
            "(SELECT 1 FROM measurements m WHERE m.frame_id=f.id) ORDER BY id"))
    assert len(attaches) == 2, "the dead session was never replaced"
    # Every unmapped read is preserved as evidence, not just the last one.
    assert kinds.count("suspect_telemetry") == UNPRODUCTIVE_POLL_LIMIT, kinds
    # The reattach is a break in capture, so the readings either side are not
    # contiguous and must not share a segment.
    assert "disconnected" in kinds, kinds
    assert len(frames) == 2, frames
    assert frames[0][0] != frames[1][0], "the reattach did not start a new segment"


def test_an_unmapped_read_before_any_reading_still_recovers(tmp_path, monkeypatch):
    """The first reply of a session can be the odd one, and it is kept.

    The recording used to open only with the first real reading, so a reply
    before it had nowhere to go and was lost with its reason. It opens at start
    now: the reply is a frame, with the event that explains it.
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

    async def discover(*_, **__):
        return reader

    monkeypatch.setattr("openpowerstation.jackery.discover_reader", discover)
    with Store(tmp_path / "jackery.sqlite", reserve_bytes=0) as store:
        asyncio.run(_jackery_ble_loop(store, 1, None, SERIAL, stop))
        frames = store.conn.execute("SELECT COUNT(*) FROM frames").fetchone()[0]
        kinds = [row[0] for row in store.conn.execute("SELECT kind FROM events")]
    assert reader.reads == 2
    assert frames == 2, "the reply before the first reading was not kept"
    assert "suspect_telemetry" in kinds, kinds
