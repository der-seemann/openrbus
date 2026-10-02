"""Typed, read-only FunctionGroup discovery and evidence-bounded members.

The runtime records are decoded from the OBD-described 3096/3097 arrays.
Only datapoints whose OBD member references explicitly name cooling or
screed-drying semantics are included in the optional filter member map.
"""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Mapping
from dataclasses import dataclass
from decimal import Decimal
from types import MappingProxyType

from openrbus.discovery import MASTER_NODE, ObjectReader
from openrbus.errors import ProtocolError, ValidationError
from openrbus.protocol.canip import ObjectAddress
from openrbus.registry import Registry
from openrbus.value_codec import StructuredValue, decode_value

FUNCTION_GROUP_DISCOVERED = ObjectAddress(0x3096, 0)
ZONE_DISCOVERED = ObjectAddress(0x3097, 0)


def _addresses(value: str) -> tuple[ObjectAddress, ...]:
    return tuple(
        ObjectAddress.parse(item if ":" in item else f"{item}:00") for item in value.split()
    )


@dataclass(frozen=True, slots=True)
class FunctionGroupKey:
    """The OBD identity tuple shared by FunctionGroup and Zone records."""

    function_group_id: int
    function_group_type: int
    connection_side: int


@dataclass(frozen=True, slots=True)
class FunctionGroupDiscovered:
    """One installed function group reported for a bus node."""

    line_id: int
    node_id: int
    key: FunctionGroupKey


@dataclass(frozen=True, slots=True)
class ZoneDiscovered:
    """A discovered zone's exact function-group and slot association."""

    line_id: int
    node_id: int
    key: FunctionGroupKey
    subindex: int


@dataclass(frozen=True, slots=True)
class FunctionGroupDiscovery:
    """A complete read snapshot of both manufacturer discovery arrays."""

    function_groups: tuple[FunctionGroupDiscovered, ...]
    zones: tuple[ZoneDiscovered, ...]


# Normalized member-address evidence extracted from OBD 1.47. Keys encode
# functionGroupId:functionGroupType:connectionSide. Values retain only exact
# cooling/screed member addresses, never the unrelated members of mixed
# profiles. Zone-array addresses are expanded only through a matching 3097
# ZoneDiscovered record.
def _members(**groups: str) -> Mapping[str, tuple[ObjectAddress, ...]]:
    return MappingProxyType({name: _addresses(value) for name, value in groups.items()})


_SCREED_ZONE_MEMBERS = "344d 3483 3484 3485 3486 3487 3488 3489 348a 348b 348c 5447 5448 5449 544a"
_FILTER_PROFILE_MEMBERS: Mapping[FunctionGroupKey, Mapping[str, tuple[ObjectAddress, ...]]] = (
    MappingProxyType(
        {
            FunctionGroupKey(0x0101, 0x02, 1): _members(screed=_SCREED_ZONE_MEMBERS),
            FunctionGroupKey(0x0101, 0x03, 1): _members(
                screed=_SCREED_ZONE_MEMBERS,
                cooling="3411 3412 341a 3460 3466",
            ),
            FunctionGroupKey(0x0101, 0x05, 1): _members(screed=_SCREED_ZONE_MEMBERS),
            FunctionGroupKey(0x0101, 0x06, 1): _members(
                screed=_SCREED_ZONE_MEMBERS,
                cooling="3411 3412 341b 3460 3466",
            ),
            FunctionGroupKey(0x0411, 0x01, 0): _members(cooling="5046"),
            FunctionGroupKey(0x0421, 0x00, 0): _members(cooling="5046"),
            FunctionGroupKey(0x0427, 0x00, 0): _members(cooling="3011 301e 301f 3411 3412 5046"),
            FunctionGroupKey(0x0501, 0x02, 2): _members(cooling="3504"),
            FunctionGroupKey(0x0501, 0x03, 2): _members(cooling="3504"),
            FunctionGroupKey(0x0501, 0x04, 2): _members(cooling="3504"),
            FunctionGroupKey(0x0502, 0x00, 0): _members(
                cooling="2a0f 2a10 2a11 2a12 2a13 2a14 2a15 2a43 2a49 2a4a 2a58 2a59"
            ),
            FunctionGroupKey(0x1001, 0x00, 1): _members(cooling="5046"),
            FunctionGroupKey(0x1001, 0x01, 1): _members(cooling="5046"),
            FunctionGroupKey(0x1001, 0x02, 1): _members(cooling="5046"),
            FunctionGroupKey(0x1001, 0x03, 2): _members(cooling="370a 5724"),
            FunctionGroupKey(0x1001, 0x07, 2): _members(cooling="370a 5724"),
            FunctionGroupKey(0x1001, 0x08, 2): _members(
                cooling="23a7 23d8 3011 301e 301f 4321 5046 5087 530f"
            ),
            FunctionGroupKey(0x1001, 0x21, 1): _members(
                cooling="2303 234f 3011 301e 301f 321d 430f 4321 5046 5087"
            ),
            FunctionGroupKey(0x1001, 0x41, 1): _members(
                cooling="2303 234f 23a7 23d8 3011 301e 301f 4321 5046 5087 530f"
            ),
            FunctionGroupKey(0x1001, 0x43, 1): _members(cooling="2303 234f 23a7 4321"),
            FunctionGroupKey(0x1001, 0x71, 1): _members(cooling="301e 301f 30ff 3103"),
            FunctionGroupKey(0x1001, 0x73, 1): _members(cooling="301e 30f3 30f4 30f5 30f9 30fa"),
            FunctionGroupKey(0x1001, 0xC9, 1): _members(cooling="23cb 23cc"),
            FunctionGroupKey(0x1007, 0x00, 0): _members(cooling="512e 5132"),
            FunctionGroupKey(0x1008, 0x00, 0): _members(cooling="5046 513c 5140 5149"),
            FunctionGroupKey(0x1010, 0x00, 0): _members(cooling="3011 301e"),
            FunctionGroupKey(0x1060, 0x00, 0): _members(cooling="3218 3219 321a"),
            FunctionGroupKey(0x1060, 0x05, 0): _members(cooling="3218 3219 321a"),
            FunctionGroupKey(0x1060, 0x06, 0): _members(cooling="3218 3219 321a"),
            FunctionGroupKey(0x1060, 0x07, 0): _members(cooling="23b3 23b4 3218 3219 321a"),
            FunctionGroupKey(0x1060, 0x09, 0): _members(cooling="23b3 23b4 3218 3219 321a"),
            FunctionGroupKey(0x1060, 0x10, 0): _members(cooling="3218 3219 321a"),
            FunctionGroupKey(0x2002, 0x00, 1): _members(cooling="4858:06"),
            FunctionGroupKey(0x2002, 0x00, 2): _members(cooling="4858:06"),
        }
    )
)


async def discover_function_group_records(
    reader: ObjectReader,
    *,
    controller_node: int = MASTER_NODE,
    registry: Registry | None = None,
    timeout: float | None = None,
) -> FunctionGroupDiscovery:
    """Read the bounded OBD 1.47 runtime FunctionGroup and zone arrays.

    The caller may treat this as optional metadata. Any unread/malformed array
    raises and must be handled fail-open; a partial list is never represented
    as complete discovery.
    """

    active_registry = registry or Registry.load_default()
    group_rows = await _read_struct_array(
        reader, FUNCTION_GROUP_DISCOVERED, controller_node, timeout, active_registry
    )
    zone_rows = await _read_struct_array(
        reader, ZONE_DISCOVERED, controller_node, timeout, active_registry
    )
    groups = tuple(_function_group_row(row) for row in group_rows)
    zones = tuple(_zone_row(row) for row in zone_rows)
    return FunctionGroupDiscovery(groups, zones)


def filter_member_addresses(
    discovery: FunctionGroupDiscovery,
    *,
    registry: Registry | None = None,
) -> dict[tuple[int, str], frozenset[ObjectAddress]]:
    """Associate exact OBD-target member refs to installed node/group records.

    Mixed group profiles are deliberately not expanded to all members. Array
    members require a matching ZoneDiscovered record so a heating-zone slot is
    not conflated with another slot.
    """

    active_registry = registry or Registry.load_default()
    groups = set(discovery.function_groups)
    zones_by_identity: dict[tuple[int, int, FunctionGroupKey], set[int]] = defaultdict(set)
    for zone in discovery.zones:
        zones_by_identity[(zone.line_id, zone.node_id, zone.key)].add(zone.subindex)
    members: dict[tuple[int, str], set[ObjectAddress]] = defaultdict(set)
    for group in groups:
        profiles = _FILTER_PROFILE_MEMBERS.get(group.key)
        if profiles is None:
            continue
        zone_slots = zones_by_identity.get((group.line_id, group.node_id, group.key), set())
        for filter_name, addresses in profiles.items():
            for address in addresses:
                definition = active_registry.find(address)
                if definition is None:
                    continue
                if definition.wire.is_array:
                    members[(group.node_id, filter_name)].update(
                        ObjectAddress(address.index, slot)
                        for slot in zone_slots
                        if 0 < slot <= (definition.wire.max_items or 0)
                    )
                else:
                    members[(group.node_id, filter_name)].add(address)
    return {key: frozenset(value) for key, value in members.items() if value}


async def _read_struct_array(
    reader: ObjectReader,
    address: ObjectAddress,
    controller_node: int,
    timeout: float | None,
    registry: Registry,
) -> tuple[StructuredValue, ...]:
    definition = registry.find(address)
    if definition is None or not definition.wire.is_array or not definition.wire.struct_name:
        raise ProtocolError(f"{address} lacks a registered structure-array definition")
    raw_count = await reader.read_raw(controller_node, address, timeout=timeout)
    if not raw_count or len(raw_count) > 4:
        raise ProtocolError(f"{address} returned an invalid array count")
    count = int.from_bytes(raw_count, "little")
    if count > (definition.wire.max_items or 0):
        raise ProtocolError(f"{address} array count {count} exceeds its declared bound")
    values: list[StructuredValue] = []
    for subindex in range(1, count + 1):
        item_address = ObjectAddress(address.index, subindex)
        raw_item = await reader.read_raw(controller_node, item_address, timeout=timeout)
        decoded = decode_value(definition, item_address, raw_item, registry=registry)
        if not isinstance(decoded, StructuredValue):
            raise ValidationError(f"{item_address} did not decode as a structure")
        values.append(decoded)
    return tuple(values)


def _function_group_row(row: StructuredValue) -> FunctionGroupDiscovered:
    values = row.as_dict()
    return FunctionGroupDiscovered(
        line_id=_byte(values, "lineId"),
        node_id=_byte(values, "nodeId"),
        key=FunctionGroupKey(
            _unsigned(values, "id", 0xFFFF),
            _byte(values, "type"),
            _byte(values, "connectionSide"),
        ),
    )


def _zone_row(row: StructuredValue) -> ZoneDiscovered:
    values = row.as_dict()
    return ZoneDiscovered(
        line_id=_byte(values, "lineId"),
        node_id=_byte(values, "nodeId"),
        key=FunctionGroupKey(
            _unsigned(values, "functionGroupId", 0xFFFF),
            _byte(values, "functionGroupType"),
            _byte(values, "fgConnectionSide"),
        ),
        subindex=_byte(values, "subIndex"),
    )


def _byte(values: Mapping[str, int | Decimal], name: str) -> int:
    return _unsigned(values, name, 0xFF)


def _unsigned(values: Mapping[str, int | Decimal], name: str, maximum: int) -> int:
    value = values.get(name)
    if not isinstance(value, int) or not 0 <= value <= maximum:
        raise ValidationError(f"invalid unsigned field {name}")
    return value
