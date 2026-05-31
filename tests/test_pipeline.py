"""Phase-2 tests: transformation helpers, KPI helpers, and the orchestrated pipeline."""
from datetime import date

import pandas as pd
import pytest

from banking_agents.agents.aggregation import _growth_pct, _safe_div
from banking_agents.agents.transformation import tokenize
from banking_agents.config import load_source_registry
from banking_agents.mcp import LocalFilesystemMCP
from banking_agents.orchestration.initiator import Initiator

D1 = date(2026, 5, 28)
D2 = date(2026, 5, 29)


# --- pure helpers -------------------------------------------------------------
def test_tokenize_is_deterministic_and_irreversible():
    assert tokenize("Jane Doe") == tokenize("Jane Doe")
    assert tokenize("Jane Doe") != tokenize("John Doe")
    assert len(tokenize("Jane Doe")) == 16 and "Jane" not in tokenize("Jane Doe")


def test_growth_and_safe_div():
    assert _growth_pct(110, 100) == 10.0
    assert _growth_pct(100, None) is None   # no prior period
    assert _growth_pct(100, 0) is None      # no divide-by-zero
    assert _safe_div(10, 0) is None
    assert _safe_div(10, 4) == 2.5


# --- orchestrated pipeline ----------------------------------------------------
@pytest.fixture
def initiator(tmp_path):
    mcp = LocalFilesystemMCP(tmp_path / "lake")
    return Initiator(mcp, load_source_registry(), seed=42), mcp


def test_pipeline_runs_and_builds_all_layers(initiator):
    init, mcp = initiator
    m = init.run(as_of=D1)
    assert m.status == "success"
    assert all(s == "passed" for s in m.stages.values())

    tables = mcp.list_tables()
    assert {"bronze.transactions", "silver.fact_transaction", "silver.dim_customer",
            "gold.kpi_daily"} <= set(tables)

    # PII never reaches Silver; token does.
    dim_customer = mcp.read("silver.dim_customer")
    assert "full_name" not in dim_customer.columns
    assert "customer_token" in dim_customer.columns

    # amounts EUR-normalized: no residual currency column on the fact
    assert "currency" not in mcp.read("silver.fact_transaction").columns


def test_kpis_are_sane(initiator):
    init, mcp = initiator
    init.run(as_of=D1)
    row = mcp.read("gold.kpi_daily").iloc[-1]
    assert row["total_deposits_eur"] > 0
    assert row["net_loans_eur"] > 0
    assert 0 <= row["npl_rate_pct"] <= 100
    assert 0 <= row["digital_channel_mix_pct"] <= 100
    assert 4000 <= row["txn_count"] <= 6000   # ~5000 ±8% daily variation
    # first day has no prior period
    assert pd.isna(row["deposits_growth_pct"]) or row["deposits_growth_pct"] is None


def test_growth_deltas_appear_on_second_day(initiator):
    init, mcp = initiator
    init.run(as_of=D1)
    init.run(as_of=D2)
    kpi = mcp.read("gold.kpi_daily")
    assert len(kpi) == 2
    day1 = kpi[kpi["date"] == D1.isoformat()].iloc[0]
    day2 = kpi[kpi["date"] == D2.isoformat()].iloc[0]
    assert day2["deposits_growth_pct"] is not None and not pd.isna(day2["deposits_growth_pct"])
    # date-seeded variation: deposits actually move day-over-day (no longer flat)
    assert day1["total_deposits_eur"] != day2["total_deposits_eur"]

    # dim_date is a proper dimension: one row per distinct day, unique key
    dim_date = mcp.read("silver.dim_date")
    assert len(dim_date) == 2
    assert dim_date["date_sk"].is_unique


def test_rerunning_a_day_is_idempotent(initiator):
    init, mcp = initiator
    init.run(as_of=D1)
    n_txn = mcp.row_count("bronze.transactions")
    init.run(as_of=D1)  # same day again
    assert mcp.row_count("bronze.transactions") == n_txn          # no duplicate append
    assert len(mcp.read("gold.kpi_daily")) == 1                    # one row for the date
