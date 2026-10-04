"""Shared evidence semantics for every event the recorders emit."""
GAP_KINDS = frozenset({
    "disconnected", "silence", "released", "corrupt_transport", "host_suspend",
    "capture_error", "capture_gap", "session_timeout", "session_error",
    "session_lease_expired", "disconnect_error",
})
SEGMENT_KINDS = GAP_KINDS | {"connected", "telemetry_resumed"}
PIN_KINDS = frozenset({"disconnected", "manual", "corrupt_transport", "capture_error", "host_suspend"})
SAFE_EVENT_KINDS = SEGMENT_KINDS | PIN_KINDS | {
    "suspect_telemetry", "device_error", "state_change", "clock_change", "connection_failed",
    "control", "control_refused", "control_unverified", "unmapped_change",
}


def contains_gaps(events, start, end):
    return any(event["kind"] in GAP_KINDS and start <= event["t"] <= end for event in events)
