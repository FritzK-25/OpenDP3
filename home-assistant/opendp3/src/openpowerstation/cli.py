"""Public command line interface; credentials never appear in arguments."""
import argparse
import asyncio
from datetime import datetime
import getpass
import json
from pathlib import Path
import sys
import time

from .config import Config, data_dir, load_config, resolve_user_id, save_config

# Consecutive unproductive Jackery polls before the BLE session is dropped and
# reacquired. A poll is unproductive when the station leaves the status query
# unanswered, or answers without any of its core telemetry
# (jackery_fields.JACKERY_CORE_KEYS) -- a settings page, a mode change, a
# firmware quirk. Any mapped field used to count, so a station answering only
# settings kept its session for ever while SOC and power aged out. One of either
# is not evidence the link is bad, and reacquiring is the expensive step: the
# station advertises only in short windows and may stop for hours once it
# rejoins Wi-Fi. Five polls is about 15 seconds at the default rate, inside the
# bridge's 30-second stale threshold. A GATT write that fails or misses its
# deadline is evidence, and still reattaches at once. Exiting the process on
# the first unmapped read once cost a supervisor restart plus a full
# rediscovery, and the EcoFlow collector likewise rides out one silent round
# before it reconnects.
UNPRODUCTIVE_POLL_LIMIT = 5

# While the station cannot be reached, how often the search is written to the
# recording and the log: the first failed attach, and the first of any other
# kind of failure, at once; otherwise one summary with the running count per
# this many seconds. An absent Explorer is searched for about once a minute and
# can stay away for hours, and a line per attempt would bury the rest of the log.
ATTACH_REPORT_SECONDS = 600.0

# The stop file each long-running worker watches in the data directory, by the
# command that runs it. main() gives a run without --stop-file its name here;
# Stop-OpenPowerstation.cmd writes all of them, start_all.py clears and passes them,
# watchdog.py writes one to restart a single worker, and the HAOS app clears
# those its children watch (home-assistant/opendp3/run.py). The DP3 bridge command has
# no --stop-file: start_all.py runs that bridge in its own process and watches
# bridge.stop for it. tests/test_stop_files.py holds every copy to these names.
STOP_FILES = {
    "record": "collector.stop",
    "jackery-record": "jackery.stop",
    "jackery-bridge": "jackery-bridge.stop",
    "bridge": "bridge.stop",
}

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
    jackery_ble_record.add_argument("--serial", required=True,
        help="The Explorer's 15-digit serial, as jackery-scan shows it; every other station is ignored")
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
    jackery_bridge.add_argument("--serial", required=True,
        help="The Explorer's 15-digit serial; only its recorded sessions are published")
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

async def _jackery_ble_loop(store, interval, hours, serial, stop_file, *, config_path=None,
                            discover=None):
    """Record one Explorer until ``stop_file`` appears, ``hours`` pass or a failure.

    ``discover`` opens a session with the station: jackery.discover_reader,
    or a caller's wrapper of it, called as it is (timeout, serial=, lease=).
    The HAOS app's worker passes one that first releases a link BlueZ still
    holds (home-assistant/opendp3/linux_worker.py).
    """
    from collections import Counter
    from . import control_queue
    from .bridge import device_id
    from .config import control_allowed
    from .health import Thresholds, configured
    from .jackery import (JACKERY_CONTROLS, StationSilent, discover_reader,
                     jackery_control_readback_matches, jackery_control_value)
    from .jackery_fields import JACKERY_CORE_KEYS, PEAK_KEYS, map_properties
    from .radio import RadioLease, radio_lock_path
    from .recorder import Compaction, Recorder
    if discover is None:
        discover = discover_reader
    config_path = config_path or store.path.parent / "config.json"
    # config.json's anomaly settings reach this recorder as they reach the
    # DP3's. The Explorer needs no DP3 identity, so without a usable
    # config.json it still records, by the defaults.
    try:
        health = configured(load_config(config_path))
    except ValueError:
        health = Thresholds()
    loop = asyncio.get_running_loop()
    start = time.monotonic()
    handle = device_id(serial)
    # Opened before the station is found, as the EcoFlow collector opens its
    # own: a search that lasts hours belongs to this recording. Opened on the
    # first reading instead, a collector restarted while the Explorer was away
    # left nothing behind for as long as it searched, and the bridge went on
    # publishing the finished session before it as "stopped".
    recorder = Recorder(store, firmware=f"Jackery Explorer 1000 v2 {serial}",
                        conditions="Jackery local BLE transport",
                        expected_interval=interval,
                        # Keep everything: old ordinary frames are thinned
                        # to one a minute rather than deleted outright.
                        retain_days=None, health=health,
                        compaction=Compaction(peak_keys=PEAK_KEYS))
    reader = None
    # The current run of unproductive polls, split by kind for the record.
    silent_polls = incomplete_polls = 0
    # The search under way while no session is open: when it began, its failed
    # attaches by exception, and when it was last reported.
    searching_since = start
    failures = Counter()
    reported = None
    # Whether this session has delivered telemetry yet. The log names that
    # change; the readings themselves are in the recording and on MQTT.
    flowing = False
    failed = False

    def say(message):
        """One add-on log line, naming the Explorer by its handle.

        The same SHA-256 handle the bridges publish under. Detail passed up
        from discovery can carry the serial, so it is replaced here rather than
        left to every message to avoid.
        """
        print(str(message).replace(serial, handle), flush=True)

    def search_cost():
        attempts = sum(failures.values())
        waited = f"{time.monotonic() - searching_since:.0f}s"
        if not attempts:
            return f"after {waited}"
        tally = ", ".join(f"{kind}={count}" for kind, count in sorted(failures.items()))
        return f"after {attempts} failed attempt{'' if attempts == 1 else 's'} in {waited} ({tally})"

    def search_failed(exc, detail):
        """Count one failed attach; report the first of each kind, then a summary."""
        nonlocal reported
        kind = type(exc).__name__
        failures[kind] += 1
        now = time.monotonic()
        if (failures[kind] > 1 and reported is not None
                and now - reported < ATTACH_REPORT_SECONDS):
            return
        reported = now
        cost = search_cost()
        # The Explorer not advertising, BlueZ refusing, the radio lease: which
        # of them kept this gap open belongs in the recording, not only in the
        # process log, and so does the search still running hours later.
        recorder.event("connection_failed",
                       f"Jackery BLE attach failed; exception={kind}; detail={detail}; "
                       f"still searching {cost}.")
        say(f"Explorer not reachable ({detail}); still searching {cost}.")

    def attached():
        """Record the session opening, and what the search before it cost."""
        nonlocal reported, flowing
        cost = search_cost()
        # A segment boundary: frames after an outage start afresh.
        recorder.event("connected", f"Jackery BLE session open {cost}.")
        say(f"Attached to Jackery {handle} over local BLE {cost}.")
        failures.clear()
        reported = None
        flowing = False

    def read_now():
        """The (UTC, monotonic) time a reply was read, which its frame carries."""
        return time.time_ns(), time.monotonic_ns()

    def keep(properties, measurements, read_at):
        """Record one reply as the station sent it, with whatever it mapped to.

        Stamped ``read_at``, when it was read, not when it is stored: a command
        can run between the two, and a reading must never appear to come after
        a command it preceded.
        """
        fields = {"device": {"serial": serial, "model": "Explorer 1000 v2", "transport": "ble"},
                  "properties": properties}
        raw = json.dumps(fields, ensure_ascii=False, sort_keys=True).encode()
        utc_ns, mono_ns = read_at
        recorder.ingest_observation(raw, fields, measurements, utc_ns=utc_ns, mono_ns=mono_ns,
                                    quality="ble_observed")

    def finished():
        return ((hours is not None and time.monotonic() - start >= hours * 3600)
                or bool(stop_file and stop_file.exists()))

    async def drain_controls(active_reader, active_recorder):
        """Consume queued Jackery control requests and verify each one by readback.

        Each readback is kept as a frame of its own, stamped when it was read,
        whether or not it confirms the command: after a command the station
        did not apply, its answer is the evidence of what it did instead.
        """
        directory = store.path.parent / control_queue.JACKERY_DIRECTORY
        if not directory.is_dir() or active_reader is None or active_recorder is None:
            return
        for entry in control_queue.drain(directory):
            control, value = entry.key, entry.value
            if control not in JACKERY_CONTROLS:
                active_recorder.event("control_refused", "Jackery unsupported control request.")
                continue
            if isinstance(entry, control_queue.Refusal):
                active_recorder.event("control_refused", f"Jackery {control}: {entry.reason}.")
                continue
            # Taken now, for this request alone: each one before it in this
            # drain spent a write and a status readback on the radio.
            lapsed = entry.lapsed()
            if lapsed:
                active_recorder.event("control_refused", f"Jackery {control}: {lapsed}.")
                continue
            allowed = control_allowed(config_path)
            active_reader.allow_control = allowed
            if not allowed:
                active_recorder.event("control_refused", "Jackery control is turned off or setup is unavailable.")
                continue
            try:
                expected = jackery_control_value(control, value)
                await active_reader.send_control(control, value)
            except PermissionError as exc:
                # Refused before the radio, by the reader's single-use
                # authority or its write-site gate: nothing was sent.
                active_recorder.event("control_refused", f"Jackery {control}: {exc}")
                continue
            except (ValueError, TimeoutError, ConnectionError, OSError) as exc:
                active_recorder.event("control_unverified", f"Jackery {control}: {exc}")
                continue
            try:
                properties = await active_reader.read(timeout=max(2.0, interval))
                read_at = read_now()
                keep(properties, map_properties(properties), read_at)
                wire = JACKERY_CONTROLS[control]["wire"]
                raw_readback = properties.get(wire)
                if not jackery_control_readback_matches(control, value, raw_readback):
                    raise ConnectionError(f"status readback did not confirm {wire}={expected}")
                active_recorder.event("control", f"Jackery {control}={value}; status confirmed.")
            except (ValueError, TimeoutError, ConnectionError, OSError) as exc:
                active_recorder.event("control_unverified", f"Jackery {control}: {exc}")

    async def drop_session(event_detail, log_detail):
        """Close the session and record why; the next pass rediscovers."""
        nonlocal reader, silent_polls, incomplete_polls, searching_since
        # A segment boundary: the frames either side are not contiguous.
        recorder.event("disconnected", event_detail)
        say(log_detail)
        close_diagnostics = await reader.close()
        for close_detail in close_diagnostics or ():
            recorder.event(
                "disconnect_error",
                f"Jackery BLE cleanup failed; detail={close_detail}",
            )
        reader = None
        silent_polls = incomplete_polls = 0
        searching_since = time.monotonic()

    async def wait_for_next_poll():
        """Sleep to the next poll deadline, skipping any already missed.

        Sleeping a fixed interval after a read makes the real period
        read+interval and lets it drift; hold a deadline instead. A deadline
        that has already passed -- a slow read, a control readback, a host
        suspend -- is dropped rather than replayed, or the missed polls would
        reach the station back to back.
        """
        nonlocal due
        due = max(due + interval, loop.time())
        await asyncio.sleep(max(0.0, due - loop.time()))

    due = loop.time()
    try:
        while True:
            properties = None
            try:
                if reader is None:
                    # The station advertises only in short windows, so attaching is
                    # the expensive part. Hold the session once it is open and only
                    # rediscover after it fails, or stays unproductive (see
                    # UNPRODUCTIVE_POLL_LIMIT).
                    #
                    # The search runs outside the shared adapter lease: it lasts up
                    # to a minute at a time for as long as the station stays away,
                    # which can be hours. Discovery takes the lease only to connect
                    # once our Explorer has advertised. A poll on an open session,
                    # the first one included, is the same GATT traffic a healthy
                    # session sends beside the DP3's recovery, so it runs outside too.
                    opened = await discover(
                        60, serial=serial, lease=RadioLease(radio_lock_path(store.path.parent)))
                    found = opened.identity.serial
                    if found != serial:
                        # discover_reader() ignores other Explorers, so only a
                        # wrapper that lost the serial reaches this: a bug, not a
                        # condition to retry against a neighbour's station.
                        await opened.close()
                        raise ValueError(
                            f"Found Jackery {device_id(str(found))} over BLE, "
                            f"not the requested {handle}."
                        )
                    reader = opened
                    attached()
                    # A new session starts a new schedule: the polls missed
                    # while the link was down are not owed to the station.
                    due = loop.time()
                properties = await reader.read(timeout=max(2.0, interval))
                read_at = read_now()
            except StationSilent as exc:
                # Nothing failed; the station did not answer this one query.
                # Keep the session and poll again at the next deadline.
                silent_polls += 1
                reason = str(exc).strip()
                say(f"Jackery left a status query unanswered ({silent_polls + incomplete_polls} "
                    f"unproductive polls in a row): {reason}")
                if silent_polls == 1:
                    # Once per unproductive run, as the EcoFlow collector does.
                    recorder.event("silence", f"Jackery left a status query unanswered; {reason}")
            except (asyncio.TimeoutError, ConnectionError, OSError) as exc:
                detail = str(exc).strip() or type(exc).__name__
                if reader is None:
                    search_failed(exc, detail)
                    if finished():
                        break
                    await asyncio.sleep(interval)
                    continue
                await drop_session(
                    f"Jackery BLE session failed; exception={type(exc).__name__}; "
                    f"detail={detail}; closing session and reattaching.",
                    f"Local BLE session lost ({detail}); reattaching.",
                )
                if finished():
                    break
                continue
            measurements = {} if properties is None else map_properties(properties)
            productive = any(key in measurements for key in JACKERY_CORE_KEYS)
            if properties is not None and not productive:
                incomplete_polls += 1
                reason = (f"Jackery answered over BLE without its core telemetry "
                          f"({', '.join(JACKERY_CORE_KEYS)}); "
                          f"{silent_polls + incomplete_polls} unproductive polls in a row.")
                say(reason)
                # Kept as the station sent it, and pinned so thinning never
                # removes it or its context: the reply that explains an anomaly
                # is exactly the one worth keeping.
                keep(properties, measurements, read_at)
                recorder.event("suspect_telemetry", reason, pin=True)
            if not productive:
                # Recoverable, like a dropped link: log it, keep the recording,
                # and only reacquire once the station has done it repeatedly.
                polls = silent_polls + incomplete_polls
                if polls >= UNPRODUCTIVE_POLL_LIMIT:
                    await drop_session(
                        f"Jackery BLE session unproductive for {polls} polls in a row "
                        f"({silent_polls} unanswered, {incomplete_polls} without core telemetry); "
                        f"last={reason}; closing session and reattaching.",
                        f"Reattaching after {polls} unproductive Jackery polls in a row.",
                    )
                    if finished():
                        break
                    continue
                if finished():
                    break
                await wait_for_next_poll()
                continue
            resumed = silent_polls + incomplete_polls
            silent_polls = incomplete_polls = 0
            # Stored before any queued command runs, so the frames and the
            # control events land in the order they happened; each readback
            # becomes a frame of its own (drain_controls).
            keep(properties, measurements, read_at)
            await drain_controls(reader, recorder)
            # A line when telemetry starts or comes back, never one per poll:
            # printed every poll, the readings were nearly all of the add-on
            # log, and pushed the lines that explain an outage out of its view
            # within minutes.
            if not flowing:
                core = " ".join(f"{key}={measurements[key]:g}"
                                for key in JACKERY_CORE_KEYS if key in measurements)
                say(f"Jackery telemetry flowing on this session: {core}.")
            elif resumed:
                say(f"Jackery telemetry resumed after {resumed} unproductive polls in a row.")
            flowing = True
            if finished():
                break
            await wait_for_next_poll()
    except Exception:
        # Leaving on an exception is a failure, not an operator stop, and the
        # recording has to say which one it was. An operator stop arrives as
        # a cancellation, which is not an Exception; see do_jackery_record.
        failed = True
        raise
    finally:
        if reader is not None:
            await reader.close()
        recorder.finish("error" if failed else "stopped")


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


def do_jackery_record(root, database, interval=3, hours=None, *, serial, stop_file=None,
                      discover=None):
    """Record Jackery telemetry over local BLE, with no account and no cloud call.

    ``serial`` names the Explorer; there is no default station. ``discover``
    is passed to _jackery_ble_loop; None means the package's own.
    """
    from .config import VIEWER_REFUSAL, install_role
    from .storage import Store
    if not 1 <= interval <= 3600:
        raise ValueError("Jackery BLE polling interval must be between 1 and 3600 seconds.")
    if not serial:
        raise ValueError("A Jackery serial is required; run jackery-scan to find it.")
    if stop_file and stop_file.exists():
        raise ValueError("Stop file already exists; remove it before starting a new run.")
    # Before the store and the radio, as runtime.Service does for the DP3.
    # This collector needs no config.json, so it reads only the role.
    if install_role(root / "config.json") != "collector":
        raise ValueError(VIEWER_REFUSAL)
    target = database or root / "jackery.sqlite"
    # Take the writer lock before touching the radio, so a duplicate invocation
    # fails instead of competing for the station's single BLE client slot.
    with Store(target) as store:
        print(f"Recording Jackery telemetry over local BLE to {target.resolve()}; Ctrl+C to stop.", flush=True)
        try:
            asyncio.run(_jackery_ble_loop(store, interval, hours, serial, stop_file,
                                         config_path=root / "config.json", discover=discover))
        except KeyboardInterrupt:
            # Ctrl+C, or the SIGINT the add-on supervisor stops a collector
            # with. asyncio.run cancels the loop, which closes the session and
            # saves the recording on its way out, then raises this. It is an
            # ordinary stop; left uncaught it ended every lease restart and app
            # stop in a traceback that read as a crash.
            print("Stopped; Jackery recording saved.", flush=True)


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
            "installed. Install the desktop extra: pip install 'openpowerstation[gui]'"
        ) from None

def main(argv=None, *, scan=None, discover=None):
    """Run one command. ``scan`` and ``discover`` replace how the record and
    jackery-record collectors find their station -- ble.scan and
    jackery.discover_reader when None. The HAOS app's worker passes wrappers
    that first release a link BlueZ still holds."""
    args = parser().parse_args(argv)
    root = args.data_dir
    # A run started by hand (not through start_all.py / Start-OpenPowerstation.cmd) that
    # omits --stop-file has no way to be told to stop -- Stop-OpenPowerstation.cmd only
    # ever signals these fixed names, the same ones start_all.py passes
    # explicitly. Defaulting to them here means Stop-OpenPowerstation.cmd reaches a
    # manual invocation too, instead of it lingering forever holding the
    # writer lock. See jackery.sqlite.writer.lock incident, 2026-09-04.
    # Only commands with a --stop-file option have the attribute at all.
    if getattr(args, "stop_file", False) is None:
        args.stop_file = root / STOP_FILES[args.command]
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
            do_jackery_record(root, args.database, args.interval, args.hours, serial=args.serial,
                              stop_file=args.stop_file, discover=discover)
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
                              notify=lambda s:print(f"{s['state']}: {s['detail']}",flush=True),
                              scan=scan)
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
            from .bridge import Bridge, describe
            cfg = load_config(root/"config.json")
            database = args.database or root/"recordings.sqlite"
            if args.interval is not None and not 1 <= args.interval <= 3600:
                raise ValueError("Publish interval must be between 1 and 3600 seconds.")
            if args.dry_run:
                describe(cfg,database)
            else:
                # run() takes the single-instance lock, and refuses with a
                # ValueError while another bridge holds it.
                try:
                    worker = Bridge(cfg,database,interval=args.interval)
                    print(f"Publishing to the configured broker every {worker.interval:g}s. Ctrl+C to stop.",flush=True)
                    worker.run(once=args.once)
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
