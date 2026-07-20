"""End-to-end demo of the Financial Agent Blueprint & Data Readiness Studio.

Walks one capability — "What is my USD exposure?" — through the full
lifecycle (§45): discovery -> questions -> catalog -> readiness ->
dataset -> tools -> evaluation -> blockage intelligence -> governance
-> Go/No-Go.  Output is deterministic: run it twice and diff.
"""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from blueprint_studio import BlueprintStudio
from blueprint_studio.architecture import DecisionGate, RequestContext, ToolSpec
from blueprint_studio.bottleneck import BlockageRules, CaseFeatures
from blueprint_studio.catalog import DataSource, FieldSpec, LineageRecord, LineageStep
from blueprint_studio.contracts import RiskLevel, ToolResponse, UserType
from blueprint_studio.dataset_lab import DatasetExample, ExpertLabel, GoldenCase
from blueprint_studio.discovery import UseCaseCard
from blueprint_studio.evaluation_lab import (
    Experiment, InteractionGrade, TransactionCost, VersionCandidate,
)
from blueprint_studio.governance import DecisionRecord, VersionRecord
from blueprint_studio.questions import DataToAnswerMap, QuestionSpec
from blueprint_studio.readiness import READINESS_DIMENSIONS


def section(title):
    print(f"\n{'=' * 62}\n{title}\n{'=' * 62}")


def main():
    studio = BlueprintStudio()
    uc = "usd exposure"

    # ------------------------------------------------ 1. Discovery (§6)
    section("1. Business Discovery — Use Case Card")
    card = studio.discovery.add(UseCaseCard(
        name=uc,
        problem="Clients call support to understand their USD exposure",
        user_type=UserType.END_CLIENT,
        desired_outcome="Self-service, sourced, dated exposure answer",
        business_value="Fewer service calls; higher trust",
        risk_level=RiskLevel.HIGH,
        user_questions=["מה החשיפה שלי לדולר?", "How exposed am I to USD?"],
        agent_actions=["get_instrument_exposures"],
        success_metrics=["factual accuracy >= 4.5", "coverage shown on every answer"],
        required_information=["Holding.currency", "Holding.market_value"],
        limitations=["information only — never advice"],
        refusal_conditions=["coverage below 50%", "client identity mismatch"],
        business_approver="Head of Wealth",
        tech_owner="Data Platform",
    ))
    print(f"card ready for development: {card.ready_for_development}")

    # ------------------------------------- 2. Question design (§7, §27)
    section("2. Question Bank + Data-to-Answer Map")
    q = "מה החשיפה שלי לדולר?"
    studio.questions.add_question(QuestionSpec(
        text=q, use_case=uc, intent="currency_exposure",
        alt_phrasings=["כמה דולר יש לי בתיק?"],
        required_entities=["Portfolio", "Holding", "Currency"],
        sub_questions=[
            QuestionSpec(text="אילו תיקים נכללים?", use_case=uc),
            QuestionSpec(text="מטבע מסחר או חשיפה כלכלית?", use_case=uc),
        ],
    ))
    studio.questions.add_map(DataToAnswerMap(
        question=q, intent="currency_exposure",
        required_information=["holdings", "fx rates", "fund look-through"],
        fields=["Holding.currency", "Holding.market_value"],
        sources=["custodian"],
        calculation_rules=["exposure_v2 (look-through, hedge-adjusted)"],
        permissions=["portfolio:read"],
        validation_rules=["coverage >= 0.9", "as_of mandatory"],
        answer_structure=["direct answer", "explanation", "as_of",
                          "source", "coverage", "missing data"],
        warnings=["unclassified instruments excluded"],
    ))
    print("unmapped questions:", studio.questions.unmapped_questions())

    # -------------------------------- 3. Catalog + lineage (§25, §17.2)
    section("3. Data Catalog, Semantic Layer, Lineage")
    studio.catalog.add_source(DataSource(
        name="custodian", source_type="portfolio",
        business_owner="Operations", tech_owner="Data Platform",
        entities=["Portfolio", "Holding"], update_frequency="daily",
        quality_score=0.95, coverage=0.98,
        licensed_for_client_display=True, permissions=["portfolio:read"],
        sla="T+1 06:00",
    ))
    for name in ("currency", "market_value"):
        studio.catalog.add_field(FieldSpec(
            name=name, entity="Holding", source="custodian", coverage=0.96,
        ))
        studio.catalog.add_lineage(LineageRecord(
            field=f"Holding.{name}", source="custodian",
            as_of="2026-07-19", ingested_at="2026-07-20T06:00:00",
            steps=[LineageStep("ingestion", "nightly custodian feed"),
                   LineageStep("mapping", "ISIN -> internal instrument id")],
            selection_rule="custodian is primary for holdings",
        ))
    print(f"lineage coverage: {studio.catalog.lineage_coverage():.0%}")

    # ------------------------------------------ 4. Readiness (§28, §29)
    section("4. Data Readiness & Gap Analysis")
    assessment = studio.readiness.assess(
        uc,
        required_fields=["Holding.currency", "Holding.market_value"],
        required_sources=["custodian"],
        scores={d: 4.0 for d in READINESS_DIMENSIONS},
    )
    print(f"readiness level: {int(assessment.level)} "
          f"(blockers: {assessment.blockers or 'none'})")

    # --------------------------------------- 5. Dataset Lab (§16, §18)
    section("5. Dataset Lab — split, leakage, golden cases")
    for i in range(12):
        studio.dataset.add(DatasetExample(
            example_id=f"e{i}", text=f"exposure question variant {i}",
            use_case=uc, group_key=f"client-{i % 4}", period="2026-06",
        ))
    syn = studio.dataset.add_variation(
        "e0", "syn-no-fx", "exposure question but FX rate missing",
        origin="synthetic",
    )
    syn.expert_label = ExpertLabel(
        problem_type="missing_fx_rate", root_cause="provider gap",
        approved_by="expert A",
    )
    studio.dataset.split_by_group()
    print("leakage report:", studio.dataset.leakage_report() or "clean")
    studio.dataset.add_golden(GoldenCase(
        question=q, context={"use_case": uc}, user_type="end_client",
        permissions=["portfolio:read"],
        source_data={"exposure_usd": 0.384, "coverage": 0.96},
        correct_answer="38.4% USD exposure at 96% coverage",
        expected_tool="get_instrument_exposures",
        expected_calculation="exposure_v2",
        expected_source="custodian",
        expected_warning="unclassified instruments excluded",
        forbidden_answers=["recommendation to buy or sell"],
    ))

    # -------------------------- 6. Tools + decision gate (§31, §34, §35)
    section("6. Tool Registry + Pre-Answer Decision Gate")
    studio.tools.register(
        ToolSpec(
            name="get_instrument_exposures",
            description="Currency/sector exposure with coverage + provenance",
            use_cases=[uc], required_permission="portfolio:read",
            data_sources=["custodian"], calculation_method="exposure_v2",
            success_metric="calculation accuracy",
            refusal_conditions=["coverage < 0.5"],
        ),
        handler=lambda ctx: ToolResponse(
            status="success", as_of="2026-07-19", source="custodian",
            data={"exposure_usd": 0.384}, coverage=0.96, confidence=0.94,
            warnings=["2 instruments (4% of value) unclassified"],
            calculation_method="exposure_v2",
        ),
    )
    ctx = RequestContext(
        user_id="u-77", client_id="c-42",
        permissions=frozenset({"portfolio:read"}), request_id="req-1001",
    )
    response = studio.tools.invoke(ctx, "get_instrument_exposures")
    verdict = DecisionGate().evaluate(ctx, response, staleness_hours=26)
    print(f"tool status: {response.status}, gate decision: {verdict.decision.value}")
    denied = studio.tools.invoke(
        RequestContext(user_id="u-99", client_id="c-42",
                       permissions=frozenset(), request_id="req-1002"),
        "get_instrument_exposures",
    )
    print(f"unpermissioned call -> {denied.status}: {denied.warnings[0]}")

    # ------------------------------------------- 7. Evaluation (§37-47)
    section("7. Evaluation Lab — grades, cost, acceptance gate")
    for i, clarity in enumerate((4, 5, 4, 5, 3)):
        studio.evaluation.record(InteractionGrade(
            interaction_id=f"i{i}", use_case=uc,
            scores={"factual_accuracy": 5, "groundedness": 5,
                    "calculation_accuracy": 5, "task_completion": 5,
                    "clarity": clarity, "cost_efficiency": 4},
            response_seconds=2.1,
            cost=TransactionCost(input_tokens=900, output_tokens=350,
                                 database_queries=3),
        ))
    summary = studio.evaluation.summary(uc)
    print(f"objective score: mean={summary.mean:.2f} "
          f"ci95=[{summary.ci95_low:.2f}, {summary.ci95_high:.2f}] "
          f"failure_rate={summary.failure_rate:.0%}")
    print(f"cost per successful task: "
          f"${studio.evaluation.cost_per_successful_task(uc):.4f}")
    studio.evaluation.log_experiment(Experiment(
        experiment_id="exp-1",
        hypothesis="server-side aggregation cuts tokens without hurting accuracy",
        changed_component="context_construction", dataset="golden-v1",
        decision="accepted",
    ))
    accepted, failures = studio.evaluation.acceptance_gate(VersionCandidate(
        version="v1.1", safety_regression=False, permission_regression=False,
        calculation_regression=False, hallucination_rate_before=0.02,
        hallucination_rate_after=0.015,
        evaluated_on_representative_dataset=True, regression_tested=True,
        cost_within_budget=True, latency_approved=True,
        stable_across_runs=True, rollback_available=True, documented=True,
    ))
    print(f"version v1.1 accepted: {accepted}")

    # --------------------------- 8. Blockage intelligence (§19-§22)
    section("8. Bottleneck Intelligence — prediction + similar cases")
    history = []
    for i in range(6):
        blocked = CaseFeatures(
            case_id=f"hist-b{i}", transaction_type="buy",
            instrument_type="bond" if i % 2 else "fund",
            process_stage="instrument_identification",
            missing_field_ratio=0.6, identification_confidence=0.4,
            blockage_type="missing_instrument_mapping", delay_hours=6.5,
            process_path=["identify", "api_fail", "manual_review"],
        )
        history.append(blocked)
        studio.case_library.add(blocked)
    for i in range(6):
        history.append(CaseFeatures(
            case_id=f"hist-g{i}", transaction_type="buy",
            instrument_type="equity", process_stage="settlement",
            missing_field_ratio=0.0, identification_confidence=0.99,
        ))
    studio.blockage_predictor.fit(history)
    new_case = CaseFeatures(
        case_id="deal-9001", transaction_type="buy", instrument_type="fund",
        process_stage="instrument_identification",
        missing_field_ratio=0.55, identification_confidence=0.45,
        process_path=["identify", "api_fail"],
    )
    prediction = studio.blockage_predictor.predict(new_case, rules=studio.blockage_rules)
    print(json.dumps(prediction.to_dict(), indent=2, ensure_ascii=False))
    neighbors = studio.case_library.most_similar(new_case, limit=2)
    print("similar cases:", [(c.case_id, round(s, 2)) for c, s in neighbors])

    # -------------------------------------------- 9. Governance (§43)
    section("9. Governance — decisions, versions, audit")
    studio.governance.log_decision(DecisionRecord(
        decision_id="d-1", subject="FX source of truth",
        decision="custodian FX rate is authoritative",
        rationale="licensed, complete, T+1", decided_by="Data Owner",
        decided_at="2026-07-20",
        affected_use_cases=[uc],
    ))
    studio.governance.register_version(VersionRecord(
        version="v1.0", released_at="2026-07-01", approved_by="Head of Wealth",
    ))
    studio.governance.register_version(VersionRecord(
        version="v1.1", released_at="2026-07-20", approved_by="Head of Wealth",
    ))
    print(f"active version: {studio.governance.active_version().version} "
          f"(rollback target: {studio.governance.active_version().previous_version})")

    # ---------------------------------------- 10. Go/No-Go + dashboard
    section("10. Capability Report — Go/No-Go (§49)")
    report = studio.capability_report(uc)
    print(f"readiness level: {report.readiness_level}")
    print(f"deliverables complete: "
          f"{sum(report.deliverables.values())}/{len(report.deliverables)}")
    print(f"GO decision: {report.go}")
    if report.reasons:
        for reason in report.reasons:
            print(f"  - {reason}")

    section("Dashboard (§43)")
    dash = studio.dashboard()
    print(json.dumps(
        {k: v for k, v in dash.items() if k != "capabilities"},
        indent=2, ensure_ascii=False,
    ))


if __name__ == "__main__":
    main()
