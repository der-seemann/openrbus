from __future__ import annotations

import pytest

from openrbus.errors import CanOpenAbortError
from openrbus.function_groups import (
    FunctionGroupDiscovered,
    FunctionGroupDiscovery,
    FunctionGroupKey,
    discover_function_group_records,
    filter_member_addresses,
)
from openrbus.protocol.canip import ObjectAddress


def _group_row(node: int, group_type: int, side: int = 1) -> bytes:
    return bytes((0, node, 0x01, 0x01, group_type, side))


def _zone_row(node: int, group_type: int, slot: int, side: int = 1) -> bytes:
    raw = bytearray(51)
    raw[15:17] = bytes((0x01, 0x01))
    raw[17] = group_type
    raw[19] = 0
    raw[20] = node
    raw[21] = slot
    raw[50] = side
    return bytes(raw)


class FunctionGroupReader:
    def __init__(self, *, zones: bool = True) -> None:
        self.values = {
            (1, ObjectAddress(0x3096, 0)): b"\x01",
            (1, ObjectAddress(0x3096, 1)): _group_row(5, 3),
            (1, ObjectAddress(0x3097, 0)): b"\x01" if zones else b"\x00",
        }
        if zones:
            self.values[(1, ObjectAddress(0x3097, 1))] = _zone_row(5, 3, 2)

    async def read_raw(
        self, node: int, address: ObjectAddress, *, timeout: float | None = None
    ) -> bytes:
        if (node, address) not in self.values:
            raise CanOpenAbortError(0x06020000, "object does not exist")
        return self.values[(node, address)]


@pytest.mark.asyncio
async def test_runtime_group_and_zone_arrays_decode_to_typed_records() -> None:
    discovery = await discover_function_group_records(FunctionGroupReader())

    assert discovery.function_groups == (
        FunctionGroupDiscovered(0, 5, FunctionGroupKey(0x0101, 3, 1)),
    )
    assert discovery.zones[0].node_id == 5
    assert discovery.zones[0].key == FunctionGroupKey(0x0101, 3, 1)
    assert discovery.zones[0].subindex == 2


@pytest.mark.asyncio
async def test_mixed_zone_group_maps_only_explicit_target_members_for_its_slot() -> None:
    discovery = await discover_function_group_records(FunctionGroupReader())

    members = filter_member_addresses(discovery)

    assert members[(5, "screed")] == frozenset(
        {
            *(
                ObjectAddress(index, 2)
                for index in (
                    0x344D,
                    0x3483,
                    0x3484,
                    0x3485,
                    0x3486,
                    0x3487,
                    0x3488,
                    0x3489,
                    0x348A,
                    0x348B,
                    0x348C,
                    0x5447,
                    0x5448,
                    0x5449,
                    0x544A,
                )
            ),
        }
    )
    assert members[(5, "cooling")] == frozenset(
        ObjectAddress(index, 2) for index in (0x3411, 0x3412, 0x341A, 0x3460, 0x3466)
    )
    assert ObjectAddress(0x3482, 2) not in members[(5, "screed")]


@pytest.mark.asyncio
async def test_zone_array_members_are_not_assigned_without_zone_metadata() -> None:
    discovery = await discover_function_group_records(FunctionGroupReader(zones=False))

    assert filter_member_addresses(discovery) == {}


def test_static_profile_key_must_match_all_runtime_identity_fields() -> None:
    discovery = FunctionGroupDiscovery(
        function_groups=(FunctionGroupDiscovered(0, 5, FunctionGroupKey(0x0101, 2, 1)),),
        zones=(),
    )

    assert filter_member_addresses(discovery) == {}


@pytest.mark.asyncio
async def test_unavailable_array_fails_as_one_incomplete_snapshot() -> None:
    reader = FunctionGroupReader()
    del reader.values[(1, ObjectAddress(0x3097, 1))]

    with pytest.raises(CanOpenAbortError):
        await discover_function_group_records(reader)
