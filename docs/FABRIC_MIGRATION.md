# Fabric Migration Runbook — Local Prototype → Fabric-Native

> Companion to [DEPLOYMENT.md](DEPLOYMENT.md). Precise, ordered steps to take the current
> local prototype (runs on `LocalFilesystemMCP` + synthetic data) to a **Fabric-native**
> nightly pipeline (agents in Fabric compute, data as Delta in OneLake, scheduled trigger,
> Power BI on top). Execute top to bottom.

> [!info] Guiding fact
> Agents only ever touch data through the **`MCPClient`** seam. So the *entire data-layer
> change* is **one new backend** (`OneLakeMCP`). Everything else is provisioning + wiring.

---

## Phase 0 — Decisions (confirm before starting)

| Decision | Recommended for first cutover |
|----------|-------------------------------|
| MCP placement | **Direct OneLake backend in the notebook** (fewer moving parts). Extract the external Remote MCP service later (Phase 8). |
| Compute engine | **Spark notebook** (authenticates to OneLake automatically) — or `deltalake`/delta-rs from a pure-Python notebook if avoiding Spark. |
| Model provider | Keep **Gemini** (or switch per security review — one line in `model.yaml`). |
| Lakehouse | **Schema-enabled** Lakehouse so `bronze`/`silver`/`gold` are real schemas. |

---

## Phase 1 — Provision Fabric (account/admin actions)

1. Enable/obtain a **Fabric capacity** (F-SKU; a Trial capacity works for first cutover).
2. Create a **workspace** (`banking-ai-dev`) and assign it to the capacity.
3. Create a **Lakehouse** (`banking_lakehouse`), **schema-enabled**.
4. Note the OneLake path you'll target:
   `abfss://banking-ai-dev@onelake.dfs.fabric.microsoft.com/banking_lakehouse.Lakehouse/Tables/`
5. (For secrets) create an **Azure Key Vault**; add secrets `model-api-key` and (later) `fabric-mcp-token`. Grant the **workspace managed identity** `get` on secrets.

---

## Phase 2 — The one code change: `OneLakeMCP` (Delta backend)

Implement a new `MCPClient` in `src/banking_agents/mcp/onelake.py` that mirrors
`LocalFilesystemMCP` semantics but reads/writes **Delta tables**.

**Mapping & semantics to preserve:**
- `Table.parse("bronze.transactions")` → Lakehouse schema `bronze`, table `transactions`.
- `write(table, df, mode, batch_id)`:
  - `mode="overwrite"` → replace the whole Delta table (full-refresh sources, dims, gold marts).
  - `mode="append"` with a date-keyed `batch_id` → **idempotent partition overwrite**: write with
    Delta `replaceWhere`/`overwrite` scoped to that date partition (so re-running a day overwrites
    its rows, never double-appends — same guarantee as the local `_batch=` scheme).
- `read(table)` → load the Delta table to pandas.
- `table_exists(table)` → check table presence.

**Engine options (pick one):**
- *Spark:* `spark.createDataFrame(df).write.format("delta").mode(...).option("replaceWhere", f"date_sk={sk}").saveAsTable("bronze.transactions")`. Authenticates to OneLake automatically inside Fabric.
- *delta-rs:* `from deltalake import write_deltalake; write_deltalake(abfss_path, df, mode=..., partition_by=["date_sk"], predicate=...)`, passing an AAD token via `storage_options` (obtain with `notebookutils.credentials.getToken`).

**Acceptance:** the existing 32 tests still pass with `LocalFilesystemMCP`; add a parity test for
`OneLakeMCP` against a **local Delta path** (delta-rs writes Delta to local FS too) so the backend
is verified before any tenant exists.

> Then update `pipelines/notebooks/run_nightly.py` to instantiate `OneLakeMCP` (pointed at the
> Lakehouse) instead of `RemoteMCP`, selected via `deploy/environments.yaml`.

---

## Phase 3 — Package the code into Fabric

1. Build a wheel: `python -m build` → `dist/banking_agents-0.1.0-py3-none-any.whl`.
2. Create a Fabric **Environment**, upload the wheel as a **custom library**, plus
   `requirements` (`pandas`, `pyarrow`, `pyyaml`, `deltalake`, and `azure-identity`/
   `azure-keyvault-secrets` if using Key Vault SDK).
3. Attach the Environment to the `run_nightly` notebook.
   *(Quick alternative for a smoke test: `%pip install` the wheel inline in the notebook.)*

---

## Phase 4 — Secrets, identity & egress

1. Confirm secret resolution: set `AZURE_KEY_VAULT_URL` (notebook env) → `resolve_secret("kv://…")`
   fetches via the workspace **managed identity** (already implemented in `config.py`).
   *(Alternative: Fabric's `notebookutils.credentials.getSecret(vault, name)`.)*
2. Set `model.yaml` `key_ref: kv://banking-ai/model-api-key`.
3. Verify **outbound egress** from the Fabric notebook to the model endpoint
   (`generativelanguage.googleapis.com` for Gemini). Locked-down tenants may need a
   managed private endpoint / approved egress.

---

## Phase 5 — The trigger (autonomous schedule)

1. Create a **Data Pipeline** (`banking-ai-nightly`) from `pipelines/fabric_pipeline.json`
   (build in the Fabric UI or via the REST/deployment API; the JSON is the spec).
2. Add a **Notebook activity** bound to `run_nightly`, passing `as_of = yesterday`.
3. Set the **daily schedule** (e.g. 02:00 W. Europe) and a failure **alert** (activity monitor / notification).
4. Set retry/timeout policy (template already has 1 retry, 1h timeout).

> At this point the pipeline is **autonomous** — it runs without a human, and the **Supervisor**
> reasons over any hard QA failure via Gemini.

---

## Phase 6 — Power BI

1. From the Lakehouse **SQL analytics endpoint**, create a **semantic model** over the Silver
   star schema (or bind directly to `gold.kpi_daily` for the pre-aggregated view).
2. Model the relationships (per `dashboards/POWERBI.md`) and paste the measures from
   `dashboards/semantic_model/measures.dax`.
3. Build the report (KPI cards + the charts mirroring the HTML dashboard).
4. Wire **refresh**: implement `powerbi_refresh` on the MCP (enhanced-refresh REST API) so the
   Aggregation agent triggers it at the end of each run; or set a scheduled dataset refresh.

---

## Phase 7 — Validate the cutover

- [ ] Run `run_nightly` manually for one date → all stages `passed`, Gold tables appear in the Lakehouse.
- [ ] **Idempotency:** re-run the same date → row counts unchanged; one `kpi_daily` row per date.
- [ ] **QA halt:** feed a known-bad source → run `failed`, no Silver promoted, Supervisor decision in the audit log.
- [ ] **Insight resilience:** simulate a model outage → KPIs still publish, briefing flagged non-fatally.
- [ ] Power BI report renders from the refreshed model.
- [ ] Manifest + audit persisted to `ops.run_manifest` / `ops.audit` in OneLake.

---

## Phase 8 — Hardening (after first native run works)

1. **External Remote MCP service:** extract the OneLake I/O into a service (FastAPI on **Azure
   Container Apps**) holding a *narrowly-scoped* Fabric identity; notebooks switch from
   `OneLakeMCP` back to the existing **`RemoteMCP`** client (the governance boundary in
   ARCHITECTURE §6 / DEPLOYMENT §3). Backend swap only — no agent changes.
2. **Real source connectors:** replace `synthetic` in the source registry with `jdbc`/`rest`/`cdc`
   connector classes (config + a class; no agent changes).
3. **test → prod:** promote via `deploy/environments.yaml` (new workspace + Key Vault); same code.
4. **Event trigger (optional):** add a OneLake file-landing / Data Activator trigger alongside the schedule.
5. **Observability:** alerting on failed runs + QA warnings off the manifest.

---

## Code-change summary (what actually gets written)

| Item | Where | Effort |
|------|-------|--------|
| `OneLakeMCP` (Delta backend) | `src/banking_agents/mcp/onelake.py` | **the main one** |
| Backend selection by env | `run_nightly.py` + `environments.yaml` | small |
| `powerbi_refresh` impl | MCP backend | small |
| Parity test (local Delta) | `tests/` | small |
| Real connectors (Phase 8) | `connectors.py` | per-source |

Everything else — agents, Initiator, Supervisor, Insight, QA contracts, KPIs, the manifest —
**ships unchanged**, because it all sits behind the MCP and ModelProvider seams.
