"""Home Assistant MQTT publisher and guarded command relay for Jackery recordings."""
import contextlib
import json
import math
from pathlib import Path
import time

from . import __version__, control_queue
from .config import VIEWER_REFUSAL
from .bridge import (DISCOVERY_PREFIX, STATUS_TOPIC, Entity, RecordingReader, as_timestamp,
                     collector_state, device_id, expired_fields, field_age_summary,
                     field_availability, log_transition, observation_fresh, single_instance,
                     unreadable_payload, value_template)
from .entity_ids import JACKERY_ENTITY_IDS, seeded_ids
from .jackery import JACKERY_CONTROLS, jackery_control_value
from .jackery_fields import JACKERY_CORE_KEYS, JACKERY_FIELDS, map_properties
from .queries import latest
from .storage import frame_tally, read_db

# A recorder that dies without finishing its session leaves status='recording',
# and the bridge would otherwise republish frozen values as live for ever. At a
# 3s poll rate that reads as second-fresh truth, so it has to be caught. Ten
# missed polls is late enough not to trip on ordinary reattach jitter.
STALE_SECONDS = 30.0
# This bridge's single-instance lock, beside its database. start_all.py and
# watchdog.py test the same name to see whether it is up.
LOCK_NAME = "jackery-bridge.lock"
# The Jackery counterparts of bridge.LOGGED_FIELD_AGES: one of these aging out
# takes its entity offline on its own, independently of the whole-stream state.
# The same keys the stream's own liveness follows, so they are not listed twice.
LOGGED_FIELD_AGES = JACKERY_CORE_KEYS


JACKERY_CONTROL_ICONS = {
    "jackery_ac_output": "mdi:power-socket",
    "jackery_dc_output": "mdi:car-battery",
    "jackery_battery_save": "mdi:battery-lock",
    "jackery_screen_timeout": "mdi:monitor-clock",
    "jackery_charging_mode": "mdi:battery-charging",
    "jackery_auto_power_off": "mdi:timer-power",
}
# Older builds advertised the two output readings as binary sensors.
# Clear those retained discovery records so they cannot collide with the
# actionable switch entities below after an upgrade.
LEGACY_JACKERY_BINARY_CONTROLS = ("jackery_ac_output", "jackery_dc_output")
# The raw telemetry field each select reads its state back from. The raw field
# keeps its own entity; screen timeout has none, so the select replaces it.
SELECT_READBACK = {
    "jackery_low_power_mode": "jackery_battery_save",
    "jackery_screen_timeout": "jackery_screen_timeout",
    "jackery_charge_mode": "jackery_charging_mode",
    "jackery_energy_saving_mode": "jackery_auto_power_off",
}


def select_option(control, value):
    """The option a raw reading names, or None when it names none of them.

    Read from the same JACKERY_CONTROLS values discovery offers as options, so
    the published state is always one of them. Home Assistant rejects any other
    state and keeps showing the previous option as the current one; None makes
    the select unavailable instead.
    """
    for option, raw in JACKERY_CONTROLS[control]["values"].items():
        if value == raw:
            return option
    return None


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


def discovery_payloads(serial):
    """Return stable Jackery MQTT discovery, independent of current BLE reachability.

    Writable entities are always discovered, whatever the control policy; the
    retained control-availability topic says whether they can act, rather than
    deleting their definitions. The device carries no sw_version: nothing
    records the Explorer's firmware.
    """
    dev_id = device_id(serial)
    base = f"jackery/{dev_id}"
    device = {"identifiers": [f"jackery_{dev_id}"], "name": "Jackery Explorer 1000",
              "manufacturer": "Jackery", "model": "Explorer 1000 v2"}
    origin = {"name": "OpenPowerstation", "sw_version": __version__}
    payloads = {}
    for entity in jackery_entities():
        is_control = entity.key in JACKERY_CONTROLS
        # Seed an entity ID from the overrides when they name one, so a
        # re-created entity lands back on the ID an installation already uses;
        # see entity_ids.py. A key without one keeps this scheme. Control keys
        # already carry their public `jackery_` prefix; older discovery
        # prepended it a second time, so a freshly recreated control could
        # become switch.jackery_jackery_ac_output.
        object_id, default_entity_id = seeded_ids(
            JACKERY_ENTITY_IDS, entity.key,
            f"{entity.component}.{entity.key}" if is_control
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


def core_telemetry_utc_ns(reading):
    """When the newest core measurement (JACKERY_CORE_KEYS) arrived, or None.

    The stream's liveness follows this, not the newest frame of any kind. A
    station answering with settings fields alone -- or with nothing the map
    knows, which is recorded too -- keeps adding frames while charge and power
    age out, and counting those frames reported a healthy link serving nothing.
    """
    stamps = [observation.get("utc_ns")
              for key, observation in (reading.get("values") or {}).items()
              if key in JACKERY_CORE_KEYS]
    stamps = [stamp for stamp in stamps if isinstance(stamp, (int, float))]
    return max(stamps) if stamps else None


def state_payload(reading, now_ns=None, stale_seconds=STALE_SECONDS):
    payload = {entity.key: None for entity in jackery_entities()}
    if not reading:
        payload["collector_state"] = "offline"
        return payload, False
    now_ns = time.time_ns() if now_ns is None else now_ns
    # The DP3 bridge's own rule, so the two cannot drift apart: a session
    # still waiting for its first core reading is "waiting" -- the collector
    # opens it before the Explorer is found -- and one whose core readings
    # stopped is "stale".
    telemetry = core_telemetry_utc_ns(reading)
    state, live = collector_state(dict(reading, last_telemetry_utc_ns=telemetry),
                                  now_ns=now_ns, stale_seconds=stale_seconds)
    payload["collector_state"] = state
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
                elif key in SELECT_READBACK:
                    # Preserve the raw telemetry sensor and derive the writable
                    # select from the same observed station field. Exact match
                    # only: a fractional reading is not truncated into an option.
                    payload[key] = value
                    payload[SELECT_READBACK[key]] = select_option(SELECT_READBACK[key], value)
                else:
                    payload[key] = value
    payload["last_frame"] = as_timestamp(reading["last_utc_ns"])
    payload["frame_count"] = reading["count"]
    return payload, live


class JackeryBridge:
    def __init__(self, config, database, serial, *, interval=60, client=None):
        # ``serial`` names the one Explorer published; there is no default.
        self.config, self.database, self.serial = config, Path(database), serial
        self.interval, self.client = float(interval), client
        self.dev_id = device_id(serial)
        self.base = f"jackery/{self.dev_id}"
        self.announced = False
        self.recording = RecordingReader(self.database, "jackery database")
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
        # The DP3's queue, so the same age limit and bound apply. A request that
        # cannot be written is dropped: a delayed output or battery-mode command
        # is worse than a missing one.
        control_queue.write_request(self.database.parent / control_queue.JACKERY_DIRECTORY,
                                    control, value)

    def _matching_session_id(self):
        """Return the newest session whose recorded device serial is ours.

        This runs on every publish, so it must not scan every frame of every
        session: the old join did, and at a one-second publish rate against a
        growing database it was the dominant cost. Walk sessions newest-first
        and look at one frame each, then remember the answer.

        A session with no frame yet is ours if the collector opened it for our
        serial: it opens its session before the Explorer is found, and while it
        searches that session is the only honest account of it. Skipping it
        published the finished session before it as "stopped" for the whole
        search. It supplies status only -- it has no values -- and is never
        cached, so once its first frame arrives the frame check below applies.
        """
        if not self.database.exists():
            return None
        opened_for_us = f"Jackery Explorer 1000 v2 {self.serial}"
        with read_db(self.database) as db:
            # Session metadata is tiny compared with frame history. Re-evaluate
            # whenever sessions change, including a recorder restart. Do not
            # cache a negative result: a new session may not have its first frame.
            sessions = db.execute(
                "SELECT id,firmware FROM sessions WHERE firmware LIKE 'Jackery%' "
                "ORDER BY start_utc_ns DESC, rowid DESC").fetchall()
            candidates = tuple(row[0] for row in sessions)
            if candidates == self._session_candidates and self._session_id == (candidates[0] if candidates else None):
                return self._session_id
            for sid, firmware in sessions:
                _, last_id = frame_tally(db, sid)
                row = None if last_id is None else db.execute(
                    "SELECT decoded FROM frames WHERE id=?", (last_id,)).fetchone()
                if not row:
                    if firmware == opened_for_us:
                        return sid
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
        payloads = discovery_payloads(self.serial)
        for topic, config in payloads.items():
            self.client.publish(topic, json.dumps(config, ensure_ascii=False), qos=1, retain=True)
        for key in LEGACY_JACKERY_BINARY_CONTROLS:
            topic = f"{DISCOVERY_PREFIX}/binary_sensor/jackery_{self.dev_id}/{key}/config"
            self.client.publish(topic, "", qos=1, retain=True)

    def read(self):
        """``(reading, error)`` for this Explorer's newest session; see RecordingReader.

        The session lookup, the reading and the backfill are one read, so an
        error in any of them is reported the same way.
        """
        return self.recording.read(self._read_session)

    def _read_session(self):
        # Never fall back to the globally newest session: the same database
        # may contain an EcoFlow recording, which must not be published as Jackery.
        sid = self._matching_session_id()
        if not sid:
            return {}
        reading = latest(self.database, sid, keys=[entity.key for entity in jackery_entities()])
        # Older recorder processes may have loaded before a new mapping was
        # added. The raw cloud JSON is already preserved, so derive the
        # additional fields here without restarting or mixing devices. From the
        # frame latest() read as newest, not whatever is newest by now, so the
        # reading describes one reply.
        last_id = reading.get("last_id")
        with read_db(self.database) as db:
            row = None if last_id is None else db.execute("""SELECT f.decoded,
                (SELECT m.quality FROM measurements m WHERE m.frame_id=f.id LIMIT 1),
                f.utc_ns,f.t
                FROM frames f WHERE f.id=? AND f.session_id=?""", (last_id, sid)).fetchone()
        if row:
            document = json.loads(row[0])
            properties = document.get("properties", {})
            # Backfilled fields inherit the transport the frame was really
            # recorded on. A local BLE reading must not claim to be cloud.
            quality = row[1] or "ble_observed"
            reading["values"].update({key: {"value": value, "quality": quality,
                                          "utc_ns": row[2], "t": row[3]}
                                      for key, value in map_properties(properties).items()})
        return reading

    def publish_once(self):
        # Discovery is retained. It is re-sent only when it may have been lost:
        # after a reconnect (on_connect) or a Home Assistant restart (on_message).
        # A timed refresh re-sent all of it every minute, and while the broker
        # was down paho queued every copy without bound to replay on reconnect.
        if not self.announced:
            self.publish_discovery()
            self.announced = True
        reading, error = self.read()
        if not reading:
            # Look again next cycle: this may be the gap before a new recording.
            self._session_id = None
        now_ns = time.time_ns()
        if error:
            # Published, as the DP3 bridge does: the retained state would
            # otherwise go on reading "recording" over values no longer seen.
            payload, live = unreadable_payload(jackery_entities()), False
            detail = "database unreadable: " + error
        else:
            payload, live = state_payload(reading, now_ns=now_ns)
            last = reading.get("last_utc_ns")
            frame_age = f"{(now_ns - last) / 1e9:.0f}s" if last else "never"
            # Reported the same way as the DP3 bridge, and from the same core
            # readings the stream's liveness follows. Replies without them are
            # recorded too, so a fresh frame next to old telemetry is a station
            # answering without its readings, not a dead collector.
            telemetry = core_telemetry_utc_ns(reading) if reading else None
            telemetry_age = f"{(now_ns - telemetry) / 1e9:.0f}s" if telemetry else "never"
            detail = (f"last frame {frame_age}; last telemetry {telemetry_age}; "
                      + field_age_summary(reading, now_ns, LOGGED_FIELD_AGES))
        log_transition("jackery collector_state", self._collector_reported,
                       payload["collector_state"], detail)
        self._collector_reported = payload["collector_state"]
        if not error:
            # An unreadable recording says nothing about any field's age.
            expired = expired_fields(payload, LOGGED_FIELD_AGES)
            log_transition("jackery expired fields",
                           None if self._expired_reported is None else
                           (", ".join(self._expired_reported) or "none"),
                           ", ".join(expired) or "none", detail)
            self._expired_reported = expired
        # Both topics are retained, so Home Assistant already holds the last
        # value. Republishing an unchanged payload every second would add broker
        # traffic and a template render in every entity on this topic without
        # telling anyone anything new. It saves no recorder rows: Home Assistant
        # records a state only when it changes, and last_frame and frame_count
        # change with every frame, which is why the README asks for those two
        # to be excluded from the recorder.
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
        if self.config.role != "collector":
            # As the DP3 bridge: a viewer publishes neither battery.
            raise ValueError(VIEWER_REFUSAL)
        if not self.config.mqtt_host:
            raise ValueError("No MQTT broker configured. Set the broker address in Settings first.")
        with single_instance(self.database, LOCK_NAME,
                             "A Jackery bridge is already publishing; stop it before starting another."):
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
                # Waited for, as the DP3 bridge does: a clean disconnect does not
                # fire the last will, so an "offline" still queued when the loop
                # stops is lost and the retained "online" stays.
                with contextlib.suppress(Exception):
                    self.client.publish(self.base + "/availability", "offline", qos=1,
                                        retain=True).wait_for_publish(timeout=2)
                with contextlib.suppress(Exception): self.client.loop_stop()
                with contextlib.suppress(Exception): self.client.disconnect()
