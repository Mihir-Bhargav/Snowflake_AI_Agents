# Power BI Dashboard — Binding Guide

Two ways to visualize the platform's output:

- **Local / now:** `build-dashboard` renders a self-contained HTML executive dashboard from
  the Gold marts (KPI cards, trend charts, drill-downs, AI briefing). No Fabric needed.
- **Fabric / production:** Power BI binds to the Gold/Silver lakehouse using the semantic
  model below. This is the enterprise presentation layer (docs/ARCHITECTURE.md §9).

---

## Local HTML dashboard
```bash
run-pipeline --as-of 2026-05-29     # produce a few days first
build-dashboard                     # writes data/dashboard.html
open data/dashboard.html            # macOS; or open the file in any browser
```
(Charts load Chart.js from a CDN, so chart rendering needs internet; cards + briefing work offline.)

---

## Power BI semantic model (in Fabric)

1. **Connect.** In the Fabric workspace, open the Lakehouse → **SQL analytics endpoint** →
   *New Power BI semantic model*, and select the Silver tables: `fact_transaction`,
   `fact_account_balance`, `fact_loan`, `dim_date`, `dim_account`, `dim_customer`,
   `dim_channel`. (For a pre-aggregated view you can instead bind directly to
   `gold.kpi_daily` and the `gold.*` marts.)

2. **Model the star schema.** Create relationships:
   - `fact_*[date_sk]` → `dim_date[date_sk]`
   - `fact_*[account_sk]` → `dim_account[account_sk]`
   - `dim_account[customer_sk]` → `dim_customer[customer_sk]`
   - `fact_transaction[channel_sk]` → `dim_channel[channel_sk]`
   - Mark `dim_date` as the date table.

3. **Add the measures.** Paste the measures from
   [`semantic_model/measures.dax`](semantic_model/measures.dax) — these are the *single
   official definition* of each KPI, so every report shows identical numbers.

4. **Build the report** (mirrors the local HTML dashboard):
   | Visual | Measure(s) | Axis / slice |
   |--------|-----------|--------------|
   | KPI cards | each of the 9 KPIs | latest date |
   | Line — Balance Sheet | Total Deposits, Net Loans | dim_date[date] |
   | Line — Risk & Liquidity | Loan-to-Deposit Ratio, NPL Rate % | dim_date[date] |
   | Column — Volume | Transaction Volume | dim_date[date] |
   | Bar — by segment | Total Deposits | dim_customer[segment] |
   | Bar — by channel | Transaction Value | dim_channel[channel] |
   | Bar — loans by status | Net Loans | loan status bucket |
   | Text/Card | exec narrative | from `gold.exec_narrative` |

5. **Refresh.** The Aggregation Agent calls `powerbi_refresh` on the dataset at the end of
   each nightly run (via the MCP), so the report is current each morning.
