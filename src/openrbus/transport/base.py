"""Abstract transport contracts used by the protocol core."""

from __future__ import annotations

from typing import Protocol, runtime_checkable


@runtime_checkable
class AsyncMessageTransport(Protocol):
    """Transport complete protocol messages without exposing BLE details.

    This boundary is used by message-framed transports such as the validated
    BLE/CAN-IP path.  Future direct-bus adapters should normally implement the
    higher-level :class:`openrbus.access.AsyncObjectAccess` contract instead,
    once their physical and framing boundaries are independently established.
    """

    @property
    def is_connected(self) -> bool:
        """Whether the underlying connection is currently usable."""

    async def connect(self) -> None:
        """Open and prepare the connection."""

    async def disconnect(self) -> None:
        """Close the connection and release resources."""

    async def request(self, message: bytes, *, timeout: float) -> bytes:
        """Send one complete message and return its matching response."""
