"""Aggregation Agent (Stage 3 → Gold). Silver star schema -> executive KPIs + marts.

Computes the nine v1 KPIs (docs/DATA_MODEL.md §4) for the run's reporting day, with
prior-period deltas read from the accumulating gold.kpi_daily. Drill-down marts support
the dashboard. See docs/AGENTS.md §4.
"""
from __future__ import annotations

from datetime import date

import pandas as pd

from ..orchestration import RunManifest
from .base import Agent


def _safe_div(num: float, den: float) -> float | None:
    return round(num / den, 4) if den else None


def _growth_pct(curr: float, prior: float | None) -> float | None:
    if prior in (None, 0) or pd.isna(prior):
        return None
    return round((curr - prior) / prior * 100, 2)


class AggregationAgent(Agent):
    name = "aggregation"

    def run(self, manifest: RunManifest, **_) -> dict:
        read, write = self.mcp.read, self.mcp.write
        as_of = date.fromisoformat(manifest.window_to)
        as_of_sk = as_of.year * 10000 + as_of.month * 100 + as_of.day

        ft = read("silver.fact_transaction")
        fab = read("silver.fact_account_balance")
        fl = read("silver.fact_loan")
        dim_channel = read("silver.dim_channel")
        dim_account = read("silver.dim_account")

        txns = ft[ft["date_sk"] == as_of_sk]

        # --- core aggregates --------------------------------------------------
        total_deposits = float(fab.loc[fab["is_deposit"], "balance_eur"].sum())
        net_loans = float(fl["principal_outstanding_eur"].sum())
        fee_income = float(-txns.loc[txns["is_fee"], "amount_eur"].sum())   # fees are negative
        interest = txns.loc[txns["is_interest"], "amount_eur"]
        nim = _safe_div((interest[interest > 0].sum() + interest[interest < 0].sum()), net_loans)
        npl_principal = float(fl.loc[fl["is_npl"], "principal_outstanding_eur"].sum())
        npl_rate = _safe_div(npl_principal, net_loans)

        # active customers transacting today (via account -> customer)
        cust_sk = txns.merge(dim_account[["account_sk", "customer_sk"]], on="account_sk", how="left")["customer_sk"]
        active_customers = int(cust_sk.nunique())

        # digital channel mix
        ch = txns.merge(dim_channel[["channel_sk", "is_digital"]], on="channel_sk", how="left")
        digital_mix = round(ch["is_digital"].mean() * 100, 2) if len(ch) else None

        # --- prior-period deltas ---------------------------------------------
        prior = None
        if self.mcp.table_exists("gold.kpi_daily"):
            hist = read("gold.kpi_daily")
            past = hist[hist["date"].astype(str) < as_of.isoformat()]
            if len(past):
                prior = past.sort_values("date").iloc[-1]

        prior_dep = float(prior["total_deposits_eur"]) if prior is not None else None
        prior_loans = float(prior["net_loans_eur"]) if prior is not None else None
        prior_active = float(prior["active_customers"]) if prior is not None else None

        row = {
            "date": as_of.isoformat(),
            "total_deposits_eur": round(total_deposits, 2),
            "deposits_growth_pct": _growth_pct(total_deposits, prior_dep),
            "net_loans_eur": round(net_loans, 2),
            "loans_growth_pct": _growth_pct(net_loans, prior_loans),
            "loan_to_deposit_ratio": _safe_div(net_loans, total_deposits),
            "nim_pct": round(nim * 100, 4) if nim is not None else None,
            "fee_income_eur": round(fee_income, 2),
            "txn_count": int(len(txns)),
            "txn_value_eur": round(float(txns["amount_eur"].abs().sum()), 2),
            "npl_rate_pct": round(npl_rate * 100, 2) if npl_rate is not None else None,
            "active_customers": active_customers,
            "net_new_customers": int(active_customers - prior_active) if prior_active is not None else None,
            "digital_channel_mix_pct": digital_mix,
        }
        # idempotent by date: same day overwrites its own partition
        write("gold.kpi_daily", pd.DataFrame([row]), mode="append", batch_id=as_of.isoformat())

        # --- drill-down marts -------------------------------------------------
        deposits_by_segment = (
            fab[fab["is_deposit"]]
            .merge(dim_account[["account_sk", "customer_sk"]], on="account_sk", how="left")
            .merge(read("silver.dim_customer")[["customer_sk", "segment"]], on="customer_sk", how="left")
            .groupby("segment", as_index=False)["balance_eur"].sum()
            .rename(columns={"balance_eur": "deposits_eur"}))
        deposits_by_segment["date"] = as_of.isoformat()
        write("gold.deposits_by_segment", deposits_by_segment, mode="append", batch_id=as_of.isoformat())

        txn_by_channel = (
            txns.merge(dim_channel[["channel_sk", "channel", "is_digital"]], on="channel_sk", how="left")
            .groupby(["channel", "is_digital"], as_index=False)
            .agg(txn_count=("txn_sk", "count"), txn_value_eur=("amount_eur", lambda s: s.abs().sum())))
        txn_by_channel["date"] = as_of.isoformat()
        write("gold.txn_by_channel", txn_by_channel, mode="append", batch_id=as_of.isoformat())

        loans_by_status = fl.assign(
            bucket=pd.cut(fl["days_past_due"], bins=[-1, 0, 89, 10_000],
                          labels=["current", "delinquent", "default"])
        ).groupby("bucket", as_index=False, observed=True)["principal_outstanding_eur"].sum()
        loans_by_status["date"] = as_of.isoformat()
        write("gold.loans_by_status", loans_by_status, mode="append", batch_id=as_of.isoformat())

        return {"rows": 1, "tables": ["gold.kpi_daily", "gold.deposits_by_segment",
                                      "gold.txn_by_channel", "gold.loans_by_status"],
                "kpis": row, "note": f"computed KPIs for {as_of.isoformat()}"}
