from __future__ import annotations

from pathlib import Path

import pytest

from openrbus.authorization import (
    EFFECTIVE_LEVEL_OBJECT,
    FREE_CHANNEL_OBJECT,
    RESPONSE_WORD_0_OBJECT,
    RESPONSE_WORD_1_OBJECT,
    SERIAL_NUMBER_OBJECT,
    TARGET_LEVEL_OBJECT,
    TOKEN_OBJECT,
    CanIpGatewayAuthorizer,
    NodeAuthorizer,
    TeaKeyComponent,
    compute_authentication_response,
)
from openrbus.errors import (
    AccessPolicyError,
    AuthorizationKeyError,
    AuthorizationUnsupportedError,
    NodeAuthorizationError,
)
from openrbus.protocol.canip import ObjectAddress
from openrbus.registry import AccessLevel

DUMMY_KEY_BYTES = bytes.fromhex("11223344")


class FakeMessageTransport:
    def __init__(self) -> None:
        self.responses = [
            bytes.fromhex("010200000201000011223344556677"),
            bytes.fromhex("01020000020300ff03ff01"),
        ]
        self.requests: list[bytes] = []
        self.is_connected = True

    async def connect(self) -> None:
        self.is_connected = True

    async def disconnect(self) -> None:
        self.is_connected = False

    async def request(self, message: bytes, *, timeout: float) -> bytes:
        self.requests.append(message)
        return self.responses.pop(0)


class FakeAuthorizationAccess:
    def __init__(self, *, effective: int = 3) -> None:
        self.effective = effective
        self.events: list[tuple[object, ...]] = []
        self.values = {
            FREE_CHANNEL_OBJECT: b"\x05",
            SERIAL_NUMBER_OBJECT: bytes.fromhex("78563412"),
            TOKEN_OBJECT: bytes.fromhex("efcdab90"),
            EFFECTIVE_LEVEL_OBJECT: bytes((effective,)),
        }

    async def read_raw(
        self, node: int, address: ObjectAddress, *, timeout: float | None = None
    ) -> bytes:
        self.events.append(("read", node, address))
        return self.values[address]

    async def _write_raw(
        self,
        node: int,
        address: ObjectAddress,
        raw_value: bytes,
        *,
        timeout: float | None = None,
    ) -> None:
        self.events.append(("write", node, address, raw_value))

    async def select_authorization_channel(
        self,
        node: int,
        channel: int,
        *,
        timeout: float | None = None,
    ) -> None:
        self.events.append(("channel", node, channel))


class PlainAccess:
    async def read_raw(
        self, node: int, address: ObjectAddress, *, timeout: float | None = None
    ) -> bytes:
        raise AssertionError("unsupported adapter must be rejected before I/O")

    async def _write_raw(
        self,
        node: int,
        address: ObjectAddress,
        raw_value: bytes,
        *,
        timeout: float | None = None,
    ) -> None:
        raise AssertionError("unsupported adapter must be rejected before I/O")


def test_tea_response_has_stable_synthetic_vector_and_redacted_repr() -> None:
    key = TeaKeyComponent.from_bytes(DUMMY_KEY_BYTES)
    response = compute_authentication_response(
        AccessLevel.PROFESSIONAL,
        0x12345678,
        0x90ABCDEF,
        key,
    )
    assert response == (0xA51A3FCF, 0x72D77867)
    assert DUMMY_KEY_BYTES.hex() not in repr(key)


@pytest.mark.asyncio
async def test_canip_gateway_authorizer_uses_network_order_and_redacts_result() -> None:
    transport = FakeMessageTransport()
    result = await CanIpGatewayAuthorizer(transport, max_access_level=3).authorize(
        3,
        key_component=DUMMY_KEY_BYTES,
    )
    assert result.authorization_level == 3
    assert transport.requests[0] == bytes.fromhex("01020000020000")
    expected_0, expected_1 = compute_authentication_response(
        3,
        0x00112233,
        0x44556677,
        TeaKeyComponent.from_bytes(DUMMY_KEY_BYTES),
    )
    assert transport.requests[1] == (
        bytes.fromhex("01020000020200ff03")
        + expected_0.to_bytes(4, "big")
        + expected_1.to_bytes(4, "big")
    )
    assert DUMMY_KEY_BYTES.hex() not in repr(result)


def test_key_loaders_require_explicit_valid_external_input(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    raw_path = tmp_path / "dummy.key"
    raw_path.write_bytes(DUMMY_KEY_BYTES)
    hex_path = tmp_path / "dummy.hex"
    hex_path.write_text("11223344\n", encoding="ascii")
    assert TeaKeyComponent.from_file(raw_path) == TeaKeyComponent.from_bytes(DUMMY_KEY_BYTES)
    assert TeaKeyComponent.from_file(hex_path) == TeaKeyComponent.from_bytes(DUMMY_KEY_BYTES)

    monkeypatch.setenv("TEST_OPENRBUS_KEY_PATH", str(hex_path))
    assert TeaKeyComponent.from_env_path("TEST_OPENRBUS_KEY_PATH") == TeaKeyComponent.from_bytes(
        DUMMY_KEY_BYTES
    )
    with pytest.raises(AuthorizationKeyError) as caught:
        TeaKeyComponent.from_bytes(DUMMY_KEY_BYTES + b"do-not-print")
    assert DUMMY_KEY_BYTES.hex() not in str(caught.value)


@pytest.mark.asyncio
async def test_authorizer_runs_official_sequence_and_verifies_effective_level() -> None:
    access = FakeAuthorizationAccess()
    result = await NodeAuthorizer(access, max_access_level=3).authorize(
        4,
        AccessLevel.PROFESSIONAL,
        key_component=DUMMY_KEY_BYTES,
    )
    assert result.node == 4
    assert result.channel == 5
    assert result.effective_level is AccessLevel.PROFESSIONAL
    assert access.events == [
        ("read", 4, FREE_CHANNEL_OBJECT),
        ("channel", 4, 5),
        ("read", 4, SERIAL_NUMBER_OBJECT),
        ("read", 4, TOKEN_OBJECT),
        ("write", 4, TARGET_LEVEL_OBJECT, b"\x03"),
        ("write", 4, RESPONSE_WORD_0_OBJECT, bytes.fromhex("cf3f1aa5")),
        ("write", 4, RESPONSE_WORD_1_OBJECT, bytes.fromhex("6778d772")),
        ("read", 4, EFFECTIVE_LEVEL_OBJECT),
    ]


@pytest.mark.asyncio
async def test_authorizer_requires_channel_capability_before_io() -> None:
    with pytest.raises(AuthorizationUnsupportedError, match="cannot select"):
        await NodeAuthorizer(PlainAccess(), max_access_level=3).authorize(
            4,
            AccessLevel.PROFESSIONAL,
            key_component=DUMMY_KEY_BYTES,
        )


@pytest.mark.asyncio
async def test_authorizer_rejects_mismatching_effective_level() -> None:
    with pytest.raises(
        NodeAuthorizationError,
        match="requested level 3, verified effective level 1",
    ):
        await NodeAuthorizer(FakeAuthorizationAccess(effective=1), max_access_level=3).authorize(
            4,
            AccessLevel.PROFESSIONAL,
            key_component=DUMMY_KEY_BYTES,
        )


@pytest.mark.asyncio
async def test_authorizer_requires_exactly_one_key_source(tmp_path: Path) -> None:
    path = tmp_path / "dummy.key"
    path.write_bytes(DUMMY_KEY_BYTES)
    with pytest.raises(AuthorizationKeyError, match="exactly one"):
        await NodeAuthorizer(FakeAuthorizationAccess(), max_access_level=3).authorize(
            4,
            AccessLevel.PROFESSIONAL,
            key_component=DUMMY_KEY_BYTES,
            key_path=path,
        )


@pytest.mark.asyncio
async def test_authorizers_default_to_user_only_before_io() -> None:
    access = FakeAuthorizationAccess()
    with pytest.raises(AccessPolicyError, match=r"required level 3.*maximum 1"):
        await NodeAuthorizer(access).authorize(
            4,
            AccessLevel.PROFESSIONAL,
            key_component=DUMMY_KEY_BYTES,
        )
    assert access.events == []

    transport = FakeMessageTransport()
    with pytest.raises(AccessPolicyError, match=r"required level 3.*maximum 1"):
        await CanIpGatewayAuthorizer(transport).authorize(
            3,
            key_component=DUMMY_KEY_BYTES,
        )
    assert transport.requests == []
