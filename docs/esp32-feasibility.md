# ESP32 feasibility and future standalone path

## Status and decision

An ESP32 can comfortably hold the OpenRBus register catalog. A linked-firmware
spike validated both the full catalog and an EHC-16-specific export with German
or English labels.

This result does **not** change the current implementation direction. The active
path is a Home Assistant custom integration that runs the Python OpenRBus core
on the Home Assistant host. ESPHome devices are passive `bluetooth_proxy`
range extenders in that architecture and contain no OpenRBus protocol logic.

The standalone ESP32 design described below is future planning only. It is not
currently being implemented.

## Measured footprint

The spike used OpenRBus `0.3.0` at commit
`0beef7144a753a02d4318c9c3a95b40d36a657c0` and compiled each generated C/C++
header into the same minimal Arduino firmware. Test anchors referenced all
tables so linker garbage collection could not remove them. Net values subtract
the identical baseline firmware.

Test environment:

- ESP32 Dev Module (`esp32dev`), 4 MB flash
- 1,310,720-byte default application partition
- 327,680-byte RAM budget
- PlatformIO Core 6.1.18
- Espressif32 platform 6.12.0
- Arduino-ESP32 3.20017.241212
- Xtensa ESP32 GCC 8.4.0+2021r2-patch5

| Variant | Registers | Raw header | Linked flash | Net catalog flash | Static RAM | Net RAM |
|---|---:|---:|---:|---:|---:|---:|
| Baseline | 0 | 0 B | 233,429 B | 0 B | 21,032 B | 0 B |
| Full, technical | 3,066 | 539,782 B | 441,553 B | 208,124 B | 21,048 B | +16 B |
| Full, German | 3,066 | 604,871 B | 502,289 B | 268,860 B | 21,048 B | +16 B |
| Full, English | 3,066 | 599,053 B | 500,977 B | 267,548 B | 21,048 B | +16 B |
| EHC-16, technical | 592 | 121,207 B | 275,997 B | 42,568 B | 21,048 B | +16 B |
| EHC-16, German | 592 | 136,382 B | 291,373 B | 57,944 B | 21,048 B | +16 B |
| EHC-16, English | 592 | 134,566 B | 290,861 B | 57,432 B | 21,048 B | +16 B |

The EHC-16 export contains 592 canonical definitions covering 624 concrete
device addresses, 73 enumeration definitions with 439 values, and no structure
definitions.

German labels add 60,736 bytes to the full technical catalog and 15,376 bytes
to the EHC-16 export. English labels add 59,424 and 14,864 bytes respectively.

## Flash placement and lookup behavior

Arduino-ESP32 defines `PROGMEM` as empty because flash is directly addressable,
but the linker still placed the complete generated tables in `.flash.rodata`.
The registry data did not increase `.dram0.data`. The measured 16-byte RAM delta
comes from the spike's table anchors and counter, not from the catalog itself.

A binary-search lookup over all 3,066 sorted register rows was also linked and
tested. It adds 140 bytes of flash code, no static RAM, and has a 32-byte stack
requirement. First, middle, last, and absent keys all returned the expected
result. A lookup needs about 12 comparisons; copying a complete current-format
row into RAM would optionally cost 56 bytes.

## Why each register row occupies 56 bytes

The current generated header favors a direct, readable representation rather
than minimum footprint. On the 32-bit ESP32 ABI, each register row contains:

- compact address, type, length, precision, flags, and item-count fields;
- three 8-byte `double` values for gain, minimum, and maximum;
- five 4-byte pointers for code, localized name, unit, enum name, and structure
  name;
- alignment padding required by the ABI.

That layout produces 56-byte register rows. Enumeration rows and structure-field
rows each occupy 12 bytes because they also contain pointers. The fixed tables
therefore account for about 189 KB before their strings are added, explaining
why the full technical catalog measures about 208 KB rather than the earlier
60-90 KB estimate for a compact packed representation.

The full German catalog uses 268,860 net bytes, only about 6.4% of the device's
4 MB flash. Together with the minimal test firmware it uses 38.3% of the
conservative 1.25 MiB application partition. The EHC-16 export with one language
is very comfortable; the full catalog with one language is also comfortable.

## Future path: standalone ESP32 module

The deferred standalone path would be an independent device, not a Bluetooth
proxy. It would implement the complete protocol locally and expose selected
registers over MQTT. That keeps it useful outside Home Assistant, including
OpenHAB, Node-RED, and arbitrary MQTT-based automation.

The implementation would need C++ equivalents of the current Python layers:

1. BLE connection management and segmented framing.
2. CAN-IP and RUB framing, checksums, request correlation, and error handling.
3. Object read/write codecs, value conversion, and batched `read_many`-style
   access.
4. Gateway and node discovery.
5. Pairing and node authorization without embedding credentials or
   manufacturer-owned secret material.
6. Conservative access-policy and write-safety gates equivalent to the Python
   core.
7. MQTT discovery/state/command mapping, reconnect behavior, availability,
   retained-state policy, and explicit write acknowledgements.
8. OTA updates, watchdog behavior, persistent configuration, diagnostics, and
   recovery from interrupted BLE or MQTT sessions.

For that path, a compact generated data format should replace the current
pointer-rich header:

- fixed-point integers plus explicit scale metadata instead of `double`;
- string-pool offsets instead of pointers;
- packed fixed-width rows with documented endianness and versioning;
- binary-search keys or a compact generated index for direct flash lookup;
- enum and structure tables pruned to the selected device families.

Build-time code generation should select device families, languages, and
technical-only versus localized output. This preserves flash and OTA headroom
without maintaining hand-edited device tables.

## Conclusion

The footprint question is resolved: an autonomous OpenRBus ESP32 is
**comfortably feasible** from a catalog-storage and lookup perspective. The
remaining work is the protocol, lifecycle, safety, and MQTT productization—not
flash or static-RAM capacity. This standalone path remains a documented future
option; current development targets the Home Assistant host integration.
