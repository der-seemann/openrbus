# OpenRBus register registry

`registry-v1.json` is the canonical, normalized public register dataset. It is
licensed under CC BY 4.0; OpenRBus source code is licensed under Apache-2.0.

The dataset contains 3,066 canonical object definitions, German and English
short labels, semantic and storage wire types, exact storage lengths, gains,
units, declared read/write access, available ranges and precision, conservative
write-safety flags, localized enum choices, packed-field names, and sanitized
evidence summaries. It additionally retains
47 configuration-only addresses as **candidates**, never as confirmed canonical
registers.

Evidence categories have deliberately narrow meanings:

- `catalog_definition`: derived from a normalized technical object dictionary.
- `device_configuration`: corroborated by one or more device profile layouts.
- `device_definition`: exposed by one or more device-family definitions.

These categories describe provenance, not live compatibility. Device and
firmware configuration may change availability, type, range, and access. Known
profile-specific type and range conflicts remain explicit instead of being
silently merged into the canonical definition.

All declared-writable registers are currently classified `unverified` and
require the separate unsafe-write opt-in. No live write validation is claimed.

Publication boundary:

- Only normalized technical facts and short DE/EN labels belong here.
- No manufacturer applications, binaries, decompiled code, proprietary source
  documents, long manual passages, encryption material, source paths or hashes.
- No packet captures, sampled values, BLE addresses, serial numbers, credentials,
  customer data, or other runtime identifiers.

The packaged minified copy under `src/openrbus/data/` is byte-for-data
equivalent. `tools/export_registry.py` reads only this public JSON and emits a
compact JSON table or an ESP32-friendly C/C++ header without SQLite. Both
exports retain enumeration labels and packed-structure field names.
