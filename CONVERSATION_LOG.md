# Project Conversation Log

> Every user prompt from the build session, each with a short summary of the response/work.
> Chronological. Prompts are reproduced from the session (long ones trimmed); responses condensed.

---

### 1 · Project vision
**Prompt:** I want to build a Snowflake-codenamed banking **AI-agent** project on **Microsoft Fabric** (data lake / OneLake) holding transactions etc. Three steps — **Extract** (land staging), **Transform** (clean, apply math, reformat), **Load** (aggregate + clean visualisation for executives). Enterprise-grade: professional, clear, airtight. AI flow = **trigger → initiator → MCP (fabric remote)**.
**Response:** Inspected the (empty) dir; asked 3 clarifying questions. On answers — **Fabric-only**, **architecture doc first**, agents do **both** orchestration + insight — wrote `ARCHITECTURE.md`, `ADR-0001 (Fabric-only)`, and `README.md`. Mapped the 3 steps to a **medallion** architecture, designed the agent fleet, and set the MCP as the single data seam. Listed 5 open decisions.

### 2 · Key decisions
**Prompt:** (1) Versatile — agents dynamically configured, **schema-on-read** regardless of source; synthetic data for now. (2) Once a day. (3) Inside Fabric. (4) Single currency (EUR). (5) You decide the KPIs by significance.
**Response:** Folded these into `ARCHITECTURE.md` — added the **source-registry / schema-on-read** model (§3.1), nightly trigger, EUR normalisation; **committed 9 executive KPIs** (§4.1); flipped "open questions" → "resolved decisions". Updated README.

### 3 · Draft the next docs
**Prompt:** Yes.
**Response:** Wrote `DATA_MODEL.md` (Bronze/Silver/Gold schemas, lineage metadata, the star schema, KPI formula dictionary, data-quality contracts) and `AGENTS.md` (per-agent spec, the run-manifest object, and the interaction flow).

### 4 · Deployment
**Prompt:** Where do you plan on deploying these agents?
**Response:** Explained the Fabric-native topology (scheduled Data Pipeline trigger, Spark notebooks + serverless functions, OneLake state, managed identity, Key Vault). Flagged two real decisions — model access + MCP placement; both deferred to me → recommended an **external Remote MCP service** and a **swappable ModelProvider**. Wrote `DEPLOYMENT.md`.

### 5 · Teach me the data side
**Prompt:** I'm new to data architecture — explain everything. I'm comfortable with AI/agents/MCPs, just not the data. How does Fabric work?
**Response:** Wrote `PRIMER.md` and a walkthrough: OLTP vs OLAP, warehouse/lake/**lakehouse**, ELT, schema-on-read, Parquet/**Delta**/columnar, partitioning, **star schema** (fact/dim, grain, SCD2), the medallion layers, the semantic model, and every **Fabric** component (OneLake, Lakehouse, notebooks/Spark, pipelines, SQL endpoint, Power BI, capacity).

### 6 · Phase 1
**Prompt:** Let's get started with phase 1.
**Response:** Built the runnable foundation: Python package (venv, `pyproject`), config + **4 source-registry YAMLs** + a contract, **synthetic data generators**, the **ModelProvider** + stub, the **LocalFilesystemMCP**, the **run manifest/audit**, the agent base, and the `generate-data` CLI. Verified: tests pass + realistic Bronze data generated.

### 7 · Test phase 1
**Prompt:** I want to test first.
**Response:** Added `quality.py` (contract validator) and the `inspect-lake` CLI; ran both; **proved the QA gate blocks corrupted data** (injected a duplicate id, bad channel, null, out-of-range → FAIL). 8 tests passing.

### 8 · Phase 2
**Prompt:** Working fine. Let's continue on with phase 2.
**Response:** Built the orchestrated vertical slice: **Extraction / Transformation / Aggregation / Validation** agents + the **Initiator** + the `run-pipeline` CLI. Bronze→Silver→Gold with QA gates; EUR normalisation, **PII tokenisation**, SCD2, surrogate keys; the **9 KPIs** + growth deltas + drill-down marts. Made writes **idempotent by date**. 14 tests passing.

### 9 · Test phase 2
**Prompt:** Once again, I want to run and test it.
**Response:** Extended `inspect-lake` to Silver + Gold; **found & fixed a `dim_date` bug** (10,000 dup rows → one per day) with a guarding test; **proved halt-on-fail** end-to-end via a corrupting connector (run FAILED, no Silver promoted). Gave test commands.

### 10 · A, B, C (AI + trends + Fabric wiring)
**Prompt:** Set up A, B and C in that order. Here's a Gemini API key (use 2.5).
**Response:** Flagged the secret (stored only in gitignored `.env`; advised rotation). **A** — `GeminiProvider` (2.5 Flash, stdlib HTTP, lazy key) + `resolve_secret` + the **Insight agent** (grounded narrative, never invents numbers), wired into the Initiator; live-tested. **B** — date-seeded variation so KPIs trend day-over-day. **C** — Azure **Key Vault** resolver, `RemoteMCP` client, `run_nightly` notebook, `fabric_pipeline.json`, `environments.yaml`. 21 tests passing.

### 11 · Visualisation + full test
**Prompt:** Let's have a visualisation for now. Can you create a Power BI dashboard? We can then run a full test.
**Response:** Built `build-dashboard` — a self-contained **HTML executive dashboard** (KPI cards, 6 charts, AI briefing) — plus Power BI **DAX measures** + `POWERBI.md`. During the 5-day full test a transient Gemini 429 surfaced a flaw → made the **Insight stage best-effort with one retry** (a flaky LLM never fails the run). 24 tests passing.

### 12 · Client deck
**Prompt:** Gamma MCP is connected. Make a McKinsey-style PowerPoint — a client offering.
**Response:** Generated a 10-slide, answer-first **client deck** via Gamma on the "Consultant" theme, exported as **PPTX** (consolidated to Gamma's 10-card limit). Shared the gamma URL; noted edits happen in the Gamma editor.

### 13 · Log to Obsidian
**Prompt:** Summarise the session by logging it in my Obsidian vault at programming/mihir → `snowflake_finance_agents` (mkdir) → 4 detailed `.md` files.
**Response:** Located the vault, created the folder, and wrote **4 cross-linked notes** (Overview & Session Log; Architecture & Data Model; Implementation & Codebase; Usage, Results & Next Steps) with frontmatter, `[[wikilinks]]`, and callouts.

### 14 · Longer briefing + how it's built
**Prompt:** Make the Gemini overview slightly longer. Also explain exactly how this is built — what is the trigger?
**Response:** Lengthened the Insight prompt (≈250–350 words); diagnosed the earlier truncation as Gemini 2.5 **thinking** consuming the token budget → disabled thinking + raised `max_tokens` (the free-tier **daily quota** was exhausted, so couldn't show it live). Explained the trigger (manual `run-pipeline` locally; scheduled **Fabric Data Pipeline** in prod), the full run flow, and the two seams.

### 15 · Per-step commands
**Prompt:** Give commands to start each step.
**Response:** Gave the command sequence — setup → `generate-data` (extract) → `run-pipeline` (transform/aggregate/insight) → `inspect-lake` → `build-dashboard` → `pytest` — and clarified that steps 2–4 run together (each needs the prior); offered a `--stage` flag.

### 16 · True automation → agentic layer
**Prompt:** To make this truly an agent we need automation. How can this be achieved?
**Response:** Separated **autonomous execution** (scheduling: cron/launchd/Fabric pipeline/event triggers) from **autonomous decision-making** (LLM-driven orchestration). You chose the **agentic decision layer** (Fabric). Built the **Supervisor**: on a hard QA fail it reasons via the LLM and chooses **retry / halt / escalate / proceed**, with a safe deterministic fallback and bounded retries — engaged only on failure. 32 tests passing.

### 17 · Explain to an expert
**Prompt:** If I had to explain this whole project to someone with expertise, how exactly would I do it?
**Response:** Gave a layered expert talk-track (one-liner → problem → the two seams + medallion → control flow → "deterministic ETL with the LLM at two guarded points" → honest real-vs-scaffolded) plus a table of likely expert questions with crisp answers.

### 18 · Next step to Fabric-native
**Prompt:** So what is the next step to make this Fabric-native?
**Response:** Identified the one missing piece — an **`OneLakeMCP` Delta backend** — and the full critical path. You chose a **migration checklist**, so I wrote `docs/FABRIC_MIGRATION.md` (provision → `OneLakeMCP` spec → package → secrets → trigger → Power BI → validate → harden).

### 19 · Handover to a data architect
**Prompt:** I'm transferring this to my mother (professional data architect, strong on data, light on AI). Create a handover explaining the AI components + the overall project. She'll set up Fabric and make it production-grade for real banks.
**Response:** Wrote `HANDOVER.md` (repo root): the AI explained for a data expert (deterministic core + two bounded LLM points; AI glossary; Insight + Supervisor; the ModelProvider seam; the **PII-never-reaches-the-LLM** invariant; an AI production checklist for banks; real-vs-scaffolded; roadmap; run instructions). Linked from the README.

### 20 · Initialise + push the repo
**Prompt:** Init the repo, private, and push current version.
**Response:** Confirmed GitHub auth and that `.env` / `data/` / `.venv` are git-ignored; `git init` (branch `main`); staged 62 files and verified **no secrets staged**; flagged an unexpected personal chat transcript and moved to exclude it (interrupted).

### 21 · This log
**Prompt:** I removed that. Create another `.md` file with every prompt I gave and your response.
**Response:** Created this file (`CONVERSATION_LOG.md`).
