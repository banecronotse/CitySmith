"""Tests for the LOD3 to LOD2 engine and geometry helpers."""

from collections import Counter
from pathlib import Path

import pytest
from lxml import etree

import citysmith
from citysmith.citygml import GML, BLDG, q
from citysmith.geometry import is_closed_shell, shell_stats, parse_pos_list

DATA = Path(__file__).parent / "data" / "box_lod3.gml"


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


# --- engine: embed mode ------------------------------------------------------

def test_embed_adds_lod2_and_keeps_lod3(tmp_path):
    out = tmp_path / "box_out.gml"
    report = citysmith.add_lod2(str(DATA), str(out))

    assert report.features == 1
    assert report.interior_rings_removed == 1  # the one window
    assert report.quality_buckets["watertight"] == 1  # clean cube stays watertight

    root = etree.parse(str(out)).getroot()
    assert _count(root, "lod3Solid") == 1     # LOD3 preserved
    assert _count(root, "lod2Solid") == 1     # LOD2 added

    # The window hole is filled: no interior ring survives in a LOD2 wall.
    lod2 = [ms for ms in root.iter(q(BLDG, "lod2MultiSurface"))]
    assert lod2, "expected lod2MultiSurface elements"
    assert not any(msf.iter(q(GML, "interior")) and list(msf.iter(q(GML, "interior")))
                   for msf in lod2)

    # Ids stay unique.
    dups = {k: v for k, v in Counter(_ids(root)).items() if v > 1}
    assert dups == {}


def test_lod2_solid_refs_resolve(tmp_path):
    out = tmp_path / "box_out.gml"
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
    out = tmp_path / "box_only.gml"
    citysmith.add_lod2(str(DATA), str(out), keep_lod3=False)
    root = etree.parse(str(out)).getroot()
    assert _count(root, "lod3Solid") == 0
    assert _count(root, "lod3MultiSurface") == 0
    assert _count(root, "lod2Solid") == 1
    # generic attributes survive the strip
    assert any(a.get("name") == "note" for a in root.iter(
        q("http://www.opengis.net/citygml/generics/2.0", "stringAttribute")))


# --- LOD0 / LOD1 -------------------------------------------------------------

def test_lod1_prism_watertight_and_lod0(tmp_path):
    from citysmith.core import _ring_points
    out = tmp_path / "box_multi.gml"
    report = citysmith.enhance(str(DATA), str(out), levels=(0, 1, 2))
    assert report.lod1_added == 1 and report.lod0_added == 1
    root = etree.parse(str(out)).getroot()
    assert _count(root, "lod1Solid") == 1
    assert _count(root, "lod0FootPrint") == 1
    solid = next(root.iter(q(BLDG, "lod1Solid")))
    rings = [_ring_points(p) for p in solid.iter(q(GML, "Polygon"))]
    assert shell_stats(rings)["closed"] is True  # prism watertight by construction
    dups = {k: v for k, v in Counter(_ids(root)).items() if v > 1}
    assert dups == {}


# --- semantics ---------------------------------------------------------------

INST = Path(__file__).parent / "data" / "installation_lod3.gml"


def test_semantics_classifies_chimney(tmp_path):
    from citysmith.semantics import enhance_semantics
    from citysmith.citygml import GEN
    out = tmp_path / "inst_sem.gml"
    report = enhance_semantics(str(INST), str(out))
    assert report.classified["chimney"] == 1
    assert report.functions_added == 1
    assert report.lod3geometry_added == 1
    root = etree.parse(str(out)).getroot()
    assert root.find(f".//{q(BLDG, 'function')}").text == "1030"
    types = [a.get("name") for a in root.iter(q(GEN, "stringAttribute"))]
    assert "type" in types
    inst = next(root.iter(q(BLDG, "BuildingInstallation")))
    assert inst.get(q(GML, "id")) is not None  # id was added


# --- cityjson ----------------------------------------------------------------

def test_cityjson_structure(tmp_path):
    import json
    from citysmith.cityjson import convert
    multi = tmp_path / "box_multi.gml"
    citysmith.enhance(str(DATA), str(multi), levels=(0, 1, 2))
    out = tmp_path / "box.city.json"
    report = convert(str(multi), str(out))
    doc = json.loads(out.read_text())
    assert doc["type"] == "CityJSON" and doc["version"] == "1.1"
    assert doc["vertices"] and all(len(v) == 3 for v in doc["vertices"])
    co = next(iter(doc["CityObjects"].values()))
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


def test_eave_based_balcony_vs_chimney(tmp_path):
    from citysmith.semantics import enhance_semantics
    fixture = Path(__file__).parent / "data" / "balcony_chimney_lod3.gml"
    out = tmp_path / "mix_sem.gml"
    report = enhance_semantics(str(fixture), str(out))
    # one installation below the eave (balcony), one above (chimney)
    assert report.classified["balcony"] == 1
    assert report.classified["chimney"] == 1
    assert report.classified["unknown"] == 0
    root = etree.parse(str(out)).getroot()
    codes = sorted(f.text for f in root.iter(q(BLDG, "function")))
    assert codes == ["1000", "1030"]   # balcony 1000, chimney 1030


# --- citydoctor bridge (parser is tested without needing a real install) -----

def test_citydoctor_parses_sample_report():
    from citysmith.citydoctor import _parse_report
    sample = Path(__file__).parent / "data" / "sample_citydoctor_report.xml"
    report = _parse_report(sample)
    assert report.num_buildings == 1
    assert report.num_error_buildings == 1
    assert report.error_counts == {"GE_P_ORIENTATION_RINGS_SAME": 1}
    assert report.total_errors == 1


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
