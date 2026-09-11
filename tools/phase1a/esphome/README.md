# Phase 1A ESPHome reference

This directory contains the non-secret ESPHome source used for the live-verified
Phase 1A BLE/OpenRBus proxy. The productive source of truth remains the YAML
under Home Assistant (`/config/esphome/heizungskeller-ble-proxy.yaml`); this
copy is the reproducible, reviewable reference for the verified build.

## Build

The reference build is pinned to ESPHome **2026.8.2**. Use an ESPHome
environment with that exact version and create a local `secrets.yaml` beside
the YAML from `secrets.yaml.example` (the file is intentionally ignored).

From the repository root:

```text
python tools/phase1a/esphome_phase1a_compile.py \
  tools/phase1a/esphome/heizungskeller-ble-proxy.yaml
```

The compile wrapper generates ESPHome C++, applies the deterministic
`esphome_phase1a_gate.py` patch, verifies its anchors, and then compiles with
ESP-IDF. The patch is deliberately tied to ESPHome 2026.8.2; an upstream
version change must be reviewed explicitly.

## ATT sequence and reconnect fix

The EHC requires notification CCCDs to be written only after the BLE link is
MITM-encrypted. The Phase 1A gate defers CCCD registration until the security
completion callback, then performs the required empty write only after CCCD
completion. This preserves the known-good sequence:

`connect → service discovery → security/encryption → CCCD registration → CCCD completion → empty IdentInfo write → gateway auth → OpenRBus read`

The manual diagnostic handle lookups in `on_connect` use ESPHome's raw UUID
byte order. The reconnect failure was caused by using the opposite order,
which produced zero notification handles after reconnect. The corrected
lookups are kept in the YAML; the separate historical IdentInfo trigger UUID
is intentionally unchanged.

The reference read is the safe, read-only request `FF / 2001:02` (device type).
The expected live response decodes to `7702`. Use the direct verifier in
`tools/phase1a/verify_live_read.py` only against an explicitly selected proxy;
never run it concurrently with a production HA coordinator.

## Deployment and source boundaries

OTA/deployment still uses the Home Assistant ESPHome installation and its
local secrets. Do not commit API keys, BLE PINs, Wi-Fi credentials, private
keys, generated build trees, or captured production secrets. A changed secret
or build timestamp may change the ESPHome config hash; reproducibility here
means equivalent source and behavior, not a fixed hash.
