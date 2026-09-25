# ESP proxy release review — 0.4.0

Status: release preparation only. No commit, tag, GitHub release, PyPI upload,
OTA update, or physical flash was performed by this review.

## Authoritative source and release shape

The ESP proxy is shipped as the ESPHome source package in
`tools/phase1a/esphome/` of `der-seemann/openrbus`. The local
`openrbus-ble-gateway` directory is a standalone ESP-IDF proof of concept with
no configured remote and is not a release input. The release should contain
source and documentation only; no universal firmware binary is safe because
the image necessarily depends on the board, target BLE address, Wi-Fi, Native
API key, OTA password, and recovery path.

The Python package version is `0.4.0`; the ESPHome package has no independent
runtime version field. Consumers must pin the complete source tree to the
GitHub tag `v0.4.0` and keep all headers and YAML from that same tag.

## Review scope and findings

Reviewed the Thin-GATT RPC envelope, ESPHome YAML integration, Native API
actions, ESPHome BLE-client callback boundary, CCCD/IdentInfo sequencing,
request/epoch correlation, queue and payload limits, disconnect fencing,
pairing state, dynamic transport, diagnostics, and build/recovery boundary.

The current source has bounded frames (2 KiB), payloads (512 bytes), scan
results (8), event queue (16), known handles (64), one in-flight ATT request,
timeouts, physical epochs, stale-callback checks, and explicit overflow
disconnect. Runtime writes remain opt-in and require an authenticated session.
Diagnostics do not include credentials or raw payloads.

The review also closed a liveness gap: asynchronous character, descriptor, and
CCCD operations now have a 10-second deadline and emit one correlated timeout
before releasing the single-request slot when a callback is lost.

One deliberate limitation remains: `openrbus_zero_write.h` gates the empty
IdentInfo write on descriptor handle `0x0022`. That is valid only for the
documented reference GATT layout; it is not a generic BLE implementation.
The release documentation now states this explicitly. A future generic
component must discover and pass the actual CCCD handle instead of relying on
that fixed layout.

The Thin-GATT gateway TEA constant is a protocol interoperability constant,
not an installation credential. Installation-specific authorization keys,
passkeys, Wi-Fi/API/OTA secrets, and target identifiers remain outside the
repository.

## Privacy and publication audit

The current release tree uses the generic `openrbus-ble-proxy.yaml`, keeps the
target address in `!secret ble_target_mac`, uses fictional test identifiers,
ignores generated/private ESPHome snapshots, and removes the old live
validation artifact. `python3 tools/check_publication.py` passes. No firmware,
map, log, capture, recovery directory, private YAML, or generated build tree is
part of the release input.

Important blocker: the already-published Git history still contains the former
installation MAC, the former installation name, and a private verifier IP.
This report does not recommend a force-push or history rewrite. Before a public
`v0.4.0` release, either publish from a newly sanitized repository/history or
obtain explicit approval for a coordinated history migration and invalidate
the old references. A normal tag on the existing history does not satisfy a
strict “no installation data in history” requirement.

## Documentation changes

`tools/phase1a/esphome/README.md` now documents the package/reference model,
immutable tag pinning, prerequisites, ESPHome/ESP-IDF version pin, local
secrets, build/flash/recovery, safety limits, upgrade/rollback, and privacy.
The complete example is generic and the opt-in package remains explicitly
opt-in. The reference source was renamed from the installation-specific YAML
name to `openrbus-ble-proxy.yaml`.

## Tests and checks

Completed without hardware access:

* ESPHome static contract tests: 11 passed.
* Combined Thin-GATT tests and ESP static tests: 18 passed.
* Additional selected RPC/session contracts: 43 passed; unittest contracts: 13 passed.
* Ruff check and format check for changed Python review tools: passed.
* `git diff --check`: passed.
* publication/secret/tree audit: passed.

* Representative ESPHome **2026.8.2** / ESP-IDF **5.5.5** `esp32dev`
  compile with fictional temporary secrets: passed. No flash or OTA was run.
  The temporary build output stayed outside the release tree and was scanned
  for installation identifiers before disposal. The generated firmware is not
  a release artifact.

Source hashes for this review snapshot:

```text
openrbus_gatt_rpc.h       7aa6ec0a208866880e020f3f7bbfba0962ed5172d264360008644ef664c76299
openrbus_transport.h      803fbbd8c60bb039616077082df2db9e9baf4b3adae60efd045a55847ead4643
openrbus_zero_write.h     6f88a36ffa57f1ad88adfc419bd25e9c5244964096f53501d73583cf685c268b
openrbus-ble-proxy.yaml   26a843892ea8f77c0ce429edad9164dd00e232d741b7b1cf7c34f677829e7f3f
```

## GitHub release preflight

Before release, on the sanitized release commit:

1. Run the full Python/HA/ESP static suites and an isolated ESPHome 2026.8.2
   compile using placeholders or local secrets that never enter artifacts.
2. Review `git archive v0.4.0` and the complete reachable history for secrets,
   addresses, private paths, binaries, traces, and device names.
3. Create an annotated immutable tag `v0.4.0` on the sanitized commit and use
   GitHub's release notes to link the changelog and limitations.
4. Publish source archives only. Do not attach a firmware image unless a
   separately reviewed, board-specific artifact can be proven secret-free.
5. The PyPI publication belongs to the Python package; the ESP proxy is not a
   separate PyPI distribution.

ESPHome's external component documentation recommends pinning Git sources to a
release/ref rather than tracking a mutable branch. GitHub releases are based
on tags and should be treated as immutable publication points.
