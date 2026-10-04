"""Headless collection service, also used by the GUI's worker thread."""
import asyncio
from collections import Counter
import contextlib
import hashlib
from pathlib import Path
import queue
import sqlite3
import threading
import time

import portalocker
from bleak.exc import BleakError
from . import ble, control_queue
from .config import VIEWER_REFUSAL, Config, control_allowed, data_dir
from .hostclock import LoopWatch, host_clocks
from .protocol import AuthenticationError, CredentialsRejected, PolicyError, ProtocolError
from .radio import (LeaseHoldExpired, LeaseUnavailable, RadioLease, configured_adapter,
                    radio_lock_path)
from .health import configured
from .recorder import Recorder
from .storage import RESERVE_REACHED, Store, StorageError

# How long a queued control request stays valid. The collector polls five times a
# second, so anything older than this waited on a disconnected or busy device.
CONTROL_MAX_AGE = control_queue.MAX_AGE_SECONDS
SESSION_FRAME_LEASE = 75.0
SESSION_LEASE_CHECK = 5.0
# Reconnect backoff: doubles after each failed session up to the ceiling, and
# returns to the floor only once a session delivers measurements. Resetting on
# authentication instead let a DP3 that authenticates and then sends nothing
# be redialled at the floor forever, a scan and handshake on the shared
# adapter every lease period.
RECONNECT_DELAY = 2.0
RECONNECT_DELAY_MAX = 60.0

# Why the collector last stopped delivering telemetry. Each needs a different
# response, and from Home Assistant they used to look alike: collector_state
# says only waiting or error, and every failed attempt was a "disconnected".
# Kept in the recording (Store.set_reason) and published by the bridge as
# collector_reason; the events beside it carry the detail. Constant strings
# only, never an exception's text. The README's table is checked against this.
REASONS = {
    "none": "Telemetry is flowing, or nothing has failed since it last did.",
    "not_advertising": "The configured DP3 did not advertise during a scan.",
    "radio_busy": "The other collector kept the shared Bluetooth adapter.",
    "bluetooth_error": "BlueZ or Bleak failed while connecting or authenticating.",
    "link_lost": "An authenticated link dropped.",
    "silent": "An authenticated link delivered no frames.",
    "no_measurements": "Frames arrived but none carried a measurement.",
    "undecodable_frames": "Frames stopped decoding: the session key no longer matches.",
    "protocol_unsupported": "The DP3 spoke a handshake or framing this decoder does not know.",
    "authentication_rejected": "The DP3 refused this account's login.",
    "storage_reserve": "Free space fell below the evidence store's reserve.",
    "storage_failed": "The evidence store or capture processing failed.",
    "collector_error": "Anything else; the app log and the events say what.",
}


def failure_reason(exc: BaseException, *, connected: bool = False) -> str:
    """The REASONS code for what ended a connection attempt or a session.

    ``connected`` says whether the attempt had authenticated: a transport
    failure after that is a lost link, before it a Bluetooth error.
    """
    reason = getattr(exc, "reason", None)
    if reason == "link_lost" and not connected:
        # Dropped during the handshake: no authenticated link was lost.
        return "bluetooth_error"
    if reason in REASONS:
        return reason
    if isinstance(exc, StorageError):
        return "storage_reserve" if str(exc) == RESERVE_REACHED else "storage_failed"
    if isinstance(exc, sqlite3.Error):
        return "storage_failed"
    if isinstance(exc, CredentialsRejected):
        return "authentication_rejected"
    if isinstance(exc, PolicyError):
        # The outbound gate refused this collector's own handshake.
        return "collector_error"
    if isinstance(exc, ProtocolError):
        return "protocol_unsupported"
    if isinstance(exc, LeaseUnavailable):
        return "radio_busy"
    if isinstance(exc, (OSError, BleakError)):
        return "link_lost" if connected else "bluetooth_error"
    return "collector_error"


def control_label(field, value):
    """``cfg_lv_ac_out_open=off``: how every control event names its command."""
    return f"{field}={'on' if value else 'off'}"


class Service(threading.Thread):
    def __init__(self, config: Config, database: Path, *, notify=lambda _: None, lock_dir=None,
                 config_path=None, scan=None):
        super().__init__(name="OpenPowerstation recorder", daemon=False)
        self.config, self.database, self.notify = config, Path(database), notify
        self.config_path = config_path
        # How a connection attempt discovers the DP3: ble.scan, or a caller's
        # wrapper of it. The HAOS app's worker passes one that first releases a
        # link BlueZ still holds (home-assistant/opendp3/linux_worker.py). It used to
        # patch openpowerstation.ble.scan instead, which reached this class only while
        # it happened to look scan up through the module.
        self.scan = ble.scan if scan is None else scan
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
        self.control_dir = self.database.parent / control_queue.DP3_DIRECTORY

    def drain_control_files(self):
        """Turn queued requests into commands. Unreadable ones are dropped.

        Always drains, even while control is off. Skipping the drain then would
        let requests pile up unseen and replay as a burst the moment control was
        enabled -- a switch obeying a press from an hour ago. Refusal is decided
        per command, and recorded, rather than by leaving files on disk.

        Whether a request is still fresh is not decided here but as it is about
        to be sent (execute_control): requests drained together run one after
        another, each write taking as long as the radio makes it.
        """
        for entry in control_queue.drain(self.control_dir):
            # Both DP3 controls are on/off outputs; nothing else is a DP3 request.
            if type(entry.value) is not bool:
                continue
            label = control_label(entry.key, entry.value)
            if isinstance(entry, control_queue.Refusal):
                self.command("control_refused", f"{label}: {entry.reason}.")
            elif not entry.guarded:
                # Only the bridge's guarded payload marks a request executable.
                # Anything else -- a bare switch press, a hand-written file, an
                # older writer -- is refused here too, where the radio is.
                self.command("control_unguarded", label)
            else:
                self.command("control", entry)

    def set_state(self, state, detail=""):
        self.state = state
        self.notify({"state": state, "detail": detail, "session_id": self.sid})

    def command(self, kind, detail=""):
        if kind == "control" and isinstance(detail, str):
            # A command from inside this process, "field=on": issued now.
            field, _, state = detail.partition("=")
            detail = control_queue.Request(field.strip(), state.strip() == "on", time.time_ns())
        self.commands.put((kind, detail))

    async def execute_control(self, recorder, request):
        """Send one request and record exactly one outcome for it.

        Everything that can refuse it is decided here, immediately before the
        write: the request's own age (control_queue), then the saved policy,
        re-read for this request, then the session. Those refusals are
        control_refused, and so is one the session raises before anything
        reaches the radio -- the outbound gate, no authenticated session.

        A write that fails is different. The command may have reached the DP3
        before the link reported the failure, so its outcome is unknown, and
        it is recorded as control_unverified. ble.Session._write turns every
        failure of the write itself into ConnectionError or TimeoutError, so
        none of them can end the recording. Nothing else is caught: a failure
        to record the outcome is an evidence-store failure, and still stops the
        recording fail closed.
        """
        label = control_label(request.key, request.value)
        lapsed = request.lapsed(CONTROL_MAX_AGE)
        if lapsed:
            recorder.event("control_refused", f"{label}: {lapsed}.")
            return
        allowed = control_allowed(self.config_path) if self.config_path else self.config.allow_control
        if not allowed:
            recorder.event("control_refused", f"{label}: Device control is turned off.")
            return
        session = self.session
        if session is None:
            recorder.event("control_refused", f"{label}: No connected device.")
            return
        session.gate.allow_control = allowed
        try:
            await session.send_control(request.key, request.value)
        except (PolicyError, ProtocolError) as exc:
            recorder.event("control_refused", f"{label}: {exc}")
        except (ConnectionError, TimeoutError, BleakError) as exc:
            recorder.event("control_unverified",
                           f"{label}: outcome unknown; {str(exc) or type(exc).__name__}")
        else:
            recorder.event("control", f"Sent {label}.")

    def stop(self):
        self.stop_requested.set()

    def run(self):
        try:
            self.config.validate()
            if self.config.role != "collector":
                # Every DP3 collector passes here -- the desktop's Start
                # recording, the record command and the launchers that run it
                # -- before the lock, the evidence store or the radio.
                raise ValueError(VIEWER_REFUSAL)
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
            reason_written = None

            def report(reason):
                """Keep the collector's reason current, writing only a change."""
                nonlocal reason_written
                if reason != reason_written:
                    store.set_reason(reason)
                    reason_written = reason

            try:
                recorder = Recorder(store, health=configured(cfg),
                                    firmware=cfg.firmware, conditions=cfg.conditions)
            except StorageError as exc:
                # No session is opened below the reserve, so this row is all
                # that says why the collector keeps exiting.
                with contextlib.suppress(sqlite3.Error):
                    report(failure_reason(exc))
                raise
            self.sid = recorder.sid
            task = None
            # Whether the attempt in ``task`` authenticated, which decides
            # whether its failure was a lost link or a failed connection.
            attempt_connected = False
            released = False
            retry_at, retry_delay = 0.0, RECONNECT_DELAY
            watch = LoopWatch(host_clocks())
            failed = False

            def on_event(kind, detail, utc, mono):
                recorder.event(kind, detail, utc, mono)
                if kind == "connected":
                    if self.first_connected_at is None:
                        self.first_connected_at = time.monotonic()
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
                # What arrived since the last measurement, by recorder outcome.
                unproductive = Counter()

                def session_event(kind, detail, utc, mono):
                    nonlocal connected_at, attempt_connected
                    on_event(kind, detail, utc, mono)
                    if kind == "connected":
                        connected_at = time.monotonic()
                        attempt_connected = True
                        connected.set()

                def capture(raw, utc, mono):
                    nonlocal last_frame, retry_delay
                    try:
                        if recorder.ingest(raw, utc, mono):
                            if last_frame is None:
                                # The first measurements of this session: the
                                # link works, so the backoff starts over, and
                                # nothing is wrong any more.
                                retry_delay = RECONNECT_DELAY
                                report("none")
                            last_frame = time.monotonic()
                            unproductive.clear()
                        else:
                            unproductive[recorder.last_outcome] += 1
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
                            # Housekeeping, a changed key and retransmits each
                            # mean something different; name what came instead.
                            arrived = (
                                f"{sum(unproductive.values())} frames since the last measurement: "
                                + ", ".join(f"{outcome} x{count}"
                                            for outcome, count in unproductive.most_common(3))
                                + ("" if len(unproductive) <= 3 else
                                   f", {len(unproductive) - 3} other kinds")
                            ) if unproductive else "no frames since the last measurement"
                            detail = (
                                f"adapter={configured_adapter()}; no valid frame for "
                                f"{SESSION_FRAME_LEASE:g}s; {arrived}; "
                                "cancelling the entire BLE session."
                            )
                            recorder.event("session_lease_expired", detail)
                            raise ble.CollectionEnded("DP3 valid-frame session lease expired.",
                                                      "no_measurements" if unproductive else "silent")

                async def end_session():
                    if session_task is not None and not session_task.done():
                        session_task.cancel()
                        with contextlib.suppress(asyncio.CancelledError, Exception):
                            await session_task

                try:
                    # Serialize only discovery and authentication. Healthy
                    # persistent sessions remain concurrent afterward. The lease
                    # is also this phase's deadline: it cancels the block at its
                    # hold limit, which is sized for the handshake, so a GATT
                    # call that never returns cannot keep the adapter from the
                    # other collector (see radio.RADIO_HOLD_LIMIT).
                    async with RadioLease(radio_lock_path(self.database.parent)):
                        found = await self.scan()
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
                                try:
                                    done, _ = await asyncio.wait(
                                        {session_task, connected_task},
                                        return_when=asyncio.FIRST_COMPLETED,
                                    )
                                except BaseException:
                                    # Close the half-open link before the lease
                                    # goes, so the other collector never starts
                                    # connecting beside a DP3 link still closing.
                                    await end_session()
                                    raise
                                if session_task in done:
                                    return await session_task
                                break
                        else:
                            raise ble.CollectionEnded("Configured DP3 is not advertising.",
                                                      "not_advertising")

                    lease_task = asyncio.create_task(enforce_frame_lease())
                    done, _ = await asyncio.wait(
                        {session_task, lease_task},
                        return_when=asyncio.FIRST_COMPLETED,
                    )
                    if lease_task in done:
                        await end_session()
                        return await lease_task
                    return await session_task
                except LeaseHoldExpired as exc:
                    # The attempt was cancelled rather than failing, so nothing
                    # inside it recorded why it ended. Only the lease raises this.
                    recorder.event("session_error", (
                        f"{ble._exception_detail('runtime', 'connect', exc)}; "
                        f"stage={self.state}"))
                    raise
                finally:
                    for auxiliary in (connected_task, lease_task):
                        if auxiliary is not None and not auxiliary.done():
                            auxiliary.cancel()
                    await end_session()
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
                    gap = watch.tick()
                    if gap and gap.kind == "host_suspend":
                        recorder.event("host_suspend", f"Host was suspended for {gap.asleep:.1f}s of a "
                                       f"{gap.seconds:.1f}s collection gap; capture is incomplete.")
                    elif gap:
                        # Awake but not running: notifications queued and were
                        # stamped late. Nothing is pinned; any the queue could
                        # not hold are recorded by the session as a capture_gap.
                        took = recorder.last_maintenance_seconds
                        recorder.event("loop_stall", (
                            f"Collector loop did not run for {gap.seconds:.1f}s while the host was awake; "
                            "frames received meanwhile carry late receipt times. "
                            + ("No storage maintenance pass yet." if took is None else
                               f"Last storage maintenance pass took {took:.2f}s.")
                            + (f" {gap.suppressed} further stalls since the previous loop_stall event "
                               "were counted but not recorded." if gap.suppressed else "")))
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
                        elif kind == "control_refused":
                            recorder.event("control_refused", detail)
                        elif kind == "control_unguarded":
                            recorder.event("control_refused",
                                           f"{detail}: not the guarded payload; a bare ON/OFF "
                                           "never changes an AC output.")
                        elif kind == "control":
                            # Recorded either way: a refused command is as much
                            # part of the session's history as an accepted one.
                            await self.execute_control(recorder, detail)
                    if task and task.done():
                        try:
                            await task
                        except (StorageError, sqlite3.Error) as exc:
                            with contextlib.suppress(sqlite3.Error, StorageError):
                                report(failure_reason(exc))
                            raise StorageError(str(exc) if isinstance(exc,StorageError) else 'Evidence storage failed; recording has stopped.') from None
                        except (AuthenticationError, PolicyError) as exc:
                            reason = failure_reason(exc)
                            recorder.event("connection_failed", f"reason={reason}; {exc}")
                            report(reason)
                            self.error = str(exc)
                            failed = True
                            self.set_state("error", self.error)
                            break
                        except Exception as exc:
                            reason = failure_reason(exc, connected=attempt_connected)
                            if attempt_connected:
                                # An authenticated link ended: a gap in the
                                # recording, and the frames around it are kept.
                                recorder.event("disconnected", (
                                    f"reason={reason}; Bluetooth session ended "
                                    f"({type(exc).__name__}); device state is unknown."))
                            else:
                                # Nothing was connected, so nothing was lost:
                                # neither a gap nor a pinned incident, and the
                                # cause is kept rather than "transport failed".
                                recorder.event("connection_failed", (
                                    f"reason={reason}; "
                                    f"{ble._exception_detail('runtime', 'connect', exc)}; "
                                    f"stage={self.state}"))
                            report(reason)
                        else:
                            # A session ends only on an exception; nothing names
                            # this one.
                            reason = "collector_error"
                            report(reason)
                        task = None
                        retry_at = time.monotonic()+retry_delay
                        self.set_state("reconnecting",
                                       f"Retry in {retry_delay:g}s ({reason}). No cloud fallback.")
                        retry_delay = min(RECONNECT_DELAY_MAX, retry_delay*2)
                    if not released and task is None and time.monotonic() >= retry_at:
                        attempt_connected = False
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
