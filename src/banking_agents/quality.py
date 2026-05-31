"""Data-quality checks against a contract (docs/DATA_MODEL.md §5).

Reusable library: the Phase-1 inspector calls it now; the Phase-2 Validation/QA agent
will call the same code at each layer boundary. Pure function of (DataFrame, contract).
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pandas as pd
import yaml

from .config import REPO_ROOT


@dataclass
class CheckResult:
    check: str
    outcome: str          # "pass" | "warn" | "fail"
    detail: str = ""

    @property
    def ok(self) -> bool:
        return self.outcome != "fail"


def load_contract(name_or_path: str) -> dict[str, Any]:
    """Accept either 'contracts/transactions.yaml' or a bare contract name."""
    path = Path(name_or_path)
    if not path.is_absolute():
        path = REPO_ROOT / path
    if not path.exists() and not str(name_or_path).endswith(".yaml"):
        path = REPO_ROOT / "contracts" / f"{name_or_path}.yaml"
    return yaml.safe_load(path.read_text())


def validate(df: pd.DataFrame, contract: dict[str, Any]) -> list[CheckResult]:
    """Run every check the contract declares; return one CheckResult per check."""
    results: list[CheckResult] = []
    schema = contract.get("schema", {})
    checks = contract.get("checks", {})

    # 1. required columns present
    required = list(schema.get("required_columns", {}))
    missing = [c for c in required if c not in df.columns]
    results.append(CheckResult(
        "required_columns",
        "fail" if missing else "pass",
        f"missing: {missing}" if missing else f"all {len(required)} present",
    ))

    # 2. not-null
    for col in checks.get("not_null", []):
        if col not in df.columns:
            results.append(CheckResult(f"not_null[{col}]", "fail", "column absent"))
            continue
        n = int(df[col].isna().sum())
        results.append(CheckResult(f"not_null[{col}]", "fail" if n else "pass",
                                   f"{n} null(s)" if n else "no nulls"))

    # 3. uniqueness
    for col in checks.get("unique", []):
        if col not in df.columns:
            results.append(CheckResult(f"unique[{col}]", "fail", "column absent"))
            continue
        dupes = int(df[col].duplicated().sum())
        results.append(CheckResult(f"unique[{col}]", "fail" if dupes else "pass",
                                   f"{dupes} duplicate(s)" if dupes else "all unique"))

    # 4. allowed values
    for col, allowed in checks.get("allowed_values", {}).items():
        if col not in df.columns:
            results.append(CheckResult(f"allowed_values[{col}]", "fail", "column absent"))
            continue
        bad = sorted(set(df[col].dropna().unique()) - set(allowed))
        results.append(CheckResult(f"allowed_values[{col}]", "fail" if bad else "pass",
                                   f"unexpected: {bad}" if bad else "within allowed set"))

    # 5. numeric ranges
    for col, bounds in checks.get("ranges", {}).items():
        if col not in df.columns:
            results.append(CheckResult(f"range[{col}]", "fail", "column absent"))
            continue
        lo, hi = bounds.get("min"), bounds.get("max")
        out = df[(df[col] < lo) | (df[col] > hi)] if lo is not None and hi is not None else df.iloc[0:0]
        results.append(CheckResult(f"range[{col}]", "fail" if len(out) else "pass",
                                   f"{len(out)} outside [{lo},{hi}]" if len(out) else f"within [{lo},{hi}]"))

    # 6. row-count tolerance — needs trailing history; not available in Phase 1
    if "row_count" in checks:
        results.append(CheckResult("row_count_tolerance", "warn",
                                   "skipped — needs trailing history (available once runs accumulate)"))
    return results


def summarize(results: list[CheckResult]) -> str:
    """Aggregate to a single outcome for a QA gate: fail > warn > pass."""
    if any(r.outcome == "fail" for r in results):
        return "fail"
    if any(r.outcome == "warn" for r in results):
        return "warn"
    return "pass"
