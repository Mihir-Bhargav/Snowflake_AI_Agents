"""Initiator / Orchestrator (docs/AGENTS.md §1). Owns a run end-to-end.

Sequences Extract -> Transform -> Aggregate with a QA gate after each hop, halting on a
hard QA fail (never promoting a layer that failed). Keeps the manifest + audit accurate
and persists it. The "initiator" of the trigger -> initiator -> workers flow.
"""
from __future__ import annotations

from datetime import date
from pathlib import Path

from ..agents.aggregation import AggregationAgent
from ..agents.extraction import ExtractionAgent
from ..agents.insight import InsightAgent
from ..agents.supervisor import SupervisorAgent
from ..agents.transformation import TransformationAgent
from ..agents.validation import ValidationAgent
from ..config import SourceConfig
from ..connectors import get_connector
from ..mcp import MCPClient
from ..model import ModelProvider
from .manifest import AuditEntry, QAResult, RunManifest, StageStatus


class Initiator:
    def __init__(self, mcp: MCPClient, registry: dict[str, SourceConfig],
                 *, model: ModelProvider | None = None, seed: int | None = None):
        self.mcp = mcp
        self.registry = registry
        self.model = model
        connector_name = next(iter(registry.values())).connector
        self.connector = get_connector(connector_name, registry, seed=seed)
        self.validator = ValidationAgent(mcp, model=model)
        # The agentic decision layer. Engaged only on a hard QA fail; on healthy runs it is
        # never called (no LLM cost). Falls back to deterministic HALT if no model is wired.
        self.supervisor = SupervisorAgent(model, mcp=mcp)

    def run(self, *, as_of: date, trigger: str = "schedule",
            manifest_dir: Path | None = None) -> RunManifest:
        # The insight stage runs only when a model provider is available.
        stages = ["extract", "transform", "aggregate"]
        if self.model is not None:
            stages.append("insight")
        manifest = RunManifest.new(
            trigger=trigger, window_from=as_of, window_to=as_of,
            sources=list(self.registry), stages=stages)

        try:
            extract = ExtractionAgent(self.mcp, self.connector, model=self.model)
            self._stage(manifest, "extract", extract, registry=self.registry, as_of=as_of)
            decision = self._gate(manifest, "bronze",
                                  rerun=lambda: self._stage(manifest, "extract", extract,
                                                            registry=self.registry, as_of=as_of),
                                  registry=self.registry)
            if decision != "proceed":
                return self._finish(manifest, "failed", "extract", manifest_dir, decision=decision)

            transform = TransformationAgent(self.mcp, model=self.model)
            self._stage(manifest, "transform", transform)
            decision = self._gate(manifest, "silver",
                                  rerun=lambda: self._stage(manifest, "transform", transform))
            if decision != "proceed":
                return self._finish(manifest, "failed", "transform", manifest_dir, decision=decision)

            self._stage(manifest, "aggregate", AggregationAgent(self.mcp, model=self.model))
            self._gate(manifest, "gold")  # gold warn/fail recorded; doesn't block (nothing downstream yet)

            # Insight narrative over Gold (only if a model is wired). Best-effort: the KPIs
            # are already complete, so a transient LLM failure must not fail the nightly run.
            if self.model is not None:
                self._stage_best_effort(manifest, "insight",
                                        InsightAgent(self.mcp, model=self.model), retries=1)

            return self._finish(manifest, "success", None, manifest_dir)
        except Exception:  # an agent raised — the base agent already audited it; fail the run
            manifest.finalize("failed")
            if manifest_dir:
                manifest.save(manifest_dir)
            raise

    # -- helpers ---------------------------------------------------------------
    def _stage(self, manifest: RunManifest, stage: str, agent, **kwargs) -> None:
        manifest.set_stage(stage, StageStatus.RUNNING)
        agent.execute(manifest, action=stage, **kwargs)
        manifest.set_stage(stage, StageStatus.PASSED)

    def _stage_best_effort(self, manifest: RunManifest, stage: str, agent, *, retries: int = 1) -> None:
        """Run a non-critical stage with retries; on final failure record a warning and
        continue (the run still succeeds). Used for the Insight narrative."""
        for attempt in range(retries + 1):
            manifest.set_stage(stage, StageStatus.RUNNING)
            try:
                agent.execute(manifest, action=stage)
                manifest.set_stage(stage, StageStatus.PASSED)
                return
            except Exception as exc:
                if attempt < retries:
                    continue
                manifest.set_stage(stage, StageStatus.FAILED)
                manifest.record_qa(QAResult(
                    stage=stage, outcome="warn",
                    details=f"{stage} skipped (non-fatal): {type(exc).__name__}: {exc}"))

    def _gate(self, manifest: RunManifest, layer: str, *, rerun=None, **kwargs) -> str:
        """Run a QA gate. On a hard fail, the Supervisor decides the action.

        Returns one of 'proceed' | 'halt' | 'escalate'. A supervisor 'retry' re-runs the
        stage (via ``rerun``) once and re-gates; a still-failing retry escalates.
        Gold has no ``rerun`` and simply records its outcome.
        """
        if self.validator.run(manifest, layer=layer, **kwargs)["outcome"] != "fail":
            return "proceed"

        decision = self.supervisor.decide(manifest, layer, self._failed_qa(manifest, layer))
        if decision.action == "retry" and rerun is not None:
            rerun()
            if self.validator.run(manifest, layer=layer, **kwargs)["outcome"] != "fail":
                return "proceed"
            decision = self.supervisor.decide(manifest, layer, self._failed_qa(manifest, layer),
                                              retries_so_far=1)
        return decision.action if decision.action in ("proceed", "escalate") else "halt"

    @staticmethod
    def _failed_qa(manifest: RunManifest, layer: str):
        return [q for q in manifest.qa
                if (q.stage == layer or q.stage.startswith(layer + ":")) and q.outcome == "fail"]

    def _finish(self, manifest: RunManifest, status: str, failed_stage: str | None,
                manifest_dir: Path | None, *, decision: str | None = None) -> RunManifest:
        if failed_stage:
            manifest.set_stage(failed_stage, StageStatus.FAILED)
        if decision == "escalate":
            manifest.record_audit(AuditEntry(
                actor="initiator", action="escalate", outcome="escalate",
                note=f"Supervisor escalated at '{failed_stage}' — flagged for human review."))
        manifest.finalize(status)
        if manifest_dir:
            manifest.save(manifest_dir)
        return manifest
