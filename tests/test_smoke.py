"""Tests for the LOD3 to LOD2 engine and geometry helpers."""

from collections import Counter
from pathlib import Path

import pytest
from lxml import etree

import citysmith
from citysmith.citygml import GML, BLDG, q
from citysmith.geometry import is_closed_shell, shell_stats, parse_pos_list

DATA = Path(__file__).parent / "lod" / "CS1_lod3.gml"


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


def _window_wall(wall_poly_id, wall_pts, window_poly_id, window_pts):
    """A WallSurface (no gml:id of its own) with one Window opening (no
    gml:id either), for readable-id-assignment tests."""
    return (
        '<bldg:boundedBy><bldg:WallSurface><bldg:lod3MultiSurface>'
        f'<gml:MultiSurface>{_poly(wall_poly_id, wall_pts)}</gml:MultiSurface>'
        '</bldg:lod3MultiSurface>'
        '<bldg:opening><bldg:Window><bldg:lod3MultiSurface>'
        f'<gml:MultiSurface>{_poly(window_poly_id, window_pts)}</gml:MultiSurface>'
        '</bldg:lod3MultiSurface></bldg:Window></bldg:opening>'
        '</bldg:WallSurface></bldg:boundedBy>'
    )


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
    src = Path(__file__).parent / "semantics" / "CS1_lod3_enhanced.gml"
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


def test_semantics_assigns_readable_ids_anchored_to_building(tmp_path):
    """Wall/Window ids with no id of their own get <building>_<type>_000N,
    not an opaque UUID; Window is a real gap this fixture exercises (it
    wasn't in _ID_REQUIRED before this scheme existed)."""
    from citysmith.semantics import enhance_semantics
    wall_pts = [(0, 0, 0), (1, 0, 0), (1, 0, 5), (0, 0, 5), (0, 0, 0)]
    win_pts = [(0.2, 0, 1), (0.5, 0, 1), (0.5, 0, 3), (0.2, 0, 3), (0.2, 0, 1)]
    src = _write_gml(tmp_path, "readable.gml", _building("MYBLDG", [
        _window_wall("wp", wall_pts, "wip", win_pts),
    ]))
    out = tmp_path / "readable_sem.gml"
    report = enhance_semantics(str(src), str(out))
    root = etree.parse(str(out)).getroot()
    wall = root.find(f".//{q(BLDG, 'WallSurface')}")
    window = root.find(f".//{q(BLDG, 'Window')}")
    assert wall.get(q(GML, "id")) == "MYBLDG_wall_0001"
    assert window.get(q(GML, "id")) == "MYBLDG_window_0001"
    assert report.ids_added == 2
    assert report.ids_overwritten == 0
    assert report.no_anchor_ids == []


def test_semantics_overwrite_ids_flag(tmp_path):
    """Default run leaves already-set ids alone (byte-identical re-run);
    --overwrite-ids replaces them, still deterministically (same document
    order -> same ids both times it's re-run with the flag)."""
    from citysmith.semantics import enhance_semantics
    wall_pts = [(0, 0, 0), (1, 0, 0), (1, 0, 5), (0, 0, 5), (0, 0, 0)]
    win_pts = [(0.2, 0, 1), (0.5, 0, 1), (0.5, 0, 3), (0.2, 0, 3), (0.2, 0, 1)]
    src = _write_gml(tmp_path, "readable2.gml", _building("MYBLDG", [
        _window_wall("wp", wall_pts, "wip", win_pts),
    ]))
    once = tmp_path / "once.gml"
    enhance_semantics(str(src), str(once))

    twice_fill = tmp_path / "twice_fill.gml"
    report_fill = enhance_semantics(str(once), str(twice_fill))
    assert report_fill.ids_added == 0
    assert report_fill.ids_overwritten == 0
    assert twice_fill.read_bytes() == once.read_bytes()

    twice_overwrite = tmp_path / "twice_overwrite.gml"
    report_ow = enhance_semantics(str(once), str(twice_overwrite), overwrite_ids=True)
    assert report_ow.ids_overwritten == 2  # wall + window
    assert twice_overwrite.read_bytes() == once.read_bytes()


def test_semantics_no_anchor_falls_back_to_uuid_and_reports(tmp_path):
    """A boundary surface outside any Building/BuildingPart (malformed, but
    source data varies) can't be anchored to a readable id; falls back to
    the old deterministic UUID scheme and is named in the report rather than
    silently accepted or dropped."""
    from citysmith.semantics import enhance_semantics
    wall_pts = [(0, 0, 0), (1, 0, 0), (1, 0, 5), (0, 0, 5), (0, 0, 0)]
    orphan_wall = (
        '<bldg:WallSurface><bldg:lod3MultiSurface>'
        f'<gml:MultiSurface>{_poly("op", wall_pts)}</gml:MultiSurface>'
        '</bldg:lod3MultiSurface></bldg:WallSurface>'
    )
    src = tmp_path / "orphan.gml"
    src.write_text(_GML_HEAD + orphan_wall + "</CityModel>", encoding="utf-8")
    out = tmp_path / "orphan_sem.gml"
    report = enhance_semantics(str(src), str(out))
    assert len(report.no_anchor_ids) == 1

    root = etree.parse(str(out)).getroot()
    wall = root.find(f".//{q(BLDG, 'WallSurface')}")
    assert wall.get(q(GML, "id")) == report.no_anchor_ids[0]
    assert wall.get(q(GML, "id")).startswith("UUID_")


def test_inspect_reports_id_coverage(tmp_path):
    from citysmith.core import inspect
    wall_pts = [(0, 0, 0), (1, 0, 0), (1, 0, 5), (0, 0, 5), (0, 0, 0)]
    win_pts = [(0.2, 0, 1), (0.5, 0, 1), (0.5, 0, 3), (0.2, 0, 3), (0.2, 0, 1)]
    src = _write_gml(tmp_path, "inspect_ids.gml", _building("MYBLDG", [
        _window_wall("wp", wall_pts, "wip", win_pts),
        _surface("GroundSurface", "g", [_poly("gA", _square(0, 0, 10, 0.0))]),
    ]))
    report = inspect(str(src))
    assert report.id_coverage["WallSurface"] == {"total": 1, "missing": 1}
    assert report.id_coverage["Window"] == {"total": 1, "missing": 1}
    assert report.id_coverage["GroundSurface"] == {"total": 1, "missing": 0}
    assert report.id_coverage["Building"] == {"total": 1, "missing": 0}


def test_semantics_cli_accepts_overwrite_ids_flag():
    from citysmith.cli import build_parser
    parser = build_parser()
    args = parser.parse_args(["semantics", "in.gml"])
    assert args.overwrite_ids is False
    args = parser.parse_args(["semantics", "in.gml", "--overwrite-ids"])
    assert args.overwrite_ids is True


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
    sample = Path(__file__).parent / "validate" / "CS1_multiLOD.citydoctor.xml"
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


# --- merge ---------------------------------------------------------------------

_MERGE_HEAD = (
    '<?xml version="1.0" encoding="UTF-8"?>\n'
    '<CityModel xmlns="http://www.opengis.net/citygml/2.0" '
    'xmlns:gml="http://www.opengis.net/gml" '
    'xmlns:xlink="http://www.w3.org/1999/xlink" '
    'xmlns:app="http://www.opengis.net/citygml/appearance/2.0" '
    'xmlns:bldg="http://www.opengis.net/citygml/building/2.0">'
    '<gml:boundedBy><gml:Envelope srsName="{srs}" srsDimension="3">'
    '<gml:lowerCorner>0 0 0</gml:lowerCorner><gml:upperCorner>1 1 1</gml:upperCorner>'
    '</gml:Envelope></gml:boundedBy>'
)


def _write_srs_gml(path, srs, *members):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(_MERGE_HEAD.format(srs=srs) + "".join(members) + "</CityModel>",
                    encoding="utf-8")
    return path


def _ground_building(bid, poly_id, x0):
    return _building(bid, [_surface("GroundSurface", f"{bid}_g",
                                    [_poly(poly_id, _square(x0, 0, 5, 0.0))])])


def test_merge_combines_inputs_and_recomputes_envelope(tmp_path):
    from citysmith.merge import merge
    a = _write_srs_gml(tmp_path / "a.gml", "EPSG:32634", _ground_building("A1", "pa", 0))
    b = _write_srs_gml(tmp_path / "b.gml", "urn:ogc:def:crs:EPSG::32634",
                       _ground_building("B1", "pb", 100))
    out = tmp_path / "ab.gml"
    report = merge([str(a), str(b)], str(out))
    assert report.members_written == 2
    assert report.srs_name == "EPSG:32634"
    assert report.duplicate_ids_skipped == []
    assert report.possible_duplicates == []

    root = etree.parse(str(out)).getroot()
    assert [b.get(q(GML, "id")) for b in root.iter(q(BLDG, "Building"))] == ["A1", "B1"]
    env = root.find(q(GML, "boundedBy") + "/" + q(GML, "Envelope"))
    assert env.get("srsName") == "EPSG:32634"
    assert env.find(q(GML, "lowerCorner")).text == "0.0 0.0 0.0"
    assert env.find(q(GML, "upperCorner")).text == "105.0 5.0 0.0"


def test_merge_refuses_different_crs(tmp_path):
    from citysmith.merge import merge, MergeError
    a = _write_srs_gml(tmp_path / "a.gml", "EPSG:32634", _ground_building("A1", "pa", 0))
    b = _write_srs_gml(tmp_path / "b.gml", "EPSG:25832", _ground_building("B1", "pb", 100))
    with pytest.raises(MergeError, match="different coordinate reference systems"):
        merge([str(a), str(b)], str(tmp_path / "ab.gml"))
    assert not (tmp_path / "ab.gml").exists()


def test_merge_duplicate_feature_id_skipped_or_renamed(tmp_path):
    from citysmith.merge import merge
    a = _write_srs_gml(tmp_path / "a.gml", "EPSG:32634", _ground_building("SAME", "pa", 0))
    b = _write_srs_gml(tmp_path / "b.gml", "EPSG:32634", _ground_building("SAME", "pb", 50))

    report = merge([str(a), str(b)], str(tmp_path / "skip.gml"))
    assert report.duplicate_ids_skipped == ["SAME"]
    assert report.members_written == 1

    report = merge([str(a), str(b)], str(tmp_path / "rename.gml"), on_duplicate_id="rename")
    assert report.duplicate_ids_renamed == {"SAME": "m2_SAME"}
    root = etree.parse(str(tmp_path / "rename.gml")).getroot()
    assert [x.get(q(GML, "id")) for x in root.iter(q(BLDG, "Building"))] == [
        "SAME", "m2_SAME"]
    ids = _ids(root)
    assert len(ids) == len(set(ids))


def test_merge_renames_clashing_inner_ids_and_their_references(tmp_path):
    """Two different buildings that happen to reuse a polygon id: the
    incoming one is renamed, and the xlink reference and texture target in
    the same file follow it."""
    from citysmith.merge import merge
    a = _write_srs_gml(tmp_path / "a.gml", "EPSG:32634", _ground_building("A1", "p1", 0))
    b_member = (
        '<cityObjectMember><bldg:Building gml:id="B1">'
        '<bldg:lod3Solid><gml:Solid><gml:exterior><gml:CompositeSurface>'
        '<gml:surfaceMember xlink:href="#p1"/>'
        '</gml:CompositeSurface></gml:exterior></gml:Solid></bldg:lod3Solid>'
        + _surface("GroundSurface", "B1_g", [_poly("p1", _square(50, 0, 5, 0.0))])
        + '</bldg:Building></cityObjectMember>'
        '<app:appearanceMember><app:Appearance><app:surfaceDataMember>'
        '<app:ParameterizedTexture><app:imageURI>tex/b.jpg</app:imageURI>'
        '<app:target uri="#p1"/></app:ParameterizedTexture>'
        '</app:surfaceDataMember></app:Appearance></app:appearanceMember>'
    )
    b = _write_srs_gml(tmp_path / "b.gml", "EPSG:32634", b_member)
    out = tmp_path / "ab.gml"
    report = merge([str(a), str(b)], str(out))
    assert report.ids_renamed == 1
    assert report.id_renames[str(b)] == {"p1": "m2_p1"}

    root = etree.parse(str(out)).getroot()
    ids = _ids(root)
    assert len(ids) == len(set(ids))
    assert "p1" in ids and "m2_p1" in ids
    hrefs = [e.get("{http://www.w3.org/1999/xlink}href") for e in root.iter()
             if e.get("{http://www.w3.org/1999/xlink}href")]
    assert hrefs == ["#m2_p1"]
    target = root.find(".//{http://www.opengis.net/citygml/appearance/2.0}target")
    assert target.get("uri") == "#m2_p1"


def test_merge_reports_overlapping_features_without_removing_them(tmp_path):
    from citysmith.merge import merge
    a = _write_srs_gml(tmp_path / "a.gml", "EPSG:32634", _ground_building("A1", "pa", 0))
    b = _write_srs_gml(tmp_path / "b.gml", "EPSG:32634", _ground_building("B9", "pb", 0.2))
    report = merge([str(a), str(b)], str(tmp_path / "ab.gml"))
    assert report.members_written == 2
    assert len(report.possible_duplicates) == 1
    dup = report.possible_duplicates[0]
    assert (dup["id"], dup["overlaps_id"]) == ("B9", "A1")
    assert dup["bbox_overlap"] == 0.923


def test_merge_rewrites_relative_texture_paths_for_output_folder(tmp_path):
    from citysmith.merge import merge
    a = _write_srs_gml(tmp_path / "a.gml", "EPSG:32634", _ground_building("A1", "pa", 0))
    b = _write_srs_gml(
        tmp_path / "part_b" / "b.gml", "EPSG:32634",
        _ground_building("B1", "pb", 50),
        '<app:appearanceMember><app:Appearance><app:surfaceDataMember>'
        '<app:ParameterizedTexture><app:imageURI>tex/b.jpg</app:imageURI>'
        '<app:target uri="#pb"/></app:ParameterizedTexture>'
        '</app:surfaceDataMember></app:Appearance></app:appearanceMember>')
    out = tmp_path / "ab.gml"
    report = merge([str(a), str(b)], str(out))
    assert report.texture_paths_rewritten == 1
    assert len(report.texture_files_missing) == 1
    root = etree.parse(str(out)).getroot()
    uri = root.find(".//{http://www.opengis.net/citygml/appearance/2.0}imageURI").text
    assert uri == "part_b/tex/b.jpg"


def test_cli_merge_parses_inputs():
    from citysmith.cli import build_parser
    args = build_parser().parse_args(["merge", "a.gml", "b.gml", "c.gml", "-o", "m.gml"])
    assert args.inputs == ["a.gml", "b.gml", "c.gml"]
    assert args.on_duplicate_id == "skip"


# --- LOD1 from measuredHeight ----------------------------------------------------

def _box_building(bid, measured=None, uom="m", part=None):
    """A 10 x 10 m building, ground at z=0, flat roof at z=10, optionally with
    a bldg:measuredHeight and one nested BuildingPart."""
    mh = (f'<bldg:measuredHeight uom="{uom}">{measured}</bldg:measuredHeight>'
          if measured is not None else "")
    return (f'<cityObjectMember><bldg:Building gml:id="{bid}">{mh}'
            + _surface("GroundSurface", f"{bid}_g", [_poly(f"{bid}_ga", _square(0, 0, 10, 0.0))])
            + _surface("RoofSurface", f"{bid}_r", [_poly(f"{bid}_ra", _square(0, 0, 10, 10.0))])
            + (part or "") + '</bldg:Building></cityObjectMember>')


def _lod1_tops(path):
    root = etree.parse(str(path)).getroot()
    tops = {}
    for feat in root.iter(q(BLDG, "Building"), q(BLDG, "BuildingPart")):
        solid = feat.find(q(BLDG, "lod1Solid"))
        if solid is None:
            continue
        zs = [z for pl in solid.iter(q(GML, "posList")) for _, _, z in parse_pos_list(pl.text)]
        tops[feat.get(q(GML, "id"))] = max(zs)
    return tops


def test_lod1_measured_height_sets_block_top(tmp_path):
    src = _write_gml(tmp_path, "m.gml", _box_building("B1", measured=10.5))
    out = tmp_path / "m_lod1.gml"
    report = citysmith.enhance(str(src), str(out), levels=(1,), lod1_height="measured")
    assert report.lod1_measured_used == 1
    assert report.lod1_measured_missing_ids == []
    assert report.lod1_measured_mismatch == []
    assert _lod1_tops(out) == {"B1": 10.5}


def test_lod1_measured_height_missing_falls_back_to_average(tmp_path):
    src = _write_gml(tmp_path, "m.gml", _box_building("NOMH"),
                     _box_building("BADUOM", measured=5, uom="furlong"))
    out = tmp_path / "m_lod1.gml"
    report = citysmith.enhance(str(src), str(out), levels=(1,), lod1_height="measured")
    assert report.lod1_measured_used == 0
    assert report.lod1_measured_missing_ids == ["NOMH", "BADUOM"]
    assert _lod1_tops(out) == {"NOMH": 10.0, "BADUOM": 10.0}


def test_lod1_measured_height_in_feet_and_mismatch_reported(tmp_path):
    src = _write_gml(tmp_path, "m.gml", _box_building("FT", measured=50, uom="ft"))
    out = tmp_path / "m_lod1.gml"
    report = citysmith.enhance(str(src), str(out), levels=(1,), lod1_height="measured")
    assert _lod1_tops(out) == {"FT": pytest.approx(15.24)}
    assert report.lod1_measured_mismatch == [
        {"id": "FT", "measured_m": 15.24, "geometric_m": 10.0}]


def test_lod1_measured_height_not_inherited_by_building_part(tmp_path):
    part = ('<bldg:consistsOfBuildingPart><bldg:BuildingPart gml:id="P1">'
            + _surface("GroundSurface", "P1_g", [_poly("P1_ga", _square(20, 0, 10, 0.0))])
            + _surface("RoofSurface", "P1_r", [_poly("P1_ra", _square(20, 0, 10, 4.0))])
            + '</bldg:BuildingPart></bldg:consistsOfBuildingPart>')
    src = _write_gml(tmp_path, "m.gml", _box_building("B1", measured=12.5, part=part))
    out = tmp_path / "m_lod1.gml"
    report = citysmith.enhance(str(src), str(out), levels=(1,), lod1_height="measured")
    assert report.lod1_measured_missing_ids == ["P1"]
    assert _lod1_tops(out) == {"B1": 12.5, "P1": 4.0}


def test_lod1_default_ignores_measured_height(tmp_path):
    src = _write_gml(tmp_path, "m.gml", _box_building("B1", measured=30))
    out = tmp_path / "m_lod1.gml"
    report = citysmith.enhance(str(src), str(out), levels=(1,))
    assert report.lod1_measured_used == 0
    assert report.lod1_measured_missing_ids == []
    assert _lod1_tops(out) == {"B1": 10.0}


def test_unknown_lod1_height_rejected(tmp_path):
    src = _write_gml(tmp_path, "m.gml", _box_building("B1"))
    with pytest.raises(ValueError, match="unsupported lod1_height"):
        citysmith.enhance(str(src), str(tmp_path / "x.gml"), levels=(1,), lod1_height="tallest")


def test_cli_merge_expands_wildcards_itself(tmp_path):
    """Windows shells pass `part_*.gml` through literally; the CLI expands it."""
    from citysmith.cli import _expand_inputs
    for name in ("part_b.gml", "part_a.gml"):
        (tmp_path / name).write_text("", encoding="utf-8")
    assert _expand_inputs([str(tmp_path / "part_*.gml")]) == [
        str(tmp_path / "part_a.gml"), str(tmp_path / "part_b.gml")]
    assert _expand_inputs(["missing_*.gml"]) == ["missing_*.gml"]


# --- XML schema validation -------------------------------------------------------

TESTS = Path(__file__).parent


@pytest.mark.parametrize("fixture", sorted(
    str(p.relative_to(TESTS)) for p in TESTS.rglob("*.gml")))
def test_every_committed_fixture_is_schema_valid(fixture):
    from citysmith.schema import validate_schema
    report = validate_schema(str(TESTS / fixture))
    assert report.checked and report.citygml_version == "2.0"
    assert report.valid, report.messages[:3]


def _schema_ok(path):
    from citysmith.schema import validate_schema
    report = validate_schema(str(path))
    assert report.valid, report.messages[:3]


@pytest.mark.parametrize("levels,keep_source,height", [
    ((0,), False, "average"), ((1,), False, "eave"), ((2,), True, "average"),
    ((2,), False, "average"), ((0, 1, 2), True, "ridge"), ((1,), True, "measured"),
])
def test_lod_output_is_schema_valid(tmp_path, levels, keep_source, height):
    """Every LOD combination CitySmith writes must be valid CityGML 2.0:
    the new LODs have a fixed place in the schema's element order."""
    out = tmp_path / "out.gml"
    citysmith.enhance(str(DATA), str(out), levels=levels, keep_source=keep_source,
                      lod1_height=height)
    _schema_ok(out)


@pytest.mark.parametrize("flags", [{}, {"add_ids": False}, {"classify": False},
                                   {"aggregate": False}])
def test_semantics_output_is_schema_valid(tmp_path, flags):
    from citysmith.semantics import enhance_semantics
    out = tmp_path / "sem.gml"
    enhance_semantics(str(DATA), str(out), **flags)
    _schema_ok(out)


def test_full_pipeline_output_is_schema_valid(tmp_path):
    """semantics, then lod, then crop and merge: the order the README uses."""
    from citysmith.semantics import enhance_semantics
    from citysmith.crop import crop
    from citysmith.merge import merge
    sem, multi = tmp_path / "sem.gml", tmp_path / "multi.gml"
    enhance_semantics(str(DATA), str(sem))
    citysmith.enhance(str(sem), str(multi), levels=(0, 1, 2))
    _schema_ok(multi)
    cropped = tmp_path / "crop.gml"
    crop(str(multi), str(cropped), ["CS1"])
    _schema_ok(cropped)
    merged = tmp_path / "merged.gml"
    # Same building twice, kept under renamed ids: exercises id renaming too.
    merge([str(cropped), str(TESTS / "lod" / "CS1_lod2.gml")], str(merged),
          on_duplicate_id="rename")
    _schema_ok(merged)


def test_schema_order_fix_moves_only_building_properties(tmp_path):
    """An lod2Solid placed after the boundary surfaces is moved before them;
    generic attributes and the rest of the file stay where they are."""
    from citysmith.citygml import sort_building_properties
    src = _write_gml(tmp_path, "o.gml", (
        '<cityObjectMember><bldg:Building gml:id="B">'
        '<bldg:function>1000</bldg:function>'
        + _surface("GroundSurface", "g", [_poly("ga", _square(0, 0, 5, 0.0))])
        + '<bldg:lod2Solid/><bldg:lod1Solid/></bldg:Building></cityObjectMember>'))
    root = etree.parse(str(src)).getroot()
    assert sort_building_properties(root) == 1
    building = root.find(f".//{q(BLDG, 'Building')}")
    assert [etree.QName(c).localname for c in building] == [
        "function", "lod1Solid", "lod2Solid", "boundedBy"]
    assert sort_building_properties(root) == 0  # already in order: untouched


def test_schema_errors_reported_with_line_numbers(tmp_path):
    from citysmith.schema import validate_schema
    bad = _write_gml(tmp_path, "bad.gml", (
        '<cityObjectMember><bldg:Building gml:id="B">\n'
        + _surface("GroundSurface", "g", [_poly("ga", _square(0, 0, 5, 0.0))])
        + '\n<bldg:function>1000</bldg:function>\n'
        '<bldg:notAProperty/></bldg:Building></cityObjectMember>'))
    report = validate_schema(str(bad))
    assert report.checked and not report.valid
    assert report.error_count >= 1
    assert all(m["line"] > 0 for m in report.messages)
    assert "function" in report.messages[0]["message"]


def test_schema_unknown_namespace_reported_not_fatal(tmp_path):
    from citysmith.schema import validate_schema
    path = tmp_path / "ade.gml"
    path.write_text(
        _GML_HEAD.replace('xmlns:bldg=', 'xmlns:x="urn:example:ade" xmlns:bldg=')
        + '<cityObjectMember><bldg:Building gml:id="B"><x:energy>5</x:energy>'
        '</bldg:Building></cityObjectMember></CityModel>', encoding="utf-8")
    report = validate_schema(str(path))
    assert report.unknown_namespaces == ["urn:example:ade"]
    assert not report.valid
    assert "--schema-file" in report.note


def test_schema_other_citygml_version_not_checked(tmp_path):
    from citysmith.schema import validate_schema
    path = tmp_path / "v1.gml"
    path.write_text('<CityModel xmlns="http://www.opengis.net/citygml/1.0"/>',
                    encoding="utf-8")
    report = validate_schema(str(path))
    assert report.citygml_version == "1.0"
    assert not report.checked
    assert "only CityGML 2.0" in report.note


def test_schema_not_well_formed_reported(tmp_path):
    from citysmith.schema import validate_schema
    path = tmp_path / "broken.gml"
    path.write_text("<CityModel><unclosed></CityModel>", encoding="utf-8")
    report = validate_schema(str(path))
    assert report.checked and not report.valid
    assert "not well-formed" in report.messages[0]["message"]


def test_cli_validate_schema_only_needs_no_citydoctor(tmp_path, monkeypatch, capsys):
    from citysmith.cli import main
    monkeypatch.delenv("CITYSMITH_CITYDOCTOR_HOME", raising=False)
    report = tmp_path / "r.json"
    assert main(["validate", str(DATA), "--schema-only", "--report", str(report)]) == 0
    assert "schema              : valid" in capsys.readouterr().out
    import json
    assert json.loads(report.read_text())["schema"]["valid"] is True
