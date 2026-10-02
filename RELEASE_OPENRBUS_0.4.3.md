# OpenRBus Core 0.4.3 release preflight

Status: **FINAL TEST-HA READ-ONLY GATE PASSED; PUBLICATION IN PROGRESS.** This
report records only the 0.4.3 release. See `RELEASE_OPENRBUS_0.4.2.md` for
the completed 0.4.2 publication history.

## Scope

- Promote editable IAE/RXDX parameters and compatible bounded zone slots into
  regular Core writable catalog rows, subject to explicit HA write enable and
  effective access authorization.
- Support a separate experimental class for registers without an explicit
  `IsReadOnly` declaration. HA exposes this class only when its additional
  default-off option and regular write settings are enabled.
- Retain Core-side explicit read-only, type-conflict, access-level, range,
  enum, and readback protections.
- Core version is 0.4.3; HA pins this exact compatible Core version. No ESP
  firmware or production controller change is part of this release.

## Validation record

The final non-editable Core 0.4.3 wheel and HA candidate passed Core 170 tests
and HA 287 tests. Core Ruff, formatting, strict mypy, compile, build, Twine,
publication and archive privacy audits passed; HA style, compile, JSON and
diff checks passed. The latest Test-HA deployment matched its worktree and
passed the uninterrupted 1,814.7-second read-only gate: 29 active states stayed
fixed, 26 remained available, and no new state became unavailable. Fast,
standard and slow poll groups advanced with no failed items or new error
counters; the proxy stayed healthy. Three baseline states remained
unavailable. The experimental option was returned off. No register-write API
was called during this read-only release validation. Software tests do not
physically validate write behavior on a target installation. These local
checks are complete; external exact-commit workflows remain pending.

## Publication

Publication proceeds only after the pushed exact commits pass their external
CI and validation workflows. Core is published before HA so the pinned
`openrbus==0.4.3` dependency is available from PyPI.
## Final exact-candidate gate update (2026-10-02 16:15 CEST)

The follow-up Core fix exposes the base `write_declared` catalog fact independently of the active RW projection. A fresh 0.4.3 wheel and sdist were built from the current source (wheel SHA-256 `d88e6697141abfc1662445de68b4ee516068b37719b41e77c7fccd96595da714`; sdist SHA-256 `45a0da2c63e65c82bb416f7095da712a555278da4a640cf6d39da3e0d084b`). Core Ruff, formatting, strict mypy, compile, Twine, publication, and archive/privacy audits passed. HA's full suite passed: 286 tests against that fresh wheel.

The exact candidate was installed in the isolated Test-HA with one active process/entry and temporary R1/W0, writes disabled. After setup completed (about 138 seconds), byte checks confirmed 28/28 Core files and 27/27 HA files matched current source. Immediate read-only snapshot was **NO-GO**: 12 of 3,702 active entities available (0.32%), 3,690 unavailable. Fast poll reported 63 successes and 18 aborts; standard poll had 63 aborts while still in progress; slow polling was still in progress. Proxy remained ready with zero queue depth/full count and no session/correlation errors. The 30-minute test was not started and no register write was issued.

The pre-run Core/component, config entry, and entity/device registries were restored from a private backup outside the HA config tree. The original active R3/W3/write-enabled data and empty options map are back; one OpenRBus entry is loaded with 3,167 number, 315 select, and 7 switch entities. This result blocks publication of 0.4.3.
