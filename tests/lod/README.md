# lod test fixtures

Derives and embeds lower LODs (LOD1 block and LOD0 footprint from LOD3 or
LOD2 data, a clean LOD2 shell from LOD3). See `citysmith lod --help` for the
full flag reference.

All files here come from the same real building, `CS1_lod3.gml` (a
CityGRID-style export with a `Building` and a `BuildingPart`).

| File | Command | What it shows |
| --- | --- | --- |
| `CS1_lod3.gml` | (source) | Unmodified LOD3 building: windows, doors, roof detail, two features. |
| `CS1_lod2.gml` | `citysmith lod CS1_lod3.gml --levels 2` | Default mode: LOD2 embedded alongside the untouched LOD3 source. |
| `CS1_lod2_lower_only.gml` | `citysmith lod CS1_lod3.gml --levels 2 --lower-only` | `--lower-only`: LOD3 source stripped out, only the derived LOD2 shell remains. |
| `CS1_lod1_eave.gml` | `citysmith lod CS1_lod3.gml --levels 1 --lower-only --lod1-height eave` | Block top = Min. Eaves Height, the lowest/most conservative option. |
| `CS1_lod1_average.gml` | `citysmith lod CS1_lod3.gml --levels 1 --lower-only --lod1-height average` | Block top = Average Roof Height (default), the midpoint between eave and ridge. |
| `CS1_lod1_ridge.gml` | `citysmith lod CS1_lod3.gml --levels 1 --lower-only --lod1-height ridge` | Block top = Max. Ridge Height, the tallest option. |
| `CS1_lod0.gml` | `citysmith lod CS1_lod3.gml --levels 0 --lower-only` | Footprint only, flattened to the base height. |
| `CS1_multiLOD.gml` | `citysmith lod CS1_lod3.gml --levels 0,1,2` (final) | Everything at once: LOD3 source plus LOD2/LOD1/LOD0, all embedded together. |

The three `CS1_lod1_*` files are all derived from the exact same source and
differ only in `--lod1-height`; on this building the block heights are
147.399 m (eave), 148.709 m (average, the exact midpoint) and 150.020 m
(ridge), confirming the flag actually changes the geometry rather than being
a no-op.

`test_smoke.py` reads `CS1_lod3.gml` directly (as its `DATA` fixture); every
other file here is a documentation/inspection artifact, not asserted on by
the test suite.

See `../README.md` for the full `tests/` folder map.
