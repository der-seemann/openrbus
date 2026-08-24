from __future__ import annotations

import pytest

from openrbus.errors import CanOpenAbortError, ProtocolError
from openrbus.protocol.canip import (
    CanIpMessage,
    GenericFunction,
    ObjectAddress,
    build_batch_read,
    build_read,
    build_write,
    parse_batch_response,
    parse_read_response,
)
from openrbus.protocol.selector import unwrap_canip, wrap_canip


def test_address_and_read_round_trip() -> None:
    address = ObjectAddress.parse("1234:05")
    request = build_read(3, address)
    assert request.encode() == bytes.fromhex("02000000000003123405")
    assert CanIpMessage.decode(request.encode()) == request
    assert unwrap_canip(wrap_canip(request.encode())) == request.encode()


def test_positive_read_response_is_correlated() -> None:
    address = ObjectAddress(0x1234, 5)
    message = CanIpMessage(GenericFunction.READ_POSITIVE, b"\x03" + address.wire + b"\x10\x20")
    parsed = parse_read_response(CanIpMessage.decode(message.encode()), 3, address)
    assert parsed.raw_value == b"\x10\x20"
    with pytest.raises(ProtocolError, match="correlation"):
        parse_read_response(message, 4, address)


def test_write_builder_is_pure_and_batch_layout_is_bounded() -> None:
    address = ObjectAddress(0x2000, 1)
    write = build_write(1, address, b"\x01\x02")
    assert write.function == GenericFunction.WRITE
    assert write.payload == b"\x01" + address.wire + b"\x01\x02"
    batch = build_batch_read(1, (address, ObjectAddress(0x2001, 2)))
    assert batch.encode()[6:] == bytes.fromhex("0200012000010001200102")
    with pytest.raises(ValueError):
        build_batch_read(1, ())
    with pytest.raises(ValueError, match=r"1\.\.100"):
        build_batch_read(1, (address,) * 101)


def test_non_generic_or_unknown_messages_are_rejected() -> None:
    with pytest.raises(ProtocolError, match="generic-purpose"):
        CanIpMessage.decode(bytes.fromhex("02000002000000000000"))
    with pytest.raises(ProtocolError, match="unsupported transparent-service selector"):
        unwrap_canip(b"\x02payload")


def test_negative_response_exposes_little_endian_abort_code() -> None:
    address = ObjectAddress(0x1234, 5)
    response = CanIpMessage(
        GenericFunction.READ_NEGATIVE,
        b"\x03" + address.wire + bytes.fromhex("00000206"),
    )
    with pytest.raises(CanOpenAbortError) as error:
        parse_read_response(response, 3, address)
    assert error.value.code == 0x06020000


def test_batch_response_supports_variable_value_length() -> None:
    address = ObjectAddress(0x1234, 5)
    entry = b"\x01\x03" + address.wire + b"\x00\x00\x02\xaa\xbb"
    message = CanIpMessage(GenericFunction.GET_LIST_RESPONSE, b"\x01" + entry)
    parsed = parse_batch_response(CanIpMessage.decode(message.encode()))
    assert len(parsed) == 1
    assert parsed[0].reserved == 0
    assert parsed[0].value == b"\xaa\xbb"


def test_batch_response_parses_validated_layout_and_abort_entry() -> None:
    synthetic = bytes.fromhex("020000000900020101123405000003aabbcc01022001020000017f")
    parsed = parse_batch_response(CanIpMessage.decode(synthetic))
    assert [(entry.node, str(entry.address), entry.value) for entry in parsed] == [
        (1, "1234:05", b"\xaa\xbb\xcc"),
        (2, "2001:02", b"\x7f"),
    ]

    address = ObjectAddress(0x1234, 5)
    abort = b"\x02\x03" + address.wire + b"\x00\x00\x04\x00\x00\x02\x06"
    parsed_abort = parse_batch_response(
        CanIpMessage(GenericFunction.GET_LIST_RESPONSE, b"\x01" + abort)
    )
    assert parsed_abort[0].abort_code == 0x06020000
