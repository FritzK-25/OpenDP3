"""The control request queue both bridges hand their collectors commands through.

Each test here drives a real bridge and a real collector; only the radio is
faked. The properties under test used to hold for one device and not the other,
or for neither, because each device had its own copy of the queue:

- a request is obeyed only while 0 <= age <= 30 s, and a clock that moved
  backwards after it was queued makes it refused, not fresh for ever;
- the age is taken immediately before each request is sent, so a request that
  waited behind a slow write in the same batch is not sent late;
- requests for one control coalesce to the newest, and the older ones are
  recorded as refused rather than replayed as a burst of toggles;
- both bridges keep at most the same number of requests on disk.
"""
import asyncio
import json
import os
import time
from types import SimpleNamespace

import pytest

from openpowerstation.bridge import CONTROL_QUEUE_MAX_FILES, Bridge, control_topic
from openpowerstation.cli import _jackery_ble_loop
from openpowerstation.config import Config, save_config
from openpowerstation.jackery_bridge import JackeryBridge
from openpowerstation.protocol import Identity
from openpowerstation.runtime import CONTROL_MAX_AGE, Service
from openpowerstation.storage import Store, read_db

DP3_SERIAL = "MR51123456789012"
JACKERY_SERIAL = "856199990000000"
HOUR = 3600.0
CONTROL_KINDS = ("control", "control_refused", "control_unverified")


def settings():
    return Config("AA:BB:CC:DD:EE:FF", DP3_SERIAL, "123456", mqtt_host="broker.invalid",
                  allow_control=True)


class Clock:
    """Wall-clock time a test can step, as NTP does to a Pi without a clock battery.

    Only the wall clock moves. The monotonic clock every timeout and loop runs
    on is left alone, exactly as a real step leaves it.
    """

    def __init__(self, monkeypatch):
        self.offset_ns = 0
        real_ns, real = time.time_ns, time.time
        monkeypatch.setattr(time, "time_ns", lambda: real_ns() + self.offset_ns)
        monkeypatch.setattr(time, "time", lambda: real() + self.offset_ns / 1e9)

    def step(self, seconds):
        self.offset_ns += int(seconds * 1e9)


def written_while_the_clock_was_ahead(clock, directory, write):
    """Queue requests while the clock reads an hour ahead, then step it back.

    The files get the modification time that clock gave them too, so the
    request looks the same whichever record of its age a collector trusts.
    """
    clock.step(HOUR)
    write()
    ahead = time.time()
    for path in directory.glob("*.json"):
        os.utime(path, (ahead, ahead))
    clock.step(-HOUR)


def wait_for(predicate, timeout=5.0):
    deadline = time.monotonic() + timeout
    while not predicate():
        assert time.monotonic() < deadline, "Timed out"
        time.sleep(.02)


def control_events(database):
    if not database.exists():
        return []
    with read_db(database) as db:
        return [tuple(row) for row in db.execute(
            "SELECT kind, detail FROM events WHERE kind IN (?, ?, ?) ORDER BY id", CONTROL_KINDS)]


def deliver(bridge, key, payload):
    """Exactly what the broker hands the DP3 bridge for one non-retained publish."""
    bridge.on_message(None, None, SimpleNamespace(
        topic=control_topic(bridge.dev_id, key), payload=payload, retain=False))


def run_dp3(tmp_path, monkeypatch, stage, *, expect, on_send=lambda: None):
    """Hand the requests ``stage`` queues to a recording DP3 collector in one drain.

    The bridge writes into a staging directory that is moved into place only
    once the collector holds a session, so every staged request is read by the
    same drain. Returns what reached the radio and the control events recorded.
    """
    config = settings()
    save_config(config, tmp_path / "config.json")
    sent = []

    async def scan(*_):
        return [(Identity(config.address, config.serial, 0, 0x13), object())]

    class Session:
        def __init__(self, identity, device, uid, on_frame, on_event, allow_control=False):
            self.on_event = on_event
            self.gate = SimpleNamespace(allow_control=allow_control)

        async def run(self):
            self.on_event("connected", "test", time.time_ns(), time.monotonic_ns())
            await asyncio.Event().wait()

        async def send_control(self, field, value):
            sent.append((field, value))
            on_send()

    monkeypatch.setattr("openpowerstation.runtime.ble.scan", scan)
    monkeypatch.setattr("openpowerstation.runtime.ble.Session", Session)
    staging = tmp_path / "staging"
    stage(Bridge(config, staging / "recordings.sqlite", client=None), staging / "commands")
    database = tmp_path / "recordings.sqlite"
    service = Service(config, database, config_path=tmp_path / "config.json",
                      lock_dir=tmp_path / "locks")
    service.start()
    try:
        wait_for(lambda: service.state == "recording")
        os.replace(staging / "commands", tmp_path / "commands")
        wait_for(lambda: len(control_events(database)) >= expect)
    finally:
        service.stop()
        service.join(5)
    assert not service.is_alive()
    assert not service.error
    return sent, control_events(database)


class StationReader:
    """An Explorer that answers every poll with ``readback``; records what it is sent."""

    def __init__(self, stop, readback, on_send=lambda: None):
        self.identity = SimpleNamespace(serial=JACKERY_SERIAL)
        self.stop, self.readback, self.on_send = stop, readback, on_send
        self.sent = []

    async def read(self, **_):
        self.stop.touch()
        return dict(self.readback)

    async def send_control(self, *args):
        self.sent.append(args)
        self.on_send()

    async def close(self):
        pass


def run_jackery(tmp_path, monkeypatch, stage, readback, *, on_send=lambda: None):
    """Queue requests through the Jackery bridge, then poll the station once."""
    config = settings()
    save_config(config, tmp_path / "config.json")
    database = tmp_path / "jackery.sqlite"
    stage(JackeryBridge(config, database, serial=JACKERY_SERIAL), tmp_path / "jackery-commands")
    reader = StationReader(tmp_path / "stop", readback, on_send)

    async def discover(*_, **__):
        return reader

    monkeypatch.setattr("openpowerstation.jackery.discover_reader", discover)
    with Store(database, reserve_bytes=0) as store:
        asyncio.run(_jackery_ble_loop(store, 3, None, JACKERY_SERIAL, reader.stop,
                                      config_path=tmp_path / "config.json"))
    return reader.sent, control_events(database)


# --------------------------------------------------------------------------- DP3


def test_a_dp3_request_dated_in_the_future_is_refused_not_sent(tmp_path, monkeypatch):
    """A request issued before the clock stepped back must not become fresh for ever.

    Only ``age > 30`` used to be refused, so a request whose issue time lay in
    the future passed the expiry check at every drain: an output command that
    could run hours after anyone pressed it.
    """
    clock = Clock(monkeypatch)

    def stage(bridge, directory):
        written_while_the_clock_was_ahead(
            clock, directory, lambda: deliver(bridge, "cfg_lv_ac_out_open", b"GUARDED_OFF"))

    sent, events = run_dp3(tmp_path, monkeypatch, stage, expect=1)
    assert sent == [], "a request dated an hour ahead reached the radio"
    [(kind, detail)] = events
    assert kind == "control_refused"
    assert detail.startswith("cfg_lv_ac_out_open=off: ")
    assert "clock" in detail


def test_a_dp3_request_that_waited_behind_a_slow_write_is_refused(tmp_path, monkeypatch):
    """Each request's age is taken when it is about to be sent, not once per drain.

    The first write here takes longer than the whole lease. The second request
    was fresh when the batch was read and is stale by the time the radio is
    free, so it is refused rather than obeyed late.
    """
    clock = Clock(monkeypatch)

    def stage(bridge, _directory):
        deliver(bridge, "cfg_hv_ac_out_open", b"GUARDED_ON")
        clock.step(.001)
        deliver(bridge, "cfg_lv_ac_out_open", b"GUARDED_OFF")

    sent, events = run_dp3(tmp_path, monkeypatch, stage, expect=2,
                           on_send=lambda: clock.step(CONTROL_MAX_AGE + 1))
    assert sent == [("cfg_hv_ac_out_open", True)]
    assert events[0] == ("control", "Sent cfg_hv_ac_out_open=on.")
    kind, detail = events[1]
    assert kind == "control_refused"
    assert detail == "cfg_lv_ac_out_open=off: request expired before it could be sent."


def test_dp3_requests_for_one_output_coalesce_to_the_newest(tmp_path, monkeypatch):
    """Two presses for one output in a drain send only the second, and record the first."""
    clock = Clock(monkeypatch)

    def stage(bridge, _directory):
        deliver(bridge, "cfg_lv_ac_out_open", b"GUARDED_ON")
        clock.step(.001)
        deliver(bridge, "cfg_lv_ac_out_open", b"GUARDED_OFF")

    sent, events = run_dp3(tmp_path, monkeypatch, stage, expect=2)
    assert sent == [("cfg_lv_ac_out_open", False)], "every queued toggle reached the radio"
    assert events == [
        ("control_refused", "cfg_lv_ac_out_open=on: superseded by a newer request for the same control."),
        ("control", "Sent cfg_lv_ac_out_open=off."),
    ]


# --------------------------------------------------------------------------- Jackery


def test_a_jackery_request_dated_in_the_future_is_refused_not_sent(tmp_path, monkeypatch):
    clock = Clock(monkeypatch)

    def stage(bridge, directory):
        written_while_the_clock_was_ahead(
            clock, directory, lambda: bridge.queue_control("jackery_ac_output", "ON"))

    sent, events = run_jackery(tmp_path, monkeypatch, stage, {"rb": 50, "oac": 1})
    assert sent == [], "a request dated an hour ahead reached the station"
    [(kind, detail)] = events
    assert kind == "control_refused"
    assert detail.startswith("Jackery jackery_ac_output: ")
    assert "clock" in detail


def test_a_jackery_request_that_waited_behind_an_earlier_command_is_refused(tmp_path, monkeypatch):
    """The Jackery drain used to read the clock once and then send and verify each
    request in turn: an 8 s write deadline plus a readback apiece, so a later
    request in the batch could leave well after its lease."""
    clock = Clock(monkeypatch)

    def stage(bridge, _directory):
        bridge.queue_control("jackery_ac_output", "ON")
        clock.step(.001)
        bridge.queue_control("jackery_dc_output", "ON")

    sent, events = run_jackery(tmp_path, monkeypatch, stage, {"rb": 50, "oac": 1, "odc": 1},
                               on_send=lambda: clock.step(CONTROL_MAX_AGE + 1))
    assert sent == [("jackery_ac_output", True)]
    assert events == [
        ("control", "Jackery jackery_ac_output=True; status confirmed."),
        ("control_refused", "Jackery jackery_dc_output: request expired before it could be sent."),
    ]


def test_jackery_requests_for_one_control_coalesce_to_the_newest(tmp_path, monkeypatch):
    clock = Clock(monkeypatch)

    def stage(bridge, _directory):
        bridge.queue_control("jackery_ac_output", "ON")
        clock.step(.001)
        bridge.queue_control("jackery_ac_output", "OFF")

    sent, events = run_jackery(tmp_path, monkeypatch, stage, {"rb": 50, "oac": 0})
    assert sent == [("jackery_ac_output", False)], "every queued toggle reached the station"
    assert events == [
        ("control_refused",
         "Jackery jackery_ac_output: superseded by a newer request for the same control."),
        ("control", "Jackery jackery_ac_output=False; status confirmed."),
    ]


def test_the_jackery_bridge_bounds_its_queue_like_the_dp3_bridge(tmp_path, monkeypatch):
    """Presses while the station is away used to pile up on the data volume without limit."""
    clock = Clock(monkeypatch)
    bridge = JackeryBridge(settings(), tmp_path / "jackery.sqlite", serial=JACKERY_SERIAL)
    total = CONTROL_QUEUE_MAX_FILES + 25
    for index in range(total):
        clock.step(.001)
        bridge.queue_control("jackery_ac_output", "ON" if index == total - 1 else "OFF")
    queued = sorted((tmp_path / "jackery-commands").glob("*.json"))
    assert len(queued) == CONTROL_QUEUE_MAX_FILES
    # The oldest go first, so the newest press is the one that survives.
    assert '"value": true' in queued[-1].read_text("utf-8")


# --------------------------------------------------------------------------- the queue itself


def test_same_tick_requests_keep_the_true_latest_entry(tmp_path, monkeypatch):
    """A coarse wall clock must not make sequence 9 sort after sequence 11."""
    from openpowerstation import control_queue

    issued = 1_800_000_000_000_000_000
    monkeypatch.setattr(control_queue.time, "time_ns", lambda: issued)
    monkeypatch.setattr(control_queue, "_SEQUENCE", iter(range(12)))
    for index in range(12):
        assert control_queue.write_request(tmp_path, "same_key", index == 11)

    drained = control_queue.drain(tmp_path)
    executable = [entry for entry in drained if isinstance(entry, control_queue.Request)]
    refused = [entry for entry in drained if isinstance(entry, control_queue.Refusal)]
    assert [entry.value for entry in executable] == [True]
    assert len(refused) == 11
    assert all(entry.reason == control_queue.SUPERSEDED for entry in refused)


def test_same_tick_pruning_discards_the_true_oldest_entries(tmp_path, monkeypatch):
    """Queue bounding follows numeric sequence order across the 9 -> 10 boundary."""
    from openpowerstation import control_queue

    issued = 1_800_000_000_000_000_000
    total = CONTROL_QUEUE_MAX_FILES + 3
    monkeypatch.setattr(control_queue.time, "time_ns", lambda: issued)
    monkeypatch.setattr(control_queue, "_SEQUENCE", iter(range(total)))
    for index in range(total):
        assert control_queue.write_request(tmp_path, f"entry_{index:02d}", True)

    queued = {
        json.loads(path.read_text(encoding="utf-8"))["key"]
        for path in tmp_path.glob("*.json")
    }
    assert len(queued) == CONTROL_QUEUE_MAX_FILES
    assert {"entry_00", "entry_01", "entry_02"}.isdisjoint(queued)
    assert "entry_10" in queued
    assert f"entry_{total - 1:02d}" in queued


def test_a_request_carries_its_own_issue_time(tmp_path, monkeypatch):
    """The expiry reference travels inside the request, not in the file's metadata."""
    from openpowerstation import control_queue

    clock = Clock(monkeypatch)
    clock.step(-HOUR)
    before = time.time_ns()
    assert control_queue.write_request(tmp_path, "jackery_ac_output", True)
    after = time.time_ns()
    clock.step(HOUR)
    # The file itself was written just now, by the real clock.
    [request] = control_queue.drain(tmp_path)
    assert (request.key, request.value, request.guarded) == ("jackery_ac_output", True, False)
    assert before <= request.issued_utc_ns <= after
    assert request.lapsed() == control_queue.EXPIRED


@pytest.mark.parametrize("age,reason", [
    (0.0, None), (CONTROL_MAX_AGE, None),
    (CONTROL_MAX_AGE + .001, "expired"), (-.001, "future"),
])
def test_a_request_is_executable_only_while_its_age_is_within_the_lease(age, reason):
    from openpowerstation import control_queue

    request = control_queue.Request("cfg_lv_ac_out_open", False, issued_utc_ns=10**18)
    now = 10**18 + int(age * 1e9)
    expected = {None: None, "expired": control_queue.EXPIRED, "future": control_queue.FUTURE}[reason]
    assert request.lapsed(now_ns=now) == expected


def test_a_request_without_an_issue_time_is_refused_and_recorded(tmp_path):
    """It names a control, so it gets a record; it has no age, so it never runs."""
    from openpowerstation import control_queue

    (tmp_path / "1-a.json").write_text(
        '{"key": "cfg_lv_ac_out_open", "value": false, "guarded": true}', encoding="utf-8")
    assert control_queue.drain(tmp_path) == [
        control_queue.Refusal("cfg_lv_ac_out_open", False, control_queue.UNDATED)]
    assert list(tmp_path.glob("*.json")) == []


def test_a_request_whose_file_cannot_be_removed_is_never_executed(tmp_path, monkeypatch):
    """A drain that could not consume a file used to act on it anyway.

    The file then stayed, and every later drain -- five a second on the DP3 --
    read and sent it again until it expired: one press, up to 150 writes.
    """
    service = Service(settings(), tmp_path / "recordings.sqlite")
    bridge = Bridge(settings(), tmp_path / "recordings.sqlite", client=None)
    deliver(bridge, "cfg_lv_ac_out_open", b"GUARDED_OFF")

    def refuse(self, *_, **__):
        raise PermissionError("in use")

    monkeypatch.setattr("pathlib.Path.unlink", refuse)
    for _ in range(3):
        service.drain_control_files()
    kinds = [service.commands.get_nowait()[0] for _ in range(service.commands.qsize())]
    assert "control" not in kinds
