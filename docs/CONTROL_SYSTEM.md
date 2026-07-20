# Dynamic QA & Observability Control System for Financial AI Agents

A runtime control plane that evaluates, monitors and safeguards a multi-agent
LLM system for financial data processing (portfolio segmentation, asset
allocation, securities analysis) — and answers the meta-question:

> **"Is this system controlled and reliable enough to be monitored in the
> first place?"**

The implementation is a dependency-free Python package (`qa_control/`,
stdlib only, Python 3.11+) designed to wrap *any* agent framework: agents are
plain callables that run inside the control plane's instrumentation. A full
simulated deployment lives in `examples/demo_control_plane.py`.

## Quick start

```bash
python3 -m pytest tests/ -q            # 91 tests
python3 examples/demo_control_plane.py # end-to-end demo, prints the verdict
```

The demo writes `examples/out/control_report.json` (scenario outcomes + KPI
sheet + verdict) and one JSONL trace file per run. Everything is
deterministic — two invocations produce byte-identical output.

## Architecture

```
                       ┌─────────────────────────────────────────────┐
 user input ──────────►│ 1. PRE-FLIGHT                               │
                       │  SecurityGuard.screen_input (injection)     │
                       │  IntentGate ─ intent & context sufficiency  │
                       │  ParameterGate ─ intake loop until complete │
                       │  ReadinessGate ─ LLM-READY checklist        │
                       │  AlignmentGate ─ business KPI alignment     │
                       └───────┬─────────────────────────────────────┘
                run / ask_user / block
                       ┌───────▼─────────────────────────────────────┐
                       │ 2. INSTRUMENTED EXECUTION (RunContext)      │
                       │  Tracer ─ spans, events, usage, JSONL logs  │
                       │  AgentBusMonitor ─ inter-agent protocol     │
                       │  DecompositionMonitor ─ sub-task graph      │
                       │  RollbackManager ─ safe checkpoints         │
                       │  guard_output per step:                     │
                       │    SecurityGuard.screen_output (leaks)      │
                       │    FinancialQA + ComplianceEngine           │
                       │    CRITICAL ⇒ automatic revert              │
                       └───────┬─────────────────────────────────────┘
                       ┌───────▼─────────────────────────────────────┐
                       │ 3. POST-FLIGHT                              │
                       │  ConsistencyEvaluator ─ variance/drift      │
                       │  Decomposition audit ─ synthesis coverage   │
                       │  SufficiencyChecker ─ evidence-backed answer│
                       │  Latency/Cost trackers, Diagnostics (MTTD)  │
                       │  FeedbackStore ─ graded run → few-shot      │
                       └───────┬─────────────────────────────────────┘
                       ┌───────▼─────────────────────────────────────┐
                       │ 4. META-VERDICT                             │
                       │  5 weighted pillars + hard vetoes           │
                       │  controlled: bool, reliability: 0..1, KPIs  │
                       └─────────────────────────────────────────────┘
```

## Capability map (requirements → modules)

| Requirement | Module | Key classes |
|---|---|---|
| Intent & context sufficiency | `gates.py` | `IntentGate` |
| Parameter mapping + intake loop | `gates.py`, `llm_ready.py` | `ParameterGate`, `ParameterIntake` |
| Prompt & few-shot readiness | `llm_ready.py` | `PromptBundle`, `check_prompt_bundle`, `ModelStagePolicy` |
| Business alignment | `gates.py` | `AlignmentGate`, `BusinessKPI` |
| Task decomposition & orchestration | `evaluation.py` | `DecompositionMonitor` (graph validation, synthesis audit) |
| Consistency & drift detection | `evaluation.py` | `ConsistencyEvaluator` (pairwise similarity, std-dev/CV, baseline drift) |
| Information sufficiency | `evaluation.py` | `SufficiencyChecker` (evidence count, topic coverage, number traceability) |
| Comprehensive logging | `observability.py` | `Tracer` (nested spans, events, per-span usage, JSONL export) |
| Multi-agent communication monitoring | `observability.py` | `AgentBusMonitor` (declared routes, kinds, payload limits) |
| Continuous training component | `observability.py` | `FeedbackStore` (graded runs → curated few-shot examples) |
| Security & guardrails | `security.py` | `SecurityGuard` (injection, leak, action authorization) |
| Domain-specific QA | `domain.py` | `FinancialQA` (weights, risk-profile fit, metric sanity) |
| Stress testing & simulation | `domain.py` | `StressTester`, `default_stress_cases`, `high_velocity` |
| Policy & regulatory compliance | `domain.py` | `ComplianceEngine` (concentration, restricted assets, liquidity, disclaimers) |
| Efficiency & cost tracking | `metrics.py` | `CostTracker` (tokens, cost/task, tokens/sec) |
| Latency & speed | `metrics.py` | `LatencyTracker` (P50/P95/P99) |
| Bug & drift diagnostics | `metrics.py` | `Diagnostics` (error streaks, silent outputs, latency degradation, MTTD) |
| Revert mechanisms | `rollback.py` | `RollbackManager` (deep-copied safe checkpoints, auto-revert) |
| Meta-verdict + KPIs | `controller.py` | `ControlPlane.verdict()` |
| Skill catalog & intent routing (no LLM passthrough) | `skills.py` | `SkillRegistry`, `RouteDecision` |
| Skill reliability gating (proven/candidate) | `skills.py` | `ReliabilityLedger`, `SkillStats` |
| Atomic-skill composition with junction QA | `skills.py` | `SkillComposer`, `PlanStep`, `SkillContract` |
| Learning new skills from interactions | `skills.py` | `SkillMiner`, `SkillProposal` |

## The meta-verdict

`ControlPlane.verdict()` computes five pillar scores — **gates**,
**consistency**, **security**, **compliance**, **operations** — and combines
them with configurable weights (`VerdictWeights`). Two mechanisms decide
"controlled":

1. **Weighted reliability score** must reach
   `min_reliability_for_controlled` (default 0.75).
2. **Hard veto**: any CRITICAL finding *not contained* by a rollback or a
   block makes the system not-controlled regardless of the score. A breach
   that was caught and reverted counts as contained — that is the system
   working.

### KPI sheet (emitted with every verdict)

- **Accuracy & reliability**: consistency scores, numeric CV, gate scores,
  pillar scores.
- **Operational**: `latency.p50/p95/p99_ms`, `cost.cost_per_task_usd`,
  `tokens_per_second` per task.
- **Resilience**: `resilience.mttd_seconds` (mean time to detect),
  `resilience.rollback_success_rate`, `resilience.stress_score`,
  `security.breach_prevention_rate`.

## Skill orchestration — the LLM is a conductor, never a performer

`skills.py` implements both granularity philosophies of the
big-LEGO vs. small-LEGO debate, and their shared conclusion: the model only
*orchestrates* deterministic, proven code components — it never performs the
substantive action itself.

**Proven composite skills ("big LEGO bricks")**

- A fixed `SkillRegistry` catalog. User intent is matched deterministically
  to a registered skill; each skill can require its own clarifying questions
  before executing.
- **No LLM passthrough, by construction**: `route()` has exactly three
  outcomes — execute a registered skill, ask clarifying questions, or an
  explicit *"no registered skill matches; the request was NOT forwarded to a
  general model"*. There is no code path that hands the raw request to a
  model.
- **Trust is earned with evidence**: a skill starts as `candidate` and is
  only routable in production after `ReliabilityLedger` records
  `min_executions_for_proven` runs at `min_success_rate` (defaults: 50 runs
  at 98%). A proven skill whose live success rate degrades below
  `demote_below_rate` is automatically demoted back to candidate.

**Atomic-skill composition ("small LEGO bricks")**

- Complex tasks are expressed as a `PlanStep` list over small skills, each
  with a typed `SkillContract` (inputs/outputs).
- `SkillComposer.validate_plan` is the QA for the connections: a step that
  references an unregistered skill (i.e., a free-form model action) is a
  CRITICAL violation; unproven skills, unbound required inputs, missing
  junction sources and type-mismatched junctions are BLOCKING.
- At runtime every junction is validated **again** on the real payloads;
  a contract violation stops the plan and is recorded against the offending
  skill in the ledger.
- Example in the demo — "fetch account holdings" decomposed exactly like the
  find-a-file example: `identify_account → fetch_holdings → verify_match`,
  each atomic, each independently testable.

**Learning new skills from user interactions**

- Every request that no skill adequately matches feeds `SkillMiner`, which
  clusters unmet needs deterministically by shared keywords; a recurring
  cluster becomes a `SkillProposal` (visible in the KPI sheet as
  `skills.proposals_pending`). Proposals enter the catalog as candidates and
  must still earn `proven` status through the ledger before serving users.

## Design rules baked in

- **LLM last, code first.** Extraction, enrichment (morphological
  normalization), routing, validation are deterministic code. The
  `ReadinessGate` refuses any model call whose bundle lacks a filled output
  example, correct *and* incorrect few-shot examples, negative guidance, a
  minimal context slice, or the right model tier for the stage
  (small → extraction, research-grade → data search, strong → final answer).
- **Never proceed on partial data.** The intake loop asks targeted questions
  until the parameter JSON is complete, bounded by `max_intake_rounds`.
- **Deny by default.** Undeclared bus routes and blocked financial actions
  (trades, transfers) never execute; violating messages are not delivered.
- **Reproducibility.** All timestamps flow from an injectable clock; stress
  cases use a fixed seed; JSONL exports are sorted and overwritten, never
  appended. The demo is byte-identical across runs.
- **Config over code.** Every threshold lives in `ControlConfig`
  (JSON-loadable) — retune per deployment without touching logic.

## Plugging in real agents

1. Describe each workflow as a `WorkflowSpec` (params with paraphrases,
   questions, few-shot examples; objective keywords; KPIs served).
2. Register `BusinessKPI`s and allowed `Route`s.
3. Build `PromptBundle`s for each stage — optionally pulling curated few-shot
   examples from `FeedbackStore.curated_few_shot()` so the system improves
   from its own graded history.
4. Call `preflight()`; on `"run"`, open `start_run()`, execute your agents
   inside `ctx.tracer.span(...)`, send inter-agent messages through
   `ctx.bus.send(...)`, checkpoint before risky steps, and screen every
   output with `guard_output()`.
5. Call `postflight()` with the iteration outputs and evidence, then
   `verdict()` on whatever cadence your monitoring needs.

A real deployment can swap the deterministic extractors for small-model calls
(e.g. a compiled DSPy module) without changing any contract — the gates,
guards and KPIs operate on the same dataclasses either way.
