"""Module 7 — Evaluation & Optimization Lab (§8, §9, §37, §39, §41, §46, §47).

Grades every interaction on the §37 rubric, enforces the §9.1 hard
constraints (any violation zeroes the interaction), combines soft
metrics through a per-use-case weighted objective (§8.2), tracks
transaction cost (§39), compares versions statistically (§41) and
gates version acceptance (§47).  Optimization here is black-box (§14):
change one component, re-run the dataset, compare, decide.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field, asdict
from typing import Any

#: §9.1 — violations that disqualify an answer outright.
HARD_CONSTRAINTS = (
    "cross_client_exposure", "unauthorized_source", "wrong_calculation",
    "fabricated_fact", "missing_correctness_date", "partial_shown_as_full",
    "forbidden_recommendation", "unvalidated_data", "unauthorized_action",
)

#: §8.2 — default objective weights; use cases override per risk profile.
DEFAULT_WEIGHTS = {
    "factual_accuracy": 0.30,
    "groundedness": 0.20,
    "calculation_accuracy": 0.15,
    "task_completion": 0.15,
    "clarity": 0.10,
    "cost_efficiency": 0.10,
}


@dataclass
class TransactionCost:
    """§39 — the full cost of one interaction, not just LLM tokens."""
    input_tokens: int = 0
    output_tokens: int = 0
    embedding_calls: int = 0
    search_calls: int = 0
    database_queries: int = 0
    external_api_calls: int = 0
    document_pages: int = 0
    retries: int = 0
    human_review_minutes: float = 0.0

    def total(self, rates: dict[str, float] | None = None) -> float:
        r = {
            "input_token": 0.000003, "output_token": 0.000015,
            "embedding_call": 0.0001, "search_call": 0.002,
            "database_query": 0.0005, "external_api_call": 0.01,
            "document_page": 0.01, "retry": 0.005,
            "human_review_minute": 1.0,
        }
        if rates:
            r.update(rates)
        return (
            self.input_tokens * r["input_token"]
            + self.output_tokens * r["output_token"]
            + self.embedding_calls * r["embedding_call"]
            + self.search_calls * r["search_call"]
            + self.database_queries * r["database_query"]
            + self.external_api_calls * r["external_api_call"]
            + self.document_pages * r["document_page"]
            + self.retries * r["retry"]
            + self.human_review_minutes * r["human_review_minute"]
        )


@dataclass
class InteractionGrade:
    """§37 — the grading sheet of one interaction (0-5 per dimension)."""
    interaction_id: str
    use_case: str
    scores: dict[str, float] = field(default_factory=dict)
    hard_violations: list[str] = field(default_factory=list)
    permission_check_passed: bool = True
    task_completed: bool = True
    response_seconds: float = 0.0
    cost: TransactionCost = field(default_factory=TransactionCost)

    def objective_score(self, weights: dict[str, float] | None = None) -> float:
        """§8.2 weighted total on a 0-5 scale; hard violations zero it."""
        if self.hard_violations or not self.permission_check_passed:
            return 0.0
        w = weights or DEFAULT_WEIGHTS
        total_weight = sum(w.values())
        if total_weight == 0:
            return 0.0
        weighted = sum(
            w[name] * self.scores.get(name, 0.0) for name in w
        )
        return weighted / total_weight

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        d["objective_score"] = self.objective_score()
        return d


@dataclass
class StatSummary:
    """§41 — never judge a version on a handful of examples."""
    n: int
    mean: float
    median: float
    std: float
    failure_rate: float
    ci95_low: float
    ci95_high: float

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def summarize(scores: list[float], failure_threshold: float = 2.0) -> StatSummary:
    if not scores:
        return StatSummary(0, 0.0, 0.0, 0.0, 1.0, 0.0, 0.0)
    n = len(scores)
    mean = sum(scores) / n
    ordered = sorted(scores)
    mid = n // 2
    median = ordered[mid] if n % 2 else (ordered[mid - 1] + ordered[mid]) / 2
    variance = sum((s - mean) ** 2 for s in scores) / n
    std = math.sqrt(variance)
    failures = sum(1 for s in scores if s < failure_threshold)
    margin = 1.96 * std / math.sqrt(n)
    return StatSummary(
        n=n, mean=mean, median=median, std=std,
        failure_rate=failures / n,
        ci95_low=mean - margin, ci95_high=mean + margin,
    )


@dataclass
class ClassificationStats:
    """§41 — for rare-event problems (blockages) accuracy is not enough."""
    true_positives: int
    false_positives: int
    true_negatives: int
    false_negatives: int

    @property
    def precision(self) -> float:
        d = self.true_positives + self.false_positives
        return self.true_positives / d if d else 0.0

    @property
    def recall(self) -> float:
        d = self.true_positives + self.false_negatives
        return self.true_positives / d if d else 0.0

    @property
    def f1(self) -> float:
        p, r = self.precision, self.recall
        return 2 * p * r / (p + r) if (p + r) else 0.0

    @property
    def false_positive_rate(self) -> float:
        d = self.false_positives + self.true_negatives
        return self.false_positives / d if d else 0.0

    def to_dict(self) -> dict[str, Any]:
        return {
            **asdict(self),
            "precision": self.precision, "recall": self.recall,
            "f1": self.f1, "false_positive_rate": self.false_positive_rate,
        }


@dataclass
class Experiment:
    """§46 — one documented optimization experiment (one component changed)."""
    experiment_id: str
    hypothesis: str
    changed_component: str
    prompt_version: str = ""
    model_version: str = ""
    schema_version: str = ""
    embedding_version: str = ""
    retrieval_settings: dict[str, Any] = field(default_factory=dict)
    dataset: str = ""
    result_before: StatSummary | None = None
    result_after: StatSummary | None = None
    cost_change: float = 0.0
    latency_change_seconds: float = 0.0
    new_failures: list[str] = field(default_factory=list)
    decision: str = "pending"     # pending | accepted | rejected

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        return d


@dataclass
class VersionCandidate:
    """Inputs the §47 acceptance gate needs about a candidate version."""
    version: str
    safety_regression: bool
    permission_regression: bool
    calculation_regression: bool
    hallucination_rate_before: float
    hallucination_rate_after: float
    evaluated_on_representative_dataset: bool
    regression_tested: bool
    cost_within_budget: bool
    latency_approved: bool
    stable_across_runs: bool
    rollback_available: bool
    documented: bool


class EvaluationLab:
    """Grades, experiments and the version acceptance gate."""

    def __init__(self) -> None:
        self._grades: list[InteractionGrade] = []
        self._experiments: dict[str, Experiment] = {}
        self._weights: dict[str, dict[str, float]] = {}   # per use case

    # -- grading -------------------------------------------------------
    def set_weights(self, use_case: str, weights: dict[str, float]) -> None:
        self._weights[use_case] = dict(weights)

    def record(self, grade: InteractionGrade) -> InteractionGrade:
        self._grades.append(grade)
        return grade

    def grades(self, use_case: str | None = None) -> list[InteractionGrade]:
        if use_case is None:
            return list(self._grades)
        return [g for g in self._grades if g.use_case == use_case]

    def objective_scores(self, use_case: str) -> list[float]:
        weights = self._weights.get(use_case)
        return [g.objective_score(weights) for g in self.grades(use_case)]

    def summary(self, use_case: str) -> StatSummary:
        return summarize(self.objective_scores(use_case))

    def cost_per_successful_task(self, use_case: str) -> float:
        """§39/§44 — total spend divided by completed, violation-free tasks."""
        grades = self.grades(use_case)
        total = sum(g.cost.total() for g in grades)
        successes = sum(
            1 for g in grades
            if g.task_completed and not g.hard_violations
            and g.permission_check_passed
        )
        return total / successes if successes else float("inf")

    # -- experiments (§46) ---------------------------------------------
    def log_experiment(self, experiment: Experiment) -> Experiment:
        self._experiments[experiment.experiment_id] = experiment
        return experiment

    def experiments(self) -> list[Experiment]:
        return list(self._experiments.values())

    # -- version acceptance (§47) --------------------------------------
    def acceptance_gate(
        self, candidate: VersionCandidate, hallucination_tolerance: float = 0.005
    ) -> tuple[bool, list[str]]:
        failures = []
        if candidate.safety_regression:
            failures.append("safety regressed")
        if candidate.permission_regression:
            failures.append("permission enforcement regressed")
        if candidate.calculation_regression:
            failures.append("calculation accuracy regressed")
        if (candidate.hallucination_rate_after
                > candidate.hallucination_rate_before + hallucination_tolerance):
            failures.append("hallucination rate increased materially")
        if not candidate.evaluated_on_representative_dataset:
            failures.append("not evaluated on a representative dataset")
        if not candidate.regression_tested:
            failures.append("no regression test run")
        if not candidate.cost_within_budget:
            failures.append("cost out of budget")
        if not candidate.latency_approved:
            failures.append("latency not approved")
        if not candidate.stable_across_runs:
            failures.append("result unstable across runs")
        if not candidate.rollback_available:
            failures.append("no rollback path")
        if not candidate.documented:
            failures.append("undocumented change")
        return (not failures, failures)
