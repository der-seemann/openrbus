"""Public exception hierarchy for OpenRBus."""

from __future__ import annotations


class OpenRBusError(Exception):
    """Base class for all expected OpenRBus failures."""


class ProtocolError(OpenRBusError):
    """A received or generated protocol message is invalid."""


class CanOpenAbortError(ProtocolError):
    """A CAN-IP negative response carries a CANopen SDO abort code."""

    def __init__(self, code: int, message: str = "CANopen SDO request aborted") -> None:
        self.code = code
        super().__init__(f"{message} (0x{code:08x})")


class ChecksumError(ProtocolError):
    """A protocol checksum does not match its message."""


class SegmentationError(ProtocolError):
    """BLE or application segments are missing, reordered, or malformed."""


class TransportError(OpenRBusError):
    """The underlying transport failed."""


class ConnectionFailedError(TransportError):
    """Connection or reconnection attempts were exhausted."""


class RequestTimeoutError(TransportError):
    """A request did not receive its matching response in time."""


class AuthenticationError(TransportError):
    """Pairing or protocol authorization failed."""


class NodeAuthorizationError(OpenRBusError):
    """A node-level access-elevation operation failed safely."""


class AuthorizationKeyError(NodeAuthorizationError):
    """External node-authorization key material is missing or invalid."""


class AuthorizationUnsupportedError(NodeAuthorizationError):
    """The object adapter cannot select the allocated authorization channel."""


class RegistryError(OpenRBusError):
    """The register registry is missing or inconsistent."""


class UnknownRegisterError(RegistryError):
    """No register definition is available for the requested address."""


class ValidationError(OpenRBusError):
    """A value or operation violates a declared constraint."""


class WritesDisabledError(ValidationError):
    """A write was requested without explicit write opt-in."""


class UnsafeWriteError(ValidationError):
    """A critical or insufficiently verified write lacks unsafe opt-in."""


class AccessLevelError(ValidationError):
    """An operation cannot satisfy or resolve its declared device access level."""


class AccessPolicyError(AccessLevelError):
    """The caller-configured maximum access level blocks an operation."""


class AccessLevelAmbiguityError(AccessLevelError):
    """Several device definitions require different access levels."""


class AccessLevelUnavailableError(AccessLevelError):
    """The live session access level could not be read or represented safely."""


class InsufficientAccessLevelError(AccessLevelError):
    """The live session's verified effective level is too low."""

    def __init__(self, address: object, required: int, effective: int) -> None:
        self.address = address
        self.required = required
        self.effective = effective
        super().__init__(
            f"register {address} requires access level {required}, "
            f"but node authorization is verified only at effective level {effective}"
        )


class WriteVerificationError(ValidationError):
    """A confirmed write could not be verified by reading the object back."""
