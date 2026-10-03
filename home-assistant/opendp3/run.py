"""HAOS freshness supervisor; private settings/data live outside the image."""
from dataclasses import asdict, dataclass
import json
import os
from pathlib import Path
import re
import signal
import sqlite3
import subprocess
import sys
import threading
import time

PROVENANCE_PATH = Path("/opt/opendp3/SOURCE-PROVENANCE.json")


def log_provenance():
    try:
        provenance = json.loads(PROVENANCE_PATH.read_text("utf-8"))
        repository = provenance.get("repository", "unknown")
        version = provenance.get("version", "unknown")
        digest = str(provenance.get("source_sha256", "unknown"))[:12]
        print(f"OpenDP3 source: {repository} {version} (sha256 {digest})", flush=True)
    except Exception:
        print("OpenDP3 source provenance unavailable.", flush=True)


# How long a collector may go without a valid decoded frame before this
# supervisor restarts that one process.
#
# This is the last-resort backstop for a worker that is alive but permanently
# dead. In-process recovery already handles ordinary stalls, so the only job
# left here is to outlast every gap a collector recovers from by itself -- a
# lease below that does not catch a zombie sooner, it just restarts a healthy
# collector and costs a rediscovery.
#
# Measured from the recorded sessions, gap between valid measurement frames
# (the same selection newest_frame() makes):
#
#   DP3      4 healthy sessions, 77h   p99  1.1s   max  75.9s
#            degraded session 09-19    p99  ---    max 251.1s  (recovered)
#   Jackery  3 healthy sessions, 11h   p99  3.1s   max  38.4s
#            degraded session 09-19    p99 104.0s  max 313.0s  (recovered)
#
# Both leases therefore sit above 313s. The previous 60s Jackery lease was
# below even its own p99 during the degraded window and restarted a collector
# that was still delivering; the restart made the next gap worse, not shorter.
ECOFLOW_FRAME_LEASE = 420.0
JACKERY_FRAME_LEASE = 420.0
FRESHNESS_CHECK_SECONDS = 5.0
RADIO_RECOVERY_SPACING = 15.0
RECOVERY_GRACE_SECONDS = 10.0


@dataclass(frozen=True)
class FreshnessPolicy:
    database: Path
    max_age: float
    adapter: str


@dataclass
class FreshnessState:
    baseline_id: int | None
    last_frame_mono_ns: int | None
    armed: bool = False
    # When this process was started, while it may still be held to the lease
    # before recording anything. None once that start-up allowance is spent.
    started_mono_ns: int | None = None


def adapter_name(value) -> str:
    adapter = str(value or "hci0").strip()
    if not re.fullmatch(r"hci[0-9]+", adapter):
        raise ValueError("Bluetooth adapter must be hci followed by digits.")
    return adapter


def newest_frame(database: Path) -> tuple[int, int] | None:
    if not database.exists():
        return None
    connection = None
    try:
        uri = f"file:{database.as_posix()}?mode=ro"
        connection = sqlite3.connect(uri, uri=True, timeout=0.2)
        row = connection.execute(
            "SELECT f.id,f.mono_ns FROM frames f "
            "WHERE f.status NOT IN ('pending','invalid_packet','repeated_unverified') "
            "AND EXISTS (SELECT 1 FROM measurements m WHERE m.frame_id=f.id) "
            "ORDER BY f.id DESC LIMIT 1"
        ).fetchone()
        return (int(row[0]), int(row[1])) if row else None
    except (OSError, sqlite3.Error, TypeError, ValueError):
        return None
    finally:
        if connection is not None:
            connection.close()


def job_environments(options) -> dict[str, dict[str, str]]:
    return {
        "ecoflow-collector": {
            "OPENDP3_BLE_ADAPTER": adapter_name(options.get("ecoflow_adapter", "hci0")),
        },
        "jackery-collector": {
            "OPENDP3_BLE_ADAPTER": adapter_name(options.get("jackery_adapter", "hci0")),
        },
    }


def freshness_policies(options, data: Path) -> dict[str, FreshnessPolicy]:
    policies = {}
    environments = job_environments(options)
    if options.get("ecoflow_enabled", True):
        policies["ecoflow-collector"] = FreshnessPolicy(
            data / "recordings.sqlite",
            ECOFLOW_FRAME_LEASE,
            environments["ecoflow-collector"]["OPENDP3_BLE_ADAPTER"],
        )
    if options.get("jackery_enabled", True):
        policies["jackery-collector"] = FreshnessPolicy(
            data / "jackery.sqlite",
            JACKERY_FRAME_LEASE,
            environments["jackery-collector"]["OPENDP3_BLE_ADAPTER"],
        )
    return policies


def prepare(data: Path, configuration: Path, options):
    from opendp3.config import load_config

    # Use a dedicated app config mount, never the full HA configuration tree.
    cfg = load_config(configuration / "import.json")
    if not cfg.mqtt_host:
        raise ValueError("A local MQTT broker must be configured.")
    # Control is opt-in: it stays off unless the app option turns it on.
    # Old queued commands are never imported, and bridges reject retained MQTT
    # messages before they reach the BLE workers.
    cfg.allow_control = bool(options.get("allow_control", False))
    data.mkdir(parents=True, exist_ok=True)
    temp = data / "config.tmp"
    fd = os.open(temp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as stream:
        json.dump(asdict(cfg), stream)
        stream.flush()
        os.fsync(stream.fileno())
    os.replace(temp, data / "config.json")
    # These are app-owned transient shutdown signals, not user databases.
    for name in ("collector.stop", "jackery.stop", "jackery-bridge.stop"):
        (data / name).unlink(missing_ok=True)


def commands(options, data: Path):
    prefix = [sys.executable, "-u", "-m", "opendp3", "--data-dir", str(data)]
    worker = [sys.executable, "-u", "/opt/opendp3/linux_worker.py",
              "--data-dir", str(data)]
    jobs = {}
    collectors = {}
    # Discovery is structural and does not depend on a live BLE session. Queue
    # every enabled MQTT publisher first, then the radio workers, so Home
    # Assistant can restore entities before either collector starts reacquiring.
    if options.get("ecoflow_enabled", True):
        jobs["ecoflow-mqtt"] = prefix + ["bridge"]
        collectors["ecoflow-collector"] = worker + ["record"]
    # A fresh app has no Jackery serial, so missing legacy options default this
    # collector off rather than making the nominal package configuration invalid.
    if options.get("jackery_enabled", False):
        serial = options.get("jackery_serial", "")
        if not isinstance(serial, str) or not re.fullmatch(r"[0-9]{15}", serial):
            raise ValueError("Set the 15-digit Jackery serial in app options.")
        jobs["jackery-mqtt"] = prefix + ["jackery-bridge", "--serial", serial]
        # linux_worker.py already wraps discover_reader with the serial-verified
        # BlueZ orphan release, and reads OPENDP3_BLE_ADAPTER for which adapter
        # to search, so the per-collector environment below is all this needs.
        collectors["jackery-collector"] = worker + ["jackery-record", "--serial", serial]
    # The Explorer can advertise only briefly after its previous session is
    # released, while the DP3 reconnect path is comparatively persistent. Give
    # Jackery first use of the radio after both MQTT bridges have restored HA.
    for name in ("jackery-collector", "ecoflow-collector"):
        if name in collectors:
            jobs[name] = collectors[name]
    if not jobs:
        raise ValueError("Enable at least one collector.")
    return jobs


def _stop_child(process, *, grace: float) -> None:
    if process.poll() is not None:
        return
    process.send_signal(signal.SIGINT)
    try:
        process.wait(timeout=max(0.01, grace))
    except subprocess.TimeoutExpired:
        process.kill()
        process.wait()


def supervise(jobs, stop, *, stagger=5, retry=15, grace=45, data=None,
              policies=None, environments=None,
              freshness_check=FRESHNESS_CHECK_SECONDS,
              recovery_spacing=RADIO_RECOVERY_SPACING,
              recovery_grace=RECOVERY_GRACE_SECONDS):
    processes = {}
    due = {}
    policies = policies or {}
    environments = environments or {}
    freshness = {}
    # Collectors already restarted once for recording nothing since they
    # started. A device that is simply switched off looks the same, so the
    # start-up lease applies again only after a valid frame has arrived.
    startup_spent = set()
    last_radio_recovery = float("-inf")
    next_freshness_check = 0.0

    def spawn(name):
        environment = None
        if name in environments:
            environment = os.environ.copy()
            environment.update(environments[name])
        process = subprocess.Popen(jobs[name], env=environment)
        if name in policies:
            baseline = newest_frame(policies[name].database)
            freshness[name] = FreshnessState(
                baseline[0] if baseline else None,
                baseline[1] if baseline else None,
                started_mono_ns=None if name in startup_spent else time.monotonic_ns(),
            )
        return process

    try:
        for name, command in jobs.items():
            if stop.is_set():
                break
            processes[name] = spawn(name)
            print(f"Started {name}.", flush=True)
            # MQTT publishers do not touch the radio, so do not spend the BLE
            # reacquisition window spacing them out. Keep the existing stagger
            # after radio workers so simultaneous BLE connects are still avoided.
            if not name.endswith("-mqtt") and stop.wait(stagger):
                break
        while not stop.wait(1):
            for name, process in list(processes.items()):
                if process.poll() is None:
                    continue
                if name not in due:
                    print(f"{name} exited ({process.returncode}); retrying in {retry}s.", flush=True)
                    due[name] = time.monotonic() + retry
                if time.monotonic() >= due[name]:
                    processes[name] = spawn(name)
                    due.pop(name)
            now = time.monotonic()
            if now < next_freshness_check:
                continue
            next_freshness_check = now + freshness_check
            for name, policy in policies.items():
                process = processes.get(name)
                state = freshness.get(name)
                if process is None or state is None or process.poll() is not None:
                    continue
                stamp = newest_frame(policy.database)
                if stamp is not None:
                    frame_id, frame_mono_ns = stamp
                    state.last_frame_mono_ns = frame_mono_ns
                    if state.baseline_id is None or frame_id > state.baseline_id:
                        state.armed = True
                        startup_spent.discard(name)
                # Measured from the newest valid frame once this process has
                # recorded one, else from its start, so a collector that hangs
                # before its first frame is not left running forever.
                reference = state.last_frame_mono_ns if state.armed else state.started_mono_ns
                if reference is None:
                    continue
                age = (time.monotonic_ns() - reference) / 1e9
                if age <= policy.max_age:
                    continue
                if now - last_radio_recovery < recovery_spacing:
                    continue
                since = "" if state.armed else " since it started"
                print(
                    f"{name} valid-frame lease expired: no valid frame for {age:.1f}s{since} "
                    f"on {policy.adapter}; restarting only this collector.",
                    flush=True,
                )
                if not state.armed:
                    startup_spent.add(name)
                _stop_child(process, grace=recovery_grace)
                due[name] = time.monotonic() + retry
                freshness[name] = FreshnessState(
                    frame_id if stamp is not None else state.baseline_id,
                    state.last_frame_mono_ns,
                )
                last_radio_recovery = now
                # One recovery at a time on a shared adapter. The next stale
                # collector is reconsidered after the spacing window.
                break
    finally:
        # CLI handlers catch KeyboardInterrupt to close BLE, flush SQLite and
        # publish MQTT offline. Bound shutdown if a backend does not respond.
        for process in processes.values():
            _stop_child(process, grace=grace)


def main():
    os.umask(0o077)
    log_provenance()
    stop = threading.Event()
    for sig in (signal.SIGTERM, signal.SIGINT):
        signal.signal(sig, lambda *_: stop.set())
    try:
        data = Path("/data")
        options = json.loads((data / "options.json").read_text("utf-8"))
        jobs = commands(options, data)
        environments = job_environments(options)
        policies = freshness_policies(options, data)
        prepare(data, Path("/config"), options)
        jobs["bluetooth-status"] = [sys.executable, "-u", "/opt/opendp3/bluetooth_status.py"]
    except Exception as exc:
        # Configuration can contain credentials. Do not print the original error.
        print(f"OpenDP3 setup invalid ({type(exc).__name__}); check private import.json and app options.", flush=True)
        return 1
    supervise(jobs, stop, data=data, policies=policies, environments=environments)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
