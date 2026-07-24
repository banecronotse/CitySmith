# Changelog

All notable changes to this project are documented here. The format follows
[Keep a Changelog](https://keepachangelog.com/) and the project uses
[Semantic Versioning](https://semver.org/).

## [Unreleased]
### Added
- Project scaffold, packaging and design document.
- CityGML 2.0 namespace helpers.
- LOD3 to LOD2 engine (`citysmith.add_lod2`) with embed and `--lod2-only` modes,
  window/door hole filling, reproducible ids and self-contained LOD2 geometry.
- Native watertightness quality report (per-building open-edge buckets).
- Command line (`citysmith`) with JSON report output.
- Test suite with a box fixture covering both modes, id uniqueness,
  reproducibility and hole filling.

- LOD1 derivation: watertight extruded block solid from the footprint, eave or
  ridge height. LOD0 derivation: footprint MultiSurface.
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

### Changed
- Installation classification now uses the building eave height: an installation
  whose body sits below the eave is a balcony, above it a chimney/roof structure.
  This catches balconies that CityGRID does not label with OuterFloorSurface
  (previously all such installations were misread as chimneys or left unknown).

### Fixed
- LOD1 prism faces now have outward-facing normals (footprint reoriented to
  counter-clockwise), so the roof/top no longer disappears under back-face
  culling in viewers such as FZK ModelViewer.
- `localname()` no longer raises on XML comment / processing-instruction nodes.

### Notes
- Source LOD3 solids are frequently not watertight (T-junctions, missing
  soffits). Watertightness is reported, not forced. Healing and extrusion-based
  reconstruction are planned.
- Building installations are not yet emitted as CityJSON CityObjects; balcony
  hard-tier geometry (thickness collapse, face reclassification) is future work.
