# OpenRBus Core 0.4.5 release preflight

Status: **candidate prepared; publication pending exact-commit CI.** Core
publication must complete before the matching Home Assistant integration
release, which requires `openrbus==0.4.5`.

## Scope

- Bound each Thin-GATT segment service dispatch to the remaining end-to-end
  request deadline, including action execution and acknowledgement polling.
- Keep the legacy one-frame ESPHome poll endpoint and add a FIFO batch endpoint
  with an eight-frame maximum and 16 KiB serialized response limit.
- Preserve the public Core API and the old-firmware polling fallback in the HA
  adapter. The ESPHome build is a source artifact only; the OTA image is
  device-configured and must remain private.

## Validation

- Core full suite, formatting/lint, strict mypy, compilation, publication
  audit, wheel/sdist build, strict Twine validation, and archive/privacy audit
  must pass locally and on the exact pushed commit.
- The candidate firmware source was built with ESPHome 2026.8.2 and ESP-IDF
  5.5.5. This does not establish that the source matches the active proxy
  image, or that any candidate image was flashed.
- The isolated Test-HA read-only gate is qualified for release preparation.
  During the 21m42 quiet observation the cumulative queue-full count remained
  flat at 2, poll groups settled, and availability delta was zero. Epochs
  advanced and no matching baseline establishes the recovery-fence cause.
  There were 30 cumulative standard poll aborts and five decode failures; no
  physical write safety was tested.

## Publication order

Pass exact-commit Core CI and publish `v0.4.5` to PyPI through the trusted
publisher. Verify public artifacts and a normal-index install before releasing
the HA integration pinned to this exact Core commit. Publish both GitHub
releases only after their exact-commit workflows pass.
