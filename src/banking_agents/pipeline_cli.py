"""``run-pipeline`` — run the full nightly flow: Extract -> Transform -> Aggregate with
QA gates, then print the executive KPIs. The Initiator owns sequencing and halt-on-fail.
"""
from __future__ import annotations

import argparse
from datetime import date
from pathlib import Path

from .config import REPO_ROOT, ModelConfig, load_source_registry
from .mcp import LocalFilesystemMCP
from .model import get_provider
from .orchestration.initiator import Initiator

_KPI_LABELS = {
    "total_deposits_eur": "Total Deposits (€)",
    "deposits_growth_pct": "  ↳ growth %",
    "net_loans_eur": "Net Loans Outstanding (€)",
    "loans_growth_pct": "  ↳ growth %",
    "loan_to_deposit_ratio": "Loan-to-Deposit Ratio",
    "nim_pct": "Net Interest Margin %",
    "fee_income_eur": "Fee & Commission Income (€)",
    "txn_count": "Transaction Volume (count)",
    "txn_value_eur": "Transaction Value (€)",
    "npl_rate_pct": "NPL / Delinquency Rate %",
    "active_customers": "Active Customers",
    "net_new_customers": "  ↳ net new",
    "digital_channel_mix_pct": "Digital Channel Mix %",
}


def _fmt(v) -> str:
    if v is None:
        return "—  (needs prior day)"
    if isinstance(v, float):
        return f"{v:,.2f}"
    return f"{v:,}"


def run(as_of: date, root: Path, seed: int = 42) -> int:
    registry = load_source_registry()
    model_cfg = ModelConfig.load()
    mcp = LocalFilesystemMCP(root)
    initiator = Initiator(mcp, registry, model=get_provider(model_cfg), seed=seed)

    print(f"Running pipeline for {as_of} (provider={model_cfg.provider}) ...\n")
    manifest = initiator.run(as_of=as_of, trigger="manual", manifest_dir=root.parent / "ops" / "manifests")

    for stage, status in manifest.stages.items():
        print(f"  stage {stage:<10} {status}")
    print("\n  QA gates:")
    for q in manifest.qa:
        mark = {"pass": "✓", "warn": "!", "fail": "✗"}[q.outcome]
        print(f"    [{mark}] {q.stage:<20} {q.details}")

    print(f"\n  Run status: {manifest.status.upper()}")
    if manifest.status == "success":
        kpis = mcp.read("gold.kpi_daily")
        row = kpis[kpis["date"].astype(str) == as_of.isoformat()].iloc[-1]
        print(f"\n{'═' * 52}\n  EXECUTIVE KPIs — {as_of}\n{'═' * 52}")
        for col, label in _KPI_LABELS.items():
            print(f"  {label:<30} {_fmt(row[col])}")

        if mcp.table_exists("gold.exec_narrative"):
            narr = mcp.read("gold.exec_narrative")
            narr = narr[narr["date"].astype(str) == as_of.isoformat()]
            if len(narr):
                r = narr.iloc[-1]
                print(f"\n{'═' * 52}\n  EXECUTIVE BRIEFING  (via {r['model']})\n{'═' * 52}")
                print("  " + r["narrative"].replace("\n", "\n  "))
    return 0 if manifest.status == "success" else 1


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description="Run the full Bronze->Silver->Gold pipeline.")
    p.add_argument("--as-of", type=lambda s: date.fromisoformat(s), default=date.today())
    p.add_argument("--root", type=Path, default=REPO_ROOT / "data" / "lake")
    p.add_argument("--seed", type=int, default=42)
    args = p.parse_args(argv)
    return run(args.as_of, args.root, args.seed)


if __name__ == "__main__":
    raise SystemExit(main())
