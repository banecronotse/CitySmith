# Examples

One real building (19 shell polygons, one chimney and one balcony
`BuildingInstallation`), extracted unmodified from the *3D-Gebäudemodell
LoD3.0-HH Hamburg* dataset, run through all four capabilities:

| File | Produced by | What it shows |
| --- | --- | --- |
| `hamburg_building_lod3.gml` | (source) | The unmodified LOD3 building |
| `hamburg_building_lod3_enhanced.gml` | `citysmith semantics` | Ids, `function`/`type` attributes and `lod3Geometry` added to the chimney and balcony |
| `hamburg_building_lod2.gml` | `citysmith lod --levels 2 --lower-only` | Derived LOD2 shell only, window/door holes filled |
| `hamburg_building_lod1.gml` | `citysmith lod --levels 1 --lower-only` | Derived LOD1 block only, watertight by construction |
| `hamburg_building_lod0.gml` | `citysmith lod --levels 0 --lower-only` | Derived LOD0 footprint only |
| `hamburg_building_multiLOD.gml` | `citysmith lod --levels 0,1,2` | LOD0/1/2 all embedded alongside the original LOD3, one file |
| `hamburg_building.city.json` | `citysmith convert` | CityJSON 1.1 export of the multi-LOD file (all 4 LODs, verified with cjio) |
| `hamburg_building_report.json` | `citysmith lod --report` | The native JSON quality report: watertightness, per-surface counts, source breakdown |

Reproduce all of them from the source file:

```bash
citysmith semantics examples/hamburg_building_lod3.gml -o hamburg_building_lod3_enhanced.gml
citysmith lod examples/hamburg_building_lod3.gml --levels 2 --lower-only -o hamburg_building_lod2.gml
citysmith lod examples/hamburg_building_lod3.gml --levels 1 --lower-only -o hamburg_building_lod1.gml
citysmith lod examples/hamburg_building_lod3.gml --levels 0 --lower-only -o hamburg_building_lod0.gml
citysmith lod examples/hamburg_building_lod3.gml --levels 0,1,2 -o hamburg_building_multiLOD.gml --report hamburg_building_report.json
citysmith convert hamburg_building_multiLOD.gml -o hamburg_building.city.json
```

## Data source and attribution

- **Dataset**: 3D-Gebäudemodell LoD3.0-HH Hamburg
- **Publisher**: Freie und Hansestadt Hamburg, Landesbetrieb Geoinformation
  und Vermessung (LGV)
- **Landing page**: https://metaver.de/trefferanzeige?docuuid=B438AD57-223B-43A4-8E74-767CEC8A96D7
- **License**: [Datenlizenz Deutschland – Namensnennung – Version 2.0](https://www.govdata.de/dl-de/by-2-0)
  (attribution required; commercial and non-commercial reuse, modification
  and redistribution permitted)

The building's geometry in `hamburg_building_lod3.gml` is unmodified from the
source; every other file here is derived from it by CitySmith itself. The
appearance/texture data that ships with the full dataset is not included,
CitySmith doesn't read or write it (see [Scope](../README.md#scope)), so it
would only add size without demonstrating anything the tool does.

Large or private city models must not be committed to the repository. Keep
them outside the repo and pass their path on the command line, same as above.
