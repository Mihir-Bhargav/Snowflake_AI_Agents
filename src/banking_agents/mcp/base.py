"""Abstract MCP client — the governed tool surface agents are allowed to call.

Conceptual tools (docs/ARCHITECTURE.md §6): OneLake I/O, run_notebook, sql_query,
pipeline_run, powerbi_refresh. Storage I/O is abstract (every backend must provide it);
the Fabric-orchestration tools default to "not available" so a local backend need not
implement them.
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass

import pandas as pd


@dataclass(frozen=True)
class Table:
    """A logical table name like ``bronze.transactions`` -> (layer, name)."""

    layer: str
    name: str

    @classmethod
    def parse(cls, qualified: str) -> "Table":
        if "." not in qualified:
            raise ValueError(f"Table name must be 'layer.name', got {qualified!r}")
        layer, name = qualified.split(".", 1)
        return cls(layer=layer, name=name)

    def __str__(self) -> str:
        return f"{self.layer}.{self.name}"


class MCPClient(ABC):
    """Single seam between agents and the platform. Agents never bypass this."""

    # --- OneLake storage I/O (required of every backend) ----------------------
    @abstractmethod
    def write(self, table: str, df: pd.DataFrame, *, mode: str = "append",
              batch_id: str | None = None) -> int:
        """Write ``df`` to ``table``. mode: 'append' | 'overwrite'. Returns rows written."""

    @abstractmethod
    def read(self, table: str) -> pd.DataFrame:
        """Read a full logical table back as a DataFrame."""

    @abstractmethod
    def table_exists(self, table: str) -> bool:
        ...

    def row_count(self, table: str) -> int:
        return len(self.read(table)) if self.table_exists(table) else 0

    # --- Fabric orchestration tools (optional per backend) --------------------
    def run_notebook(self, name: str, params: dict | None = None):  # pragma: no cover
        raise NotImplementedError(f"{type(self).__name__} cannot run_notebook('{name}')")

    def sql_query(self, query: str):  # pragma: no cover
        raise NotImplementedError(f"{type(self).__name__} does not support sql_query")

    def pipeline_run(self, name: str):  # pragma: no cover
        raise NotImplementedError(f"{type(self).__name__} cannot pipeline_run('{name}')")

    def powerbi_refresh(self, dataset: str):  # pragma: no cover
        raise NotImplementedError(f"{type(self).__name__} cannot powerbi_refresh('{dataset}')")
