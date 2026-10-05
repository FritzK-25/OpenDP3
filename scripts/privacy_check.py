"""Fail when a personal identifier is about to become public.

This repository is public, so anything committed here (files, commit authors,
commit messages) is readable by anyone, forever. Two checks:

    python scripts/privacy_check.py                   scan every tracked file
    python scripts/privacy_check.py --path DIR        scan a directory instead
    python scripts/privacy_check.py --commits A..B    check commit authors,
                                                      committers and messages

The file scan rejects email addresses, private-network IP addresses, Bluetooth
addresses, DELTA Pro 3 serial numbers, Windows user folders and LAN host
names, except the placeholders listed below. A test fixture that needs a new
fake value adds it to the matching allowlist, where a reviewer sees it.

``--deny-file FILE`` adds literal strings (one per line, ``#`` comments,
matched case-insensitively) that must not appear. It exists for a list that
cannot itself be committed here, such as a person's name or room names, kept
in a private repository and passed in when porting work from it.

tests/test_privacy_check.py runs the file scan over the repository, so a
leaked identifier fails the suite; CI runs the commit check on pull requests.
"""
import argparse
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

EMAIL = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9-]+(?:\.[A-Za-z0-9-]+)*\.[A-Za-z]{2,}")
# Addresses that identify no one: GitHub's private-email and bot addresses,
# no-reply senders, and the domains reserved for documentation (RFC 2606).
ALLOWED_EMAIL = re.compile(
    r"(?:[^@\s]+@users\.noreply\.github\.com|noreply@github\.com|noreply@anthropic\.com"
    r"|[^@\s]+@(?:[A-Za-z0-9-]+\.)*(?:example\.(?:com|org|net)|example|test|invalid|localhost))$",
    re.IGNORECASE,
)

PRIVATE_IP = re.compile(
    r"(?<![\d.])(?:10\.\d{1,3}|172\.(?:1[6-9]|2\d|3[01])|192\.168)\.\d{1,3}\.\d{1,3}(?![\d.])"
)
ALLOWED_IP: set[str] = set()

MAC = re.compile(r"(?<![0-9A-Fa-f:-])(?:[0-9A-Fa-f]{2}[:-]){5}[0-9A-Fa-f]{2}(?![0-9A-Fa-f:-])")
ALLOWED_MAC = {
    "AA:BB:CC:DD:EE:FF",
    "11:22:33:44:55:66",
    "22:33:44:55:66:77",
}

# DELTA Pro 3 serials are MR51 followed by twelve letters and digits.
DP3_SERIAL = re.compile(r"(?<![A-Za-z0-9])MR5\d[A-Za-z0-9]{12}(?![A-Za-z0-9])")
ALLOWED_DP3_SERIAL = {
    "MR51123456789012",
    "MR51ABCDEFGH1234",
    "MR51ABCDEFGHIJKL",
    "MR51XXXXXXXXXXXX",
}

WINDOWS_PROFILE = re.compile(r"[A-Za-z]:[\\/]{1,2}Users[\\/]{1,2}([^\\/\s\"'`<>]+)", re.IGNORECASE)
ALLOWED_PROFILE = {"<you>", "<user>", "%username%", "you", "user", "public", "default"}

LAN_HOST = re.compile(r"(?<![A-Za-z0-9.-])([A-Za-z0-9-]+)\.(local|lan|home\.arpa)(?![A-Za-z0-9.-])", re.IGNORECASE)
# Default host names that every installation shares.
ALLOWED_LAN_HOST = {"homeassistant.local", "broker.local"}

SKIPPED_SUFFIXES = {".png", ".ico", ".icns", ".jpg", ".jpeg", ".gif", ".exe", ".zip", ".whl", ".sqlite", ".pyc"}
# Licence texts are third-party documents reproduced verbatim.
SKIPPED_PARTS = {"THIRD_PARTY_LICENSES"}
SKIPPED_FILES = {"LICENSE"}
PUBLIC_TEXT_SUFFIXES = {".py", ".md", ".txt", ".json", ".yaml", ".yml", ".toml",
                        ".ps1", ".cmd", ".sh", ".proto", ".ini", ".cfg", ".rst", ".html", ".csv"}

# Explicit synthetic fixture values, reviewed alongside any new fixture.
FAKE_LITERALS = {"", "0", "123456", "1234567890", "1234567" + "89012345",
                 "8561999" + "90000000", "secret", "hunter2", "legacy-plaintext",
                 "PRIVATE_BROKER_PASSWORD", "BROKER_SECRET", "<password>",
                 "<user_id>", "<serial>", "YOUR_PASSWORD", "YOUR_TOKEN",
                 "fixture-user-id", "fixture-broker-password"}
SENSITIVE_LITERAL = re.compile(
    r'''\b(mqtt_password|password|api_key|access_token|refresh_token|user_id|jackery_serial)["']?\s*[:=]\s*["']([^"'\r\n]*)["']''',
    re.IGNORECASE,
)
TOKEN = re.compile(r"\b(?:gh[pousr]_[A-Za-z0-9]{30,}|github_pat_[A-Za-z0-9_]{40,}|AKIA[A-Z0-9]{16})\b")


def findings_in_text(text: str, deny: list[str] = ()) -> list[str]:
    """Every identifier in ``text`` that is not an allowlisted placeholder."""
    found = []
    for match in SENSITIVE_LITERAL.finditer(text):
        if match.group(2) not in FAKE_LITERALS:
            found.append(f"sensitive literal in {match.group(1)} (value withheld)")
    if TOKEN.search(text) or ("-----BEGIN " + "PRIVATE KEY-----") in text:
        found.append("credential material (value withheld)")
    for match in EMAIL.finditer(text):
        if not ALLOWED_EMAIL.fullmatch(match.group()):
            found.append(f"email address {match.group()}")
    for match in PRIVATE_IP.finditer(text):
        if match.group() not in ALLOWED_IP:
            found.append(f"private IP address {match.group()}")
    for match in MAC.finditer(text):
        if match.group().upper().replace("-", ":") not in ALLOWED_MAC:
            found.append(f"Bluetooth/MAC address {match.group()}")
    for match in DP3_SERIAL.finditer(text):
        if match.group().upper() not in ALLOWED_DP3_SERIAL:
            found.append(f"DELTA Pro 3 serial {match.group()}")
    for match in WINDOWS_PROFILE.finditer(text):
        if match.group(1).lower() not in ALLOWED_PROFILE:
            found.append(f"Windows user folder {match.group()}")
    for match in LAN_HOST.finditer(text):
        if match.group().lower() not in ALLOWED_LAN_HOST:
            found.append(f"LAN host name {match.group()}")
    lowered = text.lower()
    for term in deny:
        if term.lower() in lowered:
            found.append(f"denied term {term!r}")
    return found


def tracked_files() -> list[Path]:
    names = subprocess.run(
        ["git", "ls-files", "-z"], cwd=ROOT, check=True, capture_output=True
    ).stdout.decode("utf-8").split("\0")
    return [ROOT / name for name in names if name]


def files_under(base: Path) -> list[Path]:
    return [path for path in sorted(base.rglob("*"))
            if (path.is_file() or path.is_symlink() or path.is_junction()) and ".git" not in path.parts]


def scan_files(files: list[Path], base: Path, deny: list[str] = (), *, strict=False) -> list[str]:
    problems = []
    for path in files:
        relative = path.relative_to(base)
        for finding in findings_in_text(relative.as_posix(), deny):
            problems.append(f"{relative.as_posix()}:path: {finding}")
        if path.is_symlink() or path.is_junction() or not path.resolve().is_relative_to(base.resolve()):
            problems.append(f"{relative.as_posix()}: link or escaping path requires review")
            continue
        if strict and path.suffix and path.suffix.lower() not in PUBLIC_TEXT_SUFFIXES:
            problems.append(f"{relative.as_posix()}: opaque attachment requires separate review")
            continue
        if (path.suffix.lower() in SKIPPED_SUFFIXES or path.name in SKIPPED_FILES
                or SKIPPED_PARTS.intersection(relative.parts) or not path.is_file()):
            if strict:
                problems.append(f"{relative.as_posix()}: excluded attachment requires separate review")
            continue
        data = path.read_bytes()
        if b"\0" in data[:8192]:
            if strict:
                problems.append(f"{relative.as_posix()}: binary attachment requires separate review")
            continue
        text = data.decode("utf-8", errors="replace")
        for number, line in enumerate(text.splitlines(), 1):
            for finding in findings_in_text(line, deny):
                problems.append(f"{relative.as_posix()}:{number}: {finding}")
    return problems


def commit_problems(revision_range: str, deny: list[str] = ()) -> list[str]:
    """Personal emails in the authors, committers or messages of a range."""
    log = subprocess.run(
        ["git", "log", "--format=%H%x00%an%x00%ae%x00%cn%x00%ce%x00%B%x1e", revision_range],
        cwd=ROOT, check=True, capture_output=True,
    ).stdout.decode("utf-8", errors="replace")
    problems = []
    for record in filter(str.strip, log.split("\x1e")):
        sha, author, author_email, committer, committer_email, message = record.strip("\n").split("\0", 5)
        short = sha[:9]
        for role, email in (("author", author_email), ("committer", committer_email)):
            if not ALLOWED_EMAIL.fullmatch(email):
                problems.append(f"{short}: {role} email {email} is not a GitHub no-reply address")
        for finding in findings_in_text(f"{author}\n{committer}\n{message}", deny):
            problems.append(f"{short}: {finding}")
    return problems


def read_deny_file(path: Path) -> list[str]:
    lines = path.read_text("utf-8").splitlines()
    return [line.strip() for line in lines if line.strip() and not line.lstrip().startswith("#")]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    target = parser.add_mutually_exclusive_group()
    target.add_argument("--path", type=Path, help="scan every file under this directory")
    target.add_argument("--commits", metavar="RANGE", help="check commits in a git revision range")
    parser.add_argument("--deny-file", type=Path, help="extra literal terms that must not appear")
    args = parser.parse_args(argv)

    deny = read_deny_file(args.deny_file) if args.deny_file else []
    if args.commits:
        problems = commit_problems(args.commits, deny)
    elif args.path:
        base = args.path.resolve()
        problems = scan_files(files_under(base), base, deny, strict=True)
    else:
        problems = scan_files(tracked_files(), ROOT, deny)

    for problem in problems:
        print(problem)
    if problems:
        print(f"\n{len(problems)} possible personal identifier(s). Replace each with a placeholder,"
              " or add a genuinely fake value to the allowlist in scripts/privacy_check.py.",
              file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
