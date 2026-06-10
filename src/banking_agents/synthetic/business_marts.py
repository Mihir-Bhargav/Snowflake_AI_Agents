"""Synthetic HR / Operations / by-source Gold marts for the generative-BI prototype.

These stand in for business domains not yet onboarded (HR systems, ops telemetry, trading).
In Fabric they're replaced by real sources flowing through the medallion — the report
templates and the VisualizationAgent don't change. Deterministic by date (idempotent per day).
"""
from __future__ import annotations

import argparse
import random
from datetime import date
from pathlib import Path

import pandas as pd

from ..config import REPO_ROOT
from ..mcp import LocalFilesystemMCP
from .generators import ANCHOR_DATE

DEPARTMENTS = ["Retail Banking", "Corporate Banking", "Risk", "Technology",
               "Operations", "Compliance", "HR", "Finance"]
CHANNELS = ["mobile", "web", "atm", "branch", "card"]
SOURCES = ["trades", "loans", "deposits", "cards", "payments", "transfers"]


def _rng(as_of: date, salt: int) -> random.Random:
    return random.Random(as_of.toordinal() * 97 + salt)   # deterministic per date


def _grow(base: float, as_of: date, daily: float) -> float:
    return base * (1 + daily) ** (as_of - ANCHOR_DATE).days


def hr_marts(as_of: date) -> dict[str, pd.DataFrame]:
    r = _rng(as_of, 1)
    headcount = int(_grow(4200, as_of, 0.0006) + r.randint(-12, 12))
    attrition_count = r.randint(3, 18)
    payroll = round(headcount * r.uniform(5500, 7500), 2)
    daily = pd.DataFrame([{
        "date": as_of.isoformat(),
        "headcount": headcount,
        "hires": r.randint(5, 25),
        "attrition_count": attrition_count,
        "attrition_rate_pct": round(r.uniform(8.0, 13.5), 2),
        "avg_tenure_years": round(r.uniform(4.5, 6.5), 1),
        "open_positions": r.randint(40, 120),
        "payroll_cost_eur": payroll,
        "cost_per_head_eur": round(payroll / headcount, 2),
    }])
    weights = [r.uniform(0.5, 2.0) for _ in DEPARTMENTS]
    total_w = sum(weights)
    by_dept = pd.DataFrame([{
        "date": as_of.isoformat(),
        "department": d,
        "headcount": int(headcount * w / total_w),
        "attrition_rate_pct": round(r.uniform(5.0, 18.0), 2),
        "payroll_cost_eur": round(payroll * w / total_w, 2),
    } for d, w in zip(DEPARTMENTS, weights)])
    return {"gold.hr_headcount_daily": daily, "gold.hr_by_department": by_dept}


def ops_marts(as_of: date) -> dict[str, pd.DataFrame]:
    r = _rng(as_of, 2)
    daily = pd.DataFrame([{
        "date": as_of.isoformat(),
        "txn_processed": int(_grow(5000, as_of, 0.0012) + r.randint(-300, 300)),
        "stp_rate_pct": round(r.uniform(94.0, 99.0), 2),
        "exceptions": r.randint(20, 120),
        "avg_processing_ms": round(r.uniform(120, 400), 1),
        "sla_adherence_pct": round(r.uniform(97.0, 99.9), 2),
    }])
    by_channel = pd.DataFrame([{
        "date": as_of.isoformat(),
        "channel": c,
        "volume": r.randint(600, 1300),
        "uptime_pct": round(r.uniform(98.5, 100.0), 2),
        "avg_processing_ms": round(r.uniform(90, 450), 1),
    } for c in CHANNELS])
    return {"gold.ops_processing_daily": daily, "gold.ops_by_channel": by_channel}


def source_mart(as_of: date) -> dict[str, pd.DataFrame]:
    r = _rng(as_of, 3)
    scale = {"trades": (200, 5_000_000), "loans": (150, 2_000_000), "deposits": (1200, 900_000),
             "cards": (3000, 250_000), "payments": (4000, 600_000), "transfers": (1500, 1_200_000)}
    rows = [{
        "date": as_of.isoformat(),
        "source": s,
        "volume": int(scale[s][0] * r.uniform(0.85, 1.15)),
        "value_eur": round(scale[s][1] * r.uniform(0.85, 1.15), 2),
    } for s in SOURCES]
    return {"gold.activity_by_source": pd.DataFrame(rows)}


def seed(as_of: date, root: Path) -> list[str]:
    mcp = LocalFilesystemMCP(root)
    marts = {**hr_marts(as_of), **ops_marts(as_of), **source_mart(as_of)}
    for table, df in marts.items():
        mcp.write(table, df, mode="append", batch_id=as_of.isoformat())   # idempotent per date
    return list(marts)


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description="Seed synthetic HR/Operations/by-source Gold marts.")
    p.add_argument("--as-of", type=lambda s: date.fromisoformat(s), default=date.today())
    p.add_argument("--root", type=Path, default=REPO_ROOT / "data" / "lake")
    args = p.parse_args(argv)
    tables = seed(args.as_of, args.root)
    print(f"Seeded {len(tables)} marts for {args.as_of}: {', '.join(tables)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
