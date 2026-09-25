"""Small, transport-independent ATT/GATT RPC contract and state simulator.

This module is deliberately dependency free and is not imported by the runtime
transport.  It is an executable design spike for the ESPHome Native API
boundary described in ``docs/thin-gatt-rpc.md``.
"""

from __future__ import annotations

import base64
import json
from collections import deque
from dataclasses import dataclass, field
from typing import Any, Literal

MAX_PAYLOAD_BYTES = 512
MAX_FRAME_BYTES = 2048
MAX_EVENT_QUEUE = 16
MAX_REQUEST_ID = 0xFFFFFFFF

REQUEST_OPS = frozenset(
    {
        "CONNECT",
        "DISCONNECT",
        "PAIR_ENCRYPT",
        "DISCOVER",
        "HANDLE_LOOKUP",
        "WRITE_CHAR",
        "WRITE_DESCRIPTOR",
        "SUBSCRIBE",
        "CANCEL",
    }
)
EVENT_OPS = frozenset(
    {
        "CONNECTION_STATE",
        "ENCRYPTION_STATE",
        "NOTIFICATION",
        "BOND_STATE",
        "FLOW_CONTROL",
    }
)


class RpcProtocolError(ValueError):
    """Malformed or out-of-contract RPC frame."""


def b64(data: bytes) -> str:
    """Encode binary payloads in the JSON envelope without lossy text casts."""

    if len(data) > MAX_PAYLOAD_BYTES:
        raise RpcProtocolError(f"payload exceeds {MAX_PAYLOAD_BYTES} bytes")
    return base64.b64encode(data).decode("ascii")


def unb64(value: object) -> bytes:
    if not isinstance(value, str):
        raise RpcProtocolError("binary value must be base64 text")
    try:
        data = base64.b64decode(value, validate=True)
    except (ValueError, base64.binascii.Error) as exc:
        raise RpcProtocolError("invalid base64 payload") from exc
    if len(data) > MAX_PAYLOAD_BYTES:
        raise RpcProtocolError(f"payload exceeds {MAX_PAYLOAD_BYTES} bytes")
    return data


@dataclass(frozen=True, slots=True)
class RpcFrame:
    """One complete Native API action/event envelope.

    ``epoch`` is assigned by the ESP host on each physical connection.  A
    request using a previous epoch is rejected, which fences late BLE callbacks
    after a disconnect/reconnect.  ``seq`` is monotonic per epoch for events;
    consumers detect a dropped/coalesced Native API update by a gap.
    """

    kind: Literal["request", "response", "event"]
    op: str
    epoch: int
    request_id: int = 0
    payload: dict[str, Any] = field(default_factory=dict)
    status: str | None = None
    error: str | None = None
    seq: int = 0

    def __post_init__(self) -> None:
        if self.kind not in {"request", "response", "event"}:
            raise RpcProtocolError("invalid frame kind")
        if not isinstance(self.op, str) or not self.op:
            raise RpcProtocolError("operation is required")
        if not isinstance(self.epoch, int) or not 0 <= self.epoch <= MAX_REQUEST_ID:
            raise RpcProtocolError("invalid connection epoch")
        if not isinstance(self.request_id, int) or not 0 <= self.request_id <= MAX_REQUEST_ID:
            raise RpcProtocolError("invalid request id")
        if self.kind == "request":
            if self.op not in REQUEST_OPS:
                raise RpcProtocolError(f"unsupported request operation: {self.op}")
            if self.request_id == 0:
                raise RpcProtocolError("request id must be non-zero")
        elif self.kind == "event" and self.op not in EVENT_OPS:
            raise RpcProtocolError(f"unsupported event operation: {self.op}")
        if self.kind == "event" and self.seq < 0:
            raise RpcProtocolError("invalid event sequence")
        if not isinstance(self.payload, dict):
            raise RpcProtocolError("payload must be an object")

    def to_json(self) -> str:
        body: dict[str, Any] = {
            "v": 1,
            "kind": self.kind,
            "op": self.op,
            "epoch": self.epoch,
            "request_id": self.request_id,
            "payload": self.payload,
        }
        if self.kind == "event":
            body["seq"] = self.seq
        if self.status is not None:
            body["status"] = self.status
        if self.error is not None:
            body["error"] = self.error
        wire = json.dumps(body, separators=(",", ":"), sort_keys=True)
        if len(wire.encode("utf-8")) > MAX_FRAME_BYTES:
            raise RpcProtocolError(f"frame exceeds {MAX_FRAME_BYTES} bytes")
        return wire

    @classmethod
    def from_json(cls, text: str) -> RpcFrame:
        if len(text.encode("utf-8")) > MAX_FRAME_BYTES:
            raise RpcProtocolError(f"frame exceeds {MAX_FRAME_BYTES} bytes")
        try:
            body = json.loads(text)
        except json.JSONDecodeError as exc:
            raise RpcProtocolError("invalid JSON frame") from exc
        if not isinstance(body, dict) or body.get("v") != 1:
            raise RpcProtocolError("unsupported RPC envelope version")
        try:
            return cls(
                kind=body["kind"],
                op=body["op"],
                epoch=body["epoch"],
                request_id=body.get("request_id", 0),
                payload=body.get("payload", {}),
                status=body.get("status"),
                error=body.get("error"),
                seq=body.get("seq", 0),
            )
        except (KeyError, TypeError) as exc:
            raise RpcProtocolError("missing or invalid envelope field") from exc


@dataclass(slots=True)
class _Pending:
    request: RpcFrame
    expect_notification: bool = False
    notification_handle: int = 0
    write_complete: bool = False


class RpcSimulator:
    """Deterministic model of the thin ESP host boundary.

    The model intentionally has no CAN-IP/OpenRBus knowledge.  It only models
    ATT operation ownership, callback ordering, epoch fencing and bounded
    event delivery.  ``drain`` is the Native API event stream consumed by the
    Python side.
    """

    def __init__(self) -> None:
        self.epoch = 0
        self.connected = False
        self.encrypted = False
        self.bonded = False
        self._next_event_seq = 0
        self._pending: dict[int, _Pending] = {}
        self._events: deque[RpcFrame] = deque(maxlen=MAX_EVENT_QUEUE)
        self.dropped_events = 0

    def submit(self, request: RpcFrame) -> None:
        if request.kind != "request":
            raise RpcProtocolError("simulator accepts request frames only")
        if request.op != "CONNECT" and request.epoch != self.epoch:
            raise RpcProtocolError("stale connection epoch")
        if request.op == "CANCEL":
            target = request.payload.get("request_id")
            if not isinstance(target, int) or target <= 0:
                raise RpcProtocolError("CANCEL requires payload.request_id")
            self.cancel(target)
            return
        if request.op == "CONNECT":
            if request.epoch not in {0, self.epoch}:
                raise RpcProtocolError("CONNECT epoch mismatch")
            self.connected = True
            self.encrypted = False
            self.epoch += 1
            self._next_event_seq = 0
            self._pending.clear()
            self._emit_response(request.request_id, "OK", op="CONNECT")
            self._emit("CONNECTION_STATE", {"state": "connected"}, request.request_id)
            return
        if request.op == "DISCONNECT":
            self.disconnect("requested", request.request_id)
            return
        if not self.connected:
            raise RpcProtocolError("not connected")
        if request.request_id in self._pending:
            raise RpcProtocolError("duplicate request id")
        if request.op in {"WRITE_CHAR", "WRITE_DESCRIPTOR", "SUBSCRIBE"}:
            value = request.payload.get("value", "")
            if value:
                unb64(value)
            self._pending[request.request_id] = _Pending(
                request,
                expect_notification=bool(request.payload.get("expect_notification")),
                notification_handle=int(request.payload.get("notification_handle", 0)),
            )

    def write_complete(self, request_id: int, *, ok: bool = True) -> None:
        """Feed an asynchronous GATT write callback.

        A successful callback never completes a transaction that is waiting for
        an ATT notification.  This is the ordering that permits a notification
        to arrive before ``WRITE_CHAR_EVT`` without double completion.
        """

        pending = self._pending.get(request_id)
        if pending is None:
            return  # already resolved by notification or cancellation
        if not ok:
            self._pending.pop(request_id)
            self._emit_response(
                request_id,
                "WRITE_FAILED",
                op=pending.request.op,
                error="gatt_write_failed",
            )
            return
        pending.write_complete = True
        if not pending.expect_notification:
            self._pending.pop(request_id)
            self._emit_response(request_id, "OK", op=pending.request.op)

    def notification(self, value: bytes, *, handle: int = 0) -> None:
        if not self.connected:
            return
        if len(value) > MAX_PAYLOAD_BYTES:
            self._emit("FLOW_CONTROL", {"state": "rejected", "reason": "payload_too_large"})
            return
        candidate = next(
            (
                item
                for item in self._pending.values()
                if item.expect_notification and item.notification_handle in {0, handle}
            ),
            None,
        )
        request_id = candidate.request.request_id if candidate is not None else 0
        if candidate is not None:
            self._pending.pop(request_id, None)
        self._emit(
            "NOTIFICATION",
            {"handle": handle, "value": b64(value)},
            request_id,
        )

    def cancel(self, request_id: int) -> None:
        if self._pending.pop(request_id, None) is not None:
            self._emit_response(request_id, "CANCELLED", error="cancelled")

    def disconnect(self, reason: str = "link_lost", request_id: int = 0) -> None:
        self.connected = False
        self.encrypted = False
        self._pending.clear()
        self._emit("CONNECTION_STATE", {"state": "disconnected", "reason": reason}, request_id)

    def drain(self) -> list[RpcFrame]:
        result = list(self._events)
        self._events.clear()
        return result

    def _emit_response(
        self,
        request_id: int,
        status: str,
        *,
        op: str = "WRITE_CHAR",
        error: str | None = None,
    ) -> None:
        self._enqueue(
            RpcFrame(
                "response",
                op,
                self.epoch,
                request_id,
                status=status,
                error=error,
            )
        )

    def _emit(self, op: str, payload: dict[str, Any], request_id: int = 0) -> None:
        self._next_event_seq += 1
        self._enqueue(
            RpcFrame("event", op, self.epoch, request_id, payload, seq=self._next_event_seq)
        )

    def _enqueue(self, frame: RpcFrame) -> None:
        if len(self._events) == self._events.maxlen:
            self.dropped_events += 1
            # A bounded stream must fail closed rather than silently overwrite
            # a response.  Drop the buffered batch and leave one explicit
            # marker for the Python side to force reconnect/resubscribe.
            self._events.clear()
            self._next_event_seq += 1
            self._events.append(
                RpcFrame(
                    "event",
                    "FLOW_CONTROL",
                    self.epoch,
                    payload={"state": "overflow", "dropped": self.dropped_events},
                    seq=self._next_event_seq,
                )
            )
            return
        self._events.append(frame)
