"""Insight Agent (executive narrative layer) — docs/AGENTS.md §6.

Reads the Gold KPIs (today + history) and the QA warnings, and asks the model to write a
grounded executive narrative. Hard guardrail: the prompt carries *only* Gold figures and
instructs the model never to invent numbers. Output lands in gold.exec_narrative.

This is the first agent that actually calls the ModelProvider; the ETL agents do not.
"""
from __future__ import annotations

import json
from datetime import date, datetime, timezone

import pandas as pd

from ..orchestration import RunManifest
from .base import Agent

SYSTEM = (
    "You are a banking analytics assistant writing a daily briefing for bank executives. "
    "You are given the day's Gold-layer KPIs and recent history as JSON. Write a clear, "
    "neutral, professional briefing of roughly 250-350 words, organised as a short flowing "
    "narrative covering, in order: (1) a one-sentence headline; (2) a performance summary of "
    "the most material movements vs the prior day across deposits, loans and transaction "
    "activity; (3) a risk and liquidity assessment (NPL / delinquency, and the loan-to-deposit "
    "ratio); and (4) two to three grounded, actionable recommendations. "
    "RULES: use ONLY the numbers provided; never invent or estimate figures; all amounts are "
    "in EUR; if a growth field is null, say it is the first day of history rather than guessing. "
    "Output plain prose paragraphs, no markdown headers or bullet lists."
)


class InsightAgent(Agent):
    name = "insight"

    def run(self, manifest: RunManifest, **_) -> dict:
        if self.model is None:
            raise RuntimeError("InsightAgent requires a ModelProvider (model=...).")

        as_of = manifest.window_to
        kpi = self.mcp.read("gold.kpi_daily").sort_values("date")
        today = kpi[kpi["date"].astype(str) == as_of].iloc[-1].to_dict()
        history = kpi.tail(7).to_dict(orient="records")
        qa_warnings = [f"{q.stage}: {q.details}" for q in manifest.qa if q.outcome in ("warn", "fail")]

        prompt = (
            f"Reporting date: {as_of}\n\n"
            f"Today's KPIs (EUR):\n{json.dumps(today, indent=2, default=str)}\n\n"
            f"Recent history (up to 7 days):\n{json.dumps(history, indent=2, default=str)}\n\n"
            f"QA notes from this run:\n{json.dumps(qa_warnings, indent=2) if qa_warnings else 'none'}"
        )
        resp = self.model.complete(SYSTEM, [{"role": "user", "content": prompt}])
        narrative = resp.text.strip()

        out = pd.DataFrame([{
            "date": as_of,
            "narrative": narrative,
            "model": self.config_name(),
            "generated_at": datetime.now(timezone.utc).isoformat(),
        }])
        self.mcp.write("gold.exec_narrative", out, mode="append", batch_id=as_of)
        return {"rows": 1, "tables": ["gold.exec_narrative"],
                "narrative": narrative, "note": f"insight narrative for {as_of}"}

    def config_name(self) -> str:
        return getattr(self.model.config, "name", "unknown")
