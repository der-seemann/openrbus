"""Optional Bleak adapter for the validated BLE transport characteristics."""

from __future__ import annotations

import asyncio
import contextlib
from collections.abc import Awaitable, Callable
from importlib import import_module
from typing import Any, cast

from openrbus.errors import RequestTimeoutError, TransportError
from openrbus.protocol.ble_segments import BleSegmentCodec, BleSegmentReassembler
from openrbus.transport.gateway_auth import authenticate_gateway

try:  # dbus-fast is a Linux/BlueZ-only optional dependency.
    from openrbus.transport.bluez_agent import BlueZPairingAgent
except ImportError:  # pragma: no cover - exercised on non-Linux installs
    BlueZPairingAgent = None  # type: ignore[assignment,misc]

TRANSPARENT_SERVICE = "00000000-0000-4000-8000-000000000001"
REQUEST_EXTENDED = "00000000-0000-4000-8000-000000000001"
RESPONSE_EXTENDED = "00000000-0000-4000-8000-000000000001"
ERROR_MANAGEMENT = "00000000-0000-4000-8000-000000000001"
GATEWAY_IDENT_REQUEST = "00000000-0000-4000-8000-000000000001"
GATEWAY_IDENT_RESPONSE = "00000000-0000-4000-8000-000000000001"
GATEWAY_AUTH_REQUEST = "00000000-0000-4000-8000-000000000001"
GATEWAY_AUTH_RESPONSE = "00000000-0000-4000-8000-000000000001"

BleakClientFactory = Callable[..., Any]
Authorizer = Callable[[Any], Awaitable[None]]


class _NotificationBoundary:
    """Admit only a fresh frame at the start of a request.

    A notification arriving after the previous request can be a tail of that
    frame.  Such a tail must not be handed to ``BleSegmentReassembler`` as the
    beginning of the current response.  Once a tail is seen, quarantine the
    channel through its final marker before admitting the next fresh frame.
    """

    _FRESH = 0
    _ACTIVE = 1
    _QUARANTINED = 2

    def __init__(self) -> None:
        self._state = self._FRESH

    def reset(self) -> None:
        self._state = self._FRESH

    def accept(self, segment: bytes) -> bool:
        if self._state == self._QUARANTINED:
            if segment and segment[0] == 0xFF:
                self._state = self._FRESH
            return False
        if self._state == self._ACTIVE:
            return True

        # Keep malformed values visible to the reassembler so they fail the
        # request closed, rather than being silently discarded at the boundary.
        if not segment:
            self._state = self._ACTIVE
            return True
        marker = segment[0]
        if marker == 0x00 or marker == 0xFF:
            self._state = self._ACTIVE
            return True
        if 0x01 <= marker <= 0x7F:
            self._state = self._QUARANTINED
            return False
        self._state = self._ACTIVE
        return True


def _default_client_factory() -> BleakClientFactory:
    try:
        bleak_client = import_module("bleak").BleakClient
    except ImportError as exc:  # pragma: no cover - depends on optional extra
        raise RuntimeError("BLE support requires `pip install openrbus[ble]`") from exc
    return cast(BleakClientFactory, bleak_client)


class BleakMessageTransport:
    """Exchange complete messages over the extended BLE characteristics.

    Pairing is delegated to owner-controlled operating-system tooling.  This
    class contains no built-in PIN, default credential, manufacturer unlock,
    or service-access algorithm, and never stores or logs pairing credentials.
    """

    def __init__(
        self,
        address: str,
        *,
        authorizer: Authorizer | None = None,
        client_factory: BleakClientFactory | None = None,
        connect_timeout: float = 20.0,
        mtu: int = 20,
        pairing_pin: int | None = None,
        gateway_auth: bool = False,
        adapter: str | None = None,
    ) -> None:
        if not address:
            raise ValueError("BLE address or platform identifier is required")
        self._address = address
        self._authorizer = authorizer
        self._client_factory = client_factory
        self._pairing_pin = pairing_pin
        self._gateway_auth = gateway_auth
        # On Linux/BlueZ, an address alone is ambiguous when HA owns more
        # than one adapter.  Keep the selected adapter explicit all the way
        # to Bleak instead of letting BlueZ choose its default adapter.
        self._adapter = adapter
        self._connect_timeout = connect_timeout
        self._codec = BleSegmentCodec(mtu=mtu)
        self._response_reassembler = BleSegmentReassembler(mtu=mtu)
        self._error_reassembler = BleSegmentReassembler(mtu=mtu)
        # Bleak notification callbacks only enqueue immutable segments.  The
        # serialized request coroutine consumes every segment in order and
        # performs reassembly, preventing rapid middle segments from being
        # overwritten by a last-value/event callback pattern.
        self._response_segments: asyncio.Queue[bytes] = asyncio.Queue()
        self._error_segments: asyncio.Queue[bytes] = asyncio.Queue()
        self._receiving = False
        self._response_boundary = _NotificationBoundary()
        self._error_boundary = _NotificationBoundary()
        self._request_lock = asyncio.Lock()
        self._client: Any | None = None
        self.transport_generation = f"{id(self):x}"

    @property
    def is_connected(self) -> bool:
        return bool(self._client is not None and self._client.is_connected)

    async def connect(self) -> None:
        if self.is_connected:
            return
        factory = self._client_factory or _default_client_factory()
        client_kwargs: dict[str, Any] = {"timeout": self._connect_timeout}
        if self._adapter:
            # Bleak's documented BlueZ selector is the nested ``bluez``
            # argument.  Do not pass it for legacy/custom factories unless a
            # source was actually selected.
            client_kwargs["bluez"] = {"adapter": self._adapter}
        client = factory(self._address, **client_kwargs)
        agent = (
            BlueZPairingAgent(self._address, self._pairing_pin)
            if self._pairing_pin is not None
            else None
        )
        if self._pairing_pin is not None and agent is None:
            raise TransportError("native PIN pairing requires BlueZ dbus support")
        try:
            if agent is not None:
                await agent.start()
            await client.connect()
            if not client.is_connected:
                raise TransportError("BLE client did not enter connected state")
            if self._authorizer is not None:
                await self._authorizer(client)
            if self._pairing_pin is not None:
                pair = getattr(client, "pair", None)
                if pair is None:
                    raise TransportError("native PIN pairing is unsupported by the BLE client")
                await pair()
            if self._gateway_auth:
                await self._authenticate_gateway(client)
            # Pair/encrypt before subscribing.  The gateway rejects CCCD
            # writes until the link is encrypted, and BlueZ must still have
            # the temporary Agent1 registered while ``pair()`` runs.
            await client.start_notify(RESPONSE_EXTENDED, self._on_response)
            await client.start_notify(ERROR_MANAGEMENT, self._on_error)
        except Exception as exc:
            with contextlib.suppress(Exception):
                await client.disconnect()
            if isinstance(exc, TransportError):
                raise
            raise TransportError("BLE connection setup failed") from exc
        finally:
            if agent is not None:
                await agent.stop()
        self._client = client

    async def _authenticate_gateway(self, client: Any) -> None:
        """Complete the gateway's fixed BLE service-access handshake.

        The transparent CAN-IP characteristic is gated behind this exchange;
        BLE link pairing alone is not sufficient.  The gateway challenge is
        the eight-byte IdentInfo notification, and the response uses the
        capture-validated fixed TEA inputs.  No credential is retained or
        emitted by this transport.
        """

        loop = asyncio.get_running_loop()
        responses: dict[str, asyncio.Future[bytes]] = {
            "identity_notify": loop.create_future(),
            "auth_notify": loop.create_future(),
        }

        def on_ident(_sender: Any, data: bytearray) -> None:
            future = responses["identity_notify"]
            if not future.done():
                future.set_result(bytes(data))

        def on_authorization(_sender: Any, data: bytearray) -> None:
            future = responses["auth_notify"]
            if not future.done():
                future.set_result(bytes(data))

        async def subscribe(value_role: str, _cccd_role: str) -> None:
            characteristic = {
                "identity_notify": GATEWAY_IDENT_RESPONSE,
                "auth_notify": GATEWAY_AUTH_RESPONSE,
            }[value_role]
            callback = on_ident if value_role == "identity_notify" else on_authorization
            await client.start_notify(characteristic, callback)

        async def write(role: str, value: bytes, _response_role: str) -> bytes | None:
            characteristic = {
                "identity": GATEWAY_IDENT_REQUEST,
                "auth": GATEWAY_AUTH_REQUEST,
            }[role]
            await client.write_gatt_char(characteristic, value, response=True)
            return None

        async def wait(role: str, timeout: float) -> bytes:
            return await asyncio.wait_for(responses[role], timeout=timeout)

        try:
            await authenticate_gateway(
                subscribe=subscribe,
                write=write,
                wait=wait,
                epoch=lambda: True,
                timeout=self._connect_timeout,
            )
        except TimeoutError as exc:
            raise TransportError("BLE gateway authorization timed out") from exc
        finally:
            for characteristic in (GATEWAY_IDENT_RESPONSE, GATEWAY_AUTH_RESPONSE):
                stop_notify = getattr(client, "stop_notify", None)
                if stop_notify is not None:
                    with contextlib.suppress(Exception):
                        await stop_notify(characteristic)

    async def disconnect(self) -> None:
        client, self._client = self._client, None
        self._receiving = False
        self._response_boundary.reset()
        self._error_boundary.reset()
        self._response_reassembler.reset()
        self._error_reassembler.reset()
        self._clear_queue(self._response_segments)
        self._clear_queue(self._error_segments)
        if client is not None:
            stop_notify = getattr(client, "stop_notify", None)
            if stop_notify is not None:
                for characteristic in (RESPONSE_EXTENDED, ERROR_MANAGEMENT):
                    with contextlib.suppress(Exception):
                        await stop_notify(characteristic)
            with contextlib.suppress(Exception):
                await client.disconnect()

    async def request(self, message: bytes, *, timeout: float) -> bytes:
        if timeout <= 0:
            raise ValueError("timeout must be positive")
        if not self.is_connected or self._client is None:
            raise TransportError("BLE transport is not connected")
        async with self._request_lock:
            self._response_reassembler.reset()
            self._error_reassembler.reset()
            self._clear_queue(self._response_segments)
            self._clear_queue(self._error_segments)
            self._response_boundary.reset()
            self._error_boundary.reset()
            self._receiving = True
            try:
                for segment in self._codec.encode(message):
                    await self._client.write_gatt_char(REQUEST_EXTENDED, segment, response=True)
                deadline = asyncio.get_running_loop().time() + timeout
                while True:
                    remaining = deadline - asyncio.get_running_loop().time()
                    if remaining <= 0:
                        raise RequestTimeoutError("BLE response timed out")
                    response_task = asyncio.create_task(self._response_segments.get())
                    error_task = asyncio.create_task(self._error_segments.get())
                    done, pending = await asyncio.wait(
                        {response_task, error_task},
                        timeout=remaining,
                        return_when=asyncio.FIRST_COMPLETED,
                    )
                    for task in pending:
                        task.cancel()
                    await asyncio.gather(*pending, return_exceptions=True)
                    if not done:
                        raise RequestTimeoutError("BLE response timed out")

                    # Prefer a simultaneously delivered gateway error over a
                    # response, then consume any response segment in this turn.
                    for task, reassembler, is_error in (
                        (error_task, self._error_reassembler, True),
                        (response_task, self._response_reassembler, False),
                    ):
                        if task not in done:
                            continue
                        try:
                            complete = reassembler.feed(task.result())
                        except Exception as exc:
                            reassembler.reset()
                            raise TransportError("invalid BLE response segmentation") from exc
                        if complete is None:
                            continue
                        if is_error:
                            raise TransportError("gateway returned an ErrorManagement response")
                        return complete
            except (RequestTimeoutError, TransportError):
                raise
            except Exception as exc:
                raise TransportError("BLE request failed") from exc
            finally:
                self._receiving = False
                self._response_boundary.reset()
                self._error_boundary.reset()
                self._response_reassembler.reset()
                self._error_reassembler.reset()
                self._clear_queue(self._response_segments)
                self._clear_queue(self._error_segments)

    def _on_response(self, _sender: Any, data: bytearray) -> None:
        if not self._receiving or not self._response_boundary.accept(bytes(data)):
            return
        self._response_segments.put_nowait(bytes(data))

    def _on_error(self, _sender: Any, data: bytearray) -> None:
        if not self._receiving or not self._error_boundary.accept(bytes(data)):
            return
        self._error_segments.put_nowait(bytes(data))

    @staticmethod
    def _clear_queue(queue: asyncio.Queue[bytes]) -> None:
        with contextlib.suppress(asyncio.QueueEmpty):
            while True:
                queue.get_nowait()


async def discover_ble_devices(*, timeout: float = 10.0, scanner: Any | None = None) -> list[Any]:
    """Return advertisements that expose the validated transparent service."""

    if timeout <= 0:
        raise ValueError("timeout must be positive")
    if scanner is None:
        try:
            scanner = import_module("bleak").BleakScanner
        except ImportError as exc:  # pragma: no cover - optional extra
            raise RuntimeError("BLE support requires `pip install openrbus[ble]`") from exc
    discovered = await scanner.discover(timeout=timeout, return_adv=True)
    matches: list[Any] = []
    values = discovered.values() if isinstance(discovered, dict) else discovered
    for item in values:
        device, advertisement = item if isinstance(item, tuple) else (item, None)
        service_uuids = getattr(advertisement, "service_uuids", None) or []
        if any(str(uuid).lower() == TRANSPARENT_SERVICE for uuid in service_uuids):
            matches.append(device)
    return matches
