from openrbus import RegisterCatalogEntry, catalog_for_node
from openrbus.discovery import CapabilityReference, DeviceIdentity, resolve_device_identity
from openrbus.inventory import DeviceInventory, ObjectCapability, ObjectSupport
from openrbus.protocol.canip import ObjectAddress
from openrbus.registry import Registry


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
    assert ehc_cp733.safety == "source_supported"


def test_iae_source_supported_write_is_exact_family_and_readonly_stays_blocked() -> None:
    registry = Registry.load_default()
    definition = registry.get("200e:00")
    identity = resolve_device_identity(DeviceIdentity(4, 528, None, None))

    row = next(
        item for item in catalog_for_node(identity, registry) if item.address == definition.address
    )
    assert row.writable is True
    assert row.write_declared is True
    assert row.safety == "source_supported"
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
