# OpenRBus

OpenRBus is an experimental, vendor-independent Python protocol core for
reading CANopen-style objects from compatible BDR Thermea heating systems. It
provides validated BLE segmentation, CAN-IP object access, a normalized public
register registry, and an asynchronous high-level client.

The project is alpha software. Read support has hardware evidence for the
documented BLE/CAN-IP path. Writes remain deliberately conservative: every
currently published writable definition is classified as unverified, disabled
by default, and requires an explicit unsafe opt-in. Do not use writes on a
production heating system unless you understand and can recover from the
possible effects.

## Scope and boundaries

- The package contains no default PIN, shared credential, manufacturer key, or
  implicit secret path. Pairing and node authorization remain owner-controlled.
- The active BLE path carries CAN-IP messages behind a transparent-service
  selector. The separate RUB codec is available for offline and future-adapter
  work, but is not part of that active path.
- The registry contains normalized technical facts and short German/English
  labels. It contains no vendor binaries, decompiled source, packet captures,
  credentials, serial numbers, device addresses, or sampled runtime values.
- Compatibility varies with controller family, firmware, and configuration.
  Registry evidence is not a promise that an object exists on a particular
  installation.

## Requirements and installation

OpenRBus requires Python 3.11 or newer.

```console
python -m venv .venv
. .venv/bin/activate
python -m pip install -e '.[dev,ble]'
```

Omit `ble` when using only codecs, the registry, or a custom object-access
adapter.

## Reading an object

The operating system must already be paired with the gateway. Some devices may
also need an owner-supplied authorization callback.

```python
import asyncio

from openrbus import ObjectAddress, OpenRBusClient, RawObjectClient
from openrbus.transport import BleakMessageTransport, ManagedTransport


async def main() -> None:
    transport = ManagedTransport(BleakMessageTransport("platform-device-identifier"))
    await transport.connect()
    try:
        raw = RawObjectClient(transport)
        client = OpenRBusClient(raw)
        result = await client.read(
            1,
            ObjectAddress(0x3654, 1),
            device_family="Ehc-16",
        )
        print(result.definition.name("en"), result.value)
    finally:
        await transport.disconnect()


asyncio.run(main())
```

BLE identifiers are platform-specific. `discover_ble_devices()` can return
advertisements exposing the supported transparent service.

## Reading multiple objects

`read_many()` coalesces same-node objects into hardware-validated CAN-IP
`GetList` requests when the object-access adapter supports that capability.
Requests are split at 100 objects and before declared value lengths would
exceed the validated gateway message limit. Oversized individual values use a
normal single-object read. Adapters without bulk support fall back to
sequential reads.

```python
from openrbus import ReadFailure

results = await client.read_many(
    4,
    ("3402:01", "340c:01"),
    device_family="Scb-10",
)
for result in results:
    if isinstance(result, ReadFailure):
        print(result.address, result.error)
    else:
        print(result.address, result.value)
```

Results retain input order and carry per-object aborts without hiding
successful values from the same batch.

## Write safety

The default client permits only level-1 reads with complete access evidence.
Level-2/3 reads and all writes are blocked unless independently enabled. See
[`docs/access-policy.md`](docs/access-policy.md) for direct, file, and explicit
environment-variable configuration.

Writes have five independent gate groups:

1. The client's access policy permits the register's declared level.
2. Construct `OpenRBusClient` with `enable_writes=True`.
3. Pass `allow_unsafe=True` for definitions that are not hardware-validated.
4. For registers with device evidence, satisfy the declared access level. An
   active write verifies the authoritative effective level from `4002:00`.
5. Pass a value satisfying the registry type, range, precision, family, and
   conflict checks.

Use `dry_run=True` first. A dry run validates and encodes the value without I/O,
but intentionally still requires both opt-ins.

```python
from decimal import Decimal

client = OpenRBusClient(raw, enable_writes=True, max_access_level=2)
plan = await client.write(
    1,
    "2300:00",
    Decimal("12.34"),
    allow_unsafe=True,
    dry_run=True,
)
print(plan.raw_value.hex())
print(plan.required_access_level, plan.higher_risk_access)
```

An active confirmed write is rate-limited and read back by default. Writes are
always individual and sequential; OpenRBus provides no transactional or batch
write. This does not make an unverified register safe; read-back only detects a
byte-level mismatch.

Access levels 1, 2, and 3 mean user, installer, and professional. Level 2 and
above is explicitly higher-risk metadata. Access requirements are only present
where device-family evidence exists; unknown requirements are not invented.
See [`docs/writing.md`](docs/writing.md) for coverage and distinct access,
value, and write-safety errors.

Level-2/3 access is functionally equivalent to the installer/professional
access available at the physical control panel with the documented code
`0012` (see the public product manual). OpenRBus's TEA authorization does not
provide a capability that physical access to the appliance plus that code
would not provide. In both cases the protection against unauthorized access is
physical: deny access to the heating room and appliance. Without physical
access, the installation-specific BLE pairing PIN cannot be obtained, so
OpenRBus cannot establish remote BLE access.

**Warning:** Incorrect level-2/3 settings can increase energy consumption,
accelerate equipment wear, and reduce comfort. This is the same operational
risk as using the physical installer/professional menu, not an additional
security risk in the narrower access-control sense.

## Optional node authorization

Level-1 reads and gated writes need no manufacturer key. Level-2/3 objects
remain blocked unless the node reports a sufficient effective level at
`4002:00`.

`NodeAuthorizer` implements the explicit, per-node challenge-response pipeline
for adapters that implement `AsyncNodeAuthorizationAccess`. It requires a
four-byte manufacturer-owned TEA key component which OpenRBus does not include,
derive, cache, or log. Users must obtain any required material themselves;
OpenRBus deliberately does not prescribe a source.

Supply the component directly for one call. The independent access policy must
also permit the requested level:

```python
await NodeAuthorizer(access, max_access_level=3).authorize(
    4,
    AccessLevel.PROFESSIONAL,
    key_component=application_supplied_four_bytes,
)
```

Or supply an explicit external file path. The file contains the uint32 key
component as four big-endian bytes or eight conventional hexadecimal digits:

```python
await NodeAuthorizer(access, max_access_level=3).authorize(
    4,
    AccessLevel.PROFESSIONAL,
    key_path="/path/outside/the/repository/node-authorization.key",
)
```

`key_path_env="APPLICATION_SELECTED_VARIABLE"` is also supported; the named
environment variable contains the file path, not the key. There is no default
path or variable name. The ordinary generic-purpose `RawObjectClient` does not
claim authorization-channel support; an adapter must explicitly implement the
channel switch required after the free-channel request.

On the active BLE/CAN-IP path, use the separately validated purpose-2 gateway
exchange before constructing the object client. The gateway confirmation is
not itself a node grant; `refresh_session_access()` must still prove the target
node's effective level from `4002:00`:

```python
from openrbus import CanIpGatewayAuthorizer, OpenRBusClient, RawObjectClient

await CanIpGatewayAuthorizer(transport, max_access_level=3).authorize(
    3,
    key_path="/explicit/path/outside/the/repository/node-authorization.key",
)
client = OpenRBusClient(
    RawObjectClient(transport),
    enable_writes=True,
    max_access_level=3,
)
access = await client.refresh_session_access(4)
assert int(access.effective_level) >= 3
```

The gateway authorizer accepts the same three explicit key-source forms and
also has no implicit path, variable name, cache, or secret-bearing log output.

## Registry

The canonical dataset is [`data/registry/registry-v1.json`](data/registry/registry-v1.json).
It currently contains 3,066 canonical definitions and 47 explicitly separated
legacy candidates. The packaged compact copy is loaded with:

```python
from openrbus import Registry

registry = Registry.load_default()
definition = registry.get("2300:00")
print(definition.name("de"), definition.wire.type, definition.wire.unit)
```

The source code is Apache-2.0 licensed. The normalized registry dataset is
licensed under CC BY 4.0; see [`data/registry/README.md`](data/registry/README.md)
for attribution and provenance boundaries.

## Development

```console
ruff check .
ruff format --check .
mypy src
pytest
python tools/check_publication.py
python -m build
```

Security issues and contribution rules are documented in `SECURITY.md` and
`CONTRIBUTING.md`. Protocol confidence levels and write limitations are
documented under `docs/`.

## Disclaimer

OpenRBus is an independent interoperability project and is not affiliated with
or endorsed by BDR Thermea or its brands. Heating-system changes can cause
equipment damage, unsafe operation, loss of comfort, or loss of warranty. See
`DISCLAIMER.md` before connecting to real equipment.
