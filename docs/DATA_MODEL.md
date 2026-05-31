# Data Model — Bronze / Silver / Gold

> Companion to [ARCHITECTURE.md](ARCHITECTURE.md). Defines the schemas for each medallion
> layer, the lineage metadata, and the data dictionary backing the v1 KPIs (§4.1).
> Currency: **EUR**. Storage: **Delta** on OneLake. Phase source: **synthetic**.

---

## 0. Conventions

- **Layers:** `bronze.*` (raw/staging), `silver.*` (conformed), `gold.*` (serving).
- **Grain** is stated for every table. Facts are append/merge by key; dimensions are SCD-tracked.
- **Schema-on-read:** Bronze stores what arrives. Typing/conforming happens at Silver against
  a versioned **contract** (`contracts/<source>.yaml`). New columns flow through harmlessly.
- **Lineage metadata** (every Bronze row, carried forward where useful):

  | Column          | Type      | Meaning                                  |
  |-----------------|-----------|------------------------------------------|
  | `_ingested_at`  | timestamp | When the row landed in Bronze            |
  | `_source_system`| string    | `source_id` from the source registry     |
  | `_batch_id`     | string    | Logical batch (idempotency key)          |
  | `_run_id`       | string    | Orchestration run that produced the row  |
  | `_source_file`  | string    | Origin file/partition (nullable)         |

- **Naming:** `snake_case`; surrogate keys `*_sk`; natural/business keys `*_id`;
  monetary columns suffixed `_eur`.
- **PII** columns are tokenized at Silver (`*_token`); raw PII never leaves Bronze.

---

## 1. Bronze (raw / staging)

One Bronze table per source, schema-on-read. Columns below are the *expected* shapes the
synthetic generators emit; unknown extra columns are preserved.

### `bronze.transactions`
Grain: one posting/authorization event.

| Column          | Type      | Notes                                   |
|-----------------|-----------|-----------------------------------------|
| `txn_id`        | string    | Source transaction id                   |
| `account_id`    | string    | FK → accounts                           |
| `posting_ts`    | timestamp | Event time; **incremental watermark**   |
| `amount`        | decimal   | Signed (credit +, debit −), source ccy  |
| `currency`      | string    | ISO code; normalized to EUR at Silver   |
| `txn_type`      | string    | `payment`,`transfer`,`fee`,`interest`…  |
| `channel`       | string    | `mobile`,`web`,`atm`,`branch`,`card`    |
| `counterparty`  | string    | Free text / masked at Silver if PII     |
| _+ lineage_     |           |                                         |

### `bronze.accounts`
Grain: one account snapshot per ingest.

| Column          | Type      | Notes                                   |
|-----------------|-----------|-----------------------------------------|
| `account_id`    | string    | Natural key                             |
| `customer_id`   | string    | FK → customers                          |
| `product_type`  | string    | `current`,`savings`,`deposit`,`loan`…   |
| `open_date`     | date      |                                         |
| `status`        | string    | `active`,`dormant`,`closed`             |
| `balance`       | decimal   | Source ccy; EUR at Silver               |
| `currency`      | string    |                                         |
| `snapshot_date` | date      | As-of date                              |
| _+ lineage_     |           |                                         |

### `bronze.customers`  *(contains PII)*
Grain: one customer record per ingest.

| Column          | Type      | Notes                                   |
|-----------------|-----------|-----------------------------------------|
| `customer_id`   | string    | Natural key                             |
| `full_name`     | string    | **PII** → tokenized at Silver           |
| `birth_date`    | date      | **PII**                                 |
| `segment`       | string    | `retail`,`sme`,`corporate`,`private`    |
| `country`       | string    |                                         |
| `onboarded_date`| date      |                                         |
| _+ lineage_     |           |                                         |

### `bronze.loans`
Grain: one loan facility snapshot.

| Column            | Type      | Notes                                 |
|-------------------|-----------|---------------------------------------|
| `loan_id`         | string    | Natural key                           |
| `account_id`      | string    | FK → accounts                         |
| `principal_outstanding` | decimal | Source ccy; EUR at Silver         |
| `interest_rate`   | decimal   | Annual %                              |
| `origination_date`| date      |                                       |
| `days_past_due`   | int       | Drives NPL                            |
| `status`          | string    | `current`,`delinquent`,`default`      |
| `snapshot_date`   | date      |                                       |
| _+ lineage_       |           |                                       |

### `bronze.reference_*`
Lookups: `bronze.reference_products`, `bronze.reference_branches`, `bronze.reference_gl`.
Small, fully refreshed each run.

---

## 2. Silver (conformed)

Typed, deduplicated, EUR-normalized, PII-tokenized, conformed keys. Star-schema ready.

### Dimensions

| Table                | Grain / SCD            | Key columns                                   |
|----------------------|------------------------|-----------------------------------------------|
| `silver.dim_customer`| 1 row/customer, SCD2   | `customer_sk`, `customer_id`, `customer_token`, `segment`, `country`, `valid_from/to`, `is_current` |
| `silver.dim_account` | 1 row/account, SCD2    | `account_sk`, `account_id`, `customer_sk`, `product_type`, `status`, `valid_from/to` |
| `silver.dim_product` | 1 row/product          | `product_sk`, `product_type`, `product_name`  |
| `silver.dim_date`    | 1 row/day              | `date_sk`, `date`, `month`, `quarter`, `year` |
| `silver.dim_channel` | 1 row/channel          | `channel_sk`, `channel`, `is_digital` (bool)  |

### Facts

**`silver.fact_transaction`** — grain: one posting.

| Column         | Type      | Notes                                          |
|----------------|-----------|------------------------------------------------|
| `txn_sk`       | bigint    | Surrogate                                      |
| `txn_id`       | string    | Business key                                   |
| `account_sk`   | bigint    | FK → dim_account                               |
| `date_sk`      | int       | FK → dim_date (from `posting_ts`)              |
| `channel_sk`   | int       | FK → dim_channel                               |
| `amount_eur`   | decimal   | EUR-normalized signed amount                   |
| `txn_type`     | string    | Conformed enum                                 |
| `is_fee`       | boolean   | `txn_type = 'fee'`                             |
| `is_interest`  | boolean   | `txn_type = 'interest'`                        |

**`silver.fact_account_balance`** — grain: account × snapshot_date.

| Column          | Type    | Notes                                |
|-----------------|---------|--------------------------------------|
| `account_sk`    | bigint  | FK                                   |
| `date_sk`       | int     | FK                                   |
| `balance_eur`   | decimal | EUR balance                          |
| `is_deposit`    | boolean | product_type ∈ deposit/savings/current |
| `is_loan`       | boolean | product_type = loan                  |

**`silver.fact_loan`** — grain: loan × snapshot_date.

| Column                    | Type    | Notes                          |
|---------------------------|---------|--------------------------------|
| `loan_sk`                 | bigint  | Surrogate                      |
| `account_sk`              | bigint  | FK                             |
| `date_sk`                 | int     | FK                             |
| `principal_outstanding_eur`| decimal| EUR                            |
| `interest_rate`           | decimal | Annual %                       |
| `days_past_due`           | int     |                                |
| `is_npl`                  | boolean | `days_past_due >= 90`          |

> **Derivations applied here (the "math"):** EUR normalization, signed-amount logic,
> `is_*` flags, SCD effective dating, dedup by business key + latest snapshot.

---

## 3. Gold (serving / executive marts)

Small, fast, pre-aggregated. The Power BI semantic model binds to these.

### `gold.kpi_daily` — one row per reporting day (the executive headline mart)

| Column                       | Type    | KPI |
|------------------------------|---------|-----|
| `date`                       | date    | —   |
| `total_deposits_eur`         | decimal | 1   |
| `deposits_growth_pct`        | decimal | 1   |
| `net_loans_eur`              | decimal | 2   |
| `loans_growth_pct`           | decimal | 2   |
| `loan_to_deposit_ratio`      | decimal | 3   |
| `nim_pct`                    | decimal | 4   |
| `fee_income_eur`             | decimal | 5   |
| `txn_count`                  | bigint  | 6   |
| `txn_value_eur`              | decimal | 6   |
| `npl_rate_pct`               | decimal | 7   |
| `active_customers`           | bigint  | 8   |
| `net_new_customers`          | bigint  | 8   |
| `digital_channel_mix_pct`    | decimal | 9   |

### Supporting Gold marts (for drill-down)
- `gold.deposits_by_segment` — deposits & growth by customer segment.
- `gold.txn_by_channel` — volume/value split by channel (digital vs branch).
- `gold.loans_by_status` — outstanding & NPL by loan status/product.

---

## 4. KPI data dictionary (formulas)

All figures EUR, computed over the nightly window vs. prior period.

| # | KPI | Formula | Built from |
|---|-----|---------|-----------|
| 1 | Total Deposits | `Σ balance_eur WHERE is_deposit` | `fact_account_balance` |
| 1 | Deposits Growth % | `(today − prior) ÷ prior × 100` | `gold.kpi_daily` history |
| 2 | Net Loans Outstanding | `Σ principal_outstanding_eur` | `fact_loan` |
| 3 | Loan-to-Deposit Ratio | `net_loans ÷ total_deposits` | KPIs 1,2 |
| 4 | Net Interest Margin | `(interest_income − interest_expense) ÷ earning_assets` | `fact_transaction (is_interest)`, `fact_account_balance` |
| 5 | Fee & Commission Income | `Σ amount_eur WHERE is_fee` | `fact_transaction` |
| 6 | Transaction Volume / Value | `count(*)` / `Σ amount_eur` | `fact_transaction` |
| 7 | NPL / Delinquency Rate | `Σ principal WHERE is_npl ÷ Σ principal × 100` | `fact_loan` |
| 8 | Active Customers / Net New | distinct customers transacting; Δ vs prior | `fact_transaction`, `dim_customer` |
| 9 | Digital Channel Mix | `txns WHERE is_digital ÷ total txns × 100` | `fact_transaction`, `dim_channel` |

---

## 5. Data-quality contracts (per source/layer)

Enforced by the Validation Agent at each hop (`contracts/*.yaml`). Examples:

- **Bronze→Silver:** required columns present; `posting_ts` not null; `currency` in allowed set;
  row count within ±X% of trailing average.
- **Silver:** referential integrity (every `account_sk`/`customer_sk` resolves); no negative
  balances where disallowed; EUR normalization applied (no residual non-EUR rows).
- **Silver→Gold:** KPI sanity bounds (e.g. `loan_to_deposit_ratio` within plausible range;
  `npl_rate_pct` 0–100); day-over-day KPI swing beyond threshold raises a warning for the
  Insight Agent to explain rather than a hard fail.
