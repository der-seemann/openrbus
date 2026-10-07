from types import SimpleNamespace

from openrbus import RegisterCatalogEntry, catalog_for_node
from openrbus.catalog import _entry
from openrbus.discovery import CapabilityReference, DeviceIdentity, resolve_device_identity
from openrbus.inventory import DeviceInventory, ObjectCapability, ObjectSupport
from openrbus.protocol.canip import ObjectAddress
from openrbus.registry import AccessLevel, Registry, WriteClassification, WriteSafety


def test_catalog_public_exports() -> None:
    assert RegisterCatalogEntry is not None
    assert catalog_for_node(DeviceIdentity(3, 7702, 1, None)) == ()


def test_runtime_identity_catalog_is_scoped_to_observed_capabilities() -> None:
    registry = Registry.load_default()
    definition = registry.registers[0]
    identity = DeviceIdentity(
        3,
        7702,
        1,
        None,
        capabilities=(CapabilityReference(1, definition.address, 0),),
    )

    catalog = catalog_for_node(identity, registry)

    assert len(catalog) == 1
    assert catalog[0].address == definition.address
    assert catalog[0].as_dict()["object"] == str(definition.address)


def test_inventory_catalog_uses_observed_addresses_only() -> None:
    registry = Registry.load_default()
    definition = registry.registers[0]
    inventory = DeviceInventory(DeviceIdentity(3, 7702, 1, None))
    inventory.record(ObjectCapability(definition.address, ObjectSupport.SUPPORTED))

    catalog = catalog_for_node(inventory, registry)

    assert tuple(item.address for item in catalog) == (definition.address,)


def test_explicit_capabilities_override_runtime_evidence() -> None:
    registry = Registry.load_default()
    definition = registry.registers[0]

    catalog = catalog_for_node(3, registry, (definition.address, definition.address))

    assert len(catalog) == 1
    assert catalog[0].node == 3


def test_family_catalog_projects_access_ceiling_for_known_ehc() -> None:
    identity = resolve_device_identity(DeviceIdentity(1, 528, None, None))

    counts = {level: len(catalog_for_node(identity, max_access_level=level)) for level in (1, 2, 3)}

    assert counts[1] < counts[2] < counts[3]
    # Wire arrays with a 255-item bound are no longer expanded into
    # speculative aliases; evidenced scalars and bounded zone rows remain.
    assert counts[3] > 700
    assert ObjectAddress(0x200E, 0) in {
        entry.address for entry in catalog_for_node(identity, max_access_level=2)
    }


def test_inventory_family_catalog_uses_identity_family() -> None:
    identity = resolve_device_identity(DeviceIdentity(1, 528, None, None))
    inventory = DeviceInventory(identity)

    assert {entry.address for entry in catalog_for_node(inventory, max_access_level=3)} == {
        entry.address for entry in catalog_for_node(identity, max_access_level=3)
    }


def test_scb_family_catalog_projects_each_access_level() -> None:
    identity = resolve_device_identity(DeviceIdentity(4, None, None, "SCB-10"))

    counts = {level: len(catalog_for_node(identity, max_access_level=level)) for level in (1, 2, 3)}

    assert counts[1] < counts[2] < counts[3]
    assert counts[3] > 1000


def test_evidenced_gateway_and_mk3_families_project_complete_catalog() -> None:
    # Product identity is evidence-backed; node numbers are deliberately
    # varied and do not participate in family resolution.
    cases = (
        (DeviceIdentity(3, 7702, 3, "GTW-Bluetooth"), "Gtw-22"),
        (DeviceIdentity(55, 7688, 258, "GTW-08"), "IoTGTW-1"),
        (DeviceIdentity(99, 5123, 9, "MK3"), "Mk-3"),
    )
    for raw, family in cases:
        identity = resolve_device_identity(raw)
        assert identity.family == family
        complete = catalog_for_node(identity)
        level_three = catalog_for_node(identity, max_access_level=3)
        assert complete
        assert len(complete) >= len(level_three)
        assert len({entry.address for entry in complete}) == len(complete)
        assert all(entry.node == raw.node for entry in complete)


def test_mk3_does_not_expand_unrelated_255_item_arrays() -> None:
    registry = Registry.load_default()
    identity = resolve_device_identity(DeviceIdentity(99, 5123, 9, "MK3"))

    catalog = catalog_for_node(identity, registry, max_access_level=3)
    addresses = {entry.address for entry in catalog}

    # The MK3 source profile establishes exactly two 30b7 elements. Its
    # unrelated 255-item wire capacity is not evidence for 253 more objects.
    assert {address for address in addresses if address.index == 0x30B7} == {
        ObjectAddress(0x30B7, 1),
        ObjectAddress(0x30B7, 2),
    }
    assert {address for address in addresses if address.index == 0x5139} == {
        ObjectAddress(0x5139, 1)
    }

    # Explicit high-index family evidence stays available even when its array
    # wire bound exceeds the zone-array expansion policy.
    scb = resolve_device_identity(DeviceIdentity(4, None, None, "SCB-10"))
    scb_addresses = {entry.address for entry in catalog_for_node(scb, registry, max_access_level=3)}
    assert ObjectAddress(0x340C, 30) in scb_addresses

    # 3401 has unresolved wire-type variants. Retain exact SCB evidence, but
    # don't extrapolate sibling slots through a catalog-wide type conflict.
    exact_3401 = {
        row.address
        for row in registry.get("3401:00").evidence.devices
        if row.family.casefold() == "scb-10"
    }
    assert {address for address in scb_addresses if address.index == 0x3401} == exact_3401


def test_scb_array_subindexes_are_concrete_family_rows() -> None:
    """CP733's :04 is an evidence-backed array element, not a :00 alias."""

    registry = Registry.load_default()
    expected = {ObjectAddress(0x346A, subindex) for subindex in range(1, 11)}
    for node in (4, 37, 117):
        identity = resolve_device_identity(DeviceIdentity(node, None, None, "SCB-10"))
        catalog = catalog_for_node(identity, registry, max_access_level=3)
        rows = {entry.address for entry in catalog if entry.index == 0x346A}
        assert rows == expected
        cp733 = next(entry for entry in catalog if str(entry.address) == "346a:04")
        assert cp733.datatype == "ENUMERATION"
        assert cp733.storage == "UINT8"
        assert cp733.writable is True
        assert cp733.access_level_evidence["read"]["required_level"] == "professional"
        assert cp733.access_level_evidence["write"]["required_level"] == "professional"
        assert cp733.safety == "validated"
        assert cp733.write_classification == "regular"
        assert all(entry.writable for entry in catalog if entry.address.index == 0x346A)

    # EHC-16 receives comparable bounded zone rows with its own source
    # support classification; SCB's historical validation does not transfer.
    ehc = resolve_device_identity(DeviceIdentity(88, 528, None, None))
    ehc_addresses = {entry.address for entry in catalog_for_node(ehc, registry, max_access_level=3)}
    assert ObjectAddress(0x346A, 1) in ehc_addresses
    ehc_cp733 = next(
        entry
        for entry in catalog_for_node(ehc, registry, max_access_level=3)
        if entry.address == ObjectAddress(0x346A, 4)
    )
    assert ehc_cp733.writable is True
    assert ehc_cp733.safety == "unverified"
    assert ehc_cp733.write_classification == "regular"


def test_iae_source_supported_write_is_exact_family_and_readonly_stays_blocked() -> None:
    registry = Registry.load_default()
    definition = registry.get("200e:00")
    identity = resolve_device_identity(DeviceIdentity(4, 528, None, None))

    row = next(
        item for item in catalog_for_node(identity, registry) if item.address == definition.address
    )
    assert row.writable is True
    assert row.write_declared is True
    assert row.safety == "unverified"
    assert row.write_classification == "regular"
    assert row.access_level_evidence["write"]["required_level"] == "installer"

    explicit_ro = registry.get("348d:00")
    ro_identity = resolve_device_identity(DeviceIdentity(4, 528, None, None))
    ro_row = next(
        item
        for item in catalog_for_node(ro_identity, registry)
        if item.address == explicit_ro.address
    )
    assert ro_row.writable is False
    assert ro_row.write_declared is False
    assert ro_row.write_classification == "read_only"
    # The bounded header remains blocked; an OBD read-only declaration that
    # contradicts positive IAE evidence on a member remains a conflict.
    assert (
        explicit_ro.write_classification_for(ObjectAddress(0x348D, 1), "Ehc-16").value == "conflict"
    )


def test_catalog_write_classification_separates_iae_from_physical_validation() -> None:
    registry = Registry.load_default()
    ehc = resolve_device_identity(DeviceIdentity(12, 528, None, None))
    rows = {row.address: row for row in catalog_for_node(ehc, registry, max_access_level=3)}

    # Direct IAE evidence and a matching bounded family slot classify regular;
    # neither claim says that this physical device was write-validated.
    assert rows[ObjectAddress(0x346A, 1)].write_classification == "regular"
    assert rows[ObjectAddress(0x346A, 4)].write_classification == "regular"
    assert rows[ObjectAddress(0x346A, 4)].safety == "unverified"
    assert (
        registry.get("346a:00").write_classification_for(ObjectAddress(0x346A, 0), "Ehc-16").value
        == "read_only"
    )
    assert (
        registry.get("200e:00").write_classification_for(ObjectAddress(0x200E, 0), None).value
        == "unknown"
    )

    obd_only = registry.get("1000:00")
    default_rows = {
        row.address: row for row in catalog_for_node(4, registry, capabilities=(obd_only.address,))
    }
    experimental_rows = {
        row.address: row
        for row in catalog_for_node(
            4, registry, capabilities=(obd_only.address,), experimental_writes=True
        )
    }
    assert obd_only.write_classification_for(obd_only.address, "Scb-10").value == "experimental"
    assert default_rows[obd_only.address].write_classification == "experimental"
    assert default_rows[obd_only.address].writable is False
    assert experimental_rows[obd_only.address].writable is False
    assert (
        registry.get("500f:00").write_classification_for(ObjectAddress(0x500F, 0), "Scb-10").value
        == "read_only"
    )


def test_explicit_family_ro_sibling_blocks_only_unobserved_array_inference() -> None:
    from dataclasses import replace

    registry = Registry.load_default()
    definition = registry.get("346a:00")
    target = ObjectAddress(0x346A, 9)
    evidence = tuple(
        replace(row, writable_any=False, writable_all=False)
        if row.family.casefold() == "scb-10" and row.address == ObjectAddress(0x346A, 5)
        else row
        for row in definition.evidence.devices
    )
    conflicted = replace(definition, evidence=replace(definition.evidence, devices=evidence))

    assert conflicted.write_classification_for(target, "Scb-10").value == "conflict"
    # Exact positive source rows remain regular even with an unrelated RO slot.
    assert (
        conflicted.write_classification_for(ObjectAddress(0x346A, 4), "Scb-10").value == "regular"
    )


def test_catalog_write_flag_requires_one_complete_unambiguous_level() -> None:
    address = ObjectAddress(0x1000, 0)

    def project(
        levels: tuple[AccessLevel, ...],
        *,
        complete: bool,
        classification: WriteClassification = WriteClassification.EXPERIMENTAL,
    ) -> RegisterCatalogEntry:
        requirement = SimpleNamespace(
            is_known=complete and bool(levels),
            is_ambiguous=len(levels) > 1,
            required_level=levels[0] if complete and len(levels) == 1 else None,
            levels=levels,
            device_families=("Scb-10",) if levels else (),
            complete=complete,
        )
        definition = SimpleNamespace(
            access=SimpleNamespace(readable_declared=True, writable_declared=True),
            access_requirement=lambda *_args, **_kwargs: requirement,
            write_classification_for=lambda *_args: classification,
            write_safety_for=lambda *_args: WriteSafety.UNVERIFIED,
            evidence=SimpleNamespace(type_conflict=False, device_families=()),
            code=None,
            names=SimpleNamespace(de="Synthetic", en="Synthetic"),
            wire=SimpleNamespace(
                type=SimpleNamespace(value="UINT16"),
                storage=SimpleNamespace(value="UINT16"),
                gain=None,
                unit=None,
            ),
        )
        return _entry(4, definition, "Scb-10", address, experimental_writes=True)

    unknown_level = project((), complete=False)
    single_level = project((AccessLevel.USER,), complete=True)
    ambiguous_level = project((AccessLevel.USER, AccessLevel.PROFESSIONAL), complete=True)
    ambiguous_regular = project(
        (AccessLevel.USER, AccessLevel.PROFESSIONAL),
        complete=True,
        classification=WriteClassification.REGULAR,
    )

    assert unknown_level.write_declared is True
    assert unknown_level.write_classification == "experimental"
    assert unknown_level.writable is False
    assert single_level.writable is True
    assert ambiguous_level.write_classification == "experimental"
    assert ambiguous_level.writable is False
    assert ambiguous_regular.write_classification == "regular"
    assert ambiguous_regular.writable is False
