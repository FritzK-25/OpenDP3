"""Require a complete successful main CI run for the exact release commit."""
import argparse
import json
import re
import subprocess

REQUIRED = {"test (ubuntu-latest)", "test (windows-latest)", "lint", "privacy",
            "windows-build / build", "home-assistant-app",
            "home-assistant-image (amd64)", "home-assistant-image (aarch64)"}


def validate_run(commit, run, jobs):
    if not re.fullmatch(r"[0-9a-f]{40}", commit):
        raise ValueError("invalid release commit")
    if (run.get("headSha") != commit or run.get("status") != "completed"
            or run.get("conclusion") != "success" or run.get("event") != "push"
            or run.get("headBranch") != "main"):
        raise ValueError("release requires successful main CI for this exact commit")
    results = {job["name"]: job.get("conclusion") for job in jobs}
    if any(results.get(name) != "success" for name in REQUIRED):
        raise ValueError("required release jobs are absent or unsuccessful")


def gh_json(*args):
    return json.loads(subprocess.check_output(["gh", *args], text=True))


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--commit", required=True)
    parser.add_argument("--repository", required=True)
    args = parser.parse_args(argv)
    runs = gh_json("run", "list", "-R", args.repository, "--workflow", "ci.yml",
                   "--commit", args.commit, "--branch", "main", "--event", "push",
                   "--limit", "100", "--json", "databaseId,headSha,headBranch,event,status,conclusion")
    if not runs:
        raise SystemExit("No main CI run for the release commit. Run CI before tagging.")
    # A failing rerun supersedes an older success.
    run = max(runs, key=lambda item: item["databaseId"])
    jobs = gh_json("run", "view", str(run["databaseId"]), "-R", args.repository, "--json", "jobs")["jobs"]
    try:
        validate_run(args.commit, run, jobs)
    except ValueError as exc:
        raise SystemExit(str(exc)) from None
    print("Exact release commit passed all required main CI jobs.")


if __name__ == "__main__":
    main()
