"""CityGML 2.0 namespaces and small XML helpers.

Kept deliberately free of transformation logic so it can be reused by the core
engine, the geometry utilities and the tests.
"""

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
