"""Regression coverage for query-level session liveness."""

from openpowerstation.bridge import collector_state, state_payload
from openpowerstation.queries import latest


def test_latest_liveness_uses_one_newest_receipt_row(evidence, packet):
    """A fresher wall clock on an older frame must not make the session live."""
    store, recorder = evidence
    fresh_utc = recorder.start_utc + int(100e9)
    stale_utc = recorder.start_utc
    # One real observation at t=0, so the session has telemetry that can age.
    # Without it the state would be "waiting" for want of any measurement, which
    # is a different claim than the stale-receipt one under test here.
    recorder.ingest(packet(seq=1, bms_batt_soc=80), recorder.start_utc, recorder.start_mono)

    older_id = store.add_raw(
        recorder.sid, 0, fresh_utc, recorder.start_mono, 0.0, b"older", fresh_utc
    )
    newer_id = store.add_raw(
        recorder.sid, 0, stale_utc, recorder.start_mono + int(1e9), 1.0,
        b"newer", stale_utc + int(1e9),
    )
    assert older_id < newer_id

    reading = latest(store.path, recorder.sid, keys=["bms_batt_soc"])
    assert reading["count"] == 3
    assert reading["last_t"] == 1.0
    assert reading["last_utc_ns"] == stale_utc

    # The older receipt is only one second old by wall clock, while the newest
    # receipt is 101 seconds old. Independent MAX() aggregates would incorrectly
    # combine the older frame's fresh utc_ns with the newer frame's id/t and
    # report the session live.
    state, live = collector_state(
        reading, now_ns=fresh_utc + int(1e9), stale_seconds=45,
    )
    assert (state, live) == ("stale", False)


def test_replayed_elapsed_time_does_not_reopen_completed_incident(evidence, packet):
    """Incident progress follows max elapsed time, not the newest receipt's t."""
    store, recorder = evidence
    store.incident(recorder.sid, 50.0, "test", before=0, after=10)
    # Telemetry at t=100 so the session is genuinely live at t=102; the incident
    # question under test is independent of whether measurements are arriving.
    recorder.ingest(packet(seq=1, bms_batt_soc=80),
                    recorder.start_utc + int(100e9), recorder.start_mono + int(100e9))

    first_id = store.add_raw(
        recorder.sid, 0, recorder.start_utc + int(100e9),
        recorder.start_mono + int(100e9), 100.0, b"progress", recorder.start_utc + int(100e9),
    )
    replay_id = store.add_raw(
        recorder.sid, 0, recorder.start_utc + int(101e9),
        recorder.start_mono + int(20e9), 20.0, b"replay", recorder.start_utc + int(20e9),
    )
    assert first_id < replay_id

    reading = latest(store.path, recorder.sid, keys=["bms_batt_soc"])
    assert reading["last_t"] == 20.0
    assert reading["last_utc_ns"] == recorder.start_utc + int(101e9)
    assert reading["incidents"] == 0

    payload, live = state_payload(
        reading, now_ns=recorder.start_utc + int(102e9), stale_seconds=45,
    )
    assert live
    assert payload["incident"] == "OFF"


def test_housekeeping_frames_do_not_pass_as_telemetry_freshness(evidence, packet):
    """A device message that maps to no field must not look like fresh telemetry.

    The DP3 keeps emitting src=0x35 cmd_set=0x32 messages while it has stopped
    uploading measurements entirely. Those are recorded as frames, so frame age
    resets while every field ages out, and a total telemetry outage reads as a
    healthy link serving old data. The two ages have to be reported separately.
    """
    from openpowerstation.vendor.packet import Packet
    store, recorder = evidence

    # One real measurement frame, then a long silence broken only by a message
    # the decoder maps to nothing at all.
    recorder.ingest(packet(seq=1, bms_max_cell_temp=23),
                    recorder.start_utc, recorder.start_mono)
    housekeeping = Packet(0x35, 0x21, 0x32, 0x07, b"housekeeping").to_bytes()
    recorder.ingest(housekeeping,
                    recorder.start_utc + int(120e9), recorder.start_mono + int(120e9))

    reading = latest(store.path)
    frame_arrival = reading["last_utc_ns"]
    telemetry_arrival = reading["last_telemetry_utc_ns"]

    # The housekeeping frame is the newest frame; it is not newest telemetry.
    assert frame_arrival == recorder.start_utc + int(120e9)
    assert telemetry_arrival == recorder.start_utc
    assert (frame_arrival - telemetry_arrival) / 1e9 == 120


def test_housekeeping_alone_does_not_hold_the_collector_in_recording(evidence, packet):
    """"recording" while every sensor it feeds is expired cannot both be true.

    collector_state used to measure freshness from the newest frame of any kind,
    so one undecodable housekeeping frame held the Home Assistant entity at
    "recording" and the telemetry flag at online through a total outage.
    """
    from openpowerstation.vendor.packet import Packet
    store, recorder = evidence
    recorder.ingest(packet(seq=1, bms_batt_soc=80), recorder.start_utc, recorder.start_mono)
    housekeeping = Packet(0x35, 0x21, 0x32, 0x07, b"housekeeping").to_bytes()
    recorder.ingest(housekeeping,
                    recorder.start_utc + int(120e9), recorder.start_mono + int(120e9))

    reading = latest(store.path)
    # The housekeeping frame is one second old; telemetry is 121 seconds old.
    now = recorder.start_utc + int(121e9)
    assert (now - reading["last_utc_ns"]) / 1e9 == 1
    assert (now - reading["last_telemetry_utc_ns"]) / 1e9 == 121

    state, live = collector_state(reading, now_ns=now, stale_seconds=45)
    assert (state, live) == ("stale", False)

    payload, published_live = state_payload(reading, now_ns=now, stale_seconds=45)
    assert payload["collector_state"] == "stale"
    assert not published_live
    assert payload["bms_batt_soc"] is None
