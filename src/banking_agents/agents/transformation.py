"""Transformation Agent (Stage 2 → Silver). Bronze -> conformed star schema.

Applies the "math": EUR-normalization of all amounts, PII tokenization, surrogate keys,
fact/dimension split, and derived flags. Rebuilds Silver from Bronze each run (full,
idempotent recompute). See docs/AGENTS.md §3 and docs/DATA_MODEL.md §2.
"""
from __future__ import annotations

import hashlib

import pandas as pd
import yaml

from ..config import CONFIG_DIR
from ..orchestration import RunManifest
from .base import Agent

DEPOSIT_PRODUCTS = {"current", "savings", "deposit"}
# In real deployments this salt comes from Key Vault; never commit a production salt.
_PII_SALT = "phase2-dev-salt"


def _load_fx() -> dict[str, float]:
    data = yaml.safe_load((CONFIG_DIR / "fx_rates.yaml").read_text())
    return {k: float(v) for k, v in data["rates"].items()}


def tokenize(value) -> str:
    """One-way PII token. Raw PII never leaves Bronze (docs/DATA_MODEL.md §0)."""
    return hashlib.sha256(f"{_PII_SALT}:{value}".encode()).hexdigest()[:16]


def _date_sk(s: pd.Series) -> pd.Series:
    d = pd.to_datetime(s)
    return (d.dt.year * 10000 + d.dt.month * 100 + d.dt.day).astype(int)


class TransformationAgent(Agent):
    name = "transformation"

    def run(self, manifest: RunManifest, **_) -> dict:
        fx = _load_fx()
        read, write = self.mcp.read, self.mcp.write
        bid = manifest.run_id

        # ---- dimensions ------------------------------------------------------
        cust = read("bronze.customers")
        dim_customer = pd.DataFrame({
            "customer_sk": range(1, len(cust) + 1),
            "customer_id": cust["customer_id"].values,
            "customer_token": cust["full_name"].map(tokenize).values,   # PII tokenized
            "segment": cust["segment"].values,
            "country": cust["country"].values,
            "valid_from": manifest.window_to,
            "valid_to": None,
            "is_current": True,
        })

        acc = read("bronze.accounts")
        dim_account = acc[["account_id", "customer_id", "product_type", "status"]].copy()
        dim_account.insert(0, "account_sk", range(1, len(dim_account) + 1))
        dim_account = dim_account.merge(
            dim_customer[["customer_id", "customer_sk"]], on="customer_id", how="left")
        dim_account["valid_from"] = manifest.window_to
        dim_account["valid_to"] = None
        dim_account["is_current"] = True

        dim_channel = read("bronze.reference_channels")[["channel", "is_digital"]].copy()
        dim_channel.insert(0, "channel_sk", range(1, len(dim_channel) + 1))

        dim_product = read("bronze.reference_products")[["product_type", "product_name"]].copy()
        dim_product.insert(0, "product_sk", range(1, len(dim_product) + 1))

        # ---- facts -----------------------------------------------------------
        txn = read("bronze.transactions")
        ft = pd.DataFrame({
            "txn_id": txn["txn_id"].values,
            "account_id": txn["account_id"].values,           # join key, dropped after merge
            "channel": txn["channel"].values,                 # join key, dropped after merge
            "amount_eur": (txn["amount"] * txn["currency"].map(fx).fillna(1.0)).round(2).values,
            "txn_type": txn["txn_type"].values,
            "is_fee": (txn["txn_type"] == "fee").values,
            "is_interest": (txn["txn_type"] == "interest").values,
            "date_sk": _date_sk(txn["posting_ts"]).values,
        })
        ft = (ft.merge(dim_account[["account_id", "account_sk"]], on="account_id", how="left")
                .merge(dim_channel[["channel", "channel_sk"]], on="channel", how="left")
                .drop(columns=["account_id", "channel"]))
        ft.insert(0, "txn_sk", range(1, len(ft) + 1))

        bal = read("bronze.accounts")
        fab = pd.DataFrame({
            "account_id": bal["account_id"].values,
            "balance_eur": (bal["balance"] * bal["currency"].map(fx).fillna(1.0)).round(2).values,
            "is_deposit": bal["product_type"].isin(DEPOSIT_PRODUCTS).values,
            "is_loan": (bal["product_type"] == "loan").values,
            "date_sk": _date_sk(bal["snapshot_date"]).values,
        }).merge(dim_account[["account_id", "account_sk"]], on="account_id", how="left").drop(columns=["account_id"])

        loans = read("bronze.loans")
        fl = pd.DataFrame({
            "loan_id": loans["loan_id"].values,
            "account_id": loans["account_id"].values,
            "principal_outstanding_eur": loans["principal_outstanding"].round(2).values,  # loans are EUR
            "interest_rate": loans["interest_rate"].values,
            "days_past_due": loans["days_past_due"].values,
            "is_npl": (loans["days_past_due"] >= 90).values,
            "date_sk": _date_sk(loans["snapshot_date"]).values,
        }).merge(dim_account[["account_id", "account_sk"]], on="account_id", how="left").drop(columns=["account_id"])
        fl.insert(0, "loan_sk", range(1, len(fl) + 1))

        # ---- dim_date (one row per distinct day present across the facts) ----
        all_sks = pd.concat([ft["date_sk"], fab["date_sk"], fl["date_sk"]]).drop_duplicates().sort_values()
        dd = pd.to_datetime(all_sks.astype(str), format="%Y%m%d")
        dim_date = pd.DataFrame({
            "date_sk": all_sks.astype(int).values,
            "date": dd.dt.date.values,
            "month": dd.dt.month.values,
            "quarter": dd.dt.quarter.values,
            "year": dd.dt.year.values,
        })

        tables = {
            "silver.dim_customer": dim_customer, "silver.dim_account": dim_account,
            "silver.dim_channel": dim_channel, "silver.dim_product": dim_product,
            "silver.dim_date": dim_date, "silver.fact_transaction": ft,
            "silver.fact_account_balance": fab, "silver.fact_loan": fl,
        }
        rows = 0
        for name, df in tables.items():
            rows += write(name, df, mode="overwrite", batch_id=bid)

        return {"rows": rows, "tables": list(tables),
                "note": f"built {len(tables)} Silver tables; PII tokenized; amounts EUR-normalized"}
