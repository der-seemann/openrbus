"""Public OpenRBus package facade."""

from .access import AsyncBulkObjectAccess, AsyncObjectAccess, ObjectRead, RawReadResult
from .client import OpenRBusClient, ReadFailure, ReadOutcome, ReadResult, WritePlan
from .discovery import (
    CapabilityReference,
    DeviceIdentity,
    assigned_nodes,
    discover_capabilities,
    discover_devices,
    identify_node,
)
from .errors import (
    AuthenticationError,
    CanOpenAbortError,
    ChecksumError,
    ConnectionFailedError,
    OpenRBusError,
    ProtocolError,
    RegistryError,
    RequestTimeoutError,
    SegmentationError,
    TransportError,
    UnknownRegisterError,
    UnsafeWriteError,
    ValidationError,
    WritesDisabledError,
    WriteVerificationError,
)
from .object_client import RawObjectClient
from .protocol import ObjectAddress
from .registry import RegisterDefinition, Registry, WireType, WriteSafety
from .value_codec import CanOpenTimeOfDay, StructuredValue

__all__ = [
    "AsyncBulkObjectAccess",
    "AsyncObjectAccess",
    "AuthenticationError",
    "CanOpenAbortError",
    "CanOpenTimeOfDay",
    "CapabilityReference",
    "ChecksumError",
    "ConnectionFailedError",
    "DeviceIdentity",
    "ObjectAddress",
    "ObjectRead",
    "OpenRBusClient",
    "OpenRBusError",
    "ProtocolError",
    "RawObjectClient",
    "RawReadResult",
    "ReadFailure",
    "ReadOutcome",
    "ReadResult",
    "RegisterDefinition",
    "Registry",
    "RegistryError",
    "RequestTimeoutError",
    "SegmentationError",
    "StructuredValue",
    "TransportError",
    "UnknownRegisterError",
    "UnsafeWriteError",
    "ValidationError",
    "WireType",
    "WritePlan",
    "WriteSafety",
    "WriteVerificationError",
    "WritesDisabledError",
    "assigned_nodes",
    "discover_capabilities",
    "discover_devices",
    "identify_node",
]
