# Fabric notebook: run_nightly
# ---------------------------------------------------------------------------
# This file is the source for a Microsoft Fabric notebook. The nightly Data
# Pipeline (pipelines/fabric_pipeline.json) schedules and triggers it once a day.
#
# It runs the whole orchestrated flow (Extract -> Transform -> Aggregate -> QA gates ->
# Insight) via the Initiator, talking to OneLake ONLY through the Fabric Remote MCP and
# to the model through the configured ModelProvider. Agent code is identical to local dev;
# only the MCP backend and the secret source change.
#
# Pipeline parameters (injected by the Data Pipeline at runtime):
#   as_of  : reporting date (YYYY-MM-DD), defaults to yesterday
#   mcp_url: base URL of the Fabric Remote MCP service (Azure Container Apps)
# ---------------------------------------------------------------------------
from datetime import date, timedelta

from banking_agents.config import ModelConfig, load_source_registry
from banking_agents.mcp import RemoteMCP
from banking_agents.model import get_provider
from banking_agents.orchestration.initiator import Initiator

# --- parameters (Fabric injects these via a parameter cell) ----------------
as_of = None          # e.g. "2026-05-29"; None -> yesterday
mcp_url = None         # e.g. "https://banking-ai-mcp.<region>.azurecontainerapps.io"

run_date = date.fromisoformat(as_of) if as_of else date.today() - timedelta(days=1)

# --- wiring (the only things that differ from local dev) -------------------
mcp = RemoteMCP(base_url=mcp_url, token_ref="env://FABRIC_MCP_TOKEN")
model = get_provider(ModelConfig.load())          # provider: gemini; key from Key Vault
registry = load_source_registry()

# The Initiator wires the agentic Supervisor automatically from `model`: on a hard QA fail it
# reasons (retry / halt / escalate / proceed) via the same LLM, instead of blindly halting.
initiator = Initiator(mcp, registry, model=model)
manifest = initiator.run(as_of=run_date, trigger="schedule")

# Surface status to the pipeline; a non-success exits non-zero so the pipeline alerts.
print(f"run_id={manifest.run_id} status={manifest.status}")
for q in manifest.qa:
    print(f"  QA {q.stage}: {q.outcome} - {q.details}")
if manifest.status != "success":
    raise SystemExit(f"Pipeline run {manifest.run_id} ended with status={manifest.status}")
