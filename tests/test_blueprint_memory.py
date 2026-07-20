"""Tests for module 10: SurprisalGate, salient memory (Mem0-style), ITS."""
import pytest

from blueprint_studio.memory import (
    IntentTransitionModel, ITSRanker, MemoryItem, MemoryModule,
    SalientMemoryStore, SurprisalGate, rule_based_extractor,
)

#: Routine conversation pattern: greeting -> balance -> exposure, repeated.
ROUTINE = [["greeting", "balance_check", "exposure_question"]] * 20


def trained_model():
    model = IntentTransitionModel()
    model.fit(ROUTINE)
    return model


class TestSurprisalGate:
    def test_predictable_input_is_filtered(self):
        gate = SurprisalGate(trained_model().predict, threshold_bits=2.0)
        decision = gate.evaluate("greeting", "balance_check", "מה היתרה שלי?")
        assert not decision.stored
        assert decision.surprisal_bits < 2.0
        assert "filtered" in decision.reason

    def test_surprising_input_is_stored(self):
        gate = SurprisalGate(trained_model().predict, threshold_bits=2.0)
        decision = gate.evaluate(
            "greeting", "exposure_question", "מה החשיפה שלי לפרנק שוויצרי?",
        )
        # exposure after greeting never happened in training -> high surprisal
        assert decision.stored
        assert decision.surprisal_bits >= 2.0

    def test_unknown_intent_is_maximally_surprising(self):
        gate = SurprisalGate(trained_model().predict, threshold_bits=2.0)
        decision = gate.evaluate("greeting", "tax_question", "שאלת מס")
        assert decision.stored
        assert decision.surprisal_bits > 5.0

    def test_correction_always_stored_even_if_predictable(self):
        model = IntentTransitionModel()
        model.fit([["greeting", "correction"]] * 50)   # corrections became routine
        gate = SurprisalGate(model.predict, threshold_bits=2.0)
        decision = gate.evaluate("greeting", "correction", "הנתון שגוי")
        assert decision.stored
        assert decision.reason == "always-store intent"

    def test_filter_rate_reflects_decisions(self):
        gate = SurprisalGate(trained_model().predict, threshold_bits=2.0)
        gate.evaluate("greeting", "balance_check", "routine")        # filtered
        gate.evaluate("greeting", "tax_question", "surprising")      # stored
        assert gate.filter_rate() == pytest.approx(0.5)

    def test_predicted_top_intent_reported(self):
        gate = SurprisalGate(trained_model().predict)
        decision = gate.evaluate("balance_check", "exposure_question", "x")
        assert decision.predicted_top_intent == "exposure_question"


class TestSalientMemoryStore:
    def test_extracts_structured_facts_not_raw_text(self):
        items = rule_based_extractor(
            "בדקתי את US0378331005 והשקעתי 10,000 USD"
        )
        kinds = {i.kind for i in items}
        assert "identifier" in kinds and "amount" in kinds
        isin_item = next(i for i in items if i.kind == "identifier")
        assert isin_item.entities["isin"] == "US0378331005"

    def test_chitchat_yields_nothing(self):
        assert rule_based_extractor("תודה רבה, יום נעים") == []

    def test_add_update_noop_operations(self):
        store = SalientMemoryStore()
        first = store.ingest("אני מעדיף לראות הכל ב-USD")
        assert [op for op, _ in first] == ["ADD"]
        again = store.ingest("אני מעדיף לראות הכל ב-USD")
        assert [op for op, _ in again] == ["NOOP"]
        changed = store.ingest("אני מעדיף לראות הכל ב-EUR")
        assert [op for op, _ in changed] == ["UPDATE"]
        # store converged: one preference item, the latest wins
        pref = store.get("preference:stated")
        assert "EUR" in pref.fact
        assert len([i for i in store.items() if i.kind == "preference"]) == 1

    def test_constraint_marker_detected(self):
        store = SalientMemoryStore()
        applied = store.ingest("אסור להציג מוצרים ממונפים")
        assert any(item.kind == "constraint" for _, item in applied)


def memory_items():
    return [
        MemoryItem(subject="preference:currency",
                   fact="client prefers reports in USD",
                   kind="preference", entities={"currency": "USD"}),
        MemoryItem(subject="identifier:isin:US0378331005",
                   fact="client referenced ISIN US0378331005",
                   kind="identifier", entities={"isin": "US0378331005"}),
        MemoryItem(subject="smalltalk:weather",
                   fact="client said the client likes the client reports",
                   kind="event"),
    ]


class TestITSRanker:
    def test_rare_term_beats_generic_overlap(self):
        ranker = ITSRanker(memory_items())
        ranked = ranker.rank("details for US0378331005")
        assert ranked[0].item.subject == "identifier:isin:US0378331005"
        assert "us0378331005" in ranked[0].covered_terms

    def test_redundant_item_scores_zero_and_drops(self):
        items = memory_items() + [
            MemoryItem(subject="duplicate:pref",
                       fact="client prefers reports in USD",
                       kind="preference", entities={"currency": "USD"}),
        ]
        ranked = ITSRanker(items).rank("prefers USD reports")
        subjects = [r.item.subject for r in ranked]
        # only one of the two identical facts is selected — the second
        # resolves no remaining uncertainty
        assert len([s for s in subjects
                    if s in ("preference:currency", "duplicate:pref")]) == 1

    def test_unrelated_query_returns_nothing(self):
        ranked = ITSRanker(memory_items()).rank("מזג האוויר בפריז")
        assert ranked == []

    def test_scores_are_positive_bits(self):
        ranked = ITSRanker(memory_items()).rank("USD preference")
        assert ranked and all(r.its_score > 0 for r in ranked)


class TestMemoryModule:
    def test_end_to_end_gate_extract_store_recall(self):
        module = MemoryModule(threshold_bits=2.0)
        module.train_intent_model(ROUTINE)
        # routine turn: filtered, nothing extracted
        decision, applied = module.observe(
            "greeting", "balance_check", "מה היתרה? יש לי 5,000 USD",
        )
        assert not decision.stored and applied == []
        # surprising turn with salient content: stored + extracted
        decision, applied = module.observe(
            "greeting", "preference_statement",
            "מהיום אני מעדיף לראות הכל ב-USD",
        )
        assert decision.stored
        assert any(item.kind == "preference" for _, item in applied)
        # recall by ITS
        recalled = module.recall("איזה מטבע להציג? USD?")
        assert recalled and recalled[0].item.kind == "preference"

    def test_stats_shape(self):
        module = MemoryModule(threshold_bits=2.0)
        module.train_intent_model(ROUTINE)
        module.observe("greeting", "balance_check", "routine")
        stats = module.stats()
        assert stats["turns_observed"] == 1
        assert stats["filter_rate"] == 1.0
        assert set(stats["operations"]) == {"ADD", "UPDATE", "NOOP"}

    def test_stored_item_carries_surprisal_and_timestamp(self):
        module = MemoryModule(threshold_bits=0.5)
        module.train_intent_model(ROUTINE)
        _, applied = module.observe(
            "greeting", "preference_statement", "אני מעדיף EUR",
            recorded_at="2026-07-20T10:00:00", use_case="usd exposure",
        )
        _, item = applied[0]
        assert item.surprisal_bits > 0
        assert item.recorded_at == "2026-07-20T10:00:00"
        assert item.use_case == "usd exposure"
