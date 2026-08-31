from __future__ import annotations

import json
from decimal import Decimal
from pathlib import Path

import pytest

from openrbus.access import ObjectRead, RawReadResult
from openrbus.access_policy import AccessPolicy
from openrbus.client import OpenRBusClient, ReadFailure, ReadResult
from openrbus.errors import (
    AccessLevelAmbiguityError,
    AccessLevelUnavailableError,
    AccessPolicyError,
    CanOpenAbortError,
    InsufficientAccessLevelError,
    UnsafeWriteError,
    ValidationError,
    WritesDisabledError,
    WriteVerificationError,
)
from openrbus.protocol.canip import ObjectAddress
from openrbus.registry import AccessLevel, Registry

ROOT = Path(__file__).resolve().parents[1]
PACKAGED_REGISTRY = ROOT / "src/openrbus/data/registry-v1.json"
EFFECTIVE_LEVEL = ObjectAddress(0x4002, 0)


class FakeAccess:
    def __init__(self, values: dict[tuple[int, ObjectAddress], bytes] | None = None) -> None:
        self.values = values or {}
        self.writes: list[tuple[int, ObjectAddress, bytes]] = []
        self.mismatch = False

    async def read_raw(
        self, node: int, address: ObjectAddress, *, timeout: float | None = None
    ) -> bytes:
        value = self.values[(node, address)]
        if self.mismatch and address != EFFECTIVE_LEVEL:
            return b"\x00" * len(value)
        return value

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
    address = ObjectAddress(0x3654, 1)
    access = FakeAccess({(1, address): bytes.fromhex("8813")})
    client = OpenRBusClient(access, registry=registry)
    assert (await client.read(1, address, device_family="Ehc-16")).value == Decimal("50")

    with pytest.raises(WritesDisabledError):
        await client.write(
            1,
            address,
            Decimal("49.9"),
            allow_unsafe=True,
            dry_run=True,
            device_family="Ehc-16",
        )

    client = OpenRBusClient(access, registry=registry, enable_writes=True)
    write_address = ObjectAddress(0x3425, 1)
    with pytest.raises(UnsafeWriteError):
        await client.write(
            1,
            write_address,
            Decimal("40"),
            dry_run=True,
            device_family="Scb-10",
        )


@pytest.mark.asyncio
async def test_read_many_uses_bulk_capability_and_decodes_in_order(registry: Registry) -> None:
    first = ObjectAddress(0x3402, 1)
    second = ObjectAddress(0x340C, 1)
    access = FakeBulkAccess({(4, first): bytes.fromhex("d204"), (4, second): bytes.fromhex("c409")})
    client = OpenRBusClient(access, registry=registry)
    outcomes = await client.read_many(4, (first, second), device_family="Scb-10")
    assert all(isinstance(outcome, ReadResult) for outcome in outcomes)
    assert outcomes[0].value == Decimal("12.34")
    assert outcomes[1].address == second
    assert len(access.bulk_calls) == 1
    assert [item.max_value_length for item in access.bulk_calls[0]] == [2, 2]
    assert not any(isinstance(outcome, ReadFailure) for outcome in outcomes)


@pytest.mark.asyncio
async def test_dry_run_validates_without_io_and_active_write_reads_back(registry: Registry) -> None:
    address = ObjectAddress(0x2300, 0)
    access = FakeAccess(
        {
            (1, address): bytes.fromhex("0000"),
            (1, EFFECTIVE_LEVEL): b"\x03",
        }
    )
    client = OpenRBusClient(
        access,
        registry=registry,
        enable_writes=True,
        max_access_level=AccessLevel.INSTALLER,
        minimum_write_interval=0,
    )
    plan = await client.write(1, address, Decimal("12.34"), allow_unsafe=True, dry_run=True)
    assert plan.dry_run
    assert plan.raw_value == bytes.fromhex("d204")
    assert plan.required_access_level is AccessLevel.INSTALLER
    assert plan.higher_risk_access
    assert plan.session_access is None
    assert access.writes == []

    result = await client.write(1, address, Decimal("12.34"), allow_unsafe=True)
    assert result.verified
    assert result.session_access is not None
    assert result.session_access.effective_level is AccessLevel.PROFESSIONAL
    assert access.writes == [(1, address, bytes.fromhex("d204"))]


@pytest.mark.asyncio
async def test_session_access_is_read_only_cached_and_public(registry: Registry) -> None:
    access = FakeAccess({(4, EFFECTIVE_LEVEL): b"\x01"})
    client = OpenRBusClient(access, registry=registry)
    session = await client.refresh_session_access(4)
    assert session.effective_level is AccessLevel.USER
    assert session.satisfies(AccessLevel.USER)
    assert not session.satisfies(AccessLevel.INSTALLER)
    assert client.session_access(4) == session
    assert client.session_access_levels == {4: session}
    assert access.writes == []


@pytest.mark.asyncio
async def test_invalid_live_access_level_has_a_specific_error(registry: Registry) -> None:
    access = FakeAccess({(1, EFFECTIVE_LEVEL): b"\x10"})
    client = OpenRBusClient(access, registry=registry)
    with pytest.raises(AccessLevelUnavailableError, match="unsupported access level 16"):
        await client.refresh_session_access(1)


@pytest.mark.asyncio
async def test_access_object_abort_has_a_specific_error(registry: Registry) -> None:
    class AbortingAccess(FakeAccess):
        async def read_raw(
            self, node: int, address: ObjectAddress, *, timeout: float | None = None
        ) -> bytes:
            if address == EFFECTIVE_LEVEL:
                raise CanOpenAbortError(0x06010001)
            return await super().read_raw(node, address, timeout=timeout)

    access = AbortingAccess()
    client = OpenRBusClient(access, registry=registry)
    with pytest.raises(
        AccessLevelUnavailableError,
        match=r"could not read effective access level from 4002:00.*0x06010001",
    ):
        await client.refresh_session_access(1)
    assert access.writes == []


@pytest.mark.asyncio
async def test_cross_family_access_level_requires_device_family(registry: Registry) -> None:
    client = OpenRBusClient(
        FakeAccess(),
        registry=registry,
        enable_writes=True,
        max_access_level=AccessLevel.INSTALLER,
    )
    with pytest.raises(AccessLevelAmbiguityError, match=r"write levels \(1, 2\)"):
        await client.write(1, "200e:00", 0, allow_unsafe=True, dry_run=True)


@pytest.mark.asyncio
async def test_insufficient_session_level_blocks_before_raw_write(registry: Registry) -> None:
    address = ObjectAddress(0x340B, 3)
    access = FakeAccess(
        {
            (4, address): bytes.fromhex("bf00"),
            (4, EFFECTIVE_LEVEL): b"\x01",
        }
    )
    client = OpenRBusClient(
        access,
        registry=registry,
        enable_writes=True,
        max_access_level=AccessLevel.INSTALLER,
    )
    with pytest.raises(
        InsufficientAccessLevelError,
        match=(
            r"340b:03 requires access level 2, but node authorization is verified only "
            r"at effective level 1"
        ),
    ):
        await client.write(
            4,
            address,
            Decimal("19.1"),
            allow_unsafe=True,
            device_family="Scb-10",
        )
    assert access.writes == []


@pytest.mark.asyncio
async def test_validated_user_write_needs_no_unsafe_opt_in() -> None:
    raw_registry = json.loads(PACKAGED_REGISTRY.read_text(encoding="utf-8"))
    register = next(item for item in raw_registry["registers"] if item["address"] == "3425:00")
    register["safety"] = {"write": "validated", "requires_unsafe": False}
    registry = Registry.from_mapping(raw_registry)
    client = OpenRBusClient(FakeAccess(), registry=registry, enable_writes=True)
    plan = await client.write(
        4,
        "3425:01",
        Decimal("40"),
        dry_run=True,
        device_family="Scb-10",
    )
    assert plan.required_access_level is AccessLevel.USER
    assert not plan.higher_risk_access


@pytest.mark.asyncio
async def test_read_only_ranges_and_type_conflicts_are_blocked(registry: Registry) -> None:
    client = OpenRBusClient(FakeAccess(), registry=registry, enable_writes=True, max_access_level=2)
    with pytest.raises(ValidationError, match="not declared writable"):
        await client.write(1, "200d:00", 1, allow_unsafe=True, dry_run=True)
    with pytest.raises(ValidationError, match="maximum"):
        await client.write(1, "2300:00", Decimal("20.01"), allow_unsafe=True, dry_run=True)
    with pytest.raises(ValidationError, match="wire-type conflicts"):
        await client.write(1, "1003:01", b"\x00\x00", allow_unsafe=True, dry_run=True)


@pytest.mark.asyncio
async def test_range_conflict_requires_matching_device_family(registry: Registry) -> None:
    client = OpenRBusClient(FakeAccess(), registry=registry, enable_writes=True, max_access_level=2)
    with pytest.raises(ValidationError, match="device_family"):
        await client.write(1, "3043:00", 1, allow_unsafe=True, dry_run=True)
    with pytest.raises(AccessPolicyError, match="required level 5 exceeds configured maximum 2"):
        await client.write(1, "2009:00", 254, allow_unsafe=True, dry_run=True)


@pytest.mark.asyncio
async def test_readback_mismatch_is_an_error(registry: Registry) -> None:
    address = ObjectAddress(0x2300, 0)
    access = FakeAccess(
        {
            (1, address): b"\x00\x00",
            (1, EFFECTIVE_LEVEL): b"\x03",
        }
    )
    access.mismatch = True
    client = OpenRBusClient(
        access,
        registry=registry,
        enable_writes=True,
        max_access_level=2,
        minimum_write_interval=0,
    )
    with pytest.raises(WriteVerificationError):
        await client.write(1, address, Decimal("12.34"), allow_unsafe=True)


@pytest.mark.asyncio
async def test_default_policy_blocks_higher_level_read_before_io(registry: Registry) -> None:
    address = ObjectAddress(0x346A, 4)
    access = FakeAccess({(4, address): b"\x02", (4, EFFECTIVE_LEVEL): b"\x03"})
    client = OpenRBusClient(access, registry=registry)
    with pytest.raises(
        AccessPolicyError,
        match=r"blocks read on register 346a:04.*required level 3.*maximum 1",
    ):
        await client.read(4, address, device_family="Scb-10")
    assert client.session_access(4) is None


@pytest.mark.asyncio
async def test_default_policy_blocks_higher_level_write_before_raw_write(
    registry: Registry,
) -> None:
    address = ObjectAddress(0x340B, 3)
    access = FakeAccess({(4, address): bytes.fromhex("bf00"), (4, EFFECTIVE_LEVEL): b"\x03"})
    client = OpenRBusClient(access, registry=registry, enable_writes=True)
    with pytest.raises(
        AccessPolicyError,
        match=r"blocks write on register 340b:03.*required level 2.*maximum 1",
    ):
        await client.write(
            4,
            address,
            Decimal("19.1"),
            allow_unsafe=True,
            device_family="Scb-10",
        )
    assert access.writes == []
    assert client.session_access(4) is None


@pytest.mark.asyncio
async def test_explicit_policy_and_node_authorization_allow_higher_level_read(
    registry: Registry,
) -> None:
    address = ObjectAddress(0x346A, 4)
    access = FakeAccess({(4, address): b"\x02", (4, EFFECTIVE_LEVEL): b"\x03"})
    client = OpenRBusClient(access, registry=registry, max_access_level=3)
    result = await client.read(4, address, device_family="Scb-10")
    assert result.value == 2
    assert client.session_access(4).effective_level is AccessLevel.PROFESSIONAL


@pytest.mark.asyncio
async def test_explicit_policy_does_not_replace_node_authorization(registry: Registry) -> None:
    address = ObjectAddress(0x346A, 4)
    access = FakeAccess({(4, address): b"\x02", (4, EFFECTIVE_LEVEL): b"\x01"})
    client = OpenRBusClient(access, registry=registry, max_access_level=3)
    with pytest.raises(InsufficientAccessLevelError, match="effective level 1"):
        await client.read(4, address, device_family="Scb-10")


def test_access_policy_supports_direct_file_and_explicit_env(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = tmp_path / "access-level.conf"
    path.write_text("installer\n", encoding="ascii")
    assert AccessPolicy.from_file(path).max_access_level is AccessLevel.INSTALLER
    monkeypatch.setenv("TEST_OPENRBUS_MAX_LEVEL", "3")
    assert AccessPolicy.from_env("TEST_OPENRBUS_MAX_LEVEL").max_access_level is (
        AccessLevel.PROFESSIONAL
    )
    assert OpenRBusClient(FakeAccess(), max_access_level=2).access_policy.max_access_level is (
        AccessLevel.INSTALLER
    )


@pytest.mark.asyncio
async def test_unknown_access_requirement_is_blocked_before_io(registry: Registry) -> None:
    address = ObjectAddress(0x200D, 0)
    client = OpenRBusClient(FakeAccess({(1, address): b"\x01"}), registry=registry)
    with pytest.raises(AccessPolicyError, match="required access level is unknown"):
        await client.read(1, address)
