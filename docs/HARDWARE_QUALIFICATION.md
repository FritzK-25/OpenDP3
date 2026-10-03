# Hardware qualification — incomplete

Software tests cannot qualify a physical battery. Complete this checklist
against the selected DP3 and its actual firmware. Do not deliberately induce
a battery error, exceed operating limits, or change safety protections.

## Initial connection

- Confirm the Windows BLE adapter is detected and the DP3 is advertised.
- Select the exact device in local setup; record firmware version labels from
  the EcoFlow app without changing them.
- Enter the owner's user ID locally, or explicitly log in once to retrieve it.
- Close the phone's BLE connection. Start recording.
- Confirm authenticated local operation and inspect actual fields/cadence.
  Missing temperature/error data is a coverage limitation, not a zero or success.
- Compare a few steady values with the device display. To use phone BLE,
  release OpenPowerstation first; note the resulting capture gap.
- Confirm Release remains disconnected until Resume.

## Offline operation and ordinary use

- After setup, disconnect this PC's internet while leaving Bluetooth enabled.
  Confirm recording continues and a stopped recorder can reconnect with the
  stored user ID without a network login.
- During ordinary, already-safe operation, annotate observed conditions
  (idle, existing load, AC/PV input). Do not wire experimental power setups or
  force conditions intended to trigger an error.
- Verify gaps are visible if Bluetooth is released or the PC sleeps.
- Compare the Coverage tab's actual inter-arrival intervals with the desired
  diagnostic resolution. Sub-second causal ordering is not established merely
  because Windows assigns fine-grained timestamps.

## Eight-hour recording

- Keep the host awake and powered, with sufficient disk space.
- Run record --hours 8, or record for eight hours in the desktop application.
- Record start/end time, exact firmware labels, observed fields, median
  cadence, communication gaps, decode failures, and storage health.
- Confirm the stopped recording can be replayed and exported.
- Verify there are no unexplained pending/invalid frames or unexpected gaps.
  Any gap must be documented; a session's duration alone is not continuous coverage.
- Inspect at least one manual incident with its surrounding data.
- An actual fault is not necessary to qualify collection reliability.

## Qualification record

| Item | Result |
|---|---|
| Windows BLE adapter and DP3 discovery | Passed for the 2026-08-30 attempt; one authenticated segment was recorded |
| Actual-device authentication | Passed: local read-only capture authenticated over encryption type 7 |
| Actual sensor coverage/cadence | 30 fields and 621,348 measurements; primary telemetry median interval 1.094 s |
| Offline collection/reconnection | Pending |
| Eight-hour physical recording | **Incomplete:** capture ended unexpectedly after 6 h 19 min 38.6 s; no clean session end was stored |
| Firmware versions | Not entered for this attempt |
| Fault interpretation | Unverified; no firmware-cause conclusion |

An accelerated synthetic eight-hour timeline only tests storage and playback.
It does not satisfy the physical eight-hour criterion.

The short capture revealed disconnected extra-battery slots publishing zero-filled
reserved data. Decoder 0.1.1 requires the connection flag before displaying those
community-mapped measurements. Original 0.1.0 evidence remains unchanged; replay
comparison across decoder versions intentionally reports the difference.

## 2026-08-30 physical attempt

The requested eight-hour run authenticated at **2026-08-30 20:08:57.219 UTC**
(16:08:57 EDT). Its final frame arrived at **2026-08-31 02:28:35.864 UTC**
(22:28:35 EDT), giving 22,778.584 seconds, or **6 h 19 min 38.6 s**, of
authenticated frame receipts. Both recorded process IDs are no longer running.
The session still says `recording`, has no `end_t`, and the status log ends with
`recording: Authenticated local Bluetooth session.` The error log is empty and
the stop-request file is absent. This is consistent with an unclean termination,
but the available evidence does not establish whether the process, Windows, or
the host was interrupted. The run is about 1 h 40 min short and does not satisfy
the eight-hour acceptance requirement.

Within the captured interval, continuity was good: 52,813 frames occupy one
connection segment, with one `connected` event and no disconnect, silence,
host-suspend, clock-change, corrupt-transport, connection-failed, or capture-error
event. The largest positive inter-frame interval inside the segment was 1.125 s;
none exceeded five seconds. This supports a continuous 6 h 19 min capture only;
it cannot account for the abrupt end because no terminal event was committed.

The database contains 41,422 decoded frames and 11,391 preserved unknown-message
frames (21.57%), totaling 14,768,650 raw bytes. No frame is pending or marked
invalid. All 52,813 frames have a sequence value; none exposes a source timestamp.
There are 621,348 numeric observations across 30 mapped fields. Primary BMS/CMS
fields updated at a median 1.094 s interval (95th percentile 1.125 s); their
largest interval was 1.25 s. The three plug-in groups had the same median cadence
and a largest interval of 2.25 s.

Observed cell temperatures stayed at 22–25 °C and MOS temperatures at 22–26 °C.
The largest change between consecutive fresh readings was 1 °C for each of the
four temperature fields. There were no suspect-telemetry or device-error events,
and all nine observed error fields remained zero. Six raw
`cms_chg_dsg_state` transitions were recorded; they are not interpreted here as
faults. AC input reached 1,557 W, BMS power ranged from -313 to 1,374 W, aggregate
output reached 44 W, and both PV inputs remained at zero. SOC moved during the
run and ended at 79.458%; its minimum was 77.500%. These are device-reported
values, not independent physical measurements.

No incident window or manual marker was recorded, so the manual-incident part of
the checklist was not exercised. Firmware and operating-condition metadata are
also empty for this session. No offline/internet-disconnection test is evidenced.

Read-only decoder replay checked all 52,813 frames with zero mismatches, zero
invalid frames, and zero pending frames; 11,391 remained unknown. The stored
decoder is `dp3-mr521/0.1.1+7cde8e592258` and the replay decoder is 0.1.2, so
`decoder_changed=true` is expected. SQLite `quick_check` returned `ok` and the
database remained in WAL mode. At inspection, the main database was 238,387,200
bytes, its WAL was 475,946,552 bytes, free space was about 100 GiB, and there was
no sign of storage exhaustion. The large WAL may contain committed evidence;
preserve the database, `-wal`, and `-shm` files together before any maintenance.

A new qualification attempt is required. Record firmware/conditions, add a
manual marker during ordinary safe use, and complete at least eight authenticated
hours with a clean stopped session and successful replay/export. The offline
reconnect check remains a separate pending item. Do not induce a battery fault.
