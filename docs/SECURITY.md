# Security and evidence trust

Applies to OpenDP3 0.2.0, using decoder
`dp3-mr521/0.1.4+7cde8e592258`. This is an unofficial local diagnostic recorder,
not a safety system, secure device-management product, or forensic signing service.
The [security review disposition](SECURITY_REVIEW.md) records the changes in this release.

## What the application protects

OpenDP3's outbound allowlist permits the implemented authentication exchange and,
when control is enabled, a small set of explicitly allowlisted device commands.
No firmware commands, clock synchronization, cloud telemetry fallback, power-off,
factory-reset, Wi-Fi, or arbitrary battery-boundary writes are enabled. The Jackery
Explorer uses the same local BLE session for status and its allowlisted controls;
no Jackery account, token, or cloud connection is involved.
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

With control on, the DP3 can switch its HV and LV AC outputs, carried in `ConfigWrite`
to command set `0xFE`, command `0x11`. The Jackery can switch its AC and DC
outputs, set the fixed Battery Saving Mode (`lps=1`, 15–85%), and set the display
timeout through its allowlisted `slt` command (`0`, `2`, or `120` minutes) over its
authenticated RC4 session. Screen-timeout status is verified separately through
the returned `sltb` preset enum before the command is recorded as confirmed. The
gate parses the payload that is about to be written, requires that the only
fields present are those explicitly allowlisted, and then rebuilds the message from that allowlist
and requires the bytes to match exactly. Anything else is refused before it
reaches the radio — including a permitted field sent alongside a forbidden one,
and unknown protobuf tags, which survive a parse-and-re-serialize check because
protobuf preserves and re-emits fields it does not recognize. `cfg_power_off`,
`reset_factory_setting`, `cfg_bms_power_off`, charge limits and the
installment-payment fields travel on this same message and are never sent.

Commands can only be issued after authentication completes, and each one is
recorded in the relevant session. DP3 commands are `control` or
`control_refused`; Jackery commands become `control` only after status readback
confirms them, otherwise they are recorded as `control_unverified`.

The bridge does not send them. It writes a request file that the relevant
collector reads, so the bridge still opens no Bluetooth connection and a broker
that is unreachable, slow or hostile cannot directly reach the radio.

**What this trusts.** Anything able to publish to your broker can request those
allowlisted changes; MQTT access is the whole authorization boundary, so the
broker account matters as much as the setting. Home Assistant control state comes
from fresh device feedback rather than the last command OpenDP3 sent. A missing or
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
offline guesses of the user ID; OpenDP3 does not claim a particular ID entropy or
recovery time. Treat the ID and proof as sensitive. Rejecting unsupported session
seeds prevents invalid key derivation; it does not add peer authentication. The
vendored derivation table is public protocol material, not an additional secret.

This is a protocol trust limitation, not evidence that a particular recording was
attacked. It also does not mean every nearby radio can inject packets into an
existing connection without first overcoming the relevant Bluetooth link/access
constraints. OpenDP3 provides no independent application-level verification of
the peer that would resolve that risk. Use it in a physically controlled setting
and stop connecting if the selected device's behavior or identity is unexpected.

## Encryption and packet checks

The inherited type 1/type 7 codecs use AES-CBC without an application-level MAC
or authenticated-encryption tag. Classic cipher modes provide confidentiality,
not authenticated integrity; see the [PyCryptodome cipher documentation](https://www.pycryptodome.org/src/cipher/classic).
The type 7 codec retains upstream behavior on missing/invalid padding for firmware
compatibility. Padding acceptance is not an authenticity check.

Length, framing, sentinel and CRC checks reject malformed or accidentally damaged
packets. CRCs are not cryptographic signatures and do not establish who generated
the bytes. An attacker controlling an accepted peer can construct internally
consistent data. OpenDP3 cannot add authenticated device messages without support
from the device protocol; it does not bypass device protections to do so.

## Recordings are not tamper-evident

The SQLite database contains decrypted telemetry and is neither encrypted nor
signed by OpenDP3. Anyone who can write to it can alter frames, decoded values,
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
credentials over HTTPS to the selected fixed EcoFlow regional endpoint. OpenDP3
does not save EcoFlow account passwords, login tokens or session keys in
configuration, recordings or exports. The ID and device configuration are retained
locally.

The one credential OpenDP3 does store is the optional Home Assistant broker
password. It is DPAPI-protected (`CryptProtectData`, scoped to the current
Windows user) before it is written to `config.json`, and DPAPI-unprotected on
load; the plain field in the file is left empty. That ties the stored value to
this Windows account's login: another account on the same machine, or someone
who copies `config.json` elsewhere without also having that account's login,
cannot recover it. It does not protect against malware running as this same
account, a compromised OS, or a local administrator -- those can still reach
the password through this application's own process. On Windows that file
carries only inherited folder permissions, so use a broker account created
solely for this purpose regardless, so the stored value grants nothing else.
A `config.json` written before this existed still loads its plain-text
`mqtt_password` field normally, and the next save upgrades it to the
DPAPI-protected form.

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
are inherited: OpenDP3 does **not** automatically tighten or audit Windows ACLs.
Its setup warning is informational, not detection or enforcement of safe ACLs.

Keep the executable and data in folders private to your Windows account. Avoid
shared, public, network or broadly writable portable folders. Other users with
access could read the account ID or replace configuration to redirect selection.
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
SHA-256-derived handle the collector already uses for its per-device lock.

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
excessive processing or exploit a dependency flaw. OpenDP3 does not qualify its
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
