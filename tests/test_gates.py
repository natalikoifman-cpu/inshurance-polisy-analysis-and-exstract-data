"""Capability 1 tests: input & alignment validation gates."""
import pytest

from qa_control.config import GateThresholds
from qa_control.contracts import GateStatus, Severity
from qa_control.gates import (
    AlignmentGate,
    BusinessKPI,
    GateRunner,
    IntentGate,
    ParameterGate,
    ReadinessGate,
)
from qa_control.llm_ready import (
    ModelStagePolicy,
    ParameterIntake,
    ParamSpec,
    PromptBundle,
    WorkflowSpec,
    check_prompt_bundle,
)


@pytest.fixture
def spec():
    return WorkflowSpec(
        name="portfolio_segmentation",
        description="Segment a portfolio into asset-class buckets",
        params=[
            ParamSpec(
                name="account_id", description="client account identifier",
                paraphrases={"account acc-": "ACC-1001", "my main account": "ACC-1001"},
                question="Which account should I analyze?",
                example_correct="'segment account ACC-1001' -> account_id=ACC-1001",
                example_incorrect="'segment my stuff' -> account_id=stuff (wrong)",
                negative_guidance="Never invent an account id.",
            ),
            ParamSpec(
                name="risk_profile", description="client risk appetite",
                kind="choice", choices=["conservative", "balanced", "aggressive"],
                paraphrases={"don't like risk": "conservative",
                             "play it safe": "conservative"},
                question="What is the client's risk profile "
                         "(conservative / balanced / aggressive)?",
                example_correct="'she plays it safe' -> conservative",
                example_incorrect="'safe assets only' -> aggressive (wrong)",
                negative_guidance="Do not infer risk from account size.",
            ),
            ParamSpec(
                name="horizon_years", description="investment horizon years",
                kind="number", minimum=1, maximum=50,
                question="What is the investment horizon in years?",
                example_correct="'over the next 10 years' -> 10",
                example_incorrect="'10 stocks' -> 10 (wrong, that's a count)",
                negative_guidance="Ignore numbers that count assets.",
            ),
        ],
        objective_keywords=["segment", "portfolio", "asset"],
        kpis_served=["client_retention"],
        output_schema={"segments": "list"},
        output_example={"segments": [{"class": "equity", "weight": 0.6}]},
    )


@pytest.fixture
def thresholds():
    return GateThresholds()


@pytest.fixture
def kpis():
    return [BusinessKPI("client_retention",
                        "keep clients through better advice",
                        keywords=["portfolio", "client"])]


def good_bundle(stage="extraction", model="small-extractor-v1", **kw):
    defaults = dict(
        workflow="portfolio_segmentation", stage=stage, model=model,
        system_prompt="Extract parameters.",
        few_shot=[{"input": "a", "output": "b", "label": "correct"},
                  {"input": "c", "output": "d", "label": "incorrect"}],
        output_schema={"account_id": "string"},
        output_example={"account_id": "ACC-1001"},
        negative_guidance="Never invent values.",
        context_slices=["short slice"],
    )
    defaults.update(kw)
    return PromptBundle(**defaults)


class TestIntentGate:
    def test_clear_request_passes(self, spec, thresholds):
        gate = IntentGate([spec], thresholds)
        result = gate.run("Please segment the portfolio of account ACC-1001 "
                          "by asset class for a balanced client")
        assert result.status is GateStatus.PASS
        assert result.score >= thresholds.min_intent_score

    def test_vague_request_needs_input(self, spec, thresholds):
        gate = IntentGate([spec], thresholds)
        result = gate.run("do something with my stuff maybe")
        assert result.status is GateStatus.NEEDS_INPUT
        assert result.questions

    def test_unknown_workflow_needs_input(self, spec, thresholds):
        gate = IntentGate([spec], thresholds)
        result = gate.run("book me a flight to Paris tomorrow morning please")
        assert result.status is GateStatus.NEEDS_INPUT


class TestParameterIntake:
    def test_full_extraction(self, spec):
        intake = ParameterIntake(spec)
        state = intake.extract(
            "Segment my main account, she plays it safe, "
            "horizon of 10 years")
        assert state.complete
        assert state.filled["account_id"] == "ACC-1001"
        assert state.filled["risk_profile"] == "conservative"
        assert state.filled["horizon_years"] == 10.0

    def test_missing_params_produce_targeted_questions(self, spec):
        intake = ParameterIntake(spec)
        state = intake.extract("Segment my main account")
        assert not state.complete
        assert "risk_profile" in state.missing
        assert any("risk profile" in q for q in state.questions)

    def test_question_loop_until_complete(self, spec):
        intake = ParameterIntake(spec)
        state = intake.extract("Segment my main account")
        state = intake.follow_up(state, "balanced, horizon 7 years")
        assert state.complete
        assert state.rounds_used == 1

    def test_out_of_range_number_rejected(self, spec):
        intake = ParameterIntake(spec)
        state = intake.extract(
            "Segment my main account, balanced, horizon of 90 years")
        assert not state.complete
        assert any("above maximum" in e for e in state.errors)


class TestParameterGate:
    def test_partial_data_never_passes(self, spec, thresholds):
        gate = ParameterGate(thresholds)
        result = gate.run(spec, "Segment my main account")
        assert result.status is GateStatus.NEEDS_INPUT
        assert result.questions

    def test_intake_rounds_cap(self, spec, thresholds):
        gate = ParameterGate(thresholds)
        result = gate.run(spec, "Segment my main account",
                          rounds_used=thresholds.max_intake_rounds)
        assert result.status is GateStatus.FAIL


class TestReadinessChecklist:
    def test_complete_bundle_scores_one(self):
        score, findings = check_prompt_bundle(good_bundle())
        assert score == 1.0
        assert findings == []

    def test_missing_incorrect_example_flagged(self):
        bundle = good_bundle(few_shot=[
            {"input": "a", "output": "b", "label": "correct"}])
        score, findings = check_prompt_bundle(bundle)
        assert score < 1.0
        assert any(f.check == "few_shot_incorrect" for f in findings)

    def test_oversized_context_flagged(self):
        bundle = good_bundle(context_slices=["x" * 30_000])
        _, findings = check_prompt_bundle(bundle)
        assert any(f.check == "minimal_slice" for f in findings)

    def test_wrong_model_tier_flagged(self):
        bundle = good_bundle(stage="extraction", model="giant-strong-model")
        _, findings = check_prompt_bundle(bundle)
        assert any(f.check == "model_stage" for f in findings)

    def test_readiness_gate_blocks_incomplete(self, thresholds):
        gate = ReadinessGate(thresholds)
        bad = good_bundle(negative_guidance="")
        result = gate.run([bad])
        assert result.status is GateStatus.FAIL


class TestAlignmentGate:
    def test_aligned_workflow_passes(self, spec, thresholds, kpis):
        gate = AlignmentGate(kpis, thresholds)
        result = gate.run(spec, "segment the portfolio for this client")
        assert result.status is GateStatus.PASS

    def test_workflow_without_kpi_fails(self, spec, thresholds):
        gate = AlignmentGate([], thresholds)
        result = gate.run(spec, "segment the portfolio")
        assert result.status is GateStatus.FAIL
        assert any(f.check == "alignment.no_kpi" for f in result.findings)


class TestGateRunner:
    def test_full_preflight_pass(self, spec, thresholds, kpis):
        runner = GateRunner([spec], kpis, thresholds)
        results, matched = runner.preflight(
            "Segment the portfolio of my main account by asset class, "
            "client plays it safe, horizon 10 years",
            bundles=[good_bundle()],
        )
        assert matched is spec
        assert [r.gate for r in results] == [
            "intent", "parameters", "readiness", "alignment"]
        assert all(r.passed for r in results)

    def test_stops_at_first_failure(self, spec, thresholds, kpis):
        runner = GateRunner([spec], kpis, thresholds)
        results, matched = runner.preflight(
            "Segment the portfolio of my main account by asset class, "
            "client plays it safe, horizon 10 years",
            bundles=[],  # readiness must fail
        )
        assert [r.gate for r in results] == ["intent", "parameters", "readiness"]
        assert results[-1].status is GateStatus.FAIL
