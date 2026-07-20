"""Skill orchestration layer — the LLM is a conductor, never a performer.

Implements both granularity philosophies and the shared conclusion of the
big-LEGO vs. small-LEGO debate:

**Proven composite skills ("big LEGO bricks")**
- ``SkillRegistry``: a fixed catalog of skills. User intent is matched to a
  registered skill; if nothing matches, the user is told so explicitly.
  There is NO passthrough path where a question is handed raw to an LLM —
  ``route()`` can only return an executable registered skill or ``no_skill``.
- ``ReliabilityLedger``: a skill earns "proven" status only through recorded
  executions (N runs at a minimum success rate). Candidates are not routable
  in production until proven; a proven skill that degrades is demoted.

**Atomic skill composition ("small LEGO bricks")**
- ``SkillComposer``: complex tasks are expressed as a plan of small,
  deterministic steps. The composer enforces LLM-as-orchestrator-only: every
  plan step must reference a registered skill (a free-form "ask the model"
  step is rejected as CRITICAL), and every junction between steps is
  QA-checked — statically (output→input contract compatibility) and again at
  runtime (actual payloads validated against contracts before crossing).

**Learning new skills from interactions**
- ``SkillMiner``: unmatched intents are clustered deterministically by
  shared keywords; recurring clusters become candidate-skill proposals that
  enter the registry as candidates and must prove themselves through the
  ledger before serving users.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, Callable

from .config import SkillPolicy
from .contracts import Finding, Severity
from .llm_ready import _normalize

# ---------------------------------------------------------------------------
# Contracts: every skill declares typed inputs and outputs
# ---------------------------------------------------------------------------

_KINDS = {"string": str, "number": (int, float), "boolean": bool,
          "list": list, "dict": dict}


@dataclass
class FieldSpec:
    name: str
    kind: str = "string"           # string | number | boolean | list | dict
    required: bool = True


@dataclass
class SkillContract:
    inputs: list[FieldSpec] = field(default_factory=list)
    outputs: list[FieldSpec] = field(default_factory=list)

    def validate(self, payload: dict[str, Any],
                 direction: str) -> list[str]:
        """Return error strings for missing/mistyped fields."""
        specs = self.inputs if direction == "input" else self.outputs
        errors = []
        for spec in specs:
            if spec.name not in payload:
                if spec.required:
                    errors.append(f"{direction} field '{spec.name}' missing")
                continue
            expected = _KINDS.get(spec.kind)
            if expected and not isinstance(payload[spec.name], expected):
                errors.append(
                    f"{direction} field '{spec.name}' expected {spec.kind}, "
                    f"got {type(payload[spec.name]).__name__}")
        return errors


@dataclass
class SkillStats:
    executions: int = 0
    successes: int = 0

    @property
    def success_rate(self) -> float:
        return self.successes / self.executions if self.executions else 0.0


@dataclass
class Skill:
    """A deterministic, testable unit of work. ``fn`` is plain code — the
    substantive action is never performed by a model."""
    name: str
    description: str
    fn: Callable[[dict[str, Any]], dict[str, Any]]
    contract: SkillContract
    intent_keywords: list[str] = field(default_factory=list)
    granularity: str = "atomic"        # atomic | composite
    status: str = "candidate"          # candidate | proven | deprecated
    clarifying_questions: list[str] = field(default_factory=list)
    stats: SkillStats = field(default_factory=SkillStats)


# ---------------------------------------------------------------------------
# Registry + routing (no-LLM-passthrough by construction)
# ---------------------------------------------------------------------------


@dataclass
class RouteDecision:
    action: str                        # "execute" | "clarify" | "no_skill"
    skill: Skill | None
    score: float
    message: str
    questions: list[str] = field(default_factory=list)


class SkillRegistry:
    def __init__(self, policy: SkillPolicy) -> None:
        self.policy = policy
        self._skills: dict[str, Skill] = {}

    def register(self, skill: Skill) -> None:
        if skill.name in self._skills:
            raise ValueError(f"skill '{skill.name}' already registered")
        self._skills[skill.name] = skill

    def get(self, name: str) -> Skill | None:
        return self._skills.get(name)

    def all(self) -> list[Skill]:
        return list(self._skills.values())

    def match(self, text: str) -> tuple[Skill | None, float]:
        """Deterministic keyword matching over normalized text."""
        normalized = _normalize(text)
        best, best_score = None, 0.0
        for skill in self._skills.values():
            if skill.status == "deprecated" or not skill.intent_keywords:
                continue
            hits = sum(1 for kw in skill.intent_keywords
                       if _normalize(kw) in normalized)
            score = hits / len(skill.intent_keywords)
            if score > best_score:
                best, best_score = skill, score
        return best, best_score

    def route(self, text: str,
              clarified: bool = False) -> RouteDecision:
        """Match intent to a registered skill — or say so, never improvise.

        The only possible outcomes are: execute a registered skill, ask the
        skill's own clarifying questions, or an explicit "no skill available"
        message for the user. There is no branch that forwards the request
        to a bare LLM.
        """
        skill, score = self.match(text)
        if skill is None or score < self.policy.min_match_score:
            return RouteDecision(
                action="no_skill", skill=skill, score=score,
                message="No registered skill matches this request. "
                        "I can't help with this yet — the request was NOT "
                        "forwarded to a general model.")
        if skill.status != "proven" and not self.policy.allow_candidate_execution:
            return RouteDecision(
                action="no_skill", skill=skill, score=score,
                message=f"Skill '{skill.name}' matches but is not yet proven "
                        f"({skill.stats.executions} executions, "
                        f"{skill.stats.success_rate:.1%} success) — it must "
                        "pass the reliability bar before serving users.")
        if skill.clarifying_questions and not clarified:
            return RouteDecision(
                action="clarify", skill=skill, score=score,
                message="Need clarification before executing.",
                questions=list(skill.clarifying_questions))
        return RouteDecision(action="execute", skill=skill, score=score,
                             message=f"Routing to skill '{skill.name}'.")


class ReliabilityLedger:
    """Skills earn trust with evidence, not with confidence."""

    def __init__(self, policy: SkillPolicy) -> None:
        self.policy = policy
        self.transitions: list[tuple[str, str, str]] = []  # (skill, from, to)

    def record(self, skill: Skill, ok: bool) -> None:
        skill.stats.executions += 1
        if ok:
            skill.stats.successes += 1
        self._reassess(skill)

    def _reassess(self, skill: Skill) -> None:
        s = skill.stats
        if (skill.status == "candidate"
                and s.executions >= self.policy.min_executions_for_proven
                and s.success_rate >= self.policy.min_success_rate):
            self.transitions.append((skill.name, "candidate", "proven"))
            skill.status = "proven"
        elif (skill.status == "proven"
                and s.executions >= self.policy.min_executions_for_proven
                and s.success_rate < self.policy.demote_below_rate):
            self.transitions.append((skill.name, "proven", "candidate"))
            skill.status = "candidate"

    def stats(self, registry: SkillRegistry) -> dict[str, Any]:
        skills = registry.all()
        proven = [s for s in skills if s.status == "proven"]
        return {
            "skills_registered": len(skills),
            "skills_proven": len(proven),
            "skills_candidate":
                sum(1 for s in skills if s.status == "candidate"),
            "total_executions": sum(s.stats.executions for s in skills),
            "mean_success_rate": round(
                sum(s.stats.success_rate for s in skills if s.stats.executions)
                / max(1, sum(1 for s in skills if s.stats.executions)), 4),
            "demotions": sum(1 for t in self.transitions
                             if t[2] == "candidate"),
        }


# ---------------------------------------------------------------------------
# Composition of atomic skills, with QA on every junction
# ---------------------------------------------------------------------------


@dataclass
class PlanStep:
    """One orchestration step: which skill, and where its inputs come from.

    ``bind`` maps the skill's input field -> a source: ``"$init.<field>"``
    (the initial request payload), ``"$<step_id>.<field>"`` (a previous
    step's output), or a literal value.
    """
    step_id: str
    skill: str
    bind: dict[str, Any] = field(default_factory=dict)


class SkillComposer:
    def __init__(self, registry: SkillRegistry, ledger: ReliabilityLedger,
                 policy: SkillPolicy) -> None:
        self.registry = registry
        self.ledger = ledger
        self.policy = policy

    # -- static plan QA (before anything runs) -------------------------
    def validate_plan(self, steps: list[PlanStep],
                      initial_fields: dict[str, str]) -> list[Finding]:
        """The orchestrator (LLM) may only wire registered skills together.

        - unregistered step  -> CRITICAL (that would be the model acting)
        - unproven skill     -> BLOCKING (unless policy allows candidates)
        - junction mismatch  -> BLOCKING (source missing or wrong type)
        """
        findings: list[Finding] = []
        seen_outputs: dict[str, dict[str, str]] = {}
        seen_ids: set[str] = set()

        for step in steps:
            if step.step_id in seen_ids:
                findings.append(Finding(
                    "orchestration.duplicate_step", Severity.BLOCKING,
                    f"step id '{step.step_id}' is used twice"))
            seen_ids.add(step.step_id)

            skill = self.registry.get(step.skill)
            if skill is None:
                findings.append(Finding(
                    "orchestration.unregistered_step", Severity.CRITICAL,
                    f"step '{step.step_id}' references '{step.skill}' which "
                    "is not a registered skill — free-form model actions "
                    "are not allowed in a plan"))
                continue
            if skill.status != "proven" and \
                    not self.policy.allow_candidate_execution:
                findings.append(Finding(
                    "orchestration.unproven_skill", Severity.BLOCKING,
                    f"step '{step.step_id}' uses unproven skill "
                    f"'{skill.name}' ({skill.stats.executions} executions)"))

            # junction QA: every required input must have a valid source
            for spec in skill.contract.inputs:
                if spec.name not in step.bind:
                    if spec.required:
                        findings.append(Finding(
                            "orchestration.unbound_input", Severity.BLOCKING,
                            f"step '{step.step_id}': required input "
                            f"'{spec.name}' has no binding"))
                    continue
                source = step.bind[spec.name]
                if not (isinstance(source, str) and source.startswith("$")):
                    continue  # literal value, type-checked at runtime
                src_ref, _, src_field = source[1:].partition(".")
                if src_ref == "init":
                    src_kind = initial_fields.get(src_field)
                    if src_kind is None:
                        findings.append(Finding(
                            "orchestration.junction_missing",
                            Severity.BLOCKING,
                            f"step '{step.step_id}': '{source}' not in the "
                            "initial payload"))
                        continue
                else:
                    src_kind = seen_outputs.get(src_ref, {}).get(src_field)
                    if src_kind is None:
                        findings.append(Finding(
                            "orchestration.junction_missing",
                            Severity.BLOCKING,
                            f"step '{step.step_id}': '{source}' does not "
                            "exist in any earlier step's outputs"))
                        continue
                if src_kind != spec.kind:
                    findings.append(Finding(
                        "orchestration.junction_type", Severity.BLOCKING,
                        f"step '{step.step_id}': input '{spec.name}' expects "
                        f"{spec.kind} but '{source}' produces {src_kind}"))

            seen_outputs[step.step_id] = {
                o.name: o.kind for o in skill.contract.outputs}
        return findings

    # -- execution with runtime junction QA ----------------------------
    def execute(self, steps: list[PlanStep],
                initial: dict[str, Any]) -> tuple[dict[str, Any], list[Finding]]:
        """Run the plan deterministically. Every junction is re-validated on
        real payloads; a contract violation stops the plan and is recorded
        against the offending skill in the ledger."""
        initial_fields = {k: _kind_of(v) for k, v in initial.items()}
        findings = self.validate_plan(steps, initial_fields)
        if any(f.severity in (Severity.BLOCKING, Severity.CRITICAL)
               for f in findings):
            return {}, findings

        outputs: dict[str, dict[str, Any]] = {}
        for step in steps:
            skill = self.registry.get(step.skill)
            assert skill is not None  # guaranteed by validate_plan
            payload: dict[str, Any] = {}
            for name, source in step.bind.items():
                if isinstance(source, str) and source.startswith("$"):
                    src_ref, _, src_field = source[1:].partition(".")
                    pool = initial if src_ref == "init" else outputs[src_ref]
                    payload[name] = pool[src_field]
                else:
                    payload[name] = source

            errors = skill.contract.validate(payload, "input")
            if errors:
                findings.append(Finding(
                    "orchestration.input_contract", Severity.BLOCKING,
                    f"step '{step.step_id}' input contract violated: "
                    f"{errors}"))
                self.ledger.record(skill, ok=False)
                return {}, findings

            try:
                result = skill.fn(payload)
            except Exception as exc:  # noqa: BLE001 — contained, reported
                findings.append(Finding(
                    "orchestration.skill_error", Severity.CRITICAL,
                    f"step '{step.step_id}' skill '{skill.name}' raised "
                    f"{type(exc).__name__}: {exc}"))
                self.ledger.record(skill, ok=False)
                return {}, findings

            errors = skill.contract.validate(result, "output")
            if errors:
                findings.append(Finding(
                    "orchestration.output_contract", Severity.BLOCKING,
                    f"step '{step.step_id}' output contract violated: "
                    f"{errors}"))
                self.ledger.record(skill, ok=False)
                return {}, findings

            self.ledger.record(skill, ok=True)
            outputs[step.step_id] = result

        return outputs[steps[-1].step_id] if steps else {}, findings


def _kind_of(value: Any) -> str:
    if isinstance(value, bool):
        return "boolean"
    if isinstance(value, (int, float)):
        return "number"
    if isinstance(value, list):
        return "list"
    if isinstance(value, dict):
        return "dict"
    return "string"


# ---------------------------------------------------------------------------
# Learning new skills from user interactions
# ---------------------------------------------------------------------------


@dataclass
class SkillProposal:
    suggested_name: str
    anchor_keyword: str
    supporting_requests: list[str]
    occurrences: int


class SkillMiner:
    """Cluster unmatched user intents into candidate-skill proposals.

    Deterministic: requests are grouped by their most frequent shared
    normalized keyword; a cluster that recurs at least ``min_occurrences``
    times becomes a proposal. Proposals enter the registry as *candidates*
    and still have to earn "proven" status through the ledger.
    """

    STOPWORDS = {"the", "and", "for", "with", "please", "can", "you",
                 "what", "how", "לי", "את", "של", "עם"}

    def __init__(self, min_occurrences: int = 3) -> None:
        self.min_occurrences = min_occurrences
        self.unmatched: list[str] = []

    def observe(self, text: str) -> None:
        self.unmatched.append(text)

    @classmethod
    def _tokens(cls, text: str) -> set[str]:
        """Light normalization: lowercase, drop stopwords, singularize a
        plain plural 's' ('fees' -> 'fee') — gentler than prompt matching."""
        words = set()
        for w in re.findall(r"[\w-]+", text.lower()):
            if len(w) > 3 and w.endswith("s"):
                w = w[:-1]
            if len(w) >= 3 and w not in cls.STOPWORDS:
                words.add(w)
        return words

    def propose(self) -> list[SkillProposal]:
        # anchor each request on its keyword that recurs most across ALL
        # unmatched requests, so related phrasings land in the same cluster
        tokenized = [(t, self._tokens(t)) for t in self.unmatched]
        freq: dict[str, int] = {}
        for _, words in tokenized:
            for w in words:
                freq[w] = freq.get(w, 0) + 1
        clusters: dict[str, list[str]] = {}
        for text, words in tokenized:
            if not words:
                continue
            anchor = sorted(words, key=lambda w: (-freq[w], w))[0]
            clusters.setdefault(anchor, []).append(text)
        proposals = [
            SkillProposal(
                suggested_name=f"skill_{anchor}",
                anchor_keyword=anchor,
                supporting_requests=texts,
                occurrences=len(texts))
            for anchor, texts in sorted(clusters.items())
            if len(texts) >= self.min_occurrences
        ]
        return proposals
