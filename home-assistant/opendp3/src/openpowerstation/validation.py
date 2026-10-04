"""Offline decoder replay verification; never connects to a device."""
import json
from pathlib import Path

from .decoder import decode_raw
from .jackery_fields import map_properties
from .protocol import ProtocolError
from .storage import read_db
from . import DECODER_VERSION


def _verify_observation(raw: bytes, expected: dict):
    """Re-map a stored observation frame and compare it with what was recorded.

    Observation frames (the Jackery transport) hold decoded JSON rather than a
    DP3 packet, so ``parse_packet`` rejects every one of them and the whole
    database used to report as invalid.  The analogous check is to re-run the
    property mapping over the stored JSON.

    A stored value that no longer re-maps to itself is a real mismatch.  A
    mapping that now yields *more* fields than were recorded is not: the mapper
    has gained fields over time, and the bridge already backfills them from this
    same stored JSON.  Report that separately instead of failing on it.
    """
    remapped = map_properties(json.loads(raw.decode("utf-8")).get("properties", {}))
    contradicted = any(remapped.get(key) != value for key, value in expected.items())
    return contradicted, len(remapped) > len(expected)


def verify_recording(path, sid=None):
    checked = unknown = invalid = mismatches = pending = 0
    observations = widened = 0
    with read_db(Path(path)) as db:
        versions = [row[0] for row in db.execute('SELECT DISTINCT decoder FROM sessions WHERE (? IS NULL OR id=?)',(sid,sid))]
        for row in db.execute("SELECT id,raw,status FROM frames WHERE (? IS NULL OR session_id=?) ORDER BY id",(sid,sid)):
            if row["status"] == "pending":
                pending += 1
                continue
            raw = bytes(row["raw"])
            expected = dict(db.execute("SELECT key,value FROM measurements WHERE frame_id=?", (row["id"],)))
            # DP3 packets start with 0xAA; anything else is an observation frame.
            if raw[:1] != b"\xaa":
                try:
                    contradicted, wider = _verify_observation(raw, expected)
                except (ValueError, UnicodeDecodeError, AttributeError):
                    invalid += 1
                    if row["status"] != "invalid_packet":
                        mismatches += 1
                    continue
                checked += 1
                observations += 1
                mismatches += contradicted
                widened += wider
                continue
            try:
                decoded = decode_raw(raw)
            except ProtocolError:
                invalid += 1
                if row["status"] != "invalid_packet":
                    mismatches += 1
                continue
            checked += 1
            if decoded.measurements != expected:
                mismatches += 1
            if decoded.status != "decoded":
                unknown += 1
    return {"checked":checked,"unknown":unknown,"invalid":invalid,"pending_after_interruption":pending,"mismatches":mismatches,
            "observations":observations,"observations_remapped_wider":widened,
            'stored_decoders':versions,'current_decoder':DECODER_VERSION,
            'decoder_changed':any(v != DECODER_VERSION for v in versions)}
