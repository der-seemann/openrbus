# OpenRBus Core 0.4.2 release preflight

Status: **LOCAL RELEASE PREFLIGHT PASSED; GO for controlled commit/push and external CI validation.**
The exact-address filter candidate completed a clean Test-HA preflight and
1,803.1 seconds of uninterrupted read-only polling on 2026-10-02. Earlier
failed-setup and empty-pollset attempts below are historical and superseded.
Do not tag or publish until the pushed candidate passes GitHub CI, HACS
validation, and Hassfest.

The current filter design is a manually reviewed exact-address map in HA;
runtime FunctionGroup membership remains unproven on this Test-HA and is not
used by the filters. The recorded 3096/3097 and alternate-discovery probes
remain historical evidence only.

## Typed-array runtime validation — 2026-10-01

The current Core wheel was rebuilt and installed in isolated Test-HA. With
the entry at read level 1 and writes off, a read-only request to `3096:00`
returned CANopen abort `0x06020000` (object does not exist). A separate
bounded read-only probe of `3097:00` returned the same abort. Neither array
provided a count or rows, so the typed runtime decoder and tuple-to-profile
association have no live validation on this controller. The HA caller treats
the snapshot as unavailable and falls back to explicit catalog semantics.
This does not establish behavior on other controller families. Because
the filters use reviewed exact-address maps independent of runtime
FunctionGroup membership, the unavailable arrays on this controller do not
change mapped-filter behavior. Unknown or unmapped family members remain a
documented coverage boundary.

## Local verification

- Core suite: 161 passed.
- Core Ruff check and format, strict mypy, bytecode compilation, and
  `git diff --check`: passed.
- Core 0.4.2 wheel and sdist built. `twine check --strict` passed.
- Core publication audit passed. Separate wheel/sdist privacy scans passed
  (38 wheel entries and 95 sdist files).
- HA suite against the rebuilt Core 0.4.2 wheel: 266 passed, with six existing
  Home Assistant/dependency warnings. HA Ruff check, formatting of Paket-2
  changed Python files, compilation, JSON parsing, diff check, and focused ID
  migration regressions passed.
- Core and HA metadata, manifest requirement, and CI pin agree on 0.4.2.

The write path remains fail-closed: the Core requires the `validated`
classification before transport I/O, and no current registry row has that
classification. `allow_unsafe=True` does not bypass the gate.

## Earlier live attempt — 2026-10-01 (superseded)

The revised acceptance rule allows isolated register aborts, decode errors,
and temporary unavailable entities when polling and transport remain healthy
overall and the affected items are recorded. Earlier monitor attempts that
stopped only on an unavailable-entity-set change or an increase in the
per-entry GetList abort counter are not failures under that rule. The abort
counter is raised for an individual GetList item and the adapter retries that
register through the single-read path; session and correlation errors remain
separate transport-health signals.

For that earlier candidate, the Test-HA config entry was initially disabled by
the user. It was backed up, the built Core wheel and HA component were
installed in the isolated test environment, and the entry was briefly enabled
for setup. Setup failed with `ThinGattSessionStateError` because the proxy
rejected CONNECT. Four bounded proxy samples over 45 seconds reported the host,
parent, and link as not ready; queue-full and event-queue counts stayed zero.
The 30-minute gate did not start. The entry's original user-disabled state was
restored and its effective stored policy verifies read 1/write 0/write
disabled; the candidate remains installed in Test-HA. Four additional
payload-free proxy readiness samples after that correction again showed the
host, parent, and link not ready, with empty queues. No heating writes,
production actions, or source changes addressing this runtime failure were
made. The evidence does not attribute the failed CONNECT to candidate code.

Private, payload-free proxy-readiness evidence and private pre-deployment
backups are retained outside the release tree. No raw entity identifiers, BLE
addresses, credentials, network identifiers, or runtime exports belong in a
release artifact.

## Release boundary

The exact-candidate read-only preflight and 1,800-second live requirement are
now satisfied (see final gate below). External CI, HACS validation, Hassfest,
and remaining packaging/privacy checks must still be reviewed on final
commits before publication.

## Earlier exact-candidate HA attempt — 2026-10-02 (superseded)

The exact-address candidate and installed Core wheel were byte-verified in
isolated Test-HA. The first enable attempt had five runtime inventories but no
active poll set; a later enable failed under stale process-local controller
ownership. Those symptoms did not recur after one isolated HA service restart;
their earlier cause remains unproven. See the final gate below.

## Final exact-address live gate — 2026-10-02

HA component files (27 source/config files) and the installed Core 0.4.2
package (28 Python files) matched their worktrees byte-for-byte. The only
OpenRBus entry was temporarily enabled at read 1, write 0, writes off, with
both optional filters off. Discovery completed for five inventories.
Poll-selection diagnostics stayed nonempty at 35 fast / 192 standard / 32
slow pollable registers.

The bounded clean preflight passed in 190.3 seconds over 13 samples, with 421
additional successful reads, all three poll groups advanced, zero new error
counters, and 93.17% availability (19 of 278 entities unavailable at
baseline). The following uninterrupted gate passed at 1,803.1 seconds over
114 polling samples plus baseline/final snapshots (116 private records total).
Pollables remained 35/192/32; read-success deltas were 1,870
fast, 2,590 standard, and 96 slow. Failed-item and error-class deltas were
zero. The active entity set and the 19 pre-existing unavailable entities
remained unchanged. Session epoch/generation stayed at 20/1, recovery-fence
attempts/timeouts at 0/0, and proxy readiness stayed true with no boot change,
pending bootstrap, overflow, or queue buildup.

After the gate, HA API and storage confirmed the entry restored to
`disabled_by=user`, read 1/write 0, write access 0, writes disabled, and both
filters off. No write path or production system was touched. Payload-free
samples and private backups remain in local validation storage outside the
release tree.

## Final package preflight — 2026-10-02

Decision: GO for the next release worker to make the authorized commits and
push the candidate. Hold both tags and all publication until remote CI,
HACS validation, and Hassfest pass on those exact commits.

- Core's full suite passed 166 tests against the freshly built 0.4.2 wheel;
  HA's full suite passed 270 tests against that same wheel. Six focused HA
  tests passed for deterministic IDs, registry migration, filter coverage,
  off/on behavior, and option persistence.
- Core Ruff, full-repository Ruff format, strict mypy 2.4.0, bytecode compile,
  and `git diff --check` passed. HA Ruff, formatting for all 20 changed Python
  files, bytecode compile, JSON parsing (five files), and `git diff --check`
  passed. All five candidate workflow YAML files parsed successfully.
- The Core wheel and sdist built successfully. `twine check --strict` passed;
  archive privacy scan passed for both archives and all 136 members. The Core
  source publication audit passed. HA privacy review found only its expected
  brand icon and test-only address placeholders; no private paths, credentials,
  captures, or original vendor files were present.
- Core and HA release metadata, manifest, and test-workflow pin agree on
  0.4.2. No local or remote v0.4.2 tag/release exists, and PyPI returns 404 for
  Core 0.4.2. Remote `main` remains the published v0.4.1 commit.
- Local HACS/Hassfest executables and `actionlint` are unavailable. The YAML
  syntax check passed; external HACS/Hassfest/CI jobs must run after push and
  before any tag or publication.

After the live gate, only test-fixture initialization, import/format cleanup,
HA diagnostic formatting, Core FunctionGroup formatting, and removal of a
runtime-no-op `typing.cast` were changed. No transport, filter, identity,
polling, or write behavior changed. Full suites and all applicable static
checks above were rerun on the exact built wheel and current candidate.
Nothing was committed, pushed, tagged, or published during this preflight.
