"""Transport-independent recording, anomaly detection, and incident preservation."""
from collections import OrderedDict
from dataclasses import dataclass
import hashlib
import time

from .decoder import decode
from .events import SEGMENT_KINDS, PIN_KINDS
from .jackery_health import Thresholds, findings
from .protocol import ProtocolError, parse_packet
from .storage import Store

@dataclass(frozen=True)
class Compaction:
    """How old ordinary history must be before it is thinned, and to what.

    Only the observation path passes one, so the DP3 recorder keeps its existing
    delete-after-seven-days behaviour untouched by construction.
    """
    after_seconds: float = 172_800
    bucket_seconds: float = 60
    peak_keys: tuple = ()

@dataclass
class Settings:
    temperature_jump: float = 10
    temperature_window: float = 5

class Recorder:
    def __init__(self, store: Store, *, settings=None, utc_ns=None, mono_ns=None,
                 synthetic=False, firmware="", conditions="", expected_interval=None,
                 retain_days=7, health=None, compaction=None):
        # expected_interval lets the observation path tell a missing sample from
        # an idle one; retain_days=None keeps history forever and leaves thinning
        # to the downsampler instead of deleting outright.
        self.store = store
        self.settings = settings or Settings()
        self.start_utc = time.time_ns() if utc_ns is None else utc_ns
        self.start_mono = time.monotonic_ns() if mono_ns is None else mono_ns
        self.sid = store.session(utc_ns=self.start_utc, mono_ns=self.start_mono,
                                 synthetic=synthetic, firmware=firmware, conditions=conditions)
        self.segment = 0
        self.last_t = 0.0
        self.seen = OrderedDict()
        self.previous = {}
        self.last_clock = None
        self.last_maintenance = 0.0
        self.count = 0
        self.expected_interval = expected_interval
        self.retain_days = retain_days
        self.health = health or Thresholds()
        self.compaction = compaction
        self.unmapped_seen = {}
        self.last_observation_t = None

    def timestamp(self, mono_ns):
        return (mono_ns-self.start_mono)/1e9

    def event(self, kind, detail="", utc_ns=None, mono_ns=None):
        utc_ns = time.time_ns() if utc_ns is None else utc_ns
        mono_ns = time.monotonic_ns() if mono_ns is None else mono_ns
        t = self.timestamp(mono_ns)
        self.last_t = max(self.last_t, t)
        if kind in SEGMENT_KINDS:
            self.segment += 1
            self.previous.clear()
            self.seen.clear()
        self.store.event(self.sid, t, utc_ns, kind, detail)
        if kind in PIN_KINDS:
            self.store.incident(self.sid, t, kind)

    def ingest(self, raw: bytes, utc_ns=None, mono_ns=None):
        # Filter auth by header before ANY disk write, including the SQLite WAL.
        # The transport already filters these; this protects replay/import callers too.
        if len(raw) >= 18 and raw[0] == 0xAA and raw[1] in (3, 0x13) and raw[16] == 0x35:
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
            self.event("corrupt_transport", "Undecodable packet preserved locally.", utc_ns, mono_ns)
            return False
        # Defense in depth: the transport must not pass authentication packets.
        if packet.cmd_set == 0x35:
            with self.store.conn:
                self.store.conn.execute("DELETE FROM frames WHERE id=?", (frame_id,))
            self.count -= 1
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
            for key, value in decoded.measurements.items():
                old = self.previous.get(key)
                quality = decoded.quality[key]
                if (old and quality == "observed" and ("_temp" in key)
                        and 0 < t-old[0] <= self.settings.temperature_window
                        and abs(value-old[1]) >= self.settings.temperature_jump):
                    self.event("suspect_telemetry", f"{key}: {old[1]:g} -> {value:g} °C in {t-old[0]:.3f}s receipt time.",
                               utc_ns, mono_ns)
                    self.store.incident(self.sid, t, "suspect_telemetry")
                if (key == "errcode" or key.endswith("_err_code")) and value != 0 and (not old or old[1] != value):
                    self.event("device_error", f"{key} reported raw code {value:g}; meaning is unverified.", utc_ns, mono_ns)
                    self.store.incident(self.sid, t, "device_error")
                elif old and old[1] != value and key in {"cms_bms_run_state", "cms_chg_dsg_state", "plug_in_info_ac_charger_flag"}:
                    self.event("state_change", f"{key}: {old[1]:g} -> {value:g} (raw).", utc_ns, mono_ns)
                self.previous[key] = (t, value)
        if t - self.last_maintenance >= 60:
            self.store.maintain(self.start_utc + int(t*1e9), days=self.retain_days)
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
            self.store.maintain(now, days=self.retain_days)
            if self.compaction:
                self.store.downsample(now, grace_seconds=self.compaction.after_seconds,
                                      bucket_seconds=self.compaction.bucket_seconds,
                                      peak_keys=self.compaction.peak_keys,
                                      max_buckets=100, time_budget=0.025)
            self.last_maintenance = t

    def finish(self, status="stopped", mono_ns=None):
        t = max(self.last_t, self.timestamp(time.monotonic_ns() if mono_ns is None else mono_ns))
        self.store.finish(self.sid, t, status)
