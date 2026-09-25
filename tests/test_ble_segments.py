from __future__ import annotations

import pytest

from openrbus.errors import ChecksumError, SegmentationError
from openrbus.protocol.ble_segments import BleSegmentCodec, BleSegmentReassembler
from openrbus.protocol.crc import crc16_modbus_bytes


def test_known_synthetic_single_segment_crc() -> None:
    message = bytes.fromhex("0102000000000001200102")
    segment = BleSegmentCodec().encode(message)
    assert segment == (b"\xff" + message + crc16_modbus_bytes(message),)
    assert BleSegmentReassembler().feed(segment[0]) == message


def test_multisegment_round_trip_and_markers() -> None:
    message = bytes(range(80))
    segments = BleSegmentCodec().encode(message)
    assert [item[0] for item in segments[:-1]] == list(range(len(segments) - 1))
    assert segments[-1][0] == 0xFF
    reassembler = BleSegmentReassembler()
    results = [reassembler.feed(item) for item in segments]
    assert results[-1] == message
    assert all(item is None for item in results[:-1])


def test_reassembler_rejects_bad_order_and_crc() -> None:
    segments = BleSegmentCodec().encode(bytes(range(40)))
    reassembler = BleSegmentReassembler()
    with pytest.raises(SegmentationError):
        reassembler.feed(bytes((1,)) + segments[0][1:])

    damaged = bytearray(BleSegmentCodec().encode(b"payload")[0])
    damaged[-1] ^= 0x01
    with pytest.raises(ChecksumError):
        reassembler.feed(bytes(damaged))
