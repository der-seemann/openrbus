#!/usr/bin/env python3
"""Build a source-aware, per-register R/W evidence comparison.

The normalized public registry is compared with the provenance-preserving
local mapping database. The database path is explicit because it contains
private source inputs and is not shipped with OpenRBus. Output contains only
normalized register facts and aggregate IAE/config evidence, never source paths,
arbitrary raw attributes, sampled values, archive identifiers, or credentials.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sqlite3
import zipfile
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any
from xml.etree import ElementTree

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_REGISTRY = ROOT / "data/registry/registry-v1.json"


def _category(
    register: dict[str, Any],
    iae_rows: list[dict[str, Any]],
    obd_source: dict[str, Any] | None = None,
) -> str:
    """Classify evidence without converting metadata into write authorization."""
    if obd_source is not None and (
        bool(register["access"]["write"]) != bool(obd_source["writable_declared"])
        or bool(register["access"]["read"]) != bool(obd_source["readable_declared"])
    ):
        return "obd_registry_access_conflict"
    if not register["access"]["write"]:
        return "declared_read_only"
    if not iae_rows:
        return "write_declared_iae_evidence_missing"
    statuses = {row["writable"] for row in iae_rows if row["writable"] is not None}
    if len(statuses) > 1:
        return "iae_writability_conflict"
    if any(row["writable"] is None for row in iae_rows):
        return "iae_writability_unknown"
    if statuses == {0}:
        return "iae_read_only_despite_write_declaration"
    if statuses == {1}:
        return "iae_write_metadata_requires_safety_gate"
    return "iae_writability_unknown"


def _load_iae(db: sqlite3.Connection) -> dict[str, list[dict[str, Any]]]:
    """Aggregate IAE occurrence facts by canonical OBD register address."""
    db.row_factory = sqlite3.Row
    result: dict[str, list[dict[str, Any]]] = defaultdict(list)
    query = """
        SELECT d.catalog_register_adresse AS catalog_address,
               d.resolved_register_adresse AS resolved_address,
               o.device_family_key AS family,
               d.loaded, d.writable, d.read_level, d.write_level,
               count(DISTINCT o.source_id) AS source_count,
               count(DISTINCT o.definition_group) AS group_count
        FROM iae_definitions AS d
        JOIN iae_definition_occurrences AS o USING(definition_id)
        WHERE d.catalog_register_adresse IS NOT NULL
        GROUP BY d.catalog_register_adresse, d.resolved_register_adresse,
                 o.device_family_key, d.loaded, d.writable,
                 d.read_level, d.write_level
        ORDER BY d.catalog_register_adresse, o.device_family_key,
                 d.resolved_register_adresse
    """
    for raw in db.execute(query):
        row = dict(raw)
        result[row.pop("catalog_address")].append(row)
    return result


def _load_config(db: sqlite3.Connection) -> dict[str, dict[str, Any]]:
    """Return sanitized counts of config SDO agreement and access-level facts."""
    result: dict[str, dict[str, Any]] = {}
    query = """
        SELECT catalog_register_adresse AS address,
               count(*) AS occurrences,
               sum(CASE WHEN type_compatible_with_catalog=1 THEN 1 ELSE 0 END) AS type_matches,
               sum(CASE WHEN type_compatible_with_catalog=0 THEN 1 ELSE 0 END) AS type_conflicts,
               min(read_level) AS read_level_min,
               max(read_level) AS read_level_max,
               min(write_level) AS write_level_min,
               max(write_level) AS write_level_max,
               sum(CASE WHEN write_level IS NOT NULL THEN 1 ELSE 0 END) AS rows_with_write_level
        FROM config_sdo_evidence
        WHERE catalog_register_adresse IS NOT NULL
        GROUP BY catalog_register_adresse
    """
    for row in db.execute(query):
        value = dict(row)
        result[value.pop("address")] = value
    return result


def _load_obd(db: sqlite3.Connection) -> dict[str, dict[str, Any]]:
    """Read the direct normalized OBD access declaration for every address."""
    return {
        row[0]: {
            "readable_declared": bool(row[1]),
            "isreadonly": bool(row[2]),
            "writable_declared": bool(row[3]),
        }
        for row in db.execute(
            "SELECT register_adresse, readable_declared, read_only_declared, writable_declared "
            "FROM register_catalog"
        )
    }


def _rxdx_inventory(directory: Path | None) -> dict[str, Any]:
    """Count unique local exports without retaining paths, hashes, or payloads."""
    if directory is None:
        return {
            "status": "not_scanned",
            "files": None,
            "unique_contents": None,
            "duplicates": None,
            "r_w_metadata_ingested": False,
        }
    paths = sorted(
        path for path in directory.rglob("*") if path.is_file() and path.suffix.lower() == ".rxdx"
    )
    unique_files: dict[bytes, Path] = {}
    for path in paths:
        digest = hashlib.sha256()
        with path.open("rb") as source:
            for chunk in iter(lambda: source.read(1024 * 1024), b""):
                digest.update(chunk)
        unique_files.setdefault(digest.digest(), path)
    xml_exports = 0
    zip_archives = 0
    zip_members = 0
    zip_aes_members = 0
    zip_other_encrypted_members = 0
    zip_plain_members = 0
    zip_metadata_errors = 0
    for path in unique_files.values():
        with path.open("rb") as source:
            prefix = source.read(4096)
        if (
            prefix.lstrip(b"\xef\xbb\xbf\x00 \t\r\n")
            .lower()
            .startswith((b"<?xml", b"<data.file", b"<data_file"))
        ):
            xml_exports += 1
            continue
        if not prefix.startswith(b"PK\x03\x04"):
            continue
        zip_archives += 1
        try:
            with zipfile.ZipFile(path) as archive:
                for member in archive.infolist():
                    zip_members += 1
                    extra = member.extra
                    has_aes = False
                    offset = 0
                    while offset + 4 <= len(extra):
                        field_id = int.from_bytes(extra[offset : offset + 2], "little")
                        field_size = int.from_bytes(extra[offset + 2 : offset + 4], "little")
                        if offset + 4 + field_size > len(extra):
                            break
                        if field_id == 0x9901:
                            has_aes = True
                            break
                        offset += 4 + field_size
                    if has_aes:
                        zip_aes_members += 1
                    elif member.flag_bits & 1:
                        zip_other_encrypted_members += 1
                    else:
                        zip_plain_members += 1
        except (OSError, zipfile.BadZipFile):
            zip_metadata_errors += 1
    return {
        "status": "raw_exports_found_not_ingested" if paths else "no_files_found",
        "files": len(paths),
        "unique_contents": len(unique_files),
        "duplicates": len(paths) - len(unique_files),
        "unique_plaintext_xml_exports": xml_exports,
        "unique_zip_archives": zip_archives,
        "zip_members": zip_members,
        "zip_aes_members": zip_aes_members,
        "zip_other_encrypted_members": zip_other_encrypted_members,
        "zip_plain_members": zip_plain_members,
        "zip_metadata_errors": zip_metadata_errors,
        "r_w_metadata_ingested": False,
    }


def _rxdx_category(inventory: dict[str, Any]) -> str:
    """Keep located-but-unparsed primary files distinct from absent sources."""
    if inventory["status"] == "raw_exports_found_not_ingested":
        return "rxdx_primary_exports_located_not_parsed"
    if inventory["status"] == "no_files_found":
        return "rxdx_no_local_exports_found"
    return "rxdx_inventory_not_scanned"


def _iae_inventory(directory: Path | None, normalized_hashes: set[str]) -> dict[str, Any]:
    """Compare local IAE content hashes to indexed source hashes, without outputting them."""
    if directory is None:
        return {
            "status": "not_scanned",
            "files": None,
            "unique_contents": None,
            "duplicates": None,
            "normalized_unique_sources": len(normalized_hashes),
            "matching_unique_contents": None,
        }
    paths = sorted(
        path for path in directory.rglob("*") if path.is_file() and path.suffix.lower() == ".iae"
    )
    hashes: set[str] = set()
    for path in paths:
        digest = hashlib.sha256()
        with path.open("rb") as source:
            for chunk in iter(lambda: source.read(1024 * 1024), b""):
                digest.update(chunk)
        hashes.add(digest.hexdigest())
    return {
        "status": "matched" if hashes == normalized_hashes else "coverage_differs",
        "files": len(paths),
        "unique_contents": len(hashes),
        "duplicates": len(paths) - len(hashes),
        "normalized_unique_sources": len(normalized_hashes),
        "matching_unique_contents": len(hashes & normalized_hashes),
        "local_only_contents": len(hashes - normalized_hashes),
        "database_only_sources": len(normalized_hashes - hashes),
    }


def _rxdx_cache_evidence(
    directory: Path | None,
) -> tuple[dict[str, dict[str, Any]], dict[str, Any]]:
    """Read extracted PCST cache XML as presence/type/UI-access metadata only."""
    if directory is None:
        return {}, {
            "status": "not_scanned",
            "files": None,
            "unique_contents": None,
            "duplicates": None,
        }
    paths = sorted(path for path in directory.rglob("*.xml") if path.is_file())
    unique: dict[bytes, Path] = {}
    for path in paths:
        digest = hashlib.sha256(path.read_bytes()).digest()
        unique.setdefault(digest, path)
    result: dict[str, dict[str, Any]] = defaultdict(
        lambda: {
            "occurrences": 0,
            "device_families": set(),
            "wire_types": set(),
            "access_flags": set(),
            "config_readonly": Counter(),
        }
    )
    config_inventory: Counter[str] = Counter()
    config_groups = {
        "identification": "identification",
        "sample": "sample",
        "parameter": "parameters",
        "failure": "errors",
        "error": "errors",
        "warning": "errors",
        "counter": "counters",
        "status": "status",
        "configuration.nr": "configuration.nr",
        "signal": "signals",
    }
    boiler_families = {
        "0210": "EHC-16",
        "1403": "MK3",
        "190a": "SCB-10",
        "1e16": "GTW-Bluetooth",
        "2101": "unidentified-0x2101",
    }
    access_keys = {"isreadonly", "readonly", "writable", "writelevel", "write_level", "access"}
    for path in unique.values():
        root = ElementTree.parse(path).getroot()
        try:
            boiler_code = f"{int(path.name.split('-0x', 1)[1].split('-', 1)[0], 16):04x}"
        except (IndexError, ValueError):
            boiler_code = ""
        family = boiler_families.get(boiler_code, "unknown")
        for element in root.iter("sdo"):
            attrs = element.attrib
            if "index" not in attrs:
                continue
            try:
                index = int(attrs["index"], 16)
                subindex = int(attrs.get("subindex", "0"), 16)
            except ValueError:
                continue
            address = f"{index:04x}:{subindex:02x}"
            item = result[address]
            item["occurrences"] += 1
            item["device_families"].add(family)
            wire_type = attrs.get("type") or attrs.get("Type")
            if wire_type:
                item["wire_types"].add(wire_type.strip().upper())
            item["access_flags"].update(key.lower() for key in attrs if key.lower() in access_keys)

        # PCST stores its per-field readonly declaration on a configuration
        # element. Resolve its function byte offset through the corresponding
        # BDRCAN SDO byte range; this is UI/config metadata, not device access
        # authorization. De-duplicate complete cache XML content above.
        configuration = root.find("configuration")
        bdrcan = root.find("bdrcan")
        if configuration is not None and bdrcan is not None:
            for configs in configuration.findall("configurations"):
                group = configs.attrib.get("name", "")
                sdo_parent = bdrcan.find(config_groups.get(group, group))
                sdos = list(sdo_parent.findall("sdo")) if sdo_parent is not None else []
                for config in configs.findall("config"):
                    readonly = config.attrib.get("readonly")
                    if readonly is None:
                        continue
                    normalized_readonly = readonly.strip().lower()
                    if normalized_readonly not in {"true", "false"}:
                        config_inventory["readonly_unrecognized"] += 1
                        continue
                    config_inventory[f"readonly_{normalized_readonly}"] += 1
                    function = config.find("function")
                    if function is None or function.attrib.get("byte") is None:
                        config_inventory["readonly_unmapped"] += 1
                        continue
                    try:
                        offset = int(function.attrib["byte"])
                    except ValueError:
                        config_inventory["readonly_unmapped"] += 1
                        continue
                    sdo = None
                    for item in sdos:
                        try:
                            byte_start = int(item.attrib["byte"])
                            byte_end = byte_start + int(item.attrib["length"])
                        except (KeyError, ValueError):
                            continue
                        if byte_start <= offset < byte_end:
                            sdo = item
                            break
                    if sdo is None or "index" not in sdo.attrib:
                        config_inventory["readonly_unmapped"] += 1
                        continue
                    try:
                        index = int(sdo.attrib["index"], 16)
                        subindex = int(sdo.attrib.get("subindex", "0"), 16)
                        address = f"{index:04x}:{subindex:02x}"
                    except ValueError:
                        config_inventory["readonly_unmapped"] += 1
                        continue
                    result[address]["config_readonly"][normalized_readonly] += 1
                    config_inventory["readonly_mapped"] += 1
    normalized = {
        address: {
            "status": "observed_in_ingested_cache_subset",
            "classification": (
                "presence_type_and_configuration_readonly_metadata"
                if item["config_readonly"]
                else "presence_and_wire_type_only"
            ),
            "occurrences": item["occurrences"],
            "device_families": sorted(item["device_families"]),
            "wire_types": sorted(item["wire_types"]),
            "access_metadata_fields": sorted(item["access_flags"]),
            "configuration_readonly_values": sorted(item["config_readonly"]),
            "configuration_readonly_occurrences": sum(item["config_readonly"].values()),
            "configuration_readonly_status": (
                "mixed"
                if len(item["config_readonly"]) > 1
                else "readonly_true"
                if item["config_readonly"].get("true")
                else "readonly_false"
                if item["config_readonly"].get("false")
                else "not_observed"
            ),
            "configuration_readonly_source_scope": "pcst_configuration_ui_metadata",
            "writability": "unassessed",
            "can_promote_write": False,
        }
        for address, item in result.items()
    }
    return normalized, {
        "status": "parsed" if unique else "no_extracted_cache_files_found",
        "files": len(paths),
        "unique_contents": len(unique),
        "duplicates": len(paths) - len(unique),
        "register_occurrences": sum(item["occurrences"] for item in result.values()),
        "unique_addresses": len(result),
        "access_metadata_fields_present": sorted(
            {field for item in result.values() for field in item["access_flags"]}
        ),
        "configuration_readonly_entries": config_inventory["readonly_true"]
        + config_inventory["readonly_false"]
        + config_inventory["readonly_unrecognized"],
        "configuration_readonly_true": config_inventory["readonly_true"],
        "configuration_readonly_false": config_inventory["readonly_false"],
        "configuration_readonly_mapped": config_inventory["readonly_mapped"],
        "configuration_readonly_unmapped": config_inventory["readonly_unmapped"],
        "configuration_readonly_registers": sum(
            bool(item["config_readonly"]) for item in result.values()
        ),
        "configuration_readonly_conflicting_registers": sum(
            len(item["config_readonly"]) > 1 for item in result.values()
        ),
    }


def build_report(
    registry_path: Path,
    source_db: Path,
    rxdx_directory: Path | None = None,
    iae_directory: Path | None = None,
    rxdx_cache_directory: Path | None = None,
) -> dict[str, Any]:
    registry = json.loads(registry_path.read_text(encoding="utf-8"))
    # The normalized/source database is evidence input. Do not create journals
    # or mutate it while building the sanitized comparison.
    source_uri = f"{source_db.resolve().as_uri()}?mode=ro&immutable=1"
    with sqlite3.connect(source_uri, uri=True) as db:
        iae = _load_iae(db)
        config = _load_config(db)
        obd = _load_obd(db)
        source_kinds = {
            row[0]: row[1]
            for row in db.execute(
                "SELECT source_kind, count(*) FROM profitool_sources GROUP BY source_kind"
            )
        }
        iae_source_hashes = {
            row[0]
            for row in db.execute(
                "SELECT sha256 FROM profitool_sources "
                "WHERE source_kind='profitool_iae_runtime_definition'"
            )
        }
    rxdx_inventory = _rxdx_inventory(rxdx_directory)
    rxdx_cache_evidence, rxdx_cache_inventory = _rxdx_cache_evidence(rxdx_cache_directory)
    rows = []
    for register in sorted(registry["registers"], key=lambda row: row["address"]):
        address = register["address"]
        evidence = iae.get(address, [])
        obd_row = obd.get(address)
        declared_writable = bool(register["access"]["write"])
        family_rows = register["evidence"].get("devices", [])
        all_family_evidence_write = bool(family_rows) and all(
            item.get("writable_all") is True for item in family_rows
        )
        validated = register["safety"]["write"] == "validated"
        rows.append(
            {
                "address": address,
                "code": register.get("code"),
                "device_families": sorted(register["evidence"].get("device_families", [])),
                "obd_1_47": obd_row,
                "openrbus_access_declaration": {
                    "readable": bool(register["access"]["read"]),
                    "writable": declared_writable,
                    "matches_obd": bool(
                        obd_row
                        and obd_row["readable_declared"] == bool(register["access"]["read"])
                        and obd_row["writable_declared"] == declared_writable
                    ),
                },
                "iae": {
                    "status": "present" if evidence else "not_mapped",
                    "rows": evidence,
                },
                "manufacturer_config": config.get(address),
                "rxdx": rxdx_cache_evidence.get(
                    address,
                    {
                        "status": "not_observed_in_ingested_cache_subset",
                        "classification": "subset_absence_only",
                        "device_families": [],
                        "wire_types": [],
                        "configuration_readonly_values": [],
                        "configuration_readonly_occurrences": 0,
                        "configuration_readonly_status": "not_observed",
                        "configuration_readonly_source_scope": "pcst_configuration_ui_metadata",
                        "writability": "unassessed",
                        "can_promote_write": False,
                    },
                ),
                "openrbus": {
                    "safety_class": register["safety"]["write"],
                    "write_allowed": bool(
                        declared_writable and validated and all_family_evidence_write
                    ),
                    "write_allowed_reason": (
                        "all_required_gates_passed"
                        if declared_writable and validated and all_family_evidence_write
                        else "write_disabled_until_primary_safety_validation"
                    ),
                },
                "category": _category(register, evidence, obd_row),
            }
        )
    categories = Counter(row["category"] for row in rows)
    iae_registers = sum(row["iae"]["status"] == "present" for row in rows)
    return {
        "schema": "openrbus.rw_evidence_comparison.v3",
        "registry_revision": registry["metadata"]["revision"],
        "sources": {
            "obd": "canonical OpenRBus registry derived from Android OBD 1.47",
            "iae": (
                "recovered normalized IAE evidence; source records identified "
                "by family/address only"
            ),
            "manufacturer_config": "recovered normalized manufacturer config SDO evidence",
            "rxdx": (
                "offline-decoded PCST cache XML contributes SDO address/type and mapped "
                "configuration readonly UI metadata; SDO access flags and write-level "
                "fields are absent, and no RXDX metadata authorizes writes"
            ),
            "source_database_kinds": source_kinds,
            "iae_local_inventory": _iae_inventory(iae_directory, iae_source_hashes),
            "rxdx_local_inventory": rxdx_inventory,
            "rxdx_cache_inventory": rxdx_cache_inventory,
        },
        "summary": {
            "registers": len(rows),
            "iae_mapped_registers": iae_registers,
            "iae_unmapped_registers": len(rows) - iae_registers,
            "obd_registry_access_conflicts": sum(
                row["category"] == "obd_registry_access_conflict" for row in rows
            ),
            "openrbus_write_allowed": sum(row["openrbus"]["write_allowed"] for row in rows),
            "categories": dict(sorted(categories.items())),
            "rxdx_evidence_categories": dict(
                sorted(Counter(row["rxdx"]["classification"] for row in rows).items())
            ),
            "rxdx_observed_registers": sum(bool(row["rxdx"].get("occurrences")) for row in rows),
            "rxdx_not_observed_registers": sum(
                not bool(row["rxdx"].get("occurrences")) for row in rows
            ),
        },
        "registers": rows,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--registry", type=Path, default=DEFAULT_REGISTRY)
    parser.add_argument("--source-db", type=Path, required=True)
    parser.add_argument(
        "--rxdx-dir",
        type=Path,
        help="optional local RXDX directory; only counts unique file content, never reads payloads",
    )
    parser.add_argument(
        "--iae-dir",
        type=Path,
        help="optional local IAE directory; compares unique hashes without exposing them",
    )
    parser.add_argument(
        "--rxdx-cache-dir",
        type=Path,
        help=(
            "optional directory of already-extracted PCST cache XML; reports only "
            "addresses, families and wire types"
        ),
    )
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    report = build_report(
        args.registry, args.source_db, args.rxdx_dir, args.iae_dir, args.rxdx_cache_dir
    )
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(report, ensure_ascii=False, sort_keys=True, indent=2) + "\n",
        encoding="utf-8",
    )
    print(json.dumps(report["summary"], sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
