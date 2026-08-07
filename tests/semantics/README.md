# semantics test fixtures

Applies the easy-tier semantic fixes to LOD3 CityGML: assigns missing
`gml:id`s (readable, anchored to the owning Building/BuildingPart's own id,
e.g. `CS1_wall_0001`, `CS1_window_0007`, per SIG3D Part 2's mandatory-id
rule), classifies each `BuildingInstallation` as balcony or chimney, and
builds the `lod3Geometry` aggregate. All three run by default, every plain
run does all three, there is no flag that turns any of them "on"; the
`--no-*` flags only ever skip one. Existing ids (including old opaque
UUID-style ones from an earlier citysmith version) are left alone unless
`--overwrite-ids` is given. See `citysmith semantics --help` for the full
reference.

| File | Command | What it shows |
| --- | --- | --- |
| `CS1_lod3.gml` | (source) | Unmodified LOD3 building: 2 installations, no ids anywhere (including 25 `Window`s), no `function`, 1 already has `lod3Geometry` from the source export. |
| `CS1_semantics_ids_only.gml` | `citysmith semantics CS1_lod3.gml --no-classify --no-aggregate` | Only ids added (68 elements: 17 walls, 21 roofs, 2 ground surfaces, 25 windows, 2 installations, 1 BuildingPart). Installations stay unclassified (`0 balcony, 0 chimney, 2 unknown`), `lod3Geometry` count unchanged (1). |
| `CS1_semantics_classify_only.gml` | `citysmith semantics CS1_lod3.gml --no-ids --no-aggregate` | Only classification runs: both installations get a `function` code (1 balcony, 1 chimney). Only the 2 installations get ids (installation ids are unconditional, not gated by `--no-ids`), nothing else does. `lod3Geometry` count unchanged (1). |
| `CS1_semantics_aggregate_only.gml` | `citysmith semantics CS1_lod3.gml --no-ids --no-classify` | Only the aggregate step runs: the installation missing `lod3Geometry` gets one built (1 to 2). No `function` codes added. |
| `CS1_lod3_enhanced.gml` | `citysmith semantics CS1_lod3.gml` (all defaults, final) | All three fixes applied together: every installation has an id, a `function`, and a `lod3Geometry`. This is what `lod`/`convert` expect as input elsewhere in the pipeline. |

Every number above was checked against the real output XML, not just the
command's printed summary. The `Building`'s own id (`CS1`) anchors every
readable id under it; the `BuildingPart` has no id of its own in the source,
so it keeps the old deterministic-UUID fallback and everything under it
anchors to that instead (e.g. `UUID_4a9691a0-..._ground_0001`).

`test_smoke.py` reads `CS1_lod3_enhanced.gml` directly (one test); the rest
are documentation/inspection artifacts.

See `../README.md` for the full `tests/` folder map.
