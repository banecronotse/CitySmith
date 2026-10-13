"""Check a CityGML 2.0 file against the official XML schemas.

CityDoctor2 (citydoctor.py) checks geometry and semantics; this checks the
XML structure: which elements may appear where, in which order, with which
content. It needs no Java and no internet: the official schemas ship with
CitySmith, unmodified, under `data/schemas/` (see the NOTICE there), laid out
by their original URL so `http://schemas.opengis.net/...` imports resolve to
the local copies. Network access is switched off while compiling, so a
missing local copy fails loudly instead of being downloaded silently.

The approach follows citygml-tools' `validate` command: official schemas
built in, extension (ADE) schemas added from the file's own
`xsi:schemaLocation` or by the caller, each error reported once per line and
column.
"""

from __future__ import annotations

import os
import urllib.parse
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path

from lxml import etree

_SCHEMA_DIR = Path(__file__).parent / "data" / "schemas"
_XSI_SCHEMA_LOCATION = "{http://www.w3.org/2001/XMLSchema-instance}schemaLocation"

# Every CityGML 2.0 module, so that non-building content (which CitySmith
# passes through untouched) is checked too.
_CITYGML2_MODULES = {
    "http://www.opengis.net/citygml/2.0": "citygml/2.0/cityGMLBase.xsd",
    "http://www.opengis.net/citygml/appearance/2.0": "citygml/appearance/2.0/appearance.xsd",
    "http://www.opengis.net/citygml/bridge/2.0": "citygml/bridge/2.0/bridge.xsd",
    "http://www.opengis.net/citygml/building/2.0": "citygml/building/2.0/building.xsd",
    "http://www.opengis.net/citygml/cityfurniture/2.0":
        "citygml/cityfurniture/2.0/cityFurniture.xsd",
    "http://www.opengis.net/citygml/cityobjectgroup/2.0":
        "citygml/cityobjectgroup/2.0/cityObjectGroup.xsd",
    "http://www.opengis.net/citygml/generics/2.0": "citygml/generics/2.0/generics.xsd",
    "http://www.opengis.net/citygml/landuse/2.0": "citygml/landuse/2.0/landUse.xsd",
    "http://www.opengis.net/citygml/relief/2.0": "citygml/relief/2.0/relief.xsd",
    "http://www.opengis.net/citygml/transportation/2.0":
        "citygml/transportation/2.0/transportation.xsd",
    "http://www.opengis.net/citygml/tunnel/2.0": "citygml/tunnel/2.0/tunnel.xsd",
    "http://www.opengis.net/citygml/vegetation/2.0": "citygml/vegetation/2.0/vegetation.xsd",
    "http://www.opengis.net/citygml/waterbody/2.0": "citygml/waterbody/2.0/waterBody.xsd",
    "http://www.opengis.net/citygml/texturedsurface/2.0":
        "citygml/texturedsurface/2.0/texturedSurface.xsd",
}

# Namespaces the bundled set defines; anything else in a file needs an
# extension schema.
_KNOWN_NAMESPACES = set(_CITYGML2_MODULES) | {
    "http://www.opengis.net/gml",
    "http://www.w3.org/1999/xlink",
    "urn:oasis:names:tc:ciq:xsdschema:xAL:2.0",
    "http://www.w3.org/2001/SMIL20/",
    "http://www.w3.org/2001/SMIL20/Language",
    "http://www.w3.org/XML/1998/namespace",
    "http://www.w3.org/2001/XMLSchema-instance",
}

_OTHER_CITYGML_VERSIONS = {
    "http://www.opengis.net/citygml/1.0": "1.0",
    "http://www.opengis.net/citygml/3.0": "3.0",
}


class SchemaError(Exception):
    """The schemas themselves could not be loaded or compiled."""


@dataclass
class SchemaReport:
    """Outcome of a schema check."""

    path: str = ""
    citygml_version: str | None = None
    checked: bool = False
    valid: bool = False
    error_count: int = 0
    messages: list[dict] = field(default_factory=list)
    unknown_namespaces: list[str] = field(default_factory=list)
    extra_schemas: list[str] = field(default_factory=list)
    note: str = ""

    def to_dict(self) -> dict:
        return self.__dict__.copy()


class _LocalResolver(etree.Resolver):
    """Serve http(s) schema URLs from the bundled copies."""

    def resolve(self, url, pubid, context):
        parsed = urllib.parse.urlparse(url)
        if parsed.scheme in ("http", "https"):
            local = _SCHEMA_DIR / parsed.netloc / parsed.path.lstrip("/")
            if local.is_file():
                return self.resolve_filename(str(local), context)
        return None


def _file_uri(path) -> str:
    return Path(path).resolve().as_uri()


@lru_cache(maxsize=8)
def _compiled_schema(extra: tuple[tuple[str, str], ...]) -> etree.XMLSchema:
    """One XMLSchema covering every CityGML 2.0 module plus the given
    (namespace, file) extension schemas. Cached: compiling takes a few
    tenths of a second and a run may check several files."""
    imports = [(ns, f"http://schemas.opengis.net/{rel}") for ns, rel in _CITYGML2_MODULES.items()]
    imports += [(ns, _file_uri(path)) for ns, path in extra]
    wrapper = ('<xs:schema xmlns:xs="http://www.w3.org/2001/XMLSchema" '
               'targetNamespace="urn:citysmith:schema-wrapper">'
               + "".join(f'<xs:import namespace="{ns}" schemaLocation="{loc}"/>'
                         for ns, loc in imports)
               + "</xs:schema>")
    parser = etree.XMLParser(no_network=True)
    parser.resolvers.add(_LocalResolver())
    try:
        doc = etree.fromstring(wrapper.encode("utf-8"), parser,
                               base_url=_file_uri(_SCHEMA_DIR / "wrapper.xsd"))
        return etree.XMLSchema(doc)
    except (etree.XMLSchemaParseError, etree.XMLSyntaxError) as exc:
        raise SchemaError(f"could not compile the CityGML schemas: {exc}") from exc


def _target_namespace(xsd_path: str) -> str:
    try:
        root = etree.parse(xsd_path).getroot()
    except (OSError, etree.XMLSyntaxError) as exc:
        raise SchemaError(f"could not read schema {xsd_path}: {exc}") from exc
    tns = root.get("targetNamespace")
    if not tns:
        raise SchemaError(f"schema {xsd_path} declares no targetNamespace")
    return tns


def _local_ade_schemas(root, input_path) -> list[tuple[str, str]]:
    """Extension schemas the file names in its own xsi:schemaLocation, when
    they are local files next to it. URLs are never fetched."""
    tokens = (root.get(_XSI_SCHEMA_LOCATION) or "").split()
    found = []
    base = Path(input_path).resolve().parent
    for ns, loc in zip(tokens[0::2], tokens[1::2]):
        if ns in _KNOWN_NAMESPACES or urllib.parse.urlparse(loc).scheme in ("http", "https"):
            continue
        path = base / urllib.parse.unquote(loc.replace("file:///", "").replace("file://", ""))
        if path.is_file():
            found.append((ns, str(path)))
    return found


def validate_schema(input_path: str, extra_schemas=()) -> SchemaReport:
    """Validate `input_path` against the bundled CityGML 2.0 schemas.

    `extra_schemas` are paths to additional .xsd files (extension/ADE
    schemas). Local extension schemas named in the file's own
    xsi:schemaLocation are added automatically; remote ones are not fetched
    and their namespace is reported in `unknown_namespaces` instead.
    Raises SchemaError only if the schemas themselves cannot be compiled.
    """
    report = SchemaReport(path=str(input_path))
    parser = etree.XMLParser(huge_tree=True, no_network=True)
    try:
        tree = etree.parse(str(input_path), parser)
    except etree.XMLSyntaxError as exc:
        report.checked = True
        report.error_count = 1
        line, column = exc.position if exc.position else (0, 0)
        report.messages.append({"line": line, "column": column,
                                "message": f"not well-formed XML: {exc.msg}"})
        return report
    root = tree.getroot()
    root_ns = etree.QName(root).namespace
    if root_ns in _OTHER_CITYGML_VERSIONS:
        report.citygml_version = _OTHER_CITYGML_VERSIONS[root_ns]
        report.note = (f"CityGML {report.citygml_version} file; only CityGML 2.0 can be "
                       "checked (citygml-tools validates all versions)")
        return report
    if root_ns in _CITYGML2_MODULES:
        report.citygml_version = "2.0"

    extra = [(_target_namespace(p), str(Path(p).resolve())) for p in extra_schemas]
    extra += [x for x in _local_ade_schemas(root, input_path) if x[0] not in dict(extra)]
    report.extra_schemas = [path for _, path in extra]
    defined = _KNOWN_NAMESPACES | {ns for ns, _ in extra}
    used = {etree.QName(e).namespace for e in root.iter() if isinstance(e.tag, str)}
    report.unknown_namespaces = sorted(ns for ns in used - defined if ns)

    schema = _compiled_schema(tuple(sorted(extra)))
    report.checked = True
    report.valid = schema.validate(tree)
    seen = set()
    for err in schema.error_log:
        key = (err.line, err.column)
        if key in seen:
            continue
        seen.add(key)
        report.messages.append({"line": err.line, "column": err.column,
                                "message": err.message})
    report.error_count = len(report.messages)
    if report.unknown_namespaces and not report.valid:
        report.note = ("the file uses namespaces without a schema (extensions/ADEs); "
                       "errors on their elements are expected unless the schema is "
                       "given with --schema-file")
    return report


def bundled_schema_dir() -> str:
    """Where the bundled official schemas live (for the NOTICE and updates)."""
    return os.fspath(_SCHEMA_DIR)
