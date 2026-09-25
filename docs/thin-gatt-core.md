# Thin-GATT Core transport

`openrbus.transport.thin_gatt` is the transport-neutral Thin-GATT boundary
used by the Home Assistant integration. It owns session fencing, request and
notification correlation, secure link lifecycle orchestration, and segmented
message transport. BLE/native RPC adapters remain outside Core and implement
the small `ThinGattRpcChannel` protocol.

## Release provenance

The implementation is maintained as part of the public OpenRBus package and
contains no firmware, captures, credentials, installation identifiers, or
adapter-specific defaults. Keep exact internal source paths and deployment
digests in private validation records; they do not belong in a release tree.
The package version is defined once in `pyproject.toml` and must match the
consumer integration's compatibility range.

The public exports are intentional: `ThinGattSession`, `ThinGattLink`, and
`ThinGattMessageTransport`, their data/error types, and the injected channel
contract are available from both `openrbus.transport.thin_gatt` and the
`openrbus.transport` facade. The package regression test verifies these
imports so a wheel cannot silently omit the module again.
