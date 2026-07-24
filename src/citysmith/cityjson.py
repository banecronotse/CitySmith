"""Native CityJSON 1.1 writer for CityGML buildings.

Exports bldg:Building and bldg:BuildingPart features, with every LOD geometry
they carry (lod0FootPrint, lod1/2/3 Solid), shared and quantised vertices,
thematic surface semantics, generic attributes and parent/child links.

No external dependency (no citygml-tools). Building installations are not yet
exported as separate CityObjects; their geometry is omitted from the CityJSON.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field

from lxml import etree

from .citygml import BLDG, GEN, GML, NS, gml_id, localname, q
from .geometry import parse_pos_list

_FEATURE_TAGS = {q(BLDG, "Building"), q(BLDG, "BuildingPart")}
_SEMANTIC_TYPES = {
    "RoofSurface", "WallSurface", "GroundSurface",
    "OuterFloorSurface", "OuterCeilingSurface", "ClosureSurface",
}


@dataclass
class ConvertReport:
    city_objects: int = 0
    buildings: int = 0
    parts: int = 0
    geometries: int = 0
    vertices: int = 0
    lods: dict = field(default_factory=dict)

    def to_dict(self) -> dict:
        return self.__dict__.copy()


class _VertexPool:
    """Deduplicates vertices; quantises to integers on export."""

    def __init__(self, precision: int = 3):
        self.precision = precision
        self._map: dict = {}
        self.points: list = []

    def index(self, p) -> int:
        key = (round(p[0], self.precision), round(p[1], self.precision), round(p[2], self.precision))
        i = self._map.get(key)
        if i is None:
            i = len(self.points)
            self._map[key] = i
            self.points.append(key)
        return i

    def transform_and_vertices(self):
        if not self.points:
            return {"scale": [0.001, 0.001, 0.001], "translate": [0, 0, 0]}, []
        scale = 10 ** (-self.precision)
        tx = min(p[0] for p in self.points)
        ty = min(p[1] for p in self.points)
        tz = min(p[2] for p in self.points)
        verts = [[int(round((x - tx) / scale)), int(round((y - ty) / scale)),
                  int(round((z - tz) / scale))] for (x, y, z) in self.points]
        return {"scale": [scale, scale, scale], "translate": [tx, ty, tz]}, verts

    def extent(self):
        xs = [p[0] for p in self.points]
        ys = [p[1] for p in self.points]
        zs = [p[2] for p in self.points]
        return [min(xs), min(ys), min(zs), max(xs), max(ys), max(zs)] if self.points else None


def _exterior_ring(polygon):
    ext = polygon.find(q(GML, "exterior"))
    return ext.find(q(GML, "LinearRing")) if ext is not None else None


def _ring_indices(linear_ring, pool):
    pos = linear_ring.find(q(GML, "posList"))
    pts = parse_pos_list(pos.text) if pos is not None and pos.text else []
    if len(pts) > 1 and pts[0] == pts[-1]:
        pts = pts[:-1]  # CityJSON rings are implicitly closed
    return [pool.index(p) for p in pts]


def _polygon_surface(polygon, pool):
    """A CityJSON surface: [exterior_ring, interior_ring, ...] of vertex ids."""
    rings = []
    ext = _exterior_ring(polygon)
    if ext is not None:
        rings.append(_ring_indices(ext, pool))
    for interior in polygon.findall(q(GML, "interior")):
        lr = interior.find(q(GML, "LinearRing"))
        if lr is not None:
            rings.append(_ring_indices(lr, pool))
    return rings


def _boundary_type(polygon):
    for anc in polygon.iterancestors():
        ln = localname(anc)
        if ln in _SEMANTIC_TYPES:
            return ln
    return None


def _resolve_members(composite, poly_index):
    """Yield the polygons of a CompositeSurface (inline or via xlink)."""
    for sm in composite.findall(q(GML, "surfaceMember")):
        href = sm.get(q(NS["xlink"], "href"))
        if href:
            poly = poly_index.get(href.lstrip("#"))
            if poly is not None:
                yield poly
        else:
            poly = sm.find(q(GML, "Polygon"))
            if poly is not None:
                yield poly


def _lod1_semantic(index, count):
    """Position-based semantics for a generated LOD1 prism."""
    if index == 0:
        return "GroundSurface"
    if index == 1:
        return "RoofSurface"
    return "WallSurface"


def _solid_geometry(prop_el, lod, pool, poly_index):
    solid = prop_el.find(f".//{q(GML, 'Solid')}")
    if solid is None:
        return None
    comp = solid.find(f".//{q(GML, 'CompositeSurface')}")
    if comp is None:
        return None
    members = list(_resolve_members(comp, poly_index))
    if not members:
        return None

    shell = []
    surfaces = []
    values = []
    sem_index: dict = {}
    for i, poly in enumerate(members):
        shell.append(_polygon_surface(poly, pool))
        stype = _boundary_type(poly) or (_lod1_semantic(i, len(members)) if lod == "1" else None)
        if stype:
            if stype not in sem_index:
                sem_index[stype] = len(surfaces)
                surfaces.append({"type": stype})
            values.append(sem_index[stype])
        else:
            values.append(None)

    geom = {"type": "Solid", "lod": lod, "boundaries": [shell]}
    if any(v is not None for v in values):
        geom["semantics"] = {"surfaces": surfaces, "values": [values]}
    return geom


def _multisurface_geometry(prop_el, lod, pool, poly_index):
    ms = prop_el.find(f".//{q(GML, 'MultiSurface')}")
    if ms is None:
        return None
    boundaries = []
    for sm in ms.findall(q(GML, "surfaceMember")):
        poly = sm.find(q(GML, "Polygon"))
        if poly is None:
            href = sm.get(q(NS["xlink"], "href"))
            poly = poly_index.get(href.lstrip("#")) if href else None
        if poly is not None:
            boundaries.append(_polygon_surface(poly, pool))
    if not boundaries:
        return None
    return {"type": "MultiSurface", "lod": lod, "boundaries": boundaries}


def _lod_of(child) -> str | None:
    ln = localname(child)
    if not ln.startswith("lod") or len(ln) < 4 or not ln[3].isdigit():
        return None
    return ln[3]


def _attributes(feature) -> dict:
    attrs = {}
    for a in feature:
        ln = localname(a)
        if ln == "stringAttribute":
            v = a.find(q(GEN, "value"))
            if a.get("name") and v is not None:
                attrs[a.get("name")] = v.text
        elif ln in ("doubleAttribute", "intAttribute"):
            v = a.find(q(GEN, "value"))
            if a.get("name") and v is not None and v.text:
                attrs[a.get("name")] = float(v.text) if ln == "doubleAttribute" else int(v.text)
        elif ln == "function":
            attrs["function"] = a.text
    return attrs


def _geometries(feature, pool, poly_index, report):
    geoms = []
    for child in feature:
        lod = _lod_of(child)
        if lod is None:
            continue
        ln = localname(child)
        if ln.endswith("Solid"):
            g = _solid_geometry(child, lod, pool, poly_index)
        elif ln.endswith(("FootPrint", "MultiSurface", "RoofEdge")):
            g = _multisurface_geometry(child, lod, pool, poly_index)
        else:
            g = None  # TerrainIntersection, MultiCurve, aggregate Geometry
        if g is not None:
            geoms.append(g)
            report.lods[lod] = report.lods.get(lod, 0) + 1
    return geoms


def convert(input_path: str, output_path: str, *, precision: int = 3) -> ConvertReport:
    """Convert a CityGML 2.0 file to CityJSON 1.1."""
    parser = etree.XMLParser(huge_tree=True, remove_blank_text=False)
    root = etree.parse(input_path, parser).getroot()

    poly_index = {p.get(q(GML, "id")): p for p in root.iter(q(GML, "Polygon"))
                  if p.get(q(GML, "id"))}
    pool = _VertexPool(precision=precision)
    report = ConvertReport()
    city_objects: dict = {}

    # Map each part to its parent building id.
    part_parent: dict = {}
    for building in root.iter(q(BLDG, "Building")):
        bid = gml_id(building)
        for part in building.iter(q(BLDG, "BuildingPart")):
            part_parent[part] = bid

    for feature in root.iter():
        if feature.tag not in _FEATURE_TAGS:
            continue
        is_part = feature.tag == q(BLDG, "BuildingPart")
        fid = gml_id(feature) or f"co_{len(city_objects)}"
        geoms = _geometries(feature, pool, poly_index, report)

        co = {"type": "BuildingPart" if is_part else "Building"}
        attrs = _attributes(feature)
        if attrs:
            co["attributes"] = attrs
        if geoms:
            co["geometry"] = geoms
            report.geometries += len(geoms)
        if is_part and feature in part_parent and part_parent[feature]:
            co["parents"] = [part_parent[feature]]
        else:
            children = [gml_id(p) for p in feature.findall(q(BLDG, "consistsOfBuildingPart") +
                        "/" + q(BLDG, "BuildingPart")) if gml_id(p)]
            if children:
                co["children"] = children

        city_objects[fid] = co
        report.city_objects += 1
        report.parts += is_part
        report.buildings += not is_part

    transform, vertices = pool.transform_and_vertices()
    report.vertices = len(vertices)

    doc = {
        "type": "CityJSON",
        "version": "1.1",
        "transform": transform,
        "CityObjects": city_objects,
        "vertices": vertices,
    }
    extent = pool.extent()
    srs = root.find(f".//{q(GML, 'Envelope')}")
    metadata = {}
    if extent:
        metadata["geographicalExtent"] = extent
    if srs is not None and srs.get("srsName"):
        code = srs.get("srsName").split(":")[-1]
        metadata["referenceSystem"] = f"https://www.opengis.net/def/crs/EPSG/0/{code}"
    if metadata:
        doc["metadata"] = metadata

    with open(output_path, "w", encoding="utf-8") as fh:
        json.dump(doc, fh)
    return report
