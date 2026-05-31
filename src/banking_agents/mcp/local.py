"""Local filesystem MCP backend — stands in for OneLake during dev/tests.

Layout mimics partitioned Delta tables:

    data/<layer>/<name>/_batch=<batch_id>/part.parquet

- ``append``    : writes a new batch partition (each batch keyed separately).
- ``overwrite`` : clears the table dir first, then writes one partition.
- Idempotency  : re-writing the same ``batch_id`` overwrites that partition only
                 (never duplicate-appends) — matching docs/AGENTS.md §0.
"""
from __future__ import annotations

import shutil
from pathlib import Path

import pandas as pd

from .base import MCPClient, Table


class LocalFilesystemMCP(MCPClient):
    def __init__(self, root: str | Path):
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)

    def _table_dir(self, table: str) -> Path:
        t = Table.parse(table)
        return self.root / t.layer / t.name

    def write(self, table: str, df: pd.DataFrame, *, mode: str = "append",
              batch_id: str | None = None) -> int:
        if mode not in ("append", "overwrite"):
            raise ValueError(f"mode must be 'append' or 'overwrite', got {mode!r}")
        tdir = self._table_dir(table)
        if mode == "overwrite" and tdir.exists():
            shutil.rmtree(tdir)
        batch_id = batch_id or "default"
        part = tdir / f"_batch={batch_id}"
        if part.exists():               # idempotent: same batch overwrites itself
            shutil.rmtree(part)
        part.mkdir(parents=True, exist_ok=True)
        df.to_parquet(part / "part.parquet", index=False)
        return len(df)

    def read(self, table: str) -> pd.DataFrame:
        tdir = self._table_dir(table)
        parts = sorted(tdir.glob("_batch=*/part.parquet"))
        if not parts:
            raise FileNotFoundError(f"No data for table '{table}' under {tdir}")
        return pd.concat((pd.read_parquet(p) for p in parts), ignore_index=True)

    def table_exists(self, table: str) -> bool:
        return any(self._table_dir(table).glob("_batch=*/part.parquet"))

    def list_tables(self) -> list[str]:
        out = []
        for layer_dir in sorted(p for p in self.root.iterdir() if p.is_dir()):
            for name_dir in sorted(p for p in layer_dir.iterdir() if p.is_dir()):
                if any(name_dir.glob("_batch=*/part.parquet")):
                    out.append(f"{layer_dir.name}.{name_dir.name}")
        return out
