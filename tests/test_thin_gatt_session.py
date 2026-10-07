from __future__ import annotations

import asyncio
import base64
from collections import deque
from collections.abc import Mapping
from typing import Any, cast

import pytest

from openrbus.errors import RequestTimeoutError, TransportError
from openrbus.transport.gateway_auth import authenticate_gateway, compute_gateway_auth_payload
from openrbus.transport.thin_gatt import (
    CAPABILITY_MARKER,
    ConnectionIdentity,
    GattHandles,
    GattNotification,
    ThinGattCapabilityError,
    ThinGattCorrelationError,
    ThinGattFlowControlError,
    ThinGattLink,
    ThinGattMessageTransport,
    ThinGattSession,
)


class FakeChannel:
    def __init__(self, frames: list[dict[str, Any]]) -> None:
        self.frames = deque(frames)
        self.actions: list[tuple[str, dict[str, Any]]] = []

    async def action(self, name: str, payload: Mapping[str, Any], *, timeout: float) -> None:
        self.actions.append((name, dict(payload)))

    async def poll(self, *, timeout: float) -> dict[str, Any] | None:
        return self.frames.popleft() if self.frames else None

    async def diagnostics(self) -> dict[str, Any]:
        return {"queue": len(self.frames)}


@pytest.mark.asyncio
async def test_gateway_auth_helper_uses_canonical_order_and_epoch() -> None:
    calls: list[tuple[str, str, str]] = []
    current_epoch = 7

    async def subscribe(value: str, cccd: str) -> None:
        calls.append(("subscribe", value, cccd))

    async def write(role: str, value: bytes, response_role: str) -> bytes:
        calls.append(("write", role, response_role))
        return b"identity" if role == "identity" else b"\x01"

    async def wait(_role: str, _timeout: float) -> bytes:
        raise AssertionError("transport returned no correlated gateway response")

    await authenticate_gateway(
        subscribe=subscribe,
        write=write,
        wait=wait,
        epoch=lambda: current_epoch,
        timeout=1,
    )
    assert calls == [
        ("subscribe", "identity_notify", "identity_cccd"),
        ("subscribe", "auth_notify", "auth_cccd"),
        ("write", "identity", "identity_notify"),
        ("write", "auth", "auth_notify"),
    ]


@pytest.mark.asyncio
async def test_gateway_auth_helper_rejects_negative_response() -> None:
    async def subscribe(_value: str, _cccd: str) -> None:
        return None

    async def write(role: str, _value: bytes, _response_role: str) -> bytes:
        return b"identity" if role == "identity" else b"\x00"

    async def wait(_role: str, _timeout: float) -> bytes:
        raise AssertionError("transport returned no correlated gateway response")

    with pytest.raises(TransportError, match="rejected"):
        await authenticate_gateway(
            subscribe=subscribe,
            write=write,
            wait=wait,
            epoch=lambda: 1,
            timeout=1,
        )


def frame(kind: str, op: str, epoch: int, seq: int, **extra: Any) -> dict[str, Any]:
    return {"kind": kind, "op": op, "epoch": epoch, "seq": seq, **extra}


@pytest.mark.asyncio
async def test_prepare_accepts_queued_capability_and_conn_id_zero() -> None:
    identity = {"gattc_if": 2, "conn_id": 0}
    channel = FakeChannel(
        [
            frame("event", "CAPABILITY", 0, 0, payload={"state": CAPABILITY_MARKER}),
            frame("response", "CONNECT", 4, 0, request_id=1, status="OK", **identity),
            frame("event", "CONNECTION_STATE", 4, 1, payload={"state": "connected"}, **identity),
        ]
    )
    session = ThinGattSession(channel, poll_timeout=0.01)
    assert await session.prepare(timeout=1) == ConnectionIdentity(2, 0)
    assert session.epoch == 4 and session.last_seq == 1
    assert [name for name, _ in channel.actions] == ["openrbus_gatt_rpc_request"]
    assert channel.actions[0][1]["op"] == "CONNECT"


@pytest.mark.asyncio
async def test_prepare_consumes_retired_disconnect_boundary_before_new_bootstrap() -> None:
    old_identity = {"gattc_if": 1, "conn_id": 7}
    new_identity = {"gattc_if": 2, "conn_id": 0}
    channel = FakeChannel(
        [
            frame(
                "event",
                "CONNECTION_STATE",
                3,
                9,
                payload={"state": "disconnected"},
                **old_identity,
            ),
            frame("event", "CAPABILITY", 0, 0, payload={"state": CAPABILITY_MARKER}),
            frame("response", "CONNECT", 4, 0, request_id=1, status="OK", **new_identity),
            frame(
                "event",
                "CONNECTION_STATE",
                4,
                1,
                payload={"state": "connected"},
                **new_identity,
            ),
        ]
    )
    session = ThinGattSession(channel, poll_timeout=0.01)
    assert await session.prepare(timeout=1) == ConnectionIdentity(2, 0)
    assert session.epoch == 4 and session.last_seq == 1


def test_disconnect_boundary_after_identity_is_still_rejected() -> None:
    session = ThinGattSession(FakeChannel([]))
    session.connected, session.epoch, session.identity, session.last_seq = (
        True,
        2,
        ConnectionIdentity(1, 0),
        1,
    )
    with pytest.raises(ThinGattCorrelationError, match=r"stale|identity"):
        session.ingest(
            frame(
                "event",
                "CONNECTION_STATE",
                1,
                1,
                payload={"state": "disconnected"},
                gattc_if=9,
                conn_id=9,
            )
        )


def test_duplicate_and_wrong_epoch_are_rejected() -> None:
    ident = {"gattc_if": 1, "conn_id": 0}
    session = ThinGattSession(FakeChannel([]))
    session._connect_request = 1
    session.ingest(frame("response", "CONNECT", 2, 0, request_id=1, status="OK", **ident))
    session.ingest(
        frame("event", "CONNECTION_STATE", 2, 1, payload={"state": "connected"}, **ident)
    )
    with pytest.raises(ThinGattCorrelationError):
        session.ingest(
            frame("event", "CONNECTION_STATE", 2, 1, payload={"state": "connected"}, **ident)
        )
    with pytest.raises(ThinGattCorrelationError):
        session.ingest(
            frame("event", "CONNECTION_STATE", 3, 2, payload={"state": "connected"}, **ident)
        )


def test_attach_accepts_seq_two_baseline_then_requires_plus_one() -> None:
    ident = {"gattc_if": 1, "conn_id": 0}
    session = ThinGattSession(FakeChannel([]))
    session._attach_mode = True
    session._connect_request = 1
    session.ingest(frame("response", "CONNECT", 2, 0, request_id=1, status="OK", **ident))
    session.ingest(
        frame("event", "CONNECTION_STATE", 2, 2, payload={"state": "connected"}, **ident)
    )
    with pytest.raises(ThinGattCorrelationError):
        session.ingest(frame("event", "NOTIFY", 2, 4, payload={}, **ident))


def test_response_seq_zero_is_separate_from_event_sequence() -> None:
    ident = {"gattc_if": 1, "conn_id": 0}
    session = ThinGattSession(FakeChannel([]))
    session._connect_request = 1
    session.ingest(frame("response", "CONNECT", 2, 0, request_id=1, status="OK", **ident))
    session.ingest(
        frame("event", "CONNECTION_STATE", 2, 1, payload={"state": "connected"}, **ident)
    )
    session.register_request("READ", request_id=2)
    session.ingest(frame("response", "READ", 2, 0, request_id=2, status="OK", payload={}, **ident))
    session.ingest(frame("event", "NOTIFY", 2, 2, payload={}, **ident))
    with pytest.raises(ThinGattCorrelationError):
        session.ingest(
            frame("response", "READ", 2, 1, request_id=3, status="OK", payload={}, **ident)
        )


def test_non_connect_response_requires_registered_request_and_clears_once() -> None:
    ident = {"gattc_if": 1, "conn_id": 0}
    session = ThinGattSession(FakeChannel([]))
    session.connected, session.epoch, session.identity, session.last_seq = (
        True,
        2,
        ConnectionIdentity(1, 0),
        1,
    )
    session.register_request("READ", request_id=9)
    session.ingest(frame("response", "READ", 2, 0, request_id=9, status="OK", payload={}, **ident))
    with pytest.raises(ThinGattCorrelationError):
        session.ingest(
            frame("response", "READ", 2, 0, request_id=9, status="OK", payload={}, **ident)
        )
    with pytest.raises(ThinGattCorrelationError):
        session.ingest(
            frame("response", "READ", 2, 0, request_id=10, status="OK", payload={}, **ident)
        )


def test_capability_duplicate_and_post_start_are_rejected() -> None:
    session = ThinGattSession(FakeChannel([]))
    capability = frame("event", "CAPABILITY", 0, 0, payload={"state": CAPABILITY_MARKER})
    session.ingest(capability)
    with pytest.raises(ThinGattCapabilityError):
        session.ingest(capability)
    session._connect_request = 1
    with pytest.raises(ThinGattCapabilityError):
        session.ingest(capability)


def test_capability_is_rearmed_after_physical_disconnect() -> None:
    session = ThinGattSession(FakeChannel([]))
    capability = frame("event", "CAPABILITY", 0, 0, payload={"state": CAPABILITY_MARKER})
    session.ingest(capability)
    session.connected = True
    session.epoch = 3
    session.identity = ConnectionIdentity(1, 0)
    session.last_seq = 0
    session.ingest(frame("event", "DISCONNECTED", 3, 1, gattc_if=1, conn_id=0))
    session.ingest(capability)
    assert session.capability


def test_retire_fences_identity_and_outstanding_correlations() -> None:
    identity = ConnectionIdentity(1, 7)
    session = ThinGattSession(FakeChannel([]))
    session.connected = True
    session.epoch = 4
    session.identity = identity
    session.last_seq = 2
    session.register_request("PAIR_ENCRYPT", request_id=9)
    session.retire()
    assert not session.connected
    assert session.identity is None
    assert session.handles is None
    assert not session.capability
    assert session.epoch == 4
    assert 9 in session._retired_operations
    with pytest.raises(ThinGattCorrelationError):
        session.ingest(
            frame(
                "event",
                "DISCONNECTED",
                4,
                3,
                gattc_if=identity.gattc_if,
                conn_id=identity.conn_id,
            )
        )


def test_notification_and_handle_ownership_are_strict() -> None:
    ident = ConnectionIdentity(1, 0)
    session = ThinGattSession(FakeChannel([]))
    session.connected, session.epoch, session.identity, session.last_seq = True, 3, ident, 1
    session.install_handles(GattHandles({"value": 42}, epoch=3))
    session.accept_notification(GattNotification(3, ident, 42, b"x", 2))
    with pytest.raises((TypeError, ValueError)):
        GattNotification(True, ident, 42, b"x", 3)
    with pytest.raises(ThinGattCorrelationError):
        session.install_handles(GattHandles({"value": 42}, epoch=4))


def test_disconnect_invalidates_and_flow_control_is_terminal() -> None:
    session = ThinGattSession(FakeChannel([]))
    session.connected = True
    session.epoch = 3
    session.identity = ConnectionIdentity(1, 0)
    session.last_seq = 0
    session.ingest(frame("event", "DISCONNECTED", 3, 1, gattc_if=1, conn_id=0))
    assert not session.connected and session.identity is None
    with pytest.raises(ThinGattFlowControlError):
        session.ingest(frame("event", "FLOW_CONTROL", 3, 2))
    with pytest.raises(ThinGattFlowControlError):
        session.ingest(frame("event", "CAPABILITY", 0, 0, payload={"state": CAPABILITY_MARKER}))


def test_flow_control_reason_is_allowlisted_and_payload_free() -> None:
    session = ThinGattSession(FakeChannel([]))
    session.connected = True
    session.epoch = 3
    session.identity = ConnectionIdentity(1, 0)
    with pytest.raises(ThinGattFlowControlError) as error:
        session.ingest(
            frame(
                "event",
                "FLOW_CONTROL",
                3,
                1,
                payload={"state": "overflow", "reason": "queue_full", "payload": "secret"},
            )
        )
    assert error.value.reason == "queue_full"
    assert "secret" not in str(error.value)

    invalid = ThinGattFlowControlError("private payload bytes")
    assert invalid.reason is None
    assert "private payload bytes" not in str(invalid)


@pytest.mark.asyncio
async def test_message_transport_roundtrip_reassembles_notifications() -> None:
    ident = {"gattc_if": 1, "conn_id": 0}
    session = ThinGattSession(FakeChannel([]))
    session.connected, session.epoch, session.identity, session.last_seq = (
        True,
        2,
        ConnectionIdentity(1, 0),
        1,
    )
    transport = ThinGattMessageTransport(session, request_handle=7, response_handle=42)
    response = b"raw-read-response-that-spans-segments"
    segments = transport.codec.encode(response)
    cast(FakeChannel, session.channel).frames.extend(
        [
            frame("response", "WRITE_CHAR", 2, 0, request_id=1, status="OK", payload={}, **ident),
            *[
                frame(
                    "event",
                    "NOTIFICATION",
                    2,
                    index + 2,
                    handle=42,
                    payload={"value": segment},
                    **ident,
                )
                for index, segment in enumerate(segments)
            ],
        ]
    )
    session.install_handles(GattHandles({"notify": 42}, epoch=2))
    transport = ThinGattMessageTransport(session, request_handle=7, response_handle=42)
    assert await transport.request(b"request", timeout=1) == response
    assert session._pending_operations == {}


@pytest.mark.asyncio
async def test_message_transport_acknowledges_each_write_segment_before_return() -> None:
    ident = {"gattc_if": 1, "conn_id": 0}
    request = b"x" * 40
    response = b"reply"
    codec = ThinGattMessageTransport(
        ThinGattSession(FakeChannel([])), request_handle=7, response_handle=42
    ).codec
    segments = codec.encode(request)
    response_segments = codec.encode(response)

    class AckChannel(FakeChannel):
        def __init__(self) -> None:
            super().__init__([])
            self.write_count = 0
            self.seq = 1

        async def action(self, name: str, payload: Mapping[str, Any], *, timeout: float) -> None:
            await super().action(name, payload, timeout=timeout)
            if payload.get("op") != "WRITE_CHAR":
                return
            self.write_count += 1
            request_id = payload["request_id"]
            if self.write_count == len(segments):
                for segment in response_segments:
                    self.seq += 1
                    self.frames.append(
                        frame(
                            "event",
                            "NOTIFICATION",
                            2,
                            self.seq,
                            handle=42,
                            payload={"value": segment},
                            **ident,
                        )
                    )
            self.frames.append(
                frame(
                    "response",
                    "WRITE_CHAR",
                    2,
                    0,
                    request_id=request_id,
                    status="OK",
                    payload={},
                    **ident,
                )
            )

    channel = AckChannel()
    session = ThinGattSession(channel)
    session.connected, session.epoch, session.identity, session.last_seq = (
        True,
        2,
        ConnectionIdentity(1, 0),
        1,
    )
    session.install_handles(GattHandles({"notify": 42}, epoch=2))
    transport = ThinGattMessageTransport(session, request_handle=7, response_handle=42)
    assert await transport.request(request, timeout=1) == response
    assert session._pending_operations == {}


def active_transport() -> tuple[ThinGattSession, ThinGattMessageTransport, FakeChannel]:
    channel = FakeChannel([])
    session = ThinGattSession(channel)
    session.connected, session.epoch, session.identity, session.last_seq = (
        True,
        2,
        ConnectionIdentity(1, 0),
        1,
    )
    session.install_handles(GattHandles({"notify": 42}, epoch=2))
    return session, ThinGattMessageTransport(session, request_handle=7, response_handle=42), channel


class PumpTestChannel(FakeChannel):
    def __init__(self) -> None:
        super().__init__([])
        self.poll_calls = 0
        self.active_polls = 0
        self.max_active_polls = 0
        self.next_seq = 1

    async def poll(self, *, timeout: float) -> dict[str, Any] | None:
        self.poll_calls += 1
        self.active_polls += 1
        self.max_active_polls = max(self.max_active_polls, self.active_polls)
        try:
            if self.frames:
                return self.frames.popleft()
            await asyncio.sleep(min(timeout, 0.002))
            return None
        finally:
            self.active_polls -= 1

    def notification(self, seq: int, value: bytes = b"x") -> dict[str, Any]:
        return frame(
            "event",
            "NOTIFICATION",
            2,
            seq,
            request_id=0,
            handle=42,
            payload={"value": value},
            gattc_if=1,
            conn_id=0,
        )


@pytest.mark.asyncio
async def test_event_pump_drains_idle_and_burst_frames_with_single_owner() -> None:
    channel = PumpTestChannel()
    session = ThinGattSession(channel, poll_timeout=0.01)
    session.connected, session.epoch, session.identity, session.last_seq = (
        True,
        2,
        ConnectionIdentity(1, 0),
        1,
    )
    session.install_handles(GattHandles({"response": 42}, epoch=2))
    session.start_event_pump()

    channel.frames.extend(channel.notification(seq) for seq in range(2, 42))
    for _ in range(100):
        if not channel.frames and session.last_seq == 41:
            break
        await asyncio.sleep(0.002)
    assert not channel.frames
    assert session.last_seq == 41
    assert session._event_queue.empty()  # unsolicited idle notifications are retired

    session.begin_event_operation()
    request_id = session.register_request("WRITE_CHAR")
    channel.frames.extend(channel.notification(seq) for seq in range(42, 82))
    channel.frames.append(
        frame(
            "response",
            "WRITE_CHAR",
            2,
            0,
            request_id=request_id,
            status="OK",
            gattc_if=1,
            conn_id=0,
        )
    )
    for _ in range(100):
        if not channel.frames and session._event_queue.qsize() == 41:
            break
        await asyncio.sleep(0.002)
    assert session._event_queue.qsize() == 41
    burst = [await session.receive_frame(timeout=0.1) for _ in range(41)]
    assert sum(item is not None and item.get("op") == "NOTIFICATION" for item in burst) == 40
    assert burst[-1] is not None and burst[-1].get("op") == "WRITE_CHAR"
    session.end_event_operation()

    await session.stop_event_pump()
    stopped_at = channel.poll_calls
    await asyncio.sleep(0.03)
    assert channel.poll_calls == stopped_at
    assert channel.max_active_polls == 1


@pytest.mark.asyncio
async def test_event_pump_keeps_concurrent_message_requests_serialized() -> None:
    class ReplyChannel(PumpTestChannel):
        async def action(self, name: str, payload: Mapping[str, Any], *, timeout: float) -> None:
            await super().action(name, payload, timeout=timeout)
            if payload.get("op") != "WRITE_CHAR":
                return
            request_id = payload["request_id"]
            response = transport.codec.encode(b"reply")
            for segment in response:
                self.next_seq += 1
                self.frames.append(self.notification(self.next_seq, segment))
            self.frames.append(
                frame(
                    "response",
                    "WRITE_CHAR",
                    2,
                    0,
                    request_id=request_id,
                    status="OK",
                    gattc_if=1,
                    conn_id=0,
                )
            )

    channel = ReplyChannel()
    session = ThinGattSession(channel, poll_timeout=0.01)
    session.connected, session.epoch, session.identity, session.last_seq = (
        True,
        2,
        ConnectionIdentity(1, 0),
        1,
    )
    session.install_handles(GattHandles({"request": 7, "response": 42}, epoch=2))
    transport = ThinGattMessageTransport(session, request_handle=7, response_handle=42)

    results = await asyncio.gather(
        transport.request(b"first", timeout=1),
        transport.request(b"second", timeout=1),
    )
    assert results == [b"reply", b"reply"]
    writes = [payload for _, payload in channel.actions if payload.get("op") == "WRITE_CHAR"]
    assert len(writes) == 2
    assert [payload["request_id"] for payload in writes] == sorted(
        payload["request_id"] for payload in writes
    )
    assert channel.max_active_polls == 1
    await session.stop_event_pump()


@pytest.mark.asyncio
async def test_adapter_abort_error_response_cleans_pending_and_sends_cancel() -> None:
    session, transport, channel = active_transport()
    channel.frames.append(
        frame(
            "response",
            "WRITE_CHAR",
            2,
            0,
            request_id=1,
            status="ERROR",
            payload={},
            **{"gattc_if": 1, "conn_id": 0},
        )
    )
    with pytest.raises(TransportError):
        await transport.request(b"x", timeout=1)
    assert session._pending_operations == {}
    assert any(payload["op"] == "CANCEL" for _, payload in channel.actions)


@pytest.mark.asyncio
async def test_adapter_disconnect_mid_request_fails_immediately_and_cleans() -> None:
    session, transport, channel = active_transport()
    channel.frames.append(frame("event", "DISCONNECTED", 2, 2, gattc_if=1, conn_id=0))
    with pytest.raises(TransportError, match="disconnected"):
        await transport.request(b"x", timeout=1)
    assert session._pending_operations == {} and not session.connected


@pytest.mark.asyncio
async def test_adapter_timeout_emits_cancel_and_cleans_reassembly() -> None:
    session, transport, channel = active_transport()
    with pytest.raises(RequestTimeoutError):
        await transport.request(b"x", timeout=0.01)
    assert session._pending_operations == {}
    assert any(payload["op"] == "CANCEL" for _, payload in channel.actions)


@pytest.mark.asyncio
async def test_message_segment_dispatch_uses_remaining_end_to_end_deadline() -> None:
    class SlowActionChannel(FakeChannel):
        def __init__(self) -> None:
            super().__init__([])
            self.action_timeouts: list[float] = []

        async def action(self, name: str, payload: Mapping[str, Any], *, timeout: float) -> None:
            await super().action(name, payload, timeout=timeout)
            if payload.get("op") == "WRITE_CHAR":
                self.action_timeouts.append(timeout)
                # Model a service-dispatch call which is slow but obeys only
                # cancellation; the overall message deadline is shorter.
                await asyncio.sleep(0.2)

    channel = SlowActionChannel()
    session = ThinGattSession(channel)
    session.connected = True
    session.epoch = 2
    session.identity = ConnectionIdentity(1, 0)
    session.last_seq = 1
    session.install_handles(GattHandles({"notify": 42}, epoch=2))
    transport = ThinGattMessageTransport(session, request_handle=7, response_handle=42)
    timeout = 0.03
    started = asyncio.get_running_loop().time()

    with pytest.raises(RequestTimeoutError):
        await transport.request(b"x" * 40, timeout=timeout)

    elapsed = asyncio.get_running_loop().time() - started
    writes = [payload for _, payload in channel.actions if payload.get("op") == "WRITE_CHAR"]
    assert elapsed < 0.15
    assert len(writes) == 1
    assert len(channel.action_timeouts) == 1
    assert 0 < channel.action_timeouts[0] <= timeout
    assert session._pending_operations == {}


@pytest.mark.asyncio
async def test_adapter_cross_epoch_notification_rejects_and_cleans() -> None:
    session, transport, channel = active_transport()
    channel.frames.extend(
        [
            frame(
                "event",
                "NOTIFICATION",
                3,
                2,
                handle=42,
                payload={"value": b"bad"},
                gattc_if=1,
                conn_id=0,
            ),
        ]
    )
    with pytest.raises(ThinGattCorrelationError):
        await transport.request(b"x", timeout=1)
    assert session._pending_operations == {}


@pytest.mark.asyncio
async def test_adapter_wrong_response_handle_rejects_and_cleans() -> None:
    session, transport, channel = active_transport()
    channel.frames.extend(
        [
            frame(
                "event",
                "NOTIFICATION",
                2,
                2,
                handle=99,
                payload={"value": b"bad"},
                gattc_if=1,
                conn_id=0,
            ),
        ]
    )
    with pytest.raises(ThinGattCorrelationError):
        await transport.request(b"x", timeout=1)
    assert session._pending_operations == {}


@pytest.mark.asyncio
async def test_prepare_timeout_clears_pending_connect() -> None:
    session = ThinGattSession(FakeChannel([]), poll_timeout=0.01)
    with pytest.raises(RequestTimeoutError):
        await session.prepare(timeout=0.02)
    assert session._connect_request is None


@pytest.mark.asyncio
async def test_thin_gateway_auth_runs_identity_then_auth_before_transport() -> None:
    identity = {"gattc_if": 1, "conn_id": 0}

    class GatewayChannel(FakeChannel):
        def __init__(self) -> None:
            super().__init__([])
            self.seq = 2

        async def action(self, name: str, payload: Mapping[str, Any], *, timeout: float) -> None:
            await super().action(name, payload, timeout=timeout)
            if payload.get("op") != "WRITE_CHAR":
                return
            handle = payload["handle"]
            response = b"identity"
            if handle == 14:
                response = b"\x01"
            self.frames.append(
                frame(
                    "event",
                    "NOTIFICATION",
                    2,
                    self.seq,
                    request_id=payload["request_id"],
                    handle=payload["notification_handle"],
                    payload={"value": response},
                    **identity,
                )
            )
            self.seq += 1

    channel = GatewayChannel()
    session = ThinGattSession(channel)
    session.connected = True
    session.epoch = 2
    session.identity = ConnectionIdentity(1, 0)
    session.last_seq = 1
    session.install_handles(
        GattHandles(
            {"identity": 11, "identity_notify": 12, "auth": 14, "auth_notify": 15},
            epoch=2,
        )
    )
    link = ThinGattLink(
        session,
        roles={
            "identity": ("s", "identity"),
            "identity_notify": ("s", "identity_notify"),
            "auth": ("s", "auth"),
            "auth_notify": ("s", "auth_notify"),
        },
    )
    link.encrypted_epoch = link.discovered_epoch = link._ready_epoch = 2

    await link.authenticate_gateway(timeout=1)

    writes = [payload for _name, payload in channel.actions if payload.get("op") == "WRITE_CHAR"]
    assert [payload["handle"] for payload in writes] == [11, 14]
    assert writes[0]["value"] == ""
    assert writes[1]["value"]
    assert link.gateway_authenticated_epoch == 2
    assert writes[1]["value"] == base64.b64encode(compute_gateway_auth_payload(b"identity")).decode(
        "ascii"
    )
