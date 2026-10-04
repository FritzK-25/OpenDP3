# OpenPowerstation

Local Bluetooth telemetry, history, diagnostics and optional Home Assistant
integration for supported EcoFlow and Jackery power stations.

No cloud telemetry is required. Your recording stays on your own machine.

> **Public preview (0.2.0).** The recorder, decoder, storage and tests are
> mature, but hardware qualification is incomplete. See
> [what has and has not been qualified](#status-and-known-limits).

OpenPowerstation is an independent community project. It is not affiliated with,
endorsed by, or supported by EcoFlow or Jackery. It is diagnostic and
automation software, **not a safety monitor and not a replacement for the
battery's own protections (BMS)**.

## Currently tested with

| Device | Transport | Status |
|---|---|---|
| EcoFlow DELTA Pro 3 | Bluetooth LE | Telemetry and guarded control exercised on real hardware; long-run qualification incomplete |
| Jackery Explorer 1000 v2 | Bluetooth LE | Telemetry and guarded control exercised on real hardware |

Other models and firmware revisions are untested. If you own one of these and
can help, see [Help test it](#help-test-it).

## Pick how you want to use it

**I just want to record my battery (Windows).** Download `OpenDP3-0.2.0.exe`
and its `.sha256` file from the [Releases page](../../releases), check the hash,
and run it. Scan for your battery, choose it, and press record. The executable
is unsigned, so Windows SmartScreen will warn you; the hash and the build
workflow are how you check what you are running.

**I use Home Assistant OS.** Add this repository to the Home Assistant app
store, then install **OpenPowerstation Battery Telemetry**. It records over the host's
Bluetooth and publishes entities through MQTT discovery. See
[the app guide](home-assistant/opendp3/DOCS.md).

**I am a developer or researcher.** Clone the repository, `pip install -e .`,
and use the command line, the protocol decoder, the storage layer and the test
suite.

```bash
git clone https://github.com/FritzK-25/OpenPowerstation.git
cd OpenPowerstation
python -m venv .venv
.venv/bin/pip install -e ".[test]"      # Windows: .venv\Scripts\pip
.venv/bin/python -m pytest -q
.venv/bin/python -m openpowerstation scan        # find nearby supported batteries
```

Add `.[gui]` for the desktop window and `.[charts]` for evidence export.
Python 3.12 or newer is required.

## What it does

- **See what is happening:** charge, temperature and power in and out, where
  the device reports them.
- **Look back when something seems wrong:** browse charts, replay a recording,
  and export the readings around an unusual change or lost connection.
- **Keep your history local:** recording needs no cloud account. EcoFlow setup
  needs your account's numeric user ID, which stays on your machine.
- **Optionally publish to Home Assistant:** current readings over MQTT, and,
  only if you turn on *Allow control*, a small allowlist of outputs and
  battery-saving settings.

Control is **off by default** and gated by an explicit outbound allowlist. See
[Security and evidence trust](docs/SECURITY.md).

## Status and known limits

- The DELTA Pro 3 eight-hour recording qualification reached about 6 h 19 m and
  has not been completed.
- Offline reconnect qualification is still pending.
- Only the devices in the table above have been exercised on real hardware.
- Linux and macOS are not qualified for the desktop application. The Home
  Assistant app targets Linux with BlueZ.
- Telemetry fields that are raw codes or community mappings are labelled as
  diagnostic, not as verified measurements.

The full checklist and results are in
[hardware qualification](docs/HARDWARE_QUALIFICATION.md).

## Help test it

The most valuable contribution is a report from a different firmware or
hardware revision. Open an issue with the device, firmware label, operating
system, what worked, and what did not. **Do not post** serial numbers,
Bluetooth addresses, account IDs, MQTT credentials or raw recordings.

## Documentation

| | |
|---|---|
| [Reference manual](docs/REFERENCE.md) | Using the viewer, the event catalog, storage, privacy, the command line |
| [Security and evidence trust](docs/SECURITY.md) | What is sent, what is blocked, and what the recorder cannot prove |
| [Protocol schema](docs/PROTOCOL_SCHEMA.md) | The pinned protocol definitions OpenPowerstation decodes |
| [Validation](docs/VALIDATION.md) | What the tests do and do not establish |
| [Windows packaging](docs/PACKAGING.md) | Building and verifying the executable |
| [Home Assistant app](home-assistant/opendp3/DOCS.md) | Installing and configuring the app |
| [Third-party notices](THIRD_PARTY_NOTICES.md) | Upstream code, licences and local changes |

## Licence and credit

OpenPowerstation is licensed under the [Apache License 2.0](LICENSE). Its protocol
foundation is [ha-ef-ble](https://github.com/rabits/ha-ef-ble) (Apache-2.0).
The desktop build bundles Qt for Python (LGPL-3.0). Notices and licence texts
are in [THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md) and
[THIRD_PARTY_LICENSES](THIRD_PARTY_LICENSES).
