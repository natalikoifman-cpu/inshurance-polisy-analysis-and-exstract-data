"""Module 4 — Data Readiness & Gap Analysis (§28, §29).

Every capability is scored on seven dimensions and mapped to the 0-5
readiness scale.  Mandatory blockers (§28) override the score: a
capability with no source of truth, no permission model or a licensing
violation is blocked no matter how good the rest looks.
"""
from __future__ import annotations

from dataclasses import dataclass, field, asdict
from typing import Any

from .catalog import DataCatalog
from .contracts import Gap, ReadinessLevel

#: §28 — the seven readiness dimensions, each scored 0..5.
READINESS_DIMENSIONS = (
    "availability", "completeness", "accuracy", "freshness",
    "consistency", "traceability", "permission_and_licensing",
)


@dataclass
class ReadinessAssessment:
    """Scorecard of one capability across the seven dimensions."""
    use_case: str
    scores: dict[str, float] = field(default_factory=dict)  # dimension -> 0..5
    blockers: list[str] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)

    @property
    def overall(self) -> float:
        """Weakest-link score: readiness is capped by the worst dimension."""
        if not self.scores:
            return 0.0
        return min(self.scores.get(d, 0.0) for d in READINESS_DIMENSIONS)

    @property
    def level(self) -> ReadinessLevel:
        if self.blockers:
            return ReadinessLevel.UNUSABLE
        score = self.overall
        if score >= 5:
            return ReadinessLevel.PRODUCTION
        if score >= 4:
            return ReadinessLevel.CONTROLLED_CUSTOMER
        if score >= 3:
            return ReadinessLevel.PILOT_WITH_WARNINGS
        if score >= 2:
            return ReadinessLevel.INTERNAL_RESEARCH
        return ReadinessLevel.UNUSABLE

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        d["overall"] = self.overall
        d["level"] = int(self.level)
        return d


class ReadinessModule:
    """Assesses capabilities against the catalog and tracks gaps."""

    def __init__(self, catalog: DataCatalog) -> None:
        self.catalog = catalog
        self._assessments: dict[str, ReadinessAssessment] = {}
        self._gaps: list[Gap] = []

    # -- assessment ----------------------------------------------------
    def assess(
        self,
        use_case: str,
        required_fields: list[str],
        required_sources: list[str],
        scores: dict[str, float],
    ) -> ReadinessAssessment:
        """Combine analyst scores with hard checks against the catalog.

        ``scores`` carries the analyst's 0-5 rating per dimension; the
        catalog checks add the §28 mandatory blockers automatically.
        """
        assessment = ReadinessAssessment(use_case=use_case, scores=dict(scores))

        missing = self.catalog.missing_fields(required_fields)
        if missing:
            assessment.blockers.append(
                f"no source of truth for fields: {', '.join(missing)}"
            )
        unlicensed = self.catalog.unlicensed_sources(required_sources)
        if unlicensed:
            assessment.blockers.append(
                f"licensing violation — not client-displayable: {', '.join(unlicensed)}"
            )
        untraceable = self.catalog.untraceable_fields(
            [f for f in required_fields if f not in missing]
        )
        if untraceable:
            assessment.blockers.append(
                f"no audit-grade lineage for: {', '.join(untraceable)}"
            )
        for dimension in READINESS_DIMENSIONS:
            if dimension not in assessment.scores:
                assessment.blockers.append(f"dimension not assessed: {dimension}")

        self._assessments[use_case] = assessment
        return assessment

    def assessment(self, use_case: str) -> ReadinessAssessment | None:
        return self._assessments.get(use_case)

    # -- gap analysis --------------------------------------------------
    def add_gap(self, gap: Gap) -> Gap:
        self._gaps.append(gap)
        return gap

    def gaps(self, status: str | None = None) -> list[Gap]:
        if status is None:
            return list(self._gaps)
        return [g for g in self._gaps if g.status == status]

    def gap_matrix(self) -> list[dict[str, Any]]:
        """§43 Gap Matrix screen — sorted by priority then severity."""
        severity_rank = {"critical": 0, "high": 1, "medium": 2, "low": 3}
        rows = sorted(
            self._gaps,
            key=lambda g: (g.priority, severity_rank.get(g.severity.value, 9)),
        )
        return [g.to_dict() for g in rows]

    def gaps_from_assessment(self, assessment: ReadinessAssessment) -> list[Gap]:
        """Auto-open a data gap for every mandatory blocker found."""
        opened = []
        for blocker in assessment.blockers:
            gap = Gap(
                gap_type="data",
                description=blocker,
                impact=f"blocks capability '{assessment.use_case}'",
                affected_use_cases=[assessment.use_case],
                priority=1,
                closing_condition="blocker no longer reported by readiness assessment",
            )
            self._gaps.append(gap)
            opened.append(gap)
        return opened
