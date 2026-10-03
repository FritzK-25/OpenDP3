"""One-step recovery launcher: retake Bluetooth, then republish to Home Assistant.

Written for the after-a-reboot case where the DP3 was off when the machine came
up, so nothing owns Bluetooth and nothing is on the broker. Starting the
collector is most of the work -- runtime.Service already retries a missing
device forever -- so what this adds is an answer to the question a bare launcher
leaves open: did the link actually come back, or is it still hunting?

Re-running is safe. The collector takes the same single-writer lock the desktop
app takes, and the bridge worker takes one of its own, so a second copy of
either refuses to start rather than competing for the adapter or the broker.
"""
from __future__ import annotations

import argparse
import os
import re
from pathlib import Path
import sqlite3
import subprocess
import sys
import threading
import time

import portalocker

ROOT = Path(__file__).resolve().parent.parent
CONNECT_TIMEOUT = 45.0
BRIDGE_TIMEOUT = 15.0
BROKER_CHECK_TIMEOUT = 5.0
JACKERY_SERIAL_ENV = "OPENDP3_JACKERY_SERIAL"
# Poll and publish are deliberately NOT the same rate any more. The recorder and
# the bridge are independent loops, so two equal periods beat against each other
# and add up to a worst case of both. Publishing is nearly free and only reads
# SQLite, so run it faster than the radio and let it pick each frame up promptly;
# the poll rate alone decides how much is written to disk.
JACKERY_POLL_SECONDS = 3
JACKERY_PUBLISH_SECONDS = 1
# No console of its own: both workers outlive this launcher and log to a file.
DETACHED = (getattr(subprocess, "DETACHED_PROCESS", 0)
            | getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0))


def held(lock_path: Path) -> bool:
    """True when another process holds this advisory lock."""
    lock = portalocker.Lock(str(lock_path), timeout=0)
    try:
        lock.acquire()
    except (portalocker.exceptions.LockException, PermissionError, OSError):
        return True
    lock.release()
    return False


def connected_after(database: Path, since_ns: int) -> str | None:
    """Detail of an authenticated session that started at or after ``since_ns``."""
    if not database.exists():
        return None
    from opendp3.storage import read_db
    try:
        with read_db(database) as db:
            row = db.execute(
                "SELECT e.detail AS detail FROM events e JOIN sessions s ON s.id=e.session_id "
                "WHERE e.kind='connected' AND s.start_utc_ns>=? "
                "ORDER BY e.utc_ns DESC LIMIT 1", (since_ns,)).fetchone()
        return row["detail"] if row else None
    except sqlite3.Error:
        # The collector is mid-write. Not an answer; ask again on the next poll.
        return None


def jackery_frame_after(database: Path, since_ns: int) -> bool | None:
    """True once a Jackery cloud frame has landed at or after ``since_ns``."""
    if not database.exists():
        return False
    from opendp3.storage import read_db
    try:
        with read_db(database) as db:
            row = db.execute("SELECT 1 FROM frames WHERE utc_ns>=? LIMIT 1", (since_ns,)).fetchone()
        return row is not None
    except sqlite3.Error:
        # The recorder is mid-write. Not an answer; ask again on the next poll.
        return None


def verify_broker_state(data: Path, *, dp3: bool, jackery: bool,
                        timeout: float = BROKER_CHECK_TIMEOUT) -> dict[str, dict[str, str | None]]:
    """Read back what each bridge has actually retained on the broker.

    A bridge process staying alive and a log line saying it connected both
    proved true the moment they happened -- neither one notices a bridge that
    reconnects later and gets stuck with a stale retained availability topic
    (exactly what happened to the Jackery bridge before it grew an on_connect
    handler). This subscribes fresh and reports what is retained right now.
    """
    result: dict[str, dict[str, str | None]] = {}
    if dp3:
        result["DP3"] = {"availability": None, "telemetry": None}
    if jackery:
        result["Jackery"] = {"availability": None, "telemetry": None}
    if not result:
        return result

    from opendp3.bridge import device_id
    from opendp3.config import load_config
    cfg = load_config(data/"config.json")
    if not cfg.mqtt_host:
        return result

    bases = {}
    if dp3:
        bases["DP3"] = "opendp3/" + device_id(cfg.serial)
    if jackery and jackery_serial():
        bases["Jackery"] = "jackery/" + device_id(jackery_serial())
    topics = {f"{base}/{kind}": (label, kind) for label, base in bases.items()
              for kind in ("availability", "telemetry")}

    import paho.mqtt.client as mqtt

    def on_message(client, userdata, message):
        entry = topics.get(message.topic)
        if entry:
            label, kind = entry
            result[label][kind] = message.payload.decode("utf-8", "replace")

    client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2, client_id="start-all-healthcheck")
    if cfg.mqtt_username:
        client.username_pw_set(cfg.mqtt_username, cfg.mqtt_password or None)
    if cfg.mqtt_tls:
        client.tls_set()
    client.on_message = on_message
    try:
        client.connect(cfg.mqtt_host, cfg.mqtt_port, keepalive=10)
    except OSError:
        return result
    client.loop_start()
    for topic in topics:
        client.subscribe(topic, qos=1)
    deadline = time.monotonic()+timeout
    while time.monotonic() < deadline and any(None in entry.values() for entry in result.values()):
        time.sleep(0.2)
    client.loop_stop()
    client.disconnect()
    return result


def report_broker_state(state: dict[str, dict[str, str | None]]) -> None:
    for label, entry in state.items():
        availability, telemetry = entry["availability"], entry["telemetry"]
        if availability == "online" and telemetry == "online":
            print(f"Broker      {label} confirmed online on the broker.")
        elif availability is None and telemetry is None:
            print(f"Broker      {label} did not respond on the broker within {BROKER_CHECK_TIMEOUT:g}s.")
        else:
            print(f"Broker      {label} stuck: availability={availability!r} telemetry={telemetry!r}.")
            print(f"            Data may still be flowing underneath -- this is the retained-offline")
            print(f"            failure mode. Restart the {label} bridge to clear it.")


def since(log: Path, offset: int) -> str:
    """Whatever this run appended to ``log`` past ``offset`` bytes."""
    try:
        with log.open("rb") as handle:
            handle.seek(offset)
            return handle.read().decode("utf-8", errors="replace")
    except OSError:
        return ""


def tail(log: Path, lines=3) -> str:
    try:
        kept = [line.strip() for line in log.read_text("utf-8", errors="replace").splitlines() if line.strip()]
    except OSError:
        return ""
    return " | ".join(kept[-lines:])


def spawn(args: list[str], log: Path) -> subprocess.Popen:
    log.parent.mkdir(parents=True, exist_ok=True)
    with log.open("a", encoding="utf-8") as handle:
        handle.write(f"\n--- started {time.strftime('%Y-%m-%d %H:%M:%S')} ---\n")
        handle.flush()
        return subprocess.Popen(args, cwd=str(ROOT), stdout=handle, stderr=subprocess.STDOUT,
                                stdin=subprocess.DEVNULL, creationflags=DETACHED, close_fds=True)


def start_collector(data: Path, python: Path) -> str:
    database = data/"recordings.sqlite"
    if held(Path(str(database)+".writer.lock")):
        return "already"
    stop = data/"collector.stop"
    # A stop file left by the previous run would stop this one immediately.
    stop.unlink(missing_ok=True)
    spawn([str(python), "-m", "opendp3", "--data-dir", str(data), "record",
           "--stop-file", str(stop)], data/"collector.log")
    return "started"


def start_bridge(data: Path, python: Path) -> str:
    if held(data/"bridge.lock"):
        return "already"
    (data/"bridge.stop").unlink(missing_ok=True)
    log = data/"bridge.log"
    mark = log.stat().st_size if log.exists() else 0
    proc = spawn([str(python), str(Path(__file__).resolve()), "--bridge-worker",
                  "--data-dir", str(data)], log)
    # Watch the worker's own output rather than polling its lock: acquiring that
    # lock to test it, however briefly, is exactly what would make the worker fail.
    deadline = time.monotonic()+BRIDGE_TIMEOUT
    while time.monotonic() < deadline:
        if "Publishing every" in since(log, mark):
            return "started"
        if proc.poll() is not None:
            return "failed"
        time.sleep(0.3)
    return "failed"


def jackery_serial() -> str | None:
    """The Jackery serial from the environment, or None when none is configured."""
    serial = os.environ.get(JACKERY_SERIAL_ENV, "")
    return serial if re.fullmatch(r"[0-9]{15}", serial) else None


def start_jackery_recorder(data: Path, python: Path) -> str:
    serial = jackery_serial()
    if serial is None:
        return "failed"
    database = data/"jackery.sqlite"
    if held(Path(str(database)+".writer.lock")):
        return "already"
    stop = data/"jackery.stop"
    stop.unlink(missing_ok=True)
    # Local BLE only. There is no account to expire and no cloud to be down, so
    # this cannot fail the way the retired cloud recorder did.
    spawn([str(python), "-m", "opendp3", "--data-dir", str(data), "jackery-record",
           "--serial", serial, "--interval", str(JACKERY_POLL_SECONDS), "--stop-file", str(stop)],
          data/"jackery-recorder.log")
    return "started"


def start_jackery_bridge(data: Path, python: Path) -> str:
    if jackery_serial() is None:
        return "failed"
    if held(data/"jackery-bridge.lock"):
        return "already"
    (data/"jackery-bridge.stop").unlink(missing_ok=True)
    log = data/"jackery-bridge.log"
    mark = log.stat().st_size if log.exists() else 0
    proc = spawn([str(python), str(Path(__file__).resolve()), "--jackery-bridge-worker",
                  "--data-dir", str(data)], log)
    deadline = time.monotonic()+BRIDGE_TIMEOUT
    while time.monotonic() < deadline:
        if "Publishing Jackery every" in since(log, mark):
            return "started"
        if proc.poll() is not None:
            return "failed"
        time.sleep(0.3)
    return "failed"


def bridge_worker(data: Path) -> int:
    """The bridge itself: single-instance, stoppable by file, run in this process."""
    from opendp3.bridge import Bridge
    from opendp3.config import load_config
    lock = portalocker.Lock(str(data/"bridge.lock"), timeout=0)
    try:
        lock.acquire()
    except portalocker.exceptions.LockException:
        print("A bridge is already publishing; this one is not needed.", file=sys.stderr)
        return 1
    stop = data/"bridge.stop"
    try:
        worker = Bridge(load_config(data/"config.json"), data/"recordings.sqlite")

        def watch():
            # Announce only once the broker has actually accepted us, so the
            # launcher never reports success against a host that silently hangs.
            announced = False
            while not worker.stopped.wait(0.25):
                if stop.exists():
                    worker.stop()
                    return
                if not announced and worker.client is not None and worker.client.is_connected():
                    print(f"Publishing every {worker.interval:g}s. Stop with Stop-OpenDP3.cmd.",
                          flush=True)
                    announced = True

        threading.Thread(target=watch, daemon=True).start()
        worker.run()
    except (ValueError, FileNotFoundError, TypeError) as exc:
        print(f"OpenDP3: {exc}", file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        pass
    finally:
        lock.release()
    return 0


def jackery_bridge_worker(data: Path) -> int:
    """The Jackery bridge itself: single-instance, stoppable by file, run in this process."""
    from opendp3.jackery_bridge import JackeryBridge
    from opendp3.config import load_config
    lock = portalocker.Lock(str(data/"jackery-bridge.lock"), timeout=0)
    try:
        lock.acquire()
    except portalocker.exceptions.LockException:
        print("A Jackery bridge is already publishing; this one is not needed.", file=sys.stderr)
        return 1
    stop = data/"jackery-bridge.stop"
    try:
        worker = JackeryBridge(load_config(data/"config.json"), data/"jackery.sqlite",
                                serial=jackery_serial(), interval=JACKERY_PUBLISH_SECONDS)

        def watch():
            # Same announce-once-connected shape as the DP3 bridge watcher above,
            # minus the stop plumbing: JackeryBridge.run() polls the stop file itself.
            announced = False
            while not announced:
                if worker.client is not None and worker.client.is_connected():
                    print(f"Publishing Jackery every {worker.interval:g}s. Stop with Stop-OpenDP3.cmd.",
                          flush=True)
                    announced = True
                time.sleep(0.25)

        threading.Thread(target=watch, daemon=True).start()
        worker.run(stop_file=stop)
    except (ValueError, FileNotFoundError, TypeError) as exc:
        print(f"Jackery bridge: {exc}", file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        pass
    finally:
        lock.release()
    return 0


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Start local recording and the Home Assistant bridge")
    ap.add_argument("--data-dir", type=Path, default=ROOT/"data")
    ap.add_argument("--no-bridge", action="store_true", help="Record only; leave Home Assistant alone")
    ap.add_argument("--no-jackery", action="store_true", help=f"Skip the Jackery recorder and bridge (also skipped when {JACKERY_SERIAL_ENV} is unset)")
    ap.add_argument("--gui", action="store_true", help="Also open the desktop viewer")
    ap.add_argument("--bridge-worker", action="store_true", help=argparse.SUPPRESS)
    ap.add_argument("--jackery-bridge-worker", action="store_true", help=argparse.SUPPRESS)
    args = ap.parse_args(argv)
    data = args.data_dir.resolve()
    if not args.bridge_worker and not args.jackery_bridge_worker and jackery_serial() is None:
        args.no_jackery = True
    if args.bridge_worker:
        return bridge_worker(data)
    if args.jackery_bridge_worker:
        return jackery_bridge_worker(data)

    # pythonw, not python: DETACHED_PROCESS only reaches the interpreter we
    # launch, and in this venv that is a stub which re-execs the base
    # interpreter as its own child. The flag does not survive that hop, so the
    # process doing the real work was being handed a fresh console -- a visible
    # window whose closure killed the recorder and released Bluetooth.
    # pythonw is a GUI-subsystem binary and never allocates a console, so it
    # stays windowless across the re-exec. stdio is redirected to the logs
    # below, which pythonw handles normally.
    python = Path(sys.executable)
    interactive_python = python  # has a console; for messages a human will run by hand
    windowless = python.with_name("pythonw.exe")
    if windowless.exists():
        python = windowless
    config = data/"config.json"
    if not config.exists():
        print(f"OpenDP3: no device configured yet ({config}). Run setup in the desktop app first.",
              file=sys.stderr)
        return 1

    started_ns = time.time_ns()
    state = start_collector(data, python)
    if state == "already":
        print("Recorder    already running; leaving it alone.")
    else:
        print("Recorder    starting; waiting for an authenticated link...")
        deadline = time.monotonic()+CONNECT_TIMEOUT
        detail = None
        while detail is None and time.monotonic() < deadline:
            time.sleep(1.0)
            detail = connected_after(data/"recordings.sqlite", started_ns)
        if detail:
            print(f"Recorder    connected. {detail}")
        else:
            print(f"Recorder    running but not connected yet after {CONNECT_TIMEOUT:g}s.")
            print("            It retries forever, so it will attach on its own once the")
            print("            DP3 is powered on and no phone holds its Bluetooth session.")
            note = tail(data/"collector.log")
            if note:
                print(f"            Last log: {note}")

    if not args.no_bridge:
        state = start_bridge(data, python)
        if state == "already":
            print("Bridge      already publishing; leaving it alone.")
        elif state == "started":
            print("Bridge      publishing to the configured broker.")
        else:
            print("Bridge      did not start.")
            note = tail(data/"bridge.log")
            if note:
                print(f"            Last log: {note}")

    if not args.no_jackery:
        jackery_started_ns = time.time_ns()
        state = start_jackery_recorder(data, python)
        if state == "already":
            print("Jackery rec already running; leaving it alone.")
        else:
            print("Jackery rec starting; waiting for a local BLE reading...")
            deadline = time.monotonic()+CONNECT_TIMEOUT
            seen = None
            while not seen and time.monotonic() < deadline:
                time.sleep(1.0)
                seen = jackery_frame_after(data/"jackery.sqlite", jackery_started_ns)
            if seen:
                print("Jackery rec connected.")
            else:
                # The station stops advertising once it rejoins its saved Wi-Fi, so a
                # slow first attach is expected rather than a failure to report.
                print(f"Jackery rec no reading yet after {CONNECT_TIMEOUT:g}s.")
                print("            The station may not be advertising yet; the recorder")
                print("            keeps retrying and attaches on its own once it does.")
                note = tail(data/"jackery-recorder.log")
                if note:
                    print(f"            Last log: {note}")

        if not args.no_bridge:
            state = start_jackery_bridge(data, python)
            if state == "already":
                print("Jackery brg already publishing; leaving it alone.")
            elif state == "started":
                print("Jackery brg publishing to the configured broker.")
            else:
                print("Jackery brg did not start.")
                note = tail(data/"jackery-bridge.log")
                if note:
                    print(f"            Last log: {note}")

    if args.gui:
        viewer = python.with_name("pythonw.exe")
        spawn([str(viewer if viewer.exists() else python), "-m", "opendp3",
               "--data-dir", str(data), "gui"], data/"viewer.log")
        print("Viewer      opening (read-only while the headless recorder owns Bluetooth).")

    if not args.no_bridge:
        want_jackery = not args.no_jackery
        report_broker_state(verify_broker_state(data, dp3=True, jackery=want_jackery))

    print(f"\nLogs in {data}. Stop everything with Stop-OpenDP3.cmd.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
