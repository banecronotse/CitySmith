"""Combine several CityGML files into one.

The common need this serves: a dataset delivered in parts (tiles, project
phases, neighbouring areas) has to become one model. The first input is the
base; every later input is appended to it, one `cityObjectMember` at a time.

Merging is more than concatenating XML, so each step checks and reports
rather than forcing a result:

- Every input must be CityGML 2.0 and declare the same `srsName`. A mismatch
  stops the merge; CitySmith does not reproject.
- A top-level feature whose `gml:id` already exists in the merged model is
  treated as the same object delivered twice and skipped (the default), or
  kept under a renamed id with `on_duplicate_id="rename"`.
- Any other `gml:id` that clashes (polygons, rings, surfaces, appearances)
  is renamed inside the incoming file, and every reference to it in that
  file (`xlink:href`, appearance targets, texture coordinate rings) follows.
- Relative texture image paths are rewritten so they still resolve from the
  output file's folder. Images are not copied.
- Features from different files with almost the same 2D bounding box are
  listed as possible duplicates (typical at the seam between two parts,
  when the same building was delivered twice under different ids). They
  are reported only, never removed: a bounding box cannot prove that two
  features are the same building.
- The file's `gml:Envelope` is recomputed to cover the merged geometry.
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass, field
from urllib.parse import urlparse

from lxml import etree

from .citygml import GML, NS, gml_id, localname, q
from .crop import (_prune_orphaned_appearance_targets, _recompute_envelope,
                   _uses_global_appearance)
from .geometry import parse_pos_list

_CITY_OBJECT_MEMBER = "cityObjectMember"
_APPEARANCE_MEMBER = "appearanceMember"
_XSI_SCHEMA_LOCATION = "{http://www.w3.org/2001/XMLSchema-instance}schemaLocation"

# Intersection over union of the 2D bounding boxes from which two features
# from different files are listed as possible duplicates.
_OVERLAP_THRESHOLD = 0.8
_GRID_CELL = 50.0  # metres, bucket size for the overlap search


class MergeError(Exception):
    """The inputs cannot be merged safely (different CRS, not CityGML 2.0)."""


@dataclass
class MergeReport:
    """Outcome of a merge run."""

    inputs: list[str] = field(default_factory=list)
    srs_name: str | None = None
    srs_unknown: list[str] = field(default_factory=list)
    members_per_input: dict[str, int] = field(default_factory=dict)
    members_written: int = 0
    duplicate_ids_skipped: list[str] = field(default_factory=list)
    duplicate_ids_renamed: dict[str, str] = field(default_factory=dict)
    ids_renamed: int = 0
    id_renames: dict[str, dict[str, str]] = field(default_factory=dict)
    possible_duplicates: list[dict] = field(default_factory=list)
    texture_paths_rewritten: int = 0
    texture_files_missing: list[str] = field(default_factory=list)
    appearance_pruned: int = 0

    def to_dict(self) -> dict:
        return self.__dict__.copy()


def _crs_key(srs_name: str) -> str:
    """Comparable form of an srsName. 'EPSG:32634', 'epsg:32634',
    'urn:ogc:def:crs:EPSG::32634' and
    'http://www.opengis.net/def/crs/EPSG/0/32634' all name the same CRS, so
    the EPSG codes are pulled out (4 to 6 digits, which skips the version
    numbers some URN and URL forms carry). Compound CRS keep every code."""
    low = srs_name.strip().lower()
    if "epsg" in low:
        codes = re.findall(r"(?<!\d)\d{4,6}(?!\d)", low.split("epsg", 1)[1])
        if codes:
            return "epsg:" + "+".join(codes)
    return low


def _srs_name(root) -> str | None:
    """The document level srsName (root envelope), or else the first one
    declared anywhere, matching the scope note that CitySmith assumes one
    CRS per file."""
    envelope = root.find(q(GML, "boundedBy") + "/" + q(GML, "Envelope"))
    if envelope is not None and envelope.get("srsName"):
        return envelope.get("srsName")
    for elem in root.iter():
        if isinstance(elem.tag, str) and elem.get("srsName"):
            return elem.get("srsName")
    return None


def _top_feature(member):
    for child in member:
        if isinstance(child.tag, str):
            return child
    return None


def _bbox_2d(elem):
    xs, ys = [], []
    for pos_list in elem.iter(q(GML, "posList")):
        if pos_list.text:
            for x, y, _z in parse_pos_list(pos_list.text):
                xs.append(x)
                ys.append(y)
    if not xs:
        return None
    return min(xs), min(ys), max(xs), max(ys)


def _overlap_share(a, b) -> float:
    """Intersection over union of two 2D boxes. Union rather than the
    smaller box: in dense blocks a small building often lies wholly inside
    a bigger neighbour's box, which is containment, not a duplicate."""
    w = min(a[2], b[2]) - max(a[0], b[0])
    h = min(a[3], b[3]) - max(a[1], b[1])
    if w <= 0 or h <= 0:
        return 0.0
    inter = w * h
    union = (a[2] - a[0]) * (a[3] - a[1]) + (b[2] - b[0]) * (b[3] - b[1]) - inter
    return inter / union if union > 0 else 0.0


def _cells(box):
    for i in range(int(box[0] // _GRID_CELL), int(box[2] // _GRID_CELL) + 1):
        for j in range(int(box[1] // _GRID_CELL), int(box[3] // _GRID_CELL) + 1):
            yield i, j


def _remap_references(elem, renames: dict[str, str]) -> None:
    """Apply an id rename map to one element subtree: the gml:id itself,
    any attribute holding a local '#id' reference (xlink:href, app:target
    uri, the ring attribute of texture coordinates) and the '#id' text of
    X3DMaterial-style app:target elements."""
    for e in elem.iter():
        if not isinstance(e.tag, str):
            continue
        for name, value in e.attrib.items():
            if name == q(GML, "id"):
                if value in renames:
                    e.set(name, renames[value])
            elif value.startswith("#") and value[1:] in renames:
                e.set(name, "#" + renames[value[1:]])
        if localname(e) == "target" and e.text:
            ref = e.text.strip()
            if ref.startswith("#") and ref[1:] in renames:
                e.text = "#" + renames[ref[1:]]


def _rewrite_texture_paths(elem, src_dir: str, out_dir: str, report: MergeReport) -> None:
    """Make relative app:imageURI paths resolve from the output folder.
    URLs and absolute paths are left as they are."""
    for e in elem.iter():
        if not isinstance(e.tag, str) or localname(e) != "imageURI" or not e.text:
            continue
        uri = e.text.strip()
        if len(urlparse(uri).scheme) > 1 or os.path.isabs(uri):
            continue
        source_file = os.path.normpath(os.path.join(src_dir, uri))
        if not os.path.exists(source_file):
            report.texture_files_missing.append(source_file)
        new_uri = os.path.relpath(source_file, out_dir).replace(os.sep, "/")
        if new_uri != uri:
            e.text = new_uri
            report.texture_paths_rewritten += 1


def _merged_root(roots):
    """A new CityModel root whose namespace map is the union of all inputs
    (the first input's prefixes win) and whose schemaLocation lists every
    schema any input declared, so lxml does not scatter ns0-style
    declarations through the appended features."""
    nsmap = {}
    used_uris = set()
    for root in roots:
        for prefix, uri in root.nsmap.items():
            if prefix in nsmap or uri in used_uris:
                continue
            nsmap[prefix] = uri
            used_uris.add(uri)
    base = roots[0]
    new_root = etree.Element(base.tag, nsmap=nsmap)
    for name, value in base.attrib.items():
        new_root.set(name, value)
    pairs = []
    for root in roots:
        tokens = (root.get(_XSI_SCHEMA_LOCATION) or "").split()
        for ns_uri, location in zip(tokens[0::2], tokens[1::2]):
            if ns_uri not in {p[0] for p in pairs}:
                pairs.append((ns_uri, location))
    if pairs:
        new_root.set(_XSI_SCHEMA_LOCATION, " ".join(f"{a} {b}" for a, b in pairs))
    new_root.text = base.text
    return new_root, nsmap


def merge(input_paths, output_path: str, *, on_duplicate_id: str = "skip") -> MergeReport:
    """Write the features of all `input_paths` into one file at `output_path`.

    The first input is the base: its document level elements (envelope
    srsName, gml:name, description) are kept and every later input's
    cityObjectMembers and appearanceMembers are appended after it.
    `on_duplicate_id` decides what happens to a top-level feature whose
    gml:id is already taken: "skip" (default, treat it as the same object
    delivered twice) or "rename" (keep it under a prefixed id).
    Raises MergeError if the inputs are not CityGML 2.0 or name different
    coordinate reference systems.
    """
    if on_duplicate_id not in ("skip", "rename"):
        raise ValueError("on_duplicate_id must be 'skip' or 'rename'")
    input_paths = list(input_paths)
    if len(input_paths) < 2:
        raise MergeError("merge needs at least two input files")

    parser = etree.XMLParser(huge_tree=True, remove_blank_text=False)
    trees = [etree.parse(p, parser) for p in input_paths]
    roots = [t.getroot() for t in trees]
    report = MergeReport(inputs=input_paths)

    citygml2 = q(NS["core"], "CityModel")
    for path, root in zip(input_paths, roots):
        if root.tag != citygml2:
            raise MergeError(f"{path}: not a CityGML 2.0 CityModel (root is {root.tag})")

    crs_by_input = {}
    for path, root in zip(input_paths, roots):
        srs = _srs_name(root)
        if srs is None:
            report.srs_unknown.append(path)
        else:
            crs_by_input[path] = srs
    keys = {_crs_key(s) for s in crs_by_input.values()}
    if len(keys) > 1:
        listing = "\n".join(f"  {p}: {s}" for p, s in crs_by_input.items())
        raise MergeError("inputs use different coordinate reference systems, "
                         f"reproject first:\n{listing}")
    report.srs_name = next(iter(crs_by_input.values()), None)

    out_dir = os.path.dirname(os.path.abspath(output_path))
    new_root, nsmap = _merged_root(roots)
    closing_tail = roots[0][-1].tail if len(roots[0]) else None

    taken: set[str] = set()
    boxes: list[tuple[tuple, str, str]] = []  # (bbox, feature id, input path)
    grid: dict[tuple[int, int], list[int]] = {}

    for index, (path, root) in enumerate(zip(input_paths, roots)):
        src_dir = os.path.dirname(os.path.abspath(path))
        members = [c for c in root if localname(c) == _CITY_OBJECT_MEMBER]
        report.members_per_input[path] = len(members)

        if index == 0:
            # The envelope is rebuilt at the end from the merged geometry.
            for child in list(root):
                if localname(child) != "boundedBy":
                    new_root.append(child)
            incoming = [c for c in new_root if isinstance(c.tag, str)]
        else:
            # Document level extras of later inputs (envelope, name,
            # description) belong to the base; only features and appearances
            # are carried over.
            incoming = []
            duplicate_tops = []
            for member in members:
                top = _top_feature(member)
                top_id = gml_id(top) if top is not None else None
                if top_id and top_id in taken:
                    if on_duplicate_id == "skip":
                        report.duplicate_ids_skipped.append(top_id)
                        continue
                    duplicate_tops.append(top_id)
                incoming.append(member)
            incoming += [c for c in root if localname(c) == _APPEARANCE_MEMBER]

            incoming_ids = {gml_id(e) for c in incoming for e in c.iter()
                            if isinstance(e.tag, str) and gml_id(e)}
            prefix = f"m{index + 1}_"
            renames = {}
            for old in sorted(incoming_ids & taken):
                new = prefix + old
                while new in taken or new in incoming_ids:
                    new = prefix + new
                renames[old] = new
            if renames:
                for c in incoming:
                    _remap_references(c, renames)
                report.id_renames[path] = renames
                report.ids_renamed += len(renames)
            for top_id in duplicate_tops:
                report.duplicate_ids_renamed[top_id] = renames[top_id]
            for c in incoming:
                new_root.append(c)

        for c in incoming:
            if isinstance(c.tag, str):
                _rewrite_texture_paths(c, src_dir, out_dir, report)
                taken.update(gml_id(e) for e in c.iter()
                             if isinstance(e.tag, str) and gml_id(e))

        for c in incoming:
            if localname(c) != _CITY_OBJECT_MEMBER:
                continue
            top = _top_feature(c)
            box = _bbox_2d(c)
            if top is None or box is None:
                continue
            fid = gml_id(top) or "(no id)"
            near = {k for cell in _cells(box) for k in grid.get(cell, ())}
            for k in sorted(near):
                other_box, other_id, other_path = boxes[k]
                if other_path == path:
                    continue
                share = _overlap_share(box, other_box)
                if share >= _OVERLAP_THRESHOLD:
                    report.possible_duplicates.append({
                        "id": fid, "input": path, "overlaps_id": other_id,
                        "overlaps_input": other_path, "bbox_overlap": round(share, 3)})
            boxes.append((box, fid, path))
            for cell in _cells(box):
                grid.setdefault(cell, []).append(len(boxes) - 1)

    if len(new_root) and closing_tail is not None:
        new_root[-1].tail = closing_tail
    report.members_written = sum(1 for c in new_root if localname(c) == _CITY_OBJECT_MEMBER)

    if _uses_global_appearance(new_root):
        report.appearance_pruned = _prune_orphaned_appearance_targets(new_root)

    # Seed an envelope that carries the shared srsName (whichever input
    # declared it), then let crop's helper fill in the merged bounds.
    bounded_by = etree.SubElement(new_root, q(GML, "boundedBy"))
    envelope = etree.SubElement(bounded_by, q(GML, "Envelope"), srsDimension="3")
    if report.srs_name:
        envelope.set("srsName", report.srs_name)
    _recompute_envelope(new_root)
    if len(new_root) > 1 and new_root[0].tail is None:
        new_root[0].tail = new_root.text

    etree.cleanup_namespaces(new_root, top_nsmap=nsmap)
    etree.ElementTree(new_root).write(output_path, xml_declaration=True, encoding="utf-8")
    return report
