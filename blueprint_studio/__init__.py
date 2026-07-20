"""Financial Agent Blueprint, Data Readiness & Optimization Studio.

A control center for taking a financial data platform to a trustworthy
customer-facing agent: business discovery, question design, data catalog
and lineage, readiness scoring, dataset lab, tool architecture with
enforced permissions, evaluation and optimization, blockage prediction,
and governance — ending in a per-capability Go/No-Go.
"""
from .studio import BlueprintStudio, CapabilityReport, LIFECYCLE

__all__ = ["BlueprintStudio", "CapabilityReport", "LIFECYCLE"]
