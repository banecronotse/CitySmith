"""Extract a named subset of buildings out of a larger CityGML file.

The common need this serves: a city-wide export has hundreds of buildings,
and only a handful (a test area, a project site) are wanted. `crop()` keeps
exactly the requested `gml:id`s (and, for a `BuildingPart` id, its whole
parent `Building`, since a lone part is not independently meaningful
CityGML), drops everything else, and recomputes the file's `gml:Envelope`
so it describes the kept subset rather than the original extent.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from lxml import etree

from .citygml import GML, gml_id, localname, q
from .core import _FEATURE_TAGS
from .geometry import parse_pos_list

_APP_NS = "{http://www.opengis.net/citygml/appearance/2.0}"

_CITY_OBJECT_MEMBER = "cityObjectMember"


@dataclass
class CropReport:
    """Outcome of a crop run."""

    requested: int = 0
    kept: int = 0
    missing_ids: list[str] = field(default_factory=list)
    appearance_pruned: int = 0

    def to_dict(self) -> dict:
        return self.__dict__.copy()


def _member_ids(member) -> set[str]:
    """Every Building/BuildingPart gml:id inside one cityObjectMember: the
    top-level feature plus any BuildingParts nested under it, so a request
    naming either the whole building or just one of its parts matches."""
    return {gml_id(e) for e in member.iter() if e.tag in _FEATURE_TAGS and gml_id(e)}


def _recompute_envelope(root) -> None:
    """Replace the root gml:boundedBy/Envelope with the true bounds of
    whatever geometry survived the crop, keeping the source's srsName and
    srsDimension. Every other command in this project reads coordinates
    straight out of gml:posList (see geometry.parse_pos_list), so this reuses
    that rather than trusting any envelope already present.
    """
    envelope = root.find(q(GML, "boundedBy") + "/" + q(GML, "Envelope"))
    srs_name = envelope.get("srsName") if envelope is not None else None
    srs_dim = envelope.get("srsDimension") if envelope is not None else None

    xs, ys, zs = [], [], []
    for pos_list in root.iter(q(GML, "posList")):
        if not pos_list.text:
            continue
        for x, y, z in parse_pos_list(pos_list.text):
            xs.append(x)
            ys.append(y)
            zs.append(z)

    bounded_by = root.find(q(GML, "boundedBy"))
    if bounded_by is not None:
        root.remove(bounded_by)
    if not xs:
        return  # nothing survived the crop; no envelope to describe it

    bounded_by = etree.Element(q(GML, "boundedBy"))
    new_envelope = etree.SubElement(bounded_by, q(GML, "Envelope"))
    if srs_dim:
        new_envelope.set("srsDimension", srs_dim)
    if srs_name:
        new_envelope.set("srsName", srs_name)
    lower = etree.SubElement(new_envelope, q(GML, "lowerCorner"))
    lower.text = f"{min(xs)} {min(ys)} {min(zs)}"
    upper = etree.SubElement(new_envelope, q(GML, "upperCorner"))
    upper.text = f"{max(xs)} {max(ys)} {max(zs)}"
    root.insert(0, bounded_by)


def _uses_global_appearance(root) -> bool:
    """True if appearances are declared once at CityModel level (a top-level
    appearanceMember referencing polygons across many buildings by id), as
    opposed to nested once per feature (this project's Hamburg/CityGRID
    reference data): a straight crop only risks leaving stale references in
    the former case."""
    for child in root:
        if localname(child) == "appearanceMember":
            return True
    return False


def _prune_orphaned_appearance_targets(root) -> int:
    """For the global-appearance convention: drop app:target entries whose
    uri points at a polygon id no longer present after the crop. Walking
    every kept gml:id directly (rather than resolving each target's full
    xlink chain) is enough here since app:target uris reference polygon/ring
    ids, which are removed wholesale with their owning feature.
    """
    kept_polygon_ids = {gml_id(e) for e in root.iter(q(GML, "Polygon")) if gml_id(e)}
    kept_ring_ids = {gml_id(e) for e in root.iter(q(GML, "LinearRing")) if gml_id(e)}
    surviving = kept_polygon_ids | kept_ring_ids

    pruned = 0
    for target in list(root.iter(f"{_APP_NS}target")):
        uri = target.get("uri", "").lstrip("#")
        if uri and uri not in surviving:
            parent = target.getparent()
            if parent is not None:
                parent.remove(target)
                pruned += 1
    return pruned


def crop(input_path: str, output_path: str, ids) -> CropReport:
    """Write a copy of `input_path` containing only the requested feature ids.

    `ids` is any iterable of gml:id strings. Naming a `BuildingPart`'s id
    keeps its whole parent `Building` (a lone part is not independently
    meaningful CityGML); naming a `Building`'s id keeps it and all its parts,
    as normal. Ids not found in the source are reported, never silently
    dropped, the same "report, don't force" stance used throughout this
    project.
    """
    requested = set(ids)
    parser = etree.XMLParser(huge_tree=True, remove_blank_text=False)
    tree = etree.parse(input_path, parser)
    root = tree.getroot()

    # The source's true last child carries the tail text (typically a bare
    # newline, no indent) that leads into the closing </CityModel> tag; every
    # other child's tail instead indents into its *next* sibling. Whichever
    # child survives the filter below inherits its own original tail, so
    # unless the kept feature happened to already be the last one in the
    # source, the closing tag ends up wrongly indented, still valid XML, but
    # visibly inconsistent with the rest of the file. Capture the true
    # original tail up front and reapply it to whatever ends up last.
    closing_tail = root[-1].tail if len(root) else None

    found: set[str] = set()
    for member in list(root):
        if localname(member) != _CITY_OBJECT_MEMBER:
            continue
        member_ids = _member_ids(member)
        matched = member_ids & requested
        if matched:
            found |= matched
        else:
            root.remove(member)

    if len(root) and closing_tail is not None:
        root[-1].tail = closing_tail

    report = CropReport(requested=len(requested), kept=len(found),
                        missing_ids=sorted(requested - found))

    if _uses_global_appearance(root):
        report.appearance_pruned = _prune_orphaned_appearance_targets(root)

    _recompute_envelope(root)
    tree.write(output_path, xml_declaration=True, encoding="utf-8")
    return report
