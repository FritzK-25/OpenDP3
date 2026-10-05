import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import audit_history  # noqa: E402


def test_history_report_identifies_objects_without_disclosing_credential_values(monkeypatch):
    secret = "history" + "-secret-9xQ"
    oid = "a" * 40
    def git(*args):
        if args[0] == "rev-list":
            return f"{oid} old-config.txt\n".encode()
        if args[:2] == ("cat-file", "-t"):
            return b"blob\n"
        key = "mqtt_" + "password"
        return f'{key} = "{secret}"\n'.encode()
    monkeypatch.setattr(audit_history, "git", git)
    monkeypatch.setattr(audit_history.privacy_check, "commit_problems", lambda _range: [])
    report = audit_history.audit()
    assert report["findings"] == [{"object": oid, "path": "old-config.txt", "line": 1,
                                   "category": "content identifier or secret"}]
    assert secret not in str(report)
