"""Recover orphaned battery BlueZ links before OpenDP3 rediscovery."""
import asyncio
import json
import os
from pathlib import Path
import re
import sys

JACKERY_ADDRESS_STATE = "jackery-bluez.json"
MAC_RE = re.compile(r"(?:[0-9A-F]{2}:){5}[0-9A-F]{2}")
ADAPTER_RE = re.compile(r"hci[0-9]+")


def selected_adapter():
    adapter = os.environ.get("OPENDP3_BLE_ADAPTER", "hci0").strip()
    if not ADAPTER_RE.fullmatch(adapter):
        raise ValueError("Bluetooth adapter must be hci followed by digits.")
    return adapter


def _variant_value(value):
    """Unwrap dbus-fast Variant values without depending on Variant at import time."""
    return value.value if hasattr(value, "value") else value


def _bluez_bytes_map(value):
    """Convert BlueZ a{qv}/a{sv} byte mappings into plain Python bytes."""
    raw = _variant_value(value)
    if not isinstance(raw, dict):
        return {}
    result = {}
    for key, item in raw.items():
        item = _variant_value(item)
        if isinstance(item, (bytes, bytearray, memoryview)):
            result[key] = bytes(item)
        elif isinstance(item, (list, tuple)) and all(
                isinstance(part, int) and 0 <= part <= 255 for part in item):
            result[key] = bytes(item)
    return result


def _jackery_serial_from_bluez(values):
    """Decode a cached BlueZ advertisement and return its Jackery serial."""
    from opendp3.jackery import parse_advertisement
    parsed = parse_advertisement(
        _bluez_bytes_map(values.get("ManufacturerData")),
        _bluez_bytes_map(values.get("ServiceData")),
    )
    return parsed.serial if parsed else None


def load_known_jackery_address(path: Path, serial: str):
    """Read the last verified Explorer address only when it belongs to this serial."""
    try:
        saved = json.loads(path.read_text("utf-8"))
    except (OSError, ValueError, TypeError):
        return None
    if not isinstance(saved, dict) or saved.get("serial") != serial:
        return None
    address = str(saved.get("address", "")).upper()
    return address if MAC_RE.fullmatch(address) else None


def save_known_jackery_address(path: Path, serial: str, address: str):
    """Persist a successfully discovered Explorer address for precise recovery."""
    address = str(address).upper()
    if not MAC_RE.fullmatch(address):
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_suffix(path.suffix + ".tmp")
    fd = os.open(temp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as stream:
        json.dump({"serial": serial, "address": address}, stream)
        stream.flush()
        os.fsync(stream.fileno())
    os.replace(temp, path)


async def _disconnect_matching(match, label, *, require_unique=False, adapter=None,
                               bus_factory=None):
    """Disconnect connected BlueZ devices selected by a narrow predicate."""
    from dbus_fast import BusType, Message, MessageType
    from dbus_fast.aio import MessageBus
    bus = await (bus_factory() if bus_factory else MessageBus(bus_type=BusType.SYSTEM)).connect()
    try:
        reply = await asyncio.wait_for(bus.call(Message(
            destination="org.bluez", path="/",
            interface="org.freedesktop.DBus.ObjectManager", member="GetManagedObjects")), 10)
        if reply.message_type == MessageType.ERROR:
            raise ConnectionError("BlueZ device inventory unavailable")

        adapter = adapter or selected_adapter()
        prefix = f"/org/bluez/{adapter}/"
        matches = []
        for path, interfaces in reply.body[0].items():
            if not path.startswith(prefix):
                continue
            props = interfaces.get("org.bluez.Device1", {})
            values = {key: _variant_value(value) for key, value in props.items()}
            if values.get("Connected") and match(values):
                matches.append(path)

        if require_unique and len(matches) > 1:
            raise ConnectionError(f"Multiple connected {label} BLE candidates; refusing orphan recovery")

        for path in matches:
            result = await asyncio.wait_for(bus.call(Message(
                destination="org.bluez", path=path,
                interface="org.bluez.Device1", member="Disconnect")), 10)
            if result.message_type == MessageType.ERROR:
                raise ConnectionError(f"BlueZ could not release the orphaned {label} link")
            print(f"Released orphaned {label} BLE link before rediscovery.", flush=True)
            # Give the station and BlueZ a moment to return to advertising before
            # the scanner starts. This is especially important for the Explorer.
            await asyncio.sleep(1)
    finally:
        bus.disconnect()


async def disconnect_orphan(address, *, adapter=None, bus_factory=None):
    """Release the configured EcoFlow address if BlueZ still owns its link."""
    address = address.upper()
    await _disconnect_matching(
        lambda values: str(values.get("Address", "")).upper() == address,
        "EcoFlow",
        adapter=adapter,
        bus_factory=bus_factory,
    )


async def disconnect_jackery_orphan(serial, *, known_address=None,
                                    serial_resolver=None, adapter=None, bus_factory=None):
    """Release only a BlueZ Jackery link verified as the configured Explorer.

    The Explorer may stop advertising while BlueZ still reports an old local
    connection as Connected/ServicesResolved. A connected device is eligible for
    recovery only when its address was previously learned from this serial, or a
    cached BlueZ advertisement decodes to this serial. Device1 Name/Alias is not
    ownership evidence and may be absent from BlueZ's cache, so it is deliberately
    not required for serial verification.
    """
    known_address = str(known_address or "").upper()
    resolver = serial_resolver or _jackery_serial_from_bluez

    def matches(values):
        address = str(values.get("Address", "")).upper()
        if known_address and address == known_address:
            return True
        return resolver(values) == serial

    await _disconnect_matching(
        matches,
        "Jackery",
        require_unique=True,
        adapter=adapter,
        bus_factory=bus_factory,
    )


def main():
    from opendp3 import ble
    from opendp3.cli import main as cli_main
    from opendp3.config import load_config
    args = sys.argv[1:]
    if len(args) < 3 or args[0] != "--data-dir":
        raise SystemExit("Unsupported headless worker arguments")

    if len(args) == 3 and args[2] == "record":
        cfg = load_config(Path(args[1]) / "config.json")
        original_scan = ble.scan

        async def recover_then_scan(timeout=8):
            await disconnect_orphan(cfg.address)
            return await original_scan(timeout)

        ble.scan = recover_then_scan
        return cli_main(args)

    if (len(args) == 5 and args[2] == "jackery-record" and args[3] == "--serial"
            and re.fullmatch(r"[0-9]{15}", args[4])):
        # cli._jackery_ble_loop imports discover_reader when the writer lock is
        # already held. Patch the module first so every later rediscovery attempt
        # gets orphan cleanup, not just the first process startup.
        from opendp3 import jackery
        serial = args[4]
        state_path = Path(args[1]) / JACKERY_ADDRESS_STATE
        original_discover = jackery.discover_reader

        async def recover_then_discover(timeout=30, wanted=None):
            known_address = load_known_jackery_address(state_path, serial)
            await disconnect_jackery_orphan(
                serial,
                known_address=known_address,
            )
            reader = await original_discover(timeout, wanted)
            # Without a serial, discover_reader() attaches to whichever Explorer
            # advertises first, and the CLI rejects a stranger immediately
            # afterward; do not poison our recovery state by learning its
            # address under the configured serial first.
            if reader.identity.serial == serial:
                try:
                    save_known_jackery_address(state_path, serial, reader.identity.address)
                except OSError as exc:
                    # The address cache only improves future orphan recovery. A
                    # full/read-only /data must not strand the BLE reader we just
                    # opened by preventing it from reaching _jackery_ble_loop.
                    detail = str(exc).strip() or type(exc).__name__
                    print(
                        "Could not persist verified Jackery BLE address "
                        f"({detail}); continuing with active reader.",
                        flush=True,
                    )
            return reader

        jackery.discover_reader = recover_then_discover
        return cli_main(args)

    raise SystemExit("Unsupported headless worker arguments")


if __name__ == "__main__":
    raise SystemExit(main())
