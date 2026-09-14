import pytest

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
from openrbus.registry import (
    IdentityEvidence,
    Registry,
    RegistryEvidenceKind,
    RegistryMatchStatus,
)


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


def test_registry_identity_matching_requires_explicit_pair_evidence() -> None:
    registry = Registry.load_default()
    identity = DeviceIdentity(1, 0x0210, 0x0203, "EHC-16")
    evidence = {
        (0x0210, 0x0203): (
            IdentityEvidence("Ehc-16", RegistryEvidenceKind.TRUSTED, "live identity verification"),
        )
    }
    inventory = DeviceInventory(identity)
    result = inventory.resolve_registry(registry, evidence)
    assert result.status is RegistryMatchStatus.EXACT
    assert result.family == "Ehc-16"
    assert result.provenance == ("trusted:live identity verification",)
    assert result.evidence[0].source_kind is RegistryEvidenceKind.TRUSTED
    assert inventory.registry_resolution == result
    assert inventory.registry_match == "Ehc-16"


def test_registry_identity_matching_is_unknown_without_evidence() -> None:
    registry = Registry.load_default()
    result = registry.match_identity(DeviceIdentity(1, 0x0210, 0x0203, None))
    assert result.status is RegistryMatchStatus.UNKNOWN
    assert result.family is None


def test_registry_identity_matching_preserves_ambiguity() -> None:
    registry = Registry.load_default()
    identity = DeviceIdentity(1, 0x0210, 0x0203, None)
    evidence = {
        (0x0210, 0x0203): (
            IdentityEvidence("Ehc-16", RegistryEvidenceKind.TRUSTED, "source-a"),
            IdentityEvidence("Other", RegistryEvidenceKind.DISCOVERED, "source-b"),
        )
    }
    result = registry.match_identity(identity, evidence)
    assert result.status is RegistryMatchStatus.AMBIGUOUS
    assert result.family is None
    assert result.provenance == ("discovered:source-b", "trusted:source-a")
    assert {item.source_kind for item in result.evidence} == {
        RegistryEvidenceKind.TRUSTED,
        RegistryEvidenceKind.DISCOVERED,
    }
    inventory = DeviceInventory(identity)
    inventory.resolve_registry(registry, evidence)
    assert inventory.registry_resolution is not None
    assert inventory.registry_resolution.reason == result.reason


def test_registry_identity_matching_rejects_untyped_or_empty_evidence() -> None:
    registry = Registry.load_default()
    identity = DeviceIdentity(1, 0x0210, 0x0203, None)
    with pytest.raises(TypeError):
        registry.match_identity(identity, {(0x0210, 0x0203): ("Ehc-16",)})  # type: ignore[arg-type]
    with pytest.raises(ValueError):
        IdentityEvidence("", RegistryEvidenceKind.TRUSTED, "source")
    with pytest.raises(TypeError):
        IdentityEvidence(None, RegistryEvidenceKind.TRUSTED, "source")  # type: ignore[arg-type]
    with pytest.raises(TypeError):
        IdentityEvidence("family", "trusted", "source")  # type: ignore[arg-type]
