from __future__ import annotations

import asyncio

import pytest

from openrbus.protocol.ble_segments import BleSegmentCodec, BleSegmentReassembler
from openrbus.transport.ble import (
    ERROR_MANAGEMENT,
    GATEWAY_AUTH_REQUEST,
    GATEWAY_AUTH_RESPONSE,
    GATEWAY_IDENT_REQUEST,
    GATEWAY_IDENT_RESPONSE,
    REQUEST_EXTENDED,
    RESPONSE_EXTENDED,
    BleakMessageTransport,
    discover_ble_devices,
)


def test_hardware_verified_extended_response_uuid() -> None:
    assert RESPONSE_EXTENDED == "ab9af948-fb86-492d-820d-bdea2dcb7ecf"


class FakeBleakClient:
    def __init__(self, address: str, timeout: float, **kwargs):
        self.address = address
        self.timeout = timeout
        self.kwargs = kwargs
        self.is_connected = False
        self.callbacks = {}
        self.incoming = BleSegmentReassembler()
        self.response_segments = 0

    async def connect(self) -> None:
        self.is_connected = True

    async def disconnect(self) -> None:
        self.is_connected = False

    async def pair(self) -> None:
        assert self.is_connected

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
async def test_ble_transport_passes_explicit_bluez_adapter() -> None:
    transport = BleakMessageTransport(
        "synthetic-device", client_factory=FakeBleakClient, adapter="hci1"
    )
    await transport.connect()
    assert transport._client.kwargs == {"bluez": {"adapter": "hci1"}}
    await transport.disconnect()


@pytest.mark.asyncio
async def test_ble_transport_disconnect_stops_notifications_before_client_close() -> None:
    class CallbackClient(FakeBleakClient):
        def __init__(self, address: str, timeout: float):
            super().__init__(address, timeout)
            self.stopped: list[str] = []

        async def stop_notify(self, characteristic: str) -> None:
            self.stopped.append(characteristic)
            self.callbacks.pop(characteristic, None)

    client = CallbackClient("synthetic-device", 1)
    transport = BleakMessageTransport("synthetic-device", client_factory=lambda *a, **k: client)
    await transport.connect()
    await transport.disconnect()
    assert client.stopped == [RESPONSE_EXTENDED, ERROR_MANAGEMENT]
    assert not client.callbacks


@pytest.mark.asyncio
async def test_ble_transport_discards_late_tail_at_request_boundary() -> None:
    class LateTailClient(FakeBleakClient):
        injected = False

        async def write_gatt_char(
            self, characteristic: str, data: bytes, *, response: bool
        ) -> None:
            if not self.injected:
                self.injected = True
                callback = self.callbacks[RESPONSE_EXTENDED]
                callback(None, bytearray(b"\x02stale-tail"))
                callback(None, bytearray(b"\xffstale-tail-end"))
            await super().write_gatt_char(characteristic, data, response=response)

    transport = BleakMessageTransport("synthetic-device", client_factory=LateTailClient)
    await transport.connect()
    assert await transport.request(b"request", timeout=1) == b"tseuqer"


@pytest.mark.asyncio
async def test_ble_transport_discards_idle_notifications() -> None:
    client: FakeBleakClient | None = None

    def factory(address: str, timeout: float) -> FakeBleakClient:
        nonlocal client
        client = FakeBleakClient(address, timeout)
        return client

    transport = BleakMessageTransport("synthetic-device", client_factory=factory)
    await transport.connect()
    assert client is not None
    client.callbacks[RESPONSE_EXTENDED](None, bytearray(b"\x00idle"))
    client.callbacks[ERROR_MANAGEMENT](None, bytearray(b"\xffidle"))
    assert transport._response_segments.empty()
    assert transport._error_segments.empty()
    assert await transport.request(b"request", timeout=1) == b"tseuqer"


@pytest.mark.asyncio
async def test_ble_transport_missing_current_frame_start_fails_closed() -> None:
    class MissingStartClient(FakeBleakClient):
        async def write_gatt_char(
            self, characteristic: str, data: bytes, *, response: bool
        ) -> None:
            callback = self.callbacks[RESPONSE_EXTENDED]
            callback(None, bytearray(b"\x01missing-start"))
            callback(None, bytearray(b"\xffmissing-start-end"))

    transport = BleakMessageTransport("synthetic-device", client_factory=MissingStartClient)
    await transport.connect()
    with pytest.raises(Exception, match="timed out"):
        await transport.request(b"request", timeout=0.01)


@pytest.mark.asyncio
async def test_ble_transport_marker_error_after_start_is_strict() -> None:
    class InvalidMarkerClient(FakeBleakClient):
        async def write_gatt_char(
            self, characteristic: str, data: bytes, *, response: bool
        ) -> None:
            callback = self.callbacks[RESPONSE_EXTENDED]
            callback(None, bytearray(b"\x00started"))
            callback(None, bytearray(b"\x02wrong-marker"))

    transport = BleakMessageTransport("synthetic-device", client_factory=InvalidMarkerClient)
    await transport.connect()
    with pytest.raises(Exception, match="invalid BLE response segmentation"):
        await transport.request(b"request", timeout=1)


@pytest.mark.asyncio
async def test_ble_transport_back_to_back_requests_are_not_poisoned() -> None:
    transport = BleakMessageTransport("synthetic-device", client_factory=FakeBleakClient)
    await transport.connect()
    client = transport._client
    assert client is not None
    assert await transport.request(b"first", timeout=1) == b"tsrif"

    # This arrives while no request is active and must not enter the next one.
    client.callbacks[RESPONSE_EXTENDED](None, bytearray(b"\x02late-tail"))
    client.callbacks[RESPONSE_EXTENDED](None, bytearray(b"\xfflate-tail-end"))
    assert await transport.request(b"second", timeout=1) == b"dnoces"


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


@pytest.mark.asyncio
async def test_ble_transport_scoped_pairing_agent_lifecycle(monkeypatch) -> None:
    """PIN pairing is bracketed by a temporary BlueZ agent registration."""

    events: list[tuple[str, str, int | None]] = []

    class FakeAgent:
        def __init__(self, address: str, pin: int):
            events.append(("construct", address, pin))

        async def start(self) -> None:
            events.append(("start", "", None))

        async def stop(self) -> None:
            events.append(("stop", "", None))

    import openrbus.transport.ble as ble_module

    monkeypatch.setattr(ble_module, "BlueZPairingAgent", FakeAgent)
    transport = BleakMessageTransport(
        "test-device",
        pairing_pin=123456,
        client_factory=FakeBleakClient,
    )
    await transport.connect()
    await transport.disconnect()
    assert events == [
        ("construct", "test-device", 123456),
        ("start", "", None),
        ("stop", "", None),
    ]


@pytest.mark.asyncio
async def test_ble_transport_calls_pair_before_subscribing(monkeypatch) -> None:
    class PairingClient(FakeBleakClient):
        def __init__(self, address: str, timeout: float):
            super().__init__(address, timeout)
            self.paired = False
            self.pair_calls = 0

        async def pair(self) -> None:
            assert self.is_connected
            self.pair_calls += 1
            self.paired = True

        async def start_notify(self, characteristic: str, callback) -> None:
            assert self.paired
            await super().start_notify(characteristic, callback)

    class FakeAgent:
        def __init__(self, address: str, pin: int):
            pass

        async def start(self) -> None:
            return None

        async def stop(self) -> None:
            return None

    import openrbus.transport.ble as ble_module

    monkeypatch.setattr(ble_module, "BlueZPairingAgent", FakeAgent)
    client = PairingClient("synthetic-device", 1)
    transport = BleakMessageTransport(
        "synthetic-device", pairing_pin=123456, client_factory=lambda *a, **k: client
    )
    await transport.connect()
    assert client.paired
    assert client.pair_calls == 1


@pytest.mark.asyncio
async def test_ble_transport_performs_gateway_service_auth_before_app_subscriptions() -> None:
    class GatewayClient(FakeBleakClient):
        def __init__(self, address: str, timeout: float):
            super().__init__(address, timeout)
            self.gateway_callbacks = {}

        async def start_notify(self, characteristic: str, callback) -> None:
            if characteristic in {GATEWAY_IDENT_RESPONSE, GATEWAY_AUTH_RESPONSE}:
                self.gateway_callbacks[characteristic] = callback
                return
            await super().start_notify(characteristic, callback)

        async def stop_notify(self, characteristic: str) -> None:
            self.gateway_callbacks.pop(characteristic, None)

        async def write_gatt_char(
            self, characteristic: str, data: bytes, *, response: bool
        ) -> None:
            if characteristic == GATEWAY_IDENT_REQUEST:
                self.gateway_callbacks[GATEWAY_IDENT_RESPONSE](None, bytearray(range(8)))
                return
            if characteristic == GATEWAY_AUTH_REQUEST:
                self.gateway_callbacks[GATEWAY_AUTH_RESPONSE](None, bytearray(b"\x01"))
                return
            await super().write_gatt_char(characteristic, data, response=response)

    client = GatewayClient("synthetic-device", 1)
    transport = BleakMessageTransport(
        "synthetic-device", client_factory=lambda *a, **k: client, gateway_auth=True
    )
    await transport.connect()

    assert GATEWAY_IDENT_RESPONSE not in client.gateway_callbacks
    assert GATEWAY_AUTH_RESPONSE not in client.gateway_callbacks
    assert RESPONSE_EXTENDED in client.callbacks
    await transport.disconnect()


@pytest.mark.asyncio
async def test_ble_transport_authorizes_before_subscribing() -> None:
    """Pairing traffic must not enter the application response queues."""

    events: list[str] = []

    class OrderedClient(FakeBleakClient):
        async def start_notify(self, characteristic: str, callback) -> None:
            events.append(f"notify:{characteristic}")
            await super().start_notify(characteristic, callback)

    async def authorize(client: OrderedClient) -> None:
        events.append("authorize")
        assert not client.callbacks

    transport = BleakMessageTransport(
        "synthetic-device",
        client_factory=OrderedClient,
        authorizer=authorize,
    )
    await transport.connect()

    assert events == [
        "authorize",
        f"notify:{RESPONSE_EXTENDED}",
        f"notify:{ERROR_MANAGEMENT}",
    ]
