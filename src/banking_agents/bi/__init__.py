"""Generative BI — template-driven, agentic report creation over existing Gold data.

A governed template catalog (config/report_templates/) + a VisualizationAgent that picks and
parameterises a template from a natural-language request + a renderer that produces the report.
Locally the renderer emits HTML; in Fabric the same spec maps to a Power BI report deployed via
the REST API (see fabric_integration.md).
"""
from .catalog import Template, load_catalog

__all__ = ["Template", "load_catalog"]
