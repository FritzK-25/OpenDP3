"""A raw frame is synced before it is decoded; its interpretation rides on the next sync.

Every captured frame cost two commits, each synced to disk: the raw frame, then
its interpretation. The raw commit is the evidence and stays synchronous=FULL:
the host runs from a battery-backed supply that can itself fail, and the frames
just before a power loss are the ones that explain it. The interpretation can
always be derived again from those bytes, so it commits at NORMAL and becomes
durable with the next frame's sync. A power loss can therefore leave only the
newest frame pending, which is the state a crash while decoding leaves anyway.
"""
import re
import sqlite3

import pytest

from conftest import add
from openpowerstation.decoder import decode_raw
from openpowerstation.protocol import parse_packet

FULL = 2
LEVELS = {"0": 0, "OFF": 0, "1": 1, "NORMAL": 1, "2": 2, "FULL": 2, "3": 3, "EXTRA": 3}
INTERPRETATION = ("UPDATE frames SET seq=", "UPDATE frames SET status='decoded'", "INSERT INTO measurements")


def commits(store, work):
    """``(synchronous level, statements)`` for every transaction ``work`` commits."""
    level = store.conn.execute("PRAGMA synchronous").fetchone()[0]
    statements = []
    store.conn.set_trace_callback(statements.append)
    try:
        work()
    finally:
        store.conn.set_trace_callback(None)
    found, current = [], []
    for sql in statements:
        text = sql.strip()
        match = re.fullmatch(r"PRAGMA synchronous\s*=\s*(\w+)", text, re.IGNORECASE)
        if match:
            level = LEVELS[match.group(1).upper()]
        elif text.upper().startswith("BEGIN"):
            current = []
        elif text.upper() == "COMMIT":
            found.append((level, current))
            current = []
        else:
            current.append(text)
    return found


def test_each_frame_costs_one_sync_and_its_raw_bytes_are_synced_first(evidence, packet):
    store, rec = evidence

    def capture():
        for seq in range(1, 11):
            add(rec, packet(seq=seq, bms_max_cell_temp=20 + seq), seq)
        for index in range(3):
            t = 20 + index
            rec.ingest_observation(b'{"observation":%d}' % index, {"properties": {}}, {"cms_batt_soc": 50.0},
                                   utc_ns=rec.start_utc + t * 10**9, mono_ns=rec.start_mono + t * 10**9)

    found = commits(store, capture)
    raw = [level for level, sql in found if any(s.startswith("INSERT INTO frames") for s in sql)]
    assert raw == [FULL] * 13
    interpreted = [level for level, sql in found if any(s.startswith(INTERPRETATION) for s in sql)]
    assert len(interpreted) == 13
    assert [level for level, _ in found if level >= FULL] == [FULL] * 13, found
    # Whatever writes next -- an event, a pin, maintenance -- is fully synced again.
    assert store.conn.execute("PRAGMA synchronous").fetchone()[0] == FULL


def test_a_failed_interpretation_leaves_the_raw_frame_and_full_sync(evidence, packet):
    store, rec = evidence
    raw = packet(seq=1, bms_max_cell_temp=23)
    add(rec, raw, 1)
    frame_id = store.conn.execute("SELECT id FROM frames").fetchone()[0]
    # Its measurements exist already, so a second interpretation cannot commit.
    with pytest.raises(sqlite3.IntegrityError):
        store.interpret(frame_id, parse_packet(raw), decode_raw(raw), duplicate=True)
    assert store.conn.execute("PRAGMA synchronous").fetchone()[0] == FULL
    assert not store.conn.in_transaction
    assert store.conn.execute("SELECT status FROM frames").fetchone()[0] == "decoded"


def test_events_pins_and_maintenance_stay_fully_synced(evidence, packet):
    store, rec = evidence

    def capture():
        add(rec, packet(seq=1, bms_max_cell_temp=23, errcode=0), 1)
        add(rec, packet(seq=2, bms_max_cell_temp=23, errcode=5), 2)
        rec.event("disconnected", "test", rec.start_utc + 3 * 10**9, 3 * 10**9)
        # Past the one-minute mark, so a maintenance pass runs too.
        add(rec, packet(seq=3, bms_max_cell_temp=24), 70)

    found = commits(store, capture)
    other = [(level, sql) for level, sql in found if not any(s.startswith(INTERPRETATION) for s in sql)]
    assert any(s.startswith("INSERT INTO events") for _, sql in other for s in sql)
    assert any(s.startswith("INSERT INTO incidents") for _, sql in other for s in sql)
    assert any("maintenance_progress" in s for _, sql in other for s in sql)
    assert {level for level, _ in other} == {FULL}, other
