#!/usr/bin/env python3
"""Rebuild public enum labels from maintainer-supplied dictionary inputs.

Only the short ``Description`` attributes are copied.  Long ``Explanation``
text and all provenance paths/identifiers stay outside the public registry.
"""

from __future__ import annotations

import argparse
import json
import sys
import xml.etree.ElementTree as ET
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--registry", type=Path, required=True)
    parser.add_argument("--packaged", type=Path, required=True)
    parser.add_argument("--dictionary", type=Path, required=True)
    parser.add_argument("--de", type=Path, required=True, help="German translation XML")
    parser.add_argument("--en", type=Path, required=True, help="English translation XML")
    parser.add_argument("--revision", required=True)
    parser.add_argument("--check", action="store_true")
    return parser


def _enum_descriptions(path: Path) -> dict[str, dict[int, str]]:
    root = ET.parse(path).getroot()
    enums = root.find("Enums")
    if enums is None:
        raise ValueError(f"{path}: missing Enums section")
    return {
        item.attrib["Name"]: {
            int(value.attrib["Value"]): value.attrib.get("Description", "").strip()
            for value in item
        }
        for item in enums
    }


def _render(document: Mapping[str, Any], *, pretty: bool) -> bytes:
    if pretty:
        text = json.dumps(document, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    else:
        text = (
            json.dumps(
                document,
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
            )
            + "\n"
        )
    return text.encode()


def _build(args: argparse.Namespace) -> dict[str, Any]:
    document = json.loads(args.registry.read_text(encoding="utf-8"))
    base = _enum_descriptions(args.dictionary)
    translated = {"de": _enum_descriptions(args.de), "en": _enum_descriptions(args.en)}

    label_count = {"de": 0, "en": 0, "any": 0}
    for enumeration in document["enums"]:
        name = enumeration["name"]
        labels = []
        for value in enumeration["values"]:
            names = {
                locale: source.get(name, {}).get(value, "") for locale, source in translated.items()
            }
            if not names["en"]:
                names["en"] = base.get(name, {}).get(value, "")
            names = {locale: text for locale, text in names.items() if text}
            for locale in names:
                label_count[locale] += 1
            if names:
                label_count["any"] += 1
                labels.append({"names": names, "value": value})
        enumeration["labels"] = labels

    by_enum = {item["name"]: item for item in document["enums"]}
    referenced_enums = {
        item["wire"]["enum"] for item in document["registers"] if item["wire"]["enum"]
    }
    incomplete = [
        name
        for name in sorted(referenced_enums)
        if len(by_enum[name]["labels"]) != len(by_enum[name]["values"])
    ]
    if incomplete:
        raise ValueError("referenced enums have missing labels: " + ", ".join(incomplete))

    structures = document["structures"]
    bitfields = [
        item
        for item in structures
        if item["fields"] and all(field["bit_length"] == 1 for field in item["fields"])
    ]
    bitfield_names = {item["name"] for item in bitfields}
    bitfield_registers = sum(
        item["wire"]["struct"] in bitfield_names for item in document["registers"]
    )
    counts = document["metadata"]["counts"]
    counts.update(
        {
            "bitfield_fields": sum(len(item["fields"]) for item in bitfields),
            "bitfield_registers": bitfield_registers,
            "bitfield_structures": len(bitfields),
            "enum_value_labels": label_count["any"],
            "enum_value_labels_de": label_count["de"],
            "enum_value_labels_en": label_count["en"],
            "enum_values": sum(len(item["values"]) for item in document["enums"]),
        }
    )
    document["metadata"]["revision"] = args.revision
    return document


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    document = _build(args)
    outputs = {
        args.registry: _render(document, pretty=True),
        args.packaged: _render(document, pretty=False),
    }
    if args.check:
        stale = [path for path, expected in outputs.items() if path.read_bytes() != expected]
        if stale:
            print("stale registry output: " + ", ".join(map(str, stale)), file=sys.stderr)
            return 1
        return 0
    for path, content in outputs.items():
        path.write_bytes(content)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
