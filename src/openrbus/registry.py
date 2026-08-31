"""Typed access to the packaged OpenRBus register registry.

The runtime registry is JSON-only.  It deliberately contains normalized facts
and short labels, never vendor files, source paths, hashes, captures, keys, or
runtime device identifiers.
"""

from __future__ import annotations

import json
import re
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from enum import IntEnum, StrEnum
from importlib import resources
from pathlib import Path
from types import MappingProxyType
from typing import Any, TypeAlias

from .errors import RegistryError, UnknownRegisterError
from .protocol.canip import ObjectAddress

REGISTRY_SCHEMA = "openrbus.registry.v1"
DEFAULT_REGISTRY_RESOURCE = "data/registry-v1.json"
_ADDRESS_RE = re.compile(r"^(?:0x)?([0-9a-fA-F]{1,4}):(?:0x)?([0-9a-fA-F]{1,2})$")
_CODE_RE = re.compile(r"^[A-Z]{2}\d{3}$")

# A register address and a protocol object address are the same public type.
RegisterAddress: TypeAlias = ObjectAddress


class WireType(StrEnum):
    """Semantic and storage types represented by the public registry."""

    UINT8 = "UINT8"
    UINT16 = "UINT16"
    UINT32 = "UINT32"
    INT8 = "INT8"
    INT16 = "INT16"
    INT32 = "INT32"
    ENUMERATION = "ENUMERATION"
    STRUCT = "STRUCT"
    OCTETSTRING = "OCTETSTRING"
    VISIBLESTRING = "VISIBLESTRING"
    TIME_OF_DAY = "TIME_OF_DAY"


class WriteSafety(StrEnum):
    """Conservative write classification carried by every register."""

    READ_ONLY = "read_only"
    UNVERIFIED = "unverified"
    VALIDATED = "validated"


class AccessLevel(IntEnum):
    """Numeric device access level with confirmed public role names for 1..3.

    Device definitions also contain levels outside the app's documented
    user/installer/professional model.  Those values remain deliberately
    opaque and numeric instead of receiving speculative role names.
    """

    LEVEL_0 = 0
    USER = 1
    INSTALLER = 2
    PROFESSIONAL = 3
    LEVEL_4 = 4
    LEVEL_5 = 5
    LEVEL_6 = 6
    LEVEL_7 = 7
    LEVEL_8 = 8
    LEVEL_9 = 9
    LEVEL_10 = 10
    LEVEL_11 = 11
    LEVEL_12 = 12
    LEVEL_13 = 13
    LEVEL_14 = 14
    LEVEL_15 = 15

    @property
    def label(self) -> str:
        """Return a non-speculative user-facing label."""

        return {
            AccessLevel.USER: "user",
            AccessLevel.INSTALLER: "installer",
            AccessLevel.PROFESSIONAL: "professional",
        }.get(self, f"level {int(self)}")

    @property
    def is_higher_risk(self) -> bool:
        """Whether this level is above ordinary user access."""

        return self >= AccessLevel.INSTALLER


class AccessOperation(StrEnum):
    """Object operation for which an access level is required."""

    READ = "read"
    WRITE = "write"


@dataclass(frozen=True, slots=True)
class LocalizedNames:
    """Short public labels; longer proprietary descriptions are excluded."""

    de: str
    en: str

    def for_locale(self, locale: str = "en") -> str:
        """Return a short label with English fallback for unknown locales."""

        normalized = locale.replace("_", "-").casefold()
        return self.de if normalized == "de" or normalized.startswith("de-") else self.en


@dataclass(frozen=True, slots=True)
class WireDefinition:
    """Wire representation of a register value."""

    type: WireType
    storage: WireType
    length: int
    gain: Decimal | None
    unit: str | None
    is_array: bool
    max_items: int | None
    enum_name: str | None
    struct_name: str | None

    def __post_init__(self) -> None:
        if self.length <= 0:
            raise RegistryError("wire length must be positive")
        if self.gain is not None and self.gain <= 0:
            raise RegistryError("wire gain must be positive")
        if self.is_array != (self.max_items is not None):
            raise RegistryError("array registers require max_items and scalars must omit it")
        if self.max_items is not None and self.max_items <= 0:
            raise RegistryError("array max_items must be positive")


@dataclass(frozen=True, slots=True)
class AccessDefinition:
    """Global dictionary access declarations, not device-specific guarantees."""

    readable_declared: bool
    writable_declared: bool


@dataclass(frozen=True, slots=True)
class ValueConstraint:
    """A range/precision declaration and the device families supporting it."""

    minimum: Decimal | None
    maximum: Decimal | None
    precision: int | None
    device_families: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if self.minimum is not None and self.maximum is not None and self.minimum > self.maximum:
            raise RegistryError("constraint minimum exceeds maximum")
        if self.precision is not None and self.precision < 0:
            raise RegistryError("precision must be non-negative")


@dataclass(frozen=True, slots=True)
class SafetyDefinition:
    """Write safety classification for the public client."""

    write: WriteSafety
    requires_unsafe_opt_in: bool

    def __post_init__(self) -> None:
        expected = self.write is WriteSafety.UNVERIFIED
        if self.requires_unsafe_opt_in is not expected:
            raise RegistryError("unsafe opt-in must match the unverified write classification")


@dataclass(frozen=True, slots=True)
class DeviceEvidence:
    """Definition evidence for a device family, without runtime identifiers."""

    family: str
    address: RegisterAddress
    loaded: bool | None
    writable_any: bool | None
    writable_all: bool | None
    read_level_min: AccessLevel | None
    read_level_max: AccessLevel | None
    write_level_min: AccessLevel | None
    write_level_max: AccessLevel | None

    def __post_init__(self) -> None:
        for label, minimum, maximum in (
            ("read", self.read_level_min, self.read_level_max),
            ("write", self.write_level_min, self.write_level_max),
        ):
            if (minimum is None) != (maximum is None):
                raise RegistryError(f"device {label} access level requires both bounds")
            if minimum is not None and maximum is not None and minimum > maximum:
                raise RegistryError(f"device {label} access level minimum exceeds maximum")

    @property
    def required_read_level(self) -> AccessLevel | None:
        """Return the exact read level, or ``None`` for missing/ranged evidence."""

        return self.read_level_min if self.read_level_min == self.read_level_max else None

    @property
    def required_write_level(self) -> AccessLevel | None:
        """Return the exact write level, or ``None`` for missing/ranged evidence."""

        return self.write_level_min if self.write_level_min == self.write_level_max else None


@dataclass(frozen=True, slots=True)
class AccessRequirement:
    """Device-evidence access requirement for one concrete object address."""

    operation: AccessOperation
    levels: tuple[AccessLevel, ...]
    device_families: tuple[str, ...]
    complete: bool

    @property
    def required_level(self) -> AccessLevel | None:
        """Return one unambiguous level, otherwise ``None``."""

        return self.levels[0] if self.complete and len(self.levels) == 1 else None

    @property
    def is_known(self) -> bool:
        return self.complete and bool(self.levels)

    @property
    def is_ambiguous(self) -> bool:
        return len(self.levels) > 1

    @property
    def is_higher_risk(self) -> bool:
        level = self.required_level
        return level is not None and level.is_higher_risk


@dataclass(frozen=True, slots=True)
class TypeVariant:
    """A device-profile type that conflicts with the canonical dictionary."""

    address: RegisterAddress
    wire_type: WireType
    length: int | None
    profiles: tuple[str, ...]
    evidence_count: int


@dataclass(frozen=True, slots=True)
class EvidenceDefinition:
    """Sanitized provenance summary for a register."""

    categories: tuple[str, ...]
    config_occurrences: int
    config_profiles: tuple[str, ...]
    definition_occurrences: int
    device_families: tuple[str, ...]
    devices: tuple[DeviceEvidence, ...]
    type_conflict: bool


@dataclass(frozen=True, slots=True)
class RegisterDefinition:
    """One canonical register definition."""

    address: RegisterAddress
    code: str | None
    names: LocalizedNames
    wire: WireDefinition
    access: AccessDefinition
    constraint: ValueConstraint | None
    constraint_variants: tuple[ValueConstraint, ...]
    safety: SafetyDefinition
    evidence: EvidenceDefinition
    type_variants: tuple[TypeVariant, ...]

    def __post_init__(self) -> None:
        if self.code is not None and _CODE_RE.fullmatch(self.code) is None:
            raise RegistryError(f"invalid short code {self.code!r}")
        if not self.names.de or not self.names.en:
            raise RegistryError(f"register {self.address} lacks a DE/EN short name")
        if self.access.writable_declared == (self.safety.write is WriteSafety.READ_ONLY):
            raise RegistryError("write safety classification conflicts with declared access")

    def name(self, locale: str = "en") -> str:
        """Return the localized short name."""

        return self.names.for_locale(locale)

    def access_requirement(
        self,
        address: RegisterAddress,
        operation: AccessOperation | str = AccessOperation.WRITE,
        *,
        device_family: str | None = None,
    ) -> AccessRequirement:
        """Resolve static access-level evidence for one concrete address.

        No device evidence means an unknown, non-blocking requirement.  More
        than one exact level is reported as ambiguous so callers can require a
        device family instead of silently choosing the least restrictive row.
        """

        resolved_operation = AccessOperation(operation)
        rows = tuple(row for row in self.evidence.devices if row.address == address)
        if device_family is not None:
            rows = tuple(row for row in rows if row.family.casefold() == device_family.casefold())
        levels: list[AccessLevel] = []
        complete = bool(rows)
        for row in rows:
            level = (
                row.required_read_level
                if resolved_operation is AccessOperation.READ
                else row.required_write_level
            )
            if level is None:
                complete = False
            else:
                levels.append(level)
        return AccessRequirement(
            operation=resolved_operation,
            levels=tuple(sorted(set(levels))),
            device_families=tuple(sorted({row.family for row in rows})),
            complete=complete,
        )


@dataclass(frozen=True, slots=True)
class CandidateWireVariant:
    """A wire shape observed for a non-canonical legacy candidate."""

    wire_type: WireType
    length: int | None


@dataclass(frozen=True, slots=True)
class RegisterCandidate:
    """An address with configuration evidence but no canonical definition."""

    address: RegisterAddress
    wire_variants: tuple[CandidateWireVariant, ...]
    profiles: tuple[str, ...]
    evidence_count: int


@dataclass(frozen=True, slots=True)
class EnumDefinition:
    """Numeric values for an enumeration; proprietary prose is excluded."""

    name: str
    values: tuple[int, ...]


@dataclass(frozen=True, slots=True)
class StructureField:
    """One bit field in a structured wire value."""

    name: str
    wire_type: WireType
    bit_offset: int
    bit_length: int
    gain: Decimal | None
    unit: str | None
    enum_name: str | None


@dataclass(frozen=True, slots=True)
class StructureDefinition:
    """Bit layout for a structured wire value."""

    name: str
    length: int
    fields: tuple[StructureField, ...]


@dataclass(frozen=True, slots=True)
class RegistryMetadata:
    """Version and licensing information for the normalized dataset."""

    schema: str
    revision: str
    dictionary_revision: str
    license_id: str
    license_url: str
    attribution: str


class Registry:
    """Immutable, indexed view of a normalized OpenRBus registry."""

    def __init__(
        self,
        metadata: RegistryMetadata,
        registers: Iterable[RegisterDefinition],
        *,
        candidates: Iterable[RegisterCandidate] = (),
        enums: Iterable[EnumDefinition] = (),
        structures: Iterable[StructureDefinition] = (),
    ) -> None:
        register_tuple = tuple(registers)
        by_address = {item.address: item for item in register_tuple}
        if len(by_address) != len(register_tuple):
            raise RegistryError("duplicate canonical register address")
        if tuple(sorted(by_address)) != tuple(by_address):
            raise RegistryError("registers must be ordered by address")

        by_code: dict[str, list[RegisterDefinition]] = {}
        for item in register_tuple:
            if item.code is not None:
                by_code.setdefault(item.code, []).append(item)

        candidate_tuple = tuple(candidates)
        candidate_map = {item.address: item for item in candidate_tuple}
        if len(candidate_map) != len(candidate_tuple):
            raise RegistryError("duplicate candidate register address")

        enum_tuple = tuple(enums)
        enum_map = {item.name: item for item in enum_tuple}
        if len(enum_map) != len(enum_tuple):
            raise RegistryError("duplicate enumeration name")

        structure_tuple = tuple(structures)
        structure_map = {item.name: item for item in structure_tuple}
        if len(structure_map) != len(structure_tuple):
            raise RegistryError("duplicate structure name")

        self.metadata = metadata
        self.registers = register_tuple
        self.candidates = candidate_tuple
        self.enums = enum_tuple
        self.structures = structure_tuple
        self._by_address: Mapping[RegisterAddress, RegisterDefinition] = MappingProxyType(
            by_address
        )
        self._by_code: Mapping[str, tuple[RegisterDefinition, ...]] = MappingProxyType(
            {key: tuple(value) for key, value in by_code.items()}
        )
        self._candidate_map: Mapping[RegisterAddress, RegisterCandidate] = MappingProxyType(
            candidate_map
        )
        self._enum_map: Mapping[str, EnumDefinition] = MappingProxyType(enum_map)
        self._structure_map: Mapping[str, StructureDefinition] = MappingProxyType(structure_map)

    def __len__(self) -> int:
        return len(self.registers)

    def get(
        self, address: str | RegisterAddress | tuple[int, int], *, resolve_array: bool = True
    ) -> RegisterDefinition:
        """Return a definition, optionally resolving an array element to its ``:00`` base."""

        parsed = _parse_address(address)
        direct = self._by_address.get(parsed)
        if direct is not None:
            return direct
        if resolve_array and parsed.subindex:
            base = self._by_address.get(RegisterAddress(parsed.index, 0))
            if (
                base is not None
                and base.wire.is_array
                and base.wire.max_items is not None
                and parsed.subindex <= base.wire.max_items
            ):
                return base
        raise UnknownRegisterError(f"unknown register {parsed}")

    def find(self, address: str | RegisterAddress | tuple[int, int]) -> RegisterDefinition | None:
        """Return a definition or ``None`` without suppressing malformed addresses."""

        try:
            return self.get(address)
        except UnknownRegisterError:
            return None

    def access_requirement(
        self,
        address: str | RegisterAddress | tuple[int, int],
        operation: AccessOperation | str = AccessOperation.WRITE,
        *,
        device_family: str | None = None,
    ) -> AccessRequirement:
        """Resolve access evidence through the registry's array-aware lookup."""

        parsed = _parse_address(address)
        return self.get(parsed).access_requirement(
            parsed,
            operation,
            device_family=device_family,
        )

    def by_code(self, code: str) -> tuple[RegisterDefinition, ...]:
        """Return every register using a short code; a few legacy codes are duplicated."""

        return self._by_code.get(code.upper(), ())

    def candidate(
        self, address: str | RegisterAddress | tuple[int, int]
    ) -> RegisterCandidate | None:
        """Return non-canonical candidate evidence without treating it as a register."""

        return self._candidate_map.get(_parse_address(address))

    def enum(self, name: str) -> EnumDefinition | None:
        """Return a numeric enumeration definition."""

        return self._enum_map.get(name)

    def structure(self, name: str) -> StructureDefinition | None:
        """Return a structured wire layout."""

        return self._structure_map.get(name)

    @classmethod
    def load(cls, path: str | Path) -> Registry:
        """Load and validate a public registry JSON file."""

        with Path(path).open("r", encoding="utf-8") as handle:
            raw = json.load(handle)
        return cls.from_mapping(_expect_mapping(raw, "registry"))

    @classmethod
    def load_default(cls) -> Registry:
        """Load the registry packaged in the installed wheel."""

        resource = resources.files("openrbus").joinpath(DEFAULT_REGISTRY_RESOURCE)
        with resource.open("r", encoding="utf-8") as handle:
            raw = json.load(handle)
        return cls.from_mapping(_expect_mapping(raw, "registry"))

    @classmethod
    def from_mapping(cls, raw: Mapping[str, Any]) -> Registry:
        """Construct and validate a registry from decoded JSON data."""

        schema = _expect_str(raw.get("schema"), "schema")
        if schema != REGISTRY_SCHEMA:
            raise RegistryError(f"unsupported registry schema {schema!r}")
        metadata_raw = _expect_mapping(raw.get("metadata"), "metadata")
        license_raw = _expect_mapping(metadata_raw.get("license"), "metadata.license")
        metadata = RegistryMetadata(
            schema=schema,
            revision=_expect_str(metadata_raw.get("revision"), "metadata.revision"),
            dictionary_revision=_expect_str(
                metadata_raw.get("dictionary_revision"), "metadata.dictionary_revision"
            ),
            license_id=_expect_str(license_raw.get("id"), "metadata.license.id"),
            license_url=_expect_str(license_raw.get("url"), "metadata.license.url"),
            attribution=_expect_str(license_raw.get("attribution"), "metadata.license.attribution"),
        )

        registers_raw = _expect_list(raw.get("registers"), "registers")
        candidates_raw = _expect_list(raw.get("candidates", []), "candidates")
        enums_raw = _expect_list(raw.get("enums", []), "enums")
        structures_raw = _expect_list(raw.get("structures", []), "structures")
        registry = cls(
            metadata,
            (_parse_register(item, index) for index, item in enumerate(registers_raw)),
            candidates=(_parse_candidate(item, index) for index, item in enumerate(candidates_raw)),
            enums=(_parse_enum(item, index) for index, item in enumerate(enums_raw)),
            structures=(_parse_structure(item, index) for index, item in enumerate(structures_raw)),
        )
        counts = _expect_mapping(metadata_raw.get("counts"), "metadata.counts")
        expected = {
            "registers": len(registry.registers),
            "candidates": len(registry.candidates),
            "enums": len(registry.enums),
            "structures": len(registry.structures),
        }
        for key, actual in expected.items():
            if _expect_int(counts.get(key), f"metadata.counts.{key}") != actual:
                raise RegistryError(f"metadata count mismatch for {key}")
        return registry


def compact_registry_mapping(registry: Registry, *, locale: str = "en") -> dict[str, Any]:
    """Return a deterministic, runtime-focused JSON representation."""

    rows: list[list[Any]] = []
    for item in registry.registers:
        constraint = item.constraint
        flags = 0
        flags |= 1 if item.access.readable_declared else 0
        flags |= 2 if item.access.writable_declared else 0
        flags |= 4 if item.safety.requires_unsafe_opt_in else 0
        flags |= 8 if constraint is not None else 0
        flags |= 16 if item.wire.is_array else 0
        flags |= 32 if item.evidence.type_conflict else 0
        rows.append(
            [
                item.address.index,
                item.address.subindex,
                item.code,
                item.name(locale),
                item.wire.type.value,
                item.wire.storage.value,
                item.wire.length,
                _decimal_text(item.wire.gain),
                item.wire.unit,
                _decimal_text(constraint.minimum) if constraint else None,
                _decimal_text(constraint.maximum) if constraint else None,
                constraint.precision if constraint else None,
                item.wire.max_items,
                flags,
            ]
        )
    return {
        "schema": "openrbus.registry.compact.v1",
        "revision": registry.metadata.revision,
        "license": registry.metadata.license_id,
        "locale": locale,
        "columns": [
            "index",
            "subindex",
            "code",
            "name",
            "type",
            "storage",
            "length",
            "gain",
            "unit",
            "minimum",
            "maximum",
            "precision",
            "max_items",
            "flags",
        ],
        "flag_bits": {
            "read": 1,
            "write": 2,
            "unsafe": 4,
            "range": 8,
            "array": 16,
            "type_conflict": 32,
        },
        "registers": rows,
    }


def export_compact_json(registry: Registry, *, locale: str = "en") -> str:
    """Serialize a stable, minified registry for constrained runtimes."""

    return (
        json.dumps(
            compact_registry_mapping(registry, locale=locale),
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )
        + "\n"
    )


def export_c_header(
    registry: Registry, *, locale: str = "en", symbol_prefix: str = "OPENRBUS"
) -> str:
    """Generate a deterministic C/C++ header suitable for ESP32 flash tables."""

    if re.fullmatch(r"[A-Z][A-Z0-9_]*", symbol_prefix) is None:
        raise ValueError("symbol_prefix must be an uppercase C identifier")
    wire_types = tuple(WireType)
    wire_ids = {wire_type: index for index, wire_type in enumerate(wire_types)}
    lines = [
        "/* Generated by OpenRBus. Registry data: CC BY 4.0. */",
        "#ifndef OPENRBUS_REGISTRY_GENERATED_H",
        "#define OPENRBUS_REGISTRY_GENERATED_H",
        "",
        "#include <stddef.h>",
        "#include <stdint.h>",
        "",
        "#ifndef PROGMEM",
        "#define PROGMEM",
        "#endif",
        "",
        "typedef enum OpenRBusWireType {",
    ]
    for wire_type, value in wire_ids.items():
        lines.append(f"  {symbol_prefix}_WIRE_{wire_type.value} = {value},")
    lines.extend(
        [
            "} OpenRBusWireType;",
            "",
            "enum {",
            f"  {symbol_prefix}_FLAG_READ = 1u << 0,",
            f"  {symbol_prefix}_FLAG_WRITE = 1u << 1,",
            f"  {symbol_prefix}_FLAG_UNSAFE = 1u << 2,",
            f"  {symbol_prefix}_FLAG_RANGE = 1u << 3,",
            f"  {symbol_prefix}_FLAG_ARRAY = 1u << 4,",
            f"  {symbol_prefix}_FLAG_TYPE_CONFLICT = 1u << 5,",
            "};",
            "",
            "typedef struct OpenRBusRegister {",
            "  uint16_t index;",
            "  uint8_t subindex;",
            "  uint8_t wire_type;",
            "  uint8_t storage_type;",
            "  uint16_t storage_length;",
            "  double gain;",
            "  double minimum;",
            "  double maximum;",
            "  int8_t precision;",
            "  uint8_t flags;",
            "  uint16_t max_items;",
            "  const char *code;",
            "  const char *name;",
            "  const char *unit;",
            "} OpenRBusRegister;",
            "",
            f"static const OpenRBusRegister {symbol_prefix}_REGISTERS[] PROGMEM = {{",
        ]
    )
    for item in registry.registers:
        constraint = item.constraint
        flags: list[str] = []
        if item.access.readable_declared:
            flags.append(f"{symbol_prefix}_FLAG_READ")
        if item.access.writable_declared:
            flags.append(f"{symbol_prefix}_FLAG_WRITE")
        if item.safety.requires_unsafe_opt_in:
            flags.append(f"{symbol_prefix}_FLAG_UNSAFE")
        if constraint is not None:
            flags.append(f"{symbol_prefix}_FLAG_RANGE")
        if item.wire.is_array:
            flags.append(f"{symbol_prefix}_FLAG_ARRAY")
        if item.evidence.type_conflict:
            flags.append(f"{symbol_prefix}_FLAG_TYPE_CONFLICT")
        flag_expr = " | ".join(flags) if flags else "0"
        lines.append(
            "  {"
            f"0x{item.address.index:04X}, 0x{item.address.subindex:02X}, "
            f"{wire_ids[item.wire.type]}, {wire_ids[item.wire.storage]}, "
            f"{item.wire.length}, {_c_float(item.wire.gain)}, "
            f"{_c_float(constraint.minimum if constraint else None)}, "
            f"{_c_float(constraint.maximum if constraint else None)}, "
            f"{constraint.precision if constraint and constraint.precision is not None else -1}, "
            f"{flag_expr}, {item.wire.max_items or 0}, "
            f"{_c_string(item.code)}, {_c_string(item.name(locale))}, {_c_string(item.wire.unit)}"
            "},"
        )
    lines.extend(
        [
            "};",
            f"static const size_t {symbol_prefix}_REGISTER_COUNT =",
            f"    sizeof({symbol_prefix}_REGISTERS) / sizeof({symbol_prefix}_REGISTERS[0]);",
            "",
            "#endif /* OPENRBUS_REGISTRY_GENERATED_H */",
            "",
        ]
    )
    return "\n".join(lines)


def _parse_register(raw_value: Any, offset: int) -> RegisterDefinition:
    path = f"registers[{offset}]"
    raw = _expect_mapping(raw_value, path)
    names = _expect_mapping(raw.get("names"), f"{path}.names")
    wire = _expect_mapping(raw.get("wire"), f"{path}.wire")
    access = _expect_mapping(raw.get("access"), f"{path}.access")
    safety = _expect_mapping(raw.get("safety"), f"{path}.safety")
    evidence = _expect_mapping(raw.get("evidence"), f"{path}.evidence")
    constraint_raw = raw.get("constraint")
    variants_raw = _expect_list(raw.get("constraint_variants", []), f"{path}.constraint_variants")
    device_rows = _expect_list(evidence.get("devices", []), f"{path}.evidence.devices")
    type_rows = _expect_list(raw.get("type_variants", []), f"{path}.type_variants")
    code = raw.get("code")
    return RegisterDefinition(
        address=_parse_address(_expect_str(raw.get("address"), f"{path}.address")),
        code=None if code is None else _expect_str(code, f"{path}.code"),
        names=LocalizedNames(
            de=_expect_str(names.get("de"), f"{path}.names.de"),
            en=_expect_str(names.get("en"), f"{path}.names.en"),
        ),
        wire=WireDefinition(
            type=_wire_type(wire.get("type"), f"{path}.wire.type"),
            storage=_wire_type(wire.get("storage"), f"{path}.wire.storage"),
            length=_expect_int(wire.get("length"), f"{path}.wire.length"),
            gain=_decimal(wire.get("gain"), f"{path}.wire.gain"),
            unit=_optional_str(wire.get("unit"), f"{path}.wire.unit"),
            is_array=_expect_bool(wire.get("array"), f"{path}.wire.array"),
            max_items=_optional_int(wire.get("max_items"), f"{path}.wire.max_items"),
            enum_name=_optional_str(wire.get("enum"), f"{path}.wire.enum"),
            struct_name=_optional_str(wire.get("struct"), f"{path}.wire.struct"),
        ),
        access=AccessDefinition(
            readable_declared=_expect_bool(access.get("read"), f"{path}.access.read"),
            writable_declared=_expect_bool(access.get("write"), f"{path}.access.write"),
        ),
        constraint=(
            None
            if constraint_raw is None
            else _parse_constraint(constraint_raw, f"{path}.constraint")
        ),
        constraint_variants=tuple(
            _parse_constraint(value, f"{path}.constraint_variants[{index}]")
            for index, value in enumerate(variants_raw)
        ),
        safety=SafetyDefinition(
            write=_write_safety(safety.get("write"), f"{path}.safety.write"),
            requires_unsafe_opt_in=_expect_bool(
                safety.get("requires_unsafe"), f"{path}.safety.requires_unsafe"
            ),
        ),
        evidence=EvidenceDefinition(
            categories=_string_tuple(evidence.get("categories"), f"{path}.evidence.categories"),
            config_occurrences=_expect_int(
                evidence.get("config_occurrences"), f"{path}.evidence.config_occurrences"
            ),
            config_profiles=_string_tuple(
                evidence.get("config_profiles"), f"{path}.evidence.config_profiles"
            ),
            definition_occurrences=_expect_int(
                evidence.get("definition_occurrences"),
                f"{path}.evidence.definition_occurrences",
            ),
            device_families=_string_tuple(
                evidence.get("device_families"), f"{path}.evidence.device_families"
            ),
            devices=tuple(
                _parse_device_evidence(value, f"{path}.evidence.devices[{index}]")
                for index, value in enumerate(device_rows)
            ),
            type_conflict=_expect_bool(
                evidence.get("type_conflict"), f"{path}.evidence.type_conflict"
            ),
        ),
        type_variants=tuple(
            _parse_type_variant(value, f"{path}.type_variants[{index}]")
            for index, value in enumerate(type_rows)
        ),
    )


def _parse_constraint(raw_value: Any, path: str) -> ValueConstraint:
    raw = _expect_mapping(raw_value, path)
    return ValueConstraint(
        minimum=_decimal(raw.get("minimum"), f"{path}.minimum"),
        maximum=_decimal(raw.get("maximum"), f"{path}.maximum"),
        precision=_optional_int(raw.get("precision"), f"{path}.precision"),
        device_families=_string_tuple(raw.get("device_families", []), f"{path}.device_families"),
    )


def _parse_device_evidence(raw_value: Any, path: str) -> DeviceEvidence:
    raw = _expect_mapping(raw_value, path)
    return DeviceEvidence(
        family=_expect_str(raw.get("family"), f"{path}.family"),
        address=_parse_address(_expect_str(raw.get("address"), f"{path}.address")),
        loaded=_optional_bool(raw.get("loaded"), f"{path}.loaded"),
        writable_any=_optional_bool(raw.get("writable_any"), f"{path}.writable_any"),
        writable_all=_optional_bool(raw.get("writable_all"), f"{path}.writable_all"),
        read_level_min=_optional_access_level(raw.get("read_level_min"), f"{path}.read_level_min"),
        read_level_max=_optional_access_level(raw.get("read_level_max"), f"{path}.read_level_max"),
        write_level_min=_optional_access_level(
            raw.get("write_level_min"), f"{path}.write_level_min"
        ),
        write_level_max=_optional_access_level(
            raw.get("write_level_max"), f"{path}.write_level_max"
        ),
    )


def _parse_type_variant(raw_value: Any, path: str) -> TypeVariant:
    raw = _expect_mapping(raw_value, path)
    return TypeVariant(
        address=_parse_address(_expect_str(raw.get("address"), f"{path}.address")),
        wire_type=_wire_type(raw.get("type"), f"{path}.type"),
        length=_optional_int(raw.get("length"), f"{path}.length"),
        profiles=_string_tuple(raw.get("profiles"), f"{path}.profiles"),
        evidence_count=_expect_int(raw.get("evidence_count"), f"{path}.evidence_count"),
    )


def _parse_candidate(raw_value: Any, offset: int) -> RegisterCandidate:
    path = f"candidates[{offset}]"
    raw = _expect_mapping(raw_value, path)
    variants = _expect_list(raw.get("wire_variants"), f"{path}.wire_variants")
    return RegisterCandidate(
        address=_parse_address(_expect_str(raw.get("address"), f"{path}.address")),
        wire_variants=tuple(
            CandidateWireVariant(
                wire_type=_wire_type(
                    _expect_mapping(item, f"{path}.wire_variants[{index}]").get("type"),
                    f"{path}.wire_variants[{index}].type",
                ),
                length=_optional_int(
                    _expect_mapping(item, f"{path}.wire_variants[{index}]").get("length"),
                    f"{path}.wire_variants[{index}].length",
                ),
            )
            for index, item in enumerate(variants)
        ),
        profiles=_string_tuple(raw.get("profiles"), f"{path}.profiles"),
        evidence_count=_expect_int(raw.get("evidence_count"), f"{path}.evidence_count"),
    )


def _parse_enum(raw_value: Any, offset: int) -> EnumDefinition:
    path = f"enums[{offset}]"
    raw = _expect_mapping(raw_value, path)
    values = _expect_list(raw.get("values"), f"{path}.values")
    return EnumDefinition(
        name=_expect_str(raw.get("name"), f"{path}.name"),
        values=tuple(
            _expect_int(value, f"{path}.values[{index}]") for index, value in enumerate(values)
        ),
    )


def _parse_structure(raw_value: Any, offset: int) -> StructureDefinition:
    path = f"structures[{offset}]"
    raw = _expect_mapping(raw_value, path)
    fields = _expect_list(raw.get("fields"), f"{path}.fields")
    result_fields: list[StructureField] = []
    for index, value in enumerate(fields):
        field_path = f"{path}.fields[{index}]"
        field = _expect_mapping(value, field_path)
        result_fields.append(
            StructureField(
                name=_expect_str(field.get("name"), f"{field_path}.name"),
                wire_type=_wire_type(field.get("type"), f"{field_path}.type"),
                bit_offset=_expect_int(field.get("bit_offset"), f"{field_path}.bit_offset"),
                bit_length=_expect_int(field.get("bit_length"), f"{field_path}.bit_length"),
                gain=_decimal(field.get("gain"), f"{field_path}.gain"),
                unit=_optional_str(field.get("unit"), f"{field_path}.unit"),
                enum_name=_optional_str(field.get("enum"), f"{field_path}.enum"),
            )
        )
    return StructureDefinition(
        name=_expect_str(raw.get("name"), f"{path}.name"),
        length=_expect_int(raw.get("length"), f"{path}.length"),
        fields=tuple(result_fields),
    )


def _expect_mapping(value: Any, path: str) -> Mapping[str, Any]:
    if not isinstance(value, dict):
        raise RegistryError(f"{path} must be an object")
    return value


def _parse_address(
    value: str | RegisterAddress | tuple[int, int],
) -> RegisterAddress:
    if isinstance(value, ObjectAddress):
        return value
    if isinstance(value, tuple):
        if len(value) != 2:
            raise ValueError("address tuple must contain index and subindex")
        return ObjectAddress(*value)
    match = _ADDRESS_RE.fullmatch(value.strip())
    if match is None:
        raise ValueError(f"invalid register address: {value!r}")
    return ObjectAddress(int(match.group(1), 16), int(match.group(2), 16))


def _expect_list(value: Any, path: str) -> list[Any]:
    if not isinstance(value, list):
        raise RegistryError(f"{path} must be an array")
    return value


def _expect_str(value: Any, path: str) -> str:
    if not isinstance(value, str) or not value:
        raise RegistryError(f"{path} must be a non-empty string")
    return value


def _optional_str(value: Any, path: str) -> str | None:
    if value is None:
        return None
    return _expect_str(value, path)


def _expect_int(value: Any, path: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise RegistryError(f"{path} must be an integer")
    return value


def _optional_int(value: Any, path: str) -> int | None:
    if value is None:
        return None
    return _expect_int(value, path)


def _optional_access_level(value: Any, path: str) -> AccessLevel | None:
    if value is None:
        return None
    raw = _expect_int(value, path)
    try:
        return AccessLevel(raw)
    except ValueError as error:
        raise RegistryError(f"{path} contains unsupported access level {raw}") from error


def _expect_bool(value: Any, path: str) -> bool:
    if not isinstance(value, bool):
        raise RegistryError(f"{path} must be a boolean")
    return value


def _optional_bool(value: Any, path: str) -> bool | None:
    if value is None:
        return None
    return _expect_bool(value, path)


def _decimal(value: Any, path: str) -> Decimal | None:
    if value is None:
        return None
    if not isinstance(value, str):
        raise RegistryError(f"{path} must be a decimal string or null")
    try:
        result = Decimal(value)
    except InvalidOperation as error:
        raise RegistryError(f"{path} contains an invalid decimal") from error
    if not result.is_finite():
        raise RegistryError(f"{path} must be finite")
    return result


def _wire_type(value: Any, path: str) -> WireType:
    raw = _expect_str(value, path)
    try:
        return WireType(raw)
    except ValueError as error:
        raise RegistryError(f"{path} contains unsupported wire type {raw!r}") from error


def _write_safety(value: Any, path: str) -> WriteSafety:
    raw = _expect_str(value, path)
    try:
        return WriteSafety(raw)
    except ValueError as error:
        raise RegistryError(f"{path} contains unsupported write safety {raw!r}") from error


def _string_tuple(value: Any, path: str) -> tuple[str, ...]:
    raw = _expect_list(value, path)
    result = tuple(_expect_str(item, f"{path}[{index}]") for index, item in enumerate(raw))
    if result != tuple(sorted(set(result))):
        raise RegistryError(f"{path} must be sorted and unique")
    return result


def _decimal_text(value: Decimal | None) -> str | None:
    return None if value is None else format(value, "f")


def _c_float(value: Decimal | None) -> str:
    if value is None:
        return "0.0"
    text = format(value, "f")
    if "." not in text:
        text += ".0"
    return text


def _c_string(value: str | None) -> str:
    return "NULL" if value is None else json.dumps(value, ensure_ascii=True)
