# Fabric Integration — Productionising for a Real Bank Client

> The end-to-end plan to take this platform from local prototype to **running on Microsoft
> Fabric against a real bank's live data**, including the AI layers (the scheduled narrative
> **and** an on-demand "ask the data" capability). Five phases, in order.
>
> Related: [docs/FABRIC_MIGRATION.md](docs/FABRIC_MIGRATION.md) (the technical cutover runbook) ·
> [HANDOVER.md](HANDOVER.md) (the AI components explained) · [docs/DEPLOYMENT.md](docs/DEPLOYMENT.md).

## What already exists vs. what's new
- **Already built & tested:** the full deterministic ELT (Bronze→Silver→Gold), the agent fleet,
  QA gates, idempotency, the 9 KPIs, the scheduled **AI briefing**, the **Supervisor** decision
  layer, the HTML dashboard, Power BI DAX measures, and the Fabric deployment **scaffolding**.
- **New for this engagement:** real-tenant provisioning, **real source connectors**, banking
  **security/compliance**, production reliability, and the **on-demand interactive AI** ("convert
  raw data into insights if asked").

> [!important] Two decisions that shape everything below
> 1. **Capacity tier** — **F64+** unlocks Fabric's native Copilot / Data Agent for the on-demand
>    insight feature (Phase 4b). A smaller F-SKU works if you build that agent yourself.
> 2. **Model placement** — the batch narrative uses **Gemini** today; Fabric's interactive Data
>    Agent is **Azure-OpenAI-backed inside Fabric**. A bank may want to consolidate both onto
>    Azure OpenAI for one residency/governance story (a one-line provider swap on the batch side).

---

## Phase 1 — Foundation: Fabric hosting + compliance gate

**Goal:** stand up the hosting environment and clear the governance hurdles *before* any bank
data moves.

**Compliance gate (do this first — it gates everything):**
- Data Processing Agreement and **data classification**; decide **residency** (which Azure region —
  e.g. EU data stays in the EU).
- A **model-governance policy**: where the LLM runs, what it may see, and human-oversight rules.
  Banks treat AI under model-risk frameworks; GDPR, the EU AI Act, and BCBS 239 may apply.
- Security-architecture sign-off of the design in the later phases.

**Hosting on Fabric:**
- Provision a **Fabric capacity** (F64+ if you want native Copilot/Data Agent; see decision #1) and
  enable the relevant **tenant AI settings**.
- Create **workspaces** (dev / test / prod) and a **schema-enabled Lakehouse** in OneLake.
- **Network & identity:** private endpoints / managed VNet, Entra ID conditional access, and the
  workspace **managed identity** (OneLake is encrypted at rest by default).

**Why it matters:** for a bank, "hosted on Fabric" means a *governed, isolated, region-correct*
environment — not just a workspace. Getting compliance and networking right here avoids re-work later.

---

## Phase 2 — Onboard the bank's real data

**Goal:** replace synthetic data with the bank's real sources, safely and verifiably.

- **Real source connectors:** swap the `synthetic` connector for real ones — **CDC** from the core
  banking system, the card processor, files, etc. This is *config + a connector class* — **the
  agents, Initiator, Supervisor and Insight code do not change** (the MCP seam absorbs it).
- **Real data contracts:** author a data-quality contract per source; run the medallion against real
  data and **validate every KPI definition with the bank's finance team** (especially the simplified
  NIM proxy — redefine it for real interest data).
- **Data protection:** apply **row/column-level security** and **Microsoft Purview sensitivity
  labels**; classify and tokenise PII at Silver.
- **Re-verify the core invariant:** PII **never leaves Bronze** and **never reaches any LLM** — check
  this for every new source and field.

**Why it matters:** this is where correctness and privacy are proven on real data. Sign-off here is
what lets executives trust the numbers and what keeps the bank compliant.

---

## Phase 3 — Productionise the pipeline

**Goal:** make the nightly pipeline reliable, recoverable, and autonomous on real volumes.

- **`OneLakeMCP`** — the one real piece of new pipeline code: an MCP backend that reads/writes **Delta
  tables in the Lakehouse** (with idempotent partition overwrite). Spec is in `docs/FABRIC_MIGRATION.md`.
- **Autonomous trigger:** the scheduled **Fabric Data Pipeline** runs `run_nightly` once a day,
  unattended. The **Supervisor** reasons over any hard QA failure.
- **Reliability:** monitoring + **alerting** (failed runs, QA warnings, Supervisor escalations),
  **disaster recovery / backfill**, and **scale-testing** on production volumes.
- **(Hardening)** extract the **external Remote MCP service** (Azure Container Apps) for a
  least-privilege governance boundary — a backend swap, no agent changes.

**Why it matters:** a bank needs the pipeline to run on its own, never double-count, recover from
failures, and prove every run in an audit trail.

---

## Phase 4 — The AI layers

**Goal:** deliver both AI capabilities — the scheduled narrative *and* on-demand insight — under
banking-grade guardrails.

**4a. Batch narrative (already built):** the nightly **Insight** briefing over Gold — grounded in the
KPIs, never invents numbers, **best-effort** (a model outage never blocks the KPIs).

**4b. On-demand insight — "convert raw data into insights if asked" (new):** an interactive,
natural-language analytics layer. Two options:
- **Native — Microsoft Fabric Data Agent / Copilot** *(recommended for a bank):* a governed
  NL-to-insight agent built over the Lakehouse / semantic model. It **inherits the semantic model's
  security (RLS)** — a user only ever gets answers over data they're authorised to see — and runs
  Azure-OpenAI **inside Fabric's governance boundary**. (Requires F64+ and tenant AI settings.)
- **Custom** NL-query agent (text-to-SQL over Gold via the existing `ModelProvider`) — more control,
  but you must re-implement RLS enforcement and guardrails yourself.

**Guardrails for both AI layers:** answers are **grounded with citations** back to Gold; **no PII in
prompts**; **every question and answer is audited**; and any autonomous "proceed" decision is
reviewed by a human.

**Why it matters:** the on-demand layer is the differentiator executives feel — but for a bank it is
only acceptable if it respects each user's permissions and is fully auditable.

---

## Phase 5 — Security review, UAT, go-live & operate

**Goal:** prove it's safe and correct, then run it.

- **Security review / penetration test** and the bank's formal **sign-off**.
- **User acceptance testing** on real data with the finance/exec stakeholders.
- **Phased go-live** (shadow run → parallel run → cutover).
- **Operate / run model:** on-call, SLAs, alerting on failed runs and Supervisor escalations,
  and a cadence for reviewing AI outputs and KPI definitions.

**Why it matters:** go-live for a bank is a controlled, signed-off, reversible process — and the
operate model is what keeps it reliable in production.

---

### One-line summary
**Provision + clear compliance → onboard real data → harden the pipeline → turn on the two AI layers
(scheduled + on-demand) under guardrails → security-review and operate.** The deterministic ELT and
agent code carry over unchanged; the new work is real connectors, banking security, and the
interactive "ask the data" capability.

---

## Appendix — Generative BI (template-driven) — prototyped in this repo

The "Copilot triggers agents to build a dashboard" capability (Phase 4b) is **built and working
locally** as a template-driven prototype. Flow:

```
NL request → VisualizationAgent (picks ONE governed template + a source filter)
           → renderer binds it to Gold → report
```

**Components (in the repo):**
- **Template catalog** — `config/report_templates/{finance,hr,operations,source}.yaml`: governed,
  pre-approved report definitions (Finance, HR, Operations, and a source-filtered view over
  trades / loans / deposits / cards / payments / transfers). The agent may *only* assemble from these.
- **`VisualizationAgent`** (`src/banking_agents/agents/visualization.py`) — NL → template id (+ source)
  via the LLM, **grounded in the catalog**, with a deterministic **keyword fallback** so it always
  returns a valid, governed selection (proven live: some requests resolve via model, some via fallback).
- **Renderer** (`src/banking_agents/bi/render.py`) — binds the chosen template to the Gold marts and
  emits an HTML report (the local stand-in).
- **CLI** — `make-report "<request>"`; demo data via `seed-marts` (synthetic HR/Ops/source marts).

**How it maps to Fabric (Phase 4b):**
| Prototype piece | Fabric production equivalent |
|-----------------|------------------------------|
| NL request via `make-report` | **Copilot / a chat surface** in Fabric (F64+) |
| `VisualizationAgent` (template select) | Same agent, hosted in a **Fabric notebook/function** |
| Template catalog | The same governed catalog (selects measures/visuals, not raw data) |
| Renderer → HTML | **Power BI REST API** creating a **PBIR report** bound to the **semantic model** |
| Source/dimension filters | Enforced by the model's **RLS** — users see only authorised data |

**Why template-driven (not free-form):** for a bank it's predictable, auditable, and governed — the
agent decides *which approved report* to assemble and *which source* to scope to; it never authors
arbitrary queries or touches raw/PII data. The synthetic HR/Ops/source marts are demo-only; in Fabric
they're replaced by real domains flowing through the medallion — **the catalog and agent don't change.**
