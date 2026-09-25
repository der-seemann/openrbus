# Validation record template

This document defines the evidence to capture when validating a Thin-GATT
transport build. It intentionally contains no installation identifiers,
addresses, network details, timestamps from a private system, firmware image,
or runtime trace.

## Required provenance

Record the source revision, the exact ESPHome/toolchain version, and SHA-256
digests for the reviewed header, configuration template, and generated image
in a private validation record. Do not commit those values when they identify
an occupied installation or an unreleased device image.

## Bounded acceptance sequence

Use a synthetic test fixture or an isolated test device:

1. `CONNECT` returns `OK` and a connection-state event is observed.
2. A bounded `SCAN` returns `ACCEPTED` and exactly one correlated terminal
   `SCAN_DONE` event.
3. A matching `CANCEL` returns a correlated `SCAN` cancellation followed by
   `CANCEL` `OK`.
4. A subsequent bounded scan completes, proving that cancellation leaves the
   scanner reusable.

The acceptance record must state whether a scan result was observed. An empty
result is a physical observation, not evidence that the handler is missing.
Never publish BLE addresses, private IPs, hostnames, serials, credentials,
raw captures, or production traces.

## Release gate

The release is blocked until the sequence above passes against the exact
source revision intended for publication, and the public-tree and built-
artifact privacy audits pass.
