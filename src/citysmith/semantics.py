"""Semantic enhancer (easy tier).

Applies the project's LoD3 rulebook fixes that need no geometry surgery:

  * ensure every feature, building installation and thematic boundary surface
    carries a persistent gml:id (UUID),
  * classify each building installation from its structure (an OuterFloorSurface
    means balcony; a roof-plus-wall box means chimney),
  * add the required bldg:function code and a `type` generic attribute,
  * add an aggregating bldg:lod3Geometry that references the installation's
    boundary polygons, if one is missing.

Heavier corrections (BuildingPart to installation restructuring, collapsing
thick balconies to single surfaces, reclassifying faces by orientation) are out
of scope here and are left for a later tier.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field

from lxml import etree

from .citygml import BLDG, GML, NS, gml_id, localname, q
from .geometry import parse_pos_list

GEN = NS["gen"]
_ID_NAMESPACE = uuid.UUID("1b9d6bcd-0c6f-5a1e-9d0a-10b0c0ffee00")

# CityGML BuildingInstallation_function codes (SIG3D standard code list).
FUNCTION_CODES = {"chimney": "1030", "balcony": "1000"}

# Elements that must carry a gml:id per the rulebook.
_ID_REQUIRED = {
    "Building", "BuildingPart", "BuildingInstallation",
    "WallSurface", "RoofSurface", "GroundSurface", "OuterFloorSurface",
}


@dataclass
class SemanticReport:
    ids_added: int = 0
    functions_added: int = 0
    types_added: int = 0
    lod3geometry_added: int = 0
    classified: dict = field(default_factory=lambda: {"chimney": 0, "balcony": 0, "unknown": 0})

    def to_dict(self) -> dict:
        return self.__dict__.copy()


def _det_id(seed: str) -> str:
    return f"UUID_{uuid.uuid5(_ID_NAMESPACE, seed)}"


def _first_polygon_id(el):
    for poly in el.iter(q(GML, "Polygon")):
        pid = poly.get(q(GML, "id"))
        if pid:
            return pid
    return None


def _ensure_id(el, fallback_seed: str, report: SemanticReport) -> str:
    existing = gml_id(el)
    if existing:
        return existing
    seed = _first_polygon_id(el) or fallback_seed
    new = _det_id(f"{seed}:{localname(el)}:id")
    el.set(q(GML, "id"), new)
    report.ids_added += 1
    return new


def _z_extent(el):
    """(min_z, max_z) over every posList under an element, or None."""
    zs = [p[2] for pos in el.iter(q(GML, "posList")) if pos.text
          for p in parse_pos_list(pos.text)]
    return (min(zs), max(zs)) if zs else None


def _nearest_feature(el):
    for anc in el.iterancestors():
        ln = localname(anc)
        if ln in ("BuildingInstallation", "Building", "BuildingPart"):
            return anc, ln
    return None, None


def compute_eaves(root):
    """Eave (lowest main-roof height) per feature and per top building.

    Keys are element sourcelines (stable across lxml proxy objects). Roof
    surfaces belonging to a BuildingInstallation are ignored, so only the main
    building shell defines the eave.
    """
    feature_eave: dict = {}
    building_eave: dict = {}
    for rs in root.iter(q(BLDG, "RoofSurface")):
        owner, owner_ln = _nearest_feature(rs)
        if owner is None or owner_ln == "BuildingInstallation":
            continue
        ext = _z_extent(rs)
        if ext is None:
            continue
        z = ext[0]
        ol = owner.sourceline
        if ol not in feature_eave or z < feature_eave[ol]:
            feature_eave[ol] = z
        buildings = [a for a in rs.iterancestors() if localname(a) == "Building"]
        if buildings:
            bl = buildings[-1].sourceline
            if bl not in building_eave or z < building_eave[bl]:
                building_eave[bl] = z
    return feature_eave, building_eave


def eave_for(inst, feature_eave, building_eave):
    """Reference eave for an installation: its nearest feature's own eave, else
    the eave of the top building it belongs to."""
    top_bl = None
    for anc in inst.iterancestors():
        ln = localname(anc)
        if ln in ("Building", "BuildingPart") and anc.sourceline in feature_eave:
            return feature_eave[anc.sourceline]
        if ln == "Building":
            top_bl = anc.sourceline
    return building_eave.get(top_bl) if top_bl is not None else None


def classify_installation(inst, eave=None, mid_z=None) -> str | None:
    """Classify a BuildingInstallation.

    An OuterFloorSurface is a decisive balcony signal. Otherwise, when the
    building eave is known, an installation whose body sits below the eave is a
    balcony and one above it is a chimney/roof structure. Without a known eave,
    fall back to surface types (roof+wall box means chimney).
    """
    types = {localname(s) for s in inst.iter() if localname(s).endswith("Surface")}
    if "OuterFloorSurface" in types:
        return "balcony"
    if eave is not None and mid_z is not None:
        return "balcony" if mid_z < eave else "chimney"
    if "RoofSurface" in types and "WallSurface" in types:
        return "chimney"
    return None


def _has_child(el, tag_local: str) -> bool:
    return el.find(q(BLDG, tag_local)) is not None


def _add_string_attribute(el, name: str, value: str) -> None:
    attr = etree.Element(q(GEN, "stringAttribute"))
    attr.set("name", name)
    etree.SubElement(attr, q(GEN, "value")).text = value
    el.insert(0, attr)  # generic attributes come first


def _add_function(el, code: str) -> None:
    fn = etree.Element(q(BLDG, "function"))
    fn.text = code
    # Place before the first boundedBy (function precedes boundary surfaces).
    idx = next((i for i, c in enumerate(el) if localname(c) == "boundedBy"), len(el))
    el.insert(idx, fn)


def _add_lod3_geometry(inst) -> bool:
    ids = [p.get(q(GML, "id")) for p in inst.iter(q(GML, "Polygon")) if p.get(q(GML, "id"))]
    if not ids:
        return False
    prop = etree.Element(q(BLDG, "lod3Geometry"))
    ms = etree.SubElement(prop, q(GML, "MultiSurface"))
    for pid in ids:
        sm = etree.SubElement(ms, q(GML, "surfaceMember"))
        sm.set(q(NS["xlink"], "href"), f"#{pid}")
    inst.append(prop)  # aggregate geometry after the boundary surfaces
    return True


def enhance_semantics(input_path: str, output_path: str, *, add_ids: bool = True,
                      classify: bool = True, aggregate: bool = True) -> SemanticReport:
    """Apply the easy-tier semantic fixes and write the result."""
    parser = etree.XMLParser(huge_tree=True, remove_blank_text=False)
    tree = etree.parse(input_path, parser)
    root = tree.getroot()
    report = SemanticReport()

    # 1) ids on every required element.
    if add_ids:
        elems = [e for e in root.iter() if localname(e) in _ID_REQUIRED]
        for n, el in enumerate(elems):
            _ensure_id(el, fallback_seed=f"seq{n}", report=report)

    # 2) installation semantics.
    feature_eave, building_eave = compute_eaves(root) if classify else ({}, {})
    for inst in root.iter(q(BLDG, "BuildingInstallation")):
        _ensure_id(inst, fallback_seed="inst", report=report)
        if classify:
            ext = _z_extent(inst)
            mid = (ext[0] + ext[1]) / 2 if ext else None
            kind = classify_installation(inst, eave_for(inst, feature_eave, building_eave), mid)
        else:
            kind = None
        report.classified["unknown" if kind is None else kind] += 1

        if kind is not None:
            if not _has_child(inst, "function"):
                _add_function(inst, FUNCTION_CODES[kind])
                report.functions_added += 1
            has_type = any(a.get("name") == "type" for a in inst.iter(q(GEN, "stringAttribute")))
            if not has_type:
                _add_string_attribute(inst, "type", kind)
                report.types_added += 1

        if aggregate and not _has_child(inst, "lod3Geometry"):
            if _add_lod3_geometry(inst):
                report.lod3geometry_added += 1

    tree.write(output_path, xml_declaration=True, encoding="utf-8")
    return report
