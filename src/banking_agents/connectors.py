"""Source connectors. A connector turns a source-registry entry into a DataFrame for a
given run window. Phase 1/2 ships the ``synthetic`` connector; ``jdbc``/``rest``/``cdc``
land later and only need to satisfy the same ``read(source_cfg, as_of)`` shape.
"""
from __future__ import annotations

from datetime import date

import pandas as pd

from .config import SourceConfig
from .synthetic.generators import BankUniverse, GeneratorConfig, reference_tables


class SyntheticConnector:
    """Holds one consistent BankUniverse so every source stays referentially intact."""

    name = "synthetic"

    def __init__(self, registry: dict[str, SourceConfig], seed: int | None = None):
        self._universe = BankUniverse(
            GeneratorConfig(
                customer_count=registry["customers"].synthetic.get("customer_count", 1200),
                account_count=registry["accounts"].synthetic.get("account_count", 2000),
                loan_count=registry["loans"].synthetic.get("loan_count", 600),
                txns_per_day=registry["core_transactions"].synthetic.get("rows_per_day", 5000),
                seed=seed,
            )
        )

    def read(self, source_cfg: SourceConfig, as_of: date) -> pd.DataFrame:
        u, domain = self._universe, source_cfg.domain
        if domain == "transactions":
            return u.transactions_for_day(as_of)
        if domain == "accounts":
            return u.accounts_snapshot(as_of)
        if domain == "customers":
            return u.customers.copy()
        if domain == "loans":
            return u.loans_snapshot(as_of)
        raise ValueError(f"SyntheticConnector has no generator for domain '{domain}'")

    def reference(self) -> dict[str, pd.DataFrame]:
        """Lookup/reference tables, fully refreshed each run."""
        return reference_tables()


def get_connector(name: str, registry: dict[str, SourceConfig], seed: int | None = None):
    if name == "synthetic":
        return SyntheticConnector(registry, seed=seed)
    raise NotImplementedError(f"Connector '{name}' not implemented yet (Phase 1/2 = synthetic only).")
