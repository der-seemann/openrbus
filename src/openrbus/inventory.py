"""Read-only inventory and capability status helpers.

The inventory layer deliberately keeps runtime discovery separate from the
static register registry.  A registry entry describes what *may* exist; an
inventory record describes what was actually observed on a bus node.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
from time import time

from openrbus.discovery import DeviceIdentity
from openrbus.errors import CanOpenAbortError
from openrbus.protocol.canip import ObjectAddress


class DiscoveryStatus(StrEnum):
    """Status of a read-only discovery or capability operation."""

    DISCOVERED = "discovered"
    NOT_SUPPORTED = "not_supported"
    UNKNOWN = "unknown"
    TEMPORARILY_FAILED = "temporarily_failed"


class ObjectSupport(StrEnum):
    """Observed support state for one registry object."""

    SUPPORTED = "supported"
    NOT_SUPPORTED = "not_supported"
    UNKNOWN = "unknown"
    TEMPORARILY_FAILED = "temporarily_failed"


@dataclass(frozen=True, slots=True)
class ObjectCapability:
    """One observed object capability, without any write operation."""

    address: ObjectAddress
    status: ObjectSupport
    raw_value: bytes | None = field(default=None, repr=False)
    error_code: int | None = None
    observed_at: float = field(default_factory=time)


@dataclass(slots=True)
class DeviceInventory:
    """Runtime inventory for one bus node."""

    identity: DeviceIdentity
    status: DiscoveryStatus = DiscoveryStatus.DISCOVERED
    registry_match: str | None = None
    capabilities: dict[ObjectAddress, ObjectCapability] = field(default_factory=dict)
    last_seen: float = field(default_factory=time)

    def record(self, capability: ObjectCapability) -> None:
        """Record the latest read-only observation for an object."""

        self.capabilities[capability.address] = capability
        self.last_seen = capability.observed_at


def classify_abort(error: CanOpenAbortError) -> ObjectSupport:
    """Map a protocol abort to inventory semantics.

    ``0x06020000`` is the CANopen ``object does not exist`` abort observed on
    the real gateway.  Other aborts remain transient/unknown rather than being
    presented as transport failures or guessed capabilities.
    """

    if error.code == 0x06020000:
        return ObjectSupport.NOT_SUPPORTED
    return ObjectSupport.TEMPORARILY_FAILED


__all__ = [
    "DeviceInventory",
    "DiscoveryStatus",
    "ObjectCapability",
    "ObjectSupport",
    "classify_abort",
]
