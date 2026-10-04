"""Transport-independent recording, anomaly detection, and incident preservation."""
from collections import OrderedDict
from dataclasses import dataclass
import hashlib
import time

from .decoder import decode
from .events import SEGMENT_KINDS, PIN_KINDS
from .health import DP3_ROLES, Thresholds, measurement_findings
from .jackery_health import findings
from .protocol import CONTROL_FEEDBACK_FIELDS, OUTPUT_STATES, ProtocolError, parse_packet
from .storage import COLLECTOR_PIN_BYTES, COLLECTOR_PIN_DAYS, Store

# Raw DP3 state fields whose every change is a state_change event.
STATE_KEYS = frozenset({"cms_bms_run_state", "cms_chg_dsg_state", "plug_in_info_ac_charger_flag"})
# The AC outputs' own feedback. A DP3 ``control`` event says only that the write
# left the radio, since the DP3 acknowledges nothing; a change here is what says
# the output moved, whoever moved it. Only the low two bits are the outlet
# state, so the bits above them never raise an event on their own.
OUTPUT_KEYS = frozenset(CONTROL_FEEDBACK_FIELDS.values())

# Seconds one storage maintenance pass may hold the capture loop. The pass runs
# inside the frame callback, so anything longer delays receipt stamps; work
# left over resumes a minute later.
MAINTENANCE_BUDGET = 0.05
# A pass slower than this is named in the app log, so a stall can be traced.
SLOW_MAINTENANCE = 1.0

@dataclass(frozen=True)
class Compaction:
    """How old ordinary history must be before it is thinned, and to what.

    Only the observation path passes one, so the DP3 recorder keeps its existing
    delete-after-seven-days behaviour untouched by construction.
    """
    after_seconds: float = 172_800
    bucket_seconds: float = 60
    peak_keys: tuple = ()

class Recorder:
    def __init__(self, store: Store, *, utc_ns=None, mono_ns=None,
                 synthetic=False, firmware="", conditions="", expected_interval=None,
                 retain_days=7, health=None, compaction=None):
        # expected_interval lets the observation path tell a missing sample from
        # an idle one; retain_days=None keeps history forever and leaves thinning
        # to the downsampler instead of deleting outright. ``health`` is the one
        # set of anomaly thresholds both ingest paths judge by (health.py).
        self.store = store
        self.start_utc = time.time_ns() if utc_ns is None else utc_ns
        self.start_mono = time.monotonic_ns() if mono_ns is None else mono_ns
        self.segment = 0
        self.last_t = 0.0
        self.seen = OrderedDict()
        self.previous = {}
        self.last_clock = None
        self.last_maintenance = 0.0
        # How long the latest maintenance pass took; a loop_stall reports it.
        self.last_maintenance_seconds = None
        self.count = 0
        self.expected_interval = expected_interval
        self.retain_days = retain_days
        self.health = health or Thresholds()
        self.compaction = compaction
        self.unmapped_seen = {}
        self.last_observation_t = None
        # Undecodable packets in a row: one corrupt_transport per run.
        self.undecodable = 0
        # What ingest() made of the latest frame: its frame status, or
        # decoded_unmapped for a decoded upload with no mapped field, then its
        # route (src/cmd_set/cmd_id) when it parsed. Says what kept arriving
        # while no measurement did.
        self.last_outcome = None
        if store.space_low():
            self.reclaim()
        # Refused while free space is still below the reserve.
        self.sid = store.session(utc_ns=self.start_utc, mono_ns=self.start_mono,
                                 synthetic=synthetic, firmware=firmware, conditions=conditions)

    def timestamp(self, mono_ns):
        return (mono_ns-self.start_mono)/1e9

    def reclaim(self):
        """Prune-only start for a store below its free-space reserve.

        Applies this recorder's own policy in full, not a default one: the
        Jackery keeps its history and thins it, the DP3 deletes by age. The
        space goes back to the filesystem before anything new is written.
        """
        now = self.start_utc
        summary = self.store.maintain(now, days=self.retain_days, stop_at_reserve=False)
        if self.compaction:
            summary["deleted"] += self.store.downsample(
                now, grace_seconds=self.compaction.after_seconds,
                bucket_seconds=self.compaction.bucket_seconds,
                peak_keys=self.compaction.peak_keys)["deleted"]
        self.store.release_space()
        print(f"[maintenance] {self.store.path.name}: below the free-space reserve at start; "
              f"retention deleted {summary['deleted']} frames and lapsed {summary['pins_lapsed']} "
              "collector incident window(s) to get back above it", flush=True)

    def maintain(self, now_ns):
        summary = self.store.maintain(now_ns, days=self.retain_days, time_budget=MAINTENANCE_BUDGET)
        self.last_maintenance_seconds = summary["seconds"]
        if summary["seconds"] > SLOW_MAINTENANCE:
            print(f"[maintenance] {self.store.path.name}: pass took {summary['seconds']:.1f}s, "
                  f"deleted {summary['deleted']} frames and {summary['events_deleted']} events"
                  + ("" if summary["complete"] else "; more remains for later passes"), flush=True)
        if summary.get("pins_lapsed"):
            print(f"[maintenance] {self.store.path.name}: protection lapsed on {summary['pins_lapsed']} "
                  f"collector incident window(s), older than {COLLECTOR_PIN_DAYS} days or over "
                  f"{COLLECTOR_PIN_BYTES / 1e9:g} GB together", flush=True)

    def event(self, kind, detail="", utc_ns=None, mono_ns=None, *, pin=False):
        # ``pin`` protects this one event's window from thinning, for a kind
        # that does not always warrant it.
        utc_ns = time.time_ns() if utc_ns is None else utc_ns
        mono_ns = time.monotonic_ns() if mono_ns is None else mono_ns
        t = self.timestamp(mono_ns)
        self.last_t = max(self.last_t, t)
        if kind in SEGMENT_KINDS:
            self.segment += 1
            self.previous.clear()
            self.seen.clear()
            if kind != "corrupt_transport":
                self.undecodable = 0
        self.store.event(self.sid, t, utc_ns, kind, detail)
        if pin or kind in PIN_KINDS:
            self.store.incident(self.sid, t, kind)

    def ingest(self, raw: bytes, utc_ns=None, mono_ns=None):
        # Filter auth by header before ANY disk write, including the SQLite WAL.
        # The transport already filters these; this protects replay/import callers too.
        if len(raw) >= 18 and raw[0] == 0xAA and raw[1] in (3, 0x13) and raw[16] == 0x35:
            self.last_outcome = "authentication"
            return False
        utc_ns = time.time_ns() if utc_ns is None else utc_ns
        mono_ns = time.monotonic_ns() if mono_ns is None else mono_ns
        t = self.timestamp(mono_ns)
        if t < 0 or t < self.last_t:
            # Preserve payload and arrival order, but never compare reordered observations.
            self.previous.clear()
        if self.last_clock:
            prev_utc, prev_mono = self.last_clock
            drift = ((utc_ns-prev_utc)-(mono_ns-prev_mono))/1e9
            if abs(drift) > 2:
                self.event("clock_change", f"Host wall clock shifted by {drift:.3f} seconds.", utc_ns, mono_ns)
        self.last_clock = utc_ns, mono_ns
        self.last_t = max(self.last_t, t)
        # Monotonic-derived retention time is immune to in-session wall clock changes.
        frame_id = self.store.add_raw(self.sid, self.segment, utc_ns, mono_ns, t, raw,
                                      self.start_utc + int(t*1e9))
        self.count += 1
        try:
            packet = parse_packet(raw)
        except ProtocolError:
            self.store.invalid(frame_id)
            # Every one is kept, but the run is the event: a stale session key
            # makes the whole stream undecodable, and an event per frame put
            # hundreds of reasons into one pinned incident.
            self.undecodable += 1
            if self.undecodable == 1:
                self.event("corrupt_transport", "Undecodable packet preserved locally; any that "
                           "follow it in a row are preserved without an event each.",
                           utc_ns, mono_ns)
            self.last_outcome = "invalid_packet"
            return False
        self.undecodable = 0
        # Defense in depth: the transport must not pass authentication packets.
        if packet.cmd_set == 0x35:
            with self.store.conn:
                self.store.conn.execute("DELETE FROM frames WHERE id=?", (frame_id,))
            self.count -= 1
            self.last_outcome = "authentication"
            return False
        digest = hashlib.sha256(raw).digest()
        # Sequence zero often means no counter: only consecutive identical frames
        # are ambiguous then. Nonzero counters permit a bounded retransmit check.
        duplicate = digest in self.seen and 0 <= t-self.seen[digest] <= 5
        if packet.seq == b'\0'*4:
            duplicate = bool(self.seen) and next(reversed(self.seen)) == digest
        self.seen[digest] = t
        self.seen.move_to_end(digest)
        if len(self.seen) > 256:
            self.seen.popitem(last=False)
        decoded = decode(packet)
        self.store.interpret(frame_id, packet, decoded, duplicate)
        if not duplicate:
            # The temperature and charge rules the Jackery runs too, judged
            # before any value below replaces the one they compare against.
            for kind, detail in measurement_findings(DP3_ROLES, decoded.measurements,
                                                     self.previous, t, self.health):
                self.event(kind, detail, utc_ns, mono_ns)
                self.store.incident(self.sid, t, kind)
            for key, value in decoded.measurements.items():
                old = self.previous.get(key)
                if (key == "errcode" or key.endswith("_err_code")) and value != 0 and (not old or old[1] != value):
                    self.event("device_error", f"{key} reported raw code {value:g}; meaning is unverified.", utc_ns, mono_ns)
                    self.store.incident(self.sid, t, "device_error")
                elif old and old[1] != value and key in STATE_KEYS:
                    self.event("state_change", f"{key}: {old[1]:g} -> {value:g} (raw).", utc_ns, mono_ns)
                elif old and key in OUTPUT_KEYS and int(old[1]) % 4 != int(value) % 4:
                    self.event("state_change", f"{key}: {old[1]:g} -> {value:g} (raw; output "
                               f"{OUTPUT_STATES[int(old[1]) % 4]} -> {OUTPUT_STATES[int(value) % 4]}).",
                               utc_ns, mono_ns)
                self.previous[key] = (t, value)
        status = ("repeated_unverified" if duplicate else
                  "decoded_unmapped" if decoded.status == "decoded" and not decoded.measurements
                  else decoded.status)
        self.last_outcome = f"{status} {packet.src:02X}/{packet.cmd_set:02X}/{packet.cmd_id:02X}"
        if t - self.last_maintenance >= 60:
            self.maintain(self.start_utc + int(t*1e9))
            self.last_maintenance = t
        return bool(decoded.measurements) and not duplicate

    def ingest_observation(self, raw: bytes, fields: dict, measurements: dict,
                           utc_ns=None, mono_ns=None, quality="ble_observed"):
        """Record an already-decoded read-only observation from another transport."""
        utc_ns = time.time_ns() if utc_ns is None else utc_ns
        mono_ns = time.monotonic_ns() if mono_ns is None else mono_ns
        t = self.timestamp(mono_ns)
        if t < self.last_t:
            self.previous.clear()
        self.last_t = max(self.last_t, t)
        frame_id = self.store.add_raw(self.sid, self.segment, utc_ns, mono_ns, t, raw,
                                      self.start_utc + int(t * 1e9))
        self.count += 1
        self.store.interpret_observation(frame_id, fields, measurements, quality)
        # Decide what is wrong with this observation before overwriting the
        # previous values it has to be compared against.
        for kind, detail, pin in findings(measurements, fields.get("properties", {}),
                                          self.previous, self.unmapped_seen, t,
                                          expected_interval=self.expected_interval,
                                          last_t=self.last_observation_t,
                                          thresholds=self.health):
            self.event(kind, detail, utc_ns, mono_ns)
            if kind in SEGMENT_KINDS:
                # The observation after the gap belongs to the new segment too.
                with self.store.conn:
                    self.store.conn.execute("UPDATE frames SET segment=? WHERE id=?",
                                            (self.segment, frame_id))
            if pin:
                # Pinned windows are never thinned by the downsampler.
                self.store.incident(self.sid, t, kind)
        for key, value in measurements.items():
            old = self.previous.get(key)
            if old and old[1] != value and key in {"cms_batt_soc", "bms_batt_soc"}:
                self.event("state_change", f"{key}: {old[1]:g} -> {value:g} (observation).",
                           utc_ns, mono_ns)
            self.previous[key] = (t, value)
        self.last_observation_t = t
        if t - self.last_maintenance >= 60:
            now = self.start_utc + int(t * 1e9)
            self.maintain(now)
            if self.compaction:
                self.store.downsample(now, grace_seconds=self.compaction.after_seconds,
                                      bucket_seconds=self.compaction.bucket_seconds,
                                      peak_keys=self.compaction.peak_keys,
                                      max_buckets=100, time_budget=0.025)
            self.last_maintenance = t

    def finish(self, status="stopped", mono_ns=None):
        t = max(self.last_t, self.timestamp(time.monotonic_ns() if mono_ns is None else mono_ns))
        self.store.finish(self.sid, t, status)
