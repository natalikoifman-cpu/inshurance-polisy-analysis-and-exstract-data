"""Shared contracts for the Financial Agent Blueprint & Data Readiness Studio.

The Studio is a *control center* for building a customer-facing financial
agent — not the agent itself.  Every module (discovery, questions, catalog,
readiness, dataset lab, architecture, evaluation, bottleneck intelligence,
governance) communicates through the serializable dataclasses defined here,
so every artifact the Studio produces is auditable and reproducible.

Spec anchors: §4 (design principles), §31 (tool contract), §35 (client
response contract).
"""
from __future__ import annotations

import enum
from dataclasses import dataclass, field, asdict
from typing import Any


class RiskLevel(enum.Enum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    CRITICAL = "critical"


class ReadinessLevel(enum.IntEnum):
    """§28 rating scale for a capability's data readiness."""
    UNUSABLE = 0            # 0-1: cannot be used
    INTERNAL_RESEARCH = 2   # internal research only
    PILOT_WITH_WARNINGS = 3
    CONTROLLED_CUSTOMER = 4
    PRODUCTION = 5


class UserType(enum.Enum):
    END_CLIENT = "end_client"
    PORTFOLIO_MANAGER = "portfolio_manager"
    FINANCIAL_PLANNER = "financial_planner"
    SERVICE_REP = "service_rep"
    ANALYST = "analyst"


class Decision(enum.Enum):
    """§34 possible outcomes of the pre-answer decision gate."""
    ANSWER = "answer"
    ANSWER_WITH_WARNING = "answer_with_warning"
    ASK_CLARIFICATION = "ask_clarification"
    REQUEST_HUMAN_VALIDATION = "request_human_validation"
    REFUSE_MISSING_DATA = "refuse_missing_data"
    REFUSE_PERMISSION = "refuse_permission"
    CREATE_DATA_QUALITY_TASK = "create_data_quality_task"
    CREATE_SERVICE_REQUEST = "create_service_request"


@dataclass
class Provenance:
    """§4.3 — every fact shown to a client must carry its origin."""
    source: str
    as_of: str                       # correctness date of the data
    ingested_at: str                 # when the platform received it
    coverage: float = 1.0            # share of the answer backed by data
    confidence: float = 1.0
    limitations: list[str] = field(default_factory=list)
    request_id: str = ""             # audit correlation id

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class ToolResponse:
    """§31 — the mandatory envelope every agent tool returns."""
    status: str                      # "success" | "partial" | "error"
    as_of: str
    source: str
    data: dict[str, Any] = field(default_factory=dict)
    coverage: float = 1.0
    confidence: float = 1.0
    warnings: list[str] = field(default_factory=list)
    missing_fields: list[str] = field(default_factory=list)
    calculation_method: str = ""
    request_id: str = ""

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class ClientAnswer:
    """§35 — the contract of every financial answer shown to a client."""
    direct_answer: str
    explanation: str
    as_of: str
    source: str
    coverage: float
    missing_data: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    recommended_action: str = ""     # only when policy allows one
    request_id: str = ""

    def is_complete(self) -> bool:
        """A client answer without date/source violates a hard constraint."""
        return bool(self.direct_answer and self.as_of and self.source)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class Gap:
    """§29 — one identified gap blocking or degrading a capability."""
    gap_type: str        # business | data | quality | infrastructure |
                         # calculation | permission | regulatory |
                         # measurement | dataset
    description: str
    impact: str
    affected_use_cases: list[str] = field(default_factory=list)
    severity: RiskLevel = RiskLevel.MEDIUM
    owner: str = ""
    required_fix: str = ""
    open_question: str = ""
    priority: int = 3            # 1 = highest
    closing_condition: str = ""
    status: str = "open"         # open | in_progress | closed

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        d["severity"] = self.severity.value
        return d


GAP_TYPES = (
    "business", "data", "quality", "infrastructure", "calculation",
    "permission", "regulatory", "measurement", "dataset",
)
