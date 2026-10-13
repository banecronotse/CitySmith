"""CityGML 2.0 namespaces and small XML helpers.

Kept deliberately free of transformation logic so it can be reused by the core
engine, the geometry utilities and the tests.
"""

from __future__ import annotations

from lxml import etree

# CityGML 2.0 namespace URIs.
NS = {
    "core": "http://www.opengis.net/citygml/2.0",
    "bldg": "http://www.opengis.net/citygml/building/2.0",
    "gml": "http://www.opengis.net/gml",
    "gen": "http://www.opengis.net/citygml/generics/2.0",
    "xlink": "http://www.w3.org/1999/xlink",
}

GML = NS["gml"]
BLDG = NS["bldg"]
XLINK = NS["xlink"]
GEN = NS["gen"]

# Boundary-surface local names that may take part in a solid shell.
BOUNDARY_LOCALNAMES = frozenset({
    "RoofSurface", "WallSurface", "GroundSurface",
    "OuterFloorSurface", "OuterCeilingSurface", "ClosureSurface",
})

# Feature/surface/opening local names that must carry a gml:id per the
# SIG3D Modeling Guide for 3D Objects, Part 2 (gml:id is mandatory from GML
# 3.2 onwards for every feature; the guide only spells that out explicitly
# under Building and BuildingInstallation, but it applies to every one of
# these via the shared gml:AbstractFeature base type). Shared between
# core.py's inspect() and semantics.py's enhance_semantics() so the two
# can't drift apart on what "required" means.
ID_REQUIRED_LOCALNAMES = frozenset({
    "Building", "BuildingPart", "BuildingInstallation",
    *BOUNDARY_LOCALNAMES,
    "Window", "Door",
})


def q(ns_uri: str, tag: str) -> str:
    """Build a Clark-notation qualified name, e.g. q(GML, 'Polygon')."""
    return f"{{{ns_uri}}}{tag}"


def localname(elem) -> str:
    """Local name of an element, namespace stripped.

    Returns "" for comment and processing-instruction nodes, whose tag is a
    callable rather than a string.
    """
    tag = elem.tag
    if not isinstance(tag, str):
        return ""
    return etree.QName(tag).localname


def gml_id(elem):
    """Value of the gml:id attribute, or None."""
    return elem.get(q(GML, "id"))


def href_target(surface_member) -> str | None:
    """The id a gml:surfaceMember points at via xlink:href, without the '#'."""
    href = surface_member.get(q(XLINK, "href"))
    return href.lstrip("#") if href else None


# The order the CityGML 2.0 building schema (building.xsd) requires for the
# bldg: properties of each feature type, copied from its xs:sequence
# definitions. Inserting at a "looks right" position is not enough: an
# lod2Solid placed after the boundary surfaces, or a function after the
# aggregate geometry, is invalid XML that some readers reject.
_ABSTRACT_BUILDING_ORDER = (
    "class", "function", "usage", "yearOfConstruction", "yearOfDemolition", "roofType",
    "measuredHeight", "storeysAboveGround", "storeysBelowGround", "storeyHeightsAboveGround",
    "storeyHeightsBelowGround", "lod0FootPrint", "lod0RoofEdge", "lod1Solid",
    "lod1MultiSurface", "lod1TerrainIntersection", "lod2Solid", "lod2MultiSurface",
    "lod2MultiCurve", "lod2TerrainIntersection", "outerBuildingInstallation",
    "interiorBuildingInstallation", "boundedBy", "lod3Solid", "lod3MultiSurface",
    "lod3MultiCurve", "lod3TerrainIntersection", "lod4Solid", "lod4MultiSurface",
    "lod4MultiCurve", "lod4TerrainIntersection", "interiorRoom", "consistsOfBuildingPart",
    "address",
)
_INSTALLATION_ORDER = (
    "class", "function", "usage", "lod2Geometry", "lod3Geometry", "lod4Geometry",
    "lod2ImplicitRepresentation", "lod3ImplicitRepresentation", "lod4ImplicitRepresentation",
    "boundedBy",
)
_BOUNDARY_ORDER = ("lod2MultiSurface", "lod3MultiSurface", "lod4MultiSurface", "opening")
_PROPERTY_ORDER = {
    "Building": _ABSTRACT_BUILDING_ORDER,
    "BuildingPart": _ABSTRACT_BUILDING_ORDER,
    "BuildingInstallation": _INSTALLATION_ORDER,
    **{name: _BOUNDARY_ORDER for name in BOUNDARY_LOCALNAMES},
}


def sort_building_properties(root) -> int:
    """Put the bldg: properties of every Building, BuildingPart,
    BuildingInstallation and boundary surface into schema order. Returns the
    number of features whose order changed.

    Only the known bldg: properties move, and only among the positions they
    already occupy, so gml:, core:, generic and extension (ADE) elements stay
    exactly where they are. The sort is stable (several boundedBy keep their
    sequence), and each position keeps its own whitespace tail, so the file's
    indentation survives. A file already in schema order is left untouched.
    """
    changed = 0
    for elem in root.iter():
        if not isinstance(elem.tag, str) or etree.QName(elem).namespace != BLDG:
            continue
        order = _PROPERTY_ORDER.get(localname(elem))
        if order is None:
            continue
        rank = {name: i for i, name in enumerate(order)}
        children = list(elem)
        slots = [i for i, c in enumerate(children)
                 if isinstance(c.tag, str) and etree.QName(c).namespace == BLDG
                 and localname(c) in rank]
        current = [children[i] for i in slots]
        wanted = sorted(current, key=lambda c: rank[localname(c)])
        if wanted == current:
            continue
        tails = [c.tail for c in children]
        for slot, child in zip(slots, wanted):
            children[slot] = child
        elem[:] = children
        for child, tail in zip(elem, tails):
            child.tail = tail
        changed += 1
    return changed
