"""Cross-process coordination for short Bluetooth recovery operations."""
import asyncio
import os
from pathlib import Path
import re
import sys
import time

import portalocker

ADAPTER_ENV = "OPENDP3_BLE_ADAPTER"

# The adapter lease serializes the two collectors' recovery work on one
# controller: the DP3's discovery and authentication, and the Jackery's orphan
# release and connect. Its two numbers are one budget, and RadioLease enforces
# both halves instead of trusting each holder to stay inside it.
#
# RADIO_HOLD_LIMIT is the longest any holder keeps the lease. The work under it
# is cancelled when the limit passes, so a GATT call that never returns cannot
# keep the other battery off the radio for as long as it hangs. It is sized for
# the slowest legitimate holder, the DP3, whose attempt under the lease is:
#
#     orphan-link release (Linux add-on)   nothing, or about 1 s after
#                                          releasing a link; abandoned for the
#                                          scan at 12 s in all
#                                          (linux_worker.ORPHAN_CHECK_TIMEOUT)
#     discovery, ble.SCAN_TIMEOUT          8 s, always the full window
#     connect, ble.CONNECT_TIMEOUT         up to 20 s, Bleak's own deadline
#     notify subscription and handshake    no deadline on the GATT calls
#                                          themselves; each of the four
#                                          replies may take up to 20 s
#
# The one DP3 attach in a September 2026 production add-on log spent about
# four 3-second Jackery polls between scanning and authenticating, and finished
# its handshake before the next poll: roughly 10 to 15 s in all. A connect that
# runs to its own deadline still leaves the second half of the limit for the
# handshake. What the limit does not grant is every handshake reply its full
# 20 s: a DP3 that slow is abandoned at the limit, recorded, and retried under
# the collector's backoff, rather than keeping the other battery off the radio
# for two minutes. The Jackery holds the lease for less: it searches outside
# it, and takes it only to release an orphaned link and, once its Explorer
# advertises, to connect (jackery.CONNECT_TIMEOUT, 30 s).
#
# RADIO_LOCK_TIMEOUT is how long a waiter queues. A cancelled holder still
# cleans up under the lease -- a DP3 disconnect bounded at 5 s, a Jackery
# unsubscribe and disconnect bounded at 5 s each -- so the wait is the hold
# limit plus that margin, and a holder inside its budget can never make the
# other collector's recovery fail with "lease unavailable".
RADIO_HOLD_LIMIT = 60.0
RADIO_RELEASE_MARGIN = 15.0
RADIO_LOCK_TIMEOUT = RADIO_HOLD_LIMIT + RADIO_RELEASE_MARGIN
# How often a queued collector retries the lock.
RADIO_LOCK_POLL = 0.25


def configured_adapter() -> str:
    """Return the explicitly selected Linux adapter, defaulting to hci0."""
    adapter = os.environ.get(ADAPTER_ENV, "hci0").strip()
    if not re.fullmatch(r"hci[0-9]+", adapter):
        raise ValueError(f"Invalid Bluetooth adapter {adapter!r}; expected hci followed by digits.")
    return adapter


def bleak_adapter_kwargs() -> dict[str, str]:
    """Arguments accepted by Bleak's BlueZ scanner/client backends."""
    return {"adapter": configured_adapter()} if sys.platform.startswith("linux") else {}


def radio_lock_path(data_directory: Path) -> Path:
    return Path(data_directory) / f"radio-{configured_adapter()}.lock"


class LeaseUnavailable(ConnectionError):
    """The adapter lease could not be taken: the other collector kept it, or the lock failed.

    A ConnectionError, so both collectors treat it as a failed recovery attempt
    to record and retry; its own type lets the DP3 collector report it as the
    radio being busy rather than as a Bluetooth fault.
    """


class LeaseHoldExpired(ConnectionError):
    """The work under the adapter lease ran past its hold limit and was cancelled.

    A ConnectionError, like a lease that could not be taken, so both collectors
    already treat it as a failed recovery attempt to record and retry.
    """


class RadioLease:
    """Serialize scan/connect recovery without serializing healthy sessions.

    Waiting polls rather than blocking a worker thread, so a collector told to
    stop while it queues stops at once instead of after the whole timeout:
    asyncio.run() joins such a thread on the way out. Holding is bounded: the
    guarded block is cancelled at the hold limit, and the lock goes back once
    its cleanup has run. Enter one lease at a time; it may be entered again
    after it has been released.
    """

    def __init__(self, path: Path, *, timeout: float | None = None,
                 hold_limit: float | None = None):
        self.path = Path(path)
        self.timeout = RADIO_LOCK_TIMEOUT if timeout is None else timeout
        self.hold_limit = RADIO_HOLD_LIMIT if hold_limit is None else hold_limit
        self._lock = None
        self._hold = None

    def _unavailable(self, exc: BaseException) -> ConnectionError:
        return LeaseUnavailable(
            f"Bluetooth recovery lease unavailable for {configured_adapter()} "
            f"({type(exc).__name__})."
        )

    async def __aenter__(self):
        if self._lock is not None:
            raise RuntimeError("This Bluetooth recovery lease is already held.")
        self.path.parent.mkdir(parents=True, exist_ok=True)
        lock = portalocker.Lock(str(self.path), timeout=0, fail_when_locked=True)
        deadline = time.monotonic() + self.timeout
        while True:
            try:
                lock.acquire()
                break
            except portalocker.exceptions.AlreadyLocked as exc:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise self._unavailable(exc) from exc
                await asyncio.sleep(min(RADIO_LOCK_POLL, remaining))
            except (portalocker.exceptions.LockException, PermissionError, OSError) as exc:
                raise self._unavailable(exc) from exc
        hold = asyncio.timeout(self.hold_limit)
        try:
            await hold.__aenter__()
        except BaseException:
            lock.release()
            raise
        self._lock, self._hold = lock, hold
        return self

    async def __aexit__(self, exc_type, exc, tb):
        lock, hold = self._lock, self._hold
        self._lock = self._hold = None
        try:
            if hold is not None:
                await hold.__aexit__(exc_type, exc, tb)
        except TimeoutError as expired:
            raise LeaseHoldExpired(
                f"Bluetooth recovery held the {configured_adapter()} lease for its "
                f"{self.hold_limit:g}s limit and was abandoned so the other collector "
                "can recover."
            ) from expired
        finally:
            if lock is not None:
                lock.release()
