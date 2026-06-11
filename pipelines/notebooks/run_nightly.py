# Fabric notebook: run_nightly  (direct OneLake — the fast path)
# ---------------------------------------------------------------------------
# Runs the whole agentic pipeline (Extract -> Transform -> Aggregate -> QA gates ->
# Supervisor -> Insight) and writes the medallion as DELTA tables into the attached
# Lakehouse via OneLakeMCP. The nightly Data Pipeline (fabric_pipeline.json) schedules this.
#
# Setup expected (see the deploy steps): the repo is available on the notebook driver
# (git clone) and editable-installed, with a default Lakehouse attached. Agent code is
# IDENTICAL to local — only the MCP backend (OneLakeMCP) and the storage root differ.
# ---------------------------------------------------------------------------
import os
from datetime import date, timedelta

from banking_agents.config import ModelConfig, load_source_registry
from banking_agents.mcp import OneLakeMCP
from banking_agents.model import get_provider
from banking_agents.orchestration.initiator import Initiator

# --- parameters (Fabric injects via a parameter cell) ----------------------
as_of = None          # e.g. "2026-06-11"; None -> yesterday

run_date = date.fromisoformat(as_of) if as_of else date.today() - timedelta(days=1)

# Writes Delta to the attached Lakehouse (/lakehouse/default/Tables/<schema>/<table>).
mcp = OneLakeMCP()

# Optional AI: if a Gemini key is available (env or Key Vault) the Insight briefing runs;
# otherwise the pipeline still produces all KPIs (insight is best-effort, non-fatal).
try:
    model = get_provider(ModelConfig.load()) if os.environ.get("GEMINI_API_KEY") else None
except Exception:
    model = None

manifest = Initiator(mcp, load_source_registry(), model=model).run(as_of=run_date, trigger="schedule")

print(f"run_id={manifest.run_id} status={manifest.status}")
for q in manifest.qa:
    print(f"  QA {q.stage}: {q.outcome} - {q.details}")
print("tables:", mcp.list_tables())
if manifest.status != "success":
    raise SystemExit(f"Pipeline run {manifest.run_id} ended with status={manifest.status}")
