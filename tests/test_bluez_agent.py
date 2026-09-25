from __future__ import annotations

import pytest
from dbus_fast import DBusError

from openrbus.transport.bluez_agent import _AgentInterface, _device_token


def test_device_token_matches_bluez_path() -> None:
    assert _device_token("test-device") == "_ED_EC_E"


def test_agent_accepts_only_selected_target() -> None:
    agent = _AgentInterface("test-device", 123456)
    target = "/org/bluez/hci0/dev_ED_EC_E"
    other = "/org/bluez/hci0/dev_OTHER"
    assert agent.RequestPinCode.__wrapped__(agent, target) == "123456"
    assert agent.RequestPasskey.__wrapped__(agent, target) == 123456
    agent.RequestConfirmation.__wrapped__(agent, target, 123456)
    with pytest.raises(DBusError):
        agent.RequestPinCode.__wrapped__(agent, other)
    with pytest.raises(DBusError):
        agent.RequestConfirmation.__wrapped__(agent, target, 654321)
