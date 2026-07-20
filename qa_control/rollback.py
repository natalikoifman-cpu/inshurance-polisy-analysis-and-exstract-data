"""Capability 5 (fault tolerance) — checkpoint & automated rollback.

``RollbackManager`` snapshots workflow state at safe points. When any monitor
raises a policy-threshold violation, ``revert_to_last_safe`` restores the most
recent checkpoint whose state was verified clean, and records the outcome so
the KPI sheet can report a rollback success rate.

State is deep-copied on checkpoint AND on restore, so later mutations of live
state never contaminate stored snapshots.
"""
from __future__ import annotations

import copy
import time
from dataclasses import dataclass
from typing import Any, Callable

from .contracts import Checkpoint, Finding, Severity


@dataclass
class RollbackEvent:
    run_id: str
    checkpoint_id: str
    reason: str
    succeeded: bool
    at: float


class RollbackManager:
    def __init__(self, clock: Callable[[], float] = time.time) -> None:
        self.clock = clock
        self._checkpoints: dict[str, list[Checkpoint]] = {}
        self._safe: dict[str, set[str]] = {}
        self.events: list[RollbackEvent] = []
        self._counter = 0

    def checkpoint(self, run_id: str, label: str,
                   state: dict[str, Any], safe: bool = True) -> Checkpoint:
        self._counter += 1
        cp = Checkpoint(
            checkpoint_id=f"cp-{self._counter:04d}",
            run_id=run_id, label=label,
            state=copy.deepcopy(state),
            created_at=self.clock(),
        )
        self._checkpoints.setdefault(run_id, []).append(cp)
        if safe:
            self._safe.setdefault(run_id, set()).add(cp.checkpoint_id)
        return cp

    def mark_unsafe(self, run_id: str, checkpoint_id: str) -> None:
        """Retroactively distrust a checkpoint (e.g. drift found later)."""
        self._safe.get(run_id, set()).discard(checkpoint_id)

    def last_safe(self, run_id: str) -> Checkpoint | None:
        safe_ids = self._safe.get(run_id, set())
        for cp in reversed(self._checkpoints.get(run_id, [])):
            if cp.checkpoint_id in safe_ids:
                return cp
        return None

    def revert_to_last_safe(self, run_id: str,
                            reason: str) -> tuple[dict[str, Any] | None, Finding]:
        """Returns (restored state, finding describing the outcome)."""
        cp = self.last_safe(run_id)
        if cp is None:
            self.events.append(RollbackEvent(
                run_id, "-", reason, succeeded=False, at=self.clock()))
            return None, Finding(
                "rollback.no_safe_checkpoint", Severity.CRITICAL,
                f"rollback requested ({reason}) but no safe checkpoint "
                f"exists for run '{run_id}'")
        self.events.append(RollbackEvent(
            run_id, cp.checkpoint_id, reason, succeeded=True,
            at=self.clock()))
        return copy.deepcopy(cp.state), Finding(
            "rollback.performed", Severity.WARNING,
            f"run '{run_id}' reverted to checkpoint '{cp.label}' "
            f"({cp.checkpoint_id}) because: {reason}")

    def stats(self) -> dict[str, Any]:
        attempted = len(self.events)
        succeeded = sum(1 for e in self.events if e.succeeded)
        return {
            "rollbacks_attempted": attempted,
            "rollbacks_succeeded": succeeded,
            "rollback_success_rate":
                round(succeeded / attempted, 4) if attempted else 1.0,
        }
