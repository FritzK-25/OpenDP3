"""The file handoff that carries control requests from a bridge to its collector.

A bridge never touches Bluetooth. It writes each request it has validated into
a directory beside the recording, and the collector that holds the radio drains
that directory. Both devices go through this one module, so what follows holds
for either of them rather than by convention in two copies, which had already
drifted apart:

- A request carries its own issue time, and runs only while
  0 <= age <= MAX_AGE_SECONDS. The age is taken immediately before the request
  would be sent (Request.lapsed), not once for a whole drain: a request that
  waited behind a slow write is as stale as one that waited on disk. A negative
  age means the wall clock moved backwards after the request was issued, so
  its real age is unknown and it is refused. Checking only the upper bound
  left such a request fresh for ever. A step back too small to make the age
  negative still lengthens that request's lease by the step, so by at most
  MAX_AGE_SECONDS.
- At most MAX_FILES requests wait on disk for either device. The oldest go
  first, and a request that cannot be written is dropped rather than retried:
  a stale output command is worse than a missing one.
- A drain consumes what it reads. A file it cannot remove is not acted on,
  or every later drain would read it and send it again.
- A drain returns one entry for each request that names a control: the
  request, or a Refusal saying why it will not run. Requests for the same
  control coalesce to the newest. The ones it replaces come back as refused,
  so each is still recorded once, and a burst of presses is not replayed as a
  burst of toggles. A file that names no control is discarded unrecorded:
  there is nothing to record it as.

Whether a request may run at all -- the saved policy, the DP3's guarded
payload, a live session -- stays the collector's decision, taken at the same
moment as the age.
"""
import contextlib
from dataclasses import dataclass
import itertools
import json
import os
from pathlib import Path
import re
import time

# How long a queued request stays valid. The DP3 collector drains five times a
# second and the Jackery collector once a poll, so anything older than this
# waited on a disconnected or busy device, and the person who pressed it has
# long since seen something else happen.
MAX_AGE_SECONDS = 30.0
# Far above normal human switching, but it keeps an absent collector from
# turning repeated MQTT presses into unbounded data-volume growth.
MAX_FILES = 64

# Beside each device's recording. The add-on empties both whenever it starts
# (home-assistant/opendp3/run.py, prepare).
DP3_DIRECTORY = "commands"
JACKERY_DIRECTORY = "jackery-commands"
DIRECTORIES = (DP3_DIRECTORY, JACKERY_DIRECTORY)

# Why a request did not run, as the collectors record it.
EXPIRED = "request expired before it could be sent"
FUTURE = ("request is dated later than the clock now reads; the clock moved back "
          "after it was issued, so its age is unknown")
SUPERSEDED = "superseded by a newer request for the same control"
UNDATED = "request carries no issue time"

# Control keys are allowlisted identifiers on both devices, and name the file.
_KEY = re.compile(r"[a-z0-9_]{1,64}")
# Two requests issued in one clock tick still get distinct files: on Windows,
# Python 3.12's time.time_ns() advances in steps of about half a millisecond.
_SEQUENCE = itertools.count()


@dataclass(frozen=True)
class Request:
    key: str
    value: bool | str
    issued_utc_ns: int
    # Set only by a sender that passed an arm-then-change interlock. The DP3
    # collector requires it; the Jackery has no such guard.
    guarded: bool = False

    def lapsed(self, max_age=MAX_AGE_SECONDS, *, now_ns=None):
        """Why this request may no longer run, or None while it still may.

        Ask immediately before sending: the answer is only as good as the
        moment it was taken.
        """
        age_ns = (time.time_ns() if now_ns is None else now_ns) - self.issued_utc_ns
        if age_ns < 0:
            return FUTURE
        if age_ns > max_age * 1e9:
            return EXPIRED
        return None


@dataclass(frozen=True)
class Refusal:
    """A request a drain will not hand over, and why."""
    key: str
    value: bool | str
    reason: str


def write_request(directory, key, value, *, guarded=None) -> bool:
    """Queue one request for the collector; False if it was dropped instead.

    Named uniquely and renamed into place, so a drain never reads a
    half-written request. Never raises: the bridges call this from their MQTT
    callbacks, and a failure there is a dropped request, not a stopped bridge.
    """
    if not (isinstance(key, str) and _KEY.fullmatch(key) and isinstance(value, (bool, str))):
        return False
    directory = Path(directory)
    tmp = None
    try:
        directory.mkdir(parents=True, exist_ok=True)
        # The collector is the only consumer. While it is absent, keep the
        # handoff bounded by discarding the oldest before accepting another.
        pending = sorted(directory.glob("*.json"))
        for old in pending[:max(0, len(pending) - (MAX_FILES - 1))]:
            with contextlib.suppress(OSError):
                old.unlink()
        # If filesystem errors prevented enough pruning, fail closed by dropping
        # the new request instead of growing the queue beyond its ceiling.
        if sum(1 for _ in directory.glob("*.json")) >= MAX_FILES:
            return False
        issued = time.time_ns()
        request = {"key": key, "value": value, "issued_utc_ns": issued}
        if guarded is not None:
            request["guarded"] = bool(guarded)
        # Issue time first, so name order is issue order for the pruning above.
        # Keep the per-process tie-breaker fixed-width: lexical path order is
        # also the queue order, including when a coarse clock gives a burst the
        # same issued timestamp.
        stem = f"{issued}-{os.getpid()}-{next(_SEQUENCE):020d}-{key}"
        tmp = directory / (stem + ".tmp")
        tmp.write_text(json.dumps(request), encoding="utf-8")
        tmp.replace(directory / (stem + ".json"))
        return True
    except OSError:
        if tmp is not None:
            with contextlib.suppress(OSError):
                tmp.unlink()
        return False


def _entry(raw):
    """The Request or Refusal a file's contents make, or None if they name no control."""
    if not isinstance(raw, dict):
        return None
    key, value = raw.get("key"), raw.get("value")
    if not (isinstance(key, str) and _KEY.fullmatch(key) and isinstance(value, (bool, str))):
        return None
    issued = raw.get("issued_utc_ns")
    if type(issued) is not int:
        # The file's own modification time is no substitute: it is not part
        # of the request, and a copy, a restore or a clock step all move it.
        return Refusal(key, value, UNDATED)
    return Request(key, value, issued, raw.get("guarded") is True)


def drain(directory) -> list:
    """Consume every queued request, oldest first, as Requests and Refusals.

    Refuses nothing on age: that is decided per request, immediately before
    it would be sent (Request.lapsed). Partly written ``.tmp`` files are left
    for the writer to finish.
    """
    directory = Path(directory)
    if not directory.is_dir():
        return []
    entries = []
    for path in sorted(directory.glob("*.json")):
        try:
            raw = json.loads(path.read_text("utf-8"))
        except (OSError, ValueError):
            raw = None
        try:
            path.unlink()
        except OSError:
            # Not consumed, so not acted on: the next drain reads it again,
            # and acting now would send it once per drain until it expired.
            continue
        entry = _entry(raw)
        if entry is not None:
            entries.append(entry)
    # Newest per control by issue time, then by order read.
    newest = {}
    for index, entry in enumerate(entries):
        if isinstance(entry, Request):
            rank = (entry.issued_utc_ns, index)
            if entry.key not in newest or rank > newest[entry.key]:
                newest[entry.key] = rank
    return [Refusal(entry.key, entry.value, SUPERSEDED)
            if isinstance(entry, Request) and newest[entry.key][1] != index else entry
            for index, entry in enumerate(entries)]
