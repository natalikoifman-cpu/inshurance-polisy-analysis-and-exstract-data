"""Module 2 — Question & Use Case Design (§7) and Data-to-Answer mapping (§27).

Every use case gets a question tree: the client's phrasing is decomposed
into sub-questions, each mapped to intent, required entities, tools,
calculations, sources and refusal/clarification conditions.  The
Data-to-Answer map is the bridge the infrastructure team implements —
"a field named ``currency`` exists" is never enough (§27).
"""
from __future__ import annotations

from dataclasses import dataclass, field, asdict
from typing import Any

from .contracts import RiskLevel


@dataclass
class QuestionSpec:
    """§7 — one client question (or sub-question) fully specified."""
    text: str                                 # original phrasing
    use_case: str
    intent: str = ""
    alt_phrasings: list[str] = field(default_factory=list)
    required_entities: list[str] = field(default_factory=list)
    possible_missing_info: list[str] = field(default_factory=list)
    required_tool: str = ""
    required_calculation: str = ""
    expected_answer: str = ""
    sources: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    risk_level: RiskLevel = RiskLevel.MEDIUM
    clarification_conditions: list[str] = field(default_factory=list)
    refusal_conditions: list[str] = field(default_factory=list)
    sub_questions: list["QuestionSpec"] = field(default_factory=list)

    def flatten(self) -> list["QuestionSpec"]:
        out = [self]
        for sub in self.sub_questions:
            out.extend(sub.flatten())
        return out

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        d["risk_level"] = self.risk_level.value
        return d


@dataclass
class DataToAnswerMap:
    """§27 — question -> intent -> fields -> sources -> calculation ->
    permissions -> validation -> answer structure -> warnings."""
    question: str
    intent: str
    required_information: list[str] = field(default_factory=list)
    fields: list[str] = field(default_factory=list)           # entity.field
    sources: list[str] = field(default_factory=list)
    calculation_rules: list[str] = field(default_factory=list)
    permissions: list[str] = field(default_factory=list)
    validation_rules: list[str] = field(default_factory=list)
    answer_structure: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    def missing_links(self) -> list[str]:
        """A map with no fields, sources or permissions cannot be built."""
        problems = []
        if not self.fields:
            problems.append("no fields mapped")
        if not self.sources:
            problems.append("no source of truth mapped")
        if not self.calculation_rules:
            problems.append("no calculation rule defined")
        if not self.permissions:
            problems.append("no permission rule defined")
        if not self.validation_rules:
            problems.append("no validation rule defined")
        return problems

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


class QuestionBank:
    """Per-use-case registry of question trees and their answer maps."""

    def __init__(self) -> None:
        self._questions: list[QuestionSpec] = []
        self._maps: dict[str, DataToAnswerMap] = {}

    def add_question(self, spec: QuestionSpec) -> QuestionSpec:
        self._questions.append(spec)
        return spec

    def add_map(self, m: DataToAnswerMap) -> DataToAnswerMap:
        self._maps[m.question] = m
        return m

    def questions_for(self, use_case: str) -> list[QuestionSpec]:
        return [
            q
            for root in self._questions
            for q in root.flatten()
            if root.use_case == use_case
        ]

    def map_for(self, question: str) -> DataToAnswerMap | None:
        return self._maps.get(question)

    def unmapped_questions(self) -> list[str]:
        """Questions (incl. sub-questions) without a Data-to-Answer map."""
        return [
            q.text
            for root in self._questions
            for q in root.flatten()
            if q.text not in self._maps
        ]

    def incomplete_maps(self) -> dict[str, list[str]]:
        return {
            question: problems
            for question, m in self._maps.items()
            if (problems := m.missing_links())
        }
