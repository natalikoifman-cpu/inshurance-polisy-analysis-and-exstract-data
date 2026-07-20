"""Module 10 — Memory & Salience Intelligence.

Traditional agent memory is "store everything": every interaction lands
in a database with no distinction between signal and noise.  This module
implements three complementary mechanisms that filter at *ingestion*
and rank at *retrieval*, so the agent's memory holds the essential and
drops the incidental:

1. **SurprisalGate** (mnemos-style) — a predictive model estimates the
   user's next intent; the actual input is scored by its surprisal
   (-log2 p).  Only inputs above the surprisal threshold — genuinely
   new, unexpected information — are stored.  Routine, predictable
   turns are filtered out.

2. **SalienceExtractor** (Mem0-style) — captures only *salient*
   structured facts out of free conversation text (identifiers,
   amounts, preferences, constraints), leaving the unstructured bulk
   behind.  Writes go through Mem0's ADD / UPDATE / NOOP operations so
   the store converges instead of accumulating duplicates.

3. **ITS ranking** (Memanto-style Information-Theoretic Score) — at
   retrieval time, memory items are ranked by how much they *reduce the
   model's uncertainty* about the current query, not by surface
   semantic similarity.  Query terms are weighted by self-information
   (rare terms carry more bits), and items are picked greedily by
   marginal uncertainty reduction, so a second item repeating what the
   first already covered scores near zero.

Consistent with §4.2: the predictive/extractive step is a pluggable
callable — an LLM in production, a deterministic model in tests — while
thresholds, scoring and storage decisions stay in code.
"""
from __future__ import annotations

import math
import re
from dataclasses import dataclass, field, asdict
from typing import Any, Callable


# ---------------------------------------------------------------------------
# 1. SurprisalGate — filter at ingestion by prediction error
# ---------------------------------------------------------------------------

class IntentTransitionModel:
    """Deterministic next-intent predictor: a Laplace-smoothed Markov
    chain over intent sequences.  In production the same interface can
    be served by an LLM predictor; the gate only needs probabilities."""

    START = "<start>"

    def __init__(self) -> None:
        self._transitions: dict[str, dict[str, int]] = {}
        self._vocabulary: set[str] = set()

    def fit(self, sequences: list[list[str]]) -> None:
        for sequence in sequences:
            previous = self.START
            for intent in sequence:
                self._vocabulary.add(intent)
                row = self._transitions.setdefault(previous, {})
                row[intent] = row.get(intent, 0) + 1
                previous = intent

    def probability(self, previous_intent: str, actual_intent: str) -> float:
        row = self._transitions.get(previous_intent, {})
        total = sum(row.values())
        vocab = max(len(self._vocabulary), 1)
        return (row.get(actual_intent, 0) + 1) / (total + vocab)

    def predict(self, previous_intent: str) -> dict[str, float]:
        return {
            intent: self.probability(previous_intent, intent)
            for intent in sorted(self._vocabulary)
        }


@dataclass
class GateDecision:
    """Outcome of the surprisal gate for one input."""
    text: str
    actual_intent: str
    predicted_top_intent: str
    probability: float
    surprisal_bits: float
    stored: bool
    reason: str

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


class SurprisalGate:
    """Store only inputs whose surprisal crosses the threshold.

    ``predictor(previous_intent) -> {intent: probability}`` is pluggable;
    the default is the deterministic transition model.  Hard-constraint
    content (corrections, complaints, refusal triggers) can be
    force-stored regardless of surprisal via ``always_store_intents``.
    """

    def __init__(
        self,
        predictor: Callable[[str], dict[str, float]],
        threshold_bits: float = 2.0,
        always_store_intents: frozenset[str] = frozenset(
            {"correction", "complaint", "permission_change"}
        ),
    ) -> None:
        self.predictor = predictor
        self.threshold_bits = threshold_bits
        self.always_store_intents = always_store_intents
        self._decisions: list[GateDecision] = []

    def evaluate(
        self, previous_intent: str, actual_intent: str, text: str
    ) -> GateDecision:
        distribution = self.predictor(previous_intent)
        floor = 1e-9
        probability = max(distribution.get(actual_intent, floor), floor)
        surprisal = -math.log2(probability)
        top = max(sorted(distribution), key=lambda i: distribution[i]) \
            if distribution else ""
        if actual_intent in self.always_store_intents:
            stored, reason = True, "always-store intent"
        elif surprisal >= self.threshold_bits:
            stored, reason = True, (
                f"surprisal {surprisal:.2f} bits >= threshold "
                f"{self.threshold_bits:.2f}"
            )
        else:
            stored, reason = False, (
                f"predictable input ({surprisal:.2f} bits < "
                f"{self.threshold_bits:.2f}) — filtered"
            )
        decision = GateDecision(
            text=text, actual_intent=actual_intent, predicted_top_intent=top,
            probability=probability, surprisal_bits=round(surprisal, 3),
            stored=stored, reason=reason,
        )
        self._decisions.append(decision)
        return decision

    def filter_rate(self) -> float:
        """Share of inputs kept out of memory — the anti-'store everything'
        KPI."""
        if not self._decisions:
            return 0.0
        filtered = sum(1 for d in self._decisions if not d.stored)
        return filtered / len(self._decisions)

    def decisions(self) -> list[GateDecision]:
        return list(self._decisions)


# ---------------------------------------------------------------------------
# 2. Salience extraction + Mem0-style converging store
# ---------------------------------------------------------------------------

@dataclass
class MemoryItem:
    """One salient, structured fact — never raw conversation text."""
    subject: str                     # dedup/update key, e.g. "preference:channel"
    fact: str                        # normalized statement
    kind: str                        # identifier | amount | preference | constraint | event
    entities: dict[str, str] = field(default_factory=dict)
    source_text: str = ""
    surprisal_bits: float = 0.0      # novelty at ingestion time
    recorded_at: str = ""            # caller-supplied; keeps runs reproducible
    use_case: str = ""

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


#: Extraction patterns for the deterministic (non-LLM) extractor.
_ISIN_RE = re.compile(r"\b([A-Z]{2}[A-Z0-9]{9}[0-9])\b")
_PERCENT_RE = re.compile(r"\b(\d{1,3}(?:\.\d+)?)\s?%")
_AMOUNT_RE = re.compile(r"\b(\d{1,3}(?:,\d{3})+|\d+(?:\.\d+)?)\s?(USD|EUR|ILS|₪|\$|€)")
_CURRENCY_RE = re.compile(r"\b(USD|EUR|ILS|GBP|JPY|CHF)\b")
_PREFERENCE_MARKERS = ("prefer", "מעדיף", "מעדיפה", "אל תציג", "don't show", "תמיד")
_CONSTRAINT_MARKERS = ("אסור", "never", "לא מורשה", "must not", "בלי")


def rule_based_extractor(text: str) -> list[MemoryItem]:
    """Default extractor: pulls structured salient facts out of free text.

    A production deployment swaps this for an LLM extractor with the
    same signature; the storage and dedup logic downstream is identical.
    """
    items: list[MemoryItem] = []
    for isin in _ISIN_RE.findall(text):
        items.append(MemoryItem(
            subject=f"identifier:isin:{isin}",
            fact=f"client referenced ISIN {isin}",
            kind="identifier", entities={"isin": isin}, source_text=text,
        ))
    for amount, currency in _AMOUNT_RE.findall(text):
        items.append(MemoryItem(
            subject=f"amount:{currency}",
            fact=f"amount mentioned: {amount} {currency}",
            kind="amount", entities={"amount": amount, "currency": currency},
            source_text=text,
        ))
    lowered = text.lower()
    if any(marker in lowered for marker in _PREFERENCE_MARKERS):
        currencies = _CURRENCY_RE.findall(text)
        items.append(MemoryItem(
            subject="preference:stated",
            fact=text.strip(),
            kind="preference",
            entities={"currency": currencies[0]} if currencies else {},
            source_text=text,
        ))
    if any(marker in lowered for marker in _CONSTRAINT_MARKERS):
        items.append(MemoryItem(
            subject="constraint:stated", fact=text.strip(),
            kind="constraint", source_text=text,
        ))
    return items


class SalientMemoryStore:
    """Mem0-style store: extraction feeds ADD / UPDATE / NOOP operations,
    so memory converges to one current fact per subject instead of an
    append-only transcript."""

    def __init__(
        self,
        extractor: Callable[[str], list[MemoryItem]] = rule_based_extractor,
    ) -> None:
        self.extractor = extractor
        self._items: dict[str, MemoryItem] = {}
        self._operations: list[tuple[str, str]] = []   # (operation, subject)

    def ingest(
        self, text: str, surprisal_bits: float = 0.0,
        recorded_at: str = "", use_case: str = "",
    ) -> list[tuple[str, MemoryItem]]:
        """Extract salient facts from *text* and merge them into memory.
        Returns the (operation, item) pairs actually applied."""
        applied = []
        for item in self.extractor(text):
            item.surprisal_bits = surprisal_bits
            item.recorded_at = recorded_at
            item.use_case = use_case
            existing = self._items.get(item.subject)
            if existing is None:
                operation = "ADD"
                self._items[item.subject] = item
            elif existing.fact != item.fact:
                operation = "UPDATE"
                self._items[item.subject] = item
            else:
                operation = "NOOP"
            self._operations.append((operation, item.subject))
            applied.append((operation, item))
        return applied

    def items(self) -> list[MemoryItem]:
        return [self._items[k] for k in sorted(self._items)]

    def get(self, subject: str) -> MemoryItem | None:
        return self._items.get(subject)

    def operations(self) -> list[tuple[str, str]]:
        return list(self._operations)


# ---------------------------------------------------------------------------
# 3. ITS — Information-Theoretic Score retrieval ranking
# ---------------------------------------------------------------------------

_TOKEN_RE = re.compile(r"[\w₪$€%.]+", re.UNICODE)


def _tokens(text: str) -> list[str]:
    return [t.lower() for t in _TOKEN_RE.findall(text)]


@dataclass
class RankedItem:
    item: MemoryItem
    its_score: float                 # bits of query uncertainty resolved
    covered_terms: list[str]

    def to_dict(self) -> dict[str, Any]:
        return {
            "subject": self.item.subject, "its_score": self.its_score,
            "covered_terms": self.covered_terms,
        }


class ITSRanker:
    """Rank memory by uncertainty reduction, not surface similarity.

    Each query term carries self-information -log2 p(term) under a
    Laplace-smoothed background model built from the whole memory
    corpus: rare terms are the high-uncertainty part of the query.
    An item's score is the total self-information of the query terms it
    resolves *that no already-selected item resolved* — a greedy
    marginal-gain selection.  Consequences, by construction:

    - an item covering a rare, specific term beats an item full of
      generic overlapping words (similarity != information);
    - a second item repeating the first scores ~0 and drops away.
    """

    def __init__(self, items: list[MemoryItem]) -> None:
        self._items = list(items)
        counts: dict[str, int] = {}
        total = 0
        for item in self._items:
            for token in _tokens(f"{item.fact} {' '.join(item.entities.values())}"):
                counts[token] = counts.get(token, 0) + 1
                total += 1
        self._counts = counts
        self._total = total

    def self_information(self, term: str) -> float:
        vocab = max(len(self._counts), 1)
        probability = (self._counts.get(term, 0) + 1) / (self._total + vocab)
        return -math.log2(probability)

    def rank(self, query: str, limit: int = 5) -> list[RankedItem]:
        query_terms = sorted(set(_tokens(query)))
        term_bits = {t: self.self_information(t) for t in query_terms}
        remaining = set(query_terms)
        candidates = list(self._items)
        selected: list[RankedItem] = []
        while candidates and remaining and len(selected) < limit:
            best: RankedItem | None = None
            for item in candidates:
                item_terms = set(_tokens(
                    f"{item.fact} {' '.join(item.entities.values())}"
                ))
                covered = sorted(remaining & item_terms)
                gain = sum(term_bits[t] for t in covered)
                if best is None or gain > best.its_score or (
                    gain == best.its_score and item.subject < best.item.subject
                ):
                    best = RankedItem(item=item, its_score=round(gain, 3),
                                      covered_terms=covered)
            if best is None or best.its_score <= 0.0:
                break
            selected.append(best)
            candidates = [c for c in candidates if c.subject != best.item.subject]
            remaining -= set(best.covered_terms)
        return selected


# ---------------------------------------------------------------------------
# Facade
# ---------------------------------------------------------------------------

class MemoryModule:
    """Gate -> extract -> store -> rank, as one pipeline.

    ``observe`` runs the full ingestion path: the surprisal gate decides
    whether the turn carries new information at all; only stored turns
    reach the salience extractor, and only extracted facts reach memory.
    ``recall`` ranks the store by ITS for the current query.
    """

    def __init__(
        self,
        predictor: Callable[[str], dict[str, float]] | None = None,
        extractor: Callable[[str], list[MemoryItem]] = rule_based_extractor,
        threshold_bits: float = 2.0,
    ) -> None:
        self.intent_model = IntentTransitionModel()
        self.gate = SurprisalGate(
            predictor or self.intent_model.predict, threshold_bits=threshold_bits,
        )
        self.store = SalientMemoryStore(extractor)

    def train_intent_model(self, sequences: list[list[str]]) -> None:
        self.intent_model.fit(sequences)

    def observe(
        self, previous_intent: str, actual_intent: str, text: str,
        recorded_at: str = "", use_case: str = "",
    ) -> tuple[GateDecision, list[tuple[str, MemoryItem]]]:
        decision = self.gate.evaluate(previous_intent, actual_intent, text)
        if not decision.stored:
            return decision, []
        applied = self.store.ingest(
            text, surprisal_bits=decision.surprisal_bits,
            recorded_at=recorded_at, use_case=use_case,
        )
        return decision, applied

    def recall(self, query: str, limit: int = 5) -> list[RankedItem]:
        return ITSRanker(self.store.items()).rank(query, limit=limit)

    def stats(self) -> dict[str, Any]:
        operations = self.store.operations()
        return {
            "turns_observed": len(self.gate.decisions()),
            "filter_rate": round(self.gate.filter_rate(), 3),
            "memory_items": len(self.store.items()),
            "operations": {
                op: sum(1 for o, _ in operations if o == op)
                for op in ("ADD", "UPDATE", "NOOP")
            },
        }
