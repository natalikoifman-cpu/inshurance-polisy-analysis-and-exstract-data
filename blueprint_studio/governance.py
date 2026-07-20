"""Module 9 — Governance & Production Monitoring (§43, §47, §49).

The Decision Log records every business and technology decision; the
Audit Log correlates every client answer with its request id; the
Version Registry keeps rollback possible; and the deliverables
generator emits the §49 artifact checklist per capability, ending in a
Go/No-Go verdict.
"""
from __future__ import annotations

from dataclasses import dataclass, field, asdict
from typing import Any

#: §49 — the twenty deliverables the Studio produces per capability.
DELIVERABLES = (
    "business_need_document", "use_case_card", "question_bank",
    "data_to_answer_map", "field_list", "source_list", "gap_analysis",
    "developer_questions", "api_contracts", "semantic_model",
    "calculation_rules", "permission_rules", "agent_instructions",
    "dataset_plan", "evaluation_plan", "optimization_plan", "cost_model",
    "test_cases", "readiness_score", "go_no_go_decision",
)


@dataclass
class DecisionRecord:
    """§43 Decision Log — one business or technology decision."""
    decision_id: str
    subject: str
    decision: str
    rationale: str
    decided_by: str
    decided_at: str
    alternatives_considered: list[str] = field(default_factory=list)
    affected_use_cases: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class AuditEntry:
    """§4.3 — one auditable event (tool call, answer, refusal, override)."""
    request_id: str
    event: str
    actor: str
    at: str
    details: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class VersionRecord:
    """One released configuration of the agent, with rollback pointer."""
    version: str
    released_at: str
    prompt_version: str = ""
    model_version: str = ""
    schema_version: str = ""
    embedding_version: str = ""
    approved_by: str = ""
    previous_version: str = ""       # rollback target
    notes: str = ""

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


class GovernanceModule:
    """Decision log, audit trail and version registry."""

    def __init__(self) -> None:
        self._decisions: list[DecisionRecord] = []
        self._audit: list[AuditEntry] = []
        self._versions: dict[str, VersionRecord] = {}
        self._active_version: str = ""

    # -- decision log --------------------------------------------------
    def log_decision(self, record: DecisionRecord) -> DecisionRecord:
        self._decisions.append(record)
        return record

    def decisions(self) -> list[DecisionRecord]:
        return list(self._decisions)

    # -- audit ---------------------------------------------------------
    def audit(self, entry: AuditEntry) -> AuditEntry:
        self._audit.append(entry)
        return entry

    def audit_trail(self, request_id: str) -> list[AuditEntry]:
        return [e for e in self._audit if e.request_id == request_id]

    # -- versions ------------------------------------------------------
    def register_version(self, record: VersionRecord, activate: bool = True):
        record.previous_version = record.previous_version or self._active_version
        self._versions[record.version] = record
        if activate:
            self._active_version = record.version
        return record

    def active_version(self) -> VersionRecord | None:
        return self._versions.get(self._active_version)

    def rollback(self) -> VersionRecord | None:
        """Revert to the previous version — §47 requires this path exists."""
        current = self._versions.get(self._active_version)
        if current is None or not current.previous_version:
            return None
        self._active_version = current.previous_version
        return self._versions.get(self._active_version)
