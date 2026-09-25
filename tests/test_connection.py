from __future__ import annotations

from dataclasses import dataclass

import pytest

from openrbus.errors import TransportError
from openrbus.transport.connection import ConnectionPolicy, ManagedTransport


@dataclass
class FakeTransport:
    connect_failures: int = 0
    request_failures: int = 0
    connected: bool = False
    connects: int = 0
    disconnects: int = 0

    @property
    def is_connected(self) -> bool:
        return self.connected

    async def connect(self) -> None:
        self.connects += 1
        if self.connects <= self.connect_failures:
            raise OSError("synthetic connect failure")
        self.connected = True

    async def disconnect(self) -> None:
        self.disconnects += 1
        self.connected = False

    async def request(self, message: bytes, *, timeout: float) -> bytes:
        if self.request_failures:
            self.request_failures -= 1
            raise TransportError("synthetic request failure")
        return message[::-1]


@pytest.mark.asyncio
async def test_connect_retries_are_bounded() -> None:
    fake = FakeTransport(connect_failures=1)
    managed = ManagedTransport(fake, ConnectionPolicy(attempts=2, initial_backoff=0))
    await managed.connect()
    assert fake.is_connected
    assert fake.connects == 2


@pytest.mark.asyncio
async def test_request_reconnects_once_after_transport_error() -> None:
    fake = FakeTransport(request_failures=1)
    managed = ManagedTransport(fake, ConnectionPolicy(initial_backoff=0))
    assert await managed.request(b"abc", timeout=1) == b"cba"
    assert fake.disconnects == 1
    assert fake.connects == 2
