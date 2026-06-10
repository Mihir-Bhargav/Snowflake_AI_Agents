"""Render a selected template + source filter into a report.

Locally this produces a self-contained HTML file (the stand-in). In Fabric the same resolved
spec is what you hand to the Power BI REST API to create a report bound to the semantic model
(see fabric_integration.md). The renderer reads ONLY the governed Gold marts each visual declares.
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd

from ..mcp import MCPClient
from ..agents.visualization import Selection

_ACRONYMS = {"eur": "€", "npl": "NPL", "nim": "NIM", "stp": "STP", "sla": "SLA",
             "kpi": "KPI", "hr": "HR", "ms": "(ms)", "pct": ""}


def _label(field: str) -> str:
    field = field.replace("_eur", "").replace("_pct", "")
    parts = [_ACRONYMS.get(w, w.capitalize()) for w in field.split("_")]
    return " ".join(p for p in parts if p).strip()


def _fmt(field: str, value) -> str:
    if value is None or (isinstance(value, float) and pd.isna(value)):
        return "—"
    try:
        v = float(value)
    except (TypeError, ValueError):
        return str(value)
    if field.endswith("_eur"):
        return "€" + f"{v:,.0f}"
    if field.endswith("_pct"):
        return f"{v:,.2f}%"
    return f"{v:,.0f}"


def _latest(df: pd.DataFrame) -> pd.DataFrame:
    if "date" in df.columns and len(df):
        return df[df["date"].astype(str) == str(df["date"].max())]
    return df


def resolve(selection: Selection, mcp: MCPClient) -> dict:
    """Bind the template's visuals to live Gold data → a render-ready report dict."""
    t, source = selection.template, selection.source
    out_visuals = []
    for v in t.visuals:
        table = v["source"]
        if not mcp.table_exists(table):
            out_visuals.append({"type": "empty", "title": v.get("title", table),
                                "note": f"No data in {table} — run the pipeline / `seed-marts`."})
            continue
        df = mcp.read(table)
        if v.get("filter") == "source" and source and "source" in df.columns:
            df = df[df["source"] == source]
        if df.empty:
            out_visuals.append({"type": "empty", "title": v.get("title", table), "note": "No rows match."})
            continue

        if v["type"] == "kpi_cards":
            row = _latest(df).iloc[-1]
            cards = [{"label": _label(f), "value": _fmt(f, row.get(f))}
                     for f in v["fields"] if f in df.columns]
            out_visuals.append({"type": "kpi_cards", "cards": cards})
        elif v["type"] == "line":
            d = df.sort_values(v["x"])
            series = [{"name": _label(s),
                       "data": [None if pd.isna(x) else float(x) for x in d[s]]}
                      for s in v["series"] if s in d.columns]
            out_visuals.append({"type": "line", "title": v.get("title", ""),
                                "labels": [str(x) for x in d[v["x"]]], "series": series})
        elif v["type"] in ("bar", "donut"):
            d = _latest(df).groupby(v["x"], as_index=False)[v["y"]].sum()
            out_visuals.append({"type": v["type"], "title": v.get("title", ""),
                                "labels": [str(x) for x in d[v["x"]]],
                                "data": [float(x) for x in d[v["y"]]]})

    title = t.title + (f" — {source.title()}" if source else "")
    return {"title": title, "domain": t.domain, "description": t.description,
            "template_id": t.id, "source": source,
            "generated": datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC"),
            "visuals": out_visuals}


def render_html(report: dict) -> str:
    return _TEMPLATE.replace("__DATA__", json.dumps(report))


def build_report(selection: Selection, mcp: MCPClient, out_dir: Path) -> Path:
    report = resolve(selection, mcp)
    out_dir.mkdir(parents=True, exist_ok=True)
    name = report["template_id"] + (f"_{report['source']}" if report["source"] else "") + ".html"
    path = out_dir / name
    path.write_text(render_html(report))
    return path


_TEMPLATE = r"""<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Report</title>
<script src="https://cdn.jsdelivr.net/npm/chart.js@4.4.1/dist/chart.umd.min.js"></script>
<style>
:root{--bg:#0f1720;--card:#18222e;--ink:#e7eef6;--mut:#8aa0b2;--line:#26323f;--accent:#3aa0ff}
*{box-sizing:border-box}body{margin:0;font:14px/1.5 -apple-system,Segoe UI,Roboto,Arial;background:var(--bg);color:var(--ink)}
header{padding:22px 28px;border-bottom:1px solid var(--line)}
h1{font-size:19px;margin:0}.sub{color:var(--mut);font-size:12px;margin-top:4px}
.tag{display:inline-block;font-size:11px;color:var(--accent);border:1px solid var(--accent);border-radius:10px;padding:1px 8px;margin-right:6px}
main{padding:22px 28px;max-width:1200px;margin:0 auto}
.grid{display:grid;gap:14px;grid-template-columns:repeat(2,1fr)}
.cards{grid-template-columns:repeat(auto-fill,minmax(190px,1fr))}
.panel,.card{background:var(--card);border:1px solid var(--line);border-radius:12px;padding:16px}
.full{grid-column:1/-1}.card .k{color:var(--mut);font-size:12px;text-transform:uppercase;letter-spacing:.04em}
.card .v{font-size:23px;font-weight:650;margin-top:6px}
.panel h3{margin:0 0 10px;font-size:13px;color:var(--mut);font-weight:600;text-transform:uppercase}
.panel canvas{max-height:240px}.empty{color:var(--mut)}
@media(max-width:820px){.grid{grid-template-columns:1fr}}
</style></head>
<body>
<header><h1 id="title"></h1><div class="sub"><span class="tag" id="domain"></span><span id="desc"></span></div>
<div class="sub" id="gen"></div></header>
<main><section class="grid" id="root"></section></main>
<script>
const D=__DATA__;
document.getElementById("title").textContent=D.title;
document.getElementById("domain").textContent=D.domain.toUpperCase();
document.getElementById("desc").textContent=D.description;
document.getElementById("gen").textContent="Generated "+D.generated+" · template: "+D.template_id;
const mut="#8aa0b2",grid="#26323f";Chart.defaults.color=mut;Chart.defaults.borderColor=grid;
const PALETTE=["#3aa0ff","#9b8cff","#2ecc71","#ff9f43","#ff5a6a","#36d1c4"];
const root=document.getElementById("root");let n=0;
function panel(title,full){const d=document.createElement("div");d.className="panel"+(full?" full":"");
  if(title){const h=document.createElement("h3");h.textContent=title;d.appendChild(h);}root.appendChild(d);return d;}
D.visuals.forEach(v=>{
  if(v.type==="kpi_cards"){const wrap=document.createElement("section");wrap.className="grid cards full";
    wrap.innerHTML=v.cards.map(c=>`<div class="card"><div class="k">${c.label}</div><div class="v">${c.value}</div></div>`).join("");
    root.appendChild(wrap);return;}
  if(v.type==="empty"){const p=panel(v.title,true);const e=document.createElement("div");e.className="empty";e.textContent=v.note;p.appendChild(e);return;}
  const p=panel(v.title,false);const cv=document.createElement("canvas");cv.id="c"+(n++);p.appendChild(cv);
  if(v.type==="line"){new Chart(cv,{type:"line",data:{labels:v.labels,datasets:v.series.map((s,i)=>({label:s.name,data:s.data,borderColor:PALETTE[i%6],backgroundColor:PALETTE[i%6]+"22",tension:.3,fill:v.series.length===1}))},options:{responsive:true,elements:{point:{radius:2}},plugins:{legend:{labels:{boxWidth:12}}}}});}
  else if(v.type==="bar"){new Chart(cv,{type:"bar",data:{labels:v.labels,datasets:[{data:v.data,backgroundColor:PALETTE.slice(0,v.labels.length).concat(PALETTE).slice(0,v.labels.length),borderRadius:4}]},options:{responsive:true,plugins:{legend:{display:false}}}});}
  else if(v.type==="donut"){new Chart(cv,{type:"doughnut",data:{labels:v.labels,datasets:[{data:v.data,backgroundColor:PALETTE}]},options:{responsive:true}});}
});
</script></body></html>"""
