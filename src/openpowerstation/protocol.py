"""Read-only DP3 transport policy and framing; adapted from pinned ha-ef-ble."""
import hashlib
from dataclasses import dataclass

from .vendor.crc import crc8, crc16
from .vendor.packet import Packet
from .vendor.frame_assembler import SimplePacketAssembler
from .vendor import keydata

class ProtocolError(Exception):
    pass

class PolicyError(ProtocolError):
    pass

class AuthenticationError(ProtocolError):
    pass

class CredentialsRejected(AuthenticationError):
    """The DP3 refused this account's login: re-bound, reset, or a wrong user ID.

    Every other AuthenticationError is a handshake this code does not
    understand, which needs a decoder change rather than new credentials.
    """

@dataclass(frozen=True)
class Identity:
    address: str
    serial: str
    encryption: int
    advertised_version: int
    rssi: int | None = None

def identify(address: str, manufacturer_data: dict, rssi=None) -> Identity | None:
    data = manufacturer_data.get(0xB5B5, b"")
    if len(data) < 17:
        return None
    try:
        serial = data[1:17].decode("ascii")
    except UnicodeDecodeError:
        return None
    if not serial.startswith(("MR51", "MR54")):
        return None
    # Short advertisements have no flags; upstream defaults to encryption type 7.
    encryption = ((data[22] >> 3) & 7) if len(data) >= 23 else 7
    return Identity(address.upper(), serial, encryption, data[0], rssi)

AUTH_HEADER = (0x21, 0x35, 0x35, 1, 1, 3, b"\0" * 4, 0)
# ConfigWrite routing, from ha-ef-ble's DELTA Pro 3 control path at the pinned
# revision in THIRD_PARTY_NOTICES.md. Version 0x13 is v3 plus the sentinel bit,
# which parse_packet already accepts inbound.
CONTROL_HEADER = (0x20, 0x02, 0xFE, 1, 1, 0x13, b"\0" * 4, 0)
CONTROL_CMD_ID = 0x11

# The only settable fields this application will ever emit. ConfigWrite also
# carries cfg_power_off, reset_factory_setting, cfg_bms_power_off and the
# installment-payment lockout; allowing the message wholesale would put those
# one malformed request away from the radio. The gate re-parses what is about to
# be written rather than trusting the caller that built it.
CONTROL_FIELDS = {"cfg_hv_ac_out_open", "cfg_lv_ac_out_open"}
# What each control's output really reports. The DP3 publishes these bitmasks in
# DisplayPropertyUpload, and the pinned upstream implementation uses their low
# two bits for the real outlet state: 0 is off; 2 and 3 are on. Value 1 is left
# unknown rather than guessed. The bridge's switches show it (bridge.py
# control_state_template), and the recorder makes each change of it a
# state_change event, since the DP3 acknowledges no command.
CONTROL_FEEDBACK_FIELDS = {
    "cfg_hv_ac_out_open": "flow_info_ac_hv_out",
    "cfg_lv_ac_out_open": "flow_info_ac_lv_out",
}
OUTPUT_STATES = {0: "off", 1: "unknown", 2: "on", 3: "on"}


def control_packet(field: str, value: bool) -> Packet:
    """Build the one kind of device command this application supports."""
    from .vendor.pb import mr521_pb2
    if field not in CONTROL_FIELDS:
        raise PolicyError("Unsupported control field.")
    if type(value) is not bool:
        raise PolicyError("Control values must be boolean.")
    message = mr521_pb2.ConfigWrite(**{field: value})
    src, dst, cmd_set, dsrc, ddst, version, seq, product_id = CONTROL_HEADER
    return Packet(src, dst, cmd_set, CONTROL_CMD_ID, message.SerializeToString(),
                  dsrc, ddst, version, seq, product_id)


class OutboundGate:
    """Two allowlists: the authentication exchange, and opt-in device control."""
    def __init__(self, allow_control: bool = False):
        self.stage = "new"
        self.allow_control = bool(allow_control)

    def command(self, payload: bytes) -> bytes:
        if self.stage == "ecdh" and len(payload) == 42 and payload[:2] == b"\x01\x00":
            return SimplePacketAssembler.encode(payload)
        if self.stage == "key" and payload == b"\x02":
            return SimplePacketAssembler.encode(payload)
        raise PolicyError("Unapproved handshake command blocked.")

    def check_control(self, p: Packet):
        """Control is refused unless enabled, authenticated, and field-allowlisted."""
        from google.protobuf.message import DecodeError
        from .vendor.pb import mr521_pb2
        if not self.allow_control:
            raise PolicyError("Device control is turned off.")
        # Only after the handshake completes; never during or before it.
        if self.stage != "closed":
            raise PolicyError("Device control requires an authenticated session.")
        if p.cmd_id != CONTROL_CMD_ID:
            raise PolicyError("Device writes are disabled.")
        message = mr521_pb2.ConfigWrite()
        try:
            message.ParseFromString(p.payload)
        except DecodeError:
            raise PolicyError("Unreadable control payload blocked.") from None
        present = {descriptor.name: value for descriptor, value in message.ListFields()}
        if not present or not set(present) <= CONTROL_FIELDS:
            raise PolicyError("Unapproved control field blocked.")
        # Rebuild from the allowlisted fields alone and require the bytes to match.
        # Comparing against a re-serialization of the parsed message would not be
        # enough: protobuf preserves unknown fields and emits them again, so a
        # field this schema does not know -- but newer firmware might -- would
        # survive a round trip unexamined. Anything the allowlist cannot
        # reproduce exactly is refused.
        if mr521_pb2.ConfigWrite(**present).SerializeToString() != p.payload:
            raise PolicyError("Unexpected control payload blocked.")

    def check(self, p: Packet):
        header = (p.src, p.dst, p.cmd_set, p.dsrc, p.ddst, p.version, p.seq, p.product_id)
        if header == CONTROL_HEADER:
            return self.check_control(p)
        if header != AUTH_HEADER:
            raise PolicyError("Device writes are disabled.")
        if self.stage == "status" and p.cmd_id == 0x89 and p.payload == b"":
            return
        if self.stage == "login" and p.cmd_id == 0x86 and len(p.payload) == 32:
            if all(c in b"0123456789ABCDEF" for c in p.payload):
                return
        raise PolicyError("Device writes are disabled.")

def derive_session_key(seed: bytes, srand: bytes) -> bytes:
    if len(seed) != 2 or len(srand) != 16:
        raise AuthenticationError("Unsupported session key response.")
    pos = seed[0] * 16 + ((seed[1] - 1) & 255) * 256
    first, second = keydata.get8bytes(pos), keydata.get8bytes(pos + 8)
    # Python slices silently truncate outside the table. Never derive a key
    # from empty/partial material supplied by an unsupported peer response.
    if len(first) != 8 or len(second) != 8:
        raise AuthenticationError("Unsupported session key response.")
    return hashlib.md5(first + second + srand).digest()

def parse_packet(raw: bytes) -> Packet:
    """Validate transport bounds before calling the vendored packet decoder."""
    if len(raw) < 20 or raw[0] != 0xAA or raw[1] not in (3, 0x13):
        raise ProtocolError("Unsupported or short DP3 packet.")
    if crc8(raw[:4]) != raw[4]:
        raise ProtocolError("Packet header CRC mismatch.")
    length = int.from_bytes(raw[2:4], "little")
    if length > 10_000:
        raise ProtocolError("Packet length exceeds limit.")
    if raw[1] == 3:
        if len(raw) != length + 20 or crc16(raw[:-2]) != int.from_bytes(raw[-2:], "little"):
            raise ProtocolError("Packet length or CRC mismatch.")
    else:
        if len(raw) not in (length + 18, length + 20):
            raise ProtocolError("Sentinel packet length mismatch.")
        payload = raw[18:18 + length]
        decoded = bytes(v ^ raw[6] for v in payload)
        if not decoded.endswith(b"\xbb\xbb"):
            if len(raw) != length + 20 or crc16(raw[:-2]) != int.from_bytes(raw[-2:], "little"):
                raise ProtocolError("Sentinel or packet CRC missing.")
    p = Packet.from_bytes(raw, xor_payload=raw[1] == 0x13)
    # The vendored decoder can also return PacketV4; reject that type first.
    # InvalidPacket intentionally subclasses Packet, so it still needs its own check.
    if not isinstance(p, Packet) or Packet.is_invalid(p):
        raise ProtocolError("Unsupported DP3 frame.")
    return p

TYPE7_PREFIX = b"\x5a\x5a"


def _type7_size(data: bytes, at: int) -> int | None:
    """The span an EncPacket header at ``at`` claims, or None if it is no header."""
    if len(data) - at < 8:
        return None
    n = int.from_bytes(data[at + 4:at + 6], "little")
    return n + 6 if 2 <= n <= 10_000 else None


def _type7_frame_at(data: bytes, at: int) -> bool:
    """True when a whole EncPacket with a valid checksum starts at ``at``."""
    size = _type7_size(data, at)
    return (size is not None and at + size <= len(data) and
            crc16(data[at:at + size - 2]) == int.from_bytes(data[at + size - 2:at + size], "little"))


class WireBuffer:
    """Bounded, ordered framing. Embedded prefixes never truncate valid fragments.

    Type 7 resynchronises so that it never loses a frame the vendored
    EncPacketAssembler.reassemble would deliver. A candidate whose checksum
    fails is a false prefix or a frame cut short, so the scan moves on one byte
    rather than past the span it claimed, which held the start of the next
    frame. A candidate still waiting for its tail is given up only when a
    complete frame with a valid checksum starts inside it: a header with a false
    length then costs nothing behind it, and a real frame still arriving is
    never abandoned for a prefix that happens to occur in its encrypted bytes.
    """
    def __init__(self, encryption_type: int, encryption=None, simple=False):
        self.kind = encryption_type
        self.encryption = encryption
        self.simple = simple
        self.buffer = b""
        self.discarded = 0

    def _type7_resync(self) -> bool:
        """Drop the waiting candidate when a complete frame starts inside it."""
        at = self.buffer.find(TYPE7_PREFIX, 1)
        while at > 0:
            if _type7_frame_at(self.buffer, at):
                self.discarded += at
                self.buffer = self.buffer[at:]
                return True
            at = self.buffer.find(TYPE7_PREFIX, at + 1)
        return False

    async def feed(self, data: bytes) -> list[bytes]:
        self.buffer += data
        if len(self.buffer) > 65_536:
            self.buffer = b""
            raise ProtocolError("Receive buffer exceeded limit.")
        result = []
        prefix = TYPE7_PREFIX if self.kind == 7 else b"\xaa"
        while self.buffer:
            start = self.buffer.find(prefix)
            if start < 0:
                keep = 1 if self.buffer.endswith(prefix[:1]) and len(prefix) == 2 else 0
                self.discarded += len(self.buffer) - keep
                self.buffer = self.buffer[-keep:] if keep else b""
                break
            if start:
                self.discarded += start
                self.buffer = self.buffer[start:]
            if len(self.buffer) < (8 if self.kind == 7 else 5):
                break
            if self.kind == 7:
                size = _type7_size(self.buffer, 0)
                if size is None:
                    self.discarded += 1
                    self.buffer = self.buffer[1:]
                    continue
                if len(self.buffer) < size:
                    if self._type7_resync():
                        continue
                    break
                frame = self.buffer[:size]
                if crc16(frame[:-2]) != int.from_bytes(frame[-2:], "little"):
                    self.discarded += 1
                    self.buffer = self.buffer[1:]
                    continue
                self.buffer = self.buffer[size:]
                body = frame[6:-2]
                result.append(body if self.simple else await self.encryption.decrypt(body))
                continue
            if crc8(self.buffer[:4]) != self.buffer[4] or self.buffer[1] not in (3, 0x13):
                self.discarded += 1
                self.buffer = self.buffer[1:]
                continue
            n = int.from_bytes(self.buffer[2:4], "little")
            if n > 10_000:
                raise ProtocolError("Unsupported frame length.")
            inner = n + 15
            size = 5 + (((inner + 15) // 16) * 16 if self.kind == 1 else inner)
            if len(self.buffer) < size:
                break
            frame, self.buffer = self.buffer[:size], self.buffer[size:]
            if self.kind == 1:
                result.append(frame[:5] + (await self.encryption.decrypt(frame[5:]))[:inner])
            else:
                result.append(frame)
        return result

def auth_packet(cmd_id: int, payload=b"") -> Packet:
    return Packet(0x21, 0x35, 0x35, cmd_id, payload, version=3)
