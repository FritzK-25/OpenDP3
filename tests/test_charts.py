"""Chart gestures travel through Qt, including the axis event-forwarding path."""
import threading

import pytest
from PySide6.QtCore import QEvent, QPoint, QPointF, Qt
from PySide6.QtGui import QMouseEvent, QWheelEvent
from PySide6.QtWidgets import QApplication

from opendp3.gui import Window
from opendp3.ui.charts import elapsed_text, nearest_sample, sample_value, value_range
from opendp3.ui.widgets import ChartPanel
from conftest import close_window, pump


@pytest.fixture
def viewer(qtbot, tmp_path, demo_database):
    window = Window(tmp_path, demo_database(tmp_path / "charts.sqlite"))
    qtbot.addWidget(window)
    window.show()
    window.timer.stop()
    assert pump(lambda: bool(window.snap) and not window.jobs)
    yield window
    close_window(window)


def wheel(widget, position=None, delta=120, modifiers=Qt.KeyboardModifier.ControlModifier):
    position = QPointF(position or widget.rect().center())
    event = QWheelEvent(position, widget.mapToGlobal(position.toPoint()),
                        QPoint(), QPoint(0, delta), Qt.MouseButton.NoButton,
                        modifiers, Qt.ScrollPhase.NoScrollPhase, False)
    QApplication.sendEvent(widget, event)


def chart_position(panel, area="canvas"):
    item = panel.plot.getViewBox() if area == "canvas" else panel.plot.getAxis(area)
    return panel.plot.mapFromScene(item.sceneBoundingRect().center())


def drag(panel, dx, dy, button=Qt.MouseButton.LeftButton):
    viewport = panel.plot.viewport()
    start = QPointF(chart_position(panel))
    for kind, pos, pressed in [
        (QEvent.Type.MouseButtonPress, start, button),
        (QEvent.Type.MouseMove, start + QPointF(dx / 2, dy / 2), button),
        (QEvent.Type.MouseMove, start + QPointF(dx, dy), button),
        (QEvent.Type.MouseButtonRelease, start + QPointF(dx, dy), Qt.MouseButton.NoButton),
    ]:
        event = QMouseEvent(kind, pos, viewport.mapToGlobal(pos.toPoint()),
                            Qt.MouseButton.NoButton if kind == QEvent.Type.MouseMove else button,
                            pressed, Qt.KeyboardModifier.NoModifier)
        QApplication.sendEvent(viewport, event)


@pytest.mark.parametrize("group", ["temperature", "power", "soc", "state"])
@pytest.mark.parametrize("area", ["canvas", "left", "bottom"])
def test_ctrl_wheel_only_zooms_time_with_held_scales(viewer, group, area):
    panels = viewer.pages["overview"].panels
    for key, panel in panels.items():
        if key != "state":
            panel.scale_choice.setCurrentIndex(2)
    scrollbar = viewer.pages["overview"].chart_scroll.verticalScrollBar()
    scroll_position = scrollbar.value()
    before = {key: panel.plot.viewRange() for key, panel in panels.items()}
    panel = panels[group]
    wheel(panel.plot.viewport(), chart_position(panel, area))
    zoomed = panel.plot.viewRange()[0]
    assert zoomed[1] - zoomed[0] < before[group][0][1] - before[group][0][0]
    assert not viewer.follow
    assert pump(lambda: not viewer.chart_timer.isActive() and not viewer.jobs)
    viewer.refresh()
    assert pump(lambda: not viewer.jobs)
    for key, panel in panels.items():
        assert panel.plot.viewRange()[0] == pytest.approx(zoomed)
        assert panel.plot.viewRange()[1] == pytest.approx(before[key][1])
    assert viewer.snap["left"] == pytest.approx(zoomed[0])
    assert viewer.snap["right"] == pytest.approx(zoomed[1])
    assert scrollbar.value() == scroll_position


def test_drag_loads_earlier_data_without_moving_value_axes(viewer):
    panels = viewer.pages["overview"].panels
    panel = panels["power"]
    wheel(panel.plot.viewport(), chart_position(panel), 240)
    assert pump(lambda: not viewer.chart_timer.isActive() and not viewer.jobs)
    old_left, old_right = panel.plot.viewRange()[0]
    values = panel.plot.viewRange()[1]
    drag(panel, 80, 15)
    assert pump(lambda: not viewer.chart_timer.isActive() and not viewer.jobs)
    left, right = panel.plot.viewRange()[0]
    assert left < old_left and right < old_right
    assert right - left == pytest.approx(old_right - old_left)
    assert panel.plot.viewRange()[1] == values
    assert viewer.snap["left"] == pytest.approx(left)
    assert min(p["t"] for p in viewer.snap["series"]["pow_in_sum_w"]) < old_left
    for other in panels.values():
        assert other.plot.viewRange()[0] == pytest.approx((left, right))
    drag(panel, -80, -15, Qt.MouseButton.RightButton)
    assert panel.plot.viewRange()[0] == pytest.approx((left, right))


def test_zoom_bounds_and_reset(viewer, qtbot):
    overview = viewer.pages["overview"]
    panel = overview.panels["temperature"]
    for _ in range(10):
        wheel(panel.plot.viewport(), chart_position(panel), 1200)
    assert panel.plot.viewRange()[0][1] - panel.plot.viewRange()[0][0] == pytest.approx(1)
    for _ in range(10):
        wheel(panel.plot.viewport(), chart_position(panel), -1200)
    assert panel.plot.viewRange()[0] == pytest.approx((0, 899))
    qtbot.mouseClick(overview.reset_view_button, Qt.MouseButton.LeftButton)
    assert pump(lambda: not viewer.jobs)
    assert not viewer.follow and viewer.chart_range is None
    assert panel.plot.viewRange()[0] == pytest.approx((299, 899))
    viewer.latest()
    assert pump(lambda: not viewer.jobs)
    assert viewer.follow and viewer.cursor is None


def test_wheel_does_not_change_selectors_or_playback(viewer):
    overview = viewer.pages["overview"]
    for combo in (overview.session_choice, overview.span_choice):
        index = combo.currentIndex()
        wheel(combo, delta=-120)
        assert combo.currentIndex() == index
    value = overview.slider.value()
    wheel(overview.slider, delta=-120)
    assert overview.slider.value() == value
    assert viewer.follow


@pytest.mark.parametrize("action", ["preset", "seek", "latest", "session", "play"])
def test_explicit_navigation_leaves_custom_window(viewer, action):
    overview = viewer.pages["overview"]
    panel = overview.panels["soc"]
    wheel(panel.plot.viewport(), chart_position(panel))
    assert viewer.chart_range is not None
    if action == "preset":
        overview.span_choice.setCurrentIndex(3)
    elif action == "seek":
        viewer.seek(500)
    elif action == "latest":
        viewer.latest()
    elif action == "session":
        viewer.select_session(0)
    else:
        viewer.toggle_play()
    assert viewer.chart_range is None
    assert pump(lambda: not viewer.jobs)


def test_inflight_snapshot_cannot_overwrite_new_zoom(viewer, monkeypatch):
    import opendp3.gui as gui
    original = gui.snapshot
    started, release = threading.Event(), threading.Event()

    def delayed(*args, **kwargs):
        started.set()
        assert release.wait(5)
        return original(*args, **kwargs)

    rendered = []
    render = viewer.render

    def track(snap):
        rendered.append((snap["left"], snap["right"]))
        render(snap)

    monkeypatch.setattr(gui, "snapshot", delayed)
    monkeypatch.setattr(viewer, "render", track)
    viewer.refresh()
    try:
        assert pump(started.is_set)
        panel = viewer.pages["overview"].panels["temperature"]
        wheel(panel.plot.viewport(), chart_position(panel))
        target = viewer.chart_range
    finally:
        release.set()
    assert pump(lambda: not viewer.chart_timer.isActive() and not viewer.jobs)
    assert rendered
    assert all(bounds == pytest.approx(target) for bounds in rendered)


@pytest.mark.parametrize("group,bounds", [
    ("temperature", (24, 24)), ("power", (0, 0)), ("power", (-350, 1000)),
    ("soc", (61, 98)), ("soc", (-10, 130)), ("state", (0, 999)),
    ("temperature", (-40, 120)), ("state", (0, 0)),
])
def test_scales_include_all_reported_values(group, bounds):
    low, high = value_range(group, bounds)
    assert low <= bounds[0] <= bounds[1] <= high
    assert high > low
    if group == "temperature":
        assert high - low >= 10
    if group == "soc":
        assert low <= 0 and high >= 100
    if group == "power":
        assert low <= 0 <= high


def test_hold_freezes_values_and_warns_instead_of_silently_hiding_extrema(qtbot):
    panel = ChartPanel("temperature", "Temperature")
    qtbot.addWidget(panel)
    panel.fit_values((23, 24))
    original = panel.plot.viewRange()[1]
    panel.scale_choice.setCurrentIndex(2)
    panel.fit_values((24, 25))
    assert panel.plot.viewRange()[1] == original
    panel.fit_values((-10, 25))
    assert panel.plot.viewRange()[1] == original
    assert panel.outside_scale
    panel.scale_choice.setCurrentIndex(0)
    panel.fit_values((-10, 25))
    assert panel.plot.viewRange()[1][0] <= -10
    panel.fit_values((23, 24))
    assert panel.plot.viewRange()[1] == original


def test_fit_y_keeps_time_and_percentage_context(viewer, qtbot):
    overview = viewer.pages["overview"]
    panel = overview.panels["temperature"]
    panel.scale_choice.setCurrentIndex(1)
    wheel(panel.plot.viewport(), chart_position(panel), 480)
    assert pump(lambda: not viewer.chart_timer.isActive() and not viewer.jobs)
    target = viewer.chart_range
    old_y = panel.plot.viewRange()[1]
    qtbot.mouseClick(overview.fit_y_button, Qt.MouseButton.LeftButton)
    assert pump(lambda: not viewer.jobs)
    assert panel.plot.viewRange()[0] == pytest.approx(target)
    y = panel.plot.viewRange()[1]
    assert y[1] - y[0] < old_y[1] - old_y[0]
    soc = overview.panels["soc"]
    assert soc.scale_choice.currentData() == "fit"
    assert soc.plot.viewRange()[1][1] - soc.plot.viewRange()[1][0] < 100


def test_live_refresh_preserves_inspection_until_latest(qtbot, tmp_path, packet):
    from opendp3.recorder import Recorder
    from opendp3.storage import Store
    from conftest import add

    with Store(tmp_path / "live.sqlite", reserve_bytes=0) as store:
        recorder = Recorder(store)
        for i in range(6):
            add(recorder, packet(seq=i + 1, bms_max_cell_temp=23 + i % 2), 120 + i)
        window = Window(tmp_path, store.path)
        qtbot.addWidget(window)
        window.show()
        window.timer.stop()
        try:
            assert pump(lambda: bool(window.snap) and not window.jobs)
            panel = window.pages["overview"].panels["temperature"]
            wheel(panel.plot.viewport(), chart_position(panel), 240)
            assert pump(lambda: not window.chart_timer.isActive() and not window.jobs)
            target = panel.plot.viewRange()[0]
            assert target[0] >= 120
            add(recorder, packet(seq=7, bms_max_cell_temp=50), 126)
            window.refresh()
            assert pump(lambda: not window.jobs)
            assert window.snap["latest"] == 126
            assert panel.plot.viewRange()[0] == pytest.approx(target)
            window.latest()
            assert pump(lambda: not window.jobs)
            assert panel.plot.viewRange()[0] == pytest.approx((120, 126))
            assert panel.plot.viewRange()[1][1] >= 50
        finally:
            close_window(window)


def test_empty_scale_does_not_prevent_first_reading_from_fitting(qtbot):
    panel = ChartPanel("temperature", "Temperature")
    qtbot.addWidget(panel)
    panel.fit_values(None)
    panel.fit_values((24, 24))
    low, high = panel.plot.viewRange()[1]
    assert 0 < low < 24 < high < 40


def test_short_window_scrolls_complete_charts_without_double_wheel_action(viewer):
    overview = viewer.pages["overview"]
    viewer.resize(1500, 1000)
    QApplication.processEvents()
    viewer.resize(viewer.minimumSize())
    QApplication.processEvents()
    scroll = overview.chart_scroll
    assert scroll.verticalScrollBar().maximum() > 0
    for panel in overview.panels.values():
        assert panel.rect().contains(panel.plot.geometry())
        assert panel.plot.width() <= scroll.viewport().width()
    bottom = overview.panels["state"]
    scroll.ensureWidgetVisible(bottom)
    QApplication.processEvents()
    position = scroll.verticalScrollBar().value()
    assert position > 0
    before = bottom.plot.viewRange()[0]
    wheel(bottom.plot.viewport(), chart_position(bottom))
    after = bottom.plot.viewRange()[0]
    assert after[1] - after[0] < before[1] - before[0]
    assert scroll.verticalScrollBar().value() == position


@pytest.mark.parametrize("area", ["canvas", "left", "bottom"])
def test_plain_wheel_scrolls_instead_of_changing_graphs(viewer, area):
    viewer.resize(viewer.minimumSize())
    QApplication.processEvents()
    overview = viewer.pages["overview"]
    panel = overview.panels["temperature"]
    before = panel.plot.viewRange()
    scrollbar = overview.chart_scroll.verticalScrollBar()
    assert scrollbar.maximum() > 0
    wheel(panel.plot.viewport(), chart_position(panel, area), -120, Qt.KeyboardModifier.NoModifier)
    QApplication.processEvents()
    assert scrollbar.value() > 0
    assert panel.plot.viewRange() == before
    assert viewer.chart_range is None and viewer.follow


def test_error_codes_cannot_flatten_operating_states(viewer):
    import numpy as np
    overview = viewer.pages["overview"]
    panel = overview.panels["state"]
    assert "999" in [item.toPlainText() for item in overview.state_annotations]
    low, high = panel.plot.viewRange()[1]
    assert high - low < 5
    state_row = overview.state_rows["cms_bms_run_state"]
    error_row = overview.state_rows["errcode"]
    assert abs(state_row - error_row) >= 1
    for key, row in overview.state_rows.items():
        y = overview.curves[key].getOriginalDataset()[1]
        assert np.all(y[np.isfinite(y)] == row)
    # A zero code is still reported; it is not an unknown/missing row.
    assert "0" in [item.toPlainText() for item in overview.state_annotations]


def test_field_visibility_and_scale_modes_expose_soc_without_soh_flattening_it(viewer):
    overview = viewer.pages["overview"]
    panel = overview.panels["soc"]
    before = panel.plot.viewRange()[0]
    assert not overview.curves["cms_batt_soh"].isVisible()
    original_y = panel.plot.viewRange()[1]
    assert original_y[1] - original_y[0] < 10
    # Include SOH on purpose, then remove it through the same menu.
    panel.field_actions["cms_batt_soh"].trigger()
    y = panel.plot.viewRange()[1]
    assert y[1] - y[0] > original_y[1] - original_y[0]
    panel.field_actions["cms_batt_soh"].trigger()
    assert not overview.curves["cms_batt_soh"].isVisible()
    low, high = panel.plot.viewRange()[1]
    assert high - low < 10
    assert panel.plot.viewRange()[0] == before
    # Ordinary refresh must not put hidden fields back into the scale.
    viewer.refresh()
    assert pump(lambda: not viewer.jobs)
    assert not overview.curves["cms_batt_soh"].isVisible()
    assert panel.plot.viewRange()[1] == [low, high]
    panel.scale_choice.setCurrentIndex(1)
    assert panel.plot.viewRange()[1] == [0, 100]


@pytest.mark.parametrize("group", ["temperature", "power", "soc", "state"])
def test_focus_gives_inspection_space_and_preserves_time_on_return(viewer, qtbot, group):
    overview = viewer.pages["overview"]
    panel = overview.panels[group]
    qtbot.mouseClick(panel.focus_button, Qt.MouseButton.LeftButton)
    QApplication.processEvents()
    assert overview.focused_group == group
    assert panel.plot.getViewBox().height() > 300
    assert not overview.cards_widget.isVisible()
    assert all(not other.isVisible() for key, other in overview.panels.items() if key != group)
    wheel(panel.plot.viewport(), chart_position(panel))
    assert pump(lambda: not viewer.chart_timer.isActive() and not viewer.jobs)
    target = panel.plot.viewRange()[0]
    qtbot.mouseClick(overview.all_charts_button, Qt.MouseButton.LeftButton)
    QApplication.processEvents()
    assert overview.cards_widget.isVisible()
    assert overview.focused_group is None
    for other in overview.panels.values():
        assert other.isVisible()
        assert other.plot.viewRange()[0] == pytest.approx(target)


def test_fit_data_recovers_after_panning_past_anomaly(viewer):
    overview = viewer.pages["overview"]
    panel = overview.panels["temperature"]
    assert panel.scale_choice.currentData() == "fit"
    old_y = panel.plot.viewRange()[1]
    panel.plot.setXRange(600, 750, padding=0)
    viewer.inspect_chart_range((600, 750))
    assert pump(lambda: not viewer.chart_timer.isActive() and not viewer.jobs)
    low, high = panel.plot.viewRange()[1]
    assert high - low < (old_y[1] - old_y[0]) / 2
    assert panel.plot.viewRange()[0] == pytest.approx((600, 750))


def test_inspector_preserves_actual_raw_value_and_receipt(viewer):
    overview = viewer.pages["overview"]
    overview.inspect_samples("state", 400.2)
    sample = overview.inspection["errcode"]
    assert sample["value"] == 999
    assert sample["t"] == 400
    assert all(panel.crosshair.value() == 400.2 for panel in overview.panels.values())


@pytest.mark.parametrize("gap", ["time", "segment", "repeat"])
def test_sample_inspection_does_not_interpolate_or_bridge_gaps(gap):
    points = [dict(t=0, value=20, segment=1, quality="observed"),
              dict(t=10, value=30, segment=1, quality="observed")]
    if gap == "time":
        points[1]["t"] = 60
    elif gap == "segment":
        points[1]["segment"] = 2
    else:
        points[1]["quality"] = "repeated_unverified"
    assert nearest_sample(points, 5) is None
    assert nearest_sample(points, -1) is None
    assert nearest_sample(points, 61) is None
    assert nearest_sample(points, 0)["value"] == 20


def test_elapsed_labels_are_readable_across_hours_and_fractional_seconds():
    assert elapsed_text(3661) == "1:01:01"
    assert elapsed_text(59.9999, 2) == "01:00.00"
    assert elapsed_text(3.125, 3) == "00:03.125"


def test_isolated_sample_remains_inspectable_with_pixel_tolerance():
    points = [dict(t=10, value=24, segment=1, quality="observed")]
    assert nearest_sample(points, 10.002, edge_tolerance=.005) is points[0]
    assert nearest_sample(points, 10.01, edge_tolerance=.005) is None
    assert sample_value(4294967295.0) == "4294967295"


def test_idle_drift_and_single_samples_are_visible_by_default(qtbot, tmp_path, packet):
    from opendp3.recorder import Recorder
    from opendp3.storage import Store
    from conftest import add

    with Store(tmp_path / "idle.sqlite", reserve_bytes=0) as store:
        recorder = Recorder(store, synthetic=True)
        for i in range(60):
            add(recorder, packet(seq=i + 1, bms_batt_soc=79.72 - .09 * i / 59,
                                 bms_batt_soh=100, pow_get_bms=-18 - i % 4,
                                 pow_in_sum_w=0, bms_max_cell_temp=24), i)
        window = Window(tmp_path, store.path)
        qtbot.addWidget(window)
        window.show()
        window.timer.stop()
        try:
            assert pump(lambda: bool(window.snap) and not window.jobs)
            page = window.pages["overview"]
            soc = page.panels["soc"].plot.viewRange()[1]
            assert soc[0] <= 79.63 and soc[1] >= 79.72
            assert soc[1] - soc[0] < .5
            assert not page.curves["bms_batt_soh"].isVisible()
            power = page.panels["power"].plot.viewRange()[1]
            assert power[0] <= -21 and power[1] >= 0
            assert power[1] - power[0] < 50
            page.panels["temperature"].plot.setXRange(58.25, 59.25, padding=0)
            window.inspect_chart_range(page.panels["temperature"].plot.viewRange()[0])
            assert pump(lambda: not window.chart_timer.isActive() and not window.jobs)
            assert page.curves["bms_max_cell_temp"].opts["symbol"] == "o"
        finally:
            close_window(window)
