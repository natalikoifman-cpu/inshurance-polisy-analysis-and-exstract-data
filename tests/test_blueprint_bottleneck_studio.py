"""Tests for modules 8-9 and the Studio facade."""
import pytest

from blueprint_studio.bottleneck import (
    BLOCKAGE_TAXONOMY, BlockagePredictor, BlockageRules, CaseFeatures,
    CaseLibrary, process_similarity, structured_similarity,
)
from blueprint_studio.contracts import ToolResponse
from blueprint_studio.governance import (
    AuditEntry, DecisionRecord, GovernanceModule, VersionRecord,
)
from blueprint_studio.studio import BlueprintStudio
from tests.test_blueprint_discovery_data import (
    build_catalog, full_card, good_scores,
)
from blueprint_studio.dataset_lab import DatasetExample, GoldenCase
from blueprint_studio.questions import DataToAnswerMap, QuestionSpec
from blueprint_studio.architecture import ToolSpec
from blueprint_studio.evaluation_lab import Experiment, InteractionGrade, TransactionCost


def blocked_case(cid, txn="buy", instrument="bond", stage="identification",
                 btype="missing_instrument_mapping", **kw):
    return CaseFeatures(
        case_id=cid, transaction_type=txn, instrument_type=instrument,
        process_stage=stage, missing_field_ratio=kw.pop("missing", 0.6),
        identification_confidence=kw.pop("conf", 0.4),
        blockage_type=btype, delay_hours=kw.pop("delay", 6.0), **kw,
    )


def clear_case(cid, **kw):
    return CaseFeatures(
        case_id=cid, transaction_type="buy", instrument_type="equity",
        process_stage="settlement", missing_field_ratio=0.0,
        identification_confidence=0.99, **kw,
    )


class TestBottleneck:
    def test_taxonomy_has_four_categories(self):
        assert set(BLOCKAGE_TAXONOMY) == {"data", "process", "technical", "business"}

    def test_rules_flag_deterministic_blockers(self):
        findings = BlockageRules().evaluate(blocked_case("c1"))
        assert any("missing" in f for f in findings)
        assert any("confidence" in f for f in findings)

    def test_predictor_ranks_risky_case_above_clean_case(self):
        predictor = BlockagePredictor()
        history = [blocked_case(f"b{i}") for i in range(8)]
        history += [clear_case(f"g{i}") for i in range(8)]
        predictor.fit(history)
        risky = predictor.predict(blocked_case("new-risky", btype=""))
        clean = predictor.predict(clear_case("new-clean"))
        assert risky.blockage_probability > clean.blockage_probability
        assert risky.predicted_blockage_type == "missing_instrument_mapping"
        assert risky.estimated_delay_hours == pytest.approx(6.0)
        assert risky.explanation      # predictions are explainable

    def test_rule_findings_force_manual_validation(self):
        predictor = BlockagePredictor()
        predictor.fit([clear_case("g1")])
        pred = predictor.predict(blocked_case("c1", btype=""), rules=BlockageRules())
        assert pred.recommended_action == "request_manual_validation"
        assert pred.blockage_probability >= 0.8

    def test_root_cause_generalizes_across_instruments(self):
        """§22 — a bond and a fund blocked for the same root cause match."""
        bond = blocked_case("bond-1", instrument="bond")
        fund = blocked_case("fund-1", instrument="fund")
        different = clear_case("eq-1")
        assert structured_similarity(bond, fund) > structured_similarity(bond, different)

    def test_process_similarity_uses_shared_path(self):
        a = CaseFeatures(case_id="a", process_path=["identify", "api_fail", "manual"])
        b = CaseFeatures(case_id="b", process_path=["identify", "api_fail", "resolve"])
        c = CaseFeatures(case_id="c", process_path=["settle"])
        assert process_similarity(a, b) == pytest.approx(2 / 3)
        assert process_similarity(a, c) == 0.0

    def test_case_library_returns_ranked_neighbors(self):
        library = CaseLibrary()
        library.add(blocked_case("hist-similar"))
        library.add(clear_case("hist-other"))
        neighbors = library.most_similar(blocked_case("new", btype=""), limit=1)
        assert neighbors[0][0].case_id == "hist-similar"


class TestGovernance:
    def test_decision_and_audit_logs(self):
        gov = GovernanceModule()
        gov.log_decision(DecisionRecord(
            decision_id="d1", subject="fx source", decision="use custodian rate",
            rationale="licensed and complete", decided_by="data owner",
            decided_at="2026-07-20",
        ))
        gov.audit(AuditEntry(
            request_id="req-1", event="tool_call", actor="agent",
            at="2026-07-20T08:00:00",
        ))
        assert len(gov.decisions()) == 1
        assert len(gov.audit_trail("req-1")) == 1

    def test_version_rollback_chain(self):
        gov = GovernanceModule()
        gov.register_version(VersionRecord(version="v1", released_at="2026-06-01"))
        gov.register_version(VersionRecord(version="v2", released_at="2026-07-01"))
        assert gov.active_version().version == "v2"
        rolled = gov.rollback()
        assert rolled.version == "v1"
        assert gov.active_version().version == "v1"


def fully_built_studio():
    """A studio where the 'usd exposure' capability is complete."""
    studio = BlueprintStudio()
    studio.discovery.add(full_card())
    question = "מה החשיפה שלי לדולר?"
    studio.questions.add_question(QuestionSpec(
        text=question, use_case="usd exposure", intent="currency_exposure",
    ))
    studio.questions.add_map(DataToAnswerMap(
        question=question, intent="currency_exposure",
        required_information=["holdings", "fx rates"],
        fields=["Holding.currency"], sources=["custodian"],
        calculation_rules=["exposure_v2"], permissions=["portfolio:read"],
        validation_rules=["coverage >= 0.9"],
        answer_structure=["direct answer", "as_of", "source", "coverage"],
    ))
    # catalog + readiness
    src_catalog = build_catalog()
    studio.catalog._sources = src_catalog._sources
    studio.catalog._fields = src_catalog._fields
    studio.catalog._lineage = src_catalog._lineage
    studio.readiness.catalog = studio.catalog
    studio.readiness.assess(
        "usd exposure", ["Holding.currency"], ["custodian"], good_scores(),
    )
    # dataset + golden
    studio.dataset.add(DatasetExample(
        example_id="e1", text=question, use_case="usd exposure",
        group_key="client-1", period="2026-06",
    ))
    studio.dataset.split_by_group()
    studio.dataset.add_golden(GoldenCase(
        question=question, context={"use_case": "usd exposure"},
        user_type="end_client", permissions=["portfolio:read"],
        source_data={"exposure_usd": 0.384},
        correct_answer="38.4% based on 96% coverage",
        expected_tool="get_instrument_exposures",
        expected_source="custodian",
    ))
    # tools, evaluation, experiment
    studio.tools.register(ToolSpec(
        name="get_instrument_exposures", description="exposures",
        use_cases=["usd exposure"], required_permission="portfolio:read",
        data_sources=["custodian"], success_metric="calculation accuracy",
        refusal_conditions=["coverage < 0.5"],
    ))
    studio.evaluation.record(InteractionGrade(
        interaction_id="i1", use_case="usd exposure",
        scores={"factual_accuracy": 5, "groundedness": 5,
                "calculation_accuracy": 5, "task_completion": 5,
                "clarity": 4, "cost_efficiency": 4},
        cost=TransactionCost(input_tokens=800, output_tokens=300),
    ))
    studio.evaluation.log_experiment(Experiment(
        experiment_id="exp-1", hypothesis="baseline", changed_component="none",
    ))
    return studio


class TestStudio:
    def test_complete_capability_gets_go(self):
        studio = fully_built_studio()
        report = studio.capability_report("usd exposure")
        assert report.missing_deliverables == []
        assert report.blockers == []
        assert report.go, report.reasons

    def test_incomplete_capability_gets_no_go_with_reasons(self):
        studio = BlueprintStudio()
        studio.discovery.add(full_card("bare"))
        report = studio.capability_report("bare")
        assert not report.go
        assert any("no readiness assessment" in r for r in report.reasons)
        assert "question_bank" in report.missing_deliverables

    def test_dashboard_aggregates(self):
        studio = fully_built_studio()
        dash = studio.dashboard()
        assert dash["use_cases"] == 1
        assert dash["production_ready"] == 1
        assert dash["golden_cases"] == 1
        assert dash["capabilities"]["usd exposure"]["go"] is True
