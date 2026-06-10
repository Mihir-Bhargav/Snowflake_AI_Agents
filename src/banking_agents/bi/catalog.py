"""Load + validate the governed report-template catalog (config/report_templates/*.yaml)."""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

from ..config import CONFIG_DIR

TEMPLATES_DIR = CONFIG_DIR / "report_templates"
VISUAL_TYPES = {"kpi_cards", "line", "bar", "donut"}


@dataclass(frozen=True)
class Template:
    id: str
    domain: str
    title: str
    description: str
    keywords: list[str]
    visuals: list[dict[str, Any]]
    supports_source_filter: bool = False
    filter_field: str | None = None
    sources: list[str] = field(default_factory=list)

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "Template":
        visuals = d.get("visuals", [])
        for v in visuals:
            if v.get("type") not in VISUAL_TYPES:
                raise ValueError(f"template '{d.get('id')}' has unknown visual type {v.get('type')!r}")
            if not str(v.get("source", "")).startswith("gold."):
                raise ValueError(f"template '{d.get('id')}' visual must bind to a gold.* source")
        return cls(
            id=d["id"], domain=d["domain"], title=d["title"], description=d["description"],
            keywords=list(d.get("keywords", [])), visuals=visuals,
            supports_source_filter=bool(d.get("supports_source_filter", False)),
            filter_field=d.get("filter_field"), sources=list(d.get("sources", [])),
        )


def load_catalog(templates_dir: Path | None = None) -> dict[str, Template]:
    """Return {template_id: Template} across every config/report_templates/*.yaml."""
    templates_dir = templates_dir or TEMPLATES_DIR
    catalog: dict[str, Template] = {}
    for path in sorted(templates_dir.glob("*.yaml")):
        data = yaml.safe_load(path.read_text()) or {}
        for entry in data.get("templates", []):
            t = Template.from_dict(entry)
            if t.id in catalog:
                raise ValueError(f"duplicate template id '{t.id}'")
            catalog[t.id] = t
    if not catalog:
        raise FileNotFoundError(f"no templates found under {templates_dir}")
    return catalog


def catalog_summary(catalog: dict[str, Template]) -> str:
    """Compact catalog description fed to the model for grounded template selection."""
    lines = []
    for t in catalog.values():
        src = " [supports source filter]" if t.supports_source_filter else ""
        lines.append(f"- {t.id} (domain={t.domain}){src}: {t.description}")
    return "\n".join(lines)
