from __future__ import annotations

import asyncio

import pytest

from openrbus.protocol.ble_segments import BleSegmentCodec, BleSegmentReassembler
from openrbus.transport.ble import (
    ERROR_MANAGEMENT,
    REQUEST_EXTENDED,
    RESPONSE_EXTENDED,
    BleakMessageTransport,
    discover_ble_devices,
)


class FakeBleakClient:
    def __init__(self, address: str, timeout: float):
        self.address = address
        self.timeout = timeout
        self.is_connected = False
        self.callbacks = {}
        self.incoming = BleSegmentReassembler()
        self.response_segments = 0

    async def connect(self) -> None:
        self.is_connected = True

    async def disconnect(self) -> None:
        self.is_connected = False

    async def start_notify(self, characteristic: str, callback) -> None:
        self.callbacks[characteristic] = callback

    async def write_gatt_char(self, characteristic: str, data: bytes, *, response: bool) -> None:
        assert characteristic == REQUEST_EXTENDED
        assert response
        message = self.incoming.feed(data)
        if message is not None:
            for segment in BleSegmentCodec().encode(message[::-1]):
                self.response_segments += 1
                self.callbacks[RESPONSE_EXTENDED](None, bytearray(segment))


@pytest.mark.asyncio
async def test_ble_transport_segments_and_correlates_one_request() -> None:
    transport = BleakMessageTransport(
        "synthetic-device", client_factory=FakeBleakClient, connect_timeout=1
    )
    await transport.connect()
    assert await transport.request(bytes(range(40)), timeout=1) == bytes(range(40))[::-1]
    assert transport._client.response_segments == 3
    await transport.disconnect()
    assert not transport.is_connected


@pytest.mark.asyncio
async def test_error_notification_is_not_exposed_as_raw_data() -> None:
    client: FakeBleakClient | None = None

    def factory(address: str, timeout: float) -> FakeBleakClient:
        nonlocal client
        client = FakeBleakClient(address, timeout)
        return client

    transport = BleakMessageTransport("synthetic-device", client_factory=factory)
    await transport.connect()
    assert client is not None

    # This request exercises the separate ErrorManagement path only.
    async def no_response(*args, **kwargs) -> None:
        return None

    client.write_gatt_char = no_response  # type: ignore[method-assign]

    async def emit_error() -> None:
        await asyncio.sleep(0)
        for segment in BleSegmentCodec().encode(b"synthetic-error"):
            client.callbacks[ERROR_MANAGEMENT](None, bytearray(segment))

    task = asyncio.create_task(emit_error())
    with pytest.raises(Exception, match="ErrorManagement"):
        await transport.request(b"request", timeout=1)
    await task


@pytest.mark.asyncio
async def test_ble_discovery_filters_by_service_uuid() -> None:
    class Advertisement:
        def __init__(self, service_uuids):
            self.service_uuids = service_uuids

    class Scanner:
        @staticmethod
        async def discover(*, timeout: float, return_adv: bool):
            assert return_adv
            return {
                "a": ("match", Advertisement(["f8fc98e4-5919-4a5c-852e-dfe04ad383c0"])),
                "b": ("other", Advertisement([])),
            }

    assert await discover_ble_devices(timeout=1, scanner=Scanner) == ["match"]
