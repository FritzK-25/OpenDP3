"""This repository is public: nothing that identifies a person or a home may be
committed, and placeholders must stay recognisably fake."""
import os
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import privacy_check  # noqa: E402

# The repository scan covers this file too, so the personal-looking values the
# tests feed the checker are assembled at run time rather than written out.
AT = "@"
PERSONAL = "a.person" + AT + "gmail.com"
OTHER = "x.person" + AT + "gmail.com"


def test_the_repository_contains_no_personal_identifiers():
    assert privacy_check.scan_files(privacy_check.tracked_files(), ROOT) == [], \
        "run: python scripts/privacy_check.py"


@pytest.mark.parametrize("text", [
    "contact someone.real" + AT + "gmail.com",
    "broker at 192.168" + ".1.20",
    "host 10.0" + ".0.5 and 172.20" + ".3.4",
    "adapter " + ":".join(["3C", "71", "BF", "12", "34", "56"]),
    "adapter " + "-".join(["3c", "71", "bf", "12", "34", "56"]),
    "serial MR51" + "A1B2C3D4E5F6",
    "C:" + r"\Users\somebody\AppData",
    "C:" + "/Users/somebody/data",
    "http://garage-pi" + ".local:8123",
    "nas" + ".lan",
])
def test_personal_identifiers_are_found(text):
    assert privacy_check.findings_in_text(text), text


@pytest.mark.parametrize("text", [
    "FritzK-25@users.noreply.github.com",
    "49699333+dependabot[bot]@users.noreply.github.com",
    "noreply@github.com",
    "email@example.test",
    "user@example.com",
    "AA:BB:CC:DD:EE:FF and aa-bb-cc-dd-ee-ff",
    "MR51123456789012",
    r"C:\Users\<you>\AppData and %LOCALAPPDATA%",
    "http://homeassistant.local:8123",
    "version 1.10.0.12 and 192.168.1",
    "protobuf.internal",
])
def test_placeholders_and_public_values_pass(text):
    assert privacy_check.findings_in_text(text) == [], text


def test_denied_terms_match_case_insensitively():
    assert privacy_check.findings_in_text("sensor.Alex_S_Office_power", ["alex_s_office"])
    assert privacy_check.findings_in_text("sensor.kitchen_power", ["alex_s_office"]) == []


@pytest.mark.parametrize("key,value", [
    ("mqtt_password", "review" + "-credential-9xQ"),
    ("user_id", "98765" + "4321"),
    ("jackery_serial", "987654" + "321098765"),
    ("access_token", "review" + "-token-9xQ"),
])
def test_sensitive_literal_assignments_are_rejected_without_echoing_values(key, value):
    problems = privacy_check.findings_in_text(f'{key} = "{value}"')
    assert problems
    assert all(value not in problem for problem in problems)


@pytest.mark.parametrize("separator", [": ", " = "])
@pytest.mark.parametrize("value", ["review" + "-credential-9xQ", "98765" + "4321"])
def test_unquoted_credentials_are_rejected(separator, value):
    assert privacy_check.findings_in_text("mqtt_password" + separator + value)


def test_history_checks_every_name_of_a_shared_blob(tmp_path, monkeypatch):
    import audit_history
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init", "-q")
    (repo / "0-generic.txt").write_text("generic\n", "utf-8")
    _git(repo, "add", ".")
    env = {**os.environ, "GIT_AUTHOR_NAME": "Example", "GIT_COMMITTER_NAME": "Example",
           "GIT_AUTHOR_EMAIL": "user@example.com", "GIT_COMMITTER_EMAIL": "user@example.com"}
    _git(repo, "commit", "-qm", "first", env=env)
    private = PERSONAL + ".txt"
    (repo / private).write_text("generic\n", "utf-8")
    _git(repo, "add", ".")
    _git(repo, "commit", "-qm", "copy", env=env)
    monkeypatch.setattr(privacy_check, "ROOT", repo)
    assert any(row.get("path") == private and row["category"] == "path identifier"
               for row in audit_history.audit()["findings"])


def test_rules_update_preserves_additional_live_protections(tmp_path, monkeypatch):
    import repository_rules
    import json
    path = tmp_path / ".github" / "rulesets"
    path.mkdir(parents=True)
    (path / "main.json").write_text(json.dumps({"name": "main", "rules": [{"type": "deletion"}]}))
    additional = {"type": "required_signatures"}
    applied = []
    def api(endpoint, method="GET", payload=None):
        if method == "PUT":
            applied.append(payload)
            return payload
        if endpoint == "rulesets":
            return [{"name": "main", "id": 1}]
        return {"rules": [{"type": "deletion"}, additional]}
    monkeypatch.setattr(repository_rules, "ROOT", tmp_path)
    monkeypatch.setattr(repository_rules, "api", api)
    monkeypatch.setattr(sys, "argv", ["repository_rules.py", "--apply"])
    repository_rules.main()
    assert additional in applied[0]["rules"]


def test_private_filename_is_scanned_even_when_content_is_generic(tmp_path):
    (tmp_path / "private-room.txt").write_text("generic", "utf-8")
    assert privacy_check.scan_files(privacy_check.files_under(tmp_path), tmp_path, ["private-room"])


def test_binary_publication_requires_review(tmp_path):
    (tmp_path / "capture.zip").write_bytes(b"opaque")
    assert privacy_check.main(["--path", str(tmp_path)]) == 1


def test_deny_file_skips_comments_and_blank_lines(tmp_path):
    deny = tmp_path / "deny.txt"
    deny.write_text("# private terms\n\nalex\n  garage  \n", "utf-8")
    assert privacy_check.read_deny_file(deny) == ["alex", "garage"]


def test_path_scan_reports_file_and_line(tmp_path):
    (tmp_path / "notes.md").write_text(f"fine\nmail me at {PERSONAL}\n", "utf-8")
    (tmp_path / "picture.png").write_bytes(PERSONAL.encode())
    assert privacy_check.main(["--path", str(tmp_path)]) == 1
    problems = privacy_check.scan_files(privacy_check.files_under(tmp_path), tmp_path)
    assert problems == [f"notes.md:2: email address {PERSONAL}"]


def _git(repo, *args, env=None):
    subprocess.run(["git", *args], cwd=repo, check=True, capture_output=True, env=env)


def test_commit_check_rejects_a_personal_author_email(tmp_path, monkeypatch):
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init", "-q")
    for email, message in (
        ("someone@users.noreply.github.com", "first"),
        (PERSONAL, "second"),
        ("someone@users.noreply.github.com", f"third\n\nCo-authored-by: X <{OTHER}>"),
    ):
        env = {**os.environ, "GIT_AUTHOR_NAME": "Someone", "GIT_COMMITTER_NAME": "Someone",
               "GIT_AUTHOR_EMAIL": email, "GIT_COMMITTER_EMAIL": email}
        _git(repo, "-c", "commit.gpgsign=false", "commit", "-q", "--allow-empty", "-m", message, env=env)
    monkeypatch.setattr(privacy_check, "ROOT", repo)

    problems = privacy_check.commit_problems("HEAD~2..HEAD")

    assert len(problems) == 3, problems
    assert sum(f"author email {PERSONAL}" in p for p in problems) == 1
    assert sum(f"committer email {PERSONAL}" in p for p in problems) == 1
    assert sum(f"email address {OTHER}" in p for p in problems) == 1
    assert privacy_check.commit_problems("HEAD~2..HEAD~1") != []
    assert privacy_check.commit_problems("HEAD~2") == []
