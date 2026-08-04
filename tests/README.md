# tests/

`test_smoke.py` is the pytest suite (`python -m pytest -q` from the repo
root). It only reads a handful of the files in this folder directly:
`lod/CS1_lod3.gml` (as its `DATA` fixture, used throughout),
`semantics/CS1_lod3_enhanced.gml` (one test) and
`validate/CS1_multiLOD.citydoctor.xml` (one test). Everything else here
exists for hands-on inspection and documentation, real command output you
can open and read, not something the test suite asserts on.

## Per-capability folders

Each of `lod`, `semantics`, `convert` and `validate` demonstrates one
exporting capability, built from the same real building (`CS1`, a
CityGRID-style export with a `Building` and a `BuildingPart`): a source
file, one output file per flag/variant that isolates what that flag alone
does, and a final file with everything applied together. Each folder has its
own `README.md` with the exact command and effect of every file in it.

- [`lod/`](lod/README.md): derive LOD1/LOD0/LOD2 from LOD3 or LOD2 source.
- [`semantics/`](semantics/README.md): fill in ids, balcony/chimney
  classification, `lod3Geometry` aggregation.
- [`convert/`](convert/README.md): export to CityJSON 1.1.
- [`validate/`](validate/README.md): run the CityDoctor2 external validator.

`crop` (extract a named subset of buildings by `gml:id`) doesn't have a
folder here yet.

## What are the `CS2_*` files?

`CS2` is a second real-world reference building (Hamburg LGV open data,
`DEHHALKAJ0000ytX`, DL-DE-BY-2.0), run through the exact same full pipeline
as `CS1` (`lod3` to `lod3_enhanced` to `multiLOD` to `.city.json` to
`.citydoctor.xml`), but kept as one flat set here rather than split into the
per-capability folders above. Its purpose is different: the folders above
exist to isolate and demonstrate individual flags one at a time on a single
building; `CS2` exists to prove the whole pipeline works end to end on a
second, independently-sourced building, not to re-demonstrate every flag a
second time.
