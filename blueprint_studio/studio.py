"""The Studio — the control center tying all nine modules together (§3, §5).

One ``BlueprintStudio`` instance carries a capability from business
need to Go/No-Go: it aggregates the per-module state into the §43
dashboard, produces the §49 deliverables checklist per use case, and
renders the final verdict — is this capability ready for pilot or
production, and if not, exactly what is missing.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from .architecture import ToolRegistry
from .bottleneck import BlockagePredictor, BlockageRules, CaseLibrary
from .catalog import DataCatalog
from .contracts import ReadinessLevel
from .dataset_lab import DatasetLab
from .discovery import DiscoveryModule
from .evaluation_lab import EvaluationLab
from .governance import DELIVERABLES, GovernanceModule
from .memory import MemoryModule
from .orchestration import OrchestrationModule
from .questions import QuestionBank
from .readiness import ReadinessModule

#: §45 — the lifecycle stages, in order.
LIFECYCLE = (
    "discovery", "data_inventory", "data_readiness", "dataset",
    "semantic_and_api_layer", "agent_mvp", "evaluation", "optimization",
    "production",
)


@dataclass
class CapabilityReport:
    """The full picture the Studio keeps per capability (§3)."""
    use_case: str
    readiness_level: int
    deliverables: dict[str, bool]
    missing_deliverables: list[str]
    blockers: list[str]
    open_gaps: int
    golden_cases: int
    leakage_problems: list[str]
    evaluation_mean: float | None
    go: bool
    reasons: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "use_case": self.use_case,
            "readiness_level": self.readiness_level,
            "deliverables": self.deliverables,
            "missing_deliverables": self.missing_deliverables,
            "blockers": self.blockers,
            "open_gaps": self.open_gaps,
            "golden_cases": self.golden_cases,
            "leakage_problems": self.leakage_problems,
            "evaluation_mean": self.evaluation_mean,
            "go": self.go,
            "reasons": self.reasons,
        }


class BlueprintStudio:
    """Facade over the nine modules plus reporting."""

    def __init__(self) -> None:
        self.discovery = DiscoveryModule()
        self.questions = QuestionBank()
        self.catalog = DataCatalog()
        self.readiness = ReadinessModule(self.catalog)
        self.dataset = DatasetLab()
        self.tools = ToolRegistry()
        self.evaluation = EvaluationLab()
        self.blockage_rules = BlockageRules()
        self.blockage_predictor = BlockagePredictor()
        self.case_library = CaseLibrary()
        self.governance = GovernanceModule()
        self.memory = MemoryModule()
        self.orchestration = OrchestrationModule()

    # -- deliverables (§49) --------------------------------------------
    def deliverables_status(self, use_case: str) -> dict[str, bool]:
        card = self.discovery.get(use_case)
        questions = self.questions.questions_for(use_case)
        maps = [
            m for q in questions
            if (m := self.questions.map_for(q.text)) is not None
        ]
        assessment = self.readiness.assessment(use_case)
        golden = [
            c for c in self.dataset.golden_dataset()
            if c.context.get("use_case") == use_case
        ]
        tools = [t for t in self.tools.tools() if use_case in t.use_cases]
        grades = self.evaluation.grades(use_case)
        gaps = [
            g for g in self.readiness.gaps()
            if use_case in g.affected_use_cases
        ]
        dataset_examples = [
            e for e in self.dataset.examples() if e.use_case == use_case
        ]
        status = {
            "business_need_document": bool(card.problem and card.business_value),
            "use_case_card": card.ready_for_development,
            "question_bank": bool(questions),
            "data_to_answer_map": bool(maps) and not any(
                m.missing_links() for m in maps
            ),
            "field_list": any(m.fields for m in maps),
            "source_list": any(m.sources for m in maps),
            "gap_analysis": bool(gaps) or assessment is not None,
            "developer_questions": any(m.validation_rules for m in maps),
            "api_contracts": bool(tools),
            "semantic_model": bool(self.catalog.sources()),
            "calculation_rules": any(m.calculation_rules for m in maps),
            "permission_rules": any(m.permissions for m in maps),
            "agent_instructions": bool(card.agent_actions),
            "dataset_plan": bool(dataset_examples),
            "evaluation_plan": bool(golden),
            "optimization_plan": bool(self.evaluation.experiments()),
            "cost_model": any(g.cost.total() > 0 for g in grades),
            "test_cases": bool(golden),
            "readiness_score": assessment is not None,
            "go_no_go_decision": False,   # produced by capability_report itself
        }
        assert set(status) == set(DELIVERABLES)
        return status

    # -- capability report + Go/No-Go ----------------------------------
    def capability_report(
        self, use_case: str, minimum_level: ReadinessLevel = ReadinessLevel.PILOT_WITH_WARNINGS
    ) -> CapabilityReport:
        assessment = self.readiness.assessment(use_case)
        level = int(assessment.level) if assessment else 0
        blockers = list(assessment.blockers) if assessment else [
            "no readiness assessment"
        ]
        deliverables = self.deliverables_status(use_case)
        missing = [
            name for name, done in deliverables.items()
            if not done and name != "go_no_go_decision"
        ]
        leakage = self.dataset.leakage_report()
        open_gaps = len([
            g for g in self.readiness.gaps("open")
            if use_case in g.affected_use_cases
        ])
        golden = [
            c for c in self.dataset.golden_dataset()
            if c.context.get("use_case") == use_case
        ]
        scores = self.evaluation.objective_scores(use_case)
        mean = sum(scores) / len(scores) if scores else None

        reasons = []
        if blockers:
            reasons.append("readiness blockers: " + "; ".join(blockers))
        if level < int(minimum_level):
            reasons.append(
                f"readiness level {level} below required {int(minimum_level)}"
            )
        if missing:
            reasons.append("missing deliverables: " + ", ".join(missing))
        if leakage:
            reasons.append("dataset leakage problems: " + "; ".join(leakage))
        if open_gaps:
            reasons.append(f"{open_gaps} open gap(s)")
        if not golden:
            reasons.append("no golden test cases")
        if mean is None:
            reasons.append("no evaluation runs")

        go = not reasons
        deliverables["go_no_go_decision"] = True
        return CapabilityReport(
            use_case=use_case,
            readiness_level=level,
            deliverables=deliverables,
            missing_deliverables=missing,
            blockers=blockers,
            open_gaps=open_gaps,
            golden_cases=len(golden),
            leakage_problems=leakage,
            evaluation_mean=mean,
            go=go,
            reasons=reasons,
        )

    # -- dashboard (§43) -----------------------------------------------
    def dashboard(self) -> dict[str, Any]:
        cards = self.discovery.all()
        reports = {c.name: self.capability_report(c.name) for c in cards}
        return {
            "use_cases": len(cards),
            "ready_for_development": len(self.discovery.ready()),
            "production_ready": sum(1 for r in reports.values() if r.go),
            "open_gaps": len(self.readiness.gaps("open")),
            "sources": len(self.catalog.sources()),
            "lineage_coverage": round(self.catalog.lineage_coverage(), 3),
            "dataset_examples": len(self.dataset.examples()),
            "golden_cases": len(self.dataset.golden_dataset()),
            "experiments": len(self.evaluation.experiments()),
            "decisions_logged": len(self.governance.decisions()),
            "memory": self.memory.stats(),
            "orchestration": self.orchestration.stats(),
            "capabilities": {
                name: report.to_dict() for name, report in sorted(reports.items())
            },
        }
