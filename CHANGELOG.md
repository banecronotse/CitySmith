# Changelog

All notable changes to this project are documented here. The format follows
[Keep a Changelog](https://keepachangelog.com/) and the project uses
[Semantic Versioning](https://semver.org/).

## [Unreleased]
### Added
- Project scaffold, packaging and design document.
- CityGML 2.0 namespace helpers.
- LOD3 to LOD2 engine (`citysmith.add_lod2`) with embed and `--lower-only`
  modes, window/door hole filling, reproducible ids and self-contained LOD2
  geometry.
- Native watertightness quality report (per-building open-edge buckets).
- Command line (`citysmith`) with JSON report output.
- Test suite with a box fixture covering both modes, id uniqueness,
  reproducibility and hole filling.

- LOD1 derivation: watertight extruded block solid from the footprint, average
  (default), eave or ridge height. LOD0 derivation: footprint MultiSurface.
- Multi-LOD `enhance()` API and `citysmith lod --levels 0,1,2` command.
- Semantic enhancer (`enhance_semantics`, `citysmith semantics`): ids, function
  codes, `type` attributes, `lod3Geometry` aggregation, structure-based
  chimney/balcony classification.
- Native CityJSON 1.1 writer (`convert`, `citysmith convert`) with shared
  quantised vertices, surface semantics and parent/child links. Verified against
  cjio (reads, reports, upgrades to 2.0).
- CLI restructured into `lod` / `semantics` / `convert` / `validate` subcommands.
- CityDoctor2 validation bridge (`citydoctor.py`, `citysmith validate`): shells
  out to a separately downloaded CityDoctor2 install (Java, OGC-QIE check
  taxonomy), parses its XML report into a `ValidationReport`. Bundled default
  `.yml` validation plan. Verified against a real CityDoctor2 3.18.3 release on
  both the test fixture and the full 501-building output; found defect classes
  CitySmith does not check natively (non-planarity, ring orientation, roof
  fragmentation) and corroborated the native closedness report independently.

- `lod` now reads whichever detail level a feature actually has (LOD3
  preferred, LOD2 as fallback) instead of only ever looking for `lod3Solid`.
  LOD1/LOD0 derive equally well from an LOD2 source, since both only need
  Ground/Roof surface heights; LOD2 derivation still requires an LOD3 source.
  `Report` gained `source_lod3`/`source_lod2`/`source_none`/
  `lod2_already_present` so this is always visible, never silent.
- `--levels` now rejects unsupported values (e.g. `3`) with a clear error
  instead of silently ignoring them.
- The engine now recognises three structurally different CityGML shell
  encodings, not just the aggregating-`gml:Solid` style: `surfaces` (no
  solid, each boundary surface carries its own geometry directly, seen from
  SketchUp-modelled, FME-workbench-exported data) and `unclassified` (a flat,
  unclassifiable bag of polygons, detected and reported, never silently
  dropped, but genuinely not processable). Detection is structural, never
  guessed from filenames or tool metadata. `Report` gained
  `source_unclassified`/`source_pattern_solid`/`source_pattern_surfaces`.
- New `citysmith inspect <file>` command and `citysmith.inspect()`/
  `InspectReport`: a read-only preflight that reports, per feature, what
  geometry pattern was found and what each capability can and can't do with
  it, and why, without writing any output. Meant to be run first on
  unfamiliar data instead of discovering a silent zero-progress run.
- `validate`/`citysmith.validate_citydoctor` gained `--pdf`/`pdf_path`: also
  renders CityDoctor2's own human-readable PDF report (`-pdfreport`, an
  Apache-FOP walkthrough of every check and error) alongside the XML report,
  which is always produced regardless. `ValidationReport` gained
  `pdf_report_path`.
- README's example renders now come from `tests/CS1_lod*.gml` (the real,
  reproducible fixture the test suite itself runs against) instead of static
  screenshots of data no longer in the repo, rendered directly from the GML
  with a small matplotlib script, no CityGML viewer needed.
- Second real-world reference set, `tests/CS2_lod*.gml`, cropped from the
  same open-licensed Hamburg LGV dataset as the earlier Hamburg example
  (`3D-Gebäudemodell LoD3.0-HH Hamburg`, DL-DE-BY-2.0): a single-feature
  CityGRID/UVM "solid"-pattern building with 14 roof installations, run
  through the full `lod`/`semantics`/`convert`/`validate` pipeline the same
  way CS1 was.
- README credits CityGRID® as a registered trademark of UVM Systems GmbH,
  since CS1 and CS2 are both real exports from their software (previously
  "CityGRID" was only used as a technical term for the export pattern, with
  no credit to the company).
- `citysmith crop` (`crop.py`, `citysmith.crop`): extract a named subset of
  buildings by `gml:id` out of a larger file, e.g. cutting a small test area
  out of a city-wide export. Selection is `--ids` (comma list) and/or
  `--ids-file` (one id per line); naming a `BuildingPart`'s id keeps its
  whole parent `Building`, since a lone part is not independently meaningful
  CityGML. Ids not found in the source are reported by id, never silently
  dropped. The output's `gml:Envelope` is recomputed from the kept
  geometry's real bounds rather than left describing the original file's
  extent. `app:appearance` blocks nested per building (this project's own
  reference data) survive untouched by construction; the rarer document-level
  appearance convention is detected and any texture reference left pointing
  at removed geometry is pruned, reported as `appearance_pruned`. The closing
  `</CityModel>` tag keeps the source's own indentation: real CityGML gives
  only the true last `cityObjectMember` a bare-newline tail into the closing
  tag, every other one indents into its next sibling, so naively keeping an
  earlier feature (the overwhelmingly common case) left its own mid-document
  indentation behind the closing tag instead.
- `semantics` now gives every `WallSurface`/`RoofSurface`/`GroundSurface`/
  `OuterFloorSurface`/`OuterCeilingSurface`/`ClosureSurface`/`Window`/`Door`/
  `BuildingInstallation` a readable `gml:id` anchored to the owning
  `Building`/`BuildingPart`'s own id (e.g. `CS1_wall_0001`,
  `CS1_window_0007`, `CS1_installation_0001`) instead of an opaque
  `UUID_<uuid5>`, per the SIG3D Modeling Guide for 3D Objects Part 2's
  mandatory-id rule. `Window`/`Door`/`OuterCeilingSurface`/`ClosureSurface`
  were previously not covered at all (0 of 25 windows had an id on the CS1
  fixture; confirmed again on a real 500-building production CityGML export,
  0 of 8851 windows and 0 of 89 doors had one). `BuildingInstallation` ids
  stay independent of balcony/chimney classification (the id is assigned
  before classification runs and must stay stable even as the classifier
  heuristic improves), classification is still carried by `bldg:function`
  and the `type` generic attribute as before. Ids that already exist
  (including old UUID-style ones) are left untouched by default; new
  `semantics --overwrite-ids` replaces them too, deterministically (same
  document order produces the same ids on every re-run). A boundary surface
  found outside any `Building`/`BuildingPart` (malformed, but source data
  varies) falls back to the old UUID scheme rather than being left unnamed,
  and is listed by id in the run summary (`SemanticReport.no_anchor_ids`)
  rather than silently accepted.
- `citysmith inspect` reports `gml:id` coverage per element type against the
  same SIG3D mandatory-id rule (e.g. `Window: 0/25 have gml:id`), so a gap
  is visible before running `semantics`, not discovered afterward.
- `tests/` restructured into one folder per exporting capability (`lod/`,
  `semantics/`, `convert/`, `validate/`), each with a source file, one output
  file per flag/variant that isolates what that flag alone does, a final
  file with everything applied together, and its own `README.md` naming the
  exact command and effect of every file in it, plus a top-level
  `tests/README.md` mapping the whole folder. `CS2`'s existing flat set is
  unchanged, it demonstrates the pipeline end to end on a second building
  rather than isolating individual flags. Every new file's effect was
  verified against the real output (e.g. the three `--lod1-height` variants
  produce genuinely different, correctly ordered block heights; the three
  `semantics --no-*` combinations each touch only what they claim to), not
  just trusted from the command's printed summary.

### Changed
- Installation classification now uses the building eave height: an installation
  whose body sits below the eave is a balcony, above it a chimney/roof structure.
  This catches balconies that CityGRID does not label with OuterFloorSurface
  (previously all such installations were misread as chimneys or left unknown).
- `enhance()`/`add_lod2()` renamed the `keep_lod3` parameter to `keep_source`,
  since the source being kept isn't always LOD3 anymore.

### Fixed
- LOD2 walls/roofs modelled as many small panels (some real exports omit the
  panel where a window is instead of cutting a hole, so de-holing had nothing
  to fix) are now collapsed into a clean simplified surface: polygons are
  clustered by plane (`geometry.cluster_coplanar_rings`) and each coplanar
  cluster merged into its true outer boundary
  (`geometry.union_coplanar_polygons`) by cancelling every edge shared by two
  adjacent panels, pure Python, no new dependency. This is a real boundary
  union, not a convex hull, so a concave, L-shaped or stepped wall keeps its
  actual outline instead of being rounded out to a bulging envelope with
  diagonal chords (the earlier convex-hull merge did exactly that on real
  data and was wrong). Interior loops left by missing panels (window/door
  gaps) or genuine cut interior rings come out as their own loops and are
  dropped, filling them. Applies both when deriving LOD2 from LOD3 and when a
  native LOD2 source is itself panelized. Unsafe cases fall back to the
  untouched per-polygon path: a surface bundling genuinely different faces
  (this project's own `box_lod3.gml` fixture bundles all four walls of a box
  under one `WallSurface`), or panels meeting at a T-junction (a vertex
  mid-edge), where edge cancellation would leave a broken outline.
- LOD2 roof holes are now filled too (window/door/skylight openings left as
  cut interior rings are LOD3 detail, not part of an LOD2 shell); previously
  only walls were de-holed on the per-polygon fallback path, so roof
  skylights survived into LOD2. Ground interior rings are still kept (a real
  courtyard, not an opening).
- `--lower-only` no longer deletes freshly built LOD2 output when a
  feature's native source already was LOD2: the exact elements built this
  run are now tracked by object identity and always protected from
  stripping, since LOD2 tag names alone can't tell original source content
  apart from this run's own rebuilt output.
- `--lower-only` no longer leaves stray LOD3 content (e.g. window openings
  modelled as separate features with their own geometry) behind on an
  LOD2-sourced feature; LOD3 debris is now always stripped regardless of
  which tier a feature was actually derived from.
- LOD1 prism faces now have outward-facing normals (footprint reoriented to
  counter-clockwise), so the roof/top no longer disappears under back-face
  culling in viewers such as FZK ModelViewer.
- LOD1's default block height changed from Min. Eaves Height (the global
  minimum across every `RoofSurface`, which let a single low secondary roof
  segment drag the whole block down) to **Average Roof Height**, `(Min. Eaves
  Height + Max. Ridge Height) / 2`, following the SIG3D Modeling Guide for 3D
  Objects, Part 2, section 2.4. `--lod1-height eave`/`ridge` remain available
  for the guide's other two named heights.
- `localname()` no longer raises on XML comment / processing-instruction nodes.
- LOD1 is now derived for buildings whose `GroundSurface` is more than one
  polygon, which previously skipped outright: on the Hamburg reference
  dataset that was 25 of 178 buildings producing no LOD1 at all. Adjoining
  pieces are unioned into one outline (edge cancellation, the same merge
  LOD2 uses on wall panels) and still yield a single prism. Pieces that
  genuinely don't adjoin get **one prism each inside a
  `gml:CompositeSolid`**: connectivity analysis showed these are not
  separate structures but single buildings spanning a passage at ground
  level, so only the footprint is split while the volume above is
  continuous. Each piece takes its own eave/ridge from the roof surfaces
  over it, since wings of one building differ in height. Pieces below
  `LOD1_MIN_PIECE_AREA` (10 m²) are dropped as pillars rather than extruded
  into thin full-height spikes, but never the last one, so no building is
  left without geometry. `Report` gained `lod1_composite` and
  `lod1_pieces_skipped`. See docs/DESIGN.md, LOD1 section.
- `--lower-only` no longer deletes features left without geometry. Silently
  dropping a building makes a run lose count with no trace of which ones or
  why; they are now kept and named in the run summary instead, matching the
  "report, don't force" approach used everywhere else. `Report` gained
  `kept_empty_ids`.

### Notes
- Window/door openings modelled as a *notch in the wall polygon's own
  exterior ring* (the boundary juts inward around the opening and back out)
  are left as-is, not filled. Unlike an interior ring or a missing panel,
  both of which are removed, a reentrant boundary indentation can't be told
  apart from a wall's genuine concave shape (an L or a recessed entrance)
  without a size heuristic that would also flatten real concave walls. This
  encoding only appears in inconsistent exports (one wall can even mix all
  three ways of modelling openings); well-formed LOD3 uses interior rings.
  See docs/DESIGN.md, LOD2 section. Roadmap: an opt-in, size-bounded,
  wall-only boundary-notch fill for known-messy sources.
- Source LOD3 solids are frequently not watertight (T-junctions, missing
  soffits). Watertightness is reported, not forced. Healing and extrusion-based
  reconstruction are planned.
- A `Building` that merges several real structures sharing one *connected*
  footprint still gets a single LOD1 box, and no single block height is
  correct for it. Unlike the split-footprint case above, nothing in the
  geometry distinguishes it from a genuinely simple building, so it is not
  detected or flagged. Emitting such features as separate `BuildingPart`s is
  future work.
- Building installations are not yet emitted as CityJSON CityObjects; balcony
  hard-tier geometry (thickness collapse, face reclassification) is future work.
