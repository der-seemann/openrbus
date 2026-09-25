"""Transport-neutral fixed BLE gateway service authorization helpers."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Any

from openrbus.errors import TransportError

GatewaySubscribe = Callable[[str, str], Awaitable[None]]
GatewayWrite = Callable[[str, bytes, str], Awaitable[bytes | None]]
GatewayWait = Callable[[str, float], Awaitable[bytes]]


def compute_gateway_auth_payload(identification: bytes) -> bytes:
    """Build the capture-validated gateway authorization payload.

    The gateway service handshake is distinct from pairing and from CAN-IP
    purpose-2 authorization.  It uses the eight-byte identity notification as
    its two runtime TEA words and a fixed level marker of ``5``.
    """

    if len(identification) != 8:
        raise ValueError("gateway identification response must contain exactly 8 bytes")
    k0 = 368026687
    k1 = int.from_bytes(identification[:4], "little")
    k2 = int.from_bytes(identification[4:], "little")
    value_0 = value_1 = 5
    total = 0
    for _ in range(32):
        total = (total + 0x9E3779B9) & 0xFFFFFFFF
        value_0 = (
            value_0 + (((value_1 << 4) + k0) ^ (value_1 + total) ^ ((value_1 >> 5) + k1))
        ) & 0xFFFFFFFF
        value_1 = (
            value_1 + (((value_0 << 4) + k2) ^ (value_0 + total) ^ ((value_0 >> 5) + 5))
        ) & 0xFFFFFFFF
    return bytes((5,)) + value_0.to_bytes(4, "little") + value_1.to_bytes(4, "little")


def gateway_auth_succeeded(response: bytes) -> bool:
    """Return whether a gateway service authorization was accepted."""

    return response == b"\x01"


async def authenticate_gateway(
    *,
    subscribe: GatewaySubscribe,
    write: GatewayWrite,
    wait: GatewayWait,
    epoch: Callable[[], Any],
    timeout: float,
    identity_role: str = "identity",
    identity_response_role: str = "identity_notify",
    identity_cccd_role: str = "identity_cccd",
    auth_role: str = "auth",
    auth_response_role: str = "auth_notify",
    auth_cccd_role: str = "auth_cccd",
) -> None:
    """Run the fixed gateway exchange using transport callbacks.

    Pairing is deliberately not part of this exchange.  Adapters provide the
    current-epoch ATT operations and notification waiters; this function owns
    only the canonical order and protocol validation shared by Native BLE and
    Thin-RPC.
    """

    if timeout <= 0:
        raise ValueError("gateway authorization timeout must be positive")
    start_epoch = epoch()
    await subscribe(identity_response_role, identity_cccd_role)
    await subscribe(auth_response_role, auth_cccd_role)
    if epoch() != start_epoch:
        raise TransportError("gateway authorization epoch changed before challenge")
    challenge = await write(identity_role, b"", identity_response_role)
    if challenge is None:
        challenge = await wait(identity_response_role, timeout)
    if len(challenge) != 8:
        raise TransportError("gateway identification response is invalid")
    if epoch() != start_epoch:
        raise TransportError("gateway authorization epoch changed after challenge")
    result = await write(
        auth_role,
        compute_gateway_auth_payload(challenge),
        auth_response_role,
    )
    if result is None:
        result = await wait(auth_response_role, timeout)
    if not gateway_auth_succeeded(result):
        raise TransportError("gateway authorization was rejected")
    if epoch() != start_epoch:
        raise TransportError("gateway authorization epoch changed after response")


__all__ = [
    "authenticate_gateway",
    "compute_gateway_auth_payload",
    "gateway_auth_succeeded",
]
