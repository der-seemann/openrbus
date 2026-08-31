from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

import pytest

from openrbus.errors import RegistryError, UnknownRegisterError
from openrbus.protocol.canip import ObjectAddress
from openrbus.registry import (
    AccessLevel,
    AccessOperation,
    RegisterAddress,
    Registry,
    WireType,
    WriteSafety,
)

ROOT = Path(__file__).resolve().parents[1]
CANONICAL = ROOT / "data/registry/registry-v1.json"
PACKAGED = ROOT / "src/openrbus/data/registry-v1.json"


@pytest.fixture(scope="module")
def registry() -> Registry:
    return Registry.load_default()


def test_registry_is_complete_typed_and_licensed(registry: Registry) -> None:
    assert len(registry) == 3_066
    assert len(registry.candidates) == 47
    assert len(registry.enums) == 204
    assert len(registry.structures) == 55
    assert registry.metadata.license_id == "CC-BY-4.0"
    assert registry.metadata.dictionary_revision == "1.47"
    assert RegisterAddress is ObjectAddress

    assert sum(item.code is not None for item in registry.registers) == 1_800
    assert sum(item.access.writable_declared for item in registry.registers) == 2_832
    assert all(item.access.readable_declared for item in registry.registers)
    assert all(item.wire.length > 0 for item in registry.registers)
    assert all(item.names.de and item.names.en for item in registry.registers)
    assert max(len(item.names.de) for item in registry.registers) <= 20
    assert max(len(item.names.en) for item in registry.registers) <= 20


def test_exact_semantic_and_storage_types(registry: Registry) -> None:
    enum = registry.get("3001:00")
    assert enum.wire.type is WireType.ENUMERATION
    assert enum.wire.storage is WireType.UINT8
    assert enum.wire.length == 1
    assert enum.wire.enum_name == "BlockingFunction"

    structure = registry.get("3506:00")
    assert structure.wire.type is WireType.STRUCT
    assert structure.wire.storage is WireType.OCTETSTRING
    assert structure.wire.length == 19
    assert registry.structure(structure.wire.struct_name or "") is not None

    time_of_day = registry.get("504b:00")
    assert time_of_day.wire.type is WireType.TIME_OF_DAY
    assert time_of_day.wire.storage is WireType.OCTETSTRING
    assert time_of_day.wire.length == 6


def test_lookup_codes_arrays_and_candidates(registry: Registry) -> None:
    assert registry.get((0x3001, 0)).address == ObjectAddress(0x3001, 0)
    assert registry.get(ObjectAddress(0x1018, 1)).address == ObjectAddress(0x1018, 0)
    assert len(registry.by_code("hp034")) == 2

    candidate = registry.candidate("2212:00")
    assert candidate is not None
    assert candidate.evidence_count == 1
    with pytest.raises(UnknownRegisterError):
        registry.get("2212:00")


def test_constraints_conflicts_and_safety_are_explicit(registry: Registry) -> None:
    constrained = registry.get("3001:00")
    assert constrained.constraint is not None
    assert str(constrained.constraint.minimum) == "0"
    assert str(constrained.constraint.maximum) == "11"
    assert constrained.constraint.precision == 0

    range_conflict = registry.get("3030:00")
    assert range_conflict.constraint is None
    assert len(range_conflict.constraint_variants) == 2

    conflict_registers = [item for item in registry.registers if item.evidence.type_conflict]
    assert len(conflict_registers) == 78
    assert sum(len(item.type_variants) for item in conflict_registers) == 97
    assert (
        sum(variant.evidence_count for item in conflict_registers for variant in item.type_variants)
        == 223
    )
    assert sum(len(item.evidence.devices) for item in registry.registers) == 1_451

    for item in registry.registers:
        if item.access.writable_declared:
            assert item.safety.write is WriteSafety.UNVERIFIED
            assert item.safety.requires_unsafe_opt_in
        else:
            assert item.safety.write is WriteSafety.READ_ONLY
            assert not item.safety.requires_unsafe_opt_in


def test_device_access_levels_are_exact_but_catalog_coverage_is_partial(
    registry: Registry,
) -> None:
    device_rows = [row for item in registry.registers for row in item.evidence.devices]
    assert len(device_rows) == 1_451
    assert len({row.address for row in device_rows}) == 1_189
    assert all(row.required_read_level is not None for row in device_rows)
    assert all(row.required_write_level is not None for row in device_rows)

    writable = [item for item in registry.registers if item.access.writable_declared]
    assert len(writable) == 2_832
    assert sum(bool(item.evidence.devices) for item in writable) == 692

    dhw = registry.access_requirement("3654:01", AccessOperation.WRITE, device_family="Ehc-16")
    assert dhw.required_level is AccessLevel.USER
    assert not dhw.is_higher_risk

    night = registry.get("340b:03").access_requirement(
        ObjectAddress(0x340B, 3), AccessOperation.WRITE, device_family="Scb-10"
    )
    assert night.required_level is AccessLevel.INSTALLER
    assert night.is_higher_risk

    ambiguous = registry.get("200e:00").access_requirement(
        ObjectAddress(0x200E, 0), AccessOperation.WRITE
    )
    assert ambiguous.is_ambiguous
    assert ambiguous.levels == (AccessLevel.USER, AccessLevel.INSTALLER)
    ehc = registry.get("200e:00").access_requirement(
        ObjectAddress(0x200E, 0), AccessOperation.WRITE, device_family="Ehc-16"
    )
    assert ehc.required_level is AccessLevel.INSTALLER


def test_incomplete_access_evidence_remains_unknown_not_ambiguous() -> None:
    raw = json.loads(PACKAGED.read_text(encoding="utf-8"))
    register = next(item for item in raw["registers"] if item["address"] == "3654:00")
    row = next(item for item in register["evidence"]["devices"] if item["address"] == "3654:01")
    row["write_level_min"] = None
    row["write_level_max"] = None
    requirement = Registry.from_mapping(raw).access_requirement(
        "3654:01", AccessOperation.WRITE, device_family="Ehc-16"
    )
    assert not requirement.is_known
    assert not requirement.is_ambiguous
    assert requirement.required_level is None


def test_validated_writable_safety_does_not_require_unsafe_opt_in() -> None:
    raw = json.loads(PACKAGED.read_text(encoding="utf-8"))
    register = next(item for item in raw["registers"] if item["address"] == "3425:00")
    register["safety"] = {"write": "validated", "requires_unsafe": False}
    parsed = Registry.from_mapping(raw).get("3425:01")
    assert parsed.safety.write is WriteSafety.VALIDATED
    assert not parsed.safety.requires_unsafe_opt_in


def test_packaged_copy_is_data_equivalent_and_strictly_validated() -> None:
    canonical = json.loads(CANONICAL.read_text(encoding="utf-8"))
    packaged = json.loads(PACKAGED.read_text(encoding="utf-8"))
    assert canonical == packaged

    malformed = dict(packaged)
    malformed["schema"] = "unknown"
    with pytest.raises(RegistryError, match="unsupported registry schema"):
        Registry.from_mapping(malformed)


def test_publication_boundary_contains_no_sensitive_artifacts() -> None:
    data = json.loads(CANONICAL.read_text(encoding="utf-8"))
    forbidden_keys = {
        "capture",
        "key",
        "mac",
        "raw",
        "raw_value",
        "runtime_identifier",
        "serial_number",
        "sha256",
        "source",
        "source_hash",
        "source_id",
        "source_path",
    }
    forbidden_value_patterns = (
        re.compile(r"(?i)\b(?:[0-9a-f]{2}:){5}[0-9a-f]{2}\b"),
        re.compile(r"(?i)(?:^|[/\\])(?:home|users|local|captures|reversing)(?:[/\\])"),
        re.compile(r"(?i)\.(?:apk|aab|exe|dll|so|pcap|pcapng|iae|rxdx)(?:$|\b)"),
        re.compile(r"(?i)\b[0-9a-f]{32,}\b"),
    )

    def walk(value: Any) -> None:
        if isinstance(value, dict):
            assert forbidden_keys.isdisjoint(value)
            for child in value.values():
                walk(child)
        elif isinstance(value, list):
            for child in value:
                walk(child)
        elif isinstance(value, str):
            for pattern in forbidden_value_patterns:
                assert pattern.search(value) is None, value

    walk(data)
