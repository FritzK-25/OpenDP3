import hashlib
import struct
from types import SimpleNamespace

import ecdsa
import pytest

from openpowerstation.protocol import (OutboundGate,PolicyError,ProtocolError,WireBuffer,
    parse_packet,auth_packet,identify,derive_session_key,Identity,AuthenticationError)
from openpowerstation.vendor.packet import Packet
from openpowerstation.vendor.crc import crc8
from openpowerstation.vendor.encryption import Type1Encryption,Type7Encryption
from openpowerstation.vendor.frame_assembler import EncPacketAssembler,RawHeaderAssembler,PassthroughAssembler,SimplePacketAssembler

def test_crc_corruption_and_truncation(packet):
    raw = packet(bms_max_cell_temp=0)
    assert parse_packet(raw).payload
    for n in range(20):
        with pytest.raises(ProtocolError): parse_packet(raw[:n])
    corrupt = bytearray(raw); corrupt[-1] ^= 1
    with pytest.raises(ProtocolError): parse_packet(bytes(corrupt))

def test_sentinel_xor_and_missing_sentinel():
    payload = b"\x08\x24"+b"\xbb\xbb"
    seq = b"\x1a\0\0\0"
    header = b"\xaa\x13"+struct.pack("<H",len(payload))
    raw = header+bytes([crc8(header)])+b"\x0d"+seq+b"\0\0"+bytes([2,0x21,1,1,0xFE,0x15])
    raw += bytes(v^seq[0] for v in payload)
    p = parse_packet(raw)
    assert p.payload == b"\x08\x24"
    with pytest.raises(ProtocolError): parse_packet(raw[:-1]+b"\0")

@pytest.mark.parametrize("kind",[0,1,7])
async def test_all_fragment_boundaries_and_multiple_frames(kind,packet):
    encryption = None if kind == 0 else (Type1Encryption if kind == 1 else Type7Encryption)(b"a"*16,b"b"*16)
    codec = {0:PassthroughAssembler,1:RawHeaderAssembler,7:EncPacketAssembler}[kind](encryption)
    raw1,raw2 = packet(seq=5,bms_max_cell_temp=23),packet(seq=6,bms_max_cell_temp=0)
    wire1,wire2 = await codec.encode(parse_packet(raw1)),await codec.encode(parse_packet(raw2))
    buffer = WireBuffer(kind,encryption)
    got = []
    for byte in wire1+wire2:
        got.extend(await buffer.feed(bytes([byte])))
    assert got == [raw1,raw2]
    for split in range(1,len(wire1)):
        buffer = WireBuffer(kind,encryption)
        assert await buffer.feed(wire1[:split]) == []
        assert await buffer.feed(wire1[split:]) == [raw1]

async def test_corrupt_outer_frame_recovers(packet):
    enc = Type7Encryption(b"x"*16,b"y"*16)
    codec = EncPacketAssembler(enc)
    raw = packet(bms_max_cell_temp=22)
    wire = await codec.encode(parse_packet(raw))
    broken = bytearray(wire); broken[-1] ^= 1
    buf = WireBuffer(7,enc)
    assert await buf.feed(bytes(broken)+wire) == [raw]
    assert buf.discarded == len(wire)

async def test_simple_frame_embedded_prefix_and_bounds():
    payload = b"\x01\0\x5a\x5a"+b"x"*40
    wire = SimplePacketAssembler.encode(payload)
    buf = WireBuffer(7,simple=True)
    assert await buf.feed(wire[:10]) == []
    assert await buf.feed(wire[10:]) == [payload]
    with pytest.raises(ProtocolError): await buf.feed(b"x"*70000)

@pytest.mark.parametrize("stage",["new","ecdh","key","status","login","closed"])
def test_gate_rejects_device_commands(stage):
    gate = OutboundGate(); gate.stage = stage
    for cmd in range(256):
        # Even permitted auth IDs cannot be sent to an inverter/config destination.
        with pytest.raises(PolicyError):
            gate.check(Packet(0x20,2,0xFE,cmd,b"x",version=0x13))
    for command_set,cmd in [(1,0x52),(1,0x53),(0xFE,0x11),(0xFE,0x10),(0x0F,1)]:
        with pytest.raises(PolicyError): gate.check(Packet(0x21,0x35,command_set,cmd))

def test_gate_shapes_and_post_auth_lock():
    gate = OutboundGate(); gate.stage="status"
    gate.check(auth_packet(0x89))
    with pytest.raises(PolicyError): gate.check(auth_packet(0x89,b"x"))
    gate.stage="login"
    gate.check(auth_packet(0x86,b"ABCDEF0123456789"*2))
    with pytest.raises(PolicyError): gate.check(auth_packet(0x86,b"z"*32))
    gate.stage="closed"
    with pytest.raises(PolicyError): gate.check(auth_packet(0x89))
    with pytest.raises(PolicyError): gate.command(b"\x02")

def test_advertisement_truncation_and_family():
    assert identify("AA:BB:CC:DD:EE:FF",{0xB5B5:b"\x13MR51123456789012"}).encryption == 7
    for length in (18,19,20,21,22):
        data=(b"\x13MR51123456789012"+b"\0"*20)[:length]
        assert identify("AA:BB:CC:DD:EE:FF",{0xB5B5:data}).encryption == 7
    assert identify("AA",{0xB5B5:b"\x13ZZZZ123456789012"}) is None
    assert identify("AA",{0xB5B5:b"\x13"}) is None

@pytest.mark.parametrize("seed", [b"\1\1", b"\0\0"])
async def test_full_type7_auth_with_mock_device(packet, seed):
    """Both ends compute ECDH; auth notifications are split and no real BLE is used."""
    from openpowerstation.ble import Session
    identity = Identity("AA:BB:CC:DD:EE:FF","MR51123456789012",7,0x13)
    s = Session(identity,object(),"123456",lambda *a:None,lambda *a:None)
    encryption = None
    commands = []
    srand = b"r"*16
    private = ecdsa.SigningKey.generate(curve=ecdsa.SECP160r1)
    def respond(raw):
        # Splitting the prefix and every subsequent byte tests arrival buffering.
        for byte in raw: s.receive(None,bytes([byte]))
    class Client:
        async def write_gatt_char(self,char,raw,response):
            nonlocal encryption
            if s.gate.stage in {"ecdh","key"}:
                buf = WireBuffer(7,simple=True)
                payload = (await buf.feed(raw))[0]
                commands.append(payload[:1])
                if payload[0] == 1:
                    public = ecdsa.VerifyingKey.from_string(payload[2:],curve=ecdsa.SECP160r1)
                    shared = ecdsa.ECDH(ecdsa.SECP160r1,private,public).generate_sharedsecret_bytes()
                    encryption = Type7Encryption(shared[:16],hashlib.md5(shared).digest())
                    respond(SimplePacketAssembler.encode(b"\1\0\0"+private.get_verifying_key().to_string()))
                else:
                    encrypted = await encryption.encrypt(srand+seed)
                    respond(SimplePacketAssembler.encode(b"\2"+encrypted))
                    if seed != b"\0\0":
                        encryption = Type7Encryption(derive_session_key(seed,srand),encryption.iv)
            else:
                buf = WireBuffer(7,encryption)
                p = parse_packet((await buf.feed(raw))[0])
                commands.append(p.cmd_id)
                if p.cmd_id == 0x86:
                    expected = hashlib.md5(b"123456MR51123456789012").hexdigest().upper().encode()
                    assert p.payload == expected
                response_packet = Packet(0x35,0x21,0x35,p.cmd_id,b"\0")
                respond(await EncPacketAssembler(encryption).encode(response_packet))
    s.client = Client(); s.write_char = SimpleNamespace(properties=["write"])
    if seed == b"\0\0":
        with pytest.raises(AuthenticationError, match="Unsupported session key response"):
            await s.authenticate()
        assert commands == [b"\1", b"\2"]  # The user-ID digest was never sent.
        assert not s.authenticated
        return
    await s.authenticate()
    assert s.authenticated
    assert commands == [b"\1",b"\2",0x89,0x86]
    assert s.gate.stage == "closed"
    with pytest.raises(PolicyError): await s._write(packet=auth_packet(0x89))


async def test_sustained_silence_ends_the_session(monkeypatch):
    """A device that stops uploading must not hold the session open forever.

    BlueZ keeps reporting such a link Connected/ServicesResolved, so nothing in
    the transport will ever raise on its own. run() has to end the session
    itself; only then does runtime.collect() reconnect.
    """
    from openpowerstation import ble

    monkeypatch.setattr(ble, "SILENCE_TIMEOUT", 0.01)
    monkeypatch.setattr(ble, "SILENCE_LIMIT", 2)

    disconnected = []

    class Client:
        # **kwargs so the mock keeps matching BleakClient once ble.py passes
        # the BlueZ adapter selection through on Linux.
        def __init__(self, device, disconnected_callback=None, timeout=20, **kwargs):
            self.services = SimpleNamespace(
                get_characteristic=lambda uuid: SimpleNamespace(
                    service_uuid="s", properties=["write"], max_write_without_response_size=20))
        async def connect(self):
            return True
        async def start_notify(self, char, handler):
            return None
        async def disconnect(self):
            disconnected.append(1)
            return True

    monkeypatch.setattr(ble, "BleakClient", Client)

    events = []
    identity = Identity("AA:BB:CC:DD:EE:FF", "MR51123456789012", 7, 0x13)
    s = ble.Session(identity, object(), "123456", lambda *a: None,
                    lambda kind, detail, *a: events.append(kind))

    async def authenticate():
        s.authenticated = True
    monkeypatch.setattr(s, "authenticate", authenticate)

    # The device never delivers a packet: every receive times out.
    async def never(timeout=30):
        raise TimeoutError
    monkeypatch.setattr(s, "_packet", never)

    with pytest.raises(ConnectionError, match="while still connected"):
        await s.run()

    # Silence is reported once, then the link is actually torn down.
    assert events.count("silence") == 1, events
    assert disconnected, "the zombie GATT link was never disconnected"


async def test_resumed_telemetry_resets_the_silence_count(monkeypatch):
    """One late frame must not leave the session one timeout from teardown."""
    from openpowerstation import ble

    monkeypatch.setattr(ble, "SILENCE_TIMEOUT", 0.01)
    monkeypatch.setattr(ble, "SILENCE_LIMIT", 2)

    class Client:
        # **kwargs so the mock keeps matching BleakClient once ble.py passes
        # the BlueZ adapter selection through on Linux.
        def __init__(self, device, disconnected_callback=None, timeout=20, **kwargs):
            self.services = SimpleNamespace(
                get_characteristic=lambda uuid: SimpleNamespace(
                    service_uuid="s", properties=["write"], max_write_without_response_size=20))
        async def connect(self): return True
        async def start_notify(self, char, handler): return None
        async def disconnect(self): return True

    monkeypatch.setattr(ble, "BleakClient", Client)

    events, frames = [], []
    identity = Identity("AA:BB:CC:DD:EE:FF", "MR51123456789012", 7, 0x13)
    s = ble.Session(identity, object(), "123456", lambda *a: frames.append(1),
                    lambda kind, detail, *a: events.append(kind))

    async def authenticate():
        s.authenticated = True
    monkeypatch.setattr(s, "authenticate", authenticate)

    # Silent, one frame, then silent again. Without the reset this would tear
    # down on the very next timeout instead of allowing a fresh pair.
    script = [TimeoutError, "frame", TimeoutError, TimeoutError]

    async def scripted(timeout=30):
        if not script:
            raise ConnectionError("exhausted")
        step = script.pop(0)
        if step is TimeoutError:
            raise TimeoutError
        return Packet(2, 0x21, 0xFE, 0x15, b"", seq=b"\1\0\0\0").to_bytes(), 1, 1
    monkeypatch.setattr(s, "_packet", scripted)

    with pytest.raises(ConnectionError):
        await s.run()

    assert events.count("silence") == 2, events
    assert events.count("telemetry_resumed") == 1, events
    assert not script, "the session ended before the script was exhausted"


async def test_a_silent_round_lasts_the_whole_configured_timeout():
    """_packet must not let the inner receive expire first.

    _next carries its own 20s default. While _packet called it with no
    argument, asking for a 30s silent round really got 20s, so the two rounds
    before teardown were 40s rather than 60 -- inside the bridge's 45s
    freshness window, tearing the session down over gaps Home Assistant still
    considered current.
    """
    from openpowerstation import ble

    identity = Identity("AA:BB:CC:DD:EE:FF", "MR51123456789012", 7, 0x13)
    s = ble.Session(identity, object(), "123456", lambda *a: None, lambda *a: None)

    asked = []
    original = ble.Session._next

    async def record(self, timeout=20):
        asked.append(timeout)
        raise TimeoutError

    ble.Session._next = record
    try:
        with pytest.raises(TimeoutError):
            await s._packet(timeout=ble.SILENCE_TIMEOUT)
    finally:
        ble.Session._next = original
    assert asked == [ble.SILENCE_TIMEOUT], (
        f"the inner receive was given {asked}, not {ble.SILENCE_TIMEOUT}")
