from pathlib import Path
from openpowerstation.gui import Window
from conftest import close_window, pump

def test_desktop_playback_fields_markers_and_capture(qtbot,tmp_path,demo_database):
    db=demo_database(tmp_path/"demo.sqlite",seconds=900)
    w=Window(tmp_path,db); qtbot.addWidget(w); w.show()
    assert pump(lambda:w.snap.get("count")==882,timeout=10)
    assert w.badge.text()=="SYNTHETIC DEMO"
    assert "SYNTHETIC DATA" in w.banner.text()
    assert w.incident_table.rowCount()==1
    assert w.coverage_table.rowCount()>25
    assert "24" in w.cards["bms_max_cell_temp"].value.text()
    assert w.raw_text.toPlainText()
    w.seek(435)  # approximately the synthetic anomaly
    assert pump(lambda:w.snap["right"]<400,timeout=6)
    assert w.snap["right"]<400
    w.latest()
    assert pump(lambda:w.snap["right"]==899,timeout=6)
    w.toggle_play()
    assert w.playing and not w.follow
    w.toggle_play()
    Path("artifacts").mkdir(exist_ok=True)
    assert w.grab().save("artifacts/desktop-tested.png")
    close_window(w)

def test_background_view_can_stop_only_matching_qualification(qtbot,tmp_path,packet):
    import json
    from openpowerstation.storage import Store
    from openpowerstation.recorder import Recorder
    from conftest import add
    path=tmp_path/'live.sqlite'
    with Store(path,reserve_bytes=0) as store:
        rec=Recorder(store)
        add(rec,packet(bms_max_cell_temp=23),0)
        (tmp_path/'qualification-run.json').write_text(json.dumps({'session_id':rec.sid}))
        w=Window(tmp_path,path); qtbot.addWidget(w); w.show()
        assert pump(lambda:w.snap.get('count')==1,timeout=6)
        assert w.badge.text()=='BACKGROUND RECORDER'
        assert not w.start_button.isEnabled()
        assert w.stop_button.isEnabled()
        w.stop_recording()
        assert (tmp_path/'qualification.stop').read_text()=='stop\n'
        close_window(w)


# --- sessions another process wrote ------------------------------------------

NS = 10**9


def open_window(qtbot, root, path):
    """A window on ``path`` that has drawn its first snapshot; tick() is the test's to call."""
    w = Window(root, path); qtbot.addWidget(w); w.show()
    w.timer.stop()
    assert pump(lambda: bool(w.snap) and not w.jobs, timeout=12)
    return w


def counting(monkeypatch):
    """Every snapshot query the window issues from here on."""
    import openpowerstation.gui as gui
    calls = []
    real = gui.snapshot

    def snapshot(*args, **kwargs):
        calls.append(args)
        return real(*args, **kwargs)

    monkeypatch.setattr(gui, "snapshot", snapshot)
    return calls


def ticks(w, count):
    for _ in range(count):
        w.tick()
        assert pump(lambda: not w.jobs)


def test_a_session_its_crashed_recorder_left_recording_reads_as_interrupted(qtbot, tmp_path, packet):
    """A crash or a power loss leaves 'recording' behind, and nothing finalizes it

    until a writer next opens the file. Three days on, it is not a live recorder,
    and nothing it says may keep the user from recording.
    """
    import time
    from openpowerstation.recorder import Recorder
    from openpowerstation.storage import Store
    from conftest import add
    path = tmp_path/'crashed.sqlite'
    with Store(path, reserve_bytes=0) as store:
        # Recorded on an earlier boot, whose monotonic clock means nothing now.
        rec = Recorder(store, utc_ns=time.time_ns() - (3*86_400 + 600)*NS, mono_ns=time.monotonic_ns() + 10**15)
        for i in range(60):
            add(rec, packet(seq=i + 1, bms_max_cell_temp=23), i)
    w = open_window(qtbot, tmp_path, path)
    assert w.snap['session']['status'] == 'recording'
    assert w.badge.text() == 'INTERRUPTED'
    assert 'never finalized' in w.banner.text() and '3 days' in w.banner.text()
    assert w.start_button.isEnabled()
    assert w.mark_button.isEnabled() and w.play_button.isEnabled()
    assert w.stop_button.text() == 'Stop recording' and not w.stop_button.isEnabled()
    # Ages run to the right edge of the view, as for any saved session.
    assert w.cards['bms_max_cell_temp'].age.text() == 'Last observed 0.0s ago'
    close_window(w)


def test_a_live_recorder_from_another_boot_is_timed_by_the_wall_clock(qtbot, tmp_path, packet):
    """Ages of another process's session cannot come from its monotonic clock."""
    import re
    import time
    from openpowerstation.recorder import Recorder
    from openpowerstation.storage import Store
    from conftest import add
    path = tmp_path/'live.sqlite'
    with Store(path, reserve_bytes=0) as store:
        # Its writer's monotonic clock reads far from this process's, as a
        # previous boot's does after a reboot.
        rec = Recorder(store, utc_ns=time.time_ns() - 90*NS, mono_ns=time.monotonic_ns() + 10**15)
        add(rec, packet(seq=1, bms_max_cell_temp=23), 0)
        add(rec, packet(seq=2, bms_max_cell_temp=24), 60)   # received 30 s ago
        w = open_window(qtbot, tmp_path, path)
        assert w.badge.text() == 'BACKGROUND RECORDER'
        age = w.cards['bms_max_cell_temp'].age.text()
        assert 29 <= float(re.fullmatch(r'Last observed ([\d.]+)s ago', age).group(1)) < 120, age
        close_window(w)


def test_a_recording_that_finishes_is_shown_finished_then_left_alone(qtbot, tmp_path, packet, monkeypatch):
    """Followed while written, drawn once more when it ends, then not queried every tick."""
    from openpowerstation.recorder import Recorder
    from openpowerstation.storage import Store
    from conftest import add
    path = tmp_path/'live.sqlite'
    with Store(path, reserve_bytes=0) as store:
        rec = Recorder(store)
        add(rec, packet(seq=1, bms_max_cell_temp=23), 0)
        w = open_window(qtbot, tmp_path, path)
        calls = counting(monkeypatch)
        assert w.badge.text() == 'BACKGROUND RECORDER'
        ticks(w, 2)
        assert len(calls) == 2
        rec.finish()
        ticks(w, 2)
        assert w.snap['session']['status'] == 'stopped'
        assert w.badge.text() == 'SAVED RECORDING'
        calls.clear()
        ticks(w, 4)
        assert calls == []
        close_window(w)


def test_a_saved_session_is_queried_again_only_once_a_writer_changes_it(qtbot, tmp_path, packet, monkeypatch):
    """Redrawing an unchanged long recording every 750 ms held the GUI thread for seconds."""
    import time
    from openpowerstation.recorder import Recorder
    from openpowerstation.storage import Store
    from conftest import add
    path = tmp_path/'saved.sqlite'
    with Store(path, reserve_bytes=0) as store:
        rec = Recorder(store)
        for i in range(30):
            add(rec, packet(seq=i + 1, bms_max_cell_temp=23), i)
        rec.finish()
    w = open_window(qtbot, tmp_path, path)
    calls = counting(monkeypatch)
    ticks(w, 4)
    assert calls == []
    # Another process marks an incident in it.
    with Store(path, reserve_bytes=0) as store:
        store.event(rec.sid, 10, time.time_ns(), 'manual', 'private note')
        store.incident(rec.sid, 10, 'manual')
    ticks(w, 1)
    assert pump(lambda: len(w.snap['incidents']) == 1)
    assert len(calls) == 1
    calls.clear()
    ticks(w, 3)
    assert calls == []
    close_window(w)


# --- what an export covers ----------------------------------------------------

def test_export_scope_is_the_button_pressed_not_a_hidden_selection(qtbot, tmp_path, packet, monkeypatch):
    """The Incidents table's selection used to decide every export, even off screen,

    and after a session change it named another session's incident in the same row.
    """
    import openpowerstation.exporting as exporting
    import openpowerstation.gui as gui
    from openpowerstation.recorder import Recorder
    from openpowerstation.storage import Store
    from conftest import add
    path = tmp_path/'two.sqlite'
    sessions = []
    with Store(path, reserve_bytes=0) as store:
        for n, start in enumerate((1_000_000_000_000_000_000, 1_000_000_100_000_000_000)):
            rec = Recorder(store, utc_ns=start, mono_ns=0)
            for i in range(30):
                add(rec, packet(seq=i + 1, bms_max_cell_temp=23 + n), i)
            rec.event('manual', 'private note', rec.start_utc + 10*NS, rec.start_mono + 10*NS)
            rec.finish()
            sessions.append(rec.sid)
    exported = []

    class Folder:
        @staticmethod
        def getExistingDirectory(*_args):
            return str(tmp_path/'out')

    def export_evidence(database, destination, *, sid=None, incident_id=None, include_raw=False):
        exported.append((sid, incident_id))
        return destination/'report.html', None

    monkeypatch.setattr(gui, 'QFileDialog', Folder)
    monkeypatch.setattr(exporting, 'export_evidence', export_evidence)
    w = open_window(qtbot, tmp_path, path)
    monkeypatch.setattr(w, 'show_error', lambda _text: None)
    incidents = w.pages['incidents']
    assert w.sid == sessions[1] and w.incident_table.rowCount() == 1
    w.incident_table.selectRow(0)
    chosen = w.selected_incident()
    assert chosen is not None
    assert pump(lambda: not w.jobs)
    # Overview's and Exported files' buttons export the session shown; only
    # the Incidents page's exports the incident selected there.
    w.export_button.click()
    w.pages['exports'].export_button.click()
    incidents.export_button.click()
    assert pump(lambda: len(exported) == 3 and not w.jobs)
    assert exported == [(sessions[1], None), (sessions[1], None), (sessions[1], chosen)]
    # Another session: its incident sits in the same row, and nobody chose it.
    w.session_choice.setCurrentIndex(w.session_choice.findData(sessions[0]))
    assert pump(lambda: w.snap['session']['id'] == sessions[0] and not w.jobs)
    assert w.incident_table.rowCount() == 1
    assert w.selected_incident() is None
    # With nothing selected there is no incident to export.
    assert not incidents.export_button.isEnabled()
    close_window(w)


# --- a Jackery session ---------------------------------------------------------

def test_a_jackery_session_charts_and_inspects_what_its_registry_records(qtbot, tmp_path, packet):
    """The Overview read the DP3 registry: Jackery voltage, frequency, time and output

    states were never charted, its temperature card stayed empty, and hovering any
    chart raised KeyError on the first Jackery-only key.
    """
    from openpowerstation.exporting import export_evidence
    from openpowerstation.recorder import Recorder
    from openpowerstation.storage import Store
    from conftest import add, jackery_session
    path = tmp_path/'both.sqlite'
    with Store(path, reserve_bytes=0) as store:
        dp3 = Recorder(store, utc_ns=1_700_000_000_000_000_000, mono_ns=0)
        for i in range(30):
            add(dp3, packet(seq=i + 1, bms_max_cell_temp=23), i)
        dp3.finish()
        jackery = jackery_session(store, seconds=600, step=3)
    w = open_window(qtbot, tmp_path, path)
    overview = w.pages['overview']
    assert w.snap['session']['id'] == jackery.sid
    for key in ('jackery_ac_voltage_v', 'jackery_ac_frequency_hz', 'jackery_output_time_h',
                'jackery_ac_output', 'jackery_dc_output'):
        assert key in overview.curves, key
    for group in ('voltage', 'frequency', 'duration'):
        assert overview.panels[group].isVisible(), group
    assert w.cards['cms_batt_temp'].value.text() == '25.1 °C'
    for group in ('temperature', 'power', 'soc', 'frequency', 'duration', 'state', 'voltage'):
        overview._hover_time = 0
        overview.inspect_samples(group, 300)
    assert overview.inspection['jackery_ac_voltage_v']['value'] == 120
    # Exported files names the device its summary records.
    export_evidence(path, tmp_path/'exports'/'OpenPowerstation-20260101-120000-000000')
    w.pages['exports'].reload()
    assert w.pages['exports'].bundle_table.item(0, 1).text() == 'Jackery recording'
    # And a DP3 session gets the DP3's again, with nothing of the Explorer's left.
    w.session_choice.setCurrentIndex(w.session_choice.findData(dp3.sid))
    assert pump(lambda: w.snap['session']['id'] == dp3.sid and not w.jobs)
    assert not any(key.startswith('jackery_') for key in overview.curves)
    assert not overview.panels['voltage'].isVisible()
    assert 'bms_max_cell_temp' in w.cards and '23.0' in w.cards['bms_max_cell_temp'].value.text()
    close_window(w)


def test_compacted_history_is_drawn_on_the_overview(qtbot, tmp_path):
    """Thinned to a frame a minute, every point used to sit between two line breaks.

    Above 100 samples the Overview draws no symbols, so the panels were blank.
    """
    import math
    from conftest import compacted_jackery
    path = compacted_jackery(tmp_path/'jackery.sqlite')
    w = open_window(qtbot, tmp_path, path)
    w.span_choice.setCurrentIndex(3)   # All
    assert pump(lambda: w.snap['left'] == w.snap['earliest'] and not w.jobs)
    overview = w.pages['overview']
    for key in ('pow_out_sum_w', 'bms_batt_soc', 'jackery_ac_voltage_v'):
        y = list(overview.curves[key].yData)
        assert len(y) > 100 and overview.curves[key].opts['symbol'] is None
        drawable = sum(1 for a, b in zip(y, y[1:]) if math.isfinite(a) and math.isfinite(b))
        assert drawable == sum(map(math.isfinite, y)) - 1, key
    # The inspector reaches as far as the line does: between two kept frames.
    overview._hover_time = 0
    overview.inspect_samples('power', w.snap['series']['pow_out_sum_w'][10]['t'] + 30)
    assert overview.inspection['pow_out_sum_w'] is not None
    close_window(w)
