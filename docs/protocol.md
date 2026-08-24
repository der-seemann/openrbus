# Protocol status

OpenRBus labels protocol knowledge by evidence strength:

- **Hardware-validated:** exercised against compatible equipment with
  independently checked request and response behavior.
- **Derived:** reconstructed from static technical evidence or normalized
  records, but not fully exercised on hardware.
- **Hypothesis:** plausible behavior kept out of the active path until further
  evidence exists.

## Active BLE/CAN-IP path

The following behavior is hardware-validated for the supported transparent BLE
service:

1. A complete CAN-IP message is prefixed with selector byte `0x01`.
2. BLE application segments contain a one-byte marker and up to 19 payload
   bytes at the default 20-byte value size.
3. Non-final markers increment from zero; `0xff` marks the final segment.
4. The reassembled message ends with a big-endian representation of a
   CRC-16/Modbus value calculated over the message.
5. CAN-IP generic-purpose messages use a six-byte header and a minimum encoded
   size of ten bytes.
6. Single-object reads correlate node, index, and subindex in the response.

The hardware-validated function-8/function-9 `GetList` layout is implemented as
a codec. A request payload starts with a one-byte object count, followed by one
five-byte descriptor per object:

```text
count (u8)
repeat count times: 00, node (u8), index (u16 big-endian), subindex (u8)
```

A response payload starts with a one-byte result count. Each variable-size
entry is:

```text
status (u8), node (u8), index (u16 big-endian), subindex (u8), reserved (u8),
value_length (u16 big-endian), value (value_length bytes)
```

Status 1 denotes a value. Status 2 carries a little-endian CANopen abort code
in the value field. The validated GTW35 limit is 100 objects and an internal
message-size limit of 1512 bytes. Current evidence covers same-node lists, so
OpenRBus splits on node and uses declared registry lengths for size planning.
This is an arbitrary object list, not a contiguous range read.

BLE notifications carry response segments; they are not object subscriptions.
The transport queues every notification segment before reassembly. No
unsolicited object traffic or usable subscribe/unsubscribe path has been
validated on the supported BLE gateway path.

Confirmed-write construction and response correlation have independent
evidence, but real write safety has not been established. The codec can build
frames; the high-level client still blocks writes by default. Captured writes
are individual confirmed operations with one positive or negative result per
object. No batch-write or transactional commit/rollback mechanism was found in
captures or static analysis, and none is validated or exposed by OpenRBus. This
is a not-found result, not proof that no proprietary implementation can exist.

## Discovery

Assignment discovery reads the statically derived `1f85:00` directory and
checks assigned subindexes. Capability discovery reads the hardware-validated
`5826` directory. Capability flag bits are preserved as raw values because
their datatype and access semantics are not established.

Identity reads are bounded. Serial-number collection is opt-in because it is
installation-identifying data.

## RUB

The RUB v2 non-segmented frame layout and checksums are statically derived.
RUB is not part of the active BLE/CAN-IP transport. Segmented RUB receive is
rejected because no independent live vectors currently resolve inconsistent
receiver behavior. Treat any further RUB interpretation as a hypothesis until
it has independent evidence and synthetic tests.
