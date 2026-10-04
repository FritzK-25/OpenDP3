"""Regression coverage for DPAPI-at-rest protection of mqtt_password.

See docs/SECURITY.md, "Credentials, memory and Windows folders", and
config.py's load_config/save_config. Runs against the real Windows DPAPI
(no mocking), against the real Windows API rather than a stand-in.
"""
import json
import os

import pytest

from openpowerstation import dpapi
from openpowerstation.config import Config, load_config, save_config


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


# The DP3 user ID: with the serial the DP3 advertises to anyone in range, it is
# the whole Bluetooth login proof, and it used to be the one account value
# written in clear while the lower-value broker password was protected.
PRIVATE_USER_ID = "9876543210123456789"


def test_user_id_is_not_stored_in_plaintext_on_disk(tmp_path):
    path = tmp_path / "config.json"
    save_config(sample_config(user_id=PRIVATE_USER_ID), path)
    raw = path.read_text("utf-8")
    assert PRIVATE_USER_ID not in raw
    stored = json.loads(raw)
    assert stored["user_id"] == ""
    assert stored["user_id_dpapi"]
    assert load_config(path).user_id == PRIVATE_USER_ID


def test_a_plaintext_user_id_still_loads_and_the_next_save_protects_it(tmp_path):
    path = tmp_path / "config.json"
    values = {"address": "AA:BB:CC:DD:EE:FF", "serial": "MR51123456789012",
              "user_id": PRIVATE_USER_ID}
    path.write_text(json.dumps(values), encoding="utf-8")
    cfg = load_config(path)
    assert cfg.user_id == PRIVATE_USER_ID
    save_config(cfg, path)
    assert PRIVATE_USER_ID not in path.read_text("utf-8")
    assert load_config(path).user_id == PRIVATE_USER_ID


def test_a_corrupt_user_id_blob_fails_safely(tmp_path):
    path = tmp_path / "config.json"
    values = {"address": "AA:BB:CC:DD:EE:FF", "serial": "MR51123456789012",
              "user_id": "", "user_id_dpapi": "not-valid-base64-or-dpapi!!"}
    path.write_text(json.dumps(values), encoding="utf-8")
    with pytest.raises(ValueError, match="Invalid configuration file") as error:
        load_config(path)
    assert "DPAPI" not in str(error.value)


def test_without_dpapi_the_user_id_stays_in_its_plain_field(tmp_path, monkeypatch):
    # Every config needs a user ID, so a platform without DPAPI must still save
    # one: there the file is its owner's alone instead.
    from openpowerstation import config
    monkeypatch.setattr(config, "_DPAPI_AVAILABLE", False)
    path = tmp_path / "config.json"
    save_config(sample_config(user_id=PRIVATE_USER_ID), path)
    stored = json.loads(path.read_text("utf-8"))
    assert stored["user_id"] == PRIVATE_USER_ID and "user_id_dpapi" not in stored
    assert load_config(path).user_id == PRIVATE_USER_ID


def test_without_dpapi_a_broker_password_still_saves(tmp_path, monkeypatch):
    # Where DPAPI does not exist, protect() raises; that used to fail the whole
    # save. The password is worth less than the user ID, so it gets the same
    # owner-only file rather than no way to set up the bridge at all.
    from openpowerstation import config

    def no_dpapi(data):
        raise OSError("DPAPI is only available on Windows")
    monkeypatch.setattr(config, "_DPAPI_AVAILABLE", False)
    monkeypatch.setattr(dpapi, "protect", no_dpapi)
    path = tmp_path / "config.json"
    save_config(sample_config(mqtt_password="PRIVATE_BROKER_PASSWORD"), path)
    stored = json.loads(path.read_text("utf-8"))
    assert stored["mqtt_password"] == "PRIVATE_BROKER_PASSWORD"
    assert "mqtt_password_dpapi" not in stored
    assert load_config(path).mqtt_password == "PRIVATE_BROKER_PASSWORD"
