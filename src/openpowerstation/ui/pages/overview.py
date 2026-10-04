"""Overview: headline readings, the aligned charts, and playback.

What a session has -- its fields, the chart panels they need, its headline
readings -- comes from that session's own registry (fields.fields_for_session),
the one the coverage table and the evidence export read. A Jackery session
gets its voltage, frequency and estimated-time panels; a DP3 session does not.
"""
import math
import html
import time
import pyqtgraph as pg
from PySide6.QtCore import Qt
from PySide6.QtGui import QCursor
from PySide6.QtWidgets import (
    QFrame, QHBoxLayout, QPushButton, QScrollArea, QSplitter, QTableWidgetItem,
    QSizePolicy, QToolTip, QVBoxLayout, QWidget,
)

from ...fields import (CHART_GROUPS, DP3_FIELDS, JACKERY_OBSERVATIONS, chart_groups, device,
                       fields_for_session)
from ...queries import gap, plot_arrays
from ..theme import series as series_colors
from ..theme import tokens
from ..charts import CHART_HELP, ClickComboBox, PlaybackSlider, elapsed_text, nearest_sample, sample_value
from ..widgets import Card, ChartPanel, StatCard, label, table

# Compact names for the legend row; the full labels stay in tooltips and Coverage.
SHORT_NAMES = {
    "bms_max_cell_temp": "BMS max", "bms_min_cell_temp": "BMS min",
    "bms_max_mos_temp": "MOS max", "bms_min_mos_temp": "MOS min",
    "cms_batt_temp": "CMS batt",
    "bms_batt_soc": "Main SOC", "cms_batt_soc": "System SOC", "cms_batt_soh": "SOH",
    "bms_batt_soh": "Main SOH", "cms_chg_dsg_state": "Charge state",
    "plug_in_info_ac_charger_flag": "AC connected",
    "plug_in_info_pv_h_type": "PV HV type", "plug_in_info_pv_l_type": "PV LV type",
    "pow_in_sum_w": "Total in", "pow_out_sum_w": "Total out", "pow_get_ac_in": "AC in",
    "pow_get_ac_lv_out": "AC LV", "pow_get_ac_hv_out": "AC HV", "pow_get_pv_h": "PV HV",
    "pow_get_pv_l": "PV LV", "pow_get_bms": "Battery", "cms_bms_run_state": "BMS state",
    "errcode": "Raw error",
    "bms_err_code": "BMS error", "mppt_err_code": "MPPT error", "inv_err_code": "Inverter error",
    "pd_err_code": "PD error", "llc_err_code": "LLC error", "llc_inv_err_code": "LLC inv error",
    "dcdc_err_code": "DC/DC error", "plug_in_info_5p8_err_code": "5+8 port error",
    "plug_in_info_acp_err_code": "AC port error", "plug_in_info_4p8_1_err_code": "4+8 port 1 err",
    "plug_in_info_4p8_2_err_code": "4+8 port 2 err", "plug_in_info_dcp_err_code": "DC port 1 err",
    "plug_in_info_dcp2_err_code": "DC port 2 err",
    "jackery_ac_input_w": "AC in", "jackery_ac_voltage_v": "AC out",
    "jackery_ac_frequency_hz": "AC out", "jackery_charge_time_h": "Charge time",
    "jackery_output_time_h": "Output time",
}
# A panel per chart group (fields.CHART_GROUPS); a session shows the ones its
# fields use.
TITLES = {"temperature": ("Temperature", "°C"), "power": ("Power", "W"), "soc": ("SOC / SOH", "%"),
          "voltage": ("Voltage", "V"), "frequency": ("Frequency", "Hz"),
          "duration": ("Estimated time", "h"), "state": ("State / errors", "raw")}
# Headline readings per device (fields.device): key, title, glyph, tone.
CARDS = {
    "dp3": [("bms_max_cell_temp", "BMS MAX CELL", "temperature", "danger"),
            ("bms_batt_soc", "MAIN BATTERY SOC", "battery", "ok"),
            ("pow_in_sum_w", "TOTAL INPUT", "inflow", "info"),
            ("pow_out_sum_w", "TOTAL OUTPUT", "outflow", "warn")],
    "jackery": [("cms_batt_temp", "BATTERY TEMPERATURE", "temperature", "danger"),
                ("bms_batt_soc", "BATTERY SOC", "battery", "ok"),
                ("pow_in_sum_w", "TOTAL INPUT", "inflow", "info"),
                ("pow_out_sum_w", "TOTAL OUTPUT", "outflow", "warn")],
}
GAP_KINDS = {"device_error", "suspect_telemetry", "manual", "disconnected"}
FOOTER = ("Host receipt timing · Gaps are unknown state · Hardware qualification pending")


class OverviewPage(QWidget):
    KEY = "overview"
    TITLE = "Delta Pro 3 Telemetry"
    SUBTITLE = "A flight recorder for the DELTA Pro 3"

    def __init__(self, window, parent=None):
        super().__init__(parent)
        self.window = window
        self.curves = {}
        self.markers = {}
        # The registry of the session on screen (see _use_session). Until one
        # is loaded the window describes a DP3, so the page does too.
        self.registry = DP3_FIELDS
        self.fields = {field.key: field for field in self.registry}
        self.groups = chart_groups({})
        self.device = device({})
        # One brush per colour, reused: the plot's symbol cache is keyed by
        # brush object, so a fresh brush per point redraws every state symbol.
        self.brushes = {}
        # SOH is a different estimate from SOC. Showing both by default makes a
        # near-100% health line flatten small charge changes in an automatic fit.
        self.hidden_fields = {field.key for field in (*DP3_FIELDS, *JACKERY_OBSERVATIONS)
                              if field.key.endswith("_soh")}
        self.focused_group = None
        self.state_annotations = []
        self.state_rows = {}
        self.inspection = {}
        self._hover_time = 0
        self._syncing_time = False
        self.theme = window.theme
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(14)
        self.actions_widget = QWidget()
        self.actions_widget.setLayout(self._actions())
        self.actions_widget.layout().setContentsMargins(0, 0, 0, 0)
        layout.addWidget(self.actions_widget)
        self.banner = label("Open a recording, create a synthetic demo, or configure your "
                            "DP3 from Settings.", "muted", wrap=True)
        layout.addWidget(self.banner)
        self.cards_widget = QWidget()
        self.cards_widget.setLayout(self._cards())
        self.cards_widget.layout().setContentsMargins(0, 0, 0, 0)
        layout.addWidget(self.cards_widget)
        layout.addLayout(self._selectors())
        layout.addLayout(self._navigation())
        layout.addWidget(self._split(), 1)
        self.chart_help = label(CHART_HELP, "faint")
        self.chart_help.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
        layout.addWidget(self.chart_help)
        layout.addLayout(self._playback())
        self.footer = label(FOOTER, "faint")
        layout.addWidget(self.footer)

    # --- construction -----------------------------------------------------

    def _actions(self):
        row = QHBoxLayout()
        row.setSpacing(8)
        window = self.window
        self.start_button = QPushButton("Start recording")
        self.start_button.setObjectName("primary")
        self.start_button.clicked.connect(window.start_recording)
        self.release_button = QPushButton("Release Bluetooth")
        self.release_button.clicked.connect(lambda: window.send("release"))
        self.resume_button = QPushButton("Resume")
        self.resume_button.clicked.connect(lambda: window.send("resume"))
        self.mark_button = QPushButton("Mark incident")
        self.mark_button.clicked.connect(window.mark)
        self.export_button = QPushButton("Export evidence…")
        self.export_button.setToolTip("Exports the whole session shown. To export one incident's "
                                      "window, select it on Incidents and export it there.")
        self.export_button.clicked.connect(window.export_session)
        self.stop_button = QPushButton("Stop recording")
        self.stop_button.setObjectName("danger")
        self.stop_button.clicked.connect(window.stop_recording)
        for button in (self.start_button, self.release_button, self.resume_button,
                       self.mark_button, self.export_button, self.stop_button):
            row.addWidget(button)
        row.addStretch()
        return row

    def _cards(self):
        row = QHBoxLayout()
        row.setSpacing(14)
        self.cards = {}
        for key, title, glyph, tone in CARDS[self.device]:
            card = StatCard(key, title, glyph, tone, self)
            self.cards[key] = card
            row.addWidget(card)
        return row

    def _set_cards(self):
        """Replace the headline readings with this device's, in the same dict."""
        row = self.cards_widget.layout()
        for card in self.cards.values():
            row.removeWidget(card)
            card.deleteLater()
        # Cleared, not rebound: the window holds this dict as Window.cards.
        self.cards.clear()
        for key, title, glyph, tone in CARDS[self.device]:
            card = StatCard(key, title, glyph, tone, self)
            card.apply_theme(self.theme)
            self.cards[key] = card
            row.addWidget(card)

    def _selectors(self):
        row = QHBoxLayout()
        row.setSpacing(10)
        self.session_choice = ClickComboBox()
        self.session_choice.setAccessibleName("Recording session")
        self.session_choice.currentIndexChanged.connect(self.window.select_session)
        row.addWidget(self.session_choice, 1)
        row.addWidget(label("View window", "muted"))
        self.span_choice = ClickComboBox()
        self.span_choice.setAccessibleName("View window")
        for text, seconds in [("10 minutes", 600), ("30 minutes", 1800),
                              ("2 hours", 7200), ("All", None)]:
            self.span_choice.addItem(text, seconds)
        self.span_choice.currentIndexChanged.connect(self.window.change_chart_span)
        row.addWidget(self.span_choice)
        self.fit_y_button = QPushButton("Fit values")
        self.fit_y_button.setToolTip("Switch the visible numeric charts to Fit data. Does not change time or resume following.")
        self.fit_y_button.clicked.connect(self.fit_y)
        row.addWidget(self.fit_y_button)
        self.reset_view_button = QPushButton("Reset zoom")
        self.reset_view_button.setToolTip("Restore the selected time span here. Use Latest to jump to the newest reading.")
        self.reset_view_button.clicked.connect(self.window.reset_time_zoom)
        row.addWidget(self.reset_view_button)
        return row

    def _navigation(self):
        row = QHBoxLayout()
        self.all_charts_button = QPushButton("All charts")
        self.all_charts_button.setObjectName("chartControl")
        self.all_charts_button.clicked.connect(lambda: self.focus_chart(None))
        self.all_charts_button.setVisible(False)
        row.addWidget(self.all_charts_button)
        self.range_status = label("No recording loaded", "muted")
        self.range_status.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
        row.addWidget(self.range_status, 1)
        self.zoom_out_button = QPushButton("−")
        self.zoom_in_button = QPushButton("+")
        for button, factor, text in ((self.zoom_out_button, 1.5, "Zoom out in time"),
                                     (self.zoom_in_button, 1 / 1.5, "Zoom in on time")):
            button.setObjectName("chartControl")
            button.setAccessibleName(text)
            button.setToolTip(text)
            button.clicked.connect(lambda _, factor=factor: self.zoom_time(factor))
            row.addWidget(button)
        self.activity_button = QPushButton("Activity")
        self.activity_button.setObjectName("chartControl")
        self.activity_button.setCheckable(True)
        self.activity_button.setChecked(False)
        self.activity_button.toggled.connect(lambda checked: self.activity_rail.setVisible(checked))
        row.addWidget(self.activity_button)
        return row

    def _split(self):
        split = QSplitter(Qt.Orientation.Horizontal)
        charts = QWidget()
        chart_layout = QVBoxLayout(charts)
        chart_layout.setContentsMargins(0, 0, 0, 0)
        chart_layout.setSpacing(10)
        self.panels = {}
        for group in CHART_GROUPS:
            title, unit = TITLES[group]
            # All share one x axis, so only the bottom panel labels it.
            panel = ChartPanel(group, f"{title} {unit}" if unit != "raw" else title,
                               axis_label=group == CHART_GROUPS[-1], parent=self)
            if group not in self.groups:
                panel.hide()
            chart_layout.addWidget(panel, 1)
            self.panels[group] = panel
            self.markers[group] = []
            view = panel.plot.getViewBox()
            view.sigXRangeChanged.connect(lambda _, bounds, view=view: self.sync_time(view, bounds))
            view.sigRangeChangedManually.connect(
                lambda _, view=view: self.window.inspect_chart_range(view.viewRange()[0]))
            panel.fieldsChanged.connect(self.set_field_visible)
            panel.fieldsAllChanged.connect(lambda visible, group=group: self.set_group_visible(group, visible))
            panel.scaleChanged.connect(self.redraw)
            panel.focusRequested.connect(self.focus_chart)
            panel.plot.hovered.connect(lambda seconds, group=group: self.inspect_samples(group, seconds))
            panel.plot.hoverLeft.connect(self.clear_inspection)
            panel.crosshair = pg.InfiniteLine(angle=90, movable=False)
            panel.plot.addItem(panel.crosshair, ignoreBounds=True)
            panel.crosshair.hide()
        # On short windows keep each complete plot (including its axes) and
        # scroll the chart stack instead of clipping the bottom of every plot.
        self.chart_scroll = QScrollArea()
        self.chart_scroll.setWidgetResizable(True)
        self.chart_scroll.setFrameShape(QFrame.Shape.NoFrame)
        self.chart_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.chart_scroll.setWidget(charts)
        split.addWidget(self.chart_scroll)
        rail = Card("Recent activity")
        self.activity_rail = rail
        rail.setMinimumWidth(300)
        self.incident_summary = label("No incidents detected", "muted", wrap=True)
        rail.body.addWidget(self.incident_summary)
        self.activity_table = table(["Elapsed", "Event", "Detail (private)"])
        rail.body.addWidget(self.activity_table, 1)
        rail.body.addWidget(label("Details stay on this machine and are excluded from "
                                  "shareable exports.", "faint", wrap=True))
        split.addWidget(rail)
        rail.hide()
        split.setStretchFactor(0, 3)
        split.setStretchFactor(1, 1)
        split.setSizes([1000, 380])
        return split

    def _playback(self):
        row = QHBoxLayout()
        row.setSpacing(10)
        self.play_button = QPushButton("Play")
        self.play_button.clicked.connect(self.window.toggle_play)
        row.addWidget(self.play_button)
        self.slider = PlaybackSlider(Qt.Orientation.Horizontal)
        self.slider.setRange(0, 1000)
        self.slider.setAccessibleName("Playback position")
        self.slider.sliderMoved.connect(self.window.seek)
        row.addWidget(self.slider, 1)
        self.position = label("0.0 s", "muted")
        self.position.setMinimumWidth(85)
        self.position.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        row.addWidget(self.position)
        self.latest_button = QPushButton("Latest")
        self.latest_button.clicked.connect(self.window.latest)
        row.addWidget(self.latest_button)
        return row

    # --- rendering --------------------------------------------------------

    def reset_scales(self):
        for panel in self.panels.values():
            panel.value_limits = None

    def fit_y(self):
        for group, panel in self.panels.items():
            if group != "state" and (self.focused_group is None or self.focused_group == group):
                panel.scale_choice.blockSignals(True)
                panel.scale_choice.setCurrentIndex(0)
                panel.scale_choice.blockSignals(False)
                panel.value_limits = None
        self.redraw()

    def redraw(self):
        if self.window.snap and not self.window.loading and not self.window.chart_timer.isActive():
            self._render_series(self.window.snap)
        else:
            self.window.refresh()

    def set_field_visible(self, key, visible):
        if visible:
            self.hidden_fields.discard(key)
        else:
            self.hidden_fields.add(key)
        panel = self.panels[self.fields[key].group]
        if panel.scale_choice.currentData() != "hold":
            panel.value_limits = None
        self.redraw()

    def set_group_visible(self, group, visible):
        keys = {key for key, field in self.fields.items() if field.group == group}
        if visible:
            self.hidden_fields.difference_update(keys)
        else:
            self.hidden_fields.update(keys)
        if self.panels[group].scale_choice.currentData() != "hold":
            self.panels[group].value_limits = None
        self.redraw()

    def _use_session(self, session):
        """Show what this session's own registry has: its fields, panels and cards.

        The curves of the previous registry go, so nothing drawn for one device
        lingers on another's charts or keeps another's labels.
        """
        registry = fields_for_session(session)
        if registry is self.registry:
            return
        for key, curve in self.curves.items():
            self.panels[self.fields[key].group].plot.removeItem(curve)
        self.curves.clear()
        self.inspection = {}
        self.registry = registry
        self.fields = {field.key: field for field in registry}
        self.groups = chart_groups(session)
        if device(session) != self.device:
            self.device = device(session)
            self._set_cards()
        if self.focused_group not in self.groups:
            self.focused_group = None
        self._layout_panels()

    def _layout_panels(self):
        """Show the session's panels, or only the focused one."""
        focused = self.focused_group is not None
        for key, panel in self.panels.items():
            panel.setVisible(key in self.groups and (not focused or key == self.focused_group))
            panel.focus_button.setVisible(not focused)
        self.cards_widget.setVisible(not focused)
        self.actions_widget.setVisible(not focused)
        self.banner.setVisible(not focused)
        self.all_charts_button.setVisible(focused)
        self.fit_y_button.setVisible(self.focused_group != "state")

    def focus_chart(self, group):
        self.focused_group = None if group == self.focused_group else group
        focused = self.focused_group is not None
        self._layout_panels()
        for key, panel in self.panels.items():
            panel.plot.setLabel("bottom", "Elapsed receipt time (h:mm:ss)" if focused or key == "state" else None)
        self.clear_inspection()
        self.redraw()

    def zoom_time(self, factor):
        if self.window.snap:
            panel = self.panels[self.focused_group or "temperature"]
            panel.plot.getViewBox().zoom_time(factor)

    def sync_time(self, source, bounds):
        # Explicit ranges keep hidden/focused panels aligned too. ViewBox's
        # pixel-geometry links shift ranges when a hidden chart changes width.
        if self._syncing_time:
            return
        self._syncing_time = True
        try:
            for panel in self.panels.values():
                view = panel.plot.getViewBox()
                if view is not source:
                    view.setXRange(*bounds, padding=0)
        finally:
            self._syncing_time = False

    def update_snapshot(self, snap, now_t):
        self._use_session(snap["session"])
        for row in snap["coverage"]:
            card = self.cards.get(row["key"])
            if card:
                card.set_reading(row["value"], row["unit"], row["last_t"], now_t)
        self._render_series(snap)
        self._render_markers(snap)
        self._render_activity(snap)
        self.slider.setValue(int(1000 * snap["right"] / max(1, snap["latest"])))
        self.position.setText(elapsed_text(snap["right"], 1))
        self.footer.setText(f"{snap['count']:,} frames · {len(snap['incidents'])} incident "
                            f"windows · {FOOTER}")

    def _series_color(self, group, key):
        keys = [k for k, field in self.fields.items() if field.group == group]
        palette = series_colors(self.theme)
        return palette[keys.index(key) % len(palette)]

    def _brush(self, colour):
        brush = self.brushes.get(colour)
        if brush is None:
            brush = self.brushes[colour] = pg.mkBrush(colour)
        return brush

    def _render_series(self, snap):
        bounds = {}
        thinning = snap.get("thinning")
        for key, points in snap["series"].items():
            field = self.fields.get(key)
            if field is None:
                continue
            group = field.group
            if key not in self.curves:
                curve = self.panels[group].plot.plot(
                    pen=pg.mkPen(self._series_color(group, key), width=1.7),
                    name=field.label, connect="finite")
                curve.setDownsampling(auto=group != "state", method="peak")
                curve.setClipToView(group != "state")
                self.curves[key] = curve
            self.curves[key].setVisible(key not in self.hidden_fields)
            if group == "state":
                continue
            x, y = plot_arrays(points, thinning)
            self.curves[key].setData(x, y, connect="finite", symbol="o" if len(points) <= 100 else None,
                                     symbolSize=4, symbolPen=None,
                                     symbolBrush=self._series_color(group, key))
            finite = [value for value in y if math.isfinite(value)]
            if finite and key not in self.hidden_fields:
                low, high = min(finite), max(finite)
                old = bounds.get(group, (low, high))
                bounds[group] = min(low, old[0]), max(high, old[1])
        for key, curve in self.curves.items():
            if key not in snap["series"]:
                curve.setData([], [], symbol=None, symbolBrush=None)
        for group, panel in self.panels.items():
            keys = [key for key, field in self.fields.items() if field.group == group]
            observed = [key for key in keys if key in snap["series"]]
            visible = [key for key in observed if key not in self.hidden_fields]
            panel.set_fields([(key, self.fields[key].label, key in visible) for key in observed])
            entries = [f'<span style="color:{self._series_color(group, key)}">'
                       f'{html.escape(SHORT_NAMES.get(key, self.fields[key].label))}</span>' for key in visible]
            if group == "state":
                self._render_states(snap, visible)
                panel.set_legend("Separate rows · Numbers are raw codes, not magnitudes · Hover for receipts"
                                 if visible else "No visible fields — choose Fields")
            else:
                panel.fit_values(bounds.get(group), (snap["left"], snap["right"]), self.window.follow)
                prefix = "⚠ Values outside held scale · " if panel.outside_scale else ""
                panel.set_legend(prefix + (" · ".join(entries) or "No visible fields — choose Fields"))
            panel.plot.setToolTip(CHART_HELP)
            panel.legend.setToolTip(", ".join(self.fields[key].label for key in visible))
            panel.plot.setLimits(xMin=snap["earliest"],
                                 xMax=max(snap["earliest"] + 1, snap["latest"]),
                                 minXRange=1)
        first = self.panels[self.focused_group or "temperature"].plot
        left, right = self.window.chart_range or (snap["left"], snap["right"])
        first.setXRange(left, max(left + 1, right), padding=0)
        self.reset_view_button.setEnabled(bool(snap["count"]))
        self.fit_y_button.setEnabled(bool(snap["series"]))
        self.span_choice.setToolTip("Custom time window; Reset view restores this preset."
                                    if self.window.chart_range else "Time window ending at the playback position.")
        mode = "Following latest" if self.window.follow else "Playing" if self.window.playing else "Inspecting history"
        self.range_status.setText(f"{mode}  ·  {elapsed_text(left)} – {elapsed_text(right)}  ·  {elapsed_text(right-left)} shown")
        self.range_status.setToolTip(self.range_status.text())
        self.latest_button.setText("Following latest" if self.window.follow else "Latest")

    def _render_states(self, snap, visible):
        panel = self.panels["state"]
        for annotation in self.state_annotations:
            panel.plot.removeItem(annotation)
        self.state_annotations.clear()
        self.state_rows = {key: len(visible) - i - 1 for i, key in enumerate(visible)}
        panel.plot.getAxis("left").setTicks([[(row, SHORT_NAMES.get(key, self.fields[key].label.replace(" (raw)", "")))
                                             for key, row in self.state_rows.items()]])
        panel.plot.setYRange(-.65, max(.65, len(visible) - .35), padding=0)
        panel.plot.setMinimumHeight(max(100, 28 * len(visible) + 36))
        palette = tokens(self.theme)
        thinning = snap.get("thinning")
        muted, danger = self._brush(palette["muted"]), self._brush(palette["danger"])
        colors = [self._brush(colour) for colour in series_colors(self.theme)]
        for key in visible:
            points = snap["series"][key]
            row = self.state_rows[key]
            x, values = plot_arrays(points, thinning)
            y = [row if math.isfinite(value) else float("nan") for value in values]
            error = "err" in key
            brushes = [muted if not math.isfinite(value) or value == 0 else
                       danger if error else colors[int(value) % len(colors)] for value in values]
            curve = self.curves[key]
            curve.setDownsampling(auto=False)
            # The pen goes in with the data: setPen on its own redraws every
            # symbol of the previous data first.
            curve.setData(x, y, connect="finite", pen=pg.mkPen(palette["plot_axis"], width=1),
                          symbol="s", symbolSize=5, symbolPen=None, symbolBrush=brushes)
            previous = None
            last_label = -math.inf
            separation = (snap["right"] - snap["left"]) * 36 / max(100, panel.plot.width() - 104)
            for point in points:
                if point["quality"] == "repeated_unverified":
                    previous = None
                    continue
                changed = (previous is None or point["value"] != previous["value"]
                           or gap(previous, point, thinning))
                if changed and point["t"] - last_label >= separation:
                    annotation = pg.TextItem(sample_value(point["value"]), color=palette["plot_label"], anchor=(0, 1.3))
                    annotation.setPos(point["t"], row)
                    panel.plot.addItem(annotation, ignoreBounds=True)
                    self.state_annotations.append(annotation)
                    last_label = point["t"]
                previous = point

    def inspect_samples(self, group, seconds):
        if not self.window.snap or time.monotonic() - self._hover_time < .04:
            return
        self._hover_time = time.monotonic()
        rows = []
        self.inspection = {}
        view = self.panels[group].plot.getViewBox()
        left, right = view.viewRange()[0]
        tolerance = 6 * (right - left) / max(1, view.width())
        thinning = self.window.snap.get("thinning")
        for key, points in self.window.snap["series"].items():
            field = self.fields.get(key)
            # Stored keys the session's registry does not chart have no panel.
            if field is None or field.group != group or key in self.hidden_fields:
                continue
            sample = nearest_sample(points, seconds, edge_tolerance=tolerance, thinning=thinning)
            self.inspection[key] = sample
            name = html.escape(field.label)
            if sample is None:
                rows.append(f"<tr><td>{name}</td><td colspan='2'>No comparable sample / gap</td></tr>")
            else:
                value = f'{sample_value(sample["value"])} {field.unit}'.strip()
                receipt = elapsed_text(sample["t"], 3)
                quality = "" if sample["quality"] == "observed" else " · " + sample["quality"]
                rows.append(f"<tr><td>{name}</td><td><b>{html.escape(value)}</b></td><td>{receipt}{html.escape(quality)}</td></tr>")
        for panel in self.panels.values():
            panel.crosshair.setPos(seconds)
            panel.crosshair.show()
        self.chart_help.setText(f"Cursor {elapsed_text(seconds, 3)} · Nearest actual samples and their receipt times; no interpolation")
        tooltip = (f"<b>{html.escape(self.panels[group].title)} · {elapsed_text(seconds, 3)}</b>"
                   "<br>Nearest samples; independent fields can have different receipt times."
                   "<table cellspacing='6'><tr><th align='left'>Field</th><th align='left'>Value</th>"
                   "<th align='left'>Receipt</th></tr>" + "".join(rows) + "</table>")
        QToolTip.showText(QCursor.pos(), tooltip, self.panels[group].plot)

    def clear_inspection(self):
        for panel in self.panels.values():
            panel.crosshair.hide()
        QToolTip.hideText()
        if hasattr(self, "chart_help"):
            self.chart_help.setText(CHART_HELP)

    def _render_markers(self, snap):
        pen = pg.mkPen(tokens(self.theme)["marker"], width=1, style=Qt.PenStyle.DotLine)
        for group, panel in self.panels.items():
            for marker in self.markers[group]:
                panel.plot.removeItem(marker)
            self.markers[group].clear()
            for event in snap["events"]:
                if event["kind"] in GAP_KINDS and snap["left"] <= event["t"] <= snap["right"]:
                    marker = pg.InfiniteLine(event["t"], angle=90, pen=pen)
                    panel.plot.addItem(marker, ignoreBounds=True)
                    self.markers[group].append(marker)

    def _render_activity(self, snap):
        incidents = len(snap["incidents"])
        self.incident_summary.setText(
            "No incidents detected in this session." if not incidents else
            f"{incidents} protected incident window(s). Open Incidents for the full list.")
        events = list(reversed(snap["events"][-12:]))
        self.activity_table.setRowCount(len(events))
        for i, event in enumerate(events):
            values = [f"{event['t']:.3f}s", event["kind"].replace("_", " "), event["detail"]]
            for j, value in enumerate(values):
                self.activity_table.setItem(i, j, QTableWidgetItem(value))

    def apply_theme(self, theme):
        """Recolour every existing curve; pens were baked in at creation time."""
        self.theme = theme
        for key, curve in self.curves.items():
            group = self.fields[key].group
            # State rows take their pen and brushes from each render.
            if group != "state":
                curve.setPen(pg.mkPen(self._series_color(group, key), width=1.7))
                curve.setSymbolBrush(self._series_color(group, key))
        pen = pg.mkPen(tokens(theme)["marker"], width=1, style=Qt.PenStyle.DotLine)
        for markers in self.markers.values():
            for marker in markers:
                marker.setPen(pen)
        for panel in self.panels.values():
            panel.crosshair.setPen(pg.mkPen(tokens(theme)["plot_label"], width=1, style=Qt.PenStyle.DashLine))
        for annotation in self.state_annotations:
            annotation.setColor(tokens(theme)["plot_label"])
