# inshurance-polisy-analysis-and-exstract-data
AI-powered insurance policy analysis tool that extracts structured data from policy documents using Azure Document Intelligence and Azure OpenAI.

## QA & Observability Control System

`qa_control/` contains a dynamic, runtime QA and observability control plane
for multi-agent financial LLM systems — input gates, consistency/drift
evaluation, tracing, security guardrails, compliance checks, stress testing,
cost/latency KPIs and automated rollback, culminating in a meta-verdict:
*is this system controlled and reliable enough to be monitored?*

- Docs: [docs/CONTROL_SYSTEM.md](docs/CONTROL_SYSTEM.md)
- Demo: `python3 examples/demo_control_plane.py`
- Tests: `python3 -m pytest tests/ -q`
