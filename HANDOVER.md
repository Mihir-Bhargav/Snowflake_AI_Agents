# Project Handover — Banking Analytics AI-Agent Platform

> **For:** the incoming owner — a professional **data architect**.
> **Assumes:** deep data/lakehouse/modelling expertise; **little AI background** (so the AI parts
> are explained from first principles, in data terms).
> **Your mission:** stand this up on **Microsoft Fabric** and harden it to **production grade for
> real banks**.

Welcome. This is a **working prototype**, not a product. It runs end-to-end **locally** on
synthetic data (32 automated tests green), with the Microsoft Fabric deployment **wired but not
yet live**. The data engineering will feel familiar; this document spends most of its words on the
**AI components**, because that's the unfamiliar part — and on what "production for banks" demands.

---

## 1. What the project is (60 seconds)

An **agent-orchestrated medallion ELT pipeline** on Microsoft Fabric. Raw banking data lands in
**Bronze** (schema-on-read, immutable, lineage), is conformed to a **Silver** star schema
(EUR-normalised, **PII tokenised**, SCD2 dims, surrogate keys), and aggregated to **Gold** — nine
executive KPIs + drill-down marts. A nightly run is sequenced by an **Initiator** through
**data-quality gates** between every layer. On top of Gold, an **AI layer** writes a plain-language
executive briefing and helps the orchestrator decide what to do when a quality gate fails.

This is all standard, defensible data engineering **plus two bounded AI touchpoints**. That
containment is the whole design philosophy — see §3.

**Read the docs in this order** (`docs/`): `PRIMER.md` (skip — it's the data primer, you wrote the
book) → `ARCHITECTURE.md` → `DATA_MODEL.md` → `AGENTS.md` → `DEPLOYMENT.md` → `FABRIC_MIGRATION.md`.

---

## 2. The architectural backbone (so the AI parts make sense)

Two **seams** (clean interfaces) make everything swappable and testable:

- **The MCP seam** (`src/banking_agents/mcp/`) — *every* agent reads/writes data **only** through an
  `MCPClient` interface. Think of it as the **data-access layer**. Local backend = Parquet on disk;
  Fabric backend = Delta in OneLake. Agents don't know or care which.
- **The ModelProvider seam** (`src/banking_agents/model/`) — the *only* place the system talks to an
  AI model. Pluggable: `stub` (no AI, offline), `gemini` (live), others registered. **You can turn
  AI off entirely by setting the provider to `stub`** — the pipeline still produces all KPIs.

Because of these seams, going to Fabric is a **backend swap, not a rewrite**, and **the AI is
optional and additive** — the data product stands on its own without it.

---

## 3. The AI components, explained for a data architect

> [!important] The single most important thing to understand
> **The data pipeline is 100% deterministic, reproducible code — your domain, no surprises.**
> The AI (an LLM) is used at **exactly two narrow, well-guarded points**, and **no business number
> ever depends on it**. If the AI is wrong, slow, or switched off, your KPIs are unaffected.

### 3a. A short glossary in data terms
- **LLM (Large Language Model)** — a hosted service (here, Google **Gemini**) that takes text in and
  returns text out. Treat it like an **external API call to a black-box function**: useful, but
  **non-deterministic** (same input can give slightly different output) and **occasionally wrong**.
- **Prompt / system prompt** — the instructions + data we send. The *system prompt* sets the role and
  rules; the *user message* carries the data (here, the Gold KPIs as JSON).
- **Grounding** — constraining the model to use **only the data we give it** and forbidding it from
  inventing facts. This is how we make a non-deterministic component trustworthy.
- **Hallucination** — when an LLM makes up something plausible-but-false. The risk we engineer
  against (via grounding + by never letting the LLM compute the numbers).
- **Token / temperature / "thinking"** — tokens are billing/length units; temperature is randomness
  (we keep it low, 0.3); "thinking" is an internal reasoning mode (we **disabled** it for predictable
  output). You rarely need to touch these.
- **Agent** — here, just **code that runs a sequence of steps and, at two points, calls the LLM to
  make a judgment or write prose**, with guardrails and fallbacks. No magic.

### 3b. AI component #1 — the **Insight Agent** (`agents/insight.py`)
- **What it does:** after Gold is built, it writes the ~300-word **executive briefing** ("what
  changed and why it matters").
- **What it sees:** **only the Gold KPI numbers + recent history + QA notes** — all aggregates.
- **Guardrails:** the prompt forbids inventing numbers; it must use only the figures provided.
- **Failure behaviour:** it is **best-effort and non-fatal** — if the model is down/slow/rate-limited,
  the run still **succeeds with complete KPIs**; only the prose is missing (flagged in the audit log).
- **Why it's safe:** it *describes* numbers the deterministic pipeline already computed; it never
  *produces* them.

### 3c. AI component #2 — the **Supervisor** (`agents/supervisor.py`)
- **What it does:** the "agentic" part. When a **data-quality gate hard-fails**, instead of always
  halting, the Supervisor sends the **failed checks as evidence** to the LLM and asks for the best
  action: **retry / halt / escalate / proceed** (returned as structured JSON).
- **When it runs:** **only on a hard failure** — healthy nightly runs never call it (no cost, no risk).
- **Guardrails & safety:** the action is validated against an allow-list; it's told to **never
  "proceed" when integrity is violated**; and if the model is unavailable or returns anything
  unparseable, it **falls back to the conservative deterministic rule (HALT)** — exactly the old
  behaviour. Retries are capped (a second failure escalates to a human).
- **Why it's safe:** it can only ever make the pipeline *smarter on failures*; it can never make it
  *less safe* than a plain halt.

### 3d. The ModelProvider — how you control the AI (`model/provider.py`, `config/model.yaml`)
- Change or disable AI by editing **one config file** `config/model.yaml`:
  - `provider: stub` → **no AI at all** (deterministic; great for validating the core first).
  - `provider: gemini` → live (current).
  - `anthropic` / `bedrock` / `vertex` are registered stubs — implement whichever your bank's
    security review approves.
- The model **key is a secret** resolved from `.env` locally or **Azure Key Vault** in Fabric
  (`config.resolve_secret`). Never hard-coded.

### 3e. 🔒 The data-privacy invariant you must preserve (banking-critical)
> [!warning] PII never reaches the LLM — keep it that way
> Customer PII (names, birth dates) is **tokenised at Silver and never leaves the secure Bronze
> zone**. The LLM only ever receives **aggregated Gold KPIs** (Insight) or **QA check descriptions**
> (Supervisor) — **no raw customer records, no PII, ever**. For a bank this is a compliance
> cornerstone. If you add sources, agents, or prompts, **verify this invariant still holds**: nothing
> row-level or personally identifiable should ever be placed in a prompt.

### 3f. Reliability & cost characteristics of the AI parts
- **Non-determinism** is contained: no KPI depends on the LLM; the Supervisor's output is validated +
  has a deterministic fallback.
- **Cost/quota:** the LLM is called **once per run** (the briefing) **+ only on failures** (Supervisor).
  The current key is a **free-tier Gemini dev key with a daily quota** — fine for testing, **not for
  production**. Production needs a paid/enterprise endpoint.
- **Auditability:** every briefing records the **model name + timestamp**; every Supervisor decision
  records the **action + reason** in the run's audit log. Good for bank audit trails.

---

## 4. Making the AI production-grade for banks (your AI checklist)

Since AI is the less-familiar area, here's what "product-level" means specifically for these parts:

- [ ] **Model governance / security review.** Decide the sanctioned provider and endpoint
      (data residency, contracts, egress). Likely **Vertex AI** or **Bedrock** behind your cloud's
      controls rather than the public Gemini key. Implement that provider in the existing seam.
- [ ] **Secrets:** move the model key to **Key Vault**; rotate the current dev key (it was shared in
      chat during development — **treat it as compromised and replace it**).
- [ ] **Decide the AI policy per environment.** A conservative path: run **`stub` (AI off)** in early
      production to validate the deterministic core, then enable the Insight briefing, then (last) the
      Supervisor — turning on autonomy only once you trust it.
- [ ] **Re-verify the PII invariant** (§3e) for every real source and any prompt change.
- [ ] **Prompt + model versioning:** pin the model version and treat the prompts (`SYSTEM` strings in
      `insight.py`/`supervisor.py`) as governed artifacts; log which version produced each output.
- [ ] **Output validation & monitoring:** alert on Insight failures and on every Supervisor
      `escalate`/`proceed` decision (a human should review any `proceed`-on-failure).
- [ ] **SLA / fallback:** confirm the best-effort + deterministic-halt fallbacks meet your operational
      bar; set timeouts and retry budgets to taste.
- [ ] **Prompt-injection:** today prompts contain only our own numbers (low risk). If you ever feed
      free-text (e.g. transaction memos, customer notes) to the model, add sanitisation.

---

## 5. The deterministic core (your home turf — what's solid)

Standard, tested, reproducible: schema-on-read Bronze with lineage; a Silver star schema with
EUR-normalisation, PII tokenisation, SCD2, surrogate keys; Gold with 9 KPIs + growth deltas;
**data-quality contracts** at every hop; **idempotent** writes (re-running a day overwrites, never
double-counts); a full **run manifest + immutable audit log**. See `DATA_MODEL.md` / `AGENTS.md`.

> [!note] Two data caveats to validate with the business
> - **NIM** is a *simplified proxy* (synthetic data has no real interest-expense side) — redefine it
>   for real data.
> - All **KPI definitions** (`docs/DATA_MODEL.md §4`, `dashboards/semantic_model/measures.dax`) should
>   be signed off against the bank's official metric definitions.

---

## 6. Current status — real vs scaffolded

| Real (runs + tested locally) | Scaffolded (needs your Fabric work) |
|------------------------------|--------------------------------------|
| Full Bronze→Silver→Gold pipeline, 9 KPIs, growth trends | OneLake Delta backend (`OneLakeMCP`) — **the main code to write** |
| QA gates, idempotency, manifest/audit | The Fabric tenant: capacity, workspaces, Lakehouse |
| Insight briefing (live Gemini) + Supervisor (tested via scripted provider) | External Remote MCP service (governance boundary) |
| HTML executive dashboard + Power BI DAX measures | Real source connectors (replacing synthetic) |
| 32 automated tests | Scheduled trigger, Key Vault, Power BI semantic model live |

---

## 7. Your roadmap

1. **Stand up Fabric** — follow **`docs/FABRIC_MIGRATION.md`** top to bottom. The first real code is
   **`OneLakeMCP`** (a Delta-writing MCP backend; spec is in that doc, Phase 2). Everything else is
   provisioning + wiring; the agents/Initiator/Supervisor/Insight ship **unchanged**.
2. **Harden the AI** — work §4 above.
3. **Productionise the data** — real connectors, the external MCP service, monitoring/alerting,
   DR/backfill, scale testing, and business sign-off on KPI definitions.

---

## 8. How to run it today (15 minutes)

```bash
cd <repo>
python3 -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"

run-pipeline --as-of 2026-05-29     # full run → KPIs + AI briefing
inspect-lake                         # see all layers + QA + briefing
build-dashboard && open data/dashboard.html
pytest -q                            # 32 tests

# To run with NO AI (deterministic only): set provider: stub in config/model.yaml
```

**AI component file map:** `config/model.yaml` (config) · `src/banking_agents/model/` (provider seam)
· `src/banking_agents/agents/insight.py` (briefing) · `src/banking_agents/agents/supervisor.py`
(decision layer) · `.env` (the dev key — **rotate it**).

---

You have a clean, well-tested foundation and a clear migration path. The data layer is yours to
own immediately; the AI is small, contained, optional, and fully documented above. Good luck — and
the test suite is your safety net: keep it green.
