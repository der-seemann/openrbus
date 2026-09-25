"""Transport-agnostic retry and reconnection policy."""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass

from openrbus.errors import ConnectionFailedError, TransportError

from .base import AsyncMessageTransport

_LOGGER = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class ConnectionPolicy:
    """Bounded reconnection settings."""

    attempts: int = 3
    initial_backoff: float = 0.25
    maximum_backoff: float = 2.0

    def __post_init__(self) -> None:
        if self.attempts < 1:
            raise ValueError("attempts must be at least one")
        if self.initial_backoff < 0 or self.maximum_backoff < self.initial_backoff:
            raise ValueError("invalid reconnect backoff")


class ManagedTransport:
    """Apply bounded reconnect behavior to an async message transport."""

    def __init__(self, transport: AsyncMessageTransport, policy: ConnectionPolicy | None = None):
        self.transport = transport
        self.policy = policy or ConnectionPolicy()
        self._lock = asyncio.Lock()

    @property
    def is_connected(self) -> bool:
        return self.transport.is_connected

    async def connect(self) -> None:
        async with self._lock:
            if self.transport.is_connected:
                return
            delay = self.policy.initial_backoff
            last_error: Exception | None = None
            for attempt in range(1, self.policy.attempts + 1):
                try:
                    await self.transport.connect()
                    if not self.transport.is_connected:
                        raise TransportError("transport returned from connect while disconnected")
                    return
                except Exception as exc:  # adapter errors are normalized here
                    last_error = exc
                    _LOGGER.warning(
                        "connection attempt %d/%d failed", attempt, self.policy.attempts
                    )
                    if attempt < self.policy.attempts:
                        await asyncio.sleep(delay)
                        delay = min(max(delay * 2, 0.01), self.policy.maximum_backoff)
            raise ConnectionFailedError("connection attempts exhausted") from last_error

    async def disconnect(self) -> None:
        async with self._lock:
            await self.transport.disconnect()

    async def request(self, message: bytes, *, timeout: float) -> bytes:
        if timeout <= 0:
            raise ValueError("timeout must be positive")
        if not self.transport.is_connected:
            await self.connect()
        try:
            return await self.transport.request(message, timeout=timeout)
        except TransportError:
            await self.disconnect()
            await self.connect()
            return await self.transport.request(message, timeout=timeout)
