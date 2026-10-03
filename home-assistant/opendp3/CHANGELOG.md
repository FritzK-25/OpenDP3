# 0.2.0

First public release. Versioned together with the OpenDP3 core. Control stays
off by default, the app builds from a committed copy of the sources, and
`amd64` is declared alongside `aarch64`.

## 0.1.6

Raise both collector valid-frame leases to 420 seconds. The 60-second
Jackery lease sat below gaps that collector recovers from unaided and
restarted a live collector, which cost a rediscovery and lengthened the
next gap. Worst gap either collector has been recorded recovering from
by itself is 313 seconds; both leases now sit above it.

## 0.1.5
Recover collectors from expired valid-frame leases even when their processes
remain alive. Serialize discovery per Bluetooth adapter, retain exact BlueZ
failure context, and disconnect only the affected configured device before a
targeted restart. EcoFlow and Jackery can optionally be assigned to separate
adapters without enabling any controller-wide reset.

## 0.1.3

Create cold Home Assistant backups so SQLite and BLE collectors shut down before
app data is archived. Embed and log the staged OpenDP3 source provenance.

Publish OpenDP3's guarded EcoFlow and Jackery control entities when the
add-on control policy is enabled, restoring the Power-dashboard controls.

## 0.1.2

Recover an orphaned EcoFlow BlueZ connection before rediscovery, only for the
configured device and after the recorder acquires its process lock.

## 0.1.1

Add read-only BlueZ connection diagnostics.

## 0.1.0

Initial headless HAOS packaging of the existing EcoFlow and Jackery collectors.
