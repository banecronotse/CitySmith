"""citysmith: a blacksmith for CityGML. Derives lower LODs, fills in missing
semantics, converts to CityJSON and validates geometry."""

from .core import add_lod2, enhance, inspect, InspectReport, Report
from .citydoctor import validate as validate_citydoctor, ValidationReport

__all__ = ["add_lod2", "enhance", "inspect", "InspectReport", "Report",
           "validate_citydoctor", "ValidationReport"]
__version__ = "0.1.0"
