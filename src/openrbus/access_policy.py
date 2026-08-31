"""Caller-controlled ceiling for register and authorization access levels."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from openrbus.errors import AccessPolicyError
from openrbus.protocol.canip import ObjectAddress
from openrbus.registry import AccessLevel, AccessOperation, AccessRequirement


@dataclass(frozen=True, slots=True)
class AccessPolicy:
    """Maximum access level one library instance may use.

    The default is deliberately user-only. Higher levels must be selected
    explicitly by constructing a policy from a runtime value, file, or named
    environment variable.
    """

    max_access_level: AccessLevel = AccessLevel.USER

    def __post_init__(self) -> None:
        level = _parse_level(self.max_access_level)
        object.__setattr__(self, "max_access_level", level)

    @classmethod
    def from_file(cls, path: str | os.PathLike[str]) -> AccessPolicy:
        """Read ``1``, ``2``, ``3`` or a role label from an explicit file."""

        try:
            value = Path(path).read_text(encoding="ascii").strip()
        except OSError as error:
            raise AccessPolicyError("could not read the access-policy file") from error
        return cls(_parse_level(value))

    @classmethod
    def from_env(cls, variable: str) -> AccessPolicy:
        """Read a level from an explicitly selected environment variable."""

        value = os.environ.get(variable)
        if value is None or not value.strip():
            raise AccessPolicyError(
                "the selected access-policy environment variable is unset or empty"
            )
        return cls(_parse_level(value))

    def require_level(
        self,
        required: AccessLevel | int,
        operation: AccessOperation | str,
        *,
        address: ObjectAddress | None = None,
    ) -> AccessLevel:
        """Require one known level without performing any device I/O."""

        level = _parse_required_level(required)
        if level > self.max_access_level:
            target = f"register {address}" if address is not None else "node authorization"
            raise AccessPolicyError(
                f"access policy blocks {AccessOperation(operation).value} on {target}: "
                f"required level {int(level)} exceeds configured maximum "
                f"{int(self.max_access_level)}"
            )
        return level

    def require_register(
        self,
        address: ObjectAddress,
        requirement: AccessRequirement,
    ) -> AccessLevel:
        """Validate static registry evidence and return a conservative level."""

        operation = requirement.operation.value
        if not requirement.complete or not requirement.levels:
            raise AccessPolicyError(
                f"access policy blocks {operation} on register {address}: "
                "the required access level is unknown"
            )
        required = max(requirement.levels)
        self.require_level(required, requirement.operation, address=address)
        return required


def resolve_access_policy(
    access_policy: AccessPolicy | None,
    max_access_level: AccessLevel | int | None,
) -> AccessPolicy:
    """Resolve mutually exclusive policy inputs with a user-only default."""

    if access_policy is not None and max_access_level is not None:
        raise ValueError("select access_policy or max_access_level, not both")
    if access_policy is not None:
        return access_policy
    if max_access_level is not None:
        return AccessPolicy(_parse_level(max_access_level))
    return AccessPolicy()


def _parse_level(value: AccessLevel | int | str) -> AccessLevel:
    if isinstance(value, str):
        normalized = value.strip().casefold()
        aliases = {
            "user": AccessLevel.USER,
            "installer": AccessLevel.INSTALLER,
            "professional": AccessLevel.PROFESSIONAL,
        }
        if normalized in aliases:
            return aliases[normalized]
        try:
            value = int(normalized, 10)
        except ValueError as error:
            raise AccessPolicyError(
                "maximum access level must be 1, 2, 3, user, installer, or professional"
            ) from error
    try:
        level = AccessLevel(value)
    except ValueError as error:
        raise AccessPolicyError("maximum access level must be 1, 2, or 3") from error
    if level not in (AccessLevel.USER, AccessLevel.INSTALLER, AccessLevel.PROFESSIONAL):
        raise AccessPolicyError("maximum access level must be 1, 2, or 3")
    return level


def _parse_required_level(value: AccessLevel | int) -> AccessLevel:
    try:
        return AccessLevel(value)
    except ValueError as error:
        raise AccessPolicyError(f"unsupported required access level {int(value)}") from error


__all__ = ["AccessPolicy"]
