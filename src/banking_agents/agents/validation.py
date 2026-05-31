"""Validation / QA Agent — the gate between every hop (docs/AGENTS.md §5).

Hard fail blocks promotion; soft warn passes but is recorded for the Insight Agent.
Bronze is checked against per-source contracts; Silver gets integrity checks; Gold gets
KPI sanity bounds. Results are appended to the run manifest's QA log.
"""
from __future__ import annotations

from ..config import SourceConfig
from ..orchestration import QAResult, RunManifest
from ..quality import load_contract, summarize, validate
from .base import Agent


class ValidationAgent(Agent):
    name = "validation"

    def run(self, manifest: RunManifest, *, layer: str, **kwargs) -> dict:
        if layer == "bronze":
            outcome = self._bronze(manifest, kwargs["registry"])
        elif layer == "silver":
            outcome = self._silver(manifest)
        elif layer == "gold":
            outcome = self._gold(manifest)
        else:
            raise ValueError(f"Unknown QA layer '{layer}'")
        return {"outcome": outcome, "note": f"{layer} gate -> {outcome}"}

    # -- per-layer gates -------------------------------------------------------
    def _bronze(self, manifest: RunManifest, registry: dict[str, SourceConfig]) -> str:
        outcomes = []
        for source_id, cfg in registry.items():
            if not cfg.contract:
                continue
            df = self.mcp.read(cfg.target_bronze)
            results = validate(df, load_contract(cfg.contract))
            outcome = summarize(results)
            outcomes.append(outcome)
            fails = [r for r in results if r.outcome == "fail"]
            detail = "; ".join(f"{r.check}: {r.detail}" for r in fails) or f"{len(results)} checks ok"
            manifest.record_qa(QAResult(stage=f"bronze:{source_id}", outcome=outcome, details=detail))
        return summarize_outcomes(outcomes)

    def _silver(self, manifest: RunManifest) -> str:
        checks: list[tuple[str, bool, str]] = []
        ft = self.mcp.read("silver.fact_transaction")
        checks.append(("fact_transaction.account_sk resolves", ft["account_sk"].notna().all(),
                       f"{int(ft['account_sk'].isna().sum())} unresolved"))
        checks.append(("fact_transaction.amount_eur not null", ft["amount_eur"].notna().all(),
                       f"{int(ft['amount_eur'].isna().sum())} null"))
        checks.append(("amounts EUR-normalized (no currency col)", "currency" not in ft.columns,
                       "residual currency column present"))
        fl = self.mcp.read("silver.fact_loan")
        checks.append(("fact_loan.account_sk resolves", fl["account_sk"].notna().all(),
                       f"{int(fl['account_sk'].isna().sum())} unresolved"))

        outcome = "pass"
        for name, ok, detail in checks:
            res = "pass" if ok else "fail"
            if not ok:
                outcome = "fail"
            manifest.record_qa(QAResult(stage="silver", outcome=res, details=name if ok else f"{name}: {detail}"))
        return outcome

    def _gold(self, manifest: RunManifest) -> str:
        row = self.mcp.read("gold.kpi_daily").sort_values("date").iloc[-1]
        bounds = {
            "npl_rate_pct": (0, 100),
            "digital_channel_mix_pct": (0, 100),
            "loan_to_deposit_ratio": (0, 10),
        }
        outcome = "pass"
        for col, (lo, hi) in bounds.items():
            v = row.get(col)
            ok = v is None or (lo <= float(v) <= hi)
            res = "pass" if ok else "fail"
            if not ok:
                outcome = "fail"
            manifest.record_qa(QAResult(stage="gold", outcome=res,
                                        details=f"{col}={v} in [{lo},{hi}]" if ok else f"{col}={v} OUT OF [{lo},{hi}]"))
        return outcome


def summarize_outcomes(outcomes: list[str]) -> str:
    if "fail" in outcomes:
        return "fail"
    if "warn" in outcomes:
        return "warn"
    return "pass"
