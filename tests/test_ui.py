"""Unit tests for the desktop presentation layer.

The theme and preference tests are pure functions and need no QApplication. The
rest drive a real Window over the synthetic demonstration database, because the
widgets under test only fill in once a snapshot has been rendered into them.
"""
import json
import os
import re
from pathlib import Path

import pytest

from openpowerstation import DECODER_VERSION, __version__
from openpowerstation.config import load_config
from openpowerstation.exporting import export_evidence
from openpowerstation.gui import Window
from openpowerstation.ui import prefs, theme
from openpowerstation.ui.pages.exports import list_bundles
from openpowerstation.ui.widgets import NO_VALUE, Pill, StatCard

from conftest import close_window, pump

UI_ROOT = Path(__file__).resolve().parents[1] / "src" / "openpowerstation" / "ui"
HEX = re.compile(r"#[0-9a-fA-F]{3,8}\b")
DEMO_FRAMES = 882


# --- helpers --------------------------------------------------------------

def luminance(colour):
    value = colour.lstrip("#")
    channels = [int(value[i:i + 2], 16) / 255 for i in (0, 2, 4)]
    channels = [c / 12.92 if c <= .03928 else ((c + .055) / 1.055) ** 2.4 for c in channels]
    return .2126 * channels[0] + .7152 * channels[1] + .0722 * channels[2]


def contrast(foreground, background):
    high, low = sorted((luminance(foreground), luminance(background)), reverse=True)
    return (high + .05) / (low + .05)


@pytest.fixture
def demo(qtbot, tmp_path, demo_database):
    """A window showing the synthetic demonstration, fully rendered."""
    database = demo_database(tmp_path / "demo.sqlite", seconds=900)
    window = Window(tmp_path, database)
    qtbot.addWidget(window)
    window.resize(1500, 1000)
    window.show()
    assert pump(lambda: window.snap.get("count") == DEMO_FRAMES, timeout=12)
    yield window
    close_window(window)


class FakeService:
    """Stands in for a live recorder while exercising button enablement."""

    def __init__(self, state, error=None):
        self.state = state
        self.error = error

    def is_alive(self):
        return True


# --- theme ----------------------------------------------------------------

def test_theme_token_sets_match():
    assert theme.LIGHT.keys() == theme.DARK.keys()
    for name in theme.THEMES:
        # substitute() raises KeyError on a token missing from this theme.
        assert theme.stylesheet(name).strip()
    assert len(theme.SERIES["light"]) == len(theme.SERIES["dark"])
    for palette in theme.SERIES.values():
        assert all(re.fullmatch(r"#[0-9a-f]{6}", colour) for colour in palette)
    assert theme.tokens("no-such-theme") is theme.LIGHT


def test_theme_contrast_meets_aa():
    """Body text and status colours must stay readable in both themes."""
    for name, palette in theme.THEMES.items():
        for foreground, background in [("text", "surface"), ("muted", "surface"),
                                       ("text", "bg"), ("muted", "bg"),
                                       ("accent_text", "accent"), ("danger", "surface"),
                                       ("warn", "surface"), ("ok", "surface"),
                                       ("info", "surface"), ("muted", "elevated")]:
            ratio = contrast(palette[foreground], palette[background])
            assert ratio >= 4.5, f"{name}: {foreground} on {background} is {ratio:.2f}:1"
        # Incidental text is held to the large-text tier only.
        assert contrast(palette["faint"], palette["surface"]) >= 3.0


def test_no_hardcoded_colours_outside_theme():
    """Only theme.py may name a colour, so the second theme cannot rot."""
    offenders = {}
    for path in UI_ROOT.rglob("*.py"):
        if path.name == "theme.py":
            continue
        found = HEX.findall(path.read_text("utf-8"))
        if found:
            offenders[str(path.relative_to(UI_ROOT))] = found
    assert not offenders, f"Colour literals outside theme.py: {offenders}"


# --- preferences ----------------------------------------------------------

def test_theme_choice_persists(qtbot, tmp_path):
    window = Window(tmp_path)
    qtbot.addWidget(window)
    window.timer.stop()
    assert window.theme == "light"
    light_sheet = window.styleSheet()
    light_plot = window.pages["overview"].panels["temperature"].plot.backgroundBrush().color().name()

    window.toggle_theme()
    assert window.theme == "dark"
    assert window.styleSheet() != light_sheet
    dark_plot = window.pages["overview"].panels["temperature"].plot.backgroundBrush().color().name()
    assert dark_plot != light_plot
    assert json.loads((tmp_path / "ui.json").read_text("utf-8"))["theme"] == "dark"
    close_window(window)

    reopened = Window(tmp_path)
    qtbot.addWidget(reopened)
    reopened.timer.stop()
    assert reopened.theme == "dark"
    close_window(reopened)


def test_navigation_choice_persists(qtbot, tmp_path):
    window = Window(tmp_path)
    qtbot.addWidget(window)
    window.timer.stop()
    window.navigate("incidents")
    assert json.loads((tmp_path / "ui.json").read_text("utf-8"))["page"] == "incidents"
    close_window(window)

    reopened = Window(tmp_path)
    qtbot.addWidget(reopened)
    reopened.timer.stop()
    assert reopened.page == "incidents"
    close_window(reopened)


@pytest.mark.parametrize("content", ["not json", '{"theme": 7}', "[]", '{"theme": "neon"}',
                                     '{"page": "../escape"}'])
def test_corrupt_preferences_fall_back(qtbot, tmp_path, content):
    """A damaged ui.json must never stop the recorder from opening."""
    (tmp_path / "ui.json").write_text(content, encoding="utf-8")
    assert prefs.load_prefs(tmp_path)["theme"] == "light"
    window = Window(tmp_path)
    qtbot.addWidget(window)
    window.timer.stop()
    assert window.theme == "light"
    assert window.page == "overview"
    close_window(window)


def test_unwritable_preferences_are_not_fatal(tmp_path):
    """save_prefs swallows I/O errors; a theme click is not evidence."""
    (tmp_path / "ui.json").mkdir()  # A directory where the file should be.
    prefs.save_prefs(tmp_path, {"theme": "dark", "page": "overview"})


# --- widgets in isolation -------------------------------------------------

def test_stat_card_formatting(qtbot):
    card = StatCard("bms_max_cell_temp", "BMS MAX CELL", "temperature", "danger")
    qtbot.addWidget(card)

    card.set_reading(23.04, "°C", last_t=10.0, now_t=12.5)
    assert card.value.text() == "23.0 °C"
    assert card.age.text() == "Last observed 2.5s ago"

    card.set_reading(901.3, "W", last_t=10.0, now_t=10.0)
    assert card.value.text() == "901 W"

    card.set_reading(65.99, "%", last_t=10.0, now_t=10.0)
    assert card.value.text() == "66.0 %"

    card.set_reading(None, "°C", last_t=None, now_t=5.0)
    assert card.value.text() == NO_VALUE
    assert card.age.text() == "Not observed"

    # A receipt time briefly ahead of the snapshot clock must not read as the future.
    card.set_reading(1.0, "W", last_t=12.0, now_t=10.0)
    assert card.age.text() == "Last observed 0.0s ago"


def test_pill_tone_and_uppercase(qtbot):
    pill = Pill("read only")
    qtbot.addWidget(pill)
    assert pill.text() == "READ ONLY"
    pill.setText("Synthetic demo")
    assert pill.text() == "SYNTHETIC DEMO"
    pill.set_tone("danger")
    assert pill.property("tone") == "danger"


def test_header_pill_reflects_service_state(qtbot, tmp_path):
    window = Window(tmp_path)
    qtbot.addWidget(window)
    window.timer.stop()
    for state, tone in [("scanning", "warn"), ("recording", "ok"),
                        ("released", "warn"), ("error", "danger"), ("stopped", "neutral")]:
        window.on_service({"state": state, "detail": f"detail for {state}", "session_id": None})
        assert window.badge.text() == state.upper()
        assert window.badge.property("tone") == tone
        assert window.banner.text() == f"detail for {state}"
    close_window(window)


def test_pill_reserves_room_for_its_text(qtbot, tmp_path):
    """Qt omits stylesheet padding from a QLabel's hint, so the pill must ask."""
    window = Window(tmp_path)
    qtbot.addWidget(window)
    window.timer.stop()
    window.show()
    for text in ("READ ONLY", "SYNTHETIC DEMO", "RECORDING STOPPED - ERROR"):
        window.set_badge(text, "warn")
        pump(lambda: False, timeout=.05)
        needed = window.badge.fontMetrics().horizontalAdvance(window.badge.text())
        assert window.badge.width() >= needed + 20, f"{text} is clipped"
    close_window(window)


# --- navigation and pages -------------------------------------------------

def test_navigation_switches_pages(demo):
    for key, page in demo.pages.items():
        demo.navigate(key)
        assert demo.page == key
        assert demo.title.text() == page.TITLE
        assert demo.subtitle.text() == page.SUBTITLE
        checked = [name for name, button in demo.sidebar.buttons.items() if button.isChecked()]
        assert checked == [key]
    # Every sidebar entry must reach a real page.
    assert set(demo.sidebar.buttons) == set(demo.pages)


def test_overview_renders_snapshot(demo):
    overview = demo.pages["overview"]
    assert demo.badge.text() == "SYNTHETIC DEMO"
    assert "SYNTHETIC DATA" in demo.banner.text()
    assert overview.cards["bms_max_cell_temp"].value.text().startswith("24")
    assert overview.cards["bms_batt_soc"].value.text().endswith("%")
    # One curve per observed field, in the panel its group belongs to.
    assert set(overview.curves) == set(demo.snap["series"])
    assert overview.panels["temperature"].legend.text()
    assert "BMS max" in overview.panels["temperature"].legend.text()
    # The synthetic anomaly is marked on every panel.
    assert all(overview.markers[group] for group in overview.panels)
    assert overview.activity_table.rowCount() > 0
    assert "1 protected incident" in overview.incident_summary.text()
    assert f"{DEMO_FRAMES:,} frames" in demo.footer.text()
    assert "Hardware qualification pending" in demo.footer.text()


def test_live_page_populated(demo):
    demo.navigate("live")
    live = demo.pages["live"]
    assert live.coverage_table.rowCount() > 25
    assert live.coverage_table.horizontalHeaderItem(2).text() == "Age / cadence"
    decoded = json.loads(live.raw_text.toPlainText())
    assert decoded["bms_max_cell_temp"] == 24
    notes = live.notes.toPlainText()
    assert "Firmware (private):" in notes and DECODER_VERSION in notes


def test_incidents_page_actions(demo):
    demo.navigate("incidents")
    incidents = demo.pages["incidents"]
    assert incidents.incident_table.rowCount() == 1
    assert incidents.events_table.horizontalHeaderItem(2).text() == "Detail (private)"
    assert incidents.selected() is None
    assert not incidents.delete_button.isEnabled()

    incidents.incident_table.selectRow(0)
    assert incidents.selected() == 1
    assert incidents.delete_button.isEnabled()
    # Selecting an incident parks the cursor at the end of its window.
    end_t = demo.snap["incidents"][0]["end_t"]
    assert not demo.follow
    assert demo.cursor <= end_t


def test_exports_page_lists_bundles(qtbot, tmp_path, demo_database):
    database = demo_database(tmp_path / "demo.sqlite", seconds=300)
    exports = tmp_path / "exports"
    export_evidence(database, exports / "OpenDP3-20260830-120000-000001")
    export_evidence(database, exports / "OpenDP3-20260830-133000-000002")

    bundles = list_bundles(tmp_path)
    assert [b["name"] for b in bundles] == ["OpenDP3-20260830-133000-000002",
                                            "OpenDP3-20260830-120000-000001"]

    window = Window(tmp_path, database)
    qtbot.addWidget(window)
    window.show()
    assert pump(lambda: bool(window.snap), timeout=12)
    window.navigate("exports")
    page = window.pages["exports"]
    assert page.bundle_table.rowCount() == 2
    assert page.bundle_table.item(0, 0).text() == "2026-08-30 13:30:00"
    assert page.bundle_table.item(0, 1).text() == "SYNTHETIC DEMO"
    assert page.empty.isHidden()
    assert not page.report_button.isEnabled()
    page.bundle_table.selectRow(0)
    assert page.report_button.isEnabled()
    close_window(window)


def test_exports_page_empty_state(qtbot, tmp_path):
    assert list_bundles(tmp_path) == []
    window = Window(tmp_path)
    qtbot.addWidget(window)
    window.show()
    window.navigate("exports")
    page = window.pages["exports"]
    assert page.bundle_table.rowCount() == 0
    assert page.bundle_table.isHidden()
    assert "No evidence bundles" in page.empty.text()
    close_window(window)


def test_exports_page_ignores_unreadable_bundles(tmp_path):
    """A half-written or hand-edited folder is skipped, not fatal."""
    exports = tmp_path / "exports"
    (exports / "OpenDP3-20260830-120000-000001").mkdir(parents=True)  # no summary.json
    broken = exports / "OpenDP3-20260830-130000-000002"
    broken.mkdir()
    (broken / "summary.json").write_text("{not json", encoding="utf-8")
    listed = exports / "OpenDP3-20260830-140000-000003"
    listed.mkdir()
    (listed / "summary.json").write_text(json.dumps({"synthetic": True}), encoding="utf-8")
    assert [b["name"] for b in list_bundles(tmp_path)] == [listed.name]


def test_settings_page_validation(qtbot, tmp_path, monkeypatch):
    window = Window(tmp_path)
    qtbot.addWidget(window)
    window.timer.stop()
    errors = []
    monkeypatch.setattr(Window, "show_error", lambda self, text: errors.append(text))
    window.navigate("settings")
    form = window.pages["settings"].form

    # Nothing scanned yet: refuses without touching config.json.
    window.pages["settings"].save()
    assert "Scan and select your DP3 first." in errors
    assert not (tmp_path / "config.json").exists()

    form.devices.addItem("bad", ("AA:BB:CC:DD:EE:FF", "NOTADP3"))
    form.user_id.setText("12345")
    window.pages["settings"].save()
    assert "MR51/MR54" in errors[-1]
    assert not (tmp_path / "config.json").exists()

    form.devices.clear()
    form.devices.addItem("good", ("AA:BB:CC:DD:EE:FF", "MR51ABCDEFGH1234"))
    form.user_id.setText("not-a-number")
    window.pages["settings"].save()
    assert "ASCII digits" in errors[-1]
    assert not (tmp_path / "config.json").exists()

    form.user_id.setText("1234567890")
    window.pages["settings"].save()
    saved = json.loads((tmp_path / "config.json").read_text("utf-8"))
    assert saved["serial"] == "MR51ABCDEFGH1234"
    # Read back through load_config: on Windows the file holds it DPAPI-protected.
    assert load_config(tmp_path / "config.json").user_id == "1234567890"
    # The EcoFlow account password is never part of the saved configuration.
    assert "password" not in saved and saved["mqtt_password"] == ""
    assert window.sidebar.device_detail.text() == "Serial MR51ABCDEFGH1234"
    close_window(window)


def test_settings_theme_buttons_track_the_active_theme(qtbot, tmp_path):
    window = Window(tmp_path)
    qtbot.addWidget(window)
    window.timer.stop()
    window.navigate("settings")
    page = window.pages["settings"]
    assert page.theme_buttons["light"].isChecked()
    page.theme_buttons["dark"].click()
    assert window.theme == "dark"
    assert page.theme_buttons["dark"].isChecked()
    close_window(window)


def test_about_page_shows_versions(qtbot, tmp_path):
    window = Window(tmp_path)
    qtbot.addWidget(window)
    window.timer.stop()
    window.navigate("about")
    page = window.pages["about"]
    texts = [child.text() for child in page.findChildren(type(page.window.title))
             if hasattr(child, "text")]
    joined = " ".join(texts)
    assert __version__ in joined
    assert DECODER_VERSION in joined
    assert "not verified device measurement times" in joined
    assert page.choices.count() >= 1
    assert page.view.toPlainText()
    close_window(window)


# --- regression and visual ------------------------------------------------

def test_button_enable_matrix(demo):
    """The enablement rules must survive the buttons moving onto pages."""
    demo.timer.stop()
    overview = demo.pages["overview"]

    # Saved recording, no service: start and playback available, live controls not.
    assert overview.start_button.isEnabled()
    assert overview.play_button.isEnabled()
    assert not overview.release_button.isEnabled()
    assert not overview.resume_button.isEnabled()
    assert not overview.stop_button.isEnabled()
    assert overview.export_button.isEnabled()

    demo.service = FakeService("recording")
    demo.update_buttons()
    assert not overview.start_button.isEnabled()
    assert overview.stop_button.isEnabled()
    assert overview.release_button.isEnabled()
    assert not overview.resume_button.isEnabled()
    assert not overview.play_button.isEnabled()

    demo.service = FakeService("released")
    demo.update_buttons()
    assert not overview.release_button.isEnabled()
    assert overview.resume_button.isEnabled()

    demo.service = None
    demo.update_buttons()
    assert overview.start_button.isEnabled()


def test_background_recorder_locks_the_viewer(qtbot, tmp_path, packet):
    """A qualification run owned by another process may only be stopped, not started."""
    from conftest import add
    from openpowerstation.recorder import Recorder
    from openpowerstation.storage import Store
    path = tmp_path / "live.sqlite"
    with Store(path, reserve_bytes=0) as store:
        recorder = Recorder(store)
        add(recorder, packet(bms_max_cell_temp=23), 0)
        (tmp_path / "qualification-run.json").write_text(
            json.dumps({"session_id": recorder.sid}), encoding="utf-8")
        window = Window(tmp_path, path)
        qtbot.addWidget(window)
        window.show()
        assert pump(lambda: window.snap.get("count") == 1, timeout=8)
        assert window.badge.text() == "BACKGROUND RECORDER"
        assert window.badge.property("tone") == "accent"
        assert not window.start_button.isEnabled()
        assert not window.play_button.isEnabled()
        assert not window.mark_button.isEnabled()
        assert window.stop_button.text() == "Stop qualification"
        assert window.stop_button.isEnabled()
        close_window(window)


def test_both_themes_render_and_differ(demo):
    """A theme that silently fails to apply would render two identical frames."""
    Path("artifacts").mkdir(exist_ok=True)
    grabs = {}
    for name in ("light", "dark"):
        demo.set_theme(name)
        demo.navigate("overview")
        pixmap = demo.grab()
        assert pixmap.save(f"artifacts/ui-{name}.png")
        image = pixmap.toImage()
        colours = {image.pixel(x, y) for x in range(0, image.width(), 37)
                   for y in range(0, image.height(), 37)}
        assert len(colours) > 5, f"{name} rendered as a near-flat image"
        grabs[name] = image
    assert grabs["light"] != grabs["dark"]


def test_minimum_size_layout_survives(demo, qtbot):
    """At the smallest allowed window every page must still lay out."""
    demo.timer.stop()
    demo.resize(demo.minimumSize())
    for key, page in demo.pages.items():
        demo.navigate(key)
        qtbot.wait(30)
        assert demo.sidebar.width() == 212
        holder = demo.holders[key]
        assert holder.width() > 0 and holder.height() > 0
    demo.navigate("overview")
    qtbot.wait(30)
    overview = demo.pages["overview"]
    for card in overview.cards.values():
        assert card.width() > 0 and card.height() > 0
    for panel in overview.panels.values():
        assert panel.width() > 0 and panel.plot.height() > 0


@pytest.mark.skipif(os.name != "nt", reason="stores the broker password with real Windows DPAPI")
def test_bridge_and_bluetooth_cards_never_overwrite_each_other(qtbot, tmp_path):
    """Both cards write one config.json; neither may drop the other's fields."""
    from PySide6.QtWidgets import QLineEdit
    window = Window(tmp_path)
    qtbot.addWidget(window)
    window.timer.stop()
    window.navigate("settings")
    page = window.pages["settings"]
    assert page.bridge.password.echoMode() == QLineEdit.EchoMode.Password

    # The bridge card cannot run before there is a device to publish.
    page.bridge.broker.setText("homeassistant.local")
    assert not page.bridge.save()
    assert not (tmp_path / "config.json").exists()

    page.form.devices.addItem("good", ("AA:BB:CC:DD:EE:FF", "MR51ABCDEFGH1234"))
    page.form.user_id.setText("1234567890")
    page.save()
    page.bridge.password.setText("BROKER_SECRET")
    page.bridge.interval.setValue(15)
    assert page.bridge.save()

    # Re-saving Bluetooth setup must carry the broker settings through untouched.
    page.form.conditions.setText("garage")
    page.save()
    saved = json.loads((tmp_path / "config.json").read_text("utf-8"))
    assert saved["conditions"] == "garage"
    assert saved["serial"] == "MR51ABCDEFGH1234"
    assert saved["mqtt_host"] == "homeassistant.local"
    # DPAPI-protected at rest (see config.py, docs/SECURITY.md): the raw file
    # never carries the plaintext password, only its encrypted form.
    assert saved["mqtt_password"] == "" and saved["mqtt_password_dpapi"]
    assert load_config(tmp_path / "config.json").mqtt_password == "BROKER_SECRET"
    assert saved["mqtt_interval"] == 15
    close_window(window)


def test_a_viewer_install_offers_no_recording(qtbot, tmp_path):
    """One click on Start recording, on a PC kept for viewing, used to take the
    DP3 from the Pi the next time the Pi's link dropped."""
    from openpowerstation.config import VIEWER_REFUSAL, Config, save_config
    save_config(Config("AA:BB:CC:DD:EE:FF", "MR51ABCDEFGH1234", "1234567", role="viewer"),
                tmp_path / "config.json")
    window = Window(tmp_path)
    qtbot.addWidget(window)
    window.timer.stop()
    window.update_buttons()
    assert not window.start_button.isEnabled()
    assert window.start_button.toolTip() == VIEWER_REFUSAL
    errors = []
    window.show_error = errors.append
    window.start_recording()          # however it is reached, nothing starts
    assert window.service is None and errors == [VIEWER_REFUSAL]
    close_window(window)


def test_the_setup_card_keeps_every_field_it_does_not_edit(qtbot, tmp_path):
    """The Bluetooth card rebuilt config.json from its own fields plus a list
    of the broker card's, so a field added since -- the role, the Explorer
    serial -- was reset to its default on the next save."""
    from openpowerstation.config import Config, save_config
    path = tmp_path / "config.json"
    save_config(Config("AA:BB:CC:DD:EE:FF", "MR51ABCDEFGH1234", "1234567", role="viewer",
                       jackery_serial="856199990000000"), path)
    window = Window(tmp_path)
    qtbot.addWidget(window)
    window.timer.stop()
    form = window.pages["settings"].form
    assert not form.collect.isChecked()
    form.conditions.setText("garage")
    window.pages["settings"].save()
    saved = load_config(path)
    assert (saved.conditions, saved.role, saved.jackery_serial) == (
        "garage", "viewer", "856199990000000")
    form.collect.setChecked(True)
    window.pages["settings"].save()
    assert load_config(path).role == "collector"
    window.update_buttons()
    assert window.start_button.isEnabled()
    close_window(window)
