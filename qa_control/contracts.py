"""Shared data contracts for the QA & Observability Control System.

Every component in the control plane communicates through these immutable-ish
dataclasses so that gate results, traces, and verdicts are serializable,
comparable, and reproducible across runs.
"""
from __future__ import annotations

import enum
from dataclasses import dataclass, field, asdict
from typing import Any


class Severity(enum.Enum):
    INFO = "info"
    WARNING = "warning"
    BLOCKING = "blocking"
    CRITICAL = "critical"


class GateStatus(enum.Enum):
    PASS = "pass"
    NEEDS_INPUT = "needs_input"   # recoverable: ask the user for more
    FAIL = "fail"                 # do not execute the workflow


class RunStatus(enum.Enum):
    RUNNING = "running"
    COMPLETED = "completed"
    ROLLED_BACK = "rolled_back"
    BLOCKED = "blocked"
    FAILED = "failed"


@dataclass
class Finding:
    """A single issue raised by any check in the system."""
    check: str
    severity: Severity
    message: str
    details: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        d["severity"] = self.severity.value
        return d


@dataclass
class GateResult:
    """Outcome of one pre-flight gate."""
    gate: str
    status: GateStatus
    score: float                      # 0.0 .. 1.0 confidence/sufficiency score
    findings: list[Finding] = field(default_factory=list)
    questions: list[str] = field(default_factory=list)  # for NEEDS_INPUT

    @property
    def passed(self) -> bool:
        return self.status is GateStatus.PASS

    def to_dict(self) -> dict[str, Any]:
        return {
            "gate": self.gate,
            "status": self.status.value,
            "score": round(self.score, 4),
            "findings": [f.to_dict() for f in self.findings],
            "questions": list(self.questions),
        }


@dataclass
class AgentMessage:
    """One message on the inter-agent bus."""
    sender: str
    recipient: str
    kind: str                 # e.g. "task", "result", "tool_call"
    payload: dict[str, Any]
    timestamp: float


@dataclass
class TraceEvent:
    """A single instrumentation event inside a span."""
    name: str
    timestamp: float
    attributes: dict[str, Any] = field(default_factory=dict)


@dataclass
class Usage:
    """Token and cost accounting for one model call."""
    input_tokens: int = 0
    output_tokens: int = 0
    cost_usd: float = 0.0

    def add(self, other: "Usage") -> None:
        self.input_tokens += other.input_tokens
        self.output_tokens += other.output_tokens
        self.cost_usd += other.cost_usd


@dataclass
class Checkpoint:
    """A snapshot of agent/workflow state that can be reverted to."""
    checkpoint_id: str
    run_id: str
    label: str
    state: dict[str, Any]
    created_at: float


@dataclass
class Verdict:
    """The meta-answer: is the system controlled enough to be monitored?"""
    controlled: bool
    reliability_score: float          # 0.0 .. 1.0
    reasons: list[str]
    kpis: dict[str, Any]

    def to_dict(self) -> dict[str, Any]:
        return {
            "controlled": self.controlled,
            "reliability_score": round(self.reliability_score, 4),
            "reasons": list(self.reasons),
            "kpis": self.kpis,
        }
