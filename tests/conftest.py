import os
os.environ.setdefault("QT_QPA_PLATFORM","offscreen")
os.environ.setdefault("MPLCONFIGDIR",os.path.abspath("artifacts/matplotlib"))
import shutil
import time
import pytest
from openpowerstation.vendor.packet import Packet
from openpowerstation.vendor.pb.mr521_pb2 import DisplayPropertyUpload

@pytest.fixture
def packet():
    def make(seq=1, **values):
        return Packet(2,0x21,0xFE,0x15,DisplayPropertyUpload(**values).SerializeToString(),
                      seq=seq.to_bytes(4,"little")).to_bytes()
    return make

@pytest.fixture
def evidence(tmp_path):
    from openpowerstation.recorder import Recorder
    from openpowerstation.storage import Store
    with Store(tmp_path/"recording.sqlite",reserve_bytes=0) as store:
        recorder = Recorder(store,utc_ns=1_000_000_000_000_000_000,mono_ns=0)
        yield store,recorder

@pytest.fixture(autouse=True)
def no_modal_dialogs(monkeypatch):
    """Fail a test that opens a modal dialog instead of hanging the whole run.

    Offscreen, nobody can click the dialog away, so the run blocks until CI
    cancels it with no traceback. These are the GUI's modal entry points. Tests
    that expect an error patch show_error themselves, which takes precedence.
    """
    try:
        from PySide6.QtWidgets import QDialog, QFileDialog, QInputDialog, QMessageBox
    except ImportError:
        return
    def refuse(*args, **kwargs):
        pytest.fail(f"test opened a modal dialog: {args[1:3]!r}", pytrace=False)
    for name in ("warning", "critical", "information", "question"):
        monkeypatch.setattr(QMessageBox, name, refuse)
    for name in ("getOpenFileName", "getSaveFileName", "getExistingDirectory"):
        monkeypatch.setattr(QFileDialog, name, refuse)
    for name in ("getText", "getItem", "getInt", "getDouble"):
        monkeypatch.setattr(QInputDialog, name, refuse)
    monkeypatch.setattr(QDialog, "exec", refuse)

def add(rec,raw,t,wall_offset=0):
    rec.ingest(raw,rec.start_utc+int((t+wall_offset)*1e9),rec.start_mono+int(t*1e9))

def jackery_session(store, *, seconds, step, utc_ns=1_800_000_000_000_000_000, finished=True,
                    **properties):
    """A local-BLE Explorer session: one status reply every ``step`` seconds.

    The reply carries every field the desktop charts for an Explorer -- AC
    voltage and frequency, an estimated time, both output states -- unless
    ``properties`` overrides them. Unless ``finished`` is false, the session
    is then finished as its recorder would.
    """
    import json
    from openpowerstation.jackery_fields import map_properties
    from openpowerstation.recorder import Recorder
    recorder = Recorder(store, utc_ns=utc_ns, mono_ns=0, firmware="Jackery Explorer 1000 v2 TEST",
                        conditions="Jackery local BLE transport", expected_interval=step,
                        retain_days=None)
    for t in range(0, seconds, step):
        reply = {"rb": 80, "bt": 251, "op": 120 + t % 7, "ip": 0, "acov": 1200, "acohz": 60,
                 "oac": 1, "odc": 0, "ot": 55, **properties}
        fields = {"device": {"serial": "TEST"}, "properties": reply}
        recorder.ingest_observation(json.dumps(fields).encode(), fields, map_properties(reply),
                                    utc_ns=recorder.start_utc + t * 10**9,
                                    mono_ns=recorder.start_mono + t * 10**9)
    if finished:
        recorder.finish(mono_ns=recorder.start_mono + (seconds - step) * 10**9)
    return recorder

def compacted_jackery(path, hours=3):
    """An Explorer session polled every 30 s, then thinned as its recorder would.

    The downsampler keeps the first reply of each minute (and its extremes),
    the history every Jackery database holds after 48 hours. A steady load,
    so no extreme keeps a second reply: two minutes' kept frames are a
    minute apart.
    """
    from openpowerstation.jackery_fields import PEAK_KEYS
    from openpowerstation.storage import Store
    with Store(path, reserve_bytes=0) as store:
        recorder = jackery_session(store, seconds=hours * 3600, step=30, op=120)
        summary = store.downsample(recorder.start_utc + 10 * 86_400 * 10**9,
                                   bucket_seconds=60, peak_keys=PEAK_KEYS)
        assert summary["deleted"] > 0
    return path

def pump(predicate, timeout=8.0):
    """Spin the Qt event loop until predicate() is true. Returns its final value.

    Deliberately not qtbot.waitUntil: that goes through QTest.qWait, which aborts
    the process on Windows when one of the window's Job worker threads is still
    running. A plain processEvents loop is safe and just as deterministic.
    """
    from PySide6.QtWidgets import QApplication
    deadline = time.monotonic() + timeout
    while not predicate() and time.monotonic() < deadline:
        QApplication.processEvents()
        time.sleep(.005)
    QApplication.processEvents()
    return predicate()

def close_window(window, timeout=8.0):
    """Shut a Window down the way the application itself does."""
    window.timer.stop()
    assert pump(lambda: not window.jobs, timeout), "worker jobs did not finish"
    window.force_exit = True
    window.close()

@pytest.fixture(scope="session")
def demo_database(tmp_path_factory):
    """make_demo(path, seconds), built once per length and copied for each test.

    make_demo commits every frame with synchronous=FULL, about 1,800 fsyncs for
    the 900 s demo. That is under a second on Linux but several seconds on a
    Windows runner, paid again by every GUI test, and was most of the Windows
    suite's run time. Each test still gets its own file to change.

    Under pytest-xdist each worker is a session of its own. The workers' temp
    directories share a parent, so the first worker to need a length builds it
    there under a lock and the others copy it, rather than every worker
    building it at once on a contended disk.
    """
    import portalocker
    from openpowerstation.demo import make_demo
    root = tmp_path_factory.getbasetemp()
    if os.environ.get("PYTEST_XDIST_WORKER"):
        root = root.parent
    built = {}
    def copy(path, seconds=900):
        if seconds not in built:
            target = root/f"demo-{seconds}.sqlite"
            with portalocker.Lock(root/f"demo-{seconds}.lock", timeout=600):
                if not target.exists():
                    staging = tmp_path_factory.mktemp("demo")/"demo.sqlite"
                    os.replace(make_demo(staging, seconds=seconds), target)
            built[seconds] = target
        shutil.copyfile(built[seconds], path)
        return path
    return copy
