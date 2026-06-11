"""OneLakeMCP — the Fabric-native MCP backend. Writes the agents' output as **Delta tables**
in a Fabric Lakehouse (OneLake), so the SQL endpoint + Power BI see them as tables.

Same ``MCPClient`` interface as ``LocalFilesystemMCP`` — so agent code is byte-identical; only
the backend swaps. Uses delta-rs (the ``deltalake`` library, pure Python — no Spark needed),
which works both locally (for verification) and inside a Fabric Python notebook against the
mounted Lakehouse path.

Layout (schema-enabled Lakehouse):  <root>/<layer>/<name>   e.g. .../Tables/gold/kpi_daily
Idempotency: every write carries a hidden ``_batch`` column; an append replaces just that
batch's rows (read-modify-overwrite), so re-running a day overwrites it — never duplicates.
In Fabric, point ``root`` at the mounted tables path: /lakehouse/default/Tables
"""
from __future__ import annotations

from pathlib import Path

import pandas as pd
import pyarrow as pa
from deltalake import DeltaTable, write_deltalake

from .base import MCPClient, Table

FABRIC_TABLES_ROOT = "/lakehouse/default/Tables"   # default when a Lakehouse is attached


def _typed(arrow: pa.Table) -> pa.Table:
    """Delta rejects Arrow 'null' columns (all-null, e.g. SCD2 valid_to). Cast them to string."""
    for i, field in enumerate(arrow.schema):
        if pa.types.is_null(field.type):
            arrow = arrow.set_column(i, field.name, arrow.column(i).cast(pa.string()))
    return arrow


class OneLakeMCP(MCPClient):
    def __init__(self, root: str | Path = FABRIC_TABLES_ROOT):
        self.root = Path(root)

    def _path(self, table: str) -> Path:
        t = Table.parse(table)
        return self.root / t.layer / t.name

    @staticmethod
    def _is_delta(path: Path) -> bool:
        return (path / "_delta_log").exists()

    def write(self, table: str, df: pd.DataFrame, *, mode: str = "append",
              batch_id: str | None = None) -> int:
        if mode not in ("append", "overwrite"):
            raise ValueError(f"mode must be 'append' or 'overwrite', got {mode!r}")
        df = df.copy()
        df["_batch"] = batch_id or "default"
        path = self._path(table)
        path.parent.mkdir(parents=True, exist_ok=True)

        if mode == "append" and self._is_delta(path):
            existing = self.read(table, _keep_batch=True)
            existing = existing[existing["_batch"] != df["_batch"].iloc[0]]   # idempotent: drop same batch
            df = pd.concat([existing, df], ignore_index=True)

        arrow = _typed(pa.Table.from_pandas(df, preserve_index=False))
        write_deltalake(str(path), arrow, mode="overwrite", schema_mode="overwrite")
        return int((df["_batch"] == (batch_id or "default")).sum())

    def read(self, table: str, _keep_batch: bool = False) -> pd.DataFrame:
        path = self._path(table)
        if not self._is_delta(path):
            raise FileNotFoundError(f"No Delta table for '{table}' at {path}")
        df = DeltaTable(str(path)).to_pandas()
        return df if _keep_batch else df.drop(columns=["_batch"], errors="ignore")

    def table_exists(self, table: str) -> bool:
        return self._is_delta(self._path(table))

    def list_tables(self) -> list[str]:
        out: list[str] = []
        if not self.root.exists():
            return out
        for layer_dir in sorted(p for p in self.root.iterdir() if p.is_dir()):
            for name_dir in sorted(p for p in layer_dir.iterdir() if p.is_dir()):
                if self._is_delta(name_dir):
                    out.append(f"{layer_dir.name}.{name_dir.name}")
        return out
