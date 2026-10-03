"""Local configuration. No credentials are accepted on a command line."""
from dataclasses import asdict, dataclass
from pathlib import Path
import base64
import json
import math
import os
import re

from platformdirs import user_data_path

from . import dpapi

# On-disk key for the DPAPI-protected form of mqtt_password. Kept out of the
# Config dataclass itself so every in-memory reader (bridge.py, jackery_bridge.py,
# the settings UI) keeps seeing a plain string; only load_config/save_config
# know this key exists. See docs/SECURITY.md, "Credentials, memory and
# Windows folders".
_MQTT_PASSWORD_DPAPI_KEY = "mqtt_password_dpapi"

# The platform data directory was named after the project's old name. An
# install that already has one keeps using it, so its setup, recordings and
# DPAPI-protected password stay where they are; only a fresh install gets the
# new name. Nothing is moved, because a running recorder may hold the database.
DATA_DIR_NAME = "OpenPowerstation"
LEGACY_DATA_DIR_NAME = "OpenDP3"

def data_dir() -> Path:
    current = user_data_path(DATA_DIR_NAME, appauthor=False)
    legacy = user_data_path(LEGACY_DATA_DIR_NAME, appauthor=False)
    if not current.exists() and legacy.is_dir():
        return legacy
    return current

@dataclass
class Config:
    address: str
    serial: str
    user_id: str
    region: str = "US"
    firmware: str = ""
    conditions: str = ""
    temperature_jump: float = 10.0
    temperature_window: float = 5.0
    # Home Assistant bridge. An empty host disables the bridge entirely; these are
    # defaulted so a config.json written before the bridge existed still loads.
    mqtt_host: str = ""
    mqtt_port: int = 1883
    mqtt_username: str = ""
    mqtt_password: str = ""
    mqtt_tls: bool = False
    mqtt_interval: float = 5.0
    # Device control. Off unless the operator turns it on in Settings. While this
    # is false the outbound gate rejects every control packet, so the recorder
    # keeps the read-only behaviour every earlier release guaranteed.
    allow_control: bool = False

    def validate(self):
        if any(not isinstance(getattr(self, name), str) for name in
               ("address", "serial", "user_id", "region", "firmware", "conditions",
                "mqtt_host", "mqtt_username", "mqtt_password")):
            raise ValueError("Invalid configuration field type. Run local setup again.")
        if not re.fullmatch(r"[0-9A-Fa-f]{2}(?::[0-9A-Fa-f]{2}){5}", self.address):
            raise ValueError("Select a Bluetooth device address from scan.")
        if not re.fullmatch(r"MR(?:51|54)[A-Za-z0-9]{12}", self.serial):
            raise ValueError("Only DELTA Pro 3 serial prefixes MR51/MR54 are supported.")
        if not self.user_id.isascii() or not self.user_id.isdecimal():
            raise ValueError("User ID must contain only ASCII digits.")
        if self.region not in {"US", "EU", "JP", "ASIA"}:
            raise ValueError("Unsupported login region.")
        if any(type(value) not in (int, float) or (isinstance(value, float) and not math.isfinite(value))
               for value in (self.temperature_jump, self.temperature_window)):
            raise ValueError("Temperature anomaly settings must be finite numbers.")
        if not 0 < self.temperature_jump <= 100 or not 0 < self.temperature_window <= 60:
            raise ValueError("Invalid temperature anomaly settings.")
        # Bridge settings. No message may quote a value: the broker password is here.
        if type(self.mqtt_port) is not int or not 1 <= self.mqtt_port <= 65535:
            raise ValueError("MQTT port must be between 1 and 65535.")
        if type(self.mqtt_tls) is not bool:
            raise ValueError("MQTT TLS setting must be true or false.")
        if type(self.allow_control) is not bool:
            raise ValueError("Control setting must be true or false.")
        if (type(self.mqtt_interval) not in (int, float) or not math.isfinite(self.mqtt_interval)
                or not 1 <= self.mqtt_interval <= 3600):
            raise ValueError("MQTT publish interval must be between 1 and 3600 seconds.")
        if self.mqtt_host and not re.fullmatch(r"[A-Za-z0-9._:\-\[\]]{1,253}", self.mqtt_host):
            raise ValueError("Enter a plain broker hostname or IP address.")

def load_config(path: Path | None = None) -> Config:
    try:
        values = json.loads((path or data_dir() / "config.json").read_text("utf-8"))
        if not isinstance(values, dict):
            raise ValueError("Expected a configuration object.")
        # DPAPI-protected broker password, written by save_config below. Absent
        # on a config.json from before this existed, or if no password was ever
        # set -- both leave mqtt_password to load from the plain field as before.
        encrypted = values.pop(_MQTT_PASSWORD_DPAPI_KEY, None)
        if encrypted is not None:
            if not isinstance(encrypted, str):
                raise ValueError("Invalid stored broker password.")
            values["mqtt_password"] = dpapi.unprotect(base64.b64decode(encrypted)).decode("utf-8")
        c = Config(**values)
    except (ValueError, TypeError, RecursionError, OSError):
        # Never include JSON fragments, unexpected keys, account values or why
        # decryption failed (wrong Windows user, wrong machine, corrupt blob).
        raise ValueError("Invalid configuration file. Run local setup again.") from None
    c.validate()
    return c

def save_config(config: Config, path: Path | None = None):
    config.validate()
    path = path or data_dir() / "config.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    values = asdict(config)
    # On Windows, store the broker password DPAPI-protected, scoped to this
    # Windows user, rather than in the plain mqtt_password field. DPAPI does not
    # exist elsewhere; there the plain field stays, in a file only this user can
    # read. See docs/SECURITY.md.
    if values["mqtt_password"] and os.name == "nt":
        values[_MQTT_PASSWORD_DPAPI_KEY] = base64.b64encode(
            dpapi.protect(values["mqtt_password"].encode("utf-8"))
        ).decode("ascii")
        values["mqtt_password"] = ""
    tmp = path.with_suffix(".tmp")
    # Owner-only from creation, so a plain-text password is never briefly
    # readable by other users before the rename. Windows ignores the mode.
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    if os.name != "nt":
        # The mode above applies only to a new file. A temp file left by an
        # interrupted save keeps its old mode, so tighten it before writing.
        os.fchmod(fd, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        json.dump(values, f, indent=2)
        f.flush()
        os.fsync(f.fileno())
    os.replace(tmp, path)
    if os.name != "nt":
        path.chmod(0o600)


def control_allowed(path: Path) -> bool:
    """Read policy at execution, failing closed if setup is missing or invalid."""
    try:
        return load_config(path).allow_control
    except (OSError, ValueError):
        return False

async def resolve_user_id(identifier: str, password: str, region: str) -> str:
    """Explicit setup only. Discard the entire response except the account ID."""
    import httpx
    hosts = {"US": "api.ecoflow.com", "EU": "api-e.ecoflow.com",
             "JP": "api-j.ecoflow.com", "ASIA": "api-a.ecoflow.com"}
    if region not in hosts:
        raise ValueError("Select an account region before signing in.")
    body = {"scene": "IOT_APP", "appVersion": "1.0.0",
            "password": base64.b64encode(password.encode()).decode(),
            "oauth": {"bundleId": "com.ef.EcoFlow"}, "userType": "ECOFLOW",
            "email": identifier}
    try:
        async with httpx.AsyncClient(timeout=20, follow_redirects=False, trust_env=False) as client:
            response = await client.post("https://" + hosts[region] + "/auth/login", json=body)
            if response.status_code != 200:
                raise ValueError("EcoFlow login failed. Check credentials and account region.")
            try:
                result = response.json()
            except (ValueError, RecursionError):
                raise ValueError("EcoFlow returned an unreadable login response. Use manual user ID if necessary.") from None
            if not isinstance(result, dict):
                raise ValueError("EcoFlow returned an unsupported login response. Use manual user ID if necessary.")
            if str(result.get("code")) != "0":
                raise ValueError("EcoFlow did not accept the login. Use manual user ID if necessary.")
            raw_user_id = result["data"]["user"]["userId"]
            if type(raw_user_id) not in (str, int):
                raise ValueError("EcoFlow returned an unsupported user ID.")
            user_id = str(raw_user_id)
            if not user_id.isascii() or not user_id.isdecimal():
                raise ValueError("EcoFlow returned an unsupported user ID.")
            return user_id
    except (httpx.HTTPError, KeyError, TypeError):
        raise ValueError("Unable to obtain a user ID. Check connectivity or enter it manually.") from None
    finally:
        # Drop this request body's references promptly. Python cannot reliably zero
        # immutable strings. OpenPowerstation does not intentionally save EcoFlow credentials
        # in its configuration, recordings, or exports.
        body.clear()
