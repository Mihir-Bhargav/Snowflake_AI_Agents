"""Extraction Agent (Stage 1 → Bronze). Lands raw source data as-is with lineage.

No transformation, schema-on-read. Idempotent: each source writes its own batch keyed
by run_id, so a re-run overwrites rather than duplicates. See docs/AGENTS.md §2.
"""
from __future__ import annotations

from datetime import date, datetime, timezone

import pandas as pd

from ..config import SourceConfig
from ..connectors import SyntheticConnector
from ..orchestration import RunManifest
from .base import Agent


def add_lineage(df: pd.DataFrame, *, source_id: str, batch_id: str, run_id: str) -> pd.DataFrame:
    """Stamp the lineage metadata every Bronze row carries (docs/DATA_MODEL.md §0)."""
    df = df.copy()
    df["_ingested_at"] = datetime.now(timezone.utc)
    df["_source_system"] = source_id
    df["_batch_id"] = batch_id
    df["_run_id"] = run_id
    df["_source_file"] = f"synthetic://{source_id}"
    return df


class ExtractionAgent(Agent):
    name = "extraction"

    def __init__(self, mcp, connector, model=None):
        super().__init__(mcp, model=model)
        self.connector = connector

    def run(self, manifest: RunManifest, *, registry: dict[str, SourceConfig],
            as_of: date, **_) -> dict:
        total = 0
        landed: list[str] = []
        for source_id, cfg in registry.items():
            df = self.connector.read(cfg, as_of)
            # Batch keyed by the business window (not run_id) so re-running a day is
            # idempotent: an incremental re-run overwrites that day's partition.
            batch_id = f"{source_id}:{as_of.isoformat()}"
            df = add_lineage(df, source_id=source_id, batch_id=batch_id, run_id=manifest.run_id)
            mode = "append" if cfg.read_mode == "incremental" else "overwrite"
            rows = self.mcp.write(cfg.target_bronze, df, mode=mode, batch_id=batch_id)
            total += rows
            landed.append(cfg.target_bronze)

        # Reference/lookup tables (fully refreshed each run).
        if isinstance(self.connector, SyntheticConnector):
            for ref_name, ref in self.connector.reference().items():
                table = f"bronze.{ref_name}"
                ref = add_lineage(ref, source_id=ref_name, batch_id=manifest.run_id, run_id=manifest.run_id)
                self.mcp.write(table, ref, mode="overwrite", batch_id=manifest.run_id)
                landed.append(table)

        return {"rows": total, "tables": landed, "note": f"landed {len(landed)} Bronze tables"}
