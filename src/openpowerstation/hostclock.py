"""Tell a host that slept from a collector whose loop stopped running.

One clock cannot. ``time.monotonic()`` stops while Linux is suspended, so there
it never shows a sleep, and every long gap it does show is the process itself
not running. On Windows (Python 3.12 reads GetTickCount64) it keeps counting
through sleep, so there it shows both and cannot say which. Comparing a clock
that stops during suspend with one that does not separates the two: their
difference is time asleep, and the awake clock alone is how long the loop went
without running.
"""
from collections import namedtuple
import sys
import time

# A tick interval longer than this is worth recording. Unchanged from the single
# interval the collector used to test; shorter pauses are scheduling noise.
GAP_SECONDS = 10.0
# Asleep for at least this long within such an interval makes it a suspend. Far
# above the one-tick disagreement of the Windows counters, far below any sleep.
SLEEP_EVIDENCE = 1.0
# At most one loop_stall per this much awake time; the rest are only counted.
STALL_REPORT_INTERVAL = 600.0

Gap = namedtuple("Gap", "kind seconds asleep suppressed")


def _windows_clocks():
    import ctypes
    # A private handle, so declaring a return type here changes no one else's.
    kernel32 = ctypes.WinDLL("kernel32")
    kernel32.GetTickCount64.restype = ctypes.c_ulonglong
    unbiased = ctypes.c_ulonglong()

    def awake():
        # Working-state time only: excludes sleep and hibernation, 100 ns units.
        if not kernel32.QueryUnbiasedInterruptTime(ctypes.byref(unbiased)):
            raise OSError("QueryUnbiasedInterruptTime failed.")
        return unbiased.value / 1e7

    def total():
        # Milliseconds since the system started, time asleep included.
        return kernel32.GetTickCount64() / 1e3

    awake(), total()
    return awake, total


def _linux_clocks():
    boottime = time.CLOCK_BOOTTIME
    time.clock_gettime(boottime)
    # time.monotonic() is CLOCK_MONOTONIC, which excludes suspend;
    # CLOCK_BOOTTIME is the same clock with time suspended added.
    return time.monotonic, lambda: time.clock_gettime(boottime)


def host_clocks():
    """Return ``(awake, total)`` second clocks where only ``total`` counts suspend.

    A platform without a suspend-aware pair gets the monotonic clock twice. It
    then reports every long gap as a loop stall and never claims a suspend it
    cannot measure.
    """
    try:
        if sys.platform == "win32":
            return _windows_clocks()
        if sys.platform.startswith("linux"):
            return _linux_clocks()
    except (AttributeError, OSError):
        pass
    return time.monotonic, time.monotonic


class LoopWatch:
    """Classify the interval between two ticks of the collector's loop."""

    def __init__(self, clocks=None, *, threshold=GAP_SECONDS, report_interval=STALL_REPORT_INTERVAL):
        self.awake, self.total = clocks or host_clocks()
        self.threshold = threshold
        self.report_interval = report_interval
        self.last = self.awake(), self.total()
        self.stall_reported_at = None
        self.suppressed = 0

    def tick(self):
        """Return the ``Gap`` since the previous tick, or None for an ordinary one.

        A suspend is always returned. A stall is returned at most once per
        ``report_interval`` of awake time and counts the ones it held back.
        """
        awake, total = self.awake(), self.total()
        ran = awake - self.last[0]
        asleep = max(0.0, (total - self.last[1]) - ran)
        self.last = awake, total
        if ran + asleep <= self.threshold:
            return None
        if asleep >= SLEEP_EVIDENCE:
            return Gap("host_suspend", ran + asleep, asleep, 0)
        if self.stall_reported_at is not None and awake - self.stall_reported_at < self.report_interval:
            self.suppressed += 1
            return None
        gap = Gap("loop_stall", ran, 0.0, self.suppressed)
        self.stall_reported_at, self.suppressed = awake, 0
        return gap
