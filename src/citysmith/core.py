"""Core CityGML LOD derivation engine.

Given CityGML 2.0 buildings modelled in LOD3 or LOD2, derive lower levels of
detail and embed them alongside the source (or emit a lower-LOD-only file).
See docs/DESIGN.md for the rationale.

Each feature (Building/BuildingPart) is read from whichever detail level it
actually has, LOD3 preferred, LOD2 as the fallback, in whichever of three
structurally different (all valid) CityGML encodings it was exported in, see
`_find_source_shell` for the full breakdown ('solid', 'surfaces' or
'unclassified'). `inspect()` runs this same detection read-only, without
writing anything, so a file's actual shape and what each capability can and
can't do with it are knowable before committing to a real run. LOD1/LOD0
extrusion only needs Ground/Roof surface heights, which both levels and both
usable patterns provide, so it works from any of them. LOD2 *derivation*
specifically (de-holing walls, copying roof/ground) only makes sense
starting from LOD3, since that's the only source with window/door holes and
installations to remove in the first place; a feature already at LOD2 has
nothing to derive there.

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
from .geometry import (_point_in_polygon_2d, cluster_coplanar_rings, extrude_prism,
                       parse_pos_list, polygon_area_2d, shell_stats,
                       union_coplanar_polygons)

# Fixed namespace so uuid5-derived ids are stable across runs and machines.
_ID_NAMESPACE = uuid.UUID("1b9d6bcd-0c6f-5a1e-9d0a-10b0c0ffee00")

# Smallest ground piece (in the CRS's squared units, i.e. m² for a projected
# CRS) that still earns its own LOD1 prism when a footprint comes in several
# disjoint pieces. Real data has buildings spanning a ground-level passage on
# pillars: the pillars show up as ground pieces of 0.5-8 m² which, extruded to
# the building's full height, would render as thin 20-26 m spikes rather than
# massing. Anything at or above this is kept, including genuine secondary
# wings. Only ever applied when a larger piece survives, so a building that
# really is one small structure is never left without geometry. See
# docs/DESIGN.md, LOD1 section.
LOD1_MIN_PIECE_AREA = 10.0

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
_GEOMETRY_BEARING_PROPS = {"lod0FootPrint", "lod1Solid", "lod2Solid", "boundedBy"}


@dataclass
class Report:
    """Outcome of an enhance run."""

    mode: str = "embed"
    levels: tuple = (2,)
    features: int = 0
    source_lod3: int = 0
    source_lod2: int = 0
    source_none: int = 0
    source_unclassified: int = 0
    source_pattern_solid: int = 0
    source_pattern_surfaces: int = 0
    lod2_already_present: int = 0
    lod2_cleaned: int = 0
    merged_surfaces: int = 0
    merged_panels: int = 0
    walls_deholed: int = 0
    interior_rings_removed: int = 0
    roof_copied: int = 0
    ground_copied: int = 0
    other_copied: int = 0
    lod1_added: int = 0
    lod1_skipped: int = 0
    lod1_composite: int = 0
    lod1_pieces_skipped: int = 0
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
    kept_empty_ids: list[str] = field(default_factory=list)

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


def _all_ring_points(polygon):
    """Every ring of a polygon (exterior first, then interiors) as point
    lists. The boundary-union merge needs interior (hole) rings too, so a
    real cut hole cancels/drops the same way a missing-panel gap does."""
    rings = []
    ext = _ring_points(polygon)
    if ext:
        rings.append(ext)
    for interior in polygon.findall(q(GML, "interior")):
        lr = interior.find(q(GML, "LinearRing"))
        if lr is None:
            continue
        pos = lr.find(q(GML, "posList"))
        pts = parse_pos_list(pos.text) if pos is not None and pos.text else []
        if pts:
            rings.append(pts)
    return rings


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


# --- shell collection --------------------------------------------------------

def _collect_shell(solid, poly_index, report):
    """Return (groups, typed) for an aggregating solid's shell.

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


def _surface_own_polygons(surf, level, poly_index):
    """Polygons directly inside one boundary surface's own lodX geometry
    (e.g. a WallSurface's own lod3MultiSurface), inline or by local xlink.
    Used for the 'surfaces' pattern, see _find_source_shell."""
    ms_prop = surf.find(q(BLDG, f"lod{level}MultiSurface"))
    if ms_prop is None:
        return []
    polys = []
    for sm in ms_prop.iter(q(GML, "surfaceMember")):
        target = href_target(sm)
        if target:
            poly = poly_index.get(target)
            if poly is not None:
                polys.append(poly)
        else:
            poly = sm.find(q(GML, "Polygon"))
            if poly is not None:
                polys.append(poly)
    return polys


def _find_source_shell(feature, poly_index, report):
    """Return (groups, typed, level, pattern, anchor) for a feature's most
    detailed usable geometry, or (None, None, None, None, None).

    Three structurally different patterns are read, not guessed from
    filenames or authoring-tool metadata, detected from the XML shape
    itself:
      'solid'         an aggregating lodXSolid ties the shell's polygons
                       together via xlink into one gml:Solid (the common
                       CityGRID/3DCityDB style).
      'surfaces'      no aggregating solid; each boundary surface under
                       boundedBy carries its own lodXMultiSurface directly,
                       inline or by local xlink (common from SketchUp-
                       modelled, FME-exported data, which doesn't always
                       construct a closed solid).
      'unclassified'  a lodXMultiSurface directly on the feature itself (not
                       inside any boundedBy thematic surface): a flat bag of
                       polygons with no wall/roof/ground distinction at all.
                       Detected but never processable: LOD1/LOD0 extrusion
                       needs to know which polygon is the ground, which is
                       exactly the information this pattern doesn't carry.
                       Returned with groups=None so callers can tell "found
                       geometry, structurally can't use it" apart from
                       "found nothing."
    LOD3 is preferred over LOD2; within a level, 'solid' beats 'surfaces'
    beats 'unclassified' if a feature somehow has more than one, since each
    is progressively less capable. `anchor` is the index, among the
    feature's direct children, before which newly derived lower-LOD nodes
    should be inserted (immediately before the solid, or immediately after
    the last boundedBy surface used, for the 'solid'/'surfaces' patterns
    respectively; None for 'unclassified', nothing gets inserted there),
    keeping LOD ordering schema-correct either way.
    """
    unclassified_level = None
    for level in (3, 2):
        solid = feature.find(q(BLDG, f"lod{level}Solid"))
        if solid is not None:
            groups, typed = _collect_shell(solid, poly_index, report)
            if groups:
                return groups, typed, level, "solid", list(feature).index(solid)

        groups: "OrderedDict" = OrderedDict()
        typed: dict = defaultdict(list)
        last_bounded_idx = None
        for idx, child in enumerate(feature):
            if localname(child) != "boundedBy":
                continue
            surf = next((c for c in child if localname(c) in BOUNDARY_LOCALNAMES), None)
            if surf is None:
                continue
            polys = _surface_own_polygons(surf, level, poly_index)
            if not polys:
                continue
            groups.setdefault(surf, []).extend(polys)
            typed[localname(surf)].extend(polys)
            last_bounded_idx = idx
        if groups:
            return groups, typed, level, "surfaces", last_bounded_idx + 1

        if unclassified_level is None:
            flat = feature.find(q(BLDG, f"lod{level}MultiSurface"))
            if flat is not None and flat.find(f".//{q(GML, 'Polygon')}") is not None:
                unclassified_level = level

    if unclassified_level is not None:
        return None, None, unclassified_level, "unclassified", None
    return None, None, None, None, None


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

        # A surface modelled as many small panels (missing panels, not cut
        # holes, are how window/door gaps show up on some real exports)
        # can't be de-holed one polygon at a time. But a group can also
        # legitimately bundle several genuinely different faces under one
        # thematic surface (this project's own box_lod3.gml fixture does
        # exactly that for its four walls), which must never be merged
        # together. Cluster by plane first, then only merge within a
        # cluster that actually shares one; a lone polygon in its own
        # cluster falls back to the plain de-hole copy path unchanged.
        ext_rings = [_ring_points(p) for p in polys]
        all_rings = [_all_ring_points(p) for p in polys]
        clusters = cluster_coplanar_rings(ext_rings) if len(polys) > 1 else [[0]]

        for c_idx, idxs in enumerate(clusters):
            cluster_polys = [polys[i] for i in idxs]
            merged_rings = (
                union_coplanar_polygons([r for i in idxs for r in all_rings[i]])
                if len(idxs) > 1 else None
            )
            if merged_rings:
                seed_c = f"{seed}:c{c_idx}"
                counted = False
                for m_idx, mring in enumerate(merged_rings):
                    new_pid = _det_id(f"{seed_c}:lod2poly:merged:{m_idx}")
                    solid_refs.append(new_pid)
                    qc_rings.append(mring)
                    if new_pid not in emitted_ids:
                        emitted_ids.add(new_pid)
                        etree.SubElement(ms, q(GML, "surfaceMember")).append(
                            _make_polygon(mring, new_pid)
                        )
                        counted = True
                    else:
                        ms.append(_surface_member_ref(new_pid))
                if counted:
                    report.merged_surfaces += 1
                    report.merged_panels += len(idxs)
                    if is_wall:
                        report.walls_deholed += 1
                    elif btype == "RoofSurface":
                        report.roof_copied += 1
                    elif btype == "GroundSurface":
                        report.ground_copied += 1
                    else:
                        report.other_copied += 1
                continue

            for p_idx, poly in zip(idxs, cluster_polys):
                orig_pid = gml_id(poly) or f"{feature_seed}:s{s_idx}p{p_idx}"
                new_pid = _det_id(f"{orig_pid}:lod2poly")
                solid_refs.append(new_pid)
                qc_rings.append(_ring_points(poly))
                if new_pid in emitted_ids:
                    ms.append(_surface_member_ref(new_pid))
                    continue
                emitted_ids.add(new_pid)
                # Fill holes on walls and roofs alike: a window/door/skylight
                # left as a cut interior ring is LOD3 detail, not part of an
                # LOD2 shell. Ground rings are kept (an interior ring there is
                # a real courtyard, not an opening).
                dehole = btype in ("WallSurface", "RoofSurface")
                copy, removed = _copy_polygon(poly, new_pid, dehole=dehole)
                etree.SubElement(ms, q(GML, "surfaceMember")).append(copy)
                if is_wall:
                    report.walls_deholed += 1
                    report.interior_rings_removed += removed
                elif btype == "RoofSurface":
                    report.roof_copied += 1
                    report.interior_rings_removed += removed
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


def _lod1_footprint_loops(ground):
    """Return the footprint outline(s) to extrude, as closed 3D rings.

    One `GroundSurface` polygon is used as-is, the overwhelmingly common case
    and the one that must stay byte-identical. Several are unioned by edge
    cancellation (`union_coplanar_polygons`, the same merge LOD2 uses on wall
    panels): pieces that adjoin collapse into one outline, pieces that don't
    come back as separate loops. Only exterior rings are fed in, since
    `extrude_prism` takes an exterior ring anyway, so a ground interior ring
    (a courtyard) is no more represented in LOD1 than it is today, and holes
    never interact with the union's even-odd nesting rules.

    If the union refuses (T-junction, or rings that aren't really coplanar),
    fall back to one loop per ground polygon rather than giving up: separate
    prisms are still a far better answer than no geometry at all.
    """
    if len(ground) == 1:
        ring = _ring_points(ground[0])
        return [ring] if len(ring) >= 3 else []
    rings = [r for r in (_ring_points(g) for g in ground) if len(r) >= 3]
    if not rings:
        return []
    return union_coplanar_polygons(rings) or rings


def _significant_loops(loops, report):
    """Drop footprint pieces too small to be real massing, but never the last
    of them. See LOD1_MIN_PIECE_AREA for why: sub-threshold pieces are pillars
    carrying a building over a ground-level passage, and extruding one to the
    building's full height produces a spike, not a block. If every piece is
    below the threshold the building genuinely is that small, so all are kept.
    """
    if len(loops) <= 1:
        return loops
    areas = [polygon_area_2d(lp) for lp in loops]
    if max(areas) < LOD1_MIN_PIECE_AREA:
        return loops
    kept = [lp for lp, a in zip(loops, areas) if a >= LOD1_MIN_PIECE_AREA]
    report.lod1_pieces_skipped += len(loops) - len(kept)
    return kept


def _piece_roof_heights(loop, typed, whole):
    """(z_eave, z_ridge) from just the roof surfaces standing over one
    footprint piece, falling back to the whole building's values when no roof
    polygon's centroid lands inside it (a small piece, or a roof that overhangs
    clear of the ground it covers). Wings of one building really do differ:
    on the Hamburg sample one feature's second wing averages 34.9 m against
    37.0 m for its main mass."""
    poly2d = [(p[0], p[1]) for p in (loop[:-1] if loop[0] == loop[-1] else loop)]
    pts = []
    for roof in typed.get("RoofSurface", []):
        rpts = _ring_points(roof)
        if len(rpts) < 3:
            continue
        body = rpts[:-1] if rpts[0] == rpts[-1] else rpts
        cx = sum(p[0] for p in body) / len(body)
        cy = sum(p[1] for p in body) / len(body)
        if _point_in_polygon_2d((cx, cy), poly2d):
            pts.extend(rpts)
    if not pts:
        return whole[1], whole[2]
    zs = [z for _, _, z in pts]
    return min(zs), max(zs)


def _lod1_solid_node(faces, id_seed):
    """One gml:Solid prism, faces deterministically identified from id_seed."""
    solid = etree.Element(q(GML, "Solid"))
    comp = etree.SubElement(
        etree.SubElement(solid, q(GML, "exterior")), q(GML, "CompositeSurface")
    )
    for i, face in enumerate(faces):
        etree.SubElement(comp, q(GML, "surfaceMember")).append(
            _make_polygon(face, _det_id(f"{id_seed}f{i}"))
        )
    return solid


def _build_lod1(typed, feature_seed, report, height="average"):
    """Return a bldg:lod1Solid node, or None if not derivable.

    `height` selects the SIG3D-named top of the block:
      'eave'    Min. Eaves Height (the conservative, spec-literal LOD1 value)
      'ridge'   Max. Ridge Height (the tallest possible guess)
      'average' Average Roof Height = (Min. Eaves + Max. Ridge) / 2 (default,
                the spec's own formula for approximating the real roof volume)

    Normally one prism. A building whose ground plane comes in several
    disjoint pieces (it spans a passage at ground level, so only the footprint
    is split while the volume above is continuous) gets one prism per piece
    inside a `gml:CompositeSolid`, which `bldg:lod1Solid` accepts since
    `gml:CompositeSolid` substitutes for `gml:_Solid`. See docs/DESIGN.md.
    """
    ground = typed.get("GroundSurface", [])
    if not ground:
        report.lod1_skipped += 1
        return None
    hs = _heights(typed)
    if hs is None:
        report.lod1_skipped += 1
        return None
    z_base = hs[0]

    loops = _lod1_footprint_loops(ground)
    single = len(loops) == 1
    loops = _significant_loops(loops, report)
    if not loops:
        report.lod1_skipped += 1
        return None

    seed = gml_id(ground[0]) or f"{feature_seed}:g0"
    prisms = []
    for p_idx, loop in enumerate(loops):
        z_eave, z_ridge = (hs[1], hs[2]) if single else _piece_roof_heights(loop, typed, hs)
        if height == "ridge":
            z_top = z_ridge
        elif height == "eave":
            z_top = z_eave
        else:
            z_top = (z_eave + z_ridge) / 2
        if z_top <= z_base:
            z_top = z_ridge
        if z_top <= z_base:
            continue
        faces = extrude_prism([(x, y) for (x, y, _) in loop], z_base, z_top)
        # The one-piece seed is kept exactly as it always was, so output for
        # the ordinary single-GroundSurface building is unchanged.
        prisms.append((f"{seed}:lod1" if single else f"{seed}:lod1p{p_idx}", faces))
    if not prisms:
        report.lod1_skipped += 1
        return None

    solid_prop = etree.Element(q(BLDG, "lod1Solid"))
    if len(prisms) == 1:
        solid_prop.append(_lod1_solid_node(prisms[0][1], prisms[0][0]))
    else:
        composite = etree.SubElement(solid_prop, q(GML, "CompositeSolid"))
        composite.set(q(GML, "id"), _det_id(f"{seed}:lod1cs"))
        for id_seed, faces in prisms:
            etree.SubElement(composite, q(GML, "solidMember")).append(
                _lod1_solid_node(faces, id_seed)
            )
        report.lod1_composite += 1
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


def _strip_source_geometry(root, poly_index, levels, new_nodes, *, keep_source: bool) -> None:
    """Remove source content that a rebuild has made obsolete, leaving only
    the newly derived lower LODs (plus, if `keep_source`, whatever original
    content wasn't rebuilt). A file can mix features sourced from different
    levels or patterns, so this is decided per feature, not globally.

    `new_nodes` (the exact elements `_process_feature` inserted this run,
    tracked by object identity, not by tag) is checked first and always
    wins: nothing built in this run is ever stripped, full stop. This
    matters because LOD3 and LOD2 tag *names* alone can't tell freshly
    built output apart from original source content of the same tier, e.g.
    a feature whose native LOD2 was panelized and got rebuilt (see
    `_needs_lod2_build`) has both the old panelized boundedBy elements and
    the new merged ones present at the same time, both tagged
    lod2MultiSurface identically.

    LOD2 is stripped whenever it was rebuilt (`_needs_lod2_build` true for
    an LOD2 source), *regardless of `keep_source`*: the old panelized LOD2
    and the freshly merged one are two versions of the same requested level,
    not "source vs. derived output", so `keep_source` (which means "keep the
    *other*, untouched levels alongside what was derived") does not apply to
    it. Leaving both would silently double the feature's LOD2 geometry (seen
    on real data: an LOD2 source that needed a second merge pass on
    re-derivation left 4 lod2Solid elements instead of 2 in embed mode,
    before this was caught and fixed). LOD2 is left alone only if the
    feature's own source already was LOD2 and needed no rebuild (single
    polygon per surface, `2 in levels`): that LOD2 *is* the requested
    output, not source debris, and was never added to `new_nodes` since
    nothing was built for it.

    Everything else (LOD3, and installations) is only stripped when
    `keep_source` is False (lower-only mode): LOD3 can carry stray content
    alongside an LOD2 shell (confirmed on real data: window openings
    modelled as separate `bldg:opening`/Window features with their own
    lod3MultiSurface, nested inside boundary surfaces that otherwise carry
    the feature's real lod2MultiSurface shell), but in embed mode that's
    exactly the original detail embed mode exists to keep.
    """
    features = [e for e in root.iter() if e.tag in _FEATURE_TAGS]
    scratch = Report()  # re-detecting the source here must not double-count the real report
    for feat in features:
        groups, _, source_level, _, _ = _find_source_shell(feat, poly_index, scratch)
        rebuilt_lod2 = (source_level == 2 and 2 in levels
                        and _needs_lod2_build(source_level, groups or {}))
        keep_lod2 = keep_source and not rebuilt_lod2
        strip_props = set() if keep_source else set(_LOD3_GEOMETRY_PROPS)
        if not keep_lod2:
            strip_props |= _LOD2_GEOMETRY_PROPS
        for child in list(feat):
            if child in new_nodes:
                continue
            ln = localname(child)
            if ln in strip_props or (not keep_source and ln in _INSTALLATION_PROPS):
                feat.remove(child)
                continue
            if ln != "boundedBy":
                continue
            has_lod3 = not keep_source and child.find(f".//{q(BLDG, 'lod3MultiSurface')}") is not None
            has_lod2 = not keep_lod2 and child.find(f".//{q(BLDG, 'lod2MultiSurface')}") is not None
            if has_lod3 or has_lod2:
                feat.remove(child)


def _flag_empty_features(root, report) -> None:
    """Record, but never remove, any Building/BuildingPart left with no
    geometry-bearing content after stripping (e.g. a BuildingPart with no
    GroundSurface, so LOD1/LOD0 couldn't be derived for it).

    These used to be deleted outright from lower-only output. Silently
    dropping a feature makes a run lose buildings with no trace of which ones
    or why, which is the opposite of this project's "report, don't force"
    stance everywhere else: an id in the summary is actionable, a missing
    building is not. The attributes, appearances and generic properties of
    such a feature are still meaningful, and any downstream consumer can
    filter on the absence of geometry itself.
    """
    for feat in root.iter():
        if feat.tag not in _FEATURE_TAGS:
            continue
        if any(localname(c) in _GEOMETRY_BEARING_PROPS or
               localname(c) == "consistsOfBuildingPart" for c in feat):
            continue
        report.kept_empty_ids.append(gml_id(feat) or "(no id)")


def _needs_lod2_build(source_level, groups) -> bool:
    """True if LOD2 needs to be (re)built rather than left exactly as found:
    always for an LOD3 source (there is no LOD2 yet), and also for an
    already-LOD2 source where at least one thematic surface's several
    polygons can actually be merged into a clean outer-boundary surface
    (missing panels, not cut holes, are how window gaps show up in some
    real-world exports). A surface with several polygons that _aren't_
    genuinely coplanar (e.g. several distinct wall faces bundled under one
    thematic WallSurface, a legitimate, common CityGRID convention) has
    nothing safe to merge, `union_coplanar_polygons` would correctly refuse,
    so this checks the real thing rather than just counting polygons, to
    stay in sync with what `_build_lod2` will actually do.
    """
    if source_level == 3:
        return True
    for polys in groups.values():
        if len(polys) < 2:
            continue
        rings = [_ring_points(p) for p in polys]
        clusters = cluster_coplanar_rings(rings)
        if any(len(idxs) > 1 for idxs in clusters):
            return True
    return False


def _process_feature(feature, groups, typed, source_level, anchor, emitted_ids, report, levels,
                      lod1_height, new_nodes_out):
    # Stable seed from source ids (never id(obj): lxml reuses proxy addresses).
    first_pid = next((gml_id(p) for polys in groups.values() for p in polys if gml_id(p)), None)
    feature_seed = gml_id(feature) or first_pid or "feat"

    # Lower LODs are inserted at the anchor (before the source solid, or
    # after the last boundedBy surface used, for the 'solid'/'surfaces'
    # patterns respectively), low to high, so LOD ordering stays ascending.
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
        if _needs_lod2_build(source_level, groups):
            lod2_nodes, qc_rings = _build_lod2(groups, emitted_ids, report, feature_seed)
            prepend.extend(lod2_nodes)
            if source_level == 2:
                report.lod2_cleaned += 1
        else:
            # Already at LOD2, single polygon per surface: nothing to do.
            report.lod2_already_present += 1

    for offset, node in enumerate(prepend):
        feature.insert(anchor + offset, node)
    new_nodes_out.update(prepend)

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
    new_nodes: set = set()  # direct children freshly inserted this run, by object identity

    for feature in list(root.iter()):
        if feature.tag not in _FEATURE_TAGS:
            continue
        if limit is not None and report.features >= limit:
            break
        groups, typed, source_level, pattern, anchor = _find_source_shell(feature, poly_index,
                                                                            report)
        if source_level is None:
            report.source_none += 1
            continue
        if pattern == "unclassified":
            report.source_unclassified += 1
            continue
        if source_level == 3:
            report.source_lod3 += 1
        else:
            report.source_lod2 += 1
        if pattern == "solid":
            report.source_pattern_solid += 1
        else:
            report.source_pattern_surfaces += 1
        _process_feature(feature, groups, typed, source_level, anchor, emitted_ids, report,
                          levels, lod1_height, new_nodes)

    _strip_source_geometry(root, poly_index, levels, new_nodes, keep_source=keep_source)
    if not keep_source:
        _flag_empty_features(root, report)

    tree.write(output_path, xml_declaration=True, encoding="utf-8")
    return report


def add_lod2(input_path: str, output_path: str, *, keep_source: bool = True,
             limit: int | None = None) -> Report:
    """Backwards-compatible helper: add only LOD2 (requires LOD3 source)."""
    return enhance(input_path, output_path, levels=(2,), keep_source=keep_source, limit=limit)


@dataclass
class InspectReport:
    """Read-only preflight: what CitySmith found in a file and what each
    capability can do with it, without writing anything. See `inspect()`."""

    features: int = 0
    source_lod3: int = 0
    source_lod2: int = 0
    source_unclassified: int = 0
    source_none: int = 0
    pattern_solid: int = 0
    pattern_surfaces: int = 0
    installations: int = 0
    feature_detail: list[dict] = field(default_factory=list)

    def to_dict(self) -> dict:
        return self.__dict__.copy()


def inspect(input_path: str, *, limit: int | None = None) -> InspectReport:
    """Detect, per feature, what geometry pattern is present and how
    detailed each one is, without deriving or writing anything. Intended to
    be run before a real `lod`/`semantics` call on unfamiliar data, so its
    shape (and what CitySmith can and can't do with it, and why) is known
    upfront rather than discovered from a silent zero-progress run."""
    parser = etree.XMLParser(huge_tree=True, remove_blank_text=False)
    root = etree.parse(input_path, parser).getroot()
    poly_index = _build_polygon_index(root)
    report = InspectReport()

    for feature in root.iter():
        if feature.tag not in _FEATURE_TAGS:
            continue
        if limit is not None and report.features >= limit:
            break
        report.features += 1
        n_inst = sum(1 for _ in feature.iter(q(BLDG, "BuildingInstallation")))
        report.installations += n_inst

        groups, typed, level, pattern, _ = _find_source_shell(feature, poly_index, Report())
        detail = {"id": gml_id(feature) or "(no id)", "level": level, "pattern": pattern,
                   "installations": n_inst}
        if pattern in ("solid", "surfaces"):
            detail["surfaces"] = {k: len(v) for k, v in typed.items()}
            detail["has_ground"] = "GroundSurface" in typed
            detail["has_roof"] = "RoofSurface" in typed
            if level == 3:
                report.source_lod3 += 1
            else:
                report.source_lod2 += 1
            if pattern == "solid":
                report.pattern_solid += 1
            else:
                report.pattern_surfaces += 1
        elif pattern == "unclassified":
            report.source_unclassified += 1
        else:
            report.source_none += 1
        report.feature_detail.append(detail)

    return report
