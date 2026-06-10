"""``make-report`` — agentic, template-driven report creation from a natural-language request.

Copilot's local stand-in: a request in, a governed report out. The VisualizationAgent picks a
template (+ source filter), the renderer binds it to Gold and writes an HTML report.
"""
from __future__ import annotations

import argparse
from pathlib import Path

from ..agents.visualization import VisualizationAgent
from ..config import REPO_ROOT, ModelConfig
from ..mcp import LocalFilesystemMCP
from ..model import get_provider
from .render import build_report


def make_report(request: str, root: Path, out_dir: Path) -> Path:
    mcp = LocalFilesystemMCP(root)
    try:
        model = get_provider(ModelConfig.load())
    except Exception:
        model = None
    agent = VisualizationAgent(model=model)
    selection = agent.select(request)
    path = build_report(selection, mcp, out_dir)

    src = f", source={selection.source}" if selection.source else ""
    print(f'Request : "{request}"')
    print(f"Template: {selection.template.id}  (via {selection.via}{src})")
    print(f"Reason  : {selection.reason}")
    print(f"Report  : {path}")
    return path


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description="Generate a governed report from a natural-language request.")
    p.add_argument("request", help='e.g. "show me workforce attrition" or "loans activity by source"')
    p.add_argument("--root", type=Path, default=REPO_ROOT / "data" / "lake")
    p.add_argument("--out-dir", type=Path, default=REPO_ROOT / "data" / "reports")
    args = p.parse_args(argv)
    make_report(args.request, args.root, args.out_dir)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
