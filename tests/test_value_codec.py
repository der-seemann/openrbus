from __future__ import annotations

from decimal import Decimal

import pytest

from openrbus.errors import ValidationError
from openrbus.protocol.canip import ObjectAddress
from openrbus.registry import Registry
from openrbus.value_codec import (
    CanOpenTimeOfDay,
    StructuredValue,
    decode_value,
    encode_value,
)


@pytest.fixture(scope="module")
def registry() -> Registry:
    return Registry.load_default()


def test_little_endian_scaled_signed_round_trip(registry: Registry) -> None:
    definition = registry.get("2300:00")
    address = ObjectAddress(0x2300, 0)
    assert decode_value(definition, address, bytes.fromhex("18fc"), registry=registry) == Decimal(
        "-10"
    )
    assert (
        encode_value(
            definition,
            address,
            Decimal("12.34"),
            registry=registry,
            constraint=definition.constraint,
        ).hex()
        == "d204"
    )
    with pytest.raises(ValidationError, match="maximum"):
        encode_value(
            definition,
            address,
            Decimal("20.01"),
            registry=registry,
            constraint=definition.constraint,
        )
    with pytest.raises(ValidationError, match="representable"):
        encode_value(
            definition,
            address,
            Decimal("1.001"),
            registry=registry,
            constraint=None,
        )


def test_enum_membership_and_array_count(registry: Registry) -> None:
    definition = registry.get("3001:00")
    address = ObjectAddress(0x3001, 0)
    assert encode_value(definition, address, 11, registry=registry) == b"\x0b"
    with pytest.raises(ValidationError, match="not declared"):
        encode_value(definition, address, 14, registry=registry)

    array = registry.get("1018:00")
    assert decode_value(array, ObjectAddress(0x1018, 0), b"\x04", registry=registry) == 4
    assert (
        decode_value(array, ObjectAddress(0x1018, 1), bytes.fromhex("78563412"), registry=registry)
        == 0x12345678
    )
    with pytest.raises(ValidationError, match="subindex count"):
        encode_value(array, ObjectAddress(0x1018, 0), 4, registry=registry)


def test_structure_is_decoded_losslessly_and_raw_only_for_writes(registry: Registry) -> None:
    definition = registry.get("3506:00")
    raw = bytes(range(19))
    decoded = decode_value(definition, ObjectAddress(0x3506, 0), raw, registry=registry)
    assert isinstance(decoded, StructuredValue)
    assert decoded.raw == raw
    assert decoded.structure == "TimeProgram"
    assert decoded.fields
    assert encode_value(definition, ObjectAddress(0x3506, 0), raw, registry=registry) == raw
    with pytest.raises(ValidationError, match="lossless raw bytes"):
        encode_value(definition, ObjectAddress(0x3506, 0), 1, registry=registry)


def test_canopen_time_of_day_keeps_calendar_counter_explicit(registry: Registry) -> None:
    definition = registry.get("504b:00")
    value = CanOpenTimeOfDay(milliseconds=45_296_789, days=123)
    encoded = encode_value(definition, ObjectAddress(0x504B, 0), value, registry=registry)
    assert encoded == value.milliseconds.to_bytes(4, "little") + b"{\x00"
    assert decode_value(definition, ObjectAddress(0x504B, 0), encoded, registry=registry) == value


def test_floats_are_rejected_to_avoid_rounding_ambiguity(registry: Registry) -> None:
    definition = registry.get("2300:00")
    with pytest.raises(ValidationError, match="float"):
        encode_value(definition, ObjectAddress(0x2300, 0), 1.1, registry=registry)  # type: ignore[arg-type]
