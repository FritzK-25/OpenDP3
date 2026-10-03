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
