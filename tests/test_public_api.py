from __future__ import annotations

import openrbus


def test_package_facade_exposes_stable_core_types() -> None:
    assert openrbus.ObjectAddress.parse("2300:00") == openrbus.ObjectAddress(0x2300, 0)
    assert len(openrbus.Registry.load_default()) == 3066
    assert openrbus.ReadOutcome == openrbus.ReadResult | openrbus.ReadFailure
    assert issubclass(openrbus.WriteVerificationError, openrbus.ValidationError)
