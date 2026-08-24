"""High-level async read client with explicitly gated writes."""

from __future__ import annotations

import asyncio
import logging
import time
from collections.abc import Iterable
from dataclasses import dataclass, field
from typing import TypeAlias

from openrbus.access import (
    AsyncBulkObjectAccess,
    AsyncObjectAccess,
    ObjectRead,
    RawReadResult,
)
from openrbus.errors import (
    CanOpenAbortError,
    ProtocolError,
    UnsafeWriteError,
    ValidationError,
    WritesDisabledError,
    WriteVerificationError,
)
from openrbus.protocol.canip import ObjectAddress
from openrbus.registry import RegisterDefinition, Registry, ValueConstraint
from openrbus.value_codec import (
    CanOpenTimeOfDay,
    DecodedValue,
    EncodableValue,
    decode_value,
    encode_value,
)

_LOGGER = logging.getLogger(__name__)

AddressLike = str | ObjectAddress | tuple[int, int]


@dataclass(frozen=True, slots=True)
class ReadResult:
    """A typed register value and its definition."""

    node: int
    address: ObjectAddress
    definition: RegisterDefinition
    value: DecodedValue
    raw: bytes = field(repr=False)


@dataclass(frozen=True, slots=True)
class ReadFailure:
    """One object-level abort from an otherwise completed multi-read."""

    node: int
    address: ObjectAddress
    definition: RegisterDefinition
    error: CanOpenAbortError


ReadOutcome: TypeAlias = ReadResult | ReadFailure


@dataclass(frozen=True, slots=True)
class WritePlan:
    """The fully validated result of planning a write."""

    node: int
    address: ObjectAddress
    definition: RegisterDefinition
    raw_value: bytes = field(repr=False)
    dry_run: bool = True
    verified: bool = False


class OpenRBusClient:
    """Registry-driven asynchronous client.

    Reading is always enabled.  Writing requires constructor-level
    ``enable_writes=True`` and, for every currently published writable
    definition, call-level ``allow_unsafe=True``.  This deliberately remains
    conservative until reversible writes are independently hardware-validated.
    """

    def __init__(
        self,
        access: AsyncObjectAccess,
        *,
        registry: Registry | None = None,
        enable_writes: bool = False,
        minimum_write_interval: float = 1.0,
    ) -> None:
        if minimum_write_interval < 0:
            raise ValueError("minimum_write_interval must not be negative")
        self.access = access
        self.registry = registry or Registry.load_default()
        self.enable_writes = enable_writes
        self.minimum_write_interval = minimum_write_interval
        self._write_lock = asyncio.Lock()
        self._last_write = float("-inf")

    async def read(
        self,
        node: int,
        address: AddressLike,
        *,
        timeout: float | None = None,
    ) -> ReadResult:
        """Read and decode one register."""

        resolved = _address(address)
        definition = self.registry.get(resolved)
        raw = await self.access.read_raw(node, resolved, timeout=timeout)
        value = decode_value(definition, resolved, raw, registry=self.registry)
        return ReadResult(node, resolved, definition, value, raw)

    async def read_many(
        self,
        node: int,
        addresses: Iterable[AddressLike],
        *,
        timeout: float | None = None,
    ) -> tuple[ReadOutcome, ...]:
        """Read and decode multiple same-node registers efficiently.

        A bulk-capable object adapter uses validated GetList batches.  Other
        adapters retain compatibility through sequential single reads.  CANopen
        aborts are returned per object so successful values remain available.
        """

        resolved = tuple(_address(address) for address in addresses)
        definitions = tuple(self.registry.get(address) for address in resolved)
        if isinstance(self.access, AsyncBulkObjectAccess):
            raw_results = await self.access.read_many_raw(
                tuple(
                    ObjectRead(node, address, definition.wire.length)
                    for address, definition in zip(resolved, definitions, strict=True)
                ),
                timeout=timeout,
            )
        else:
            fallback: list[RawReadResult] = []
            for address in resolved:
                try:
                    raw = await self.access.read_raw(node, address, timeout=timeout)
                except CanOpenAbortError as exc:
                    fallback.append(RawReadResult(node, address, error=exc))
                else:
                    fallback.append(RawReadResult(node, address, raw=raw))
            raw_results = tuple(fallback)

        if len(raw_results) != len(resolved):
            raise ProtocolError(
                f"bulk object access returned {len(raw_results)} results for {len(resolved)} items"
            )
        outcomes: list[ReadOutcome] = []
        for address, definition, result in zip(resolved, definitions, raw_results, strict=True):
            if result.node != node or result.address != address:
                raise ProtocolError(
                    f"bulk object correlation mismatch: node={result.node:02x}, "
                    f"address={result.address}"
                )
            if result.error is not None:
                outcomes.append(ReadFailure(node, address, definition, result.error))
                continue
            if result.raw is None:
                raise ProtocolError("bulk object result has neither value nor abort")
            value = decode_value(
                definition,
                address,
                result.raw,
                registry=self.registry,
            )
            outcomes.append(ReadResult(node, address, definition, value, result.raw))
        return tuple(outcomes)

    async def write(
        self,
        node: int,
        address: AddressLike,
        value: EncodableValue,
        *,
        allow_unsafe: bool = False,
        dry_run: bool = False,
        verify: bool = True,
        device_family: str | None = None,
        timeout: float | None = None,
    ) -> WritePlan:
        """Validate and optionally perform one confirmed write.

        ``dry_run`` performs every safety and datatype check but does no I/O.
        It still requires the explicit write and unsafe opt-ins so that the
        same call cannot silently become active under different configuration.
        """

        resolved = _address(address)
        definition = self.registry.get(resolved)
        constraint = self._validate_write(
            definition,
            resolved,
            enable_writes=self.enable_writes,
            allow_unsafe=allow_unsafe,
            device_family=device_family,
        )
        raw = encode_value(
            definition,
            resolved,
            value,
            registry=self.registry,
            constraint=constraint,
        )
        plan = WritePlan(node, resolved, definition, raw, dry_run=dry_run, verified=False)
        _LOGGER.info("write planned: node=%02x object=%s dry_run=%s", node, resolved, dry_run)
        if dry_run:
            return plan

        async with self._write_lock:
            remaining = self.minimum_write_interval - (time.monotonic() - self._last_write)
            if remaining > 0:
                await asyncio.sleep(remaining)
            _LOGGER.warning("write started: node=%02x object=%s", node, resolved)
            try:
                await self.access._write_raw(node, resolved, raw, timeout=timeout)
            finally:
                self._last_write = time.monotonic()
            if verify:
                read_back = await self.access.read_raw(node, resolved, timeout=timeout)
                if read_back != raw:
                    _LOGGER.error("write verification failed: node=%02x object=%s", node, resolved)
                    raise WriteVerificationError(
                        f"read-back mismatch for node {node:02x} object {resolved}"
                    )
            _LOGGER.warning(
                "write completed: node=%02x object=%s verified=%s", node, resolved, verify
            )
            return WritePlan(node, resolved, definition, raw, dry_run=False, verified=verify)

    @staticmethod
    def _validate_write(
        definition: RegisterDefinition,
        address: ObjectAddress,
        *,
        enable_writes: bool,
        allow_unsafe: bool,
        device_family: str | None,
    ) -> ValueConstraint | None:
        if not enable_writes:
            raise WritesDisabledError("writes require enable_writes=True")
        if not definition.access.writable_declared:
            raise ValidationError(f"register {address} is not declared writable")
        if definition.safety.requires_unsafe_opt_in and not allow_unsafe:
            raise UnsafeWriteError(
                f"register {address} is unverified and requires allow_unsafe=True"
            )
        if definition.evidence.type_conflict:
            raise ValidationError(
                f"register {address} has unresolved device-specific wire-type conflicts"
            )

        matching_evidence = tuple(
            row
            for row in definition.evidence.devices
            if row.address == address
            and device_family is not None
            and row.family.casefold() == device_family.casefold()
        )
        if matching_evidence and not any(row.writable_any is True for row in matching_evidence):
            raise ValidationError(
                f"register {address} is not writable in device family {device_family}"
            )

        if definition.constraint is not None:
            return definition.constraint
        if not definition.constraint_variants:
            return None
        if device_family is None:
            raise ValidationError(
                f"register {address} has device-specific range conflicts; device_family is required"
            )
        matches = tuple(
            item
            for item in definition.constraint_variants
            if any(family.casefold() == device_family.casefold() for family in item.device_families)
        )
        if len(matches) != 1:
            raise ValidationError(
                f"no unambiguous range is available for {address} and {device_family}"
            )
        return matches[0]


def _address(value: AddressLike) -> ObjectAddress:
    if isinstance(value, ObjectAddress):
        return value
    if isinstance(value, tuple):
        if len(value) != 2:
            raise ValueError("address tuple must contain index and subindex")
        return ObjectAddress(*value)
    return ObjectAddress.parse(value)


__all__ = [
    "CanOpenTimeOfDay",
    "OpenRBusClient",
    "ReadFailure",
    "ReadOutcome",
    "ReadResult",
    "WritePlan",
]
