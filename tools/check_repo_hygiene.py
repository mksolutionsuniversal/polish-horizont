#!/usr/bin/env python3
"""Block obvious accidental publication of credentials and runtime files.

Defense in depth, not a replacement for reviewing public changes.
Only tracked files are scanned; content is not printed on failure.
"""
from __future__ import annotations

import pathlib
import re
import subprocess
import sys

FORBIDDEN_SUFFIXES = {".pem", ".key", ".p12", ".pfx", ".db", ".sqlite", ".sqlite3", ".log"}
FORBIDDEN_DIRS = {"data", "private", "secrets", "logs", "backups", "exports", ".ssh"}
TOKEN_PATTERNS = (
    ("private key", re.compile(rb"-----BEGIN [A-Z0-9 ]*PRIVATE KEY-----")),
    ("GitHub token", re.compile(rb"gh[pousr]_[A-Za-z0-9_]{24,}|github_pat_[A-Za-z0-9_]{20,}")),
    ("AWS access key", re.compile(rb"AKIA[0-9A-Z]{16}")),
)
MAX_SCANNED_BYTES = 2_000_000


def check_path(name: str) -> str | None:
    parts = pathlib.PurePosixPath(name).parts
    base = parts[-1].lower() if parts else ""
    if any(part.lower() in FORBIDDEN_DIRS for part in parts[:-1]):
        return "private directory"
    if base == ".env" or (base.startswith(".env.") and base != ".env.example"):
        return "private environment file"
    if pathlib.PurePosixPath(base).suffix in FORBIDDEN_SUFFIXES:
        return "private file extension"
    if base in {"id_rsa", "id_ed25519", "known_hosts"}:
        return "private credential file"
    return None


def check_data(data: bytes) -> str | None:
    for description, pattern in TOKEN_PATTERNS:
        if pattern.search(data):
            return description
    return None


def main() -> int:
    root = pathlib.Path(__file__).resolve().parents[1]
    tracked = subprocess.run(["git", "ls-files", "-z"], cwd=root, check=True, capture_output=True)
    bad: list[tuple[str, str]] = []
    for raw in tracked.stdout.split(b"\0"):
        if not raw:
            continue
        name = raw.decode("utf-8", errors="replace")
        reason = check_path(name)
        if reason:
            bad.append((name, reason))
            continue
        file_path = root / name
        if not file_path.is_file():
            continue
        if file_path.stat().st_size > MAX_SCANNED_BYTES:
            bad.append((name, "oversize file requires review"))
            continue
        reason = check_data(file_path.read_bytes())
        if reason:
            bad.append((name, reason))
    for name, reason in bad:
        print(f"BLOCKED: {name} ({reason}; content not printed)", file=sys.stderr)
    if bad:
        print(f"Repository hygiene FAILED: {len(bad)} file(s).", file=sys.stderr)
        return 1
    print("Repository hygiene PASS (basic checks only).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
