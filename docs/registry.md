# Public register registry

`data/registry/registry-v1.json` is the language-neutral canonical public
dataset. Short public labels live in `data/registry/i18n/de.json` and
`data/registry/i18n/en.json`. The packaged copies under `src/openrbus/data/`
are compact but data-equivalent. The current
revision contains 3,066 canonical register definitions and 47 legacy
candidates that are explicitly not promoted to canonical definitions.

## Contents

Each canonical entry can include:

- address and optional short code;
- a short English fallback only where no stable short code exists;
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

## Enumerations and bit fields

The public DE/EN sidecars cover all 3,066 register IDs, every packed field, and
the date/time and wire-type display categories. The current catalog contains
629 enumeration registers backed by 204 enum
definitions and 1,159 numeric values. Short labels are published for 1,158
values: 1,127 have German labels and all 1,158 have English labels. The sole
unlabelled value is an unused empty unit sentinel; every enum referenced by a
register has complete value labels. For example, CP733 resolves through
`ZoneHeatUpSpeed` and exposes the six exact German choices from `Extra langsam`
through `Schnellste`, plus their English counterparts.

The main catalog retains packed-field identifiers but not display labels.
Public sidecars map those identifiers to their short labels. Thirteen pure
bitfield definitions are referenced by 33 registers and describe 117
individual bits; seven additional mixed structures used by ten registers
contain 15 one-bit fields. The provenance has technical names for every field,
but no localized structure-field translations. Longer field explanations are
therefore not copied as substitute labels.

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

Earlier revisions deliberately exported enum values as numbers only under the
same conservative prose-exclusion rule. That omission was a content-policy
choice, not a source or parser limitation. Enum `Description` values are short
technical labels; only longer `Explanation` text remains excluded.

The registry is licensed under CC BY 4.0 with attribution to OpenRBus
contributors. Code that parses or exports it is Apache-2.0 licensed. The
machine-readable metadata records the schema, revision, dictionary revision,
counts, content policy, evidence status, and license attribution.

`tools/sync_registry.py` validates the language-neutral boundary and
synchronizes only canonical public JSON into the packaged copies. It rejects
long enum labels and locale documents containing keys associated with original
or explanatory text. It has no input for private/vendor locale files and
supports `--check` for drift.

Full original locale material is deliberately outside the repository under
the ignored `local/registry/i18n-original/` tree. Local tooling generates the
same stable register, enum, and structure-field IDs for all 28 available source
locales, while preserving short, medium, long, and explanatory source fields.
Those files are never an input to `sync_registry.py`. The publication and
artifact scanners additionally reject `*_original.json` anywhere in a public
tree or release archive.

Only the reviewed DE/EN short-label files are currently public. Other source
locales stay private until the same short-label policy can be checked without
silently publishing longer descriptive text.

`tools/export_registry.py` remains the public-only downstream exporter. It
reads no source database or vendor input and produces deterministic compact
JSON or a C/C++ header. Both formats now retain enum/structure references,
enum value labels for the selected locale, and packed-field names.
