# Security policy

## Reporting a vulnerability

Please report security problems privately through GitHub's
**Security → Report a vulnerability** form on this repository, not in a public
issue. Include the version, what you observed, and how to reproduce it, without
posting real serial numbers, account IDs or credentials.

This is a volunteer project. Expect an acknowledgement within about a week.

## Scope

In scope: the outbound command allowlist, the control gate, handling of
credentials and recordings, the MQTT bridge, and the packaged executable.

Out of scope: weaknesses in the vendors' own Bluetooth protocols, which this
project reads but does not control.

## How the project thinks about it

[docs/SECURITY.md](docs/SECURITY.md) describes the threat model, what is
protected and what is not. In short: control is off by default, only a small
allowlist of commands can ever reach the radio, and OpenPowerstation is not a safety
system.
