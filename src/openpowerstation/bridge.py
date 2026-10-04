"""Outbound-only Home Assistant bridge over MQTT.

Reads the recording database through the existing read-only path and publishes
current values using Home Assistant discovery. Nothing here opens a listening
socket, writes to the database, or touches the Bluetooth transport: the collector
runs as a separate process and is unaffected if this one stops or the broker dies.

The device serial is never published. Topics and unique IDs are keyed on the same
hash the collector already uses for its per-device lock.
"""
import contextlib
from dataclasses import dataclass
from datetime import datetime, timezone
import hashlib
import json
import math
from pathlib import Path
import sqlite3
import sys
import threading
import time

import portalocker

from . import __version__, control_queue
from .config import VIEWER_REFUSAL
from .decoder import FIELDS
from .entity_ids import DP3_ENTITY_IDS, seeded_ids
from .protocol import CONTROL_FEEDBACK_FIELDS
from .queries import latest

DISCOVERY_PREFIX = "homeassistant"
# Frozen identifier, independent of the project's display name. Every existing
# install has it stored in MQTT topics, entity unique_ids and entity ids, and
# the Home Assistant device registry; changing it orphans those entities and
# creates duplicates. tests/test_frozen_identifiers.py pins it.
HA_ID_PREFIX = "opendp3"
STATUS_TOPIC = DISCOVERY_PREFIX + "/status"
# The controls this bridge will relay, matching protocol.CONTROL_FIELDS exactly.
# Anything else is refused by the outbound gate in the collector regardless, so
# publishing it here would only offer a switch that cannot work.
CONTROLS = [("cfg_hv_ac_out_open", "AC output (HV)"),
            ("cfg_lv_ac_out_open", "AC output (LV)")]
CONTROL_ICONS = {
    "cfg_hv_ac_out_open": "mdi:power-socket",
    "cfg_lv_ac_out_open": "mdi:power-socket-outline",
}
# The only command payloads that change an AC output. Every control relayed here
# is an AC output, and the LV output can power the host running Home Assistant, so
# a change must come from a caller that has passed an arm-then-change interlock.
# The discovered switch only ever sends its payload_on/payload_off (ON/OFF), from
# the device page, a more-info dialog, Developer tools or any automation. Those
# are handed to the collector flagged unguarded, to be recorded as refused, and
# never reach the radio. Deliberately not case-folded: the one intended sender
# publishes these exact strings on the switch's own command topic.
GUARDED_PAYLOADS = {"GUARDED_ON": True, "GUARDED_OFF": False}
# The file handoff's bound, shared with the Jackery bridge (control_queue).
CONTROL_QUEUE_MAX_FILES = control_queue.MAX_FILES
# No new frame for this long and the device state is genuinely unknown. The
# transport reports its own silence at 30s; allow one interval beyond that.
STALE_SECONDS = 45.0
# Fields worth naming individually in the transition log. Each is expired on its
# own timer, so one of these going quiet takes a Home Assistant entity offline
# while everything else keeps flowing -- the exact case that is otherwise
# invisible from outside and has to be inferred from BlueZ status.
LOGGED_FIELD_AGES = ("bms_batt_soc", "pow_out_sum_w", "pow_get_bms")
# Power that entered the unit and reached neither a load nor the cells:
#
#   conversion_loss_w = pow_in_sum_w - pow_out_sum_w - pow_get_bms
#
# Derived here, once per publish, rather than in a Home Assistant template.
# Home Assistant writes the entities of one /state message one after another
# and re-renders a template as each input changes, so subtracting three of them
# there recorded two in-between values per publish that mixed new and old
# operands: 56% of the recorded loss states, spanning -856 to +836 W.
CONVERSION_LOSS_OPERANDS = ("pow_in_sum_w", "pow_out_sum_w", "pow_get_bms")
# This bridge's single-instance lock, beside its database. start_all.py and
# watchdog.py test the same name to see whether it is up.
LOCK_NAME = "bridge.lock"
# What reading a recording can raise without this process being at fault: a
# database that is busy, locked, malformed or replaced mid-read (sqlite3), the
# file itself failing (OSError), or a stored frame that is not the JSON it
# should be (ValueError). Anything else is a bug, and still stops the bridge.
READ_ERRORS = (sqlite3.Error, OSError, ValueError)
# The collector_state a bridge publishes for a recording it cannot read. Never
# one the collector records itself, so the two cannot be confused.
UNREADABLE = "unreadable"


def log_transition(what, old, new, detail=""):
    """One line per state change, on stdout for the app/add-on log.

    Only ever called with values this process computed: never a serial, a
    credential, or a payload echoed back from the broker.
    """
    if old == new:
        return
    was = "-" if old is None else old
    print(f"[telemetry] {what}: {was} -> {new}" + (f"  ({detail})" if detail else ""),
          flush=True)


def expired_fields(payload, keys=LOGGED_FIELD_AGES):
    """Which of the named fields this publish is withholding.

    A field is published as null once its own observation ages past the stale
    window, which takes that one Home Assistant entity offline. Nothing else in
    the log moves when this happens -- collector_state stays "recording" and
    telemetry stays online -- so without this the most common cause of
    "a battery bridge is not reporting" leaves no trace at all.
    """
    return tuple(key for key in keys if payload.get(key) is None)


def field_age_summary(reading, now_ns, keys=LOGGED_FIELD_AGES):
    """``key=age`` for the named fields, or ``key=never`` if never observed."""
    parts = []
    for key in keys:
        observation = (reading.get("values") or {}).get(key)
        stamp = observation.get("utc_ns") if observation else None
        if isinstance(stamp, (int, float)):
            parts.append(f"{key}={(now_ns - stamp) / 1e9:.0f}s")
        else:
            parts.append(f"{key}=never")
    return " ".join(parts)


def bridge_lock(database, name=LOCK_NAME):
    """The single-instance lock of the bridge publishing from ``database``."""
    return portalocker.Lock(str(Path(database).resolve().parent / name), timeout=0)


@contextlib.contextmanager
def single_instance(database, name, refusal):
    """Hold ``bridge_lock`` for a whole run, or refuse the run with ``refusal``.

    Taken by each bridge's run(), not by whatever launched it, so every way of
    starting one is covered: the add-on runs the CLI, Windows runs start_all.py.
    A second bridge for the same device connects under the same MQTT client ID,
    the broker drops the first for it, and the two then reconnect in turn for
    as long as both run. Held until shutdown has published "offline", which
    could otherwise land after a successor's "online".
    """
    lock = bridge_lock(database, name)
    try:
        lock.acquire()
    except portalocker.exceptions.LockException:
        raise ValueError(refusal) from None
    try:
        yield
    finally:
        lock.release()


class RecordingReader:
    """Both bridges' one way to read their recording: ``(reading, error)``.

    A database that does not exist yet is no recording, ``({}, None)``: no
    collector has opened it. One that exists and cannot be read is
    ``(None, "Class: message")``, for the bridge to publish as collector_state
    "unreadable". The DP3 bridge used to take that for no recording, so its
    retained collector_state kept reading "recording"; the Jackery bridge did
    not catch it at all, and its process exited to be restarted 15 s later.

    The cause is logged when reads start failing, when it changes and when
    reads recover; never once per publish. SQLite, the OS and the JSON parser
    write these messages, not the device, so they carry no serial.
    """

    def __init__(self, database, what="database"):
        self.database = Path(database)
        self.what = what
        self.error = None

    def read(self, fetch):
        reading, error = {}, None
        if self.database.exists():
            try:
                reading = fetch()
            except READ_ERRORS as exc:
                reading, error = None, f"{type(exc).__name__}: {exc}"
        log_transition(self.what + " read error", self.error or "none", error or "none")
        self.error = error
        return reading, error


def unreadable_payload(entities):
    """Every value withheld, and collector_state saying the recording is unreadable.

    A withheld value takes its entity offline through its own availability
    template, exactly as an expired one does.
    """
    payload = {entity.key: None for entity in entities}
    payload["collector_state"] = UNREADABLE
    return payload


def device_id(serial: str) -> str:
    """Stable pseudonymous handle. Same hashing precedent as the collector lock.

    Not the serial, but unsalted: anyone who can guess the serial can confirm it.
    """
    return hashlib.sha256(serial.encode()).hexdigest()[:12]


@dataclass(frozen=True)
class Entity:
    key: str
    name: str
    component: str = "sensor"
    unit: str = ""
    device_class: str = ""
    state_class: str = ""
    diagnostic: bool = False


# Published values the bridge computes rather than decodes. Each is withheld,
# and its entity goes unavailable, exactly as a decoder field is.
DERIVED = (Entity("conversion_loss_w", "Conversion loss", unit="W", device_class="power",
                  state_class="measurement"),)


def entities() -> list[Entity]:
    """One entity per decoder field and derived value, plus capture diagnostics."""
    result = []
    for field in FIELDS:
        device_class = state_class = ""
        diagnostic = field.group == "state"
        if field.group == "temperature":
            device_class, state_class = "temperature", "measurement"
        elif field.group == "power":
            device_class, state_class = "power", "measurement"
        elif field.group == "soc":
            state_class = "measurement"
            if field.key.endswith("_soc"):
                device_class = "battery"
            else:
                diagnostic = True  # State of health is not a charge level.
        # Community-mapped and unverified extras stay out of the primary view;
        # Field.label already carries that caveat in words.
        if field.key.startswith(("extra1_", "extra2_")):
            diagnostic = True
        result.append(Entity(field.key, field.label, unit=field.unit, device_class=device_class,
                             state_class=state_class, diagnostic=diagnostic))
    result += DERIVED
    result += [
        Entity("collector_state", "Collector state", diagnostic=True),
        # Why telemetry last stopped (runtime.REASONS); "none" once it flows.
        Entity("collector_reason", "Collector reason", diagnostic=True),
        Entity("last_frame", "Last frame", device_class="timestamp", diagnostic=True),
        Entity("frame_count", "Frames recorded", state_class="total_increasing", diagnostic=True),
        Entity("last_event", "Last event", diagnostic=True),
        Entity("incident", "Incident", component="binary_sensor", device_class="problem", diagnostic=True),
    ]
    return result


def value_template(key: str) -> str:
    """Absent or null renders empty, which Home Assistant treats as "no update"
    rather than writing the literal string "None" into history."""
    return ("{% if value_json." + key + " is defined and value_json." + key + " is not none %}"
            "{{ value_json." + key + " }}{% endif %}")


def field_availability(base, key):
    """An old value must become unavailable even while other fields update."""
    return [{"topic": base + "/availability"}, {"topic": base + "/telemetry"},
            {"topic": base + "/state", "value_template":
             "{{ 'online' if value_json." + key + " is defined and value_json." + key +
             " is not none else 'offline' }}"}]


def control_state_template(key):
    """Turn a fresh DP3 flow-info bitmask into Home Assistant's ON/OFF payload.

    The low two bits are the outlet state (protocol.CONTROL_FEEDBACK_FIELDS):
    0 is off; 2 and 3 are on. Value 1 is left unavailable rather than guessed.
    """
    return ("{% if value_json." + key + " is defined and value_json." + key + " is not none %}"
            "{% set port = (value_json." + key + " | int) % 4 %}"
            "{% if port in [2, 3] %}ON{% elif port == 0 %}OFF{% endif %}{% endif %}")


def control_availability(base, key):
    """A control is usable only when its device-reported state is fresh and understood."""
    return [{"topic": base + "/availability"}, {"topic": base + "/telemetry"},
            {"topic": base + "/state", "value_template":
             "{% if value_json." + key + " is defined and value_json." + key + " is not none %}"
             "{% set port = (value_json." + key + " | int) % 4 %}"
             "{{ 'online' if port in [0, 2, 3] else 'offline' }}{% else %}offline{% endif %}"}]


def observation_fresh(observation, now_ns, stale_seconds):
    stamp = observation.get("utc_ns")
    return (isinstance(stamp, (int, float)) and
            -2 <= (now_ns - stamp) / 1e9 <= stale_seconds)


def control_topic(dev_id: str, key: str) -> str:
    return f"{HA_ID_PREFIX}/{dev_id}/control/{key}/set"


def control_discovery(dev_id: str, device: dict, origin: dict) -> dict:
    """Telemetry-backed switch entities for the two opt-in DP3 controls.

    DisplayPropertyUpload carries the actual HV/LV output bitmasks. Home
    Assistant therefore waits for fresh device feedback instead of assuming a
    command succeeded. Before a feedback field is observed the switch is
    unavailable, which distinguishes "not checked yet" from a real off state.
    """
    base = HA_ID_PREFIX + "/" + dev_id
    payloads = {}
    for key, name in CONTROLS:
        feedback = CONTROL_FEEDBACK_FIELDS[key]
        topic = "/".join([DISCOVERY_PREFIX, "switch", HA_ID_PREFIX + "_" + dev_id, key, "config"])
        object_id, default_entity_id = seeded_ids(DP3_ENTITY_IDS, key, "switch." + HA_ID_PREFIX + "_" + key)
        payloads[topic] = {
            "name": name,
            "unique_id": HA_ID_PREFIX + "_" + dev_id + "_" + key,
            "object_id": object_id,
            "default_entity_id": default_entity_id,
            "command_topic": control_topic(dev_id, key),
            "state_topic": base + "/state",
            "value_template": control_state_template(feedback),
            "payload_on": "ON",
            "payload_off": "OFF",
            "icon": CONTROL_ICONS[key],
            "entity_category": "config",
            # A command can only be delivered while the bridge is up AND the
            # collector holds the device. Requiring the state field as well means
            # startup is unknown/unavailable until the DP3 itself reports it.
            "availability": control_availability(base, feedback),
            "availability_mode": "all",
            "device": device,
            "origin": origin,
        }
    return payloads


def discovery_payloads(dev_id: str, *, firmware: str = "", control: bool = False) -> dict:
    """``{discovery topic: config payload}``, generated from the decoder field table."""
    base = HA_ID_PREFIX + "/" + dev_id
    device = {"identifiers": [HA_ID_PREFIX + "_" + dev_id], "name": "EcoFlow DELTA Pro 3",
              "manufacturer": "EcoFlow", "model": "DELTA Pro 3"}
    if firmware:
        device["sw_version"] = firmware
    origin = {"name": "OpenPowerstation", "sw_version": __version__}
    per_field = {field.key for field in FIELDS} | {entity.key for entity in DERIVED}
    payloads = {}
    for entity in entities():
        # Home Assistant creates the entity under default_entity_id: the ID an
        # installation's overrides name, if any, so a re-created entity lands
        # back on the one its dashboards read. See entity_ids.py.
        object_id, default_entity_id = seeded_ids(
            DP3_ENTITY_IDS, entity.key, entity.component + "." + HA_ID_PREFIX + "_" + entity.key)
        config = {
            "name": entity.name,
            "unique_id": HA_ID_PREFIX + "_" + dev_id + "_" + entity.key,
            "object_id": object_id,
            "default_entity_id": default_entity_id,
            "state_topic": base + "/state",
            "value_template": value_template(entity.key),
            "device": device,
            "origin": origin,
        }
        if entity.diagnostic:
            # Diagnostics follow the bridge alone, so they stay readable while
            # telemetry is down and can say why the measurements went away.
            config["availability_topic"] = base + "/availability"
        else:
            # Measurements need BOTH the bridge alive and telemetry live.
            # Binding them to /telemetry alone held stale readings whenever the
            # bridge died: the last will only marks /availability offline, while
            # the retained /telemetry stays "online", so the last values kept
            # displaying as though current. "all" means available only when
            # every listed topic reports online.
            config["availability"] = [{"topic": base + "/availability"},
                                      {"topic": base + "/telemetry"}]
            config["availability_mode"] = "all"
        if entity.unit:
            config["unit_of_measurement"] = entity.unit
        if entity.device_class:
            config["device_class"] = entity.device_class
        if entity.state_class:
            config["state_class"] = entity.state_class
        if entity.diagnostic:
            config["entity_category"] = "diagnostic"
        if entity.key in per_field:
            config.pop("availability_topic", None)
            config["availability"] = field_availability(base, entity.key)
            config["availability_mode"] = "all"
        topic = "/".join([DISCOVERY_PREFIX, entity.component, HA_ID_PREFIX + "_" + dev_id, entity.key, "config"])
        payloads[topic] = config
    if control:
        payloads.update(control_discovery(dev_id, device, origin))
    return payloads


def as_timestamp(utc_ns):
    if not utc_ns:
        return None
    return datetime.fromtimestamp(utc_ns / 1e9, tz=timezone.utc).isoformat()


def collector_state(reading, *, now_ns, stale_seconds=STALE_SECONDS):
    """``(state, telemetry is live)``. Never reports live for a synthetic recording.

    Freshness is measured from the newest frame that carried a measurement, not
    from the newest frame of any kind. A device can keep sending messages the
    decoder maps to no field -- the DP3 emits housekeeping throughout a telemetry
    outage -- and counting those as liveness reports "recording" while every
    sensor it feeds expires, which is the one combination that cannot be true.
    """
    session = reading["session"]
    if session["synthetic"]:
        return "synthetic", False
    if session["status"] != "recording":
        return session["status"], False
    last_telemetry = reading.get("last_telemetry_utc_ns")
    if last_telemetry is None:
        # No measurement has arrived yet in this session. Frames alone, however
        # recent, do not make a recording; this is still the pre-telemetry wait.
        return "waiting", False
    if (now_ns - last_telemetry) / 1e9 > stale_seconds:
        return "stale", False
    return "recording", True


def conversion_loss(payload):
    """Loss from the operands this payload publishes, or None if any is withheld.

    Never a partial figure: a missing operand counted as zero would book the
    whole of the other side as loss. Deliberately not clamped at zero either;
    a negative value is meter disagreement, and clamping it would bias the
    accumulated loss energy upward. Rounded to a tenth of a watt because the
    operands are float32 on the wire and their difference carries noise digits.
    """
    operands = [payload.get(key) for key in CONVERSION_LOSS_OPERANDS]
    if not all(isinstance(value, (int, float)) for value in operands):
        return None
    total_in, total_out, pack = operands
    return round(total_in - total_out - pack, 1)


def state_payload(reading, *, now_ns=None, stale_seconds=STALE_SECONDS):
    """``(payload, live)``. Measurements are omitted whenever telemetry is not live."""
    now_ns = time.time_ns() if now_ns is None else now_ns
    state, live = collector_state(reading, now_ns=now_ns, stale_seconds=stale_seconds)
    payload = {entity.key: None for entity in entities()}
    payload["collector_state"] = state
    if state == "synthetic":
        # A demonstration recording must never reach Home Assistant's long-term
        # statistics, which survive recorder purges.
        return payload, False
    if live:
        for key, observation in reading["values"].items():
            if not observation_fresh(observation, now_ns, stale_seconds):
                continue
            value = observation["value"]
            # SoC is an estimate, not a precision measurement. The device
            # reports noisy float values with meaningless extra digits;
            # publish tenths-of-a-percent values to avoid needless MQTT and HA
            # state churn while retaining the raw value in the local recorder.
            if key in {"bms_batt_soc", "cms_batt_soc"} and isinstance(value, (int, float)):
                value = round(value, 1)
            payload[key] = value if isinstance(value, (int, float)) and math.isfinite(value) else None
        # From the values above, after their own freshness and finiteness
        # checks, so the loss exists exactly while all three operands do.
        payload["conversion_loss_w"] = conversion_loss(payload)
    payload["frame_count"] = reading["count"]
    # The collector's own constant (runtime.REASONS), which tells apart the
    # failures collector_state cannot: waiting or error for every one of them.
    payload["collector_reason"] = reading.get("reason")
    payload["last_frame"] = as_timestamp(reading["last_utc_ns"])
    payload["last_event"] = reading["event"]["kind"] if reading["event"] else None
    payload["incident"] = "ON" if reading["incidents"] else "OFF"
    return payload, live


class Bridge:
    def __init__(self, config, database, *, interval=None, stale_seconds=STALE_SECONDS, client=None):
        self.config = config
        self.database = Path(database)
        self.interval = float(interval or config.mqtt_interval)
        self.stale_seconds = stale_seconds
        self.dev_id = device_id(config.serial)
        self.base = HA_ID_PREFIX + "/" + self.dev_id
        self.client = client
        self.announced = False
        self.telemetry_online = None
        self.available_online = None
        self.collector_reported = None
        self.reason_reported = None
        self.expired_reported = None
        self.recording = RecordingReader(self.database)
        self.stopped = threading.Event()

    def build_client(self):
        import paho.mqtt.client as mqtt
        client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2, client_id=HA_ID_PREFIX + "-" + self.dev_id)
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
        # paho passes the reason differently across callback signatures; take
        # the last positional argument that looks like one and never format a
        # broker-supplied string into the log.
        reason = args[-2] if len(args) >= 2 else (args[0] if args else "unknown")
        log_transition("mqtt", "connected", "disconnected", f"reason={reason}")
        # Everything retained is now suspect: report the next values afresh.
        self.available_online = None
        self.telemetry_online = None
        self.collector_reported = None
        self.reason_reported = None
        self.expired_reported = None

    def on_connect(self, client, userdata, flags, reason_code, properties=None):
        if getattr(reason_code, "is_failure", reason_code != 0):
            log_transition("mqtt", "connecting", "refused", f"reason={reason_code}")
            return
        log_transition("mqtt", "disconnected", "connected", "device " + self.dev_id)
        # Re-announce after every reconnect; a broker restart can drop retained state.
        self.announced = False
        self.telemetry_online = None
        client.subscribe(STATUS_TOPIC, qos=1)
        if self.config.allow_control:
            client.subscribe(self.base + "/control/+/set", qos=1)
        client.publish(self.base + "/availability", "online", qos=1, retain=True)
        log_transition("availability", self.available_online, "online")
        self.available_online = "online"

    def on_message(self, client, userdata, message):
        # Home Assistant publishes its birth message after every restart. Discovery is
        # retained, but replaying it is what reliably rebuilds the entities.
        if message.topic == STATUS_TOPIC and message.payload.decode("utf-8", "replace").strip() == "online":
            self.announced = False
            return
        # Retained output requests may predate this subscription by months.
        # Never give them a new file timestamp and hence a new execution lease.
        if getattr(message, "retain", False):
            return
        # <HA_ID_PREFIX>/<dev_id>/control/<key>/set
        parts = message.topic.split("/")
        if len(parts) == 5 and parts[:3] == [HA_ID_PREFIX, self.dev_id, "control"] and parts[4] == "set":
            self.queue_control(parts[3], message.payload.decode("utf-8", "replace").strip())

    def queue_control(self, key, payload):
        """Hand a command to the collector as a file. This process never touches BLE.

        A bare ON/OFF is still handed over, marked unguarded, so the collector
        records the refusal beside every other control event instead of this
        process keeping a second evidence trail of its own.
        """
        if not self.config.allow_control:
            return
        if key not in {name for name, _ in CONTROLS}:
            return
        if payload in GUARDED_PAYLOADS:
            value, guarded = GUARDED_PAYLOADS[payload], True
        elif payload.upper() in ("ON", "OFF"):
            value, guarded = payload.upper() == "ON", False
        else:
            return
        # Dated, bounded, and dropped rather than retried if it cannot be
        # written: a stale output command is worse than a missing one.
        control_queue.write_request(self.database.parent / control_queue.DP3_DIRECTORY,
                                    key, value, guarded=guarded)

    def announce(self):
        payloads = discovery_payloads(self.dev_id, firmware=self.config.firmware,
                                      control=self.config.allow_control)
        for topic, config in payloads.items():
            self.client.publish(topic, json.dumps(config, ensure_ascii=False), qos=1, retain=True)
        if not self.config.allow_control:
            # Retained discovery outlives the setting. Clear the switches so
            # turning control off actually removes them from Home Assistant.
            for topic in control_discovery(self.dev_id, {}, {}):
                self.client.publish(topic, "", qos=1, retain=True)
        self.announced = True

    def set_telemetry(self, live, detail=""):
        if live != self.telemetry_online:
            log_transition("telemetry",
                           None if self.telemetry_online is None else
                           ("online" if self.telemetry_online else "offline"),
                           "online" if live else "offline", detail)
            self.client.publish(self.base + "/telemetry", "online" if live else "offline", qos=1, retain=True)
            self.telemetry_online = live

    def read(self):
        """``(reading, error)``; see RecordingReader."""
        return self.recording.read(lambda: latest(self.database))

    def publish_once(self):
        reading, error = self.read()
        if not self.announced:
            self.announce()
        if not reading and not error:
            self.set_telemetry(False, "no recording")
            return None
        now_ns = time.time_ns()
        if error:
            # Published, not skipped: the retained state would otherwise go on
            # reading "recording" over values this bridge can no longer see.
            payload, live = unreadable_payload(entities()), False
            detail = "database unreadable: " + error
        else:
            payload, live = state_payload(reading, now_ns=now_ns, stale_seconds=self.stale_seconds)
            # Frame age, telemetry age and per-field ages together say WHICH
            # expiry fired: the link going quiet, the telemetry stream stopping
            # while the device still sends other messages, or one property aging
            # out while the rest keep arriving. Frame age alone cannot tell the
            # first two apart, and reports the second as a healthy link serving
            # old data.
            last = reading.get("last_utc_ns")
            frame_age = f"{(now_ns - last) / 1e9:.0f}s" if last else "never"
            telemetry = reading.get("last_telemetry_utc_ns")
            telemetry_age = f"{(now_ns - telemetry) / 1e9:.0f}s" if telemetry else "never"
            detail = (f"last frame {frame_age}; last telemetry {telemetry_age}; "
                      + field_age_summary(reading, now_ns))
        log_transition("collector_state", self.collector_reported,
                       payload["collector_state"], detail)
        self.collector_reported = payload["collector_state"]
        if not error:
            # An unreadable recording says nothing about the collector's reason
            # or any field's age; those resume from what was last reported.
            log_transition("collector_reason", self.reason_reported, payload["collector_reason"])
            self.reason_reported = payload["collector_reason"]
            expired = expired_fields(payload)
            log_transition("expired fields",
                           None if self.expired_reported is None else
                           (", ".join(self.expired_reported) or "none"),
                           ", ".join(expired) or "none", detail)
            self.expired_reported = expired
        self.client.publish(self.base + "/state", json.dumps(payload, allow_nan=False), qos=0, retain=True)
        self.set_telemetry(live, detail)
        return payload

    def stop(self):
        self.stopped.set()

    def run(self, once=False):
        if self.config.role != "collector":
            # The batteries' MQTT identity belongs to the machine recording
            # them; a second publisher would overwrite its retained state.
            raise ValueError(VIEWER_REFUSAL)
        if not self.config.mqtt_host:
            raise ValueError("No MQTT broker configured. Set the broker address in Settings first.")
        with single_instance(self.database, LOCK_NAME,
                             "A bridge is already publishing; stop it before starting another."):
            self.client = self.client or self.build_client()
            try:
                self.client.connect(self.config.mqtt_host, self.config.mqtt_port, keepalive=60)
            except OSError:
                # Never echo the host, user or password back out.
                raise ValueError("Cannot reach the MQTT broker. Check the address, port and credentials.") from None
            self.client.loop_start()
            try:
                while True:
                    self.publish_once()
                    if once or self.stopped.wait(self.interval):
                        break
            finally:
                self.shutdown()

    def shutdown(self):
        log_transition("availability", self.available_online, "offline", "bridge stopping")
        self.available_online = "offline"
        with contextlib.suppress(Exception):
            self.client.publish(self.base + "/availability", "offline", qos=1,
                                retain=True).wait_for_publish(timeout=2)
        with contextlib.suppress(Exception):
            self.client.loop_stop()
        with contextlib.suppress(Exception):
            self.client.disconnect()


def describe(config, database, *, stale_seconds=STALE_SECONDS, out=None):
    """Print what would be published, connecting to nothing. Verification without a broker."""
    out = out or sys.stdout
    dev_id = device_id(config.serial)
    payloads = discovery_payloads(dev_id, firmware=config.firmware,
                                  control=config.allow_control)
    # Wide enough for the longest seeded ID; the name-derived ones run long.
    width = max(len(payload["default_entity_id"]) for payload in payloads.values()) + 2
    row = "  {:<" + str(width) + "}{:<6}{:<14}{:<18}{}"
    header = row.format("entity_id", "unit", "device_class", "state_class", "category")
    base = HA_ID_PREFIX + "/" + dev_id
    print("Device        " + HA_ID_PREFIX + "_" + dev_id + "  (EcoFlow DELTA Pro 3)", file=out)
    print("State topic   " + base + "/state", file=out)
    print("Availability  " + base + "/availability (bridge), "
          + base + "/telemetry (device)", file=out)
    print("\n{} discovery messages:\n".format(len(payloads)), file=out)
    print(header, file=out)
    for topic, payload in payloads.items():
        print(row.format(
            payload["default_entity_id"],
            payload.get("unit_of_measurement", ""),
            payload.get("device_class", ""),
            payload.get("state_class", ""),
            payload.get("entity_category", "")), file=out)
    reading = latest(database) if Path(database).exists() else {}
    print("", file=out)
    if not reading:
        print("No recorded sessions; the bridge would publish telemetry offline.", file=out)
        return payloads, None
    payload, live = state_payload(reading, stale_seconds=stale_seconds)
    print("Telemetry availability: " + ("online" if live else "offline"), file=out)
    print("State payload:", file=out)
    print(json.dumps(payload, indent=2, ensure_ascii=False, allow_nan=False), file=out)
    return payloads, payload
