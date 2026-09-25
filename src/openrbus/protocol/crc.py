"""Checksums used by the validated BLE and RUB framing layers."""

from __future__ import annotations


def crc16_modbus(data: bytes, *, initial: int = 0xFFFF) -> int:
    """Return CRC-16/Modbus (poly ``0xA001``, initial ``0xFFFF``).

    The protocol places this numeric CRC on the wire in big-endian order,
    unlike the conventional Modbus RTU byte order.  Use
    :func:`crc16_modbus_bytes` for that representation.
    """

    crc = initial & 0xFFFF
    for byte in data:
        crc ^= byte
        for _ in range(8):
            crc = (crc >> 1) ^ 0xA001 if crc & 1 else crc >> 1
    return crc & 0xFFFF


def crc16_modbus_bytes(data: bytes) -> bytes:
    """Return the protocol's big-endian CRC-16/Modbus representation."""

    return crc16_modbus(data).to_bytes(2, "big")
