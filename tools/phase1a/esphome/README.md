# OpenRBus ESPHome BLE proxy

This directory contains the public ESPHome source for the optional Thin-GATT
proxy used by OpenRBus. It is a source package, not a universal firmware
image: the device name, target BLE address, Wi-Fi/API/OTA credentials, and
board-specific recovery settings belong to the operator's private ESPHome
configuration.

## What is shipped

* `openrbus-ble-proxy.yaml` is a complete, generic ESP32/ESP-IDF reference
  configuration. It uses `!secret` for every deployment value and contains no
  installation address or network identity.
* `thin-gatt-rpc-optin.yaml` is an additive package fragment for an existing
  ESPHome BLE client. It expects the caller to provide the existing client and
  to own its pairing/CCCD lifecycle.
* `openrbus_gatt_rpc.h` implements the bounded, versioned Native-API RPC
  envelope, scan stream, request IDs, epoch fencing, and event backpressure.
* `openrbus_transport.h` is the optional one-request-at-a-time raw transport
  bridge. It reuses the existing BLE client and never creates a second
  connection or notification subscription.
* `openrbus_zero_write.h` contains the ESP-IDF boundary needed for the
  protocol's zero-length ATT write. Its descriptor-handle assumption is
  specific to the documented reference target; do not use it as a generic
  BLE component without validating the target's GATT layout.

The source is intentionally opt-in. The default ESPHome BLE client remains
the lifecycle and security owner, while OpenRBus owns protocol framing,
authorization policy, object codecs, and Home Assistant state.

## Installation from a tagged release

Pin the repository to an immutable release tag. Do not use `main` for a
production device and do not put secrets in a package or in a Git repository.
For a complete reference configuration, copy the YAML and headers into the
ESPHome configuration directory and create a local `secrets.yaml` from
`secrets.yaml.example`. Set `ble_target_mac` in that private file.

For an existing ESPHome BLE client, include the package fragment and headers
from the same release. The fragment deliberately does not declare a second
`ble_client`; its caller must provide the client ID used by its own YAML
integration. Keep the dynamic transport disabled until the encrypted gateway
session has been authenticated.

ESPHome also supports Git-backed external components and packages. This
release is distributed as an ESPHome package/reference tree rather than a
native Python external-component platform, so consumers must pin the package
files to the release tag and review the included YAML before compiling.

## Requirements

* ESP32-class hardware with BLE GATT client support and a recoverable local
  flashing path.
* ESPHome **2026.8.2** and the ESP-IDF framework. The generated-source gate is
  version-pinned; another ESPHome version requires a fresh review of the
  generated BLE client anchors.
* A target gateway that supports the documented encrypted GATT service and
  protocol handshake. UUIDs and the authorization exchange are protocol
  details, not a promise of compatibility with every BLE device.
* A private ESPHome secrets file containing an API encryption key, OTA
  password, Wi-Fi credentials, fallback AP password, and target BLE address.

## Build and flash

Validate and compile locally from the repository root:

```console
python3 -m pip install "esphome==2026.8.2"
cp tools/phase1a/esphome/secrets.yaml.example \
   tools/phase1a/esphome/secrets.yaml
# Edit the private secrets file; never commit it.
python3 tools/phase1a/esphome_phase1a_compile.py \
  tools/phase1a/esphome/openrbus-ble-proxy.yaml
```

The wrapper runs ESPHome code generation, applies the checked-in ATT ordering
gate, verifies its anchors, and then invokes the ESP-IDF build. Generated
`.esphome` directories, binaries, maps, logs, and device-specific reports
must remain outside the release tree. Flash only after an operator has
verified the target, has a local recovery path, and has performed a dry-run
configuration check. This project does not publish a universal firmware
binary because it would necessarily encode board, network, and target choices.

## Runtime safety boundary

The RPC stream has a 2 KiB envelope limit, 512-byte payload limit, eight-result
bounded scan, 16-frame event queue, one in-flight ATT request, request-ID
correlation, physical-connection epochs, and explicit overflow/disconnect
fencing. `WRITE_CHAR`, descriptor writes, pairing, and dynamic transport are
not enabled by merely installing the package. Native API encryption and the
operator's BLE pairing remain mandatory.

The runtime intentionally does not log passkeys, API keys, Wi-Fi values,
authorization payloads, BLE addresses, serial numbers, or raw production
captures. Diagnostics expose counters, state, epoch, and bounded error codes
only. Do not enable verbose ESP-IDF logging on a shared network without
reviewing the resulting logs.

## Recovery and upgrades

Keep the original ESPHome YAML and a known-good firmware image until the new
build has connected, authenticated, and completed a read-only request. If a
connection is lost, power-cycle or use the board's local serial/recovery path;
do not repeatedly invoke pairing or OTA while a Thin-GATT session is active.
The package does not migrate bonds or secrets. Re-check the target BLE
address, API key, and OTA credentials after every upgrade. Roll back by
flashing the previously verified image through the local recovery path.

## Validation

Offline contract tests do not require BLE hardware or production data:

```console
python3 -m unittest tools.phase1a.test_phase2_transport_static -v
python3 tools/check_publication.py
```

The tests cover bounded input, zero/negative request-ID rejection, handle
refresh, disconnect fencing, CCCD ownership, pairing ordering, and dynamic
session gating. Hardware acceptance is necessarily installation-specific and
must be recorded privately; never commit addresses, serials, credentials,
network details, raw traces, or firmware artifacts.

## License and support

The repository is Apache-2.0 licensed. Report security issues privately using
`SECURITY.md`; do not post credentials, BLE identifiers, or packet captures in
public issues.
