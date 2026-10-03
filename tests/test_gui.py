from pathlib import Path
from PySide6.QtCore import Qt
from opendp3.gui import Window
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
    from opendp3.storage import Store
    from opendp3.recorder import Recorder
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
