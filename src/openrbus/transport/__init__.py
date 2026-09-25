"""Transport contracts and connection management."""

from .base import AsyncMessageTransport
from .ble import BleakMessageTransport, discover_ble_devices
from .connection import ConnectionPolicy, ManagedTransport
from .thin_gatt import (
    ConnectionIdentity,
    GattHandles,
    GattNotification,
    ThinGattCapabilityError,
    ThinGattCorrelationError,
    ThinGattError,
    ThinGattFlowControlError,
    ThinGattLink,
    ThinGattMessageTransport,
    ThinGattProfile,
    ThinGattRpcChannel,
    ThinGattRpcServices,
    ThinGattSecureSession,
    ThinGattSession,
    ThinGattSessionStateError,
)

__all__ = [
    "AsyncMessageTransport",
    "BleakMessageTransport",
    "ConnectionIdentity",
    "ConnectionPolicy",
    "GattHandles",
    "GattNotification",
    "ManagedTransport",
    "ThinGattCapabilityError",
    "ThinGattCorrelationError",
    "ThinGattError",
    "ThinGattFlowControlError",
    "ThinGattLink",
    "ThinGattMessageTransport",
    "ThinGattProfile",
    "ThinGattRpcChannel",
    "ThinGattRpcServices",
    "ThinGattSecureSession",
    "ThinGattSession",
    "ThinGattSessionStateError",
    "discover_ble_devices",
]
