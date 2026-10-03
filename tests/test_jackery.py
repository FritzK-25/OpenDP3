import asyncio
import base64
import json
from types import SimpleNamespace

import pytest

from opendp3.jackery import (
    Identity,
    LocalReader,
    _ResponseAssembler,
    _command,
    _rc4_frame,
    _session_keys,
    jackery_control_command,
    jackery_control_value,
    _winrt_address_type,
    _winrt_device_id,
    _winrt_connectable,
    parse_advertisement,
)


def test_explorer_1000_v2_advertisement_decodes():
    manufacturer = {12546: bytes.fromhex("3233343536373839303132333435")}
    service = {
        "0000bdee-0000-1000-8000-00805f9b34fb":
            bytes.fromhex("2e00498f4510af77c78afc63ce9d")
    }
    identity = parse_advertisement(manufacturer, service)
    assert identity is not None
    assert identity.serial == "123456789012345"
    assert identity.model_code == 8
    assert identity.battery_level == 84
    assert identity.encryption_key


def test_session_key_keeps_every_derived_byte():
    manufacturer = {12546: bytes.fromhex("3233343536373839303132333435")}
    service = {
        "0000bdee-0000-1000-8000-00805f9b34fb":
            bytes.fromhex("2e00498f4510af77c78afc63ce9d")
    }
    identity = parse_advertisement(manufacturer, service)
    keys = _session_keys(identity.encryption_key)
    # Six serial characters, a six-byte GUID and the ten-byte salt.  RC4 takes
    # the lot; cutting it to an AES block size is what silenced the station.
    assert len(keys[0]) == 22
    assert keys[0] == base64.b64decode(identity.encryption_key)
    assert keys[-1] == keys[0][:16]


def test_truncated_key_cannot_read_a_full_key_frame():
    keys = _session_keys(base64.b64encode(bytes(range(22))).decode())
    properties = {"rb": 84, "oac": 1}
    reply = _command(0xFC, 0x03, json.dumps(properties, separators=(",", ":")))
    frame = _rc4_frame(reply, keys[0])
    assert _ResponseAssembler(keys[0]).feed(frame) == properties
    assert _ResponseAssembler(keys[1]).feed(frame) is None
    # A single connection may try both, so the assembler must find the right one.
    assert _ResponseAssembler(keys).feed(frame) == properties


def test_single_packet_status_response_decodes():
    key = b"0123456789abcdef"
    properties = {"rb": 84, "ip": 623, "oac": 1}
    decoded = _command(0xFC, 0x03, json.dumps(properties, separators=(",", ":")))
    assert _ResponseAssembler(key).feed(_rc4_frame(decoded, key)) == properties


def test_battery_save_command_uses_the_allowlisted_portable_route():
    expected = _command(0x0B, 0x04, '{"lps":1}')
    assert jackery_control_command("jackery_battery_save", "save") == expected
    assert jackery_control_value("jackery_battery_save", "save") == 1


def test_jackery_control_rejects_unapproved_values():
    with pytest.raises(ValueError):
        jackery_control_command("jackery_battery_save", "custom")
    with pytest.raises(ValueError):
        jackery_control_command("jackery_power_off", True)


def test_local_reader_control_defaults_fail_closed_before_wire_write():
    identity = Identity(
        "AA:BB:CC:DD:EE:FF", "Explorer", "123456789012345", 8, 50, -40,
        base64.b64encode(b"0123456789abcdef").decode(),
    )
    reader = LocalReader(identity)
    reader.key = b"0123456789abcdef"
    writes = []

    async def capture(payload, label):
        writes.append((payload, label))

    reader._write = capture
    with pytest.raises(PermissionError, match="turned off"):
        asyncio.run(reader.send_control("jackery_ac_output", True))
    assert writes == []


def test_local_reader_control_boundary_authority_is_single_use():
    identity = Identity(
        "AA:BB:CC:DD:EE:FF", "Explorer", "123456789012345", 8, 50, -40,
        base64.b64encode(b"0123456789abcdef").decode(),
    )
    reader = LocalReader(identity, allow_control=True)
    reader.key = b"0123456789abcdef"
    writes = []

    async def capture(payload, label):
        writes.append((payload, label))

    reader._write = capture
    asyncio.run(reader.send_control("jackery_ac_output", True))
    assert len(writes) == 1
    assert writes[0][1] == "control jackery_ac_output"
    assert reader.allow_control is False

    with pytest.raises(PermissionError, match="turned off"):
        asyncio.run(reader.send_control("jackery_ac_output", False))
    assert len(writes) == 1


def test_local_reader_control_authority_is_consumed_before_write_await():
    identity = Identity(
        "AA:BB:CC:DD:EE:FF", "Explorer", "123456789012345", 8, 50, -40,
        base64.b64encode(b"0123456789abcdef").decode(),
    )
    reader = LocalReader(identity, allow_control=True)
    reader.key = b"0123456789abcdef"
    writes = []

    async def exercise():
        write_started = asyncio.Event()
        release_write = asyncio.Event()

        async def blocking_write(payload, label):
            writes.append((payload, label))
            write_started.set()
            await release_write.wait()

        reader._write = blocking_write
        first = asyncio.create_task(reader.send_control("jackery_ac_output", True))
        await write_started.wait()
        assert reader.allow_control is False
        with pytest.raises(PermissionError, match="turned off"):
            await reader.send_control("jackery_ac_output", False)
        assert len(writes) == 1
        release_write.set()
        await first

    asyncio.run(exercise())
    assert len(writes) == 1


def test_multi_packet_status_response_reassembles():
    key = base64.b64decode("MDEyMzQ1CgsMDQ4PNipTWTFjNUI5QA==")[:16]
    properties = {"rb": 84, "ip": 623, "op": 154, "oac": 1, "odc": 0}
    body = json.dumps(properties, separators=(",", ":")).encode().hex()
    split = (len(body) // 4) * 2
    fragments = [body[:split], body[split:]]
    assembler = _ResponseAssembler(key)
    first = "DFEC" + "80000000" + "0001" + "0002" + fragments[0]
    second = "DFEC" + "80000000" + "0002" + "0002" + fragments[1]
    assert assembler.feed(_rc4_frame(first, key)) is None
    assert assembler.feed(_rc4_frame(second, key)) == properties


def test_multi_packet_status_response_accepts_out_of_order_tail():
    key = b"0123456789abcdef"
    properties = {"rb": 41, "bt": 287}
    body = json.dumps(properties, separators=(",", ":")).encode().hex()
    split = (len(body) // 4) * 2
    first = "DFEC" + "80000000" + "0001" + "0002" + body[:split]
    second = "DFEC" + "80000000" + "0002" + "0002" + body[split:]
    assembler = _ResponseAssembler(key)
    assert assembler.feed(_rc4_frame(second, key)) is None
    assert assembler.feed(_rc4_frame(first, key)) == properties


def test_winrt_advertisement_address_type_is_preserved():
    public = SimpleNamespace(details=SimpleNamespace(
        adv=SimpleNamespace(bluetooth_address_type=0), scan=None))
    random = SimpleNamespace(details=SimpleNamespace(
        adv=None, scan=SimpleNamespace(bluetooth_address_type=1)))
    assert _winrt_address_type(public) == "public"
    assert _winrt_address_type(random) == "random"
    assert _winrt_address_type(object()) is None


def test_winrt_connectable_advertisement_is_required():
    connectable = SimpleNamespace(details=SimpleNamespace(
        adv=SimpleNamespace(advertisement_type=0)))
    directed = SimpleNamespace(details=SimpleNamespace(
        adv=SimpleNamespace(advertisement_type=1)))
    beacon = SimpleNamespace(details=SimpleNamespace(
        adv=SimpleNamespace(advertisement_type=3)))
    assert _winrt_connectable(connectable) is True
    assert _winrt_connectable(directed) is True
    assert _winrt_connectable(beacon) is False
    assert _winrt_connectable(object()) is None


def _discovery_fakes(monkeypatch, serials):
    """Advertise one Explorer per serial, in order, and record what is attached."""
    from types import SimpleNamespace
    from opendp3 import jackery

    opened = []

    class FakeScanner:
        def __init__(self, detection_callback, **_):
            self.callback = detection_callback

        async def start(self):
            for index, serial in enumerate(serials):
                device = SimpleNamespace(address=f"AA:BB:CC:DD:EE:{index:02X}",
                                         name="HT Explorer")
                advertisement = SimpleNamespace(manufacturer_data={"serial": serial},
                                                service_data={}, rssi=-50)
                self.callback(device, advertisement)

        async def stop(self):
            pass

    class FakeReader:
        def __init__(self, identity):
            self.identity = identity

        async def open(self):
            opened.append(self.identity.serial)
            return self

    def parse(manufacturer_data, _service_data):
        return jackery.Identity("", "", manufacturer_data["serial"], 8, 50, None, "key")

    async def no_device_id(*_):
        return None

    monkeypatch.setattr(jackery, "BleakScanner", FakeScanner)
    monkeypatch.setattr(jackery, "LocalReader", FakeReader)
    monkeypatch.setattr(jackery, "parse_advertisement", parse)
    monkeypatch.setattr(jackery, "_winrt_device_id", no_device_id)
    monkeypatch.setattr(jackery, "_winrt_address_type", lambda _device: None)
    return jackery, opened


async def test_discover_reader_skips_other_explorers_when_given_a_serial(monkeypatch):
    jackery, opened = _discovery_fakes(monkeypatch, ["111111111111111", "222222222222222"])

    reader = await jackery.discover_reader(1, "222222222222222")

    assert reader.identity.serial == "222222222222222"
    assert opened == ["222222222222222"]


async def test_discover_reader_without_a_serial_takes_the_first_explorer(monkeypatch):
    jackery, opened = _discovery_fakes(monkeypatch, ["111111111111111", "222222222222222"])

    reader = await jackery.discover_reader(1)

    assert reader.identity.serial == "111111111111111"
    assert opened == ["111111111111111"]
