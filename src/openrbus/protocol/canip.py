"""CAN-IP 2.0 generic-purpose message codec.

The six-byte header layout and function numbers are derived from independent
interoperability analysis.  Read traffic is hardware-validated.  Write message
construction is available for validation and dry-run planning, but live writes
remain explicitly gated by :mod:`openrbus.client`.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import IntEnum

from openrbus.errors import CanOpenAbortError, ProtocolError

MAX_GET_LIST_OBJECTS = 100
MAX_GET_LIST_MESSAGE_SIZE = 1512


class GenericFunction(IntEnum):
    READ = 0
    READ_POSITIVE = 1
    READ_NEGATIVE = 2
    WRITE_UNCONFIRMED = 3
    WRITE = 4
    WRITE_POSITIVE = 5
    WRITE_NEGATIVE = 6
    GET_LIST = 8
    GET_LIST_RESPONSE = 9
    SUBSCRIBE = 10
    UNSUBSCRIBE = 14
    READ_WITH_PAYLOAD = 17


@dataclass(frozen=True, slots=True, order=True)
class ObjectAddress:
    """CANopen-style 16-bit index and 8-bit subindex."""

    index: int
    subindex: int = 0

    def __post_init__(self) -> None:
        if not 0 <= self.index <= 0xFFFF:
            raise ValueError("index must be 0x0000..0xffff")
        if not 0 <= self.subindex <= 0xFF:
            raise ValueError("subindex must be 0x00..0xff")

    @property
    def wire(self) -> bytes:
        """Return the validated index-high, index-low, subindex representation."""

        return self.index.to_bytes(2, "big") + bytes((self.subindex,))

    @classmethod
    def parse(cls, value: str) -> ObjectAddress:
        try:
            index, subindex = value.split(":", 1)
            return cls(int(index, 16), int(subindex, 16))
        except (ValueError, TypeError) as exc:
            raise ValueError("address must use hexadecimal hhhh:ss form") from exc

    def __str__(self) -> str:
        return f"{self.index:04x}:{self.subindex:02x}"


@dataclass(frozen=True, slots=True)
class CanIpMessage:
    """One generic-purpose CAN-IP 2.0 message."""

    function: GenericFunction
    payload: bytes = b""
    qos: int = 0
    line_id: int = 0
    major: int = 2
    minor: int = 0

    def __post_init__(self) -> None:
        for label, value in (("qos", self.qos), ("line_id", self.line_id)):
            if not 0 <= value <= 0xFF:
                raise ValueError(f"{label} must be a byte")
        if self.major != 2:
            raise ValueError("only validated CAN-IP major version 2 is supported")

    def encode(self) -> bytes:
        data = bytes((self.major, self.minor, self.line_id, 0, self.function, self.qos))
        data += self.payload
        return data.ljust(10, b"\x00")

    @classmethod
    def decode(cls, data: bytes) -> CanIpMessage:
        if len(data) < 10:
            raise ProtocolError("generic CAN-IP message is shorter than 10 bytes")
        major, minor, line_id, purpose, function, qos = data[:6]
        if major != 2:
            raise ProtocolError(f"unsupported CAN-IP major version {major}")
        if purpose != 0:
            raise ProtocolError("only generic-purpose CAN-IP messages are public/supported")
        try:
            parsed_function = GenericFunction(function)
        except ValueError as exc:
            raise ProtocolError(f"unknown generic CAN-IP function {function}") from exc
        return cls(parsed_function, data[6:], qos, line_id, major, minor)


def build_read(node: int, address: ObjectAddress, *, qos: int = 0) -> CanIpMessage:
    _validate_node(node)
    return CanIpMessage(GenericFunction.READ, bytes((node,)) + address.wire, qos=qos)


def build_write(
    node: int,
    address: ObjectAddress,
    raw_value: bytes,
    *,
    confirmed: bool = True,
    qos: int = 0,
) -> CanIpMessage:
    """Build a derived write message without transmitting it."""

    _validate_node(node)
    if not raw_value:
        raise ValueError("raw_value must not be empty")
    function = GenericFunction.WRITE if confirmed else GenericFunction.WRITE_UNCONFIRMED
    return CanIpMessage(function, bytes((node,)) + address.wire + raw_value, qos=qos)


def build_batch_read(node: int, addresses: tuple[ObjectAddress, ...]) -> CanIpMessage:
    """Build a hardware-validated function-8 ``GetList`` request.

    Each five-byte descriptor carries its own reserved byte, node, index, and
    subindex.  Current hardware evidence validates same-node lists of at most
    100 objects.
    """

    _validate_node(node)
    if not 1 <= len(addresses) <= MAX_GET_LIST_OBJECTS:
        raise ValueError(f"batch must contain 1..{MAX_GET_LIST_OBJECTS} addresses")
    payload = bytes((len(addresses),))
    payload += b"".join(b"\x00" + bytes((node,)) + address.wire for address in addresses)
    return CanIpMessage(GenericFunction.GET_LIST, payload)


@dataclass(frozen=True, slots=True)
class ReadResponse:
    node: int
    address: ObjectAddress
    raw_value: bytes


def parse_read_response(
    message: CanIpMessage, expected_node: int, expected: ObjectAddress
) -> ReadResponse:
    if message.function == GenericFunction.READ_NEGATIVE:
        raise _abort_error(message.payload, expected_node, expected, "read")
    if message.function != GenericFunction.READ_POSITIVE:
        raise ProtocolError(f"expected positive read response, received {message.function.name}")
    if len(message.payload) < 4:
        raise ProtocolError("positive read response has no object address")
    node = message.payload[0]
    address = ObjectAddress(int.from_bytes(message.payload[1:3], "big"), message.payload[3])
    if node != expected_node or address != expected:
        raise ProtocolError(
            f"read response correlation mismatch: node={node:02x}, address={address}"
        )
    return ReadResponse(node, address, message.payload[4:])


def validate_write_response(
    message: CanIpMessage, expected_node: int, expected: ObjectAddress
) -> None:
    """Validate the derived confirmed-write response envelope."""

    if message.function == GenericFunction.WRITE_NEGATIVE:
        raise _abort_error(message.payload, expected_node, expected, "write")
    if message.function != GenericFunction.WRITE_POSITIVE:
        raise ProtocolError(f"expected positive write response, received {message.function.name}")
    if len(message.payload) < 4:
        raise ProtocolError("positive write response has no object address")
    node = message.payload[0]
    address = ObjectAddress(int.from_bytes(message.payload[1:3], "big"), message.payload[3])
    if node != expected_node or address != expected:
        raise ProtocolError(
            f"write response correlation mismatch: node={node:02x}, address={address}"
        )


def _validate_node(node: int) -> None:
    if not 1 <= node <= 0xFF:
        raise ValueError("node must be 0x01..0xff")


_ABORT_DESCRIPTIONS = {
    0x05030000: "toggle bit not alternated",
    0x05040000: "SDO protocol timed out",
    0x06010000: "unsupported object access",
    0x06010001: "attempt to read a write-only object",
    0x06010002: "attempt to write a read-only object",
    0x06020000: "object does not exist",
    0x06040041: "object cannot be mapped",
    0x06070010: "datatype or length mismatch",
    0x06090030: "value range exceeded",
    0x08000000: "general error",
}


def _abort_error(
    payload: bytes, expected_node: int, expected: ObjectAddress, operation: str
) -> CanOpenAbortError:
    if len(payload) < 8:
        raise ProtocolError(f"negative {operation} response is too short")
    node = payload[0]
    address = ObjectAddress(int.from_bytes(payload[1:3], "big"), payload[3])
    if node != expected_node or address != expected:
        raise ProtocolError(
            f"negative {operation} response correlation mismatch: "
            f"node={node:02x}, address={address}"
        )
    code = int.from_bytes(payload[4:8], "little")
    description = _ABORT_DESCRIPTIONS.get(code, f"unknown CANopen {operation} abort")
    return CanOpenAbortError(code, description)


@dataclass(frozen=True, slots=True)
class BatchEntry:
    status: int
    node: int
    address: ObjectAddress
    reserved: int
    value: bytes

    @property
    def abort_code(self) -> int | None:
        """Return the little-endian abort code carried by a status-2 entry."""

        if self.status != 2 or len(self.value) < 4:
            return None
        return int.from_bytes(self.value[:4], "little")


def parse_batch_response(message: CanIpMessage) -> tuple[BatchEntry, ...]:
    """Parse capture-validated function-9 entries with variable value lengths."""

    if message.function != GenericFunction.GET_LIST_RESPONSE:
        raise ProtocolError("expected function-9 batch response")
    if not message.payload:
        raise ProtocolError("batch response has no entry count")
    count = message.payload[0]
    data = message.payload[1:]
    entries: list[BatchEntry] = []
    position = 0
    for _ in range(count):
        if len(data) - position < 8:
            raise ProtocolError("truncated batch response entry")
        status = data[position]
        node = data[position + 1]
        address = ObjectAddress(
            int.from_bytes(data[position + 2 : position + 4], "big"), data[position + 4]
        )
        reserved = data[position + 5]
        length = int.from_bytes(data[position + 6 : position + 8], "big")
        position += 8
        if len(data) - position < length:
            raise ProtocolError("batch response value is shorter than declared")
        value = data[position : position + length]
        position += length
        entries.append(BatchEntry(status, node, address, reserved, value))
    if any(data[position:]):
        raise ProtocolError("batch response has non-padding trailing bytes")
    return tuple(entries)
