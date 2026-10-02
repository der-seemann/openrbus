# Changelog

All notable changes to OpenRBus are documented here. Version numbers follow
semantic versioning while the project remains in pre-1.0 development.

## 0.4.2 — release candidate

### Added and changed

- Machine-readable per-register comparison of OBD declarations, normalized
  IAE facts, and local manufacturer-configuration metadata is maintained as a
  development audit; metadata alone does not authorize writes.
- HA write classification remains fail-closed unless complete, exact-family
  write evidence is present. No register is currently enabled for writing.

### Compatibility and safety

- Python 3.11 or newer is required.
- No register is claimed safe to write solely because a codec or read-back
  succeeds. See `DISCLAIMER.md` and `docs/writing.md`.

## 0.4.1 — release candidate

### Added

- Calendar date access for CANopen `TIME_OF_DAY` values, without timezone
  interpretation.

### Changed

- Catalogs expose write capability only when validated write safety and
  complete, exact-family evidence support it.
- Direct writes require the same validated safety and evidence checks.

### Compatibility and safety

- Python 3.11 or newer is required.
- The 0.4 API is intended for the Home Assistant integration's current
  compatibility range; downstream integrations should run their full test
  matrix before upgrading.
- No register is claimed safe to write solely because a codec or read-back
  succeeds. See `DISCLAIMER.md` and `docs/writing.md`.

This candidate remains unreleased pending coordinated release checks.

## 0.4.0 — unreleased

### Added

- Thin-GATT transport/session primitives for an injected RPC channel.
- Node-scoped discovery, capability projection, and family-aware register
  catalog helpers.
- Explicit gateway authorization support with caller-supplied key material.
- Reusable publication and distribution-archive privacy audits.

### Changed

- Native BLE transport can select an explicit BlueZ adapter, avoiding implicit
  multi-adapter selection.
- Reads, higher-level access checks, and writes use distinct typed outcomes and
  explicit policy gates.
- Writable operations remain disabled by default and confirmed writes are
  serialized, rate-limited, and read back by default.
- Documentation and examples use synthetic identifiers only.

### Compatibility and safety

- Python 3.11 or newer is required.
- The 0.4 API is intended for the Home Assistant integration's current
  compatibility range; downstream integrations should run their full test
  matrix before upgrading.
- No register is claimed safe to write solely because a codec or read-back
  succeeds. See `DISCLAIMER.md` and `docs/writing.md`.

This section is release-ready but remains unreleased until the coordinated
Home Assistant and ESP proxy components are tagged and published.
