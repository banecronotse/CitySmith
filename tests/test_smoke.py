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
