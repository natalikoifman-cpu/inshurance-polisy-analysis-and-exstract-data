# Financial Agent Blueprint, Data Readiness & Optimization Studio

A control center for turning a financial data platform into a trustworthy,
customer-facing agent. The Studio is **not the agent itself** — it is the
planning, research, control and optimization system that accompanies the
whole lifecycle:

```
business need → client questions → required information → sources & fields
→ quality & sufficiency → gaps → semantic layer → APIs & tools → dataset
→ agent → evaluation → optimization → monitoring & continuous improvement
```

Package: `blueprint_studio/` · Demo: `python3 examples/demo_blueprint_studio.py`
· Tests: `python3 -m pytest tests/ -q`

## The three foundational questions

| Question | Where it is answered |
|---|---|
| **What is the optimization problem?** | `evaluation_lab.py` — hard constraints vs. soft metrics, per-use-case weighted objective, unit of optimization (message / conversation / task / population statistics) |
| **What data do we use?** | `catalog.py` + `dataset_lab.py` — catalog, lineage, group/time-based Train-Validation-Test splits, leakage checks, expert bootstrapping, golden dataset, active learning |
| **How do we build the system?** | `architecture.py` — focused tools with mandatory response envelopes, permissions enforced in code, deterministic pre-answer decision gate, layered reference architecture |

## The nine modules

| # | Module | File | Spec anchors |
|---|---|---|---|
| 1 | Business Discovery | `discovery.py` | §6, §48 — Use Case Cards, discovery interview, Definition of Ready |
| 2 | Question & Use Case Design | `questions.py` | §7, §27 — question trees, Data-to-Answer maps ("a field named `currency` is not enough") |
| 3 | Data Catalog & Semantic Mapping | `catalog.py` | §17.2, §25, §26 — sources, fields, semantic entities, field-level lineage |
| 4 | Data Readiness & Gap Analysis | `readiness.py` | §28, §29 — seven dimensions, 0-5 scale, mandatory blockers, gap matrix |
| 5 | Dataset & Expert Knowledge Lab | `dataset_lab.py` | §15-§18, §38, §42 — splits, leakage report, synthetic-with-approval, golden cases, active learning queue |
| 6 | Agent Architecture & Tool Design | `architecture.py` | §31-§34, §40 — tool registry, permission gate, decision gate, vector/LLM usage policy |
| 7 | Evaluation & Optimization Lab | `evaluation_lab.py` | §8, §9, §37, §39, §41, §46, §47 — grading, objective function, transaction cost, statistics, experiments, acceptance gate |
| 8 | Bottleneck & Blockage Intelligence | `bottleneck.py` | §19-§22, §24 — taxonomy, rules engine, explainable frequency predictor, structured/process similarity |
| 9 | Governance & Production Monitoring | `governance.py` | §43, §47, §49 — decision log, audit trail, version registry with rollback |
| 10 | Memory & Salience Intelligence | `memory.py` | SurprisalGate (mnemos), salient extraction with ADD/UPDATE/NOOP (Mem0), Information-Theoretic Score retrieval (Memanto) |

`studio.py` ties them together: per-capability deliverables checklist
(the 20 artifacts of §49), the Go/No-Go verdict, and the §43 dashboard.
`contracts.py` holds the shared envelopes: `Provenance`, `ToolResponse`
(§31), `ClientAnswer` (§35), `Gap` (§29).

## Design principles enforced in code

- **Never connect the LLM directly to the data** (§4.1). The only path to
  data is `ToolRegistry.invoke`, which checks permissions *before* the
  handler runs and returns the mandatory envelope (source, `as_of`,
  coverage, confidence, warnings, `request_id`).
- **Facts vs. phrasing** (§4.2, §40). Calculations, permissions, source
  selection, validation and refusal conditions are deterministic Python.
  The LLM's role (intent, phrasing, explanation) sits *after*
  `DecisionGate.evaluate`, which can refuse, ask clarification, escalate
  to a human, or open a data-quality task before any text is generated.
- **Every answer has a source** (§4.3). `ClientAnswer.is_complete()`
  fails without `as_of` + source; `LineageRecord.is_traceable()` gates
  readiness; missing lineage is a mandatory blocker.
- **No answer without permission** (§4.4). `PermissionGate` denies
  unauthenticated users, unknown tools (deny-by-default) and missing
  permissions — no prompt involved.
- **Every capability is measured** (§4.5). `ToolSpec.capability_checklist`
  refuses to register a tool without use case, source, success metric,
  refusal conditions and a permission requirement.
- **Hard constraints beat soft metrics** (§9). Any of the nine §9.1
  violations zeroes `InteractionGrade.objective_score`, regardless of
  clarity or speed.
- **No leakage, group-first splits** (§16-§17). Splits are hash-based on
  the group key (conversation/client/deal); variations and synthetics
  inherit their seed's group; `split_by_time` sends the newest period to
  test with group integrity winning over period; `leakage_report()`
  flags cross-split groups and unapproved synthetic data.
- **Statistics, not anecdotes** (§41). `summarize()` gives mean, median,
  std, failure rate and a 95% CI; `ClassificationStats` gives
  precision/recall/F1/FPR for rare-event blockage prediction.
- **One change per experiment** (§46), and a version ships only through
  the eleven-point acceptance gate (§47) with a rollback path
  (`GovernanceModule.rollback`).

## Blockage intelligence (§19-§22)

`BLOCKAGE_TAXONOMY` covers the four families (data / process / technical
/ business). `BlockageRules` handles deterministic blockers (missing
mandatory fields, confidence under the floor, stale data, approval
required) — when a rule fires, the prediction is forced to
`request_manual_validation`. `BlockagePredictor` is a transparent
Laplace-smoothed frequency model over discretized features: it outputs
probability, most likely type/stage, expected delay, similar-case count
and a per-feature explanation, exactly the §21 output shape. Similarity
combines structured feature overlap with process-path similarity, so a
bond and a fund blocked for the same root cause ("internal id exists but
no provider mapping") rank as neighbors (§22).

## Memory & salience (module 10)

Traditional agent memory is a passive recorder — every turn lands in a
database ("store everything"). `memory.py` filters at ingestion and
ranks by information value at retrieval:

- **SurprisalGate** (mnemos-style). A predictive model estimates the
  user's next intent; the actual turn is scored by its surprisal,
  `-log2 p(actual | context)`. Only turns above the threshold (new,
  unexpected information — the essential) are stored; predictable,
  routine turns (the incidental) are filtered out. Corrections,
  complaints and permission changes are always stored regardless of
  surprisal. The predictor is pluggable — an LLM in production, a
  deterministic Laplace-smoothed intent-transition Markov model
  (`IntentTransitionModel`) in tests — while the threshold and the
  store/skip decision stay in code. `filter_rate()` is the
  anti-store-everything KPI, surfaced on the dashboard.
- **Salient extraction with a converging store** (Mem0-style).
  `SalientMemoryStore.ingest` extracts only structured salient facts
  (identifiers, amounts, preferences, constraints) out of free text —
  raw conversation text is never stored — and merges them through
  ADD / UPDATE / NOOP operations keyed by subject, so memory converges
  to one current fact per subject instead of accumulating duplicates.
  The extractor is a pluggable callable with a rule-based default.
- **ITS retrieval ranking** (Memanto-style Information-Theoretic
  Score). `ITSRanker` ranks memory items by how much they *reduce the
  model's uncertainty* about the current query, not by surface
  similarity: query terms are weighted by self-information under a
  background model of the whole memory corpus (rare terms carry more
  bits), and items are selected greedily by *marginal* gain — so an
  item covering a rare specific term beats one full of generic
  overlapping words, and a second item repeating what the first
  already covered scores zero and drops away.

`MemoryModule.observe` chains the pipeline (gate → extract → store):
only turns that pass the gate reach the extractor, and only extracted
facts reach memory. `recall` runs ITS over the store.

## Go/No-Go

`BlueprintStudio.capability_report(use_case)` renders the verdict: it is
**Go** only when the readiness assessment has no blockers and reaches the
minimum level, all 20 deliverables exist, the dataset has no leakage
problems, no gaps are open, golden cases exist and at least one
evaluation run was recorded. Otherwise the report lists exactly what is
missing — the report is the §48/§49 gate in executable form.
