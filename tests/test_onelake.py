"""OneLakeMCP (Delta backend) — verified against a LOCAL Delta path before any Fabric tenant.

The decisive test runs the ENTIRE pipeline against OneLakeMCP and checks Gold appears as Delta
tables — i.e. proves the Fabric migration works end-to-end (only the storage root changes in Fabric).
"""
from datetime import date

import pandas as pd

from banking_agents.config import load_source_registry
from banking_agents.mcp import OneLakeMCP
from banking_agents.orchestration.initiator import Initiator

D1, D2 = date(2026, 5, 28), date(2026, 5, 29)


def test_delta_roundtrip_and_idempotency(tmp_path):
    mcp = OneLakeMCP(root=tmp_path / "Tables")
    df = pd.DataFrame({"a": [1, 2, 3], "b": ["x", "y", "z"]})

    mcp.write("bronze.t", df, mode="overwrite", batch_id="b1")
    assert mcp.table_exists("bronze.t")
    got = mcp.read("bronze.t")
    assert "_batch" not in got.columns and len(got) == 3        # hidden col dropped on read

    # re-append the same batch -> idempotent (replaces, no duplication)
    mcp.write("bronze.t", df, mode="append", batch_id="b1")
    assert mcp.row_count("bronze.t") == 3
    # a new batch appends
    mcp.write("bronze.t", df, mode="append", batch_id="b2")
    assert mcp.row_count("bronze.t") == 6
    assert mcp.list_tables() == ["bronze.t"]


def test_full_pipeline_runs_on_delta(tmp_path):
    """The migration proof: the same Initiator + agents, writing Delta to OneLakeMCP."""
    mcp = OneLakeMCP(root=tmp_path / "Tables")
    init = Initiator(mcp, load_source_registry(), seed=42)   # no model -> deterministic, offline
    m1 = init.run(as_of=D1)
    m2 = init.run(as_of=D2)

    assert m1.status == "success" and m2.status == "success"
    tables = mcp.list_tables()
    assert {"bronze.transactions", "silver.fact_transaction", "gold.kpi_daily"} <= set(tables)

    kpi = mcp.read("gold.kpi_daily")
    assert len(kpi) == 2 and kpi["total_deposits_eur"].gt(0).all()
    # idempotency across the whole pipeline on Delta
    init.run(as_of=D2)
    assert len(mcp.read("gold.kpi_daily")) == 2
