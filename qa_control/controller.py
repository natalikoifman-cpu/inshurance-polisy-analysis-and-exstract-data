"""The ControlPlane — orchestrates every capability around one agent run.

Lifecycle of a monitored run:

1. ``preflight(text)``      — security screen + the four input gates.
                              Returns a decision: run / ask the user / block.
2. ``start_run(...)``       — opens a RunContext carrying the tracer, bus
                              monitor, checkpoint manager and diagnostics that
                              the agent code is instrumented with.
3. ``guard_output(...)``    — per-step screening (security, domain QA,
                              compliance) with automatic rollback when a
                              CRITICAL violation appears.
4. ``postflight(...)``      — consistency/drift, decomposition audit,
                              sufficiency, latency/cost checks; grades the run
                              into the feedback store.
5. ``verdict()``            — the meta-question: is this system controlled and
                              reliable enough to be monitored in the first
                              place? Weighted score across all pillars.
"""
from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any, Callable

from .config import ControlConfig
from .contracts import (
    Finding,
    GateResult,
    RunStatus,
    Severity,
    Usage,
    Verdict,
)
from .domain import ComplianceEngine, FinancialQA, Position, StressReport
from .evaluation import (
    ConsistencyEvaluator,
    ConsistencyReport,
    DecompositionMonitor,
    EvidenceItem,
    SufficiencyChecker,
)
from .gates import BusinessKPI, GateRunner
from .llm_ready import PromptBundle, WorkflowSpec
from .metrics import CostTracker, Diagnostics, KPIRegistry, LatencyTracker
from .observability import AgentBusMonitor, FeedbackRecord, FeedbackStore, Route, Tracer
from .rollback import RollbackManager
from .security import SecurityGuard


@dataclass
class PreflightDecision:
    action: str                      # "run" | "ask_user" | "block"
    gate_results: list[GateResult]
    workflow: WorkflowSpec | None
    params: dict[str, Any]
    questions: list[str]
    findings: list[Finding]

    def to_dict(self) -> dict[str, Any]:
        return {
            "action": self.action,
            "workflow": self.workflow.name if self.workflow else None,
            "params": self.params,
            "questions": list(self.questions),
            "gates": [g.to_dict() for g in self.gate_results],
            "findings": [f.to_dict() for f in self.findings],
        }


@dataclass
class RunContext:
    run_id: str
    workflow: WorkflowSpec
    params: dict[str, Any]
    tracer: Tracer
    bus: AgentBusMonitor
    rollback: RollbackManager
    diagnostics: Diagnostics
    decomposition: DecompositionMonitor
    status: RunStatus = RunStatus.RUNNING
    findings: list[Finding] = field(default_factory=list)
    started_at: float = 0.0
    ended_at: float = 0.0


class ControlPlane:
    def __init__(self, config: ControlConfig,
                 workflows: list[WorkflowSpec],
                 kpis: list[BusinessKPI],
                 routes: list[Route],
                 feedback_path: str | None = None,
                 clock: Callable[[], float] = time.time) -> None:
        self.config = config
        self.clock = clock
        self.workflows = workflows
        self.gates = GateRunner(workflows, kpis, config.gates)
        self.security = SecurityGuard(config.security)
        self.financial_qa = FinancialQA()
        self.compliance = ComplianceEngine(config.compliance)
        self.consistency = ConsistencyEvaluator(config.consistency)
        self.latency = LatencyTracker(config.latency)
        self.cost = CostTracker(config.cost)
        self.feedback = FeedbackStore(feedback_path)
        self.kpi_registry = KPIRegistry()
        self._routes = routes
        self._runs: list[RunContext] = []
        self._run_counter = 0
        self._stress_reports: list[StressReport] = []
        self._gate_history: list[GateResult] = []

    # ------------------------------------------------------------------
    # 1. Pre-flight
    # ------------------------------------------------------------------
    def preflight(self, text: str, bundles: list[PromptBundle],
                  provided_params: dict[str, Any] | None = None,
                  rounds_used: int = 0) -> PreflightDecision:
        # security first: injected input never reaches the gates' matcher
        sec = self.security.screen_input(text, source="user")
        if not sec.allowed:
            return PreflightDecision(
                action="block", gate_results=[], workflow=None, params={},
                questions=[], findings=sec.findings)

        results, workflow = self.gates.preflight(
            text, bundles, provided_params, rounds_used)
        self._gate_history.extend(results)
        findings = [f for r in results for f in r.findings]
        last = results[-1]
        params = getattr(last, "filled_params", None) or {}
        for r in results:
            params = getattr(r, "filled_params", params) or params

        if all(r.passed for r in results):
            return PreflightDecision("run", results, workflow, params, [],
                                     findings)
        if last.questions:
            return PreflightDecision("ask_user", results, workflow, params,
                                     last.questions, findings)
        return PreflightDecision("block", results, workflow, params, [],
                                 findings)

    # ------------------------------------------------------------------
    # 2. Instrumented execution
    # ------------------------------------------------------------------
    def start_run(self, workflow: WorkflowSpec,
                  params: dict[str, Any]) -> RunContext:
        self._run_counter += 1
        run_id = f"run-{self._run_counter:04d}"
        ctx = RunContext(
            run_id=run_id, workflow=workflow, params=params,
            tracer=Tracer(run_id=run_id, clock=self.clock),
            bus=AgentBusMonitor(self._routes,
                                self.config.security.max_payload_chars,
                                clock=self.clock),
            rollback=RollbackManager(clock=self.clock),
            diagnostics=Diagnostics(clock=self.clock),
            decomposition=DecompositionMonitor(workflow.description),
            started_at=self.clock(),
        )
        self._runs.append(ctx)
        return ctx

    # ------------------------------------------------------------------
    # 3. Per-step output guarding with automatic revert
    # ------------------------------------------------------------------
    def guard_output(self, ctx: RunContext, agent: str, text: str = "",
                     positions: list[Position] | None = None,
                     risk_metrics: dict[str, float] | None = None,
                     risk_profile: str | None = None) -> dict[str, Any]:
        """Screen one agent output. On a CRITICAL finding: auto-rollback.

        Returns {"allowed": bool, "reverted_state": dict|None,
                 "findings": [...]}.
        """
        findings: list[Finding] = []
        if text:
            decision = self.security.screen_output(text, agent=agent)
            findings.extend(decision.findings)
        if positions is not None:
            findings.extend(self.financial_qa.check_portfolio(
                positions, risk_profile=risk_profile))
            findings.extend(self.compliance.check(positions, text))
        if risk_metrics:
            findings.extend(self.financial_qa.check_risk_metrics(risk_metrics))

        ctx.findings.extend(findings)
        critical = [f for f in findings if f.severity is Severity.CRITICAL]
        reverted_state = None
        if critical:
            reasons = "; ".join(f.message for f in critical[:3])
            reverted_state, rb_finding = ctx.rollback.revert_to_last_safe(
                ctx.run_id, reasons)
            ctx.findings.append(rb_finding)
            # only a rollback that actually restored state contains the breach
            ctx.status = (RunStatus.ROLLED_BACK if reverted_state is not None
                          else RunStatus.FAILED)
            ctx.tracer.event("auto_rollback", reason=reasons,
                             restored=reverted_state is not None)
        return {"allowed": not critical, "reverted_state": reverted_state,
                "findings": findings}

    # ------------------------------------------------------------------
    # 4. Post-flight evaluation
    # ------------------------------------------------------------------
    def postflight(self, ctx: RunContext,
                   iteration_texts: list[str],
                   iteration_numerics: list[dict[str, float]] | None = None,
                   evidence: list[EvidenceItem] | None = None,
                   final_answer: str = "",
                   synthesized_refs: list[str] | None = None,
                   sufficiency: SufficiencyChecker | None = None,
                   ) -> ConsistencyReport:
        ctx.ended_at = self.clock()

        # consistency & drift
        report = self.consistency.evaluate(
            ctx.workflow.name, iteration_texts, iteration_numerics)
        ctx.findings.extend(report.findings)
        for f in report.findings:
            if f.check == "consistency.drift":
                ctx.diagnostics.report_drift(f.message, ctx.started_at)

        # decomposition audit
        if synthesized_refs is not None:
            decomp = ctx.decomposition.audit_synthesis(synthesized_refs)
            ctx.findings.extend(decomp.findings)

        # information sufficiency
        if sufficiency is not None and evidence is not None:
            _, suff_findings = sufficiency.check(final_answer, evidence)
            ctx.findings.extend(suff_findings)

        # bus protocol violations become run findings
        ctx.findings.extend(ctx.bus.violations)
        # diagnostics incidents too
        ctx.findings.extend(ctx.diagnostics.findings())

        # latency + cost roll-up from the tracer
        for sp in ctx.tracer.spans:
            self.latency.record(sp.name, sp.duration_ms)
            ctx.diagnostics.observe_latency(sp.duration_ms,
                                            occurred_at=sp.end or sp.start)
        duration_s = max(ctx.ended_at - ctx.started_at, 1e-9)
        self.cost.record_task(ctx.run_id, ctx.tracer.total_usage(),
                              duration_s)

        if ctx.status is RunStatus.RUNNING:
            has_blocking = any(
                f.severity in (Severity.BLOCKING, Severity.CRITICAL)
                for f in ctx.findings)
            ctx.status = RunStatus.FAILED if has_blocking else RunStatus.COMPLETED

        # feedback loop: grade the run for continuous training
        grade = "good" if ctx.status is RunStatus.COMPLETED else "bad"
        self.feedback.add(FeedbackRecord(
            run_id=ctx.run_id, workflow=ctx.workflow.name,
            input_text=str(ctx.params), output_text=final_answer,
            grade=grade,
            reasons=[f.message for f in ctx.findings
                     if f.severity in (Severity.BLOCKING, Severity.CRITICAL)][:5]))
        return report

    def record_stress_report(self, report: StressReport) -> None:
        self._stress_reports.append(report)

    # ------------------------------------------------------------------
    # 5. The meta-verdict
    # ------------------------------------------------------------------
    def _pillar_scores(self) -> dict[str, float]:
        # gates pillar: average score of all executed gates
        gate_scores = [g.score for g in self._gate_history]
        gates = sum(gate_scores) / len(gate_scores) if gate_scores else 0.0

        # consistency pillar: mean consistency across completed runs, penalized
        # by drift incidents
        cons_scores = []
        drift_incidents = 0
        for ctx in self._runs:
            drift_incidents += sum(
                1 for i in ctx.diagnostics.incidents if i.kind == "drift")
        for ctx in self._runs:
            run_cons = [f for f in ctx.findings
                        if f.check.startswith("consistency.")]
            cons_scores.append(0.0 if run_cons else 1.0)
        consistency = (sum(cons_scores) / len(cons_scores)) if cons_scores else 1.0
        if drift_incidents:
            consistency *= 0.5

        # security pillar: breach prevention rate, zero if any threat got through
        sec_stats = self.security.breach_stats()
        security = float(sec_stats["breach_prevention_rate"])
        bus_violations = sum(len(ctx.bus.violations) for ctx in self._runs)
        delivered_violations = 0  # bus blocks violating messages by design
        if bus_violations and delivered_violations:
            security = 0.0

        # compliance pillar: runs without compliance/finqa criticals
        comp_clean = []
        for ctx in self._runs:
            bad = [f for f in ctx.findings
                   if f.check.startswith(("compliance.", "finqa."))
                   and f.severity in (Severity.BLOCKING, Severity.CRITICAL)]
            rolled_back = ctx.status is RunStatus.ROLLED_BACK
            # a breach that was caught AND reverted still counts as contained
            comp_clean.append(1.0 if not bad else (0.6 if rolled_back else 0.0))
        compliance = (sum(comp_clean) / len(comp_clean)) if comp_clean else 1.0

        # operations pillar: latency + cost within thresholds, few incidents,
        # rollbacks succeed, stress resilience
        ops_findings = len(self.latency.check()) + len(self.cost.check())
        ops = 1.0 if ops_findings == 0 else max(0.0, 1.0 - 0.25 * ops_findings)
        rb_rates = [ctx.rollback.stats()["rollback_success_rate"]
                    for ctx in self._runs if ctx.rollback.events]
        if rb_rates:
            ops = min(ops, sum(rb_rates) / len(rb_rates))
        if self._stress_reports:
            resilience = sum(r.resilience_score for r in self._stress_reports) \
                / len(self._stress_reports)
            ops = (ops + resilience) / 2

        return {"gates": gates, "consistency": consistency,
                "security": security, "compliance": compliance,
                "operations": ops}

    def collect_kpis(self) -> dict[str, Any]:
        reg = self.kpi_registry
        reg.merge("latency", self.latency.stats())
        reg.merge("cost", self.cost.overall())
        reg.merge("security", self.security.breach_stats())
        reg.merge("compliance", self.compliance.stats())

        mttds = [ctx.diagnostics.mttd_seconds() for ctx in self._runs]
        mttds = [m for m in mttds if m is not None]
        reg.set("resilience.mttd_seconds",
                round(sum(mttds) / len(mttds), 3) if mttds else None)

        rb_attempted = sum(len(ctx.rollback.events) for ctx in self._runs)
        rb_succeeded = sum(
            sum(1 for e in ctx.rollback.events if e.succeeded)
            for ctx in self._runs)
        reg.set("resilience.rollbacks_attempted", rb_attempted)
        reg.set("resilience.rollback_success_rate",
                round(rb_succeeded / rb_attempted, 4) if rb_attempted else 1.0)

        if self._stress_reports:
            reg.set("resilience.stress_score", round(
                sum(r.resilience_score for r in self._stress_reports)
                / len(self._stress_reports), 4))

        statuses = [ctx.status.value for ctx in self._runs]
        reg.set("runs.total", len(self._runs))
        reg.set("runs.completed",
                statuses.count(RunStatus.COMPLETED.value))
        reg.set("runs.rolled_back",
                statuses.count(RunStatus.ROLLED_BACK.value))
        reg.set("runs.failed", statuses.count(RunStatus.FAILED.value))
        for wf in self.workflows:
            stats = self.feedback.improvement_stats(wf.name)
            if stats["total"]:
                reg.set(f"feedback.{wf.name}.good_rate", stats["good_rate"])
        return reg.to_dict()

    def verdict(self) -> Verdict:
        """Answer the meta-question with a weighted reliability score."""
        pillars = self._pillar_scores()
        w = self.config.verdict_weights
        score = (w.gates * pillars["gates"]
                 + w.consistency * pillars["consistency"]
                 + w.security * pillars["security"]
                 + w.compliance * pillars["compliance"]
                 + w.operations * pillars["operations"])

        reasons = [
            f"{name} pillar score {value:.2f}"
            for name, value in sorted(pillars.items())
        ]
        controlled = score >= self.config.min_reliability_for_controlled
        # hard vetoes: an uncontained critical anywhere means not controlled
        uncontained = [
            f for ctx in self._runs for f in ctx.findings
            if f.severity is Severity.CRITICAL
            and ctx.status not in (RunStatus.ROLLED_BACK, RunStatus.BLOCKED)
            and not f.check.startswith("rollback.performed")
        ]
        if uncontained:
            controlled = False
            reasons.append(
                f"VETO: {len(uncontained)} critical finding(s) were not "
                "contained by rollback or blocking")

        kpis = self.collect_kpis()
        kpis["pillars"] = {k: round(v, 4) for k, v in pillars.items()}
        return Verdict(controlled=controlled, reliability_score=score,
                       reasons=reasons, kpis=kpis)
