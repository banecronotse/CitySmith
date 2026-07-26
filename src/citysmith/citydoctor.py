"""Bridge to CityDoctor2, an external Java validator for CityGML.

CityDoctor2 (https://transfer.hft-stuttgart.de/gitlab/citydoctor/citydoctor2) is
a mature, independently developed validator covering a much larger check
taxonomy than CitySmith's own quick watertightness report: ring, polygon and
shell-level geometry checks (self-intersection, non-planarity, ring
orientation, non-manifold edges/vertices, connected components) plus a few
semantic checks.

CitySmith does not bundle or build CityDoctor2 (it is a ~100 MB Java
application with its own bundled runtime). Instead this module shells out to a
CityDoctor2 installation the user has downloaded separately, feeding it
CityGML directly, no format conversion needed, and parses its XML report into
a small typed structure.

Installation
------------
Download a prebuilt release ("CityDoctorValidation-<version>-<os>.zip") from
https://transfer.hft-stuttgart.de/gitlab/citydoctor/citydoctorreleases,
unzip it anywhere, and either pass that directory as `citydoctor_home` / a
CLI flag, or point the CITYSMITH_CITYDOCTOR_HOME environment variable at it.
The directory is expected to contain an `app/` folder with the CityDoctor
jars, and optionally a bundled `runtime/bin/java(.exe)`.

Note: CityDoctor2's `-out` option re-serializes the file with per-feature
Quality-ADE error annotations; it does not currently repair geometry
automatically (verified empirically, not just documented). Treat this bridge
as validate-and-annotate, not auto-heal.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import tempfile
from dataclasses import dataclass, field
from pathlib import Path

from lxml import etree

_MAIN_CLASS = "de.hft.stuttgart.citydoctor2.CityDoctorValidation"
_CD_NS = {"cd": "http://www.citydoctor.eu"}
_DEFAULT_CONFIG = Path(__file__).parent / "data" / "default_validation.yml"

_DOWNLOAD_HINT = (
    "CityDoctor2 was not found. Download a prebuilt release "
    "(CityDoctorValidation-<version>-<os>.zip) from "
    "https://transfer.hft-stuttgart.de/gitlab/citydoctor/citydoctorreleases, "
    "unzip it, and either pass its path explicitly or set the "
    "CITYSMITH_CITYDOCTOR_HOME environment variable to it."
)


class CityDoctorNotFound(RuntimeError):
    pass


class CityDoctorError(RuntimeError):
    pass


@dataclass
class ValidationReport:
    """Parsed CityDoctor2 error_report."""

    num_buildings: int = 0
    num_error_buildings: int = 0
    error_counts: dict = field(default_factory=dict)
    xml_report_path: str = ""
    pdf_report_path: str = ""

    @property
    def total_errors(self) -> int:
        return sum(self.error_counts.values())

    def to_dict(self) -> dict:
        d = self.__dict__.copy()
        d["total_errors"] = self.total_errors
        return d


def locate_citydoctor(citydoctor_home: str | None = None) -> Path:
    """Resolve the CityDoctor2 install directory.

    Resolution order: explicit argument, then CITYSMITH_CITYDOCTOR_HOME.
    Raises CityDoctorNotFound with setup instructions if neither works.
    """
    candidate = citydoctor_home or os.environ.get("CITYSMITH_CITYDOCTOR_HOME")
    if not candidate:
        raise CityDoctorNotFound(_DOWNLOAD_HINT)
    # Resolve to absolute: the subprocess runs with cwd set to this directory,
    # so a relative path here would be re-interpreted against itself.
    home = Path(candidate).resolve()
    app_dir = home / "app"
    if not app_dir.is_dir() or not any(app_dir.glob("CityDoctorValidation-*.jar")):
        raise CityDoctorNotFound(
            f"'{home}' does not look like a CityDoctor2 install "
            f"(expected an app/ folder with CityDoctorValidation-*.jar). {_DOWNLOAD_HINT}"
        )
    return home


def _java_executable(home: Path) -> str:
    bundled = home / "runtime" / "bin" / ("java.exe" if os.name == "nt" else "java")
    if bundled.is_file():
        return str(bundled)
    system_java = shutil.which("java")
    if system_java:
        return system_java
    raise CityDoctorNotFound(
        "No bundled runtime found in the CityDoctor2 install and no 'java' on PATH. "
        "Install Java 17+ or use a CityDoctor2 release that bundles a runtime."
    )


def _parse_report(xml_path: Path) -> ValidationReport:
    root = etree.parse(str(xml_path)).getroot()
    report = ValidationReport(xml_report_path=str(xml_path))

    n = root.find(".//cd:model_statistics/cd:num_buildings", _CD_NS)
    if n is not None and n.text:
        report.num_buildings = int(n.text)
    ne = root.find(".//cd:global_error_statistics/cd:num_error_buildings", _CD_NS)
    if ne is not None and ne.text:
        report.num_error_buildings = int(ne.text)

    for err in root.findall(".//cd:global_statistics/cd:errors/cd:error", _CD_NS):
        name = err.get("name")
        if name and err.text:
            report.error_counts[name] = int(err.text)

    return report


def validate(input_path: str, *, citydoctor_home: str | None = None,
             config_path: str | None = None, timeout: int = 600,
             pdf_path: str | None = None) -> ValidationReport:
    """Run CityDoctor2 on a CityGML file and return a parsed report.

    pdf_path: if given, also ask CityDoctor2 to render a human-readable PDF
        report (its own `-pdfreport` flag, an Apache-FOP-rendered walkthrough
        of every check and error, distinct from and in addition to the XML
        report the parsed ValidationReport is always built from) and write it
        there.

    Raises CityDoctorNotFound if no installation can be located, or
    CityDoctorError if the CityDoctor2 process fails.
    """
    home = locate_citydoctor(citydoctor_home)
    java = _java_executable(home)
    config = config_path or str(_DEFAULT_CONFIG)
    classpath = f"{home / 'app'}{os.sep}*"

    with tempfile.TemporaryDirectory(prefix="citysmith_citydoctor_") as tmp:
        tmp_dir = Path(tmp)
        xml_out = tmp_dir / "report.xml"
        db_dir = tmp_dir / "db"
        db_dir.mkdir()

        cmd = [
            java, "-classpath", classpath, _MAIN_CLASS,
            "-in", str(Path(input_path).resolve()),
            "-config", str(Path(config).resolve()),
            "-xmlReport", str(xml_out),
            "-db_location", str(db_dir) + os.sep,
        ]
        pdf_tmp = None
        if pdf_path is not None:
            pdf_tmp = tmp_dir / "report.pdf"
            cmd += ["-pdfreport", str(pdf_tmp)]
        try:
            proc = subprocess.run(cmd, capture_output=True, text=True,
                                  timeout=timeout, cwd=str(home))
        except FileNotFoundError as exc:
            raise CityDoctorNotFound(str(exc)) from exc
        except subprocess.TimeoutExpired as exc:
            raise CityDoctorError(f"CityDoctor2 timed out after {timeout}s") from exc

        if not xml_out.exists():
            raise CityDoctorError(
                "CityDoctor2 did not produce a report.\n"
                f"stdout:\n{proc.stdout}\nstderr:\n{proc.stderr}"
            )
        if pdf_tmp is not None and not pdf_tmp.exists():
            raise CityDoctorError(
                "CityDoctor2 did not produce the requested PDF report.\n"
                f"stdout:\n{proc.stdout}\nstderr:\n{proc.stderr}"
            )

        report = _parse_report(xml_out)
        # Copy the report(s) out of the temp dir so the caller can keep them.
        persisted = Path(input_path).with_suffix(".citydoctor.xml")
        persisted.write_bytes(xml_out.read_bytes())
        report.xml_report_path = str(persisted)
        if pdf_tmp is not None:
            pdf_dest = Path(pdf_path)
            pdf_dest.write_bytes(pdf_tmp.read_bytes())
            report.pdf_report_path = str(pdf_dest)
        return report
