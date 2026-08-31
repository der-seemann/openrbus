"""Explicit, externally keyed CANopen node authorization.

The module contains no manufacturer key material and has no implicit key
location. Applications must supply the four-byte vendor key component for
each explicit authorization request.
"""

from __future__ import annotations

import os
import time
from dataclasses import dataclass, field
from pathlib import Path

from openrbus.access import AsyncNodeAuthorizationAccess, AsyncObjectAccess
from openrbus.access_policy import AccessPolicy, resolve_access_policy
from openrbus.errors import (
    AuthorizationKeyError,
    AuthorizationUnsupportedError,
    NodeAuthorizationError,
)
from openrbus.protocol.canip import ObjectAddress
from openrbus.protocol.selector import unwrap_canip, wrap_canip
from openrbus.registry import AccessLevel
from openrbus.transport.base import AsyncMessageTransport

SERIAL_NUMBER_OBJECT = ObjectAddress(0x2001, 0x0A)
TOKEN_OBJECT = ObjectAddress(0x4001, 0x00)
EFFECTIVE_LEVEL_OBJECT = ObjectAddress(0x4002, 0x00)
RESPONSE_WORD_0_OBJECT = ObjectAddress(0x4003, 0x01)
RESPONSE_WORD_1_OBJECT = ObjectAddress(0x4003, 0x02)
TARGET_LEVEL_OBJECT = ObjectAddress(0x4003, 0x03)
FREE_CHANNEL_OBJECT = ObjectAddress(0x4004, 0x00)

_UINT32_MASK = 0xFFFFFFFF
_TEA_DELTA = 0x9E3779B9
_CANIP_AUTHORIZATION_PURPOSE = 2
_IDENTIFICATION_REQUEST = 0
_IDENTIFICATION_RESPONSE = 1
_AUTHORIZATION_REQUEST = 2
_AUTHORIZATION_RESPONSE = 3
_AUTHORIZATION_SUCCESS = b"\xff\x01"


@dataclass(frozen=True, slots=True)
class TeaKeyComponent:
    """Four-byte vendor key component kept out of repr and error messages."""

    _word: int = field(repr=False)

    @classmethod
    def from_bytes(cls, value: bytes) -> TeaKeyComponent:
        """Create key material from exactly four runtime-supplied bytes."""

        if len(value) != 4:
            raise AuthorizationKeyError("the external TEA key component must be exactly 4 bytes")
        # External material uses the conventional hexadecimal/byte order of
        # the uint32 key component. CANopen response words themselves remain
        # little-endian on the wire.
        return cls(int.from_bytes(value, "big"))

    @classmethod
    def from_file(cls, path: str | os.PathLike[str]) -> TeaKeyComponent:
        """Load four raw bytes or eight ASCII hexadecimal digits from an explicit path."""

        try:
            raw = Path(path).read_bytes()
        except OSError as error:
            raise AuthorizationKeyError("could not read the external TEA key file") from error
        if len(raw) == 4:
            return cls.from_bytes(raw)
        try:
            text = raw.decode("ascii").strip()
            if len(text) != 8:
                raise ValueError
            decoded = bytes.fromhex(text)
        except (UnicodeDecodeError, ValueError) as error:
            raise AuthorizationKeyError(
                "the external TEA key file must contain 4 raw bytes or 8 hexadecimal digits"
            ) from error
        return cls.from_bytes(decoded)

    @classmethod
    def from_env_path(cls, variable: str) -> TeaKeyComponent:
        """Load from the path named by an explicitly selected environment variable."""

        path = os.environ.get(variable)
        if not path:
            raise AuthorizationKeyError(
                "the selected TEA key-path environment variable is unset or empty"
            )
        return cls.from_file(path)

    @property
    def _value(self) -> int:
        return self._word


@dataclass(frozen=True, slots=True)
class NodeAuthorizationResult:
    """Verified effective access resulting from one explicit authorization."""

    node: int
    channel: int
    effective_level: AccessLevel
    sampled_at: float = field(repr=False)


@dataclass(frozen=True, slots=True)
class GatewayAuthorizationResult:
    """One successful CAN-IP gateway authorization exchange.

    This result proves only that the gateway accepted the CAN-IP
    challenge-response. Per-node access remains authoritative only after
    reading ``4002:00`` on the target node.
    """

    authorization_level: int
    sampled_at: float = field(repr=False)


class CanIpGatewayAuthorizer:
    """Authorize the active BLE/CAN-IP transport with external key material.

    CAN-IP authorization uses the same TEA inputs as node authorization, but
    serial, token and response words are network-byte-order values. No
    challenge input or response is logged, retained or exposed in the result.
    """

    def __init__(
        self,
        transport: AsyncMessageTransport,
        *,
        access_policy: AccessPolicy | None = None,
        max_access_level: AccessLevel | int | None = None,
    ) -> None:
        self._transport = transport
        self.access_policy = resolve_access_policy(access_policy, max_access_level)

    async def authorize(
        self,
        authorization_level: int,
        *,
        key_component: TeaKeyComponent | bytes | None = None,
        key_path: str | os.PathLike[str] | None = None,
        key_path_env: str | None = None,
        node: int = 0xFF,
        timeout: float = 10.0,
    ) -> GatewayAuthorizationResult:
        """Run the capture-validated CAN-IP purpose-2 exchange explicitly."""

        if not 1 <= authorization_level <= 0xFF:
            raise ValueError("authorization_level must be 0x01..0xff")
        if not 1 <= node <= 0xFF:
            raise ValueError("node must be 0x01..0xff")
        if timeout <= 0:
            raise ValueError("timeout must be positive")
        self.access_policy.require_level(authorization_level, "write")
        key = _resolve_key(key_component, key_path, key_path_env)

        identification = await self._transport.request(
            wrap_canip(_encode_authorization_message(_IDENTIFICATION_REQUEST)),
            timeout=timeout,
        )
        payload = _parse_authorization_message(
            unwrap_canip(identification),
            expected_function=_IDENTIFICATION_RESPONSE,
        )
        if len(payload) != 8:
            raise NodeAuthorizationError(
                "the CAN-IP identification response must contain exactly 8 bytes"
            )
        serial = int.from_bytes(payload[:4], "big")
        token = int.from_bytes(payload[4:], "big")
        response_0, response_1 = compute_authentication_response(
            authorization_level,
            serial,
            token,
            key,
        )
        response = response_0.to_bytes(4, "big") + response_1.to_bytes(4, "big")
        confirmation = await self._transport.request(
            wrap_canip(
                _encode_authorization_message(
                    _AUTHORIZATION_REQUEST,
                    bytes((node, authorization_level)) + response,
                )
            ),
            timeout=timeout,
        )
        confirmation_payload = _parse_authorization_message(
            unwrap_canip(confirmation),
            expected_function=_AUTHORIZATION_RESPONSE,
        )
        if len(confirmation_payload) != 4:
            raise NodeAuthorizationError("the CAN-IP authorization response has invalid length")
        if confirmation_payload[:2] != bytes((node, authorization_level)):
            raise NodeAuthorizationError("the CAN-IP authorization response does not correlate")
        if confirmation_payload[2:] != _AUTHORIZATION_SUCCESS:
            raise NodeAuthorizationError("the CAN-IP gateway rejected authorization")
        return GatewayAuthorizationResult(authorization_level, time.monotonic())


class NodeAuthorizer:
    """Perform the official per-node CANopen challenge-response sequence."""

    def __init__(
        self,
        access: AsyncObjectAccess,
        *,
        access_policy: AccessPolicy | None = None,
        max_access_level: AccessLevel | int | None = None,
    ) -> None:
        self._access = access
        self.access_policy = resolve_access_policy(access_policy, max_access_level)

    async def authorize(
        self,
        node: int,
        target_level: AccessLevel,
        *,
        key_component: TeaKeyComponent | bytes | None = None,
        key_path: str | os.PathLike[str] | None = None,
        key_path_env: str | None = None,
        timeout: float | None = None,
    ) -> NodeAuthorizationResult:
        """Explicitly authorize one node and verify its effective level.

        Exactly one key source is required. ``key_path_env`` names an
        environment variable whose value is a path; it never carries or logs
        the key itself. No default path or environment-variable name exists.
        """

        if not 1 <= node <= 0xFF:
            raise ValueError("node must be 0x01..0xff")
        if target_level not in (
            AccessLevel.USER,
            AccessLevel.INSTALLER,
            AccessLevel.PROFESSIONAL,
        ):
            raise ValueError("node authorization supports access levels 1..3")
        self.access_policy.require_level(target_level, "write")
        key = _resolve_key(key_component, key_path, key_path_env)
        if not isinstance(self._access, AsyncNodeAuthorizationAccess):
            raise AuthorizationUnsupportedError(
                "the object adapter cannot select a node authorization channel"
            )

        channel_raw = await self._access.read_raw(node, FREE_CHANNEL_OBJECT, timeout=timeout)
        channel = _decode_exact_uint(channel_raw, 1, "free authorization channel")
        if channel == 0:
            raise NodeAuthorizationError("the device reported no free authorization channel")
        await self._access.select_authorization_channel(node, channel, timeout=timeout)

        serial_raw = await self._access.read_raw(node, SERIAL_NUMBER_OBJECT, timeout=timeout)
        token_raw = await self._access.read_raw(node, TOKEN_OBJECT, timeout=timeout)
        serial = _decode_exact_uint(serial_raw, 4, "node serial number")
        token = _decode_exact_uint(token_raw, 4, "authorization token")
        response_0, response_1 = compute_authentication_response(
            target_level,
            serial,
            token,
            key,
        )

        await self._access._write_raw(
            node, TARGET_LEVEL_OBJECT, bytes((int(target_level),)), timeout=timeout
        )
        await self._access._write_raw(
            node, RESPONSE_WORD_0_OBJECT, response_0.to_bytes(4, "little"), timeout=timeout
        )
        await self._access._write_raw(
            node, RESPONSE_WORD_1_OBJECT, response_1.to_bytes(4, "little"), timeout=timeout
        )

        effective_raw = await self._access.read_raw(node, EFFECTIVE_LEVEL_OBJECT, timeout=timeout)
        effective_value = _decode_exact_uint(effective_raw, 1, "effective access level")
        try:
            effective = AccessLevel(effective_value)
        except ValueError as error:
            raise NodeAuthorizationError(
                "the device returned an unsupported effective access level"
            ) from error
        if effective is not target_level:
            raise NodeAuthorizationError(
                f"node authorization failed: requested level {int(target_level)}, "
                f"verified effective level {int(effective)}"
            )
        return NodeAuthorizationResult(node, channel, effective, time.monotonic())


def compute_authentication_response(
    target_level: AccessLevel | int,
    serial_number: int,
    token: int,
    key_component: TeaKeyComponent,
) -> tuple[int, int]:
    """Compute the two official TEA response words from synthetic/runtime inputs."""

    for label, value in (("serial_number", serial_number), ("token", token)):
        if not 0 <= value <= _UINT32_MASK:
            raise ValueError(f"{label} must be a uint32")
    level = int(target_level)
    if not 1 <= level <= 0xFF:
        raise ValueError("target_level must be 0x01..0xff")

    value_0 = level
    value_1 = level
    key = (key_component._value, serial_number, token, level)
    total = 0
    for _ in range(32):
        total = (total + _TEA_DELTA) & _UINT32_MASK
        value_0 = (
            value_0 + (((value_1 << 4) + key[0]) ^ (value_1 + total) ^ ((value_1 >> 5) + key[1]))
        ) & _UINT32_MASK
        value_1 = (
            value_1 + (((value_0 << 4) + key[2]) ^ (value_0 + total) ^ ((value_0 >> 5) + key[3]))
        ) & _UINT32_MASK
    return value_0, value_1


def _resolve_key(
    direct: TeaKeyComponent | bytes | None,
    path: str | os.PathLike[str] | None,
    path_env: str | None,
) -> TeaKeyComponent:
    selected = sum(value is not None for value in (direct, path, path_env))
    if selected != 1:
        raise AuthorizationKeyError("select exactly one external TEA key source")
    if direct is not None:
        return direct if isinstance(direct, TeaKeyComponent) else TeaKeyComponent.from_bytes(direct)
    if path is not None:
        return TeaKeyComponent.from_file(path)
    assert path_env is not None
    return TeaKeyComponent.from_env_path(path_env)


def _decode_exact_uint(raw: bytes, length: int, description: str) -> int:
    if len(raw) != length:
        raise NodeAuthorizationError(
            f"the {description} response has {len(raw)} bytes; expected {length}"
        )
    return int.from_bytes(raw, "little")


def _encode_authorization_message(function: int, payload: bytes = b"") -> bytes:
    return bytes((2, 0, 0, _CANIP_AUTHORIZATION_PURPOSE, function, 0)) + payload


def _parse_authorization_message(data: bytes, *, expected_function: int) -> bytes:
    if len(data) < 6:
        raise NodeAuthorizationError("the CAN-IP authorization response is too short")
    major, _minor, _line, purpose, function, _qos = data[:6]
    if major != 2 or purpose != _CANIP_AUTHORIZATION_PURPOSE:
        raise NodeAuthorizationError("the response is not a CAN-IP authorization message")
    if function != expected_function:
        raise NodeAuthorizationError("the CAN-IP authorization response function is unexpected")
    return data[6:]


__all__ = [
    "CanIpGatewayAuthorizer",
    "GatewayAuthorizationResult",
    "NodeAuthorizationResult",
    "NodeAuthorizer",
    "TeaKeyComponent",
    "compute_authentication_response",
]
