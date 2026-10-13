"""citysmith: a blacksmith for CityGML. Derives lower LODs, fills in missing
semantics, converts to CityJSON and validates XML structure and geometry."""

from .core import add_lod2, enhance, inspect, InspectReport, Report
from .citydoctor import validate as validate_citydoctor, ValidationReport
from .crop import crop, CropReport
from .merge import merge, MergeError, MergeReport
from .schema import validate_schema, SchemaError, SchemaReport

__all__ = ["add_lod2", "enhance", "inspect", "InspectReport", "Report",
           "validate_citydoctor", "ValidationReport", "crop", "CropReport",
           "merge", "MergeError", "MergeReport",
           "validate_schema", "SchemaError", "SchemaReport"]
__version__ = "0.1.0"
