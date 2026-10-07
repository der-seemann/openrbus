"""Transport-neutral state and correlation for the Thin-GATT RPC boundary.

This module deliberately knows nothing about BLE libraries, ESPHome, EHC
profiles, or the OpenRBus object protocol.  An adapter supplies the small
``ThinGattRpcChannel`` contract and receives/returns JSON-like mappings.
"""

from __future__ import annotations

import asyncio
import base64
import contextlib
from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any, Protocol, runtime_checkable

from openrbus.errors import ProtocolError, RequestTimeoutError, TransportError
from openrbus.protocol.ble_segments import BleSegmentCodec, BleSegmentReassembler
from openrbus.transport.gateway_auth import authenticate_gateway

CAPABILITY_MARKER = "openrbus_thin_gatt.v1"


class ThinGattError(TransportError):
    """Base for bounded Thin-GATT session failures."""


class ThinGattCorrelationError(ProtocolError):
    """A frame cannot be associated with the current session."""


class ThinGattCapabilityError(ThinGattError):
    """The channel did not advertise the required Thin-GATT capability."""


class ThinGattFlowControlError(ThinGattError):
    """The peer reported a terminal event-stream overflow or desync."""

    _ALLOWED_REASONS = frozenset(
        {"queue_full", "frame_too_large", "handle_registry_full", "payload_too_large"}
    )

    def __init__(self, reason: object = None) -> None:
        self.reason = (
            reason if isinstance(reason, str) and reason in self._ALLOWED_REASONS else None
        )
        message = "Thin-GATT event stream is desynchronized"
        if self.reason is not None:
            message = f"{message}: {self.reason}"
        super().__init__(message)


class ThinGattSessionStateError(ThinGattError):
    """An operation is invalid for the current session state."""


@runtime_checkable
class ThinGattRpcChannel(Protocol):
    """Minimal injected channel used by :class:`ThinGattSession`."""

    async def action(self, name: str, payload: Mapping[str, Any], *, timeout: float) -> None:
        """Send one RPC action; responses arrive through :meth:`poll`."""

    async def poll(self, *, timeout: float) -> Mapping[str, Any] | None:
        """Return the next frame, or ``None`` when the poll times out."""

    async def diagnostics(self) -> Mapping[str, Any]:
        """Return an adapter-provided, non-sensitive diagnostic snapshot."""


@dataclass(frozen=True, slots=True)
class ThinGattRpcServices:
    """Configurable action names; UUIDs and vendor defaults do not belong here."""

    request: str = "openrbus_gatt_rpc_request"
    poll: str = "openrbus_gatt_rpc_poll"
    diagnostics: str = "openrbus_gatt_rpc_diagnostics"


@dataclass(frozen=True, slots=True)
class ThinGattProfile:
    """Caller-supplied canonical UUID roles and optional characteristic map.

    The generic transport deliberately has no UUID defaults.  ``roles`` maps
    an application role to ``(service, characteristic[, descriptor])`` and is
    consumed by :class:`ThinGattLink`; values are copied at construction so a
    caller cannot mutate a live profile behind the session's back.
    """

    service: str
    identity: str
    auth: str
    notify: str | None = None
    roles: Mapping[str, tuple[str, ...]] = field(default_factory=dict)

    def __post_init__(self) -> None:
        for value in (self.service, self.identity, self.auth, self.notify):
            if value is not None and not value:
                raise ValueError("profile UUID roles must be non-empty")
        copied: dict[str, tuple[str, ...]] = {}
        for role, uuids in self.roles.items():
            if not isinstance(role, str) or not role:
                raise ValueError("profile role names must be non-empty")
            if not isinstance(uuids, tuple) or len(uuids) not in {2, 3}:
                raise ValueError("profile roles must contain service, characteristic[, descriptor]")
            if any(not isinstance(value, str) or not value for value in uuids):
                raise ValueError("profile UUID roles must be non-empty")
            copied[role] = uuids
        object.__setattr__(self, "roles", copied)


@dataclass(frozen=True, slots=True)
class ConnectionIdentity:
    """Physical-link identity supplied by the adapter."""

    gattc_if: int
    conn_id: int

    def __post_init__(self) -> None:
        if type(self.gattc_if) is not int or self.gattc_if < 0:
            raise ValueError("gattc_if must be a non-negative integer")
        if type(self.conn_id) is not int or self.conn_id < 0:
            raise ValueError("conn_id must be a non-negative integer")


@dataclass(frozen=True, slots=True)
class GattHandles:
    """Resolved handles owned by one connection epoch."""

    values: Mapping[str, int] = field(default_factory=dict)
    epoch: int = 0

    def __post_init__(self) -> None:
        if type(self.epoch) is not int or self.epoch < 0:
            raise ValueError("epoch must be a non-negative integer")
        copied = dict(self.values)
        if any(type(v) is not int or v < 0 for v in copied.values()):
            raise ValueError("handles must be non-negative integers")
        object.__setattr__(self, "values", copied)


@dataclass(frozen=True, slots=True)
class GattNotification:
    """A notification fenced to a physical identity and epoch."""

    epoch: int
    identity: ConnectionIdentity
    handle: int
    payload: bytes
    seq: int

    def __post_init__(self) -> None:
        if type(self.epoch) is not int or self.epoch <= 0:
            raise ValueError("notification epoch must be a positive integer")
        if type(self.seq) is not int or self.seq < 0:
            raise ValueError("notification sequence must be a non-negative integer")
        if type(self.handle) is not int or self.handle < 0:
            raise ValueError("notification handle must be a non-negative integer")
        if not isinstance(self.payload, bytes):
            raise TypeError("notification payload must be bytes")


class ThinGattSession:
    """Initial correlation/state layer for an injected Thin-RPC channel."""

    def __init__(
        self,
        channel: ThinGattRpcChannel,
        *,
        services: ThinGattRpcServices | None = None,
        profile: ThinGattProfile | None = None,
        poll_timeout: float = 1.0,
    ) -> None:
        if poll_timeout <= 0:
            raise ValueError("poll_timeout must be positive")
        self.channel = channel
        self.services = services or ThinGattRpcServices()
        self.profile = profile
        self.poll_timeout = poll_timeout
        self.capability = False
        self.connected = False
        self.epoch = 0
        self.identity: ConnectionIdentity | None = None
        self.handles: GattHandles | None = None
        self.last_seq = -1
        self.stream_error: ThinGattFlowControlError | None = None
        self._connect_request: int | None = None
        self._completed_connect_request: int | None = None
        self._pending_operations: dict[int, str] = {}
        self._retired_operations: set[int] = set()
        self._next_request = 1
        self._capability_seen = False
        self._lock = asyncio.Lock()
        self._attach_mode = False
        self._event_queue: asyncio.Queue[Mapping[str, Any] | BaseException] = asyncio.Queue(
            maxsize=64
        )
        self._event_pump_task: asyncio.Task[None] | None = None
        self._event_operation_active = False
        self._event_pump_error: BaseException | None = None

    async def prepare(self, *, timeout: float = 10.0, attach: bool = False) -> ConnectionIdentity:
        """Bootstrap capability and connect, returning the fenced identity."""
        if timeout <= 0:
            raise ValueError("timeout must be positive")
        async with self._lock:
            if self.stream_error:
                raise self.stream_error
            if self.connected and self.identity is not None:
                return self.identity
            self._attach_mode = attach
            request_id = self._next_request
            self._next_request += 1
            self._connect_request = request_id
            await self.channel.action(
                self.services.request,
                {"op": "CONNECT", "request_id": request_id, "attach": attach},
                timeout=timeout,
            )
            deadline = asyncio.get_running_loop().time() + timeout
            while not self.connected:
                remaining = deadline - asyncio.get_running_loop().time()
                if remaining <= 0:
                    await self._cancel_bootstrap(request_id, timeout)
                    raise RequestTimeoutError("Thin-GATT CONNECT timed out")
                frame = await self.channel.poll(timeout=min(self.poll_timeout, remaining))
                if frame is not None:
                    self.ingest(frame)
            return self.identity  # type: ignore[return-value]

    def ingest(self, frame: Mapping[str, Any]) -> Any:
        """Validate and consume one frame, returning response/event metadata."""
        if self.stream_error:
            raise self.stream_error
        if not isinstance(frame, Mapping):
            raise ThinGattCorrelationError("malformed Thin-GATT frame")
        try:
            kind, op = frame["kind"], frame["op"]
            epoch, seq = frame["epoch"], frame["seq"]
        except (KeyError, TypeError) as exc:
            raise ThinGattCorrelationError("malformed Thin-GATT frame") from exc
        if type(epoch) is not int or type(seq) is not int or epoch < 0 or seq < 0:
            raise ThinGattCorrelationError("invalid frame epoch or sequence")
        if op == "CAPABILITY":
            if (
                self._capability_seen
                or self.connected
                or kind != "event"
                or epoch != 0
                or seq != 0
                or frame.get("payload") != {"state": CAPABILITY_MARKER}
            ):
                raise ThinGattCapabilityError("invalid Thin-GATT capability frame")
            self.capability = self._capability_seen = True
            return frame
        if op == "FLOW_CONTROL":
            payload = frame.get("payload")
            reason = payload.get("reason") if isinstance(payload, Mapping) else None
            self.stream_error = ThinGattFlowControlError(reason)
            raise self.stream_error
        frame_request_id = frame.get("request_id")
        if type(frame_request_id) is int and frame_request_id in self._retired_operations:
            # A cancellation/disconnect callback may be queued after the
            # operation has already been finalized.  Retired ids are the
            # only safe exception to the unsolicited-frame fail-closed rule.
            return frame
        if op == "NOTIFICATION":
            self._validate_notification_frame(frame)
        if self.epoch and epoch < self.epoch:
            raise ThinGattCorrelationError("stale Thin-GATT epoch")
        if (
            self.epoch
            and epoch > self.epoch
            and not (kind == "response" and op == "CONNECT" and self._connect_request is not None)
        ):
            raise ThinGattCorrelationError("future Thin-GATT epoch")
        if kind not in {"response", "event"}:
            raise ThinGattCorrelationError("invalid Thin-GATT frame kind")
        if kind == "response":
            if seq != 0:
                raise ThinGattCorrelationError("Thin-GATT responses must use seq=0")
            if type(frame_request_id) is int and frame_request_id in self._retired_operations:
                return frame
            if op != "CONNECT":
                request_id = frame.get("request_id")
                if type(request_id) is not int or self._pending_operations.get(request_id) != op:
                    raise ThinGattCorrelationError("unsolicited or mismatched Thin-GATT response")
                if (
                    self.identity is None
                    or epoch != self.epoch
                    or self._frame_identity(frame) != self.identity
                ):
                    raise ThinGattCorrelationError("response identity or epoch is not current")
                del self._pending_operations[request_id]
        bootstrap_state = kind == "event" and op == "CONNECTION_STATE" and not self.connected
        valid_baseline = bootstrap_state and (
            (self._attach_mode and seq >= 1) or (not self._attach_mode and seq == 1)
        )
        if kind == "event" and self.epoch and seq != self.last_seq + 1 and not valid_baseline:
            raise ThinGattCorrelationError("Thin-GATT sequence gap or duplicate")
        if kind == "response" and op == "CONNECT":
            if (
                self._connect_request is None
                or frame.get("request_id") != self._connect_request
                or type(frame.get("request_id")) is not int
                or type(frame.get("gattc_if")) is not int
                or type(frame.get("conn_id")) is not int
                or frame["gattc_if"] <= 0
            ):
                raise ThinGattCorrelationError("unmatched CONNECT response")
            if frame.get("status") != "OK":
                raise ThinGattSessionStateError("CONNECT was not accepted")
            if (not self._attach_mode and epoch <= self.epoch) or epoch <= 0:
                raise ThinGattCorrelationError("CONNECT epoch did not advance")
            try:
                identity = ConnectionIdentity(frame["gattc_if"], frame["conn_id"])
            except (KeyError, TypeError, ValueError) as exc:
                raise ThinGattCorrelationError("invalid CONNECT identity") from exc
            self.epoch = epoch
            self.identity = identity
            self.last_seq = -1
            self._completed_connect_request = self._connect_request
            self._connect_request = None
        elif kind == "event" and op == "CONNECTION_STATE":
            payload = frame.get("payload")
            if not isinstance(payload, Mapping) or set(payload) != {"state"}:
                raise ThinGattCorrelationError("invalid connection-state payload")
            if payload["state"] in {"disconnected", "failed"}:
                # The ESP bridge retains exactly one physical disconnect
                # boundary when it opens a new bootstrap stream.  It is
                # emitted after CONNECT, before the fresh capability/connected
                # markers, and necessarily carries the retired epoch and
                # identity.  During a still-unbound CONNECT bootstrap this is
                # a stale fence, not evidence about the new session.  Consume
                # only this narrowly-scoped boundary; once an identity or a
                # connected session exists, preserve the normal fail-closed
                # identity checks below.
                if (
                    not self.connected
                    and self.identity is None
                    and self._connect_request is not None
                ):
                    return frame
                self._require_identity(frame)
                self._invalidate()
            else:
                identity = self._frame_identity(frame)
                if self.identity is None:
                    self.identity = identity
                if identity != self.identity:
                    raise ThinGattCorrelationError("connection identity changed")
                self.epoch = epoch
                self.connected = True
                if self._completed_connect_request is not None:
                    # CONNECT has completed once its connected lifecycle
                    # callback establishes the stream baseline. Retire its
                    # token so a duplicate callback left in the queue is
                    # harmless to the following operation.
                    self._retired_operations.add(self._completed_connect_request)
                    self._completed_connect_request = None
        elif kind == "event" and op == "DISCONNECTED":
            self._require_identity(frame)
            self._invalidate()
        if kind == "event" and epoch == self.epoch:
            self.last_seq = seq
        return frame

    async def disconnect(self, *, timeout: float = 5.0) -> None:
        await self.stop_event_pump()
        async with self._lock:
            request_id = self._next_request
            self._next_request += 1
            identity = self.identity
            if identity is None or not self.connected:
                self._invalidate()
                return
            await self.channel.action(
                self.services.request,
                {
                    "op": "DISCONNECT",
                    "request_id": request_id,
                    "epoch": self.epoch,
                    "gattc_if": identity.gattc_if,
                    "conn_id": identity.conn_id,
                },
                timeout=timeout,
            )
            self._retired_operations.add(request_id)
            self._invalidate()

    def retire(self) -> None:
        """Synchronously fence this generation before a fresh CONNECT.

        Adapters use this when a physical link was lost outside the Core
        request loop.  It preserves the last wire epoch as a stale-frame
        fence, while invalidating identity, capabilities, handles, pairing
        proof, and all outstanding correlations.
        """
        self._retired_operations.update(self._pending_operations)
        if self._connect_request is not None:
            self._retired_operations.add(self._connect_request)
        task = self._event_pump_task
        self._event_pump_task = None
        if task is not None:
            task.cancel()
        self._event_operation_active = False
        while not self._event_queue.empty():
            self._event_queue.get_nowait()
        self._invalidate()

    async def _cancel_bootstrap(self, request_id: int, timeout: float) -> None:
        self._connect_request = None
        self._pending_operations.clear()
        with contextlib.suppress(Exception):
            await self.channel.action(
                self.services.request,
                {"op": "CANCEL", "request_id": request_id, "epoch": self.epoch},
                timeout=min(timeout, 1.0),
            )

    def register_request(self, op: str, *, request_id: int | None = None) -> int:
        """Register one future non-CONNECT operation for response correlation."""
        if not self.connected or self.identity is None:
            raise ThinGattSessionStateError("cannot register a request while disconnected")
        if not op or op == "CONNECT":
            raise ValueError("a non-CONNECT operation is required")
        request_id = self._next_request if request_id is None else request_id
        if (
            type(request_id) is not int
            or request_id <= 0
            or request_id in self._pending_operations
            or request_id in self._retired_operations
        ):
            raise ValueError("request_id must be a unique positive integer")
        self._next_request = max(self._next_request, request_id + 1)
        self._pending_operations[request_id] = op
        return request_id

    def install_handles(self, handles: GattHandles) -> None:
        """Install handles only for the currently connected epoch."""
        if not self.connected or handles.epoch != self.epoch:
            raise ThinGattCorrelationError("handles belong to a different session epoch")
        self.handles = handles

    def start_event_pump(self) -> None:
        """Continuously drain the peer stream once link setup is complete."""
        if self._event_pump_task is not None and not self._event_pump_task.done():
            return
        self._event_pump_error = None
        self._event_pump_task = asyncio.create_task(self._run_event_pump())

    async def stop_event_pump(self) -> None:
        """Stop the sole stream consumer before retiring this session."""
        task = self._event_pump_task
        self._event_pump_task = None
        self._event_operation_active = False
        if task is None:
            return
        task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await task
        while not self._event_queue.empty():
            self._event_queue.get_nowait()

    def begin_event_operation(self) -> None:
        """Open a request boundary and discard notifications from the prior idle gap."""
        if self._event_pump_error is not None:
            raise self._event_pump_error
        self._discard_queued_notifications()
        self._event_operation_active = True

    def end_event_operation(self) -> None:
        """Close a request boundary so later unsolicited notifications are dropped."""
        self._event_operation_active = False
        self._discard_queued_notifications()

    def _discard_queued_notifications(self) -> None:
        retained: list[Mapping[str, Any] | BaseException] = []
        while not self._event_queue.empty():
            item = self._event_queue.get_nowait()
            if isinstance(item, BaseException) or item.get("op") != "NOTIFICATION":
                retained.append(item)
        for item in retained:
            self._event_queue.put_nowait(item)

    async def receive_frame(self, *, timeout: float) -> Mapping[str, Any] | None:
        """Receive the next validated stream frame through its single owner."""
        if timeout <= 0:
            raise ValueError("poll timeout must be positive")
        if self._event_pump_error is not None:
            raise self._event_pump_error
        task = self._event_pump_task
        if (task is None or task.done()) and self._event_queue.empty():
            frame = await self.channel.poll(timeout=timeout)
            if frame is not None:
                self.ingest(frame)
            return frame
        try:
            item = await asyncio.wait_for(self._event_queue.get(), timeout=timeout)
        except TimeoutError:
            if self._event_pump_error is not None:
                raise self._event_pump_error from None
            return None
        if isinstance(item, BaseException):
            raise item
        return item

    async def _run_event_pump(self) -> None:
        """Drain notifications even between CAN requests; fail closed on stream errors."""
        idle_delay = 0.02
        try:
            while True:
                frame = await self.channel.poll(timeout=self.poll_timeout)
                if frame is None:
                    # The ESP poll action returns immediately for an empty ring.
                    # Back off while idle, then return to a short cadence as soon
                    # as traffic arrives. This avoids a tight Native-API loop.
                    await asyncio.sleep(min(idle_delay, self.poll_timeout))
                    idle_delay = min(0.25, idle_delay * 2)
                    continue
                idle_delay = 0.02
                self.ingest(frame)
                if frame.get("op") == "NOTIFICATION" and not self._event_operation_active:
                    continue
                if (
                    frame.get("kind") == "response"
                    and frame.get("request_id") in self._retired_operations
                ):
                    continue
                try:
                    self._event_queue.put_nowait(frame)
                except asyncio.QueueFull as exc:
                    raise ThinGattCorrelationError(
                        "Thin-GATT consumer event queue overflow"
                    ) from exc
                if frame.get("op") == "DISCONNECTED" or (
                    frame.get("op") == "CONNECTION_STATE"
                    and isinstance(frame.get("payload"), Mapping)
                    and frame["payload"].get("state") in {"disconnected", "failed"}
                ):
                    return
        except asyncio.CancelledError:
            raise
        except Exception as error:
            self._event_pump_error = error
            self.stream_error = (
                error if isinstance(error, ThinGattFlowControlError) else self.stream_error
            )
            while not self._event_queue.empty():
                self._event_queue.get_nowait()
            self._event_queue.put_nowait(error)

    def accept_notification(
        self, notification: GattNotification, *, already_ingested: bool = False
    ) -> None:
        """Validate a notification's epoch, identity, handle, and sequence."""
        if not self.connected or self.identity != notification.identity:
            raise ThinGattCorrelationError("notification identity is not current")
        valid_sequence = (
            0 < notification.seq <= self.last_seq
            if already_ingested
            else notification.seq == self.last_seq + 1
        )
        if notification.epoch != self.epoch or not valid_sequence:
            raise ThinGattCorrelationError("notification is stale or out of sequence")
        if self.handles is not None and notification.handle not in self.handles.values.values():
            raise ThinGattCorrelationError("notification handle is not resolved")
        if not already_ingested:
            self.last_seq = notification.seq

    def _frame_identity(self, frame: Mapping[str, Any]) -> ConnectionIdentity:
        try:
            return ConnectionIdentity(frame["gattc_if"], frame["conn_id"])
        except (KeyError, TypeError, ValueError) as exc:
            raise ThinGattCorrelationError("frame has no valid connection identity") from exc

    def _validate_notification_frame(self, frame: Mapping[str, Any]) -> None:
        try:
            payload = frame["payload"]
            handle = frame.get("handle")
            if handle is None and isinstance(payload, Mapping):
                handle = payload.get("handle")
            if (
                type(frame["epoch"]) is not int
                or frame["epoch"] <= 0
                or type(frame["seq"]) is not int
                or frame["seq"] <= 0
                or type(handle) is not int
                or handle < 0
                or not isinstance(payload, Mapping)
                or not isinstance(payload.get("value"), bytes)
            ):
                raise ValueError("notification fields")
            if self.identity is not None and self._frame_identity(frame) != self.identity:
                raise ValueError("notification identity")
        except (KeyError, TypeError, ValueError, ThinGattCorrelationError) as error:
            raise ThinGattCorrelationError("Thin-GATT notification frame is invalid") from error

    def _require_identity(self, frame: Mapping[str, Any]) -> None:
        if self.identity is None or self._frame_identity(frame) != self.identity:
            raise ThinGattCorrelationError("frame identity is not current")

    def _invalidate(self) -> None:
        self.connected = False
        # CAPABILITY is a bootstrap marker for one CONNECT boundary.  The
        # firmware emits it again when a later CONNECT opens a fresh stream;
        # do not carry the previous boundary's marker state across the
        # physical disconnect.
        self.capability = False
        self._capability_seen = False
        self.identity = None
        self.handles = None
        self.last_seq = -1
        self._connect_request = None
        self._pending_operations.clear()


class ThinGattLink:
    """Secure, vendor-neutral Thin-GATT lifecycle orchestration.

    ``ThinGattSession`` owns frame validation and connection fencing.  This
    class owns the proven ATT order on top of it: discovery, explicit
    encryption proof, fresh handle lookup, CCCD subscriptions, and optional
    zero-length characteristic writes.  Every operation is serialized and is
    correlated by its request id plus the current epoch and physical identity.
    No operation infers encryption from diagnostics or a response alone.
    """

    _TERMINAL_STATUSES = frozenset({"OK", "WRITE_FAILED", "ERROR", "CANCELLED"})

    def __init__(
        self,
        session: ThinGattSession,
        *,
        profile: ThinGattProfile | None = None,
        roles: Mapping[str, tuple[str, ...]] | None = None,
        subscriptions: tuple[tuple[str, str], ...] = (),
        zero_write: tuple[str, str] | None = None,
        poll_timeout: float | None = None,
    ) -> None:
        self.session = session
        self.profile = profile
        configured_roles = roles if roles is not None else (profile.roles if profile else {})
        self.roles = self._copy_roles(configured_roles)
        self.subscriptions = tuple(subscriptions)
        self.zero_write = zero_write
        self.poll_timeout = poll_timeout or session.poll_timeout
        if self.poll_timeout <= 0:
            raise ValueError("poll_timeout must be positive")
        self.encrypted_epoch: int | None = None
        self.discovered_epoch: int | None = None
        self._ready_epoch: int | None = None
        self.gateway_authenticated_epoch: int | None = None
        self._subscribed_roles: set[tuple[str, str]] = set()
        self.last_request_id: int | None = None
        self._operation_lock = asyncio.Lock()

    @staticmethod
    def _copy_roles(roles: Mapping[str, tuple[str, ...]]) -> dict[str, tuple[str, ...]]:
        copied: dict[str, tuple[str, ...]] = {}
        for role, value in roles.items():
            if not isinstance(role, str) or not role:
                raise ValueError("Thin-GATT role names must be non-empty")
            if not isinstance(value, tuple) or len(value) not in {2, 3}:
                raise ValueError(
                    "Thin-GATT roles must contain service, characteristic[, descriptor]"
                )
            if any(not isinstance(item, str) or not item for item in value):
                raise ValueError("Thin-GATT UUID roles must be non-empty")
            copied[role] = value
        return copied

    @property
    def is_ready(self) -> bool:
        return (
            self.session.connected
            and self.session.identity is not None
            and self._ready_epoch == self.session.epoch
            and self.encrypted_epoch == self.session.epoch
            and self.discovered_epoch == self.session.epoch
            and self.session.handles is not None
        )

    @property
    def is_connected(self) -> bool:
        return self.session.connected and self.session.identity is not None

    async def connect(self, *, timeout: float = 20.0) -> ConnectionIdentity:
        """Connect only; callers may then drive the explicit lifecycle steps."""
        async with self._operation_lock:
            return await self.session.prepare(timeout=timeout)

    async def prepare(
        self, *, timeout: float = 20.0, zero_write: bool | None = None
    ) -> GattHandles:
        """Establish a current secure link and install fresh handles."""
        if timeout <= 0:
            raise ValueError("timeout must be positive")
        async with self._operation_lock:
            if self.is_ready:
                handles = self.session.handles
                assert handles is not None
                return handles
            await self.session.prepare(timeout=timeout)
            # ``prepare`` may be called after a disconnect.  The session's
            # identity/epoch is authoritative; all link-scoped proof is reset.
            if self._ready_epoch != self.session.epoch:
                self.encrypted_epoch = None
                self.discovered_epoch = None
                self._ready_epoch = None
                self.gateway_authenticated_epoch = None
                self._subscribed_roles.clear()
            await self._operation("DISCOVER", {}, timeout=timeout, event="discovered")
            self.discovered_epoch = self.session.epoch
            await self._operation(
                "PAIR_ENCRYPT",
                {"mode": "mitm"},
                timeout=timeout,
                event="encrypted",
                require_event=True,
            )
            self.encrypted_epoch = self.session.epoch
            values: dict[str, int] = {}
            for role, uuids in self.roles.items():
                values[role] = await self._lookup(role, uuids, timeout=timeout)
            handles = GattHandles(values, epoch=self.session.epoch)
            self.session.install_handles(handles)
            for value_role, cccd_role in self.subscriptions:
                await self._subscribe(value_role, cccd_role, timeout=timeout)
            # Mark the link ready before the optional write so it goes through
            # the same current-epoch gate as any caller-issued write.
            self._ready_epoch = self.session.epoch
            do_zero_write = self.zero_write is not None if zero_write is None else zero_write
            if do_zero_write and self.zero_write is not None:
                value_role, notify_role = self.zero_write
                await self._write_current(
                    value_role, b"", response_role=notify_role, timeout=timeout
                )
            self.session.start_event_pump()
            return handles

    async def authenticate_gateway(self, *, timeout: float = 20.0) -> None:
        """Run the fixed gateway service handshake on the prepared link.

        This is the Thin-RPC equivalent of the native BLE adapter's
        transport-level handshake.  It must complete before the transparent
        characteristic is used for CAN-IP traffic; pairing/encryption alone
        is not gateway authorization.
        """

        if timeout <= 0:
            raise ValueError("timeout must be positive")
        async with self._operation_lock:
            self._require_ready()
            if self.gateway_authenticated_epoch == self.session.epoch:
                return

            async def subscribe(value_role: str, cccd_role: str) -> None:
                # ``prepare`` may already have enabled these notifications.
                # Keep the exchange idempotent while preserving one CCCD write
                # per role in the normal fresh-session path.
                if (value_role, cccd_role) in self._subscribed_roles:
                    return
                if self.session.handles is None or cccd_role not in self.session.handles.values:
                    return
                await self._subscribe(value_role, cccd_role, timeout=timeout)

            async def write(role: str, value: bytes, response_role: str) -> bytes | None:
                return await self._write_current(
                    role, value, response_role=response_role, timeout=timeout
                )

            async def wait(_role: str, _timeout: float) -> bytes:
                # The Thin-RPC write operation carries the correlated
                # notification result itself; this fallback is unreachable.
                raise ThinGattCorrelationError("Thin-GATT gateway response was not correlated")

            await authenticate_gateway(
                subscribe=subscribe,
                write=write,
                wait=wait,
                epoch=lambda: self.session.epoch,
                timeout=timeout,
            )
            self.gateway_authenticated_epoch = self.session.epoch

    async def discover(self, *, timeout: float = 20.0) -> None:
        """Run discovery and require its correlated lifecycle event."""
        async with self._operation_lock:
            self._require_connected()
            await self._operation("DISCOVER", {}, timeout=timeout, event="discovered")
            self.discovered_epoch = self.session.epoch

    async def pair_encrypt(self, *, timeout: float = 20.0, mode: str = "mitm") -> None:
        """Prove current-link encryption with the correlated terminal event."""
        async with self._operation_lock:
            self._require_connected()
            if self.discovered_epoch != self.session.epoch:
                raise ThinGattSessionStateError("Thin-GATT discovery is required before encryption")
            await self._operation(
                "PAIR_ENCRYPT",
                {"mode": mode},
                timeout=timeout,
                event="encrypted",
                require_event=True,
            )
            self.encrypted_epoch = self.session.epoch

    async def resolve_handles(self, *, timeout: float = 20.0) -> GattHandles:
        """Resolve and install all configured role handles for this epoch."""
        async with self._operation_lock:
            self._require_connected()
            if self.encrypted_epoch != self.session.epoch:
                raise ThinGattSessionStateError("Thin-GATT encryption proof is required")
            values = {
                role: await self._lookup(role, uuids, timeout=timeout)
                for role, uuids in self.roles.items()
            }
            handles = GattHandles(values, epoch=self.session.epoch)
            self.session.install_handles(handles)
            return handles

    async def subscribe(self, value_role: str, cccd_role: str, *, timeout: float = 20.0) -> None:
        """Enable one current-epoch notification subscription."""
        async with self._operation_lock:
            self._require_connected()
            self._require_ready_for_handles()
            await self._subscribe(value_role, cccd_role, timeout=timeout)

    async def disconnect(self, *, timeout: float = 5.0) -> None:
        """Close the physical link and discard all link-scoped proof."""
        async with self._operation_lock:
            await self.session.disconnect(timeout=timeout)
            self.encrypted_epoch = None
            self.discovered_epoch = None
            self._ready_epoch = None
            self.gateway_authenticated_epoch = None
            self._subscribed_roles.clear()

    async def write(
        self,
        role: str,
        value: bytes,
        *,
        response_role: str | None = None,
        timeout: float = 20.0,
    ) -> bytes | None:
        """Write a current-epoch handle, optionally awaiting its notification."""
        if not isinstance(value, bytes):
            raise TypeError("Thin-GATT characteristic values must be bytes")
        async with self._operation_lock:
            return await self._write_current(
                role, value, response_role=response_role, timeout=timeout
            )

    async def _write_current(
        self, role: str, value: bytes, *, response_role: str | None, timeout: float
    ) -> bytes | None:
        self._require_ready()
        handle = self._handle(role)
        response_handle = self._handle(response_role) if response_role else None
        payload: dict[str, Any] = {
            "handle": handle,
            "value": base64.b64encode(value).decode("ascii"),
            "response": True,
        }
        if response_handle is not None:
            payload.update({"expect_notification": True, "notification_handle": response_handle})
        result = await self._operation(
            "WRITE_CHAR",
            payload,
            timeout=timeout,
            notification_handle=response_handle,
        )
        if response_handle is None or result is None:
            return None
        notification_payload = result.get("payload")
        if not isinstance(notification_payload, Mapping):
            raise ThinGattCorrelationError("Thin-GATT notification payload is invalid")
        data = notification_payload.get("value")
        if not isinstance(data, bytes):
            raise ThinGattCorrelationError("Thin-GATT notification value is invalid")
        return data

    async def zero_length_write(
        self, role: str, *, response_role: str | None = None, timeout: float = 20.0
    ) -> bytes | None:
        """Issue the proven empty characteristic write without a dummy byte."""
        return await self.write(role, b"", response_role=response_role, timeout=timeout)

    async def _lookup(self, role: str, uuids: tuple[str, ...], *, timeout: float) -> int:
        payload: dict[str, Any] = {"service": uuids[0], "characteristic": uuids[1]}
        if len(uuids) == 3:
            payload["descriptor"] = uuids[2]
        result = await self._operation("HANDLE_LOOKUP", payload, timeout=timeout)
        body = result.get("payload") if result else None
        handle = body.get("handle") if isinstance(body, Mapping) else None
        if type(handle) is not int or handle <= 0:
            raise ThinGattCorrelationError(f"handle lookup did not return role {role!r}")
        return handle

    async def _subscribe(self, value_role: str, cccd_role: str, *, timeout: float) -> None:
        if (value_role, cccd_role) in self._subscribed_roles:
            return
        await self._operation(
            "SUBSCRIBE",
            {
                "value_handle": self._handle(value_role),
                "cccd_handle": self._handle(cccd_role),
                "enable": True,
            },
            timeout=timeout,
        )
        self._subscribed_roles.add((value_role, cccd_role))

    async def _operation(
        self,
        op: str,
        payload: Mapping[str, Any],
        *,
        timeout: float,
        event: str | None = None,
        require_event: bool = False,
        notification_handle: int | None = None,
    ) -> dict[str, Any] | None:
        if timeout <= 0:
            raise ValueError("timeout must be positive")
        request_id = self.session.register_request(op)
        identity = self.session.identity
        if identity is None:
            raise ThinGattSessionStateError("Thin-GATT session identity is unavailable")
        request_payload = dict(payload)
        request_payload.update(
            {
                "op": op,
                "request_id": request_id,
                "epoch": self.session.epoch,
                "gattc_if": identity.gattc_if,
                "conn_id": identity.conn_id,
            }
        )
        response: dict[str, Any] | None = None
        matched_event: dict[str, Any] | None = None
        need_notification = notification_handle is not None
        deadline = asyncio.get_running_loop().time() + timeout
        self.session.begin_event_operation()
        try:
            await self.session.channel.action(
                self.session.services.request, request_payload, timeout=timeout
            )
            while (
                (not need_notification and response is None)
                or (event is not None and matched_event is None)
                or (need_notification and matched_event is None)
            ):
                remaining = deadline - asyncio.get_running_loop().time()
                if remaining <= 0:
                    raise RequestTimeoutError(f"Thin-GATT {op} timed out")
                frame = await self.session.receive_frame(timeout=min(self.poll_timeout, remaining))
                if frame is None:
                    await asyncio.sleep(min(0.01, remaining))
                    continue
                if not isinstance(frame, Mapping):
                    raise ThinGattCorrelationError("Thin-GATT frame is not an object")
                frame_op = frame.get("op")
                frame_kind = frame.get("kind")
                if frame_kind == "event" and frame_op == "NOTIFICATION":
                    if notification_handle is None:
                        # Unsolicited notifications remain stream traffic; a
                        # caller cannot use one as proof for another operation.
                        continue
                    candidate_payload = frame.get("payload")
                    candidate_handle = frame.get("handle")
                    if candidate_handle is None and isinstance(candidate_payload, Mapping):
                        candidate_handle = candidate_payload.get("handle")
                    if (
                        candidate_handle == notification_handle
                        and frame.get("request_id") != request_id
                    ):
                        raise ThinGattCorrelationError("Thin-GATT notification token mismatch")
                    if frame.get("request_id") != request_id:
                        continue
                    notification = self._notification_from_frame(frame, notification_handle)
                    self.session.accept_notification(notification, already_ingested=True)
                    # The proven ESP adapter treats a correlated notification
                    # as the terminal result for notification-waiting writes:
                    # it clears the pending firmware operation and emits no
                    # separate WRITE_CHAR response.  Retire the host request
                    # here so a defensive late ACK cannot leak into a later
                    # operation.
                    self.session._pending_operations.pop(request_id, None)
                    self.session._retired_operations.add(request_id)
                    matched_event = dict(frame)
                    continue
                expected_event_op = (
                    "ENCRYPTION_STATE" if event == "encrypted" else "CONNECTION_STATE"
                )
                if (
                    frame_kind == "event"
                    and event is not None
                    and frame_op == expected_event_op
                    and frame.get("request_id") != request_id
                ):
                    # A cancelled operation may leave its terminal lifecycle
                    # event in the bounded ESPHome queue.  Consume that
                    # retired event to keep sequence correlation intact, but
                    # never allow an active/unknown token to complete this
                    # operation.
                    if frame.get("request_id") in {0, *self.session._retired_operations}:
                        continue
                    raise ThinGattCorrelationError("Thin-GATT lifecycle event token mismatch")
                if (
                    frame_kind == "event"
                    and event is not None
                    and frame_op
                    == ("ENCRYPTION_STATE" if event == "encrypted" else "CONNECTION_STATE")
                    and frame.get("request_id") == request_id
                    and isinstance(frame.get("payload"), Mapping)
                    and frame["payload"].get("state") != event
                ):
                    raise ThinGattSessionStateError(
                        f"Thin-GATT {op} terminal event was not {event!r}"
                    )
                if frame_op == "DISCONNECTED" or (
                    frame_op == "CONNECTION_STATE"
                    and isinstance(frame.get("payload"), Mapping)
                    and frame["payload"].get("state") in {"disconnected", "failed"}
                ):
                    raise TransportError("Thin-GATT disconnected during operation")
                if frame_kind == "response" and frame.get("request_id") == request_id:
                    if frame_op != op:
                        raise ThinGattCorrelationError("Thin-GATT response operation mismatch")
                    status = frame.get("status")
                    if status not in self._TERMINAL_STATUSES and status != "accepted":
                        raise TransportError(f"Thin-GATT {op} was not accepted")
                    if status in {"WRITE_FAILED", "ERROR", "CANCELLED"}:
                        raise TransportError(f"Thin-GATT {op} failed")
                    # DISCOVER's accepted response is paired with its
                    # CONNECTION_STATE=discovered event and has no second
                    # callback in the proven adapter. Other operations keep
                    # waiting for a terminal OK after accepted.
                    if status in self._TERMINAL_STATUSES or op == "DISCOVER":
                        response = dict(frame)
                if (
                    frame_kind == "event"
                    and event is not None
                    and frame_op
                    == ("ENCRYPTION_STATE" if event == "encrypted" else "CONNECTION_STATE")
                    and frame.get("request_id") == request_id
                    and isinstance(frame.get("payload"), Mapping)
                    and frame["payload"].get("state") == event
                ):
                    if (
                        frame.get("epoch") != self.session.epoch
                        or self.session.identity != self.session._frame_identity(frame)
                    ):
                        raise ThinGattCorrelationError(
                            "Thin-GATT lifecycle event identity mismatch"
                        )
                    matched_event = dict(frame)
            if require_event and matched_event is None:
                raise ThinGattCorrelationError(f"Thin-GATT {op} lacked its terminal event")
            self.last_request_id = request_id
            return matched_event if notification_handle is not None else response
        except Exception:
            self.session._pending_operations.pop(request_id, None)
            await self._cancel(request_id, identity, timeout)
            raise
        finally:
            self.session.end_event_operation()

    def _notification_from_frame(
        self, frame: Mapping[str, Any], expected_handle: int
    ) -> GattNotification:
        """Normalize raw notification-shape failures to correlation errors."""
        try:
            payload_frame = frame.get("payload")
            frame_handle = frame.get("handle")
            if frame_handle is None and isinstance(payload_frame, Mapping):
                frame_handle = payload_frame.get("handle")
            if (
                not isinstance(payload_frame, Mapping)
                or not isinstance(payload_frame.get("value"), bytes)
                or type(frame_handle) is not int
            ):
                raise ValueError("notification payload shape")
            notification = GattNotification(
                frame["epoch"],
                self.session._frame_identity(frame),
                frame_handle,
                payload_frame["value"],
                frame["seq"],
            )
            if notification.handle != expected_handle:
                raise ValueError("notification handle")
            return notification
        except (KeyError, TypeError, ValueError, ThinGattCorrelationError) as error:
            raise ThinGattCorrelationError(
                "Thin-GATT notification correlation is invalid"
            ) from error

    async def _cancel(self, request_id: int, identity: ConnectionIdentity, timeout: float) -> None:
        self.session._pending_operations.pop(request_id, None)
        self.session._retired_operations.add(request_id)
        try:
            cancel_id = self.session.register_request("CANCEL")
        except (ThinGattSessionStateError, ValueError):
            return
        with contextlib.suppress(Exception):
            await self.session.channel.action(
                self.session.services.request,
                {
                    "op": "CANCEL",
                    "request_id": cancel_id,
                    "epoch": self.session.epoch,
                    "gattc_if": identity.gattc_if,
                    "conn_id": identity.conn_id,
                    "payload": {"request_id": request_id},
                },
                timeout=min(timeout, 1.0),
            )
        self.session._pending_operations.pop(cancel_id, None)
        self.session._retired_operations.add(cancel_id)

    def _handle(self, role: str) -> int:
        if self.session.handles is None or role not in self.session.handles.values:
            raise ThinGattSessionStateError(f"Thin-GATT handle role {role!r} is unresolved")
        return self.session.handles.values[role]

    def _require_connected(self) -> None:
        if not self.session.connected or self.session.identity is None:
            raise ThinGattSessionStateError("Thin-GATT link is disconnected")

    def _require_ready(self) -> None:
        self._require_connected()
        if not self.is_ready:
            raise ThinGattSessionStateError("Thin-GATT link is not securely prepared")

    def _require_ready_for_handles(self) -> None:
        self._require_connected()
        if (
            self.encrypted_epoch != self.session.epoch
            or self.session.handles is None
            or self.session.handles.epoch != self.session.epoch
        ):
            raise ThinGattSessionStateError("Thin-GATT handles are not securely prepared")


# Descriptive alias for callers that model the link as a secure session.
ThinGattSecureSession = ThinGattLink


class ThinGattMessageTransport:
    """Adapt Thin-RPC segmented writes/notifications to message transport."""

    def __init__(
        self,
        session: ThinGattSession,
        *,
        mtu: int = 20,
        request_handle: int | None = None,
        response_handle: int | None = None,
    ) -> None:
        self.session = session
        self.codec = BleSegmentCodec(mtu=mtu)
        self.reassembler = BleSegmentReassembler(mtu=mtu)
        self._request_lock = asyncio.Lock()
        self.request_handle = request_handle
        self.response_handle = response_handle

    @property
    def is_connected(self) -> bool:
        return self.session.connected and self.session.identity is not None

    async def connect(self) -> None:
        await self.session.prepare()

    async def disconnect(self) -> None:
        await self.session.disconnect()

    async def request(self, message: bytes, *, timeout: float) -> bytes:
        if not isinstance(message, bytes):
            raise TypeError("Thin-GATT messages must be bytes")
        if timeout <= 0:
            raise ValueError("timeout must be positive")
        if not self.is_connected:
            await self.connect()
        async with self._request_lock:
            self.session.start_event_pump()
            self.session.begin_event_operation()
            self.reassembler.reset()
            request_ids: list[int] = []
            identity = self.session.identity
            if identity is None:
                raise TransportError("Thin-GATT session lost its identity")
            try:
                request_handle = self.request_handle
                response_handle = self.response_handle
                if request_handle is None or response_handle is None:
                    raise ThinGattSessionStateError(
                        "Thin-GATT request/response handles are required"
                    )
                complete_result: bytes | None = None
                deadline = asyncio.get_running_loop().time() + timeout
                for segment in self.codec.encode(message):
                    remaining = deadline - asyncio.get_running_loop().time()
                    if remaining <= 0:
                        raise RequestTimeoutError("Thin-GATT message response timed out")
                    request_id = self.session.register_request("WRITE_CHAR")
                    request_ids.append(request_id)
                    try:
                        await asyncio.wait_for(
                            self.session.channel.action(
                                self.session.services.request,
                                {
                                    "op": "WRITE_CHAR",
                                    "request_id": request_id,
                                    "epoch": self.session.epoch,
                                    "gattc_if": identity.gattc_if,
                                    "conn_id": identity.conn_id,
                                    "handle": request_handle,
                                    "value": base64.b64encode(segment).decode("ascii"),
                                    "response": True,
                                },
                                timeout=remaining,
                            ),
                            timeout=remaining,
                        )
                    except TimeoutError as exc:
                        raise RequestTimeoutError("Thin-GATT message response timed out") from exc
                    acknowledged = False
                    while not acknowledged:
                        remaining = deadline - asyncio.get_running_loop().time()
                        if remaining <= 0:
                            raise RequestTimeoutError("Thin-GATT message response timed out")
                        frame = await self.session.receive_frame(timeout=remaining)
                        if frame is None:
                            continue
                        if frame.get("op") == "NOTIFICATION":
                            self._consume_notification(frame, response_handle)
                            complete_result = self.reassembler.feed(frame["payload"]["value"])
                            continue
                        if frame.get("op") == "DISCONNECTED":
                            raise TransportError("Thin-GATT disconnected during message request")
                        if frame.get("op") == "WRITE_CHAR":
                            if frame.get("status") != "OK":
                                raise TransportError("Thin-GATT write was rejected")
                            acknowledged = True
                    request_ids.remove(request_id)
                while complete_result is None:
                    remaining = deadline - asyncio.get_running_loop().time()
                    if remaining <= 0:
                        raise RequestTimeoutError("Thin-GATT message response timed out")
                    frame = await self.session.receive_frame(timeout=remaining)
                    if frame is None:
                        continue
                    if frame.get("op") != "NOTIFICATION":
                        if frame.get("op") == "DISCONNECTED":
                            raise TransportError("Thin-GATT disconnected during message request")
                        continue
                    self._consume_notification(frame, response_handle)
                    complete_result = self.reassembler.feed(frame["payload"]["value"])
                return complete_result
            except Exception:
                await self._abort(request_ids, timeout)
                raise
            finally:
                self.session.end_event_operation()

    def _consume_notification(self, frame: Mapping[str, Any], response_handle: int) -> None:
        payload = frame.get("payload")
        if not isinstance(payload, Mapping) or not isinstance(payload.get("value"), bytes):
            raise ProtocolError("invalid Thin-GATT notification payload")
        notification = GattNotification(
            frame["epoch"],
            self.session._frame_identity(frame),
            frame["handle"],
            payload["value"],
            frame["seq"],
        )
        if notification.handle != response_handle:
            raise ThinGattCorrelationError(
                "notification handle is not the configured response handle"
            )
        self.session.accept_notification(notification, already_ingested=True)

    async def _abort(self, request_ids: list[int], timeout: float) -> None:
        await self.session.stop_event_pump()
        identity = self.session.identity
        for request_id in request_ids:
            with contextlib.suppress(Exception):
                await self.session.channel.action(
                    self.session.services.request,
                    {
                        "op": "CANCEL",
                        "request_id": request_id,
                        "epoch": self.session.epoch,
                        "gattc_if": identity.gattc_if if identity else 0,
                        "conn_id": identity.conn_id if identity else 0,
                    },
                    timeout=min(timeout, 1.0),
                )
        self.session._pending_operations.clear()
        self.reassembler.reset()
        self.session._invalidate()
