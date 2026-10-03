"""Offline Qt desktop shell. Device I/O and DB queries use worker threads.

This module owns the window, the recorder service, the worker jobs and the
snapshot; everything visual lives in :mod:`opendp3.ui`. Pages receive each new
snapshot and call back here for anything that touches the device or the database.
"""
from datetime import datetime
import json
from pathlib import Path
import time

from PySide6.QtCore import Qt, QThread, QTimer, Signal, QObject
from PySide6.QtGui import QFontDatabase
from PySide6.QtWidgets import (
    QApplication, QMainWindow, QWidget, QVBoxLayout, QHBoxLayout, QFrame,
    QDialog, QDialogButtonBox, QFileDialog, QMessageBox, QInputDialog,
    QStackedWidget, QSystemTrayIcon, QMenu, QScrollArea, QTableWidget,
)

from . import __version__
from .config import load_config
from .queries import sessions, snapshot
from .runtime import Service
from .storage import Store
from .ui import icons
from .ui.pages import PAGES
from .ui.pages.settings import SetupForm
from .ui.prefs import load_prefs, save_prefs
from .ui.theme import stylesheet, tokens
from .ui.widgets import IconButton, Pill, Sidebar, label

# Live service states that are worth colouring in the header pill.
STATE_TONES = {"recording": "ok", "released": "warn", "stale": "warn",
               "reconnecting": "warn", "scanning": "warn", "authenticating": "warn",
               "error": "danger", "stopped": "neutral"}


class Job(QThread):
    result = Signal(object)
    error = Signal(str)

    def __init__(self, fn, parent=None):
        super().__init__(parent)
        self.fn = fn

    def run(self):
        try:
            self.result.emit(self.fn())
        except Exception as exc:
            self.error.emit(str(exc) if isinstance(exc,(ValueError,FileNotFoundError,FileExistsError)) else type(exc).__name__)
        finally:
            self.fn = None


class Jobs:
    def init_jobs(self):
        self.jobs = []

    def job(self, fn, callback, error=None):
        task = Job(fn,self)
        self.jobs.append(task)
        task.result.connect(callback)
        task.error.connect(error or self.show_error)
        task.finished.connect(lambda: self.jobs.remove(task) if task in self.jobs else None)
        task.finished.connect(task.deleteLater)
        task.start()
        return task

    def show_error(self, text):
        QMessageBox.warning(self,"OpenPowerstation",text)


class SetupDialog(QDialog, Jobs):
    """First-run modal wrapper around the same form the Settings page shows."""

    def __init__(self, root, parent=None):
        super().__init__(parent)
        self.init_jobs()
        self.root = Path(root)
        self.setWindowTitle("OpenPowerstation · Local Bluetooth setup")
        self.resize(590, 620)
        if parent is not None:
            self.setStyleSheet(parent.styleSheet())
        layout = QVBoxLayout(self)
        self.form = SetupForm(self.root, self, self)
        layout.addWidget(self.form)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Save |
                                   QDialogButtonBox.StandardButton.Cancel)
        buttons.accepted.connect(self.save)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def save(self):
        if self.form.save():
            self.accept()

    def reject(self):
        if self.jobs:
            self.form.status.setText("Waiting for the current scan/login to finish before closing.")
            return
        self.form.clear_password()
        super().reject()


class Bridge(QObject):
    status = Signal(object)


def jackery_transport(session) -> str | None:
    """Name the transport that recorded a Jackery session, or None if not Jackery.

    Sessions used to be labelled from the firmware prefix alone, which called
    every Jackery recording a cloud one -- including the local BLE sessions that
    are now the only kind produced.  The transport is recorded in `conditions`;
    read it there instead of guessing.
    """
    conditions = str(session.get("conditions", "") or "")
    if not str(session.get("firmware", "") or "").startswith("Jackery"):
        return None
    return "cloud" if "cloud" in conditions.lower() else "local BLE"


class Window(QMainWindow, Jobs):
    def __init__(self, root: Path, database: Path | None = None):
        super().__init__()
        # Windows' offscreen Qt plugin exposes no system font database. Load the
        # installed font explicitly for headless QA (normal Windows uses its fonts).
        if not QFontDatabase.families():
            import os
            font_path = Path(os.environ.get('WINDIR', 'C:/Windows'))/'Fonts'/'segoeui.ttf'
            if font_path.exists():
                QFontDatabase.addApplicationFont(str(font_path))
        self.init_jobs()
        self.root = Path(root)
        self.database = None
        self.sid = None
        self.service = None
        self.snap = {}
        self.loading = False
        self.follow = True
        self.cursor = None
        self.chart_range = None
        self.chart_revision = 0
        self.playing = False
        self.force_exit = False
        # Qt clears table selections while tearing a window down, which re-enters
        # focus_incident/refresh. Starting a query against a dying window crashes.
        self.closing = False
        self.prefs = load_prefs(self.root)
        self.theme = self.prefs["theme"]
        self.bridge = Bridge(self)
        self.bridge.status.connect(self.on_service)
        self.setWindowTitle(f"OpenPowerstation {__version__} · Local flight recorder")
        self.resize(1490,980)
        self.setMinimumSize(1080,820)
        self.build()
        self.tray = QSystemTrayIcon(self.windowIcon(),self)
        menu = QMenu(self)
        menu.addAction("Show OpenPowerstation",self.showNormal)
        menu.addAction("Stop and exit",self.stop_and_exit)
        self.tray.setContextMenu(menu)
        self.tray.activated.connect(lambda reason:self.showNormal() if reason == QSystemTrayIcon.ActivationReason.DoubleClick else None)
        self.apply_theme(self.theme)
        self.navigate(self.prefs["page"], remember=False)
        self.reload_device_summary()
        self.timer = QTimer(self); self.timer.timeout.connect(self.tick); self.timer.start(750)
        self.chart_timer = QTimer(self)
        self.chart_timer.setSingleShot(True)
        self.chart_timer.setInterval(120)
        self.chart_timer.timeout.connect(self.refresh)
        if database:
            self.open_database(database)
        elif (self.root/"recordings.sqlite").exists():
            self.open_database(self.root/"recordings.sqlite")

    # --- construction -----------------------------------------------------

    def build(self):
        self.build_menus()
        central = QWidget()
        self.setCentralWidget(central)
        shell = QHBoxLayout(central)
        shell.setContentsMargins(0,0,0,0)
        shell.setSpacing(0)
        self.sidebar = Sidebar(__version__, self)
        for key, button in self.sidebar.buttons.items():
            button.clicked.connect(lambda _, name=key: self.navigate(name))
        shell.addWidget(self.sidebar)
        content = QWidget()
        content.setObjectName("content")
        outer = QVBoxLayout(content)
        outer.setContentsMargins(26,18,26,18)
        outer.setSpacing(14)
        outer.addLayout(self.build_header())
        self.stack = QStackedWidget()
        self.pages = {}
        self.holders = {}
        for page_class in PAGES:
            page = page_class(self)
            self.pages[page_class.KEY] = page
            self.holders[page_class.KEY] = self.wrap(page, page_class.KEY)
            self.stack.addWidget(self.holders[page_class.KEY])
        outer.addWidget(self.stack,1)
        shell.addWidget(content,1)
        self.alias_widgets()
        self.update_buttons()

    def wrap(self, page, key):
        """Settings and About can exceed the window; give those pages a scroll area."""
        if key not in ("settings", "about"):
            return page
        area = QScrollArea()
        area.setWidgetResizable(True)
        area.setFrameShape(QFrame.Shape.NoFrame)
        area.setWidget(page)
        return area

    def build_header(self):
        header = QHBoxLayout()
        header.setSpacing(10)
        titles = QVBoxLayout()
        titles.setSpacing(2)
        heading = QHBoxLayout()
        heading.setSpacing(10)
        self.title = label("Delta Pro 3 Telemetry", "title")
        self.badge = Pill("READ ONLY")
        heading.addWidget(self.title)
        heading.addWidget(self.badge)
        heading.addStretch()
        titles.addLayout(heading)
        self.subtitle = label("A flight recorder for the DELTA Pro 3", "subtitle")
        titles.addWidget(self.subtitle)
        header.addLayout(titles)
        header.addStretch()
        self.theme_button = IconButton("moon", "Switch to dark theme", self)
        self.theme_button.clicked.connect(self.toggle_theme)
        header.addWidget(self.theme_button, 0, Qt.AlignmentFlag.AlignTop)
        self.more_button = IconButton("more", "More actions", self)
        self.more_button.setMenu(self.overflow_menu())
        header.addWidget(self.more_button, 0, Qt.AlignmentFlag.AlignTop)
        return header

    def build_menus(self):
        file_menu = self.menuBar().addMenu("File")
        file_menu.addAction("Open recording…",self.choose_database)
        file_menu.addAction("Create synthetic demonstration",self.demo)
        file_menu.addAction("Local Bluetooth setup…",self.setup)
        file_menu.addSeparator()
        file_menu.addAction("Stop and exit",self.stop_and_exit)
        help_menu = self.menuBar().addMenu("Help")
        help_menu.addAction("Third-party licenses",lambda: self.navigate("about"))

    def overflow_menu(self):
        menu = QMenu(self)
        menu.addAction("Open recording…",self.choose_database)
        menu.addAction("Create synthetic demonstration",self.demo)
        menu.addAction("Local Bluetooth setup…",self.setup)
        menu.addSeparator()
        menu.addAction("Third-party licenses",lambda: self.navigate("about"))
        menu.addAction("Stop and exit",self.stop_and_exit)
        return menu

    def alias_widgets(self):
        """Stable names for the widgets the smoke test and tests reach for."""
        overview, live, incidents = self.pages["overview"], self.pages["live"], self.pages["incidents"]
        self.banner = overview.banner
        self.cards = overview.cards
        self.footer = overview.footer
        self.session_choice = overview.session_choice
        self.span_choice = overview.span_choice
        self.slider = overview.slider
        self.position = overview.position
        self.start_button = overview.start_button
        self.release_button = overview.release_button
        self.resume_button = overview.resume_button
        self.mark_button = overview.mark_button
        self.export_button = overview.export_button
        self.stop_button = overview.stop_button
        self.play_button = overview.play_button
        self.coverage_table = live.coverage_table
        self.raw_text = live.raw_text
        self.raw_label = live.raw_label
        self.notes = live.notes
        self.incident_table = incidents.incident_table
        self.events_table = incidents.events_table

    # --- theming and navigation -------------------------------------------

    def apply_theme(self, theme):
        self.theme = theme
        palette = tokens(theme)
        self.setStyleSheet(stylesheet(theme))
        self.setWindowIcon(icons.app_icon(palette))
        if getattr(self, "tray", None):
            self.tray.setIcon(self.windowIcon())
        self.theme_button.set_glyph("sun" if theme == "dark" else "moon", theme)
        self.theme_button.setToolTip("Switch to light theme" if theme == "dark"
                                     else "Switch to dark theme")
        for child in self.findChildren(QWidget):
            handler = getattr(child, "apply_theme", None)
            if callable(handler):
                handler(theme)
        if self.snap:
            self.render(self.snap)

    def set_theme(self, theme):
        if theme == self.theme:
            return
        self.apply_theme(theme)
        self.prefs["theme"] = theme
        save_prefs(self.root, self.prefs)

    def toggle_theme(self):
        self.set_theme("dark" if self.theme == "light" else "light")

    def navigate(self, key, remember=True):
        page = self.pages.get(key)
        if page is None:
            key, page = "overview", self.pages["overview"]
        self.stack.setCurrentWidget(self.holders[key])
        for name, button in self.sidebar.buttons.items():
            button.setChecked(name == key)
            button.apply_theme(self.theme)
        self.title.setText(page.TITLE)
        self.subtitle.setText(page.SUBTITLE)
        if remember and self.prefs.get("page") != key:
            self.prefs["page"] = key
            save_prefs(self.root, self.prefs)

    @property
    def page(self):
        """Key of the destination currently on screen."""
        current = self.stack.currentWidget()
        for key, holder in self.holders.items():
            if holder is current:
                return key
        return "overview"

    def reload_device_summary(self):
        """Sidebar footer. Shows what is configured without a product photograph."""
        try:
            config = load_config(self.root/"config.json")
        except (FileNotFoundError,ValueError,TypeError):
            self.sidebar.set_device("DELTA Pro 3", "No device configured",
                                    "Open Settings to pair over local Bluetooth")
            return
        state = self.service.state if self.service and self.service.is_alive() else "not connected"
        self.sidebar.set_device("DELTA Pro 3", f"Serial {config.serial}",
                                f"Bluetooth {state}")

    # --- button state -----------------------------------------------------

    def recording(self):
        return bool(self.service and self.service.is_alive())

    def can_mark(self):
        return bool(self.database and self.sid) and not self.background_recording()

    def update_buttons(self):
        active = self.recording()
        external = self.background_recording()
        state = self.service.state if active else ""
        self.start_button.setEnabled(not active and not external)
        self.stop_button.setText('Stop qualification' if external else 'Stop recording')
        self.stop_button.setEnabled(active or (external and self.qualification_selected()))
        self.release_button.setEnabled(active and state != "released")
        self.resume_button.setEnabled(active and state == "released")
        self.mark_button.setEnabled(self.can_mark())
        self.export_button.setEnabled(bool(self.snap.get("count")))
        self.play_button.setEnabled(bool(self.database) and not active and not external)
        for page in self.pages.values():
            update = getattr(page, "update_actions", None)
            if callable(update):
                update()

    def background_recording(self):
        return bool(self.snap.get('session',{}).get('status') == 'recording'
                    and not (self.service and self.service.is_alive()))

    def qualification_selected(self):
        try:
            metadata = json.loads((self.root/'qualification-run.json').read_text('utf-8-sig'))
            return metadata.get('session_id') == self.sid
        except (OSError,ValueError):
            return False

    # --- recording lifecycle ----------------------------------------------

    def setup(self):
        if self.recording():
            return self.show_error("Stop recording before editing connection setup.")
        self.navigate("settings")

    def start_recording(self):
        try:
            config = load_config(self.root/"config.json")
        except (FileNotFoundError,ValueError,TypeError):
            dialog = SetupDialog(self.root,self)
            dialog.exec()
            try: config = load_config(self.root/"config.json")
            except (FileNotFoundError,ValueError,TypeError): return
        self.database = self.root/"recordings.sqlite"
        self.sid = None; self.follow = True; self.playing = False
        self.reset_chart_view(reset_scales=True)
        self.service = Service(config,self.database,notify=self.bridge.status.emit,
                               config_path=self.root/"config.json")
        self.service.start()
        self.banner.setText("Starting a read-only local recording…")
        self.reload_device_summary()
        self.update_buttons()

    def on_service(self, value):
        self.set_badge(value["state"].upper(), STATE_TONES.get(value["state"], "neutral"))
        self.banner.setText(value["detail"])
        if value.get("session_id") and self.sid != value["session_id"]:
            self.sid = value["session_id"]
            self.reset_chart_view(reset_scales=True)
            self.reload_sessions()
        self.reload_device_summary()
        self.update_buttons()

    def set_badge(self, text, tone="neutral"):
        self.badge.setText(text)
        self.badge.set_tone(tone)

    def stop_recording(self):
        if self.background_recording() and self.qualification_selected():
            (self.root/'qualification.stop').write_text('stop\n',encoding='utf-8')
        elif self.service: self.service.stop()
        self.banner.setText("Stopping Bluetooth and saving evidence…")

    def send(self, command):
        if self.recording():
            self.service.command(command)

    # --- databases and snapshots ------------------------------------------

    def choose_database(self):
        if self.recording():
            return self.show_error("Stop recording before opening another database.")
        name,_ = QFileDialog.getOpenFileName(self,"Open a recording from a trusted source",str(self.root),"SQLite recordings (*.sqlite *.db)")
        if name: self.open_database(Path(name))

    def open_database(self, path):
        self.database = Path(path); self.sid = None; self.snap = {}
        self.reset_chart_view(reset_scales=True)
        self.follow = True; self.playing = False; self.reload_sessions()

    def reload_sessions(self):
        if not self.database or not self.database.exists(): return
        current_path = self.database
        def done(rows):
            if self.database != current_path: return
            self.session_choice.blockSignals(True); self.session_choice.clear()
            for s in rows:
                text = datetime.fromtimestamp(s["start_utc_ns"]/1e9).strftime("%Y-%m-%d %H:%M:%S")
                if s["synthetic"]:
                    text += "  ·  SYNTHETIC DEMO"
                elif jackery_transport(s):
                    text += f"  ·  Jackery {jackery_transport(s)} recording"
                else:
                    text += "  ·  DP3 recording"
                self.session_choice.addItem(text,s["id"])
            idx = self.session_choice.findData(self.sid)
            self.session_choice.setCurrentIndex(max(0,idx))
            self.sid = self.session_choice.currentData()
            self.session_choice.blockSignals(False)
            self.refresh()
        self.job(lambda:sessions(current_path),done)

    def select_session(self,index):
        self.sid = self.session_choice.itemData(index)
        self.reset_chart_view(reset_scales=True)
        self.follow = True; self.cursor = None; self.refresh()

    def demo(self):
        if self.recording():
            return self.show_error("Stop live recording before switching to the demo.")
        from .demo import make_demo
        target = self.root/"demo"/(datetime.now().strftime("%Y%m%d-%H%M%S-%f")+".sqlite")
        self.banner.setText("Generating a clearly labeled synthetic demonstration…")
        self.job(lambda:make_demo(target),self.open_database)

    def tick(self):
        self.update_buttons()
        if self.playing and self.snap:
            self.cursor = min(self.snap["latest"],(self.cursor or 0)+.75*10)
            if self.cursor >= self.snap["latest"]:
                self.playing = False; self.play_button.setText("Play")
        self.refresh()

    def refresh(self):
        if self.closing or self.loading or not self.database or not self.database.exists(): return
        if self.chart_timer.isActive(): return
        path,sid = self.database,self.sid
        request = self.chart_request()
        revision = self.chart_revision
        cursor, span = request
        self.loading = True
        def done(snap):
            self.loading = False
            if self.database == path and self.sid == sid and revision == self.chart_revision:
                self.render(snap)
            else:
                # A wheel/seek/preset change can arrive while a query is running.
                # Never paint that old window over the user's newer selection.
                self.refresh()
        def error(message):
            self.loading = False
            self.banner.setText("Unable to read recording: "+message)
        self.job(lambda:snapshot(path,sid,end_t=cursor,span=span),done,error)

    def render(self,snap):
        if not snap or self.closing: return
        self.snap = snap
        active = self.recording()
        session = snap["session"]
        synthetic = bool(session["synthetic"])
        transport = jackery_transport(session)
        if transport:
            link = "Read-only HTTPS" if transport == "cloud" else "Read-only Bluetooth"
            self.title.setText("Jackery Explorer Telemetry")
            self.subtitle.setText(f"{transport} telemetry · device controls are available through Home Assistant when enabled")
            self.sidebar.set_device("Explorer 1000", f"Jackery {transport} recording", link)
        elif not synthetic:
            self.title.setText("Delta Pro 3 Telemetry")
            self.subtitle.setText("A flight recorder for the DELTA Pro 3")
        if not active:
            self.set_badge("SYNTHETIC DEMO" if synthetic else "SAVED RECORDING",
                           "warn" if synthetic else "neutral")
            self.banner.setText("SYNTHETIC DATA — generated example, not evidence from your battery." if synthetic else
                                f"Saved session · {session['status']} · {snap['count']:,} received frames · {self.database.name}")
            if self.service and self.service.error:
                self.set_badge("RECORDING STOPPED — ERROR","danger")
                self.banner.setText(self.service.error)
            elif self.background_recording():
                self.set_badge('BACKGROUND RECORDER','accent')
                self.banner.setText('Live view of the independent qualification recorder. Stop qualification releases Bluetooth; closing this viewer does not stop it.')
        now_t = (time.monotonic_ns()-session["start_mono_ns"])/1e9 if active or self.background_recording() else snap["right"]
        # Named update_snapshot, not render: QWidget already defines render() and a
        # page without its own hook would silently call Qt's paint routine instead.
        for page in self.pages.values():
            update = getattr(page, "update_snapshot", None)
            if callable(update):
                update(snap, now_t)
        self.update_buttons()

    # --- playback ---------------------------------------------------------

    def chart_request(self):
        if self.chart_range is not None:
            left, right = self.chart_range
            return right, right - left
        return None if self.follow else self.cursor, self.span_choice.currentData()

    def reset_chart_view(self, reset_scales=False):
        self.chart_range = None
        self.chart_revision += 1
        self.chart_timer.stop()
        if reset_scales:
            self.pages["overview"].reset_scales()

    def reset_time_zoom(self):
        self.reset_chart_view()
        self.refresh()

    def change_chart_span(self, _index):
        self.reset_chart_view()
        self.refresh()

    def inspect_chart_range(self, bounds):
        if self.closing or not self.snap:
            return
        self.chart_range = tuple(bounds)
        self.chart_revision += 1
        self.follow = False
        self.playing = False
        self.cursor = bounds[1]
        self.play_button.setText("Play")
        # Pan/zoom affects the viewer only. The recorder continues independently.
        self.chart_timer.start()

    def seek(self,value):
        self.reset_chart_view()
        self.follow = False; self.playing = False
        self.cursor = self.snap.get("latest",0)*value/1000
        self.play_button.setText("Play"); self.refresh()

    def latest(self):
        self.reset_chart_view()
        self.cursor = None
        self.follow = True; self.playing = False; self.play_button.setText("Play"); self.refresh()

    def toggle_play(self):
        self.reset_chart_view()
        self.playing = not self.playing; self.follow = False
        if self.cursor is None or self.cursor>=self.snap.get("latest",0): self.cursor = 0
        self.play_button.setText("Pause (10x)" if self.playing else "Play")

    # --- incidents and evidence -------------------------------------------

    def selected_incident(self):
        return self.pages["incidents"].selected()

    def focus_incident(self):
        if self.closing: return
        selected = self.selected_incident()
        for incident in self.snap.get("incidents",[]):
            if incident["id"] == selected:
                self.reset_chart_view()
                self.playing = False; self.play_button.setText("Play")
                self.follow = False; self.cursor = min(incident["end_t"],self.snap["latest"])
                self.span_choice.setCurrentIndex(1)
                self.refresh()

    def mark(self):
        if not self.sid: return
        note,ok = QInputDialog.getText(self,"Mark incident","Private note (excluded from shareable export):")
        if not ok: return
        if self.recording():
            self.service.command("mark",note)
        else:
            path,sid,t = self.database,self.sid,self.snap["right"]
            utc = self.snap["session"]["start_utc_ns"]+int(t*1e9)
            def annotate():
                with Store(path) as store:
                    store.event(sid,t,utc,"manual",note[:4000])
                    store.incident(sid,t,"manual")
            self.job(annotate,lambda _:self.refresh())

    def delete_incident(self):
        selected = self.selected_incident()
        if selected is None: return
        if QMessageBox.question(self,"Delete incident protection",
             "Remove this incident's retention protection? Ordinary retention can then delete its old data.") != QMessageBox.StandardButton.Yes:
            return
        if self.recording():
            self.service.command("delete_incident",str(selected))
        else:
            path = self.database
            def remove():
                with Store(path) as store: store.delete_incident(selected)
            self.job(remove,lambda _:self.refresh())

    def export(self):
        # Default into the folder the Exported files page lists, so a bundle the
        # viewer wrote is findable again without the user remembering where.
        default = self.root/"exports"
        try:
            default.mkdir(parents=True, exist_ok=True)
        except OSError:
            default = self.root
        parent = QFileDialog.getExistingDirectory(self,"Choose folder for a new evidence bundle",str(default))
        if not parent: return
        from .exporting import export_evidence
        destination = Path(parent)/("OpenPowerstation-"+datetime.now().strftime("%Y%m%d-%H%M%S-%f"))
        path,sid,incident = self.database,self.sid,self.selected_incident()
        self.banner.setText("Exporting sanitized numeric data and offline charts…")
        def done(result):
            report,_ = result
            self.pages["exports"].reload()
            self.show_error(f"Evidence saved:\n{report}\n\nIdentifiers, notes, and opaque payloads are excluded.\n"
                            "Use the CLI --raw option only when you need a separate private raw archive.")
        self.job(lambda:export_evidence(path,destination,sid=sid,incident_id=incident),done)

    # --- shutdown ---------------------------------------------------------

    def stop_and_exit(self):
        self.force_exit = True
        self.closing = True
        if self.service: self.service.stop()
        self.banner.setText("Saving and stopping before exit…")
        self.wait_exit()

    def wait_exit(self):
        self.timer.stop()
        self.chart_timer.stop()
        if (self.service and self.service.is_alive()) or self.jobs:
            QTimer.singleShot(150,self.wait_exit)
        else:
            self.timer.stop(); self.tray.hide(); self.close()

    def closeEvent(self,event):
        active = self.recording()
        if active and not self.force_exit:
            if QSystemTrayIcon.isSystemTrayAvailable():
                self.tray.show(); self.hide()
                self.tray.showMessage("OpenPowerstation is still recording","Double-click the tray icon to return. Use Stop and exit to finish.")
            else:
                self.banner.setText("System tray unavailable. Use Stop and exit to close safely.")
            event.ignore(); return
        if active or self.jobs:
            event.ignore(); self.stop_and_exit(); return
        self.teardown()
        event.accept()

    def teardown(self):
        """Stop everything that could still touch a widget after this point.

        Two hazards, both of which abort the process rather than raise:
        Qt clears table selections while destroying children, which re-enters
        focus_incident; and a Job is a QThread parented to this window, so a job
        whose deleteLater has not been flushed yet would be destroyed mid-run.
        """
        self.closing = True
        self.timer.stop()
        self.chart_timer.stop()
        self.tray.hide()
        for table_widget in self.findChildren(QTableWidget):
            table_widget.blockSignals(True)
        # findChildren, not self.jobs: a job is dropped from that list on the
        # finished signal, which fires before its thread has actually exited.
        for task in self.findChildren(Job):
            # A queued result would land in render() after the widgets are gone.
            task.result.disconnect()
            task.error.disconnect()
            task.wait(5000)
        self.jobs.clear()
        QApplication.processEvents()  # Flush the queued deleteLater calls.


def launch(root, database=None):
    app = QApplication.instance() or QApplication([])
    app.setApplicationName("OpenPowerstation")
    app.setQuitOnLastWindowClosed(True)
    window = Window(root,database)
    window.show()
    return app.exec()
