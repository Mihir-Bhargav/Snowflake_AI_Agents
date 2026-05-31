# Data Architecture & Fabric — A Primer

> For someone fluent in AI agents / models / MCP but new to data architecture.
> Everything is anchored to **our** project so the concepts stay concrete.

---

## 0. The one-paragraph mental model

A bank runs **live systems** that handle one customer at a time (move €50, open an account).
Those systems are great at *transactions* but terrible at *questions* like "what were total
deposits across all branches yesterday, and how did that move vs last week?" **Data
architecture** is the discipline of copying that operational data into a separate place,
*reshaping* it, and *pre-computing* answers so executives get fast, trustworthy numbers. Our
whole project is one instance of this: raw banking data → cleaned → aggregated → dashboard.

---

## 1. Two worlds: OLTP vs OLAP

| | **OLTP** (operational) | **OLAP** (analytical) |
|---|---|---|
| Job | Run the business | Understand the business |
| Pattern | Many tiny reads/writes, one record | Few huge scans, millions of rows |
| Example | "Debit account 123 by €50" | "Sum all debits by branch this month" |
| In our project | The **source systems** we extract from | Everything we **build** (Bronze→Gold) |

**Analogy you'll get:** OLTP is an API endpoint serving one request fast. OLAP is a batch job
that sweeps the entire dataset. You don't run analytics on the live DB for the same reason you
don't run a full-table scan inside a hot request path — it'd melt the system everyone depends on.
So we copy the data out first.

---

## 2. Where analytical data lives: warehouse vs lake vs lakehouse

- **Data warehouse** — a database tuned for analytics. Structured tables, strict schema,
  SQL. Trustworthy and fast, but rigid and pricey; you must define structure *before* loading.
- **Data lake** — cheap object storage where you dump *raw files* of any shape. Flexible and
  scalable, but with no discipline it becomes a "data swamp" nobody trusts.
- **Lakehouse** — the modern synthesis: lake-style cheap file storage **plus** warehouse-style
  reliable tables (ACID transactions, SQL) layered on top. **Microsoft Fabric is a lakehouse.**
  You get the lake's flexibility *and* the warehouse's trust.

We get the best of both: land anything cheaply (lake), then expose clean SQL tables (warehouse).

---

## 3. ETL vs ELT (and why we land raw data first)

- **ETL** — Extract → **Transform** → Load. Clean the data *before* it lands. Classic warehouse style.
- **ELT** — Extract → Load → **Transform**. Land it *raw first*, transform *in place* afterwards.
  This is the lakehouse way, and it's what we do.

**Why ELT wins for us:** storage is cheap, so we keep an immutable raw copy (great for audit and
reprocessing), and if we get the transformation logic wrong we just re-run it against the raw —
we never lost the original. Our three steps are ELT: **land Bronze (raw) → transform to Silver →
aggregate to Gold.**

---

## 4. Schema-on-write vs schema-on-read (you'll feel right at home here)

- **Schema-on-write** — you must declare columns/types *before* you can store data. Like a
  **strict typed function signature**: wrong shape → rejected at the door.
- **Schema-on-read** — store whatever arrives; interpret the structure *when you read it*. Like
  an **LLM tolerating messy input** — it copes with extra or missing fields.

We chose **schema-on-read at Bronze** (ARCHITECTURE §3.1). That's why a source can add a column
and our pipeline doesn't break — Bronze just keeps it, and the *contract* at Silver decides how
to interpret things. This is the heart of the "versatile / dynamically configured" requirement.

---

## 5. Batch vs streaming

- **Batch** — process a chunk on a schedule. Our **nightly run** is batch.
- **Streaming** — process events continuously as they arrive (sub-second).

We're batch because executive KPIs refresh daily — no need for streaming complexity. This is
also *why* the deployment uses run-to-completion jobs, not always-on services (DEPLOYMENT §1).

---

## 6. How data is physically stored: files, formats, partitions

Under every "table" in a lakehouse are just **files in object storage**. The format matters a lot:

- **CSV** — plain text, no types, no compression. Fine for tiny hand-offs, terrible for analytics.
- **Parquet** — **columnar**, compressed, typed. The analytics workhorse.
- **Delta** — Parquet **+ a transaction log**. Adds ACID transactions, updates/deletes, and
  "time travel" (query the table as it was yesterday). **OneLake stores Delta.** This is what
  makes a pile of files behave like a real, reliable table.

**Why columnar matters (key intuition):** analytics reads *a few columns across millions of rows*
("sum `amount_eur`"). Row formats force you to read every column of every row. **Columnar formats
let you read only the columns you need** → dramatically faster, cheaper scans.

**Partitioning** — physically splitting a table's files by a column (e.g. by `date`). A query for
"yesterday" then reads only yesterday's files and skips the rest. Think of it as sharding/indexing
for big scans. Our facts partition by date.

---

## 7. Modeling data for analytics: the star schema

This is the single most important *modeling* idea, and it's why Silver looks the way it does.

You split analytical tables into two kinds:

- **Fact table** — the *measurements/events* you aggregate. Numbers + foreign keys, nothing else.
  Long and skinny, grows forever. → `fact_transaction`, `fact_loan`, `fact_account_balance`.
- **Dimension table** — the *descriptive context*: who / what / when / where. Short and wide.
  → `dim_customer`, `dim_account`, `dim_date`, `dim_channel`, `dim_product`.

Arrange one fact surrounded by its dimensions and you get a **star schema** (fact in the middle,
dimensions as the points). BI tools and humans both reason about it naturally: *"sum this number
(fact) sliced by these attributes (dimensions)."*

Three sub-concepts that show up in our DATA_MODEL:

- **Grain** — what exactly *one row means*. "One posting." "One account on one day." You decide
  the grain *first*; everything else follows. Mixing grains is the classic beginner bug.
- **Surrogate vs business keys** — `account_id` is the bank's natural key; `account_sk` is a
  synthetic integer *we* mint. Surrogate keys decouple us from source quirks and let us track
  history (next point).
- **Slowly Changing Dimensions (SCD)** — what to do when a dimension *changes* (a customer moves
  from `retail` to `private`). **SCD Type 2** keeps *both* rows with `valid_from`/`valid_to` +
  `is_current`, so a transaction from last year still maps to who the customer *was then*. We use
  SCD2 on customer and account.

---

## 8. The medallion architecture (why three layers, not one)

This is the convention behind our three steps. Each layer has one job and increasing trust:

| Layer | A.k.a. | Contains | Trust | Who reads it |
|-------|--------|----------|-------|--------------|
| **Bronze** | raw / staging | source data *as-is* + lineage | low | engineers, reprocessing |
| **Silver** | conformed | cleaned, typed, EUR-normalized star schema | medium | analysts, downstream jobs |
| **Gold** | serving | small pre-aggregated KPIs/marts | high | executives, dashboards |

**Why layer at all?** Separation of concerns and *reprocessability*. If a KPI formula is wrong,
you fix Gold logic and recompute from Silver — Bronze/Silver untouched. If a *cleaning* rule is
wrong, you recompute Silver from the immutable Bronze. You never have to re-pull from the source.
Trust increases as you climb; executives only ever see Gold.

---

## 9. The serving layer: semantic model & measures

Gold tables hold numbers, but two analysts can still compute "Net Interest Margin" two different
ways. The **semantic model** (a Power BI concept) fixes that: it defines **measures** (the *one*
official formula for NIM, deposits, etc.) and the relationships between tables, *once*. Everyone
querying it gets identical, governed numbers. It's the "single source of truth for *metrics*"
sitting on top of the "single source of truth for *data*" (Gold). Our Aggregation Agent refreshes
this; the dashboard binds to it.

---

## 10. What Microsoft Fabric actually is

Fabric is Microsoft's **all-in-one SaaS analytics platform**. Instead of stitching together a
storage system + a Spark cluster + a SQL warehouse + a BI tool + an orchestrator (the old way),
Fabric bundles them into one product billed against a single **capacity**. The components:

| Component | What it is | Our use |
|-----------|------------|---------|
| **OneLake** | The single storage layer for the whole tenant — *"OneDrive for data."* All data is Delta/Parquet here. | Holds every Bronze/Silver/Gold table. |
| **Lakehouse** | A Fabric item = a OneLake area + a set of Delta tables + an auto SQL endpoint. | Where our medallion tables live. |
| **Notebooks + Spark** | Distributed Python/PySpark compute for transforming data at scale. | Extract / Transform / Aggregate agents run here. |
| **Data Pipelines** (Data Factory) | Drag-and-drop orchestration + **scheduling/triggers**. | Our nightly trigger + stage sequencing. |
| **SQL analytics endpoint / Warehouse** | Query the Delta tables with T-SQL. | QA + Insight agents read via SQL. |
| **Power BI** | Dashboards + the semantic model, native to Fabric. | The executive dashboard. |
| **User Data Functions** | Serverless Python (no Spark). | Light Initiator + Insight logic (DEPLOYMENT). |
| **Workspace** | A container for items with access control. | Our dev / test / prod environments. |
| **Capacity (F-SKUs)** | The compute you buy; all workloads share it. | Sizing/cost concern. |

### The one idea that makes Fabric "click"
> **One copy of data (OneLake), many engines.** Spark, SQL, and Power BI all read the *same Delta
> files*. There's **no copying data between tools** and no separate ETL just to move it around.
> In older stacks you'd physically shuttle data from the lake → warehouse → BI cube. In Fabric
> they're all just *views over the same OneLake files*. That's the headline feature.

---

## 11. The whole project, end to end (putting it together)

```
SOURCE SYSTEMS (OLTP-style, synthetic for now)
      │  Extraction Agent  (Spark notebook, via MCP)        ── ELT: load raw ──┐
      ▼                                                                        │
BRONZE  bronze.transactions / accounts / customers / loans   (Delta, raw)      │ schema-on-read
      │  Transformation Agent (Spark notebook)              ── clean + math ──┤
      ▼                                                                        │
SILVER  fact_* + dim_* star schema  (typed, EUR, SCD2, PII tokenized)         │
      │  Aggregation Agent (Spark notebook)                 ── aggregate ─────┤
      ▼                                                                        │
GOLD    gold.kpi_daily + drill-down marts  (9 executive KPIs + deltas)        │
      │  → semantic model (the official measures)                             │
      ▼                                                                        │
POWER BI DASHBOARD  +  Insight Agent narrative ("what changed & why")  ◀──────┘

Orchestrated nightly by a Fabric Data Pipeline (the trigger).
Every data operation flows through the Fabric Remote MCP. Agents run inside Fabric.
```

If you can read that diagram and explain each arrow, you understand the data architecture of this
project. The rest is detail.

---

## 12. Glossary (quick reference)

- **OLTP / OLAP** — operational vs analytical workloads.
- **Lakehouse** — cheap file storage + reliable SQL tables; Fabric is one.
- **ELT** — land raw, transform in place.
- **Schema-on-read** — interpret structure at query time; tolerant of change.
- **Delta** — Parquet + transaction log = ACID tables on files.
- **Columnar** — store by column; fast analytical scans.
- **Partition** — split files by a column to skip irrelevant data.
- **Fact / Dimension** — measurements vs descriptive context.
- **Grain** — what one row of a table means.
- **Star schema** — one fact surrounded by dimensions.
- **SCD2** — keep history when a dimension changes.
- **Surrogate key** — a synthetic key we mint (`*_sk`).
- **Medallion** — Bronze (raw) → Silver (clean) → Gold (serving).
- **Semantic model / measure** — the one official definition of each metric.
- **OneLake** — Fabric's single storage layer for all data.
- **Capacity** — the shared compute you buy in Fabric.
