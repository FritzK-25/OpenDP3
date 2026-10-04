"""BLE discovery, status and guarded control helpers for Jackery stations.

This is deliberately separate from the EcoFlow DP3 transport.  Jackery uses a
different GATT layout and advertises enough material to derive the session key,
but discovery is the safe first step: it does not connect or write anything.
"""
from dataclasses import dataclass, replace
import contextlib
import asyncio
import base64
from datetime import datetime
import json
import random
import sys
import time
from bleak import BleakClient
from bleak import BleakScanner
from .radio import bleak_adapter_kwargs

JACKERY_NAME_PREFIXES = ("HT", "JACKERY", "JK", "EXPLORER")
SERVICE_DATA_UUID = "0000bdee-0000-1000-8000-00805f9b34fb"
SALT_RC4 = b"LYx*G!6u9#"
SALT_KEY = b"6*SY1c5B9@"
DATA_WRITE_UUID = "0000ee01-0000-1000-8000-00805f9b34fb"
DATA_NOTIFY_UUID = "0000ee02-0000-1000-8000-00805f9b34fb"
GATT_OPERATION_TIMEOUT = 8.0
GATT_CLOSE_TIMEOUT = 5.0
# Bleak's own connect deadline, stated rather than inherited so the shared
# adapter lease, which is held across the connect, can be sized against it
# (radio.RADIO_HOLD_LIMIT). It is Bleak 3's default.
CONNECT_TIMEOUT = 30.0


def _operation_failure(backend: str, operation: str, exc: BaseException) -> str:
    """Keep BlueZ/WinRT diagnostics while excluding packets and key material."""
    message = " ".join(str(exc).split())[:500]
    suffix = f": {message}" if message else ""
    return f"backend={backend}; operation={operation}; exception={type(exc).__name__}{suffix}"


async def _bounded_operation(awaitable, *, backend: str, operation: str, timeout: float | None):
    """Run one backend call so any failure arrives as TimeoutError or ConnectionError.

    Bleak raises its own exception family (BleakDBusError and the rest), which
    is neither of those, so an unwrapped call ended the collector process
    instead of reaching its reattach path. ``timeout=None`` adds no deadline of
    ours: the backend's own -- Bleak's 30-second connect, say -- still governs,
    and only the classification applies.
    """
    try:
        return await asyncio.wait_for(awaitable, timeout)
    except asyncio.CancelledError:
        raise
    except asyncio.TimeoutError as exc:
        limit = "" if timeout is None else f"; timeout={timeout:g}s"
        raise TimeoutError(f"{_operation_failure(backend, operation, exc)}{limit}") from exc
    except Exception as exc:
        raise ConnectionError(_operation_failure(backend, operation, exc)) from exc


class StationSilent(TimeoutError):
    """The Explorer left a status query unanswered on a link that raised nothing.

    Distinct from a write that failed or ran out its deadline: that is the link
    failing, while this is the station staying quiet for one poll, which is not
    by itself a reason to give up a session that took an advertising window to
    open.
    """


class StationNotFound(TimeoutError):
    """No advertisement from the wanted Explorer arrived before the deadline."""

# These are the only Jackery controls exposed by OpenPowerstation.  The portable
# protocol has many more action IDs, including Wi-Fi and battery-boundary
# writes; keeping this map deliberately small prevents a broker message from
# becoming an arbitrary encrypted command. The write site checks every command
# against it again (check_outbound), so a new caller cannot go around it.
#
# Screen timeout is unusual: the command writes minutes to `slt`, while status
# reports a preset enum in `sltb` (1=no timeout, 2=2 minutes, 3=2 hours).  Keep
# the readback enum in `values` so the recorder's existing verification remains
# fail-closed, and translate to the command minutes only while building a frame.
JACKERY_CONTROLS = {
    "jackery_ac_output": {"wire": "oac", "action": 0x04, "kind": "switch"},
    "jackery_dc_output": {"wire": "odc", "action": 0x01, "kind": "switch"},
    "jackery_battery_save": {"wire": "lps", "action": 0x0B, "kind": "select",
                              "values": {"full": 0, "save": 1}},
    "jackery_screen_timeout": {
        "wire": "sltb", "command_wire": "slt", "action": 0x08, "kind": "select",
        "values": {"always_on": 1, "2m": 2, "2h": 3},
        "command_values": {"always_on": 0, "2m": 2, "2h": 120},
    },
    # Explorer 1000 v2 documentation explicitly exposes Quiet Charging.
    # Protocol research maps standard to cs=0 and quiet/silent to cs=1.
    # Deliberately omit cs=2 (custom): Jackery does not document custom
    # AC charging speed for this model.
    "jackery_charging_mode": {
        "wire": "cs", "action": 0x0A, "kind": "select",
        "values": {"standard": 0, "quiet": 1},
    },
    # Jackery documents Auto Power-Off as Off/2h/8h/12h/24h. The
    # reverse-engineered portable command writes those minute values to pm.
    "jackery_auto_power_off": {
        "wire": "pm", "action": 0x0C, "kind": "select",
        "values": {"off": 0, "2h": 120, "8h": 480, "12h": 720, "24h": 1440},
    },
}


def jackery_control_value(control: str, value):
    """Validate and normalize one user-facing Jackery control value for readback."""
    spec = JACKERY_CONTROLS.get(control)
    if spec is None:
        raise ValueError("Unsupported Jackery control.")
    if spec["kind"] == "switch":
        if type(value) is not bool:
            raise ValueError("Jackery switch controls require a boolean value.")
        return 1 if value else 0
    if not isinstance(value, str) or value not in spec["values"]:
        raise ValueError("Unsupported Jackery select option.")
    return spec["values"][value]


def jackery_control_readback_matches(control: str, value, raw) -> bool:
    """Return whether raw status exactly confirms the requested control value."""
    spec = JACKERY_CONTROLS.get(control)
    if spec is None:
        return False
    try:
        expected = jackery_control_value(control, value)
    except ValueError:
        return False
    # Selects are discrete protocol enums. Python considers True == 1 and
    # 1.0 == 1, so require an actual integer before accepting readback.
    if spec["kind"] == "select":
        return type(raw) is int and raw == expected
    return raw == expected


def jackery_control_command(control: str, value) -> str:
    """Build the plaintext command for a validated portable control."""
    spec = JACKERY_CONTROLS.get(control)
    if spec is None:
        raise ValueError("Unsupported Jackery control.")
    normalized = jackery_control_value(control, value)
    command_value = spec.get("command_values", {}).get(value, normalized)
    command_wire = spec.get("command_wire", spec["wire"])
    body = json.dumps({command_wire: command_value}, separators=(",", ":"))
    return _command(spec["action"], 0x04, body)


@dataclass(frozen=True)
class Identity:
    address: str
    name: str
    serial: str | None
    model_code: int | None
    battery_level: int | None
    rssi: int | None
    encryption_key: str | None = None
    manufacturer_data: dict | None = None
    service_data: dict | None = None
    device: object | None = None
    address_type: str | None = None
    device_id: str | None = None


def rc4(data: bytes, key: bytes) -> bytes:
    s = list(range(256))
    j = 0
    for i in range(256):
        j = (j + s[i] + key[i % len(key)]) & 255
        s[i], s[j] = s[j], s[i]
    i = j = 0
    out = bytearray()
    for value in data:
        i = (i + 1) & 255
        j = (j + s[i]) & 255
        s[i], s[j] = s[j], s[i]
        out.append(value ^ s[(s[i] + s[j]) & 255])
    return bytes(out)


def _manufacturer_serial(manufacturer_id: int, data: bytes) -> str:
    # Jackery splits the 15-character serial between the little-endian company
    # identifier and the manufacturer payload.
    raw = f"{manufacturer_id:04x}"
    prefix = bytes.fromhex(raw[:2]).decode("ascii")
    return prefix + data.decode("ascii")


def _jackery_crc(data: bytes) -> str:
    """Jackery's CRC-16/ARC variant, returned in little-endian hex order."""
    value = 0xFFFF
    for byte in data:
        value ^= byte
        for _ in range(8):
            value = (value >> 1) ^ 0xA001 if value & 1 else value >> 1
    return f"{value & 0xFF:02X}{value >> 8:02X}"


def _jackery_crc_hex(hex_data: str) -> str:
    return _jackery_crc(bytes.fromhex(hex_data))


def _rc4_frame(payload_hex: str, key: bytes) -> bytes:
    """Encode a portable RC4 command, including Jackery integrity fields."""
    security = random.randint(1, 255)
    xor_hex = bytes(value ^ security for value in bytes.fromhex(payload_hex)).hex()
    plain_hex = xor_hex + f"{security:02x}"
    return rc4(bytes.fromhex(plain_hex + _jackery_crc_hex(plain_hex)), key)


def _decrypt_rc4_frame(data: bytes, key: bytes) -> str | None:
    plain = rc4(data, key)
    if len(plain) < 8:
        return None
    body, expected = plain[:-2], plain[-2:].hex().upper()
    if _jackery_crc(body) != expected:
        return None
    security = body[-1]
    decoded = bytes(value ^ security for value in body[:-1]).hex().upper()
    if not decoded.startswith("DFEC"):
        return None
    return decoded[4:]


def _session_keys(encryption_key: str) -> list[bytes]:
    """Return the RC4 session keys to try against this station, likeliest first.

    Jackery's RC4 keys are not block-cipher keys and are not cut to a block
    size.  ``parse_advertisement`` above proves it on this hardware: it decrypts
    the advertisement with an 18-byte key and the station's own CRC validates
    the result.  The session key is therefore the whole 22 bytes of derived
    material, which is also what the community Private Jack client sends.
    Truncating it to 16 bytes yields an unrelated keystream, so the station
    fails the frame's CRC, drops it, and replies to nothing -- precisely the
    silence this project recorded.  The truncated key is kept as a fallback
    because this station advertises only in short windows, so one connection
    should be able to rule it out rather than costing another acquisition.
    """
    material = base64.b64decode(encryption_key)
    return [material, material[:16]] if len(material) > 16 else [material]


class _ResponseAssembler:
    """Turn encrypted Jackery notifications into complete JSON objects.

    Device-property replies are normally larger than one BLE notification.
    Those replies use an ``80`` envelope followed by a one-based packet number,
    a packet count, and a fragment of the JSON body.  Treating each fragment as
    a complete command (the old behavior) silently discarded every useful
    status response from the Explorer 1000 v2.
    """

    def __init__(self, keys: bytes | list[bytes]):
        # A single key is the ordinary case; a list asks the assembler to work
        # out which candidate the station is actually talking in.
        self.keys = [keys] if isinstance(keys, (bytes, bytearray)) else list(keys)
        self.key: bytes | None = None
        self.fragments: dict[int, str] = {}
        self.expected = 0

    @staticmethod
    def _json(hex_body: str) -> dict | None:
        try:
            parsed = json.loads(bytes.fromhex(hex_body).decode("utf-8"))
            return parsed if isinstance(parsed, dict) else None
        except (ValueError, UnicodeDecodeError, json.JSONDecodeError):
            return None

    def _decrypt(self, encrypted: bytes) -> str | None:
        for key in ([self.key] if self.key else self.keys):
            payload = _decrypt_rc4_frame(encrypted, key)
            if payload is not None:
                self.key = key
                return payload
        return None

    def feed(self, encrypted: bytes) -> dict | None:
        payload = self._decrypt(encrypted)
        if payload is None:
            return None
        if payload.startswith("80"):
            try:
                packet = int(payload[8:12], 16)
                total = int(payload[12:16], 16)
            except (ValueError, IndexError):
                return None
            if packet < 1 or total < 1 or packet > total or total > 256:
                return None
            # A new first fragment replaces an abandoned partial response.
            if packet == 1 and (self.expected != total or 1 in self.fragments):
                self.fragments.clear()
            self.expected = total
            self.fragments[packet] = payload[16:]
            if not all(index in self.fragments for index in range(1, total + 1)):
                return None
            combined = "".join(self.fragments[index] for index in range(1, total + 1))
            self.fragments.clear()
            self.expected = 0
            return self._json(combined)
        # Ordinary responses retain an eight-hex-character command header after
        # the DFEC magic removed by _decrypt_rc4_frame.
        return self._json(payload[8:]) if len(payload) >= 8 else None


def _command(action: int, message_type: int, body: str = "") -> str:
    encoded = body.encode().hex()
    return f"DFEC00{action:02X}{message_type:02X}{len(encoded) // 2:02X}{encoded}"


# The property query, the one command a poll sends.
STATUS_COMMAND = _command(0xFC, 0x03)
_TIME_SYNC_HEADER = _command(0x0F, 0x08)[:10]


def time_sync_command(ts: int, utc_offset: int) -> str:
    """The clock sync the station needs before it answers a property query."""
    return _command(0x0F, 0x08, json.dumps({"ts": ts, "uo": utc_offset}, separators=(",", ":")))


# Every control command the allowlist can build, and so the only ones a grant
# from send_control can carry to the radio.
_CONTROL_COMMANDS = frozenset(
    jackery_control_command(control, value)
    for control, spec in JACKERY_CONTROLS.items()
    for value in ((True, False) if spec["kind"] == "switch" else spec["values"])
)


def check_outbound(command, control_grant: str | None = None) -> None:
    """Refuse any Jackery write but the status query, a clock sync, or a granted control.

    The Jackery counterpart of the DP3's OutboundGate, and like it this checks
    what is about to be written rather than trusting the caller that built it.
    It accepts exactly three shapes:

    * the status query, with no body;
    * a clock sync whose body is ``{"ts":<int>,"uo":<int>}`` and nothing else,
      byte for byte as ``time_sync_command`` spells it;
    * a control command, only when it is the one ``control_grant`` names and
      only when the allowlist itself builds it.

    Anything else raises PermissionError, naming no payload bytes.
    """
    if not isinstance(command, str):
        raise PermissionError("Jackery outbound gate: only a plaintext command may be written.")
    if command == STATUS_COMMAND:
        return
    if command.startswith(_TIME_SYNC_HEADER):
        try:
            body = json.loads(bytes.fromhex(command[12:]).decode("utf-8"))
        except (ValueError, UnicodeDecodeError):
            body = None
        if (isinstance(body, dict) and set(body) == {"ts", "uo"}
                and type(body["ts"]) is int and type(body["uo"]) is int
                and command == time_sync_command(body["ts"], body["uo"])):
            return
        raise PermissionError("Jackery outbound gate: unexpected clock-sync body blocked.")
    if control_grant is not None and command == control_grant and command in _CONTROL_COMMANDS:
        return
    raise PermissionError("Jackery outbound gate: command outside the allowlist blocked.")


def _winrt_address_type(device) -> str | None:
    """Preserve the public/random address type that Bleak 3 drops at connect.

    Windows' address-only WinRT lookup can return ``E_FAIL`` for a random BLE
    address.  The scanner has the missing type on its native advertisement;
    pass it explicitly to ``BleakClient`` instead of asking Windows to guess.
    """
    try:
        details = device.details
        event = details.adv or details.scan
        return "public" if int(event.bluetooth_address_type) == 0 else "random"
    except (AttributeError, TypeError, ValueError):
        return None


def _winrt_connectable(device) -> bool | None:
    """Return whether the native advertisement offers a GATT connection."""
    try:
        advertisement = device.details.adv
        if advertisement is None:
            return None
        # WinRT: 0=connectable undirected, 1=connectable directed. Values 2-4
        # are scannable/non-connectable/scan-response; 5 is an extended packet
        # whose connectability is not exposed by this enum.
        return int(advertisement.advertisement_type) in {0, 1}
    except (AttributeError, TypeError, ValueError):
        return None


async def _winrt_device_id(address: str, address_type: str | None) -> str | None:
    """Resolve a BLE address to its Windows AEP identifier.

    Bleak normally opens WinRT devices with ``FromBluetoothAddressAsync``.  On
    this machine that lookup consistently returns E_FAIL for the Explorer even
    while its connectable advertisement is visible.  Windows also exposes the
    DeviceInformation/``FromIdAsync`` path used by Microsoft's own GATT sample;
    resolving the identifier here lets us exercise that materially different
    path without changing anything on the power station.
    """
    import sys
    if sys.platform != "win32":
        return None
    from winrt.windows.devices.bluetooth import BluetoothAdapter, BluetoothAddressType, BluetoothLEDevice
    from winrt.windows.devices.enumeration import DeviceInformation

    bluetooth_address = int(address.replace(":", ""), 16)
    # AEP BLE identifiers are deterministic for a local-adapter/remote-device
    # pair. Creating that ID directly takes milliseconds; FindAllAsync takes a
    # fixed ~30 seconds even with an exact-address AQS filter on this host.
    adapter = await BluetoothAdapter.get_default_async()
    if adapter is not None:
        local_hex = f"{adapter.bluetooth_address:012x}"
        local = ":".join(local_hex[index:index + 2] for index in range(0, 12, 2))
        remote_hex = f"{bluetooth_address:012x}"
        remote = ":".join(remote_hex[index:index + 2] for index in range(0, 12, 2))
        candidate = f"BluetoothLE#BluetoothLE{local}-{remote}"
        device = await DeviceInformation.create_from_id_async(candidate)
        if device is not None:
            return device.id

    if address_type in {"public", "random"}:
        selector = BluetoothLEDevice.get_device_selector_from_bluetooth_address_with_bluetooth_address_type(
            bluetooth_address,
            BluetoothAddressType.PUBLIC if address_type == "public" else BluetoothAddressType.RANDOM,
        )
    else:
        selector = BluetoothLEDevice.get_device_selector_from_bluetooth_address(bluetooth_address)
    devices = await DeviceInformation.find_all_async_aqs_filter(selector)
    return devices[0].id if devices else None


async def _run_query_sequence(write, ready: asyncio.Event,
                              keys: list[bytes], timeout: float) -> bytes | None:
    """Run the app's connection sequence once per candidate session key.

    The station expects an encrypted time sync after notification setup and
    before it answers a property query.  ``write(command, key, label)`` takes
    the plaintext command and the candidate key to encrypt it with.  Returns
    the key that produced a decodable notification, or ``None`` if the station
    stayed silent for all of them.
    """
    utc_offset = datetime.now().astimezone().utcoffset()
    offset = int(utc_offset.total_seconds()) if utc_offset else 0
    sync = time_sync_command(int(time.time()), offset)
    for key in keys:
        await asyncio.sleep(.1)
        await write(sync, key, "time-sync")
        await asyncio.sleep(.1)
        await write(STATUS_COMMAND, key, "status")
        try:
            await asyncio.wait_for(ready.wait(), timeout)
        except asyncio.TimeoutError:
            continue
        # Some firmware sends the property map as several complete replies.
        # Allow that short burst to finish before the caller merges it.
        await asyncio.sleep(.35)
        return key
    return None


class LocalReader:
    """A local BLE link to one Explorer that stays open across polls.

    The station stops advertising once it rejoins its saved Wi-Fi network, so
    the window in which a connection can be opened may not come back for hours.
    A recorder therefore has to keep the session it managed to open instead of
    reconnecting for every reading, which is why this is a session object and
    not another one-shot read.
    """

    def __init__(self, identity: Identity, *, allow_control: bool = False):
        if not identity.serial or not identity.encryption_key:
            raise ValueError("Jackery advertisement did not contain usable key material.")
        self.identity = identity
        self.keys = _session_keys(identity.encryption_key)
        self.allow_control = bool(allow_control)
        # The one control command the write site may pass next. Only
        # send_control sets it, after spending allow_control, and the write
        # site clears it before anything else.
        self._control_grant: str | None = None
        # Set once the station answers, so later polls skip the candidate sweep.
        self.key: bytes | None = None
        self.assembler = _ResponseAssembler(self.keys)
        self.responses: list[dict] = []
        self.notification_count = 0
        self.ready = asyncio.Event()
        self._bleak = None
        self._device = self._session = self._session_token = None
        self._services: list = []
        self._write_char = self._notify_char = self._token = None

    async def __aenter__(self) -> "LocalReader":
        return await self.open()

    async def __aexit__(self, *_) -> None:
        await self.close()

    def _consume(self, data: bytes) -> None:
        self.notification_count += 1
        parsed = self.assembler.feed(data)
        if parsed is not None:
            self.responses.append(parsed)
            self.ready.set()

    def _backend_name(self) -> str:
        return "WinRT" if self._write_char is not None else f"Bleak/{sys.platform}"

    def _silence(self, operation: str, message: str) -> StationSilent:
        return StationSilent(
            f"backend={self._backend_name()}; operation={operation}; "
            f"exception=TimeoutError; transport_error=none_observed; {message}"
        )

    async def open(self) -> "LocalReader":
        """Attach and subscribe, without sending anything to the station yet."""
        try:
            if self.identity.device_id:
                await self._open_winrt()
            else:
                await self._open_bleak()
        except BaseException:
            await self.close()
            raise
        return self

    async def read(self, timeout: float = 8) -> dict:
        """Send one property query on the open session and merge the reply."""
        self.responses = []
        self.notification_count = 0
        self.ready.clear()
        if self.key is None:
            async def write_with_deadline(command, key, label):
                await self._write(command, label, key=key, timeout=timeout)

            self.key = await _run_query_sequence(write_with_deadline, self.ready, self.keys, timeout)
        else:
            await self._write(STATUS_COMMAND, "status", timeout=timeout)
            try:
                await asyncio.wait_for(self.ready.wait(), timeout)
                await asyncio.sleep(.35)
            except asyncio.TimeoutError:
                pass
        if not self.responses:
            if self.notification_count:
                raise self._silence(
                    "notification_decode",
                    f"Jackery sent {self.notification_count} GATT notification(s), but none decoded as status.",
                )
            if self.key is None:
                raise self._silence(
                    "notification_wait",
                    "Jackery accepted the local GATT connection but sent no notification for any "
                    f"of the {len(self.keys)} candidate session key(s).",
                )
            raise self._silence(
                "notification_wait",
                "Jackery stopped answering property queries on an open GATT session.",
            )
        merged = {}
        for response in self.responses:
            merged.update(response)
        return merged

    async def send_control(self, control: str, value) -> None:
        """Send one allowlisted control with single-use boundary authority.

        Callers still re-read persisted policy immediately before each send.
        The boundary flag authorizes exactly one control attempt and is consumed
        synchronously before any await, so concurrent callers cannot share one
        grant and a long-lived reader cannot carry an earlier policy decision
        into a later caller. Jackery does not provide a useful acknowledgement
        for these portable commands, so callers must follow this with a status
        read and compare the returned raw property before treating the action
        as confirmed.

        The write site checks the command again (check_outbound): it passes a
        control only as the grant set here names it, so a write of a control
        body from anywhere else is refused.
        """
        if not self.allow_control:
            raise PermissionError("Jackery control is turned off.")
        self.allow_control = False
        if self.key is None:
            raise ConnectionError("Jackery control requires an authenticated status session.")
        command = jackery_control_command(control, value)
        # Nothing awaits between this and the write site taking it back.
        self._control_grant = command
        await self._write(command, f"control {control}")

    async def close(self) -> list[str]:
        diagnostics = []
        if self._bleak is not None:
            client, self._bleak = self._bleak, None
            for operation, awaitable in (
                ("stop_notify", client.stop_notify(DATA_NOTIFY_UUID)),
                ("disconnect", client.disconnect()),
            ):
                try:
                    await _bounded_operation(
                        awaitable,
                        backend=f"Bleak/{sys.platform}",
                        operation=operation,
                        timeout=GATT_CLOSE_TIMEOUT,
                    )
                except Exception as exc:
                    diagnostics.append(str(exc))
        if self._device is not None or self._session is not None:
            diagnostics.extend(await self._close_winrt())
        return diagnostics

    async def _open_bleak(self) -> None:
        # The discovery result already gave us the Windows Bluetooth address.
        # Use it directly so a short advertising burst cannot disappear between
        # scan and connect.
        winrt = ({"address_type": self.identity.address_type}
                 if self.identity.address_type in {"public", "random"} else {})
        client = BleakClient(
            self.identity.device or self.identity.address,
            winrt=winrt,
            timeout=CONNECT_TIMEOUT,
            **bleak_adapter_kwargs(),
        )
        backend = f"Bleak/{sys.platform}"
        # Classified, not bounded here: Bleak's own deadline, CONNECT_TIMEOUT, governs.
        await _bounded_operation(client.connect(), backend=backend,
                                 operation="connect", timeout=None)
        self._bleak = client
        await _bounded_operation(
            client.start_notify(DATA_NOTIFY_UUID, lambda _, data: self._consume(bytes(data))),
            backend=backend, operation="start_notify", timeout=None,
        )

    async def _open_winrt(self) -> None:
        """Attach with native WinRT, bypassing Bleak's services-changed loop."""
        import uuid
        from winrt.windows.devices.bluetooth import BluetoothCacheMode, BluetoothLEDevice
        from winrt.windows.devices.bluetooth.genericattributeprofile import (
            GattClientCharacteristicConfigurationDescriptorValue,
            GattSession,
            GattSessionStatus,
        )

        loop = asyncio.get_running_loop()
        self._device = await BluetoothLEDevice.from_id_async(self.identity.device_id)
        if self._device is None:
            raise ConnectionError("Windows could not open the Jackery device ID.")
        self._session = await GattSession.from_device_id_async(self._device.bluetooth_device_id)
        if self._session is None or not self._session.can_maintain_connection:
            raise ConnectionError("Windows could not create a persistent Jackery GATT session.")
        active = asyncio.Event()

        def session_changed(_, args):
            if args.status == GattSessionStatus.ACTIVE:
                loop.call_soon_threadsafe(active.set)

        self._session_token = self._session.add_session_status_changed(session_changed)
        if self._session.session_status == GattSessionStatus.ACTIVE:
            active.set()
        self._session.maintain_connection = True
        await asyncio.wait_for(active.wait(), 5)

        result = await self._device.get_gatt_services_with_cache_mode_async(BluetoothCacheMode.UNCACHED)
        if int(result.status) != 0:
            raise ConnectionError(f"Windows GATT discovery failed with status {int(result.status)}.")
        self._services = list(result.services)
        write_uuid = uuid.UUID(DATA_WRITE_UUID)
        notify_uuid = uuid.UUID(DATA_NOTIFY_UUID)
        for service in self._services:
            if self._write_char is None:
                found = await service.get_characteristics_for_uuid_with_cache_mode_async(
                    write_uuid, BluetoothCacheMode.UNCACHED)
                if int(found.status) == 0 and found.characteristics:
                    self._write_char = found.characteristics[0]
            if self._notify_char is None:
                found = await service.get_characteristics_for_uuid_with_cache_mode_async(
                    notify_uuid, BluetoothCacheMode.UNCACHED)
                if int(found.status) == 0 and found.characteristics:
                    self._notify_char = found.characteristics[0]
        if self._write_char is None or self._notify_char is None:
            raise ConnectionError("Jackery EE01/EE02 GATT characteristics were not found.")

        def changed(_, args):
            data = bytes(args.characteristic_value)
            loop.call_soon_threadsafe(self._consume, data)

        self._token = self._notify_char.add_value_changed(changed)
        subscribed = await self._notify_char.write_client_characteristic_configuration_descriptor_with_result_async(
            GattClientCharacteristicConfigurationDescriptorValue.NOTIFY
        )
        if int(subscribed.status) != 0:
            raise ConnectionError(f"Jackery notification setup failed with status {int(subscribed.status)}.")

    async def _close_winrt(self) -> list[str]:
        from winrt.windows.devices.bluetooth.genericattributeprofile import (
            GattClientCharacteristicConfigurationDescriptorValue,
        )
        diagnostics = []
        if self._notify_char is not None and self._token is not None:
            try:
                await _bounded_operation(
                    self._notify_char.write_client_characteristic_configuration_descriptor_with_result_async(
                        GattClientCharacteristicConfigurationDescriptorValue.NONE
                    ),
                    backend="WinRT",
                    operation="stop_notify",
                    timeout=GATT_CLOSE_TIMEOUT,
                )
            except (TimeoutError, ConnectionError, OSError) as exc:
                diagnostics.append(str(exc))
            self._notify_char.remove_value_changed(self._token)
        self._token = self._notify_char = self._write_char = None
        for service in self._services:
            service.close()
        self._services = []
        if self._session is not None:
            self._session.maintain_connection = False
            if self._session_token is not None:
                self._session.remove_session_status_changed(self._session_token)
            self._session.close()
        self._session = self._session_token = None
        if self._device is not None:
            self._device.close()
        self._device = None
        return diagnostics

    async def _write(self, command: str, label: str, *, key: bytes | None = None,
                     timeout: float | None = None) -> None:
        """The only Jackery GATT write site. Every command passes check_outbound here.

        It takes the plaintext command and encrypts it itself, with the session
        key or the candidate ``key`` a new session is trying, so no caller can
        hand it bytes the gate never read. The control grant is taken before
        anything else and is good for this one write whatever it carries.
        """
        grant, self._control_grant = self._control_grant, None
        check_outbound(command, grant)
        key = self.key if key is None else key
        if key is None:
            raise ConnectionError("Jackery write requires a session key.")
        payload = _rc4_frame(command, key)
        deadline = GATT_OPERATION_TIMEOUT if timeout is None else timeout
        if self._bleak is not None:
            await _bounded_operation(
                self._bleak.write_gatt_char(DATA_WRITE_UUID, payload, response=False),
                backend=f"Bleak/{sys.platform}",
                operation=f"write:{label}",
                timeout=deadline,
            )
            return
        from winrt.windows.devices.bluetooth.genericattributeprofile import GattWriteOption
        from winrt.windows.storage.streams import Buffer
        buffer = Buffer(len(payload))
        buffer.length = buffer.capacity
        with memoryview(buffer) as view:
            view[:] = payload
        result = await _bounded_operation(
            self._write_char.write_value_with_result_and_option_async(
                buffer, GattWriteOption.WRITE_WITHOUT_RESPONSE
            ),
            backend="WinRT",
            operation=f"write:{label}",
            timeout=deadline,
        )
        if int(result.status) != 0:
            raise ConnectionError(f"Jackery {label} write failed with status {int(result.status)}.")


async def discover_reader(timeout: float = 30, *, serial: str | None = None,
                          lease=None) -> LocalReader:
    """Discover an Explorer and open a session while its advertisement is live.

    The Explorer's connectable advertising window can end as soon as an attach
    is attempted.  On WinRT, stopping the watcher before opening GATT can also
    leave the just-returned ``BLEDevice`` unusable (``E_FAIL``).  Keep discovery
    running until the session is open or the attempt has failed.

    With ``serial``, every other Explorer is ignored before anything connects:
    the station accepts one BLE client, so opening a neighbour's only to reject
    it afterwards occupies that station's slot and costs this one its window.
    Either an open reader is returned, or an exception is raised with nothing
    left open.

    ``lease``, the shared adapter lease, is entered only once the wanted
    advertisement has arrived and held only while the session opens. The search
    can run for the whole ``timeout``, and the Explorer may not advertise for
    hours, so holding the lease through it kept the other collector from
    recovering for as long as this station was away.
    """
    loop = asyncio.get_running_loop()
    ready = loop.create_future()
    ignored = set()
    backend = f"Bleak/{sys.platform}"

    def callback(device, advertisement):
        if ready.done():
            return
        name = (device.name or "").upper()
        if not any(prefix in name for prefix in JACKERY_NAME_PREFIXES):
            return
        parsed = parse_advertisement(advertisement.manufacturer_data or {},
                                     advertisement.service_data or {})
        # The address-only WinRT path returned E_FAIL for advertisements marked
        # non-connectable.  The AEP-id/FromIdAsync fallback below is deliberately
        # meant to bypass that lookup, so let it qualify the decoded Explorer
        # instead of filtering the packet before the fallback can run.
        if parsed and parsed.model_code == 8 and parsed.encryption_key:
            if serial is not None and parsed.serial != serial:
                ignored.add(device.address)
                return
            ready.set_result(Identity(device.address.upper(), device.name or "",
                                      parsed.serial, parsed.model_code,
                                      parsed.battery_level, advertisement.rssi,
                                      parsed.encryption_key, device=device,
                                      address_type=_winrt_address_type(device)))

    scanner = BleakScanner(detection_callback=callback, **bleak_adapter_kwargs())
    scanning = False
    reader = None
    await _bounded_operation(scanner.start(), backend=backend, operation="scan_start", timeout=None)
    scanning = True
    try:
        try:
            identity = await asyncio.wait_for(ready, timeout)
        except asyncio.TimeoutError:
            wanted = f"Jackery {serial}" if serial else "an Explorer 1000 v2"
            others = f"; ignored {len(ignored)} other Explorer(s)" if ignored else ""
            raise StationNotFound(
                f"No usable advertisement from {wanted} within {timeout:g}s{others}."
            ) from None
        device_id = await _winrt_device_id(identity.address, identity.address_type)
        if device_id:
            identity = replace(identity, device_id=device_id)
            # FromIdAsync no longer relies on the live watcher. Stopping it
            # avoids a WinRT services-changed loop during GATT discovery.
            await _bounded_operation(scanner.stop(), backend=backend,
                                     operation="scan_stop", timeout=None)
            scanning = False
        async with lease or contextlib.nullcontext():
            reader = await LocalReader(identity).open()
    finally:
        if scanning:
            try:
                await _bounded_operation(scanner.stop(), backend=backend,
                                         operation="scan_stop", timeout=None)
            except BaseException:
                # Raising past an open session would strand the station's only
                # client slot with nothing left holding it.
                if reader is not None:
                    await reader.close()
                raise
    return reader


async def discover_and_read_status(timeout: float = 30) -> dict:
    """Discover, take one status snapshot, and release the connection."""
    reader = await discover_reader(timeout)
    try:
        return await reader.read()
    finally:
        await reader.close()


def parse_advertisement(manufacturer_data: dict, service_data: dict) -> Identity | None:
    """Decode the public Jackery advertisement and derive its read key.

    Invalid or incomplete advertisements are ignored.  The key is returned for
    the eventual connection backend, but no key or device data is persisted by
    this discovery command.
    """
    try:
        manufacturer_id, manufacturer = next(iter(manufacturer_data.items()))
        serial = _manufacturer_serial(manufacturer_id, bytes(manufacturer))
        if len(serial) != 15:
            return None
        service = next((bytes(value) for uuid, value in service_data.items()
                        if SERVICE_DATA_UUID in str(uuid).lower()), None)
        if service is None or len(service) < 14:
            return None
        decrypted = rc4(service[:14], (serial[:3] + serial[-5:]).encode() + SALT_RC4)
        if _jackery_crc(decrypted[:-2]) != decrypted[-2:].hex().upper():
            return None
        data = decrypted[:-2]
        xor_key = data[-1]
        decoded = bytes(value ^ xor_key for value in data[:-1])
        if len(decoded) < 9:
            return None
        model_code = int.from_bytes(decoded[:2], "big")
        guid = decoded[2:8]
        battery = decoded[8]
        key = base64.b64encode(serial[-6:].encode() + guid + SALT_KEY).decode()
        return Identity("", "", serial, model_code, battery, None, key)
    except (StopIteration, UnicodeDecodeError, ValueError, IndexError, TypeError):
        return None


async def scan(timeout: float = 10) -> list[Identity]:
    """Discover nearby Jackery advertisements without opening a connection."""
    found: dict[str, Identity] = {}

    def callback(device, advertisement):
        name = device.name or ""
        upper = name.upper()
        if not any(prefix in upper for prefix in JACKERY_NAME_PREFIXES):
            return
        parsed = parse_advertisement(advertisement.manufacturer_data or {},
                                     advertisement.service_data or {})
        if parsed is None:
            found[device.address] = Identity(device.address.upper(), name, None,
                                              None, None, advertisement.rssi,
                                              manufacturer_data={str(k): bytes(v).hex()
                                                                 for k, v in (advertisement.manufacturer_data or {}).items()},
                                              service_data={str(k): bytes(v).hex()
                                                            for k, v in (advertisement.service_data or {}).items()},
                                              device=device)
        else:
            found[device.address] = Identity(device.address.upper(), name, parsed.serial,
                                              parsed.model_code, parsed.battery_level,
                                              advertisement.rssi, parsed.encryption_key,
                                              device=device,
                                              address_type=_winrt_address_type(device))

    scanner = BleakScanner(detection_callback=callback, **bleak_adapter_kwargs())
    await scanner.start()
    try:
        import asyncio
        await asyncio.sleep(timeout)
    finally:
        await scanner.stop()
    return list(found.values())
