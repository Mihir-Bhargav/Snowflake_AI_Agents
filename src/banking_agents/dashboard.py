"""``build-dashboard`` — render a self-contained HTML executive dashboard from Gold.

Reads the Gold marts (kpi_daily + drill-downs + exec_narrative) and writes one standalone
HTML file: KPI cards, trend charts, segment/channel/loan breakdowns, and the AI briefing.
This is the local "for now" visualization; the Power BI binding lives in dashboards/.
(Charts use Chart.js from a CDN, so chart rendering needs internet; cards/text work offline.)
"""
from __future__ import annotations

import argparse
import json
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd

from .config import REPO_ROOT
from .mcp import LocalFilesystemMCP


def _latest_rows(df: pd.DataFrame) -> pd.DataFrame:
    return df[df["date"].astype(str) == str(df["date"].max())] if "date" in df else df


def build_context(mcp: LocalFilesystemMCP) -> dict:
    if not mcp.table_exists("gold.kpi_daily"):
        raise FileNotFoundError("gold.kpi_daily not found — run `run-pipeline` first.")
    kpi = mcp.read("gold.kpi_daily").sort_values("date")
    latest = kpi.iloc[-1].where(pd.notna(kpi.iloc[-1]), None).to_dict()

    def col(name):
        return [None if pd.isna(v) else round(float(v), 2) for v in kpi[name]]

    ctx = {
        "title": "Banking Analytics — Executive Dashboard",
        "generated": datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC"),
        "dates": [str(d) for d in kpi["date"]],
        "latest": {k: (None if v is None else v) for k, v in latest.items()},
        "series": {k: col(k) for k in [
            "total_deposits_eur", "net_loans_eur", "loan_to_deposit_ratio", "npl_rate_pct",
            "txn_count", "txn_value_eur", "digital_channel_mix_pct", "nim_pct",
            "fee_income_eur", "active_customers", "deposits_growth_pct"]},
    }
    if mcp.table_exists("gold.deposits_by_segment"):
        d = _latest_rows(mcp.read("gold.deposits_by_segment"))
        ctx["deposits_by_segment"] = d[["segment", "deposits_eur"]].to_dict("records")
    if mcp.table_exists("gold.txn_by_channel"):
        c = _latest_rows(mcp.read("gold.txn_by_channel"))
        ctx["txn_by_channel"] = c[["channel", "txn_count", "txn_value_eur"]].to_dict("records")
    if mcp.table_exists("gold.loans_by_status"):
        s = _latest_rows(mcp.read("gold.loans_by_status"))
        s = s.rename(columns={"bucket": "status"})
        ctx["loans_by_status"] = s[["status", "principal_outstanding_eur"]].astype(
            {"status": str}).to_dict("records")
    if mcp.table_exists("gold.exec_narrative"):
        n = mcp.read("gold.exec_narrative").sort_values("date").iloc[-1]
        ctx["narrative"] = {"text": n["narrative"], "model": n["model"], "date": str(n["date"])}
    return ctx


def render_html(ctx: dict) -> str:
    return _TEMPLATE.replace("__DATA__", json.dumps(ctx))


def build(root: Path, out: Path) -> Path:
    ctx = build_context(LocalFilesystemMCP(root))
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(render_html(ctx))
    return out


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description="Build the HTML executive dashboard from Gold.")
    p.add_argument("--root", type=Path, default=REPO_ROOT / "data" / "lake")
    p.add_argument("--out", type=Path, default=REPO_ROOT / "data" / "dashboard.html")
    args = p.parse_args(argv)
    path = build(args.root, args.out)
    print(f"Dashboard written: {path}\nOpen it in a browser:  open {path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())


_TEMPLATE = r"""<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Banking Analytics — Executive Dashboard</title>
<script src="https://cdn.jsdelivr.net/npm/chart.js@4.4.1/dist/chart.umd.min.js"></script>
<style>
:root{--bg:#0f1720;--card:#18222e;--ink:#e7eef6;--mut:#8aa0b2;--line:#26323f;--accent:#3aa0ff;--good:#2ecc71;--bad:#ff5a6a}
*{box-sizing:border-box}body{margin:0;font:14px/1.5 -apple-system,Segoe UI,Roboto,Helvetica,Arial;background:var(--bg);color:var(--ink)}
header{padding:22px 28px;border-bottom:1px solid var(--line);display:flex;justify-content:space-between;align-items:baseline;flex-wrap:wrap;gap:8px}
h1{font-size:19px;margin:0;font-weight:600}.sub{color:var(--mut);font-size:12px}
main{padding:22px 28px;max-width:1280px;margin:0 auto}
.grid{display:grid;gap:14px}.cards{grid-template-columns:repeat(auto-fill,minmax(210px,1fr))}
.charts{grid-template-columns:repeat(2,1fr);margin-top:18px}
.card,.panel{background:var(--card);border:1px solid var(--line);border-radius:12px;padding:16px}
.card .k{color:var(--mut);font-size:12px;text-transform:uppercase;letter-spacing:.04em}
.card .v{font-size:24px;font-weight:650;margin-top:6px}.card .d{font-size:12px;margin-top:4px}
.up{color:var(--good)}.down{color:var(--bad)}.flat{color:var(--mut)}
.panel h3{margin:0 0 10px;font-size:13px;color:var(--mut);font-weight:600;text-transform:uppercase;letter-spacing:.04em}
.panel canvas{max-height:240px}
.briefing{margin-top:18px;border-left:3px solid var(--accent)}
.briefing .meta{color:var(--mut);font-size:12px;margin-bottom:8px}
.briefing p{white-space:pre-wrap;margin:0}
@media(max-width:820px){.charts{grid-template-columns:1fr}}
</style></head>
<body>
<header><div><h1 id="title"></h1><div class="sub" id="sub"></div></div>
<div class="sub" id="gen"></div></header>
<main>
<section class="grid cards" id="cards"></section>
<section class="grid charts" id="charts">
  <div class="panel"><h3>Balance Sheet (€)</h3><canvas id="c_bs"></canvas></div>
  <div class="panel"><h3>Risk &amp; Liquidity</h3><canvas id="c_risk"></canvas></div>
  <div class="panel"><h3>Transaction Volume</h3><canvas id="c_vol"></canvas></div>
  <div class="panel"><h3>Deposits by Segment (€)</h3><canvas id="c_seg"></canvas></div>
  <div class="panel"><h3>Transactions by Channel (€)</h3><canvas id="c_chan"></canvas></div>
  <div class="panel"><h3>Loans by Status (€)</h3><canvas id="c_loan"></canvas></div>
</section>
<section class="panel briefing" id="briefing" style="display:none">
  <h3>AI Executive Briefing</h3><div class="meta" id="b_meta"></div><p id="b_text"></p></section>
</main>
<script>
const D = __DATA__;
const eur = v => v==null ? "—" : "€"+Number(v).toLocaleString(undefined,{maximumFractionDigits:0});
const num = v => v==null ? "—" : Number(v).toLocaleString(undefined,{maximumFractionDigits:2});
document.getElementById("title").textContent = D.title;
document.getElementById("sub").textContent = "Reporting period " + D.dates[0] + " → " + D.dates[D.dates.length-1];
document.getElementById("gen").textContent = "Generated " + D.generated;

const L = D.latest;
const cardDefs = [
 ["Total Deposits", eur(L.total_deposits_eur), L.deposits_growth_pct, "%"],
 ["Net Loans", eur(L.net_loans_eur), L.loans_growth_pct, "%"],
 ["Loan-to-Deposit", num(L.loan_to_deposit_ratio), null],
 ["Net Interest Margin", num(L.nim_pct)+"%", null],
 ["Fee Income", eur(L.fee_income_eur), null],
 ["Txn Volume", num(L.txn_count), null],
 ["Txn Value", eur(L.txn_value_eur), null],
 ["NPL Rate", num(L.npl_rate_pct)+"%", null],
 ["Active Customers", num(L.active_customers), L.net_new_customers, "n"],
 ["Digital Mix", num(L.digital_channel_mix_pct)+"%", null],
];
document.getElementById("cards").innerHTML = cardDefs.map(([k,v,delta,kind])=>{
  let d="";
  if(delta!=null){const cls=delta>0?"up":delta<0?"down":"flat";const arrow=delta>0?"▲":delta<0?"▼":"▪";
    d=`<div class="d ${cls}">${arrow} ${kind==="%"?num(delta)+"% vs prior day":(delta>0?"+":"")+num(delta)+" net new"}</div>`;}
  return `<div class="card"><div class="k">${k}</div><div class="v">${v}</div>${d}</div>`;
}).join("");

const ink="#e7eef6",mut="#8aa0b2",grid="#26323f";
Chart.defaults.color=mut;Chart.defaults.borderColor=grid;Chart.defaults.font.family="-apple-system,Segoe UI,Roboto";
const line=(id,labels,ds,opts={})=>new Chart(document.getElementById(id),{type:"line",
  data:{labels,datasets:ds},options:{responsive:true,plugins:{legend:{labels:{boxWidth:12}}},
  elements:{point:{radius:2}},...opts}});
const bar=(id,labels,data,color)=>new Chart(document.getElementById(id),{type:"bar",
  data:{labels,datasets:[{data,backgroundColor:color,borderRadius:4}]},
  options:{responsive:true,plugins:{legend:{display:false}}}});

line("c_bs",D.dates,[
 {label:"Deposits",data:D.series.total_deposits_eur,borderColor:"#3aa0ff",backgroundColor:"#3aa0ff22",fill:true,tension:.3},
 {label:"Net Loans",data:D.series.net_loans_eur,borderColor:"#9b8cff",backgroundColor:"#9b8cff22",fill:true,tension:.3}]);
line("c_risk",D.dates,[
 {label:"Loan-to-Deposit",data:D.series.loan_to_deposit_ratio,borderColor:"#3aa0ff",yAxisID:"y",tension:.3},
 {label:"NPL %",data:D.series.npl_rate_pct,borderColor:"#ff5a6a",yAxisID:"y1",tension:.3}],
 {scales:{y:{position:"left"},y1:{position:"right",grid:{drawOnChartArea:false}}}});
bar("c_vol",D.dates,D.series.txn_count,"#3aa0ff");

if(D.deposits_by_segment) bar("c_seg",D.deposits_by_segment.map(r=>r.segment),D.deposits_by_segment.map(r=>r.deposits_eur),"#2ecc71");
if(D.txn_by_channel) bar("c_chan",D.txn_by_channel.map(r=>r.channel),D.txn_by_channel.map(r=>r.txn_value_eur),"#3aa0ff");
if(D.loans_by_status) bar("c_loan",D.loans_by_status.map(r=>r.status),D.loans_by_status.map(r=>r.principal_outstanding_eur),"#ff9f43");

if(D.narrative){document.getElementById("briefing").style.display="block";
  document.getElementById("b_meta").textContent=`${D.narrative.date} · via ${D.narrative.model}`;
  document.getElementById("b_text").textContent=D.narrative.text;}
</script></body></html>"""
