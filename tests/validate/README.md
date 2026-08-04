# validate test fixtures

Runs the CityDoctor2 external validator and reports the results (plus
CitySmith's own native watertightness report elsewhere in the pipeline). See
`citysmith validate --help` for the full flag reference.

| File | Command | What it shows |
| --- | --- | --- |
| `CS1_multiLOD.gml` | (source) | The full multi-LOD CityGML being validated. |
| `CS1_multiLOD.citydoctor.xml` | `citysmith validate CS1_multiLOD.gml` (default, final) | The XML report CityDoctor2 always produces: 1 building, 6 defect classes found (`GE_S_MULTIPLE_CONNECTED_COMPONENTS`, `GE_S_NOT_CLOSED` x3, `GE_S_NON_MANIFOLD_VERTEX`, `GE_P_ORIENTATION_RINGS_SAME` x6, `SE_BS_UNFRAGMENTED` x3, `GE_S_NON_MANIFOLD_EDGE`), none of which CitySmith's own native watertightness report checks for. |
| `CS1_report.pdf` | `citysmith validate CS1_multiLOD.gml --pdf CS1_report.pdf` | `--pdf`: the same validation run, plus a human-readable PDF walkthrough of every check and error (CityDoctor2's own `-pdfreport`, Apache FOP-rendered). The XML report is still produced alongside it either way. |

`test_smoke.py` reads `CS1_multiLOD.citydoctor.xml` directly (one test, to
exercise the XML report parser); the rest are documentation/inspection
artifacts.

See `../README.md` for the full `tests/` folder map.
