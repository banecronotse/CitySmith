"""Tests for the LOD3 to LOD2 engine and geometry helpers."""

from collections import Counter
from pathlib import Path

import pytest
from lxml import etree

import citysmith
from citysmith.citygml import GML, BLDG, q
from citysmith.geometry import is_closed_shell, shell_stats, parse_pos_list

DATA = Path(__file__).parent / "CS1_lod3.gml"


def _count(root, tag):
    return sum(1 for _ in root.iter(q(BLDG, tag)))


def _ids(root):
    return [x.get(q(GML, "id")) for x in root.iter() if x.get(q(GML, "id"))]


# --- package -----------------------------------------------------------------

def test_package_exposes_api():
    assert hasattr(citysmith, "add_lod2")
    assert hasattr(citysmith, "Report")


# --- geometry ----------------------------------------------------------------

def test_parse_pos_list():
    assert parse_pos_list("1 2 3 4 5 6") == [(1, 2, 3), (4, 5, 6)]


def test_cube_is_closed():
    cube = [
        [(0, 0, 0), (1, 0, 0), (1, 1, 0), (0, 1, 0), (0, 0, 0)],
        [(0, 0, 1), (1, 0, 1), (1, 1, 1), (0, 1, 1), (0, 0, 1)],
        [(0, 0, 0), (1, 0, 0), (1, 0, 1), (0, 0, 1), (0, 0, 0)],
        [(1, 0, 0), (1, 1, 0), (1, 1, 1), (1, 0, 1), (1, 0, 0)],
        [(1, 1, 0), (0, 1, 0), (0, 1, 1), (1, 1, 1), (1, 1, 0)],
        [(0, 1, 0), (0, 0, 0), (0, 0, 1), (0, 1, 1), (0, 1, 0)],
    ]
    assert is_closed_shell(cube)
    assert shell_stats(cube)["closed"] is True


def test_open_shell_not_closed():
    open_box = [  # missing the top face
        [(0, 0, 0), (1, 0, 0), (1, 1, 0), (0, 1, 0), (0, 0, 0)],
        [(0, 0, 0), (1, 0, 0), (1, 0, 1), (0, 0, 1), (0, 0, 0)],
    ]
    stats = shell_stats(open_box)
    assert stats["closed"] is False
    assert stats["boundary_edges"] > 0


# --- engine: embed mode --------------------------------------------------------
# CS1 is a real CityGRID (aggregating-solid) building: a Building plus one
# BuildingPart, panelized LOD3 walls/roofs, real interior-ring windows, and
# known-open (not watertight) source geometry, see docs/DESIGN.md.

def test_embed_adds_lod2_and_keeps_lod3(tmp_path):
    out = tmp_path / "cs1_out.gml"
    report = citysmith.add_lod2(str(DATA), str(out))

    assert report.features == 2
    assert report.source_lod3 == 2
    assert report.source_pattern_solid == 2
    assert report.walls_deholed == 26

    root = etree.parse(str(out)).getroot()
    assert _count(root, "lod3Solid") == 2     # LOD3 preserved (embed mode)
    assert _count(root, "lod2Solid") == 2     # LOD2 added, one per feature

    # No interior ring (window/door hole) survives in a derived LOD2 wall.
    lod2 = list(root.iter(q(BLDG, "lod2MultiSurface")))
    assert lod2, "expected lod2MultiSurface elements"
    assert not any(list(msf.iter(q(GML, "interior"))) for msf in lod2)

    # Ids stay unique.
    dups = {k: v for k, v in Counter(_ids(root)).items() if v > 1}
    assert dups == {}


def test_lod2_solid_refs_resolve(tmp_path):
    out = tmp_path / "cs1_out.gml"
    citysmith.add_lod2(str(DATA), str(out))
    root = etree.parse(str(out)).getroot()
    poly_ids = {p.get(q(GML, "id")) for p in root.iter(q(GML, "Polygon"))}
    for solid in root.iter(q(BLDG, "lod2Solid")):
        for sm in solid.iter(q(GML, "surfaceMember")):
            href = sm.get(q("http://www.w3.org/1999/xlink", "href"))
            assert href.lstrip("#") in poly_ids


def test_reproducible_ids(tmp_path):
    a, b = tmp_path / "a.gml", tmp_path / "b.gml"
    citysmith.add_lod2(str(DATA), str(a))
    citysmith.add_lod2(str(DATA), str(b))
    assert a.read_bytes() == b.read_bytes()


# --- engine: lod2-only mode --------------------------------------------------

def test_lod2_only_strips_lod3(tmp_path):
    out = tmp_path / "cs1_only.gml"
    citysmith.add_lod2(str(DATA), str(out), keep_source=False)
    root = etree.parse(str(out)).getroot()
    assert _count(root, "lod3Solid") == 0
    assert _count(root, "lod3MultiSurface") == 0
    assert _count(root, "lod2Solid") == 2
    # a real generic attribute from the source survives the strip
    assert any(a.get("name") == "citygrid_UnitID" for a in root.iter(
        q("http://www.opengis.net/citygml/generics/2.0", "stringAttribute")))


# --- LOD0 / LOD1 -------------------------------------------------------------

def test_lod1_prism_watertight_and_lod0(tmp_path):
    from citysmith.core import _ring_points
    out = tmp_path / "cs1_multi.gml"
    report = citysmith.enhance(str(DATA), str(out), levels=(0, 1, 2))
    assert report.lod1_added == 2 and report.lod0_added == 2
    root = etree.parse(str(out)).getroot()
    assert _count(root, "lod1Solid") == 2
    assert _count(root, "lod0FootPrint") == 2
    for solid in root.iter(q(BLDG, "lod1Solid")):
        rings = [_ring_points(p) for p in solid.iter(q(GML, "Polygon"))]
        assert shell_stats(rings)["closed"] is True  # prism watertight by construction
    dups = {k: v for k, v in Counter(_ids(root)).items() if v > 1}
    assert dups == {}


# --- engine: split ground footprints ------------------------------------------
#
# Neither CS1 nor CS2 has a multi-polygon GroundSurface, so these build the
# minimum CityGML that exercises it: the 'surfaces' pattern (each thematic
# surface carries its own lod3MultiSurface), which needs no aggregating solid
# or xlinks to be a valid source.

_GML_HEAD = (
    '<?xml version="1.0" encoding="UTF-8"?>\n'
    '<CityModel xmlns="http://www.opengis.net/citygml/2.0" '
    'xmlns:gml="http://www.opengis.net/gml" '
    'xmlns:bldg="http://www.opengis.net/citygml/building/2.0">'
)


def _square(x0, y0, size, z):
    return [(x0, y0, z), (x0 + size, y0, z), (x0 + size, y0 + size, z),
            (x0, y0 + size, z), (x0, y0, z)]


def _poly(pid, pts):
    coords = " ".join(f"{x} {y} {z}" for x, y, z in pts)
    return (f'<gml:surfaceMember><gml:Polygon gml:id="{pid}"><gml:exterior>'
            f'<gml:LinearRing><gml:posList srsDimension="3">{coords}</gml:posList>'
            f'</gml:LinearRing></gml:exterior></gml:Polygon></gml:surfaceMember>')


def _surface(kind, sid, polys):
    return (f'<bldg:boundedBy><bldg:{kind} gml:id="{sid}"><bldg:lod3MultiSurface>'
            f'<gml:MultiSurface>{"".join(polys)}</gml:MultiSurface>'
            f'</bldg:lod3MultiSurface></bldg:{kind}></bldg:boundedBy>')


def _building(bid, surfaces):
    return (f'<cityObjectMember><bldg:Building gml:id="{bid}">'
            f'{"".join(surfaces)}</bldg:Building></cityObjectMember>')


def _write_gml(tmp_path, name, *buildings):
    path = tmp_path / name
    path.write_text(_GML_HEAD + "".join(buildings) + "</CityModel>", encoding="utf-8")
    return path


def test_lod1_disjoint_footprint_becomes_composite_solid(tmp_path):
    """A building whose ground plane is split into two separated pieces (it
    spans a passage) gets one prism per piece in a gml:CompositeSolid, each
    taking its own roof height rather than one shared block height."""
    from citysmith.core import _ring_points
    src = _write_gml(tmp_path, "split.gml", _building("B1", [
        _surface("GroundSurface", "g", [_poly("gA", _square(0, 0, 10, 0.0)),
                                        _poly("gB", _square(20, 0, 10, 0.0))]),
        _surface("RoofSurface", "r", [_poly("rA", _square(0, 0, 10, 10.0)),
                                      _poly("rB", _square(20, 0, 10, 8.0))]),
    ]))
    out = tmp_path / "split_lod1.gml"
    report = citysmith.enhance(str(src), str(out), levels=(1,), keep_source=False)
    assert report.lod1_added == 1
    assert report.lod1_composite == 1
    assert report.lod1_pieces_skipped == 0

    root = etree.parse(str(out)).getroot()
    composite = root.find(f".//{q(BLDG, 'lod1Solid')}/{q(GML, 'CompositeSolid')}")
    assert composite is not None
    assert len(composite.findall(q(GML, "solidMember"))) == 2
    tops = set()
    for solid in composite.iter(q(GML, "Solid")):
        rings = [_ring_points(p) for p in solid.iter(q(GML, "Polygon"))]
        assert shell_stats(rings)["closed"] is True  # every prism still watertight
        tops.add(round(max(z for r in rings for _, _, z in r), 3))
    assert tops == {10.0, 8.0}


def test_lod1_sliver_ground_piece_dropped(tmp_path):
    """A ground piece too small to be massing (a pillar carrying the building
    over a passage) is dropped rather than extruded into a tall thin spike,
    leaving a plain gml:Solid rather than a one-member CompositeSolid."""
    src = _write_gml(tmp_path, "sliver.gml", _building("B1", [
        _surface("GroundSurface", "g", [_poly("gA", _square(0, 0, 10, 0.0)),
                                        _poly("gS", _square(20, 0, 1, 0.0))]),
        _surface("RoofSurface", "r", [_poly("rA", _square(0, 0, 10, 10.0))]),
    ]))
    out = tmp_path / "sliver_lod1.gml"
    report = citysmith.enhance(str(src), str(out), levels=(1,), keep_source=False)
    assert report.lod1_added == 1
    assert report.lod1_pieces_skipped == 1
    assert report.lod1_composite == 0

    root = etree.parse(str(out)).getroot()
    assert root.find(f".//{q(BLDG, 'lod1Solid')}/{q(GML, 'CompositeSolid')}") is None
    assert root.find(f".//{q(BLDG, 'lod1Solid')}/{q(GML, 'Solid')}") is not None


def test_lod1_all_small_pieces_still_get_geometry(tmp_path):
    """The size threshold must never leave a building with no LOD1 at all: if
    every piece is below it, the building genuinely is that small, so all are
    kept."""
    src = _write_gml(tmp_path, "tiny.gml", _building("B1", [
        _surface("GroundSurface", "g", [_poly("gA", _square(0, 0, 1, 0.0)),
                                        _poly("gB", _square(5, 0, 2, 0.0))]),
        _surface("RoofSurface", "r", [_poly("rA", _square(0, 0, 1, 3.0)),
                                      _poly("rB", _square(5, 0, 2, 3.0))]),
    ]))
    out = tmp_path / "tiny_lod1.gml"
    report = citysmith.enhance(str(src), str(out), levels=(1,), keep_source=False)
    assert report.lod1_added == 1
    assert report.lod1_pieces_skipped == 0
    root = etree.parse(str(out)).getroot()
    composite = root.find(f".//{q(BLDG, 'lod1Solid')}/{q(GML, 'CompositeSolid')}")
    assert len(composite.findall(q(GML, "solidMember"))) == 2


def test_lower_only_keeps_and_reports_features_without_geometry(tmp_path):
    """A feature no LOD1 can be derived for (no GroundSurface to extrude) must
    survive the run and be named in the report, never silently deleted: a
    missing building is not actionable, an id in the summary is."""
    src = _write_gml(
        tmp_path, "noground.gml",
        _building("HASGROUND", [
            _surface("GroundSurface", "g", [_poly("gA", _square(0, 0, 10, 0.0))]),
            _surface("RoofSurface", "r", [_poly("rA", _square(0, 0, 10, 10.0))]),
        ]),
        _building("NOGROUND", [
            _surface("WallSurface", "w", [_poly("wA", [(0, 0, 0), (1, 0, 0),
                                                       (1, 0, 5), (0, 0, 5), (0, 0, 0)])]),
        ]),
    )
    out = tmp_path / "noground_lod1.gml"
    report = citysmith.enhance(str(src), str(out), levels=(1,), keep_source=False)
    assert report.lod1_added == 1
    assert report.lod1_skipped == 1
    assert report.kept_empty_ids == ["NOGROUND"]

    root = etree.parse(str(out)).getroot()
    assert {b.get(q(GML, "id")) for b in root.iter(q(BLDG, "Building"))} == {
        "HASGROUND", "NOGROUND"}


# --- crop ----------------------------------------------------------------------

def test_crop_keeps_only_requested_ids(tmp_path):
    from citysmith.crop import crop
    src = _write_gml(
        tmp_path, "three.gml",
        _building("KEEP1", [_surface("GroundSurface", "g1",
                                     [_poly("g1a", _square(0, 0, 5, 0.0))])]),
        _building("DROP", [_surface("GroundSurface", "g2",
                                    [_poly("g2a", _square(10, 0, 5, 0.0))])]),
        _building("KEEP2", [_surface("GroundSurface", "g3",
                                     [_poly("g3a", _square(20, 0, 5, 0.0))])]),
    )
    out = tmp_path / "three_crop.gml"
    report = crop(str(src), str(out), ["KEEP1", "KEEP2"])
    assert report.requested == 2
    assert report.kept == 2
    assert report.missing_ids == []

    root = etree.parse(str(out)).getroot()
    assert {b.get(q(GML, "id")) for b in root.iter(q(BLDG, "Building"))} == {
        "KEEP1", "KEEP2"}


def test_crop_missing_id_is_reported_not_silent(tmp_path):
    from citysmith.crop import crop
    src = _write_gml(tmp_path, "one.gml", _building(
        "ONLY", [_surface("GroundSurface", "g", [_poly("ga", _square(0, 0, 5, 0.0))])]))
    out = tmp_path / "one_crop.gml"
    report = crop(str(src), str(out), ["ONLY", "GHOST"])
    assert report.requested == 2
    assert report.kept == 1
    assert report.missing_ids == ["GHOST"]

    root = etree.parse(str(out)).getroot()
    assert [b.get(q(GML, "id")) for b in root.iter(q(BLDG, "Building"))] == ["ONLY"]


def test_crop_by_building_part_id_keeps_whole_parent(tmp_path):
    """Naming just a BuildingPart's id must keep its whole parent Building,
    a lone part is not independently meaningful CityGML."""
    from citysmith.crop import crop
    part_id = "UUID_4a9691a0-985c-5245-9ac2-8ad61ece0965"
    out = tmp_path / "cs1_part_crop.gml"
    src = Path(__file__).parent / "CS1_lod3_enhanced.gml"
    report = crop(str(src), str(out), [part_id])
    assert report.kept == 1
    assert report.missing_ids == []

    root = etree.parse(str(out)).getroot()
    assert [b.get(q(GML, "id")) for b in root.iter(q(BLDG, "Building"))] == ["CS1"]
    assert root.find(f".//{q(BLDG, 'BuildingPart')}[@{{{GML}}}id='{part_id}']") is not None


def test_crop_recomputes_envelope_to_kept_geometry(tmp_path):
    from citysmith.crop import crop
    src = _write_gml(
        tmp_path, "two.gml",
        _building("NEAR", [_surface("GroundSurface", "g1",
                                    [_poly("g1a", _square(0, 0, 5, 0.0))])]),
        _building("FAR", [_surface("GroundSurface", "g2",
                                   [_poly("g2a", _square(1000, 1000, 5, 0.0))])]),
    )
    out = tmp_path / "two_crop.gml"
    crop(str(src), str(out), ["NEAR"])

    root = etree.parse(str(out)).getroot()
    env = root.find(f"{q(GML, 'boundedBy')}/{q(GML, 'Envelope')}")
    assert env is not None
    lower = [float(x) for x in env.find(q(GML, "lowerCorner")).text.split()]
    upper = [float(x) for x in env.find(q(GML, "upperCorner")).text.split()]
    assert lower[0] == 0.0 and upper[0] == 5.0  # NEAR's extent, not FAR's


def test_crop_closing_tag_keeps_source_indentation(tmp_path):
    """A kept feature that wasn't already the last one in the source must not
    leave its own mid-document indentation (meant to lead into the next
    sibling) behind the closing </CityModel> tag: real CityGML formats each
    non-last cityObjectMember's tail as '\\n\\t' (indenting into the next
    sibling) and only the true last one as a bare '\\n' (leading straight into
    the closing tag), so naively keeping an earlier child's own tail wrongly
    indents the file's closing tag."""
    from citysmith.crop import crop
    src = tmp_path / "ordered.gml"
    src.write_text(
        _GML_HEAD
        + _building("FIRST", [_surface("GroundSurface", "g1",
                                       [_poly("g1a", _square(0, 0, 5, 0.0))])])
        + "\n\t"
        + _building("LAST", [_surface("GroundSurface", "g2",
                                      [_poly("g2a", _square(10, 0, 5, 0.0))])])
        + "\n</CityModel>",
        encoding="utf-8",
    )
    out = tmp_path / "ordered_crop.gml"
    crop(str(src), str(out), ["FIRST"])  # keep the one that was NOT last

    text = out.read_text(encoding="utf-8")
    assert text.rstrip("\n").endswith("</cityObjectMember>\n</CityModel>")


# --- engine: LOD2 as source (not just LOD3) -----------------------------------

def test_lod1_lod0_derivable_from_lod2_source(tmp_path):
    """LOD1/LOD0 only need Ground/Roof surface heights, which an LOD2-only
    file already has, so they must be derivable without any LOD3 present."""
    lod2_only = tmp_path / "cs1_lod2_only.gml"
    citysmith.add_lod2(str(DATA), str(lod2_only), keep_source=False)

    out = tmp_path / "cs1_lod2_plus_lower.gml"
    report = citysmith.enhance(str(lod2_only), str(out), levels=(0, 1))
    assert report.source_lod3 == 0
    assert report.source_lod2 == 2
    assert report.lod1_added == 2
    assert report.lod0_added == 2
    root = etree.parse(str(out)).getroot()
    assert _count(root, "lod1Solid") == 2
    assert _count(root, "lod0FootPrint") == 2


def test_lod2_rebuild_on_lod2_source_replaces_not_duplicates(tmp_path):
    """Asking for LOD2 again on an already-LOD2 source must never leave both
    the old and a freshly rebuilt LOD2 coexisting, in either mode. A real
    building's second merge pass can still find something to clean up (it
    isn't always a pure no-op the way a small synthetic fixture is), so this
    checks the actual replacement invariant rather than assuming a no-op."""
    lod2_only = tmp_path / "cs1_lod2_only.gml"
    citysmith.add_lod2(str(DATA), str(lod2_only), keep_source=False)

    embed_out = tmp_path / "cs1_lod2_embed.gml"
    r_embed = citysmith.enhance(str(lod2_only), str(embed_out), levels=(1, 2))
    assert r_embed.lod1_added == 2
    root_embed = etree.parse(str(embed_out)).getroot()
    assert _count(root_embed, "lod2Solid") == 2  # not 4: old rebuilt LOD2 was replaced

    lower_out = tmp_path / "cs1_lod2_lower.gml"
    r_lower = citysmith.enhance(str(lod2_only), str(lower_out), levels=(2,),
                                 keep_source=False)
    root_lower = etree.parse(str(lower_out)).getroot()
    assert _count(root_lower, "lod2Solid") == 2


def test_unsupported_level_rejected(tmp_path):
    out = tmp_path / "cs1_bad.gml"
    with pytest.raises(ValueError, match="unsupported"):
        citysmith.enhance(str(DATA), str(out), levels=(2, 3))


# --- semantics ---------------------------------------------------------------

def test_semantics_classifies_installations(tmp_path):
    from citysmith.semantics import enhance_semantics
    out = tmp_path / "cs1_sem.gml"
    report = enhance_semantics(str(DATA), str(out))
    assert report.classified == {"chimney": 1, "balcony": 1, "unknown": 0}
    assert report.functions_added == 2
    root = etree.parse(str(out)).getroot()
    codes = sorted(f.text for f in root.iter(q(BLDG, "function")))
    assert codes == ["1000", "1030"]   # balcony 1000, chimney 1030
    for inst in root.iter(q(BLDG, "BuildingInstallation")):
        assert inst.get(q(GML, "id")) is not None  # id was added


# --- cityjson ----------------------------------------------------------------

def test_cityjson_structure(tmp_path):
    import json
    from citysmith.cityjson import convert
    multi = tmp_path / "cs1_multi.gml"
    citysmith.enhance(str(DATA), str(multi), levels=(0, 1, 2))
    out = tmp_path / "cs1.city.json"
    report = convert(str(multi), str(out))
    doc = json.loads(out.read_text())
    assert doc["type"] == "CityJSON" and doc["version"] == "1.1"
    assert doc["vertices"] and all(len(v) == 3 for v in doc["vertices"])
    assert len(doc["CityObjects"]) == 2  # Building + BuildingPart
    for co in doc["CityObjects"].values():
        lods = {g["lod"] for g in co["geometry"]}
        assert {"0", "1", "2", "3"} <= lods
        solids = [g for g in co["geometry"] if g["type"] == "Solid"]
        assert all("semantics" in g for g in solids)
    assert report.vertices == len(doc["vertices"])


def test_extrude_prism_outward_normals():
    from citysmith.geometry import extrude_prism, shell_stats
    # square footprint given clockwise on purpose; must be reoriented
    fp = [(0, 0), (0, 10), (10, 10), (10, 0), (0, 0)]
    faces = extrude_prism(fp, 0.0, 3.0)

    def nz(ring):
        loop = ring[:-1] if ring[0] == ring[-1] else ring
        return sum((loop[i][0] - loop[(i + 1) % len(loop)][0]) *
                   (loop[i][1] + loop[(i + 1) % len(loop)][1]) for i in range(len(loop)))

    assert nz(faces[0]) < 0   # bottom normal points down
    assert nz(faces[1]) > 0   # top (roof) normal points up
    assert shell_stats(faces)["closed"] is True


# --- citydoctor bridge (parser is tested without needing a real install) -----

def test_citydoctor_parses_sample_report():
    from citysmith.citydoctor import _parse_report
    sample = Path(__file__).parent / "CS1_multiLOD.citydoctor.xml"
    report = _parse_report(sample)
    assert report.num_buildings == 1
    assert report.num_error_buildings == 1
    assert report.error_counts == {
        "GE_P_ORIENTATION_RINGS_SAME": 6,
        "GE_S_NOT_CLOSED": 3,
        "SE_BS_UNFRAGMENTED": 3,
        "GE_S_MULTIPLE_CONNECTED_COMPONENTS": 1,
        "GE_S_NON_MANIFOLD_VERTEX": 1,
        "GE_S_NON_MANIFOLD_EDGE": 1,
    }
    assert report.total_errors == 15


def test_citydoctor_not_found_without_home(tmp_path, monkeypatch):
    from citysmith.citydoctor import locate_citydoctor, CityDoctorNotFound
    monkeypatch.delenv("CITYSMITH_CITYDOCTOR_HOME", raising=False)
    with pytest.raises(CityDoctorNotFound):
        locate_citydoctor(None)


def test_citydoctor_not_found_for_bad_path(tmp_path):
    from citysmith.citydoctor import locate_citydoctor, CityDoctorNotFound
    with pytest.raises(CityDoctorNotFound):
        locate_citydoctor(str(tmp_path))  # empty dir, no app/*.jar


def test_citydoctor_locate_resolves_relative_path(tmp_path, monkeypatch):
    from citysmith.citydoctor import locate_citydoctor
    (tmp_path / "app").mkdir()
    (tmp_path / "app" / "CityDoctorValidation-9.9.9.jar").write_bytes(b"")
    monkeypatch.chdir(tmp_path.parent)
    relative = tmp_path.name  # a relative path, not absolute
    home = locate_citydoctor(relative)
    assert home.is_absolute()
    assert home == tmp_path.resolve()


def test_validate_cli_accepts_pdf_flag():
    """--pdf is optional (None when omitted) and threaded through to
    citydoctor.validate(); a real CityDoctor2 install isn't available in the
    test environment, so this only checks the argument wiring, not an actual
    PDF render (verified manually, see CHANGELOG)."""
    from citysmith.cli import build_parser
    parser = build_parser()
    args = parser.parse_args(["validate", "in.gml"])
    assert args.pdf is None
    args = parser.parse_args(["validate", "in.gml", "--pdf", "out.pdf"])
    assert args.pdf == "out.pdf"
