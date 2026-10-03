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
