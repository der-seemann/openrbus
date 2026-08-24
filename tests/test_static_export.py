from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest

from openrbus.registry import Registry, export_c_header, export_compact_json

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture(scope="module")
def registry() -> Registry:
    return Registry.load_default()


def test_compact_json_is_deterministic_and_complete(registry: Registry) -> None:
    first = export_compact_json(registry, locale="de")
    second = export_compact_json(registry, locale="de")
    assert first == second
    assert first.endswith("\n")

    document = json.loads(first)
    assert document["schema"] == "openrbus.registry.compact.v1"
    assert document["license"] == "CC-BY-4.0"
    assert document["locale"] == "de"
    assert len(document["registers"]) == 3_066

    columns = {name: index for index, name in enumerate(document["columns"])}
    row = next(
        item
        for item in document["registers"]
        if item[columns["index"]] == 0x3001 and item[columns["subindex"]] == 0
    )
    assert row[columns["type"]] == "ENUMERATION"
    assert row[columns["storage"]] == "UINT8"
    assert row[columns["minimum"]] == "0"
    assert row[columns["maximum"]] == "11"
    assert row[columns["flags"]] & document["flag_bits"]["unsafe"]


def test_c_header_is_deterministic_and_contains_static_table(registry: Registry) -> None:
    first = export_c_header(registry, locale="en")
    second = export_c_header(registry, locale="en")
    assert first == second
    assert "Registry data: CC BY 4.0" in first
    assert "static const OpenRBusRegister OPENRBUS_REGISTERS[] PROGMEM" in first
    assert "{0x3001, 0x00" in first
    assert "OPENRBUS_REGISTER_COUNT" in first
    assert "sqlite" not in first.casefold()


def test_generated_header_compiles_as_c_and_cpp(registry: Registry, tmp_path: Path) -> None:
    cc = shutil.which("cc")
    cxx = shutil.which("c++")
    if cc is None or cxx is None:
        pytest.skip("C/C++ compilers are not installed")

    header = tmp_path / "openrbus_registry.h"
    header.write_text(export_c_header(registry), encoding="utf-8", newline="\n")
    c_source = tmp_path / "check.c"
    c_source.write_text(
        '#include "openrbus_registry.h"\nint main(void) { return OPENRBUS_REGISTER_COUNT == 0; }\n',
        encoding="utf-8",
    )
    cpp_source = tmp_path / "check.cpp"
    cpp_source.write_text(
        '#include "openrbus_registry.h"\nint main() { return OPENRBUS_REGISTER_COUNT == 0; }\n',
        encoding="utf-8",
    )
    subprocess.run(
        [cc, "-std=c11", "-Wall", "-Wextra", "-Werror", "-fsyntax-only", str(c_source)],
        check=True,
        cwd=tmp_path,
    )
    subprocess.run(
        [cxx, "-std=c++17", "-Wall", "-Wextra", "-Werror", "-fsyntax-only", str(cpp_source)],
        check=True,
        cwd=tmp_path,
    )


def test_cli_writes_identical_exports(tmp_path: Path) -> None:
    first = tmp_path / "first.json"
    second = tmp_path / "second.json"
    command = [
        "python3",
        str(ROOT / "tools/export_registry.py"),
        "--format",
        "compact-json",
        "--locale",
        "en",
    ]
    subprocess.run([*command, "--output", str(first)], check=True, cwd=ROOT)
    subprocess.run([*command, "--output", str(second)], check=True, cwd=ROOT)
    assert first.read_bytes() == second.read_bytes()
