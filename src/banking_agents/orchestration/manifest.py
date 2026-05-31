"""The run manifest — the contract object every agent reads/updates (docs/AGENTS.md §0).

It threads run state, QA results, and an immutable audit log through a nightly run, and
serializes to JSON so it can be persisted (locally to disk; in Fabric to ``ops.run_manifest``).
"""
from __future__ import annotations

import json
import uuid
from dataclasses import asdict, dataclass, field
from datetime import date, datetime, timezone
from enum import Enum
from pathlib import Path


class StageStatus(str, Enum):
    PENDING = "pending"
    RUNNING = "running"
    PASSED = "passed"
    FAILED = "failed"
    SKIPPED = "skipped"


@dataclass
class QAResult:
    stage: str
    outcome: str             # "pass" | "warn" | "fail"
    details: str = ""
    checked_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())


@dataclass
class AuditEntry:
    actor: str               # which agent
    action: str              # what it did
    rows: int | None = None
    duration_s: float | None = None
    outcome: str = "ok"
    note: str = ""
    at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())


@dataclass
class RunManifest:
    run_id: str
    trigger: str
    window_from: str
    window_to: str
    sources: list[str]
    stages: dict[str, str] = field(default_factory=dict)
    qa: list[QAResult] = field(default_factory=list)
    audit: list[AuditEntry] = field(default_factory=list)
    status: str = "running"

    # -- construction ----------------------------------------------------------
    @classmethod
    def new(cls, *, trigger: str, window_from: date, window_to: date,
            sources: list[str], stages: list[str]) -> "RunManifest":
        run_id = f"{window_to:%Y-%m-%d}T00:00Z-{trigger}-{uuid.uuid4().hex[:6]}"
        return cls(
            run_id=run_id,
            trigger=trigger,
            window_from=window_from.isoformat(),
            window_to=window_to.isoformat(),
            sources=list(sources),
            stages={s: StageStatus.PENDING.value for s in stages},
        )

    # -- mutation helpers ------------------------------------------------------
    def set_stage(self, stage: str, status: StageStatus) -> None:
        self.stages[stage] = status.value

    def record_qa(self, result: QAResult) -> None:
        self.qa.append(result)

    def record_audit(self, entry: AuditEntry) -> None:
        self.audit.append(entry)

    def finalize(self, status: str) -> None:
        self.status = status

    # -- persistence -----------------------------------------------------------
    def to_dict(self) -> dict:
        d = asdict(self)
        return d

    def save(self, directory: str | Path) -> Path:
        directory = Path(directory)
        directory.mkdir(parents=True, exist_ok=True)
        path = directory / f"{self.run_id}.json"
        path.write_text(json.dumps(self.to_dict(), indent=2, default=str))
        return path
