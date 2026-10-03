# Security review disposition — 2026-08-30

Release: **OpenDP3 0.1.1**. Decoder:
`dp3-mr521/0.1.2+7cde8e592258`. This records the response to the supplied code
review. The [threat model](SECURITY.md) describes the remaining boundaries.

## 1. Unused developer-key diagnostic encryption: verified and removed

Before editing, `vendor/encryption.py` matched the pinned
[upstream encryption module](https://github.com/rabits/ha-ef-ble/blob/7cde8e5922589b5e3c81585890b5188747f5b037/custom_components/ef_ble/eflib/encryption.py)
after normalizing line endings. The SHA-256 of both UTF-8, LF-normalized files was:

```text
63d8c236d15f3eeaa4d7ecb1be675bd2a22351c51d47cd1fd0b195a94489d502
```

The unused `Session` helper and hardcoded recipient public key were present in
that upstream revision; they were not an unexplained local insertion. No caller
or upload path was found in OpenDP3. This finding does not establish exfiltration
or a supply-chain compromise. The unrelated live BLE `Session` class is required
and remains.

Removed the unused diagnostics helper, its nonce helper and its exclusive imports.
The three retained AES strategy/class bodies are unchanged from the pinned source.
The packaging verifier inspects the built module's compiled code to check that the
removed class/key machinery is absent, without executing that module. Attribution
and Apache-2.0 license text are retained in [third-party notices](../THIRD_PARTY_NOTICES.md).

## 2. Invalid session-key seed: fail closed

The 65,280-byte table has no complete 16-byte window for 2,176 of the 65,536
possible two-byte seeds. Previously both slices were empty for those seeds.
Derivation now requires both eight-byte slices in full and raises a controlled
`AuthenticationError` otherwise. It also rejects partially available material.

Regression tests enumerate every seed and check golden vectors at the start and
end of the valid table range. A simulated encrypted handshake verifies that a bad
seed stops processing before the status request or user-ID login proof is sent.
Valid key derivation and the existing ECDH point validation are unchanged.

The review's suggestion that this public table provides another secret barrier
after ECDH compromise is not a supported security claim. The fix enforces the
implemented protocol's supported input range; it does not strengthen peer identity.

## 3. InvalidPacket subclass: explicitly rejected

`parse_packet()` now checks `Packet.is_invalid()` as well as the result's type.
The existing framing/CRC checks remain. A regression test supplies a valid outer
frame while forcing the vendored parser to return `InvalidPacket`, and verifies
that it is rejected rather than recorded as an accepted telemetry packet.

## 4. Peer identity and authenticated integrity: documented limitation

The README and threat model now explain spoofable address/serial selection,
exposure of the ID-derived proof to an impersonating peer, AES-CBC without a MAC,
and the distinction between CRC/replay consistency and authenticity. Recordings
and adjacent checksums are explicitly described as not tamper-evident. The setup
dialog displays the device-identity limitation.

No new cryptographic device authentication is claimed. No precise user-ID length,
brute-force time, or unconditional over-the-air injection claim is adopted from
the review: those require evidence about the ID space and actual attack conditions.
The upstream CBC and padding behavior remain unchanged for compatibility.

## 5. Packet error logs: payloads removed

All nine payload-bearing error paths in the pinned
[packet module](https://github.com/rabits/ha-ef-ble/blob/7cde8e5922589b5e3c81585890b5188747f5b037/custom_components/ef_ble/eflib/packet.py)
now emit constant messages. Returned invalid-packet descriptions also omit the
bytes. Tests exercise malformed V3/V4 inputs and check both log records and
returned descriptions. Some upstream branches are structurally unreachable;
they were sanitized too. Packet parsing behavior is otherwise unchanged.

## 6. Configuration, login and local-file boundaries

| Finding | Disposition |
| --- | --- |
| Malformed login JSON and unexpected response shapes | Controlled messages; no parser fragments or response contents in the error. Invalid user-ID types are rejected. |
| Malformed configuration, missing/unexpected keys and wrong types | Controlled `ValueError`; CLI regression verifies no traceback or input echo. Anomaly settings reject booleans, non-finite values and out-of-range values. |
| Password lifetime | Documented: not intentionally persisted, but present in memory; reference clearing is not secure erasure. |
| Portable-folder Windows permissions | Setup warning and private-folder guidance added. No automatic ACL audit/tightening or data migration; residual local-access risk remains. |
| Foreign SQLite recordings | Picker says to use a trusted source; threat model explains read-only access is not a parser sandbox. No claim of hostile-file isolation. |

## Validation and rollout

The regression suite passed **79 tests** on Windows/Python 3.12.8, including the
existing outbound-policy, credential-exclusion, recorder, retention, replay and
Qt tests. These are software tests with synthetic/mocked inputs. See
[software validation](VALIDATION.md) for the executable verification result.

The decoder revision changes because packet acceptance changed; this release
does not rewrite historical recordings or change valid temperature/power mappings.
Already running processes retain their loaded code. A separately named
`OpenDP3-0.1.1.exe` can be built and checked without replacing the original
executable or ending a recording. Switch to it after the current run ends.

The updated release still requires real-device reconnection and offline/hardware
qualification. No fault was deliberately induced, no battery settings were
changed, and synthetic tests do not complete the eight-hour hardware requirement.
