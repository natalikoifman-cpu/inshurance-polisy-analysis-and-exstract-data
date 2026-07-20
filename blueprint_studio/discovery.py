"""Module 1 — Business Discovery (§6).

The Studio starts from the business need, never from the technology.
Its product is the Use Case Card: a full picture of why a capability
exists, who uses it, what it must and must not do, and how success is
measured.  A card that fails the Definition-of-Ready check (§48) cannot
enter development.
"""
from __future__ import annotations

from dataclasses import dataclass, field, asdict
from typing import Any

from .contracts import RiskLevel, UserType


#: §6.1 — the discovery interview every use case must answer before design.
DISCOVERY_QUESTIONS = (
    "Who is the user?",
    "Is the user an end client, portfolio manager, planner, service rep or analyst?",
    "What is the user trying to understand or do?",
    "How is the task performed today?",
    "How long does the current process take?",
    "What mistakes happen today?",
    "Which systems are involved?",
    "Is there a dependency on a human expert?",
    "What is the business damage of a wrong answer?",
    "Does the user need information, explanation, comparison, recommendation or action?",
    "Is real-time data required?",
    "Could the answer be considered regulated advice?",
    "What is the agent forbidden to do?",
)


@dataclass
class UseCaseCard:
    """§6.2 — the canonical record of one agent capability."""
    name: str
    problem: str
    user_type: UserType
    desired_outcome: str
    current_process: str = ""
    future_process: str = ""
    business_value: str = ""
    risk_level: RiskLevel = RiskLevel.MEDIUM
    user_questions: list[str] = field(default_factory=list)
    agent_actions: list[str] = field(default_factory=list)
    success_metrics: list[str] = field(default_factory=list)
    required_information: list[str] = field(default_factory=list)
    limitations: list[str] = field(default_factory=list)
    refusal_conditions: list[str] = field(default_factory=list)
    business_approver: str = ""
    tech_owner: str = ""
    # Filled by other modules as the lifecycle advances:
    readiness_level: int = 0
    status: str = "discovery"    # discovery | design | pilot | production

    def definition_of_ready(self) -> list[str]:
        """§48 — return the list of missing prerequisites (empty = ready)."""
        checks = {
            "user defined": bool(self.user_type),
            "problem defined": bool(self.problem),
            "real user questions collected": bool(self.user_questions),
            "desired outcome defined": bool(self.desired_outcome),
            "required information defined": bool(self.required_information),
            "success metrics defined": bool(self.success_metrics),
            "limitations defined": bool(self.limitations),
            "refusal conditions defined": bool(self.refusal_conditions),
            "business approver assigned": bool(self.business_approver),
            "tech owner assigned": bool(self.tech_owner),
        }
        return [name for name, ok in checks.items() if not ok]

    @property
    def ready_for_development(self) -> bool:
        return not self.definition_of_ready()

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        d["user_type"] = self.user_type.value
        d["risk_level"] = self.risk_level.value
        return d


#: §6.3 — seed catalog of typical financial-agent use cases.
EXAMPLE_USE_CASES = (
    "Explain why the portfolio went up or down",
    "Show exposure by currency, country, sector or asset type",
    "Explain a security",
    "Compare financial products",
    "Identify unrecognized securities",
    "Validate name / ISIN / ticker / product-type consistency",
    "Explain gross vs. net return",
    "Detect portfolio anomalies",
    "Detect missing data",
    "Open a service task",
    "Early detection of a process blockage",
)


class DiscoveryModule:
    """Registry of use case cards plus readiness screening."""

    def __init__(self) -> None:
        self._cards: dict[str, UseCaseCard] = {}

    def add(self, card: UseCaseCard) -> UseCaseCard:
        self._cards[card.name] = card
        return card

    def get(self, name: str) -> UseCaseCard:
        return self._cards[name]

    def all(self) -> list[UseCaseCard]:
        return list(self._cards.values())

    def ready(self) -> list[UseCaseCard]:
        return [c for c in self._cards.values() if c.ready_for_development]

    def blocked(self) -> dict[str, list[str]]:
        """Map of card name -> unmet Definition-of-Ready items."""
        return {
            c.name: missing
            for c in self._cards.values()
            if (missing := c.definition_of_ready())
        }
