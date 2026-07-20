"""LLM-READY preparation layer.

The LLM is the last step of the pipeline, never the first. This module holds
everything that must exist *before* a model call is allowed:

- ``ParamSpec`` / ``WorkflowSpec``: the JSON-schema-like definition of what a
  workflow needs, including per-parameter few-shot material (correct AND
  incorrect examples, paraphrase mappings, negative guidance).
- ``ParameterIntake``: a deterministic extraction + question loop that keeps
  asking targeted questions until every required parameter is filled. The
  workflow never proceeds on partial data.
- ``PromptBundle`` + ``check_prompt_bundle``: the pre-call checklist. A bundle
  that is missing few-shot examples, negative guidance, a filled output
  example, or that ships too much raw data, is not ready.
- ``ModelStagePolicy``: match the model tier to the pipeline stage
  (small model for extraction, research-grade for data search, strong model
  only for the final answer).
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

from .contracts import Finding, Severity

# ---------------------------------------------------------------------------
# Parameter and workflow specifications
# ---------------------------------------------------------------------------


@dataclass
class ParamSpec:
    """Definition of one required workflow parameter, few-shot included."""
    name: str
    description: str
    kind: str = "string"                       # "string" | "number" | "choice"
    choices: list[str] = field(default_factory=list)
    # phrase (lowercase) -> canonical value; how users actually say it
    paraphrases: dict[str, str] = field(default_factory=dict)
    question: str = ""                         # targeted question when missing
    example_correct: str = ""
    example_incorrect: str = ""
    negative_guidance: str = ""
    minimum: float | None = None
    maximum: float | None = None

    def validate_value(self, value: Any) -> str | None:
        """Return an error message if the value violates the spec, else None."""
        if self.kind == "number":
            try:
                num = float(value)
            except (TypeError, ValueError):
                return f"{self.name}: expected a number, got {value!r}"
            if self.minimum is not None and num < self.minimum:
                return f"{self.name}: {num} below minimum {self.minimum}"
            if self.maximum is not None and num > self.maximum:
                return f"{self.name}: {num} above maximum {self.maximum}"
        elif self.kind == "choice":
            if str(value).lower() not in [c.lower() for c in self.choices]:
                return (
                    f"{self.name}: {value!r} not one of {self.choices}"
                )
        return None


@dataclass
class WorkflowSpec:
    """What a workflow needs before it may run, and which KPIs it serves."""
    name: str
    description: str
    params: list[ParamSpec] = field(default_factory=list)
    objective_keywords: list[str] = field(default_factory=list)
    kpis_served: list[str] = field(default_factory=list)
    output_schema: dict[str, Any] = field(default_factory=dict)
    output_example: dict[str, Any] = field(default_factory=dict)

    def param(self, name: str) -> ParamSpec:
        for p in self.params:
            if p.name == name:
                return p
        raise KeyError(name)


# ---------------------------------------------------------------------------
# Deterministic parameter intake loop
# ---------------------------------------------------------------------------

_NUMBER_RE = re.compile(r"(-?\d[\d,]*\.?\d*)\s*(%|percent|k|m|million|thousand)?", re.I)

_SCALES = {"k": 1e3, "thousand": 1e3, "m": 1e6, "million": 1e6}


def _normalize(text: str) -> str:
    """Lowercase and strip simple English suffixes so paraphrase matching
    tolerates morphological variation ('plays it safe' ~ 'play it safe')."""
    words = re.findall(r"[\w-]+", text.lower())
    normalized = []
    for w in words:
        for suffix in ("ing", "es", "s"):
            if len(w) > 3 and w.endswith(suffix):
                w = w[: -len(suffix)]
                break
        normalized.append(w)
    return " ".join(normalized)


@dataclass
class IntakeState:
    """Result of one extraction round."""
    filled: dict[str, Any]
    missing: list[str]
    questions: list[str]
    errors: list[str]
    rounds_used: int = 0

    @property
    def complete(self) -> bool:
        return not self.missing and not self.errors


class ParameterIntake:
    """Extract parameters deterministically; loop until the JSON is complete.

    Extraction is code, not a model: paraphrase mappings, choice keywords and
    number patterns cover the structured part. A real deployment can plug a
    small extraction model behind ``extract`` — the loop contract stays the
    same: never continue the flow on partial data.
    """

    def __init__(self, spec: WorkflowSpec, max_rounds: int = 5) -> None:
        self.spec = spec
        self.max_rounds = max_rounds

    def extract(self, text: str, provided: dict[str, Any] | None = None,
                rounds_used: int = 0) -> IntakeState:
        filled: dict[str, Any] = dict(provided or {})
        lowered = text.lower()
        for p in self.spec.params:
            if p.name in filled and filled[p.name] not in (None, ""):
                continue
            value = self._extract_one(p, lowered)
            if value is not None:
                filled[p.name] = value

        errors: list[str] = []
        for name, value in list(filled.items()):
            try:
                spec = self.spec.param(name)
            except KeyError:
                continue
            err = spec.validate_value(value)
            if err:
                errors.append(err)
                del filled[name]

        missing = [p.name for p in self.spec.params if p.name not in filled]
        questions = [
            self.spec.param(name).question
            or f"Please provide a value for '{name}' ({self.spec.param(name).description})."
            for name in missing
        ]
        return IntakeState(filled=filled, missing=missing, questions=questions,
                           errors=errors, rounds_used=rounds_used)

    def follow_up(self, state: IntakeState, answer_text: str) -> IntakeState:
        """One more round of the question loop with the user's answer."""
        return self.extract(answer_text, provided=state.filled,
                            rounds_used=state.rounds_used + 1)

    def _extract_one(self, p: ParamSpec, lowered: str) -> Any:
        normalized = _normalize(lowered)
        for phrase, value in p.paraphrases.items():
            if phrase.lower() in lowered or _normalize(phrase) in normalized:
                return value
        if p.kind == "choice":
            for choice in p.choices:
                if choice.lower() in lowered:
                    return choice
        if p.kind == "number":
            # look for a number near the parameter's name or description words
            anchors = [p.name.lower().replace("_", " ")] + [
                w for w in p.description.lower().split() if len(w) > 3
            ]
            for anchor in anchors:
                idx = lowered.find(anchor)
                if idx < 0:
                    continue
                window = lowered[max(0, idx - 40): idx + len(anchor) + 40]
                m = _NUMBER_RE.search(window)
                if m:
                    return self._to_number(m)
        return None

    @staticmethod
    def _to_number(m: re.Match) -> float:
        num = float(m.group(1).replace(",", ""))
        suffix = (m.group(2) or "").lower()
        if suffix in _SCALES:
            num *= _SCALES[suffix]
        return num


# ---------------------------------------------------------------------------
# Prompt bundle checklist (pre-call gate)
# ---------------------------------------------------------------------------


@dataclass
class PromptBundle:
    """Everything that will be sent to the model for one stage."""
    workflow: str
    stage: str                                  # "extraction" | "search" | "final"
    model: str
    system_prompt: str
    few_shot: list[dict[str, str]] = field(default_factory=list)  # {"input","output","label"}
    output_schema: dict[str, Any] = field(default_factory=dict)
    output_example: dict[str, Any] = field(default_factory=dict)
    negative_guidance: str = ""
    context_slices: list[str] = field(default_factory=list)       # retrieved data only
    max_context_chars: int = 20_000


class ModelStagePolicy:
    """Match the model tier to the stage. Tiers are configurable name patterns."""

    DEFAULT_TIERS = {
        "extraction": ("small", "haiku", "mini", "nano"),
        "search": ("research", "deep", "agentic", "opus", "fable"),
        "final": ("strong", "opus", "sonnet", "fable", "gpt-5"),
    }

    def __init__(self, tiers: dict[str, tuple[str, ...]] | None = None) -> None:
        self.tiers = tiers or dict(self.DEFAULT_TIERS)

    def check(self, stage: str, model: str) -> Finding | None:
        patterns = self.tiers.get(stage)
        if not patterns:
            return Finding("model_stage", Severity.WARNING,
                           f"unknown stage '{stage}' for model policy")
        if any(p in model.lower() for p in patterns):
            return None
        return Finding(
            "model_stage", Severity.WARNING,
            f"model '{model}' does not match expected tier for stage "
            f"'{stage}' (expected one of {patterns})",
        )


def check_prompt_bundle(bundle: PromptBundle,
                        policy: ModelStagePolicy | None = None) -> tuple[float, list[Finding]]:
    """The LLM-READY checklist. Returns (readiness score 0..1, findings).

    A score of 1.0 means every checklist item holds; anything less should
    block the model call (threshold configurable in ControlConfig).
    """
    policy = policy or ModelStagePolicy()
    findings: list[Finding] = []
    checks = 0
    passed = 0

    def item(ok: bool, name: str, msg: str, sev: Severity = Severity.BLOCKING) -> None:
        nonlocal checks, passed
        checks += 1
        if ok:
            passed += 1
        else:
            findings.append(Finding(name, sev, msg))

    item(bool(bundle.output_schema), "schema_defined",
         "output schema is missing from the prompt bundle")
    item(bool(bundle.output_example), "schema_example_filled",
         "output example with concrete values is missing")
    labels = {ex.get("label") for ex in bundle.few_shot}
    item("correct" in labels, "few_shot_correct",
         "no correct few-shot example in the bundle")
    item("incorrect" in labels, "few_shot_incorrect",
         "no incorrect few-shot example in the bundle")
    item(bool(bundle.negative_guidance), "negative_guidance",
         "negative guidance (what NOT to return) is missing")
    total_context = sum(len(s) for s in bundle.context_slices)
    item(total_context <= bundle.max_context_chars, "minimal_slice",
         f"context is {total_context} chars, above the "
         f"{bundle.max_context_chars} minimal-slice limit — retrieve less")
    stage_finding = policy.check(bundle.stage, bundle.model)
    checks += 1
    if stage_finding is None:
        passed += 1
    else:
        findings.append(stage_finding)

    return passed / checks, findings
