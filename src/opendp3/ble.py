"""Windows-capable BLE connection. No cloud calls, clocks, or auto replies.

The opt-in HV/LV AC output setters are the only DP3 device controls, and the
outbound gate refuses them unless the operator enabled control in Settings.
"""
import asyncio
from collections import deque
import hashlib
import time
import sys

import ecdsa
from bleak import BleakClient, BleakScanner

from .protocol import (AuthenticationError, Identity, OutboundGate, ProtocolError,
                       WireBuffer, auth_packet, control_packet, derive_session_key,
                       identify, parse_packet)
from .radio import bleak_adapter_kwargs
from .vendor.encryption import Type1Encryption, Type7Encryption
from .vendor.frame_assembler import EncPacketAssembler, RawHeaderAssembler, PassthroughAssembler

# How long one receive may block before the stream counts as silent, and how
# many consecutive silent rounds are tolerated before the session is torn down.
#
# A GATT link can stay Connected/ServicesResolved in BlueZ while the DP3 stops
# delivering notifications entirely. Reporting silence and continuing to wait on
# that same connection leaves a zombie: the transport never raises, so the task
# never ends, so runtime.collect() never reaches its reconnect/backoff path. The
# link then sits dead indefinitely while Home Assistant shows the measurements
# going unavailable. Two rounds rather than one, so an ordinary pause between
# uploads does not cost a full rediscovery.
SILENCE_TIMEOUT = 30.0
SILENCE_LIMIT = 2

# Recovery must not be able to hang inside its own cleanup. A BlueZ disconnect
# can block exactly as thoroughly as the read it is abandoning, and an unbounded
# one would hold the reconnect path open for as long as the stall it recovers.
DISCONNECT_TIMEOUT = 5.0


def _exception_detail(backend: str, operation: str, exc: BaseException) -> str:
    """Name a transport failure without recording packets or credentials.

    Only the backend, the operation, and the exception's own type and message
    are kept. Frame bytes, session keys, and the user id never reach an event.
    """
    message = " ".join(str(exc).split())[:500]
    suffix = f": {message}" if message else ""
    return f"backend={backend}; operation={operation}; exception={type(exc).__name__}{suffix}"

# Upstream's DP3 transport choices. A pair must belong to the same service.
PAIRS = [
    ("00000003-0000-1000-8000-00805f9b34fb", "00000002-0000-1000-8000-00805f9b34fb"),
    ("6e400003-b5a3-f393-e0a9-e50e24dcca9e", "6e400002-b5a3-f393-e0a9-e50e24dcca9e"),
]

async def adapter_status() -> dict:
    if sys.platform != 'win32':
        return {'available': None, 'detail': 'Adapter detection delegated to Bleak.'}
    try:
        from winrt.windows.devices.bluetooth import BluetoothAdapter
        adapter = await BluetoothAdapter.get_default_async()
        if adapter is None:
            return {'available': False, 'detail': 'No Windows Bluetooth adapter found.'}
        if not adapter.is_low_energy_supported:
            return {'available': False, 'detail': 'The default Bluetooth adapter does not support BLE.'}
        return {'available': True, 'detail': 'Windows Bluetooth LE adapter detected.'}
    except Exception:
        return {'available': None, 'detail': 'Windows adapter query unavailable; trying a BLE scan.'}

async def scan(timeout=8) -> list[tuple[Identity, object]]:
    status = await adapter_status()
    if status['available'] is False:
        raise ValueError(status['detail'])
    results = await BleakScanner.discover(
        timeout=timeout,
        return_adv=True,
        **bleak_adapter_kwargs(),
    )
    found = []
    for device, adv in results.values():
        identity = identify(device.address, adv.manufacturer_data, adv.rssi)
        if identity:
            found.append((identity, device))
    return found

class Session:
    def __init__(self, identity, device, user_id, on_frame, on_event, allow_control=False):
        self.identity, self.device, self.user_id = identity, device, user_id
        self.on_frame, self.on_event = on_frame, on_event
        self.queue = asyncio.Queue(maxsize=2048)
        self.pending = deque()
        self.gate = OutboundGate(allow_control)
        # Control is written from the service's command task while the session's
        # own task is reading. One writer at a time on the characteristic.
        self.write_lock = asyncio.Lock()
        self.disconnected = asyncio.Event()
        self.overflow = False
        self.client = None
        self.authenticated = False
        self.codec = None
        self.wire = None
        self.last_rx = None
        # Set once this session has already recorded why it is ending, so run()
        # does not file a second, less specific event for the same failure.
        self.reported_failure = False

    def receive(self, _, data):
        stamp = (time.time_ns(), time.monotonic_ns())
        try:
            self.queue.put_nowait((bytes(data), *stamp))
        except asyncio.QueueFull:
            self.overflow = True
            self.disconnected.set()

    async def _next(self, timeout=20):
        if self.overflow:
            raise ProtocolError("BLE receive queue overflow; capture is incomplete.")
        # Short timeout also makes a dropped connection fail promptly.
        async with asyncio.timeout(timeout):
            while True:
                if self.disconnected.is_set():
                    raise ConnectionError("Bluetooth disconnected.")
                try:
                    return await asyncio.wait_for(self.queue.get(), 0.25)
                except TimeoutError:
                    continue

    async def _write(self, *, command=None, packet=None):
        """The only GATT write site. Both representations pass the policy gate."""
        if command is not None:
            raw = self.gate.command(command)
        elif packet is not None:
            self.gate.check(packet)
            raw = await self.codec.encode(packet)
        else:
            raise ProtocolError("No permitted outbound message.")
        response = "write" in self.write_char.properties
        if not response and "write-without-response" not in self.write_char.properties:
            raise ProtocolError("Unsupported BLE write characteristic.")
        # Match GATT's transfer limit; the device reassembles protocol fragments.
        chunk = 512 if response else self.write_char.max_write_without_response_size
        async with self.write_lock:
            for offset in range(0, len(raw), chunk):
                await self.client.write_gatt_char(self.write_char, raw[offset:offset + chunk], response=response)

    async def send_control(self, field: str, value: bool):
        """Send one allowlisted ConfigWrite. The gate re-checks before the wire."""
        if not self.authenticated or self.codec is None:
            raise ProtocolError("No authenticated session for control.")
        await self._write(packet=control_packet(field, value))

    async def _simple_response(self, buffer):
        async with asyncio.timeout(20):
            while True:
                raw, _, _ = await self._next()
                frames = await buffer.feed(raw)
                if frames:
                    if len(frames) != 1:
                        raise AuthenticationError("Unexpected handshake response sequence.")
                    return frames[0]

    async def _packet(self, timeout=20):
        if self.pending:
            return self.pending.popleft()
        async with asyncio.timeout(timeout):
            while True:
                # The inner receive must not expire first, or the caller's
                # timeout is silently shortened to _next's own default: a
                # 30-second silent round became 20, and the two rounds before
                # teardown became 40 seconds rather than 60 -- inside the
                # bridge's 45-second freshness window, so the session was torn
                # down over gaps Home Assistant still considered current.
                raw, utc, mono = await self._next(timeout)
                old_discarded = self.wire.discarded
                frames = await self.wire.feed(raw)
                if self.authenticated and self.wire.discarded != old_discarded:
                    self.on_event("corrupt_transport", "Discarded corrupt transport bytes.", utc, mono)
                for frame in frames:
                    self.pending.append((frame, utc, mono))
                if self.pending:
                    return self.pending.popleft()

    async def authenticate(self):
        kind = self.identity.encryption
        if kind not in (0, 1, 7) or (self.identity.advertised_version & 15) != 3:
            raise AuthenticationError("Unsupported advertised DP3 protocol; no fallback attempted.")
        encryption = None
        if kind == 7:
            simple = WireBuffer(7, simple=True)
            self.gate.stage = "ecdh"
            private = ecdsa.SigningKey.generate(curve=ecdsa.SECP160r1)
            await self._write(command=b"\x01\x00" + private.get_verifying_key().to_string())
            response = await self._simple_response(simple)
            if len(response) < 43 or response[0] != 1 or response[1] != 0 or response[2] not in (0, 5):
                raise AuthenticationError("Unsupported ECDH response.")
            public = ecdsa.VerifyingKey.from_string(response[3:43], curve=ecdsa.SECP160r1)
            shared = ecdsa.ECDH(ecdsa.SECP160r1, private, public).generate_sharedsecret_bytes()
            encryption = Type7Encryption(shared[:16], hashlib.md5(shared).digest())
            self.gate.stage = "key"
            await self._write(command=b"\x02")
            response = await self._simple_response(simple)
            if not response or response[0] != 2:
                raise AuthenticationError("Invalid key response.")
            data = await encryption.decrypt(response[1:])
            if len(data) != 18:
                raise AuthenticationError("Unsupported session key format.")
            encryption = Type7Encryption(derive_session_key(data[16:18], data[:16]), encryption.iv)
        elif kind == 1:
            serial = self.identity.serial
            encryption = Type1Encryption(hashlib.md5(serial.encode()).digest(),
                                         hashlib.md5(serial[::-1].encode()).digest())
        self.codec = {0: PassthroughAssembler, 1: RawHeaderAssembler, 7: EncPacketAssembler}[kind](encryption)
        self.wire = WireBuffer(kind, encryption)
        self.gate.stage = "status"
        await self._write(packet=auth_packet(0x89))
        async with asyncio.timeout(20):
            while True:
                raw, _, _ = await self._packet()
                p = parse_packet(raw)
                if (p.src, p.cmd_set, p.cmd_id) == (0x35, 0x35, 0x89):
                    break
        self.gate.stage = "login"
        digest = hashlib.md5((self.user_id + self.identity.serial).encode("ascii")).hexdigest().upper().encode()
        await self._write(packet=auth_packet(0x86, digest))
        async with asyncio.timeout(20):
            while True:
                raw, utc, mono = await self._packet()
                p = parse_packet(raw)
                if (p.src, p.cmd_set, p.cmd_id) == (0x35, 0x35, 0x86):
                    if p.payload != b"\x00":
                        raise AuthenticationError("Device rejected authentication. Verify account binding and user ID.")
                    break
                # Upstream accepts first telemetry as auth completion; narrow to DP3 upload.
                if (p.src, p.cmd_set, p.cmd_id) == (2, 0xFE, 0x15):
                    self.pending.appendleft((raw, utc, mono))
                    break
        self.authenticated = True
        self.gate.stage = "closed"  # No outbound traffic after authentication.

    async def _collect_authenticated(self):
        """Read frames until the device goes quiet or the transport gives up."""
        silence_reported = False
        silent_rounds = 0
        while True:
            try:
                raw, utc, mono = await self._packet(timeout=SILENCE_TIMEOUT)
            except TimeoutError as exc:
                silent_rounds += 1
                if not silence_reported:
                    self.on_event("silence",
                                  f"No telemetry for {SILENCE_TIMEOUT:g} seconds; device state is unknown.",
                                  time.time_ns(), time.monotonic_ns())
                    silence_reported = True
                if silent_rounds >= SILENCE_LIMIT:
                    # End the session deliberately. BlueZ still calls this link
                    # connected, so nothing else will ever end it; the caller's
                    # reconnect path is the only thing that can recover a DP3
                    # that has stopped uploading. ConnectionError is an OSError,
                    # which runtime.collect() already treats as a lost transport.
                    #
                    # Two causes look identical from Home Assistant: a device
                    # that simply went quiet, and a BlueZ/GATT operation that
                    # failed. Only the second is evidence against the radio, so
                    # say which one this was rather than leaving it to be
                    # inferred from a reconnect. Nothing raised here, hence
                    # transport_error=none_observed.
                    self.reported_failure = True
                    self.on_event(
                        "session_timeout",
                        f"{_exception_detail('Bleak', 'notification_wait', exc)}; "
                        f"consecutive_silences={silent_rounds}; transport_error=none_observed; "
                        "closing the stalled GATT session for reconnect.",
                        time.time_ns(), time.monotonic_ns())
                    raise ConnectionError(
                        f"No telemetry for {SILENCE_TIMEOUT * silent_rounds:g} seconds while still "
                        "connected; dropping the session to force reacquisition.") from exc
                continue
            silent_rounds = 0
            if silence_reported:
                self.on_event("telemetry_resumed", "Telemetry resumed after a gap.", utc, mono)
                silence_reported = False
            # Auth packets, including late replies, never enter the recorder.
            # Invalid post-auth frames are kept locally for diagnostic replay.
            try:
                p = parse_packet(raw)
            except ProtocolError:
                self.on_frame(raw, utc, mono)
                continue
            if p.cmd_set == 0x35:
                continue
            self.on_frame(raw, utc, mono)

    async def run(self):
        self.reported_failure = False
        self.client = BleakClient(
            self.device,
            disconnected_callback=lambda _: self.disconnected.set(),
            timeout=20,
            **bleak_adapter_kwargs(),
        )
        try:
            await self.client.connect()
            for notify, write in PAIRS:
                nc = self.client.services.get_characteristic(notify)
                wc = self.client.services.get_characteristic(write)
                if nc and wc and nc.service_uuid == wc.service_uuid:
                    self.write_char, self.notify_char = wc, nc
                    break
            else:
                raise ProtocolError("Unsupported DP3 GATT services.")
            await self.client.start_notify(self.notify_char, self.receive)
            await self.authenticate()
            self.on_event("connected", "Authenticated local Bluetooth session.", time.time_ns(), time.monotonic_ns())
            await self._collect_authenticated()
        except Exception as exc:
            # Whatever ended the session, record what raised it before the
            # reconnect path swallows the distinction. _collect_authenticated
            # already said why it gave up on a quiet device; only an unexplained
            # failure needs a second record. CancelledError is a BaseException
            # and is deliberately not caught here: a cancelled session is the
            # supervisor acting, not a fault to attribute to the transport.
            if not self.reported_failure:
                self.on_event("session_error", _exception_detail("Bleak", "session", exc),
                              time.time_ns(), time.monotonic_ns())
            raise
        finally:
            self.authenticated = False
            self.gate.stage = "closed"
            if self.client:
                try:
                    await asyncio.wait_for(self.client.disconnect(), DISCONNECT_TIMEOUT)
                except Exception as exc:
                    # A disconnect that fails or times out used to vanish. It is
                    # the strongest single signal that the adapter, not the
                    # device, is the problem, so it is worth one event.
                    self.on_event("disconnect_error",
                                  _exception_detail("Bleak", "disconnect", exc),
                                  time.time_ns(), time.monotonic_ns())
            self.pending.clear()
            self.wire = self.codec = None
            self.user_id = ""
            while not self.queue.empty():
                self.queue.get_nowait()
