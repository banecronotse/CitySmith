# Design

## Goal

Given a CityGML 2.0 file whose buildings are modelled in LOD3, add a
standards-compliant, watertight LOD2 representation to every building and
building part, without altering the existing LOD3.

## Scope

CitySmith targets the **CityGML 2.0 Building module only**. This is a
structural fact, not a soft preference: `citygml.py`'s `NS` map registers
exactly `core`, `bldg`, `gml`, `gen` and `xlink`, no `brid` (Bridge), `tun`
(Tunnel), `tran` (Transportation), `veg` (Vegetation), `wtr` (WaterBody),
`luse` (LandUse), `frn` (CityFurniture), `dem` (Relief) or `app`
(Appearance). `_FEATURE_TAGS` in both `core.py` and `cityjson.py` hardcode
`{bldg:Building, bldg:BuildingPart}` as the only units of work. Features from
other modules pass through the XML tree untouched, since the engine never
looks for them, but are also never validated, converted or enriched.

The Appearance module deserves a specific callout since it's easy to miss:
CitySmith has no code path that reads or writes `app:Appearance`,
`app:ParameterizedTexture` or texture coordinates anywhere. Because every
polygon CitySmith generates (LOD0/1/2) gets a freshly derived id (see
`_det_id`), any pre-existing appearance's `app:target` xlink references,
which point at specific LOD3 polygon ids, can never resolve against generated
geometry. LOD3 itself is untouched so its own textures remain intact, but
CitySmith-derived LODs are always untextured, silently. This was verified
against real test data with 178 `app:Appearance` elements and 30k+
`app:textureCoordinates` entries.

CitySmith is also downgrade-only (LOD3 to LOD0/1/2, never upward, no LOD4)
and, within semantics, easy-tier only (see [Roadmap](#roadmap) for the hard
tier). See the [README's Scope section](../README.md#scope) for the
user-facing version of this list, including the CRS and merged-building
caveats.

## Input assumptions and how they are detected

The engine does not hard-code any single vendor's export. It keys off the
CityGML data model and degrades gracefully:

1. The unit of work is any feature that owns a `bldg:lod3Solid`
   (a `bldg:Building` or a `bldg:BuildingPart`).
2. An `lod3Solid` is a `gml:Solid` whose exterior `gml:CompositeSurface`
   references the shell polygons. Two encodings are supported:
   - references by `xlink:href` to polygons defined inside the boundary
     surfaces (the common CityGRID / 3DCityDB style), and
   - polygons defined inline in the composite surface.
3. Each shell polygon is classified by its nearest boundary-surface ancestor
   (`RoofSurface`, `WallSurface`, `GroundSurface`, and the other
   `*Surface` thematic types).
4. Wall openings are expected as `gml:interior` rings on the wall polygon
   (optionally with `bldg:opening` glass/leaf geometry). Openings modelled as
   recesses are out of scope for v1 and are reported, not silently mangled.

Anything that does not fit is counted and reported rather than crashing, so the
tool is safe to point at unfamiliar data.

## Transformation

For each feature with an `lod3Solid`:

1. Collect the shell polygon ids from the composite surface, in order.
2. Partition them by thematic type.

### LOD2 (faithful shell)

3. **Roof and ground**: copy the LOD3 polygon into the LOD2 with a fresh
   reproducible id. LOD2 geometry is kept self-contained (no cross-LOD XLinks)
   so each LOD is independent and third-party readers do not have to resolve
   references into the LOD3 tree. Copies are exact, so the LOD2 roof is
   identical to the LOD3 roof (the "keep the detailed roof" target).
4. **Walls**: deep-copy the polygon, remove every `gml:interior` ring (this
   fills window and door holes), and assign fresh `gml:id`s. The exterior ring
   is never modified, so shared edges with roof, ground and neighbouring walls
   stay vertex-for-vertex identical.
5. Emit one `bldg:boundedBy` per thematic type, each holding a
   `bldg:lod2MultiSurface`. Roof and ground members are `xlink` references; wall
   members carry the new de-holed polygons inline.
6. Emit a `bldg:lod2Solid` whose composite surface references the reused roof
   and ground ids plus the new wall ids.
7. Insert the new nodes immediately before the `lod3Solid`, so the `lod2Solid`
   precedes the `lod3Solid` (schema-correct solid ordering) and the LOD2
   boundary surfaces stay within the `boundedBy` group.

Openings (`bldg:opening` / Window / Door) and `outerBuildingInstallation`
(dormers, chimneys, roof bumps) are simply not referenced by the LOD2, which is
the standard LOD2 content model. They remain present in the LOD3.

### LOD1 (extruded block)

LOD1 is a single prism: the ground surface's footprint, extruded straight up
from a base height to a top height (`extrude_prism` in `geometry.py`, watertight
by construction, see below). Per the SIG3D Modeling Guide for 3D Objects, Part
2, section 2.1, LOD1 is by definition "exactly one prismatic extrusion solid"
per `Building`/`BuildingPart`, which is exactly this shape: horizontal ground
and top, vertical walls.

Heights follow the vocabulary defined in the same guide, section 2.4
("Heights"):

- **Min. Relief Height** (`z_base`): the lowest point of the `GroundSurface`,
  or the lowest point in the whole shell if no ground surface is present.
  Used as the prism's base.
- **Min. Eaves Height** (`z_eave`): the lowest point of any `RoofSurface` in
  the shell.
- **Max. Ridge Height** (`z_ridge`): the highest point anywhere in the shell.
- **Average Roof Height**: `(Min. Eaves Height + Max. Ridge Height) / 2`, the
  formula given verbatim in the guide's height diagram (section 2.4, page 6).

`--lod1-height` selects the prism's top height from these, by name:

- `average` (**default**): Average Roof Height. This is the block height
  CitySmith uses unless told otherwise.
- `eave`: Min. Eaves Height, the most conservative (lowest) option.
- `ridge`: Max. Ridge Height, the tallest option.

All three are literal min/max over the shell's points, no clustering or area
weighting, matching how the guide itself defines them.

**Known limitation**: LOD1 is still fundamentally "one box per `Building`."
If a `Building` in the source data actually represents several real
structures merged into a single feature (see the honest note in
[README.md](../README.md#lod3-to-lod1-how-the-block-height-is-chosen)), no
single height, eave, ridge, or average, is a correct answer, because the
guide's height model assumes one coherent roof to begin with. CitySmith does
not currently detect or flag this case; it is a known gap, tracked on the
[Roadmap](#roadmap), consistent with the "report, don't force" approach
already used for watertightness.

### LOD0 (footprint)

The `GroundSurface` ring(s), flattened to `z_base` and re-emitted as a
`bldg:lod0FootPrint` `MultiSurface`. No height reasoning beyond `z_base` is
needed since LOD0 has no vertical extent.

## Watertightness: measured reality, not assumption

The LOD2 shell inherits the vertices of the LOD3 shell (with window holes
filled). It is therefore watertight only if the LOD3 shell already tiles into a
closed 2-manifold. On a real-world pilot dataset (937 solids) that is rarely the
case:

| tolerance | watertight solids |
| --- | --- |
| 1 mm | 83 / 937 |
| 1 cm | 90 / 937 |
| 10 cm | 91 / 937 |

The near-flat curve shows the gaps are structural (T-junctions between the single
ground polygon, the subdivided roof and the walls; roof overhangs without soffit
surfaces), not near-coincident vertices that a weld tolerance would fix. Filling
the window holes removes roughly 7,900 interior rings and genuinely improves
closedness, but it cannot manufacture a manifold from surface soup.

Decision for v0.1: **report, do not force.** `shell_stats` classifies every
building (watertight, 1-4, 5-20, 20+ open edges). Guaranteed-closed output comes
from LOD1 instead (extruded prism, watertight by construction); a healing pass
that makes LOD2 itself watertight is future work.

## Quality control

`geometry.is_closed_shell` rebuilds the edge set of each LOD2 solid and asserts
that every undirected edge is used exactly twice. Coordinates are rounded to
millimetre precision before comparison. The per-feature result is aggregated in
the `Report`, and any non-closed feature id is listed so it can be inspected.
This is a direct implementation of the closed-solid definition in the [SIG3D
Modeling Guide for 3D Objects, Part 1](https://files.sig3d.org/file/ag-qualitaet/201311_SIG3D_Modeling_Guide_for_3D_Objects_Part_1.pdf)
(section 10, `gml:Solid`): every edge shared by exactly two polygons, and every
polygon connected to every other through that shared-edge graph (condition v
in the guide is the "umbrella axiom" from Gröger & Plümer 2011, every point is
surrounded by a single closed cycle of polygons). Part 1 is also, not
coincidentally, the rule set CityDoctor2's own geometry checks (`GE_S_*`,
`GE_P_*`) are built on, which is why our closedness numbers and CityDoctor2's
`GE_S_NOT_CLOSED` findings corroborate each other (see below).

This native check is intentionally narrow (closedness only) and has zero
dependencies. For a broader, independently developed validation, `validate`
bridges to [CityDoctor2](https://transfer.hft-stuttgart.de/gitlab/citydoctor/citydoctor2)
(`src/citysmith/citydoctor.py`): a Java tool from HFT Stuttgart implementing the
OGC CityGML Quality Interoperability Experiment check taxonomy (ring, polygon
and shell level: self-intersection, non-planarity, ring orientation,
non-manifold edges/vertices, connected components, plus some semantic checks).
CityDoctor2 is fed CityGML directly (no CityJSON conversion; the Java runtime is
purely an implementation detail invoked as a subprocess) and returns a
structured XML report, which is parsed into a `ValidationReport`. It is not
bundled (a large Java application with its own runtime); the bridge locates a
user-provided install via `--citydoctor-home` or `CITYSMITH_CITYDOCTOR_HOME`.

Verified empirically (not just from documentation) against CitySmith's own
output (`out_lod2_only.gml`, 501 buildings): CityDoctor2 parsed it with no
compatibility issues in about 10 seconds, corroborated our own closedness
finding independently (`GE_S_NOT_CLOSED` on most of the same buildings our
`shell_stats` flags), and surfaced defect classes we do not check at all, most
notably `GE_P_NON_PLANAR_POLYGON_DISTANCE_PLANE`, `SE_BS_UNFRAGMENTED` (roof
should be split into planar facets) and `GE_P_ORIENTATION_RINGS_SAME` (it also
caught a real ring-winding bug in our own hand-written test fixture). Its `-out`
option does not auto-repair geometry today (confirmed by diffing input against
output): it re-serializes the file with per-feature Quality-ADE error
annotations. So the bridge is validate-and-report, not auto-heal.

## Interoperability and transferability

- **Format**: CityGML 2.0 in, CityGML 2.0 out. Namespaces, prefixes and the
  file's generic attributes (for example `gen:stringAttribute`) are preserved.
- **Schema**: output element ordering follows the `_AbstractBuilding` content
  model so it validates against the CityGML 2.0 building schema.
- **Downstream**: designed to load in 3DCityDB, FME and QGIS; CityJSON export is
  native (`cityjson.py`), verified through `cjio` (parses, reports, upgrades
  cleanly to CityJSON 2.0); geometry validated through the CityDoctor2 bridge.
- **Portability**: pure Python plus `lxml`; no OS-specific or commercial
  dependencies; runs the same on Windows, macOS and Linux.
- **Determinism**: a run over the same input yields the same structure. New ids
  are the only non-deterministic part and can be made reproducible with a seed
  if required.

## Roadmap

### Done (v0.1)

- LOD0/LOD1/LOD2 derivation from LOD3, embed or lower-only output.
- Semantic enhancer, easy tier: ids, `function` codes, `type` attributes,
  `lod3Geometry` aggregation, eave-height-based balcony/chimney classification.
- Native CityJSON 1.1 writer.
- CityDoctor2 external validation bridge.

### Planned

- **Semantic rulebook expansion**: the [SIG3D Modeling Guide for 3D Objects,
  Part 2](https://files.sig3d.org/file/ag-qualitaet/201311_SIG3D_Modeling_Guide_for_3D_Objects_Part_2.pdf)
  (the broader CityGML coding standard the project's chimney and balcony
  rulebook itself cites) has now informed the LOD1 height methodology (section
  2.4, see [LOD1 (extruded block)](#lod1-extruded-block)); its later
  "Extended Modeling" sections on `BuildingInstallation` and boundary-surface
  codelists are still unreviewed and are likely candidates for more structured
  semantic-enrichment rules, the same way the current easy tier came from the
  chimney/balcony spec.
- **Semantic enhancer, hard tier**: restructure `BuildingPart`-modelled
  balconies into `outerBuildingInstallation`, collapse thick (>0.5 m per
  SIG3D Part 2 sec. 2.6, "Overhanging Building Elements") elements to solids
  vs. surfaces, reclassify faces by orientation (bottom to
  `OuterFloorSurface`).
- **Eave-heuristic refinement**: a size/shape filter to reduce false positives
  where non-balcony elements below the eave (canopies, awnings) get
  misclassified as balconies.
- **Merged-building diagnostic for LOD1**: see the "Known limitation" note in
  [LOD1 (extruded block)](#lod1-extruded-block). Detect and flag `Building`
  features whose roof heights split into multiple large, comparably-sized
  groups (a signal the feature actually merges several real structures, a
  known export artifact e.g. from shared ALKIS footprints or addresses), so
  users can review those buildings instead of silently trusting one LOD1 box
  for them. Deferred for now; documented as a known limitation in the
  meantime, the same "report, don't force" precedent as watertightness.
- **Geometric healing**: an optional pass to make LOD2 itself watertight
  (T-junction resolution, soffit closing), rather than relying on LOD1 as the
  only guaranteed-closed output.
- CityGML 3.0 support; recessed (non-hole) openings; optional LOD2.0 footprint
  extrusion as an alternative to the "keep the LOD3 roof" target; a
  `--keep-installations` switch; LOD0 RoofEdge; installations exported as their
  own CityJSON CityObjects.
