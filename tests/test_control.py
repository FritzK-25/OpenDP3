"""Device control: the outbound gate, the command queue, and the bridge relay.

Control is the one place this application writes to the battery, so the tests
that matter most are the ones asserting what it refuses.
"""
import json
import os
import time
from types import SimpleNamespace

import pytest

from opendp3.bridge import (CONTROL_FEEDBACK_FIELDS, CONTROL_QUEUE_MAX_FILES, CONTROLS, Bridge,
                            control_discovery, control_topic, discovery_payloads)
from opendp3.config import Config
from opendp3.protocol import (CONTROL_CMD_ID, CONTROL_FIELDS, CONTROL_HEADER, OutboundGate,
                              PolicyError, auth_packet, control_packet)
from opendp3.runtime import CONTROL_MAX_AGE, Service
from opendp3.vendor.packet import Packet
from opendp3.vendor.pb import mr521_pb2

SERIAL = "MR51123456789012"


def make_config(**overrides):
    values = dict(address="AA:BB:CC:DD:EE:FF", serial=SERIAL, user_id="123456",
                  mqtt_host="192.0.2.10")
    values.update(overrides)
    return Config(**values)


def raw_packet(payload, cmd_id=CONTROL_CMD_ID):
    src, dst, cmd_set, dsrc, ddst, version, seq, product_id = CONTROL_HEADER
    return Packet(src, dst, cmd_set, cmd_id, payload, dsrc, ddst, version, seq, product_id)


def open_gate():
    gate = OutboundGate(allow_control=True)
    gate.stage = "closed"  # Authentication complete.
    return gate


# --------------------------------------------------------------------------- packet


def test_control_packet_matches_the_upstream_delta_pro_3_route():
    """Header comes from ha-ef-ble's control path; see THIRD_PARTY_NOTICES.md."""
    p = control_packet("cfg_hv_ac_out_open", True)
    assert (p.src, p.dst, p.cmd_set, p.cmd_id) == (0x20, 0x02, 0xFE, 0x11)
    assert (p.dsrc, p.ddst, p.version, p.seq, p.product_id) == (1, 1, 0x13, b"\0" * 4, 0)
    echo = mr521_pb2.ConfigWrite()
    echo.ParseFromString(p.payload)
    assert echo.cfg_hv_ac_out_open is True
    assert [d.name for d, _ in echo.ListFields()] == ["cfg_hv_ac_out_open"]


def test_control_packet_encodes_off_as_a_present_false():
    """"Off" must travel as an explicitly set false, not an omitted field."""
    p = control_packet("cfg_lv_ac_out_open", False)
    echo = mr521_pb2.ConfigWrite()
    echo.ParseFromString(p.payload)
    assert [d.name for d, _ in echo.ListFields()] == ["cfg_lv_ac_out_open"]
    assert echo.cfg_lv_ac_out_open is False


@pytest.mark.parametrize("field", ["cfg_power_off", "reset_factory_setting",
                                   "cfg_bms_power_off", "cfg_usb_open", "nonsense"])
def test_control_packet_refuses_to_build_anything_unapproved(field):
    with pytest.raises(PolicyError):
        control_packet(field, True)


def test_control_packet_requires_a_boolean():
    with pytest.raises(PolicyError):
        control_packet("cfg_hv_ac_out_open", 1)


# --------------------------------------------------------------------------- gate


@pytest.mark.parametrize("field", sorted(CONTROL_FIELDS))
@pytest.mark.parametrize("value", [True, False])
def test_gate_allows_the_two_output_controls(field, value):
    open_gate().check(control_packet(field, value))


def test_gate_refuses_control_while_it_is_turned_off():
    gate = OutboundGate(allow_control=False)
    gate.stage = "closed"
    with pytest.raises(PolicyError, match="turned off"):
        gate.check(control_packet("cfg_hv_ac_out_open", True))


@pytest.mark.parametrize("stage", ["new", "ecdh", "key", "status", "login"])
def test_gate_refuses_control_before_authentication_completes(stage):
    gate = OutboundGate(allow_control=True)
    gate.stage = stage
    with pytest.raises(PolicyError, match="authenticated"):
        gate.check(control_packet("cfg_lv_ac_out_open", True))


@pytest.mark.parametrize("field,value", [("cfg_power_off", True),
                                         ("reset_factory_setting", 1),
                                         ("cfg_bms_power_off", True),
                                         ("cfg_usb_open", True),
                                         ("cfg_max_chg_soc", 90),
                                         ("cfg_installment_payment_serve_enable", True)])
def test_gate_refuses_every_other_configwrite_field(field, value):
    """ConfigWrite carries factory reset and power-off. None may reach the radio."""
    message = mr521_pb2.ConfigWrite(**{field: value})
    with pytest.raises(PolicyError):
        open_gate().check(raw_packet(message.SerializeToString()))


def test_gate_refuses_an_approved_field_smuggled_beside_a_dangerous_one():
    message = mr521_pb2.ConfigWrite(cfg_hv_ac_out_open=True, cfg_power_off=True)
    with pytest.raises(PolicyError):
        open_gate().check(raw_packet(message.SerializeToString()))


def test_gate_refuses_an_empty_or_unreadable_payload():
    with pytest.raises(PolicyError):
        open_gate().check(raw_packet(b""))
    with pytest.raises(PolicyError):
        open_gate().check(raw_packet(b"\xff\xff\xff"))


@pytest.mark.parametrize("trailer", [
    b"\x9a\x02\x01\x01",   # field 35, length-delimited: unknown to this schema
    b"\xf8\x3f\x01",       # field 127, varint: unknown to this schema
    b"\x98\x02\x01",       # field 35, varint
])
def test_gate_refuses_unknown_fields_riding_behind_an_approved_one(trailer):
    """Protobuf keeps unknown fields and re-emits them, so a round-trip check is
    not enough. Only bytes the allowlist can reproduce exactly are permitted --
    otherwise a field this schema does not know, but firmware might, gets through."""
    payload = control_packet("cfg_hv_ac_out_open", True).payload
    with pytest.raises(PolicyError):
        open_gate().check(raw_packet(payload + trailer))


def test_gate_refuses_a_non_canonical_encoding_of_an_approved_field():
    """A duplicate field is legal protobuf but is not what the allowlist emits."""
    payload = control_packet("cfg_hv_ac_out_open", True).payload
    with pytest.raises(PolicyError):
        open_gate().check(raw_packet(payload + payload))


def test_gate_refuses_a_different_command_id_on_the_control_route():
    payload = control_packet("cfg_hv_ac_out_open", True).payload
    with pytest.raises(PolicyError):
        open_gate().check(raw_packet(payload, cmd_id=0x12))


def test_gate_still_permits_the_authentication_exchange_with_control_off():
    gate = OutboundGate(allow_control=False)
    gate.stage = "status"
    gate.check(auth_packet(0x89))
    gate.stage = "login"
    gate.check(auth_packet(0x86, b"0123456789ABCDEF0123456789ABCDEF"))


def test_enabling_control_does_not_widen_the_authentication_allowlist():
    gate = OutboundGate(allow_control=True)
    gate.stage = "status"
    with pytest.raises(PolicyError):
        gate.check(auth_packet(0x89, b"unexpected"))


# --------------------------------------------------------------------------- queue


def test_service_turns_request_files_into_commands(tmp_path):
    service = Service(make_config(allow_control=True), tmp_path / "recordings.sqlite")
    service.control_dir.mkdir(parents=True)
    (service.control_dir / "1-a.json").write_text(
        json.dumps({"field": "cfg_hv_ac_out_open", "value": True}), encoding="utf-8")
    (service.control_dir / "2-b.json").write_text(
        json.dumps({"field": "cfg_lv_ac_out_open", "value": False}), encoding="utf-8")
    service.drain_control_files()
    assert [service.commands.get_nowait() for _ in range(2)] == [
        ("control", "cfg_hv_ac_out_open=on"), ("control", "cfg_lv_ac_out_open=off")]
    # Consumed, so a restart cannot replay a stale output command.
    assert list(service.control_dir.glob("*.json")) == []


@pytest.mark.parametrize("body", ['{"field": "cfg_hv_ac_out_open"}',
                                  '{"field": 5, "value": true}',
                                  '{"field": "cfg_hv_ac_out_open", "value": "yes"}',
                                  '["cfg_hv_ac_out_open", true]',
                                  'not json at all'])
def test_service_drops_malformed_request_files(tmp_path, body):
    service = Service(make_config(allow_control=True), tmp_path / "recordings.sqlite")
    service.control_dir.mkdir(parents=True)
    (service.control_dir / "1-a.json").write_text(body, encoding="utf-8")
    service.drain_control_files()
    assert service.commands.empty()
    assert list(service.control_dir.glob("*.json")) == []


def test_service_drains_even_while_control_is_off(tmp_path):
    """Otherwise requests accumulate unseen and replay in a burst once enabled."""
    service = Service(make_config(allow_control=False), tmp_path / "recordings.sqlite")
    service.control_dir.mkdir(parents=True)
    (service.control_dir / "1-a.json").write_text(
        json.dumps({"field": "cfg_lv_ac_out_open", "value": True}), encoding="utf-8")
    service.drain_control_files()
    assert service.commands.get_nowait() == ("control", "cfg_lv_ac_out_open=on")
    assert list(service.control_dir.glob("*.json")) == []


def test_service_expires_a_request_that_waited_too_long(tmp_path):
    """A switch must not obey a press the operator made minutes ago."""
    service = Service(make_config(allow_control=True), tmp_path / "recordings.sqlite")
    service.control_dir.mkdir(parents=True)
    stale = service.control_dir / "1-a.json"
    stale.write_text(json.dumps({"field": "cfg_lv_ac_out_open", "value": True}), encoding="utf-8")
    old = time.time() - (CONTROL_MAX_AGE + 5)
    os.utime(stale, (old, old))
    service.drain_control_files()
    assert service.commands.get_nowait() == ("control_expired", "cfg_lv_ac_out_open=on")
    assert list(service.control_dir.glob("*.json")) == []


def test_service_ignores_partly_written_requests(tmp_path):
    """The bridge renames into place; anything still .tmp is not read."""
    service = Service(make_config(allow_control=True), tmp_path / "recordings.sqlite")
    service.control_dir.mkdir(parents=True)
    (service.control_dir / "1-a.tmp").write_text('{"field": "cfg_hv_ac_out_open"', encoding="utf-8")
    service.drain_control_files()
    assert service.commands.empty()
    assert (service.control_dir / "1-a.tmp").exists()


# --------------------------------------------------------------------------- bridge


def test_switches_are_published_only_when_control_is_enabled():
    off = discovery_payloads("abc", control=False)
    assert not [t for t in off if "/switch/" in t]
    on = discovery_payloads("abc", control=True)
    switches = [t for t in on if "/switch/" in t]
    assert len(switches) == len(CONTROLS)
    assert len(on) == len(off) + len(CONTROLS)


def test_switches_use_real_dp3_feedback_and_field_availability():
    """A switch must stay unknown until the DP3 reports its actual port state."""
    payloads = control_discovery("abc", {}, {})
    for key, _ in CONTROLS:
        feedback = CONTROL_FEEDBACK_FIELDS[key]
        topic = f"homeassistant/switch/opendp3_abc/{key}/config"
        payload = payloads[topic]
        assert "optimistic" not in payload
        assert payload["state_topic"] == "opendp3/abc/state"
        assert feedback in payload["value_template"]
        assert "% 4" in payload["value_template"]
        assert payload["availability_mode"] == "all"
        assert [entry["topic"] for entry in payload["availability"]] == [
            "opendp3/abc/availability", "opendp3/abc/telemetry", "opendp3/abc/state"]
        assert feedback in payload["availability"][2]["value_template"]


def test_control_feedback_map_matches_the_two_allowlisted_controls():
    assert set(CONTROL_FEEDBACK_FIELDS) == CONTROL_FIELDS
    assert set(CONTROL_FEEDBACK_FIELDS.values()) == {
        "flow_info_ac_hv_out", "flow_info_ac_lv_out"
    }


def test_bridge_subscribes_to_commands_only_when_control_is_enabled(tmp_path):
    for allow, expected in ((False, 1), (True, 2)):
        client = SimpleNamespace(subscribed=[], published=[])
        client.subscribe = lambda topic, qos=0: client.subscribed.append(topic)
        client.publish = lambda topic, payload=None, qos=0, retain=False: None
        bridge = Bridge(make_config(allow_control=allow), tmp_path / "recordings.sqlite",
                        client=client)
        bridge.on_connect(client, None, None, 0)
        assert len(client.subscribed) == expected
        assert (bridge.base + "/control/+/set" in client.subscribed) is allow


def test_bridge_writes_a_command_file_the_service_can_read(tmp_path):
    database = tmp_path / "recordings.sqlite"
    bridge = Bridge(make_config(allow_control=True), database, client=None)
    bridge.on_message(None, None, SimpleNamespace(
        topic=control_topic(bridge.dev_id, "cfg_hv_ac_out_open"), payload=b"ON"))
    service = Service(make_config(allow_control=True), database)
    service.drain_control_files()
    assert service.commands.get_nowait() == ("control", "cfg_hv_ac_out_open=on")


def test_bridge_control_queue_is_bounded_without_collector(tmp_path, monkeypatch):
    """Repeated MQTT presses cannot grow the data-volume handoff without bound."""
    database = tmp_path / "recordings.sqlite"
    bridge = Bridge(make_config(allow_control=True), database, client=None)
    total = CONTROL_QUEUE_MAX_FILES + 25
    ticks = iter(10**18 + index for index in range(total))
    monkeypatch.setattr("opendp3.bridge.time.time_ns", lambda: next(ticks))

    for index in range(total):
        key = "cfg_hv_ac_out_open" if index % 2 == 0 else "cfg_lv_ac_out_open"
        bridge.queue_control(key, "ON" if index == total - 1 else "OFF")

    queued = sorted((tmp_path / "commands").glob("*.json"))
    assert len(queued) == CONTROL_QUEUE_MAX_FILES
    # Oldest-first pruning keeps exactly the newest bounded window.
    assert queued[0].name.startswith(str(10**18 + total - CONTROL_QUEUE_MAX_FILES))
    assert queued[-1].name.startswith(str(10**18 + total - 1))
    assert json.loads(queued[-1].read_text("utf-8"))["value"] is True


def test_bounded_queue_still_delivers_a_fresh_surviving_command(tmp_path, monkeypatch):
    database = tmp_path / "recordings.sqlite"
    bridge = Bridge(make_config(allow_control=True), database, client=None)
    total = CONTROL_QUEUE_MAX_FILES + 3
    ticks = iter(10**18 + index for index in range(total))
    monkeypatch.setattr("opendp3.bridge.time.time_ns", lambda: next(ticks))
    for index in range(total):
        bridge.queue_control("cfg_hv_ac_out_open", "ON" if index == total - 1 else "OFF")

    service = Service(make_config(allow_control=True), database)
    service.drain_control_files()
    commands = [service.commands.get_nowait() for _ in range(CONTROL_QUEUE_MAX_FILES)]
    assert commands[-1] == ("control", "cfg_hv_ac_out_open=on")
    assert list(service.control_dir.glob("*.json")) == []


@pytest.mark.parametrize("topic_key,payload", [("cfg_power_off", b"ON"),
                                               ("cfg_usb_open", b"ON"),
                                               ("cfg_hv_ac_out_open", b"MAYBE"),
                                               ("cfg_hv_ac_out_open", b"")])
def test_bridge_refuses_to_queue_unapproved_commands(tmp_path, topic_key, payload):
    database = tmp_path / "recordings.sqlite"
    bridge = Bridge(make_config(allow_control=True), database, client=None)
    bridge.on_message(None, None, SimpleNamespace(
        topic=control_topic(bridge.dev_id, topic_key), payload=payload))
    assert not list((tmp_path / "commands").glob("*.json")) if (tmp_path / "commands").exists() else True


def test_bridge_ignores_commands_entirely_while_control_is_off(tmp_path):
    database = tmp_path / "recordings.sqlite"
    bridge = Bridge(make_config(allow_control=False), database, client=None)
    bridge.on_message(None, None, SimpleNamespace(
        topic=control_topic(bridge.dev_id, "cfg_hv_ac_out_open"), payload=b"ON"))
    assert not (tmp_path / "commands").exists()


def test_bridge_relay_matches_the_protocol_allowlist_exactly():
    """A switch the gate would refuse must never be offered in Home Assistant."""
    assert {key for key, _ in CONTROLS} == CONTROL_FIELDS
