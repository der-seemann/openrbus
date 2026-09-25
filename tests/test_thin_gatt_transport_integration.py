from __future__ import annotations

import base64
from collections import deque
from collections.abc import Mapping
from typing import Any

import pytest

from openrbus.access import ObjectRead
from openrbus.errors import CanOpenAbortError
from openrbus.object_client import RawObjectClient
from openrbus.protocol.ble_segments import BleSegmentCodec, BleSegmentReassembler
from openrbus.protocol.canip import CanIpMessage, GenericFunction, ObjectAddress
from openrbus.protocol.selector import unwrap_canip, wrap_canip
from openrbus.transport.thin_gatt import (
    ConnectionIdentity,
    GattHandles,
    ThinGattCorrelationError,
    ThinGattMessageTransport,
    ThinGattSession,
)


class ObjectChannel:
    def __init__(self) -> None:
        self.frames: deque[dict[str, Any]] = deque()
        self.request_reassembler = BleSegmentReassembler()
        self.actions: list[tuple[str, dict[str, Any]]] = []
        self._next_seq = 2

    async def action(self, name: str, payload: Mapping[str, Any], *, timeout: float) -> None:
        body = dict(payload)
        self.actions.append((name, body))
        if body["op"] == "CONNECT":
            self.epoch += 1
            self._next_seq = 2
            self.frames.extend(
                [
                    {
                        "kind": "response",
                        "op": "CONNECT",
                        "epoch": self.epoch,
                        "seq": 0,
                        "request_id": body["request_id"],
                        "status": "OK",
                        "gattc_if": 1,
                        "conn_id": 0,
                    },
                    {
                        "kind": "event",
                        "op": "CONNECTION_STATE",
                        "epoch": self.epoch,
                        "seq": 1,
                        "payload": {"state": "connected"},
                        "gattc_if": 1,
                        "conn_id": 0,
                    },
                ]
            )
            return
        if body["op"] != "WRITE_CHAR":
            return
        segment = base64.b64decode(body["value"])
        request = self.request_reassembler.feed(segment)
        self.frames.append(
            {
                "kind": "response",
                "op": "WRITE_CHAR",
                "epoch": body["epoch"],
                "seq": 0,
                "request_id": body["request_id"],
                "status": "OK",
                "payload": {},
                "gattc_if": 1,
                "conn_id": 0,
            }
        )
        if request is not None:
            decoded = CanIpMessage.decode(unwrap_canip(request))
            node, address = decoded.payload[0], decoded.payload[1:4]
            if decoded.function is GenericFunction.GET_LIST:
                count = decoded.payload[0]
                entries = bytearray()
                position = 1
                for index in range(count):
                    item_node = decoded.payload[position + 1]
                    item_address = decoded.payload[position + 2 : position + 5]
                    if index == 1:
                        entries.extend(
                            b"\x02"
                            + bytes((item_node,))
                            + item_address
                            + b"\x00\x00\x04"
                            + b"\x05\x00\x00\x00"
                        )
                    else:
                        entries.extend(
                            b"\x01"
                            + bytes((item_node,))
                            + item_address
                            + b"\x00\x00\x01"
                            + bytes((index,))
                        )
                    position += 5
                response = CanIpMessage(
                    GenericFunction.GET_LIST_RESPONSE, bytes((count,)) + entries
                )
            else:
                response = CanIpMessage(
                    GenericFunction.READ_POSITIVE,
                    bytes((node,)) + address + bytes((address[1],)),
                )
            response_wire = wrap_canip(response.encode())
            for response_segment in BleSegmentCodec().encode(response_wire):
                self.frames.append(
                    {
                        "kind": "event",
                        "op": "NOTIFICATION",
                        "epoch": body["epoch"],
                        "seq": self._next_seq,
                        "handle": 42,
                        "payload": {"value": response_segment},
                        "gattc_if": 1,
                        "conn_id": 0,
                    }
                )
                self._next_seq += 1

    async def poll(self, *, timeout: float) -> Mapping[str, Any] | None:
        return self.frames.popleft() if self.frames else None

    async def diagnostics(self) -> Mapping[str, Any]:
        return {"queue": len(self.frames)}

    epoch = 2


@pytest.mark.asyncio
async def test_raw_object_client_reads_through_thin_gatt_transport() -> None:
    channel = ObjectChannel()
    session = ThinGattSession(channel)
    session.connected, session.epoch, session.identity, session.last_seq = (
        True,
        2,
        ConnectionIdentity(1, 0),
        1,
    )
    session.install_handles(GattHandles({"request": 7, "response": 42}, epoch=2))
    transport = ThinGattMessageTransport(session, request_handle=7, response_handle=42)
    client = RawObjectClient(transport, timeout=1)

    first = await client.read_raw(3, ObjectAddress(0x1234, 1))
    second = await client.read_raw(3, ObjectAddress(0x1235, 1))
    assert first == b"\x34" and second == b"\x35"
    assert all(name == "openrbus_gatt_rpc_request" for name, _ in channel.actions)
    assert all(
        payload["handle"] == 7 and payload["response"] is True
        for name, payload in channel.actions
        if payload["op"] == "WRITE_CHAR"
    )
    assert all(
        base64.b64decode(payload["value"])[0] <= 127 or base64.b64decode(payload["value"])[0] == 255
        for name, payload in channel.actions
        if payload["op"] == "WRITE_CHAR"
    )


@pytest.mark.asyncio
async def test_raw_object_client_batch_preserves_partial_canopen_abort() -> None:
    channel = ObjectChannel()
    session = ThinGattSession(channel)
    session.connected, session.epoch, session.identity, session.last_seq = (
        True,
        2,
        ConnectionIdentity(1, 0),
        1,
    )
    session.install_handles(GattHandles({"response": 42}, epoch=2))
    client = RawObjectClient(
        ThinGattMessageTransport(session, request_handle=7, response_handle=42)
    )
    results = await client.read_many_raw(
        [ObjectRead(3, ObjectAddress(0x1200), 1), ObjectRead(3, ObjectAddress(0x1201), 1)]
    )
    assert results[0].raw == b"\x00"
    assert isinstance(results[1].error, CanOpenAbortError)


@pytest.mark.asyncio
async def test_raw_object_client_reconnects_with_higher_epoch() -> None:
    channel = ObjectChannel()
    session = ThinGattSession(channel)
    session.connected, session.epoch, session.identity, session.last_seq = (
        True,
        2,
        ConnectionIdentity(1, 0),
        1,
    )
    session.install_handles(GattHandles({"response": 42}, epoch=2))
    transport = ThinGattMessageTransport(session, request_handle=7, response_handle=42)
    client = RawObjectClient(transport, timeout=1)
    await client.read_raw(3, ObjectAddress(0x1234, 1))
    await transport.disconnect()
    await session.prepare(timeout=1)
    session.install_handles(GattHandles({"response": 42}, epoch=session.epoch))
    assert session.epoch == 3
    assert await client.read_raw(3, ObjectAddress(0x1235, 1)) == b"\x35"


@pytest.mark.asyncio
async def test_raw_object_client_rejects_cross_epoch_notification_and_cleans() -> None:
    channel = ObjectChannel()
    session = ThinGattSession(channel)
    session.connected, session.epoch, session.identity, session.last_seq = (
        True,
        2,
        ConnectionIdentity(1, 0),
        1,
    )
    session.install_handles(GattHandles({"response": 42}, epoch=2))
    channel.frames.append(
        {
            "kind": "event",
            "op": "NOTIFICATION",
            "epoch": 3,
            "seq": 2,
            "handle": 42,
            "payload": {"value": b"bad"},
            "gattc_if": 1,
            "conn_id": 0,
        }
    )
    with pytest.raises(ThinGattCorrelationError):
        await RawObjectClient(
            ThinGattMessageTransport(session, request_handle=7, response_handle=42)
        ).read_raw(3, ObjectAddress(0x1234, 1))
    assert session._pending_operations == {}
