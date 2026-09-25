"""Statically derived non-segmented RUB v2 frame codec.

RUB is not part of the hardware-validated transparent BLE/CAN-IP path.  This
codec exists for future adapters and offline analysis.  Segmented RUB receive
behavior is deliberately unsupported because the inspected vendor receiver is
internally inconsistent and no independent live vectors establish it.
"""

from __future__ import annotations

from dataclasses import dataclass

from openrbus.errors import ChecksumError, ProtocolError

from .crc import crc16_modbus_bytes

SYNC = 0x01
VERSION = 0x02
MAX_SEGMENT_PAYLOAD = 1600


def header_checksum(data: bytes) -> int:
    """Return the 8-bit one's complement end-around-carry checksum."""

    total = sum(data)
    while total > 0xFF:
        total = (total & 0xFF) + (total >> 8)
    return (~total) & 0xFF


@dataclass(frozen=True, slots=True)
class RubFrame:
    """One non-segmented RUB v2 frame."""

    payload_id: int
    payload: bytes
    reserved: int = 0

    def __post_init__(self) -> None:
        if not 0 <= self.payload_id <= 0x0F:
            raise ValueError("payload_id must be 0..15")
        if not 0 <= self.reserved <= 0xFF:
            raise ValueError("reserved must be a byte")
        if len(self.payload) > MAX_SEGMENT_PAYLOAD:
            raise ValueError(f"non-segmented RUB payload exceeds {MAX_SEGMENT_PAYLOAD} bytes")

    def encode(self) -> bytes:
        length = len(self.payload)
        packed = (self.payload_id << 4) | ((length >> 8) & 0x07)
        header_without_checksum = bytes((VERSION, self.reserved, packed, length & 0xFF))
        header = header_without_checksum + bytes((header_checksum(header_without_checksum),))
        crc_input = header + self.payload
        return bytes((SYNC,)) + crc_input + crc16_modbus_bytes(crc_input)

    @classmethod
    def decode(cls, data: bytes) -> RubFrame:
        if len(data) < 8:
            raise ProtocolError("RUB frame is shorter than its header and CRC")
        if data[0] != SYNC or data[1] != VERSION:
            raise ProtocolError("unsupported RUB sync or version")
        reserved, packed, length, received_header_checksum = data[2:6]
        if packed & 0x08:
            raise ProtocolError("segmented RUB receive is not independently validated")
        header_without_checksum = data[1:5]
        expected_header_checksum = header_checksum(header_without_checksum)
        if received_header_checksum != expected_header_checksum:
            raise ChecksumError("RUB header checksum mismatch")
        payload_length = ((packed & 0x07) << 8) | length
        expected_size = 6 + payload_length + 2
        if len(data) != expected_size:
            raise ProtocolError(
                f"RUB frame length {len(data)} does not match declared {expected_size}"
            )
        crc_input, received_crc = data[1:-2], data[-2:]
        if crc16_modbus_bytes(crc_input) != received_crc:
            raise ChecksumError("RUB message CRC mismatch")
        return cls(packed >> 4, data[6:-2], reserved)
