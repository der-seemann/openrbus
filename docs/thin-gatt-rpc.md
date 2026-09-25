# Thin ATT/GATT RPC spike

Status: design spike only; the validated ESPHome runtime and firmware are not
changed by this document or by `tools/thin_gatt_rpc.py`.

## Decision

Use the existing encrypted ESPHome Native API as the control/event channel. A
single custom service accepts `(request_id: uint32, frame: string)` and the
device publishes a bounded JSON-lines stream through one dedicated text
sensor. Native API already supplies discovery, reconnect, encryption and
Home-Assistant ownership. A new TCP/WebSocket/MQTT server would duplicate
TLS/authentication, socket lifecycle, provisioning and OTA maintenance on the
ESP, so it is not justified for ATT-sized traffic.

The custom service is one-way (`CustomAPIDevice` dynamic services do not
support action responses). Therefore responses and unsolicited events are
explicit envelopes on the stream, not assumptions about action-call return
ordering. The Python caller allows one BLE transaction in flight per physical
connection. The event stream has a fixed ESP ring (16 envelopes); sequence gaps
are fatal to a transaction and force reconnect/resubscribe rather than silently
using stale state.

## Envelope

Every frame is compact JSON, UTF-8, version `v: 1`, and at most 2048 bytes:

```json
{"v":1,"kind":"request","op":"WRITE_CHAR","epoch":7,"request_id":42,
 "payload":{"handle":123,"value":"","response":true,
 "expect_notification":true}}
```

`kind` is `request`, `response`, or `event`. Request IDs are non-zero uint32
and are never reused while a connection is alive. `epoch` is zero only for
`CONNECT`; ESP increments it for every successful physical connection and
copies it to all subsequent frames. Python rejects a response/event whose
epoch is not the current one. Events have a monotonic `seq` per epoch.
Binary values are base64 and individually limited to 512 bytes. Empty base64
(`"value":""`) is valid and means an ATT zero-length write. The ESP adapter
must pass a non-null dummy pointer to ESP-IDF while retaining length zero; the
RPC payload must not encode a fake byte.

## Operations

Requests are:

* `CONNECT {address}`; `DISCONNECT {reason}`
* `PAIR_ENCRYPT {mode}` (no PIN or key in a loggable envelope)
* `DISCOVER {service_uuids?}` or `HANDLE_LOOKUP {service, characteristic}`
* `WRITE_CHAR {handle, value, response, expect_notification?}`
* `WRITE_DESCRIPTOR {handle, value, response}`
* `SUBSCRIBE {value_handle, cccd_handle, enable}`
* `CANCEL {request_id}`

Responses contain the matching `request_id`, `epoch`, `status` (`OK`,
`WRITE_FAILED`, `TIMEOUT`, `CANCELLED`, or `ERROR`) and a bounded payload/error
code. Events are `CONNECTION_STATE`, `ENCRYPTION_STATE`, `NOTIFICATION`,
`BOND_STATE`, and `FLOW_CONTROL`. A notification has `{handle,value}` and
`request_id: 0` when it is unsolicited; when a write requested a response,
the ESP associates the first matching-characteristic notification with that
request ID. Correlation is on `(epoch, handle, request_id)`, never callback
ordering. A late `WRITE_CHAR_EVT` after notification resolution is ignored.

## Ownership and lifecycle

Python owns OpenRBus/CAN-IP framing, BLE segmentation above the ATT payload,
registry/discovery policy, object codecs, retries, read/write authorization,
and HA state. ESP owns only BLE host calls and their unavoidable callbacks:
connect/disconnect, security request, service/characteristic/CCCD lookup,
ATT write, notification callback, bond removal, and epoch/sequence fencing.
ESP does not know OpenRBus, CAN-IP, gateway/session/registry/discovery state,
or object semantics.

`PAIR_ENCRYPT` is an explicit lifecycle operation. Bond status is reported as
`none`, `bonded`, or `removed`; the ESP never logs or persists the passkey in
the RPC layer. On link loss it emits `CONNECTION_STATE=disconnected`, cancels
all pending operations, and drops subscriptions. Python reconnects with a
new epoch, performs encryption check, discovers/looks up handles, and
resubscribes before issuing writes. A disconnect races every late callback;
epoch and pending-ID checks make those callbacks harmless.

## Backpressure, timeouts, and errors

There is one serialized ATT operation per connection and a fixed 16-frame
outgoing ring. Python sends the next operation only after a terminal response;
notification consumers must drain promptly. Overflow emits `FLOW_CONTROL` and
the Python side treats the connection as unusable. The ESP boundary uses a
10 s deadline for every asynchronous ATT write, descriptor, subscription, or
notification transaction. Suggested end-to-end deadlines are 20 s connect,
10 s pair/encrypt, 5 s discovery/handle lookup, and caller-selected bounded
notification wait. Timeout sends `CANCEL`
best effort, then disconnects if the transaction cannot be fenced.

Error codes are stable lowercase values: `not_connected`, `stale_epoch`,
`busy`, `invalid_handle`, `invalid_payload`, `gatt_write_failed`,
`security_required`, `not_supported`, `timeout`, `cancelled`, and
`event_overflow`. They are transport errors only; OpenRBus/CANopen errors stay
inside Python's existing protocol layer.

## Throughput and limits

The current bridge accepts up to 64-byte request and 512-byte response
payloads. This generic boundary keeps ATT payloads at 512 bytes and wire JSON
at 2048 bytes, enough for one segmented CAN-IP message and ordinary GATT
notifications without allocating an unbounded ESP buffer. At one outstanding
request, throughput is bounded by BLE connection interval plus one Native API
round trip; it is not a bulk data plane. If future use needs sustained
notifications, add explicit chunking/window ACKs to version 2 rather than
raising these limits silently.

## Prototype evidence and acceptance tests

`tools/thin_gatt_rpc.py` and `tests/test_thin_gatt_rpc.py` run without ESPHome,
BLE, or OpenRBus dependencies. They cover:

1. valid zero-length writes preserve empty payload semantics;
2. notification-before-write-complete resolves exactly once and the late
   callback cannot create a second response;
3. disconnect clears pending operations and rejects stale-epoch callbacks;
4. cancellation, binary-size and JSON-frame limits; and
5. unsolicited notification routing and monotonic event sequence.

This proves the ordering/fencing contract, not radio interoperability. A
hardware acceptance pass remains required for encrypted reconnect, CCCD
write, real BDR-Thermea notification handles, Native API stream delivery, and
ring-overflow behavior.

## Migration compatibility

Keep the existing `openrbus_raw_read(request_id, frame)` action and its
response/generation entities during migration. Implement the new action as a
separate additive service and stream; Python can select the generic RPC only
after seeing a `v:1` capability event. On rollback, disable the generic action
and leave the known-good raw-read path untouched. No migration, OTA, or
production enablement is part of this spike.

## Bounded tracker scan (v1)

The canonical ESPHome source is
`tools/phase1a/esphome/openrbus_gatt_rpc.h`, included by the reference YAML.
`SCAN` consumes the already-running ESPHome tracker raw-advertisement stream;
it does not create a second scanner or connection owner. Results are
deduplicated by address, capped at eight entries, and include address, name,
RSSI, and address type. A request is bounded to 250--10000 ms and emits a
correlated `SCAN_RESULT` sequence followed by exactly one `SCAN_DONE`; a
matching `CANCEL` emits a correlated terminal cancellation.

The header and YAML are the reviewable source of the image. Build provenance
must record their SHA-256 values and the resulting ESPHome artifact hash in a
private validation record; an older live `SCAN accepted` observation without
this tuple is not evidence for the implementation. The exact ESPHome version
is pinned by the companion build gate and must be reviewed when it changes.
The bounded acceptance sequence is documented in
[`live-validation.md`](live-validation.md); no installation-specific trace or
artifact digest belongs in this repository.

### Session-independent discovery contract

`SCAN` is accepted before `CONNECT` and before a target address is selected.
The handler consumes the existing ESPHome tracker callback registered by this
component; it does not create a scanner, GATT connection, epoch, or connection
owner. The request and every `SCAN_RESULT`/`SCAN_DONE` envelope use
`epoch=0`, `gattc_if=0`, and `conn_id=0` and remain correlated by
`request_id`. `CONNECT`, handle lookup, pairing, and all ATT operations retain
the existing target-specific session and identity checks. HA must therefore
issue `SCAN` directly with zero routing metadata and may only offer addresses
returned by that bounded result stream for subsequent target selection.

## Complexity and removals

ESP work is a small adapter around existing `BLEClientNode` callbacks plus a
ring, JSON envelope validation, handle cache and epoch fencing (roughly one
isolated component; no protocol tables). Python adds one Native API stream
adapter and maps it to the existing `AsyncMessageTransport`/access interfaces.
The generic design removes the ESP gateway-auth action, OpenRBus frame
constants, read-generation state, registry/discovery state, and gateway/session
state machines. It does not remove ESPHome's BLE security/bond state or the
unavoidable callback/connection lifecycle.
