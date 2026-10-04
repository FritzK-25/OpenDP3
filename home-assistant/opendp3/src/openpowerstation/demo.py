"""Deterministic synthetic observations. Never opened as a live-device session."""
import math
from pathlib import Path
from .recorder import Recorder
from .storage import Store
from .vendor.packet import Packet
from .vendor.pb.mr521_pb2 import DisplayPropertyUpload

def make_demo(path: Path, seconds=900) -> Path:
    path = Path(path)
    if path.exists():
        raise FileExistsError("Demo destination exists; choose a new file.")
    start = 1_788_084_000_000_000_000
    mono = 10_000_000_000
    with Store(path) as store:
        rec = Recorder(store, utc_ns=start, mono_ns=mono, synthetic=True,
                       firmware="SYNTHETIC — no device firmware",
                       conditions="Generated demonstration; not evidence of a device fault.")
        rec.event("connected", "SYNTHETIC Bluetooth connection.", start, mono)
        for t in range(seconds):
            if t == 430:
                rec.event("disconnected", "SYNTHETIC connection loss, not a confirmed reboot.",
                          start+t*10**9, mono+t*10**9)
            if 430 <= t < 448:
                continue
            if t == 448:
                rec.event("connected", "SYNTHETIC reconnection.", start+t*10**9, mono+t*10**9)
            temp = 0 if 390 <= t <= 392 else int(23 + 2*math.sin(t/110))
            fault = 999 if 391 <= t < 415 else 0  # Intentionally NOT an Error 036 mapping.
            msg = DisplayPropertyUpload(
                bms_max_cell_temp=temp, bms_min_cell_temp=temp-1,
                bms_batt_soc=min(95,60+t/150), cms_batt_soc=min(95,60+t/150), cms_batt_soh=98,
                pow_get_pv_h=800+80*math.sin(t/25), pow_get_pv_l=180+30*math.sin(t/18),
                pow_get_ac_in=0, pow_get_ac_lv_out=-350, pow_get_ac_hv_out=0,
                pow_get_bms=0 if fault else 630+60*math.sin(t/25),
                pow_in_sum_w=980+80*math.sin(t/25), pow_out_sum_w=350,
                cms_bms_run_state=0 if fault else 1, errcode=fault)
            raw = Packet(2, 0x21, 0xFE, 0x15, msg.SerializeToString(),
                         seq=(t+1).to_bytes(4,"little")).to_bytes()
            rec.ingest(raw, start+t*10**9, mono+t*10**9)
            if t == 389:
                rec.event("manual", "SYNTHETIC example incident marker.", start+t*10**9, mono+t*10**9)
        rec.finish(mono_ns=mono+(seconds-1)*10**9)
    return path
