# Write policy

No published register is currently classified as hardware-validated for safe
writing. Definitions with declared write access are marked `unverified`; all
others are read-only.

## Required gates

`OpenRBusClient.write()` requires all applicable checks to pass:

1. The client was constructed with `enable_writes=True`.
2. The register is globally declared writable.
3. `allow_unsafe=True` is provided for an unverified definition.
4. No unresolved wire-type conflict exists.
5. Supplied device-family evidence does not mark the register non-writable.
6. A conflicting range has exactly one matching device-family variant.
7. The value matches the semantic type, storage width, gain, range, precision,
   enumeration, and supported structure policy.

`dry_run=True` performs these checks and returns the encoded bytes without I/O.
It still requires the write and unsafe opt-ins so the same call cannot become
active merely because configuration changes elsewhere.

## Active writes

Active writes are serialized and subject to `minimum_write_interval`. The raw
client uses a confirmed-write request and validates node/address correlation in
the response. Read-back verification is enabled by default and compares exact
bytes. The client does not automatically retry an interrupted write.

Writes are deliberately single-object and sequential. No group-write, commit,
or rollback operation was found in captures or static analysis, and none is
validated on the supported protocol path. OpenRBus therefore does not offer a
transactional `write_many()` facade. Applications coordinating several writes
must treat every confirmed result independently and allow for partial
completion. The not-found result is not proof that no proprietary
implementation can exist.

Read-back proves only that the returned bytes match the request. It does not
prove that the setting is meaningful, persistent, reversible, or physically
safe. A controller may normalize a value, delay its effect, reject it later, or
apply side effects elsewhere.

## Hardware-validation requirements

Before any definition can receive a stronger safety classification, testing
must be explicit, reversible, and independently reviewed. At minimum record:

- exact object address, wire type, starting value, and intended test value;
- device family and relevant firmware without publishing unique identifiers;
- an observation plan and a tested recovery path;
- confirmed response, read-back, physical effect, persistence, and restoration;
- failure behavior for out-of-range and interrupted requests.

Tests must start with a dry run and use the smallest reversible change. No live
write should be performed solely to improve test coverage.
