# Related work

This page places CitySmith next to the open-source tools and papers closest to
what it does. It is based on public documentation and repositories checked on
2026-10-13. Corrections are welcome.

## Closest tools

### CityLDT (CityGML Levels of Detail Transformation Tool)

Malhotra, Raming, Frisch and van Treeck, *Open-Source Tool for Transforming
CityGML Levels of Detail*, Energies 2021, 14(24), 8250
([doi:10.3390/en14248250](https://doi.org/10.3390/en14248250)). Repository:
[RWTH-E3D/CityLDT](https://github.com/RWTH-E3D/CityLDT), MIT license.

- Changes the level of detail of buildings between LoD0 and LoD2, with a
  desktop GUI, aimed at urban energy simulation.
- It does not reuse the existing geometry. It reads only the ground surface
  (or the LoD0 footprint) and rebuilds the building from parameters: building
  height, roof type, roof height and heading, taken from attributes or typed
  in by the user. Going down to LoD1 extrudes the footprint to one height;
  going up to LoD2 puts a template roof (flat, shed, gable, hip, pyramid) on
  it, for four-cornered footprints only.
- Support for LoD3 and LoD4 is named as future work in the paper and the
  README.
- Version 0.1, installed by cloning the repository. The latest commit on the
  development branch dates from November 2021, there are no tagged releases.

Where CitySmith differs: it never rebuilds from parameters. It derives LoD2,
LoD1 and LoD0 from the existing LoD3 geometry (and LoD1 and LoD0 from LoD2):
window and door holes are filled in the real walls, the LoD1 block takes its
height from the real roof per the SIG3D definitions (or from
`measuredHeight` if asked), and footprints split by a passage stay split. It
only goes downwards, so it adds no geometry the source does not contain, and
it is a command line tool meant for batch use on large files. CityLDT's
upward direction (footprint plus parameters to LoD1 or LoD2) is the part
CitySmith does not cover.

### CityEnrich

Malhotra, Shamovich, Raming, Frisch and van Treeck, *An Open-Source CityGML
Enrichment Tool (CityEnrich)*, BauSim 2022
([paper](https://publications.ibpsa.org/proceedings/bausim/2022/papers/bausim2022_Malhotra_Avichal.pdf)).
Repository: [RWTH-E3D/CityEnrich](https://github.com/RWTH-E3D/CityEnrich), MIT
license.

- Manual, GUI based enrichment of LoD2 buildings with energy attributes
  (thermal zones and construction materials), exported as CityGML 2.0 with
  Energy ADE 1.0.
- Version 0.1. The latest commit dates from August 2023, there are no tagged
  releases.

Where CitySmith differs: its semantic enrichment is automatic and rule based,
and it targets LoD3 content (identifiers, function codes and type attributes
for building installations, aggregated LoD3 geometry) rather than energy data.
The two tools solve different problems and do not overlap in what they add.

## Image based LoD3 reconstruction

Tang, Li, Liang, Wysocki, Biljecki, Holst and Jutzi, *Texture2LoD3: Enabling
LoD3 Building Reconstruction With Panoramic Images*, CVPR Workshops 2025
([project page](https://wenzhaotang.github.io/Texture2LoD3/),
[arXiv:2504.05249](https://arxiv.org/abs/2504.05249)), with the ReLoD3
benchmark of 27 LoD3 buildings.

- Uses an existing LoD2 (or LoD1) model as a flat target to rectify street
  level panoramas, then segments windows and doors on the rectified facade
  texture as a step towards LoD3.
- Works in the opposite direction to CitySmith: it adds detail from images,
  while CitySmith works on CityGML that already has the detail and derives
  simpler levels and missing semantics from it. LoD3 models produced this
  way are possible CitySmith input.

## Conversion and format tools CitySmith builds on or interoperates with

- [citygml-tools](https://github.com/citygml4j/citygml-tools) and
  [cjio](https://github.com/cityjson/cjio) convert and process CityGML and
  CityJSON. CitySmith's CityJSON writer is checked against cjio.
  citygml-tools is the companion tool for what CitySmith leaves out on
  purpose: reprojection, upgrading to CityGML 3.0, XSLT transformations and
  texture handling. Both have a `merge` command; CitySmith's stops on
  different coordinate reference systems and reports duplicate buildings,
  while citygml-tools streams very large inputs and copies textures along.
  CitySmith's schema check follows citygml-tools' `validate`: official
  schemas built in, extension schemas added on request, each error reported
  once per position. citygml-tools also checks CityGML 1.0 and 3.0.
- [CityDoctor2](https://github.com/CityDoctor/CityDoctor2) validates CityGML.
  CitySmith calls it and reports its findings without changing the data.

## Rulebooks and background

- SIG3D, *Modeling Guide for 3D Objects*, Part 1 (basics and geometry
  validation rules) and Part 2 (buildings in LoD1 to LoD3). CitySmith follows
  these for closed solids and for the LoD1 height definitions.
- Generalization of building models within CityGML, including the derivation
  of lower levels of detail from higher ones: see for example the framework in
  [*Generalization of buildings within the framework of CityGML*](https://www.tandfonline.com/doi/full/10.1080/10095020.2013.866617)
  and [*A three-step strategy for generalization of 3D building models based on
  CityGML specifications*](https://link.springer.com/article/10.1007/s10708-013-9475-0).

## Scope note: IFC and BIM sources

CitySmith starts from CityGML that already exists and completes or simplifies
it. It does not convert IFC. Tools for that exist, for example the algorithm
by Donkers, Ledoux et al. (2016), BIM2GIS4J, and the CityGML 3.0 converter
from the TUM GIS group. Their CityGML output can be used as CitySmith input.
