# Access-level policy

OpenRBus has a caller-controlled access ceiling in addition to device
authorization and write safety. Every `OpenRBusClient`, `NodeAuthorizer`, and
`CanIpGatewayAuthorizer` defaults to `AccessLevel.USER` (level 1).

With no configuration:

- only registers with complete registry evidence for level-1 reads are read;
- writes remain disabled because `enable_writes` defaults to `False`;
- level-2/3 reads, writes, and authorization attempts are rejected before
  device I/O;
- definitions without a known access requirement are rejected rather than
  silently treated as user-level objects.

An `AccessPolicyError` identifies this local policy decision. It is distinct
from `InsufficientAccessLevelError`, which means the policy allowed the
operation but the node's verified `4002:00` level was too low, and from
`UnsafeWriteError`, which concerns write confidence.

## Physical access boundary

Level-2/3 authorization is functionally equivalent to the
installer/professional access available at the physical control panel with the
documented code `0012` (see the public product manual). The TEA mechanism does
not expose a capability that physical access to the appliance plus that code
would not expose.

Protection against unauthorized access is physical in both cases: restrict
access to the heating room and appliance. Physical access is also needed to
obtain the installation-specific BLE pairing PIN; without that PIN, OpenRBus
cannot establish remote BLE access.

Level-2/3 access remains operationally high risk. Incorrect settings can
increase energy consumption, accelerate equipment wear, and reduce comfort,
just as incorrect changes at the physical installer/professional menu can.
This operational risk is independent of the narrower access-control security
boundary.

## Explicit higher-level opt-in

Levels 2 (installer) and 3 (professional) can expose or change higher-risk
heating-system configuration. Select the highest level this application
instance is allowed to use explicitly:

```python
from openrbus import AccessLevel, OpenRBusClient

client = OpenRBusClient(raw, max_access_level=AccessLevel.INSTALLER)
```

For one configuration shared by the authorizer and client, construct an
`AccessPolicy` once:

```python
from openrbus import AccessPolicy, CanIpGatewayAuthorizer, OpenRBusClient

policy = AccessPolicy(max_access_level=3)
authorizer = CanIpGatewayAuthorizer(transport, access_policy=policy)
client = OpenRBusClient(raw, access_policy=policy)
```

An explicit file may contain `1`, `2`, `3`, `user`, `installer`, or
`professional`:

```python
policy = AccessPolicy.from_file("/explicit/application/config/access-level.conf")
```

Alternatively, a caller-selected environment variable may contain the level:

```python
policy = AccessPolicy.from_env("APPLICATION_SELECTED_MAX_ACCESS_LEVEL")
```

There is no default file path or environment-variable name. Selecting a policy
does not authorize a node and never triggers elevation automatically.

## Independent gates

For a level-2/3 read, the client first checks the local policy, then refreshes
the node's authoritative effective level from `4002:00`. The actual object read
occurs only when both checks pass.

Writes additionally require `enable_writes=True`, declared write access, the
applicable `allow_unsafe=True` opt-in, value validation, and node authorization.
Raising the access-policy ceiling does not weaken any of those gates.

Use `device_family` where registry evidence differs across controller
families. Unknown or incomplete access evidence remains blocked even with a
level-3 policy; the library does not invent a permissive level.
