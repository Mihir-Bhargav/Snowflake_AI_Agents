# Snowflake_AI_Agents

Enterprise banking analytics platform where **AI agents** turn raw data in a
**Microsoft Fabric** data lake into clean, aggregated, executive-ready dashboards.

> "Snowflake" is the project codename. The data platform is **Microsoft Fabric / OneLake**
> (see [ADR 0001](docs/adr/0001-fabric-only.md)).

> 📌 **New owner?** Start with **[HANDOVER.md](HANDOVER.md)** — it explains the AI components
> (for a data expert new to AI) and the path to Microsoft Fabric + production.

## What it does — three steps

1. **Extract** — read raw banking data, land it in staging → **Bronze**
2. **Transform** — clean, apply math/derivations, reshape → **Silver**
3. **Load** — aggregate into executive KPIs & publish dashboards → **Gold**

## How it works
A **trigger** starts a run. An **Initiator** agent plans it and delegates to worker agents
(Extract → Transform → Aggregate), with a **Validation** gate between each hop. An
**Insight** agent then writes the executive narrative. All data operations flow through the
**Fabric Remote MCP** — agents never touch storage directly.

## Key decisions
Microsoft Fabric runtime · **nightly** (once-daily) batch · **EUR** only · **synthetic**
data for now · **schema-on-read** with dynamically-configured agents (sources onboarded by
config) · 9 committed executive KPIs. Details in the architecture doc §9 and §4.1.

## Status
**Phases 1–3 complete.** The full nightly flow runs end-to-end: Extraction → Transformation
→ Aggregation → **Insight** agents, sequenced by the Initiator with a QA gate after each hop.
Gold holds the 9 executive KPIs (idempotent per day, with day-over-day trends) and an
LLM-written executive briefing (Gemini 2.5 via the swappable `ModelProvider`). Fabric
deployment wiring (Remote MCP client, orchestrator notebook, Data Pipeline, Key Vault) is
scaffolded in `pipelines/` + `deploy/`. New here? Read **[docs/PRIMER.md](docs/PRIMER.md)** first.

> **Model config:** `config/model.yaml` selects the provider; the key resolves from `.env`
> locally (`GEMINI_API_KEY`) or Key Vault in Fabric. Set `provider: stub` to run fully offline.

## Getting started
```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"
run-pipeline --as-of 2026-05-29        # full Bronze→Silver→Gold run + executive KPIs + AI briefing
build-dashboard                        # render data/dashboard.html (open it in a browser)
seed-marts --as-of 2026-05-29          # synthetic HR/Operations/by-source Gold marts (for BI)
make-report "workforce attrition by department"   # agentic, template-driven report → data/reports/
inspect-lake                           # summarize tables, integrity & contract checks
pytest -q                              # run the test suite (37 tests)
```
`generate-data --as-of <date>` runs the extract stage only. Generated data lands under
`data/lake/<layer>/<table>/`, manifests in `data/ops/manifests/` (both gitignored).

## Docs
- [**Primer**](docs/PRIMER.md) — new to data architecture? Start here: warehouses, lakes, star schemas, and how Fabric works
- [Architecture & design](docs/ARCHITECTURE.md)
- [ADR 0001 — Fabric-only](docs/adr/0001-fabric-only.md)
- [Data model](docs/DATA_MODEL.md) — Bronze/Silver/Gold schemas, lineage, KPI dictionary
- [Agent specs](docs/AGENTS.md) — per-agent goal, tools, I/O contract, prompt
- [Deployment](docs/DEPLOYMENT.md) — where agents run in Fabric, MCP placement, model access
- [Fabric Migration Runbook](docs/FABRIC_MIGRATION.md) — step-by-step local → Fabric-native cutover
- [Power BI binding](dashboards/POWERBI.md) — local HTML dashboard + Fabric semantic-model (DAX) guide
