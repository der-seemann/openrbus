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


class WriteVerificationError(ValidationError):
    """A confirmed write could not be verified by reading the object back."""
