"""Registry-driven CANopen value encoding and decoding.

Scalar byte order is little-endian.  Register indexes, transport lengths, and
checksums have their own protocol-specific byte orders and are handled by the
corresponding framing codecs.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from typing import TypeAlias

from openrbus.errors import ValidationError
from openrbus.protocol.canip import ObjectAddress
from openrbus.registry import (
    RegisterDefinition,
    Registry,
    StructureDefinition,
    ValueConstraint,
    WireType,
)


@dataclass(frozen=True, slots=True)
class CanOpenTimeOfDay:
    """The six-byte CiA 301 time value without timezone interpretation.

    ``milliseconds`` is the elapsed time since midnight.  ``days`` is the
    protocol day counter.  Keeping both fields explicit avoids inventing a
    timezone or calendar interpretation.
    """

    milliseconds: int
    days: int = 0

    def __post_init__(self) -> None:
        if not 0 <= self.milliseconds < 86_400_000:
            raise ValueError("milliseconds must be within one day")
        if not 0 <= self.days <= 0xFFFF:
            raise ValueError("days must fit in an unsigned 16-bit value")


@dataclass(frozen=True, slots=True)
class StructureFieldValue:
    """One decoded field of a packed registry structure."""

    name: str
    value: int | Decimal
    unit: str | None
    enum_name: str | None


@dataclass(frozen=True, slots=True)
class StructuredValue:
    """Decoded structure fields plus the lossless original bytes."""

    structure: str
    fields: tuple[StructureFieldValue, ...]
    raw: bytes

    def as_dict(self) -> dict[str, int | Decimal]:
        """Return a convenient field-name mapping."""

        return {field.name: field.value for field in self.fields}


DecodedValue: TypeAlias = int | Decimal | str | bytes | CanOpenTimeOfDay | StructuredValue
EncodableValue: TypeAlias = int | Decimal | str | bytes | CanOpenTimeOfDay


def decode_value(
    definition: RegisterDefinition,
    address: ObjectAddress,
    raw: bytes,
    *,
    registry: Registry,
) -> DecodedValue:
    """Decode bytes using one registry definition.

    Array object ``:00`` is the CANopen subindex count and is decoded as an
    unsigned integer.  Element subindexes use the array's declared item type.
    """

    if definition.wire.is_array and address.subindex == 0:
        if not 1 <= len(raw) <= 4:
            raise ValidationError("array subindex count must be a 1..4 byte integer")
        return int.from_bytes(raw, "little")

    semantic = definition.wire.type
    storage = definition.wire.storage
    if semantic is WireType.TIME_OF_DAY:
        _require_length(raw, 6, definition)
        return CanOpenTimeOfDay(
            milliseconds=int.from_bytes(raw[:4], "little"),
            days=int.from_bytes(raw[4:], "little"),
        )
    if semantic is WireType.STRUCT:
        _require_length(raw, definition.wire.length, definition)
        structure = _required_structure(definition, registry)
        return _decode_structure(structure, raw)
    if storage is WireType.VISIBLESTRING:
        if len(raw) > definition.wire.length:
            raise ValidationError(f"value for {definition.address} exceeds declared wire length")
        try:
            return raw.split(b"\0", 1)[0].decode("ascii")
        except UnicodeDecodeError as exc:
            raise ValidationError(f"value for {definition.address} is not an ASCII string") from exc
    if storage is WireType.OCTETSTRING:
        if len(raw) > definition.wire.length:
            raise ValidationError(f"value for {definition.address} exceeds declared wire length")
        return bytes(raw)

    _require_length(raw, definition.wire.length, definition)
    integer = _decode_integer(storage, raw)
    gain = definition.wire.gain or Decimal(1)
    return integer if gain == 1 else Decimal(integer) * gain


def encode_value(
    definition: RegisterDefinition,
    address: ObjectAddress,
    value: EncodableValue,
    *,
    registry: Registry,
    constraint: ValueConstraint | None = None,
) -> bytes:
    """Validate and encode an engineering value for a confirmed write."""

    if definition.wire.is_array and address.subindex == 0:
        raise ValidationError("writing an array subindex count is not supported")

    semantic = definition.wire.type
    storage = definition.wire.storage
    if semantic is WireType.TIME_OF_DAY:
        if not isinstance(value, CanOpenTimeOfDay):
            raise ValidationError("TIME_OF_DAY values require CanOpenTimeOfDay")
        return value.milliseconds.to_bytes(4, "little") + value.days.to_bytes(2, "little")
    if semantic is WireType.STRUCT:
        if not isinstance(value, bytes):
            raise ValidationError(
                "STRUCT writes require lossless raw bytes; "
                "mapping writes are intentionally unsupported"
            )
        _require_length(value, definition.wire.length, definition)
        _required_structure(definition, registry)
        return value
    if storage is WireType.VISIBLESTRING:
        if not isinstance(value, str):
            raise ValidationError("VISIBLESTRING values must be str")
        try:
            encoded = value.encode("ascii")
        except UnicodeEncodeError as exc:
            raise ValidationError("VISIBLESTRING only supports ASCII") from exc
        if len(encoded) > definition.wire.length:
            raise ValidationError(f"string exceeds declared length {definition.wire.length}")
        return encoded.ljust(definition.wire.length, b"\0")
    if storage is WireType.OCTETSTRING:
        if not isinstance(value, bytes):
            raise ValidationError("OCTETSTRING values must be bytes")
        _require_length(value, definition.wire.length, definition)
        return value

    engineering = _decimal_value(value)
    _validate_constraint(engineering, constraint)
    gain = definition.wire.gain or Decimal(1)
    raw_decimal = engineering / gain
    integral = raw_decimal.to_integral_value()
    if raw_decimal != integral:
        raise ValidationError(f"value {engineering} is not representable with gain {gain}")
    integer = int(integral)
    if semantic is WireType.ENUMERATION:
        enumeration = registry.enum(definition.wire.enum_name or "")
        if enumeration is not None and integer not in enumeration.values:
            raise ValidationError(
                f"enumeration value {integer} is not declared for {enumeration.name}"
            )
    return _encode_integer(storage, integer, definition.wire.length)


def _decode_integer(storage: WireType, raw: bytes) -> int:
    if storage not in _INTEGER_TYPES:
        raise ValidationError(f"unsupported scalar storage type {storage.value}")
    return int.from_bytes(raw, "little", signed=storage in _SIGNED_TYPES)


def _encode_integer(storage: WireType, value: int, length: int) -> bytes:
    if storage not in _INTEGER_TYPES:
        raise ValidationError(f"unsupported scalar storage type {storage.value}")
    signed = storage in _SIGNED_TYPES
    bits = length * 8
    minimum = -(1 << (bits - 1)) if signed else 0
    maximum = (1 << (bits - 1)) - 1 if signed else (1 << bits) - 1
    if not minimum <= value <= maximum:
        raise ValidationError(f"raw integer {value} is outside {storage.value}")
    return value.to_bytes(length, "little", signed=signed)


def _decode_structure(structure: StructureDefinition, raw: bytes) -> StructuredValue:
    packed = int.from_bytes(raw, "little")
    fields: list[StructureFieldValue] = []
    for field in structure.fields:
        if field.bit_length <= 0 or field.bit_offset < 0:
            raise ValidationError(f"invalid field layout in structure {structure.name}")
        mask = (1 << field.bit_length) - 1
        integer = (packed >> field.bit_offset) & mask
        if field.wire_type in _SIGNED_TYPES and integer & (1 << (field.bit_length - 1)):
            integer -= 1 << field.bit_length
        gain = field.gain or Decimal(1)
        decoded: int | Decimal = integer if gain == 1 else Decimal(integer) * gain
        fields.append(StructureFieldValue(field.name, decoded, field.unit, field.enum_name))
    return StructuredValue(structure.name, tuple(fields), bytes(raw))


def _required_structure(definition: RegisterDefinition, registry: Registry) -> StructureDefinition:
    name = definition.wire.struct_name
    structure = registry.structure(name or "")
    if structure is None:
        raise ValidationError(f"register {definition.address} has no public structure layout")
    if structure.length != definition.wire.length:
        raise ValidationError(f"structure {structure.name} length conflicts with its register")
    return structure


def _require_length(raw: bytes, expected: int, definition: RegisterDefinition) -> None:
    if len(raw) != expected:
        raise ValidationError(
            f"value for {definition.address} has {len(raw)} bytes; expected {expected}"
        )


def _decimal_value(value: object) -> Decimal:
    if isinstance(value, bool) or not isinstance(value, (int, Decimal)):
        raise ValidationError(
            "numeric values must be int or Decimal; float is intentionally rejected"
        )
    try:
        result = Decimal(value)
    except (InvalidOperation, ValueError) as exc:
        raise ValidationError("invalid numeric value") from exc
    if not result.is_finite():
        raise ValidationError("numeric value must be finite")
    return result


def _validate_constraint(value: Decimal, constraint: ValueConstraint | None) -> None:
    if constraint is None:
        return
    if constraint.minimum is not None and value < constraint.minimum:
        raise ValidationError(f"value {value} is below minimum {constraint.minimum}")
    if constraint.maximum is not None and value > constraint.maximum:
        raise ValidationError(f"value {value} is above maximum {constraint.maximum}")
    if constraint.precision is not None:
        quantum = Decimal(1).scaleb(-constraint.precision)
        if value.quantize(quantum) != value:
            raise ValidationError(
                f"value {value} exceeds declared precision {constraint.precision}"
            )


_SIGNED_TYPES = frozenset({WireType.INT8, WireType.INT16, WireType.INT32})
_INTEGER_TYPES = frozenset(
    {
        WireType.UINT8,
        WireType.UINT16,
        WireType.UINT32,
        WireType.INT8,
        WireType.INT16,
        WireType.INT32,
    }
)
