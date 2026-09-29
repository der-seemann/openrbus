from __future__ import annotations

import pytest

from openrbus.bitfields import decode_bitfields
from openrbus.registry import Registry


def test_registry_bitfield_decoding_and_localized_names() -> None:
    registry = Registry.load_default()
    definition = registry.structure("BufferConfiguration")
    assert definition is not None
    field = next(item for item in definition.fields if item.bit_length == 1)
    raw = bytearray(definition.length)
    raw[field.bit_offset // 8] |= 1 << (field.bit_offset % 8)

    assert decode_bitfields(definition, raw) == {field.name: True}
    assert field.label("de")
    assert field.label("en")
    assert field.label("fr-FR")


def test_bitfield_decode_rejects_incomplete_or_oversized_data() -> None:
    registry = Registry.load_default()
    definition = registry.structure("BufferConfiguration")
    assert definition is not None
    with pytest.raises(ValueError):
        decode_bitfields(definition, b"")
    with pytest.raises(ValueError):
        decode_bitfields(definition, bytes(definition.length + 1))


def test_read_only_structure_decodes_and_unknown_register_stays_unknown() -> None:
    registry = Registry.load_default()
    read_only = registry.get("5619:00")
    unknown = registry.find("ffff:ff")

    assert read_only.wire.struct_name
    assert unknown is None
    structure = registry.structure(read_only.wire.struct_name)
    assert structure is not None
    assert not any(decode_bitfields(structure, bytes(read_only.wire.length)).values())
