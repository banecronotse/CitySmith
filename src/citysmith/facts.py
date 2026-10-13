"""File-level facts for `inspect`: what a CityGML file is, before anything
is done with it.

`core.inspect()` answers "what can CitySmith's commands do with each
building?". This module answers the questions that come first with a file
someone just handed over: which CityGML version and coordinate system, where
it lies, what kinds of objects it holds, how much of it is textures, which
extra attributes the provider added, whether `measuredHeight` is there, and
whether it uses extensions the schema check will need help with.
`compare_files()` puts several files side by side, which doubles as a
preflight for `merge`: same CRS, shared ids, overlapping extents.
"""

from __future__ import annotations

import os
import urllib.parse
from collections import Counter
from dataclasses import dataclass, field

from lxml import etree

from .citygml import BLDG, BOUNDARY_LOCALNAMES, GEN, GML, localname, q
from .geometry import parse_pos_list
from .merge import _crs_key, _top_feature
from .schema import _KNOWN_NAMESPACES

_APP = "http://www.opengis.net/citygml/appearance/2.0"
_CITYGML_VERSIONS = {
    "http://www.opengis.net/citygml/1.0": "1.0",
    "http://www.opengis.net/citygml/2.0": "2.0",
    "http://www.opengis.net/citygml/3.0": "3.0",
}
# Building element types worth counting, beyond the top-level features.
_BUILDING_TYPES = ("Building", "BuildingPart", "BuildingInstallation",
                   *sorted(BOUNDARY_LOCALNAMES), "Window", "Door")
# Optional building attributes whose presence decides what some options can
# do (e.g. measuredHeight for `lod --lod1-height measured`).
_BUILDING_ATTRIBUTES = ("measuredHeight", "function", "usage", "roofType",
                        "storeysAboveGround", "yearOfConstruction")
_GENERIC_TYPES = {"stringAttribute": "string", "intAttribute": "int",
                  "doubleAttribute": "double", "dateAttribute": "date",
                  "uriAttribute": "uri", "measureAttribute": "measure",
                  "genericAttributeSet": "set"}


@dataclass
class FileFacts:
    """File-level facts gathered by `file_facts()`."""

    path: str = ""
    size_bytes: int = 0
    citygml_version: str | None = None
    srs_names: dict = field(default_factory=dict)
    extent_declared: list | None = None
    extent_measured: list | None = None
    extent_note: str = ""
    top_level_types: dict = field(default_factory=dict)
    building_elements: dict = field(default_factory=dict)
    building_attributes: dict = field(default_factory=dict)
    generic_attributes: dict = field(default_factory=dict)
    appearance: dict = field(default_factory=dict)
    namespaces_without_schema: list[str] = field(default_factory=list)
    schema: dict | None = None
    top_level_ids: set = field(default_factory=set, repr=False)

    def to_dict(self) -> dict:
        d = self.__dict__.copy()
        d["top_level_ids"] = len(self.top_level_ids)
        return d


def _qualified(elem) -> str:
    """'bldg:Building' style name, using the file's own prefix."""
    qn = etree.QName(elem)
    prefix = elem.prefix
    return f"{prefix}:{qn.localname}" if prefix else qn.localname


def _measured_extent(root):
    lo = [float("inf")] * 3
    hi = [float("-inf")] * 3
    for pos_list in root.iter(q(GML, "posList"), q(GML, "pos")):
        if not pos_list.text:
            continue
        for pt in parse_pos_list(pos_list.text):
            for i in range(3):
                lo[i] = min(lo[i], pt[i])
                hi[i] = max(hi[i], pt[i])
    return None if lo[0] == float("inf") else [lo, hi]


def _declared_extent(root):
    env = root.find(q(GML, "boundedBy") + "/" + q(GML, "Envelope"))
    if env is None:
        return None
    try:
        lower = [float(v) for v in env.findtext(q(GML, "lowerCorner")).split()]
        upper = [float(v) for v in env.findtext(q(GML, "upperCorner")).split()]
    except (AttributeError, ValueError):
        return None
    return [lower, upper]


def _extent_note(declared, measured) -> str:
    if measured is None:
        return "no coordinates found"
    if declared is None:
        return "no envelope declared"
    # 1 m slack: rounding in the declared corners is normal.
    for i in range(min(len(declared[0]), 3)):
        if measured[0][i] < declared[0][i] - 1 or measured[1][i] > declared[1][i] + 1:
            return "declared envelope does not cover all coordinates (outdated?)"
    return "declared envelope covers all coordinates"


def _appearance_facts(root, input_path) -> dict:
    themes = Counter()
    textures = materials = georef = 0
    images = set()
    for app in root.iter(q(_APP, "Appearance")):
        themes[app.findtext(q(_APP, "theme")) or "(no theme)"] += 1
    for elem in root.iter(q(_APP, "ParameterizedTexture"), q(_APP, "GeoreferencedTexture"),
                          q(_APP, "X3DMaterial")):
        name = localname(elem)
        if name == "X3DMaterial":
            materials += 1
            continue
        if name == "GeoreferencedTexture":
            georef += 1
        else:
            textures += 1
        uri = (elem.findtext(q(_APP, "imageURI")) or "").strip()
        if uri:
            images.add(uri)
    base = os.path.dirname(os.path.abspath(input_path))
    found_bytes, missing = 0, []
    for uri in sorted(images):
        if len(urllib.parse.urlparse(uri).scheme) > 1:
            continue  # a URL, not a local file
        path = uri if os.path.isabs(uri) else os.path.join(base, uri)
        if os.path.isfile(path):
            found_bytes += os.path.getsize(path)
        else:
            missing.append(uri)
    global_apps = sum(1 for c in root if localname(c) == "appearanceMember")
    return {"themes": dict(themes), "parameterized_textures": textures,
            "georeferenced_textures": georef, "materials": materials,
            "image_files": len(images), "image_files_missing": len(missing),
            "missing_examples": missing[:5], "image_bytes": found_bytes,
            "global_appearance_members": global_apps}


def file_facts(input_path: str, root=None, *, with_schema: bool = False) -> FileFacts:
    """Gather the file-level facts. `root` can be passed in when the caller
    has already parsed the file; `with_schema` adds a schema check summary."""
    if root is None:
        root = etree.parse(input_path, etree.XMLParser(huge_tree=True)).getroot()
    facts = FileFacts(path=str(input_path), size_bytes=os.path.getsize(input_path))
    facts.citygml_version = _CITYGML_VERSIONS.get(etree.QName(root).namespace)

    srs = Counter(e.get("srsName") for e in root.iter()
                  if isinstance(e.tag, str) and e.get("srsName"))
    facts.srs_names = dict(srs)
    facts.extent_declared = _declared_extent(root)
    facts.extent_measured = _measured_extent(root)
    facts.extent_note = _extent_note(facts.extent_declared, facts.extent_measured)

    top = Counter()
    for member in root:
        if localname(member) != "cityObjectMember":
            continue
        feat = _top_feature(member)
        if feat is None:
            continue
        top[_qualified(feat)] += 1
        fid = feat.get(q(GML, "id"))
        if fid:
            facts.top_level_ids.add(fid)
    facts.top_level_types = dict(top.most_common())

    counts = Counter(localname(e) for e in root.iter()
                     if isinstance(e.tag, str) and etree.QName(e).namespace == BLDG)
    facts.building_elements = {t: counts[t] for t in _BUILDING_TYPES if counts[t]}

    features = [e for e in root.iter(q(BLDG, "Building"), q(BLDG, "BuildingPart"))]
    facts.building_attributes = {
        name: {"present": sum(1 for f in features if f.find(q(BLDG, name)) is not None),
               "of": len(features)}
        for name in _BUILDING_ATTRIBUTES}

    generic = {}
    for elem in root.iter():
        if not isinstance(elem.tag, str) or etree.QName(elem).namespace != GEN:
            continue
        kind = _GENERIC_TYPES.get(localname(elem))
        if kind is None:
            continue
        name = elem.get("name") or elem.findtext(q(GEN, "name")) or "(no name)"
        entry = generic.setdefault(name, {"type": kind, "count": 0})
        entry["count"] += 1
        if entry["type"] != kind:
            entry["type"] = "mixed"
    facts.generic_attributes = dict(sorted(generic.items(), key=lambda kv: -kv[1]["count"]))

    facts.appearance = _appearance_facts(root, input_path)

    used = {etree.QName(e).namespace for e in root.iter() if isinstance(e.tag, str)}
    if facts.citygml_version == "2.0":
        facts.namespaces_without_schema = sorted(ns for ns in used - _KNOWN_NAMESPACES if ns)

    if with_schema:
        from .schema import validate_schema
        sr = validate_schema(input_path)
        facts.schema = {"checked": sr.checked, "valid": sr.valid,
                        "error_count": sr.error_count, "note": sr.note,
                        "first_errors": sr.messages[:3]}
    return facts


def _overlap_2d(a, b) -> bool:
    return (a is not None and b is not None
            and a[0][0] <= b[1][0] and b[0][0] <= a[1][0]
            and a[0][1] <= b[1][1] and b[0][1] <= a[1][1])


def compare_files(all_facts: list[FileFacts]) -> dict:
    """Side-by-side checks for several files, phrased as a merge preflight:
    `merge` stops on different coordinate systems and skips (or renames)
    features whose gml:id already appeared in an earlier file."""
    versions = {f.path: f.citygml_version for f in all_facts}
    crs = {f.path: sorted(f.srs_names) for f in all_facts}
    crs_keys = {_crs_key(s) for f in all_facts for s in f.srs_names}
    shared = []
    for i, a in enumerate(all_facts):
        for b in all_facts[i + 1:]:
            common = sorted(a.top_level_ids & b.top_level_ids)
            if common:
                shared.append({"files": [a.path, b.path], "count": len(common),
                               "examples": common[:5]})
    overlaps = [[a.path, b.path] for i, a in enumerate(all_facts)
                for b in all_facts[i + 1:] if _overlap_2d(a.extent_measured, b.extent_measured)]

    problems = []
    if len(set(versions.values())) > 1 or any(v != "2.0" for v in versions.values()):
        problems.append("not all files are CityGML 2.0: merge needs 2.0 everywhere")
    if len(crs_keys) > 1:
        problems.append("different coordinate systems: merge would stop, reproject first")
    elif not crs_keys:
        problems.append("no coordinate system declared in any file")
    notes = []
    if shared:
        total = sum(s["count"] for s in shared)
        notes.append(f"{total} top-level id(s) appear in more than one file: merge skips "
                     "them as duplicates (or renames them with --on-duplicate-id rename)")
    if overlaps:
        notes.append("extents overlap: check the seam for the same building under "
                     "different ids (merge lists these as possible duplicates)")
    return {"citygml_versions": versions, "srs_names": crs,
            "same_crs": len(crs_keys) == 1, "shared_top_level_ids": shared,
            "overlapping_extents": overlaps,
            "merge_preflight": "would stop" if problems else "ok",
            "problems": problems, "notes": notes}
