"""Capability 2 — Advanced Evaluation & Cognitive Control.

- ``DecompositionMonitor``: oversees how a complex goal is broken into
  sub-tasks and whether the synthesis step recombines *all* of them.
- ``ConsistencyEvaluator``: runs the same task N times, quantifies variance /
  standard deviation of numeric outputs and pairwise textual similarity, and
  flags semantic drift against a stored baseline.
- ``SufficiencyChecker``: has the agent gathered enough evidence to answer?
"""
from __future__ import annotations

import math
import re
from collections import Counter
from dataclasses import dataclass, field
from typing import Any

from .config import ConsistencyThresholds
from .contracts import Finding, Severity

# ---------------------------------------------------------------------------
# Task decomposition & orchestration oversight
# ---------------------------------------------------------------------------


@dataclass
class SubTask:
    task_id: str
    description: str
    assigned_agent: str
    depends_on: list[str] = field(default_factory=list)
    status: str = "pending"          # pending | done | failed
    output_ref: str | None = None    # key under which its result was stored


@dataclass
class DecompositionReport:
    complete: bool
    findings: list[Finding]
    coverage: float                  # fraction of sub-task outputs synthesized


class DecompositionMonitor:
    """Track a goal's sub-task graph and audit the final synthesis."""

    def __init__(self, goal: str) -> None:
        self.goal = goal
        self.subtasks: dict[str, SubTask] = {}

    def register(self, subtask: SubTask) -> None:
        self.subtasks[subtask.task_id] = subtask

    def mark_done(self, task_id: str, output_ref: str) -> None:
        st = self.subtasks[task_id]
        st.status = "done"
        st.output_ref = output_ref

    def mark_failed(self, task_id: str) -> None:
        self.subtasks[task_id].status = "failed"

    def validate_graph(self) -> list[Finding]:
        """Detect dangling dependencies and cycles before execution starts."""
        findings: list[Finding] = []
        for st in self.subtasks.values():
            for dep in st.depends_on:
                if dep not in self.subtasks:
                    findings.append(Finding(
                        "decomposition.dangling_dep", Severity.BLOCKING,
                        f"sub-task '{st.task_id}' depends on unknown '{dep}'"))
        # cycle detection via DFS
        WHITE, GRAY, BLACK = 0, 1, 2
        color = {tid: WHITE for tid in self.subtasks}

        def visit(tid: str) -> bool:
            color[tid] = GRAY
            for dep in self.subtasks[tid].depends_on:
                if dep not in color:
                    continue
                if color[dep] == GRAY:
                    return True
                if color[dep] == WHITE and visit(dep):
                    return True
            color[tid] = BLACK
            return False

        for tid in self.subtasks:
            if color[tid] == WHITE and visit(tid):
                findings.append(Finding(
                    "decomposition.cycle", Severity.BLOCKING,
                    f"dependency cycle detected involving '{tid}'"))
                break
        return findings

    def audit_synthesis(self, synthesized_refs: list[str]) -> DecompositionReport:
        """Did the recombination step actually use every finished sub-task?"""
        findings = self.validate_graph()
        done = [st for st in self.subtasks.values() if st.status == "done"]
        failed = [st for st in self.subtasks.values() if st.status == "failed"]
        pending = [st for st in self.subtasks.values() if st.status == "pending"]

        for st in failed:
            findings.append(Finding(
                "decomposition.failed_subtask", Severity.CRITICAL,
                f"sub-task '{st.task_id}' ({st.description}) failed"))
        for st in pending:
            findings.append(Finding(
                "decomposition.unfinished", Severity.BLOCKING,
                f"sub-task '{st.task_id}' never completed"))

        used = [st for st in done if st.output_ref in synthesized_refs]
        missing = [st for st in done if st.output_ref not in synthesized_refs]
        for st in missing:
            findings.append(Finding(
                "decomposition.dropped_output", Severity.BLOCKING,
                f"synthesis ignored output of sub-task '{st.task_id}'"))

        coverage = len(used) / len(done) if done else 0.0
        complete = (not failed and not pending and not missing
                    and bool(done))
        return DecompositionReport(complete=complete, findings=findings,
                                   coverage=coverage)


# ---------------------------------------------------------------------------
# Consistency, variance and drift
# ---------------------------------------------------------------------------

_TOKEN_RE = re.compile(r"[a-zA-Z֐-׿]{2,}|\d+\.?\d*")


def _tokens(text: str) -> Counter:
    return Counter(t.lower() for t in _TOKEN_RE.findall(text))


def cosine_similarity(a: str, b: str) -> float:
    """Cosine similarity over term-frequency vectors (deterministic, no model)."""
    ta, tb = _tokens(a), _tokens(b)
    if not ta or not tb:
        return 0.0
    common = set(ta) & set(tb)
    dot = sum(ta[t] * tb[t] for t in common)
    norm = math.sqrt(sum(v * v for v in ta.values())) * \
        math.sqrt(sum(v * v for v in tb.values()))
    return min(1.0, dot / norm) if norm else 0.0


@dataclass
class ConsistencyReport:
    consistency_score: float          # mean pairwise similarity, 0..1
    numeric_mean: dict[str, float]
    numeric_std: dict[str, float]
    numeric_cv: dict[str, float]      # coefficient of variation per metric
    drift_vs_baseline: float | None   # similarity to baseline, None if no baseline
    findings: list[Finding]

    @property
    def stable(self) -> bool:
        return not any(f.severity in (Severity.BLOCKING, Severity.CRITICAL)
                       for f in self.findings)


class ConsistencyEvaluator:
    """Quantify output variance across iterations and drift against a baseline."""

    def __init__(self, thresholds: ConsistencyThresholds) -> None:
        self.thresholds = thresholds
        self._baselines: dict[str, str] = {}   # task_key -> reference output

    def set_baseline(self, task_key: str, output: str) -> None:
        self._baselines[task_key] = output

    def evaluate(self, task_key: str, texts: list[str],
                 numerics: list[dict[str, float]] | None = None) -> ConsistencyReport:
        findings: list[Finding] = []

        # pairwise textual similarity
        sims: list[float] = []
        for i in range(len(texts)):
            for j in range(i + 1, len(texts)):
                sims.append(cosine_similarity(texts[i], texts[j]))
        consistency = sum(sims) / len(sims) if sims else 1.0
        if consistency < self.thresholds.min_consistency_score:
            findings.append(Finding(
                "consistency.low", Severity.BLOCKING,
                f"mean pairwise similarity {consistency:.3f} below "
                f"{self.thresholds.min_consistency_score}",
                {"pairwise": [round(s, 3) for s in sims]}))

        # numeric variance / std-dev / coefficient of variation
        numeric_mean: dict[str, float] = {}
        numeric_std: dict[str, float] = {}
        numeric_cv: dict[str, float] = {}
        if numerics:
            keys = set().union(*[set(n) for n in numerics])
            for key in sorted(keys):
                values = [n[key] for n in numerics if key in n]
                if len(values) < 2:
                    continue
                mean = sum(values) / len(values)
                var = sum((v - mean) ** 2 for v in values) / (len(values) - 1)
                std = math.sqrt(var)
                cv = std / abs(mean) if mean else math.inf
                numeric_mean[key] = mean
                numeric_std[key] = std
                numeric_cv[key] = cv
                if cv > self.thresholds.max_numeric_cv:
                    findings.append(Finding(
                        "consistency.numeric_variance", Severity.BLOCKING,
                        f"metric '{key}' varies too much across iterations "
                        f"(cv={cv:.3f} > {self.thresholds.max_numeric_cv})",
                        {"mean": mean, "std": std}))

        # semantic drift vs. stored baseline
        drift_sim: float | None = None
        baseline = self._baselines.get(task_key)
        if baseline is not None and texts:
            drift_sim = min(cosine_similarity(baseline, t) for t in texts)
            if drift_sim < self.thresholds.drift_alert_similarity:
                findings.append(Finding(
                    "consistency.drift", Severity.CRITICAL,
                    f"output drifted from baseline (similarity "
                    f"{drift_sim:.3f} < {self.thresholds.drift_alert_similarity})"))

        return ConsistencyReport(
            consistency_score=consistency,
            numeric_mean=numeric_mean, numeric_std=numeric_std,
            numeric_cv=numeric_cv, drift_vs_baseline=drift_sim,
            findings=findings)


# ---------------------------------------------------------------------------
# Information sufficiency
# ---------------------------------------------------------------------------


@dataclass
class EvidenceItem:
    source: str
    content: str
    kind: str = "data"    # data | citation | calculation


class SufficiencyChecker:
    """Has the agent gathered enough evidence for a compliant answer?

    Rules are deterministic: minimum number of evidence items, every required
    topic covered by at least one item, and every numeric claim in the answer
    traceable to some evidence item.
    """

    def __init__(self, min_items: int = 2,
                 required_topics: list[str] | None = None) -> None:
        self.min_items = min_items
        self.required_topics = required_topics or []

    def check(self, answer: str, evidence: list[EvidenceItem]) -> tuple[float, list[Finding]]:
        findings: list[Finding] = []
        checks, passed = 0, 0

        checks += 1
        if len(evidence) >= self.min_items:
            passed += 1
        else:
            findings.append(Finding(
                "sufficiency.too_few_sources", Severity.BLOCKING,
                f"only {len(evidence)} evidence items, need {self.min_items}"))

        corpus = " ".join(e.content.lower() for e in evidence)
        for topic in self.required_topics:
            checks += 1
            if topic.lower() in corpus:
                passed += 1
            else:
                findings.append(Finding(
                    "sufficiency.topic_uncovered", Severity.BLOCKING,
                    f"no evidence covers required topic '{topic}'"))

        # every numeric claim in the answer must appear in the evidence
        answer_numbers = set(re.findall(r"\d+\.?\d*", answer))
        evidence_numbers = set(re.findall(r"\d+\.?\d*", corpus))
        unsupported = sorted(answer_numbers - evidence_numbers)
        checks += 1
        if not unsupported:
            passed += 1
        else:
            findings.append(Finding(
                "sufficiency.unsupported_numbers", Severity.CRITICAL,
                f"answer contains numbers not present in any evidence: "
                f"{unsupported}"))

        return passed / checks if checks else 1.0, findings
