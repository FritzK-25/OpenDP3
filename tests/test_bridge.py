"""The Home Assistant bridge must publish only verified, live, non-synthetic readings.

No broker is involved: the client is a stand-in that records what would be sent.
"""
import json
from types import SimpleNamespace

import pytest
import portalocker

from opendp3.bridge import (
    Bridge, CONTROL_FEEDBACK_FIELDS, CONTROLS, bridge_lock, collector_state, device_id,
    discovery_payloads, entities, state_payload,
)
from opendp3.config import Config
from opendp3.decoder import FIELDS
from opendp3.queries import latest
from opendp3.recorder import Recorder
from conftest import add

SERIAL = "MR51ABCDEFGHIJKL"
USER_ID = "fixture-user-id"
PASSWORD = "fixture-broker-password"


def make_config(**overrides):
    values = dict(address="AA:BB:CC:DD:EE:FF", serial=SERIAL, user_id=USER_ID,
                  mqtt_host="192.0.2.10", mqtt_username="opendp3", mqtt_password=PASSWORD)
    values.update(overrides)
    return Config(**values)


class FakeClient:
    """Records publishes and subscriptions instead of talking to a broker."""

    def __init__(self):
        self.published = []
        self.subscribed = []
        self.connected = None
        self.loop_running = False
        self.disconnected = False

    def publish(self, topic, payload=None, qos=0, retain=False):
        self.published.append((topic, payload, retain))
        return SimpleNamespace(wait_for_publish=lambda timeout=None: None)

    def subscribe(self, topic, qos=0):
        self.subscribed.append(topic)

    def connect(self, host, port, keepalive=60):
        self.connected = (host, port)

    def loop_start(self):
        self.loop_running = True

    def loop_stop(self):
        self.loop_running = False

    def disconnect(self):
        self.disconnected = True

    def topics(self):
        return [topic for topic, _, _ in self.published]

    def last(self, suffix):
        for topic, payload, _ in reversed(self.published):
            if topic.endswith(suffix):
                return payload
        return None


# --------------------------------------------------------------------------- discovery

def test_discovery_covers_every_decoder_field_with_stable_identifiers():
    payloads = discovery_payloads(device_id(SERIAL), firmware="1.2.3")
    prefix = "opendp3_" + device_id(SERIAL) + "_"
    assert all(payload["unique_id"].startswith(prefix) for payload in payloads.values())
    published = {topic.split("/")[-2] for topic in payloads}
    assert {field.key for field in FIELDS} <= published, "a decoder field has no entity"
    assert len({payload["unique_id"] for payload in payloads.values()}) == len(payloads)
    assert len({payload["object_id"] for payload in payloads.values()}) == len(payloads)
    for payload in payloads.values():
        # Entity IDs are pinned so the Home Assistant package YAML can name them.
        assert payload["object_id"].startswith("opendp3_")
        assert payload["device"]["sw_version"] == "1.2.3"


def test_entity_classification_matches_field_semantics():
    by_key = {entity.key: entity for entity in entities()}
    assert by_key["cms_batt_temp"].device_class == "temperature"
    assert by_key["pow_in_sum_w"].device_class == "power"
    assert by_key["bms_batt_soc"].device_class == "battery"
    # State of health is not a charge level, and belongs out of the primary view.
    assert by_key["bms_batt_soh"].device_class == ""
    assert by_key["bms_batt_soh"].diagnostic
    # Raw codes and output-state bitmasks carry no verified physical meaning/unit.
    assert by_key["errcode"].diagnostic and by_key["errcode"].state_class == ""
    assert by_key["flow_info_ac_hv_out"].diagnostic
    assert by_key["flow_info_ac_lv_out"].diagnostic
    # Community mappings must not look like first-class measurements.
    assert by_key["extra1_soc"].diagnostic and by_key["extra2_temperature"].diagnostic
    assert by_key["incident"].component == "binary_sensor"


def test_measurements_and_diagnostics_use_separate_availability():
    payloads = {payload["object_id"]: payload for payload in discovery_payloads("abc").values()}

    # Diagnostics follow the bridge alone, so they stay readable while telemetry
    # is down and the reason stays visible.
    diagnostic = payloads["opendp3_collector_state"]
    assert diagnostic["availability_topic"].endswith("/availability")
    assert "availability" not in diagnostic

    # Measurements require the bridge AND telemetry. Binding them to /telemetry
    # alone held stale readings when the bridge died: the last will only marks
    # /availability offline, and the retained /telemetry stays "online".
    measurement = payloads["opendp3_bms_batt_soc"]
    assert measurement["availability_mode"] == "all"
    assert [entry["topic"] for entry in measurement["availability"]] == [
        "opendp3/abc/availability", "opendp3/abc/telemetry", "opendp3/abc/state"]
    # Mutually exclusive in Home Assistant's MQTT discovery schema.
    assert "availability_topic" not in measurement


def test_every_dp3_control_is_a_usable_home_assistant_switch():
    payloads = discovery_payloads("abc", control=True)
    controls = {payload["object_id"]: payload for payload in payloads.values()
                if payload.get("command_topic")}
    assert set(controls) == {"opendp3_" + key for key, _ in CONTROLS}
    for key, _ in CONTROLS:
        payload = controls["opendp3_" + key]
        feedback = CONTROL_FEEDBACK_FIELDS[key]
        assert payload["command_topic"] == f"opendp3/abc/control/{key}/set"
        assert payload["state_topic"] == "opendp3/abc/state"
        assert payload["payload_on"] == "ON" and payload["payload_off"] == "OFF"
        assert "optimistic" not in payload
        assert feedback in payload["value_template"]
        assert payload["entity_category"] == "config"
        assert payload["icon"].startswith("mdi:")
        assert payload["availability_mode"] == "all"
        assert {entry["topic"] for entry in payload["availability"]} == {
            "opendp3/abc/availability", "opendp3/abc/telemetry", "opendp3/abc/state"}
        assert feedback in payload["availability"][2]["value_template"]


def test_every_measurement_is_gated_on_the_bridge_being_alive():
    """A dead bridge must take every measurement with it, not just diagnostics."""
    for payload in discovery_payloads("abc").values():
        if payload.get("entity_category") == "diagnostic":
            continue
        topics = [entry["topic"] for entry in payload["availability"]]
        assert "opendp3/abc/availability" in topics, payload["object_id"]
        assert payload["availability_mode"] == "all", payload["object_id"]


# --------------------------------------------------------------------------- state

def test_live_session_publishes_observed_values(evidence, packet):
    store, recorder = evidence
    add(recorder, packet(seq=1, bms_batt_soc=80, pow_in_sum_w=1200, cms_batt_temp=24,
                         flow_info_ac_hv_out=0, flow_info_ac_lv_out=2), 0)
    reading = latest(store.path)
    payload, live = state_payload(reading, now_ns=recorder.start_utc + int(1e9))
    assert live
    assert payload["bms_batt_soc"] == 80
    assert payload["pow_in_sum_w"] == 1200
    assert payload["flow_info_ac_hv_out"] == 0
    assert payload["flow_info_ac_lv_out"] == 2
    assert payload["collector_state"] == "recording"
    assert payload["frame_count"] == 1
    assert payload["last_frame"].startswith("20")
    # A field the device never reported stays absent rather than being invented.
    assert payload["extra1_soc"] is None


def test_soc_is_rounded_before_mqtt_publication(evidence, packet):
    store, recorder = evidence
    add(recorder, packet(seq=1, bms_batt_soc=79.64, cms_batt_soc=80.46), 0)
    reading = latest(store.path)
    payload, live = state_payload(reading, now_ns=recorder.start_utc + int(1e9))
    assert live
    assert payload["bms_batt_soc"] == 79.6
    assert payload["cms_batt_soc"] == 80.5


def test_bridge_lock_rejects_a_second_publisher(tmp_path):
    first = bridge_lock(tmp_path / "recordings.sqlite")
    second = bridge_lock(tmp_path / "recordings.sqlite")
    first.acquire()
    try:
        with pytest.raises(portalocker.exceptions.LockException):
            second.acquire()
    finally:
        first.release()


def test_uncertain_repeats_are_never_published(evidence, packet):
    store, recorder = evidence
    add(recorder, packet(seq=1, bms_batt_soc=80), 0)
    repeat = packet(seq=2, bms_batt_soc=79)
    add(recorder, repeat, 100)
    add(recorder, repeat, 101)  # An unverified retransmit must not refresh the reading.
    reading = latest(store.path)
    assert reading["values"]["bms_batt_soc"]["t"] == 100
    assert reading["values"]["bms_batt_soc"]["quality"] != "repeated_unverified"


def test_synthetic_recording_never_reaches_home_assistant(evidence, packet):
    store, recorder = evidence
    demo = Recorder(store, utc_ns=recorder.start_utc + 1000, mono_ns=0, synthetic=True)
    add(demo, packet(seq=1, bms_batt_soc=42, pow_out_sum_w=900), 0)
    reading = latest(store.path)
    assert reading["session"]["synthetic"]
    payload, live = state_payload(reading, now_ns=demo.start_utc + int(1e9))
    assert not live
    assert payload["collector_state"] == "synthetic"
    # Demo values would otherwise land in long-term statistics, which survive purges.
    assert payload["bms_batt_soc"] is None and payload["pow_out_sum_w"] is None
    assert payload["frame_count"] is None


@pytest.mark.parametrize("age,expected", [(1, "recording"), (600, "stale")])
def test_silent_capture_is_reported_rather_than_held(evidence, packet, age, expected):
    store, recorder = evidence
    add(recorder, packet(seq=1, bms_batt_soc=80), 0)
    reading = latest(store.path)
    payload, live = state_payload(reading, now_ns=recorder.start_utc + int(age * 1e9))
    assert payload["collector_state"] == expected
    assert live is (expected == "recording")
    # A stale reading must not be published as if it were current.
    assert (payload["bms_batt_soc"] == 80) is live


def test_finished_session_reports_its_own_status(evidence, packet):
    store, recorder = evidence
    add(recorder, packet(seq=1, bms_batt_soc=80), 0)
    recorder.finish("stopped", mono_ns=recorder.start_mono + int(1e9))
    reading = latest(store.path)
    state, live = collector_state(reading, now_ns=recorder.start_utc + int(1e9))
    assert (state, live) == ("stopped", False)


def test_no_sessions_yields_nothing_to_publish(tmp_path):
    from opendp3.storage import Store
    with Store(tmp_path / "empty.sqlite", reserve_bytes=0) as store:
        assert latest(store.path) == {}


# --------------------------------------------------------------------------- client

def bridge_with_fake(database, *, stale_seconds=1e9, **overrides):
    """The evidence fixture pins its clock to 2001; staleness has its own test."""
    client = FakeClient()
    return Bridge(make_config(**overrides), database, client=client,
                  stale_seconds=stale_seconds), client


def test_publish_cycle_announces_then_reports_state(evidence, packet):
    store, recorder = evidence
    add(recorder, packet(seq=1, bms_batt_soc=80), 0)
    bridge, client = bridge_with_fake(store.path)
    bridge.run(once=True)
    assert client.connected == ("192.0.2.10", 1883)
    discovery = [t for t in client.topics() if t.startswith("homeassistant/")]
    # Control is off by default, so the only configured entities are the sensors.
    # The switch topics are still addressed, but to clear any retained config a
    # previous run with control enabled may have left behind.
    switches = [t for t in discovery if t.startswith("homeassistant/switch/")]
    assert len(discovery) - len(switches) == len(entities())
    assert len(switches) == 2
    assert all(payload == "" for topic, payload, _ in client.published
               if topic.startswith("homeassistant/switch/"))
    assert all(retain for topic, _, retain in client.published if topic.startswith("homeassistant/"))
    assert json.loads(client.last("/state"))["bms_batt_soc"] is not None
    # Clean shutdown marks the bridge offline rather than leaving a stale retained value.
    assert client.published[-1][1] == "offline"
    assert client.published[-1][0].endswith("/availability")
    assert not client.loop_running and client.disconnected


def test_connect_publishes_online_and_watches_for_the_birth_message(evidence):
    store, _ = evidence
    bridge, client = bridge_with_fake(store.path)
    bridge.on_connect(client, None, None, 0)
    assert "homeassistant/status" in client.subscribed
    assert (bridge.base + "/availability", "online", True) in client.published


def test_home_assistant_restart_replays_discovery(evidence, packet):
    store, recorder = evidence
    add(recorder, packet(seq=1, bms_batt_soc=80), 0)
    bridge, client = bridge_with_fake(store.path)
    bridge.publish_once()
    assert bridge.announced
    before = len(client.published)
    bridge.on_message(client, None, SimpleNamespace(topic="homeassistant/status", payload=b"online"))
    assert not bridge.announced, "a restarted Home Assistant must be re-announced to"
    bridge.publish_once()
    assert len(client.published) - before > len(entities())


def test_telemetry_availability_is_published_only_on_change(evidence, packet):
    store, recorder = evidence
    add(recorder, packet(seq=1, bms_batt_soc=80), 0)
    bridge, client = bridge_with_fake(store.path)
    bridge.publish_once()
    bridge.publish_once()
    assert [t for t, _, _ in client.published if t.endswith("/telemetry")] == [bridge.base + "/telemetry"]


def test_unreadable_database_does_not_stop_the_bridge(tmp_path):
    bridge, client = bridge_with_fake(tmp_path / "missing.sqlite")
    assert bridge.publish_once() is None
    assert bridge.announced, "entities should still exist so the fault is visible"
    assert (bridge.base + "/telemetry", "offline", True) in client.published


def test_missing_broker_is_rejected_before_any_work(evidence):
    store, _ = evidence
    bridge, _ = bridge_with_fake(store.path, mqtt_host="")
    with pytest.raises(ValueError, match="No MQTT broker"):
        bridge.run(once=True)


def test_last_will_is_registered_before_connecting(evidence):
    """An unclean exit must still mark the device offline."""
    store, _ = evidence
    client = Bridge(make_config(), store.path).build_client()
    assert client._will_topic == (b"opendp3/" + device_id(SERIAL).encode() + b"/availability")
    assert client._will_payload == b"offline"


# --------------------------------------------------------------------------- disclosure

def test_nothing_published_discloses_the_serial_or_any_credential(evidence, packet):
    store, recorder = evidence
    add(recorder, packet(seq=1, bms_batt_soc=80, cms_batt_temp=25), 0)
    bridge, client = bridge_with_fake(store.path, firmware="1.2.3", conditions="garage, private")
    bridge.on_connect(client, None, None, 0)
    bridge.run(once=True)
    wire = "\n".join(topic + " " + str(payload) for topic, payload, _ in client.published)
    assert wire, "nothing was published"
    for secret in (SERIAL, USER_ID, PASSWORD, "garage, private"):
        assert secret not in wire, "bridge disclosed a private value"
    # The device is addressed by the same hash the collector uses for its lock.
    assert device_id(SERIAL) in wire


# --------------------------------------------------------------------------- transition log

def test_an_expired_field_is_logged_while_the_stream_stays_healthy(evidence, packet, capsys):
    """The flap that otherwise leaves no trace anywhere.

    The DP3 keeps streaming, so collector_state stays 'recording' and telemetry
    stays online; only bms_batt_soc ages past the stale window and is published
    as null, taking one Home Assistant entity offline. Without a line naming the
    field and its age this has to be inferred from BlueZ status.
    """
    store, recorder = evidence
    # SOC observed once at t=0; power keeps arriving up to t=100.
    add(recorder, packet(seq=1, bms_batt_soc=80, pow_out_sum_w=120), 0)
    for index, t in enumerate(range(5, 105, 5), start=2):
        add(recorder, packet(seq=index, pow_out_sum_w=120 + t), t)

    bridge, _ = bridge_with_fake(store.path, stale_seconds=45)
    # "Now" is one second after the newest frame: the stream is fresh, the SOC
    # observation is 101 seconds old.
    import opendp3.bridge as module
    real_time_ns = module.time.time_ns
    module.time.time_ns = lambda: recorder.start_utc + int(101e9)
    try:
        payload = bridge.publish_once()
    finally:
        module.time.time_ns = real_time_ns

    assert payload["collector_state"] == "recording"
    assert payload["pow_out_sum_w"] is not None
    assert payload["bms_batt_soc"] is None

    log = capsys.readouterr().out
    assert "expired fields: - -> bms_batt_soc" in log, log
    # The age of the field and the age of the stream must both be visible, or
    # the line cannot distinguish per-field expiry from a dead collector.
    assert "bms_batt_soc=101s" in log, log
    assert "last frame 1s" in log, log


def test_the_transition_log_is_quiet_when_nothing_changes(evidence, packet, capsys):
    """One line per change, not per publish, or the add-on log is unreadable."""
    store, recorder = evidence
    add(recorder, packet(seq=1, bms_batt_soc=80), 0)
    bridge, _ = bridge_with_fake(store.path)
    bridge.publish_once()
    capsys.readouterr()
    for _ in range(5):
        bridge.publish_once()
    assert capsys.readouterr().out == ""


def test_the_transition_log_never_names_the_serial(evidence, packet, capsys):
    store, recorder = evidence
    add(recorder, packet(seq=1, bms_batt_soc=80), 0)
    bridge, _ = bridge_with_fake(store.path)
    bridge.run(once=True)
    log = capsys.readouterr().out
    assert log, "expected at least one transition line"
    assert SERIAL not in log
    assert PASSWORD not in log
    assert USER_ID not in log


def test_a_telemetry_outage_is_not_logged_as_a_fresh_link(evidence, packet, capsys):
    """The line that sent a real investigation down the wrong path.

    During the 2026-09-19 DP3 outage the bridge logged 'last frame 6s;
    bms_batt_soc=121s'. Both numbers were true: the device had stopped uploading
    telemetry entirely but kept emitting src=0x35 cmd_set=0x32 housekeeping,
    which reset frame age. Read alone, frame age said the link was healthy and
    one property had aged out, when in fact the whole stream was down.
    """
    from opendp3.vendor.packet import Packet
    store, recorder = evidence
    add(recorder, packet(seq=1, bms_batt_soc=80, pow_out_sum_w=120), 0)
    # 120s later: a frame arrives, but it carries no measurement at all.
    housekeeping = Packet(0x35, 0x21, 0x32, 0x07, b"housekeeping").to_bytes()
    add(recorder, housekeeping, 120)

    bridge, _ = bridge_with_fake(store.path, stale_seconds=45)
    import opendp3.bridge as module
    real_time_ns = module.time.time_ns
    module.time.time_ns = lambda: recorder.start_utc + int(126e9)
    try:
        bridge.publish_once()
    finally:
        module.time.time_ns = real_time_ns

    log = capsys.readouterr().out
    # Frame age stays low because the housekeeping frame is only 6s old...
    assert "last frame 6s" in log, log
    # ...so the telemetry age has to be there too, or the outage is invisible.
    assert "last telemetry 126s" in log, log
