# Software validation

Validated on Windows with Python 3.12.8 and the versions pinned in requirements.lock.

- Automated tests: run the pytest suite for the current authoritative count.
- Desktop QA: Qt playback, packet inspector, coverage, incident selection, and
  screenshot rendering exercised with a synthetic recording.
- Native BLE discovery: Windows BLE adapter detected; a DELTA Pro 3 advertisement
  was observed using encryption type 7. Device/account identifiers are omitted here.
- Accelerated storage/replay test: 28,782 synthetic frames spanning 28,799 seconds
  (an eight-hour timeline with a deliberately inserted 18-second gap).
  All 28,782 decoded again with zero mismatches, invalid frames, or pending frames.
  SQLite file size was approximately 63 MB. Generation and replay took about
  32 seconds; this was not an eight-hour wall-clock or hardware test.
- Shareable export: CSV, JSON, HTML and PNG inspected; separate private raw ZIP
  verified. Automated tests inject identifying strings to check their exclusion
  from shareable files.
- Dependency consistency: pip check passed.

Actual-device authentication and a short read-only capture subsequently passed.
BMS cell/MOS temperatures, SOC/SOH, power and error fields were observed, with
temperature updates approximately 1.1 seconds apart. Unknown message types were
preserved rather than discarded; no invalid frames were observed in the short run.

The live observation exposed zero-filled disconnected extra-battery slot data.
Decoder 0.1.1 added presence gating and three regression cases; at that stage
the test suite contained 42 passing tests. Historical 0.1.0 evidence is preserved unchanged.

The scheduled physical eight-hour attempt did not complete. It captured one
continuous authenticated segment for 6 h 19 min 38.6 s, then ended without a
clean session close or terminal event. The cause is not recorded. See
HARDWARE_QUALIFICATION.md for the audited counts, cadence, coverage, storage and
replay results. Actual internet-disconnection/offline reconnection testing remains
pending separately. No firmware diagnosis is established.

## 2026-08-30 hardware-attempt verification

The relevant session contains 52,813 frames: 41,422 decoded and 11,391 preserved
unknown messages, with no pending or invalid frames. It produced 621,348 numeric
observations across 30 fields. Temperature cadence had a median of 1.094 s and no
fresh temperature step exceeded 1 °C. All observed error fields stayed at zero;
no suspect-telemetry or device-error event was generated.

Replay with decoder 0.1.2 checked every frame against the stored decoder 0.1.1
measurements and found zero mismatches. SQLite `quick_check` returned `ok`; free
space was about 100 GiB, so storage exhaustion is not indicated. The database and
WAL together were about 681 MiB at inspection and must be preserved together.

The original process IDs are no longer running, but the session remains marked
`recording` with no end time. The status log ends at successful recording, the
error log is empty, and no stop file exists. With only 22,778.584 seconds between
the first and last frame, the run fails the eight-hour duration requirement. It
also lacks a manual marker, firmware/conditions metadata, and evidence of an
offline reconnect. No part of this result establishes a firmware fault.

## Standalone Windows build

OpenDP3.exe was built with Python 3.12.8 and PyInstaller 6.22.0, then copied
alone into an isolated directory. The frozen smoke test passed with Python
overrides removed and PATH restricted to Windows System32. All import paths
resolved into the extracted executable bundle, with no source checkout or
virtual environment imports. Windows Bluetooth adapter detection, native
crypto, protobuf decoding, SQLite, the Qt viewer, and PNG/HTML/CSV export passed.
The viewer rendered 882 synthetic frames; decoder replay had zero mismatches.
The screenshot was visually inspected. At that stage the application suite passed 42 tests.

The build initially collected an incompatible ICU DLL from an unrelated tool.
The build now sanitizes PATH inside Python as well as the PowerShell launcher,
and rejects native library sources outside the project environment, Python
installation, and Windows. The final artifact excludes account configuration
and recording databases. It is an unsigned local development build.

Packaging verification details: artifacts/packaging-verification.json.
The SHA-256 checksum is alongside OpenDP3.exe. Packaging verification is
synthetic and does not replace the ongoing physical eight-hour qualification.

## Security update — app 0.1.1 / decoder 0.1.2

On 2026-08-30 the supplied security review was checked against pinned upstream
source and addressed as recorded in [SECURITY_REVIEW.md](SECURITY_REVIEW.md).

- All **79 tests passed** in 4.50 seconds with Python 3.12.8. This includes
  enumeration of every two-byte session seed, rejection of all 2,176 unsupported
  seeds, valid derivation vectors, and a simulated handshake proving that an
  invalid seed stops before the user-ID proof is sent.
- Invalid-packet subclass handling, parse-log privacy, malformed configuration
  and login responses passed regression checks. Existing outbound allowlist,
  recorder/storage, credential-exclusion, replay, export and Qt tests passed.
- The retained AES class bodies match pinned upstream syntax trees; diagnostics
  crypto was removed without changing those codecs. Dependency consistency and
  generated protocol-reference checks passed; local document links resolved.
- `OpenDP3-0.1.1.exe` was built beside the running original executable, with
  **86,578,459 bytes** and SHA-256
  `521a3cd5217e1c600e653707a9aef037fbb92d596090c5a58ece12d635c480f9`.
  Its Windows file version is 0.1.1. It remains unsigned (`NotSigned`).
- Inspection of the executable's embedded Python module confirmed that the
  developer-key diagnostics class/helper names and public-key constant are
  absent. The retained CBC codecs are present. The outer archive contains no
  `config.json` or SQLite recording files.
- The copied executable passed the isolated smoke test with no source/venv
  import paths. It reported app 0.1.1 and decoder
  `dp3-mr521/0.1.2+7cde8e592258`, detected the Windows BLE adapter, rendered
  **882 synthetic frames**, and re-decoded all 882 with zero mismatches, unknown
  or invalid frames. Qt, crypto, SQLite and PNG/HTML/CSV export succeeded.
  The screenshot was inspected; synchronized chart gaps and synthetic labeling
  were visible. This test did not scan, authenticate or connect to the DP3.
- The updated decoder also re-read **20,756 already captured real packets** from
  the active session without modifying its database. It found zero numerical
  mismatches, invalid frames or pending frames; 4,477 packets remained unknown
  telemetry, as with the stored decoder. `decoder_changed=true` is expected
  because the session records decoder 0.1.1. This tests replay compatibility,
  not the new build's live authentication. The new setup warning was also
  rendered and inspected using an empty test folder, without loading real
  configuration or making a network request.

The original `OpenDP3.exe` checksum was unchanged. A read-only health check of
the ongoing physical run found status `recording`, 20,422 frames over 2.449
hours, and a most recent receipt 0.3 seconds old. Its loaded decoder remained
0.1.1, as expected for an already running process. No stop was requested.
These figures are a point-in-time health check, not eight-hour acceptance.

Current machine-readable build verification: `artifacts/packaging-verification.json`.
Source comparison details: `artifacts/security-review/source-verification.json`.
The updated executable still needs real-device reconnect and offline qualification
after the existing run. No firmware-cause or authenticated-evidence claim is made.

## Initial pull request — app 0.1.2

The committed source snapshot, including the sidebar/light-dark viewer and
single-source version/release tooling, passed **125 tests** in an isolated
checkout on 2026-08-30. The pinned protocol-reference check also passed. App
0.1.2 retains decoder `dp3-mr521/0.1.2+7cde8e592258`; the UI release does not
reinterpret existing measurements.

The same application/build source produced a local `OpenDP3-0.1.2.exe` of
86,620,987 bytes, SHA-256
`6f1e57a3bba87d24dc22d9e62a49ba3ba3a196030fe4ad82e2321f0b64848ad1`.
The copied executable passed isolated frozen verification: 882 synthetic frames,
zero replay mismatches, imports from its extracted bundle, Windows BLE adapter
detection, and no bundled configuration/database or removed diagnostics crypto.
The synthetic desktop screenshot was inspected. The binary remains unsigned.

Only source, tests, documentation, dependency/license notices and build scripts
are included in this initial PR. Executables, real recordings, account setup,
private evidence exports, local logs and generated packaging files are excluded.
The 72 tracked files passed a credential-pattern and excluded-path audit.
The local executable is not attached to the PR or published as a GitHub release.
Hardware/offline qualification remains pending; neither these tests nor the
synthetic frozen test replace the eight-hour actual-device requirement.
## Chart interaction update — app 0.1.3 / decoder unchanged

- All **159 tests passed** in 45.56 seconds. The 34 chart tests deliver real Qt
  wheel/drag events over the canvases and axes, check synchronized time ranges,
  stable value scales, history loading, live inspection, reset controls, and
  rejection of obsolete background snapshots after navigation.
- Wheel zoom and left-drag pan affect time only. Vertical ranges keep useful
  unit-specific context and expand for new extrema; Fit Y explicitly refits the
  displayed window. Selectors and the playback slider ignore wheel changes.
- Light/dark rendering and the 1080 × 820 minimum window were inspected. Short
  windows scroll complete charts; a wheel event over a chart cannot also scroll
  its container. All four panels fit at the default window size.
- The update does not change decoding, Bluetooth commands, stored observations,
  or evidence exports. Existing executables remain alongside the new version;
  no running recorder was stopped or replaced. This is synthetic/software
  verification, not new real-device or eight-hour qualification evidence.
- `OpenDP3-0.1.3.exe` passed the isolated frozen smoke test: 882 synthetic frames,
  zero replay mismatches, desktop rendering and sanitized evidence export. The
  five changed chart/query/UI modules embedded in the executable match the
  final source. The build remains unsigned. SHA-256:
  `cb24dacfb367d81083e9baa1f07039a9bb7a4c9ed6934c2e5fa60166219d1f2c`.

## Chart redesign — app 0.1.4 / decoder unchanged

The earlier interaction patch did not resolve the interpretation problems:
continuous numeric axes mixed raw error codes with small states, SOC/SOH shared
an unhelpful default range, old extrema permanently widened scales, and the
viewer offered no trace selection or sample inspector.

- Plain wheel events now scroll the chart stack; Ctrl+wheel and the time zoom
  buttons zoom time. This is tested over both axes and the canvas. Reset zoom
  stays at the historical position; Latest is a separate action.
- Fields selection excludes hidden traces from fitting. SOC starts without SOH,
  which remains selectable. Fit data, Context, and Hold have distinct behavior;
  Hold flags out-of-range readings instead of silently hiding them. Focus gives
  one chart the available space and preserves time when returning to the stack.
- States/errors occupy labeled rows. Raw codes appear at changes, sample colors
  distinguish values, and receipt gaps remain visible. Hover uses original
  samples and receipt times, never interpolated values or uncertain repeats.
- A read-only ten-minute query on the local multi-hour recording improved from
  about 1.9 seconds to 0.306–0.311 seconds on this workstation. All 40 coverage
  values and receipt times matched the previous query at the same cutoff.
  Rendering that recording took 0.162–0.298 seconds in the offscreen check.
  These are point-in-time measurements, not general performance guarantees.
- All **178 tests passed** in 61.46 seconds for the release. This includes 51
  chart tests and two query regressions protecting missing fields, uncertain
  repeats, historical receipts, future observations, and session separation.
  Synthetic visual checks covered slow SOC drift with SOH=100%, signed idle
  power, state changes, a communication gap, focus/restore, and both themes.
- `OpenDP3-0.1.4.exe` passed the isolated frozen smoke test with 882 synthetic
  frames and zero replay mismatches. The six changed implementation modules
  match the final source. Old executable checksums remain unchanged; no recorder
  was stopped, started, or replaced. No new hardware qualification is claimed.

The executable remains unsigned. SHA-256:
`5202fe395e2d2129a262cab6314efe645f7fbfcd558ff939f8ba5d6ad8dd16b7`.
Local verification artifacts: `artifacts/chart-redesign-verification.json`,
`artifacts/packaging-verification.json`, and `artifacts/chart-redesign-release.log`.
