import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import release_gate  # noqa: E402


def candidate():
    commit = "a" * 40
    run = dict(headSha=commit, status="completed", conclusion="success", event="push", headBranch="main")
    jobs = [dict(name=name, conclusion="success") for name in release_gate.REQUIRED]
    return commit, run, jobs


@pytest.mark.parametrize("field,value", [("headSha", "b" * 40), ("event", "pull_request"),
                                         ("headBranch", "unreviewed"), ("conclusion", "failure"),
                                         ("status", "in_progress")])
def test_wrong_revision_or_unaccepted_run_cannot_release(field, value):
    commit, run, jobs = candidate()
    run[field] = value
    with pytest.raises(ValueError):
        release_gate.validate_run(commit, run, jobs)


@pytest.mark.parametrize("name", sorted(release_gate.REQUIRED))
def test_missing_or_skipped_release_job_is_rejected(name):
    commit, run, jobs = candidate()
    with pytest.raises(ValueError):
        release_gate.validate_run(commit, run, [job for job in jobs if job["name"] != name])
    next(job for job in jobs if job["name"] == name)["conclusion"] = "skipped"
    with pytest.raises(ValueError):
        release_gate.validate_run(commit, run, jobs)


def test_complete_exact_revision_is_accepted():
    release_gate.validate_run(*candidate())


def test_tag_workflow_invokes_exact_commit_gate_before_the_build():
    source = (Path(__file__).resolve().parents[1] / ".github/workflows/release.yml").read_text("utf-8")
    # Executable shell lines, not comments or an echo describing a check.
    commands = [line.strip() for line in source.splitlines()]
    gate = 'python3 scripts/release_gate.py --commit "$GITHUB_SHA" --repository "$REPOSITORY"'
    assert gate in commands
    assert 'git merge-base --is-ancestor "$GITHUB_SHA" origin/main' in commands
    assert source.index(gate) < source.index("windows:\n")
