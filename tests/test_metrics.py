"""Capability 5 tests: latency/cost KPIs, diagnostics (MTTD), rollback."""
import pytest

from qa_control.config import CostThresholds, LatencyThresholds
from qa_control.contracts import Severity, Usage
from qa_control.metrics import (
    CostTracker,
    Diagnostics,
    KPIRegistry,
    LatencyTracker,
    percentile,
)
from qa_control.rollback import RollbackManager


class FakeClock:
    def __init__(self, start=0.0, step=1.0):
        self.t = start
        self.step = step

    def __call__(self):
        self.t += self.step
        return self.t


class TestLatency:
    def test_percentiles(self):
        values = [float(i) for i in range(1, 101)]
        assert percentile(values, 50) == 50.0
        assert percentile(values, 95) == 95.0
        assert percentile(values, 99) == 99.0

    def test_threshold_breach_flagged(self):
        tracker = LatencyTracker(LatencyThresholds(p95_ms=100, p99_ms=200))
        for _ in range(49):
            tracker.record("call", 50)
        tracker.record("call", 5_000)
        findings = tracker.check()
        assert any(f.check == "latency.p99" for f in findings)

    def test_within_thresholds_clean(self):
        tracker = LatencyTracker(LatencyThresholds())
        for _ in range(20):
            tracker.record("call", 100)
        assert tracker.check() == []


class TestCost:
    def test_task_stats_and_tokens_per_second(self):
        tracker = CostTracker(CostThresholds())
        tracker.record_task("t1", Usage(1000, 500, 0.02), duration_s=3.0)
        stats = tracker.task_stats("t1")
        assert stats["tokens_per_second"] == 500.0
        assert stats["cost_usd"] == 0.02

    def test_budget_breach_flagged(self):
        tracker = CostTracker(CostThresholds(max_cost_per_task_usd=0.01))
        tracker.record_task("t1", Usage(100, 100, 0.05), duration_s=1.0)
        findings = tracker.check()
        assert any(f.check == "cost.per_task" for f in findings)

    def test_overall_cost_per_task(self):
        tracker = CostTracker(CostThresholds())
        tracker.record_task("t1", Usage(10, 10, 0.10), 1.0)
        tracker.record_task("t2", Usage(10, 10, 0.30), 1.0)
        assert tracker.overall()["cost_per_task_usd"] == pytest.approx(0.20)


class TestDiagnostics:
    def test_error_streak_detected_with_mttd(self):
        clock = FakeClock(start=100.0, step=1.0)
        diag = Diagnostics(clock=clock, error_streak_threshold=3)
        diag.observe_result(False, occurred_at=10.0)
        diag.observe_result(False, occurred_at=11.0)
        diag.observe_result(False, occurred_at=12.0)
        assert len(diag.incidents) == 1
        incident = diag.incidents[0]
        assert incident.kind == "error_streak"
        # first symptom at t=10, detected by fake clock later => MTTD > 0
        assert diag.mttd_seconds() > 0

    def test_success_resets_streak(self):
        diag = Diagnostics(clock=FakeClock(), error_streak_threshold=3)
        diag.observe_result(False, output_text="x")
        diag.observe_result(True, output_text="fine")
        diag.observe_result(False, output_text="x")
        diag.observe_result(False, output_text="x")
        assert diag.incidents == []

    def test_silent_output_bug_detected(self):
        diag = Diagnostics(clock=FakeClock())
        diag.observe_result(True, output_text="   ")
        assert diag.incidents[0].kind == "silent_output"

    def test_latency_degradation_detected(self):
        diag = Diagnostics(clock=FakeClock(), degradation_factor=3.0)
        for _ in range(5):
            diag.observe_latency(100.0)
        diag.observe_latency(1_000.0)
        assert any(i.kind == "latency_degradation" for i in diag.incidents)

    def test_no_incidents_means_no_mttd(self):
        assert Diagnostics(clock=FakeClock()).mttd_seconds() is None


class TestRollback:
    def test_revert_restores_deep_copy(self):
        rb = RollbackManager(clock=FakeClock())
        state = {"portfolio": {"equity": 0.6}}
        rb.checkpoint("run1", "after_load", state)
        state["portfolio"]["equity"] = 0.99   # live state mutated afterwards
        restored, finding = rb.revert_to_last_safe("run1", "drift detected")
        assert restored == {"portfolio": {"equity": 0.6}}
        assert finding.check == "rollback.performed"
        assert rb.stats()["rollback_success_rate"] == 1.0

    def test_unsafe_checkpoint_skipped(self):
        rb = RollbackManager(clock=FakeClock())
        rb.checkpoint("run1", "good", {"v": 1})
        cp2 = rb.checkpoint("run1", "suspect", {"v": 2})
        rb.mark_unsafe("run1", cp2.checkpoint_id)
        restored, _ = rb.revert_to_last_safe("run1", "policy violation")
        assert restored == {"v": 1}

    def test_no_safe_checkpoint_is_critical(self):
        rb = RollbackManager(clock=FakeClock())
        restored, finding = rb.revert_to_last_safe("run1", "anything")
        assert restored is None
        assert finding.severity is Severity.CRITICAL
        assert rb.stats()["rollback_success_rate"] == 0.0


class TestKPIRegistry:
    def test_merge_and_sort(self):
        reg = KPIRegistry()
        reg.set("b.metric", 2)
        reg.merge("a", {"x": 1, "y": 2})
        assert list(reg.to_dict()) == ["a.x", "a.y", "b.metric"]
