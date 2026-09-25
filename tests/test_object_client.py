from __future__ import annotations

import pytest

from openrbus.access import ObjectRead
from openrbus.object_client import RawObjectClient
from openrbus.protocol.canip import CanIpMessage, GenericFunction, ObjectAddress
from openrbus.protocol.selector import unwrap_canip, wrap_canip


class FakeTransport:
    is_connected = True

    def __init__(self) -> None:
        self.functions: list[GenericFunction] = []
        self.batch_counts: list[int] = []

    async def connect(self) -> None:
        return None

    async def disconnect(self) -> None:
        return None

    async def request(self, message: bytes, *, timeout: float) -> bytes:
        request = CanIpMessage.decode(unwrap_canip(message))
        self.functions.append(request.function)
        if request.function is GenericFunction.GET_LIST:
            count = request.payload[0]
            self.batch_counts.append(count)
            entries = bytearray()
            position = 1
            for _ in range(count):
                assert request.payload[position] == 0
                node = request.payload[position + 1]
                address = request.payload[position + 2 : position + 5]
                value = bytes((address[1],))
                entries.extend(b"\x01" + bytes((node,)) + address + b"\x00\x00\x01" + value)
                position += 5
            response = CanIpMessage(
                GenericFunction.GET_LIST_RESPONSE, bytes((count,)) + bytes(entries)
            )
            return wrap_canip(response.encode())

        node, address = request.payload[0], request.payload[1:4]
        if request.function is GenericFunction.READ:
            response = CanIpMessage(
                GenericFunction.READ_POSITIVE, bytes((node,)) + address + b"\x2a"
            )
        else:
            response = CanIpMessage(GenericFunction.WRITE_POSITIVE, bytes((node,)) + address)
        return wrap_canip(response.encode())


@pytest.mark.asyncio
async def test_raw_object_read_and_private_write_correlate_responses() -> None:
    client = RawObjectClient(FakeTransport())
    address = ObjectAddress(0x1234, 1)
    assert await client.read_raw(3, address) == b"\x2a"
    await client._write_raw(3, address, b"\x01")


@pytest.mark.asyncio
async def test_read_many_splits_at_100_and_preserves_order() -> None:
    transport = FakeTransport()
    client = RawObjectClient(transport)
    items = tuple(ObjectRead(3, ObjectAddress(0x2000 + index), 1) for index in range(101))
    results = await client.read_many_raw(items)
    assert len(results) == 101
    assert transport.batch_counts == [100, 1]
    assert [result.address for result in results] == [item.address for item in items]
    assert results[1].raw == b"\x01"


@pytest.mark.asyncio
async def test_read_many_obeys_response_size_and_single_read_fallback() -> None:
    transport = FakeTransport()
    client = RawObjectClient(transport)
    items = (
        ObjectRead(1, ObjectAddress(0x3000), 800),
        ObjectRead(1, ObjectAddress(0x3001), 800),
        ObjectRead(1, ObjectAddress(0x3002), 1600),
    )
    results = await client.read_many_raw(items)
    assert [result.raw for result in results] == [b"\x00", b"\x01", b"\x2a"]
    assert transport.batch_counts == [1, 1]
    assert transport.functions[-1] is GenericFunction.READ
