"""Pruned history goes back to the disk, and a full disk does not strand it.

The store runs with auto_vacuum=INCREMENTAL so it can shrink, but maintain()
never stepped its `PRAGMA incremental_vacuum(2000)`. The pragma frees one page
per step, so each pass gave 4 KiB back however much it had deleted. And a store
below its free-space reserve refused to open at all, so a collector restarted
then could never prune the history its own policy lets it drop, and stayed
stopped for good.
"""
import os
from types import SimpleNamespace

import pytest

from conftest import add
from openpowerstation import storage
from openpowerstation.recorder import Compaction, Recorder
from openpowerstation.storage import Store, StorageError

START = 1_000_000_000_000_000_000
DAY = 86_400
NS = 10**9
SLACK = 64 * 1024
RESERVE = 4 * 1024 * 1024


def history(store, sid, count, *, step=60, blob=2_000):
    """``count`` frames ``step`` s apart from the session start, each ``blob`` bytes of raw."""
    rows = [(sid, 0, START + t * NS, t * NS, t, START + t * NS, os.urandom(blob), "decoded", blob + 256)
            for t in range(0, count * step, step)]
    with store.conn:
        store.conn.executemany(
            "INSERT INTO frames(session_id,segment,utc_ns,mono_ns,t,retention_ns,raw,status,size) "
            "VALUES(?,?,?,?,?,?,?,?,?)", rows)


def free_bytes(store):
    page = store.conn.execute("PRAGMA page_size").fetchone()[0]
    return store.conn.execute("PRAGMA freelist_count").fetchone()[0] * page


def test_pruned_history_goes_back_to_the_filesystem(tmp_path, monkeypatch):
    monkeypatch.setattr(storage, "VACUUM_SLACK_BYTES", SLACK, raising=False)
    path = tmp_path / "vacuum.sqlite"
    with Store(path, reserve_bytes=0) as store:
        sid = store.session(utc_ns=START, mono_ns=0)
        history(store, sid, 3_000)
        store.conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
        grown = path.stat().st_size
        assert store.maintain(START + 30 * DAY * NS)["deleted"] == 3_000
        assert free_bytes(store) <= SLACK
        # The checkpoint that ends the pass hands the freed pages to the filesystem.
        assert path.stat().st_size < grown // 10, (grown, path.stat().st_size)


def test_every_pass_gives_back_space_however_small_its_budget(tmp_path, monkeypatch):
    monkeypatch.setattr(storage, "VACUUM_SLACK_BYTES", SLACK, raising=False)
    with Store(tmp_path / "budget.sqlite", reserve_bytes=0) as store:
        sid = store.session(utc_ns=START, mono_ns=0)
        history(store, sid, 3_000)
        with store.conn:
            store.conn.execute("DELETE FROM frames")
        freed = []
        while free_bytes(store) > SLACK:
            before = free_bytes(store)
            store.maintain(START + 60 * NS, time_budget=0)
            freed.append(before - free_bytes(store))
            assert len(freed) < 50, f"{len(freed)} passes freed only {sum(freed)} bytes"
        assert len(freed) > 1 and min(freed) > 4_096, freed


def test_free_pages_within_the_slack_are_kept_for_new_frames(tmp_path, monkeypatch):
    """Routine churn reuses its own pages; moving them to shrink the file is wasted writes."""
    monkeypatch.setattr(storage, "VACUUM_SLACK_BYTES", 1024 * 1024, raising=False)
    with Store(tmp_path / "churn.sqlite", reserve_bytes=0) as store:
        sid = store.session(utc_ns=START, mono_ns=0)
        history(store, sid, 200)
        with store.conn:
            store.conn.execute("DELETE FROM frames WHERE t<?", (100 * 60,))
        kept = free_bytes(store)
        assert 0 < kept <= 1024 * 1024
        store.maintain(START + 200 * 60 * NS)
        assert free_bytes(store) == kept


class Disk:
    """A partition holding only this database, one byte short of the reserve.

    Its free space rises and falls with the database's own files, so pruning
    and returning pages is what brings it back above the reserve.
    """

    def __init__(self, path, reserve):
        self.path = path
        self.capacity = self.used() + reserve - 1

    def used(self):
        return sum(p.stat().st_size for p in self.path.parent.glob(self.path.name + "*")
                   if not p.name.endswith(".lock"))

    def __call__(self, _):
        return SimpleNamespace(free=self.capacity - self.used())


def recording(path, *, step=60, count=3_000):
    with Store(path, reserve_bytes=0) as store:
        sid = store.session(utc_ns=START, mono_ns=0)
        history(store, sid, count, step=step)
        return sid


def test_a_store_below_its_reserve_prunes_then_records_again(tmp_path, monkeypatch, packet, capsys):
    path = tmp_path / "recording.sqlite"
    recording(path)
    disk = Disk(path, RESERVE)
    monkeypatch.setattr("openpowerstation.storage.shutil.disk_usage", disk)
    # A month later all of it is past the seven-day limit.
    with Store(path, reserve_bytes=RESERVE) as store:
        rec = Recorder(store, utc_ns=START + 30 * DAY * NS, mono_ns=0)
        assert disk(None).free >= RESERVE
        add(rec, packet(seq=1, bms_max_cell_temp=23), 1)
        assert store.conn.execute("SELECT COUNT(*) FROM frames").fetchone()[0] == 1
    out = capsys.readouterr().out
    assert "[maintenance] recording.sqlite: below the free-space reserve at start; " \
           "retention deleted 3000 frames" in out, out


def test_giving_space_back_never_needs_room_for_all_of_it_at_once(tmp_path, monkeypatch):
    """Moving a page writes it to the WAL first, and a disk below its reserve has little room.

    The history still kept sits at the end of the file, behind the expired
    history pruning frees, so giving that space back moves every page of it.
    One vacuum of the whole freelist would first grow the WAL by all of them.
    """
    path = tmp_path / "recording.sqlite"
    recording(path, count=20_000)
    monkeypatch.setattr("openpowerstation.storage.shutil.disk_usage", Disk(path, RESERVE))
    wal = path.with_name(path.name + "-wal")
    largest = [0]

    def watch(_):
        if wal.exists():
            largest[0] = max(largest[0], wal.stat().st_size)

    with Store(path, reserve_bytes=RESERVE) as store:
        store.conn.set_trace_callback(watch)
        # Seven days after the last quarter began: the first three quarters expired.
        Recorder(store, utc_ns=START + (15_000 * 60 + 7 * DAY) * NS, mono_ns=0)
        store.conn.set_trace_callback(None)
        assert store.conn.execute("SELECT COUNT(*) FROM frames").fetchone()[0] == 5_000
        assert free_bytes(store) == 0
        kept = path.stat().st_size
    # SQLite's automatic checkpoint holds the WAL near 1,000 pages between steps.
    assert largest[0] < 8 * 1024 * 1024 < kept, (largest[0], kept)


def test_a_store_below_its_reserve_with_nothing_it_may_drop_does_not_record(tmp_path, monkeypatch):
    path = tmp_path / "recording.sqlite"
    recording(path)
    monkeypatch.setattr("openpowerstation.storage.shutil.disk_usage", Disk(path, RESERVE))
    with Store(path, reserve_bytes=RESERVE) as store:
        # A day on, every frame is inside the retention policy.
        with pytest.raises(StorageError, match="reserve"):
            Recorder(store, utc_ns=START + DAY * NS, mono_ns=0)
        assert store.conn.execute("SELECT COUNT(*) FROM sessions").fetchone()[0] == 1
        assert store.conn.execute("SELECT COUNT(*) FROM frames").fetchone()[0] == 3_000


def test_a_thinned_store_below_its_reserve_keeps_to_its_own_policy(tmp_path, monkeypatch):
    """The Jackery keeps history forever, thinned; a full disk must not age it out."""
    path = tmp_path / "jackery.sqlite"
    recording(path, step=3)
    monkeypatch.setattr("openpowerstation.storage.shutil.disk_usage", Disk(path, RESERVE))
    with Store(path, reserve_bytes=RESERVE) as store:
        Recorder(store, utc_ns=START + 30 * DAY * NS, mono_ns=0, retain_days=None, compaction=Compaction())
        times = [r[0] for r in store.conn.execute("SELECT t FROM frames ORDER BY t")]
        assert times == list(range(0, 3_000 * 3, 60))
