"""Transparent-service selector used by the hardware-validated BLE path.

The leading ``0x01`` seen before CAN-IP v2.0 is a transport selector, not a
RUB sync/version pair.  RUB framing is a separate, currently unused layer.
"""

from __future__ import annotations

from openrbus.errors import ProtocolError

CANIP_SELECTOR = 0x01


def wrap_canip(message: bytes) -> bytes:
    """Prefix one CAN-IP message for the active transparent-service path."""

    if not message:
        raise ValueError("CAN-IP message must not be empty")
    return bytes((CANIP_SELECTOR,)) + message


def unwrap_canip(message: bytes) -> bytes:
    """Remove and validate the active CAN-IP selector."""

    if not message or message[0] != CANIP_SELECTOR:
        selector = message[0] if message else None
        rendered = "empty" if selector is None else f"0x{selector:02x}"
        raise ProtocolError(f"unsupported transparent-service selector {rendered}")
    if len(message) == 1:
        raise ProtocolError("selector has no CAN-IP payload")
    return message[1:]
