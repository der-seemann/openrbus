"""Scoped BlueZ Agent1 support for native, PIN-protected BLE pairing."""

import contextlib
import re

from dbus_fast import BusType, DBusError
from dbus_fast.aio import MessageBus
from dbus_fast.message import Message
from dbus_fast.service import ServiceInterface, method

_BLUEZ = "org.bluez"
_AGENT_MANAGER = "org.bluez.AgentManager1"
_AGENT_PATH = "/org/bluez/openrbus/agent"
_ADDRESS_RE = re.compile(r"[^0-9a-f]", re.IGNORECASE)


def _device_token(address: str) -> str:
    clean = _ADDRESS_RE.sub("", address).upper()
    return "_" + "_".join(clean[index : index + 2] for index in range(0, len(clean), 2))


class _AgentInterface(ServiceInterface):
    """BlueZ Agent1 implementation accepting only the selected target."""

    def __init__(self, address: str, pin: int) -> None:
        super().__init__("org.bluez.Agent1")
        self._address = _ADDRESS_RE.sub("", address).upper()
        self._pin = pin

    def _check_target(self, device: str) -> None:
        if not device.upper().endswith(_device_token(self._address)):
            raise DBusError("org.bluez.Error.Rejected", "unrelated device")

    @method()
    def Release(self) -> None:
        return None

    @method()
    def Cancel(self) -> None:
        return None

    @method()
    def RequestPinCode(self, device: "o") -> "s":  # type: ignore[name-defined]  # noqa: F821
        self._check_target(device)
        return f"{self._pin:06d}"

    @method()
    def RequestPasskey(self, device: "o") -> "u":  # type: ignore[name-defined]  # noqa: F821
        self._check_target(device)
        return self._pin

    @method()
    def RequestConfirmation(self, device: "o", passkey: "u") -> None:  # type: ignore[name-defined]  # noqa: F821
        self._check_target(device)
        if passkey != self._pin:
            raise DBusError("org.bluez.Error.Rejected", "passkey mismatch")

    @method()
    def AuthorizeService(self, device: "o", uuid: "s") -> None:  # type: ignore[name-defined]  # noqa: F821
        self._check_target(device)


class BlueZPairingAgent:
    """Register a temporary BlueZ agent for one address and pairing PIN."""

    def __init__(self, address: str, pin: int) -> None:
        if not address:
            raise ValueError("BLE address is required")
        if not 0 <= pin <= 999999:
            raise ValueError("pairing PIN must be between 0 and 999999")
        self._address = address
        self._pin = pin
        self._bus: MessageBus | None = None
        self._interface: _AgentInterface | None = None
        self._registered = False

    async def start(self) -> None:
        if self._bus is not None:
            return
        bus = await MessageBus(bus_type=BusType.SYSTEM).connect()
        interface = _AgentInterface(self._address, self._pin)
        bus.export(_AGENT_PATH, interface)
        try:
            await bus.call(
                Message(
                    destination=_BLUEZ,
                    path="/org/bluez",
                    interface=_AGENT_MANAGER,
                    member="RegisterAgent",
                    # AgentManager1.RegisterAgent(agent, capability) is
                    # ``os``; ``a`` would make dbus-fast reject the message
                    # before it reaches BlueZ.
                    signature="os",
                    body=[_AGENT_PATH, "KeyboardOnly"],
                )
            )
            await bus.call(
                Message(
                    destination=_BLUEZ,
                    path="/org/bluez",
                    interface=_AGENT_MANAGER,
                    member="RequestDefaultAgent",
                    signature="o",
                    body=[_AGENT_PATH],
                )
            )
        except Exception:
            bus.unexport(_AGENT_PATH, interface)
            bus.disconnect()
            raise
        self._bus = bus
        self._interface = interface
        self._registered = True

    async def stop(self) -> None:
        bus, interface = self._bus, self._interface
        self._bus = self._interface = None
        if bus is None:
            return
        if self._registered:
            with contextlib.suppress(Exception):
                await bus.call(
                    Message(
                        destination=_BLUEZ,
                        path="/org/bluez",
                        interface=_AGENT_MANAGER,
                        member="UnregisterAgent",
                        signature="o",
                        body=[_AGENT_PATH],
                    )
                )
        self._registered = False
        if interface is not None:
            bus.unexport(_AGENT_PATH, interface)
        bus.disconnect()
