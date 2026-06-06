"""``generate-data`` — extract-only shortcut: land synthetic data into Bronze.

Thin wrapper over the ExtractionAgent (the single source of truth for landing). For the
full Bronze->Silver->Gold flow use ``run-pipeline``.
"""
from __future__ import annotations

import argparse
from datetime import date
from pathlib import Path

from ..agents.extraction import ExtractionAgent
from ..config import REPO_ROOT, load_source_registry
from ..connectors import get_connector
from ..mcp import LocalFilesystemMCP
from ..orchestration import RunManifest, StageStatus


def generate_and_land(as_of: date, root: Path) -> RunManifest:
    registry = load_source_registry()
    mcp = LocalFilesystemMCP(root)
    connector = get_connector(next(iter(registry.values())).connector, registry)

    manifest = RunManifest.new(trigger="manual", window_from=as_of, window_to=as_of,
                               sources=list(registry), stages=["extract"])
    manifest.set_stage("extract", StageStatus.RUNNING)
    result = ExtractionAgent(mcp, connector).execute(manifest, action="extract",
                                                      registry=registry, as_of=as_of)
    manifest.set_stage("extract", StageStatus.PASSED)
    manifest.finalize("success")
    path = manifest.save(root.parent / "ops" / "manifests")

    print(f"Run {manifest.run_id} (as_of={as_of})")
    for table in result["tables"]:
        print(f"  landed -> {table}")
    print(f"\nBronze tables: {', '.join(mcp.list_tables())}")
    print(f"Manifest saved: {path}")
    return manifest


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description="Generate synthetic banking data and land it into Bronze.")
    p.add_argument("--as-of", type=lambda s: date.fromisoformat(s), default=date.today())
    p.add_argument("--root", type=Path, default=REPO_ROOT / "data" / "lake")
    args = p.parse_args(argv)
    generate_and_land(args.as_of, args.root)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
