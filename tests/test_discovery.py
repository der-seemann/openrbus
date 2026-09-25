from __future__ import annotations

import pytest

from openrbus.discovery import (
    DeviceIdentity,
    assigned_nodes,
    discover_capabilities,
    discover_devices,
    resolve_device_identity,
)
from openrbus.errors import ProtocolError
from openrbus.protocol.canip import ObjectAddress


class FakeReader:
    def __init__(self) -> None:
        self.calls: list[tuple[int, ObjectAddress]] = []

    async def read_raw(
        self, node: int, address: ObjectAddress, *, timeout: float | None = None
    ) -> bytes:
        self.calls.append((node, address))
        values = {
            (1, "1f85:00"): b"\x04",
            (1, "1f85:03"): b"\x01",
            (1, "1f85:04"): b"\x01",
            (1, "2001:02"): b"\x34\x12",
            (1, "2001:05"): b"\x02\x00",
            (1, "300f:00"): b"Controller-A\0",
            (3, "2001:02"): b"\x35\x12",
            (3, "2001:05"): b"\x03\x00",
            (3, "300f:00"): b"Module-B\0",
            (4, "2001:02"): b"\x36\x12",
            (4, "2001:05"): b"\x04\x00",
            (4, "300f:00"): b"Module-C\0",
            (1, "5826:00"): b"\x02",
            (1, "5826:01"): bytes.fromhex("00340012"),
            (1, "5826:02"): bytes.fromhex("01350112"),
        }
        key = (node, str(address))
        if key not in values:
            raise ProtocolError("synthetic negative response")
        return values[key]


@pytest.mark.asyncio
async def test_assignment_directory_avoids_bruteforce_and_identity_is_little_endian() -> None:
    reader = FakeReader()
    assert await assigned_nodes(reader) == (1, 3, 4)
    devices = await discover_devices(reader)
    assert [device.name for device in devices] == ["Controller-A", "Module-B", "Module-C"]
    assert devices[0].device_code == 0x1234
    assert all(device.serial_number is None for device in devices)
    assert not any(address.subindex > 4 for _, address in reader.calls if address.index == 0x1F85)


@pytest.mark.asyncio
async def test_internal_configuration_keeps_unknown_flags_opaque() -> None:
    refs = await discover_capabilities(FakeReader(), 1)
    assert [str(ref.address) for ref in refs] == ["1234:00", "1235:01"]
    assert [ref.flags for ref in refs] == [0, 1]


def test_device_code_metadata_names_only_evidenced_models() -> None:
    assert resolve_device_identity(DeviceIdentity(1, 528, None, None)).display_name == "EHC-16"
    assert resolve_device_identity(DeviceIdentity(2, 5123, None, None)).display_name == "MK3"
    assert resolve_device_identity(DeviceIdentity(3, 5123, None, None)).family == "Mk-3"
    assert resolve_device_identity(DeviceIdentity(5, 7688, None, None)).family == "IoTGTW-1"
    assert resolve_device_identity(DeviceIdentity(6, 7702, None, None)).family == "Gtw-22"
    unknown = resolve_device_identity(DeviceIdentity(7, 9999, None, None))
    assert unknown.display_name == "OpenRBus node 7"
    assert unknown.model is None and unknown.family is None


def test_scb_name_metadata_resolves_family_without_device_code() -> None:
    identity = resolve_device_identity(DeviceIdentity(4, None, None, "SCB-10"))

    assert identity.model == "SCB-10"
    assert identity.family == "Scb-10"


def test_product_name_metadata_resolves_canonical_gateway_families() -> None:
    assert (
        resolve_device_identity(DeviceIdentity(3, None, None, "GTW-Bluetooth")).family == "Gtw-22"
    )
    assert resolve_device_identity(DeviceIdentity(5, None, None, "GTW-08")).family == "IoTGTW-1"
    assert resolve_device_identity(DeviceIdentity(6, None, None, "MK3")).family == "Mk-3"


def test_rediscovery_replaces_stale_resolved_family() -> None:
    identity = DeviceIdentity(3, 7702, None, "GTW-Bluetooth", family="Mk-3")

    resolved = resolve_device_identity(identity)

    assert resolved.family == "Gtw-22"
    assert resolved.model == "GTW-Bluetooth"
