OpenDP3's own code is licensed under the Apache License 2.0 (see `LICENSE`).
OpenDP3 is an independent project and is not affiliated with EcoFlow or Jackery.

# Third-party source

The files in `src/opendp3/vendor/` originate from
https://github.com/rabits/ha-ef-ble at commit
`7cde8e5922589b5e3c81585890b5188747f5b037` (2026-08-25).
Original locations: `custom_components/ef_ble/eflib/` and its `pb/` directory.
Copyright belongs to the original ha-ef-ble contributors. Licensed under
Apache License 2.0; full text in `THIRD_PARTY_LICENSES/ha-ef-ble.txt`.

OpenDP3's discovery, authentication and field mapping are adapted from that
revision's `connection.py`, `devicebase.py`, `devices/delta_pro_3.py` and
`props/resv_info_parser.py`, and the setup-only `login.py`. The application does
not import that revision's time-sync, Home Assistant, or automatic response code.

From its control code OpenDP3 adapts only the DELTA Pro 3 `ConfigWrite` routing
and the two AC output setters (`enable_ac_hv_port`, `enable_ac_lv_port`) in
`devices/delta_pro_3.py`. The upstream control framework, its decorators and its
remaining setters are not imported; OpenDP3 emits those two fields alone, behind
an opt-in setting and its own outbound allowlist.

Local changes in OpenDP3 0.1.1:

- `vendor/encryption.py`: removed the unused developer-diagnostics `Session`
  class, hardcoded recipient public key, `_counter_nonce` helper and exclusive
  imports. This code was present in the pinned upstream source; no OpenDP3 caller
  was found. The retained AES strategy classes are unchanged.
- `vendor/packet.py`: removed raw input bytes from all nine parse-error log
  messages and returned error descriptions. Framing logic is unchanged.
- Both modified modules carry a local-change notice. The application wrapper
  rejects incomplete session-key table material and invalid-packet objects.

Provenance comparison and regression results: [security review](https://github.com/FritzK-25/OpenDP3/blob/main/docs/SECURITY_REVIEW.md).
These deletions do not remove or supersede upstream copyright/license notices.

Python dependencies retain their respective licenses. The Windows executable
bundles Python and dynamically loaded Qt/PySide6 6.11.2 libraries from their
original wheels. Qt/PySide6 is used under the open-source LGPLv3 option;
LGPLv3 and GPLv3 texts are included in THIRD_PARTY_LICENSES. In the executable,
Help > Third-party licenses exposes dependency versions and supplied notices.
Qt and PySide6 are unmodified. Corresponding upstream source is available at
https://download.qt.io/official_releases/qt/6.11/6.11.2/submodules/ and
https://download.qt.io/official_releases/QtForPython/pyside6/PySide6-6.11.2-src/.
Rebuilding with alternative compatible libraries is supported by the supplied
source and packaging/build.ps1. Debugging modifications to LGPL libraries is
not restricted. Release executables are built from the tagged source by the
repository's public Release workflow and published with a SHA-256 file and a
build provenance attestation. They are not code-signed.
The vendored protocol research includes the upstream session derivation table;
this is protocol material, not a user's password or captured session key.

Jackery BLE framing, key derivation, multi-packet response handling, and command
identifiers were independently checked against the community Private Jack Home
Assistant integration at https://github.com/porcupin26/private_jack (accessed
2026-09-04). No Private Jack source file is vendored or imported by OpenDP3.
