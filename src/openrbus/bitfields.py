"""Utilities for projecting registry-defined one-bit structure fields."""

from __future__ import annotations

from collections.abc import Mapping

from .registry import StructureDefinition


def decode_bitfields(
    structure: StructureDefinition, raw_value: bytes | bytearray | memoryview
) -> Mapping[str, bool]:
    """Decode the explicitly one-bit fields in a packed structure value.

    Offsets use the registry's least-significant-bit numbering. The complete
    structure is required so callers never publish guessed states from a
    truncated or oversized value.
    """

    raw = bytes(raw_value)
    if len(raw) != structure.length:
        raise ValueError(f"{structure.name} requires {structure.length} bytes, received {len(raw)}")
    packed = int.from_bytes(raw, "little", signed=False)
    result: dict[str, bool] = {}
    for field in structure.fields:
        if field.bit_length != 1:
            continue
        if field.bit_offset < 0 or field.bit_offset >= structure.length * 8:
            raise ValueError(f"{structure.name}.{field.name} has an invalid bit offset")
        result[field.name] = bool((packed >> field.bit_offset) & 1)
    return result
