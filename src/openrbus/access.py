"""Transport-independent object-access contract."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import Protocol, runtime_checkable

from openrbus.errors import CanOpenAbortError
from openrbus.protocol.canip import ObjectAddress


@dataclass(frozen=True, slots=True)
class ObjectRead:
    """One bulk-read item with an upper bound for response-size planning."""

    node: int
    address: ObjectAddress
    max_value_length: int

    def __post_init__(self) -> None:
        if not 1 <= self.node <= 0xFF:
            raise ValueError("node must be 0x01..0xff")
        if self.max_value_length <= 0:
            raise ValueError("max_value_length must be positive")


@dataclass(frozen=True, slots=True)
class RawReadResult:
    """Raw value or per-object abort returned by a bulk read."""

    node: int
    address: ObjectAddress
    raw: bytes | None = field(default=None, repr=False)
    error: CanOpenAbortError | None = None

    def __post_init__(self) -> None:
        if (self.raw is None) == (self.error is None):
            raise ValueError("exactly one of raw or error must be set")


@runtime_checkable
class AsyncObjectAccess(Protocol):
    """Read and write raw CANopen-style objects asynchronously.

    The BLE/CAN-IP stack implements this interface through ``RawObjectClient``.
    A future independently validated direct-bus adapter can implement the same
    boundary without importing BLE-specific code.
    """

    async def read_raw(
        self, node: int, address: ObjectAddress, *, timeout: float | None = None
    ) -> bytes:
        """Return the raw value bytes for one object."""

    async def _write_raw(
        self,
        node: int,
        address: ObjectAddress,
        raw_value: bytes,
        *,
        timeout: float | None = None,
    ) -> None:
        """Internal primitive used only behind the public write safety gates."""


@runtime_checkable
class AsyncBulkObjectAccess(AsyncObjectAccess, Protocol):
    """Optional capability for efficient, size-aware multi-object reads."""

    async def read_many_raw(
        self,
        items: Sequence[ObjectRead],
        *,
        timeout: float | None = None,
    ) -> tuple[RawReadResult, ...]:
        """Read multiple objects while preserving order and per-item aborts."""


@runtime_checkable
class AsyncNodeAuthorizationAccess(AsyncObjectAccess, Protocol):
    """Object access that can switch a node to an allocated authorization channel.

    A plain CAN-IP generic-purpose adapter does not expose CANopen SDO channels
    and therefore must not claim this capability. Direct CANopen adapters and a
    future CAN-IP authorization-purpose adapter can implement it explicitly.
    """

    async def select_authorization_channel(
        self,
        node: int,
        channel: int,
        *,
        timeout: float | None = None,
    ) -> None:
        """Use ``channel`` for subsequent authorization object access on ``node``."""
