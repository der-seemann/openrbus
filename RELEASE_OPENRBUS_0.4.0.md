# OpenRBus 0.4.0 release-readiness report

Status: prepared for coordinated release review; no commit, tag, GitHub
release, PyPI upload, or push was performed by this review.

## Scope and decision

This report covers the Python Core repository (`openrbus`) at the current
working-tree revision. The requested release target is `0.4.0`, coordinated
with the Home Assistant and ESP proxy components. The Core package is pure
Python and declares Python 3.11+ compatibility.

The source tree is not yet independently publishable until the coordinated
release owner confirms the final clean commit, the other two component
versions, and the live CI/package preflight. This report intentionally does
not contain installation identifiers, private network data, credentials,
runtime traces, or artifact digests from an occupied installation.

## Requirement matrix

| Requirement | Core status | Evidence / remaining action |
| --- | --- | --- |
| Single source version | Ready | `pyproject.toml` is `0.4.0`; publish workflow defaults match `v0.4.0`. |
| Public API and safety review | Reviewed | Explicit access policy, write opt-ins, per-node effective-level checks, sequential confirmed writes, read-back, and typed errors are present. |
| BLE transport lifecycle | Reviewed | Request serialization, notification queues, segmentation reset, bounded timeouts, explicit BlueZ adapter selection, and disconnect cleanup are covered by tests. |
| Thin-GATT lifecycle | Reviewed | Capability, epoch, identity, request correlation, sequence fencing, cancellation, encryption proof, handle ownership, and gateway-auth boundaries are explicit. |
| Catalog/discovery | Reviewed | Runtime capability evidence is separated from static registry projection; unknown family/access evidence fails closed. |
| Privacy audit | Ready for current tree | Publication and archive audits now reject private IPv4/home paths/MACs and ignore local validation workspaces; only the documented synthetic MAC fixture is allowed. |
| Historical privacy | Blocker to claim clean history | Older public commits contain installation-specific BLE/IP/name/path material. Do not rewrite or force-push history without maintainer authorization; release from a cleaned commit and disclose the history limitation. |
| Documentation | Ready pending coordinated review | README, access/write policy, Thin-GATT docs, ESP reference naming, validation template, disclaimer, security policy, and changelog were updated to avoid installation data. |
| Packaging metadata | Ready pending build | PEP 621 metadata, SPDX license expression/files, package data, optional BLE/dev extras, URLs, and `py.typed` are declared. |
| CI/release workflow | Ready pending CI run | CI covers Python 3.11/3.12/3.13, Ruff, mypy, tests, publication audit, build, and archive audit. PyPI workflows use OIDC trusted publishing and tag/version validation. |

## Code-review findings

No new release-blocking correctness defect was identified in the reviewed Core
changes. The most important safety properties are:

- no bundled key, PIN, default secret path, or implicit authorization source;
- authorization key material is caller-supplied and excluded from repr/logs;
- higher access levels require explicit local policy and live `4002:00` proof;
- writes require constructor and call opt-ins, type/range/family checks, a
  minimum interval, serialization, confirmed transport, and optional default
  read-back;
- bulk reads preserve order and return per-object aborts without masking other
  values;
- transports clear queues/reassemblers and stop notifications on failures and
  disconnects;
- Thin-GATT rejects stale/future epochs, identity changes, sequence gaps,
  unsolicited responses, flow-control desync, and unproven encryption.

Residual operational risks are intentional and documented: this is Python
software controlling heating equipment; a successful protocol write or
read-back does not establish physical safety, persistence, or reversibility.

## Privacy and artifact boundary

Current-tree checks pass after replacing runtime validation material with
synthetic values and moving the live validation record to a generic template.
Local build/review directories are ignored. The full public history still
contains older installation identifiers; history rewriting was not performed.
Before release, inspect the exact commit and both archives again, including
all files added by the coordinated HA/ESP changes.

The observed Home Assistant test topology contained 1,799 OpenRBus entities.
That is a node- and catalog-dependent validation result, not a universal
entity-count promise for other installations.

## Tests and static checks

Completed in this environment:

- `python3 -m compileall -q src tests tools`
- `python3 -m py_compile tools/check_publication.py tools/check_artifacts.py`
- `python3 tools/check_publication.py` — passed
- `git diff --check` — passed
- TOML metadata parse — passed
- read-only remote/default-branch/tag/workflow inspection — completed

Not completed here because the environment lacked an installed test/build
toolchain and the attempted temporary install exceeded the available disk
quota:

- full `pytest`, Ruff, mypy;
- clean `python -m build` sdist/wheel;
- `twine check --strict`;
- clean-venv wheel/sdist installation smoke test;
- archive privacy audit on the final coordinated artifact set.

The release owner must run these in CI or a clean release environment and
attach the resulting package hashes to the private release record, not this
public report.

## Packaging guidance

The preflight follows PyPA guidance to build both sdist and wheel with
`python -m build`, validate with `python -m twine check --strict`, and publish
through PyPI Trusted Publishing from GitHub Actions. References:

- <https://packaging.python.org/en/latest/guides/writing-pyproject-toml/>
- <https://packaging.python.org/en/latest/flow/>
- <https://packaging.python.org/en/latest/discussions/setup-py-deprecated/>
- <https://packaging.python.org/en/latest/guides/publishing-package-distribution-releases-using-github-actions-ci-cd-workflows/>

## Files changed for Core release preparation

- `pyproject.toml`
- `.github/workflows/publish-pypi.yml`
- `.github/workflows/publish-testpypi.yml`
- `.gitignore`
- `README.md`, `CHANGELOG.md`, `DISCLAIMER.md`, `SECURITY.md`
- `docs/access-policy.md`, `docs/writing.md`, `docs/thin-gatt-core.md`,
  `docs/thin-gatt-rpc.md`, `docs/esp32-feasibility.md`,
  `docs/live-validation.md`
- `tools/check_publication.py`, `tools/check_artifacts.py`
- synthetic test/config identifiers and the generic ESPHome reference name

Other modified Core source/test files were already dirty when this review
started; they were not reset or discarded.

## Exact remaining blockers

1. Run the complete release preflight in a clean environment and resolve any
   CI/test/build/lint/type failures.
2. Complete the coordinated HA and ESP proxy review and align all three
   versions and compatibility ranges.
3. Review the final staged tree and generated archives for private data.
4. Decide how the maintainer wants to disclose older public-history privacy
   material; no history rewrite or force-push is authorized by this review.
5. Only after the above, commit, tag `v0.4.0`, push, create the GitHub release,
   and invoke the OIDC PyPI workflow.
