# convert test fixtures

Exports CityGML to CityJSON 1.1. See `citysmith convert --help` for the full
flag reference.

| File | Command | What it shows |
| --- | --- | --- |
| `CS1_multiLOD.gml` | (source) | The full multi-LOD CityGML (LOD0 to LOD3 embedded together), needed to get more than one LOD into the CityJSON output. |
| `CS1_precision3.city.json` | `citysmith convert CS1_multiLOD.gml --precision 3` (default, final) | `transform.scale` is `[0.001, 0.001, 0.001]`. |
| `CS1_precision6.city.json` | `citysmith convert CS1_multiLOD.gml --precision 6` | `transform.scale` is `[1e-06, 1e-06, 1e-06]`, confirming `--precision` really controls the quantisation grid. |

Both outputs verified against cjio (reads, reports, upgrades cleanly to
CityJSON 2.0).

Neither file here is read directly by `test_smoke.py` (`convert()` is
exercised against `tests/lod/CS1_lod3.gml` inside the test suite itself);
these are documentation/inspection artifacts.

See `../README.md` for the full `tests/` folder map.
