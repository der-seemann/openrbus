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

- The BLE transport contains no default PIN, shared credential, manufacturer
  unlock, or service-access algorithm. Pairing and authorization must remain
  owner-controlled and can be injected through an application callback.
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
        result = await client.read(1, ObjectAddress(0x2300, 0))
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

results = await client.read_many(1, ("2300:00", "200d:00"))
for result in results:
    if isinstance(result, ReadFailure):
        print(result.address, result.error)
    else:
        print(result.address, result.value)
```

Results retain input order and carry per-object aborts without hiding
successful values from the same batch.

## Write safety

Writes have three independent gates:

1. Construct `OpenRBusClient` with `enable_writes=True`.
2. Pass `allow_unsafe=True` for definitions that are not hardware-validated.
3. Pass a value satisfying the registry type, range, precision, family, and
   conflict checks.

Use `dry_run=True` first. A dry run validates and encodes the value without I/O,
but intentionally still requires both opt-ins.

```python
from decimal import Decimal

client = OpenRBusClient(raw, enable_writes=True)
plan = await client.write(
    1,
    "2300:00",
    Decimal("12.34"),
    allow_unsafe=True,
    dry_run=True,
)
print(plan.raw_value.hex())
```

An active confirmed write is rate-limited and read back by default. Writes are
always individual and sequential; OpenRBus provides no transactional or batch
write. This does not make an unverified register safe; read-back only detects a
byte-level mismatch.

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
