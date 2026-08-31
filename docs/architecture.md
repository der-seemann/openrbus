# Architecture

OpenRBus separates transport, protocol framing, raw object access, registry
interpretation, and application-level safety policy.

```text
application
    |
OpenRBusClient          policy-gated typed reads and writes
    |
AsyncObjectAccess       transport-independent raw object contract
    |                    optional AsyncBulkObjectAccess capability
    |
RawObjectClient         CAN-IP request/response correlation
    |
AsyncMessageTransport   complete-message transport contract
    |
ManagedTransport        bounded connection and retry policy
    |
BleakMessageTransport   BLE segmentation and notifications
```

`Registry` and `value_codec` sit beside the high-level client. They translate
between raw bytes and engineering values but do not perform I/O.

The high-level client first applies an immutable `AccessPolicy`, which defaults
to user-level operation. For permitted level-2/3 reads and writes it maintains
read-only `SessionAccess` samples per node, reads the authoritative effective
level from `4002:00`, and stops before object I/O when device authorization is
insufficient. `4003:00` is not used as evidence of a grant.

`NodeAuthorizer` is a separate, explicit boundary. It accepts external key
material for one call and requires `AsyncNodeAuthorizationAccess`, whose
channel-selection method makes the free-channel transition explicit. A plain
generic-purpose `RawObjectClient` cannot represent that CANopen channel change
and therefore does not satisfy this protocol.

Both node authorizers apply the same user-only default policy before reading
challenge material or sending an authorization request.

The active BLE adapter uses the transparent-service selector and CAN-IP codec.
The RUB codec is separate and is not inserted into this stack.

`OpenRBusClient.read_many()` preserves ordinary object semantics. When the
adapter implements `AsyncBulkObjectAccess`, `RawObjectClient` partitions reads
into same-node GetList batches by object count and predicted response size.
Other adapters use sequential `read_raw()` calls. Per-object aborts are returned
alongside successful values; framing or correlation failures still fail the
whole operation.

## Trust boundaries

- The transport accepts a platform BLE identifier and an optional injected
  authorizer. OpenRBus neither derives nor persists authorization material.
- Node authorization accepts a four-byte vendor key component only through an
  explicit runtime value, file path, or caller-selected key-path environment
  variable. It has no secret default and exposes no key-bearing result.

`CanIpGatewayAuthorizer` implements the active BLE gateway's capture-validated
authorization-purpose exchange. It shares only the pure TEA calculation and
external key-source policy with `NodeAuthorizer`; CAN-IP word byte order and
message correlation are handled separately. Gateway acceptance never replaces
the per-node `4002:00` verification performed by `OpenRBusClient`.
- `AsyncObjectAccess` allows future adapters without forcing BLE framing onto a
  direct-bus implementation.
- `_write_raw` is private by convention so ordinary application code reaches it
  through `OpenRBusClient`. It is not a security sandbox against hostile Python
  code.
- The registry is evidence and metadata, not device discovery. Live object
  availability must be determined through bounded, read-only operations.
- The public tree contains only normalized data. Private research inputs and
  vendor material are outside the build and export paths.

## Failure behavior

Protocol, transport, registry, and validation failures use subclasses of
`OpenRBusError`. Transport retries are bounded. A write is serialized, rate-
limited, and read back by default; a mismatch raises `WriteVerificationError`.
The client does not retry a write automatically.
