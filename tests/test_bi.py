"""Tests for the generative-BI layer: catalog, VisualizationAgent selection, and rendering."""
from datetime import date

import pandas as pd
import pytest

from banking_agents.agents.visualization import VisualizationAgent
from banking_agents.bi.catalog import VISUAL_TYPES, load_catalog
from banking_agents.bi.render import build_report
from banking_agents.config import ModelConfig
from banking_agents.mcp import LocalFilesystemMCP
from banking_agents.model import ModelProvider, ModelResponse
from banking_agents.synthetic import business_marts

D1, D2 = date(2026, 5, 28), date(2026, 5, 29)


class ScriptedProvider(ModelProvider):
    def __init__(self, text):
        super().__init__(ModelConfig(name="x", provider="stub", key_ref="none", region="eu"))
        self.text = text

    def complete(self, system, messages):
        return ModelResponse(text=self.text)


@pytest.fixture
def seeded(tmp_path):
    root = tmp_path / "lake"
    mcp = LocalFilesystemMCP(root)
    # minimal finance Gold
    kpi_cols = dict(total_deposits_eur=9.8e6, deposits_growth_pct=0.2, net_loans_eur=2.0e7,
                    loans_growth_pct=0.3, loan_to_deposit_ratio=2.05, nim_pct=0.4,
                    fee_income_eur=15000.0, txn_count=5000, txn_value_eur=3.9e5, npl_rate_pct=17.0,
                    active_customers=790, net_new_customers=2, digital_channel_mix_pct=40.5)
    for i, d in enumerate((D1, D2)):
        mcp.write("gold.kpi_daily", pd.DataFrame([{"date": d.isoformat(), **kpi_cols}]),
                  mode="append", batch_id=d.isoformat())
    mcp.write("gold.deposits_by_segment", pd.DataFrame(
        [{"date": D2.isoformat(), "segment": s, "deposits_eur": v}
         for s, v in [("retail", 5e6), ("sme", 2e6), ("corporate", 2e6), ("private", 0.8e6)]]),
        mode="overwrite", batch_id=D2.isoformat())
    mcp.write("gold.loans_by_status", pd.DataFrame(
        [{"date": D2.isoformat(), "status": s, "principal_outstanding_eur": v}
         for s, v in [("current", 1.6e7), ("delinquent", 2e6), ("default", 2e6)]]),
        mode="overwrite", batch_id=D2.isoformat())
    mcp.write("gold.txn_by_channel", pd.DataFrame(
        [{"date": D2.isoformat(), "channel": c, "txn_count": 1000, "txn_value_eur": 80000.0}
         for c in ["mobile", "web", "atm", "branch", "card"]]),
        mode="overwrite", batch_id=D2.isoformat())
    # HR / Ops / source marts
    business_marts.seed(D1, root)
    business_marts.seed(D2, root)
    return mcp, root


# -- catalog -------------------------------------------------------------------
def test_catalog_loads_all_domains_and_is_valid():
    cat = load_catalog()
    domains = {t.domain for t in cat.values()}
    assert {"finance", "hr", "operations", "source"} <= domains
    assert "finance_executive_overview" in cat and "source_activity" in cat
    for t in cat.values():
        assert t.visuals
        for v in t.visuals:
            assert v["type"] in VISUAL_TYPES and v["source"].startswith("gold.")
    assert cat["source_activity"].supports_source_filter


# -- agent selection -----------------------------------------------------------
def test_keyword_fallback_picks_right_domain():
    a = VisualizationAgent(model=None)
    assert a.select("show me workforce attrition and retention").template.domain == "hr"
    assert a.select("operations channel uptime and sla").template.domain == "operations"
    assert a.select("finance executive overview for the board").template.id == "finance_executive_overview"


def test_source_filter_is_extracted():
    a = VisualizationAgent(model=None)
    sel = a.select("loans activity by source")
    assert sel.template.id == "source_activity" and sel.source == "loans"


def test_model_selection_is_honoured_and_validated():
    a = VisualizationAgent(model=ScriptedProvider('{"template_id":"hr_cost","source":null,"reason":"r"}'))
    sel = a.select("anything")
    assert sel.template.id == "hr_cost" and sel.via == "model"
    # invalid id from model -> falls back to keywords (still a valid template)
    a2 = VisualizationAgent(model=ScriptedProvider('{"template_id":"does_not_exist"}'))
    assert a2.select("workforce headcount").via == "fallback"


# -- rendering -----------------------------------------------------------------
def test_render_finance_and_source_reports(seeded):
    mcp, root = seeded
    a = VisualizationAgent(model=None)
    out = root.parent / "reports"

    p1 = build_report(a.select("finance executive overview"), mcp, out)
    assert p1.exists()
    html = p1.read_text()
    assert "__DATA__" not in html and "Executive Overview" in html and "canvas" in html

    p2 = build_report(a.select("loans activity by source"), mcp, out)
    assert p2.exists() and "loans" in p2.name

    p3 = build_report(a.select("workforce attrition"), mcp, out)
    assert p3.exists() and "Attrition" in p3.read_text()
