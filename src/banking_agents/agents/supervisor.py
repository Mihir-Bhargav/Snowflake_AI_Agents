"""Supervisor — the agentic decision layer (docs/AGENTS.md §1).

This is what makes the Initiator *reason* rather than follow a fixed rule. When a QA gate
HARD-FAILS, the Supervisor is given the failed checks as evidence and asks the model to
choose the best action:

    retry     — the failure looks transient/infrastructural; re-running may fix it
    halt      — data integrity is compromised; stop and do not publish
    escalate  — systemic or repeated failure; halt AND flag for a human
    proceed   — the failure is minor and non-blocking; continue

Critical property: it degrades safely. If no model is wired, the model errors, or the
response can't be parsed, it falls back to the conservative deterministic policy (HALT on a
hard fail) — identical to the pre-agentic behaviour. So the agentic layer can only ever make
the pipeline *smarter*, never less safe.
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass

from ..model import ModelProvider
from ..orchestration import AuditEntry, RunManifest

ACTIONS = {"retry", "halt", "escalate", "proceed"}

SYSTEM = (
    "You are the supervising orchestrator of a nightly banking data pipeline. A data-quality "
    "gate has FAILED. Using only the failed checks provided as evidence, choose the single best "
    "action. Options:\n"
    "- 'retry': the failure looks transient or infrastructural (e.g. an empty/partial load) and "
    "re-running the stage may resolve it.\n"
    "- 'halt': data integrity is compromised; stop and do not publish.\n"
    "- 'escalate': the failure is systemic or has recurred; halt AND flag for a human.\n"
    "- 'proceed': the issue is minor and genuinely non-blocking; continue.\n"
    "Be conservative: NEVER 'proceed' when integrity is violated (duplicate keys, nulls in key "
    "fields, broken referential integrity, or out-of-range values). "
    'Respond with ONLY a JSON object: {"action": "<one of retry|halt|escalate|proceed>", '
    '"reason": "<one concise sentence>"}.'
)


@dataclass
class Decision:
    action: str
    reason: str


class SupervisorAgent:
    name = "supervisor"

    def __init__(self, model: ModelProvider | None, mcp=None):
        self.model = model
        self.mcp = mcp

    def decide(self, manifest: RunManifest, layer: str, qa_results,
               *, retries_so_far: int = 0) -> Decision:
        failed = [r for r in qa_results if getattr(r, "outcome", None) == "fail"]
        # Accept either QAResult (.stage/.details) or CheckResult (.check/.detail).
        def _ev(r) -> str:
            label = getattr(r, "check", None) or getattr(r, "stage", "check")
            detail = getattr(r, "detail", None) or getattr(r, "details", "")
            return f"- {label}: {detail}"
        evidence = "\n".join(_ev(r) for r in failed) or "- (unspecified failure)"

        decision = self._reason(layer, evidence, retries_so_far)

        # Guard against unbounded retrying: a second 'retry' becomes an escalation.
        if decision.action == "retry" and retries_so_far >= 1:
            decision = Decision("escalate", "retry budget exhausted; escalating for review")

        manifest.record_audit(AuditEntry(
            actor=self.name, action=f"decide:{layer}", outcome=decision.action,
            note=decision.reason))
        return decision

    # -- internals -------------------------------------------------------------
    def _reason(self, layer: str, evidence: str, retries_so_far: int) -> Decision:
        if self.model is None:
            return Decision("halt", "no model wired; conservative deterministic halt on fail")
        prompt = (f"Gate: {layer}\nRetries already attempted: {retries_so_far}\n"
                  f"Failed checks (evidence):\n{evidence}")
        try:
            resp = self.model.complete(SYSTEM, [{"role": "user", "content": prompt}])
            return self._parse(resp.text)
        except Exception as exc:
            return Decision("halt", f"supervisor fallback ({type(exc).__name__}); halting on fail")

    @staticmethod
    def _parse(text: str) -> Decision:
        match = re.search(r"\{.*\}", text, re.DOTALL)
        if not match:
            return Decision("halt", "unparseable model response; halting on fail")
        try:
            data = json.loads(match.group(0))
            action = str(data.get("action", "")).strip().lower()
            reason = str(data.get("reason", "")).strip() or "(no reason given)"
        except (json.JSONDecodeError, AttributeError):
            return Decision("halt", "invalid JSON from model; halting on fail")
        if action not in ACTIONS:
            return Decision("halt", f"unknown action '{action}'; halting on fail")
        return Decision(action, reason)
