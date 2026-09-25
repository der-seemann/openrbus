# CP733 entity catalog

## Root cause

`346a:04` is a real SCB-10 array element of CP730 (`346a:00`), not a
second alias for the base row and not a dynamically invented subindex.  The
normalized registry has one canonical `346a:00` definition with
`wire.array=true`, `max_items=10`, and device evidence rows `346a:01` through
`346a:05` for `Scb-10`.  The `:04` evidence row is therefore the primary
family/product/variant proof needed for projection.

The previous family projection iterated only canonical registry addresses.
For an array this meant that its evidence-backed concrete rows were omitted;
runtime capability references could still add a row, which made the result
depend on the particular node's discovery response.  Consequently CP733 was
missing from a family-only HA catalog even though the registry could decode
and write it.

## Metadata and safety

For `Scb-10`, `346a:04` resolves to:

- datatype/storage: `ENUMERATION` / `UINT8`;
- enum: `ZoneHeatUpSpeed`, values 0..5;
- declared readable and writable;
- read and write evidence: access level 3 (`professional`);
- write safety: `unverified`, so the existing explicit unsafe-write gate still
  applies.

The projection now adds only concrete subindexes present in the selected
family's `evidence.devices` rows.  It keeps the canonical `:00` row where the
existing catalog policy includes it, never enumerates an array by guessing,
and deduplicates addresses before creating entries.  Family matching remains
case-insensitive and uses the resolved product family, never a node number.

## Fix and entity identity

`catalog_for_node()` now projects `(definition.address, *family evidence
addresses)` for each family definition.  `RegisterCatalogEntry.address`
remains the concrete object address, so `346a:04` has a stable object address
and HA unique-id input distinct from `346a:00`; no duplicate alias is
generated.  HA sees the registry wire type as an enum and the declared write
flag, allowing the existing Select/write path to be used with current-value
polling.

The HA projection treats an array's canonical `:00` specially: it remains a
readable catalog/sensor row (the CANopen subindex count), but is not offered as
a writable Number/Select/Switch because Core rejects writes to the count.
Only concrete evidence-backed elements such as `346a:04` become typed
writable controls.

The same rule applies to every evidence-backed array and every supported
family.  An EHC-16 projection, for example, receives its evidenced CP730
`:01` row but not SCB-10-only `:04`, demonstrating that rows do not leak
between families.

## Verification

Focused catalog tests pass (`9 passed`).  They cover arbitrary SCB-10 node
numbers, CP733 type/range/access/safety provenance, stable concrete addresses,
duplicate prevention, and unrelated-family isolation.  Level-3 projection
counts for the current registry are:

| family | catalog rows |
| --- | ---: |
| SCB-10 | 617 |
| EHC-16 | 388 |
| GTW-Bluetooth | 22 |
| GTW-08 | 22 |
| MK3 | 50 |

These are catalog rows before HA diagnostic entities.  This change is in the
Core source tree only; no HA deployment, restart, backend switch, or live
write was performed.

## Verified HA projection counts

For a family-only synthetic identity, the complete catalog and level-3
polling/typed-control projections are:

| family | complete rows | level-3 pollable | sensors | numbers | selects | switches |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| SCB-10 | 855 | 617 | 186 / 55 | 481 / 400 | 125 / 109 | 63 / 53 |
| EHC-16 | 784 | 388 | 220 / 58 | 415 / 229 | 89 / 66 | 60 / 35 |
| GTW-Bluetooth | 26 | 22 | 6 / 5 | 15 / 13 | 4 / 3 | 1 / 1 |
| GTW-08 | 26 | 22 | 5 / 4 | 19 / 16 | 2 / 2 | 0 / 0 |
| MK3 | 68 | 50 | 20 / 5 | 40 / 37 | 6 / 6 | 2 / 2 |

Each `sensors`, `numbers`, and `selects` cell is `complete / level-3
pollable`. Rows above the proven effective read level remain registered but
unavailable and are excluded from polling batches. The same filtering is used
by all HA platforms, so adding concrete array rows does not create a polling
flood.

The SCB-10 CP733 assertion is exact: one `346a:04` catalog row, one HA Select,
six labeled enum options (`0..5`), professional read/write evidence, and a
unique ID containing `object:346a:04` for every node number. `346a:00` is not
counted as a writable Select.
