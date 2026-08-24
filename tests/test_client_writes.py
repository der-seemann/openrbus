from __future__ import annotations

from decimal import Decimal

import pytest

from openrbus.access import ObjectRead, RawReadResult
from openrbus.client import OpenRBusClient, ReadFailure, ReadResult
from openrbus.errors import (
    UnsafeWriteError,
    ValidationError,
    WritesDisabledError,
    WriteVerificationError,
)
from openrbus.protocol.canip import ObjectAddress
from openrbus.registry import Registry


class FakeAccess:
    def __init__(self, values: dict[tuple[int, ObjectAddress], bytes] | None = None) -> None:
        self.values = values or {}
        self.writes: list[tuple[int, ObjectAddress, bytes]] = []
        self.mismatch = False

    async def read_raw(
        self, node: int, address: ObjectAddress, *, timeout: float | None = None
    ) -> bytes:
        value = self.values[(node, address)]
        return b"\x00" * len(value) if self.mismatch else value

    async def _write_raw(
        self,
        node: int,
        address: ObjectAddress,
        raw_value: bytes,
        *,
        timeout: float | None = None,
    ) -> None:
        self.writes.append((node, address, raw_value))
        self.values[(node, address)] = raw_value


class FakeBulkAccess(FakeAccess):
    def __init__(self, values: dict[tuple[int, ObjectAddress], bytes]) -> None:
        super().__init__(values)
        self.bulk_calls: list[tuple[ObjectRead, ...]] = []

    async def read_many_raw(
        self,
        items: tuple[ObjectRead, ...],
        *,
        timeout: float | None = None,
    ) -> tuple[RawReadResult, ...]:
        self.bulk_calls.append(items)
        return tuple(
            RawReadResult(item.node, item.address, raw=self.values[(item.node, item.address)])
            for item in items
        )


@pytest.fixture(scope="module")
def registry() -> Registry:
    return Registry.load_default()


@pytest.mark.asyncio
async def test_read_is_default_and_write_requires_both_opt_ins(registry: Registry) -> None:
    address = ObjectAddress(0x2300, 0)
    access = FakeAccess({(1, address): bytes.fromhex("d204")})
    client = OpenRBusClient(access, registry=registry)
    assert (await client.read(1, address)).value == Decimal("12.34")

    with pytest.raises(WritesDisabledError):
        await client.write(1, address, Decimal("10"), allow_unsafe=True, dry_run=True)

    client = OpenRBusClient(access, registry=registry, enable_writes=True)
    with pytest.raises(UnsafeWriteError):
        await client.write(1, address, Decimal("10"), dry_run=True)


@pytest.mark.asyncio
async def test_read_many_uses_bulk_capability_and_decodes_in_order(registry: Registry) -> None:
    first = ObjectAddress(0x2300, 0)
    second = ObjectAddress(0x200D, 0)
    access = FakeBulkAccess({(1, first): bytes.fromhex("d204"), (1, second): b"\x01"})
    client = OpenRBusClient(access, registry=registry)
    outcomes = await client.read_many(1, (first, second))
    assert all(isinstance(outcome, ReadResult) for outcome in outcomes)
    assert outcomes[0].value == Decimal("12.34")
    assert outcomes[1].address == second
    assert len(access.bulk_calls) == 1
    assert [item.max_value_length for item in access.bulk_calls[0]] == [2, 1]
    assert not any(isinstance(outcome, ReadFailure) for outcome in outcomes)


@pytest.mark.asyncio
async def test_dry_run_validates_without_io_and_active_write_reads_back(registry: Registry) -> None:
    address = ObjectAddress(0x2300, 0)
    access = FakeAccess({(1, address): bytes.fromhex("0000")})
    client = OpenRBusClient(access, registry=registry, enable_writes=True, minimum_write_interval=0)
    plan = await client.write(1, address, Decimal("12.34"), allow_unsafe=True, dry_run=True)
    assert plan.dry_run
    assert plan.raw_value == bytes.fromhex("d204")
    assert access.writes == []

    result = await client.write(1, address, Decimal("12.34"), allow_unsafe=True)
    assert result.verified
    assert access.writes == [(1, address, bytes.fromhex("d204"))]


@pytest.mark.asyncio
async def test_read_only_ranges_and_type_conflicts_are_blocked(registry: Registry) -> None:
    client = OpenRBusClient(FakeAccess(), registry=registry, enable_writes=True)
    with pytest.raises(ValidationError, match="not declared writable"):
        await client.write(1, "200d:00", 1, allow_unsafe=True, dry_run=True)
    with pytest.raises(ValidationError, match="maximum"):
        await client.write(1, "2300:00", Decimal("20.01"), allow_unsafe=True, dry_run=True)
    with pytest.raises(ValidationError, match="wire-type conflicts"):
        await client.write(1, "1003:01", b"\x00\x00", allow_unsafe=True, dry_run=True)


@pytest.mark.asyncio
async def test_range_conflict_requires_matching_device_family(registry: Registry) -> None:
    client = OpenRBusClient(FakeAccess(), registry=registry, enable_writes=True)
    with pytest.raises(ValidationError, match="device_family"):
        await client.write(1, "2009:00", 1, allow_unsafe=True, dry_run=True)
    with pytest.raises(ValidationError, match="not writable in device family Scb-10"):
        await client.write(
            1,
            "2009:00",
            254,
            allow_unsafe=True,
            dry_run=True,
            device_family="Scb-10",
        )


@pytest.mark.asyncio
async def test_readback_mismatch_is_an_error(registry: Registry) -> None:
    address = ObjectAddress(0x2300, 0)
    access = FakeAccess({(1, address): b"\x00\x00"})
    access.mismatch = True
    client = OpenRBusClient(access, registry=registry, enable_writes=True, minimum_write_interval=0)
    with pytest.raises(WriteVerificationError):
        await client.write(1, address, Decimal("12.34"), allow_unsafe=True)
