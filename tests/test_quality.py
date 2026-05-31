"""Tests for the contract validator (reused later by the QA agent)."""
import pandas as pd

from banking_agents.quality import summarize, validate

CONTRACT = {
    "schema": {"required_columns": {"id": "string", "kind": "string", "amt": "decimal"}},
    "checks": {
        "not_null": ["id", "amt"],
        "unique": ["id"],
        "allowed_values": {"kind": ["a", "b"]},
        "ranges": {"amt": {"min": -100, "max": 100}},
        "row_count": {"tolerance_pct": 40},
    },
}


def test_clean_data_passes_all_hard_checks():
    df = pd.DataFrame({"id": [1, 2, 3], "kind": ["a", "b", "a"], "amt": [10, -5, 99]})
    results = validate(df, CONTRACT)
    assert summarize(results) == "warn"  # only the row_count history check warns
    assert all(r.ok for r in results)    # no hard failures


def test_each_violation_is_caught():
    df = pd.DataFrame({"id": [1, 1, None], "kind": ["a", "z", "b"], "amt": [10, 250, -5]})
    by = {r.check: r for r in validate(df, CONTRACT)}
    assert by["not_null[id]"].outcome == "fail"
    assert by["unique[id]"].outcome == "fail"
    assert by["allowed_values[kind]"].outcome == "fail"
    assert by["range[amt]"].outcome == "fail"
    assert summarize(validate(df, CONTRACT)) == "fail"


def test_missing_required_column_fails():
    df = pd.DataFrame({"id": [1], "amt": [5]})  # no 'kind'
    by = {r.check: r for r in validate(df, CONTRACT)}
    assert by["required_columns"].outcome == "fail"
