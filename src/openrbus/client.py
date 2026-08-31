"""High-level async read client with explicitly gated writes."""

from __future__ import annotations

import asyncio
import logging
import time
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from types import MappingProxyType
from typing import TypeAlias

from openrbus.access import (
    AsyncBulkObjectAccess,
    AsyncObjectAccess,
    ObjectRead,
    RawReadResult,
)
from openrbus.access_policy import AccessPolicy, resolve_access_policy
from openrbus.errors import (
    AccessLevelAmbiguityError,
    AccessLevelUnavailableError,
    CanOpenAbortError,
    InsufficientAccessLevelError,
    ProtocolError,
    UnsafeWriteError,
    ValidationError,
    WritesDisabledError,
    WriteVerificationError,
)
from openrbus.protocol.canip import ObjectAddress
from openrbus.registry import (
    AccessLevel,
    AccessOperation,
    RegisterDefinition,
    Registry,
    ValueConstraint,
)
from openrbus.value_codec import (
    CanOpenTimeOfDay,
    DecodedValue,
    EncodableValue,
    decode_value,
    encode_value,
)

_LOGGER = logging.getLogger(__name__)

AddressLike = str | ObjectAddress | tuple[int, int]
EFFECTIVE_ACCESS_LEVEL_OBJECT = ObjectAddress(0x4002, 0x00)


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
    required_access_level: AccessLevel | None = None
    session_access: SessionAccess | None = None

    @property
    def higher_risk_access(self) -> bool:
        """Whether static evidence requires installer level or above."""

        return bool(
            self.required_access_level is not None and self.required_access_level.is_higher_risk
        )


@dataclass(frozen=True, slots=True)
class SessionAccess:
    """Authoritatively verified effective access level for one live node."""

    node: int
    effective_level: AccessLevel
    sampled_at: float = field(repr=False)

    def satisfies(self, required: AccessLevel) -> bool:
        """Return whether the verified effective level satisfies a requirement."""

        return self.effective_level >= required


class OpenRBusClient:
    """Registry-driven asynchronous client.

    The local access policy defaults to known level-1 reads. Writing requires
    constructor-level ``enable_writes=True``. Every currently published
    writable definition additionally requires call-level ``allow_unsafe=True``;
    independently hardware-validated definitions can omit that second opt-in.
    """

    def __init__(
        self,
        access: AsyncObjectAccess,
        *,
        registry: Registry | None = None,
        enable_writes: bool = False,
        access_policy: AccessPolicy | None = None,
        max_access_level: AccessLevel | int | None = None,
        minimum_write_interval: float = 1.0,
    ) -> None:
        if minimum_write_interval < 0:
            raise ValueError("minimum_write_interval must not be negative")
        self.access = access
        self.registry = registry or Registry.load_default()
        self.enable_writes = enable_writes
        self.access_policy = resolve_access_policy(access_policy, max_access_level)
        self.minimum_write_interval = minimum_write_interval
        self._write_lock = asyncio.Lock()
        self._last_write = float("-inf")
        self._session_access_levels: dict[int, SessionAccess] = {}

    @property
    def session_access_levels(self) -> Mapping[int, SessionAccess]:
        """Return an immutable snapshot of access levels read from live nodes."""

        return MappingProxyType(dict(self._session_access_levels))

    def session_access(self, node: int) -> SessionAccess | None:
        """Return the last access-level sample for ``node``, if any."""

        return self._session_access_levels.get(node)

    async def refresh_session_access(
        self,
        node: int,
        *,
        timeout: float | None = None,
    ) -> SessionAccess:
        """Read authoritative effective access level ``4002:00``.

        This method is intentionally read-only. Node elevation, when explicitly
        requested by an application, is handled separately by ``NodeAuthorizer``.
        """

        if not 1 <= node <= 0xFF:
            raise ValueError("node must be 0x01..0xff")
        try:
            effective_raw = await self.access.read_raw(
                node, EFFECTIVE_ACCESS_LEVEL_OBJECT, timeout=timeout
            )
        except CanOpenAbortError as error:
            raise AccessLevelUnavailableError(
                f"node {node:02x} could not read effective access level "
                f"from {EFFECTIVE_ACCESS_LEVEL_OBJECT}: {error}"
            ) from error
        effective = _decode_access_level(
            effective_raw, node=node, address=EFFECTIVE_ACCESS_LEVEL_OBJECT
        )
        result = SessionAccess(node, effective, time.monotonic())
        self._session_access_levels[node] = result
        return result

    async def read(
        self,
        node: int,
        address: AddressLike,
        *,
        device_family: str | None = None,
        timeout: float | None = None,
    ) -> ReadResult:
        """Read and decode one register."""

        resolved = _address(address)
        definition = self.registry.get(resolved)
        required = self._validate_access_policy(
            resolved,
            AccessOperation.READ,
            device_family=device_family,
        )
        if required.is_higher_risk:
            session = await self.refresh_session_access(node, timeout=timeout)
            self._validate_session_access(resolved, required, session)
        raw = await self.access.read_raw(node, resolved, timeout=timeout)
        value = decode_value(definition, resolved, raw, registry=self.registry)
        return ReadResult(node, resolved, definition, value, raw)

    async def read_many(
        self,
        node: int,
        addresses: Iterable[AddressLike],
        *,
        device_family: str | None = None,
        timeout: float | None = None,
    ) -> tuple[ReadOutcome, ...]:
        """Read and decode multiple same-node registers efficiently.

        A bulk-capable object adapter uses validated GetList batches.  Other
        adapters retain compatibility through sequential single reads.  CANopen
        aborts are returned per object so successful values remain available.
        """

        resolved = tuple(_address(address) for address in addresses)
        definitions = tuple(self.registry.get(address) for address in resolved)
        required_levels = tuple(
            self._validate_access_policy(
                address,
                AccessOperation.READ,
                device_family=device_family,
            )
            for address in resolved
        )
        required = max(required_levels, default=AccessLevel.USER)
        if required.is_higher_risk:
            session = await self.refresh_session_access(node, timeout=timeout)
            for address, item_required in zip(resolved, required_levels, strict=True):
                self._validate_session_access(address, item_required, session)
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
        constraint, required_access_level = self._validate_write(
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
        session_access = self.session_access(node)
        if required_access_level is not None and session_access is not None:
            self._validate_session_access(resolved, required_access_level, session_access)
        plan = WritePlan(
            node,
            resolved,
            definition,
            raw,
            dry_run=dry_run,
            verified=False,
            required_access_level=required_access_level,
            session_access=session_access,
        )
        _LOGGER.info(
            "write planned: node=%02x object=%s dry_run=%s required_access=%s",
            node,
            resolved,
            dry_run,
            int(required_access_level) if required_access_level is not None else "unknown",
        )
        if dry_run:
            return plan

        async with self._write_lock:
            remaining = self.minimum_write_interval - (time.monotonic() - self._last_write)
            if remaining > 0:
                await asyncio.sleep(remaining)
            if required_access_level is not None:
                session_access = await self.refresh_session_access(node, timeout=timeout)
                self._validate_session_access(resolved, required_access_level, session_access)
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
            return WritePlan(
                node,
                resolved,
                definition,
                raw,
                dry_run=False,
                verified=verify,
                required_access_level=required_access_level,
                session_access=session_access,
            )

    def _validate_write(
        self,
        definition: RegisterDefinition,
        address: ObjectAddress,
        *,
        enable_writes: bool,
        allow_unsafe: bool,
        device_family: str | None,
    ) -> tuple[ValueConstraint | None, AccessLevel | None]:
        if not enable_writes:
            raise WritesDisabledError("writes require enable_writes=True")
        if not definition.access.writable_declared:
            raise ValidationError(f"register {address} is not declared writable")
        if definition.evidence.type_conflict:
            raise ValidationError(
                f"register {address} has unresolved device-specific wire-type conflicts"
            )

        access_requirement = self.registry.access_requirement(
            address,
            AccessOperation.WRITE,
            device_family=device_family,
        )
        required_for_policy = self.access_policy.require_register(address, access_requirement)
        if access_requirement.is_ambiguous:
            levels = ", ".join(str(int(level)) for level in access_requirement.levels)
            families = ", ".join(access_requirement.device_families) or "unknown families"
            raise AccessLevelAmbiguityError(
                f"register {address} has conflicting required write levels "
                f"({levels}) across {families}; device_family is required"
            )
        required_access_level = access_requirement.required_level
        assert required_access_level is not None
        assert required_access_level == required_for_policy

        if definition.safety.requires_unsafe_opt_in and not allow_unsafe:
            raise UnsafeWriteError(
                f"register {address} is unverified and requires allow_unsafe=True"
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
            return definition.constraint, required_access_level
        if not definition.constraint_variants:
            return None, required_access_level
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
        return matches[0], required_access_level

    def _validate_access_policy(
        self,
        address: ObjectAddress,
        operation: AccessOperation,
        *,
        device_family: str | None,
    ) -> AccessLevel:
        requirement = self.registry.access_requirement(
            address,
            operation,
            device_family=device_family,
        )
        return self.access_policy.require_register(address, requirement)

    @staticmethod
    def _validate_session_access(
        address: ObjectAddress,
        required: AccessLevel,
        session: SessionAccess,
    ) -> None:
        if not session.satisfies(required):
            raise InsufficientAccessLevelError(
                address,
                int(required),
                int(session.effective_level),
            )


def _address(value: AddressLike) -> ObjectAddress:
    if isinstance(value, ObjectAddress):
        return value
    if isinstance(value, tuple):
        if len(value) != 2:
            raise ValueError("address tuple must contain index and subindex")
        return ObjectAddress(*value)
    return ObjectAddress.parse(value)


def _decode_access_level(raw: bytes, *, node: int, address: ObjectAddress) -> AccessLevel:
    if len(raw) != 1:
        raise AccessLevelUnavailableError(
            f"node {node:02x} object {address} returned {len(raw)} bytes; expected one"
        )
    try:
        return AccessLevel(raw[0])
    except ValueError as error:
        raise AccessLevelUnavailableError(
            f"node {node:02x} object {address} returned unsupported access level {raw[0]}"
        ) from error


__all__ = [
    "CanOpenTimeOfDay",
    "OpenRBusClient",
    "ReadFailure",
    "ReadOutcome",
    "ReadResult",
    "SessionAccess",
    "WritePlan",
]
