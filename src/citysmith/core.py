"""Core CityGML LOD derivation engine.

Given CityGML 2.0 buildings modelled in LOD3 or LOD2, derive lower levels of
detail and embed them alongside the source (or emit a lower-LOD-only file).
See docs/DESIGN.md for the rationale.

Each feature (Building/BuildingPart) is read from whichever detail level it
actually has: `lod3Solid` if present, else `lod2Solid`. LOD1/LOD0 extrusion
only needs Ground/Roof surface heights, which both levels provide, so it
works from either source. LOD2 *derivation* specifically (de-holing walls,
copying roof/ground) only makes sense starting from LOD3, since that's the
only source with window/door holes and installations to remove in the first
place; a feature already at LOD2 has nothing to derive there.

LOD2  faithful shell, derived from LOD3 only: roof and ground copied, walls
      copied with window/door holes filled, structure mirrored from LOD3.
      Watertight only if the source shell is; watertightness is reported, not
      forced.
LOD1  extruded block, derived from LOD3 or LOD2: the footprint (ground
      surface) raised from base to a top height. Watertight by construction.
LOD0  footprint, derived from LOD3 or LOD2: the ground surface flattened to
      base height.
"""

from __future__ import annotations

import uuid
from collections import OrderedDict, defaultdict
from dataclasses import dataclass, field

from lxml import etree

from .citygml import BLDG, GML, XLINK, BOUNDARY_LOCALNAMES, gml_id, href_target, localname, q
from .geometry import extrude_prism, parse_pos_list, shell_stats

# Fixed namespace so uuid5-derived ids are stable across runs and machines.
_ID_NAMESPACE = uuid.UUID("1b9d6bcd-0c6f-5a1e-9d0a-10b0c0ffee00")

_LOD3_GEOMETRY_PROPS = {
    "lod3Solid", "lod3MultiSurface", "lod3Geometry",
    "lod3TerrainIntersection", "lod3MultiCurve",
}
_LOD2_GEOMETRY_PROPS = {
    "lod2Solid", "lod2MultiSurface", "lod2Geometry",
    "lod2TerrainIntersection", "lod2MultiCurve",
}
_SOURCE_GEOMETRY_PROPS = {3: _LOD3_GEOMETRY_PROPS, 2: _LOD2_GEOMETRY_PROPS}
_INSTALLATION_PROPS = {"outerBuildingInstallation", "interiorBuildingInstallation"}
_FEATURE_TAGS = {q(BLDG, "Building"), q(BLDG, "BuildingPart")}


@dataclass
class Report:
    """Outcome of an enhance run."""

    mode: str = "embed"
    levels: tuple = (2,)
    features: int = 0
    source_lod3: int = 0
    source_lod2: int = 0
    source_none: int = 0
    lod2_already_present: int = 0
    walls_deholed: int = 0
    interior_rings_removed: int = 0
    roof_copied: int = 0
    ground_copied: int = 0
    other_copied: int = 0
    lod1_added: int = 0
    lod1_skipped: int = 0
    lod0_added: int = 0
    lod0_skipped: int = 0
    closed: int = 0
    open: int = 0
    skipped_no_type: int = 0
    boundary_edges_total: int = 0
    quality_buckets: dict = field(default_factory=lambda: {
        "watertight": 0, "1-4_open_edges": 0, "5-20_open_edges": 0, "20+_open_edges": 0,
    })
    open_feature_ids: list[str] = field(default_factory=list)

    @property
    def total(self) -> int:
        return self.closed + self.open

    def _record_quality(self, stats: dict) -> None:
        self.boundary_edges_total += stats["boundary_edges"]
        b = stats["boundary_edges"]
        if stats["closed"]:
            self.quality_buckets["watertight"] += 1
        elif b <= 4:
            self.quality_buckets["1-4_open_edges"] += 1
        elif b <= 20:
            self.quality_buckets["5-20_open_edges"] += 1
        else:
            self.quality_buckets["20+_open_edges"] += 1

    def to_dict(self) -> dict:
        d = self.__dict__.copy()
        d["total"] = self.total
        d["levels"] = list(self.levels)
        return d


# --- small helpers -----------------------------------------------------------

def _det_id(seed: str) -> str:
    return f"UUID_{uuid.uuid5(_ID_NAMESPACE, seed)}"


def _fmt(v: float) -> str:
    return f"{v:.3f}"


def _pos_list_text(points) -> str:
    return " ".join(f"{_fmt(x)} {_fmt(y)} {_fmt(z)}" for (x, y, z) in points)


def _exterior_ring(polygon):
    ext = polygon.find(q(GML, "exterior"))
    return ext.find(q(GML, "LinearRing")) if ext is not None else None


def _ring_points(polygon):
    ring = _exterior_ring(polygon)
    if ring is None:
        return []
    pos = ring.find(q(GML, "posList"))
    return parse_pos_list(pos.text) if pos is not None and pos.text else []


def _boundary_ancestor(polygon):
    for anc in polygon.iterancestors():
        if localname(anc) in BOUNDARY_LOCALNAMES:
            return anc
    return None


def _make_polygon(points, pid: str):
    poly = etree.Element(q(GML, "Polygon"))
    poly.set(q(GML, "id"), pid)
    ring = etree.SubElement(etree.SubElement(poly, q(GML, "exterior")), q(GML, "LinearRing"))
    ring.set(q(GML, "id"), _det_id(f"{pid}:ring"))
    pos = etree.SubElement(ring, q(GML, "posList"))
    pos.set("srsDimension", "3")
    pos.text = _pos_list_text(points)
    return poly


def _surface_member_ref(pid: str):
    sm = etree.Element(q(GML, "surfaceMember"))
    sm.set(q(XLINK, "href"), f"#{pid}")
    return sm


def _copy_polygon(polygon, new_pid: str, *, dehole: bool):
    copy = etree.fromstring(etree.tostring(polygon))
    removed = 0
    if dehole:
        for interior in copy.findall(q(GML, "interior")):
            copy.remove(interior)
            removed += 1
    copy.set(q(GML, "id"), new_pid)
    for n, ring in enumerate(copy.iter(q(GML, "LinearRing"))):
        seed = ring.get(q(GML, "id")) or f"{new_pid}:{n}"
        ring.set(q(GML, "id"), _det_id(f"{seed}:lod2ring"))
    return copy, removed


def _build_polygon_index(root) -> dict:
    index = {}
    for poly in root.iter(q(GML, "Polygon")):
        pid = poly.get(q(GML, "id"))
        if pid:
            index[pid] = poly
    return index


def _shell_polygon_ids(solid) -> list[str]:
    ids = []
    for sm in solid.iter(q(GML, "surfaceMember")):
        target = href_target(sm)
        if target:
            ids.append(target)
    return ids


def _find_source_solid(feature):
    """Return (solid_element, level) for a feature's most detailed geometry.

    lod3Solid is preferred; lod2Solid is the fallback so LOD1/LOD0 can still
    be derived from LOD2-only input, which is the far more common real-world
    format. (None, None) if the feature has neither (e.g. LOD1/LOD0-only or
    LOD4 data, which this engine does not read).
    """
    lod3 = feature.find(q(BLDG, "lod3Solid"))
    if lod3 is not None:
        return lod3, 3
    lod2 = feature.find(q(BLDG, "lod2Solid"))
    if lod2 is not None:
        return lod2, 2
    return None, None


# --- shell collection --------------------------------------------------------

def _collect_shell(solid, poly_index, report):
    """Return (groups, typed) for a solid's shell.

    groups: OrderedDict(boundary-surface element -> [polygon]) for structure
            mirroring. typed: dict(thematic type -> [polygon]). Works for a
            lod3Solid or a lod2Solid identically: both reference their shell
            polygons via gml:surfaceMember, classified by nearest boundary
            surface ancestor.
    """
    groups: "OrderedDict" = OrderedDict()
    typed: dict = defaultdict(list)
    for pid in _shell_polygon_ids(solid):
        poly = poly_index.get(pid)
        if poly is None:
            continue
        surf = _boundary_ancestor(poly)
        if surf is None:
            report.skipped_no_type += 1
            continue
        groups.setdefault(surf, []).append(poly)
        typed[localname(surf)].append(poly)
    return groups, typed


# --- LOD builders ------------------------------------------------------------

def _build_lod2(groups, emitted_ids, report, feature_seed):
    """Return (list of bldg:boundedBy + bldg:lod2Solid nodes, qc_rings)."""
    new_nodes = []
    solid_refs: list[str] = []
    qc_rings = []

    for s_idx, (surf, polys) in enumerate(groups.items()):
        btype = localname(surf)
        is_wall = btype == "WallSurface"
        bounded = etree.Element(q(BLDG, "boundedBy"))
        surf_el = etree.SubElement(bounded, q(BLDG, btype))
        seed = gml_id(surf) or gml_id(polys[0]) or f"{feature_seed}:s{s_idx}"
        surf_el.set(q(GML, "id"), _det_id(f"{seed}:lod2surf"))
        ms = etree.SubElement(
            etree.SubElement(surf_el, q(BLDG, "lod2MultiSurface")), q(GML, "MultiSurface")
        )
        for p_idx, poly in enumerate(polys):
            orig_pid = gml_id(poly) or f"{feature_seed}:s{s_idx}p{p_idx}"
            new_pid = _det_id(f"{orig_pid}:lod2poly")
            solid_refs.append(new_pid)
            qc_rings.append(_ring_points(poly))
            if new_pid in emitted_ids:
                ms.append(_surface_member_ref(new_pid))
                continue
            emitted_ids.add(new_pid)
            copy, removed = _copy_polygon(poly, new_pid, dehole=is_wall)
            etree.SubElement(ms, q(GML, "surfaceMember")).append(copy)
            if is_wall:
                report.walls_deholed += 1
                report.interior_rings_removed += removed
            elif btype == "RoofSurface":
                report.roof_copied += 1
            elif btype == "GroundSurface":
                report.ground_copied += 1
            else:
                report.other_copied += 1
        new_nodes.append(bounded)

    solid_prop = etree.Element(q(BLDG, "lod2Solid"))
    comp = etree.SubElement(
        etree.SubElement(etree.SubElement(solid_prop, q(GML, "Solid")), q(GML, "exterior")),
        q(GML, "CompositeSurface"),
    )
    for pid in solid_refs:
        comp.append(_surface_member_ref(pid))
    new_nodes.append(solid_prop)
    return new_nodes, qc_rings


def _heights(typed):
    """Return (z_base, z_eave, z_ridge) from a shell's polygons, or None.

    Named after the SIG3D Modeling Guide for 3D Objects, Part 2, section 2.4
    "Heights": z_base is Min. Relief Height (the terrain intersection), z_eave
    is Min. Eaves Height (the lowest point of any roof surface) and z_ridge is
    Max. Ridge Height (the highest point of the shell). Both are literal
    min/max, not weighted or clustered, matching the spec's own definitions.
    """
    ground = typed.get("GroundSurface", [])
    roof = typed.get("RoofSurface", [])
    all_pts = [p for polys in typed.values() for poly in polys for p in _ring_points(poly)]
    if not all_pts:
        return None
    z_all = [z for _, _, z in all_pts]
    z_base = min(z for _, _, z in
                 [pt for poly in ground for pt in _ring_points(poly)]) if ground else min(z_all)
    roof_pts = [pt for poly in roof for pt in _ring_points(poly)]
    z_eave = min(z for _, _, z in roof_pts) if roof_pts else max(z_all)
    return z_base, z_eave, max(z_all)


def _build_lod1(typed, feature_seed, report, height="average"):
    """Return a bldg:lod1Solid prism node, or None if not derivable.

    `height` selects the SIG3D-named top of the block:
      'eave'    Min. Eaves Height (the conservative, spec-literal LOD1 value)
      'ridge'   Max. Ridge Height (the tallest possible guess)
      'average' Average Roof Height = (Min. Eaves + Max. Ridge) / 2 (default,
                the spec's own formula for approximating the real roof volume)
    """
    ground = typed.get("GroundSurface", [])
    if len(ground) != 1:
        report.lod1_skipped += 1
        return None
    hs = _heights(typed)
    if hs is None:
        report.lod1_skipped += 1
        return None
    z_base, z_eave, z_ridge = hs
    if height == "ridge":
        z_top = z_ridge
    elif height == "eave":
        z_top = z_eave
    else:
        z_top = (z_eave + z_ridge) / 2
    if z_top <= z_base:
        z_top = z_ridge
    if z_top <= z_base:
        report.lod1_skipped += 1
        return None

    seed = gml_id(ground[0]) or f"{feature_seed}:g0"
    ring = _ring_points(ground[0])
    footprint = [(x, y) for (x, y, _) in ring]
    faces = extrude_prism(footprint, z_base, z_top)

    solid_prop = etree.Element(q(BLDG, "lod1Solid"))
    comp = etree.SubElement(
        etree.SubElement(etree.SubElement(solid_prop, q(GML, "Solid")), q(GML, "exterior")),
        q(GML, "CompositeSurface"),
    )
    for i, face in enumerate(faces):
        pid = _det_id(f"{seed}:lod1f{i}")
        etree.SubElement(comp, q(GML, "surfaceMember")).append(_make_polygon(face, pid))
    report.lod1_added += 1
    return solid_prop


def _build_lod0(typed, feature_seed, report):
    """Return a bldg:lod0FootPrint node, or None if not derivable."""
    ground = typed.get("GroundSurface", [])
    if not ground:
        report.lod0_skipped += 1
        return None
    hs = _heights(typed)
    z_base = hs[0] if hs else 0.0

    prop = etree.Element(q(BLDG, "lod0FootPrint"))
    ms = etree.SubElement(prop, q(GML, "MultiSurface"))
    for j, g in enumerate(ground):
        seed = gml_id(g) or f"{feature_seed}:g{j}"
        ring = _ring_points(g)
        flat = [(x, y, z_base) for (x, y, _) in ring]
        pid = _det_id(f"{seed}:lod0")
        etree.SubElement(ms, q(GML, "surfaceMember")).append(_make_polygon(flat, pid))
    report.lod0_added += 1
    return prop


def _strip_source_geometry(root) -> None:
    """Remove each feature's source-LOD geometry (whichever it was sourced
    from, LOD3 or LOD2) and any installations, leaving only the newly derived
    lower LODs. A file can mix features sourced from different levels, so this
    is decided per feature, not globally."""
    features = [e for e in root.iter() if e.tag in _FEATURE_TAGS]
    for feat in features:
        _, source_level = _find_source_solid(feat)
        props = _SOURCE_GEOMETRY_PROPS.get(source_level, set())
        multi_surface_tag = f"lod{source_level}MultiSurface" if source_level else None
        for child in list(feat):
            ln = localname(child)
            if ln in props or ln in _INSTALLATION_PROPS:
                feat.remove(child)
            elif (multi_surface_tag and ln == "boundedBy"
                  and child.find(f".//{q(BLDG, multi_surface_tag)}") is not None):
                feat.remove(child)


def _process_feature(feature, solid, source_level, poly_index, emitted_ids, report, levels,
                      lod1_height):
    groups, typed = _collect_shell(solid, poly_index, report)
    if not groups:
        return
    # Stable seed from source ids (never id(obj): lxml reuses proxy addresses).
    first_pid = next((gml_id(p) for polys in groups.values() for p in polys if gml_id(p)), None)
    feature_seed = gml_id(feature) or first_pid or gml_id(solid) or "feat"

    # Lower LODs are inserted before the source solid, low to high, so the
    # solids stay in ascending-LOD order (lod1Solid, lod2Solid, lod3Solid).
    prepend = []
    if 0 in levels:
        node = _build_lod0(typed, feature_seed, report)
        if node is not None:
            prepend.append(node)
    if 1 in levels:
        node = _build_lod1(typed, feature_seed, report, height=lod1_height)
        if node is not None:
            prepend.append(node)

    qc_rings = None
    if 2 in levels:
        if source_level == 3:
            lod2_nodes, qc_rings = _build_lod2(groups, emitted_ids, report, feature_seed)
            prepend.extend(lod2_nodes)
        else:
            # Already at LOD2 (or lower): nothing to derive, LOD2 needs LOD3
            # detail (window/door holes, installations) to remove.
            report.lod2_already_present += 1

    insert_at = list(feature).index(solid)
    for offset, node in enumerate(prepend):
        feature.insert(insert_at + offset, node)

    if qc_rings is not None:
        stats = shell_stats(qc_rings)
        report._record_quality(stats)
        if stats["closed"]:
            report.closed += 1
        else:
            report.open += 1
            report.open_feature_ids.append(gml_id(feature) or "(no id)")
    report.features += 1


# --- public API --------------------------------------------------------------

def enhance(input_path: str, output_path: str, *, levels=(2,), keep_source: bool = True,
            lod1_height: str = "average", limit: int | None = None) -> Report:
    """Derive lower LODs for every building/building part and write the result.

    Each feature is read from whichever detail level it has, LOD3 preferred,
    LOD2 as the fallback. LOD1/LOD0 extrusion works from either; LOD2
    derivation (window/door hole filling) only runs for features actually
    sourced from LOD3, see Report.source_lod3/source_lod2/source_none and
    Report.lod2_already_present.

    levels: which lower LODs to add, any subset of {0, 1, 2}. LOD3 can only
        ever be a source, never a derivation target (there is no detail to
        invent), so 3 is not a valid value here.
    keep_source: keep the feature's original source geometry (embed) or strip
        it, leaving only the newly derived lower LODs.
    lod1_height: 'average' (default, SIG3D Average Roof Height), 'eave'
        (Min. Eaves Height) or 'ridge' (Max. Ridge Height) for the LOD1 block
        top. See docs/DESIGN.md#lod1-extruded-block.
    """
    levels = tuple(sorted(set(levels)))
    invalid = [lv for lv in levels if lv not in (0, 1, 2)]
    if invalid:
        raise ValueError(
            f"unsupported --levels value(s) {invalid}: CitySmith only derives 0, 1 or 2. "
            "LOD3 can be a source (if present in the input) but is never a derivation target."
        )
    parser = etree.XMLParser(huge_tree=True, remove_blank_text=False)
    tree = etree.parse(input_path, parser)
    root = tree.getroot()

    report = Report(mode="embed" if keep_source else "lower-only", levels=levels)
    poly_index = _build_polygon_index(root)
    emitted_ids: set[str] = set()

    for feature in list(root.iter()):
        if feature.tag not in _FEATURE_TAGS:
            continue
        if limit is not None and report.features >= limit:
            break
        solid, source_level = _find_source_solid(feature)
        if solid is None:
            report.source_none += 1
            continue
        if source_level == 3:
            report.source_lod3 += 1
        else:
            report.source_lod2 += 1
        _process_feature(feature, solid, source_level, poly_index, emitted_ids, report, levels,
                          lod1_height)

    if not keep_source:
        _strip_source_geometry(root)

    tree.write(output_path, xml_declaration=True, encoding="utf-8")
    return report


def add_lod2(input_path: str, output_path: str, *, keep_source: bool = True,
             limit: int | None = None) -> Report:
    """Backwards-compatible helper: add only LOD2 (requires LOD3 source)."""
    return enhance(input_path, output_path, levels=(2,), keep_source=keep_source, limit=limit)
