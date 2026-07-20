"""Module 3 — Data Catalog, Semantic Layer and Lineage (§17.2, §25, §26).

The LLM is never pointed at the warehouse (§4.1).  Instead the catalog
records every source, entity and field — with owners, quality, coverage,
licensing and SLA — and the semantic layer defines the unified financial
entities that tools are built on.  Field-level lineage lets any client
answer be traced back to its origin.
"""
from __future__ import annotations

from dataclasses import dataclass, field, asdict
from typing import Any

#: §26 — the unified financial entities of the semantic layer.
SEMANTIC_ENTITIES = (
    "Client", "Account", "Portfolio", "Holding", "Transaction",
    "FinancialInstrument", "Issuer", "Fund", "ShareClass", "Currency",
    "Price", "ExchangeRate", "Benchmark", "Classification", "Exposure",
    "Rating", "Document", "DataSource", "ValidationResult",
)


@dataclass
class FieldSpec:
    """One field of a semantic entity, tied to its authoritative source."""
    name: str
    entity: str
    source: str = ""                 # authoritative source of truth
    coverage: float = 0.0            # share of records populated
    freshness_hours: float | None = None
    ai_generated: bool = False       # §30 — fields produced by AI need review
    permission_level: str = "internal"

    @property
    def qualified(self) -> str:
        return f"{self.entity}.{self.name}"

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class DataSource:
    """§25 — one registered information source."""
    name: str
    source_type: str                 # master | market | portfolio | documents
    business_owner: str = ""
    tech_owner: str = ""
    entities: list[str] = field(default_factory=list)
    update_frequency: str = ""       # e.g. "daily", "intraday", "monthly"
    quality_score: float = 0.0       # 0..1, from profiling
    coverage: float = 0.0
    licensed_for_client_display: bool = False
    permissions: list[str] = field(default_factory=list)
    sla: str = ""
    schema_version: str = "1"
    is_primary: bool = True          # primary vs. supplementary source

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class LineageStep:
    stage: str        # ingestion | cleaning | mapping | calculation | api | agent
    description: str
    version: str = "1"
    approver: str = ""


@dataclass
class LineageRecord:
    """§17.2 — the full path of one field from external source to answer."""
    field: str                       # qualified entity.field
    source: str
    ingested_at: str = ""
    as_of: str = ""
    steps: list[LineageStep] = field(default_factory=list)
    selection_rule: str = ""         # why this source wins on conflicts
    dependent_uses: list[str] = field(default_factory=list)

    def is_traceable(self) -> bool:
        return bool(self.source and self.as_of and self.steps)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


class DataCatalog:
    """Registry of sources, fields and lineage with sufficiency queries."""

    def __init__(self) -> None:
        self._sources: dict[str, DataSource] = {}
        self._fields: dict[str, FieldSpec] = {}
        self._lineage: dict[str, LineageRecord] = {}

    # -- registration -------------------------------------------------
    def add_source(self, source: DataSource) -> DataSource:
        self._sources[source.name] = source
        return source

    def add_field(self, spec: FieldSpec) -> FieldSpec:
        self._fields[spec.qualified] = spec
        return spec

    def add_lineage(self, record: LineageRecord) -> LineageRecord:
        self._lineage[record.field] = record
        return record

    # -- lookups ------------------------------------------------------
    def source(self, name: str) -> DataSource | None:
        return self._sources.get(name)

    def field(self, qualified: str) -> FieldSpec | None:
        return self._fields.get(qualified)

    def lineage(self, qualified: str) -> LineageRecord | None:
        return self._lineage.get(qualified)

    def sources(self) -> list[DataSource]:
        return list(self._sources.values())

    # -- sufficiency checks used by the readiness module ---------------
    def missing_fields(self, required: list[str]) -> list[str]:
        return [f for f in required if f not in self._fields]

    def unlicensed_sources(self, source_names: list[str]) -> list[str]:
        """Sources that may not be displayed to clients (§28 blocker)."""
        return [
            n for n in source_names
            if (s := self._sources.get(n)) is None
            or not s.licensed_for_client_display
        ]

    def untraceable_fields(self, required: list[str]) -> list[str]:
        return [
            f for f in required
            if (rec := self._lineage.get(f)) is None or not rec.is_traceable()
        ]

    def lineage_coverage(self) -> float:
        """§44 — share of cataloged fields with traceable lineage."""
        if not self._fields:
            return 0.0
        traced = sum(
            1 for f in self._fields
            if (rec := self._lineage.get(f)) and rec.is_traceable()
        )
        return traced / len(self._fields)
