"""Command-line interface for citysmith.

Subcommands:
  lod        derive and embed lower LODs (LOD0/1/2) from LOD3
  semantics  apply the easy-tier semantic fixes (ids, function, type, aggregate)
  convert    export CityGML to CityJSON 1.1
  validate   run the CityDoctor2 external validator and report the results
"""

import argparse
import json
import sys

from . import __version__
from .core import enhance
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


# --- lod ---------------------------------------------------------------------

def _cmd_lod(args) -> int:
    levels = tuple(int(x) for x in args.levels.split(",") if x.strip() != "")
    out = args.output or _default_out(args.input, "_lod")
    print(f"reading  : {args.input}")
    report = enhance(args.input, out, levels=levels, keep_lod3=not args.lower_only,
                     lod1_height=args.lod1_height, limit=args.limit)
    print(f"written  : {out}")
    print(f"mode                : {report.mode}   levels: {list(report.levels)}")
    print(f"features processed  : {report.features}")
    if 2 in levels:
        print(f"  walls de-holed    : {report.walls_deholed} "
              f"(holes filled: {report.interior_rings_removed})")
        print("  LOD2 watertightness (report-only; reflects source LOD3 quality):")
        for k in ("watertight", "1-4_open_edges", "5-20_open_edges", "20+_open_edges"):
            print(f"    {k:16}: {report.quality_buckets[k]}")
    if 1 in levels:
        print(f"  LOD1 added/skipped: {report.lod1_added}/{report.lod1_skipped}")
    if 0 in levels:
        print(f"  LOD0 added/skipped: {report.lod0_added}/{report.lod0_skipped}")
    if args.report:
        _write_report(args.report, report.to_dict())
    return 0


# --- semantics ---------------------------------------------------------------

def _cmd_semantics(args) -> int:
    out = args.output or _default_out(args.input, "_sem")
    print(f"reading  : {args.input}")
    report = enhance_semantics(args.input, out, add_ids=not args.no_ids,
                               classify=not args.no_classify, aggregate=not args.no_aggregate)
    print(f"written  : {out}")
    print(f"ids added           : {report.ids_added}")
    print(f"functions added     : {report.functions_added}")
    print(f"type attrs added    : {report.types_added}")
    print(f"lod3Geometry added  : {report.lod3geometry_added}")
    print(f"classified          : {report.classified}")
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
                          config_path=args.config, timeout=args.timeout)
    except CityDoctorNotFound as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    except CityDoctorError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1

    print(f"xml report          : {report.xml_report_path}")
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


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="citysmith", description="A CityGML enhancer.")
    p.add_argument("--version", action="version", version=f"citysmith {__version__}")
    sub = p.add_subparsers(dest="command", required=True)

    lod = sub.add_parser("lod", help="derive and embed lower LODs from LOD3")
    lod.add_argument("input")
    lod.add_argument("-o", "--output")
    lod.add_argument("--levels", default="2", help="comma list of LODs to add, e.g. 0,1,2 (default 2)")
    lod.add_argument("--lower-only", action="store_true", help="strip LOD3, keep only the lower LODs")
    lod.add_argument("--lod1-height", choices=["average", "eave", "ridge"], default="average",
                      help="LOD1 block top, per SIG3D Part 2 sec 2.4: 'average' (default, "
                           "(Min. Eaves + Max. Ridge) / 2), 'eave' (Min. Eaves Height) or "
                           "'ridge' (Max. Ridge Height)")
    lod.add_argument("--limit", type=int, default=None)
    lod.add_argument("--report")
    lod.set_defaults(func=_cmd_lod)

    sem = sub.add_parser("semantics", help="apply easy-tier semantic fixes")
    sem.add_argument("input")
    sem.add_argument("-o", "--output")
    sem.add_argument("--no-ids", action="store_true")
    sem.add_argument("--no-classify", action="store_true")
    sem.add_argument("--no-aggregate", action="store_true")
    sem.add_argument("--report")
    sem.set_defaults(func=_cmd_semantics)

    conv = sub.add_parser("convert", help="export CityGML to CityJSON 1.1")
    conv.add_argument("input")
    conv.add_argument("-o", "--output")
    conv.add_argument("--precision", type=int, default=3, help="coordinate decimals (default 3)")
    conv.add_argument("--report")
    conv.set_defaults(func=_cmd_convert)

    val = sub.add_parser("validate", help="run the CityDoctor2 external validator")
    val.add_argument("input")
    val.add_argument("--citydoctor-home", help="path to an unzipped CityDoctor2 release "
                     "(or set CITYSMITH_CITYDOCTOR_HOME)")
    val.add_argument("--config", help="CityDoctor2 validation plan YAML (default: bundled)")
    val.add_argument("--timeout", type=int, default=600)
    val.add_argument("--report")
    val.set_defaults(func=_cmd_validate)
    return p


def main(argv=None) -> int:
    args = build_parser().parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
