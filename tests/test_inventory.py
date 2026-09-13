from openrbus.discovery import DeviceIdentity
from openrbus.errors import CanOpenAbortError
from openrbus.inventory import (
    DeviceInventory,
    DiscoveryStatus,
    ObjectCapability,
    ObjectSupport,
    classify_abort,
)
from openrbus.protocol.canip import ObjectAddress


def test_inventory_records_supported_and_unsupported_objects() -> None:
    inventory = DeviceInventory(DeviceIdentity(0xFF, 0x1E16, None, None))
    supported = ObjectCapability(ObjectAddress(0x2001, 2), ObjectSupport.SUPPORTED, b"\x16\x1e")
    unsupported = ObjectCapability(ObjectAddress(0x5013, 0), ObjectSupport.NOT_SUPPORTED)
    inventory.record(supported)
    inventory.record(unsupported)
    assert inventory.status is DiscoveryStatus.DISCOVERED
    assert inventory.capabilities[ObjectAddress(0x2001, 2)].raw_value == b"\x16\x1e"
    assert inventory.capabilities[ObjectAddress(0x5013, 0)].status is ObjectSupport.NOT_SUPPORTED


def test_object_not_exist_abort_is_not_supported() -> None:
    assert classify_abort(CanOpenAbortError(0x06020000)) is ObjectSupport.NOT_SUPPORTED
    assert classify_abort(CanOpenAbortError(0x05040000)) is ObjectSupport.TEMPORARILY_FAILED
