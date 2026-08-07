"""Command-line interface for citysmith.

Subcommands:
  inspect    read-only preflight: what geometry CitySmith found and what
             each capability can/can't do with it, without writing anything
  crop       extract a named subset of buildings by gml:id out of a larger file
  lod        derive and embed lower LODs (LOD0/1/2) from an LOD3 or LOD2 source
  semantics  apply the easy-tier semantic fixes (ids, function, type, aggregate)
  convert    export CityGML to CityJSON 1.1
  validate   run the CityDoctor2 external validator and report the results
"""

import argparse
import json
import sys

from . import __version__
from .core import enhance, inspect as inspect_source
from .crop import crop as crop_source
from .semantics import enhance_semantics
from .cityjson import convert
from .citydoctor import validate, CityDoctorNotFound, CityDoctorError


def _default_out(path, suffix, ext=None):
    base = path.rsplit(".", 1)[0]
    return f"{base}{suffix}.{ext}" if ext else f"{base}{suffix}.gml"


def _write_report(path, data):
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(data, fh, indent=2)
    print(f"report              : {path}")


# --- inspect -------------------------------------------------------------------

_PATTERN_LABEL = {
    "solid": "aggregating Solid (CityGRID/3DCityDB style)",
    "surfaces": "per-surface MultiSurface, no Solid (e.g. SketchUp/FME-exported data)",
}


def _cmd_inspect(args) -> int:
    print(f"reading  : {args.input}")
    report = inspect_source(args.input, limit=args.limit)
    print(f"features found      : {report.features}")
    print(f"  usable, LOD3       : {report.source_lod3}")
    print(f"  usable, LOD2       : {report.source_lod2}")
    print(f"  unclassified       : {report.source_unclassified} "
          "(geometry found, but no wall/roof/ground distinction: not processable)")
    print(f"  no geometry found  : {report.source_none}")
    print(f"  pattern: solid     : {report.pattern_solid}")
    print(f"  pattern: surfaces  : {report.pattern_surfaces}")
    print(f"  BuildingInstallations: {report.installations}")
    print()
    print("gml:id coverage (mandatory per SIG3D Modeling Guide Part 2):")
    missing_types = []
    for name, counts in sorted(report.id_coverage.items()):
        total, missing = counts["total"], counts["missing"]
        print(f"  {name:22}: {total - missing}/{total} have gml:id")
        if missing:
            missing_types.append(name)
    if missing_types:
        print(f"  -> citysmith semantics will fill these in (run without --no-ids): "
              f"{', '.join(missing_types)}")
    print()
    print("What each capability will do with this file:")
    usable = report.source_lod3 + report.source_lod2
    print(f"  lod (LOD1/LOD0)    : {'yes, ' + str(usable) + ' feature(s)' if usable else 'no usable source'}")
    print(f"  lod (LOD2)         : {'yes, ' + str(report.source_lod3) + ' feature(s)' if report.source_lod3 else 'no'} "
          "(needs an LOD3 source; LOD2-sourced features have nothing to derive there)")
    print(f"  semantics          : {'yes, ' + str(report.installations) + ' installation(s) to classify' if report.installations else 'no BuildingInstallations found'}")
    print("  convert            : yes, whatever geometry/semantics is already present converts to CityJSON")
    print("  validate           : yes, native + CityDoctor2 both work on the raw file regardless of pattern")
    if report.source_unclassified:
        print()
        print(f"Note: {report.source_unclassified} feature(s) have geometry CitySmith can see but not use, "
              "a flat MultiSurface with no boundedBy thematic classification, so there's no way to tell "
              "which polygon is the roof, wall or ground. Not a bug, just not enough information in the "
              "source data for this tool's approach.")
    if args.report:
        _write_report(args.report, report.to_dict())
    return 0


# --- crop ----------------------------------------------------------------------

def _read_ids(args) -> list[str]:
    ids = []
    if args.ids:
        ids.extend(x.strip() for x in args.ids.split(",") if x.strip())
    if args.ids_file:
        with open(args.ids_file, encoding="utf-8") as fh:
            ids.extend(line.strip() for line in fh if line.strip())
    return ids


def _cmd_crop(args) -> int:
    ids = _read_ids(args)
    if not ids:
        print("error: no ids given, use --ids and/or --ids-file", file=sys.stderr)
        return 2
    out = args.output or _default_out(args.input, "_crop")
    print(f"reading  : {args.input}")
    report = crop_source(args.input, out, ids)
    print(f"written  : {out}")
    print(f"requested: {report.requested}   kept: {report.kept}")
    if report.missing_ids:
        print(f"  not found in source: {len(report.missing_ids)}")
        for fid in report.missing_ids[:10]:
            print(f"    {fid}")
        if len(report.missing_ids) > 10:
            print(f"    ... and {len(report.missing_ids) - 10} more")
    if report.appearance_pruned:
        print(f"  appearance references pruned (pointed at removed geometry): "
              f"{report.appearance_pruned}")
    if args.report:
        _write_report(args.report, report.to_dict())
    return 0 if not report.missing_ids else 1


# --- lod ---------------------------------------------------------------------

def _cmd_lod(args) -> int:
    levels = tuple(int(x) for x in args.levels.split(",") if x.strip() != "")
    out = args.output or _default_out(args.input, "_lod")
    print(f"reading  : {args.input}")
    try:
        report = enhance(args.input, out, levels=levels, keep_source=not args.lower_only,
                         lod1_height=args.lod1_height, limit=args.limit)
    except ValueError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    print(f"written  : {out}")
    print(f"mode                : {report.mode}   levels: {list(report.levels)}")
    print(f"features processed  : {report.features}  "
          f"(sourced from LOD3: {report.source_lod3}, from LOD2: {report.source_lod2}, "
          f"no usable source: {report.source_none})")
    if 2 in levels:
        print(f"  walls de-holed    : {report.walls_deholed} "
              f"(holes filled: {report.interior_rings_removed})")
        if report.lod2_already_present:
            print(f"  LOD2 already present, nothing to derive: {report.lod2_already_present} "
                  "feature(s) (LOD2 can only be derived from an LOD3 source)")
        print("  LOD2 watertightness (report-only; reflects source LOD3 quality):")
        for k in ("watertight", "1-4_open_edges", "5-20_open_edges", "20+_open_edges"):
            print(f"    {k:16}: {report.quality_buckets[k]}")
    if 1 in levels:
        print(f"  LOD1 added/skipped: {report.lod1_added}/{report.lod1_skipped}")
        if report.lod1_composite:
            print(f"    of which multi-part (footprint split by a passage): "
                  f"{report.lod1_composite}")
        if report.lod1_pieces_skipped:
            print(f"    ground pieces too small to extrude: {report.lod1_pieces_skipped}")
    if 0 in levels:
        print(f"  LOD0 added/skipped: {report.lod0_added}/{report.lod0_skipped}")
    if report.kept_empty_ids:
        print(f"  kept without geometry: {len(report.kept_empty_ids)} "
              "(reported, never dropped)")
        for fid in report.kept_empty_ids[:10]:
            print(f"    {fid}")
        if len(report.kept_empty_ids) > 10:
            print(f"    ... and {len(report.kept_empty_ids) - 10} more")
    if args.report:
        _write_report(args.report, report.to_dict())
    return 0


# --- semantics ---------------------------------------------------------------

def _cmd_semantics(args) -> int:
    out = args.output or _default_out(args.input, "_sem")
    print(f"reading  : {args.input}")
    report = enhance_semantics(args.input, out, add_ids=not args.no_ids,
                               classify=not args.no_classify, aggregate=not args.no_aggregate,
                               overwrite_ids=args.overwrite_ids)
    print(f"written  : {out}")
    print(f"ids added           : {report.ids_added}")
    if report.ids_overwritten:
        print(f"ids overwritten     : {report.ids_overwritten}")
    print(f"functions added     : {report.functions_added}")
    print(f"type attrs added    : {report.types_added}")
    print(f"lod3Geometry added  : {report.lod3geometry_added}")
    c = report.classified
    print(f"classified          : {sum(c.values())} total "
          f"({c['balcony']} balcony, {c['chimney']} chimney, {c['unknown']} unknown)")
    if report.no_anchor_ids:
        print(f"  ids with no owning Building/BuildingPart found, fell back to the old "
              f"UUID scheme: {len(report.no_anchor_ids)}")
        for fid in report.no_anchor_ids[:10]:
            print(f"    {fid}")
        if len(report.no_anchor_ids) > 10:
            print(f"    ... and {len(report.no_anchor_ids) - 10} more")
    if args.report:
        _write_report(args.report, report.to_dict())
    return 0


# --- convert -----------------------------------------------------------------

def _cmd_convert(args) -> int:
    out = args.output or _default_out(args.input, "", ext="city.json")
    print(f"reading  : {args.input}")
    report = convert(args.input, out, precision=args.precision)
    print(f"written  : {out}")
    print(f"city objects        : {report.city_objects} "
          f"({report.buildings} buildings, {report.parts} parts)")
    print(f"geometries          : {report.geometries}  by lod: {report.lods}")
    print(f"vertices            : {report.vertices}")
    if args.report:
        _write_report(args.report, report.to_dict())
    return 0


# --- validate ------------------------------------------------------------------

def _cmd_validate(args) -> int:
    print(f"reading  : {args.input}")
    try:
        report = validate(args.input, citydoctor_home=args.citydoctor_home,
                          config_path=args.config, timeout=args.timeout,
                          pdf_path=args.pdf)
    except CityDoctorNotFound as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    except CityDoctorError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1

    print(f"xml report          : {report.xml_report_path}")
    if report.pdf_report_path:
        print(f"pdf report          : {report.pdf_report_path}")
    print(f"buildings           : {report.num_buildings}")
    print(f"buildings w/ errors : {report.num_error_buildings}")
    print(f"total errors        : {report.total_errors}")
    if report.error_counts:
        print("error breakdown:")
        for name, count in sorted(report.error_counts.items(), key=lambda kv: -kv[1]):
            print(f"    {name:44}: {count}")
    if args.report:
        _write_report(args.report, report.to_dict())
    return 0 if report.num_error_buildings == 0 else 1


# Every subcommand's --help ends with a runnable example, on the theory that
# a copy-pasteable command teaches faster than a flag list on its own.
# RawDescriptionHelpFormatter is required for the epilog's line breaks to
# survive; argparse otherwise re-wraps and collapses them into one paragraph.
_REPORT_HELP = "also write the full machine-readable result as JSON to this path"


def _sub(sub, name, *, help, epilog, description=None):
    return sub.add_parser(
        name, help=help, epilog=epilog, description=description or help,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="citysmith", description="A CityGML enhancer.")
    p.add_argument("--version", action="version", version=f"citysmith {__version__}")
    sub = p.add_subparsers(dest="command", required=True)

    insp = _sub(sub, "inspect",
                help="read-only preflight: what's in this file and what each capability "
                     "can do with it",
                epilog="example:\n  citysmith inspect unfamiliar_city_model.gml")
    insp.add_argument("input", help="source CityGML file")
    insp.add_argument("--limit", type=int, default=None,
                       help="only look at the first N features (quick check on a huge file)")
    insp.add_argument("--report", help=_REPORT_HELP)
    insp.set_defaults(func=_cmd_inspect)

    crop = _sub(sub, "crop",
                help="extract a named subset of buildings by gml:id",
                epilog="examples:\n"
                       "  citysmith crop citywide.gml --ids ID1,ID2,ID3 -o area.gml\n"
                       "  citysmith crop citywide.gml --ids-file wanted_ids.txt -o area.gml")
    crop.add_argument("input", help="source CityGML file")
    crop.add_argument("-o", "--output",
                       help="output file (default: <input>_crop.gml next to the source)")
    crop.add_argument("--ids", help="comma-separated gml:id list to keep")
    crop.add_argument("--ids-file", help="file with one gml:id per line to keep; combined "
                       "with --ids if both are given")
    crop.add_argument("--report", help=_REPORT_HELP)
    crop.set_defaults(func=_cmd_crop)

    lod = _sub(sub, "lod",
               help="derive and embed lower LODs from LOD3 or LOD2 source",
               epilog="examples:\n"
                      "  citysmith lod city_lod3.gml --levels 0,1,2\n"
                      "  citysmith lod city_lod3.gml --levels 2 --lower-only -o city_lod2.gml")
    lod.add_argument("input", help="source CityGML file, LOD3 or LOD2")
    lod.add_argument("-o", "--output",
                      help="output file (default: <input>_lod.gml next to the source)")
    lod.add_argument("--levels", default="2",
                      help="comma list of LODs to derive, any of 0,1,2 (default 2). LOD1/LOD0 "
                           "work from an LOD3 or LOD2 source; LOD2 needs an LOD3 source.")
    lod.add_argument("--lower-only", action="store_true",
                      help="strip the source geometry, keep only the newly derived lower LODs")
    lod.add_argument("--lod1-height", choices=["average", "eave", "ridge"], default="average",
                      help="LOD1 block top, per SIG3D Part 2 sec 2.4: 'average' (default, "
                           "(Min. Eaves + Max. Ridge) / 2), 'eave' (Min. Eaves Height) or "
                           "'ridge' (Max. Ridge Height)")
    lod.add_argument("--limit", type=int, default=None,
                      help="only process the first N features")
    lod.add_argument("--report", help=_REPORT_HELP)
    lod.set_defaults(func=_cmd_lod)

    sem = _sub(sub, "semantics",
               help="apply easy-tier semantic fixes",
               description="Applies three fixes, all ON by default: a plain run with no flags\n"
                           "does all three.\n"
                           "  1) assigns gml:ids to anything missing one (Building, BuildingPart,\n"
                           "     BuildingInstallation, every boundary surface type and every\n"
                           "     Window/Door), readable and anchored to the owning building's own\n"
                           "     id, e.g. JAPR34_wall_0001, JAPR34_window_0007 -- per SIG3D Part 2,\n"
                           "     gml:id is mandatory on all of these, not just Building itself.\n"
                           "     Existing ids (including old opaque UUID-style ones from an\n"
                           "     earlier citysmith version) are left alone unless --overwrite-ids\n"
                           "     is given.\n"
                           "  2) classifies every BuildingInstallation as balcony or chimney\n"
                           "     (an OuterFloorSurface means balcony outright; otherwise, below\n"
                           "     the building's eave height is a balcony, above it a\n"
                           "     chimney/roof structure)\n"
                           "  3) builds the lod3Geometry aggregate element\n"
                           "There is no flag that turns classification 'on'. It already runs\n"
                           "unless you explicitly skip it with --no-classify.",
               epilog="examples:\n"
                      "  citysmith semantics city_lod3.gml -o city_sem.gml --report sem.json\n"
                      "  citysmith semantics city_sem.gml -o city_sem2.gml --overwrite-ids")
    sem.add_argument("input", help="source CityGML file (needs an LOD3 source)")
    sem.add_argument("-o", "--output",
                      help="output file (default: <input>_sem.gml next to the source)")
    sem.add_argument("--no-ids", action="store_true",
                      help="skip assigning gml:ids to features/surfaces that don't have one "
                           "(runs by default)")
    sem.add_argument("--overwrite-ids", action="store_true",
                      help="also replace ids that already exist (including old UUID-style ones) "
                           "with the readable scheme; default is to fill only what's missing and "
                           "leave existing ids untouched")
    sem.add_argument("--no-classify", action="store_true",
                      help="skip balcony/chimney classification of BuildingInstallations "
                           "(runs by default)")
    sem.add_argument("--no-aggregate", action="store_true",
                      help="skip building the lod3Geometry aggregate element (runs by default)")
    sem.add_argument("--report", help=_REPORT_HELP)
    sem.set_defaults(func=_cmd_semantics)

    conv = _sub(sub, "convert",
                help="export CityGML to CityJSON 1.1",
                epilog="example:\n  citysmith convert city_multiLOD.gml -o city.city.json")
    conv.add_argument("input", help="source CityGML file")
    conv.add_argument("-o", "--output",
                       help="output file (default: <input>.city.json next to the source)")
    conv.add_argument("--precision", type=int, default=3, help="coordinate decimals (default 3)")
    conv.add_argument("--report", help=_REPORT_HELP)
    conv.set_defaults(func=_cmd_convert)

    val = _sub(sub, "validate",
               help="run the CityDoctor2 external validator",
               epilog="example:\n"
                      "  citysmith validate city.gml --pdf report.pdf --report report.json")
    val.add_argument("input", help="CityGML file to validate")
    val.add_argument("--citydoctor-home", help="path to an unzipped CityDoctor2 release "
                     "(or set CITYSMITH_CITYDOCTOR_HOME)")
    val.add_argument("--config", help="CityDoctor2 validation plan YAML (default: bundled)")
    val.add_argument("--timeout", type=int, default=600,
                      help="seconds to wait for CityDoctor2 before giving up (default 600)")
    val.add_argument("--report", help=_REPORT_HELP)
    val.add_argument("--pdf", help="also render a human-readable PDF validation report "
                     "at this path (CityDoctor2's own -pdfreport)")
    val.set_defaults(func=_cmd_validate)
    return p


def main(argv=None) -> int:
    args = build_parser().parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
