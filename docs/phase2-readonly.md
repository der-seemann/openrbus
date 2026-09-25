# Phase 2 read-only transport

The runtime bridge accepts one raw OpenRBus request at a time over the
ESPHome Native API.  OpenRBus remains responsible for CAN-IP framing, CRC,
registry decoding and units; the ESPHome component only transports bytes.

## Discovery and capability semantics

The core discovery implementation uses the bounded `1f85` assignment
directory, then reads `2001:02`, `2001:05` and `300f:00` for each assigned
node.  It does not scan all 256 node addresses.  The optional `5826` directory
is retained as opaque capability references until its flags have a confirmed
meaning.

The live gateway target `FF` currently confirms `2001:02` (device code `7702`).
The candidate objects `5013:00`, `5016:00` and `501E:00` returned the protocol
negative response `0x06020000` at that target; they are therefore classified as
`NOT_SUPPORTED`, not as transport failures or HA entities.  They must only be
retried after a real bus node has been discovered.

## Single and batch reads

`RawObjectClient.read_many_raw` uses the validated CAN-IP `GET_LIST` function
when the response-size limits permit it, preserving per-object aborts and
falling back to sequential reads when necessary.  `OpenRBusClient.read_many`
decodes those results with partial-success semantics.  This is distinct from
the ESPHome runtime queue, which intentionally remains one request in flight.

Native `GET_LIST` support is implemented and historically hardware-validated;
live validation through the current ESPHome gateway remains a separate test
because the runtime notification size is bounded.
