"""Core CityGML LOD derivation engine.

Given CityGML 2.0 buildings modelled in LOD3, derive lower levels of detail and
embed them alongside the LOD3 (or emit a lower-LOD-only file). See
docs/DESIGN.md for the rationale.

LOD2  faithful shell: roof and ground copied, walls copied with window/door
      holes filled, structure mirrored from LOD3. Watertight only if the source
      shell is; watertightness is reported, not forced.
LOD1  extruded block: the footprint (ground surface) raised from base to eave
      height. Watertight by construction.
LOD0  footprint: the ground surface flattened to base height.
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
_INSTALLATION_PROPS = {"outerBuildingInstallation", "interiorBuildingInstallation"}
_FEATURE_TAGS = {q(BLDG, "Building"), q(BLDG, "BuildingPart")}


@dataclass
class Report:
    """Outcome of an enhance run."""

    mode: str = "embed"
    levels: tuple = (2,)
    features: int = 0
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


def _shell_polygon_ids(lod3solid) -> list[str]:
    ids = []
    for sm in lod3solid.iter(q(GML, "surfaceMember")):
        target = href_target(sm)
        if target:
            ids.append(target)
    return ids


# --- shell collection --------------------------------------------------------

def _collect_shell(lod3solid, poly_index, report):
    """Return (groups, typed) for a solid's shell.

    groups: OrderedDict(boundary-surface element -> [polygon]) for structure
            mirroring. typed: dict(thematic type -> [polygon]).
    """
    groups: "OrderedDict" = OrderedDict()
    typed: dict = defaultdict(list)
    for pid in _shell_polygon_ids(lod3solid):
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
    """Return (z_base, z_eave, z_ridge) from a shell's polygons, or None."""
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


def _build_lod1(typed, feature_seed, report, height="eave"):
    """Return a bldg:lod1Solid prism node, or None if not derivable."""
    ground = typed.get("GroundSurface", [])
    if len(ground) != 1:
        report.lod1_skipped += 1
        return None
    hs = _heights(typed)
    if hs is None:
        report.lod1_skipped += 1
        return None
    z_base, z_eave, z_ridge = hs
    z_top = z_ridge if height == "ridge" else z_eave
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


def _strip_lod3(root) -> None:
    features = [e for e in root.iter() if e.tag in _FEATURE_TAGS]
    for feat in features:
        for child in list(feat):
            ln = localname(child)
            if ln in _LOD3_GEOMETRY_PROPS or ln in _INSTALLATION_PROPS:
                feat.remove(child)
            elif ln == "boundedBy" and child.find(f".//{q(BLDG, 'lod3MultiSurface')}") is not None:
                feat.remove(child)


def _process_feature(feature, lod3solid, poly_index, emitted_ids, report, levels, lod1_height):
    groups, typed = _collect_shell(lod3solid, poly_index, report)
    if not groups:
        return
    # Stable seed from source ids (never id(obj): lxml reuses proxy addresses).
    first_pid = next((gml_id(p) for polys in groups.values() for p in polys if gml_id(p)), None)
    feature_seed = gml_id(feature) or first_pid or gml_id(lod3solid) or "feat"

    # Lower LODs are inserted before the lod3Solid. Order low to high so the
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
        lod2_nodes, qc_rings = _build_lod2(groups, emitted_ids, report, feature_seed)
        prepend.extend(lod2_nodes)

    insert_at = list(feature).index(lod3solid)
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

def enhance(input_path: str, output_path: str, *, levels=(2,), keep_lod3: bool = True,
            lod1_height: str = "eave", limit: int | None = None) -> Report:
    """Derive lower LODs for every LOD3 solid and write the result.

    levels: which lower LODs to add, any subset of {0, 1, 2}.
    keep_lod3: keep LOD3 (embed) or strip it, leaving lower LODs only.
    lod1_height: 'eave' (default) or 'ridge' for the LOD1 block top.
    """
    levels = tuple(sorted(set(levels)))
    parser = etree.XMLParser(huge_tree=True, remove_blank_text=False)
    tree = etree.parse(input_path, parser)
    root = tree.getroot()

    report = Report(mode="embed" if keep_lod3 else "lower-only", levels=levels)
    poly_index = _build_polygon_index(root)
    emitted_ids: set[str] = set()

    for solid in list(root.iter(q(BLDG, "lod3Solid"))):
        if limit is not None and report.features >= limit:
            break
        feature = solid.getparent()
        if feature is not None:
            _process_feature(feature, solid, poly_index, emitted_ids, report, levels, lod1_height)

    if not keep_lod3:
        _strip_lod3(root)

    tree.write(output_path, xml_declaration=True, encoding="utf-8")
    return report


def add_lod2(input_path: str, output_path: str, *, keep_lod3: bool = True,
             limit: int | None = None) -> Report:
    """Backwards-compatible helper: add only LOD2."""
    return enhance(input_path, output_path, levels=(2,), keep_lod3=keep_lod3, limit=limit)
