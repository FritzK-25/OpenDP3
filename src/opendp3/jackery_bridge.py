"""Home Assistant MQTT publisher and guarded command relay for Jackery recordings."""
import contextlib
import json
import math
from pathlib import Path
import time

from . import __version__
from .bridge import (DISCOVERY_PREFIX, STATUS_TOPIC, Entity, as_timestamp, device_id,
                     expired_fields, field_age_summary, field_availability,
                     log_transition, observation_fresh, value_template)
from .jackery import JACKERY_CONTROLS, jackery_control_value
from .jackery_fields import JACKERY_FIELDS, map_properties
from .queries import latest
from .storage import read_db

# A recorder that dies without finishing its session leaves status='recording',
# and the bridge would otherwise republish frozen values as live for ever. At a
# 3s poll rate that reads as second-fresh truth, so it has to be caught. Ten
# missed polls is late enough not to trip on ordinary reattach jitter.
STALE_SECONDS = 30.0
# MQTT discovery is retained, but a periodic refresh makes the bridge self-heal
# if a transient app configuration or broker event cleared one of those retained
# records while Home Assistant remained online.
DISCOVERY_REFRESH_SECONDS = 60.0
# The Jackery counterparts of bridge.LOGGED_FIELD_AGES: one of these aging out
# takes its entity offline on its own, independently of the whole-stream state.
LOGGED_FIELD_AGES = ("bms_batt_soc", "pow_in_sum_w", "pow_out_sum_w")


JACKERY_CONTROL_ICONS = {
    "jackery_ac_output": "mdi:power-socket",
    "jackery_dc_output": "mdi:car-battery",
    "jackery_battery_save": "mdi:battery-lock",
    "jackery_screen_timeout": "mdi:monitor-clock",
    "jackery_charging_mode": "mdi:battery-charging",
    "jackery_auto_power_off": "mdi:timer-power",
}
# Older OpenDP3 builds advertised the two output readings as binary sensors.
# Clear those retained discovery records so they cannot collide with the
# actionable switch entities below after an upgrade.
LEGACY_JACKERY_BINARY_CONTROLS = ("jackery_ac_output", "jackery_dc_output")


def jackery_entities():
    result = [Entity(key, label, component=component, unit=unit, device_class=device_class,
                     state_class=state_class, diagnostic=diagnostic)
              for key, label, unit, component, device_class, state_class, diagnostic in JACKERY_FIELDS]
    # Writable aliases stay separate from raw numeric telemetry so
    # recorder/history compatibility remains unchanged.
    result += [Entity("jackery_charging_mode", "Charging mode", component="select"),
               Entity("jackery_auto_power_off", "Auto Power-Off", component="select")]
    result += [Entity("collector_state", "Collector state", diagnostic=True),
               Entity("last_frame", "Last frame", device_class="timestamp", diagnostic=True),
               Entity("frame_count", "Frames recorded", state_class="total_increasing", diagnostic=True)]
    return result


def jackery_control_availability(base, key):
    """Control availability requires telemetry AND the explicit write policy.

    Control discovery is intentionally persistent even when writes are disabled.
    That keeps dashboards stable across app rebuilds and BLE outages: Home
    Assistant shows an unavailable control instead of deleting the entity. The
    bridge still refuses to subscribe to command topics while allow_control is
    false, and the recorder independently checks the same policy before writing.
    """
    return field_availability(base, key) + [{"topic": base + "/control-availability"}]


def discovery_payloads(serial, firmware="", control=False):
    """Return stable Jackery MQTT discovery, independent of current BLE reachability.

    ``control`` is retained for compatibility with older callers. Writable
    entities are always discovered now; runtime policy is represented by the
    retained control-availability topic instead of deleting entity definitions.
    """
    dev_id = device_id(serial)
    base = f"jackery/{dev_id}"
    device = {"identifiers": [f"jackery_{dev_id}"], "name": "Jackery Explorer 1000",
              "manufacturer": "Jackery", "model": "Explorer 1000 v2"}
    if firmware:
        device["sw_version"] = firmware
    origin = {"name": "OpenDP3", "sw_version": __version__}
    payloads = {}
    for entity in jackery_entities():
        is_control = entity.key in JACKERY_CONTROLS
        # Control keys already carry their public `jackery_` prefix. Older
        # discovery prepended it a second time, so a freshly recreated control
        # could become switch.jackery_jackery_ac_output until a registry
        # repair caught up. Seed the correct ID at source;
        # keep legacy naming unchanged for the wider telemetry surface.
        object_id = entity.key if is_control else f"jackery_{entity.key}"
        default_entity_id = (f"{entity.component}.{entity.key}" if is_control
                             else f"{entity.component}.jackery_{entity.key}")
        config = {"name": entity.name, "unique_id": f"jackery_{dev_id}_{entity.key}",
                  "object_id": object_id,
                  "default_entity_id": default_entity_id,
                  "state_topic": base + "/state",
                  "device": device, "origin": origin,
                  "value_template": f"{{% if value_json.{entity.key} is defined %}}{{{{ value_json.{entity.key} }}}}{{% endif %}}"}
        if entity.component == "binary_sensor":
            config["payload_on"], config["payload_off"] = "ON", "OFF"
        if is_control:
            config["command_topic"] = f"{base}/control/{entity.key}/set"
            config["icon"] = JACKERY_CONTROL_ICONS[entity.key]
            config["entity_category"] = "config"
            if entity.component == "switch":
                config["payload_on"], config["payload_off"] = "ON", "OFF"
            else:
                config["options"] = list(JACKERY_CONTROLS[entity.key]["values"])
        elif entity.component == "switch":
            config["entity_category"] = "diagnostic"
        if entity.diagnostic:
            config["availability_topic"] = base + "/availability"
        else:
            config["availability"] = [{"topic": base + "/availability"},
                                       {"topic": base + "/telemetry"}]
            config["availability_mode"] = "all"
        if entity.unit: config["unit_of_measurement"] = entity.unit
        if entity.component == "switch":
            # `power` is a valid sensor class, but not a valid MQTT switch
            # device class. `outlet` gives HA the correct control semantics.
            config["device_class"] = "outlet"
        elif entity.device_class:
            config["device_class"] = entity.device_class
        if entity.state_class: config["state_class"] = entity.state_class
        if entity.diagnostic: config["entity_category"] = "diagnostic"
        if entity.key not in {"collector_state", "last_frame", "frame_count"}:
            config.pop("availability_topic", None)
            config["availability"] = (jackery_control_availability(base, entity.key)
                                      if is_control else field_availability(base, entity.key))
            config["availability_mode"] = "all"
            config["value_template"] = value_template(entity.key)
        topic = f"{DISCOVERY_PREFIX}/{entity.component}/jackery_{dev_id}/{entity.key}/config"
        payloads[topic] = config
    return payloads


def state_payload(reading, now_ns=None, stale_seconds=STALE_SECONDS):
    payload = {entity.key: None for entity in jackery_entities()}
    if not reading:
        payload["collector_state"] = "offline"
        return payload, False
    session = reading["session"]
    now_ns = time.time_ns() if now_ns is None else now_ns
    last = reading["last_utc_ns"]
    stale = last is not None and (now_ns-last)/1e9 > stale_seconds
    live = (not session.get("synthetic") and session["status"] == "recording"
            and last is not None and not stale)
    if stale and session["status"] == "recording":
        payload["collector_state"] = "stale"
        payload["last_frame"] = as_timestamp(last)
        payload["frame_count"] = reading["count"]
        return payload, False
    payload["collector_state"] = "recording" if live else session["status"]
    if live:
        for key, observation in reading["values"].items():
            if not observation_fresh(observation, now_ns, stale_seconds):
                continue
            value = observation["value"]
            if key in payload and isinstance(value, str):
                payload[key] = value
            elif key in payload and isinstance(value, (int, float)) and math.isfinite(value):
                if key in {"jackery_ac_output", "jackery_dc_output"}:
                    payload[key] = "ON" if value else "OFF"
                elif key == "jackery_low_power_mode":
                    payload["jackery_battery_save"] = {0: "full", 1: "save", 2: "custom"}.get(
                        int(value), "unknown")
                elif key == "jackery_screen_timeout":
                    payload[key] = {1: "always_on", 2: "2m", 3: "2h"}.get(int(value))
                elif key == "jackery_charge_mode":
                    # Preserve the raw telemetry sensor and derive the documented
                    # writable select from the same observed station field.
                    payload[key] = value
                    payload["jackery_charging_mode"] = {0: "standard", 1: "quiet"}.get(value)
                elif key == "jackery_energy_saving_mode":
                    # pm is already recorded as raw telemetry. Jackery's published
                    # Auto Power-Off choices match these minute values exactly.
                    payload[key] = value
                    payload["jackery_auto_power_off"] = {
                        0: "off", 120: "2h", 480: "8h", 720: "12h", 1440: "24h"
                    }.get(value)
                else:
                    payload[key] = value
    payload["last_frame"] = as_timestamp(reading["last_utc_ns"])
    payload["frame_count"] = reading["count"]
    return payload, live


class JackeryBridge:
    def __init__(self, config, database, serial, *, interval=60, client=None):
        self.config, self.database, self.serial = config, Path(database), serial
        self.interval, self.client = float(interval), client
        self.dev_id = device_id(serial)
        self.base = f"jackery/{self.dev_id}"
        self.announced = False
        self._last_discovery = 0.0
        # Cache frame-based identity checks, while tracking session-list changes.
        self._session_id = None
        self._session_candidates = None
        self._last_state = None
        self._last_telemetry = None
        self._available_online = None
        self._collector_reported = None
        self._expired_reported = None

    def build_client(self):
        import paho.mqtt.client as mqtt
        client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2, client_id="jackery-" + self.dev_id)
        if self.config.mqtt_username:
            client.username_pw_set(self.config.mqtt_username, self.config.mqtt_password or None)
        if self.config.mqtt_tls:
            client.tls_set()
        # Registered before connect, so an unclean exit still marks the device offline.
        client.will_set(self.base + "/availability", "offline", qos=1, retain=True)
        client.on_connect = self.on_connect
        client.on_disconnect = self.on_disconnect
        client.on_message = self.on_message
        client.reconnect_delay_set(min_delay=2, max_delay=60)
        return client

    def on_disconnect(self, client, userdata, *args):
        reason = args[-2] if len(args) >= 2 else (args[0] if args else "unknown")
        log_transition("jackery mqtt", "connected", "disconnected", f"reason={reason}")
        self._available_online = None
        self._last_telemetry = None
        self._collector_reported = None
        self._expired_reported = None

    def on_connect(self, client, userdata, flags, reason_code, properties=None):
        if getattr(reason_code, "is_failure", reason_code != 0):
            log_transition("jackery mqtt", "connecting", "refused", f"reason={reason_code}")
            return
        log_transition("jackery mqtt", "disconnected", "connected", "device " + self.dev_id)
        # Re-announce after every reconnect. Without this, a broker restart or even
        # a brief network blip leaves the retained availability topic stuck at the
        # LWT's "offline" forever, even though telemetry keeps flowing underneath.
        self.announced = False
        self._last_state = self._last_telemetry = None
        if self.config.allow_control:
            client.subscribe(self.base + "/control/+/set", qos=1)
        # Re-announce discovery after Home Assistant restarts. Retained
        # discovery normally survives, but HA deliberately removes unique-ID
        # entities from its in-memory registry until it receives discovery again.
        client.subscribe(STATUS_TOPIC, qos=1)
        client.publish(self.base + "/availability", "online", qos=1, retain=True)
        log_transition("jackery availability", self._available_online, "online")
        self._available_online = "online"
        client.publish(self.base + "/control-availability",
                       "online" if self.config.allow_control else "offline",
                       qos=1, retain=True)

    def on_message(self, client, userdata, message):
        if message.topic == STATUS_TOPIC and message.payload.decode("utf-8", "replace").strip() == "online":
            self.announced = False
            return
        if getattr(message, "retain", False):
            return
        parts = message.topic.split("/")
        if len(parts) != 5 or parts[:3] != ["jackery", self.dev_id, "control"] or parts[4] != "set":
            return
        self.queue_control(parts[3], message.payload.decode("utf-8", "replace").strip())

    def queue_control(self, control, payload):
        """Queue a validated Jackery command for the BLE recorder process."""
        if not self.config.allow_control or control not in JACKERY_CONTROLS:
            return
        spec = JACKERY_CONTROLS[control]
        if spec["kind"] == "switch":
            if payload.upper() not in {"ON", "OFF"}:
                return
            value = payload.upper() == "ON"
        else:
            value = payload.lower()
            try:
                jackery_control_value(control, value)
            except ValueError:
                return
        directory = self.database.parent / "jackery-commands"
        try:
            directory.mkdir(parents=True, exist_ok=True)
            stem = f"{time.time_ns()}-{control}"
            tmp = directory / (stem + ".tmp")
            tmp.write_text(json.dumps({"control": control, "value": value}), encoding="utf-8")
            tmp.replace(directory / (stem + ".json"))
        except OSError:
            # A delayed output or battery-mode command is worse than a missing one.
            pass

    def _matching_session_id(self):
        """Return the newest session whose recorded device serial is ours.

        This runs on every publish, so it must not scan every frame of every
        session: the old join did, and at a one-second publish rate against a
        growing database it was the dominant cost. Walk sessions newest-first
        and look at one frame each, then remember the answer.
        """
        if not self.database.exists():
            return None
        with read_db(self.database) as db:
            # Session metadata is tiny compared with frame history. Re-evaluate
            # whenever sessions change, including a recorder restart. Do not
            # cache a negative result: a new session may not have its first frame.
            candidates = tuple(row[0] for row in db.execute(
                "SELECT id FROM sessions WHERE firmware LIKE 'Jackery%' ORDER BY start_utc_ns DESC, rowid DESC"))
            if candidates == self._session_candidates and self._session_id == (candidates[0] if candidates else None):
                return self._session_id
            for sid in candidates:
                row = db.execute(
                    "SELECT decoded FROM frames WHERE session_id=? ORDER BY id DESC LIMIT 1",
                    (sid,)).fetchone()
                if not row:
                    continue
                try:
                    document = json.loads(row[0])
                except (TypeError, ValueError):
                    continue
                if str((document.get("device") or {}).get("serial", "")) == self.serial:
                    self._session_id = sid
                    self._session_candidates = candidates
                    return sid
        return None

    def publish_discovery(self):
        # Discovery is independent of BLE reachability and write enablement.
        # Runtime availability communicates whether a control can actually act.
        payloads = discovery_payloads(self.serial, control=self.config.allow_control)
        for topic, config in payloads.items():
            self.client.publish(topic, json.dumps(config, ensure_ascii=False), qos=1, retain=True)
        for key in LEGACY_JACKERY_BINARY_CONTROLS:
            topic = f"{DISCOVERY_PREFIX}/binary_sensor/jackery_{self.dev_id}/{key}/config"
            self.client.publish(topic, "", qos=1, retain=True)
        self._last_discovery = time.monotonic()

    def publish_once(self):
        if (not self.announced or
                time.monotonic() - self._last_discovery >= DISCOVERY_REFRESH_SECONDS):
            self.publish_discovery()
            self.announced = True
        reading = {}
        if self.database.exists():
            # Never fall back to the globally newest session: the same database
            # may contain an EcoFlow recording, which must not be published as Jackery.
            sid = self._matching_session_id()
            if sid:
                reading = latest(self.database, sid,
                                 keys=[entity.key for entity in jackery_entities()])
                # Older recorder processes may have loaded before a new mapping
                # was added. The raw cloud JSON is already preserved, so derive
                # the additional fields here without restarting or mixing devices.
                with read_db(self.database) as db:
                    row = db.execute("""SELECT f.decoded,
                        (SELECT m.quality FROM measurements m WHERE m.frame_id=f.id LIMIT 1),
                        f.utc_ns,f.t
                        FROM frames f WHERE f.session_id=? ORDER BY f.id DESC LIMIT 1""",
                                     (sid,)).fetchone()
                if row:
                    document = json.loads(row[0])
                    properties = document.get("properties", {})
                    # Backfilled fields inherit the transport the frame was really
                    # recorded on. A local BLE reading must not claim to be cloud.
                    quality = row[1] or "ble_observed"
                    reading["values"].update({key: {"value": value, "quality": quality,
                                                  "utc_ns": row[2], "t": row[3]}
                                              for key, value in map_properties(properties).items()})
        if not reading:
            # Look again next cycle: this may be the gap before a new recording.
            self._session_id = None
        now_ns = time.time_ns()
        payload, live = state_payload(reading, now_ns=now_ns)
        last = reading.get("last_utc_ns")
        frame_age = f"{(now_ns - last) / 1e9:.0f}s" if last else "never"
        # Reported the same way as the DP3 bridge. The Explorer's observations
        # always carry measurements, so these two normally agree; a divergence
        # here would itself be the finding.
        telemetry = reading.get("last_telemetry_utc_ns")
        telemetry_age = f"{(now_ns - telemetry) / 1e9:.0f}s" if telemetry else "never"
        detail = (f"last frame {frame_age}; last telemetry {telemetry_age}; "
                  + field_age_summary(reading, now_ns, LOGGED_FIELD_AGES))
        log_transition("jackery collector_state", self._collector_reported,
                       payload["collector_state"], detail)
        self._collector_reported = payload["collector_state"]
        expired = expired_fields(payload, LOGGED_FIELD_AGES)
        log_transition("jackery expired fields",
                       None if self._expired_reported is None else
                       (", ".join(self._expired_reported) or "none"),
                       ", ".join(expired) or "none", detail)
        self._expired_reported = expired
        # Both topics are retained, so Home Assistant already holds the last
        # value. Republishing an unchanged payload every second would add broker
        # traffic, PUBACK round trips and Home Assistant recorder rows without
        # telling anyone anything new.
        state = json.dumps(payload, allow_nan=False)
        if state != self._last_state:
            self.client.publish(self.base + "/state", state, qos=0, retain=True)
            self._last_state = state
        telemetry = "online" if live else "offline"
        if telemetry != self._last_telemetry:
            log_transition("jackery telemetry", self._last_telemetry, telemetry, detail)
            self.client.publish(self.base + "/telemetry", telemetry, qos=1, retain=True)
            self._last_telemetry = telemetry

    def run(self, once=False, stop_file=None):
        if not self.config.mqtt_host:
            raise ValueError("No MQTT broker configured. Set the broker address in Settings first.")
        self.client = self.client or self.build_client()
        try:
            try:
                self.client.connect(self.config.mqtt_host, self.config.mqtt_port, keepalive=60)
            except OSError:
                raise ValueError("Cannot reach the MQTT broker. Check the address, port and credentials.") from None
            self.client.loop_start()
            while True:
                self.publish_once()
                if once:
                    break
                if stop_file and stop_file.exists():
                    break
                time.sleep(self.interval)
        finally:
            with contextlib.suppress(Exception):
                self.client.publish(self.base + "/control-availability", "offline", qos=1, retain=True)
            log_transition("jackery availability", self._available_online, "offline",
                           "bridge stopping")
            self._available_online = "offline"
            with contextlib.suppress(Exception):
                self.client.publish(self.base + "/availability", "offline", qos=1, retain=True)
            with contextlib.suppress(Exception): self.client.loop_stop()
            with contextlib.suppress(Exception): self.client.disconnect()
