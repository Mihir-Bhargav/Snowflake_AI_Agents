"""Phase-1 smoke tests: config, generators, MCP roundtrip/idempotency, manifest, provider."""
from datetime import date

import pandas as pd

from banking_agents.config import ModelConfig, load_source_registry
from banking_agents.mcp import LocalFilesystemMCP
from banking_agents.model import StubProvider, get_provider
from banking_agents.orchestration import RunManifest, StageStatus
from banking_agents.synthetic.generators import BankUniverse, GeneratorConfig, reference_tables


def test_source_registry_loads_four_domains():
    reg = load_source_registry()
    assert {"core_transactions", "accounts", "customers", "loans"} <= set(reg)
    assert reg["customers"].pii_columns == ["full_name", "birth_date"]


def test_model_config_loads_and_provider_factory_works():
    cfg = ModelConfig.load()
    assert cfg.provider in {"stub", "gemini", "anthropic", "bedrock", "vertex"}
    # The stub provider is always available and offline — used by tests.
    stub = get_provider(ModelConfig(name="x", provider="stub", key_ref="none", region="eu"))
    assert isinstance(stub, StubProvider)
    resp = stub.complete("sys", [{"role": "user", "content": "hi"}])
    assert "stub" in resp.text


def test_generators_are_consistent_and_deterministic():
    cfg = GeneratorConfig(customer_count=50, account_count=80, loan_count=20, txns_per_day=200, seed=7)
    u1, u2 = BankUniverse(cfg), BankUniverse(cfg)
    # deterministic
    pd.testing.assert_frame_equal(u1.customers, u2.customers)
    # referential integrity: every account maps to a real customer
    assert set(u1.accounts["customer_id"]) <= set(u1.customers["customer_id"])
    # transactions reference real accounts; daily volume varies ±8% but is deterministic
    txns = u1.transactions_for_day(date(2026, 5, 30))
    assert len(txns) == len(u2.transactions_for_day(date(2026, 5, 30)))   # deterministic
    assert 180 <= len(txns) <= 220                                        # ~200 ±8%
    assert set(txns["account_id"]) <= set(u1.accounts["account_id"])
    # expected business columns present
    assert {"txn_id", "amount", "posting_ts", "txn_type", "channel"} <= set(txns.columns)
    assert set(reference_tables()) == {"reference_products", "reference_branches", "reference_channels"}


def test_mcp_roundtrip_and_idempotency(tmp_path):
    mcp = LocalFilesystemMCP(tmp_path / "lake")
    df = pd.DataFrame({"a": [1, 2, 3]})
    mcp.write("bronze.t", df, mode="overwrite", batch_id="b1")
    assert mcp.table_exists("bronze.t")
    assert mcp.row_count("bronze.t") == 3
    # re-writing the same batch overwrites, never duplicates
    mcp.write("bronze.t", df, mode="append", batch_id="b1")
    assert mcp.row_count("bronze.t") == 3
    # a new batch appends
    mcp.write("bronze.t", df, mode="append", batch_id="b2")
    assert mcp.row_count("bronze.t") == 6
    assert mcp.list_tables() == ["bronze.t"]


def test_manifest_lifecycle_and_serialization(tmp_path):
    m = RunManifest.new(trigger="manual", window_from=date(2026, 5, 30),
                        window_to=date(2026, 5, 30), sources=["accounts"], stages=["extract"])
    m.set_stage("extract", StageStatus.PASSED)
    m.finalize("success")
    path = m.save(tmp_path)
    assert path.exists()
    assert m.to_dict()["stages"]["extract"] == "passed"
