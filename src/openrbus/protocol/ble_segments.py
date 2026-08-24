"""Validated 20-byte BLE application segmentation.

Each ATT value carries a one-byte marker followed by at most 19 payload bytes.
Non-final markers count from ``0x00`` through ``0x7f``.  The final marker is
``0xff`` and the final two reassembled bytes are a big-endian CRC-16/Modbus of
the message before segmentation.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from openrbus.errors import ChecksumError, SegmentationError

from .crc import crc16_modbus_bytes


@dataclass(frozen=True, slots=True)
class BleSegmentCodec:
    """Fragment complete messages into BLE characteristic values."""

    mtu: int = 20

    def encode(self, message: bytes) -> tuple[bytes, ...]:
        """Return ordered BLE segments including the framing CRC."""

        if self.mtu < 4:
            raise ValueError("MTU must leave room for marker, data, and CRC")
        wire = message + crc16_modbus_bytes(message)
        chunk_size = self.mtu - 1
        chunks = [wire[pos : pos + chunk_size] for pos in range(0, len(wire), chunk_size)]
        if not chunks:
            chunks = [wire]
        segments: list[bytes] = []
        for index, chunk in enumerate(chunks):
            final = index == len(chunks) - 1
            marker = 0xFF if final else index & 0x7F
            segments.append(bytes((marker,)) + chunk)
        return tuple(segments)


@dataclass(slots=True)
class BleSegmentReassembler:
    """Stateful receiver for one ordered BLE message at a time."""

    mtu: int = 20
    _buffer: bytearray = field(default_factory=bytearray, init=False, repr=False)
    _expected: int = field(default=0, init=False, repr=False)

    def reset(self) -> None:
        """Discard a partial message."""

        self._buffer.clear()
        self._expected = 0

    def feed(self, segment: bytes) -> bytes | None:
        """Consume one segment and return a complete verified message if final."""

        if not 1 <= len(segment) <= self.mtu:
            self.reset()
            raise SegmentationError(f"BLE segment length {len(segment)} is outside 1..{self.mtu}")
        marker, payload = segment[0], segment[1:]
        if marker != 0xFF:
            if marker != self._expected:
                expected = self._expected
                self.reset()
                raise SegmentationError(
                    f"expected BLE segment marker 0x{expected:02x}, received 0x{marker:02x}"
                )
            self._buffer.extend(payload)
            self._expected = (self._expected + 1) & 0x7F
            return None

        self._buffer.extend(payload)
        wire = bytes(self._buffer)
        self.reset()
        if len(wire) < 2:
            raise SegmentationError("final BLE segment does not contain a CRC")
        message, received_crc = wire[:-2], wire[-2:]
        expected_crc = crc16_modbus_bytes(message)
        if received_crc != expected_crc:
            raise ChecksumError(
                f"BLE CRC mismatch: expected {expected_crc.hex()}, received {received_crc.hex()}"
            )
        return message
