# Agent Specifications

> Companion to [ARCHITECTURE.md](ARCHITECTURE.md) and [DATA_MODEL.md](DATA_MODEL.md).
> Per-agent contract: **goal · trigger/inputs · tools (via Fabric Remote MCP) · outputs ·
> success & failure · prompt sketch.** All agents run **inside Microsoft Fabric** and reach
> data **only** through the Fabric Remote MCP. LLM: latest **Claude Opus**.

---

## 0. Shared conventions

- **Run manifest** — the contract object the Initiator creates and every agent reads/updates:

  ```json
  {
    "run_id": "2026-05-30T02:00Z-nightly",
    "trigger": "schedule",
    "window": { "from": "2026-05-29", "to": "2026-05-30" },
    "sources": ["core_transactions", "accounts", "customers", "loans"],
    "stages": { "extract": "pending", "transform": "pending",
                "aggregate": "pending", "insight": "pending" },
    "qa": [], "audit": [], "status": "running"
  }
  ```

- **Tool plane:** agents call MCP tools — `onelake_read`, `onelake_write`, `run_notebook`,
  `sql_query`, `pipeline_run`, `powerbi_refresh` (conceptual names; see ARCHITECTURE §6).
- **Idempotency:** every write keyed by `run_id` + `_batch_id`; re-runs overwrite by
  partition, never duplicate-append.
- **Every agent** appends a structured entry to `manifest.audit` (inputs, row counts, duration,
  outcome) — this is the immutable run log.
- **Dynamic configuration:** worker agents are *parameterized by a source-registry entry*
  (DATA_MODEL §0 / ARCHITECTURE §3.1). Same code, any domain.

---

## 1. Initiator / Orchestrator Agent  *(control plane)*

| Field | Spec |
|-------|------|
| **Goal** | Own a run end-to-end: plan it, sequence stages, handle failures, finalize & publish. |
| **Trigger / inputs** | Nightly schedule (primary); also event/manual. Reads `config/sources/*.yaml`. |
| **Tools** | `pipeline_run` (monitor), `sql_query` (status checks); spawns/sequences worker agents. |
| **Outputs** | The **run manifest**; final status; triggers `powerbi_refresh`; immutable audit record. |
| **Decision logic** | After each stage + QA gate: **proceed** if pass; on a hard **fail**, delegate to the **Supervisor** (§1a) for an LLM-reasoned action (retry / halt / escalate / proceed). Never promotes a layer that failed QA. |
| **Success** | All stages green, dashboard refreshed, manifest `status=success`, audit complete. |
| **Failure** | Stops the pipeline, records cause + the Supervisor's decision, escalates for human review when warranted; partial run resumable. |

**Prompt sketch:** *"You are the Initiator for a nightly banking-analytics run. Read the source
registry and the run window, build the run manifest, and delegate Extract→Transform→Aggregate
to worker agents, enforcing a QA gate after each. On QA failure, consult the Supervisor for the
action. On success, trigger the insight narrative and the Power BI refresh. Keep the manifest and
audit log accurate at every step. Never advance a stage whose QA gate did not pass."*

---

## 1a. Supervisor — the agentic decision layer

| Field | Spec |
|-------|------|
| **Goal** | Turn the Initiator from a fixed-rule script into a reasoning agent: when a QA gate **hard-fails**, decide the best action instead of always halting. |
| **Inputs** | The failed QA checks (as evidence) + the layer + retries-so-far. |
| **Tools** | The `ModelProvider` (same swappable LLM the Insight agent uses — Gemini in Fabric). |
| **Output** | A structured `Decision{action, reason}` where action ∈ `retry · halt · escalate · proceed`, recorded to the audit log. |
| **Engaged when** | **Only on a hard fail** — healthy runs never call it (no LLM cost). |
| **Safety** | Degrades to deterministic **halt** if no model is wired, the model errors, or the response can't be parsed. Retries are capped (a second retry escalates). It can only make the pipeline *smarter*, never less safe. |

**Prompt sketch:** *"You are the supervising orchestrator. A QA gate has failed; given the failed
checks as evidence, choose retry / halt / escalate / proceed. Be conservative — never proceed when
data integrity is violated. Respond as JSON {action, reason}."*

---

## 2. Extraction Agent  *(Stage 1 → Bronze)*

| Field | Spec |
|-------|------|
| **Goal** | Land raw source data as-is into Bronze with full provenance. |
| **Inputs** | One source-registry entry; run window; watermark for incremental reads. |
| **Tools** | `onelake_read` (source/files), `run_notebook` (connector), `onelake_write` (Bronze). |
| **Outputs** | `bronze.<domain>` Delta partition(s) + lineage metadata; updated watermark. |
| **Behavior** | **No transformation.** Schema-on-read: preserve unknown columns. Incremental by watermark where declared, else full. |
| **Success** | Expected partitions written; row count recorded; watermark advanced. |
| **Failure** | Source unreachable / empty when data expected → fail stage; nothing half-written (atomic by partition). |

**Prompt sketch:** *"You are the Extraction Agent for source `{source_id}`. Read raw data for the
run window and write it unchanged to `{target_bronze}` with lineage metadata. Do not filter,
cast, or reshape. Advance the watermark only after a successful write."*

---

## 3. Transformation Agent  *(Stage 2 → Silver)*

| Field | Spec |
|-------|------|
| **Goal** | Produce conformed, EUR-normalized, PII-tokenized, analysis-ready Silver tables. |
| **Inputs** | Bronze partitions for the run; the source **contract** (`contracts/<source>.yaml`). |
| **Tools** | `run_notebook`/Spark (the heavy transform), `onelake_read`/`onelake_write`. |
| **Behavior** | Type-cast, dedup, conform keys/enums, **apply math** (EUR normalize, signed-amount, `is_*` flags), SCD2 dimensions, tokenize PII. Interpret schema against the contract (schema-on-read). |
| **Outputs** | `silver.dim_*` and `silver.fact_*` (DATA_MODEL §2). |
| **Success** | All declared derivations applied; no residual non-EUR rows; keys resolve. |
| **Failure** | Contract violation it cannot reconcile → fail stage with a precise reason for the QA log. |

**Prompt sketch:** *"You are the Transformation Agent. Read the run's Bronze data and conform it to
the Silver model per the source contract: cast types, deduplicate, normalize all amounts to EUR,
compute derived measures and flags, maintain SCD2 dimensions, and tokenize PII. Document any column
the contract does not cover rather than dropping it silently."*

---

## 4. Aggregation Agent  *(Stage 3 → Gold)*

| Field | Spec |
|-------|------|
| **Goal** | Build the executive Gold marts & KPIs and refresh the semantic model. |
| **Inputs** | Silver facts/dims; prior `gold.kpi_daily` (for deltas). |
| **Tools** | `run_notebook`/Spark (aggregations), `onelake_write` (Gold), `powerbi_refresh`. |
| **Behavior** | Roll up to executive grains; compute the **9 v1 KPIs** + prior-period deltas (DATA_MODEL §4); write `gold.kpi_daily` + drill-down marts. |
| **Outputs** | `gold.kpi_daily`, `gold.deposits_by_segment`, `gold.txn_by_channel`, `gold.loans_by_status`; refreshed Power BI model. |
| **Success** | KPIs within sanity bounds; marts written; semantic model refreshed. |
| **Failure** | KPI out of plausible range → flag for QA/Insight rather than silently publishing. |

**Prompt sketch:** *"You are the Aggregation Agent. From Silver, compute the nine executive KPIs and
their prior-period deltas exactly per the data dictionary, write `gold.kpi_daily` and the drill-down
marts, and refresh the Power BI semantic model. Flag any KPI outside its sanity bounds instead of
publishing it unquestioned."*

---

## 5. Validation / QA Agent  *(gate between every hop)*

| Field | Spec |
|-------|------|
| **Goal** | Enforce data-quality contracts at each layer boundary; gate promotion. |
| **Inputs** | The just-produced layer + its contract (`contracts/*.yaml`, DATA_MODEL §5). |
| **Tools** | `sql_query` (counts, integrity, bounds). |
| **Behavior** | Run schema/row-count/referential/range checks. **Hard fail** blocks promotion; **soft warn** (e.g. large day-over-day KPI swing) passes but is attached to the manifest for the Insight Agent. |
| **Outputs** | A QA result appended to `manifest.qa` with pass/warn/fail + details. |
| **Success** | All hard checks pass. |
| **Failure** | Any hard check fails → returns fail to the Initiator with specifics. |

**Prompt sketch:** *"You are the QA Agent. Validate the freshly written layer against its contract:
schema, row-count tolerance, referential integrity, and value ranges. Return pass, warn, or fail
with concrete evidence. A fail must block promotion; a warn passes but is recorded for the narrative."*

---

## 6. Insight Agent  *(executive narrative layer)*

| Field | Spec |
|-------|------|
| **Goal** | Turn Gold KPIs into a clear, accurate executive narrative + flagged anomalies. |
| **Inputs** | `gold.kpi_daily` (today + history), drill-down marts, any QA warnings. |
| **Tools** | `sql_query` (read Gold), `onelake_write` (narrative table the dashboard binds to). |
| **Behavior** | Compute/restate deltas vs prior period; explain **what changed and why it matters**; surface anomalies and QA warnings; offer concise, grounded recommendations. **No numbers invented** — every figure traces to Gold. |
| **Outputs** | `gold.exec_narrative` (date, headline, per-KPI commentary, anomalies, recommendations). |
| **Success** | Narrative is factual, traceable, executive-readable; anomalies addressed. |
| **Failure** | Insufficient/uncertain data → state the limitation explicitly rather than speculate. |

**Prompt sketch:** *"You are the Insight Agent writing for bank executives. Using only the Gold KPIs
and their history, summarize the day's performance, explain the most material movements, flag
anomalies (including any QA warnings), and give a few grounded recommendations. Every figure must
trace back to the Gold marts — never invent or estimate numbers. Be concise, precise, and neutral."*

---

## 7. Interaction summary

```
Initiator ─plan→ manifest
  ├─▶ Extraction  → bronze.*   ─▶ QA gate ─┐
  ├─▶ Transform   → silver.*   ─▶ QA gate ─┼─▶ Initiator: proceed / retry / halt
  ├─▶ Aggregation → gold.*     ─▶ QA gate ─┘
  └─▶ Insight     → gold.exec_narrative
Initiator ─finalize→ powerbi_refresh + audit record + status
```

All boxes touch storage **only** through the Fabric Remote MCP.
