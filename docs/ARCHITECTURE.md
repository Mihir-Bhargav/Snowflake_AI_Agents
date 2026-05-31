# Banking Analytics AI-Agent Platform — Architecture & Design

> **Codename:** Snowflake_AI_Agents
> **Status:** Draft v0.1 (design — no implementation yet)
> **Data platform:** Microsoft Fabric (OneLake) — single source of truth
> **Tool plane:** Fabric Remote MCP
> **Audience:** Enterprise architecture review, data engineering, executive sponsors

---

## 1. Objective

Build an enterprise-grade, agent-driven analytics platform that turns raw banking data
stored in a Microsoft Fabric data lake into clean, aggregated, executive-ready dashboards.

The platform must be **professional, clear, and airtight**: every run is reproducible,
auditable, governed, and recoverable. AI agents both **orchestrate the pipeline** and
**generate the executive insight layer** on top of the curated data.

It must also be **versatile**: agents are **configured dynamically** and operate
**schema-on-read**, so a new or changed source can be onboarded by configuration rather
than code. Runtime is **inside Microsoft Fabric**, the cadence is a **once-daily nightly
batch**, and all monetary values are reported in a **single currency (EUR)**. For the
current phase we develop against **synthetic banking data** that mimics real source shapes.

The pipeline is expressed in three steps, mapped onto a **medallion architecture**:

| # | Your step      | Action                                                        | Medallion layer        |
|---|----------------|---------------------------------------------------------------|------------------------|
| 1 | **Extract**    | Read raw data, land it in a staging area                      | **Bronze** (raw)       |
| 2 | **Transform**  | Clean, apply math/derivations, reshape & conform              | **Silver** (conformed) |
| 3 | **Load**       | Aggregate, finalize, and publish for visualization            | **Gold** (serving)     |

---

## 2. High-level architecture

```
                         ┌──────────────────────────────────────────────┐
   TRIGGER               │              AGENT CONTROL PLANE              │
 (schedule / event /  ──▶│                                              │
  manual / file land)    │   ┌────────────┐                            │
                         │   │ INITIATOR  │  plans the run, owns state │
                         │   │  (Orches-  │  & the run manifest        │
                         │   │   trator)  │                            │
                         │   └─────┬──────┘                            │
                         │         │ delegates                         │
                         │   ┌─────▼───────────────────────────────┐  │
                         │   │   WORKER AGENTS (per stage)          │  │
                         │   │  Extract → Transform → Aggregate     │  │
                         │   │           ↓                          │  │
                         │   │   Quality/Validation gate (each hop) │  │
                         │   │           ↓                          │  │
                         │   │        Insight Agent                 │  │
                         │   └──────────────┬───────────────────────┘  │
                         └──────────────────┼──────────────────────────┘
                                            │ all data ops go through
                                            ▼
                              ┌────────────────────────────┐
                              │     FABRIC REMOTE MCP       │  (single tool plane)
                              │  OneLake · Pipelines ·      │
                              │  Notebooks · SQL endpoint · │
                              │  Power BI semantic model    │
                              └──────────────┬─────────────┘
                                             ▼
        ┌───────────────────────── MICROSOFT FABRIC / OneLake ─────────────────────────┐
        │                                                                              │
        │   BRONZE (raw/staging) ──▶ SILVER (clean, math) ──▶ GOLD (aggregated)        │
        │      transactions             conformed facts          exec KPIs / marts     │
        │      accounts, customers      derived measures         semantic model        │
        │                                                              │               │
        └──────────────────────────────────────────────────────────────┼──────────────┘
                                                                        ▼
                                                            ┌────────────────────┐
                                                            │  POWER BI DASHBOARDS│
                                                            │  + AI exec narrative│
                                                            └────────────────────┘
```

**Core principle:** Agents never touch storage directly. *All* reads/writes/compute go
through the **Fabric Remote MCP**, which gives us one governed, auditable, mockable seam.

---

## 3. Data domains (banking)

Source domains expected to land in Bronze. (Refined per source-system survey.)

| Domain         | Example entities                                   | Notes                          |
|----------------|----------------------------------------------------|--------------------------------|
| Transactions   | postings, authorizations, transfers, fees          | High volume; append-only       |
| Accounts       | account master, balances, status, product type     | Slowly changing                |
| Customers      | party, KYC attributes, segments                    | **PII** — must be masked       |
| Cards          | card master, card transactions                     | PCI-sensitive                  |
| Loans/Credit   | facilities, schedules, delinquency, arrears        | Drives risk KPIs               |
| Reference      | branches, GL accounts, product catalog             | Dimensions / lookups           |

> **Phase note:** sources are currently **synthetic** generators producing realistic
> shapes/volumes for each domain. Because the pipeline is schema-on-read (below), swapping a
> synthetic source for a real one is a config change, not a redesign.

### 3.1 Versatility — dynamic config & schema-on-read

The platform onboards sources by **configuration, not code**. A **source registry**
(`config/sources/*.yaml`) declares each source; the **Initiator** reads it at run time and
configures the worker agents dynamically. Nothing about a source is hard-coded in an agent.

A source entry declares (illustrative):

```yaml
source_id: core_transactions
domain: transactions
connector: synthetic            # later: jdbc | rest | onelake_files | cdc
read_mode: incremental          # full | incremental
watermark: posting_ts           # column driving incremental reads
target_bronze: bronze.transactions
pii_columns: []                 # tokenized at Silver if present
contract: contracts/transactions.yaml
```

- **Schema-on-read:** Bronze captures whatever arrives (Delta/Parquet, tolerant of new or
  missing columns). The schema is *interpreted* at the Silver step against the source's
  declared **contract**, not enforced at ingest. New columns flow through harmlessly;
  contract changes are versioned.
- **Dynamic agents:** each worker agent is parameterized by the source entry — same agent
  code handles transactions, accounts, loans, or a brand-new domain.
- **Reporting currency:** **EUR** only; any non-EUR amounts are normalized at Silver.

---

## 4. The three stages in detail

### Stage 1 — Extract (→ Bronze / staging)
- **Goal:** land raw source data *as-is*, immutable, with full provenance.
- **Actions:** connect to source (DB/API/file drop in OneLake), read incrementally
  (watermark/CDC where available), write to Bronze in Delta/Parquet.
- **Rules:** no transformation, no filtering of columns; capture `_ingested_at`,
  `_source_system`, `_batch_id`, `_source_file`. Schema-on-read tolerant.
- **Idempotency:** re-running a batch with the same `_batch_id` is a no-op (overwrite by
  partition), never a duplicate-append.

### Stage 2 — Transform (→ Silver / conformed)
- **Goal:** trustworthy, conformed, analysis-ready facts and dimensions.
- **Actions:** type-cast, deduplicate, standardize codes, **apply math/derivations**
  (e.g. net flow = credits − debits, running balances, fee revenue, FX normalization to
  reporting currency), conform keys across domains, mask/tokenize PII.
- **Rules:** every derived column documented in the data dictionary; nulls and outliers
  handled by explicit policy, not silently dropped.
- **Output:** star-schema-ready conformed fact & dimension tables.

### Stage 3 — Load / Aggregate (→ Gold / serving)
- **Goal:** small, fast, executive-grade aggregates and KPIs.
- **Actions:** roll up to the grains execs care about (daily/branch/product/segment),
  compute KPIs, build the **Power BI semantic model** (measures, hierarchies).
- **Output:** Gold marts + semantic model that the dashboards bind to.

### 4.1 v1 Executive KPI set (committed)

Chosen for executive significance and derivability from the core synthetic domains
(transactions, accounts, loans, customers). All monetary values in **EUR**, refreshed nightly.

| # | KPI                          | Definition (plain)                                   | Why it matters / source domain        |
|---|------------------------------|------------------------------------------------------|---------------------------------------|
| 1 | **Total Deposits (€)**       | Sum of deposit-account balances + period growth %    | Funding base & momentum / accounts    |
| 2 | **Net Loans Outstanding (€)**| Sum of loan principal outstanding + growth %         | Core earning assets / loans           |
| 3 | **Loan-to-Deposit Ratio**    | Net loans ÷ total deposits                           | Liquidity & balance-sheet health      |
| 4 | **Net Interest Margin (NIM)**| (Interest income − interest expense) ÷ earning assets| Core profitability / loans + deposits |
| 5 | **Fee & Commission Income (€)**| Sum of fee/commission transactions                 | Non-interest revenue / transactions   |
| 6 | **Transaction Volume & Value**| Count and € value of postings in period             | Activity & customer engagement / txns |
| 7 | **NPL / Delinquency Rate**   | Loans 90+ days past due ÷ total loans                | Credit-risk health / loans            |
| 8 | **Active Customers + Net New**| Distinct customers transacting + net change          | Franchise growth / customers + txns   |
| 9 | **Digital Channel Mix**      | Share of transactions via digital vs. branch         | Channel strategy / transactions       |

Each KPI carries a **prior-period delta** and trend so the **Insight Agent** can write the
"what changed and why it matters" narrative for executives.

---

## 5. Agent fleet

Agents do **both** jobs: orchestrate the ETL **and** generate the insight layer.

| Agent                 | Type        | Responsibility                                                                 |
|-----------------------|-------------|--------------------------------------------------------------------------------|
| **Initiator / Orchestrator** | Control | Receives the trigger, builds the **run manifest**, sequences stages, owns retries, status, and final sign-off. The "initiator" in your flow. |
| **Extraction Agent**  | Worker      | Stage 1. Pulls raw data into Bronze via MCP; records provenance & watermarks.  |
| **Transformation Agent** | Worker   | Stage 2. Cleaning, math, conforming, PII masking into Silver.                  |
| **Aggregation Agent** | Worker      | Stage 3. Builds Gold marts, KPIs, and refreshes the semantic model.            |
| **Validation / QA Agent** | Gate    | Runs data-quality contracts **between every hop**; blocks promotion on failure.|
| **Insight Agent**     | Analyst     | Reads Gold, computes deltas vs. prior period, writes the **executive narrative**, flags anomalies and recommendations for the dashboard. |

### Control flow (trigger → initiator → workers)
```
TRIGGER
  → INITIATOR builds run_manifest {run_id, window, scope, expected sources}
      → Extraction Agent ──▶ QA gate ──┐
                                       ├─ fail → INITIATOR decides: retry / halt / alert
      → Transformation Agent ─▶ QA gate┤
      → Aggregation Agent ────▶ QA gate┘
      → Insight Agent (narrative + anomalies)
  → INITIATOR finalizes run, writes audit record, publishes dashboard refresh
```

**Primary trigger:** a **nightly schedule** (once daily). Event/file-landing,
manual/on-demand, and upstream-pipeline completion are also supported as alternate triggers.

---

## 6. Fabric Remote MCP — the tool plane

The Fabric Remote MCP is the **only** way agents interact with the platform. It exposes
Fabric capabilities as governed tools. Conceptual tool surface the agents rely on:

| Capability        | Used by                | Purpose                                          |
|-------------------|------------------------|--------------------------------------------------|
| OneLake file/Delta I/O | Extract, Transform, Aggregate | Read/write Bronze/Silver/Gold tables.    |
| Run notebook / Spark job | Transform, Aggregate | Execute heavy transforms & aggregations close to data. |
| SQL endpoint query | QA, Insight           | Validate counts, read KPIs for narrative.        |
| Data pipeline run  | Initiator             | Kick off / monitor native Fabric pipelines.      |
| Power BI refresh / semantic model | Aggregation | Publish the serving model for dashboards. |

**Why a single MCP seam:** governance (every call authz'd & logged), testability (mock the
MCP for local dev), and a clean boundary between agent *reasoning* and platform *execution*.

---

## 7. Enterprise guardrails (the "airtight" part)

- **Lineage & provenance:** every row carries batch/source metadata; run manifest links
  inputs → outputs for full traceability.
- **Data-quality contracts:** schema, row-count tolerances, referential integrity, null/range
  checks enforced at each QA gate; failures block promotion to the next layer.
- **Idempotency & recovery:** stages keyed by `run_id`/`batch_id`; safe to re-run; partial
  failures resume from last good checkpoint, never double-count.
- **PII / regulatory:** customer identifiers masked/tokenized at Silver; PCI fields isolated;
  access controlled per layer (Bronze locked down, Gold broadly readable).
- **Audit:** immutable run log — who/what/when, inputs, row counts, QA results, sign-off.
- **Observability:** structured run status, metrics, and alerting on the Initiator.
- **Secrets:** no credentials in code; sourced from a vault/Fabric-managed identity.
- **Determinism:** same inputs + same code → same Gold output (reproducible reporting).

---

## 8. Proposed repository layout

```
Snowflake_AI_Agents/
├── README.md
├── pyproject.toml / requirements.txt   ← Python package + deps
├── docs/
│   ├── PRIMER.md                ← data-architecture & Fabric primer (start here if new)
│   ├── ARCHITECTURE.md          ← this document
│   ├── DATA_MODEL.md            ← Bronze/Silver/Gold schemas & data dictionary
│   ├── AGENTS.md                ← per-agent spec: goal, tools, prompts, I/O contract
│   ├── DEPLOYMENT.md            ← where agents run in Fabric; MCP & model access
│   └── adr/0001-fabric-only.md  ← architecture decision records
├── config/
│   ├── model.yaml               ← swappable model provider config
│   └── sources/*.yaml           ← the source registry (drives dynamic agents)
├── contracts/*.yaml             ← data-quality contracts (schema + checks)
├── src/banking_agents/          ← the Python package
│   ├── config.py                ← load model config + source registry
│   ├── model/                   ← ModelProvider interface + providers (stub wired)
│   ├── mcp/                     ← MCP client interface + LocalFilesystem backend
│   ├── orchestration/           ← RunManifest + audit log
│   ├── agents/                  ← Agent base (stage agents land in Phase 2)
│   └── synthetic/               ← synthetic data generators + `generate-data` CLI
├── data/                        ← generated lake (gitignored): lake/<layer>/<table>, ops/manifests
└── tests/                       ← smoke + (later) pipeline/contract tests
```

> _Phase 1 status:_ scaffolding, config, synthetic generators, MCP local backend,
> model-provider seam, and run-manifest/audit — done & tested.
>
> _Phase 2 status:_ the orchestrated vertical slice — Extraction / Transformation /
> Aggregation / Validation agents + Initiator — runs Bronze→Silver→Gold with QA gates and
> produces the 9 KPIs. EUR-normalization, PII tokenization, surrogate keys, growth deltas,
> and per-day idempotency are implemented & tested (`run-pipeline`).
>
> _Phase 3 status:_ the **Insight agent** writes a grounded executive briefing over Gold via
> a real LLM (Gemini 2.5 through the swappable `ModelProvider`; key from `.env`/Key Vault).
> Synthetic data now trends day-over-day. **Fabric deployment** is wired as scaffolding:
> `RemoteMCP` client, orchestrator notebook (`pipelines/notebooks/run_nightly.py`), Data
> Pipeline (`pipelines/fabric_pipeline.json`), env bindings (`deploy/environments.yaml`), and
> Azure Key Vault resolution. The local HTML dashboard + Power BI DAX binding are done.
>
> _Agentic layer:_ a **Supervisor** (docs/AGENTS.md §1a) makes the Initiator *reason* on a hard
> QA failure — choosing retry / halt / escalate / proceed via the LLM, with a safe deterministic
> fallback. It engages only on failures (no cost on healthy runs) and runs inside the `run_nightly`
> notebook in Fabric. Still ahead: standing up a real Fabric tenant, and autonomous **scheduling**
> (Fabric Data Pipeline / event triggers) so runs fire without a human.

---

## 9. Resolved decisions

| # | Decision        | Choice                                                                       |
|---|-----------------|------------------------------------------------------------------------------|
| 1 | **Sources**     | **Synthetic** generators for now; onboarded via the source registry (§3.1). Schema-on-read so real sources swap in by config. |
| 2 | **Run cadence** | **Once daily** — nightly batch (alternate triggers supported, §5).           |
| 3 | **Agent runtime** | **Inside Microsoft Fabric** (notebooks/Spark). LLM: latest **Claude Opus** for orchestration + insight. |
| 4 | **Currency/calendar** | **EUR only**; non-EUR amounts normalized at Silver. Standard calendar (fiscal calendar TBD if needed). |
| 5 | **v1 KPIs**     | 9 committed executive KPIs — see **§4.1**.                                   |

### Still to confirm later
- Synthetic-data volumes & realism level (rows/day per domain) for performance testing.
- Fabric capacity sizing for nightly Spark + SQL endpoint + Power BI refresh.
- Whether a fiscal (non-calendar) reporting calendar is required.

---

## 10. Phased delivery (recommended)

| Phase | Outcome                                                                 |
|-------|------------------------------------------------------------------------|
| 0     | Approve this architecture; lock data model & v1 KPI list.              |
| 1     | Scaffold repo + Fabric Remote MCP wiring + run manifest/audit skeleton.|
| 2     | Vertical slice: one domain (transactions) Bronze→Silver→Gold + 1 chart.|
| 3     | Add QA gates + Validation agent + idempotency/recovery.               |
| 4     | Insight agent + full executive dashboard + semantic model.            |
| 5     | Harden: governance, alerting, scheduling, multi-domain scale-out.     |
```
