"""Module 5 — Dataset & Expert Knowledge Lab (§15-18, §38, §42).

Even when no model is trained, examples are split into Training /
Validation / Test (§16).  Splitting is *group-based* (same conversation,
client, portfolio, deal or instrument never crosses sets) and optionally
*time-based* (test = the newest period), which together prevent the
§17.1 leakage failure modes.  Expert labels (§18) and the Golden
Dataset (§38) live here, and the Active-Learning queue (§42) picks the
cases most worth an expert's time.
"""
from __future__ import annotations

import hashlib
from dataclasses import dataclass, field, asdict
from typing import Any

SPLITS = ("training", "validation", "test")


@dataclass
class ExpertLabel:
    """§18 step 3 — what an expert records on one case."""
    problem_type: str
    process_stage: str = ""
    early_signals: list[str] = field(default_factory=list)
    root_cause: str = ""
    severity: str = "medium"
    resolution: str = ""
    missing_information: list[str] = field(default_factory=list)
    preventive_action: str = ""
    automatable: bool | None = None
    approved_by: str = ""


@dataclass
class DatasetExample:
    """One case in the lab — real, variation or controlled-synthetic."""
    example_id: str
    text: str                        # question / failure description
    use_case: str
    group_key: str                   # conversation/client/deal id for splitting
    period: str = ""                 # e.g. "2026-05" for time-based split
    origin: str = "real"             # real | variation | synthetic
    parent_id: str = ""              # variations/synthetics point at the seed
    expert_label: ExpertLabel | None = None
    model_confidence: float | None = None   # feeds active learning
    business_impact: str = "medium"
    context: dict[str, Any] = field(default_factory=dict)

    @property
    def approved(self) -> bool:
        """§18 step 5 — synthetic data is unusable until expert-approved."""
        if self.origin == "real":
            return True
        return bool(self.expert_label and self.expert_label.approved_by)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class GoldenCase:
    """§38 — one fully specified evaluation case."""
    question: str
    context: dict[str, Any]
    user_type: str
    permissions: list[str]
    source_data: dict[str, Any]
    correct_answer: str
    expected_tool: str = ""
    expected_calculation: str = ""
    expected_source: str = ""
    expected_warning: str = ""
    forbidden_answers: list[str] = field(default_factory=list)
    minimum_score: float = 4.0

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def _stable_bucket(group_key: str) -> float:
    """Deterministic 0..1 hash of the split group — reproducible runs."""
    digest = hashlib.sha256(group_key.encode("utf-8")).hexdigest()
    return int(digest[:8], 16) / 0xFFFFFFFF


class DatasetLab:
    """Splitting, leakage checks, bootstrapping and active learning."""

    def __init__(self) -> None:
        self._examples: dict[str, DatasetExample] = {}
        self._assignments: dict[str, str] = {}       # example_id -> split
        self._golden: list[GoldenCase] = []

    # -- collection ----------------------------------------------------
    def add(self, example: DatasetExample) -> DatasetExample:
        self._examples[example.example_id] = example
        return example

    def add_golden(self, case: GoldenCase) -> GoldenCase:
        self._golden.append(case)
        return case

    def golden_dataset(self) -> list[GoldenCase]:
        return list(self._golden)

    def examples(self, split: str | None = None) -> list[DatasetExample]:
        if split is None:
            return list(self._examples.values())
        return [
            e for eid, e in self._examples.items()
            if self._assignments.get(eid) == split
        ]

    # -- splitting -----------------------------------------------------
    def split_by_group(
        self, train_share: float = 0.7, validation_share: float = 0.15
    ) -> dict[str, str]:
        """Hash-based group split: a group lands whole in exactly one set,
        and every variation/synthetic follows its seed example's group."""
        for example in self._examples.values():
            root = self._root_of(example)
            bucket = _stable_bucket(root.group_key)
            if bucket < train_share:
                split = "training"
            elif bucket < train_share + validation_share:
                split = "validation"
            else:
                split = "test"
            self._assignments[example.example_id] = split
        return dict(self._assignments)

    def split_by_time(self, validation_from: str, test_from: str) -> dict[str, str]:
        """§17.1 time-based split: history trains, the newest period tests.

        Group integrity still wins: if any example of a group falls in the
        test period, the whole group goes to test (else validation).
        """
        group_split: dict[str, str] = {}
        for example in self._examples.values():
            root = self._root_of(example)
            if example.period >= test_from:
                candidate = "test"
            elif example.period >= validation_from:
                candidate = "validation"
            else:
                candidate = "training"
            rank = {"training": 0, "validation": 1, "test": 2}
            current = group_split.get(root.group_key, "training")
            if rank[candidate] > rank[current]:
                group_split[root.group_key] = candidate
            else:
                group_split.setdefault(root.group_key, candidate)
        for example in self._examples.values():
            root = self._root_of(example)
            self._assignments[example.example_id] = group_split[root.group_key]
        return dict(self._assignments)

    def _root_of(self, example: DatasetExample) -> DatasetExample:
        seen = {example.example_id}
        while example.parent_id and example.parent_id in self._examples:
            example = self._examples[example.parent_id]
            if example.example_id in seen:      # defensive: cycle guard
                break
            seen.add(example.example_id)
        return example

    # -- leakage checks (§17.1) ----------------------------------------
    def leakage_report(self) -> list[str]:
        problems: list[str] = []
        group_splits: dict[str, set[str]] = {}
        for eid, split in self._assignments.items():
            root = self._root_of(self._examples[eid])
            group_splits.setdefault(root.group_key, set()).add(split)
        for group, splits in sorted(group_splits.items()):
            if len(splits) > 1:
                problems.append(
                    f"group '{group}' crosses splits: {sorted(splits)}"
                )
        for example in self._examples.values():
            if example.example_id not in self._assignments:
                problems.append(f"example '{example.example_id}' is unassigned")
            if example.origin != "real" and not example.approved:
                problems.append(
                    f"synthetic/variation '{example.example_id}' lacks expert approval"
                )
        return problems

    # -- bootstrapping (§18) -------------------------------------------
    def add_variation(
        self, parent_id: str, example_id: str, text: str, **overrides: Any
    ) -> DatasetExample:
        parent = self._examples[parent_id]
        variation = DatasetExample(
            example_id=example_id,
            text=text,
            use_case=overrides.pop("use_case", parent.use_case),
            group_key=parent.group_key,     # inherits split group by design
            period=overrides.pop("period", parent.period),
            origin=overrides.pop("origin", "variation"),
            parent_id=parent_id,
            **overrides,
        )
        return self.add(variation)

    # -- active learning (§42) -----------------------------------------
    def active_learning_queue(self, limit: int = 10) -> list[DatasetExample]:
        """Cases most worth labeling: unlabeled, ranked by low confidence
        and high business impact."""
        impact_rank = {"high": 0, "medium": 1, "low": 2}
        unlabeled = [
            e for e in self._examples.values() if e.expert_label is None
        ]
        unlabeled.sort(
            key=lambda e: (
                e.model_confidence if e.model_confidence is not None else 0.0,
                impact_rank.get(e.business_impact, 1),
                e.example_id,
            )
        )
        return unlabeled[:limit]
