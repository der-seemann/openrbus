#!/usr/bin/env python3
"""Split and synchronize the public language-neutral registry artifacts.

This tool reads public data only.  It has no option for original/vendor locale
files, and rejects public locale documents containing original-text fields.
"""

from __future__ import annotations

import argparse
import json
import sys
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
CANONICAL = ROOT / "data/registry/registry-v1.json"
PACKAGED = ROOT / "src/openrbus/data/registry-v1.json"
LOCALE_DIR = ROOT / "data/registry/i18n"
PACKAGED_LOCALE_DIR = ROOT / "src/openrbus/data/i18n"
PUBLIC_LOCALES = ("de", "en")
FORBIDDEN_LOCALE_KEYS = {"explanation", "long", "medium", "original", "source"}

WIRE_LABELS = {
    "de": {
        "UINT8": "8-Bit vorzeichenlos",
        "UINT16": "16-Bit vorzeichenlos",
        "UINT32": "32-Bit vorzeichenlos",
        "INT8": "8-Bit Ganzzahl",
        "INT16": "16-Bit Ganzzahl",
        "INT32": "32-Bit Ganzzahl",
        "ENUMERATION": "Auswahl",
        "STRUCT": "Struktur",
        "OCTETSTRING": "Bytefolge",
        "VISIBLESTRING": "Text",
        "TIME_OF_DAY": "Tageszeit",
    },
    "en": {
        "UINT8": "Unsigned 8-bit",
        "UINT16": "Unsigned 16-bit",
        "UINT32": "Unsigned 32-bit",
        "INT8": "Signed 8-bit",
        "INT16": "Signed 16-bit",
        "INT32": "Signed 32-bit",
        "ENUMERATION": "Enumeration",
        "STRUCT": "Structure",
        "OCTETSTRING": "Byte string",
        "VISIBLESTRING": "Text",
        "TIME_OF_DAY": "Time of day",
    },
}

FORMAT_LABELS = {
    "de": {"date": "Datum", "date_time": "Datum und Uhrzeit", "time": "Uhrzeit"},
    "en": {"date": "Date", "date_time": "Date and time", "time": "Time"},
}

PUBLIC_ENUM_OVERRIDES = {
    "de": {
        ("BlockingFunction", 13): "Elektrische Leistungsbegrenzung",
        ("ConfigIoPermission", 3): "Konfigurierbar, HMI-Funktion verborgen",
        ("SystemDiscoveryStatus", 0): "Systemerkennung wird vorbereitet",
    },
    "en": {
        ("BlockingFunction", 13): "Electrical power limitation",
        ("ConfigIoPermission", 3): "Configurable, HMI function hidden",
        ("IOConfigAuxOutputs", 3): "System pump output",
        ("IOconfigDigitalInputs", 6): "External DHW system error input",
        ("IOconfigDigitalInputs", 7): "External component error input",
        ("InputSensorConfiguration", 6): "External generator flow sensor",
        ("SpecialPowerAlgorithm", 11): "DHW boost with all heat sources",
        ("SystemDiscoveryStatus", 0): "System discovery is being prepared",
    },
}


def _dump(value: Mapping[str, Any], *, pretty: bool) -> bytes:
    options: dict[str, Any] = {"ensure_ascii": False, "sort_keys": True}
    if pretty:
        options["indent"] = 2
    else:
        options["separators"] = (",", ":")
    return (json.dumps(value, **options) + "\n").encode()


def _walk_keys(value: Any) -> set[str]:
    result: set[str] = set()
    if isinstance(value, dict):
        result.update(str(key).casefold() for key in value)
        for child in value.values():
            result.update(_walk_keys(child))
    elif isinstance(value, list):
        for child in value:
            result.update(_walk_keys(child))
    return result


def _extract(document: dict[str, Any]) -> dict[str, dict[str, Any]]:
    locales = {
        locale: {
            "schema": "openrbus.registry.locale.v1",
            "locale": locale,
            "revision": document["metadata"]["revision"],
            "registers": {},
            "enums": {},
            "structure_fields": {},
            "wire_types": WIRE_LABELS[locale],
            "formats": FORMAT_LABELS[locale],
        }
        for locale in PUBLIC_LOCALES
    }
    for register in document["registers"]:
        names = register.pop("names")
        address = register["address"]
        for locale in PUBLIC_LOCALES:
            locales[locale]["registers"][address] = names[locale]
        if register["code"] is None:
            register["fallback_name_en"] = names["en"]

    for enumeration in document["enums"]:
        labels = enumeration.pop("labels", [])
        for label in labels:
            value = label["value"]
            for locale in PUBLIC_LOCALES:
                text = label["names"].get(locale)
                override = PUBLIC_ENUM_OVERRIDES[locale].get((enumeration["name"], value))
                if override or text:
                    locales[locale]["enums"].setdefault(enumeration["name"], {})[str(value)] = (
                        override or text
                    )

    for structure in document["structures"]:
        for locale in PUBLIC_LOCALES:
            locales[locale]["structure_fields"][structure["name"]] = {
                field["name"]: field["name"] for field in structure["fields"]
            }

    counts = document["metadata"]["counts"]
    counts["public_locales"] = len(PUBLIC_LOCALES)
    document["metadata"]["locale_files"] = {
        locale: f"i18n/{locale}.json" for locale in PUBLIC_LOCALES
    }
    return locales


def _validate(document: Mapping[str, Any], locales: Mapping[str, Mapping[str, Any]]) -> None:
    if any("names" in item for item in document["registers"]):
        raise ValueError("main registry still contains localized register names")
    if any("labels" in item for item in document["enums"]):
        raise ValueError("main registry still contains localized enum labels")
    for item in document["registers"]:
        if item["code"] is None and not item.get("fallback_name_en"):
            raise ValueError(f"missing English fallback for {item['address']}")
        if item["code"] is not None and "fallback_name_en" in item:
            raise ValueError(f"coded register has fallback name: {item['address']}")

    addresses = {item["address"] for item in document["registers"]}
    fields = {
        (structure["name"], field["name"])
        for structure in document["structures"]
        for field in structure["fields"]
    }
    for locale, data in locales.items():
        forbidden = _walk_keys(data) & FORBIDDEN_LOCALE_KEYS
        if forbidden:
            raise ValueError(f"{locale}: forbidden public locale keys: {sorted(forbidden)}")
        if set(data["registers"]) != addresses:
            raise ValueError(f"{locale}: register label coverage mismatch")
        locale_fields = {
            (structure, field)
            for structure, values in data["structure_fields"].items()
            for field in values
        }
        if locale_fields != fields:
            raise ValueError(f"{locale}: structure field coverage mismatch")
        labels = [text for values in data["enums"].values() for text in values.values()]
        if any(len(text) > 64 or "\n" in text for text in labels):
            raise ValueError(f"{locale}: enum label exceeds the public short-label policy")


def _outputs(
    document: Mapping[str, Any], locales: Mapping[str, Mapping[str, Any]]
) -> dict[Path, bytes]:
    result = {
        CANONICAL: _dump(document, pretty=True),
        PACKAGED: _dump(document, pretty=False),
    }
    for locale, data in locales.items():
        result[LOCALE_DIR / f"{locale}.json"] = _dump(data, pretty=True)
        result[PACKAGED_LOCALE_DIR / f"{locale}.json"] = _dump(data, pretty=False)
    return result


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--extract", action="store_true", help="one-time split of embedded labels")
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args(argv)

    document = json.loads(CANONICAL.read_text(encoding="utf-8"))
    if args.extract:
        locales = _extract(document)
    else:
        locales = {
            locale: json.loads((LOCALE_DIR / f"{locale}.json").read_text(encoding="utf-8"))
            for locale in PUBLIC_LOCALES
        }
    _validate(document, locales)
    outputs = _outputs(document, locales)
    if args.check:
        stale = [
            path
            for path, expected in outputs.items()
            if not path.exists() or path.read_bytes() != expected
        ]
        if stale:
            print("stale registry output: " + ", ".join(map(str, stale)), file=sys.stderr)
            return 1
        return 0
    for path, content in outputs.items():
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(content)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
