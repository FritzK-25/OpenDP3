"""Shared evidence semantics for every event the recorders emit."""
GAP_KINDS = frozenset({
    "disconnected", "silence", "released", "corrupt_transport", "host_suspend",
    "capture_error", "capture_gap", "session_timeout", "session_error",
    "session_lease_expired", "disconnect_error",
})
SEGMENT_KINDS = GAP_KINDS | {"connected", "telemetry_resumed"}
PIN_KINDS = frozenset({"disconnected", "manual", "corrupt_transport", "capture_error", "host_suspend"})
# Pins the collector raises about its own link or host, not about what the
# device reported. They keep context rather than evidence, so their protection
# lapses (Store.maintain). Every other pin -- manual, device_error,
# suspect_telemetry, unmapped_change, a supplied capture_error -- is kept until
# a person removes it, and one such reason in a merged window keeps the whole
# window. capture_gap pins through jackery_health rather than PIN_KINDS.
COLLECTOR_PIN_KINDS = frozenset({"disconnected", "corrupt_transport", "host_suspend", "capture_gap"})
# Collector diagnostics: recorded and exported, but they neither split a
# segment nor pin history. A loop stall loses no frames -- notifications queue
# and are stamped late -- so treating it as a gap would pin routine history.
DIAGNOSTIC_KINDS = frozenset({"loop_stall"})
SAFE_EVENT_KINDS = SEGMENT_KINDS | PIN_KINDS | DIAGNOSTIC_KINDS | {
    "suspect_telemetry", "device_error", "state_change", "clock_change", "connection_failed",
    "control", "control_refused", "control_unverified", "unmapped_change",
}


def contains_gaps(events, start, end):
    return any(event["kind"] in GAP_KINDS and start <= event["t"] <= end for event in events)
