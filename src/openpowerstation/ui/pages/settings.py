"""Settings: local Bluetooth setup and viewer appearance.

The setup form lives here as a widget so the Settings page and the first-run modal
dialog share one implementation, one validation path, and one set of warnings.
"""
import asyncio

from PySide6.QtWidgets import (
    QCheckBox, QComboBox, QDoubleSpinBox, QFormLayout, QHBoxLayout, QLineEdit,
    QPushButton, QSpinBox, QVBoxLayout, QWidget,
)

from ...config import Config, load_config, save_config, resolve_user_id
from ..widgets import Card, label

SECURITY = ("Security: BLE device identity is not cryptographically verified. Keep the "
            "data folder private to your Windows account; shared folders can expose or "
            "let others replace your configuration. Passwords are used in memory during "
            "login.")
INTRO = ("Choose your DP3, then provide its owner's EcoFlow user ID. Only the optional "
         "login button contacts EcoFlow. Nothing changes on the battery.")
IDLE_STATUS = "No passwords or account IDs should be pasted into chat."
BRIDGE = ("Publishes the current DP3 and Jackery readings to a Home Assistant MQTT broker on "
          "your network, which creates the sensors and optional controls for you. The "
          "connection is outbound only: OpenPowerstation never listens for one. Recording continues "
          "normally if the broker is unreachable. Leave the address empty to publish nothing.")
BRIDGE_SECURITY = ("The broker password is stored in config.json beside your recordings, "
                   "encrypted with Windows DPAPI to this account -- another account on this "
                   "PC can't read it back out. Still use a Home Assistant user created only "
                   "for this, so it grants nothing beyond the broker.")
CONTROL = ("Lets Home Assistant control the DP3 HV/LV AC outputs and the Jackery AC/DC "
           "outputs, Battery Saving Mode, and screen timeout. Everything else stays blocked: "
           "power-off, factory reset, Wi-Fi, firmware and arbitrary battery-boundary writes "
           "are never sent. Leave this off and OpenPowerstation stays read-only.")
CONTROL_CAUTION = ("Anything with access to your broker can change these device settings. "
                   "OpenPowerstation publishes controls from fresh device feedback rather than assuming "
                   "a write succeeded; a control is unavailable when its readback is stale or "
                   "missing. Do not enable this if an output or setting change could interrupt "
                   "something important.")
MQTT_FIELDS = ("mqtt_host", "mqtt_port", "mqtt_username", "mqtt_password", "mqtt_tls",
               "mqtt_interval", "allow_control")


class SetupForm(QWidget):
    """The local-setup form. ``host`` supplies the worker-job plumbing."""

    def __init__(self, root, host, parent=None):
        super().__init__(parent)
        self.root = root
        self.host = host
        # Full-width text inputs are hard to scan; keep the form to a readable column.
        self.setMaximumWidth(680)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(10)
        layout.addWidget(label(INTRO, "muted", wrap=True))
        layout.addWidget(label(SECURITY, "faint", wrap=True))
        self.scan_button = QPushButton("Scan nearby DP3 devices")
        self.scan_button.clicked.connect(self.scan)
        layout.addWidget(self.scan_button)
        form = QFormLayout()
        form.setSpacing(9)
        self.devices = QComboBox()
        self.devices.setMinimumWidth(340)
        self.devices.setAccessibleName("Device")
        form.addRow("Device", self.devices)
        self.user_id = QLineEdit()
        self.user_id.setEchoMode(QLineEdit.EchoMode.Password)
        self.user_id.setAccessibleName("User ID")
        form.addRow("User ID (stored locally)", self.user_id)
        self.region = QComboBox()
        self.region.addItems(["US", "EU", "JP", "ASIA"])
        form.addRow("Account region", self.region)
        self.email = QLineEdit()
        self.password = QLineEdit()
        self.password.setEchoMode(QLineEdit.EchoMode.Password)
        form.addRow("Email (optional login)", self.email)
        form.addRow("Password (never saved)", self.password)
        self.login = QPushButton("Retrieve user ID once via EcoFlow")
        self.login.clicked.connect(self.retrieve)
        form.addRow("", self.login)
        self.firmware = QLineEdit()
        form.addRow("Firmware version (private)", self.firmware)
        self.conditions = QLineEdit()
        form.addRow("Operating conditions (private)", self.conditions)
        self.jump = QDoubleSpinBox()
        self.jump.setRange(.1, 100)
        self.jump.setValue(10)
        self.jump.setSuffix(" °C")
        self.window_seconds = QDoubleSpinBox()
        self.window_seconds.setRange(.1, 60)
        self.window_seconds.setValue(5)
        self.window_seconds.setSuffix(" seconds")
        form.addRow("Suspect temperature change", self.jump)
        form.addRow("Within", self.window_seconds)
        layout.addLayout(form)
        self.status = label(IDLE_STATUS, "muted", wrap=True)
        layout.addWidget(self.status)
        self.load()

    def load(self):
        try:
            config = load_config(self.root / "config.json")
        except (FileNotFoundError, ValueError, TypeError):
            return
        self.devices.addItem(f"{config.serial} · {config.address}", (config.address, config.serial))
        self.user_id.setText(config.user_id)
        self.region.setCurrentText(config.region)
        self.firmware.setText(config.firmware)
        self.conditions.setText(config.conditions)
        self.jump.setValue(config.temperature_jump)
        self.window_seconds.setValue(config.temperature_window)

    def scan(self):
        from ...ble import scan
        self.status.setText("Scanning for eight seconds. Close the phone's Bluetooth session if needed.")

        def done(found):
            self.devices.clear()
            for identity, _ in found:
                self.devices.addItem(f"{identity.serial} · {identity.address} · {identity.rssi} dBm",
                                     (identity.address, identity.serial))
            self.status.setText(f"Found {len(found)} DP3 device(s)." if found else
                                "No DP3 advertisements. Check Bluetooth, range, and the phone connection.")
        self.host.job(lambda: asyncio.run(scan()), done)

    def retrieve(self):
        email, password = self.email.text().strip(), self.password.text()
        region = self.region.currentText()
        if not email or not password:
            return self.host.show_error("Enter an email and password, or enter your user ID directly.")
        self.password.clear()
        self.login.setEnabled(False)
        self.status.setText("Setup-only HTTPS login. Password and token will not be saved.")

        def done(user_id):
            self.user_id.setText(user_id)
            self.email.clear()
            self.login.setEnabled(True)
            self.status.setText("User ID retrieved. Recording will work without cloud access.")

        def error(message):
            self.login.setEnabled(True)
            self.status.setText(message)
        self.host.job(lambda: asyncio.run(resolve_user_id(email, password, region)), done, error)

    def save(self) -> bool:
        """Validate and persist. Returns False and reports why on any rejection."""
        if getattr(self.host, "jobs", None):
            self.host.show_error("Wait for setup activity to finish.")
            return False
        device = self.devices.currentData()
        if not device:
            self.host.show_error("Scan and select your DP3 first.")
            self.status.setText("Scan and select your DP3 first.")
            return False
        path = self.root / "config.json"
        config = Config(*device, self.user_id.text().strip(), self.region.currentText(),
                        self.firmware.text(), self.conditions.text(),
                        self.jump.value(), self.window_seconds.value())
        # Bridge settings have their own card; carry them through untouched.
        try:
            saved = load_config(path)
        except (FileNotFoundError, ValueError, TypeError):
            saved = None
        for name in MQTT_FIELDS if saved else ():
            setattr(config, name, getattr(saved, name))
        try:
            save_config(config, path)
        except (ValueError, OSError) as exc:
            self.host.show_error(str(exc))
            self.status.setText(str(exc))
            return False
        self.status.setText("Setup saved locally. Nothing was sent to the battery.")
        return True

    def clear_password(self):
        self.password.clear()


class BridgeForm(QWidget):
    """Home Assistant broker settings. Saved into the same config.json, alone."""

    def __init__(self, root, host, parent=None):
        super().__init__(parent)
        self.root = root
        self.host = host
        self.setMaximumWidth(680)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(10)
        layout.addWidget(label(BRIDGE, "muted", wrap=True))
        layout.addWidget(label(BRIDGE_SECURITY, "faint", wrap=True))
        form = QFormLayout()
        form.setSpacing(9)
        self.broker = QLineEdit()
        self.broker.setMinimumWidth(340)
        self.broker.setPlaceholderText("homeassistant.local")
        self.broker.setAccessibleName("Broker address")
        form.addRow("Broker address", self.broker)
        self.port = QSpinBox()
        self.port.setRange(1, 65535)
        self.port.setValue(1883)
        form.addRow("Port", self.port)
        self.username = QLineEdit()
        form.addRow("Broker username", self.username)
        self.password = QLineEdit()
        self.password.setEchoMode(QLineEdit.EchoMode.Password)
        self.password.setAccessibleName("Broker password")
        form.addRow("Broker password", self.password)
        self.tls = QCheckBox("Connect with TLS")
        form.addRow("", self.tls)
        self.interval = QDoubleSpinBox()
        self.interval.setRange(1, 3600)
        self.interval.setValue(5)
        self.interval.setSuffix(" seconds")
        form.addRow("Publish every", self.interval)
        layout.addLayout(form)
        layout.addWidget(label(CONTROL, "muted", wrap=True))
        layout.addWidget(label(CONTROL_CAUTION, "faint", wrap=True))
        self.control = QCheckBox("Allow control")
        self.control.setAccessibleName("Allow Home Assistant to control the DP3 and Jackery")
        layout.addWidget(self.control)
        self.status = label("Saving stores the broker only. Start publishing by running "
                            "OpenPowerstation-Bridge.cmd from the source installation.", "muted", wrap=True)
        layout.addWidget(self.status)
        self.load()

    def load(self):
        try:
            config = load_config(self.root / "config.json")
        except (FileNotFoundError, ValueError, TypeError):
            return
        self.broker.setText(config.mqtt_host)
        self.port.setValue(config.mqtt_port)
        self.username.setText(config.mqtt_username)
        self.password.setText(config.mqtt_password)
        self.tls.setChecked(config.mqtt_tls)
        self.interval.setValue(config.mqtt_interval)
        self.control.setChecked(config.allow_control)

    def save(self) -> bool:
        """Update only the broker fields, so Bluetooth setup is never disturbed."""
        path = self.root / "config.json"
        try:
            config = load_config(path)
        except (FileNotFoundError, ValueError, TypeError):
            self.status.setText("Complete the Bluetooth setup above before configuring the bridge.")
            return False
        config.mqtt_host = self.broker.text().strip()
        config.mqtt_port = self.port.value()
        config.mqtt_username = self.username.text().strip()
        config.mqtt_password = self.password.text()
        config.mqtt_tls = self.tls.isChecked()
        config.mqtt_interval = self.interval.value()
        config.allow_control = self.control.isChecked()
        try:
            save_config(config, path)
        except (ValueError, OSError) as exc:
            # Messages from validate() never quote a value; safe to show as-is.
            self.host.show_error(str(exc))
            self.status.setText(str(exc))
            return False
        self.status.setText("Broker saved locally." if config.mqtt_host else
                            "Broker cleared. Nothing will be published.")
        return True


class SettingsPage(QWidget):
    KEY = "settings"
    TITLE = "Settings"
    SUBTITLE = "Local Bluetooth setup and viewer appearance"

    def __init__(self, window, parent=None):
        super().__init__(parent)
        self.window = window
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(14)
        setup_card = Card("Local Bluetooth setup")
        self.form = SetupForm(window.root, window, self)
        setup_card.body.addWidget(self.form)
        buttons = QHBoxLayout()
        self.save_button = QPushButton("Save setup")
        self.save_button.setObjectName("primary")
        self.save_button.clicked.connect(self.save)
        buttons.addWidget(self.save_button)
        buttons.addStretch()
        setup_card.body.addLayout(buttons)
        layout.addWidget(setup_card)

        bridge_card = Card("Home Assistant bridge")
        self.bridge = BridgeForm(window.root, window, self)
        bridge_card.body.addWidget(self.bridge)
        bridge_buttons = QHBoxLayout()
        self.bridge_button = QPushButton("Save broker")
        self.bridge_button.clicked.connect(self.bridge.save)
        bridge_buttons.addWidget(self.bridge_button)
        bridge_buttons.addStretch()
        bridge_card.body.addLayout(bridge_buttons)
        layout.addWidget(bridge_card)

        appearance = Card("Appearance")
        row = QHBoxLayout()
        row.setSpacing(8)
        self.theme_buttons = {}
        for key, text in (("light", "Light"), ("dark", "Dark")):
            button = QPushButton(text)
            button.setCheckable(True)
            button.setAutoExclusive(True)
            button.clicked.connect(lambda _, name=key: self.window.set_theme(name))
            self.theme_buttons[key] = button
            row.addWidget(button)
        row.addStretch()
        appearance.body.addLayout(row)
        appearance.body.addWidget(label(
            "The theme is stored in ui.json beside your recordings. It is a viewer "
            "preference only and is never part of an evidence export.", "faint", wrap=True))
        layout.addWidget(appearance)
        layout.addStretch()

    def save(self):
        if self.window.recording():
            return self.window.show_error("Stop recording before editing connection setup.")
        if self.form.save():
            self.window.reload_device_summary()

    def apply_theme(self, theme):
        for key, button in self.theme_buttons.items():
            button.setChecked(key == theme)

    def update_snapshot(self, snap, now_t):
        self.save_button.setEnabled(not self.window.recording())
