"""Module 6 — Agent Architecture & Tool Design (§31-§34, §40).

The agent never receives a database dump — only focused tools with a
mandatory response envelope (§31).  Permissions are enforced here, in
code, never by prompt (§4.4).  The pre-answer decision gate (§34) runs
every deterministic check before the LLM is allowed to phrase anything,
and the usage policy (§32) records what must stay out of vector search
and out of the LLM entirely.
"""
from __future__ import annotations

from dataclasses import dataclass, field, asdict
from typing import Any, Callable

from .contracts import Decision, RiskLevel, ToolResponse

#: §31 — the recommended focused tool surface.
STANDARD_TOOLS = (
    "get_client_portfolios", "get_portfolio_summary", "get_portfolio_holdings",
    "get_instrument_details", "get_instrument_exposures",
    "get_performance_breakdown", "compare_financial_products",
    "get_data_quality_status", "search_approved_documents",
    "explain_calculation", "find_similar_cases", "predict_process_blockage",
    "create_service_request",
)

#: §32 — vector search must never replace exact comparison of these.
VECTOR_SEARCH_FORBIDDEN = (
    "balances", "prices", "percentages", "returns", "ratings",
    "permissions", "identifiers", "statuses",
)

#: §32 — the LLM must never be the executor of these.
LLM_FORBIDDEN = (
    "financial_calculations", "permission_decisions", "source_of_truth_selection",
    "date_validation", "rating_determination", "exposure_calculation",
    "identification_by_guess",
)

#: §33 — the layered reference architecture, top to bottom.
ARCHITECTURE_LAYERS = (
    "Client Channel", "Authentication and Authorization", "Agent Orchestrator",
    "Intent and Entity Layer", "Policy and Permission Gate", "Tool Router",
    "Execution Services (APIs / Calculations / Rules / Models / Retrieval)",
    "Response Validation", "LLM Explanation Layer", "Final Compliance Gate",
    "Client Response",
)


@dataclass
class ToolSpec:
    """One agent tool: purpose, permissions and measurable contract (§4.5)."""
    name: str
    description: str
    use_cases: list[str] = field(default_factory=list)
    required_permission: str = ""
    data_sources: list[str] = field(default_factory=list)
    calculation_method: str = ""     # approved method id, "" = no calculation
    success_metric: str = ""
    refusal_conditions: list[str] = field(default_factory=list)
    deterministic: bool = True
    cost_per_call: float = 0.0

    def capability_checklist(self) -> list[str]:
        """§4.5 — a tool may not ship without these; returns what's missing."""
        checks = {
            "use case": bool(self.use_cases),
            "data source": bool(self.data_sources),
            "success metric": bool(self.success_metric),
            "refusal conditions": bool(self.refusal_conditions),
            "permission requirement": bool(self.required_permission),
        }
        return [name for name, ok in checks.items() if not ok]

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class RequestContext:
    """What the decision gate knows about one client request."""
    user_id: str
    client_id: str
    permissions: frozenset[str]
    authenticated: bool = True
    identified_client_matches: bool = True
    risk_level: RiskLevel = RiskLevel.MEDIUM
    ambiguous: bool = False
    could_be_advice: bool = False
    request_id: str = ""


@dataclass
class GateVerdict:
    decision: Decision
    reasons: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {"decision": self.decision.value, "reasons": self.reasons}


class PermissionGate:
    """§4.4 — deterministic permission enforcement, outside the LLM."""

    def __init__(self, tool_permissions: dict[str, str]) -> None:
        self._tool_permissions = dict(tool_permissions)

    def allows(self, ctx: RequestContext, tool_name: str) -> bool:
        if not ctx.authenticated:
            return False
        required = self._tool_permissions.get(tool_name)
        if required is None:          # unregistered tool: deny by default
            return False
        return required in ctx.permissions


class DecisionGate:
    """§34 — every deterministic check that runs before any answer."""

    def __init__(
        self,
        min_coverage: float = 0.9,
        min_confidence: float = 0.8,
        max_staleness_hours: float = 48.0,
    ) -> None:
        self.min_coverage = min_coverage
        self.min_confidence = min_confidence
        self.max_staleness_hours = max_staleness_hours

    def evaluate(
        self,
        ctx: RequestContext,
        tool_response: ToolResponse,
        staleness_hours: float,
        conflicting_sources: bool = False,
    ) -> GateVerdict:
        # Hard refusals first — §9.1 hard constraints.
        if not ctx.authenticated:
            return GateVerdict(Decision.REFUSE_PERMISSION, ["user not authenticated"])
        if not ctx.identified_client_matches:
            return GateVerdict(
                Decision.REFUSE_PERMISSION, ["client identity mismatch"]
            )
        if tool_response.status == "error":
            return GateVerdict(
                Decision.REFUSE_MISSING_DATA, ["tool call failed"]
            )
        if not tool_response.as_of:
            return GateVerdict(
                Decision.REFUSE_MISSING_DATA,
                ["answer has no correctness date — hard constraint"],
            )
        if ctx.ambiguous:
            return GateVerdict(
                Decision.ASK_CLARIFICATION, ["request is ambiguous"]
            )
        if ctx.could_be_advice:
            return GateVerdict(
                Decision.REQUEST_HUMAN_VALIDATION,
                ["answer could constitute regulated advice"],
            )
        if conflicting_sources:
            return GateVerdict(
                Decision.CREATE_DATA_QUALITY_TASK,
                ["unresolved conflict between sources"],
            )
        if tool_response.coverage < self.min_coverage:
            if tool_response.coverage <= 0.5:
                return GateVerdict(
                    Decision.REFUSE_MISSING_DATA,
                    [f"coverage {tool_response.coverage:.0%} below usable floor"],
                )
            return GateVerdict(
                Decision.ANSWER_WITH_WARNING,
                [f"coverage {tool_response.coverage:.0%} below target "
                 f"{self.min_coverage:.0%}"],
            )
        warnings = []
        if staleness_hours > self.max_staleness_hours:
            warnings.append(
                f"data is {staleness_hours:.0f}h old "
                f"(limit {self.max_staleness_hours:.0f}h)"
            )
        if tool_response.confidence < self.min_confidence:
            warnings.append(
                f"confidence {tool_response.confidence:.0%} below "
                f"{self.min_confidence:.0%}"
            )
        if tool_response.missing_fields:
            warnings.append(
                "missing fields: " + ", ".join(tool_response.missing_fields)
            )
        if warnings:
            return GateVerdict(Decision.ANSWER_WITH_WARNING, warnings)
        return GateVerdict(Decision.ANSWER, [])


class ToolRegistry:
    """The complete tool surface, with per-tool permission enforcement."""

    def __init__(self) -> None:
        self._tools: dict[str, ToolSpec] = {}
        self._handlers: dict[str, Callable[..., ToolResponse]] = {}

    def register(
        self,
        spec: ToolSpec,
        handler: Callable[..., ToolResponse] | None = None,
        allow_incomplete: bool = False,
    ) -> ToolSpec:
        missing = spec.capability_checklist()
        if missing and not allow_incomplete:
            raise ValueError(
                f"tool '{spec.name}' missing required definitions: "
                + ", ".join(missing)
            )
        self._tools[spec.name] = spec
        if handler is not None:
            self._handlers[spec.name] = handler
        return spec

    def permission_gate(self) -> PermissionGate:
        return PermissionGate(
            {name: t.required_permission for name, t in self._tools.items()}
        )

    def invoke(self, ctx: RequestContext, name: str, **kwargs: Any) -> ToolResponse:
        """Permission-checked tool call — the only path to a handler."""
        if not self.permission_gate().allows(ctx, name):
            return ToolResponse(
                status="error", as_of="", source="",
                warnings=[f"permission denied for tool '{name}'"],
                request_id=ctx.request_id,
            )
        handler = self._handlers.get(name)
        if handler is None:
            return ToolResponse(
                status="error", as_of="", source="",
                warnings=[f"tool '{name}' has no handler"],
                request_id=ctx.request_id,
            )
        response = handler(ctx=ctx, **kwargs)
        response.request_id = response.request_id or ctx.request_id
        return response

    def tools(self) -> list[ToolSpec]:
        return list(self._tools.values())
