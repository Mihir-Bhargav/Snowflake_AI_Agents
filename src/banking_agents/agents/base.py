"""Base agent: every agent acts on data *only* through the MCP, reasons through the
ModelProvider, and records what it did to the run manifest's audit log.

Concrete stage agents (Extraction, Transformation, Aggregation, Validation, Insight)
subclass this in Phase 2 and implement ``run``. The base enforces the shared contract:
timed execution + an audit entry every time.
"""
from __future__ import annotations

import time
from abc import ABC, abstractmethod

from ..mcp import MCPClient
from ..model import ModelProvider
from ..orchestration import AuditEntry, RunManifest


class Agent(ABC):
    #: human-readable actor name recorded in the audit log
    name: str = "agent"

    def __init__(self, mcp: MCPClient, model: ModelProvider | None = None):
        self.mcp = mcp
        self.model = model

    @abstractmethod
    def run(self, manifest: RunManifest, **kwargs) -> dict:
        """Do the stage's work. Return a small result dict (rows, tables, etc.)."""

    def execute(self, manifest: RunManifest, **kwargs) -> dict:
        """Run with timing + automatic audit entry. Stage agents call this, not run()."""
        started = time.perf_counter()
        try:
            result = self.run(manifest, **kwargs)
            manifest.record_audit(
                AuditEntry(
                    actor=self.name,
                    action=kwargs.get("action", "run"),
                    rows=result.get("rows"),
                    duration_s=round(time.perf_counter() - started, 3),
                    outcome="ok",
                    note=result.get("note", ""),
                )
            )
            return result
        except Exception as exc:  # record the failure, then re-raise for the orchestrator
            manifest.record_audit(
                AuditEntry(
                    actor=self.name,
                    action=kwargs.get("action", "run"),
                    duration_s=round(time.perf_counter() - started, 3),
                    outcome="error",
                    note=f"{type(exc).__name__}: {exc}",
                )
            )
            raise
