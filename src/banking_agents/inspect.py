"""``inspect-lake`` — a readable summary + integrity check of a generated lake.

Lets you eyeball what Phase 1 produced without touching Parquet by hand: tables, row
counts, schemas, key distributions, cross-table referential integrity, and a contract
validation of bronze.transactions.
"""
from __future__ import annotations

import argparse
from pathlib import Path

import pandas as pd

from .config import REPO_ROOT, load_source_registry
from .mcp import LocalFilesystemMCP
from .quality import load_contract, summarize, validate


def _rule(title: str) -> None:
    print(f"\n{'─' * 4} {title} {'─' * (60 - len(title))}")


def inspect_lake(root: Path) -> int:
    mcp = LocalFilesystemMCP(root)
    tables = mcp.list_tables()
    if not tables:
        print(f"No tables under {root}. Run `generate-data` first.")
        return 1

    _rule("Tables")
    frames: dict[str, pd.DataFrame] = {}
    for t in tables:
        df = mcp.read(t)
        frames[t] = df
        business = [c for c in df.columns if not c.startswith("_")]
        print(f"  {t:<28} {len(df):>6} rows  {len(business)} cols (+{len(df.columns)-len(business)} lineage)")

    txn = frames.get("bronze.transactions")
    if txn is not None:
        _rule("Transactions — distributions")
        print("  currency :", txn["currency"].value_counts().to_dict())
        print("  txn_type :", txn["txn_type"].value_counts().to_dict())
        print("  channel  :", txn["channel"].value_counts().to_dict())
        print(f"  amount   : min {txn['amount'].min():.2f}  max {txn['amount'].max():.2f}  "
              f"mean {txn['amount'].mean():.2f}")
        eur_pct = (txn["currency"] == "EUR").mean() * 100
        print(f"  EUR share: {eur_pct:.1f}%  (rest needs normalization at Silver)")

    _rule("Referential integrity")
    acc = frames.get("bronze.accounts")
    cust = frames.get("bronze.customers")
    loans = frames.get("bronze.loans")
    if acc is not None and cust is not None:
        orphan = set(acc["customer_id"]) - set(cust["customer_id"])
        print(f"  accounts -> customers : {'OK' if not orphan else f'{len(orphan)} orphans'}")
    if txn is not None and acc is not None:
        orphan = set(txn["account_id"]) - set(acc["account_id"])
        print(f"  transactions -> accounts: {'OK' if not orphan else f'{len(orphan)} orphans'}")
    if loans is not None and acc is not None:
        orphan = set(loans["account_id"]) - set(acc["account_id"])
        print(f"  loans -> accounts      : {'OK' if not orphan else f'{len(orphan)} orphans'}")
        npl = (loans["days_past_due"] >= 90).mean() * 100
        print(f"  loan NPL (>=90 dpd)    : {npl:.1f}%")

    if txn is not None:
        _rule("Contract validation — bronze.transactions")
        reg = load_source_registry()
        contract = load_contract(reg["core_transactions"].contract)
        results = validate(txn, contract)
        for r in results:
            mark = {"pass": "✓", "warn": "!", "fail": "✗"}[r.outcome]
            print(f"  [{mark}] {r.check:<24} {r.detail}")
        print(f"  → gate outcome: {summarize(results).upper()}")

    # --- Silver star schema -------------------------------------------------
    silver = {t: f for t, f in frames.items() if t.startswith("silver.")}
    if silver:
        _rule("Silver — star schema")
        dims = {t: f for t, f in silver.items() if "dim_" in t}
        facts = {t: f for t, f in silver.items() if "fact_" in t}
        print("  dimensions:", ", ".join(f"{t.split('.')[1]}({len(f)})" for t, f in dims.items()))
        print("  facts     :", ", ".join(f"{t.split('.')[1]}({len(f)})" for t, f in facts.items()))
        ft = silver.get("silver.fact_transaction")
        if ft is not None:
            print("  fact_transaction sample (surrogate keys resolved):")
            print(ft[["txn_sk", "account_sk", "date_sk", "channel_sk", "amount_eur", "txn_type"]]
                  .head(3).to_string(index=False).replace("\n", "\n    "))

    # --- Gold executive KPIs ------------------------------------------------
    kpi = frames.get("gold.kpi_daily")
    if kpi is not None:
        _rule("Gold — executive KPIs (by date)")
        view = kpi.sort_values("date").set_index("date").T
        print(view.to_string().replace("\n", "\n  ").rjust(0))

    # --- Insight narrative --------------------------------------------------
    narr = frames.get("gold.exec_narrative")
    if narr is not None:
        latest = narr.sort_values("date").iloc[-1]
        _rule(f"Gold — executive briefing ({latest['date']}, via {latest['model']})")
        print("  " + latest["narrative"].replace("\n", "\n  "))
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Inspect a generated lake.")
    parser.add_argument("--root", type=Path, default=REPO_ROOT / "data" / "lake",
                        help="Lake root directory. Default: data/lake.")
    args = parser.parse_args(argv)
    return inspect_lake(args.root)


if __name__ == "__main__":
    raise SystemExit(main())
