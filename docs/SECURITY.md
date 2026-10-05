# Security and evidence trust

Applies to OpenPowerstation 0.3.0, using decoder
`dp3-mr521/0.1.4+7cde8e592258`. This is an unofficial local diagnostic recorder,
not a safety system, secure device-management product, or forensic signing service.
The [security review disposition](SECURITY_REVIEW.md) records the changes in this release.

## What the application protects

OpenPowerstation's outbound allowlists permit the implemented authentication exchange and,
when control is enabled, a small set of explicitly allowlisted device commands.
No firmware commands, cloud telemetry fallback, factory-reset, Wi-Fi, or arbitrary
battery-boundary writes are enabled on either device. The DP3 is sent no clock
synchronization and no power-off. The Jackery Explorer is sent a clock sync on
each new session, which it requires before it answers a status query, and with
control enabled its allowlist includes an Auto Power-Off timer (both described
below). It uses the same local BLE session for status and its allowlisted
controls; no Jackery account, token, or cloud connection is involved.
Raw authentication exchanges are excluded before telemetry persistence. Shareable
exports use explicit field/event allowlists; opaque protocol bytes belong in a
separate private archive. No recording or diagnostic upload is implemented. The
optional Home Assistant bridge publishes an allowlisted subset of current readings
to a broker you configure; it is disabled until you set one, and is described below.

These controls reduce accidental device changes and unintended disclosure. They
do not authenticate incoming measurements or protect a compromised Windows account.

## Device control

Control defaults off in the core application and in a fresh HAOS app
configuration. While it is off the gate refuses every command, and the
application behaves exactly as earlier read-only releases did.

Every installation, including a fresh Home Assistant app, starts with control
off and must opt in explicitly.

With control on, the DP3 can switch its HV and LV AC outputs, `cfg_hv_ac_out_open`
and `cfg_lv_ac_out_open`, carried in `ConfigWrite` to command set `0xFE`, command
`0x11`. The DP3's gate parses the payload that is about to be written, requires
that the only fields present are those explicitly allowlisted, and then rebuilds
the message from that allowlist and requires the bytes to match exactly. Anything
else is refused before it reaches the radio — including a permitted field sent
alongside a forbidden one, and unknown protobuf tags, which survive a
parse-and-re-serialize check because protobuf preserves and re-emits fields it
does not recognize. `cfg_power_off`, `reset_factory_setting`, `cfg_bms_power_off`,
charge limits and the installment-payment fields travel on this same message and
are never sent.

The Jackery is written to only over its authenticated RC4 session, through one
write site that takes the plaintext command, checks it, and encrypts it itself.
Its gate admits exactly three shapes and refuses anything else before it reaches
the radio:

- the status query, action `0xFC`, with no body;
- the clock sync, action `0x0F`, whose body is the host's Unix time and UTC
  offset in seconds, `{"ts":…,"uo":…}`, and nothing else. The station answers no
  status query until it has had one, so a session sends one, under each
  candidate session key, before every status query until the station first
  answers, and none after that;
- with control on, one allowlisted control command, byte for byte as the
  allowlist builds it. Each send spends a single-use grant naming exactly that
  command, so the write site passes no control body that arrives any other way.

The Jackery's allowlisted controls, by the property each one writes:

| Control | Property | Options |
|---|---|---|
| AC output | `oac` | on, off |
| DC output | `odc` | on, off |
| Battery Saving Mode | `lps` | `full` (0), `save` (1, the fixed 15–85% range) |
| Screen timeout | `slt` | `always_on` (0 minutes), `2m` (2), `2h` (120) |
| Charging mode | `cs` | `standard` (0), `quiet` (1) |
| Auto Power-Off | `pm` | `off`, `2h`, `8h`, `12h`, `24h` (0 to 1440 minutes) |

Auto Power-Off is a power-off setting: it sets the station's own automatic
power-off timer to one of Jackery's documented presets, after which the station,
not OpenPowerstation, may switch itself off. No command that powers it off directly is
allowlisted. Screen-timeout status is verified through the returned `sltb`
preset enum, because the station reports a preset rather than the minutes written.

Commands can only be issued after authentication completes, and each one is
recorded in the relevant session. DP3 commands are `control` or
`control_refused`, or `control_unverified` when the write itself fails and the
command may have reached the device anyway. The DP3 acknowledges nothing, so a
DP3 `control` records that the write left the radio, not that the output
changed. What the output did is recorded separately: each change in the low two
bits of `flow_info_ac_hv_out` or `flow_info_ac_lv_out`, the feedback the switches
show, is a `state_change` event, whether a command or the device's own buttons
caused it. Jackery commands become `control` only after status readback confirms
them, `control_unverified` when the write or the readback fails or disagrees, and
`control_refused` when they are refused before reaching the radio. Each readback
is kept as a frame stamped when it was read, as is the reading taken before the
command, so neither appears on the wrong side of it.

The bridge does not send them. It writes a request file that the relevant
collector reads, so the bridge still opens no Bluetooth connection and a broker
that is unreachable, slow or hostile cannot directly reach the radio. Each
request carries its own issue time and is refused unless it is between 0 and 30
seconds old at the moment it would be written. A request that waited in the
queue or behind another write is refused rather than sent late, and so is one
dated later than the clock now reads, which is what a clock stepped back after
it was issued looks like. The handoff is capped at 64 requests per device.
Requests for the same control coalesce to the newest, and filenames carry a
fixed-width sequence tie-breaker after the issue timestamp. That preserves true
numeric issue order when a coarse clock or burst gives 10+ requests the same
timestamp; the same order is used for pruning the oldest queued files. The local handoff is bounded to 64 requests per
device. Requests for the same control coalesce to the newest, and a fixed-width
sequence component preserves numeric issue order when many requests share one
coarse wall-clock timestamp. The same ordering is used to choose the oldest
files for pruning, so a burst cannot make request 9 appear newer than request
10 merely because filenames are compared lexically.

The DP3 AC outputs need a guarded payload. The LV output can power the host that
runs Home Assistant, so the discovered switches' own `ON`/`OFF` never changes an
output. A tap on the device page, a more-info dialog, Developer tools or an
automation calling `switch.turn_off` reaches the bridge. The bridge hands it to
the collector marked unguarded, and the collector records `control_refused`. Only
`GUARDED_ON`/`GUARDED_OFF` on the same command topic is executed. That payload is
published by an arm-then-change interlock such as this deployment's
`script.ecoflow_ac_output_guarded_toggle`. The collector also refuses any
request file that is not explicitly marked guarded.

**What this trusts.** Anything able to publish to your broker can request those
allowlisted changes; MQTT access is the whole authorization boundary, so the
broker account matters as much as the setting. The guarded payload stops
accidental one-tap changes, not a deliberate publisher: it is a fixed string, not
a credential. Home Assistant control state comes from fresh device feedback
rather than the last command OpenPowerstation sent. A missing or
stale readback makes the corresponding control unavailable instead of guessing
that a write succeeded. This improves state honesty but does not authenticate the
device telemetry itself; the Bluetooth identity limits below still apply.

## Bluetooth identity and the login proof

Selection checks the configured Bluetooth address and advertised serial. Those
are identifiers, not cryptographic proof of device identity. The implemented BLE
handshake does not verify a pinned device certificate or a manufacturer-signed
challenge. Its login exchange supplies a proof derived from the account user ID
and serial to the responding peer. Validating the ECDH public point prevents
invalid-point inputs; it does not establish who owns the point.

A nearby malicious device that successfully impersonates the selected DP3 could
receive that ID-derived proof and supply fabricated telemetry. The proof permits
offline guesses of the user ID; OpenPowerstation does not claim a particular ID entropy or
recovery time. Treat the ID and proof as sensitive. Rejecting unsupported session
seeds prevents invalid key derivation; it does not add peer authentication. The
vendored derivation table is public protocol material, not an additional secret.

This is a protocol trust limitation, not evidence that a particular recording was
attacked. It also does not mean every nearby radio can inject packets into an
existing connection without first overcoming the relevant Bluetooth link/access
constraints. OpenPowerstation provides no independent application-level verification of
the peer that would resolve that risk. Use it in a physically controlled setting
and stop connecting if the selected device's behavior or identity is unexpected.

## Encryption and packet checks

The DP3 type 7 handshake uses native OpenSSL 3 secp160r1 ECDH instead of
python-ecdsa, affected by [GHSA-wj6h-64fc-37mp](https://github.com/advisories/GHSA-wj6h-64fc-37mp).
The peer's two 20-byte coordinates and the 20-byte shared secret retain their
existing wire representation. Private keys remain inside native memory and are
freed immediately after exchange. Invalid points are rejected before derivation.
Windows uses the crypto DLL beside CPython's `_ssl` module; the frozen build
includes that runtime. Linux requires the image/system OpenSSL 3 library. Missing
native support fails closed; there is no Python arithmetic fallback.

This removes the affected Python dependency, rather than claiming a timing audit
of every native implementation. The device fixes the legacy 160-bit curve; this
change neither upgrades its strength nor adds peer authentication. Synthetic
exchange tests and native image checks do not qualify a new firmware or a live
radio session. Keep the runtime and container base patched.

The inherited type 1/type 7 codecs use AES-CBC without an application-level MAC
or authenticated-encryption tag. Classic cipher modes provide confidentiality,
not authenticated integrity; see the [PyCryptodome cipher documentation](https://www.pycryptodome.org/src/cipher/classic).
The type 7 codec retains upstream behavior on missing/invalid padding for firmware
compatibility. Padding acceptance is not an authenticity check.

Length, framing, sentinel and CRC checks reject malformed or accidentally damaged
packets. CRCs are not cryptographic signatures and do not establish who generated
the bytes. An attacker controlling an accepted peer can construct internally
consistent data. OpenPowerstation cannot add authenticated device messages without support
from the device protocol; it does not bypass device protections to do so.

## Recordings are not tamper-evident

The SQLite database contains decrypted telemetry and is neither encrypted nor
signed by OpenPowerstation. Anyone who can write to it can alter frames, decoded values,
events, notes, or session metadata. Re-decoding checks consistency with a decoder,
not origin: someone who modifies both the raw and decoded records may preserve
that consistency. A private ZIP's SHA-256 entries detect differences relative to
those checksums; someone who replaces both files and checksums can defeat that
comparison. The executable's adjacent checksum has the same trust limitation.

UTC and monotonic receipt times describe the recording host's observations, not
independently certified device time. Host compromise, time changes, collection
gaps, unavailable fields and unknown message types limit conclusions. Keep the
original recording private, retain an unchanged backup, record the app/decoder
version and conditions, and distinguish reported values from physical measurements.
An incident report alone does not prove that firmware caused a battery failure.

## Credentials, memory and Windows folders

Manual user-ID setup requires no network request. Optional login sends the entered
credentials over HTTPS to the selected fixed EcoFlow regional endpoint. OpenPowerstation
does not save EcoFlow account passwords, login tokens or session keys in
configuration, recordings or exports. The ID and device configuration are retained
locally.

OpenPowerstation stores two credentials. The more valuable is the DP3's numeric **user
ID**: the login proof is derived from it and the serial, and the serial is in
the DP3's own advertisement, so anyone holding the user ID has everything a
nearby radio needs to log in to the DP3 as its owner's app during any reconnect
gap -- and to send commands OpenPowerstation's outbound gate would refuse, because that
gate is OpenPowerstation's, not the device's. The other is the optional Home Assistant
broker password.

On Windows both are DPAPI-protected (`CryptProtectData`, scoped to the current
Windows user) before they are written to `config.json`, and DPAPI-unprotected
on load; their plain fields in the file are left empty. That ties the stored
values to this Windows account's login: another account on the same machine, or
someone who copies `config.json` elsewhere without also having that account's
login, cannot recover them. It does not protect against malware running as this
same account, a compromised OS, or a local administrator -- those can still
reach them through this application's own process. On Windows that file
carries only inherited folder permissions, so use a broker account created
solely for this purpose regardless, so the stored value grants nothing else.
A `config.json` written before this existed still loads its plain-text
`user_id` and `mqtt_password` fields normally, and the next save upgrades them
to the DPAPI-protected form; an OpenPowerstation build from before then refuses the
upgraded file. Without DPAPI (Linux, macOS) the user ID and the broker password
stay in their plain fields, in a `config.json` only its owner can read (mode
600 from the moment it is created, so neither is ever briefly readable by other
accounts).

**The HAOS app does not have this protection.** Its configuration arrives as
`import.json` in the app's `addon_configs` folder -- which the Samba app shares
and the nightly backup includes -- and the app copies it to `/data/config.json`.
Both hold the user ID and the broker password in plain text, readable to
anyone with the Samba credential or a copy of a backup. Moving them out needs a
release of the app, not a code change alone: the broker credentials from the
Supervisor's MQTT service (`services: mqtt:need`, then
`http://supervisor/services/mqtt`), and the DP3 address, serial and user ID as
app options with the `password` schema type, after which `import.json`, its
`addon_config` map and `scripts/export_opendp3_pi_config.py` can go. Until
then, treat the Samba account and the backups as holding the DP3's login.

DPAPI exists only on Windows. On Linux and macOS the password stays in the
plain `mqtt_password` field, and `config.json` is created readable and writable
by its owner alone (mode 0600). The Home Assistant app writes its working copy
the same way.

Passwords and the login response necessarily exist in process memory during setup.
Clearing a dictionary, closing a dialog or dropping a Python reference does not
guarantee secure erasure of immutable strings, library buffers, Qt text buffers,
crash dumps or the Windows pagefile. The application makes no claim that secrets
are never in memory or that the operating system cannot persist memory contents.

The default data location is the current user's application-data directory. For
compatibility, a frozen executable reuses an existing `data` directory beside
it, and launchers/`--data-dir` can choose a different location. Windows permissions
are inherited: OpenPowerstation does **not** automatically tighten or audit Windows ACLs.
Its setup warning is informational, not detection or enforcement of safe ACLs.

Keep the executable and data in folders private to your Windows account. Avoid
shared, public, network or broadly writable portable folders. Other users with
access could replace configuration to redirect selection, and read the account ID
from a `config.json` written before it was DPAPI-protected and not saved since.
Review the folder's Windows Security permissions before setup; do not grant
other accounts write access for convenience. Running as administrator is not
required and does not solve this trust problem. Local administrators, malware
running as you, and a compromised operating system are outside this app's defenses.

## Home Assistant bridge

The bridge is optional and off until a broker address is saved. It is a separate
process: it opens the recording database read-only and never writes to it, never
reaches the Bluetooth transport, and never opens a listening socket. The only
network activity is an outbound MQTT connection to the broker you configure. A
broker that is unreachable, slow or hostile cannot stall or stop a recording,
because the collector does not depend on the bridge in any way.

What the bridge publishes is the allowlisted numeric field set from the decoder,
plus capture-health diagnostics. Raw frames, decoded protobuf documents, the
account ID and the operating-conditions note are never published. The device
serial is not published either: topics and entity identifiers use the same
SHA-256-derived handle the collector already uses for its per-device lock. The
handle is pseudonymous rather than anonymous: it is unsalted, so anyone who can
guess the serial can confirm it. The Jackery collector's log lines name the unit
by the same handle, never by its serial, and record state changes rather than
every reading.

Anything the broker can read, everyone with access to that broker can read.
Published values are retained on the broker until overwritten. Readings older than
45 seconds, recordings that have stopped, and synthetic demonstration data are all
published as unavailable rather than as current values, so a stale number is not
mistaken for a live one. None of this authenticates the measurements themselves;
the trust limits described above apply unchanged to anything Home Assistant shows.

## Opening recordings from other sources

Open only recordings from a source you trust. `mode=ro` and `query_only` limit
database writes; they are not a sandbox. SQLite still parses the file, and the
viewer parses stored JSON and numerical data. A malicious file can cause errors,
excessive processing or exploit a dependency flaw. OpenPowerstation does not qualify its
viewer as safe for arbitrary hostile SQLite files. Keep the app and its bundled
dependencies updated. See [SQLite's guidance for untrusted input](https://www.sqlite.org/security.html).

## Distributing diagnostics

Share the sanitized export first. Inspect any separately requested raw archive
before sharing: unknown telemetry can contain identifiers. Do not send
`config.json`, account credentials, memory dumps or unreviewed logs. Parser
failures in this release report constant messages without payload bytes. That
does not sanitize logs produced by external tools or captures from older builds.

The Windows executable is an unsigned development build. Do not disable Windows
security protections to run it. These targeted fixes and tests are not a complete
independent security audit, hardware certification, or proof of protocol security.
