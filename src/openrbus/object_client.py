"""Low-level async CANopen object access over a message transport."""

from __future__ import annotations

from collections.abc import Sequence

from openrbus.access import ObjectRead, RawReadResult
from openrbus.errors import CanOpenAbortError, ProtocolError
from openrbus.protocol.canip import (
    MAX_GET_LIST_MESSAGE_SIZE,
    MAX_GET_LIST_OBJECTS,
    CanIpMessage,
    ObjectAddress,
    build_batch_read,
    build_read,
    build_write,
    parse_batch_response,
    parse_read_response,
    validate_write_response,
)
from openrbus.protocol.selector import unwrap_canip, wrap_canip
from openrbus.transport.base import AsyncMessageTransport


class RawObjectClient:
    """Read objects and provide an internal confirmed-write primitive.

    Applications should use :class:`openrbus.client.OpenRBusClient`; its write
    gates cannot be bypassed accidentally through this class because the raw
    write operation is intentionally private.
    """

    def __init__(self, transport: AsyncMessageTransport, *, timeout: float = 10.0) -> None:
        if timeout <= 0:
            raise ValueError("timeout must be positive")
        self.transport = transport
        self.timeout = timeout

    async def read_raw(
        self, node: int, address: ObjectAddress, *, timeout: float | None = None
    ) -> bytes:
        effective_timeout = self._timeout(timeout)
        request = wrap_canip(build_read(node, address).encode())
        raw_response = await self.transport.request(request, timeout=effective_timeout)
        response = CanIpMessage.decode(unwrap_canip(raw_response))
        return parse_read_response(response, node, address).raw_value

    async def read_many_raw(
        self,
        items: Sequence[ObjectRead],
        *,
        timeout: float | None = None,
    ) -> tuple[RawReadResult, ...]:
        """Read objects through size-aware, same-node ``GetList`` batches.

        Items whose declared maximum value cannot fit into one validated
        GetList response fall back to the ordinary single-object read.  Result
        order always matches input order and a per-entry abort does not hide
        successful entries from the same response.
        """

        effective_timeout = self._timeout(timeout)
        results: list[RawReadResult] = []
        for batch in _partition_reads(items):
            if _estimated_response_size(batch) > MAX_GET_LIST_MESSAGE_SIZE:
                item = batch[0]
                raw = await self.read_raw(item.node, item.address, timeout=effective_timeout)
                results.append(RawReadResult(item.node, item.address, raw=raw))
                continue
            results.extend(await self._read_batch(batch, timeout=effective_timeout))
        return tuple(results)

    async def _read_batch(
        self,
        items: tuple[ObjectRead, ...],
        *,
        timeout: float,
    ) -> tuple[RawReadResult, ...]:
        node = items[0].node
        request = wrap_canip(build_batch_read(node, tuple(item.address for item in items)).encode())
        raw_response = await self.transport.request(request, timeout=timeout)
        response = CanIpMessage.decode(unwrap_canip(raw_response))
        entries = parse_batch_response(response)
        if len(entries) != len(items):
            raise ProtocolError(
                f"GetList response count mismatch: expected {len(items)}, received {len(entries)}"
            )

        results: list[RawReadResult] = []
        for expected, entry in zip(items, entries, strict=True):
            if entry.node != expected.node or entry.address != expected.address:
                raise ProtocolError(
                    "GetList response correlation mismatch: "
                    f"node={entry.node:02x}, address={entry.address}"
                )
            if entry.status == 1:
                results.append(RawReadResult(entry.node, entry.address, raw=entry.value))
                continue
            if entry.status == 2 and entry.abort_code is not None:
                results.append(
                    RawReadResult(
                        entry.node,
                        entry.address,
                        error=CanOpenAbortError(entry.abort_code, "CANopen batch read aborted"),
                    )
                )
                continue
            raise ProtocolError(f"unsupported GetList entry status {entry.status}")
        return tuple(results)

    async def _write_raw(
        self,
        node: int,
        address: ObjectAddress,
        raw_value: bytes,
        *,
        timeout: float | None = None,
    ) -> None:
        effective_timeout = self._timeout(timeout)
        request = wrap_canip(build_write(node, address, raw_value, confirmed=True).encode())
        raw_response = await self.transport.request(request, timeout=effective_timeout)
        response = CanIpMessage.decode(unwrap_canip(raw_response))
        validate_write_response(response, node, address)

    def _timeout(self, value: float | None) -> float:
        effective = self.timeout if value is None else value
        if effective <= 0:
            raise ValueError("timeout must be positive")
        return effective


def _estimated_response_size(items: Sequence[ObjectRead]) -> int:
    # Six-byte CAN-IP header, one-byte count, then eight bytes plus value per entry.
    return 7 + sum(8 + item.max_value_length for item in items)


def _partition_reads(items: Sequence[ObjectRead]) -> tuple[tuple[ObjectRead, ...], ...]:
    batches: list[tuple[ObjectRead, ...]] = []
    current: list[ObjectRead] = []
    current_size = 7

    for item in items:
        item_size = 8 + item.max_value_length
        oversized = 7 + item_size > MAX_GET_LIST_MESSAGE_SIZE
        if oversized:
            if current:
                batches.append(tuple(current))
                current = []
                current_size = 7
            batches.append((item,))
            continue

        must_split = bool(
            current
            and (
                item.node != current[0].node
                or len(current) == MAX_GET_LIST_OBJECTS
                or current_size + item_size > MAX_GET_LIST_MESSAGE_SIZE
            )
        )
        if must_split:
            batches.append(tuple(current))
            current = []
            current_size = 7
        current.append(item)
        current_size += item_size

    if current:
        batches.append(tuple(current))
    return tuple(batches)
