"""Visualization Agent — template-driven generative BI.

Turns a natural-language request ("show me workforce attrition", "deposits by segment",
"loans activity by source") into a choice of one GOVERNED template + an optional source
filter. It can ONLY pick a template id from the catalog and a source from the allowed list —
it never authors free-form queries or touches raw data. Falls back to deterministic keyword
matching when no model is available, so it always returns a valid, governed selection.
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass

from ..bi.catalog import Template, catalog_summary, load_catalog
from ..model import ModelProvider

KNOWN_SOURCES = ["trades", "loans", "deposits", "cards", "payments", "transfers"]

SYSTEM = (
    "You are a BI assistant for a bank. Given a user's request and a catalog of APPROVED report "
    "templates, pick the SINGLE best template by its id. If that template supports a source "
    "filter, also extract the requested business source from the allowed list (else null). "
    "You may ONLY choose a template_id that appears in the catalog. "
    'Respond with ONLY JSON: {"template_id": "...", "source": "<allowed source or null>", '
    '"reason": "<one short sentence>"}.'
)


@dataclass
class Selection:
    template: Template
    source: str | None
    reason: str
    via: str  # "model" | "fallback"


class VisualizationAgent:
    name = "visualization"

    def __init__(self, model: ModelProvider | None = None, catalog: dict[str, Template] | None = None):
        self.model = model
        self.catalog = catalog or load_catalog()

    def select(self, request: str) -> Selection:
        sel = self._select_via_model(request) if self.model is not None else None
        if sel is None:
            sel = self._select_via_keywords(request)
        # Validate / normalise the source against the chosen template.
        source = sel.source if sel.template.supports_source_filter else None
        if source and sel.template.sources and source not in sel.template.sources:
            source = None
        if sel.template.supports_source_filter and not source:
            source = self._detect_source(request) or (sel.template.sources or KNOWN_SOURCES)[0]
        return Selection(sel.template, source, sel.reason, sel.via)

    # -- model-driven selection ------------------------------------------------
    def _select_via_model(self, request: str) -> Selection | None:
        prompt = (f"User request: {request}\n\nApproved templates:\n{catalog_summary(self.catalog)}\n\n"
                  f"Allowed sources for source-filtered templates: {', '.join(KNOWN_SOURCES)}.")
        try:
            resp = self.model.complete(SYSTEM, [{"role": "user", "content": prompt}])
            m = re.search(r"\{.*\}", resp.text, re.DOTALL)
            data = json.loads(m.group(0)) if m else {}
            tid = str(data.get("template_id", "")).strip()
            if tid not in self.catalog:
                return None
            src = data.get("source")
            src = str(src).strip().lower() if src else None
            return Selection(self.catalog[tid], src, str(data.get("reason", "")) or "model selection", "model")
        except Exception:
            return None  # any failure -> keyword fallback

    # -- deterministic fallback ------------------------------------------------
    def _select_via_keywords(self, request: str) -> Selection:
        text = request.lower()
        best, best_score = None, -1
        for t in self.catalog.values():
            score = sum(1 for kw in t.keywords if kw in text)
            if t.domain in text:
                score += 1
            if t.supports_source_filter and self._detect_source(text):
                score += 1
            if score > best_score:
                best, best_score = t, score
        best = best or next(iter(self.catalog.values()))
        return Selection(best, self._detect_source(text), "keyword match", "fallback")

    @staticmethod
    def _detect_source(text: str) -> str | None:
        for s in KNOWN_SOURCES:
            if s in text or s.rstrip("s") in text:   # 'trade' matches 'trades'
                return s
        return None
