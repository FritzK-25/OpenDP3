"""Headless collection service, also used by the GUI's worker thread."""
import asyncio
import contextlib
import hashlib
import json
from pathlib import Path
import queue
import sqlite3
import threading
import time

import portalocker
from bleak.exc import BleakError
from . import ble
from .config import Config, control_allowed, data_dir
from .protocol import AuthenticationError, PolicyError, ProtocolError
from .radio import RadioLease, configured_adapter, radio_lock_path
from .recorder import Recorder, Settings
from .storage import Store, StorageError

# How long a queued control request stays valid. The collector polls five times a
# second, so anything older than this waited on a disconnected or busy device.
CONTROL_MAX_AGE = 30.0
SESSION_FRAME_LEASE = 75.0
SESSION_LEASE_CHECK = 5.0

class Service(threading.Thread):
    def __init__(self, config: Config, database: Path, *, notify=lambda _: None, lock_dir=None,
                 config_path=None):
        super().__init__(name="OpenPowerstation recorder", daemon=False)
        self.config, self.database, self.notify = config, Path(database), notify
        self.config_path = config_path
        self.lock_dir = Path(lock_dir) if lock_dir else data_dir() / "locks"
        self.commands = queue.Queue()
        self.stop_requested = threading.Event()
        self.state = "stopped"
        self.error = ""
        self.sid = None
        self.loop = None
        self.root_task = None
        self.first_connected_at = None
        # The live BLE session, while one exists. Control needs it; there is
        # deliberately no other route from this process to the radio.
        self.session = None
        # Control requests arrive as files, the same way stop requests do. The
        # bridge writes them; the recorder still opens no socket of its own, so
        # a broken or hostile broker cannot reach the radio or stall a recording.
        self.control_dir = self.database.parent / "commands"

    def drain_control_files(self):
        """Turn queued request files into commands. Unreadable ones are dropped.

        Always drains, even while control is off. Skipping the drain then would
        let requests pile up unseen and replay as a burst the moment control was
        enabled -- a switch obeying a press from an hour ago. Refusal is decided
        per command, and recorded, rather than by leaving files on disk.
        """
        if not self.control_dir.is_dir():
            return
        now = time.time()
        for path in sorted(self.control_dir.glob("*.json")):
            request, age = None, None
            try:
                age = now - path.stat().st_mtime
                request = json.loads(path.read_text("utf-8"))
            except (OSError, ValueError):
                pass
            with contextlib.suppress(OSError):
                path.unlink()
            if not isinstance(request, dict):
                continue
            field, value = request.get("field"), request.get("value")
            if not (isinstance(field, str) and type(value) is bool):
                continue
            state = "on" if value else "off"
            # An output command that waited is not one to obey late: the person
            # who pressed it has long since seen something else happen.
            expired = age is None or age > CONTROL_MAX_AGE
            self.command("control_expired" if expired else "control", f"{field}={state}")

    def set_state(self, state, detail=""):
        self.state = state
        self.notify({"state": state, "detail": detail, "session_id": self.sid})

    def command(self, kind, detail=""):
        self.commands.put((kind, detail))

    def stop(self):
        self.stop_requested.set()

    def run(self):
        try:
            self.config.validate()
            self.lock_dir.mkdir(parents=True, exist_ok=True)
            # Stable across database paths; every collector for this device shares a lock.
            key = hashlib.sha256(self.config.serial.encode()).hexdigest()
            with portalocker.Lock(self.lock_dir / (key + ".lock"), timeout=0):
                asyncio.run(self.collect())
        except portalocker.exceptions.LockException:
            self.error = "Another OpenPowerstation collector already owns this device."
            self.set_state("error", self.error)
        except Exception as exc:
            # Never propagate arbitrary BLE/account payloads into logs or UI.
            self.error = str(exc) if isinstance(exc, (StorageError, ValueError)) else f"Collection stopped ({type(exc).__name__})."
            self.set_state("error", self.error)

    async def collect(self):
        self.loop = asyncio.get_running_loop()
        cfg = self.config
        with Store(self.database) as store:
            recorder = Recorder(store, settings=Settings(cfg.temperature_jump, cfg.temperature_window),
                                firmware=cfg.firmware, conditions=cfg.conditions)
            self.sid = recorder.sid
            task = None
            released = False
            retry_at, retry_delay = 0.0, 2.0
            last_tick = time.monotonic()
            failed = False

            def on_event(kind, detail, utc, mono):
                nonlocal retry_delay
                recorder.event(kind, detail, utc, mono)
                if kind == "connected":
                    if self.first_connected_at is None:
                        self.first_connected_at = time.monotonic()
                    retry_delay = 2.0
                    self.set_state("recording", detail)
                elif kind == "silence":
                    self.set_state("stale", detail)
                elif kind == "telemetry_resumed":
                    self.set_state("recording", detail)

            async def connect():
                self.set_state("scanning", "Looking for the configured DP3; close its phone Bluetooth session.")
                connected = asyncio.Event()
                connected_at = last_frame = None
                session_task = connected_task = lease_task = None

                def session_event(kind, detail, utc, mono):
                    nonlocal connected_at
                    on_event(kind, detail, utc, mono)
                    if kind == "connected":
                        connected_at = time.monotonic()
                        connected.set()

                def capture(raw, utc, mono):
                    nonlocal last_frame
                    try:
                        if recorder.ingest(raw, utc, mono):
                            last_frame = time.monotonic()
                    except StorageError:
                        raise
                    except Exception:
                        raise StorageError(
                            "Capture processing failed; recording stopped. "
                            "Committed raw frames remain available."
                        ) from None

                async def enforce_frame_lease():
                    while True:
                        await asyncio.sleep(SESSION_LEASE_CHECK)
                        reference = last_frame if last_frame is not None else connected_at
                        if reference is not None and time.monotonic() - reference > SESSION_FRAME_LEASE:
                            detail = (
                                f"adapter={configured_adapter()}; no valid frame for "
                                f"{SESSION_FRAME_LEASE:g}s; cancelling the entire BLE session."
                            )
                            recorder.event("session_lease_expired", detail)
                            raise ConnectionError("DP3 valid-frame session lease expired.")

                try:
                    # Serialize only discovery and authentication. Healthy
                    # persistent sessions remain concurrent afterward.
                    async with RadioLease(radio_lock_path(self.database.parent)):
                        found = await ble.scan()
                        for identity, device in found:
                            if (identity.serial == cfg.serial
                                    and identity.address.upper() == cfg.address.upper()):
                                self.set_state("authenticating", "Establishing local session.")
                                session = ble.Session(
                                    identity,
                                    device,
                                    cfg.user_id,
                                    capture,
                                    session_event,
                                    allow_control=cfg.allow_control,
                                )
                                self.session = session
                                session_task = asyncio.create_task(session.run())
                                connected_task = asyncio.create_task(connected.wait())
                                done, _ = await asyncio.wait(
                                    {session_task, connected_task},
                                    return_when=asyncio.FIRST_COMPLETED,
                                )
                                if session_task in done:
                                    return await session_task
                                break
                        else:
                            raise ConnectionError("Configured DP3 is not advertising.")

                    lease_task = asyncio.create_task(enforce_frame_lease())
                    done, _ = await asyncio.wait(
                        {session_task, lease_task},
                        return_when=asyncio.FIRST_COMPLETED,
                    )
                    if lease_task in done:
                        session_task.cancel()
                        with contextlib.suppress(asyncio.CancelledError, Exception):
                            await session_task
                        return await lease_task
                    return await session_task
                finally:
                    for auxiliary in (connected_task, lease_task):
                        if auxiliary is not None and not auxiliary.done():
                            auxiliary.cancel()
                    if session_task is not None and not session_task.done():
                        session_task.cancel()
                        with contextlib.suppress(asyncio.CancelledError, Exception):
                            await session_task
                    self.session = None

            async def cancel_task():
                nonlocal task
                if task:
                    if not task.done():
                        task.cancel()
                    with contextlib.suppress(asyncio.CancelledError, Exception):
                        await task
                    task = None

            try:
                while not self.stop_requested.is_set():
                    now = time.monotonic()
                    if now-last_tick > 10:
                        recorder.event("host_suspend", "Host scheduling/sleep gap; capture is incomplete.")
                    last_tick = now
                    self.drain_control_files()
                    while not self.commands.empty():
                        kind, detail = self.commands.get_nowait()
                        if kind == "release":
                            released = True
                            await cancel_task()
                            recorder.event("released", "Bluetooth released by the user.")
                            self.set_state("released", "Phone may connect. Resume to collect again.")
                        elif kind == "resume":
                            released = False
                            retry_at = 0
                        elif kind == "mark":
                            recorder.event("manual", detail[:4000])
                        elif kind == "delete_incident":
                            store.delete_incident(int(detail))
                        elif kind == "control_expired":
                            recorder.event("control_refused",
                                           f"{detail}: request expired before it could be sent.")
                        elif kind == "control":
                            # Recorded either way: a refused command is as much
                            # part of the session's history as an accepted one.
                            field, _, raw_value = str(detail).partition("=")
                            field, value = field.strip(), raw_value.strip() == "on"
                            try:
                                allowed = (control_allowed(self.config_path) if self.config_path else cfg.allow_control)
                                if not allowed:
                                    raise PolicyError("Device control is turned off.")
                                if self.session is None:
                                    raise ConnectionError("No connected device.")
                                self.session.gate.allow_control = allowed
                                await self.session.send_control(field, value)
                                recorder.event("control", f"Sent {field}={'on' if value else 'off'}.")
                            except (PolicyError, ProtocolError, ConnectionError, BleakError) as exc:
                                recorder.event("control_refused",
                                               f"{field}={'on' if value else 'off'}: {exc}")
                    if task and task.done():
                        try:
                            await task
                        except (StorageError, sqlite3.Error, OSError) as exc:
                            if isinstance(exc, (StorageError, sqlite3.Error)):
                                raise StorageError(str(exc) if isinstance(exc,StorageError) else 'Evidence storage failed; recording has stopped.') from None
                            recorder.event("disconnected", "Bluetooth transport failed; device state is unknown.")
                        except (AuthenticationError, PolicyError) as exc:
                            recorder.event("connection_failed", str(exc))
                            self.error = str(exc)
                            failed = True
                            self.set_state("error", self.error)
                            break
                        except Exception as exc:
                            recorder.event("disconnected", f"Bluetooth session ended ({type(exc).__name__}); device state unknown.")
                        task = None
                        retry_at = time.monotonic()+retry_delay
                        self.set_state("reconnecting", f"Retry in {retry_delay:g}s. No cloud fallback.")
                        retry_delay = min(60, retry_delay*2)
                    if not released and task is None and time.monotonic() >= retry_at:
                        task = asyncio.create_task(connect())
                    await asyncio.sleep(0.2)
            except Exception:
                failed = True
                raise
            finally:
                await cancel_task()
                with contextlib.suppress(sqlite3.Error, StorageError):
                    recorder.finish("error" if failed else "stopped")
                if not failed:
                    self.set_state("stopped", "Recording saved.")
