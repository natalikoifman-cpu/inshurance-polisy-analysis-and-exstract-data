"""Capability 3 — Agentic Observability (runtime instrumentation).

- ``Tracer``: hierarchical spans + events with token/cost usage per span,
  exportable as deterministic JSONL run logs.
- ``AgentBusMonitor``: every inter-agent message passes through here; it
  enforces the communication contract (allowed routes, payload size) and
  keeps the full message trace.
- ``FeedbackStore``: the continuous-training component — persists graded run
  outcomes and serves curated examples back as few-shot training material.

Timestamps come from an injectable ``clock`` so runs are reproducible in
tests and simulations; production uses ``time.time`` by default.
"""
from __future__ import annotations

import json
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Iterator
from contextlib import contextmanager

from .contracts import AgentMessage, Finding, Severity, TraceEvent, Usage

# ---------------------------------------------------------------------------
# Tracing
# ---------------------------------------------------------------------------


@dataclass
class Span:
    span_id: str
    name: str
    agent: str
    start: float
    end: float | None = None
    parent_id: str | None = None
    events: list[TraceEvent] = field(default_factory=list)
    usage: Usage = field(default_factory=Usage)
    status: str = "ok"                 # ok | error
    error: str | None = None
    attributes: dict[str, Any] = field(default_factory=dict)

    @property
    def duration_ms(self) -> float:
        if self.end is None:
            return 0.0
        return (self.end - self.start) * 1000.0

    def to_dict(self) -> dict[str, Any]:
        return {
            "span_id": self.span_id,
            "name": self.name,
            "agent": self.agent,
            "parent_id": self.parent_id,
            "start": self.start,
            "end": self.end,
            "duration_ms": round(self.duration_ms, 3),
            "status": self.status,
            "error": self.error,
            "usage": {
                "input_tokens": self.usage.input_tokens,
                "output_tokens": self.usage.output_tokens,
                "cost_usd": round(self.usage.cost_usd, 6),
            },
            "attributes": self.attributes,
            "events": [
                {"name": e.name, "timestamp": e.timestamp,
                 "attributes": e.attributes}
                for e in self.events
            ],
        }


class Tracer:
    """Deep instrumentation: spans for every agent step, JSONL export."""

    def __init__(self, run_id: str | None = None,
                 clock: Callable[[], float] = time.time,
                 id_factory: Callable[[], str] | None = None) -> None:
        self.run_id = run_id or uuid.uuid4().hex[:12]
        self.clock = clock
        self._counter = 0
        self._id_factory = id_factory or self._next_id
        self.spans: list[Span] = []
        self._stack: list[Span] = []

    def _next_id(self) -> str:
        self._counter += 1
        return f"span-{self._counter:04d}"

    @contextmanager
    def span(self, name: str, agent: str = "system",
             **attributes: Any) -> Iterator[Span]:
        parent = self._stack[-1] if self._stack else None
        sp = Span(span_id=self._id_factory(), name=name, agent=agent,
                  start=self.clock(),
                  parent_id=parent.span_id if parent else None,
                  attributes=dict(attributes))
        self.spans.append(sp)
        self._stack.append(sp)
        try:
            yield sp
        except Exception as exc:
            sp.status = "error"
            sp.error = f"{type(exc).__name__}: {exc}"
            raise
        finally:
            sp.end = self.clock()
            self._stack.pop()

    def event(self, name: str, **attributes: Any) -> None:
        ev = TraceEvent(name=name, timestamp=self.clock(),
                        attributes=attributes)
        if self._stack:
            self._stack[-1].events.append(ev)
        else:  # orphan event: attach to a zero-length system span
            with self.span(f"event:{name}") as sp:
                sp.events.append(ev)

    def record_usage(self, usage: Usage) -> None:
        if self._stack:
            self._stack[-1].usage.add(usage)

    def total_usage(self) -> Usage:
        total = Usage()
        for sp in self.spans:
            total.add(sp.usage)
        return total

    def durations_ms(self) -> list[float]:
        return [sp.duration_ms for sp in self.spans if sp.end is not None]

    def error_spans(self) -> list[Span]:
        return [sp for sp in self.spans if sp.status == "error"]

    def export_jsonl(self, path: str | Path) -> Path:
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("w", encoding="utf-8") as fh:   # overwrite, never append
            for sp in self.spans:
                record = {"run_id": self.run_id, **sp.to_dict()}
                fh.write(json.dumps(record, ensure_ascii=False,
                                    sort_keys=True) + "\n")
        return path


# ---------------------------------------------------------------------------
# Multi-agent communication monitoring
# ---------------------------------------------------------------------------


@dataclass
class Route:
    sender: str
    recipient: str
    kinds: tuple[str, ...] = ("task", "result")


class AgentBusMonitor:
    """All inter-agent traffic flows through ``send``; violations are recorded.

    The allowed-routes table is the communication protocol: any message on an
    undeclared route, of an undeclared kind, or with an oversized payload is a
    protocol violation. Violating messages are NOT delivered.
    """

    def __init__(self, allowed_routes: list[Route],
                 max_payload_chars: int = 100_000,
                 clock: Callable[[], float] = time.time) -> None:
        self._routes = {(r.sender, r.recipient): r for r in allowed_routes}
        self.max_payload_chars = max_payload_chars
        self.clock = clock
        self.messages: list[AgentMessage] = []
        self.violations: list[Finding] = []

    def send(self, sender: str, recipient: str, kind: str,
             payload: dict[str, Any]) -> bool:
        """Returns True if the message conforms to protocol and is delivered."""
        msg = AgentMessage(sender=sender, recipient=recipient, kind=kind,
                           payload=payload, timestamp=self.clock())
        self.messages.append(msg)

        route = self._routes.get((sender, recipient))
        if route is None:
            self.violations.append(Finding(
                "bus.unauthorized_route", Severity.CRITICAL,
                f"no declared route {sender} -> {recipient}"))
            return False
        if kind not in route.kinds:
            self.violations.append(Finding(
                "bus.unknown_kind", Severity.BLOCKING,
                f"kind '{kind}' not allowed on {sender} -> {recipient}"))
            return False
        size = len(json.dumps(payload, ensure_ascii=False, default=str))
        if size > self.max_payload_chars:
            self.violations.append(Finding(
                "bus.payload_too_large", Severity.BLOCKING,
                f"payload {size} chars exceeds {self.max_payload_chars} "
                f"on {sender} -> {recipient}"))
            return False
        return True

    def conversation(self, agent_a: str, agent_b: str) -> list[AgentMessage]:
        return [m for m in self.messages
                if {m.sender, m.recipient} == {agent_a, agent_b}]


# ---------------------------------------------------------------------------
# Continuous training / feedback loop
# ---------------------------------------------------------------------------


@dataclass
class FeedbackRecord:
    run_id: str
    workflow: str
    input_text: str
    output_text: str
    grade: str                     # "good" | "bad"
    reasons: list[str] = field(default_factory=list)


class FeedbackStore:
    """Persist graded outcomes; serve curated examples for future prompts.

    This closes the loop: graded good runs become few-shot 'correct' examples
    and graded bad runs become 'incorrect' examples for the next prompt
    bundle — the logged data systematically improves the agents.
    """

    def __init__(self, path: str | Path | None = None) -> None:
        self.path = Path(path) if path else None
        self.records: list[FeedbackRecord] = []
        if self.path and self.path.exists():
            for line in self.path.read_text(encoding="utf-8").splitlines():
                if line.strip():
                    self.records.append(FeedbackRecord(**json.loads(line)))

    def add(self, record: FeedbackRecord) -> None:
        self.records.append(record)
        if self.path:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            # rewrite the whole store: state is exactly self.records
            with self.path.open("w", encoding="utf-8") as fh:
                for r in self.records:
                    fh.write(json.dumps(r.__dict__, ensure_ascii=False,
                                        sort_keys=True) + "\n")

    def curated_few_shot(self, workflow: str,
                         limit_per_label: int = 3) -> list[dict[str, str]]:
        """Newest graded examples first, both labels, prompt-bundle format."""
        examples: list[dict[str, str]] = []
        for grade, label in (("good", "correct"), ("bad", "incorrect")):
            matches = [r for r in reversed(self.records)
                       if r.workflow == workflow and r.grade == grade]
            for r in matches[:limit_per_label]:
                examples.append({"input": r.input_text,
                                 "output": r.output_text, "label": label})
        return examples

    def improvement_stats(self, workflow: str) -> dict[str, Any]:
        relevant = [r for r in self.records if r.workflow == workflow]
        good = sum(1 for r in relevant if r.grade == "good")
        return {
            "total": len(relevant),
            "good": good,
            "bad": len(relevant) - good,
            "good_rate": round(good / len(relevant), 4) if relevant else None,
        }
