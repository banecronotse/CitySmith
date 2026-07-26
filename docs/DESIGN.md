# Design

## Goal

Given a CityGML 2.0 file whose buildings are modelled in LOD3 or LOD2, derive
standards-compliant lower levels of detail (LOD2 needs LOD3 as its source;
LOD1 and LOD0 work from either) for every building and building part, without
altering the existing source geometry. LOD2 is the realistic default input:
real-world CityGML is overwhelmingly LOD2, not LOD3, see [Which LOD is my
data?](../README.md#which-lod-is-my-data) in the README.

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

CitySmith is also downgrade-only (never upward, no LOD4) and, within
semantics, easy-tier only (see [Roadmap](#roadmap) for the hard tier). See the
[README's Scope section](../README.md#scope) for the user-facing version of
this list, including the CRS and merged-building caveats.

## Input assumptions and how they are detected

The engine does not hard-code any single vendor's export or authoring tool.
It keys off the CityGML data model itself and degrades gracefully; `_find_source_shell`
in `core.py` is the single place this detection happens, and `citysmith inspect`
runs it read-only so a file's shape is knowable before committing to a real run.

1. The unit of work is any feature that owns usable LOD3 or LOD2 geometry (a
   `bldg:Building` or a `bldg:BuildingPart`), LOD3 preferred. Three
   structurally different, all valid, CityGML encodings are recognised, by
   structure, never by guessing the authoring tool from a filename:
   - **`solid`**: an aggregating `lodXSolid` (a `gml:Solid`) whose exterior
     `gml:CompositeSurface` references the shell polygons, either by
     `xlink:href` to polygons defined inside the boundary surfaces (the
     common CityGRID / 3DCityDB style) or inline in the composite surface.
   - **`surfaces`**: no aggregating solid; each boundary surface under
     `boundedBy` carries its own `lodXMultiSurface` directly, inline or by
     local `xlink:href` (seen from SketchUp-modelled, FME-workbench-exported
     data, which doesn't always construct a closed solid; verified against
     real files from that pipeline). Structurally just as usable as `solid`
     for every capability except that watertightness can't be assumed, since
     nothing claimed the shell was closed in the first place.
   - **`unclassified`**: a `lodXMultiSurface` directly on the feature, not
     inside any `boundedBy` thematic surface, a flat bag of polygons with no
     wall/roof/ground distinction. Detected, reported (`source_unclassified`
     in `Report`/`InspectReport`), but never processable: LOD1/LOD0
     extrusion needs to know which polygon is the ground, information this
     pattern simply doesn't carry. Not a defect in the tool or the data, just
     a genuine information gap.
2. Each shell polygon (in the `solid` and `surfaces` patterns) is classified
   by its nearest boundary-surface ancestor (`RoofSurface`, `WallSurface`,
   `GroundSurface`, and the other `*Surface` thematic types).
3. Wall openings are expected as `gml:interior` rings on the wall polygon
   (optionally with `bldg:opening` glass/leaf geometry). Openings modelled as
   recesses are out of scope for v1 and are reported, not silently mangled.

Anything that does not fit is counted and reported rather than crashing, so the
tool is safe to point at unfamiliar data.

## Transformation

For each feature with a usable `solid` or `surfaces` shell (`unclassified`
features are skipped, see above):

1. Collect the shell polygons, from the composite surface's ids (`solid`) or
   directly from each boundary surface's own geometry (`surfaces`).
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

#### Panelized surfaces: outer-boundary merge, not just de-holing

Removing `gml:interior` rings only fixes one way window/door gaps get
modelled. Some real exports (confirmed on SketchUp-modelled, FME-workbench-
exported data) instead tile a facade into many small panel polygons and
simply omit the panel where a window is, there is no hole to strip, the
geometry for that patch was never captured. De-holing can't fix a gap that
was never a hole.

For any thematic surface with more than one polygon, `_build_lod2` first
calls `geometry.cluster_coplanar_rings` to group the polygons by which plane
they actually lie on (same normal direction, same offset, within tolerance).
This matters because a single `boundedBy` can legitimately bundle either
several small panels of the *same* physical face (the missing-panel pattern
above) or several genuinely different faces altogether, this project's own
`box_lod3.gml` test fixture bundles all four walls of a box under one
`WallSurface`, and merging those would fit a meaningless plane through
perpendicular faces. Clustering by plane, not just counting polygons, is
what tells the two cases apart.

Each resulting cluster with more than one polygon is then collapsed by
`geometry.union_coplanar_polygons`: project every ring (exterior *and*
interior) into the cluster's fitted plane, build the undirected multiset of
all their edges, and drop every edge shared by two adjacent panels. An edge
walked by two panels appears twice and cancels; an edge on the true outline
appears once and survives. The surviving edges are traced into closed loops,
and loops nested inside an odd number of others (window/door gaps left as
missing panels, or genuine cut interior rings) are dropped, filling them.
This is a real boundary union, not a convex hull, so a concave, L-shaped or
stepped wall keeps its actual outline rather than being rounded out to a
bulging convex envelope with diagonal chords across the concavity (the
earlier convex-hull merge did exactly that, and on real SketchUp/FME data it
produced visibly oversized, diagonal-cut walls, it was wrong, not an
acceptable approximation). A lone polygon in its own cluster is untouched,
going through the normal de-hole copy path. `Report` gains
`merged_surfaces`/`merged_panels` to make this visible.

Edge cancellation is only safe when panels meet edge-to-edge. If any vertex
lands strictly in the interior of another panel's edge (a T-junction), the
shared edges won't cancel and the traced outline would be broken, so
`union_coplanar_polygons` detects that and returns `None`, and the cluster
falls back to the untouched per-polygon path. The same happens if the
cluster's points don't actually fit one plane (a mis-grouped cluster of
genuinely different faces): fitting one plane through them would be silently
wrong, so it bails rather than guess.

Three ways a window/door opening can be modelled, and how each is handled
when simplifying to LOD2:

1. *Interior ring* (`gml:interior`), the standard: the ring is dropped when
   copying the wall (de-holing), or, on the merge path, comes out as its own
   loop and is discarded. Removed.
2. *Missing panel*: the facade is tiled into panels and the panel where the
   window is was simply never emitted, leaving a gap. Edge cancellation
   traces the gap as an interior loop and discards it. Removed.
3. *Exterior-boundary notch*: the opening is carved directly into the wall
   polygon's own exterior ring, which juts inward to trace around the
   opening and back out, sometimes through a very thin neck. **Not removed.**
   A reentrant indentation in a boundary is geometrically indistinguishable
   from an intentional concavity (an L-shaped wall, a recessed entrance):
   there is no local test that says "this notch is a window, that one is the
   building's real shape." Removing them would require a size heuristic (fill
   any inward bay below some width/depth) that would also flatten genuinely
   concave walls, including in clean CityGRID data, so it is deliberately not
   done. This encoding shows up only in inconsistent exports (the same
   building can even mix all three ways of modelling openings in a single
   wall); a well-formed LOD3 source uses interior rings, which are handled.
   The outline is left faithful to the source rather than guessed at.

The same cleanup also applies when a feature's *native* source already is
LOD2 but is itself panelized (`_needs_lod2_build` checks real mergeability,
not just polygon count, so it stays in sync with what `_build_lod2` will
actually do): requesting LOD2 output no longer means "leave a messy native
LOD2 exactly as found," it means "produce a clean one either way." A single
scratch `_strip_source_geometry` pass afterwards can't tell freshly built
LOD2 content apart from original LOD2 debris by tag name alone (both are
tagged `lod2MultiSurface` identically), so the exact elements
`_process_feature` inserts are tracked by object identity in a `new_nodes`
set and always protected from stripping, regardless of tag-based rules.

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
[README.md](../README.md#lod1-how-the-block-height-is-chosen)), no
single height, eave, ridge, or average, is a correct answer, because the
guide's height model assumes one coherent roof to begin with. CitySmith does
not currently detect or flag this case; it is a known gap, tracked on the
[Roadmap](#roadmap), consistent with the "report, don't force" approach
already used for watertightness.

### LOD0 (footprint)

The `GroundSurface` ring(s), flattened to `z_base` and re-emitted as a
`bldg:lod0FootPrint` `MultiSurface`. No height reasoning beyond `z_base` is
needed since LOD0 has no vertical extent.

## Semantic enrichment

`semantics.py` / `enhance_semantics()` applies the easy tier of the project's
LoD3 rulebook, with no geometry changes. It needs an LOD3 source: the
rulebook's target is `bldg:BuildingInstallation` (balconies, chimneys,
dormers) as modelled in the LOD3 CityGRID/UVM exports it was written against.

1. **Ids**: every `Building`, `BuildingPart`, `BuildingInstallation` and
   thematic boundary surface (`WallSurface`, `RoofSurface`, `GroundSurface`,
   `OuterFloorSurface`) gets a `gml:id` if it doesn't already have one,
   deterministically seeded (uuid5) from the first polygon id found inside
   it, so re-runs are reproducible.
2. **Classification** (`classify_installation`): each `BuildingInstallation`
   is classified `"balcony"`, `"chimney"`, or left unknown, in this order:
   - an `OuterFloorSurface` among its boundary surfaces is a decisive
     balcony signal (an exposed floor is the defining trait of a balcony),
     regardless of height;
   - otherwise, if the installation's own eave height (below) is known, an
     installation whose vertical midpoint sits below that eave is a balcony,
     above it a chimney or other roof structure;
   - otherwise (eave unknown), a `RoofSurface` + `WallSurface` combination (a
     small roofed box shape) falls back to chimney;
   - anything matching none of these is left unclassified.
3. **Eave height** (`compute_eaves`): the lowest point of every `RoofSurface`
   belonging to the building's own main shell, explicitly excluding roof
   surfaces that themselves belong to a `BuildingInstallation` (so a
   chimney's own tiny roof cap never counts as "the building's eave").
   Computed per feature (`Building`/`BuildingPart`) and per top-level
   `Building`, so an installation on a `BuildingPart` uses that part's own
   eave if known, falling back to the parent `Building`'s eave otherwise
   (`eave_for`).
4. **Function code and type**: `bldg:function` gets the SIG3D/CityGML
   standard code-list value (`1000` balcony, `1030` chimney,
   `FUNCTION_CODES` in `semantics.py`), plus a `type` generic attribute
   (`gen:stringAttribute`) with the same word, for tools that don't resolve
   function codelists.
5. **lod3Geometry aggregation**: if an installation lacks an aggregating
   `bldg:lod3Geometry`, one is added, referencing (via `xlink:href`) the
   polygons already defined in its own boundary surfaces.

The eave-height heuristic is the user's own idea, developed to catch real
balconies that the source CityGRID export doesn't reliably tag with
`OuterFloorSurface` (verified on real data: 352 balconies, 2246 chimneys, 0
unknown, up from 0 balconies found with a structure-only, no-eave approach).
Known limitation, tracked on the [Roadmap](#roadmap): no size/shape filter
yet, so a low canopy or awning below the eave can be misclassified as a
balcony.

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

This matters even more for the `surfaces` pattern (see [Input
assumptions](#input-assumptions-and-how-they-are-detected)): a `solid`-pattern
source at least *claims* to be a closed shell (that's what a `gml:Solid` means),
even if it turns out not to be one in practice. A `surfaces`-pattern source
never made that claim in the first place, it's just an independently listed
set of boundary surfaces, so open edges there are the expected default, not a
surprise. The same `shell_stats` check runs identically either way; the
numbers just mean something different depending on which pattern produced them.

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

- LOD0/LOD1/LOD2 derivation from LOD3, embed or lower-only output. LOD1/LOD0
  also derivable from an LOD2-only source.
- Three CityGML shell encodings recognised (`solid`, `surfaces`,
  `unclassified`), detected structurally, not guessed from filenames or
  authoring-tool metadata; verified against real CityGRID/3DCityDB-style,
  SketchUp/FME-workbench-exported data.
- `citysmith inspect`: read-only preflight reporting what was found and what
  each capability can/can't do with it, before committing to a real run.
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
