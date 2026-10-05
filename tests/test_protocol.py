import hashlib
import struct
from types import SimpleNamespace

import pytest

from openpowerstation.ecdh import EphemeralKey

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

async def type7_wires(packet, count, enc):
    codec = EncPacketAssembler(enc)
    raws = [packet(seq=n, bms_max_cell_temp=20 + n) for n in range(1, count + 1)]
    return raws, [await codec.encode(parse_packet(raw)) for raw in raws]

async def test_a_frame_cut_short_costs_only_itself(packet):
    """A notification lost mid-frame must not take the next frame with it.

    The checksum failure discarded the whole span the cut frame claimed, which
    held the start of the next one; the vendored reassembler WireBuffer
    replaced moves on and keeps it. Every cut point, delivered in one
    notification and a byte at a time.
    """
    enc = Type7Encryption(b"x"*16, b"y"*16)
    raws, wires = await type7_wires(packet, 3, enc)
    for cut in range(1, len(wires[0])):
        stream = wires[0][:cut] + wires[1] + wires[2]
        whole = WireBuffer(7, enc)
        assert await whole.feed(stream) == raws[1:], f"cut at {cut}"
        assert whole.discarded == cut, f"cut at {cut}"
        trickle, got = WireBuffer(7, enc), []
        for byte in stream:
            got.extend(await trickle.feed(bytes([byte])))
        assert got == raws[1:], f"cut at {cut}, a byte at a time"

async def test_a_length_that_never_arrives_holds_back_nothing(packet):
    """A header claiming more than follows must not stall the frames behind it.

    WireBuffer waited for the whole claimed span, up to 10,000 bytes or tens of
    seconds of DP3 telemetry, and then discarded every frame inside it. A
    complete frame with a valid checksum inside the span shows the header was
    not one, so each frame is delivered as it completes.
    """
    enc = Type7Encryption(b"x"*16, b"y"*16)
    raws, wires = await type7_wires(packet, 30, enc)
    stray = b"\x5a\x5a\x10\x01" + (4000).to_bytes(2, "little")
    buf = WireBuffer(7, enc)
    got = [await buf.feed((stray if n == 0 else b"") + wire) for n, wire in enumerate(wires)]
    assert got == [[raw] for raw in raws]
    assert buf.discarded == len(stray)

async def test_type7_resync_keeps_every_frame_the_vendored_reassembler_keeps(packet):
    """Damaged streams, fed to both in the same notification-sized pieces.

    Frames lose their tail or a stretch of their middle, or carry a flipped
    bit, with stray bytes and plausible headers between them. WireBuffer has to
    deliver every intact frame the vendored reassembler delivers, in order.
    """
    import random
    enc = Type7Encryption(b"x"*16, b"y"*16)
    codec = EncPacketAssembler(enc)
    for seed in range(200):
        rng = random.Random(seed)
        stream, intact = b"", []
        for seq in range(1, rng.randrange(3, 10)):
            raw = packet(seq=seq, bms_max_cell_temp=rng.randrange(10, 40),
                         pow_in_sum_w=float(rng.randrange(3000)))
            wire = await codec.encode(parse_packet(raw))
            roll = rng.random()
            if roll < 0.15:
                wire = wire[:rng.randrange(1, len(wire))]
            elif roll < 0.25:
                cut = rng.randrange(len(wire))
                wire = wire[:cut] + wire[cut + rng.randrange(1, 40):]
            elif roll < 0.35:
                damaged = bytearray(wire)
                damaged[rng.randrange(len(damaged))] ^= 1 << rng.randrange(8)
                wire = bytes(damaged)
            else:
                intact.append(raw)
            junk = rng.random()
            if junk < 0.1:
                stream += b"\x5a"
            elif junk < 0.2:
                stream += b"\x5a\x5a\x10\x01" + rng.randrange(2, 10_001).to_bytes(2, "little")
            stream += wire
        ours, theirs = WireBuffer(7, enc), EncPacketAssembler(enc)
        got_ours, got_theirs, at = [], [], 0
        while at < len(stream):
            size = rng.choice([1, 20, 182, 244, 512])
            got_ours += await ours.feed(stream[at:at + size])
            got_theirs += await theirs.reassemble(stream[at:at + size])
            at += size
        kept = [raw for raw in got_ours if raw in intact]
        assert kept == [raw for raw in intact if raw in kept], f"seed {seed}: out of order"
        lost = [raw for raw in got_theirs if raw in intact and raw not in kept]
        assert not lost, f"seed {seed}: {len(lost)} frame(s) the vendored reassembler kept"

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
async def test_full_type7_auth_with_mock_device(packet, seed, request):
    """Both ends compute ECDH; auth notifications are split and no real BLE is used."""
    from openpowerstation.ble import Session
    identity = Identity("AA:BB:CC:DD:EE:FF","MR51123456789012",7,0x13)
    s = Session(identity,object(),"123456",lambda *a:None,lambda *a:None)
    encryption = None
    commands = []
    srand = b"r"*16
    private = EphemeralKey()
    request.addfinalizer(private.close)
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
                    shared = private.exchange(payload[2:])
                    encryption = Type7Encryption(shared[:16],hashlib.md5(shared).digest())
                    respond(SimplePacketAssembler.encode(b"\1\0\0"+private.public_bytes()))
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
