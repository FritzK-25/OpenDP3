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
        print(f"OpenPowerstation source: {repository} {version} (sha256 {digest})", flush=True)
    except Exception:
        print("OpenPowerstation source provenance unavailable.", flush=True)


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
# (the same selection newest_frame() makes; the Jackery's now counts only frames
# carrying core telemetry, as every reading in the 09-28 production log did):
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
# How long the children have, all together, to stop by themselves once the
# Supervisor stops the app; any still running are then killed. config.yaml's
# timeout (60 s) is when the Supervisor kills the whole container, and its stop
# can arrive while a lease recovery is still waiting RECOVERY_GRACE_SECONDS
# for the collector it restarts, so the two together stay well inside it. A
# collector needs a few seconds: its lease wait and BLE work are cancelled,
# and closing the link is bounded at 5 s per call (radio.py).
SHUTDOWN_GRACE_SECONDS = 40.0


class SetupError(ValueError):
    """A setup problem described by one fixed message that quotes no value.

    main() prints these in full so the log names the cause of a failed start,
    which is the first thing to read when the app will not start. Any
    other error is reported by its type alone: configuration holds the broker
    password, and an arbitrary message could quote it.
    """


@dataclass(frozen=True)
class FreshnessPolicy:
    database: Path
    max_age: float
    adapter: str
    # The measurement keys that make a frame valid for this lease; empty means
    # any measurement at all.
    keys: tuple[str, ...] = ()


@dataclass
class FreshnessState:
    baseline_id: int | None
    last_frame_mono_ns: int | None
    armed: bool = False
    # When this process was started, while it may still be held to the lease
    # before recording anything. None once that start-up allowance is spent.
    started_mono_ns: int | None = None


# Used when options.json predates an option. A fresh app has no Jackery serial,
# so that collector defaults off. The jobs and their freshness policies must
# read the same answer, so both go through enabled().
COLLECTOR_DEFAULTS = {"ecoflow": True, "jackery": False}


def enabled(options, collector) -> bool:
    return bool(options.get(collector + "_enabled", COLLECTOR_DEFAULTS[collector]))


def adapter_name(value) -> str:
    adapter = str(value or "hci0").strip()
    if not re.fullmatch(r"hci[0-9]+", adapter):
        raise SetupError("Bluetooth adapter must be hci followed by digits.")
    return adapter


def newest_frame(database: Path, keys: tuple[str, ...] = ()) -> tuple[int, int] | None:
    """The newest decoded frame carrying a measurement, or one of ``keys`` if given."""
    if not database.exists():
        return None
    connection = None
    try:
        uri = f"file:{database.as_posix()}?mode=ro"
        connection = sqlite3.connect(uri, uri=True, timeout=0.2)
        measured = "SELECT 1 FROM measurements m WHERE m.frame_id=f.id"
        if keys:
            measured += " AND m.key IN (" + ",".join("?" for _ in keys) + ")"
        row = connection.execute(
            "SELECT f.id,f.mono_ns FROM frames f "
            "WHERE f.status NOT IN ('pending','invalid_packet','repeated_unverified') "
            f"AND EXISTS ({measured}) "
            "ORDER BY f.id DESC LIMIT 1",
            tuple(keys),
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
    """One valid-frame lease for each collector job commands() starts.

    Derived from those jobs rather than from the options a second time: the
    two used to default jackery_enabled differently. supervise() refuses a
    collector with no lease, which nothing would ever restart.
    """
    policies = {}
    environments = job_environments(options)
    collectors = commands(options, data)
    if "ecoflow-collector" in collectors:
        policies["ecoflow-collector"] = FreshnessPolicy(
            data / "recordings.sqlite",
            ECOFLOW_FRAME_LEASE,
            environments["ecoflow-collector"]["OPENDP3_BLE_ADAPTER"],
        )
    if "jackery-collector" in collectors:
        from openpowerstation.jackery_fields import JACKERY_CORE_KEYS

        policies["jackery-collector"] = FreshnessPolicy(
            data / "jackery.sqlite",
            JACKERY_FRAME_LEASE,
            environments["jackery-collector"]["OPENDP3_BLE_ADAPTER"],
            # The collector also records replies without these -- settings
            # pages, fields it cannot map -- and a station sending only those
            # is the outage this lease exists for, not a renewal of it.
            JACKERY_CORE_KEYS,
        )
    return policies


def prepare(data: Path, configuration: Path, options):
    from openpowerstation.config import ConfigError, load_config

    # Use a dedicated app config mount, never the full HA configuration tree.
    try:
        cfg = load_config(configuration / "import.json")
    except ConfigError as exc:
        # openpowerstation.config words every ConfigError as one constant string.
        raise SetupError(str(exc)) from None
    if not cfg.mqtt_host:
        raise SetupError("A local MQTT broker must be configured.")
    # Control is opt-in: it stays off unless the app option turns it on.
    # Old queued commands are discarded below, and bridges reject retained MQTT
    # messages before they reach the BLE workers.
    cfg.allow_control = bool(options.get("allow_control", False))
    # The app is the collector by definition, whatever the imported file says:
    # import.json may be copied from a desktop config that was made a viewer
    # when this Pi took the batteries over. Its options pick the devices.
    cfg.role = "collector"
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
    # Nor does a queued control request (openpowerstation.control_queue) outlive a start.
    # Each is dated by the wall clock, which a Pi with no clock battery can
    # restore at boot to about where it stopped, so a request queued just before
    # a restart could read as seconds old hours later. A file that cannot be
    # removed only waits to be refused on age; it does not stop the app.
    discarded = 0
    for name in ("commands", "jackery-commands"):
        try:
            leftovers = [path for path in (data / name).iterdir() if path.is_file()]
        except OSError:
            # Absent until a bridge first queues a request.
            continue
        for leftover in leftovers:
            try:
                leftover.unlink()
                discarded += 1
            except OSError:
                pass
    if discarded:
        print(f"Discarded {discarded} control request file(s) queued before this start; "
              "none was sent.", flush=True)


def commands(options, data: Path):
    prefix = [sys.executable, "-u", "-m", "openpowerstation", "--data-dir", str(data)]
    worker = [sys.executable, "-u", "/opt/opendp3/linux_worker.py",
              "--data-dir", str(data)]
    jobs = {}
    collectors = {}
    # Discovery is structural and does not depend on a live BLE session. Queue
    # every enabled MQTT publisher first, then the radio workers, so Home
    # Assistant can restore entities before either collector starts reacquiring.
    if enabled(options, "ecoflow"):
        jobs["ecoflow-mqtt"] = prefix + ["bridge"]
        collectors["ecoflow-collector"] = worker + ["record"]
    # A fresh app has no Jackery serial, so missing legacy options default this
    # collector off rather than making the nominal package configuration invalid.
    if enabled(options, "jackery"):
        serial = options.get("jackery_serial", "")
        if not isinstance(serial, str) or not re.fullmatch(r"[0-9]{15}", serial):
            raise SetupError("Set the 15-digit Jackery serial in app options.")
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
        raise SetupError("Enable at least one collector.")
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


def _stop_children(processes, *, grace: float) -> None:
    """Ask every child to stop at once, then wait for all against one deadline.

    Stopping them one at a time gave each its own grace, so the last child was
    asked only after every one before it had exited or been killed: up to
    five graces in all, against the Supervisor's single 60 s timeout, with the
    collectors -- started last -- signalled last.
    """
    # Newest first: the radio workers were started after the MQTT bridges.
    live = [process for process in reversed(processes) if process.poll() is None]
    for process in live:
        process.send_signal(signal.SIGINT)
    deadline = time.monotonic() + grace
    for process in live:
        try:
            process.wait(timeout=max(0.01, deadline - time.monotonic()))
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait()


def supervise(jobs, stop, *, stagger=5, retry=15, grace=SHUTDOWN_GRACE_SECONDS, data=None,
              policies=None, environments=None,
              freshness_check=FRESHNESS_CHECK_SECONDS,
              recovery_spacing=RADIO_RECOVERY_SPACING,
              recovery_grace=RECOVERY_GRACE_SECONDS):
    processes = {}
    due = {}
    policies = policies or {}
    environments = environments or {}
    # The lease is the only thing that restarts a collector alive but no longer
    # recording, so none is started without one.
    unleased = [name for name in jobs if name.endswith("-collector") and name not in policies]
    if unleased:
        raise ValueError(f"No valid-frame lease for {', '.join(unleased)}; refusing to start.")
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
            baseline = newest_frame(policies[name].database, policies[name].keys)
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
                stamp = newest_frame(policy.database, policy.keys)
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
        _stop_children(list(processes.values()), grace=grace)


def use_entity_id_overrides(configuration: Path):
    """Seed the entity IDs an installation already holds; see entity_ids.py.

    Read once here so a malformed file stops setup with a clear cause instead
    of failing every bridge restart. Every child inherits the variable.
    """
    from openpowerstation.entity_ids import OVERRIDES_ENV, EntityIdOverrideError, load_overrides

    path = configuration / "entity_ids.json"
    if not path.is_file():
        return
    try:
        load_overrides(path)
    except EntityIdOverrideError as exc:
        raise SetupError(str(exc)) from None
    os.environ[OVERRIDES_ENV] = str(path)


def main(data=Path("/data"), configuration=Path("/config")):
    os.umask(0o077)
    log_provenance()
    stop = threading.Event()
    for sig in (signal.SIGTERM, signal.SIGINT):
        signal.signal(sig, lambda *_: stop.set())
    try:
        options = json.loads((data / "options.json").read_text("utf-8"))
        jobs = commands(options, data)
        environments = job_environments(options)
        policies = freshness_policies(options, data)
        prepare(data, configuration, options)
        jobs["bluetooth-status"] = [sys.executable, "-u", "/opt/opendp3/bluetooth_status.py"]
        use_entity_id_overrides(configuration)
    except SetupError as exc:
        # A fixed message that quotes no value; it names the cause.
        print(f"OpenPowerstation setup invalid: {exc}", flush=True)
        return 1
    except Exception as exc:
        # Configuration can contain credentials. Do not print the original error.
        print(f"OpenPowerstation setup invalid ({type(exc).__name__}); check private import.json and app options.", flush=True)
        return 1
    supervise(jobs, stop, data=data, policies=policies, environments=environments)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
