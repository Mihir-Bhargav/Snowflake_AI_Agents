"""Insight Agent tests — use the offline StubProvider so tests need no network/key."""
from datetime import date

from banking_agents.agents.insight import InsightAgent
from banking_agents.config import ModelConfig, load_source_registry
from banking_agents.mcp import LocalFilesystemMCP
from banking_agents.model import StubProvider
from banking_agents.orchestration.initiator import Initiator

D1 = date(2026, 5, 28)


def _stub():
    return StubProvider(ModelConfig(name="stub-1", provider="stub", key_ref="none", region="eu"))


def test_insight_stage_runs_and_writes_narrative(tmp_path):
    mcp = LocalFilesystemMCP(tmp_path / "lake")
    init = Initiator(mcp, load_source_registry(), model=_stub(), seed=42)
    m = init.run(as_of=D1)

    assert m.status == "success"
    assert "insight" in m.stages and m.stages["insight"] == "passed"
    assert mcp.table_exists("gold.exec_narrative")
    narr = mcp.read("gold.exec_narrative")
    assert len(narr) == 1
    assert narr.iloc[0]["date"] == D1.isoformat()
    assert narr.iloc[0]["narrative"]  # non-empty


def test_pipeline_without_model_skips_insight(tmp_path):
    mcp = LocalFilesystemMCP(tmp_path / "lake")
    init = Initiator(mcp, load_source_registry(), seed=42)  # no model
    m = init.run(as_of=D1)
    assert m.status == "success"
    assert "insight" not in m.stages
    assert not mcp.table_exists("gold.exec_narrative")


class _FailingProvider(StubProvider):
    def complete(self, system, messages):
        raise RuntimeError("simulated LLM outage")


def test_insight_failure_is_non_fatal(tmp_path):
    """A transient LLM failure must not fail the run — KPIs are already complete."""
    mcp = LocalFilesystemMCP(tmp_path / "lake")
    bad = _FailingProvider(ModelConfig(name="x", provider="stub", key_ref="none", region="eu"))
    init = Initiator(mcp, load_source_registry(), model=bad, seed=42)
    m = init.run(as_of=D1)

    assert m.status == "success"                       # run still succeeds
    assert m.stages["insight"] == "failed"             # but insight is flagged
    assert any(q.stage == "insight" and q.outcome == "warn" for q in m.qa)
    assert mcp.table_exists("gold.kpi_daily")          # the KPIs survived
    assert not mcp.table_exists("gold.exec_narrative")
