# Changelog

All notable changes to OpenRBus are documented here. Version numbers follow
semantic versioning while the project remains in pre-1.0 development.

## 0.4.7 — release candidate

### Fixed

- Bound Thin-GATT `GetList` responses to the declared CAN-IP wire-size limit.
- Preserve and report bounded `FLOW_CONTROL` transport reasons for diagnostics.

### Compatibility and limits

- The public protocol API remains compatible; bounded responses may require
  callers to request large lists in smaller batches.
- This release does not establish physical write safety.

## 0.4.6 — 2026-10-07

### Changed

- Separate source-backed write classification from physical write-validation
  status. Source-backed regular controls can remain physically unverified.
- Classify positive IAE writable facts with complete, consistent family and
  access-level evidence as regular; permit only bounded, matching family-array
  inference. RXDX SDO presence/type and PCST readonly metadata do not establish
  write permission.
- Keep OBD-only `IsReadOnly=False` declarations experimental. Explicit
  read-only, conflicting, unknown, and incomplete evidence remains blocked.
- Require a complete, unambiguous write access level for regular and
  experimental controls; experimental writes remain separately opt-in.

## 0.4.5 — release candidate

### Fixed

- Bound each Thin-GATT segment dispatch to the remaining end-to-end message
  deadline, including service-action time as well as acknowledgement polling.

## 0.4.3 — release

### Added and changed

- Editable IAE/RXDX parameters with compatible bounded zone slots are exposed as regular controls when write access is enabled. Registers without an explicit read-only declaration can be exposed separately through a default-off experimental option. Read-only declarations, unresolved wire-type conflicts, access-level ambiguity, and invalid values remain blocked.
- The Core registry and write client enforce the same regular and experimental write classification; HA requires its write and experimental opt-ins.

## 0.4.4 — release candidate

### Changed

- Bound family-derived sibling expansion to small, type-consistent heating and
  zone arrays with exact family evidence. Unrelated arrays with a wire bound
  of 255 no longer generate speculative catalog rows; concrete source-backed
  addresses remain available.

### Compatibility

- The CANopen protocol API is unchanged. Catalogs contain fewer inferred array
  aliases where the source evidence does not establish those elements.

## 0.4.2 — release

### Added and changed

- Machine-readable per-register comparison of OBD declarations, normalized
  IAE facts, and local manufacturer-configuration metadata is maintained as a
  development audit; metadata alone does not authorize writes.
- The published 0.4.2 catalog keeps all register writes fail-closed pending
  the candidate classification and control flow introduced in 0.4.3.

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
