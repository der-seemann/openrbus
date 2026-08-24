"""Optional Bleak adapter for the validated BLE transport characteristics."""

from __future__ import annotations

import asyncio
import contextlib
from collections.abc import Awaitable, Callable
from importlib import import_module
from typing import Any, cast

from openrbus.errors import RequestTimeoutError, TransportError
from openrbus.protocol.ble_segments import BleSegmentCodec, BleSegmentReassembler

TRANSPARENT_SERVICE = "f8fc98e4-5919-4a5c-852e-dfe04ad383c0"
REQUEST_EXTENDED = "496b1b03-cebc-4d59-9c32-14ea88c266f9"
RESPONSE_EXTENDED = "ab9af948-fb86-492d-820d-bdea2ecb7ecf"
ERROR_MANAGEMENT = "cff32957-5279-4075-bf3d-8e583d418c0d"

BleakClientFactory = Callable[..., Any]
Authorizer = Callable[[Any], Awaitable[None]]


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
    ) -> None:
        if not address:
            raise ValueError("BLE address or platform identifier is required")
        self._address = address
        self._authorizer = authorizer
        self._client_factory = client_factory
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
        self._request_lock = asyncio.Lock()
        self._client: Any | None = None

    @property
    def is_connected(self) -> bool:
        return bool(self._client is not None and self._client.is_connected)

    async def connect(self) -> None:
        if self.is_connected:
            return
        factory = self._client_factory or _default_client_factory()
        client = factory(self._address, timeout=self._connect_timeout)
        try:
            await client.connect()
            if not client.is_connected:
                raise TransportError("BLE client did not enter connected state")
            await client.start_notify(RESPONSE_EXTENDED, self._on_response)
            await client.start_notify(ERROR_MANAGEMENT, self._on_error)
            if self._authorizer is not None:
                await self._authorizer(client)
        except Exception as exc:
            with contextlib.suppress(Exception):
                await client.disconnect()
            if isinstance(exc, TransportError):
                raise
            raise TransportError("BLE connection setup failed") from exc
        self._client = client

    async def disconnect(self) -> None:
        client, self._client = self._client, None
        self._response_reassembler.reset()
        self._error_reassembler.reset()
        self._clear_queue(self._response_segments)
        self._clear_queue(self._error_segments)
        if client is not None:
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

    def _on_response(self, _sender: Any, data: bytearray) -> None:
        self._response_segments.put_nowait(bytes(data))

    def _on_error(self, _sender: Any, data: bytearray) -> None:
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
