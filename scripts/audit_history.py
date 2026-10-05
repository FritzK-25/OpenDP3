"""Write a local, redacted inventory of reachable historical privacy findings.

Does not rewrite history or upload the report. Review credentials for revocation
and opaque attachments separately; a clean report is not a complete secret audit.
"""
import argparse
import json
import subprocess
from pathlib import Path

import privacy_check


def git(*args):
    return subprocess.check_output(["git", *args], cwd=privacy_check.ROOT)


def audit():
    findings = []
    for problem in privacy_check.commit_problems("--all"):
        commit, detail = problem.split(":", 1)
        category = "author email" if "author email" in detail else "committer email" if "committer email" in detail else "metadata identifier"
        findings.append({"commit_prefix": commit, "category": category})
    entries = set()
    for commit in git("rev-list", "--all").decode("ascii").splitlines():
        for row in git("ls-tree", "-r", "-z", commit).split(b"\0"):
            if not row:
                continue
            meta, name = row.split(b"\t", 1)
            _mode, kind, oid = meta.split()
            if kind == b"blob":
                entries.add((oid.decode("ascii"), name.decode("utf-8")))
    seen = set()
    for oid, name in sorted(entries):
        path = Path(name)
        for _finding in privacy_check.findings_in_text(name):
            findings.append({"object": oid, "path": name, "category": "path identifier"})
        if oid in seen:
            continue
        seen.add(oid)
        if path.suffix.lower() in privacy_check.SKIPPED_SUFFIXES:
            findings.append({"object": oid, "path": name, "category": "opaque attachment review"})
            continue
        if path.name == "LICENSE" or "THIRD_PARTY_LICENSES" in path.parts:
            continue
        data = git("cat-file", "blob", oid)
        if b"\0" in data[:8192]:
            findings.append({"object": oid, "path": name, "category": "binary review"})
            continue
        for number, _finding in privacy_check.text_findings(data.decode("utf-8", errors="replace"), python_source=path.suffix == ".py"):
            findings.append({"object": oid, "path": name, "line": number, "category": "content identifier or secret"})
    return {"scope": "reachable refs: metadata, every historical file path and distinct blobs; no issue/release/attachment API audit",
            "findings": findings}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if args.output.exists():
        raise SystemExit("Choose a new private report destination.")
    report = audit()
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, indent=2) + "\n", "utf-8")
    print(f"Redacted report written: {len(report['findings'])} findings. Nothing was published or rewritten.")


if __name__ == "__main__":
    main()
