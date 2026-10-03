"""Regression coverage for DPAPI-at-rest protection of mqtt_password.

See docs/SECURITY.md, "Credentials, memory and Windows folders", and
config.py's load_config/save_config. Runs against the real Windows DPAPI
(no mocking), against the real Windows API rather than a stand-in.
"""
import json
import os

import pytest

from opendp3 import dpapi
from opendp3.config import Config, load_config, save_config


pytestmark = pytest.mark.skipif(os.name != "nt", reason="real Windows DPAPI")


def sample_config(**overrides):
    values = dict(address="AA:BB:CC:DD:EE:FF", serial="MR51123456789012", user_id="123456")
    values.update(overrides)
    return Config(**values)


def test_dpapi_round_trips_arbitrary_bytes():
    secret = b"PRIVATE_VALUE \x00\xff not text"
    assert dpapi.unprotect(dpapi.protect(secret)) == secret


def test_mqtt_password_round_trips_through_save_and_load(tmp_path):
    path = tmp_path / "config.json"
    save_config(sample_config(mqtt_password="hunter2"), path)
    assert load_config(path).mqtt_password == "hunter2"


def test_mqtt_password_is_not_stored_in_plaintext_on_disk(tmp_path):
    path = tmp_path / "config.json"
    save_config(sample_config(mqtt_password="PRIVATE_BROKER_PASSWORD"), path)
    raw = path.read_text("utf-8")
    assert "PRIVATE_BROKER_PASSWORD" not in raw
    stored = json.loads(raw)
    assert stored["mqtt_password"] == ""
    assert stored["mqtt_password_dpapi"]


def test_empty_mqtt_password_writes_no_dpapi_key(tmp_path):
    path = tmp_path / "config.json"
    save_config(sample_config(), path)
    stored = json.loads(path.read_text("utf-8"))
    assert stored["mqtt_password"] == ""
    assert "mqtt_password_dpapi" not in stored


def test_legacy_plaintext_config_still_loads(tmp_path):
    # A config.json written before this feature existed: plain field, no
    # mqtt_password_dpapi key at all.
    path = tmp_path / "config.json"
    values = {"address": "AA:BB:CC:DD:EE:FF", "serial": "MR51123456789012",
              "user_id": "123456", "mqtt_password": "legacy-plaintext"}
    path.write_text(json.dumps(values), encoding="utf-8")
    assert load_config(path).mqtt_password == "legacy-plaintext"


def test_loading_then_resaving_a_legacy_config_upgrades_it(tmp_path):
    path = tmp_path / "config.json"
    values = {"address": "AA:BB:CC:DD:EE:FF", "serial": "MR51123456789012",
              "user_id": "123456", "mqtt_password": "legacy-plaintext"}
    path.write_text(json.dumps(values), encoding="utf-8")
    cfg = load_config(path)
    save_config(cfg, path)
    stored = json.loads(path.read_text("utf-8"))
    assert stored["mqtt_password"] == ""
    assert stored["mqtt_password_dpapi"]
    assert load_config(path).mqtt_password == "legacy-plaintext"


def test_corrupt_dpapi_blob_fails_safely(tmp_path):
    path = tmp_path / "config.json"
    values = {"address": "AA:BB:CC:DD:EE:FF", "serial": "MR51123456789012",
              "user_id": "123456", "mqtt_password_dpapi": "not-valid-base64-or-dpapi!!"}
    path.write_text(json.dumps(values), encoding="utf-8")
    with pytest.raises(ValueError, match="Invalid configuration file") as error:
        load_config(path)
    assert "DPAPI" not in str(error.value) and "Windows" not in str(error.value)


def test_dpapi_blob_from_a_different_protection_scope_fails_safely(tmp_path):
    # Simulate a config.json moved from another Windows account/machine: a
    # syntactically valid DPAPI blob this account cannot unprotect.
    import base64
    foreign = base64.b64encode(dpapi.protect(b"not for this config")).decode("ascii")
    # Flip a byte inside the blob so it's well-formed base64 but not a blob
    # this account's DPAPI will accept -- CryptUnprotectData fails closed on
    # any input it did not itself produce for this account.
    damaged = bytearray(base64.b64decode(foreign))
    damaged[-1] ^= 1  # Always change a byte, even when the original is zero.
    corrupted = base64.b64encode(damaged).decode("ascii")
    path = tmp_path / "config.json"
    values = {"address": "AA:BB:CC:DD:EE:FF", "serial": "MR51123456789012",
              "user_id": "123456", "mqtt_password_dpapi": corrupted}
    path.write_text(json.dumps(values), encoding="utf-8")
    with pytest.raises(ValueError, match="Invalid configuration file"):
        load_config(path)
