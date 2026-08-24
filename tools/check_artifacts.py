#!/usr/bin/env python3
"""Audit built wheel and source archives without extracting them."""

from __future__ import annotations

import argparse
import tarfile
import zipfile
from collections.abc import Iterable, Iterator
from dataclasses import dataclass
from pathlib import Path, PurePosixPath

from check_publication import (
    FORBIDDEN_DIRECTORIES,
    FORBIDDEN_NAMES,
    FORBIDDEN_SUFFIXES,
    TEXT_PATTERNS,
)


@dataclass(frozen=True, slots=True)
class ArchiveMember:
    archive: Path
    name: str
    data: bytes


def artifact_paths(directory: Path) -> tuple[Path, ...]:
    """Return supported distribution archives in deterministic order."""

    artifacts = tuple(
        sorted(
            path
            for path in directory.iterdir()
            if path.is_file() and (path.suffix == ".whl" or path.name.endswith(".tar.gz"))
        )
    )
    if not artifacts:
        raise ValueError(f"no wheel or source archive found in {directory}")
    return artifacts


def archive_members(path: Path) -> Iterator[ArchiveMember]:
    """Yield regular files from one supported distribution archive."""

    if path.suffix == ".whl":
        with zipfile.ZipFile(path) as archive:
            for info in archive.infolist():
                if not info.is_dir():
                    yield ArchiveMember(path, info.filename, archive.read(info))
        return

    with tarfile.open(path, mode="r:gz") as archive:
        for info in archive.getmembers():
            if info.issym() or info.islnk():
                raise ValueError(f"link member in {path.name}: {info.name}")
            if info.isfile():
                source = archive.extractfile(info)
                if source is None:
                    raise ValueError(f"cannot read {path.name}: {info.name}")
                yield ArchiveMember(path, info.name, source.read())


def audit(members: Iterable[ArchiveMember]) -> tuple[list[str], int]:
    """Return findings and the number of inspected members."""

    failures: list[str] = []
    count = 0
    for member in members:
        count += 1
        relative = PurePosixPath(member.name)
        label = f"{member.archive.name}:{relative}"
        if relative.is_absolute() or ".." in relative.parts:
            failures.append(f"unsafe archive path: {label}")
            continue
        if any(part in FORBIDDEN_DIRECTORIES for part in relative.parts[:-1]):
            failures.append(f"forbidden directory: {label}")
            continue
        if relative.name in FORBIDDEN_NAMES or relative.suffix.lower() in FORBIDDEN_SUFFIXES:
            failures.append(f"forbidden file: {label}")
            continue
        data = member.data
        if b"\0" in data[:8192]:
            failures.append(f"binary file: {label}")
            continue
        if len(data) > 8_000_000:
            failures.append(f"oversized file: {label} ({len(data)} bytes)")
            continue
        text = data.decode("utf-8", "replace")
        for pattern_label, pattern in TEXT_PATTERNS.items():
            if pattern.search(text):
                failures.append(f"{pattern_label}: {label}")
    return failures, count


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("directory", nargs="?", type=Path, default=Path("dist"))
    args = parser.parse_args()
    try:
        paths = artifact_paths(args.directory.resolve())
        failures, member_count = audit(member for path in paths for member in archive_members(path))
    except (OSError, ValueError, tarfile.TarError, zipfile.BadZipFile) as exc:
        print(f"ERROR: artifact audit failed: {exc}")
        return 1
    for failure in failures:
        print(f"ERROR: {failure}")
    if failures:
        print(f"artifact audit failed with {len(failures)} finding(s)")
        return 1
    print(f"artifact audit passed: {len(paths)} archive(s), {member_count} file(s)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
