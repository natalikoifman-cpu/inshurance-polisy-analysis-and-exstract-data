"""Module 8 — Bottleneck & Blockage Intelligence (§19-§22, §24).

Three cooperating layers (§24): a Rules Engine for deterministic
blockers, a statistical predictor for probabilistic risk, and (outside
this module) an LLM only for phrasing and explanation.  The predictor
is a transparent frequency model learned from the labeled dataset —
deterministic, explainable, reproducible.  Similarity search combines
structured features and process paths so the system generalizes by
*root cause*, not by product type (§22).
"""
from __future__ import annotations

from dataclasses import dataclass, field, asdict
from typing import Any

#: §20 — the blockage taxonomy.
BLOCKAGE_TAXONOMY = {
    "data": (
        "missing_data", "invalid_data", "conflicting_data",
        "stale_data", "unidentified_instrument",
    ),
    "process": (
        "missing_approval", "manual_review", "waiting_for_external_party",
        "incorrect_workflow_state",
    ),
    "technical": (
        "api_failure", "timeout", "authentication_error",
        "schema_error", "system_unavailable",
    ),
    "business": (
        "undefined_rule", "unsupported_product",
        "regulatory_restriction", "ownership_unclear",
    ),
}


@dataclass
class BlockageType:
    """§20 — the full definition of one blockage kind."""
    name: str
    category: str                # data | process | technical | business
    definition: str
    signals: list[str] = field(default_factory=list)
    stage: str = ""
    severity: str = "medium"
    owner: str = ""
    typical_resolution_hours: float = 0.0
    impact: str = ""
    resolution: str = ""
    preventive_action: str = ""
    agent_may_handle: bool = False

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class CaseFeatures:
    """§21 — structured features of one transaction/process case."""
    case_id: str
    transaction_type: str = ""
    instrument_type: str = ""
    currency: str = ""
    amount: float = 0.0
    systems_involved: int = 1
    sources_involved: int = 1
    missing_field_ratio: float = 0.0
    identification_confidence: float = 1.0
    hours_since_update: float = 0.0
    attempts: int = 1
    data_provider: str = ""
    process_stage: str = ""
    prior_failures: int = 0
    manual_actions: int = 0
    needs_approval: bool = False
    product_complexity: str = "simple"    # simple | medium | complex
    process_path: list[str] = field(default_factory=list)
    error_text: str = ""
    # Label (known only for historical cases):
    blockage_type: str = ""               # "" = no blockage occurred
    delay_hours: float = 0.0

    @property
    def blocked(self) -> bool:
        return bool(self.blockage_type)


@dataclass
class BlockagePrediction:
    """§21 — the predictor's output for one open case."""
    case_id: str
    blockage_probability: float
    predicted_blockage_type: str
    predicted_stage: str
    estimated_delay_hours: float
    recommended_action: str
    similar_cases: int
    rule_findings: list[str] = field(default_factory=list)
    explanation: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


class BlockageRules:
    """§24 Rules Engine — deterministic blockers, no probability involved."""

    def __init__(self, confidence_floor: float = 0.7, stale_hours: float = 72.0):
        self.confidence_floor = confidence_floor
        self.stale_hours = stale_hours

    def evaluate(self, case: CaseFeatures) -> list[str]:
        findings = []
        if case.missing_field_ratio >= 0.5:
            findings.append("mandatory fields mostly missing")
        if case.identification_confidence < self.confidence_floor:
            findings.append(
                f"identification confidence {case.identification_confidence:.0%} "
                f"below floor {self.confidence_floor:.0%}"
            )
        if case.hours_since_update > self.stale_hours:
            findings.append(
                f"data stale: {case.hours_since_update:.0f}h since update"
            )
        if case.needs_approval:
            findings.append("approval required before proceeding")
        return findings


def _bucket(value: float, edges: list[float]) -> int:
    for i, edge in enumerate(edges):
        if value < edge:
            return i
    return len(edges)


def _feature_keys(case: CaseFeatures) -> list[str]:
    """Discretized features the frequency model learns over."""
    return [
        f"txn={case.transaction_type}",
        f"instrument={case.instrument_type}",
        f"provider={case.data_provider}",
        f"stage={case.process_stage}",
        f"complexity={case.product_complexity}",
        f"missing_bucket={_bucket(case.missing_field_ratio, [0.1, 0.3, 0.5])}",
        f"confidence_bucket={_bucket(case.identification_confidence, [0.5, 0.7, 0.9])}",
        f"prior_failures={min(case.prior_failures, 3)}",
        f"manual_actions={min(case.manual_actions, 3)}",
        f"needs_approval={case.needs_approval}",
    ]


class BlockagePredictor:
    """§21 — transparent per-feature frequency model (naive-Bayes style).

    Fit on labeled historical cases; predicts probability, most likely
    type/stage, expected delay — with a per-feature explanation so an
    expert can audit every prediction.
    """

    def __init__(self) -> None:
        self._n_blocked = 0
        self._n_clear = 0
        self._blocked_counts: dict[str, int] = {}
        self._clear_counts: dict[str, int] = {}
        self._type_by_feature: dict[str, dict[str, int]] = {}
        self._stage_by_type: dict[str, dict[str, int]] = {}
        self._delay_by_type: dict[str, list[float]] = {}

    def fit(self, cases: list[CaseFeatures]) -> None:
        for case in cases:
            keys = _feature_keys(case)
            if case.blocked:
                self._n_blocked += 1
                for k in keys:
                    self._blocked_counts[k] = self._blocked_counts.get(k, 0) + 1
                    by_type = self._type_by_feature.setdefault(k, {})
                    by_type[case.blockage_type] = by_type.get(case.blockage_type, 0) + 1
                stages = self._stage_by_type.setdefault(case.blockage_type, {})
                stages[case.process_stage] = stages.get(case.process_stage, 0) + 1
                self._delay_by_type.setdefault(case.blockage_type, []).append(
                    case.delay_hours
                )
            else:
                self._n_clear += 1
                for k in keys:
                    self._clear_counts[k] = self._clear_counts.get(k, 0) + 1

    def predict(
        self, case: CaseFeatures, rules: BlockageRules | None = None
    ) -> BlockagePrediction:
        keys = _feature_keys(case)
        # Laplace-smoothed odds accumulated across features.
        log_odds = 0.0
        explanation = []
        import math
        prior_blocked = (self._n_blocked + 1) / (self._n_blocked + self._n_clear + 2)
        log_odds += math.log(prior_blocked / (1 - prior_blocked))
        for k in keys:
            p_b = (self._blocked_counts.get(k, 0) + 1) / (self._n_blocked + 2)
            p_c = (self._clear_counts.get(k, 0) + 1) / (self._n_clear + 2)
            contribution = math.log(p_b / p_c)
            log_odds += contribution
            if abs(contribution) >= 0.4:
                direction = "raises" if contribution > 0 else "lowers"
                explanation.append(f"{k} {direction} risk")
        probability = 1 / (1 + math.exp(-log_odds))

        # Most likely type: vote across the case's features.
        type_votes: dict[str, int] = {}
        for k in keys:
            for btype, count in self._type_by_feature.get(k, {}).items():
                type_votes[btype] = type_votes.get(btype, 0) + count
        predicted_type = (
            max(sorted(type_votes), key=lambda t: type_votes[t])
            if type_votes else "unknown"
        )
        stages = self._stage_by_type.get(predicted_type, {})
        predicted_stage = (
            max(sorted(stages), key=lambda s: stages[s]) if stages else ""
        )
        delays = self._delay_by_type.get(predicted_type, [])
        estimated_delay = sum(delays) / len(delays) if delays else 0.0

        rule_findings = rules.evaluate(case) if rules else []
        if rule_findings:
            probability = max(probability, 0.8)   # deterministic blockers dominate
            action = "request_manual_validation"
        elif probability >= 0.6:
            action = "request_manual_validation"
        elif probability >= 0.3:
            action = "monitor_closely"
        else:
            action = "proceed"

        similar = sum(self._blocked_counts.get(k, 0) for k in keys)
        return BlockagePrediction(
            case_id=case.case_id,
            blockage_probability=round(probability, 3),
            predicted_blockage_type=predicted_type,
            predicted_stage=predicted_stage,
            estimated_delay_hours=round(estimated_delay, 1),
            recommended_action=action,
            similar_cases=similar,
            rule_findings=rule_findings,
            explanation=explanation,
        )


def structured_similarity(a: CaseFeatures, b: CaseFeatures) -> float:
    """§22 — feature overlap between two cases, 0..1."""
    keys_a, keys_b = set(_feature_keys(a)), set(_feature_keys(b))
    union = keys_a | keys_b
    return len(keys_a & keys_b) / len(union) if union else 0.0


def process_similarity(a: CaseFeatures, b: CaseFeatures) -> float:
    """§22 — longest-common-prefix similarity of the process paths."""
    path_a, path_b = a.process_path, b.process_path
    if not path_a or not path_b:
        return 0.0
    common = 0
    for step_a, step_b in zip(path_a, path_b):
        if step_a != step_b:
            break
        common += 1
    return common / max(len(path_a), len(path_b))


class CaseLibrary:
    """Find the historical cases most similar to a new one (§22)."""

    def __init__(self) -> None:
        self._cases: list[CaseFeatures] = []

    def add(self, case: CaseFeatures) -> CaseFeatures:
        self._cases.append(case)
        return case

    def most_similar(
        self, case: CaseFeatures, limit: int = 5,
        structured_weight: float = 0.6,
    ) -> list[tuple[CaseFeatures, float]]:
        scored = [
            (
                other,
                structured_weight * structured_similarity(case, other)
                + (1 - structured_weight) * process_similarity(case, other),
            )
            for other in self._cases
            if other.case_id != case.case_id
        ]
        scored.sort(key=lambda pair: (-pair[1], pair[0].case_id))
        return scored[:limit]
