# OpenPowerstation

## At a glance

OpenPowerstation lets your Windows PC keep a history of what your EcoFlow DELTA Pro 3
or Jackery Explorer 1000 v2 reports over Bluetooth.

- **See what is happening:** view battery charge, temperature, and power going
  in and out, where the device reports those readings.
- **Look back when something seems wrong:** browse charts and save the readings
  around an unusual change or a lost connection.
- **Keep your history on your PC:** recording does not need a cloud account or
  Home Assistant. EcoFlow setup needs your account's numeric user ID.
- **Share useful evidence:** export charts and readings to help explain a problem.
- **Optionally connect Home Assistant:** show readings there and, if you turn
  on Allow control, switch supported outputs or change Jackery's Battery Saving Mode or screen timeout.

The app records what the battery tells it. It cannot prove why a problem happened,
and it is not a safety alarm or a replacement for the battery's own protections.

A local flight recorder and opt-in control bridge for the EcoFlow DELTA Pro 3 and
Jackery Explorer. Windows desktop
charts and a headless recorder share the same Bluetooth, decoding, and storage
core. No Home Assistant, web server, or cloud telemetry is required — though an
optional bridge can publish current readings to Home Assistant if you want them.

OpenPowerstation helps answer: **what did the DP3 report before, during, and after a
suspicious reading or loss of communication?** It collects local Bluetooth
notifications, preserves protocol frames, plots time-aligned measurements,
and protects incident windows from ordinary history cleanup. It investigates
the firmware hypothesis; it does not assume the firmware caused a fault.

### Contents

- [At a glance](#at-a-glance)
- [Open the application](#open-the-application)
- [Which EcoFlow API does this use?](#which-ecoflow-api-does-this-use)
- [Read the evidence](#read-the-evidence)
- [Events and BLE API reference](#events-and-ble-api-reference)
- [Complete timeline-event catalog](#complete-opendp3-timeline-event-catalog)
- [Device-originated messages](#device-originated-messages-and-event-structures)
- [Mapped telemetry fields](#telemetry-fields-available-to-this-decoder)
- [Storage and privacy](#storage-and-privacy)
- [Security and evidence trust](SECURITY.md)
- [Command line](#command-line)
- [Verification](#verification)
- [Complete pinned protocol schema](PROTOCOL_SCHEMA.md)

**Status:** the application and synthetic/mock verification are implemented.
Actual-device Bluetooth authentication and telemetry receipt have succeeded.
Offline operation and an eight-hour hardware recording still require completed
qualification; see docs/HARDWARE_QUALIFICATION.md. A demo is not
evidence about your battery. This is unofficial diagnostic software, not a
safety monitor or a replacement BMS.

## Open the application

This repository contains source, tests, documentation and build scripts.
Recording data is excluded from Git. Download the executable from the GitHub
Releases page, build it yourself with
[the Windows build instructions](PACKAGING.md), or use the source launcher
after installing the dependencies below.

Double-click **OpenDP3-0.2.0.exe**, the current build. It is a standalone Windows x64 desktop build:
Python and its dependencies are included; no Python installation is needed.
Startup may take several seconds while the bundled libraries unpack.
It does not connect to the battery automatically.

Each release has a separate filename so an older executable can finish its
current recording. Running processes are not upgraded in place; use the updated
executable after the current run ends. Source builds default to a versioned
filename; `-OutputName OpenPowerstation.exe` is an explicit override.

If a data folder exists beside the executable, it uses that folder, including
any existing setup and recordings. Otherwise it uses
%LOCALAPPDATA%/OpenPowerstation (or %LOCALAPPDATA%/OpenDP3 if that folder
already exists from an install made before the rename). Copying only the executable does not copy your account
setup or recordings. Optional arguments: --data-dir PATH and a recording
database path. Keep the executable outside protected system folders.

The build is unsigned. Windows may show a publisher/reputation warning; the
SHA-256 checksum is alongside it in the matching `.exe.sha256` file. Do not disable Windows security.
Help > Third-party licenses lists bundled dependency notices.
See docs/PACKAGING.md to rebuild or verify the binary.

OpenPowerstation.cmd remains available as a developer launcher using the local .venv.

For a fresh checkout with Python 3.12 installed:

    py -3.12 -m venv .venv
    .\.venv\Scripts\python.exe -m pip install -r requirements.lock
    .\.venv\Scripts\python.exe -m pip install --no-deps -e .

The requirements.lock file pins the tested Windows environment. On another
platform, install with pip install -e ".[gui,test,test-gui]" instead; Linux hardware is not qualified.

The desktop packages are optional extras, so plain pip install -e . gives the
headless collectors, bridges and CLI without Qt -- which is what the Home
Assistant add-on installs. Add ".[gui]" for the desktop window, or ".[charts]"
on a headless box that only needs opendp3 export to render evidence.

1. Choose **File → Create synthetic demonstration** to explore the interface.
   Synthetic and device recordings use different databases.
2. Open **Settings** in the sidebar. Scan, explicitly select your DP3, and
   enter the owning account's numeric EcoFlow user ID.
3. Alternatively use **Retrieve user ID once via EcoFlow** on that page.
   Select your account region. The password is used only for this explicit
   HTTPS login and is never saved by the app. Login tokens are discarded.
   Credentials necessarily exist in memory during login; secure memory erasure
   is not guaranteed.
4. Choose **Save setup**, then go to **Overview** and click **Start recording**.
5. Use **Release Bluetooth** before connecting the EcoFlow phone app by BLE.
   Click **Resume** afterward. Release suspends reconnect attempts.
6. Use **Stop recording** to finish. Closing the recording window hides it to
   the tray; **File → Stop and exit** closes the application safely.

### The viewer

The sidebar holds six destinations. **Overview** carries the headline readings,
the four aligned charts and playback. **Live telemetry** lists every mapped
field with its age and cadence, beside the decoded frame at the cursor.
**Incidents** shows protected retention windows and the full event timeline.
**Exported files** lists evidence bundles this viewer wrote to its default
`exports` folder — bundles you saved elsewhere are not tracked. **Settings**
holds local Bluetooth setup and the light/dark choice, which is stored in
`ui.json` beside your recordings and is never part of an evidence export.
**About** carries versions, the qualification statement, and bundled licences.
The **File** and **Help** menus and the **⋯** button reach the same actions.

On the Overview charts, **plain wheel scrolling moves down the chart stack**.
**Ctrl + wheel** (or the **− / +** buttons) zooms time; **left-drag** pans time.
The gesture does the same thing over the canvas and the axes. The range label
shows the actual elapsed window and whether the viewer is following or inspecting
history. **Reset zoom** restores the selected span at the current position;
**Latest** separately returns to the newest reading. Recording continues while
you inspect history. Scrolling does not change selectors or the playback slider.

Each chart has **Fields** to show/hide measurements and **Focus** to give one chart
the available space. **All charts** restores the stack without losing the time
window. The activity rail is optional. SOC is shown by default; SOH is available
in Fields, because mixing health and charge estimates can obscure small changes.

Each numeric chart offers three vertical scale modes:

- **Fit data** (default) fits the visible fields in the displayed window, including
  small SOC changes and signed power. Scales recover when an old extreme leaves
  the window. Quiet live readings use modest hysteresis to avoid jitter.
- **Context** provides wider reference ranges: 0–100% for percentages, zero for
  power, and at least ten degrees for temperature. It expands for reported outliers.
- **Hold** freezes the current scale. A visible warning flags readings outside
  that held range. **Fit values** switches the visible numeric charts back to Fit data.

State/error fields use separate labeled rows, with raw values at changes and
colored sample marks. Error numbers are identifiers, not magnitudes: a large
code cannot flatten another field's 0/1 state. Color does not decode a fault or
establish what an unfamiliar state means. Gaps remain gaps.

Hover to inspect actual neighboring samples, their receipt times, and mapping
qualifications. No values are interpolated across segments, uncertain repeats,
or long gaps. Isolated samples remain visible as points. Time navigation remains
bounded by the recording, with a minimum one-second window.

The battery must already be bound to the account using EcoFlow's app. OpenPowerstation
does not bind/unbind, reset, modify firmware, or alter device settings. Keep
the PC awake, powered, and nearby. The application does not change Windows
power settings. Sleeping or losing power interrupts collection.

## Which EcoFlow API does this use?

**This application uses EcoFlow's local Bluetooth Low Energy (BLE) protocol,
decoded using community research. It does not use the official cloud
developer API for recording.** The optional HTTPS login is only for retrieving
the account user ID during setup. It does not subscribe to cloud telemetry.

These are different interfaces. The [EcoFlow developer portal](https://developer.ecoflow.com/)
documents a separate service; this README is not an exhaustive catalog of
EcoFlow's cloud products, MQTT topics, other devices, or firmware-specific
fault codes. Here, “all events” means all categories recognized by this version
of OpenPowerstation, plus the event-related definitions present in its pinned DP3 schema.

The protocol foundation is [ha-ef-ble](https://github.com/rabits/ha-ef-ble) at
revision `7cde8e5922589b5e3c81585890b5188747f5b037`. OpenPowerstation uses its DP3 framing,
authentication research and `mr521.proto` definitions without requiring Home
Assistant. The decoder version is `dp3-mr521/0.1.4+7cde8e592258`; the application
version is `0.2.0`. Schema presence does not establish that the selected DP3
publishes a field, that OpenPowerstation knows its routing, or that its units are verified.

The app supports one explicitly selected DP3, with serial prefixes `MR51` or
`MR54`. Attached-battery observations are conditional on what that DP3 exposes.
Discovery is not an authenticated connection, and authentication does not
guarantee useful temperature, power, or error coverage.

### Terms used in the viewer

| Term | Meaning in this application |
|---|---|
| BMS | Battery management system; `bms_*` fields concern the main battery/controller reporting path |
| CMS | System-level battery-management telemetry namespace; keep it distinct from `bms_*` even when values match |
| SOC | Reported state of charge, in percent; an estimate rather than a direct energy-meter reading |
| SOH | Reported state of health, in percent; not an independent capacity or safety test |
| MOS temperature | Device-reported temperature associated with its switching electronics; not an external probe or a cell-temperature measurement |
| MPPT / PV | Solar charge-controller / photovoltaic input terminology; a port's power value alone does not prove the source is solar |
| HV / LV | High-/low-voltage port labels used in the schema; do not treat these names as wiring instructions |
| Session | One recording run; it can contain multiple BLE connections and gaps |
| Segment | A contiguous comparison region separated by connection or capture-boundary events |
| Incident | A protected time window with trigger reasons, not a confirmed diagnosis |

## Read the evidence

- Charts share elapsed **host monotonic receipt time**. This is not a verified
  device sampling clock. Records retain UTC and monotonic timestamps,
  source sequence bytes when available, and the decoder revision.
- The application logs every received post-authentication protocol frame at the
  native update cadence. By default it sends no device commands after
  authentication; **Settings → Allow control** enables only the documented,
  allowlisted output, Battery Saving Mode, and Jackery screen-timeout commands. There is
  no guarantee that the device exposes every BMS sensor, reports all changes,
  or continues publishing without app-like clock/configuration exchanges.
- Each protobuf packet is decoded independently. Absent values never become
  zero and cached values are not merged into new observations. Exact repeats
  are labeled repeated_unverified and do not refresh measurement age.
- The Coverage tab shows observed fields, their age, and median inter-arrival
  time **within the displayed window**. This cadence is receipt cadence, not
  proof of independent physical measurements.
- Pack/PV voltage and current are currently **not mapped**. Configuration
  limits are not used as substitutes for measured electrical quantities.
- Extra-battery SOC/temperature mappings come from community research.
  Temperature mapping is unverified; these values do not trigger automatic
  temperature-jump incidents.
  Disconnected slots and partial packets without a connection flag are not
  displayed as batteries, even when their reserved data contains zeros.
- Raw error codes are preserved without guessing their meaning. In particular,
  code 36, 999, or any other number is not automatically labeled Error 036.
  A disconnect means lost communication, not a confirmed battery reboot.
- The default suspect-temperature rule is a change of at least 10 °C within
  five seconds between comparable, observed packets. It does not prove an
  impossible physical reading or firmware fault. Configure it in setup.
- Incident windows preserve five minutes before/after triggers; overlapping
  windows merge. Incomplete pre/post coverage and communication gaps are
  reported separately. A full time window can still contain gaps.
- The Raw fields tab shows the latest packet at the playback cursor. Move the
  slider or play at 10× to inspect packet/state changes. Firmware and operating
  notes are private session metadata.

## Events and BLE API reference

### Four different meanings of an event

| Layer | Example | What it means |
|---|---|---|
| Device telemetry field | `bms_max_cell_temp`, `bms_err_code` | A field actually present in a decoded DP3 property packet. It may be a measurement, estimate, state, or raw error value. |
| Device protocol message | `DisplayPropertyUpload`, schema-defined `EventPush` | A structured payload. Only the display-property packet currently has a verified route in OpenPowerstation's decoder. |
| OpenPowerstation timeline event | `suspect_telemetry`, `disconnected` | A local observation or inference recorded by the application; not an EcoFlow-issued event identifier. |
| Recorder/decoder status | `reconnecting`, `unknown_message` | Collection health or processing outcome. These are not battery fault codes or necessarily timeline rows. |

Do not treat a raw integer as an event's meaning. In particular, a PV source
type of `5` is not error 5, a run-state value of `1` is not a fault, and a
numeric value of `36` is not automatically the display's **Error 036**.

### Complete OpenPowerstation timeline-event catalog

The following 18 strings are recognized by the recorder/export path. Seventeen
have production emission paths; `capture_error` is reserved as described
below. The desktop displays spaces in place of underscores; SQLite and CSV
retain the exact identifiers.

“New segment” means subsequent comparisons do not cross this boundary.
“Pins incident” means the event requests protection for five minutes before
and after its receipt time. Neither property establishes the battery's state.

| Identifier | Origin / trigger | New segment | Pins incident |
|---|---|:---:|:---:|
| `connected` | Local BLE authentication completed | Yes | No |
| `disconnected` | Collector connection/session attempt ended with a recoverable failure | Yes | Yes |
| `released` | User explicitly released this collector's Bluetooth session | Yes | No |
| `silence` | BLE receive loop timed out waiting 30 seconds for a complete protocol frame | Yes | No |
| `telemetry_resumed` | First complete frame after a reported silence | Yes | No |
| `session_timeout` | Receive loop reached the consecutive-silence limit and ended the session itself | Yes | No |
| `session_error` | A BLE session ended on a transport exception rather than on silence | Yes | No |
| `session_lease_expired` | No decoded, measurement-bearing frame arrived within the session's valid-frame lease | Yes | No |
| `disconnect_error` | Bounded session cleanup timed out or raised while stopping notifications or disconnecting | Yes | No |
| `corrupt_transport` | Post-authentication framing discarded bytes, or a delivered frame failed validation | Yes | Yes |
| `host_suspend` | Recorder loop observed a scheduling interval greater than 10 seconds | Yes | Yes |
| `capture_error` | Reserved capture-failure category; not currently emitted automatically | Yes, if supplied | Yes, if supplied |
| `connection_failed` | Authentication or outgoing-message policy rejected the connection attempt | No | No |
| `suspect_telemetry` | A comparable, nonduplicate temperature observation crossed the configured jump threshold | No | Yes |
| `device_error` | An observed error field became nonzero or changed to another nonzero value | No | Yes |
| `state_change` | One of three supported raw state fields changed after an earlier comparable observation | No | No |
| `clock_change` | UTC receipt-time advancement differed from monotonic advancement by more than 2 seconds between frames | No | No |
| `manual` | User added a private incident marker/note | No | Yes |

Implementation sources: [BLE receiver](../src/opendp3/ble.py),
[collection service](../src/opendp3/runtime.py), [recorder](../src/opendp3/recorder.py),
and [export allowlist](../src/opendp3/exporting.py). The database accepts a string
category rather than enforcing a closed EcoFlow enum.

#### `connected` — authenticated BLE session

Emitted after the local authentication flow accepts the connection, not merely
when a device advertisement is discovered or a GATT link opens. A reconnect
within the same recording produces another `connected` event and a new
comparison segment. Typical detail: “Authenticated local Bluetooth session.”

It does not mean the battery is charging, outputs are enabled, all fields are
available, or its sensors are correct. Confirm Coverage and measurement age.

#### `disconnected` — communication unavailable

Emitted by the collector when a connection/session task fails outside the
special authentication/policy or storage-failure paths. It can also occur
when the configured DP3 cannot be found during a reconnect scan; therefore
the label does not prove there was a successful connection immediately before
it. The details identify a transport failure or exception class, without
copying arbitrary device/account payloads.

It breaks comparisons, protects an incident, and normally leads to reconnect
attempts with delays of 2, 4, 8, 16, 32, then at most 60 seconds. A successful
authentication resets the delay. Causes could include range, interference,
another BLE client, an unavailable device, or host/driver problems. The event
does **not** establish a battery shutdown, restart, or BMS trip. Normal Stop
and intentional Release do not deliberately emit this category.

#### `released` — intentional handoff

Emitted when **Release Bluetooth** cancels this app's connection task.
Reconnection remains suspended until **Resume**. A new segment prevents
temperature/state comparisons across the missing data. Intentional release
does not automatically pin an incident; add a manual marker if it matters.

There is no separate `resumed` timeline category. Resume schedules connection
work, followed by `connected` if authentication succeeds. A viewer of a
separate background collector cannot release that collector's Bluetooth;
the qualification workflow has an explicit Stop qualification action instead.

#### `silence` — no complete protocol frame

The receive loop waits up to 30 seconds for a complete frame. On timeout it
emits this event once for the current silent stretch, changes collection
health to `stale`, and waits once more. Further timeouts in the same stretch
do not repeatedly emit it; the second consecutive timeout ends the session
instead, as `session_timeout` below. Partial BLE fragments alone may not
complete a frame.

This is a communication test, not a per-sensor freshness test. Unknown message
types and even late authentication frames can return from the packet reader
without updating charted fields. A live connection can therefore carry traffic
while a specific sensor remains stale. Inspect individual measurement ages.
Silence breaks the comparison segment but currently does not pin an incident
by itself; use a manual marker if you want its surrounding history protected.
It also does not by itself identify BlueZ or the battery as the cause; the
`session_timeout` event says whether any transport exception was observed.

#### `session_timeout` — repeated DP3 notification silence

Emitted after two consecutive 30-second receive timeouts. It records the Bleak
backend, the notification-wait operation, the timeout class, the consecutive
silence count, and `transport_error=none_observed`, then deliberately ends the
session so the existing reconnect backoff opens a new GATT connection. BlueZ
keeps reporting such a link connected, so nothing else would ever end it.

#### `session_error` — BLE session ended on an exception

Records the backend, operation, exception class, and a whitespace-collapsed
message capped at 500 characters, before the exception reaches reconnect
supervision. It is not emitted for the silence teardown above, which has
already recorded its own more specific reason. It never records packets,
session keys, or the user id.

#### `session_lease_expired` — decoded frames stopped arriving

An independent 75-second lease is renewed only by a decoded frame that carries
mapped measurements and is not a duplicate. Corrupt, authentication, unknown,
measurement-free, and repeated frames cannot hold a stalled session open.
Expiry cancels the whole session task, including an operation that the
ordinary notification timeout does not cover.

#### `disconnect_error` — bounded cleanup failed

Records a timeout or backend exception raised while stopping notifications or
disconnecting. Cleanup is bounded, so a cleanup that cannot finish is reported
rather than allowed to block the next recovery attempt.

#### `telemetry_resumed` — frames returned after silence

Emitted for the first complete frame after `silence`, before the frame's
telemetry classification. It changes service health back to `recording` and
starts another segment. It does not prove that the returned frame contains a
new temperature sample: it could be an unsupported message or a late auth
reply that is subsequently excluded. It is not a device reboot indication.

#### `corrupt_transport` — unusable transport data

There are two paths. The stream reassembler can discard invalid framing or
checksum bytes and report the discard after authentication. Separately, a
complete post-authentication frame delivered to the recorder can fail packet
validation; that frame is first committed locally and then marked
`invalid_packet`. Both paths clear comparison history and pin an incident.

**Preservation limit:** discarded fragments in the transport reassembler are
not a wireless packet capture and are not retained individually. The raw store
contains delivered, decrypted protocol frames. A valid packet whose protobuf
body cannot be parsed instead receives `invalid_protobuf`; that status does
not currently produce this timeline event automatically. None of these cases
is sufficient to attribute corruption to EcoFlow firmware.

#### `host_suspend` — host collection gap

The collector checks its scheduling interval on its roughly 0.2-second loop.
An interval **greater than 10 seconds** produces this event, breaks comparison
history, and pins an incident. Sleep is one possible explanation; a blocked or
delayed process can look similar. OpenPowerstation does not read a definitive Windows
sleep/resume event here and cannot prove the host slept from this label alone.

#### `capture_error` — recognized, reserved category

Recorder, gap reporting and export code recognize this identifier. If a caller
supplies it, it breaks the segment and pins an incident. **The current live
collector does not emit it automatically on storage/processing failure.**

Those failures stop collection and surface an `error` health state; the
session is marked `error` if a final database write succeeds. If the disk
cannot be written, a final event row cannot be promised. Check application
health and session status rather than expecting a `capture_error` row for
every failed capture.

#### `connection_failed` — authentication/policy refusal

Emitted for an `AuthenticationError` or `PolicyError`, such as unsupported
advertised authentication, rejection of the account/device combination, or
an outgoing message blocked by the control allowlist. Collection stops with
an error rather than retrying an authentication bypass or using cloud data.

The event does not pin an incident. Adapter errors, discovery failures and
other connection exceptions may follow a different path; not every failure
to connect produces this exact category. Review the displayed reason.

#### `suspect_telemetry` — temperature jump candidate

For each mapped temperature field, the default rule is:

    absolute(new_value - previous_value) >= 10 degrees Celsius
    AND 0 < monotonic_receipt_delta <= 5 seconds
    AND current quality == observed
    AND the packet is not a recognized duplicate

The comparison is between the same field and the last eligible observation,
not between minimum and maximum sensors or between the main and extra
batteries. Missing fields do not update the comparison value. Connection/gap
boundaries clear the history. The first value in a segment is a baseline,
and a comparison older than the time window does not trigger. Comparisons use
the original numeric values, not the rounded values on the cards.

Configure the jump and time window in setup. The settings apply when starting
a collector; old sessions and existing incident records are not silently
reclassified. Unverified extra-battery temperatures do not enter this rule.
A legitimate zero is allowed; zero alone is not an anomaly. An abrupt return
from a low value can create another event and extend the same incident.

Example **synthetic** detail: `bms_max_cell_temp: 23 -> 0 °C in 1.100s receipt time.`
The conclusion is “suspect telemetry,” not “physically impossible” or “bad BMS.”

#### `device_error` — a nonzero device-reported error field

Applies to mapped `errcode` and names ending in `_err_code`. A nonzero value
triggers when the field has no prior comparable value, or differs from its
previous value. An unchanged nonzero code does not generate an event on every
packet. A return to zero is saved as a measurement but **does not emit an
error-cleared event**. If it later becomes nonzero again, it can trigger again.

A reconnect resets comparison history, so a persisting nonzero code may be
reported again on the first observation. Multiple fields changing together
can produce several `device_error` rows and one merged incident. Exact repeated
packets do not trigger additional error events.

The private detail contains the field name and raw number. No verified
controller-specific code dictionary or bitmask interpretation is supplied.
For example, `errcode`, `bms_err_code` and `mppt_err_code` are different sources;
their numbers must not be combined into one universal error-number namespace.
An absent error field means unknown, not a verified zero or a healthy battery.

#### `state_change` — selected raw state transitions

Only these three fields generate this event:

- `cms_bms_run_state`
- `cms_chg_dsg_state`
- `plug_in_info_ac_charger_flag`

A prior comparable value is required; the first observed state is not a
transition. The detail records the field and old/new raw values. Changes to
other fields, including PV source type, SOC, power and schema-only output
flags, do not automatically create `state_change` rows. This event does not
pin an incident, and a run-state transition is not proof of a controller reboot.

#### `clock_change` — Windows wall-clock discontinuity

Between successive delivered frames, OpenPowerstation compares the elapsed UTC time
with elapsed monotonic time. An absolute difference **greater than 2 seconds**
creates this event. It is about the host clock, not the battery's clock.
Charts and temperature comparisons continue on monotonic receipt time; the
event does not start a new segment or pin an incident. Fine timestamp precision
still does not establish device-side timing or causation.

#### `manual` — user annotation and incident marker

**Mark incident** saves a private note of up to 4,000 characters and pins the
surrounding window. During live collection, the timestamp is assigned when
the recorder processes the marker command. During saved-recording playback,
it is placed at the playback cursor; UTC is derived from session start plus
elapsed cursor time. That playback timestamp is not the wall-clock time at
which the user typed the note, and can differ from historical UTC after a
recorded clock adjustment. The original telemetry is not rewritten.

Notes are local observations, not statements issued by the DP3. Their text is
excluded from shareable exports; `events.csv` retains only the `manual`
category and time. Manual annotation controls are disabled in the viewer of
the separately running qualification collector.

### Incident protection is separate from event logging

Automatic incident triggers are `suspect_telemetry`, `device_error`,
`disconnected`, `corrupt_transport`, and `host_suspend`, plus user-created
`manual` markers. The reserved `capture_error` category also pins if supplied.
Each trigger requests `[event time - 300 s, event time + 300 s]`; overlapping
windows in the same session merge and retain their trigger reasons.

`connected`, `released`, `silence`, `telemetry_resumed`, `connection_failed`,
`state_change`, and `clock_change` do not pin history automatically. A timeline
row is therefore not necessarily protected from ordinary retention forever.

“Window complete” means the requested pre/post span is covered by the stored
frame bounds and the pre-history was available when marked. It does not mean
continuous measurements, complete sensor coverage, an eight-hour test passed,
or a healthy battery. Gap events are reported separately. Deleting incident
protection allows ordinary retention to remove old data; it is not a device
command and does not immediately erase every associated row.

### Collection health, session status and decoder outcomes

These labels are **not** additional EcoFlow events:

| Collection health | Meaning |
|---|---|
| `stopped` | No active collector, or collection finished and saved |
| `scanning` | Searching for the configured device |
| `authenticating` | Establishing an authenticated local BLE session |
| `recording` | Authenticated receive loop active; check per-field ages for actual freshness |
| `stale` | Receive loop has reported silence |
| `reconnecting` | Waiting for the capped backoff before another connection attempt |
| `released` | User suspended Bluetooth collection until Resume |
| `error` | Collection failed or was refused; the displayed reason is significant |

Persisted session status is one of `recording`, `stopped`, `error`, or
`interrupted`. On exclusive-writer startup, previously unfinished `recording`
sessions are marked `interrupted`, with an end based on their last stored
frame. This recovery does not generate an `interrupted` event row and does
not establish a battery reboot. A stale on-disk `recording` status is not a
process-liveness guarantee before that recovery happens.

| Frame status | Meaning and treatment |
|---|---|
| `pending` | Raw bytes committed; interpretation has not completed, possibly due to interruption |
| `decoded` | The supported property payload parsed; this does not guarantee every desired field was present |
| `unknown_message` | Packet passed framing but its source/command route is not supported; raw frame retained, no guessed fields |
| `invalid_packet` | Delivered protocol frame failed packet validation; raw frame retained and a corruption incident created |
| `invalid_protobuf` | Supported route, but its body could not be parsed with the pinned protobuf schema; raw retained, no automatic protobuf-specific incident |
| `repeated_unverified` | Identical raw packet recognized as a possible duplicate; bytes retained, freshness/comparison updates suppressed |

Duplicate checking uses a bounded history of 256 packet hashes and a five-second
window for nonzero sequences. A zero sequence instead compares with the most
recent packet hash without that time-window check; it does not prove retransmission. The
duplicate status can override the underlying decode classification. Different
packets containing an unchanged sensor value can still be `observed`; a changing
sequence or another changing field is not independent proof that every sensor
was physically sampled again.

Measurement quality is `observed` (a mapped numeric field was present),
`community_mapping` (derived extra-battery SOC), `unverified` (extra-battery
temperature heuristic), or `repeated_unverified`. “Not observed” and “not mapped”
are viewer coverage labels, not numeric zero or device fault statuses.

### Device-originated messages and event structures

This routing table is intentionally narrower than the full schema inventory.
The pinned [upstream DP3 handler](https://github.com/rabits/ha-ef-ble/blob/7cde8e5922589b5e3c81585890b5188747f5b037/custom_components/ef_ble/eflib/devices/delta_pro_3.py)
provides the display-property route and an automatic time-reply path. OpenPowerstation
adapts the former and deliberately excludes the latter.

| Message / route | OpenPowerstation behavior |
|---|---|
| `DisplayPropertyUpload`: source `0x02`, command set `0xFE`, command `0x15` | Decode each payload independently with presence preserved; map selected numeric fields; keep other known fields locally |
| Authentication: command set `0x35`, including status `0x89` and login `0x86` | Handle only in the constrained setup/session handshake; exclude authentication packets from evidence |
| Upstream time request: source `0x35`, command set `0x01`, command `0x52` | No clock reply; if delivered after authentication it has an unsupported route and is retained as `unknown_message` |
| `EventPush` / `EventAck` | Schema definitions exist, but no verified route/decoder or event-ack sender is implemented |
| `RuntimePropertyUpload` | Schema exists; no runtime-property route is implemented; do not assign unknown packets to it by guesswork |
| `DevRequest` / `DevRequestAck` | Schema exists; not serviced by the recorder |
| `ConfigRead` / `ConfigReadAck` | Schema exists; not requested or decoded as standalone messages |
| `ConfigWrite` / `ConfigWriteAck` and other configuration helpers | Not used; outgoing configuration, output-control, clock and firmware writes are blocked |
| Any other post-authentication route | Retain the delivered raw frame as `unknown_message`; no automatic acknowledgement or cloud fallback |

Authentication may use the device's advertised mode 0, 1 or 7, subject to the
supported advertised protocol version. Only the verified authentication shapes
are allowed out. After authentication the outgoing gate closes completely:
**no polling, event acknowledgements, keepalive commands, upload-period changes,
clock synchronization or output controls are sent.** This restriction may
limit what a particular firmware publishes. “Read-only” does not mean radio
silence during authentication; BLE connection/subscription traffic is needed.

#### EcoFlow `EventPush`: what the pinned schema actually defines

These are device-schema fields, unlike the local timeline categories above:

| Structure | Tag | Field | Protobuf type | Meaning / qualification |
|---|---:|---|---|---|
| `EventPush` | 1 | `event_ver` | `uint32` | Event-format version field; supported values not verified |
| `EventPush` | 2 | `event_seq` | `uint32` | Event-message sequence field; reset/wrap semantics not verified |
| `EventPush` | 3 | `event_item` | repeated `EventPush.LogItem` | A list of event records |
| `EventPush.LogItem` | 1 | `unix_time` | `uint32` | Device-side time field by schema name; clock accuracy and epoch/unit interpretation not qualified in this app |
| `EventPush.LogItem` | 2 | `ms` | `uint32` | Subsecond-looking time field by name; range and combination with `unix_time` not verified |
| `EventPush.LogItem` | 3 | `event_no` | `uint32` | Numeric event identifier, **not an enumerated list of named event types** |
| `EventPush.LogItem` | 4 | `event_detail` | repeated `float` | Event-specific numeric values; order, units and interpretation are not defined here |
| `EventAck` | 1 | `result` | `uint32` | Acknowledgement result; no verified result-code dictionary |
| `EventAck` | 2 | `event_seq` | `uint32` | Sequence field in the acknowledgement |
| `EventAck` | 3 | `event_item_num` | `uint32` | Event-item count field by schema name |

The scalar fields above have protobuf presence; missing is distinct from an
explicitly present zero. Repeated fields do not provide the same scalar
presence distinction. `event_seq` belongs to the payload and is not the
transport header's sequence bytes or an OpenPowerstation session ID.

**There is no verified `event_no → fault/event name` dictionary in the bundled
schema, and OpenPowerstation currently does not consume `EventPush`.** It would be
misleading to list invented “overtemperature,” “BMS reboot,” or “Error 036”
event numbers as supported API events. The presence of `event_detail` also
does not establish its layout. Firmware-specific routing, units, identifiers
and acknowledgement requirements remain unqualified. The observed count of
unknown messages alone does not tell us how many are `EventPush` packets.

#### Error-history structures are different from live error fields

`DisplayPropertyUpload.err_code_record_list` (tag `141`) can contain an
`ErrcodeRecordList`. Its repeated `list_info` entries are `ErrcodeRecordItem`
messages with `errcode` (tag `1`, `uint32`) and `errcode_timestamp` (tag `2`,
`uint32`). If present in a supported display upload, these known nested fields
are retained in the local decoded JSON.

They are **not** converted into timeline `device_error` events, backdated
incidents, verified UTC timestamps, or shareable numeric CSV columns. Error
number meanings and timestamp units are not qualified. A history record is
not automatically evidence that the same fault is asserted now.

#### Runtime measurements and configuration definitions

The schema's separate `RuntimePropertyUpload` has 144 fields, including names
such as `bms_batt_vol`, `bms_batt_amp`, `cms_batt_vol`, `cms_batt_amp`,
`plug_in_info_pv_h_vol`, `plug_in_info_pv_h_amp`, `plug_in_info_pv_l_vol`,
`plug_in_info_pv_l_amp`, `temp_pcs_ac`, `temp_pcs_dc`, `temp_pv_h`, `temp_pv_l`,
`bms_high_temp_icon`, `bms_low_temp_icon`, and controller-communication flags.
Those definitions are useful research leads, **not currently available chart
measurements**. Even a familiar name needs verified routing, presence, scaling
and units before it becomes a normalized field. Configured charging limits
and requested volts/amps must not be substituted for measured values.

`ConfigReadAck` contains firmware-version fields for PD, IoT, MPPT, LLC,
inverter and BMS controllers. OpenPowerstation does not request this message; manually
entered firmware labels are private session metadata, not an automatic API
version read. `PropertyUploadPeriod` defines display/runtime full/incremental
period fields, but the recorder does not request or change them. Their schema
presence is not a guarantee of any sampling rate.

For every definition, field tag, protobuf type, presence flag and enum in the
bundled file, see [the complete schema inventory](PROTOCOL_SCHEMA.md).
It contains 46 top-level messages plus one nested message, 749 field
definitions, and 12 enums with 68 named values. It is an inventory of the
pinned schema, not a promise that all 749 fields are reachable on your DP3.

### Telemetry fields available to this decoder

OpenPowerstation maps **40 numeric field names**: 36 directly from
`DisplayPropertyUpload`, plus four conditional extra-battery values. Firmware
may publish only a subset. The complete field-by-field mapping, including
tags, units and event eligibility, is in
[the normalized field catalog](PROTOCOL_SCHEMA.md#normalized-field-catalog).

| Group | Mapped fields | Automatic timeline behavior |
|---|---|---|
| Temperatures, °C | `bms_max_cell_temp`, `bms_min_cell_temp`, `bms_max_mos_temp`, `bms_min_mos_temp`, `cms_batt_temp` | Eligible for the configured `suspect_telemetry` rule |
| Charge/health estimates, % | `bms_batt_soc`, `cms_batt_soc`, `bms_batt_soh`, `cms_batt_soh` | No SOC-drop or SOH-change event rule implemented |
| Power, W | `pow_in_sum_w`, `pow_out_sum_w`, `pow_get_ac_in`, `pow_get_ac_lv_out`, `pow_get_ac_hv_out`, `pow_get_pv_h`, `pow_get_pv_l`, `pow_get_bms` | No automatic zero-power, overload or power-balance event rule implemented |
| Selected state values | `cms_bms_run_state`, `cms_chg_dsg_state`, `plug_in_info_ac_charger_flag` | Eligible for `state_change` |
| PV source types | `plug_in_info_pv_h_type`, `plug_in_info_pv_l_type` | Stored raw; no automatic state-change event |
| Device/controller errors | `errcode`, `bms_err_code`, `mppt_err_code`, `inv_err_code`, `pd_err_code`, `llc_err_code`, `llc_inv_err_code`, `dcdc_err_code` | Eligible for `device_error` on a new nonzero value |
| Attached-port errors | `plug_in_info_5p8_err_code`, `plug_in_info_acp_err_code`, `plug_in_info_4p8_1_err_code`, `plug_in_info_4p8_2_err_code`, `plug_in_info_dcp_err_code`, `plug_in_info_dcp2_err_code` | Same raw-error rule; port labels are not fault-code meanings |
| Conditional extra batteries | `extra1_soc`, `extra2_soc` (%); `extra1_temperature`, `extra2_temperature` (°C, unverified) | No automatic temperature-jump incidents from these heuristic values |

Power signs are preserved. Upstream interprets positive `pow_get_bms` as
charging and negative as discharging; OpenPowerstation retains one raw signed field
rather than manufacturing two independently measured channels. AC output
fields can also use negative signs. PV port power is retained even if the
source-type interpretation is uncertain; it is not forced to zero by a solar
classification rule. All-zero power needs operating context, especially if
the user reports actual charging or a load.

The pinned upstream's community PV-source interpretation is `0=OFF`, `1=CAR`,
`2=SOLAR`, `3=DC_CHARGING`; it explicitly leaves `5` unknown. Its `UNKNOWN=-1`
is a community parser sentinel, not a newly established device state. OpenPowerstation keeps the
numeric value and does not infer a fault or claim a verified meaning for `5`.
Other run-state numbers are likewise retained without inventing an enum.

Extra-battery mapping requires a nonzero `plug_in_info_4p8_1_in_flag` or
`plug_in_info_4p8_2_in_flag` **in the same packet**, plus sufficiently long
reserved data. SOC reinterprets the first reserved 32-bit word as a float;
temperature uses an upstream byte-selection heuristic from reserved word 13.
These are not independent physical sensors. Missing connection flags or
zero-filled disconnected slots do not create fictitious batteries.

The display schema contains 296 fields. Fields outside the 36 direct numeric
mappings, such as per-USB/12 V power, flow flags, fan data, remaining-time
estimates, configuration values and error history, can remain visible in
local decoded JSON **if they arrive in a supported display packet**. They
are not automatically charted, normalized, exported, or given event rules.
Unknown protobuf tags survive only in the preserved raw frame; the current
Raw fields view cannot name or interpret them.

### What the exported event file includes

`events.csv` has exactly three columns:

| Column | Meaning |
|---|---|
| `receipt_utc` | UTC event timestamp rendered to milliseconds; usually host receipt/detection time, with the playback-marker qualification above |
| `elapsed_s` | Event position in the session's monotonic timeline |
| `kind` | One of the 14 recognized identifiers, or the export-only fallback `unrecognized_event` |

`unrecognized_event` is a sanitization label applied to an event category not
on the export allowlist. It is not an incoming EcoFlow event type and is not
a new row emitted by the live collector. Private `detail` text, field-specific
error detail, notes and session/device identifiers are omitted from this CSV;
use the local timeline/raw data to inspect those details.

For a whole-session export, the included window runs from the first to the
last stored frame. For an incident export, it is the requested incident
window. Events outside those bounds are omitted: an authentication event can
precede the first frame, and a final failure can occur after the last frame.
An empty `events.csv` therefore does not prove a connection never occurred or
that the session never ended. Check session status, coverage and the selected
export window as well.

## Storage and privacy

**Security limitation:** BLE address/serial selection does not cryptographically
verify the battery's identity; a nearby peer that successfully impersonates it
could capture the ID-derived login proof and supply fabricated telemetry.
Recordings are not tamper-evident: CRCs, re-decoding and adjacent file checksums
do not prove device origin or prevent deliberate alteration. See the
[security and evidence trust boundaries](SECURITY.md) and
[security review fixes](SECURITY_REVIEW.md).

The launcher stores configuration and recordings in data/ beside the project.
Direct CLI usage defaults to the user's platform data directory
(%LOCALAPPDATA%\OpenPowerstation on Windows, or %LOCALAPPDATA%\OpenDP3 if
that folder already exists from before the rename); use --data-dir before the subcommand to
choose the same local directory as the launcher.

The config.json file contains device selection and the account user ID,
**not passwords or tokens**. Protect that directory with your normal Windows
account access controls. Do not commit or share it. Data, databases, and
configuration should remain in a private per-user folder, not a shared or publicly
writable portable folder. OpenPowerstation inherits Windows permissions; it does not
audit or tighten ACLs automatically. Open recordings only from trusted sources;
read-only SQLite access is not a security sandbox. Data, databases, and
credentials are ignored by Git. Device-wide lock files live under the user's
OpenPowerstation data directory so separate database paths cannot claim the same device.

Raw frames commit before decoding in SQLite WAL mode with FULL synchronization.
These are decrypted protocol frames; wireless ciphertext and session keys are not retained.
The database includes unknown telemetry for future decoder work. Authentication
exchanges and session keys are excluded. Interrupted sessions are identified on
the next writer startup, and undecoded committed frames remain marked pending.

Ordinary history defaults to seven days or 5 GB of conservatively accounted
record data. Pinned incidents are excluded from pruning. SQLite page/index/WAL
overhead and pinned incidents use additional disk space; the database is not
a strict 5 GB file-size cap. Maintenance runs during collection. It stops visibly
at a 128 MiB free-space reserve or on write/processing failure. Existing pinned
evidence is never silently deleted. Remove an incident's protection only when
you are ready for normal retention to remove its old data.

**Export evidence** writes numeric telemetry CSV, event-category CSV, an offline
HTML report, a PNG chart, and summary JSON. With a selected incident it exports
that window; otherwise it exports the selected session. Shareable exports
exclude identifiers, private notes/firmware text, unknown fields and raw bytes.
No files are uploaded or sent anywhere.

The CLI's --raw option additionally writes a **separate PRIVATE ZIP** containing
protocol bytes and SHA-256 checksums. Opaque bytes can include identifiers.
Review it before sharing. The private ZIP contains no authentication material.

## Home Assistant

An optional bridge publishes the current readings to a Home Assistant MQTT broker,
which creates the sensors for you. It is off until you set a broker address in
**Settings → Home Assistant bridge**.

The bridge is a **separate process from the recorder**. It opens the recording
database read-only, never writes to it, never touches Bluetooth, and never opens a
listening socket — the only network activity is an outbound connection to your
broker. A broker that is down, slow, or unreachable cannot stall or stop a
recording. That separation is the point: capture never depends on publication.

    .\.venv\Scripts\python.exe -m opendp3 --data-dir data bridge --dry-run
    .\.venv\Scripts\python.exe -m opendp3 --data-dir data bridge

Or run **OpenPowerstation-Bridge.cmd**. Use `--dry-run` first: it prints every entity and the
current state payload without connecting to anything.

Home Assistant receives the decoder's allowlisted numeric fields plus capture-health
diagnostics. Raw frames, decoded protobuf, the account ID and private notes are never
published, and the serial is replaced by the same hash the collector uses for its
per-device lock. Fields whose meaning is a raw code or a community mapping arrive as
**diagnostic** entities, so they do not read as verified measurements.

Readings go unavailable rather than stale: if the recorder stops, the battery falls
silent for 45 seconds, or the newest session is a synthetic demo, the bridge publishes
no values and reports why through `sensor.opendp3_collector_state`. A number shown in
Home Assistant is one the device actually reported recently.

The broker password is stored in `config.json` in plain text. Use a broker account
created only for this, so the stored value grants nothing else. See
[docs/SECURITY.md](SECURITY.md).

## Command line

Run commands with the project's Python:

    .\.venv\Scripts\python.exe -m opendp3 scan
    .\.venv\Scripts\python.exe -m opendp3 jackery-scan --seconds 15
    .\.venv\Scripts\python.exe -m opendp3 jackery-status --seconds 30
    .\.venv\Scripts\python.exe -m opendp3 --data-dir data setup
    .\.venv\Scripts\python.exe -m opendp3 --data-dir data setup --login
    .\.venv\Scripts\python.exe -m opendp3 --data-dir data record --hours 8
    .\.venv\Scripts\python.exe -m opendp3 --data-dir data jackery-record --interval 3
    .\.venv\Scripts\python.exe -m opendp3 --data-dir data jackery-compact
    .\.venv\Scripts\python.exe -m opendp3 --data-dir data jackery-bridge --interval 60
    .\.venv\Scripts\python.exe -m opendp3 --data-dir data gui
    .\.venv\Scripts\python.exe -m opendp3 --data-dir data bridge --dry-run
    .\.venv\Scripts\python.exe -m opendp3 --data-dir data bridge
    .\.venv\Scripts\python.exe -m opendp3 demo --database artifacts\example.sqlite
    .\.venv\Scripts\python.exe -m opendp3 replay artifacts\example.sqlite --verify
    .\.venv\Scripts\python.exe -m opendp3 export data\recordings.sqlite artifacts\incident-001 --incident 1
    .\.venv\Scripts\python.exe -m opendp3 export data\recordings.sqlite artifacts\private-export --raw

Do not pass passwords or user IDs on command lines. Setup prompts locally.

### Jackery local BLE path

The Explorer 1000 v2 (advertised model code 8 / `E1000Pro2`) has a real local
control plane over Bluetooth; a Jackery account is not part of its BLE session.
Its advertisement contains the serial and encrypted device GUID needed to
derive the RC4 session key. Status and control then use GATT characteristics
`EE01` (write) and `EE02` (notify). Large property replies are split across an
`80` multi-packet envelope and must be reassembled before decoding the JSON.

`jackery-scan` is passive. `jackery-status` connects, derives the key, sends only
the device-property query, reassembles the reply, prints it, and disconnects.
The phone app must release its BLE connection first because the Explorer accepts
only one BLE client. This is currently a hardware-qualification probe, not yet
the unattended recorder selected by `Start-OpenPowerstation.cmd`.

**Observed Explorer 1000 v2 constraint (2026-09-04):** on this installed unit,
ordinary radio activation is not a practical acquisition window. The station
reconnects to its saved Wi-Fi network and stops BLE advertising before the local
collector can attach reliably. Do not send an operator through repeated
DC/USB+AC activation and scan attempts while that Wi-Fi profile remains saved.
For BLE qualification, deliberately reset/forget the station's Wi-Fi configuration
using the device's documented button procedure, leave Wi-Fi unconfigured during
the test, and restore it later through the Jackery app if cloud access is wanted.
That reset can also affect the app binding, so it is an explicit operator action,
not something OpenPowerstation performs or recommends as an unattended recovery step.

Windows qualification result (2026-09-04): the advertisement, serial, model code,
session-key material, address type, and connectable flag were decoded repeatedly,
but WinRT returned `E_FAIL` while creating the GATT device, before service discovery
or any protocol write. Temporarily stopping the authenticated EcoFlow collector
and fully releasing its BLE connection produced the identical result, so adapter
connection contention is disproved. Do not repeat Wi-Fi resets merely to retry the
same Windows path. The next useful experiment is a BlueZ/Home Assistant Bluetooth
host (the environment used by Private Jack), or a Windows-native connection path
that bypasses this WinRT address lookup.

OpenPowerstation now includes that Windows-native fallback: while the connectable
advertisement is live it resolves the station's Windows AEP device identifier
with `DeviceInformation`, then asks WinRT to open that identifier with
`BluetoothLEDevice.FromIdAsync` instead of repeating Bleak's failing
address-only lookup. This needs no ESP32 or additional hardware. Qualification
on this unit proved that path can open the device, establish an active persistent
GATT session, negotiate a 517-byte PDU, discover `BDFF`/`BDEE`, subscribe to
`EE02`, and successfully write without response to `EE01`. Bleak itself loops on
spurious Windows `services changed` events, so OpenPowerstation uses direct WinRT GATT for
this device. The station nevertheless emitted no notification after either the
encrypted time-sync or device-property command. Windows reported the device as
pairable but both ordinary and confirmation-only pairing failed without creating
a bond. Do not repeat radio/Wi-Fi resets for this result.

That silence was ours, not the firmware's. The session key was being cut to
sixteen bytes before RC4, as if it were an AES key. The derived material is
twenty-two bytes -- six serial characters, the six-byte GUID from the
advertisement, and the ten-byte salt -- and RC4 takes all of it. This module
already proves that on this hardware: `parse_advertisement` decrypts the
advertisement with an eighteen-byte key and the station's own CRC validates the
result. A truncated key produces an unrelated keystream, so every frame we sent
failed the station's CRC and was dropped without a reply, which is exactly the
symptom recorded above. No pairing, bond, cloud token, or missing handshake byte
was involved, and no HCI capture is needed to find it.

`jackery-status` now sends the full-length key and falls back to the truncated
one within the same connection, so a single acquisition window settles the
question either way.

**Qualified on this unit (2026-09-04).** With the full-length key the Explorer
answered the very first property query, over the multi-packet `80` envelope, on
the fallback's first candidate. No pairing, bond, Jackery account, cloud token,
or Wi-Fi reset was involved. The reply carries the same property names the cloud
API returns -- `rb`, `bt`, `ip`, `op`, `oac`, `odc`, `ec` and the rest -- so
`jackery_fields.map_properties` maps the local payload unchanged. Local BLE is now
a qualified transport for this model, not a probe.

Seven returned keys are still unmapped and deliberately unpublished: `acpsp`,
`acpss`, `bs`, `cip`, `pal`, `pmb`, `ta`. Their units are unverified, so
they are not exported as measurements. `sltb` is now mapped as the verified
screen-timeout preset enum used for control readback.

For a Home Assistant host (or an ESPHome Bluetooth proxy) within BLE range, the
community [Private Jack integration](https://github.com/porcupin26/private_jack)
already implements local polling and local controls without an account or cloud
call. OpenPowerstation exposes the qualified transport's guarded AC/DC output controls,
Battery Saving Mode, and screen timeout through the same **Settings → Allow control**
switch used by the DP3 bridge. Battery Saving Mode is the fixed 15–85% range;
arbitrary battery-boundary writes remain blocked.

The Explorer's Wi-Fi interface is a different path. The observed ESP32 endpoint
responds on the LAN but exposes no common TCP service; available evidence and the
app behavior indicate that it initiates an outbound cloud connection instead of
hosting a local API. Redirecting that connection to a private broker is possible
on some Jackery product families, but BLE is the simpler and less invasive local
route for this portable model.

`jackery-record` discovers the Explorer, opens one BLE session, and polls it on that
session, writing snapshots to `data\jackery.sqlite`. The station only advertises in
short windows, so the recorder holds the session it manages to open instead of
reconnecting for every poll, and only rediscovers after the link actually drops.
The GATT write that starts each read shares the read's deadline rather than
running unbounded, and session cleanup is bounded too, so a stalled BlueZ
operation ends that session instead of pausing collection until it returns.
A local recording involves no Jackery account, no cached session, and no outbound
connection to anything but your own broker. **Start-OpenPowerstation.cmd** starts it.

The default poll rate is **3 seconds**. One read on an already-open session takes
about half a second, so the radio is comfortable at that rate; the constraint is
disk, not Bluetooth. The bridge publishes every **1 second** and only when the
payload actually changed, so Home Assistant sees each reading about a second after
it lands without the broker carrying redundant traffic. The two are deliberately
different rates: they are independent loops, and two equal periods beat against each
other and add up to a worst case of both.

Jackery uses a separate `data\jackery.sqlite` database so an active EcoFlow recording
can never block it or be mistaken for Jackery telemetry. After configuring MQTT,
`jackery-bridge` publishes a distinct `Jackery Explorer 1000` device under the
`jackery/` topic namespace. Its entity IDs and discovery identifiers are separate
from the EcoFlow `opendp3/` device. When **Allow control** is enabled, it also
publishes these actionable Home Assistant entities on the device pages:

* DP3: HV AC output and LV AC output switches.
* Jackery: AC output and DC output switches, plus a Battery Saving Mode selector
  (`full` or `save`, the fixed 15–85% range).

Each control has its command topic, live bridge/telemetry availability, and a
device-page icon. Jackery commands are relayed to the authenticated local BLE
recorder and verified by status readback. The bridges re-announce discovery when
Home Assistant sends its restart/birth message. Start `gui` in a second terminal
to watch a live recording.
Start `gui` in a second terminal to watch a live recording.

Readings are stored as `ble_observed`, so the database says which transport produced
each number. Sessions recorded before 2026-09-04 carry `cloud_observed` and the
`Jackery cloud read-only transport` marker; that path has been removed, but its
recordings are kept and still read back, replay, export and publish normally.

One entity, `last_frame`, changes on every single frame. At a 3-second poll that is
roughly 28,000 rows a day in Home Assistant's own recorder database for a timestamp
nobody charts. Exclude it, and `frame_count`, in your Home Assistant `recorder:`
configuration.

### What is kept on disk

At 3 seconds the recorder writes about 28,800 frames a day, roughly 100 MB. Left
alone that would be 37 GB a year, so old ordinary history is thinned rather than
kept whole — or, as it used to be, deleted outright after seven days.

A frame is kept at full fidelity when any of these is true:

- it lies inside an **incident window**, or
- it is younger than the **grace period** (48 hours by default), or
- it is the **first frame of its minute**, or
- it holds that minute's **minimum or maximum** for output power, input power or
  battery temperature.

Everything else is deleted. That last rule matters: thinning to one sample a minute
and nothing else would silently discard the peak draw that made a minute worth
looking at. Steady state is roughly 200 MB of recent full-rate history plus tens of
megabytes a year of minute-level history, and nothing is forgotten after a week any
more.

**Only whole frames are ever deleted.** No measurement is removed while its frame
survives, no `raw` blob is rewritten, and no averaged frame is ever synthesized, so
every surviving row still re-derives from bytes the station actually sent and
`replay --verify` stays meaningful. A minute-level average would be a number no
sensor ever reported.

Thinning runs automatically inside the recorder. To inspect or run it by hand, stop
the recorder first — it holds the database's single-writer lock — and use:

    .\.venv\Scripts\python.exe -m opendp3 --data-dir data jackery-compact
    .\.venv\Scripts\python.exe -m opendp3 --data-dir data jackery-compact --apply

Without `--apply` it deletes nothing and only reports what it would remove.

### What counts as an anomaly

Thinning is gated on fault detection, so the detectors decide what "ordinary" means.
The bias is deliberately asymmetric: a false positive costs a few hundred kilobytes
kept forever, a false negative destroys the only recording of a fault. Any of these
pins an incident window, and pinned windows are never thinned:

| Signal | Rule |
|---|---|
| `errcode` | non-zero and changed |
| Battery temperature | outside −20…60 °C, or jumping ≥10 °C within 5 s |
| State of charge | moving ≥5 % within 10 s — faster than the cell chemistry allows, so a sensor fault or a BMS recalibration |
| AC output | while the inverter is on: voltage outside 90–260 V, frequency outside 45–65 Hz, or voltage moving ≥15 % between samples |
| Unverified properties | one of the seven unmapped properties takes a value not seen before this session |
| Capture gaps | no observation for four poll intervals — a missing sample is evidence too |

The AC bands are deliberately wide enough to cover any mains in the world, because
assuming a 120 V or a 230 V nominal would flag either every reading or none of them.
The relative step rule is what catches a droop without needing to know the nominal.

Ordinary operation — power ramping, output switching, charge drift, mode changes —
is recorded as an event but does not pin, or nothing would ever be thinned. An
unmapped property that produces more than 32 distinct values is reclassified as a
measurement rather than a state code and stops pinning, so a counter cannot pin
every window forever.

These detectors have never seen a real fault on this unit: `errcode` has been `0`
for every frame ever recorded. They are written conservatively and are cheap to
re-tune, but a window thinned under a too-lax detector cannot be recovered — which
is what the 48-hour grace period is for.

Exports refuse to overwrite an existing directory or private archive.

Timed recording starts its duration at the first authenticated connection, and
stops if it cannot authenticate within five minutes. An interrupted Bluetooth
session still creates a gap; eight hours elapsed does not imply continuous data.
For a background qualification run, --stop-file data/qualification.stop enables
the **Stop-Qualification.cmd** launcher to stop it gracefully. Remove that stop
file before starting another run. A headless collector owns Bluetooth separately
from the desktop; stop it before starting desktop collection or using phone BLE.

The replay --verify command re-decodes committed raw packets and compares stored
numerical observations. It never contacts hardware or changes the source recording.
A future decoder update requires a distinct revision and explicit comparison,
not rewriting historical evidence.

## Restarting after a reboot

If the battery was off when the machine started, nothing is recording and nothing
is on the broker: the desktop app never starts collection on its own, and the
collector's retry loop only runs once a recording exists. **Start-OpenPowerstation.cmd**
covers that case in one step. It starts the headless collector, waits for a real
authenticated link rather than just reporting that a process launched, then starts
the bridge.

    Recorder    starting; waiting for an authenticated link...
    Recorder    connected. Authenticated local Bluetooth session.
    Bridge      publishing to the configured broker.

Running it twice is safe. The collector takes the same single-writer lock the
desktop app takes and the bridge takes one of its own, so a copy that is already
running is left alone instead of competing for the adapter or the broker. If the
DP3 is still off, the launcher says so and leaves the collector running: it retries
forever and attaches on its own once the battery is powered on.

Both processes are detached and log to `data\collector.log` and `data\bridge.log`.
**Stop-OpenPowerstation.cmd** ends them gracefully through their stop files — the session is
closed and saved, Bluetooth is released for the phone, and the bridge marks the
device offline in Home Assistant rather than leaving stale values behind. Add
`--gui` to also open the viewer, which shows the headless recording read-only.

## Controlling the DP3 and Jackery from Home Assistant

OpenPowerstation records without writing to either device until you explicitly opt in.
**Settings → Allow control** publishes the DP3's HV/LV AC output switches and the
Jackery's AC/DC output switches plus Battery Saving Mode and screen-timeout selectors.
They appear in Home Assistant after the corresponding bridge reconnects. Jackery
commands are sent over its authenticated local BLE session and verified by a
follow-up status read.

Everything else stays refused. On the DP3, `ConfigWrite` also carries
`cfg_power_off`, `reset_factory_setting`, `cfg_bms_power_off`, charge limits and
the installment-payment fields. Its outbound gate parses each payload before it is
written, requires the two allowed AC-output fields and nothing else, rebuilds the
message from that allowlist, and compares the bytes. A permitted field beside a
forbidden one is refused, as are unknown protobuf tags. Jackery uses a separate
exact control/option allowlist before its compact JSON command is encrypted; Wi-Fi,
firmware and arbitrary battery-boundary writes are not reachable through it.

Commands reach the radio the same way stop requests do: the bridge writes a
request file, the collector picks it up. The recorder never opens a socket, so a
broker that is unreachable, slow or hostile still cannot disturb a recording.
Every DP3 command is recorded in the session as a `control` or `control_refused`
event. Jackery commands are recorded as `control` after status readback, or as
`control_unverified` if the write or verification fails.

**Two things to understand before enabling it.** Anything that can publish to
your broker can request these allowlisted changes; broker access is the entire
authorization boundary. Home Assistant control state comes from fresh device
feedback rather than the last command OpenPowerstation sent. Missing or stale readback
makes the corresponding control unavailable instead of guessing that a write
succeeded.

## Verification

    .\.venv\Scripts\python.exe -m pytest -q

Tests cover encrypted handshake interoperability using a simulated peer,
outbound-command blocking, fragmented/corrupt frames, field presence and zeros,
unknown payloads, duplicate observations, reconnect boundaries, clock jumps,
retention, raw-first persistence, disk failure, locking, exports, and Qt playback.

See [hardware qualification](HARDWARE_QUALIFICATION.md) before treating this
as a validated recorder for your installed firmware. No deliberate fault
induction, BMS overrides, firmware flashing, automatic power switching, or
cloud fallback is implemented.

Development results: [software validation](VALIDATION.md).

Protocol sources and attribution: [THIRD_PARTY_NOTICES.md](../THIRD_PARTY_NOTICES.md).
