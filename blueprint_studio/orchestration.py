"""Module 11 — Skill Orchestration: big Lego bricks, small Lego bricks.

Two vendor approaches to agent skills, both implemented here so a
capability can choose per use case:

* **Composite skills** ("big Lego bricks") — a fixed list of large,
  pre-built skills, each proven over thousands of invocations.  The
  agent understands intent (asking clarifying questions until the
  intent is complete), matches it to exactly one skill, runs it and
  returns the result.  No match -> tell the user.  There is *no*
  scenario where the question is passed straight to the LLM and its
  free-form answer returned.

* **Atomic skills** ("small Lego bricks") — tasks are decomposed into
  small, single-purpose skills composed into pipelines (e.g. find-file
  = identify_file_type -> search_files -> verify_match).  Each atomic
  skill is easy to test in isolation; a connection-QA check validates
  that every step's output contract feeds the next step's input
  contract *before* execution; and relevance filtering keeps
  irrelevant skills (Excel skills for a Word task) out of the
  planner's context, saving tokens.

Shared ground of both approaches, enforced in code:

- The LLM is an **orchestrator only**: a pluggable planner may choose
  among registered skills and their order — it never executes the
  substantive work and can never inject an unregistered step.
- Skills are deterministic code components with declared input/output
  contracts and tracked reliability (invocations, success rate); a
  skill below the proven-threshold is not eligible for production
  routing.
- Unmatched intents are mined: recurring unmet needs become proposed
  new skills (the learning loop), never silent LLM fallbacks.
"""
from __future__ import annotations

import enum
from dataclasses import dataclass, field, asdict
from typing import Any, Callable


class Granularity(enum.Enum):
    COMPOSITE = "composite"      # big, pre-built, proven end-to-end skill
    ATOMIC = "atomic"            # small single-purpose building block


class ResolutionOutcome(enum.Enum):
    """Every request ends in exactly one of these — by design there is
    no 'pass through to raw LLM' outcome."""
    MATCHED = "matched"
    NEEDS_CLARIFICATION = "needs_clarification"
    NO_MATCHING_SKILL = "no_matching_skill"


@dataclass
class SkillContract:
    """Typed connection points — what composition QA validates."""
    inputs: dict[str, str] = field(default_factory=dict)    # name -> type
    outputs: dict[str, str] = field(default_factory=dict)


@dataclass
class Skill:
    """One deterministic code component the orchestrator may invoke."""
    name: str
    description: str
    granularity: Granularity
    domain: str                          # relevance tag, e.g. "portfolio"
    intents: list[str] = field(default_factory=list)   # composite: direct match
    contract: SkillContract = field(default_factory=SkillContract)
    handler: Callable[..., dict[str, Any]] | None = None
    deterministic: bool = True
    # Reliability record — "tested tens of thousands of times":
    invocations: int = 0
    successes: int = 0

    @property
    def success_rate(self) -> float:
        return self.successes / self.invocations if self.invocations else 0.0

    def is_proven(self, min_invocations: int, min_success_rate: float) -> bool:
        return (self.invocations >= min_invocations
                and self.success_rate >= min_success_rate)

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name, "description": self.description,
            "granularity": self.granularity.value, "domain": self.domain,
            "intents": self.intents,
            "inputs": self.contract.inputs, "outputs": self.contract.outputs,
            "invocations": self.invocations,
            "success_rate": round(self.success_rate, 4),
        }


class SkillRegistry:
    """The fixed skill list, with reliability tracking and relevance
    filtering (never show Excel skills to a Word task)."""

    def __init__(
        self, min_invocations: int = 100, min_success_rate: float = 0.95
    ) -> None:
        self.min_invocations = min_invocations
        self.min_success_rate = min_success_rate
        self._skills: dict[str, Skill] = {}

    def register(self, skill: Skill) -> Skill:
        if not skill.deterministic:
            raise ValueError(
                f"skill '{skill.name}' is not deterministic — the LLM "
                "orchestrates deterministic code components only"
            )
        if not skill.contract.outputs:
            raise ValueError(f"skill '{skill.name}' declares no output contract")
        self._skills[skill.name] = skill
        return skill

    def get(self, name: str) -> Skill | None:
        return self._skills.get(name)

    def all(self) -> list[Skill]:
        return [self._skills[k] for k in sorted(self._skills)]

    def relevant(self, domain: str) -> list[Skill]:
        """Token-efficiency: only same-domain skills reach the planner."""
        return [s for s in self.all() if s.domain == domain]

    def proven(self, domain: str | None = None) -> list[Skill]:
        pool = self.relevant(domain) if domain else self.all()
        return [
            s for s in pool
            if s.is_proven(self.min_invocations, self.min_success_rate)
        ]

    def record_outcome(self, name: str, success: bool) -> None:
        skill = self._skills[name]
        skill.invocations += 1
        if success:
            skill.successes += 1


@dataclass
class Intent:
    """A user intent with the slots a skill needs to run."""
    name: str
    required_slots: list[str] = field(default_factory=list)
    slots: dict[str, Any] = field(default_factory=dict)

    def missing_slots(self) -> list[str]:
        return [s for s in self.required_slots if s not in self.slots]


@dataclass
class Resolution:
    """The structured outcome of intent resolution — never raw LLM text."""
    outcome: ResolutionOutcome
    skill: str = ""
    clarifying_questions: list[str] = field(default_factory=list)
    message: str = ""

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        d["outcome"] = self.outcome.value
        return d


class IntentResolver:
    """Vendor-1 flow: clarify until the intent is complete, match it to a
    proven skill from the fixed list, or say honestly that none fits."""

    def __init__(self, registry: SkillRegistry, require_proven: bool = True):
        self.registry = registry
        self.require_proven = require_proven

    def resolve(self, intent: Intent, domain: str) -> Resolution:
        missing = intent.missing_slots()
        if missing:
            return Resolution(
                outcome=ResolutionOutcome.NEEDS_CLARIFICATION,
                clarifying_questions=[
                    f"Please provide: {slot}" for slot in missing
                ],
            )
        pool = (self.registry.proven(domain) if self.require_proven
                else self.registry.relevant(domain))
        for skill in pool:
            if intent.name in skill.intents:
                return Resolution(
                    outcome=ResolutionOutcome.MATCHED, skill=skill.name,
                )
        return Resolution(
            outcome=ResolutionOutcome.NO_MATCHING_SKILL,
            message=(
                f"no skill currently handles intent '{intent.name}' — "
                "the request was not answered by a raw language model"
            ),
        )


@dataclass
class ConnectionIssue:
    """One broken joint found by composition QA."""
    from_skill: str
    to_skill: str
    problem: str


class SkillPipeline:
    """Vendor-2 flow: atomic skills chained with connection QA.

    ``validate()`` proves every joint *before* execution: each input of
    step N+1 must be produced (name and type) by some earlier step or
    supplied in the initial payload.  ``execute()`` refuses to run an
    invalid pipeline and validates each step's actual output against
    its declared contract — a step returning less than it promised
    fails loudly instead of corrupting the next step.
    """

    def __init__(self, name: str, registry: SkillRegistry, steps: list[str]):
        self.name = name
        self.registry = registry
        self.steps = list(steps)

    def validate(self, initial_inputs: dict[str, str]) -> list[ConnectionIssue]:
        issues: list[ConnectionIssue] = []
        available: dict[str, str] = dict(initial_inputs)   # name -> type
        previous = "<input>"
        for step_name in self.steps:
            skill = self.registry.get(step_name)
            if skill is None:
                issues.append(ConnectionIssue(
                    previous, step_name, "skill not registered",
                ))
                continue
            if skill.granularity is not Granularity.ATOMIC:
                issues.append(ConnectionIssue(
                    previous, step_name,
                    "pipelines compose atomic skills only",
                ))
            for arg, arg_type in skill.contract.inputs.items():
                supplied = available.get(arg)
                if supplied is None:
                    issues.append(ConnectionIssue(
                        previous, step_name,
                        f"input '{arg}' is not produced by any earlier step",
                    ))
                elif supplied != arg_type:
                    issues.append(ConnectionIssue(
                        previous, step_name,
                        f"input '{arg}' type mismatch: needs {arg_type}, "
                        f"gets {supplied}",
                    ))
            available.update(skill.contract.outputs)
            previous = step_name
        return issues

    def execute(self, payload: dict[str, Any]) -> dict[str, Any]:
        issues = self.validate({k: type(v).__name__ for k, v in payload.items()})
        if issues:
            raise ValueError(
                f"pipeline '{self.name}' failed connection QA: "
                + "; ".join(f"{i.from_skill}->{i.to_skill}: {i.problem}"
                            for i in issues)
            )
        state = dict(payload)
        for step_name in self.steps:
            skill = self.registry.get(step_name)
            args = {k: state[k] for k in skill.contract.inputs}
            try:
                result = skill.handler(**args)
                missing = [k for k in skill.contract.outputs if k not in result]
                if missing:
                    raise ValueError(
                        f"step '{step_name}' broke its contract — "
                        f"missing outputs: {', '.join(missing)}"
                    )
            except Exception:
                self.registry.record_outcome(step_name, success=False)
                raise
            self.registry.record_outcome(step_name, success=True)
            state.update(result)
        return state


@dataclass
class PlanStep:
    skill: str


class Orchestrator:
    """The LLM as conductor only.

    ``planner`` (pluggable — an LLM in production, a deterministic
    function in tests) receives the *filtered* skill listing for the
    request's domain and returns an ordered list of skill names.  The
    orchestrator then:

    1. rejects any plan step naming an unregistered skill — the planner
       cannot invent executors;
    2. builds a pipeline and runs connection QA on the joints;
    3. executes deterministic handlers only.

    The planner never produces the answer content itself.
    """

    def __init__(
        self,
        registry: SkillRegistry,
        planner: Callable[[str, list[dict[str, Any]]], list[str]],
    ) -> None:
        self.registry = registry
        self.planner = planner

    def run(
        self, goal: str, domain: str, payload: dict[str, Any]
    ) -> dict[str, Any]:
        listing = [s.to_dict() for s in self.registry.relevant(domain)]
        proposed = self.planner(goal, listing)
        unknown = [name for name in proposed if self.registry.get(name) is None]
        if unknown:
            raise ValueError(
                "planner proposed unregistered skills: " + ", ".join(unknown)
            )
        pipeline = SkillPipeline(f"plan:{goal}", self.registry, proposed)
        return pipeline.execute(payload)


@dataclass
class SkillProposal:
    """A recurring unmet need, promoted into a candidate skill."""
    intent: str
    occurrences: int
    example_requests: list[str]
    status: str = "proposed"     # proposed | approved | rejected


class SkillGapMiner:
    """Learning loop: unmatched intents are recorded, and once a need
    recurs past the threshold it becomes a skill proposal for the
    backlog — instead of silently degrading to free-form LLM answers."""

    def __init__(self, proposal_threshold: int = 3) -> None:
        self.proposal_threshold = proposal_threshold
        self._unmatched: dict[str, list[str]] = {}
        self._proposals: dict[str, SkillProposal] = {}

    def record_unmatched(self, intent: str, request_text: str) -> None:
        requests = self._unmatched.setdefault(intent, [])
        requests.append(request_text)
        if (len(requests) >= self.proposal_threshold
                and intent not in self._proposals):
            self._proposals[intent] = SkillProposal(
                intent=intent, occurrences=len(requests),
                example_requests=requests[:5],
            )

    def proposals(self) -> list[SkillProposal]:
        return [self._proposals[k] for k in sorted(self._proposals)]

    def unmatched_counts(self) -> dict[str, int]:
        return {k: len(v) for k, v in sorted(self._unmatched.items())}


class OrchestrationModule:
    """Facade: registry + resolver + gap miner + orchestrator factory."""

    def __init__(
        self, min_invocations: int = 100, min_success_rate: float = 0.95,
        proposal_threshold: int = 3,
    ) -> None:
        self.registry = SkillRegistry(min_invocations, min_success_rate)
        self.resolver = IntentResolver(self.registry)
        self.gap_miner = SkillGapMiner(proposal_threshold)

    def handle_intent(
        self, intent: Intent, domain: str, request_text: str = ""
    ) -> Resolution:
        resolution = self.resolver.resolve(intent, domain)
        if resolution.outcome is ResolutionOutcome.NO_MATCHING_SKILL:
            self.gap_miner.record_unmatched(intent.name, request_text)
        return resolution

    def orchestrator(
        self, planner: Callable[[str, list[dict[str, Any]]], list[str]]
    ) -> Orchestrator:
        return Orchestrator(self.registry, planner)

    def stats(self) -> dict[str, Any]:
        skills = self.registry.all()
        return {
            "skills": len(skills),
            "composite": sum(
                1 for s in skills if s.granularity is Granularity.COMPOSITE
            ),
            "atomic": sum(
                1 for s in skills if s.granularity is Granularity.ATOMIC
            ),
            "proven": len(self.registry.proven()),
            "skill_proposals": len(self.gap_miner.proposals()),
        }
