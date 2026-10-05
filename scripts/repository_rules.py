"""Inspect proposed rules; apply only with an explicit --apply operation.

Preserves existing rule fields except the intended required checks, strict
updates, resolved discussions and no-bypass boundary. Tag creation stays allowed;
release workflows separately authenticate the exact accepted main commit.
"""
import argparse
import json
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
REPOSITORY = "FritzK-25/OpenPowerstation"


def api(path, method="GET", payload=None):
    command = ["gh", "api", f"repos/{REPOSITORY}/{path}", "--method", method]
    if payload is not None:
        command += ["--input", "-"]
    result = subprocess.run(command, input=json.dumps(payload) if payload is not None else None,
                            text=True, check=True, capture_output=True)
    return json.loads(result.stdout)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args()
    existing = api("rulesets")
    for path in sorted((ROOT / ".github/rulesets").glob("*.json")):
        desired = json.loads(path.read_text("utf-8"))
        current = next((rule for rule in existing if rule["name"] == desired["name"]), None)
        if current:
            live = api(f"rulesets/{current['id']}")
            for rule in desired["rules"]:
                previous = next((item for item in live["rules"] if item["type"] == rule["type"]), None)
                if previous and "parameters" in previous:
                    rule["parameters"] = {**previous["parameters"], **rule.get("parameters", {})}
        print(json.dumps(desired, indent=2))
        if args.apply:
            endpoint = f"rulesets/{current['id']}" if current else "rulesets"
            api(endpoint, "PUT" if current else "POST", desired)
            print(f"Applied and readable: {desired['name']}")


if __name__ == "__main__":
    main()
