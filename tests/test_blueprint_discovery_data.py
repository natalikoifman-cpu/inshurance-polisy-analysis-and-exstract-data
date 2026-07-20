"""Tests for modules 1-4: discovery, questions, catalog, readiness."""
import pytest

from blueprint_studio.catalog import (
    DataCatalog, DataSource, FieldSpec, LineageRecord, LineageStep,
)
from blueprint_studio.contracts import Gap, ReadinessLevel, RiskLevel, UserType
from blueprint_studio.discovery import DiscoveryModule, UseCaseCard
from blueprint_studio.questions import DataToAnswerMap, QuestionBank, QuestionSpec
from blueprint_studio.readiness import READINESS_DIMENSIONS, ReadinessModule


def full_card(name="usd exposure"):
    return UseCaseCard(
        name=name,
        problem="clients cannot see currency exposure",
        user_type=UserType.END_CLIENT,
        desired_outcome="client understands USD exposure",
        business_value="fewer service calls",
        risk_level=RiskLevel.HIGH,
        user_questions=["מה החשיפה שלי לדולר?"],
        agent_actions=["get_instrument_exposures"],
        success_metrics=["factual accuracy >= 4.5"],
        required_information=["Holding.currency"],
        limitations=["no advice"],
        refusal_conditions=["coverage below 50%"],
        business_approver="head of wealth",
        tech_owner="data platform team",
    )


class TestDiscovery:
    def test_complete_card_is_ready(self):
        assert full_card().ready_for_development

    def test_incomplete_card_lists_missing_items(self):
        card = UseCaseCard(
            name="x", problem="p", user_type=UserType.ANALYST,
            desired_outcome="d",
        )
        missing = card.definition_of_ready()
        assert "real user questions collected" in missing
        assert "business approver assigned" in missing
        assert not card.ready_for_development

    def test_module_partitions_ready_and_blocked(self):
        module = DiscoveryModule()
        module.add(full_card())
        module.add(UseCaseCard(
            name="incomplete", problem="p",
            user_type=UserType.SERVICE_REP, desired_outcome="d",
        ))
        assert [c.name for c in module.ready()] == ["usd exposure"]
        assert "incomplete" in module.blocked()


class TestQuestionBank:
    def test_question_tree_flattens_and_unmapped_detected(self):
        bank = QuestionBank()
        root = QuestionSpec(
            text="למה התיק שלי ירד?", use_case="performance",
            sub_questions=[
                QuestionSpec(text="מהי תקופת ההשוואה?", use_case="performance"),
                QuestionSpec(text="האם היו הפקדות או משיכות?", use_case="performance"),
            ],
        )
        bank.add_question(root)
        assert len(bank.questions_for("performance")) == 3
        assert len(bank.unmapped_questions()) == 3

    def test_incomplete_map_reports_missing_links(self):
        m = DataToAnswerMap(question="q", intent="exposure", fields=["Holding.currency"])
        problems = m.missing_links()
        assert "no source of truth mapped" in problems
        assert "no permission rule defined" in problems

    def test_complete_map_has_no_missing_links(self):
        m = DataToAnswerMap(
            question="q", intent="exposure",
            fields=["Holding.currency"], sources=["custodian"],
            calculation_rules=["exposure_v2"], permissions=["portfolio:read"],
            validation_rules=["coverage >= 0.9"],
        )
        assert m.missing_links() == []


def build_catalog(licensed=True, with_lineage=True):
    catalog = DataCatalog()
    catalog.add_source(DataSource(
        name="custodian", source_type="portfolio",
        licensed_for_client_display=licensed, coverage=0.98,
    ))
    catalog.add_field(FieldSpec(
        name="currency", entity="Holding", source="custodian", coverage=0.96,
    ))
    if with_lineage:
        catalog.add_lineage(LineageRecord(
            field="Holding.currency", source="custodian",
            as_of="2026-07-19", ingested_at="2026-07-20T06:00:00",
            steps=[LineageStep("ingestion", "nightly custodian feed")],
        ))
    return catalog


def good_scores():
    return {d: 4.0 for d in READINESS_DIMENSIONS}


class TestReadiness:
    def test_clean_assessment_reaches_controlled_customer(self):
        module = ReadinessModule(build_catalog())
        a = module.assess(
            "usd exposure", ["Holding.currency"], ["custodian"], good_scores(),
        )
        assert a.blockers == []
        assert a.level is ReadinessLevel.CONTROLLED_CUSTOMER

    def test_missing_field_blocks(self):
        module = ReadinessModule(build_catalog())
        a = module.assess(
            "usd exposure", ["Holding.hedge_ratio"], ["custodian"], good_scores(),
        )
        assert any("no source of truth" in b for b in a.blockers)
        assert a.level is ReadinessLevel.UNUSABLE

    def test_unlicensed_source_blocks(self):
        module = ReadinessModule(build_catalog(licensed=False))
        a = module.assess(
            "usd exposure", ["Holding.currency"], ["custodian"], good_scores(),
        )
        assert any("licensing" in b for b in a.blockers)

    def test_missing_lineage_blocks(self):
        module = ReadinessModule(build_catalog(with_lineage=False))
        a = module.assess(
            "usd exposure", ["Holding.currency"], ["custodian"], good_scores(),
        )
        assert any("lineage" in b for b in a.blockers)

    def test_unassessed_dimension_blocks(self):
        module = ReadinessModule(build_catalog())
        scores = good_scores()
        scores.pop("freshness")
        a = module.assess("usd exposure", ["Holding.currency"], ["custodian"], scores)
        assert any("freshness" in b for b in a.blockers)

    def test_weakest_dimension_caps_overall(self):
        module = ReadinessModule(build_catalog())
        scores = good_scores()
        scores["completeness"] = 2.0
        a = module.assess("usd exposure", ["Holding.currency"], ["custodian"], scores)
        assert a.overall == 2.0
        assert a.level is ReadinessLevel.INTERNAL_RESEARCH

    def test_blockers_open_priority_one_gaps(self):
        module = ReadinessModule(build_catalog(with_lineage=False))
        a = module.assess("usd exposure", ["Holding.currency"], ["custodian"], good_scores())
        gaps = module.gaps_from_assessment(a)
        assert gaps and all(g.priority == 1 for g in gaps)
        assert module.gap_matrix()[0]["gap_type"] == "data"

    def test_gap_matrix_sorted_by_priority(self):
        module = ReadinessModule(build_catalog())
        module.add_gap(Gap(gap_type="quality", description="b", impact="i", priority=2))
        module.add_gap(Gap(gap_type="regulatory", description="a", impact="i", priority=1))
        matrix = module.gap_matrix()
        assert [row["gap_type"] for row in matrix] == ["regulatory", "quality"]

    def test_lineage_coverage(self):
        catalog = build_catalog()
        catalog.add_field(FieldSpec(name="price", entity="Holding", source="custodian"))
        assert catalog.lineage_coverage() == pytest.approx(0.5)
