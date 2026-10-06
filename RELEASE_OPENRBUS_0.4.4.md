# OpenRBus Core 0.4.4 release preflight

Status: **candidate prepared; publication pending.** Core 0.4.4 is a required
dependency for the matching Home Assistant integration candidate. Its PyPI
publication must precede the integration release.

## Scope

- Limit synthetic family-array expansion to small, type-consistent heating and
  zone arrays with exact family evidence. Do not infer up to 255 sibling
  objects from unrelated CANopen array bounds.
- Preserve explicitly evidenced subindices, including high-index SCB zone
  slots, while retaining unresolved type conflicts without extrapolation.
- No CANopen wire-format, read/write, or authorization behavior changed.

## Validation

- Python 3.14.4: Ruff check and formatting, strict mypy, 171 tests, publication
  audit, wheel and sdist builds, strict Twine checks, and archive/privacy audit
  passed.
- This local candidate run covers Python 3.14 only. The repository CI matrix
  covers Python 3.11–3.13 and must pass on the release commit.
- The candidate wheel matches the Core catalog source used by the isolated HA
  test environment. No register write was issued as part of the HA validation.

## Publication order

Publish Core `v0.4.4` and make `openrbus==0.4.4` available on PyPI before
publishing the HA integration `v0.4.4`, which pins that exact dependency.
