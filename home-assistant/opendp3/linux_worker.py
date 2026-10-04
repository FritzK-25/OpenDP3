"""Recover orphaned battery BlueZ links before OpenPowerstation rediscovery."""
import asyncio
import contextlib
import json
import os
from pathlib import Path
import re
import sys

JACKERY_ADDRESS_STATE = "jackery-bluez.json"
MAC_RE = re.compile(r"(?:[0-9A-F]{2}:){5}[0-9A-F]{2}")
ADAPTER_RE = re.compile(r"hci[0-9]+")
# How long one orphan check may take in all: connecting to the system bus,
# reading BlueZ's device inventory, releasing a link and the second after it.
# Past this it is abandoned and discovery goes ahead without it. It runs under
# the shared adapter lease, whose hold limit (radio.RADIO_HOLD_LIMIT) the DP3's
# scan, connect and handshake have to fit into after it.
ORPHAN_CHECK_TIMEOUT = 12.0


class AmbiguousOrphan(ConnectionError):
    """More than one connected device matched; which link is ours is unknown."""


class OrphanCheck:
    """One collector's orphan release, run as best effort and bounded in time.

    Releasing a link BlueZ still holds helps rediscovery; it is not a
    condition for it. It used to run unguarded in front of every discovery,
    so any failure in it stopped every DP3 scan for as long as it lasted, and
    ended the Jackery collector for anything not an OSError: dbus-fast missing
    or changed after a rebuild, the system bus refusing the connection
    (dbus-fast's AuthError), BlueZ answering with an error, or a call that
    never returned. Now a failure is logged by its type alone, once until the
    check works again, and discovery goes ahead.

    AmbiguousOrphan is the exception: two connected candidates for one
    Explorer mean this worker cannot tell which link is its own, so the
    attempt is still refused, as a ConnectionError the collector records and
    retries.
    """

    def __init__(self, release, label, *, timeout=None):
        self.release, self.label = release, label
        self.timeout = ORPHAN_CHECK_TIMEOUT if timeout is None else timeout
        self.failing = None

    async def __call__(self, *args, **kwargs):
        try:
            async with asyncio.timeout(self.timeout):
                await self.release(*args, **kwargs)
        except AmbiguousOrphan:
            raise
        except Exception as exc:
            kind = type(exc).__name__
            if kind != self.failing:
                print(f"BlueZ orphan check for {self.label} skipped ({kind}); "
                      "discovering anyway.", flush=True)
            self.failing = kind
            return
        if self.failing is not None:
            print(f"BlueZ orphan check for {self.label} working again.", flush=True)
        self.failing = None


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
    from openpowerstation.jackery import parse_advertisement
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
                               bus_factory=None) -> int:
    """Disconnect connected BlueZ devices selected by a narrow predicate.

    Returns how many links were released.
    """
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
            raise AmbiguousOrphan(f"Multiple connected {label} BLE candidates; refusing orphan recovery")

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
        return len(matches)
    finally:
        bus.disconnect()


async def disconnect_orphan(address, *, adapter=None, bus_factory=None) -> int:
    """Release the configured EcoFlow address if BlueZ still owns its link."""
    address = address.upper()
    return await _disconnect_matching(
        lambda values: str(values.get("Address", "")).upper() == address,
        "EcoFlow",
        adapter=adapter,
        bus_factory=bus_factory,
    )


async def disconnect_jackery_orphan(serial, *, known_address=None,
                                    serial_resolver=None, adapter=None, bus_factory=None) -> int:
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

    return await _disconnect_matching(
        matches,
        "Jackery",
        require_unique=True,
        adapter=adapter,
        bus_factory=bus_factory,
    )


def main():
    from openpowerstation import ble
    from openpowerstation.cli import main as cli_main
    from openpowerstation.config import load_config
    args = sys.argv[1:]
    if len(args) < 3 or args[0] != "--data-dir":
        raise SystemExit("Unsupported headless worker arguments")

    # Each wrapper is handed to the collector through cli.main, which calls it
    # for every discovery attempt, not only the first. It used to be patched
    # into openpowerstation.ble and openpowerstation.jackery, which reached the collectors only
    # while they happened to look those names up through the module at call
    # time; moving an import to the top of a file silently dropped the release.
    if len(args) == 3 and args[2] == "record":
        cfg = load_config(Path(args[1]) / "config.json")
        original_scan = ble.scan
        release = OrphanCheck(disconnect_orphan, "EcoFlow")

        async def recover_then_scan(*args, **kwargs):
            # The scan keeps its own window (ble.SCAN_TIMEOUT), which the shared
            # adapter lease's hold limit is sized against; this only adds the
            # release before it, which never keeps the scan from running.
            await release(cfg.address)
            return await original_scan(*args, **kwargs)

        return cli_main(args, scan=recover_then_scan)

    if (len(args) == 5 and args[2] == "jackery-record" and args[3] == "--serial"
            and re.fullmatch(r"[0-9]{15}", args[4])):
        from openpowerstation import jackery
        serial = args[4]
        state_path = Path(args[1]) / JACKERY_ADDRESS_STATE
        original_discover = jackery.discover_reader
        release = OrphanCheck(disconnect_jackery_orphan, "Jackery")

        async def recover_then_discover(timeout=30, *, lease=None, **_caller):
            known_address = load_known_jackery_address(state_path, serial)
            # The collector's adapter lease covers the BlueZ operations and not
            # the search between them: this release under it, and the connect,
            # which discovery takes it for once the Explorer advertises. Only
            # two candidates for this Explorer stop the attempt here.
            async with lease or contextlib.nullcontext():
                await release(
                    serial,
                    known_address=known_address,
                )
            # This worker exists for one Explorer, so discovery is pinned to its
            # serial whatever the caller passed: another station advertising
            # first is ignored rather than connected to and then rejected.
            options = {"serial": serial}
            if lease is not None:
                options["lease"] = lease
            reader = await original_discover(timeout, **options)
            # Defence in depth: should a discovery ever hand back a different
            # Explorer, the CLI rejects it; do not poison our recovery state by
            # learning its address under the configured serial first.
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

        return cli_main(args, discover=recover_then_discover)

    raise SystemExit("Unsupported headless worker arguments")


if __name__ == "__main__":
    raise SystemExit(main())
