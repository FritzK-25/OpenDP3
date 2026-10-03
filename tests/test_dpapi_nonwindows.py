"""Non-Windows regression coverage for the DPAPI contract."""
import base64
import json
import os

import pytest

from opendp3 import dpapi
from opendp3.config import load_config


pytestmark = pytest.mark.skipif(os.name == "nt", reason="non-Windows DPAPI contract")


@pytest.mark.parametrize("operation", [dpapi.protect, dpapi.unprotect])
def test_dpapi_primitives_raise_oserror_off_windows(operation):
    with pytest.raises(OSError, match="DPAPI is only available on Windows"):
        operation(b"x")


def test_load_config_sanitizes_dpapi_failure_off_windows(tmp_path):
    path = tmp_path / "config.json"
    values = {
        "address": "AA:BB:CC:DD:EE:FF",
        "serial": "MR51123456789012",
        "user_id": "123456",
        "mqtt_password_dpapi": base64.b64encode(b"not-a-windows-dpapi-blob").decode("ascii"),
    }
    path.write_text(json.dumps(values), encoding="utf-8")

    with pytest.raises(ValueError) as error:
        load_config(path)

    assert str(error.value) == "Invalid configuration file. Run local setup again."


def _config():
    from opendp3.config import Config
    return Config("AA:BB:CC:DD:EE:FF", "MR51123456789012", "123456",
                  mqtt_host="broker", mqtt_password="secret")


def test_save_config_keeps_the_password_owner_only_off_windows(tmp_path):
    from opendp3.config import save_config
    path = tmp_path / "config.json"

    save_config(_config(), path)

    stored = json.loads(path.read_text(encoding="utf-8"))
    assert stored["mqtt_password"] == "secret"
    assert "mqtt_password_dpapi" not in stored
    assert path.stat().st_mode & 0o777 == 0o600
    assert load_config(path).mqtt_password == "secret"


def test_legacy_plaintext_config_can_be_saved_again_off_windows(tmp_path):
    from opendp3.config import save_config
    path = tmp_path / "config.json"
    path.write_text(json.dumps({"address": "AA:BB:CC:DD:EE:FF", "serial": "MR51123456789012",
                                "user_id": "123456", "mqtt_host": "broker",
                                "mqtt_password": "secret"}), encoding="utf-8")
    config = load_config(path)
    config.firmware = "1.0"

    save_config(config, path)

    assert load_config(path).firmware == "1.0"
    assert load_config(path).mqtt_password == "secret"
