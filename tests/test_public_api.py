from __future__ import annotations

import openrbus


def test_package_facade_exposes_stable_core_types() -> None:
    assert openrbus.ObjectAddress.parse("2300:00") == openrbus.ObjectAddress(0x2300, 0)
    assert len(openrbus.Registry.load_default()) == 3066
    assert openrbus.ReadOutcome == openrbus.ReadResult | openrbus.ReadFailure
    assert issubclass(openrbus.WriteVerificationError, openrbus.ValidationError)
    assert openrbus.AccessLevel.USER == 1
    assert openrbus.AccessLevel.INSTALLER.is_higher_risk
    assert openrbus.AccessPolicy().max_access_level is openrbus.AccessLevel.USER
    assert issubclass(openrbus.AccessPolicyError, openrbus.AccessLevelError)
    assert issubclass(openrbus.InsufficientAccessLevelError, openrbus.AccessLevelError)
    assert issubclass(openrbus.AuthorizationKeyError, openrbus.NodeAuthorizationError)
    assert openrbus.NodeAuthorizer is not None
    assert openrbus.TeaKeyComponent is not None
    assert hasattr(openrbus.OpenRBusClient, "refresh_session_access")
