"""Dashboard generation test — offline (stub model), no browser needed."""
from datetime import date

from banking_agents.config import ModelConfig, load_source_registry
from banking_agents.dashboard import build, build_context, render_html
from banking_agents.mcp import LocalFilesystemMCP
from banking_agents.model import StubProvider
from banking_agents.orchestration.initiator import Initiator


def _run_two_days(root):
    mcp = LocalFilesystemMCP(root)
    stub = StubProvider(ModelConfig(name="stub-1", provider="stub", key_ref="none", region="eu"))
    init = Initiator(mcp, load_source_registry(), model=stub, seed=42)
    init.run(as_of=date(2026, 5, 28))
    init.run(as_of=date(2026, 5, 29))
    return mcp


def test_context_has_kpis_series_and_marts(tmp_path):
    mcp = _run_two_days(tmp_path / "lake")
    ctx = build_context(mcp)
    assert len(ctx["dates"]) == 2
    assert ctx["latest"]["total_deposits_eur"] > 0
    assert len(ctx["series"]["total_deposits_eur"]) == 2
    assert ctx["deposits_by_segment"] and ctx["txn_by_channel"] and ctx["loans_by_status"]
    assert ctx["narrative"]["text"]  # stub narrative present


def test_render_and_build_writes_html(tmp_path):
    mcp = _run_two_days(tmp_path / "lake")
    html = render_html(build_context(mcp))
    assert "<canvas id=\"c_bs\">" in html and "Executive Dashboard" in html
    assert "__DATA__" not in html  # placeholder was substituted

    out = build(tmp_path / "lake", tmp_path / "dashboard.html")
    assert out.exists() and out.stat().st_size > 1000
