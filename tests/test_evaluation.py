"""Capability 2 tests: evaluation & cognitive control."""
from qa_control.config import ConsistencyThresholds
from qa_control.contracts import Severity
from qa_control.evaluation import (
    ConsistencyEvaluator,
    DecompositionMonitor,
    EvidenceItem,
    SubTask,
    SufficiencyChecker,
    cosine_similarity,
)


def make_monitor():
    m = DecompositionMonitor("build a client risk report")
    m.register(SubTask("t1", "load portfolio", "data_agent"))
    m.register(SubTask("t2", "compute risk", "risk_agent", depends_on=["t1"]))
    m.register(SubTask("t3", "write summary", "writer_agent", depends_on=["t2"]))
    return m


class TestDecomposition:
    def test_complete_synthesis(self):
        m = make_monitor()
        for tid, ref in [("t1", "r1"), ("t2", "r2"), ("t3", "r3")]:
            m.mark_done(tid, ref)
        report = m.audit_synthesis(["r1", "r2", "r3"])
        assert report.complete and report.coverage == 1.0

    def test_dropped_output_detected(self):
        m = make_monitor()
        for tid, ref in [("t1", "r1"), ("t2", "r2"), ("t3", "r3")]:
            m.mark_done(tid, ref)
        report = m.audit_synthesis(["r1", "r3"])  # r2 was ignored
        assert not report.complete
        assert any(f.check == "decomposition.dropped_output"
                   for f in report.findings)

    def test_failed_subtask_is_critical(self):
        m = make_monitor()
        m.mark_done("t1", "r1")
        m.mark_failed("t2")
        m.mark_done("t3", "r3")
        report = m.audit_synthesis(["r1", "r3"])
        assert any(f.severity is Severity.CRITICAL for f in report.findings)

    def test_cycle_detected(self):
        m = DecompositionMonitor("goal")
        m.register(SubTask("a", "x", "ag", depends_on=["b"]))
        m.register(SubTask("b", "y", "ag", depends_on=["a"]))
        findings = m.validate_graph()
        assert any(f.check == "decomposition.cycle" for f in findings)

    def test_dangling_dependency_detected(self):
        m = DecompositionMonitor("goal")
        m.register(SubTask("a", "x", "ag", depends_on=["ghost"]))
        findings = m.validate_graph()
        assert any(f.check == "decomposition.dangling_dep" for f in findings)


class TestConsistency:
    def test_identical_outputs_are_consistent(self):
        ev = ConsistencyEvaluator(ConsistencyThresholds())
        text = "equity 60 percent bonds 40 percent expected return 5.2"
        report = ev.evaluate("k", [text, text, text])
        assert report.consistency_score == 1.0
        assert report.stable

    def test_divergent_outputs_flagged(self):
        ev = ConsistencyEvaluator(ConsistencyThresholds())
        report = ev.evaluate("k", [
            "equity heavy allocation with growth stocks",
            "the weather in tel aviv is sunny today entirely",
            "quantum computing hardware roadmap discussion",
        ])
        assert not report.stable
        assert any(f.check == "consistency.low" for f in report.findings)

    def test_numeric_variance_flagged(self):
        ev = ConsistencyEvaluator(ConsistencyThresholds(max_numeric_cv=0.05))
        same_text = "allocation report"
        report = ev.evaluate("k", [same_text] * 3, numerics=[
            {"expected_return": 5.0}, {"expected_return": 9.0},
            {"expected_return": 2.0}])
        assert any(f.check == "consistency.numeric_variance"
                   for f in report.findings)
        assert report.numeric_std["expected_return"] > 0

    def test_drift_against_baseline(self):
        th = ConsistencyThresholds(min_consistency_score=0.0)
        ev = ConsistencyEvaluator(th)
        ev.set_baseline("k", "diversified portfolio with equity and bond mix")
        report = ev.evaluate("k", ["recipe for chocolate cake with sprinkles"])
        assert report.drift_vs_baseline is not None
        assert any(f.check == "consistency.drift" and
                   f.severity is Severity.CRITICAL for f in report.findings)

    def test_cosine_similarity_bounds(self):
        assert cosine_similarity("alpha beta gamma", "alpha beta gamma") == 1.0
        assert cosine_similarity("alpha beta", "gamma delta") == 0.0


class TestSufficiency:
    def test_sufficient_answer_passes(self):
        checker = SufficiencyChecker(min_items=2, required_topics=["risk"])
        evidence = [
            EvidenceItem("holdings.xlsx", "portfolio risk score 42 equity 60"),
            EvidenceItem("model.calc", "projected value 42 under risk stress"),
        ]
        score, findings = checker.check("risk score is 42", evidence)
        assert score == 1.0 and findings == []

    def test_unsupported_number_is_critical(self):
        checker = SufficiencyChecker(min_items=1)
        evidence = [EvidenceItem("doc", "equity share is 60 percent")]
        _, findings = checker.check("your fee is 999 shekels", evidence)
        assert any(f.check == "sufficiency.unsupported_numbers" and
                   f.severity is Severity.CRITICAL for f in findings)

    def test_sentence_period_not_part_of_number(self):
        # regression: "cash 25." must extract 25, not "25."
        checker = SufficiencyChecker(min_items=1)
        evidence = [EvidenceItem("doc", "cash allocation 25 percent")]
        _, findings = checker.check("we hold cash 25.", evidence)
        assert not any(f.check == "sufficiency.unsupported_numbers"
                       for f in findings)

    def test_uncovered_topic_flagged(self):
        checker = SufficiencyChecker(min_items=1, required_topics=["liquidity"])
        evidence = [EvidenceItem("doc", "equity share is 60")]
        _, findings = checker.check("answer 60", evidence)
        assert any(f.check == "sufficiency.topic_uncovered" for f in findings)
