"""Capability 1 — Input & Alignment Validation (dynamic guardrails).

Four pre-flight gates run before any workflow executes:

1. ``IntentGate``       — does the input carry enough intent/context to act on?
2. ``ParameterGate``    — are all required parameters extracted, typed and valid?
3. ``ReadinessGate``    — is the prompt bundle LLM-READY (few-shot, schema,
                          negative guidance, minimal slice, right model tier)?
4. ``AlignmentGate``    — does the interpreted task serve a defined business KPI?

``GateRunner`` executes them in order and stops at the first non-pass, so an
under-specified request never reaches a model.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field

from .contracts import Finding, GateResult, GateStatus, Severity
from .config import GateThresholds
from .llm_ready import (
    ModelStagePolicy,
    ParameterIntake,
    PromptBundle,
    WorkflowSpec,
    check_prompt_bundle,
)

_AMBIGUITY_MARKERS = (
    "something", "stuff", "whatever", "somehow", "maybe", "etc",
    "you know", "things", "not sure", "anything",
)

_ACTION_VERBS = (
    "analyze", "allocate", "rebalance", "segment", "compare", "extract",
    "evaluate", "assess", "project", "optimize", "review", "calculate",
    "report", "screen", "summarize", "build",
)


class IntentGate:
    """Intent & context sufficiency: score the raw user input before anything else.

    Deterministic scoring over four signals — an action verb, a match against a
    known workflow's objective keywords, enough substance (tokens), and absence
    of ambiguity markers. Below the threshold the gate asks for a restatement
    instead of failing silently.
    """

    def __init__(self, workflows: list[WorkflowSpec], thresholds: GateThresholds) -> None:
        self.workflows = workflows
        self.thresholds = thresholds

    def match_workflow(self, text: str) -> tuple[WorkflowSpec | None, float]:
        """Pick the workflow whose objective keywords best match the input."""
        lowered = text.lower()
        best: WorkflowSpec | None = None
        best_score = 0.0
        for wf in self.workflows:
            if not wf.objective_keywords:
                continue
            hits = sum(1 for kw in wf.objective_keywords if kw.lower() in lowered)
            score = hits / len(wf.objective_keywords)
            if score > best_score:
                best, best_score = wf, score
        return best, best_score

    def run(self, text: str) -> GateResult:
        findings: list[Finding] = []
        lowered = text.lower()
        tokens = re.findall(r"\w+", lowered)

        has_verb = any(v in lowered for v in _ACTION_VERBS)
        wf, kw_score = self.match_workflow(text)
        substance = min(len(tokens) / 8.0, 1.0)  # ~8 meaningful tokens saturates
        ambiguous = [m for m in _AMBIGUITY_MARKERS if m in lowered]
        clarity = max(0.0, 1.0 - 0.34 * len(ambiguous))

        score = 0.30 * (1.0 if has_verb else 0.0) \
            + 0.30 * min(kw_score * 2, 1.0) \
            + 0.20 * substance \
            + 0.20 * clarity

        if not has_verb:
            findings.append(Finding(
                "intent.verb", Severity.WARNING,
                "no clear action verb found — objective is unclear"))
        if wf is None:
            findings.append(Finding(
                "intent.workflow", Severity.BLOCKING,
                "input does not match any known workflow"))
        if ambiguous:
            findings.append(Finding(
                "intent.ambiguity", Severity.WARNING,
                f"ambiguous phrasing detected: {ambiguous}"))

        if wf is None or score < self.thresholds.min_intent_score:
            questions = [
                "Could you restate the request with a concrete objective "
                "(e.g. 'segment my portfolio by asset class' or "
                "'analyze risk for account X')?"
            ]
            return GateResult("intent", GateStatus.NEEDS_INPUT, score,
                              findings, questions)
        return GateResult("intent", GateStatus.PASS, score, findings)


class ParameterGate:
    """Parameter mapping: every required parameter extracted, typed, in range.

    Wraps the deterministic ``ParameterIntake`` loop. Missing parameters come
    back as ``NEEDS_INPUT`` with the spec's targeted questions; the workflow is
    never triggered on partial data. ``max_intake_rounds`` bounds the loop.
    """

    def __init__(self, thresholds: GateThresholds) -> None:
        self.thresholds = thresholds

    def run(self, spec: WorkflowSpec, text: str,
            provided: dict | None = None, rounds_used: int = 0) -> GateResult:
        intake = ParameterIntake(spec, max_rounds=self.thresholds.max_intake_rounds)
        state = intake.extract(text, provided=provided, rounds_used=rounds_used)

        findings = [
            Finding("parameters.invalid", Severity.BLOCKING, err)
            for err in state.errors
        ]
        total = len(spec.params) or 1
        score = len(state.filled) / total

        if state.complete:
            result = GateResult("parameters", GateStatus.PASS, score, findings)
        elif rounds_used >= self.thresholds.max_intake_rounds:
            findings.append(Finding(
                "parameters.intake_exhausted", Severity.BLOCKING,
                f"still missing {state.missing} after "
                f"{rounds_used} intake rounds"))
            result = GateResult("parameters", GateStatus.FAIL, score, findings)
        else:
            result = GateResult("parameters", GateStatus.NEEDS_INPUT, score,
                                findings, state.questions)
        result.filled_params = state.filled  # type: ignore[attr-defined]
        return result


class ReadinessGate:
    """Prompt & few-shot readiness: enforce the LLM-READY checklist in code."""

    def __init__(self, thresholds: GateThresholds,
                 policy: ModelStagePolicy | None = None) -> None:
        self.thresholds = thresholds
        self.policy = policy or ModelStagePolicy()

    def run(self, bundles: list[PromptBundle]) -> GateResult:
        if not bundles:
            return GateResult("readiness", GateStatus.FAIL, 0.0, [Finding(
                "readiness.no_bundles", Severity.BLOCKING,
                "no prompt bundles prepared for this workflow")])
        findings: list[Finding] = []
        scores: list[float] = []
        for bundle in bundles:
            score, bundle_findings = check_prompt_bundle(bundle, self.policy)
            scores.append(score)
            for f in bundle_findings:
                f.details["stage"] = bundle.stage
            findings.extend(bundle_findings)
        score = sum(scores) / len(scores)
        blocking = any(f.severity is Severity.BLOCKING for f in findings)
        if blocking or score < self.thresholds.min_readiness_score:
            return GateResult("readiness", GateStatus.FAIL, score, findings)
        return GateResult("readiness", GateStatus.PASS, score, findings)


@dataclass
class BusinessKPI:
    """A strategic objective the agent system is supposed to serve."""
    name: str
    description: str
    keywords: list[str] = field(default_factory=list)


class AlignmentGate:
    """Business alignment: the interpreted task must serve a registered KPI."""

    def __init__(self, kpis: list[BusinessKPI], thresholds: GateThresholds) -> None:
        self.kpis = {k.name: k for k in kpis}
        self.thresholds = thresholds

    def run(self, spec: WorkflowSpec, text: str) -> GateResult:
        findings: list[Finding] = []
        served = [name for name in spec.kpis_served if name in self.kpis]
        unknown = [name for name in spec.kpis_served if name not in self.kpis]
        for name in unknown:
            findings.append(Finding(
                "alignment.unknown_kpi", Severity.WARNING,
                f"workflow claims KPI '{name}' which is not registered"))
        if not served:
            findings.append(Finding(
                "alignment.no_kpi", Severity.BLOCKING,
                f"workflow '{spec.name}' serves no registered business KPI"))
            return GateResult("alignment", GateStatus.FAIL, 0.0, findings)

        # keyword overlap between the request and the KPIs the workflow serves
        lowered = text.lower()
        overlap_scores = []
        for name in served:
            kpi = self.kpis[name]
            if not kpi.keywords:
                overlap_scores.append(1.0)
                continue
            hits = sum(1 for kw in kpi.keywords if kw.lower() in lowered)
            overlap_scores.append(hits / len(kpi.keywords))
        score = 0.5 + 0.5 * max(overlap_scores)  # serving a KPI is worth half

        if score < self.thresholds.min_alignment_score:
            findings.append(Finding(
                "alignment.weak", Severity.BLOCKING,
                f"request does not clearly serve KPIs {served} "
                f"(score {score:.2f})"))
            return GateResult("alignment", GateStatus.FAIL, score, findings)
        return GateResult("alignment", GateStatus.PASS, score, findings)


class GateRunner:
    """Run all four gates in order; stop at the first non-pass."""

    def __init__(self, workflows: list[WorkflowSpec], kpis: list[BusinessKPI],
                 thresholds: GateThresholds) -> None:
        self.intent = IntentGate(workflows, thresholds)
        self.parameters = ParameterGate(thresholds)
        self.readiness = ReadinessGate(thresholds)
        self.alignment = AlignmentGate(kpis, thresholds)

    def preflight(self, text: str, bundles: list[PromptBundle],
                  provided_params: dict | None = None,
                  rounds_used: int = 0) -> tuple[list[GateResult], WorkflowSpec | None]:
        results: list[GateResult] = []

        intent_result = self.intent.run(text)
        results.append(intent_result)
        if not intent_result.passed:
            return results, None
        spec, _ = self.intent.match_workflow(text)
        assert spec is not None  # guaranteed by a passing intent gate

        param_result = self.parameters.run(spec, text, provided_params, rounds_used)
        results.append(param_result)
        if not param_result.passed:
            return results, spec

        readiness_result = self.readiness.run(bundles)
        results.append(readiness_result)
        if not readiness_result.passed:
            return results, spec

        alignment_result = self.alignment.run(spec, text)
        results.append(alignment_result)
        return results, spec
