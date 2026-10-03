"""The Jackery bridge must publish local BLE recordings, and label them honestly.

No broker and no radio are involved: the client is a stand-in that records what
would be sent, and the observation is the property map the Explorer 1000 v2
actually returned over local BLE.
"""
import contextlib
import json
from types import SimpleNamespace

from opendp3.config import Config
from opendp3.jackery import JACKERY_CONTROLS
from opendp3.jackery_bridge import JackeryBridge, discovery_payloads
from opendp3.jackery_fields import map_properties
from opendp3.recorder import Recorder
from opendp3.storage import Store

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


def test_the_bridge_backfill_keeps_the_recorded_transport(tmp_path, monkeypatch):
    """publish_once re-derives mapped fields from the stored raw frame.

    Those backfilled entries used to be hard-coded `cloud_observed`, which would
    have recorded a Jackery account as the source of a reading that came off the
    radio. The label never reaches MQTT, but it must not be wrong in the record.
    """
    import opendp3.jackery_bridge as module
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
    bridge.on_message(client, None, SimpleNamespace(
        topic=f"{bridge.base}/control/jackery_battery_save/set", payload=b"save"))
    request = next((tmp_path / "jackery-commands").glob("*.json"))
    assert json.loads(request.read_text("utf-8")) == {
        "control": "jackery_battery_save", "value": "save"
    }


def test_jackery_control_queue_is_bounded_without_collector(tmp_path, monkeypatch):
    """The recorder drains only while attached, so presses must not pile up."""
    from opendp3.bridge import CONTROL_QUEUE_MAX_FILES
    database = tmp_path / "jackery.sqlite"
    config = Config(address="AA:BB:CC:DD:EE:FF", serial=SERIAL, user_id="0",
                    mqtt_host="broker.invalid", allow_control=True)
    bridge = JackeryBridge(config, database, serial=SERIAL, client=FakeClient())
    total = CONTROL_QUEUE_MAX_FILES + 25
    ticks = iter(10**18 + index for index in range(total))
    monkeypatch.setattr("opendp3.bridge.time.time_ns", lambda: next(ticks))

    for index in range(total):
        bridge.queue_control("jackery_ac_output", "ON" if index == total - 1 else "OFF")

    queued = sorted((tmp_path / "jackery-commands").glob("*.json"))
    assert len(queued) == CONTROL_QUEUE_MAX_FILES
    assert queued[0].name.startswith(str(10**18 + total - CONTROL_QUEUE_MAX_FILES))
    assert json.loads(queued[-1].read_text("utf-8")) == {
        "control": "jackery_ac_output", "value": True
    }


def test_every_jackery_control_stays_discovered_even_when_writes_are_disabled():
    # Control definitions are structural. Whether they can act is a separate
    # retained availability topic, so a rebuild or BLE outage cannot delete the
    # entity from dashboards and produce "Entity not found".
    payloads = discovery_payloads(SERIAL, control=False)
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


def test_jackery_periodically_refreshes_discovery(tmp_path, monkeypatch):
    import opendp3.jackery_bridge as module
    client = FakeClient()
    config = Config(address="AA:BB:CC:DD:EE:FF", serial=SERIAL, user_id="0",
                    mqtt_host="broker.invalid", allow_control=True)
    bridge = JackeryBridge(config, tmp_path / "jackery.sqlite", serial=SERIAL, client=client)
    clock = iter((10.0, 71.0, 71.0))
    monkeypatch.setattr(module.time, "monotonic", lambda: next(clock))
    bridge.publish_once()
    first = len([t for t, _, _ in client.published if t.startswith("homeassistant/")])
    bridge.publish_once()
    second = len([t for t, _, _ in client.published if t.startswith("homeassistant/")])
    assert second > first


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
    from opendp3.jackery_bridge import STALE_SECONDS, state_payload
    path = tmp_path / "jackery.sqlite"
    with record_ble_observation(path):
        payload, _ = published_state(path)
        assert payload["collector_state"] == "recording"
        reading = {"session": {"status": "recording"}, "count": 1,
                   "last_utc_ns": clock.time_ns(), "values": {}}
        aged = clock.time_ns() + int((STALE_SECONDS + 5) * 1e9)
        stale_payload, live = state_payload(reading, now_ns=aged)
    assert not live
    assert stale_payload["collector_state"] == "stale"
    assert stale_payload["bms_batt_soc"] is None, "stale values were still published"
