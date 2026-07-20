"""Configuration for the control plane.

Every threshold that decides pass/fail lives here, JSON-loadable, so the
control system can be re-tuned per deployment without code changes.
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field, asdict
from pathlib import Path
from typing import Any


@dataclass
class GateThresholds:
    min_intent_score: float = 0.6          # intent & context sufficiency
    min_alignment_score: float = 0.5       # business KPI alignment
    min_readiness_score: float = 1.0       # LLM-READY checklist must be complete
    max_intake_rounds: int = 5             # parameter intake question loop cap


@dataclass
class ConsistencyThresholds:
    min_consistency_score: float = 0.75    # mean pairwise similarity across iterations
    max_numeric_cv: float = 0.10           # coefficient of variation for numeric outputs
    drift_alert_similarity: float = 0.60   # below this vs. baseline => drift alarm


@dataclass
class LatencyThresholds:
    p95_ms: float = 5_000.0
    p99_ms: float = 10_000.0


@dataclass
class CostThresholds:
    max_cost_per_task_usd: float = 0.50
    max_tokens_per_task: int = 200_000


@dataclass
class SecurityPolicy:
    blocked_actions: list[str] = field(default_factory=lambda: [
        "execute_trade", "transfer_funds", "wire_transfer", "sell_position",
        "buy_position", "change_beneficiary", "close_account",
    ])
    max_payload_chars: int = 100_000


@dataclass
class CompliancePolicy:
    max_single_asset_weight: float = 0.40      # concentration limit
    max_illiquid_weight: float = 0.15
    restricted_assets: list[str] = field(default_factory=lambda: [
        "crypto_leveraged", "otc_derivative_unlisted",
    ])
    require_disclaimer: bool = True
    disclaimer_markers: list[str] = field(default_factory=lambda: [
        "not financial advice", "informational purposes",
    ])


@dataclass
class SkillPolicy:
    """Governs the skill catalog: routing threshold, and the evidence bar a
    skill must clear before it may serve users (LLM-as-orchestrator-only is
    structural — there is no passthrough toggle to loosen)."""
    min_match_score: float = 0.5           # intent->skill routing threshold
    min_executions_for_proven: int = 50    # runs needed to earn "proven"
    min_success_rate: float = 0.98         # success bar for promotion
    demote_below_rate: float = 0.90        # proven skill degrades -> demoted
    allow_candidate_execution: bool = False


@dataclass
class VerdictWeights:
    """How much each pillar contributes to the meta reliability score."""
    gates: float = 0.20
    consistency: float = 0.20
    security: float = 0.20
    compliance: float = 0.20
    operations: float = 0.20  # latency + cost + diagnostics


@dataclass
class ControlConfig:
    gates: GateThresholds = field(default_factory=GateThresholds)
    consistency: ConsistencyThresholds = field(default_factory=ConsistencyThresholds)
    latency: LatencyThresholds = field(default_factory=LatencyThresholds)
    cost: CostThresholds = field(default_factory=CostThresholds)
    security: SecurityPolicy = field(default_factory=SecurityPolicy)
    compliance: CompliancePolicy = field(default_factory=CompliancePolicy)
    skills: SkillPolicy = field(default_factory=SkillPolicy)
    verdict_weights: VerdictWeights = field(default_factory=VerdictWeights)
    min_reliability_for_controlled: float = 0.75

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "ControlConfig":
        return cls(
            gates=GateThresholds(**data.get("gates", {})),
            consistency=ConsistencyThresholds(**data.get("consistency", {})),
            latency=LatencyThresholds(**data.get("latency", {})),
            cost=CostThresholds(**data.get("cost", {})),
            security=SecurityPolicy(**data.get("security", {})),
            compliance=CompliancePolicy(**data.get("compliance", {})),
            skills=SkillPolicy(**data.get("skills", {})),
            verdict_weights=VerdictWeights(**data.get("verdict_weights", {})),
            min_reliability_for_controlled=data.get(
                "min_reliability_for_controlled", 0.75
            ),
        )

    @classmethod
    def from_json(cls, path: str | Path) -> "ControlConfig":
        return cls.from_dict(json.loads(Path(path).read_text(encoding="utf-8")))
