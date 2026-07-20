"""Tests for module 11: skill registry, intent resolution, pipelines,
orchestrator constraints, and skill-gap mining."""
import pytest

from blueprint_studio.orchestration import (
    ConnectionIssue, Granularity, Intent, IntentResolver, Orchestrator,
    OrchestrationModule, Resolution, ResolutionOutcome, Skill, SkillContract,
    SkillGapMiner, SkillPipeline, SkillRegistry,
)


def composite_skill(name="portfolio_summary", intents=None, domain="portfolio",
                    invocations=10_000, successes=9_800):
    return Skill(
        name=name, description="full portfolio summary",
        granularity=Granularity.COMPOSITE, domain=domain,
        intents=intents or ["portfolio_summary"],
        contract=SkillContract(inputs={"client_id": "str"},
                               outputs={"summary": "dict"}),
        handler=lambda client_id: {"summary": {"client": client_id}},
        invocations=invocations, successes=successes,
    )


def atomic_find_file_skills():
    """The vendor-2 example: find-file split into three atomic skills."""
    identify = Skill(
        name="identify_file_type", description="ask user for file details",
        granularity=Granularity.ATOMIC, domain="files",
        contract=SkillContract(inputs={"description": "str"},
                               outputs={"file_type": "str", "keywords": "str"}),
        handler=lambda description: {
            "file_type": "docx" if "word" in description.lower() else "xlsx",
            "keywords": description,
        },
    )
    search = Skill(
        name="search_files", description="search the machine for candidates",
        granularity=Granularity.ATOMIC, domain="files",
        contract=SkillContract(inputs={"file_type": "str", "keywords": "str"},
                               outputs={"candidate": "str"}),
        handler=lambda file_type, keywords: {
            "candidate": f"/docs/report.{file_type}",
        },
    )
    verify = Skill(
        name="verify_match", description="verify candidate matches intent",
        granularity=Granularity.ATOMIC, domain="files",
        contract=SkillContract(inputs={"candidate": "str", "keywords": "str"},
                               outputs={"verified": "bool", "path": "str"}),
        handler=lambda candidate, keywords: {
            "verified": True, "path": candidate,
        },
    )
    return identify, search, verify


class TestSkillRegistry:
    def test_non_deterministic_skill_rejected(self):
        registry = SkillRegistry()
        with pytest.raises(ValueError, match="not deterministic"):
            registry.register(Skill(
                name="freeform", description="llm does it",
                granularity=Granularity.COMPOSITE, domain="x",
                contract=SkillContract(outputs={"answer": "str"}),
                deterministic=False,
            ))

    def test_skill_without_output_contract_rejected(self):
        registry = SkillRegistry()
        with pytest.raises(ValueError, match="no output contract"):
            registry.register(Skill(
                name="void", description="returns nothing",
                granularity=Granularity.ATOMIC, domain="x",
            ))

    def test_relevance_filter_hides_other_domains(self):
        """Token efficiency: Excel skills never shown to a Word task."""
        registry = SkillRegistry()
        registry.register(composite_skill("excel_tool", domain="excel"))
        registry.register(composite_skill("word_tool", domain="word"))
        assert [s.name for s in registry.relevant("word")] == ["word_tool"]

    def test_proven_threshold(self):
        registry = SkillRegistry(min_invocations=100, min_success_rate=0.95)
        registry.register(composite_skill("proven", invocations=200, successes=195))
        registry.register(composite_skill("new", invocations=5, successes=5))
        registry.register(composite_skill("flaky", invocations=200, successes=150))
        assert [s.name for s in registry.proven()] == ["proven"]


class TestIntentResolver:
    def test_missing_slots_trigger_clarification_loop(self):
        registry = SkillRegistry()
        registry.register(composite_skill())
        resolver = IntentResolver(registry)
        intent = Intent(name="portfolio_summary",
                        required_slots=["client_id", "period"],
                        slots={"client_id": "c-1"})
        resolution = resolver.resolve(intent, domain="portfolio")
        assert resolution.outcome is ResolutionOutcome.NEEDS_CLARIFICATION
        assert resolution.clarifying_questions == ["Please provide: period"]

    def test_complete_intent_matches_proven_skill(self):
        registry = SkillRegistry()
        registry.register(composite_skill())
        resolver = IntentResolver(registry)
        intent = Intent(name="portfolio_summary",
                        required_slots=["client_id"],
                        slots={"client_id": "c-1"})
        resolution = resolver.resolve(intent, domain="portfolio")
        assert resolution.outcome is ResolutionOutcome.MATCHED
        assert resolution.skill == "portfolio_summary"

    def test_unproven_skill_not_matched_in_production_mode(self):
        registry = SkillRegistry(min_invocations=100)
        registry.register(composite_skill(invocations=3, successes=3))
        resolver = IntentResolver(registry, require_proven=True)
        intent = Intent(name="portfolio_summary", slots={})
        resolution = resolver.resolve(intent, domain="portfolio")
        assert resolution.outcome is ResolutionOutcome.NO_MATCHING_SKILL

    def test_no_match_tells_user_never_raw_llm(self):
        registry = SkillRegistry()
        resolver = IntentResolver(registry)
        resolution = resolver.resolve(Intent(name="tax_advice"), domain="portfolio")
        assert resolution.outcome is ResolutionOutcome.NO_MATCHING_SKILL
        assert "not answered by a raw language model" in resolution.message
        # by construction there is no passthrough outcome at all
        assert "raw" not in [o.value for o in ResolutionOutcome]


class TestSkillPipeline:
    def registry_with_find_file(self):
        registry = SkillRegistry()
        for skill in atomic_find_file_skills():
            registry.register(skill)
        return registry

    def test_valid_pipeline_passes_connection_qa_and_runs(self):
        registry = self.registry_with_find_file()
        pipeline = SkillPipeline(
            "find_file", registry,
            ["identify_file_type", "search_files", "verify_match"],
        )
        assert pipeline.validate({"description": "str"}) == []
        result = pipeline.execute({"description": "the word file about fees"})
        assert result["verified"] is True
        assert result["path"] == "/docs/report.docx"

    def test_broken_joint_detected_before_execution(self):
        registry = self.registry_with_find_file()
        # search_files needs file_type+keywords which nothing produced
        pipeline = SkillPipeline("bad", registry, ["search_files"])
        issues = pipeline.validate({"description": "str"})
        assert any("file_type" in i.problem for i in issues)
        with pytest.raises(ValueError, match="connection QA"):
            pipeline.execute({"description": "x"})

    def test_composite_skill_rejected_in_pipeline(self):
        registry = self.registry_with_find_file()
        registry.register(composite_skill(domain="files"))
        pipeline = SkillPipeline("mixed", registry, ["portfolio_summary"])
        issues = pipeline.validate({"client_id": "str"})
        assert any("atomic skills only" in i.problem for i in issues)

    def test_contract_breach_fails_loudly_and_records_failure(self):
        registry = SkillRegistry()
        registry.register(Skill(
            name="liar", description="promises more than it returns",
            granularity=Granularity.ATOMIC, domain="files",
            contract=SkillContract(inputs={"x": "str"},
                                   outputs={"a": "str", "b": "str"}),
            handler=lambda x: {"a": "only a"},
        ))
        pipeline = SkillPipeline("p", registry, ["liar"])
        with pytest.raises(ValueError, match="broke its contract"):
            pipeline.execute({"x": "v"})
        assert registry.get("liar").invocations == 1
        assert registry.get("liar").successes == 0

    def test_success_updates_reliability_record(self):
        registry = self.registry_with_find_file()
        pipeline = SkillPipeline(
            "find_file", registry,
            ["identify_file_type", "search_files", "verify_match"],
        )
        pipeline.execute({"description": "word file"})
        assert registry.get("search_files").success_rate == 1.0


class TestOrchestrator:
    def test_planner_sees_only_relevant_domain(self):
        registry = SkillRegistry()
        for skill in atomic_find_file_skills():
            registry.register(skill)
        registry.register(composite_skill("excel_tool", domain="excel"))
        seen = {}

        def planner(goal, listing):
            seen["names"] = [s["name"] for s in listing]
            return ["identify_file_type", "search_files", "verify_match"]

        Orchestrator(registry, planner).run(
            "find the word file", "files", {"description": "word file"},
        )
        assert "excel_tool" not in seen["names"]

    def test_planner_cannot_invent_skills(self):
        registry = SkillRegistry()
        for skill in atomic_find_file_skills():
            registry.register(skill)
        orchestrator = Orchestrator(
            registry, lambda goal, listing: ["hack_the_db"],
        )
        with pytest.raises(ValueError, match="unregistered skills"):
            orchestrator.run("goal", "files", {})


class TestSkillGapMiner:
    def test_recurring_unmet_need_becomes_proposal(self):
        miner = SkillGapMiner(proposal_threshold=3)
        for i in range(3):
            miner.record_unmatched("tax_report", f"request {i}")
        proposals = miner.proposals()
        assert len(proposals) == 1
        assert proposals[0].intent == "tax_report"
        assert proposals[0].occurrences == 3

    def test_below_threshold_no_proposal(self):
        miner = SkillGapMiner(proposal_threshold=3)
        miner.record_unmatched("rare_need", "once")
        assert miner.proposals() == []
        assert miner.unmatched_counts() == {"rare_need": 1}


class TestOrchestrationModule:
    def test_unmatched_intent_feeds_gap_miner(self):
        module = OrchestrationModule(proposal_threshold=2)
        for i in range(2):
            resolution = module.handle_intent(
                Intent(name="crypto_analysis"), domain="portfolio",
                request_text=f"analyze my crypto {i}",
            )
            assert resolution.outcome is ResolutionOutcome.NO_MATCHING_SKILL
        assert module.gap_miner.proposals()[0].intent == "crypto_analysis"

    def test_stats_shape(self):
        module = OrchestrationModule()
        module.registry.register(composite_skill())
        for skill in atomic_find_file_skills():
            module.registry.register(skill)
        stats = module.stats()
        assert stats["skills"] == 4
        assert stats["composite"] == 1
        assert stats["atomic"] == 3
        assert stats["proven"] == 1
