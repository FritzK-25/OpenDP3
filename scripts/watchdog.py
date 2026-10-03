"""Keep the OpenDP3 recorder and MQTT bridges healthy.

This is the one process owned by the Windows ``OpenDP3 Recorder`` scheduled
task.  It starts the detached workers through ``start_all.py`` and periodically
checks both the recording databases and the retained MQTT health topics.  Recovery is scoped to one worker. Device absence and broker outages never
restart a collector that still owns its database.
"""
from __future__ import annotations

import argparse
import logging
from pathlib import Path
import sqlite3
import sys
import time

import portalocker

# Running a file from ``scripts`` puts that directory, not the repository root,
# on sys.path.  Add the root so the editable OpenDP3 install and start_all module
# resolve consistently from Task Scheduler and from a developer shell.
ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from opendp3.bridge import STALE_SECONDS
from opendp3.storage import read_db
from scripts import start_all
from scripts.start_all import verify_broker_state

CHECK_SECONDS = 60.0
DP3_MAX_AGE = max(90.0, STALE_SECONDS * 2)
JACKERY_MAX_AGE = 180.0
RECOVERY_COOLDOWN = 300.0
STOP_WAIT_SECONDS = 45.0
# name: (ownership lock, stop request, launcher)
WORKERS = {
    "DP3 recorder": ("recordings.sqlite.writer.lock", "collector.stop", "start_collector"),
    "DP3 bridge": ("bridge.lock", "bridge.stop", "start_bridge"),
    "Jackery recorder": ("jackery.sqlite.writer.lock", "jackery.stop", "start_jackery_recorder"),
    "Jackery bridge": ("jackery-bridge.lock", "jackery-bridge.stop", "start_jackery_bridge"),
}


def newest_frame(database: Path) -> int | None:
    if not database.exists():
        return None
    try:
        with read_db(database) as db:
            row = db.execute("SELECT MAX(utc_ns) AS utc_ns FROM frames").fetchone()
        return int(row["utc_ns"]) if row and row["utc_ns"] is not None else None
    except (OSError, ValueError, TypeError, sqlite3.Error):
        return None


def lock_held(path: Path) -> bool:
    lock = portalocker.Lock(str(path), timeout=0)
    try:
        lock.acquire()
    except (portalocker.exceptions.LockException, PermissionError, OSError):
        return True
    lock.release()
    return False


def recover_worker(data: Path, name: str, restart: bool) -> bool:
    """Stop only the selected bridge; never reset a collector for stale telemetry."""
    lock_name, stop_name, launcher = WORKERS[name]
    if restart:
        if name.endswith("recorder"):
            raise ValueError("A held collector must be allowed to reconnect independently.")
        (data / stop_name).write_text("stop", encoding="ascii")
        deadline = time.monotonic() + STOP_WAIT_SECONDS
        while lock_held(data / lock_name):
            if time.monotonic() >= deadline:
                logging.error("%s did not release its lock", name)
                return False
            time.sleep(1)
    elif lock_held(data / lock_name):
        return True
    result = getattr(start_all, launcher)(data, Path(sys.executable))
    logging.info("%s: %s", name, result)
    return result in {"started", "already"}


def health(data: Path) -> tuple[bool, str, dict[str, bool]]:
    """Return diagnostic status and independent recovery actions.

    False means start a missing worker; True means restart just that bridge.
    An owned but stale collector can be scanning for a powered-off device. Only
    loss of ownership establishes a collector failure we can recover safely.
    """
    now_ns = time.time_ns()
    ages = {"DP3": (newest_frame(data / "recordings.sqlite"), DP3_MAX_AGE),
            "Jackery": (newest_frame(data / "jackery.sqlite"), JACKERY_MAX_AGE)}
    reasons, actions = [], {}
    live = {}
    for label, (stamp, max_age) in ages.items():
        live[label] = stamp is not None and 0 <= (now_ns-stamp)/1e9 <= max_age
        if not live[label]:
            reasons.append(f"{label} recording stale; collector may be reconnecting")
    for name, (lock_name, _, _) in WORKERS.items():
        if not lock_held(data / lock_name):
            reasons.append(f"{name} not running")
            actions[name] = False
    try:
        broker = verify_broker_state(data, dp3=True, jackery=True, timeout=5.0)
        for label, state in broker.items():
            if state["availability"] != "online" or state["telemetry"] != "online":
                reasons.append(f"{label} MQTT health is not online")
                # No retained response can mean the broker itself is down.
                # Let running clients reconnect, without resetting either radio.
                if (live.get(label) and None not in state.values()
                        and label + " bridge" not in actions):
                    actions[label + " bridge"] = True
    except Exception as exc:
        reasons.append(f"MQTT health check failed: {type(exc).__name__}")
    return not reasons, "; ".join(reasons) or "healthy", actions


def run(data: Path) -> int:
    data.mkdir(parents=True, exist_ok=True)
    lock = portalocker.Lock(str(data / "watchdog.lock"), timeout=0)
    try:
        lock.acquire()
    except (portalocker.exceptions.LockException, PermissionError, OSError):
        logging.info("Another OpenDP3 watchdog is already running.")
        return 0
    try:
        last_recovery = {}
        while True:
            try:
                ok, reason, actions = health(data)
            except Exception:
                logging.exception("Health probe failed; retrying next cycle")
                time.sleep(CHECK_SECONDS)
                continue
            if ok:
                logging.info("healthy")
            else:
                logging.warning("%s", reason)
                for name, restart in actions.items():
                    now = time.monotonic()
                    if now - last_recovery.get(name, float("-inf")) < RECOVERY_COOLDOWN:
                        continue
                    last_recovery[name] = now
                    try:
                        if not recover_worker(data, name, restart):
                            logging.error("%s recovery did not complete", name)
                    except Exception:
                        logging.exception("%s recovery failed; retrying after cooldown", name)
            time.sleep(CHECK_SECONDS)
    finally:
        lock.release()
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="OpenDP3 telemetry watchdog")
    parser.add_argument("--data-dir", type=Path, required=True)
    args = parser.parse_args()
    args.data_dir.mkdir(parents=True, exist_ok=True)
    log_path = args.data_dir / "watchdog.log"
    logging.basicConfig(filename=log_path, level=logging.INFO,
                        format="%(asctime)s %(levelname)s %(message)s")
    return run(args.data_dir.resolve())


if __name__ == "__main__":
    raise SystemExit(main())
