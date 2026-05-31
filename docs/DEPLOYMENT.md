# Deployment Topology

> Companion to [ARCHITECTURE.md](ARCHITECTURE.md) and [AGENTS.md](AGENTS.md).
> **Where the agents run and how they reach data and the model.**
> Runtime: **inside Microsoft Fabric**. Cadence: **nightly batch** (run-to-completion).

---

## 1. Guiding principle

A nightly batch needs **run-to-completion jobs, not always-on services**. So we deploy onto
Fabric's batch-native primitives (scheduled pipeline + notebooks/functions) — no idle compute
between runs, and every stage is a discrete, individually audited activity.

---

## 2. Topology

| Layer | Fabric primitive | Role |
|-------|------------------|------|
| **Trigger + sequencing** | **Fabric Data Pipeline** (daily schedule) | The trigger and the Initiator's execution backbone: invokes each stage in order, runs the QA gate between hops, owns retry/halt. |
| **Heavy worker agents** | **Fabric Notebooks** on a Spark pool | Extract / Transform / Aggregate. Each boots the Claude Opus agent loop, connects to the MCP, runs its stage, writes lineage/audit to OneLake. |
| **Light reasoning agents** | **Fabric User Data Functions** (serverless Python) | Initiator decision logic + Insight Agent — no Spark needed, cheaper serverless. |
| **State / audit** | OneLake Delta tables `ops.run_manifest`, `ops.audit` | Durable run state across stages; the immutable run log. |
| **Identity** | Fabric workspace **managed identity** | Agents authenticate to OneLake; no static storage creds. |
| **Secrets** | **Azure Key Vault** | Model API key + any connector secrets; referenced, never in notebooks. |

```
            ┌──────────── Fabric Data Pipeline (nightly schedule) ────────────┐
 schedule ─▶│  Extract NB ─▶ QA ─▶ Transform NB ─▶ QA ─▶ Aggregate NB ─▶ QA   │
            │                                              └▶ Insight (UDF)    │
            │  Initiator (UDF) owns manifest/retry/halt across all activities │
            └───────────────┬───────────────────────────────┬────────────────┘
                            │ data ops only via              │ model calls via
                            ▼                                 ▼
              ┌────────────────────────────┐    ┌──────────────────────────┐
              │  FABRIC REMOTE MCP service  │    │   ModelProvider (cfg)    │
              │  (Azure Container Apps,     │    │  Anthropic | Bedrock |   │
              │   same tenant/region)       │    │  Vertex  — swappable     │
              │  narrow Fabric identity,    │    └──────────────────────────┘
              │  allowlisted tools, audit   │
              └──────────────┬─────────────┘
                             ▼
                    Microsoft Fabric / OneLake
```

---

## 3. Fabric Remote MCP — **external remote endpoint** (decision)

The MCP runs as its **own service** (Azure Container Apps, same tenant/region as the Fabric
capacity), not co-located in the notebooks.

**Why:**
- **Real trust boundary** — authz, audit, rate-limiting, and tool allowlisting live in one
  explicit, independently testable place.
- **Least privilege** — the MCP service holds a *narrowly scoped* Fabric managed identity and
  exposes only allowlisted tools; agents get **no** broad Fabric rights and can act only
  *through* the MCP. (Co-location would put Fabric credentials in every notebook.)
- **Lifecycle decoupling** — version/upgrade tools without redeploying agent notebooks; mock
  the MCP for local dev.

**Trade-off:** one extra network hop + a service to operate. Mitigated by hosting in-boundary
(same Azure tenant/region) on infra that **scales to zero** between nightly runs.

---

## 4. Model access — **provider-swappable** (decision)

Model provider is deferred to security review, so it is **pluggable**, not hard-wired.

- A thin **`ModelProvider`** interface sits between agents and the model
  (`complete(messages, tools) -> response`).
- Implementations: **Anthropic API**, **Amazon Bedrock**, **Google Vertex** — selected by
  `config` (e.g. `model.provider: anthropic`).
- Key is read from **Azure Key Vault** at runtime.
- Note: Azure does not host Claude, so every option involves an outbound call leaving the Azure
  boundary; egress + data-residency policy decides which. Swapping providers is a config change,
  not a refactor.

```yaml
# config/model.yaml
model:
  name: claude-opus-4-8
  provider: anthropic        # anthropic | bedrock | vertex  (decided at security review)
  key_ref: kv://banking-ai/model-api-key
  region: westeurope
```

---

## 5. Environments

| Env | Fabric workspace | Data | Model |
|-----|------------------|------|-------|
| **dev** | `banking-ai-dev` | synthetic | mocked MCP + small model or stub |
| **test** | `banking-ai-test` | synthetic, full volume | real MCP, chosen provider |
| **prod** | `banking-ai-prod` | real sources (later) | chosen provider |

Promotion is config-driven (source registry + `model.yaml` + workspace binding) — same agent
code across all three.

---

## 6. What this buys us (recap)

- No idle compute (batch-native, scales to zero).
- One governed, auditable seam to Fabric (the MCP service) with least-privilege identity.
- Model provider decided later without a rewrite.
- Identical code dev→test→prod; only configuration changes.

---

## C. Concrete deployment wiring (implemented scaffolding)

The artifacts below make the topology above real. They require a Fabric workspace + the
MCP service to actually run, so they are **not exercised by the local test suite** — but the
seams they plug into (MCPClient, ModelProvider, secret resolution) are the same ones local
dev uses and tests cover.

| Artifact | Purpose |
|----------|---------|
| `pipelines/notebooks/run_nightly.py` | The Fabric **orchestrator notebook**: runs the full Initiator with `RemoteMCP` + the configured provider. Identical agent code to local dev. |
| `pipelines/fabric_pipeline.json` | The **Data Pipeline** definition: nightly schedule + triggers the notebook, with retry/timeout/alert policy. |
| `deploy/environments.yaml` | dev/test/prod **bindings** (workspace, lakehouse, MCP URL, Key Vault, provider). Same code; only these change. |
| `banking_agents/mcp/remote.py` (`RemoteMCP`) | Notebook-side **client** for the Fabric Remote MCP service — implements the `MCPClient` interface over HTTP (Parquet on the wire). |
| `banking_agents.config.resolve_secret` | Secret resolution: `env://` (local `.env`) and `kv://` (**Azure Key Vault** via managed identity, lazy-imported in the Fabric image). |

**Orchestration choice:** a *single* orchestrator notebook runs all stages via the Initiator
(rather than five separate per-stage notebooks juggling shared manifest state). This keeps the
QA-gate/halt logic in one place and the manifest in-process. If granular per-stage retry is
later required, the same agents can be split into per-stage notebooks coordinated by the
pipeline, persisting the manifest to `ops.run_manifest` between activities.

**Still required to go live (needs a Fabric tenant):** create the workspaces + lakehouse,
deploy the MCP service (Azure Container Apps) with a scoped Fabric managed identity, create the
Key Vault secrets (`model-api-key`, `fabric-mcp-token`), import the pipeline, and bind the
notebook. The real source connectors (replacing `synthetic`) also land here.
