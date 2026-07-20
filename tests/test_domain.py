"""Capability 4 tests: financial domain QA, compliance, stress testing."""
from qa_control.config import CompliancePolicy
from qa_control.contracts import Severity
from qa_control.domain import (
    ComplianceEngine,
    FinancialQA,
    Position,
    StressTester,
    default_stress_cases,
)


def balanced_portfolio():
    return [
        Position("SPY", "equity", 0.40),
        Position("TLT", "bond", 0.35),
        Position("CASH", "cash", 0.15),
        Position("GLD", "alternative", 0.10),
    ]


class TestFinancialQA:
    def test_valid_portfolio_clean(self):
        findings = FinancialQA().check_portfolio(balanced_portfolio(),
                                                 risk_profile="balanced")
        assert findings == []

    def test_weights_must_sum_to_one(self):
        bad = [Position("SPY", "equity", 0.5), Position("TLT", "bond", 0.3)]
        findings = FinancialQA().check_portfolio(bad)
        assert any(f.check == "finqa.weights_sum" for f in findings)

    def test_negative_weight_blocked(self):
        bad = [Position("SPY", "equity", 1.2), Position("TLT", "bond", -0.2)]
        findings = FinancialQA().check_portfolio(bad)
        checks = {f.check for f in findings}
        assert "finqa.negative_weight" in checks
        assert "finqa.weight_above_one" in checks

    def test_profile_mismatch_detected(self):
        aggressive = [Position("QQQ", "equity", 0.9),
                      Position("CASH", "cash", 0.1)]
        findings = FinancialQA().check_portfolio(aggressive,
                                                 risk_profile="conservative")
        assert any(f.check == "finqa.profile_mismatch" for f in findings)

    def test_risk_metric_out_of_range(self):
        findings = FinancialQA().check_risk_metrics(
            {"volatility": 4.2, "sharpe": 1.1})
        assert any(f.check == "finqa.metric_out_of_range" for f in findings)
        assert len(findings) == 1  # sharpe 1.1 is fine

    def test_empty_portfolio_blocked(self):
        findings = FinancialQA().check_portfolio([])
        assert findings[0].check == "finqa.empty_portfolio"


class TestCompliance:
    def engine(self, **kw):
        return ComplianceEngine(CompliancePolicy(**kw))

    def test_clean_output_passes(self):
        findings = self.engine().check(
            balanced_portfolio(),
            "Here is the analysis. This is not financial advice.")
        assert findings == []

    def test_concentration_breach_critical(self):
        heavy = [Position("TSLA", "equity", 0.55),
                 Position("CASH", "cash", 0.45)]
        findings = self.engine().check(heavy, "not financial advice")
        assert any(f.check == "compliance.concentration" and
                   f.severity is Severity.CRITICAL for f in findings)

    def test_restricted_asset_blocked(self):
        pf = [Position("3X-BTC", "crypto_leveraged", 0.10),
              Position("CASH", "cash", 0.90)]
        findings = self.engine().check(pf, "not financial advice")
        assert any(f.check == "compliance.restricted_asset" for f in findings)

    def test_illiquidity_limit(self):
        pf = [Position("PE-FUND", "alternative", 0.30, liquid=False),
              Position("CASH", "cash", 0.70)]
        findings = self.engine().check(pf, "not financial advice")
        assert any(f.check == "compliance.illiquidity" for f in findings)

    def test_missing_disclaimer(self):
        findings = self.engine().check(balanced_portfolio(),
                                       "buy these now, guaranteed returns!")
        assert any(f.check == "compliance.missing_disclaimer"
                   for f in findings)

    def test_breach_stats_counted(self):
        eng = self.engine()
        eng.check(balanced_portfolio(), "not financial advice")
        eng.check([Position("TSLA", "equity", 0.9),
                   Position("CASH", "cash", 0.1)], "not financial advice")
        assert eng.stats() == {"outputs_checked": 2,
                               "outputs_with_breaches": 1}


class TestStress:
    def test_graceful_entrypoint_survives(self):
        def entrypoint(text: str):
            if not text.strip():
                return {"status": "needs_input"}
            if "ignore" in text.lower():
                return {"status": "blocked"}
            return {"status": "ok", "answer": "fine"}

        cases = default_stress_cases("segment my portfolio by asset class")
        report = StressTester(entrypoint).run(cases)
        assert report.resilience_score == 1.0

    def test_crashing_entrypoint_flagged(self):
        def entrypoint(text: str):
            if not text.strip():
                raise ValueError("empty!")
            return {"status": "ok"}

        cases = default_stress_cases("segment my portfolio")
        report = StressTester(entrypoint).run(cases)
        assert report.resilience_score < 1.0
        assert any(f.check == "stress.crash" and
                   f.severity is Severity.CRITICAL for f in report.findings)

    def test_malformed_result_flagged(self):
        report = StressTester(lambda t: {"weird": True}).run(
            default_stress_cases("x"))
        assert report.resilience_score == 0.0
        assert any(f.check == "stress.ungraceful" for f in report.findings)

    def test_stress_cases_deterministic(self):
        a = default_stress_cases("segment my portfolio", seed=7)
        b = default_stress_cases("segment my portfolio", seed=7)
        assert [c.input_text for c in a] == [c.input_text for c in b]

    def test_high_velocity_burst(self):
        calls = []

        def entrypoint(text: str):
            calls.append(text)
            return {"status": "ok"}

        report = StressTester(entrypoint).high_velocity(
            ["req one", "req two"], burst=50)
        assert len(calls) == 50
        assert report.resilience_score == 1.0
