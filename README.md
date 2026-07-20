# inshurance-polisy-analysis-and-exstract-data
AI-powered insurance policy analysis tool that extracts structured data from policy documents using Azure Document Intelligence and Azure OpenAI.

## Financial Agent Blueprint & Data Readiness Studio

`blueprint_studio/` is a control center for turning a financial data
platform into a trustworthy customer-facing agent — nine modules covering
business discovery, question design, data catalog & lineage, readiness
scoring with mandatory blockers, a dataset lab (leakage-safe splits,
expert bootstrapping, golden dataset, active learning), tool architecture
with code-enforced permissions, an evaluation & optimization lab (hard
constraints, weighted objective, transaction cost, version acceptance),
blockage prediction, and governance — ending in a per-capability
Go/No-Go verdict.

- Docs: [docs/BLUEPRINT_STUDIO.md](docs/BLUEPRINT_STUDIO.md)
- Demo: `python3 examples/demo_blueprint_studio.py`
- Tests: `python3 -m pytest tests/ -q`

## QA & Observability Control System

`qa_control/` contains a dynamic, runtime QA and observability control plane
for multi-agent financial LLM systems — input gates, consistency/drift
evaluation, tracing, security guardrails, compliance checks, stress testing,
cost/latency KPIs and automated rollback, culminating in a meta-verdict:
*is this system controlled and reliable enough to be monitored?*

- Docs: [docs/CONTROL_SYSTEM.md](docs/CONTROL_SYSTEM.md)
- Demo: `python3 examples/demo_control_plane.py`
- Tests: `python3 -m pytest tests/ -q`
