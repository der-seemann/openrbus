from __future__ import annotations

import pytest

from openrbus.errors import ChecksumError, ProtocolError
from openrbus.protocol.rub import RubFrame, header_checksum


def test_nonsegmented_rub_round_trip_uses_separate_header_and_message_checksums() -> None:
    frame = RubFrame(payload_id=1, payload=b"synthetic-canip")
    encoded = frame.encode()
    assert encoded[0:2] == b"\x01\x02"
    assert encoded[5] == header_checksum(encoded[1:5])
    assert RubFrame.decode(encoded) == frame


def test_statically_derived_synthetic_rub_vector() -> None:
    assert RubFrame(payload_id=1, payload=bytes.fromhex("020000000000")).encode().hex() == (
        "0102001006e70200000000005ad6"
    )


def test_rub_rejects_segmented_flag_and_corruption() -> None:
    encoded = bytearray(RubFrame(payload_id=1, payload=b"payload").encode())
    encoded[3] |= 0x08
    with pytest.raises(ProtocolError, match="segmented"):
        RubFrame.decode(bytes(encoded))

    encoded = bytearray(RubFrame(payload_id=1, payload=b"payload").encode())
    encoded[-1] ^= 1
    with pytest.raises(ChecksumError, match="message CRC"):
        RubFrame.decode(bytes(encoded))
