"""Read-only node identity and capability discovery."""

from __future__ import annotations

from dataclasses import dataclass, replace
from typing import Protocol

from openrbus.errors import OpenRBusError, ProtocolError
from openrbus.protocol.canip import ObjectAddress

MASTER_NODE = 0x01
SLAVE_ASSIGNMENT = ObjectAddress(0x1F85, 0)
INTERNAL_CONFIGURATION = 0x5826

# Device-code metadata is deliberately small and evidence-backed. Unknown
# codes remain unknown rather than being turned into guessed product names.
DEVICE_IDENTITY_METADATA: dict[int, tuple[str, str]] = {
    528: ("EHC-16", "EHC-16"),
    # The display labels are intentionally human-facing, while the family
    # value is the exact canonical key used by registry-v1.json.  Keeping
    # those two concerns separate avoids a silent empty family projection.
    5123: ("MK3", "Mk-3"),
    # These device codes and family assignments are evidenced by the
    # recovered device/configuration mappings.  They are product identities,
    # never node-number assumptions.
    7688: ("GTW-08", "IoTGTW-1"),
    7702: ("GTW-Bluetooth", "Gtw-22"),
}

# SCB-10 installations expose the family label through the public name
# object, while their device code is not a stable family discriminator.  Keep
# this deliberately exact and evidence-backed; unknown names remain unknown.
DEVICE_NAME_METADATA: dict[str, tuple[str, str]] = {
    "scb-10": ("SCB-10", "Scb-10"),
    "mk3": ("MK3", "Mk-3"),
    "gtw-08": ("GTW-08", "IoTGTW-1"),
    "gtw-bluetooth": ("GTW-Bluetooth", "Gtw-22"),
}


class ObjectReader(Protocol):
    """Small read-only contract shared by BLE and future bus adapters."""

    async def read_raw(
        self, node: int, address: ObjectAddress, *, timeout: float | None = None
    ) -> bytes: ...


@dataclass(frozen=True, slots=True)
class DeviceIdentity:
    node: int
    device_code: int | None
    parameter_number: int | None
    name: str | None
    serial_number: int | None = None
    # Optional identity fields are populated only when the transport or a
    # future public registry mapping provides them.  Keeping them nullable is
    # important: an unknown device must never be given a guessed model name.
    manufacturer: str | None = None
    family: str | None = None
    model: str | None = None
    # Runtime capability evidence scopes catalog projections to addresses
    # observed on this node; it is not static registry identity evidence.
    capabilities: tuple[CapabilityReference, ...] = ()

    @property
    def display_name(self) -> str:
        """Return the best supported public label for this node.

        The node fallback is intentionally real and stable; callers should not
        manufacture model names from a device code without registry evidence.
        """

        for candidate in (self.name, self.model, self.family):
            if candidate and candidate.strip():
                return candidate.strip()
        return f"OpenRBus node {self.node}"


def resolve_device_identity(identity: DeviceIdentity) -> DeviceIdentity:
    """Attach known model/family metadata without changing unknown identities."""

    metadata = (
        DEVICE_IDENTITY_METADATA.get(identity.device_code)
        if identity.device_code is not None
        else None
    )
    if metadata is None and identity.name:
        metadata = DEVICE_NAME_METADATA.get(identity.name.strip().casefold())
    if metadata is None:
        return identity
    model, family = metadata
    # A rediscovery must be authoritative for the current identity evidence.
    # Do not carry a family/model resolved for an earlier device at the same
    # node into the new catalog projection.  Unknown identities still return
    # unchanged above, so callers can preserve externally supplied evidence
    # only when no current code/name mapping exists.
    return replace(identity, model=model, family=family)


@dataclass(frozen=True, slots=True)
class CapabilityReference:
    directory_subindex: int
    address: ObjectAddress
    flags: int


async def assigned_nodes(reader: ObjectReader, *, timeout: float | None = None) -> tuple[int, ...]:
    """Discover assigned nodes through the statically derived ``0x1f85`` directory."""

    raw_count = await reader.read_raw(MASTER_NODE, SLAVE_ASSIGNMENT, timeout=timeout)
    if not raw_count:
        raise ProtocolError("0x1f85:00 returned no assignment bound")
    highest = raw_count[0]
    if highest > 0x7F:
        raise ProtocolError(f"invalid assignment upper bound {highest}")
    nodes = [MASTER_NODE]
    for candidate in range(3, highest + 1):
        try:
            await reader.read_raw(
                MASTER_NODE, ObjectAddress(SLAVE_ASSIGNMENT.index, candidate), timeout=timeout
            )
        except OpenRBusError:
            continue
        nodes.append(candidate)
    return tuple(nodes)


async def identify_node(
    reader: ObjectReader,
    node: int,
    *,
    include_serial: bool = False,
    timeout: float | None = None,
) -> DeviceIdentity:
    """Read stable identity objects; serial collection is privacy-opt-in."""

    device_code = await _optional(reader, node, ObjectAddress(0x2001, 0x02), timeout)
    parameter = await _optional(reader, node, ObjectAddress(0x2001, 0x05), timeout)
    name_raw = await _optional(reader, node, ObjectAddress(0x300F, 0x00), timeout)
    serial_raw = (
        await _optional(reader, node, ObjectAddress(0x2001, 0x0A), timeout)
        if include_serial
        else None
    )
    return resolve_device_identity(
        DeviceIdentity(
            node=node,
            device_code=_little_uint(device_code, 2),
            parameter_number=_little_uint(parameter, 2),
            name=_ascii(name_raw),
            serial_number=_little_uint(serial_raw, 4),
        )
    )


async def discover_devices(
    reader: ObjectReader,
    *,
    include_serial: bool = False,
    timeout: float | None = None,
) -> tuple[DeviceIdentity, ...]:
    """Discover and identify all assigned nodes without brute-force scanning."""

    nodes = await assigned_nodes(reader, timeout=timeout)
    return tuple(
        [
            await identify_node(reader, node, include_serial=include_serial, timeout=timeout)
            for node in nodes
        ]
    )


async def discover_capabilities(
    reader: ObjectReader, node: int, *, timeout: float | None = None
) -> tuple[CapabilityReference, ...]:
    """Read the hardware-validated ``0x5826`` internal configuration directory.

    Directory flags are retained but deliberately not interpreted as access or
    datatype metadata; those semantics are not established.
    """

    raw_count = await reader.read_raw(
        node, ObjectAddress(INTERNAL_CONFIGURATION, 0), timeout=timeout
    )
    if not raw_count:
        raise ProtocolError("0x5826:00 returned no directory size")
    count = raw_count[0]
    references: list[CapabilityReference] = []
    for subindex in range(1, count + 1):
        raw = await reader.read_raw(
            node, ObjectAddress(INTERNAL_CONFIGURATION, subindex), timeout=timeout
        )
        if len(raw) != 4:
            continue
        flags, index_low, target_subindex, index_high = raw
        references.append(
            CapabilityReference(
                subindex,
                ObjectAddress((index_high << 8) | index_low, target_subindex),
                flags,
            )
        )
    return tuple(references)


async def _optional(
    reader: ObjectReader, node: int, address: ObjectAddress, timeout: float | None
) -> bytes | None:
    try:
        return await reader.read_raw(node, address, timeout=timeout)
    except OpenRBusError:
        return None


def _little_uint(raw: bytes | None, width: int) -> int | None:
    return int.from_bytes(raw[:width], "little") if raw is not None and len(raw) >= width else None


def _ascii(raw: bytes | None) -> str | None:
    if not raw:
        return None
    candidate = raw.split(b"\0", 1)[0]
    if not candidate or any(byte < 32 or byte > 126 for byte in candidate):
        return None
    return candidate.decode("ascii")
