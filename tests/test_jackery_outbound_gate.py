"""The Jackery's one GATT write site checks every command it is handed.

The allowlist used to live only where control commands are built
(``jackery_control_command``). ``LocalReader._write`` took bytes that were
already encrypted and sent whatever it was given, so a new write site, or a new
action added beside the controls, would have reached the station with no second
check: the design the DP3's ``OutboundGate`` exists to rule out. These tests use
a fake Bleak client, so the session and its write site are the real ones.
"""
import asyncio
import base64
import json
import re

import pytest

from openpowerstation import jackery
from openpowerstation.jackery import (
    JACKERY_CONTROLS,
    Identity,
    LocalReader,
    _command,
    _decrypt_rc4_frame,
    _rc4_frame,
    jackery_control_command,
)

KEY = b"0123456789abcdef"
STATUS = _command(0xFC, 0x03)


class Radio:
    """A Bleak client that keeps every payload put on the air, unconverted."""

    def __init__(self):
        self.payloads = []
        self.answer = None

    async def write_gatt_char(self, _uuid, payload, response=False):
        self.payloads.append(payload)
        if self.answer is not None:
            self.answer(payload)


def open_reader(*, allow_control=False, key=KEY):
    identity = Identity("AA:BB:CC:DD:EE:FF", "Explorer", "856199990000000", 8, 50, -40,
                        base64.b64encode(KEY).decode())
    reader = LocalReader(identity, allow_control=allow_control)
    reader._bleak = Radio()
    reader.key = key
    return reader


def on_air(reader):
    """Each payload that reached the radio, decrypted back to its plaintext command."""
    return [None if not isinstance(payload, bytes) else "DFEC" + (_decrypt_rc4_frame(payload, KEY) or "")
            for payload in reader._bleak.payloads]


def test_bytes_already_encrypted_never_reach_the_radio():
    """The write site encrypts; a caller cannot hand it bytes the gate never read."""
    reader = open_reader()
    with pytest.raises(PermissionError):
        asyncio.run(reader._write(_rc4_frame(STATUS, KEY), "status"))
    assert reader._bleak.payloads == []


@pytest.mark.parametrize("command", [
    # An action outside the allowlist, written as any control would be.
    _command(0x0D, 0x04, '{"wifi":1}'),
    # The status query carrying a body.
    _command(0xFC, 0x03, '{"oac":1}'),
    # The clock sync with an extra key, a key missing, a non-integer value,
    # and a spelling the allowlist would not have built.
    _command(0x0F, 0x08, '{"ts":1790000000,"uo":0,"pm":0}'),
    _command(0x0F, 0x08, '{"ts":1790000000}'),
    _command(0x0F, 0x08, '{"ts":"1790000000","uo":0}'),
    _command(0x0F, 0x08, '{"ts":true,"uo":0}'),
    _command(0x0F, 0x08, '{"ts": 1790000000,"uo":0}'),
    _command(0x0F, 0x08, '{"ts":1790000000,"ts":1,"uo":0}'),
    # An allowlisted control body, but with no grant from send_control.
    jackery_control_command("jackery_ac_output", True),
    jackery_control_command("jackery_auto_power_off", "2h"),
    # Not hex at all.
    "not a command",
], ids=["unknown-action", "status-with-body", "sync-extra-key", "sync-missing-key",
        "sync-string", "sync-bool", "sync-respelled", "sync-duplicate-key",
        "ungranted-ac", "ungranted-power-off", "not-hex"])
def test_the_write_site_refuses_anything_outside_its_three_shapes(command):
    reader = open_reader(allow_control=True)
    with pytest.raises(PermissionError):
        asyncio.run(reader._write(command, "probe"))
    assert reader._bleak.payloads == [], "a refused command reached the radio"


def test_a_granted_control_goes_out_once_and_only_as_built():
    reader = open_reader(allow_control=True)
    command = jackery_control_command("jackery_auto_power_off", "2h")

    asyncio.run(reader.send_control("jackery_auto_power_off", "2h"))
    assert on_air(reader) == [command.upper()]

    # The grant covered that one write. The same bytes again, from anywhere
    # but send_control, are refused at the write site.
    with pytest.raises(PermissionError):
        asyncio.run(reader._write(command, "replay"))
    assert len(reader._bleak.payloads) == 1


def test_a_grant_admits_only_the_command_it_was_given_for():
    """Even granted, the write site requires the exact allowlisted bytes."""
    granted = jackery_control_command("jackery_ac_output", True)
    jackery.check_outbound(granted, granted)
    with pytest.raises(PermissionError):
        jackery.check_outbound(jackery_control_command("jackery_ac_output", False), granted)
    for outside in (_command(0x04, 0x04, '{"oac":1,"pm":0}'), _command(0x0C, 0x04, '{"pm":5}')):
        with pytest.raises(PermissionError):
            jackery.check_outbound(outside, granted)
        with pytest.raises(PermissionError):
            # A grant somehow naming a command outside the allowlist admits nothing.
            jackery.check_outbound(outside, outside)


@pytest.mark.parametrize("control,value", [
    (control, value)
    for control, spec in JACKERY_CONTROLS.items()
    for value in ((True, False) if spec["kind"] == "switch" else spec["values"])
])
def test_every_allowlisted_control_passes_the_gate_when_granted(control, value):
    reader = open_reader(allow_control=True)
    asyncio.run(reader.send_control(control, value))
    assert on_air(reader) == [jackery_control_command(control, value).upper()]


def test_a_new_session_sends_only_the_clock_sync_and_the_status_query():
    """The sweep over candidate keys still passes the gate, and sends nothing else."""
    reader = open_reader(key=None)
    reply = _command(0xFC, 0x03, json.dumps({"rb": 50, "oac": 0}, separators=(",", ":")))

    def answer(payload):
        if "DFEC" + (_decrypt_rc4_frame(payload, KEY) or "") == STATUS:
            reader._consume(_rc4_frame(reply, KEY))

    reader._bleak.answer = answer
    assert asyncio.run(reader.read(timeout=1)) == {"rb": 50, "oac": 0}
    sync, status = on_air(reader)
    assert re.fullmatch(r"DFEC000F08[0-9A-F]{2}[0-9A-F]+", sync)
    body = json.loads(bytes.fromhex(sync[12:]))
    assert set(body) == {"ts", "uo"} and all(type(body[key]) is int for key in body)
    assert status == STATUS
    assert reader.key == KEY
