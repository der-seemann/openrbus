#!/usr/bin/env python3
"""Fail when a candidate public tree contains private or vendor material."""

from __future__ import annotations

import argparse
import re
import subprocess
from pathlib import Path

FORBIDDEN_DIRECTORIES = {"local", "sources", "captures", "reversing"}
FORBIDDEN_SUFFIXES = {
    ".aab",
    ".apk",
    ".dll",
    ".dylib",
    ".exe",
    ".iae",
    ".pcap",
    ".pcapng",
    ".pfx",
    ".rxp",
    ".rxdx",
    ".so",
    ".xapk",
}
FORBIDDEN_NAMES = {"id_rsa", "id_ed25519", ".env"}
TEXT_PATTERNS = {
    "private key": re.compile(r"-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----"),
    "GitHub token": re.compile(r"\b(?:ghp|github_pat)_[A-Za-z0-9_]{20,}\b"),
    "absolute home path": re.compile(r"/(?:home|Users)/[A-Za-z0-9_.-]+/"),
    "Windows user path": re.compile(r"[A-Za-z]:\\\\Users\\\\[^\\\\\s]+"),
    "BLE MAC address": re.compile(r"(?i)\b(?:[0-9a-f]{2}:){5}[0-9a-f]{2}\b"),
    # Split the spellings so the audit tool does not flag its own source.
    "legacy vendor key symbol": re.compile(
        r"\b(?:XML_" + r"BIN_KEY|OBD_" + r"KEY|IAE_" + r"KEY)\b"
    ),
}


def tracked_files(root: Path) -> list[Path]:
    result = subprocess.run(
        ["git", "ls-files", "-co", "--exclude-standard"],
        cwd=root,
        check=True,
        capture_output=True,
        text=True,
    )
    return [root / line for line in result.stdout.splitlines() if line]


def audit(root: Path) -> list[str]:
    failures: list[str] = []
    for path in tracked_files(root):
        relative = path.relative_to(root)
        if any(part in FORBIDDEN_DIRECTORIES for part in relative.parts[:-1]):
            failures.append(f"forbidden directory: {relative}")
            continue
        if path.name in FORBIDDEN_NAMES or path.suffix.lower() in FORBIDDEN_SUFFIXES:
            failures.append(f"forbidden file: {relative}")
            continue
        try:
            data = path.read_bytes()
        except OSError as exc:
            failures.append(f"cannot read {relative}: {exc}")
            continue
        if b"\0" in data[:8192]:
            failures.append(f"binary file: {relative}")
            continue
        if len(data) > 8_000_000:
            failures.append(f"oversized file: {relative} ({len(data)} bytes)")
            continue
        text = data.decode("utf-8", "replace")
        for label, pattern in TEXT_PATTERNS.items():
            if pattern.search(text):
                failures.append(f"{label}: {relative}")
    return failures


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("root", nargs="?", type=Path, default=Path(__file__).resolve().parents[1])
    args = parser.parse_args()
    failures = audit(args.root.resolve())
    for failure in failures:
        print(f"ERROR: {failure}")
    if failures:
        print(f"publication audit failed with {len(failures)} finding(s)")
        return 1
    print("publication audit passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
