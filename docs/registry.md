# Public register registry

`data/registry/registry-v1.json` is the canonical public dataset. The packaged
copy under `src/openrbus/data/` is compact but data-equivalent. The current
revision contains 3,066 canonical register definitions and 47 legacy
candidates that are explicitly not promoted to canonical definitions.

## Contents

Each canonical entry can include:

- address and optional short code;
- short German and English labels;
- semantic type, storage type, byte length, gain, unit, and array shape;
- declared read/write access;
- range, precision, enumeration, or packed-structure information;
- sanitized device-family evidence and explicit type/range conflicts;
- exact per-family read/write access levels where device evidence exists;
- a conservative write-safety classification.

Evidence categories are narrow:

- `catalog_definition` means derived from a normalized technical dictionary.
- `device_configuration` means corroborated by one or more profile layouts.
- `device_definition` means exposed by one or more family definitions.

All three are static derivations. They do not prove that a register is present,
readable, writable, or safe on a live installation.

Access-level evidence is complete inside the 1,451 current device rows, but
coverage of the global dictionary is partial: 692 of 2,832 declared-writable
definitions have device evidence. Known levels are exposed as `AccessLevel`
values; roles are named only for user (`1`), installer (`2`), and professional
(`3`). Other numeric levels are retained without inferred semantics.

## Conflict handling

The registry does not silently collapse incompatible type or range evidence.
Known wire-type conflicts are retained and block high-level writes. Range
variants require an unambiguous device family. Device-family evidence can
further block an object that is globally declared writable but is not writable
for that family.

Cross-family access-level differences are retained in the same way. A concrete
address with several possible levels is ambiguous until a device family is
supplied; the client never chooses the least restrictive row automatically.

Canonical definitions and legacy candidates are separate. Client lookup never
uses a candidate as if it were confirmed.

## Provenance and licensing

The dataset contains normalized technical facts and short labels only. It does
not include its private working inputs, proprietary documents or formats,
source locations, hashes, captures, credentials, device identifiers, serial
numbers, or sampled values.

The registry is licensed under CC BY 4.0 with attribution to OpenRBus
contributors. Code that parses or exports it is Apache-2.0 licensed. The
machine-readable metadata records the schema, revision, dictionary revision,
counts, content policy, evidence status, and license attribution.

`tools/export_registry.py` reads only public JSON and produces deterministic
compact JSON or a C/C++ header. It deliberately has no path to private source
databases or vendor formats.
