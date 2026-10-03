"""Every third-party action is pinned to a commit, not a movable tag."""
from pathlib import Path
import re

WORKFLOWS = Path(__file__).resolve().parents[1] / ".github" / "workflows"
USES = re.compile(r"^\s*(?:-\s*)?uses:\s*(\S+)(.*)$")


def test_remote_actions_are_pinned_to_a_commit_sha():
    unpinned = []
    for workflow in sorted(WORKFLOWS.glob("*.yml")):
        for number, line in enumerate(workflow.read_text(encoding="utf-8").splitlines(), 1):
            match = USES.match(line)
            if not match or match.group(1).startswith("./"):
                continue
            action, _, ref = match.group(1).partition("@")
            # The trailing "# vX.Y.Z" comment is what Dependabot updates alongside the SHA.
            if not re.fullmatch(r"[0-9a-f]{40}", ref) or not re.search(r"#\s*v\d", match.group(2)):
                unpinned.append(f"{workflow.name}:{number}: {match.group(1)}")
    assert not unpinned, "pin these to a commit SHA with a version comment: " + ", ".join(unpinned)
