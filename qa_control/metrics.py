"""Capability 5 — Performance, Cost & Infrastructure Metrics (KPIs).

- ``LatencyTracker``: per-operation latency with P50/P95/P99.
- ``CostTracker``: token and dollar spend per task, tokens/sec, budget checks.
- ``Diagnostics``: runtime symptom flagging (error streaks, latency
  degradation, silent-output bugs) with detection timestamps for MTTD.
- ``KPIRegistry``: aggregates every pillar's numbers into the KPI sheet the
  meta-verdict is computed from.
"""
from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any, Callable

from .config import CostThresholds, LatencyThresholds
from .contracts import Finding, Severity, Usage


def percentile(values: list[float], pct: float) -> float:
    """Nearest-rank percentile; deterministic, no numpy."""
    if not values:
        return 0.0
    ordered = sorted(values)
    rank = max(1, round(pct / 100.0 * len(ordered)))
    return ordered[min(rank, len(ordered)) - 1]


class LatencyTracker:
    def __init__(self, thresholds: LatencyThresholds) -> None:
        self.thresholds = thresholds
        self.samples: dict[str, list[float]] = {}

    def record(self, operation: str, duration_ms: float) -> None:
        self.samples.setdefault(operation, []).append(duration_ms)

    def stats(self, operation: str | None = None) -> dict[str, float]:
        values = (self.samples.get(operation, []) if operation
                  else [v for vs in self.samples.values() for v in vs])
        return {
            "count": len(values),
            "p50_ms": round(percentile(values, 50), 3),
            "p95_ms": round(percentile(values, 95), 3),
            "p99_ms": round(percentile(values, 99), 3),
            "max_ms": round(max(values), 3) if values else 0.0,
        }

    def check(self) -> list[Finding]:
        findings: list[Finding] = []
        stats = self.stats()
        if stats["p95_ms"] > self.thresholds.p95_ms:
            findings.append(Finding(
                "latency.p95", Severity.BLOCKING,
                f"P95 latency {stats['p95_ms']}ms exceeds "
                f"{self.thresholds.p95_ms}ms"))
        if stats["p99_ms"] > self.thresholds.p99_ms:
            findings.append(Finding(
                "latency.p99", Severity.BLOCKING,
                f"P99 latency {stats['p99_ms']}ms exceeds "
                f"{self.thresholds.p99_ms}ms"))
        return findings


class CostTracker:
    def __init__(self, thresholds: CostThresholds) -> None:
        self.thresholds = thresholds
        self.tasks: dict[str, Usage] = {}
        self.task_durations_s: dict[str, float] = {}

    def record_task(self, task_id: str, usage: Usage,
                    duration_s: float) -> None:
        self.tasks.setdefault(task_id, Usage()).add(usage)
        self.task_durations_s[task_id] = \
            self.task_durations_s.get(task_id, 0.0) + duration_s

    def task_stats(self, task_id: str) -> dict[str, float]:
        usage = self.tasks.get(task_id, Usage())
        duration = self.task_durations_s.get(task_id, 0.0)
        tokens = usage.input_tokens + usage.output_tokens
        return {
            "input_tokens": usage.input_tokens,
            "output_tokens": usage.output_tokens,
            "cost_usd": round(usage.cost_usd, 6),
            "tokens_per_second":
                round(tokens / duration, 2) if duration else 0.0,
        }

    def overall(self) -> dict[str, float]:
        total = Usage()
        for u in self.tasks.values():
            total.add(u)
        n = len(self.tasks) or 1
        return {
            "tasks": len(self.tasks),
            "total_tokens": total.input_tokens + total.output_tokens,
            "total_cost_usd": round(total.cost_usd, 6),
            "cost_per_task_usd": round(total.cost_usd / n, 6),
        }

    def check(self) -> list[Finding]:
        findings: list[Finding] = []
        for task_id, usage in self.tasks.items():
            tokens = usage.input_tokens + usage.output_tokens
            if usage.cost_usd > self.thresholds.max_cost_per_task_usd:
                findings.append(Finding(
                    "cost.per_task", Severity.BLOCKING,
                    f"task '{task_id}' cost ${usage.cost_usd:.4f} exceeds "
                    f"${self.thresholds.max_cost_per_task_usd}"))
            if tokens > self.thresholds.max_tokens_per_task:
                findings.append(Finding(
                    "cost.token_budget", Severity.BLOCKING,
                    f"task '{task_id}' used {tokens} tokens, over the "
                    f"{self.thresholds.max_tokens_per_task} budget"))
        return findings


# ---------------------------------------------------------------------------
# Bug & drift diagnostics with MTTD
# ---------------------------------------------------------------------------


@dataclass
class Incident:
    kind: str
    message: str
    occurred_at: float          # when the underlying symptom happened
    detected_at: float          # when diagnostics flagged it

    @property
    def time_to_detect_s(self) -> float:
        return max(0.0, self.detected_at - self.occurred_at)


class Diagnostics:
    """Proactively flag bug/degradation symptoms at runtime."""

    def __init__(self, clock: Callable[[], float] = time.time,
                 error_streak_threshold: int = 3,
                 degradation_factor: float = 3.0) -> None:
        self.clock = clock
        self.error_streak_threshold = error_streak_threshold
        self.degradation_factor = degradation_factor
        self.incidents: list[Incident] = []
        self._error_streak = 0
        self._first_streak_error_at: float | None = None
        self._latency_history: list[float] = []

    def observe_result(self, ok: bool, output_text: str = "",
                       occurred_at: float | None = None) -> None:
        now = occurred_at if occurred_at is not None else self.clock()
        if ok and not output_text.strip():
            self.incidents.append(Incident(
                "silent_output", "step reported success but produced "
                "empty output", now, self.clock()))
        if ok:
            self._error_streak = 0
            self._first_streak_error_at = None
            return
        self._error_streak += 1
        if self._first_streak_error_at is None:
            self._first_streak_error_at = now
        if self._error_streak >= self.error_streak_threshold:
            self.incidents.append(Incident(
                "error_streak",
                f"{self._error_streak} consecutive step failures — "
                "infrastructure or software bug likely",
                self._first_streak_error_at, self.clock()))
            self._error_streak = 0
            self._first_streak_error_at = None

    def observe_latency(self, duration_ms: float,
                        occurred_at: float | None = None) -> None:
        now = occurred_at if occurred_at is not None else self.clock()
        history = self._latency_history
        if len(history) >= 5:
            baseline = sorted(history)[len(history) // 2]  # median
            if baseline > 0 and duration_ms > baseline * self.degradation_factor:
                self.incidents.append(Incident(
                    "latency_degradation",
                    f"step took {duration_ms:.0f}ms vs. median baseline "
                    f"{baseline:.0f}ms — performance degradation",
                    now, self.clock()))
        history.append(duration_ms)

    def report_drift(self, message: str, occurred_at: float) -> None:
        self.incidents.append(Incident("drift", message, occurred_at,
                                       self.clock()))

    def mttd_seconds(self) -> float | None:
        if not self.incidents:
            return None
        return sum(i.time_to_detect_s for i in self.incidents) / len(self.incidents)

    def findings(self) -> list[Finding]:
        return [Finding(f"diagnostics.{i.kind}", Severity.BLOCKING, i.message,
                        {"time_to_detect_s": round(i.time_to_detect_s, 3)})
                for i in self.incidents]


# ---------------------------------------------------------------------------
# KPI registry — the single sheet the verdict reads
# ---------------------------------------------------------------------------


@dataclass
class KPIRegistry:
    values: dict[str, Any] = field(default_factory=dict)

    def set(self, name: str, value: Any) -> None:
        self.values[name] = value

    def merge(self, prefix: str, stats: dict[str, Any]) -> None:
        for key, value in stats.items():
            self.values[f"{prefix}.{key}"] = value

    def to_dict(self) -> dict[str, Any]:
        return dict(sorted(self.values.items()))
