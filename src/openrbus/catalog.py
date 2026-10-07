"""Public, read-only register catalog assembled from discovered capabilities."""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from decimal import Decimal

from .discovery import CapabilityReference, DeviceIdentity
from .inventory import DeviceInventory
from .protocol.canip import ObjectAddress
from .registry import AccessOperation, RegisterDefinition, Registry, WriteClassification


@dataclass(frozen=True, slots=True)
class RegisterCatalogEntry:
    """A registry definition projected for one runtime node."""

    node: int
    index: int
    subindex: int
    internal_code: str | None
    name_de: str
    name_en: str
    datatype: str
    storage: str
    scale: Decimal | None
    unit: str | None
    readable: bool
    writable: bool
    write_declared: bool
    write_classification: str
    access_level_evidence: dict[str, object]
    safety: str
    provenance: tuple[str, ...]

    @property
    def address(self) -> ObjectAddress:
        return ObjectAddress(self.index, self.subindex)

    def as_dict(self) -> dict[str, object]:
        """Return a JSON-safe diagnostics/table representation."""
        return {
            "node": self.node,
            "index": self.index,
            "subindex": self.subindex,
            "object": str(self.address),
            "internal_code": self.internal_code,
            "name_de": self.name_de,
            "name_en": self.name_en,
            "datatype": self.datatype,
            "storage": self.storage,
            "scale": str(self.scale) if self.scale is not None else None,
            "unit": self.unit,
            "readable": self.readable,
            "writable": self.writable,
            "write_declared": self.write_declared,
            "write_classification": self.write_classification,
            "access_level_evidence": self.access_level_evidence,
            "safety": self.safety,
            "provenance": list(self.provenance),
        }


def _entry(
    node: int,
    definition: RegisterDefinition,
    family: str | None,
    address: ObjectAddress,
    experimental_writes: bool = False,
) -> RegisterCatalogEntry:
    write_requirement = definition.access_requirement(
        address, AccessOperation.WRITE, device_family=family
    )
    classification = definition.write_classification_for(address, family)
    evidenced_writable = (
        classification is WriteClassification.REGULAR and write_requirement.is_known
    )
    experimental_writable = (
        experimental_writes and classification is WriteClassification.EXPERIMENTAL
    )

    def evidence(operation: AccessOperation) -> dict[str, object]:
        requirement = definition.access_requirement(address, operation, device_family=family)
        return {
            "known": requirement.is_known,
            "required_level": (
                requirement.required_level.label if requirement.required_level else None
            ),
            "levels": [level.label for level in requirement.levels],
            "families": list(requirement.device_families),
            "complete": requirement.complete,
        }

    return RegisterCatalogEntry(
        node=node,
        index=address.index,
        subindex=address.subindex,
        internal_code=definition.code,
        name_de=definition.names.de,
        name_en=definition.names.en,
        datatype=definition.wire.type.value,
        storage=definition.wire.storage.value,
        scale=definition.wire.gain,
        unit=definition.wire.unit,
        readable=definition.access.readable_declared,
        writable=evidenced_writable or experimental_writable,
        write_declared=definition.access.writable_declared,
        write_classification=classification.value,
        access_level_evidence={
            "read": evidence(AccessOperation.READ),
            "write": evidence(AccessOperation.WRITE),
        },
        safety=definition.write_safety_for(address, family).value,
        provenance=tuple(sorted(set(definition.evidence.device_families)))
        or ((family,) if family else ()),
    )


def catalog_for_node(
    node: DeviceInventory | DeviceIdentity | int,
    registry: Registry | None = None,
    capabilities: Iterable[CapabilityReference | ObjectAddress] | None = None,
    experimental_writes: bool = False,
    *,
    max_access_level: int | None = None,
) -> tuple[RegisterCatalogEntry, ...]:
    """Return canonical definitions scoped to a node's capabilities/family.

    Observed capability references are always retained.  When a node has an
    evidence-backed device family, the family register map is also projected;
    the optional access ceiling limits that static projection to rows the
    selected catalog policy allows.  This is intentionally separate from the
    effective runtime access level: HA may register higher-level rows as
    disabled while Core still fail-closes their polling/writes.

    A runtime identity without capability or family evidence returns an empty
    catalog; the immutable registry is not proof that every object exists on
    every node.
    """
    registry = registry or Registry.load_default()
    if isinstance(node, DeviceInventory):
        node_id = node.identity.node
        resolution = getattr(node, "registry_resolution", None)
        family = (
            getattr(resolution, "family", None)
            or getattr(node, "registry_match", None)
            or getattr(node.identity, "family", None)
        )
        addresses = tuple(node.capabilities)
    elif isinstance(node, DeviceIdentity):
        node_id, family = node.node, getattr(node, "family", None)
        addresses = tuple(
            item.address if isinstance(item, CapabilityReference) else item
            for item in getattr(node, "capabilities", ())
        )
    else:
        node_id, family, addresses = int(node), None, ()
    if capabilities is not None:
        addresses = tuple(
            item.address if isinstance(item, CapabilityReference) else item for item in capabilities
        )
    # Keep observed runtime evidence first.  The internal configuration
    # directory is useful for node-specific objects that are not present in
    # the static family map.
    projected: list[ObjectAddress] = list(addresses)
    if family:
        for definition in registry.registers:
            # The registry stores an array as one canonical ``:00`` row and
            # keeps device-profile evidence for its concrete subindexes in
            # ``evidence.devices``.  Projecting only ``definition.address``
            # therefore silently dropped real rows such as SCB-10 CP733
            # (346a:04).  Add those evidence-backed addresses explicitly;
            # do not synthesize aliases or enumerate an array speculatively.
            family_rows = {
                row.address
                for row in definition.evidence.devices
                if row.family.casefold() == family.casefold()
            }
            # Require a concrete device-evidence row for the family.  The
            # aggregate family list is provenance for the definition, but is
            # not enough to make an address valid when a stale/variant row
            # belongs to another family.
            has_family_evidence = bool(family_rows)
            candidate_addresses = (definition.address, *sorted(family_rows))
            if (
                definition.wire.is_array
                and definition.wire.max_items is not None
                and definition.wire.max_items <= 10
                and not definition.evidence.type_conflict
                and (
                    0x3400 <= definition.address.index <= 0x3477
                    or 0x5402 <= definition.address.index <= 0x5444
                )
                and has_family_evidence
            ):
                # The manufacturer defines at most ten comparable heating/
                # zone slots in these object ranges. Other CANopen arrays can
                # have a wire bound of 255 while the family evidence records
                # only one or two concrete items; expanding those arrays made
                # an MK3's catalog exceed 3,000 speculative rows. Keep the
                # bounded, type-consistent zone projection, but require
                # evidence for this exact family and address before
                # synthesizing its siblings.
                candidate_addresses = (
                    *candidate_addresses,
                    *(
                        ObjectAddress(definition.address.index, subindex)
                        for subindex in range(1, definition.wire.max_items + 1)
                    ),
                )
            for address in candidate_addresses:
                requirement = definition.access_requirement(
                    address, AccessOperation.READ, device_family=family
                )
                if not requirement.is_known:
                    # A family-backed definition with incomplete access evidence
                    # is still a known catalog row.  Keep it in the complete
                    # projection so callers can create a stable unavailable/
                    # diagnostic entity.  A bounded access projection remains
                    # fail-closed and excludes it from polling/entity defaults.
                    if max_access_level is not None or not has_family_evidence:
                        continue
                elif max_access_level is not None and not any(
                    int(level) <= int(max_access_level) for level in requirement.levels
                ):
                    continue
                projected.append(address)

    definitions: list[tuple[ObjectAddress, RegisterDefinition]] = []
    seen: set[ObjectAddress] = set()
    for address in projected:
        found_definition = registry.find(address)
        if found_definition is not None and address not in seen:
            definitions.append((address, found_definition))
            seen.add(address)
    return tuple(
        _entry(node_id, definition, family, address, experimental_writes)
        for address, definition in definitions
    )


__all__ = ["RegisterCatalogEntry", "catalog_for_node"]
