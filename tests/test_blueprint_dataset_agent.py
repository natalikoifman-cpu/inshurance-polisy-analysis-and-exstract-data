"""Tests for modules 5-7: dataset lab, architecture, evaluation lab."""
import pytest

from blueprint_studio.architecture import (
    DecisionGate, RequestContext, ToolRegistry, ToolSpec,
)
from blueprint_studio.contracts import Decision, ToolResponse
from blueprint_studio.dataset_lab import (
    DatasetExample, DatasetLab, ExpertLabel, GoldenCase,
)
from blueprint_studio.evaluation_lab import (
    ClassificationStats, EvaluationLab, Experiment, InteractionGrade,
    TransactionCost, VersionCandidate, summarize,
)


def example(eid, group, period="2026-01", **kw):
    return DatasetExample(
        example_id=eid, text=f"case {eid}", use_case="performance",
        group_key=group, period=period, **kw,
    )


class TestDatasetLab:
    def test_group_split_keeps_group_together(self):
        lab = DatasetLab()
        for i in range(30):
            group = f"client-{i % 10}"
            lab.add(example(f"e{i}", group))
        lab.split_by_group()
        assert lab.leakage_report() == []
        by_group = {}
        for split in ("training", "validation", "test"):
            for ex in lab.examples(split):
                by_group.setdefault(ex.group_key, set()).add(split)
        assert all(len(splits) == 1 for splits in by_group.values())

    def test_variation_follows_seed_group(self):
        lab = DatasetLab()
        lab.add(example("seed", "client-1"))
        lab.add_variation("seed", "var1", "reworded question")
        lab.split_by_group()
        report = lab.leakage_report()
        # the only allowed problem is the unapproved variation, not a split leak
        assert not any("crosses splits" in p for p in report)

    def test_unapproved_synthetic_is_flagged(self):
        lab = DatasetLab()
        lab.add(example("seed", "client-1"))
        lab.add_variation("seed", "syn1", "no ISIN present", origin="synthetic")
        lab.split_by_group()
        assert any("lacks expert approval" in p for p in lab.leakage_report())

    def test_approved_synthetic_is_clean(self):
        lab = DatasetLab()
        lab.add(example("seed", "client-1"))
        var = lab.add_variation("seed", "syn1", "no ISIN", origin="synthetic")
        var.expert_label = ExpertLabel(
            problem_type="missing_identifier", approved_by="expert A",
        )
        lab.split_by_group()
        assert lab.leakage_report() == []

    def test_time_split_newest_period_is_test(self):
        lab = DatasetLab()
        lab.add(example("old", "g1", period="2026-01"))
        lab.add(example("mid", "g2", period="2026-05"))
        lab.add(example("new", "g3", period="2026-07"))
        lab.split_by_time(validation_from="2026-05", test_from="2026-07")
        assert [e.example_id for e in lab.examples("training")] == ["old"]
        assert [e.example_id for e in lab.examples("validation")] == ["mid"]
        assert [e.example_id for e in lab.examples("test")] == ["new"]

    def test_time_split_group_integrity_wins(self):
        lab = DatasetLab()
        lab.add(example("a", "same-deal", period="2026-01"))
        lab.add(example("b", "same-deal", period="2026-07"))
        lab.split_by_time(validation_from="2026-05", test_from="2026-07")
        assert lab.leakage_report() == []
        assert {e.example_id for e in lab.examples("test")} == {"a", "b"}

    def test_active_learning_prefers_low_confidence_high_impact(self):
        lab = DatasetLab()
        lab.add(example("sure", "g1", model_confidence=0.95, business_impact="low"))
        lab.add(example("unsure", "g2", model_confidence=0.2, business_impact="high"))
        labeled = example("done", "g3", model_confidence=0.1)
        labeled.expert_label = ExpertLabel(problem_type="x", approved_by="e")
        lab.add(labeled)
        queue = lab.active_learning_queue(limit=2)
        assert queue[0].example_id == "unsure"
        assert all(e.expert_label is None for e in queue)


def tool_spec(name="get_instrument_exposures", permission="portfolio:read"):
    return ToolSpec(
        name=name, description="exposure by currency",
        use_cases=["usd exposure"], required_permission=permission,
        data_sources=["custodian"], calculation_method="exposure_v2",
        success_metric="calculation accuracy", refusal_conditions=["coverage < 0.5"],
    )


def ctx(**kw):
    defaults = dict(
        user_id="u1", client_id="c1",
        permissions=frozenset({"portfolio:read"}), request_id="req-1",
    )
    defaults.update(kw)
    return RequestContext(**defaults)


def ok_response(**kw):
    defaults = dict(
        status="success", as_of="2026-07-19", source="custodian",
        coverage=0.96, confidence=0.94,
    )
    defaults.update(kw)
    return ToolResponse(**defaults)


class TestArchitecture:
    def test_incomplete_tool_rejected(self):
        registry = ToolRegistry()
        bad = ToolSpec(name="mystery", description="no metadata")
        with pytest.raises(ValueError, match="missing required definitions"):
            registry.register(bad)

    def test_permission_enforced_in_code(self):
        registry = ToolRegistry()
        registry.register(tool_spec(), handler=lambda ctx: ok_response())
        denied = registry.invoke(
            ctx(permissions=frozenset({"other:read"})), "get_instrument_exposures",
        )
        assert denied.status == "error"
        assert "permission denied" in denied.warnings[0]
        allowed = registry.invoke(ctx(), "get_instrument_exposures")
        assert allowed.status == "success"
        assert allowed.request_id == "req-1"

    def test_unregistered_tool_denied_by_default(self):
        gate = ToolRegistry().permission_gate()
        assert not gate.allows(ctx(), "unknown_tool")

    def test_gate_answers_clean_case(self):
        verdict = DecisionGate().evaluate(ctx(), ok_response(), staleness_hours=10)
        assert verdict.decision is Decision.ANSWER

    def test_gate_refuses_without_correctness_date(self):
        verdict = DecisionGate().evaluate(
            ctx(), ok_response(as_of=""), staleness_hours=1,
        )
        assert verdict.decision is Decision.REFUSE_MISSING_DATA

    def test_gate_refuses_identity_mismatch(self):
        verdict = DecisionGate().evaluate(
            ctx(identified_client_matches=False), ok_response(), staleness_hours=1,
        )
        assert verdict.decision is Decision.REFUSE_PERMISSION

    def test_gate_low_coverage_warns_then_refuses(self):
        gate = DecisionGate(min_coverage=0.9)
        warn = gate.evaluate(ctx(), ok_response(coverage=0.7), staleness_hours=1)
        assert warn.decision is Decision.ANSWER_WITH_WARNING
        refuse = gate.evaluate(ctx(), ok_response(coverage=0.4), staleness_hours=1)
        assert refuse.decision is Decision.REFUSE_MISSING_DATA

    def test_gate_routes_ambiguity_advice_and_conflicts(self):
        gate = DecisionGate()
        assert gate.evaluate(
            ctx(ambiguous=True), ok_response(), 1,
        ).decision is Decision.ASK_CLARIFICATION
        assert gate.evaluate(
            ctx(could_be_advice=True), ok_response(), 1,
        ).decision is Decision.REQUEST_HUMAN_VALIDATION
        assert gate.evaluate(
            ctx(), ok_response(), 1, conflicting_sources=True,
        ).decision is Decision.CREATE_DATA_QUALITY_TASK

    def test_gate_stale_data_warns(self):
        verdict = DecisionGate(max_staleness_hours=48).evaluate(
            ctx(), ok_response(), staleness_hours=100,
        )
        assert verdict.decision is Decision.ANSWER_WITH_WARNING


def grade(iid, scores=None, **kw):
    return InteractionGrade(
        interaction_id=iid, use_case="usd exposure",
        scores=scores or {
            "factual_accuracy": 5, "groundedness": 5, "calculation_accuracy": 5,
            "task_completion": 5, "clarity": 4, "cost_efficiency": 4,
        },
        **kw,
    )


class TestEvaluationLab:
    def test_hard_violation_zeroes_score(self):
        g = grade("i1", hard_violations=["fabricated_fact"])
        assert g.objective_score() == 0.0

    def test_weighted_objective(self):
        g = grade("i1")
        assert g.objective_score() == pytest.approx(4.8)

    def test_per_use_case_weights_change_score(self):
        lab = EvaluationLab()
        lab.set_weights("usd exposure", {"clarity": 1.0})
        lab.record(grade("i1"))
        assert lab.objective_scores("usd exposure") == [4.0]

    def test_summary_statistics(self):
        s = summarize([4.0, 4.5, 5.0, 1.0])
        assert s.n == 4
        assert s.mean == pytest.approx(3.625)
        assert s.failure_rate == pytest.approx(0.25)
        assert s.ci95_low < s.mean < s.ci95_high

    def test_cost_per_successful_task(self):
        lab = EvaluationLab()
        lab.record(grade("ok", cost=TransactionCost(input_tokens=1000, output_tokens=500)))
        lab.record(grade("fail", hard_violations=["wrong_calculation"],
                         cost=TransactionCost(input_tokens=1000)))
        cost = lab.cost_per_successful_task("usd exposure")
        assert cost == pytest.approx(
            TransactionCost(input_tokens=1000, output_tokens=500).total()
            + TransactionCost(input_tokens=1000).total()
        )

    def test_classification_stats_for_rare_events(self):
        stats = ClassificationStats(
            true_positives=8, false_positives=2,
            true_negatives=85, false_negatives=5,
        )
        assert stats.precision == pytest.approx(0.8)
        assert stats.recall == pytest.approx(8 / 13)
        assert 0 < stats.f1 < 1

    def test_acceptance_gate_blocks_safety_regression(self):
        lab = EvaluationLab()
        candidate = VersionCandidate(
            version="v2", safety_regression=True, permission_regression=False,
            calculation_regression=False, hallucination_rate_before=0.01,
            hallucination_rate_after=0.01, evaluated_on_representative_dataset=True,
            regression_tested=True, cost_within_budget=True, latency_approved=True,
            stable_across_runs=True, rollback_available=True, documented=True,
        )
        accepted, failures = lab.acceptance_gate(candidate)
        assert not accepted and failures == ["safety regressed"]

    def test_acceptance_gate_passes_clean_candidate(self):
        lab = EvaluationLab()
        candidate = VersionCandidate(
            version="v2", safety_regression=False, permission_regression=False,
            calculation_regression=False, hallucination_rate_before=0.02,
            hallucination_rate_after=0.015, evaluated_on_representative_dataset=True,
            regression_tested=True, cost_within_budget=True, latency_approved=True,
            stable_across_runs=True, rollback_available=True, documented=True,
        )
        accepted, failures = lab.acceptance_gate(candidate)
        assert accepted and failures == []

    def test_experiment_log(self):
        lab = EvaluationLab()
        lab.log_experiment(Experiment(
            experiment_id="exp-1", hypothesis="fewer docs improve groundedness",
            changed_component="retrieval.top_k",
        ))
        assert len(lab.experiments()) == 1
