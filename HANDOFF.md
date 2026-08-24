# OpenRBus release handoff

Stand: 2026-08-24. This records the fully verified initial public release with
the final GetList and `read_many()` implementation.

## Completed

- Chosen project/package name: `openrbus`. At the time checked,
  `der-seemann/openrbus` did not exist on GitHub and `openrbus` returned no
  PyPI project.
- Fresh Git repository created without importing the legacy repository's
  history.
- License decisions recorded: Apache-2.0 for code; CC BY 4.0 for the normalized
  register dataset.
- Authentication publication audit completed. A cross-install fixed/override
  access path cannot be excluded, so no pairing PIN, unlock algorithm,
  manufacturer authentication constant, or service credential is included.
  The public BLE adapter only provides an injected authorizer boundary.
- Public registry generated from the private working data:
  - 3,066 canonical registers and 47 legacy candidates
  - German and English short labels
  - semantic/storage wire types, lengths, gains, units, declared access,
    constraints, enums, structures, device-family evidence, and conflicts
  - 2,832 declared-writable definitions, all conservatively classified as
    unverified and requiring unsafe opt-in
  - deterministic compact JSON and C/C++ header exports
- Initial async implementation added for:
  - validated BLE segmentation and CRC
  - transparent selector plus CAN-IP read/write/batch framing
  - separate, non-segmented RUB codec (not inserted into the active BLE path)
  - optional Bleak transport, bounded reconnect, raw object access
  - assignment/capability discovery
  - registry-driven scalar, enum, struct, string, octet, and time-of-day codecs
  - high-level read client and write gates (`enable_writes`, `allow_unsafe`,
    `dry_run`, validation, rate limit, audit logs, read-back verification)
- Corrected the CAN-IP function-8/function-9 codec to the hardware-validated
  GetList wire layout: one-byte count, repeated five-byte object descriptors,
  16-bit response lengths, per-item status, and a 100-object GTW35 limit.
- Added `read_many()` with an optional bulk object-access capability, same-node
  batching, count/response-size partitioning, single-read fallback for oversized
  values, preserved result order, and per-object abort outcomes.
- Changed BLE receive handling to queue every notification segment before
  ordered reassembly; rapid multi-segment GetList responses cannot be replaced
  by a last-value callback.
- Initial CI, pre-commit configuration, publication scanner, ignore rules, and
  synthetic tests added.
- Added the stable package facade, installation README, SECURITY, DISCLAIMER,
  CONTRIBUTING, and architecture/protocol/registry/write-boundary documentation.
- Packaging metadata now uses the PEP 639 license expression without a redundant
  classifier. The sdist contains the public registry, documentation, tools, and
  both license sets; the wheel carries Apache-2.0 and CC BY 4.0 license files.
- Added a non-extracting wheel/sdist audit and included it in CI.
- Final release verification is green on Python 3.12:
  - editable development installation
  - ruff lint and format check
  - strict mypy over `src/openrbus`
  - 46 synthetic tests with no skips
  - tracked-file publication audit
  - wheel and sdist build
  - archive audit: 2 archives, 89 files
  - clean-clone installation and the complete quality suite
  - all 46 tests from the unpacked sdist
  - isolated wheel import/registry/`read_many` smoke test

## HANDOFF audit

- `README.md` exists and packaging installation succeeds.
- The two original failing test assumptions were corrected: enum value 14 is
  the invalid case and `Scb-10` non-writability is asserted rather than bypassed.
- `src/openrbus/__init__.py` provides the stable public facade, now including
  the bulk-read result types.
- SECURITY, DISCLAIMER, CONTRIBUTING, data provenance/license notes, and the
  protocol, registry, writing, architecture, and proprietary-format documents
  are present. Protocol/architecture/write docs now cover GetList and the lack
  of transactional writes.
- The former instruction to archive `thermea-ble` is obsolete. That repository
  is the canonical private research tree and remains intact and unmodified by
  this release work; its private sources never enter OpenRBus.

## Known incomplete state

- CI is configured for Python 3.11, 3.12, and 3.13, but only Python 3.12 was
  available for this local verification.
- No real write was performed. Confirmed-write framing has independent
  evidence, but safe reversible hardware validation remains deliberately
  postponed.
- Authentication is deliberately incomplete in public code. Fresh devices may
  require owner-controlled pairing/authorization outside OpenRBus before the
  transport is usable.

## Post-publication follow-up

1. Let hosted CI verify the Python 3.11/3.12/3.13 matrix.
2. Perform a safe reversible hardware write only as a separate, explicitly
   approved validation task.
3. Do not delete or otherwise modify any old GitHub repository without David's
   separate explicit approval.

## Publication boundaries

Never add vendor apps/binaries, decompiled code, proprietary source files or
formats, keys, authentication constants, real BLE identifiers, serial numbers,
credentials, customer data, captures, or raw runtime values. The legacy repo
and all private research material remain outside this fresh repository.
