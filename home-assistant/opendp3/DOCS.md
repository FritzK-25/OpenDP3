# OpenPowerstation Battery Telemetry

Runs the OpenPowerstation EcoFlow DELTA Pro 3 and Jackery Explorer 1000 v2 Bluetooth
collectors and their MQTT publishers on Home Assistant OS. The app uses the
host's BlueZ through D-Bus, keeps SQLite recordings in its persistent `/data`,
and restarts exited workers. Desktop GUI dependencies are not used.

OpenPowerstation is an independent community project, not affiliated with EcoFlow or
Jackery, and it is not a safety monitor or a replacement for a battery's own
protections.

## Status

This is a public preview. The app has been run on a Raspberry Pi 5 (`aarch64`)
with both batteries on the onboard Bluetooth radio. `amd64` is declared but has
not been tested on real hardware. Host-reboot and overnight reliability
qualification is incomplete.

## Install

1. In Home Assistant open **Settings → Apps → App store → ⋮ → Repositories**
   and add `https://github.com/FritzK-25/OpenPowerstation`.
2. Install **OpenPowerstation Battery Telemetry**.
3. Create the configuration file described below, then start the app.

You need an MQTT broker, for example the Mosquitto app, and Bluetooth on the
Home Assistant host.

## Configuration file

The EcoFlow collector reads `import.json` from the app's configuration folder
(`/addon_configs/<id>_opendp3/import.json` on the host, mounted at
`/config/import.json`). It holds the device identity and broker settings, so
keep it private and never commit it. Fields:

```json
{
  "address": "AA:BB:CC:DD:EE:FF",
  "serial": "MR51XXXXXXXXXXXX",
  "user_id": "1234567890",
  "region": "US",
  "mqtt_host": "core-mosquitto",
  "mqtt_port": 1883,
  "mqtt_username": "opendp3",
  "mqtt_password": "use-a-dedicated-account"
}
```

- `address` and `serial` come from `openpowerstation scan` on any machine with
  Bluetooth, or from the Windows application's setup page.
- `user_id` is your EcoFlow account's numeric user ID.
- Use a broker account created only for this, so the stored password grants
  nothing else.

### Keeping existing entity IDs

Home Assistant creates each entity under the ID discovery suggests, such as
`sensor.opendp3_bms_batt_soc`. If your entities already carry other IDs (for
example ones Home Assistant derived from their names), put an `entity_ids.json`
beside `import.json` naming them, keyed by device and discovery key:

```json
{
  "dp3": {"bms_batt_soc": "sensor.my_battery_soc"},
  "jackery": {"bms_batt_soc": "sensor.my_explorer_soc"}
}
```

An entity Home Assistant creates again (after deleting the device, or
re-adding the MQTT integration) then lands back on the ID your dashboards and
automations use. The file changes no entity that already exists. The app
refuses to start if the file is malformed.

## App options

| Option | Default | Meaning |
|---|---|---|
| `ecoflow_enabled` | `true` | Run the DELTA Pro 3 collector and bridge |
| `jackery_enabled` | `false` | Run the Jackery collector and bridge |
| `jackery_serial` | `""` | The Jackery's 15-digit serial; required when Jackery is enabled |
| `ecoflow_adapter` | `hci0` | Bluetooth adapter for the DELTA Pro 3 |
| `jackery_adapter` | `hci0` | Bluetooth adapter for the Jackery |
| `allow_control` | `false` | Expose the guarded control entities |

**Control is off by default.** With it on, OpenPowerstation accepts only its allowlisted
output and battery-saving commands, only while the collector holds a live
Bluetooth session, and it rejects retained MQTT commands. Everything else is
refused before it reaches the radio. See
[Security and evidence trust](https://github.com/FritzK-25/OpenPowerstation/blob/main/docs/SECURITY.md).

## How it runs

The app starts the MQTT publishers first, then the Bluetooth collectors, each
as its own process:

```text
ecoflow-mqtt      -> read-only recordings.sqlite -> MQTT
ecoflow-collector -> BLE -> recordings.sqlite

jackery-mqtt      -> read-only jackery.sqlite -> MQTT
jackery-collector -> BLE -> jackery.sqlite
```

The publishers never open Bluetooth, and the collectors never wait on MQTT to
keep recording. A control request goes the other way, through a file queue
beside the recording: the publisher validates the MQTT message and writes the
request; the collector checks the policy again immediately before the radio
write. A request is valid for 30 seconds, at most 64 wait per device, repeated
presses of one control coalesce to the newest, and anything still queued when
the app starts is discarded rather than sent.

The Jackery's serial comes only from the `jackery_serial` option and is passed
to both its collector and its publisher. Discovery connects only to that
serial, so another Explorer in range is never opened.

On a stop the app asks every worker to stop at once and gives them 40 seconds
together to close their Bluetooth links and recordings, inside the
Supervisor's 60-second stop timeout.

## Connection recovery

A per-collector valid-frame lease restarts only a worker that first produced
fresh telemetry in its current process and then stalled. A powered-off battery
that never produced a frame does not enter a restart loop. Both leases are 420
seconds, above the longest gap a collector has been recorded recovering from
unaided (313 seconds).

After an app restart the EcoFlow device can stay connected in BlueZ and stop
advertising. The Linux wrapper disconnects only the configured EcoFlow device
before rediscovery, after acquiring its process lock. The Jackery's BlueZ
address is recorded privately after a serial-matched connection and gets the
same targeted cleanup. No code path resets or powers down the whole adapter.

Discovery and authentication take a short lease scoped to the selected adapter,
so two batteries sharing one radio do not scan at the same time. Healthy
connections run concurrently after the lease is released.

The targeted cleanup helps rediscovery but never blocks it. If BlueZ refuses,
errors, or takes more than 12 seconds, the log says
`BlueZ orphan check for EcoFlow skipped (<error type>); discovering anyway.`
(or `for Jackery`) once, and `... working again.` when it next succeeds. The
one exception is two connected BlueZ devices that both match the configured
Jackery: the app cannot tell which link is its own, so that attempt is refused
and retried. The Jackery waits for its advertisement outside the adapter
lease, because it may not advertise for hours, and no holder keeps the lease
for more than 60 seconds.

## Backup and rollback

The app declares `backup: cold`: Home Assistant stops it before backing up its
data and starts it again afterwards, so SQLite files are not captured while
being written. Stopping or uninstalling the app is the rollback. Keep a backup
before uninstalling because recordings live in `/data`.

## Maintaining the app

The app builds from this folder only, so `src/` here is a committed copy of the
repository's sources. After changing the sources, run
`python scripts/sync_ha_app.py`; the test suite fails if the copy drifts.
