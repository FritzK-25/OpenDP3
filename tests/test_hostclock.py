"""A host that slept and a collector that stalled are different evidence.

A suspend means the radio link and the capture were down; a stall means the
process was awake but its loop did not run, so notifications queued and were
stamped late. Only the first warrants a pinned incident and a new segment.
"""
import sys
import time

import pytest

from openpowerstation.events import DIAGNOSTIC_KINDS, GAP_KINDS, PIN_KINDS, SAFE_EVENT_KINDS, SEGMENT_KINDS
from openpowerstation.hostclock import GAP_SECONDS, SLEEP_EVIDENCE, STALL_REPORT_INTERVAL, LoopWatch, host_clocks


class Host:
    """Hand-driven clocks: ``awake`` stops while suspended, ``total`` does not."""

    def __init__(self):
        self.awake = 1_000.0
        self.asleep = 0.0

    def clocks(self):
        return (lambda: self.awake), (lambda: self.awake + self.asleep)

    def run(self, seconds):
        self.awake += seconds

    def sleep(self, seconds):
        self.asleep += seconds


def seen(gap):
    return None if gap is None else (gap.kind, round(gap.seconds, 3), round(gap.asleep, 3))


def test_a_long_interval_while_awake_is_a_loop_stall_not_a_suspend():
    host = Host()
    watch = LoopWatch(host.clocks())
    host.run(0.2)
    assert watch.tick() is None
    host.run(12)
    assert seen(watch.tick()) == ("loop_stall", 12, 0)


def test_time_the_host_spent_asleep_is_a_suspend():
    host = Host()
    watch = LoopWatch(host.clocks())
    host.run(0.2)
    host.sleep(30)
    assert seen(watch.tick()) == ("host_suspend", 30.2, 30)


def test_a_short_suspend_inside_a_long_gap_is_still_a_suspend():
    # Neither part alone passes the threshold, but the host did sleep and the
    # link cannot have survived it untouched.
    host = Host()
    watch = LoopWatch(host.clocks())
    host.run(8)
    host.sleep(SLEEP_EVIDENCE + 2)
    assert seen(watch.tick()) == ("host_suspend", 11, 3)


@pytest.mark.parametrize("gap", ["run", "sleep"])
def test_gaps_up_to_the_threshold_are_ordinary(gap):
    host = Host()
    watch = LoopWatch(host.clocks())
    getattr(host, gap)(GAP_SECONDS)
    assert watch.tick() is None


def test_clock_jitter_is_not_mistaken_for_sleep():
    # Windows reads two tick-driven counters; they can disagree by one tick.
    host = Host()
    watch = LoopWatch(host.clocks())
    host.run(12)
    host.sleep(0.016)
    assert seen(watch.tick())[0] == "loop_stall"


def test_repeated_stalls_are_rate_limited_and_counted():
    host = Host()
    watch = LoopWatch(host.clocks())
    host.run(12)
    assert watch.tick().suppressed == 0
    for _ in range(3):
        host.run(11)
        assert watch.tick() is None
    host.run(STALL_REPORT_INTERVAL)
    reported = watch.tick()
    assert seen(reported) == ("loop_stall", STALL_REPORT_INTERVAL, 0)
    assert reported.suppressed == 3


def test_a_suspend_is_never_rate_limited():
    host = Host()
    watch = LoopWatch(host.clocks())
    host.run(12)
    assert watch.tick().kind == "loop_stall"
    for _ in range(2):
        host.sleep(20)
        assert watch.tick().kind == "host_suspend"


def test_platform_clocks_advance_together_while_the_host_is_awake():
    awake, total = host_clocks()
    before = awake(), total()
    time.sleep(0.25)
    ran, elapsed = awake() - before[0], total() - before[1]
    assert 0.2 <= ran < 5
    # Far below SLEEP_EVIDENCE: an awake host never reads as a sleeping one.
    assert abs(elapsed - ran) < 0.1


def test_supported_platforms_get_two_distinct_clocks():
    # OpenPowerstation runs on Windows (desktop) and Linux (add-on). Handing back one
    # clock twice there would silently turn every suspend into a stall.
    assert sys.platform == "win32" or sys.platform.startswith("linux")
    awake, total = host_clocks()
    assert awake is not total


def test_loop_stall_is_a_recognized_diagnostic_that_neither_splits_nor_pins():
    assert "loop_stall" in DIAGNOSTIC_KINDS <= SAFE_EVENT_KINDS
    assert not DIAGNOSTIC_KINDS & (GAP_KINDS | SEGMENT_KINDS | PIN_KINDS)
    # A real suspend keeps its evidence meaning.
    assert "host_suspend" in GAP_KINDS & PIN_KINDS
