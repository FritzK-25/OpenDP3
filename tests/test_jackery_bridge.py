"""The Jackery bridge must publish local BLE recordings, and label them honestly.

No broker and no radio are involved: the client is a stand-in that records what
would be sent, and the observation is the property map the Explorer 1000 v2
actually returned over local BLE.
"""
import contextlib
import json
import time
from types import SimpleNamespace

import pytest

from openpowerstation import jackery_bridge
from openpowerstation.config import Config
from openpowerstation.jackery import JACKERY_CONTROLS
from openpowerstation.jackery_bridge import JackeryBridge, discovery_payloads, state_payload
from openpowerstation.jackery_fields import map_properties
from openpowerstation.recorder import Recorder
from openpowerstation.storage import Store

SERIAL = "123456789012345"

# Captured from the installed unit, 2026-09-04, over local BLE.
BLE_PROPERTIES = {"acip": 0, "acohz": 0, "acov": 0, "acpsp": 0, "acpss": 0, "ast": 120,
                  "bs": 0, "bt": 290, "cip": 0, "cs": 0, "ec": 0, "ip": 0, "it": 0,
                  "lm": 0, "lps": 0, "oac": 1, "odc": 0, "op": 154, "ot": 999,
                  "pal": 0, "pm": 720, "pmb": 0, "rb": 100, "sfc": 0, "sltb": 1, "ta": 0}


class FakeClient:
    def __init__(self):
        self.published = []
        self.subscribed = []

    def publish(self, topic, payload=None, qos=0, retain=False):
        self.published.append((topic, payload, retain))
        return SimpleNamespace(wait_for_publish=lambda timeout=None: None)

    def subscribe(self, topic, qos=0):
        self.subscribed.append((topic, qos))


@contextlib.contextmanager
def record_ble_observation(path, properties=BLE_PROPERTIES):
    """Write one local-BLE observation the way `jackery-record` writes it."""
    with Store(path, reserve_bytes=0) as store:
        recorder = Recorder(store, firmware=f"Jackery Explorer 1000 v2 {SERIAL}",
                            conditions="Jackery local BLE transport")
        fields = {"device": {"serial": SERIAL, "model": "Explorer 1000 v2", "transport": "ble"},
                  "properties": properties}
        raw = json.dumps(fields, ensure_ascii=False, sort_keys=True).encode()
        recorder.ingest_observation(raw, fields, map_properties(properties),
                                    quality="ble_observed")
        yield store


def published_state(path):
    client = FakeClient()
    bridge = JackeryBridge(Config(address="AA:BB:CC:DD:EE:FF", serial=SERIAL, user_id="0",
                                  mqtt_host="broker.invalid"), path, serial=SERIAL,
                           client=client)
    bridge.publish_once()
    state = next(payload for topic, payload, _ in client.published
                 if topic.endswith("/state"))
    return json.loads(state), client


def test_local_ble_recording_reaches_home_assistant(tmp_path):
    path = tmp_path / "jackery.sqlite"
    with record_ble_observation(path):
        payload, client = published_state(path)
    assert payload["collector_state"] == "recording"
    assert payload["bms_batt_soc"] == 100.0
    assert payload["cms_batt_temp"] == 29.0
    assert payload["pow_out_sum_w"] == 154.0
    # Switch/select entities publish user-facing states, not raw protocol enums.
    assert payload["jackery_ac_output"] == "ON"
    assert payload["jackery_dc_output"] == "OFF"
    assert payload["jackery_battery_save"] == "full"
    assert payload["jackery_screen_timeout"] == "always_on"
    assert any(topic.endswith("/telemetry") and payload_ == "online"
               for topic, payload_, _ in client.published)


def test_every_mapped_property_reaches_its_own_entity(tmp_path):
    """A field the station reports must not vanish between mapping and publication.

    The low-power branch derived the Battery Saving select and dropped the raw
    reading, so the discovered Low-power mode sensor sat unavailable in
    production while the recorder logged lps on every poll.
    """
    path = tmp_path / "jackery.sqlite"
    properties = dict(BLE_PROPERTIES, lps=1)
    with record_ble_observation(path, properties):
        payload, _ = published_state(path)
    assert [key for key in map_properties(properties) if payload.get(key) is None] == []
    # One observation feeds both the raw sensor and the select derived from it.
    assert payload["jackery_low_power_mode"] == 1.0
    assert payload["jackery_battery_save"] == "save"


def published_from(properties, now=1_000_000_000):
    """The state a live reading of these station properties publishes."""
    reading = {"session": {"synthetic": False, "status": "recording"},
               "last_utc_ns": now, "count": 1,
               "values": {key: {"value": value, "quality": "ble_observed", "utc_ns": now, "t": 0.0}
                          for key, value in map_properties(properties).items()}}
    payload, live = state_payload(reading, now_ns=now)
    assert live
    return payload


@pytest.mark.parametrize("wire,raw", [
    ("lps", 0), ("lps", 1), ("lps", 2), ("lps", 7), ("lps", 1.5),
    ("cs", 1), ("cs", 2), ("pm", 720), ("pm", 60), ("sltb", 3),
])
def test_a_select_publishes_one_of_its_options_or_nothing(wire, raw):
    """Home Assistant rejects a select state outside its options.

    It logs an error and keeps showing the previous option as the current one,
    so a reading that names no option must make the select unavailable rather
    than stale. lps=2 used to publish "custom", any other value "unknown", and
    a fractional value was truncated into a real option.
    """
    payload = published_from(dict(BLE_PROPERTIES, **{wire: raw}))
    options = {config["object_id"]: config["options"]
               for config in discovery_payloads(SERIAL).values() if "options" in config}
    assert set(options) == {control for control, spec in JACKERY_CONTROLS.items()
                            if spec["kind"] == "select"}
    for select, choices in options.items():
        assert payload[select] is None or payload[select] in choices, (select, payload[select])
    if wire == "lps":
        # The raw reading stays visible even when it names no option.
        assert payload["jackery_low_power_mode"] == raw
        assert payload["jackery_battery_save"] == {0: "full", 1: "save"}.get(raw)


def test_every_select_reads_back_from_the_field_its_command_writes():
    """The readback table cannot drift from the controls or the property map."""
    readback = jackery_bridge.SELECT_READBACK
    selects = {control for control, spec in JACKERY_CONTROLS.items() if spec["kind"] == "select"}
    assert set(readback.values()) == selects
    for control in selects:
        spec = JACKERY_CONTROLS[control]
        mapped = map_properties({spec["wire"]: next(iter(spec["values"].values()))})
        assert len(mapped) == 1, control
        assert readback[next(iter(mapped))] == control


def test_the_bridge_backfill_keeps_the_recorded_transport(tmp_path, monkeypatch):
    """publish_once re-derives mapped fields from the stored raw frame.

    Those backfilled entries used to be hard-coded `cloud_observed`, which would
    have recorded a Jackery account as the source of a reading that came off the
    radio. The label never reaches MQTT, but it must not be wrong in the record.
    """
    import openpowerstation.jackery_bridge as module
    captured = {}
    original = module.state_payload

    def capture(reading, *args, **kwargs):
        captured.update(reading.get("values", {}))
        return original(reading, *args, **kwargs)

    monkeypatch.setattr(module, "state_payload", capture)
    path = tmp_path / "jackery.sqlite"
    with record_ble_observation(path):
        published_state(path)
    assert captured, "the bridge published nothing to inspect"
    assert {value["quality"] for value in captured.values()} == {"ble_observed"}


def test_a_neighbouring_serial_is_never_published(tmp_path):
    """The database is keyed by serial: another station's session must not leak."""
    path = tmp_path / "jackery.sqlite"
    with record_ble_observation(path):
        client = FakeClient()
        bridge = JackeryBridge(Config(address="AA:BB:CC:DD:EE:FF", serial=SERIAL, user_id="0",
                                  mqtt_host="broker.invalid"), path,
                               serial="123456789099999", client=client)
        bridge.publish_once()
    payload = json.loads(next(p for t, p, _ in client.published if t.endswith("/state")))
    assert payload["collector_state"] == "offline"
    assert payload["bms_batt_soc"] is None


def test_enabled_jackery_bridge_announces_and_queues_controls(tmp_path):
    database = tmp_path / "jackery.sqlite"
    client = FakeClient()
    config = Config(address="AA:BB:CC:DD:EE:FF", serial=SERIAL, user_id="0",
                    mqtt_host="broker.invalid", allow_control=True)
    bridge = JackeryBridge(config, database, serial=SERIAL, client=client)
    bridge.on_connect(client, None, None, 0)
    assert client.subscribed == [(f"{bridge.base}/control/+/set", 1), ("homeassistant/status", 1)]
    assert (bridge.base + "/control-availability", "online", True) in client.published
    before = time.time_ns()
    bridge.on_message(client, None, SimpleNamespace(
        topic=f"{bridge.base}/control/jackery_battery_save/set", payload=b"save"))
    request = json.loads(next((tmp_path / "jackery-commands").glob("*.json")).read_text("utf-8"))
    issued = request.pop("issued_utc_ns")
    assert request == {"key": "jackery_battery_save", "value": "save"}
    # Dated by the bridge as it queues, for the collector to age it by.
    assert before <= issued <= time.time_ns()


def test_every_jackery_control_stays_discovered_even_when_writes_are_disabled():
    # Control definitions are structural. Whether they can act is a separate
    # retained availability topic, so a rebuild or BLE outage cannot delete the
    # entity from dashboards and produce "Entity not found".
    payloads = discovery_payloads(SERIAL)
    controls = {payload["object_id"]: payload for payload in payloads.values()
                if payload.get("command_topic")}
    assert set(controls) == set(JACKERY_CONTROLS)
    for key, spec in JACKERY_CONTROLS.items():
        payload = controls[key]
        dev_id = payload["device"]["identifiers"][0][8:]
        assert payload["default_entity_id"] == f"{payload['default_entity_id'].split('.', 1)[0]}.{key}"
        assert payload["command_topic"] == f"jackery/{dev_id}/control/{key}/set"
        assert payload["entity_category"] == "config"
        assert payload["icon"].startswith("mdi:")
        assert payload["availability_mode"] == "all"
        assert {entry["topic"] for entry in payload["availability"]} == {
            f"jackery/{dev_id}/availability", f"jackery/{dev_id}/telemetry",
            f"jackery/{dev_id}/state", f"jackery/{dev_id}/control-availability",
        }
        if spec["kind"] == "switch":
            assert payload["payload_on"] == "ON" and payload["payload_off"] == "OFF"
            assert payload["device_class"] == "outlet"
            assert "options" not in payload
        else:
            assert payload["options"] == list(spec["values"])
            assert "payload_on" not in payload


def test_disabled_jackery_bridge_keeps_controls_unavailable_instead_of_deleting_them(tmp_path):
    client = FakeClient()
    config = Config(address="AA:BB:CC:DD:EE:FF", serial=SERIAL, user_id="0",
                    mqtt_host="broker.invalid", allow_control=False)
    bridge = JackeryBridge(config, tmp_path / "jackery.sqlite", serial=SERIAL, client=client)
    bridge.on_connect(client, None, None, 0)
    assert client.subscribed == [("homeassistant/status", 1)]
    assert (bridge.base + "/control-availability", "offline", True) in client.published
    bridge.publish_discovery()
    control_configs = [(topic, payload) for topic, payload, retain in client.published
                       if ("/switch/" in topic or "/select/" in topic)
                       and topic.endswith("/config") and payload]
    assert len(control_configs) >= len(JACKERY_CONTROLS)
    assert not any(payload == "" and ("/switch/" in topic or "/select/" in topic) and (
        "/jackery_ac_output/config" in topic or
        "/jackery_dc_output/config" in topic or
        "/jackery_battery_save/config" in topic or
        "/jackery_screen_timeout/config" in topic)
        for topic, payload, _ in client.published)


def test_jackery_reannounces_after_home_assistant_restart(tmp_path):
    client = FakeClient()
    config = Config(address="AA:BB:CC:DD:EE:FF", serial=SERIAL, user_id="0",
                    mqtt_host="broker.invalid", allow_control=True)
    bridge = JackeryBridge(config, tmp_path / "jackery.sqlite", serial=SERIAL, client=client)
    bridge.announced = True
    bridge.on_message(client, None, SimpleNamespace(topic="homeassistant/status", payload=b"online"))
    assert not bridge.announced


def test_jackery_discovery_clears_legacy_binary_sensor_controls(tmp_path):
    client = FakeClient()
    config = Config(address="AA:BB:CC:DD:EE:FF", serial=SERIAL, user_id="0",
                    mqtt_host="broker.invalid", allow_control=True)
    bridge = JackeryBridge(config, tmp_path / "jackery.sqlite", serial=SERIAL, client=client)
    bridge.publish_discovery()
    cleared = {topic for topic, payload, retain in client.published
               if topic.startswith("homeassistant/binary_sensor/") and payload == "" and retain}
    assert cleared == {
        f"homeassistant/binary_sensor/jackery_{bridge.dev_id}/jackery_ac_output/config",
        f"homeassistant/binary_sensor/jackery_{bridge.dev_id}/jackery_dc_output/config",
    }


def test_disabled_jackery_bridge_does_not_queue_controls(tmp_path):
    database = tmp_path / "jackery.sqlite"
    client = FakeClient()
    config = Config(address="AA:BB:CC:DD:EE:FF", serial=SERIAL, user_id="0",
                    mqtt_host="broker.invalid", allow_control=False)
    bridge = JackeryBridge(config, database, serial=SERIAL, client=client)
    bridge.on_message(client, None, SimpleNamespace(
        topic=f"{bridge.base}/control/jackery_battery_save/set", payload=b"save"))
    assert not (tmp_path / "jackery-commands").exists()


def test_an_unchanged_reading_is_not_republished(tmp_path):
    """Retained topics already hold the value; resending it every second is noise."""
    path = tmp_path / "jackery.sqlite"
    with record_ble_observation(path):
        client = FakeClient()
        bridge = JackeryBridge(Config(address="AA:BB:CC:DD:EE:FF", serial=SERIAL, user_id="0",
                                      mqtt_host="broker.invalid"), path, serial=SERIAL,
                               client=client)
        bridge.publish_once()
        first = len(client.published)
        bridge.publish_once()
        bridge.publish_once()
    assert len(client.published) == first, "an unchanged payload was published again"
    assert any(t.endswith("/state") for t, _, _ in client.published)


def test_a_recorder_that_died_stops_reading_as_live(tmp_path):
    """A session left at status='recording' must not publish frozen values for ever.

    The recorder marks its session finished on a clean stop, so a session still
    claiming to record with no recent frame means the process died. At a 3s poll
    those stale numbers would otherwise look second-fresh in Home Assistant.
    """
    import time as clock
    from openpowerstation.jackery_bridge import STALE_SECONDS, state_payload
    path = tmp_path / "jackery.sqlite"
    with record_ble_observation(path):
        payload, _ = published_state(path)
        assert payload["collector_state"] == "recording"
        # What latest() returns once that process is gone: its last reading, aging.
        last = clock.time_ns()
        reading = {"session": {"synthetic": False, "status": "recording"}, "count": 1,
                   "last_utc_ns": last,
                   "values": {"bms_batt_soc": {"value": 100.0, "quality": "ble_observed",
                                               "utc_ns": last, "t": 0.0}}}
        aged = clock.time_ns() + int((STALE_SECONDS + 5) * 1e9)
        stale_payload, live = state_payload(reading, now_ns=aged)
    assert not live
    assert stale_payload["collector_state"] == "stale"
    assert stale_payload["bms_batt_soc"] is None, "stale values were still published"


def ingest(recorder, properties, *, serial=SERIAL, t=None):
    """Record one reply the way `jackery-record` does; ``t`` seconds into the session."""
    fields = {"device": {"serial": serial, "model": "Explorer 1000 v2", "transport": "ble"},
              "properties": properties}
    at = {} if t is None else {"utc_ns": recorder.start_utc + int(t * 1e9),
                               "mono_ns": recorder.start_mono + int(t * 1e9)}
    recorder.ingest_observation(json.dumps(fields, sort_keys=True).encode(), fields,
                                map_properties(properties), quality="ble_observed", **at)


def test_a_collector_still_searching_reads_as_waiting_not_stopped(tmp_path):
    """The collector opens its session before it finds the Explorer.

    Until its first frame, that session has nothing to prove whose it is, and
    was skipped: the bridge published the finished session before it as
    "stopped" for as long as the search lasted -- hours, in production -- as
    though the collector had been stopped on purpose.
    """
    path = tmp_path / "jackery.sqlite"
    with Store(path, reserve_bytes=0) as store:
        earlier = Recorder(store, firmware=f"Jackery Explorer 1000 v2 {SERIAL}")
        ingest(earlier, BLE_PROPERTIES)
        earlier.finish("stopped")
        searching = Recorder(store, firmware=f"Jackery Explorer 1000 v2 {SERIAL}",
                             conditions="Jackery local BLE transport")
        payload, client = published_state(path)
        assert payload["collector_state"] == "waiting", payload["collector_state"]
        assert payload["bms_batt_soc"] is None
        assert payload["last_frame"] is None
        assert ("jackery/" + jackery_bridge.device_id(SERIAL) + "/telemetry", "offline", True
                ) in client.published
        # Found: the same session now carries the reading, and it is published.
        ingest(searching, BLE_PROPERTIES)
        payload, _ = published_state(path)
    assert payload["collector_state"] == "recording"
    assert payload["bms_batt_soc"] == 100.0


def test_another_explorers_empty_session_is_not_taken_for_ours(tmp_path):
    """Only a session opened for this serial can stand for its collector."""
    path = tmp_path / "jackery.sqlite"
    with Store(path, reserve_bytes=0) as store:
        ours = Recorder(store, firmware=f"Jackery Explorer 1000 v2 {SERIAL}")
        ingest(ours, BLE_PROPERTIES)
        ours.finish("stopped")
        Recorder(store, firmware="Jackery Explorer 1000 v2 856124082499999")
        payload, _ = published_state(path)
    assert payload["collector_state"] == "stopped"


def test_settings_alone_do_not_keep_the_jackery_live(tmp_path):
    """Liveness follows the core telemetry, as the DP3 bridge's has since #201.

    It followed the newest frame of any kind, so a station answering only
    settings fields kept collector_state "recording" and telemetry online while
    SOC and power aged out underneath it.
    """
    from openpowerstation.queries import latest
    path = tmp_path / "jackery.sqlite"
    with Store(path, reserve_bytes=0) as store:
        recorder = Recorder(store, firmware=f"Jackery Explorer 1000 v2 {SERIAL}")
        ingest(recorder, BLE_PROPERTIES, t=0)
        ingest(recorder, {"sltb": 1, "ec": 0, "pmb": 0}, t=40)
        reading = latest(path, recorder.sid,
                         keys=[entity.key for entity in jackery_bridge.jackery_entities()])
        payload, live = state_payload(reading, now_ns=recorder.start_utc + 41 * 10**9)
    assert not live, "settings alone kept the Jackery telemetry online"
    assert payload["collector_state"] == "stale"
    assert payload["jackery_screen_timeout"] is None


def test_the_core_telemetry_keys_are_defined_once():
    """The collector, the bridge and the supervisor's lease all key health on these."""
    from openpowerstation.jackery_fields import JACKERY_CORE_KEYS
    assert jackery_bridge.LOGGED_FIELD_AGES is JACKERY_CORE_KEYS
    published = {entity.key for entity in jackery_bridge.jackery_entities()}
    assert set(JACKERY_CORE_KEYS) <= published
    assert set(JACKERY_CORE_KEYS) <= set(map_properties(BLE_PROPERTIES))
