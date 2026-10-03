"""Public command line interface; credentials never appear in arguments."""
import argparse
import asyncio
import contextlib
from datetime import datetime
import getpass
import json
from pathlib import Path
import sys
import time

import portalocker

from .config import Config, data_dir, load_config, resolve_user_id, save_config

# Consecutive Jackery reads that map to nothing before the BLE session is
# dropped and reacquired. A station can answer with a status frame carrying no
# dashboard field -- a settings page, a mode change, a firmware quirk -- and one
# of those is not evidence the link is bad. Exiting the process on the first one
# cost a supervisor restart plus a full rediscovery, a far longer outage than
# the reattach below, and the EcoFlow collector has never behaved that way.
UNMAPPED_READ_LIMIT = 5

def parser():
    p = argparse.ArgumentParser(description="OpenPowerstation — local DELTA Pro 3 and Jackery telemetry and control")
    p.add_argument("--data-dir",type=Path,default=data_dir(),help="Local data/config directory")
    subs = p.add_subparsers(dest="command",required=True)
    scan = subs.add_parser("scan",help="Detect adapter and discover DP3 advertisements (no connection)")
    scan.add_argument("--seconds",type=float,default=8)
    jackery_scan = subs.add_parser("jackery-scan", help="Discover nearby Jackery BLE advertisements (no connection)")
    jackery_scan.add_argument("--seconds", type=float, default=10)
    jackery_status = subs.add_parser("jackery-status", help="Read one Jackery status snapshot over BLE")
    jackery_status.add_argument("--seconds", type=float, default=30)
    jackery_ble_record = subs.add_parser("jackery-record", help="Record and optionally control Jackery over local BLE (no account)")
    jackery_ble_record.add_argument("--database", type=Path)
    jackery_ble_record.add_argument("--serial", required=True, help="15-digit Jackery serial (see jackery-scan)")
    jackery_ble_record.add_argument("--interval", type=float, default=3)
    jackery_ble_record.add_argument("--hours", type=float)
    jackery_ble_record.add_argument("--stop-file", type=Path,
        help="Gracefully stop when this local file is created (default: <data-dir>/jackery.stop, "
             "the name Stop-OpenPowerstation.cmd signals)")
    jackery_compact = subs.add_parser("jackery-compact",
        help="Thin old ordinary Jackery history to one sample a minute (reports only unless --apply)")
    jackery_compact.add_argument("--database", type=Path)
    jackery_compact.add_argument("--grace-hours", type=float, default=48)
    jackery_compact.add_argument("--bucket-seconds", type=float, default=60)
    jackery_compact.add_argument("--apply", action="store_true",
        help="Actually delete the thinned frames; without this nothing is removed")
    jackery_bridge = subs.add_parser("jackery-bridge", help="Publish Jackery readings to Home Assistant over MQTT")
    jackery_bridge.add_argument("--database", type=Path)
    jackery_bridge.add_argument("--serial", required=True, help="15-digit Jackery serial (see jackery-scan)")
    jackery_bridge.add_argument("--interval", type=float, default=1)
    jackery_bridge.add_argument("--once", action="store_true")
    jackery_bridge.add_argument("--stop-file", type=Path,
        help="Gracefully stop when this local file is created (default: <data-dir>/jackery-bridge.stop, "
             "so Stop-OpenPowerstation.cmd can always reach a manually-started run)")
    setup = subs.add_parser("setup",help="Select a device and enter user ID or log in once")
    setup.add_argument("--login",action="store_true",help="Explicitly permit one setup-only EcoFlow login")
    record = subs.add_parser("record",help="Record the configured DP3 over BLE")
    record.add_argument("--database",type=Path)
    record.add_argument("--hours",type=float,help="Stop after this many hours; qualification is manual")
    record.add_argument("--stop-file", type=Path,
        help="Gracefully stop when this local file is created (default: <data-dir>/collector.stop, "
             "so Stop-OpenPowerstation.cmd can always reach a manually-started run)")
    replay = subs.add_parser("replay",help="Inspect/redecode a saved database without Bluetooth")
    replay.add_argument("database",type=Path)
    replay.add_argument("--session",help="Restrict inspection and verification to this recorded session")
    replay.add_argument("--verify",action="store_true",help="Re-decode raw frames and compare observations")
    export = subs.add_parser("export",help="Export numeric CSV and an offline report")
    export.add_argument("database",type=Path)
    export.add_argument("destination",type=Path)
    export.add_argument("--session")
    export.add_argument("--incident",type=int)
    export.add_argument("--raw",action="store_true",help="Also make a separate PRIVATE archive that can contain device identifiers")
    gui = subs.add_parser("gui",help="Open desktop viewer")
    gui.add_argument("database",type=Path,nargs="?")
    demo = subs.add_parser("demo",help="Create a separate synthetic demonstration recording")
    demo.add_argument("--database",type=Path)
    bridge = subs.add_parser("bridge",help="Publish current readings to Home Assistant over MQTT")
    bridge.add_argument("--database",type=Path)
    bridge.add_argument("--interval",type=float,help="Seconds between publishes; overrides the saved setting")
    bridge.add_argument("--once",action="store_true",help="Publish a single update and exit")
    bridge.add_argument("--dry-run",action="store_true",help="Print what would be published; connect to nothing")
    return p

async def do_scan(seconds=8):
    from .ble import scan, adapter_status
    print((await adapter_status())['detail'])
    try:
        found = await scan(seconds)
    except Exception as exc:
        raise ValueError(f"Bluetooth scan unavailable ({type(exc).__name__}). Enable the adapter and Windows Bluetooth permission.") from None
    if not found:
        print("Scan completed; no DP3 advertisements seen. Move closer and close the phone Bluetooth session.")
    for index,(identity,_) in enumerate(found,1):
        print(f"{index}. {identity.address}  {identity.serial}  RSSI {identity.rssi} dBm  encryption {identity.encryption}")
    return found

async def do_jackery_scan(seconds=10):
    from .jackery import scan
    print("Scanning for Jackery BLE advertisements; no connection or writes will be made.")
    try:
        found = await scan(seconds)
    except Exception as exc:
        raise ValueError(f"Bluetooth scan unavailable ({type(exc).__name__}). Enable the adapter and Windows Bluetooth permission.") from None
    if not found:
        print("Scan completed; no Jackery advertisements seen. Wake the Explorer and close the phone app.")
    for index, device in enumerate(found, 1):
        details = f"serial {device.serial}" if device.serial else "advertisement not yet decoded"
        if device.model_code is not None:
            details += f", model code {device.model_code}"
        if device.battery_level is not None:
            details += f", battery {device.battery_level}%"
        print(f"{index}. {device.address}  {device.name or '(unnamed)'}  RSSI {device.rssi} dBm  {details}")
        if device.manufacturer_data or device.service_data:
            print(f"   manufacturer={device.manufacturer_data or {}} service={device.service_data or {}}")
    return found

async def do_jackery_status(seconds=30):
    from .jackery import discover_and_read_status
    print("Looking for Jackery; one read-only status query will be sent.")
    try:
        result = await discover_and_read_status(seconds)
    except asyncio.TimeoutError as exc:
        detail = str(exc).strip()
        raise ValueError(detail or "Explorer 1000 v2 was not found or its advertisement could not be decoded.") from None
    except OSError as exc:
        raise ValueError(
            f"Explorer advertisement decoded, but Windows could not open its BLE connection "
            f"({type(exc).__name__}: {exc}). Reactivate the radio once and retry."
        ) from None
    print(json.dumps(result, indent=2, sort_keys=True))
    return result

async def _jackery_ble_loop(store, interval, hours, serial, stop_file, *, config_path=None):
    from .config import control_allowed
    from .jackery import (JACKERY_CONTROLS, discover_reader, jackery_control_readback_matches,
                     jackery_control_value)
    from .jackery_fields import PEAK_KEYS, map_properties
    from .radio import RadioLease, radio_lock_path
    from .recorder import Compaction, Recorder
    start = time.monotonic()
    recorder = reader = None
    unmapped_reads = 0

    def finished():
        return ((hours is not None and time.monotonic() - start >= hours * 3600)
                or bool(stop_file and stop_file.exists()))

    async def drain_controls(active_reader, active_recorder):
        """Consume fresh Jackery control requests and verify each one by readback."""
        directory = store.path.parent / "jackery-commands"
        if not directory.is_dir() or active_reader is None or active_recorder is None:
            return None
        latest_properties = None
        now = time.time()
        for path in sorted(directory.glob("*.json")):
            request = None
            try:
                age = now - path.stat().st_mtime
                request = json.loads(path.read_text("utf-8"))
            except (OSError, ValueError):
                age = None
            try:
                path.unlink()
            except OSError:
                pass
            if not isinstance(request, dict):
                continue
            control, value = request.get("control"), request.get("value")
            if control not in JACKERY_CONTROLS:
                active_recorder.event("control_refused", "Jackery unsupported control request.")
                continue
            if age is None or age > 30.0:
                active_recorder.event("control_refused", f"Jackery {control}: request expired before it could be sent.")
                continue
            allowed = control_allowed(config_path or store.path.parent / "config.json")
            active_reader.allow_control = allowed
            if not allowed:
                active_recorder.event("control_refused", "Jackery control is turned off or setup is unavailable.")
                continue
            try:
                expected = jackery_control_value(control, value)
                await active_reader.send_control(control, value)
                properties = await active_reader.read(timeout=max(2.0, interval))
                wire = JACKERY_CONTROLS[control]["wire"]
                raw_readback = properties.get(wire)
                if not jackery_control_readback_matches(control, value, raw_readback):
                    raise ConnectionError(f"status readback did not confirm {wire}={expected}")
                active_recorder.event("control", f"Jackery {control}={value}; status confirmed.")
                latest_properties = properties
            except (ValueError, TimeoutError, ConnectionError, OSError) as exc:
                active_recorder.event("control_unverified", f"Jackery {control}: {exc}")
        return latest_properties

    due = asyncio.get_running_loop().time()
    try:
        while True:
            connecting = reader is None
            lease = (RadioLease(radio_lock_path(store.path.parent))
                     if connecting else contextlib.nullcontext())
            try:
                # Serialize discovery, GATT setup, and the first property read.
                # Healthy persistent sessions run concurrently afterward.
                async with lease:
                    if connecting:
                        # The station advertises only in short windows, so attaching is
                        # the expensive part. Hold the session once it is open and only
                        # rediscover after it actually drops.
                        reader = await discover_reader(60, serial)
                        found = reader.identity.serial
                        if found != serial:
                            await reader.close()
                            raise ValueError(
                                f"Found Jackery {found} over BLE, not the requested {serial}."
                            )
                        print(f"Attached to Jackery {found} over local BLE.", flush=True)
                    properties = await reader.read(timeout=max(2.0, interval))
            except (asyncio.TimeoutError, ConnectionError, OSError) as exc:
                detail = str(exc).strip() or type(exc).__name__
                if reader is None:
                    print(f"Explorer not reachable ({detail}); waiting for it to advertise.", flush=True)
                    if finished():
                        break
                    await asyncio.sleep(interval)
                    due = asyncio.get_running_loop().time()
                    continue
                if recorder is not None:
                    recorder.event(
                        "disconnected",
                        f"Jackery BLE session failed; exception={type(exc).__name__}; "
                        f"detail={detail}; closing session and reattaching.",
                    )
                print(f"Local BLE session lost ({detail}); reattaching.", flush=True)
                close_diagnostics = await reader.close()
                if recorder is not None:
                    for close_detail in close_diagnostics or ():
                        recorder.event(
                            "disconnect_error",
                            f"Jackery BLE cleanup failed; detail={close_detail}",
                        )
                reader = None
                if finished():
                    break
                continue
            measurements = map_properties(properties)
            if not measurements:
                # Recoverable, like a dropped link: log it, keep the recording,
                # and only reacquire once the station has done it repeatedly.
                unmapped_reads += 1
                detail = ("Jackery answered over BLE but returned no dashboard-mapped "
                          f"properties ({unmapped_reads} in a row).")
                print(detail, flush=True)
                if recorder is not None:
                    recorder.event("suspect_telemetry", detail)
                if unmapped_reads >= UNMAPPED_READ_LIMIT:
                    print("Reattaching after repeated unmapped Jackery reads.", flush=True)
                    if recorder is not None:
                        # A segment boundary: the frames either side are not contiguous.
                        recorder.event("disconnected",
                                       "Dropped the Jackery link after repeated unmapped reads.")
                    await reader.close()
                    reader = None
                    unmapped_reads = 0
                    if finished():
                        break
                    continue
                if finished():
                    break
                due += interval
                await asyncio.sleep(max(0.0, due - asyncio.get_running_loop().time()))
                continue
            unmapped_reads = 0
            if recorder is None:
                recorder = Recorder(store, firmware=f"Jackery Explorer 1000 v2 {serial}",
                                    conditions="Jackery local BLE transport",
                                    expected_interval=interval,
                                    # Keep everything: old ordinary frames are thinned
                                    # to one a minute rather than deleted outright.
                                    retain_days=None,
                                    compaction=Compaction(peak_keys=PEAK_KEYS))
            changed = await drain_controls(reader, recorder)
            if changed:
                properties.update(changed)
                measurements = map_properties(properties)
            fields = {"device": {"serial": serial, "model": "Explorer 1000 v2", "transport": "ble"},
                      "properties": properties}
            raw = json.dumps(fields, ensure_ascii=False, sort_keys=True).encode()
            recorder.ingest_observation(raw, fields, measurements, quality="ble_observed")
            print(json.dumps({"serial": serial, "measurements": measurements}, sort_keys=True), flush=True)
            if finished():
                break
            # Sleeping a fixed interval after a read makes the real period
            # read+interval and lets it drift; hold a deadline instead.
            due += interval
            await asyncio.sleep(max(0.0, due - asyncio.get_running_loop().time()))
    except KeyboardInterrupt:
        print("Stopping; saving recording...", flush=True)
    finally:
        if reader is not None:
            await reader.close()
        if recorder:
            recorder.finish()


def do_jackery_compact(root, database, grace_hours=48, bucket_seconds=60, apply_changes=False):
    """Thin ordinary Jackery history; flagged and recent windows are untouched.

    Reports without deleting unless asked to apply, because this is the one
    command in OpenPowerstation whose whole job is destroying recorded frames.
    """
    from .jackery_fields import PEAK_KEYS
    from .storage import Store
    if grace_hours < 1:
        raise ValueError("Keep at least one hour of full-fidelity history.")
    target = database or root / "jackery.sqlite"
    if not target.exists():
        raise ValueError(f"No Jackery recording at {target}.")
    # Store takes the single-writer lock, so a running recorder blocks this
    # rather than the two of them deleting underneath each other.
    with Store(target) as store:
        summary = store.downsample(time.time_ns(), grace_seconds=grace_hours*3600,
                                   bucket_seconds=bucket_seconds, peak_keys=PEAK_KEYS,
                                   dry_run=not apply_changes)
    summary["applied"] = bool(apply_changes)
    print(json.dumps(summary, indent=2, sort_keys=True))
    if not apply_changes and summary["deleted"]:
        print()
        print("Nothing was deleted. Re-run with --apply to remove "
              f"{summary['deleted']} frames.")
    return summary


def do_jackery_record(root, database, interval=3, hours=None, serial=None, stop_file=None):
    """Record Jackery telemetry over local BLE, with no account and no cloud call."""
    from .storage import Store
    if not 1 <= interval <= 3600:
        raise ValueError("Jackery BLE polling interval must be between 1 and 3600 seconds.")
    if not serial:
        raise ValueError("A Jackery serial is required; run jackery-scan to find it.")
    if stop_file and stop_file.exists():
        raise ValueError("Stop file already exists; remove it before starting a new run.")
    target = database or root / "jackery.sqlite"
    # Take the writer lock before touching the radio, so a duplicate invocation
    # fails instead of competing for the station's single BLE client slot.
    with Store(target) as store:
        print(f"Recording Jackery telemetry over local BLE to {target.resolve()}; Ctrl+C to stop.", flush=True)
        asyncio.run(_jackery_ble_loop(store, interval, hours, serial, stop_file,
                                     config_path=root / "config.json"))


def setup(root, login):
    found = asyncio.run(do_scan())
    if not found:
        return
    selection = int(input("Select DP3 number: "))-1
    if not 0 <= selection < len(found):
        raise ValueError("Choose a listed device.")
    identity = found[selection][0]
    region = input("Account region [US/EU/JP/ASIA, default US]: ").strip().upper() or "US"
    if login:
        identifier = input("EcoFlow account email (not saved): ").strip()
        password = getpass.getpass("EcoFlow password (not saved): ")
        try:
            uid = asyncio.run(resolve_user_id(identifier,password,region))
        finally:
            password = identifier = ""
    else:
        uid = getpass.getpass("EcoFlow numeric user ID (saved locally): ").strip()
    cfg = Config(identity.address,identity.serial,uid,region,
                 input("Firmware version from app (optional): ").strip(),
                 input("Operating conditions (optional, private): ").strip())
    save_config(cfg,root/"config.json")
    print("Configuration saved locally. Recording makes no internet calls.")

def desktop_component(module, attribute):
    """Import a desktop-only component, or name the extra that provides it.

    Qt and pyqtgraph live in the optional `gui` extra so a collector or bridge
    installs without them. Reaching a desktop command from a headless install is
    a configuration mistake, not a crash, so it gets the same "OpenPowerstation: ..."
    treatment as any other operator error.

    Evidence export is not routed through here: `exporting` imports cleanly
    without matplotlib and only needs it to render charts, so it guards its own
    import at the point of use and names the `charts` extra instead.
    """
    from importlib import import_module
    try:
        return getattr(import_module(module, __package__), attribute)
    except ModuleNotFoundError as exc:
        raise ValueError(
            f"The desktop commands need the '{exc.name}' package, which is not "
            "installed. Install the desktop extra: pip install 'opendp3[gui]'"
        ) from None

def main(argv=None):
    args = parser().parse_args(argv)
    root = args.data_dir
    # A run started by hand (not through start_all.py / Start-OpenPowerstation.cmd) that
    # omits --stop-file has no way to be told to stop -- Stop-OpenPowerstation.cmd only
    # ever signals these fixed names, the same ones start_all.py passes
    # explicitly. Defaulting to them here means Stop-OpenPowerstation.cmd reaches a
    # manual invocation too, instead of it lingering forever holding the
    # writer lock. See jackery.sqlite.writer.lock incident, 2026-09-04.
    default_stop_files = {"record": "collector.stop", "jackery-record": "jackery.stop",
                           "jackery-bridge": "jackery-bridge.stop"}
    if args.command in default_stop_files and args.stop_file is None:
        args.stop_file = root / default_stop_files[args.command]
    try:
        if args.command == "scan":
            asyncio.run(do_scan(args.seconds))
        elif args.command == "jackery-scan":
            asyncio.run(do_jackery_scan(args.seconds))
        elif args.command == "jackery-status":
            asyncio.run(do_jackery_status(args.seconds))
        elif args.command == "jackery-record":
            if args.hours is not None and args.hours <= 0:
                raise ValueError("Recording hours must be positive.")
            do_jackery_record(root, args.database, args.interval, args.hours, args.serial, args.stop_file)
        elif args.command == "jackery-compact":
            do_jackery_compact(root, args.database, args.grace_hours,
                               args.bucket_seconds, args.apply)
        elif args.command == "jackery-bridge":
            from .jackery_bridge import JackeryBridge
            if not 1 <= args.interval <= 3600:
                raise ValueError("Jackery bridge interval must be between 1 and 3600 seconds.")
            if args.stop_file and args.stop_file.exists():
                raise ValueError("Stop file already exists; remove it before starting a new run.")
            cfg = load_config(root / "config.json")
            worker = JackeryBridge(cfg, args.database or root / "jackery.sqlite",
                                   serial=args.serial, interval=args.interval)
            print("Publishing the distinct Jackery Explorer entity to Home Assistant; Ctrl+C to stop.", flush=True)
            try:
                worker.run(once=args.once, stop_file=args.stop_file)
            except KeyboardInterrupt:
                print("Stopping Jackery bridge...", flush=True)
        elif args.command == "setup":
            setup(root,args.login)
        elif args.command == "record":
            from .runtime import Service
            if args.hours is not None and args.hours <= 0:
                raise ValueError('Recording hours must be positive.')
            if args.stop_file and args.stop_file.exists():
                raise ValueError('Stop file already exists; remove it before starting a new run.')
            cfg = load_config(root/"config.json")
            database = args.database or root/"recordings.sqlite"
            service = Service(cfg,database,config_path=root/"config.json",
                              notify=lambda s:print(f"{s['state']}: {s['detail']}",flush=True))
            service.start()
            deadline = None
            startup_deadline = time.monotonic()+300
            startup_failed = False
            try:
                while service.is_alive():
                    service.join(.25)
                    if args.hours and deadline is None and service.first_connected_at is not None:
                        deadline = service.first_connected_at+args.hours*3600
                    if deadline and time.monotonic()>=deadline:
                        service.stop()
                    if args.hours and deadline is None and time.monotonic()>=startup_deadline:
                        startup_failed = True
                        service.stop()
                    if args.stop_file and args.stop_file.exists():
                        service.stop()
            except KeyboardInterrupt:
                print("Stopping; saving recording...")
                service.stop()
                service.join()
            if service.error:
                raise ValueError(service.error)
            if startup_failed:
                raise ValueError('No authenticated connection within five minutes; timed recording stopped.')
        elif args.command == "replay":
            from .queries import snapshot
            snap = snapshot(args.database,args.session)
            if not snap:
                raise ValueError("No recorded sessions.")
            print(json.dumps({k:v for k,v in snap.items() if k in {"session","coverage","counts","incidents"}},indent=2))
            if args.verify:
                from .validation import verify_recording
                result = verify_recording(args.database,args.session)
                print(json.dumps(result,indent=2))
                if result["mismatches"]:
                    raise ValueError("Replay differed from stored measurements.")
        elif args.command == "export":
            from .exporting import export_evidence
            report,raw = export_evidence(args.database,args.destination,sid=args.session,
                                         incident_id=args.incident,include_raw=args.raw)
            print(report.resolve())
            if raw:
                print(f"PRIVATE raw archive (review before sharing): {raw.resolve()}")
        elif args.command == "demo":
            from .demo import make_demo
            target = args.database or root/"demo"/(datetime.now().strftime("%Y%m%d-%H%M%S")+".sqlite")
            print(make_demo(target).resolve())
        elif args.command == "bridge":
            from .bridge import Bridge, bridge_lock, describe
            cfg = load_config(root/"config.json")
            database = args.database or root/"recordings.sqlite"
            if args.interval is not None and not 1 <= args.interval <= 3600:
                raise ValueError("Publish interval must be between 1 and 3600 seconds.")
            if args.dry_run:
                describe(cfg,database)
            else:
                try:
                    with bridge_lock(database):
                        worker = Bridge(cfg,database,interval=args.interval)
                        print(f"Publishing to the configured broker every {worker.interval:g}s. Ctrl+C to stop.",flush=True)
                        worker.run(once=args.once)
                except portalocker.exceptions.LockException:
                    raise ValueError("A bridge is already publishing; stop it before starting another.") from None
                except KeyboardInterrupt:
                    print("Stopping; marking the device offline...")
        elif args.command == "gui":
            launch = desktop_component(".gui","launch")
            launch(root,args.database)
    except (ValueError,FileNotFoundError,FileExistsError,PermissionError) as exc:
        print(f"OpenPowerstation: {exc}",file=sys.stderr)
        return 1
    return 0

if __name__ == "__main__":
    sys.exit(main())
