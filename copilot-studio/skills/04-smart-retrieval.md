# Skill 4 — Smart retrieval: from 10,000 rows to 8 paragraphs

**Problem it fixes:** pasting a whole Excel/policy document at the model is like
handing someone a 500-page binder and saying "find the answer" without saying where.
The model guesses what matters — and every run it guesses differently. It is also
expensive: you pay for every row it reads.

## The funnel (most of it is code, not AI)

```
All the data in the file            ─ 10,000 rows
→ Identify the relevant topic group ─ 1 group out of ~20
→ Enriched search keywords          ─ variations, typos, synonyms (~200 words)
→ Smart text search (BM25)          ─ 7–8 paragraphs
→ What the model sees               ─ ONLY this
```

The model answers from one focused page — not a binder. That is why it is accurate,
and why it costs pennies instead of shekels per question.

## With the custom connector

Call the **Retrieve relevant paragraphs** action (`/retrieve`) with the customer's
question and the document text (or a stored dataset name). The backend:

1. Extracts keywords from the question.
2. Enriches them **in code**: Hebrew prefixes (ו/ה/ב/ל/מ/ש/כ), plural/singular,
   your synonym list ("רכב" ↔ "אוטו" ↔ "מכונית").
3. Runs BM25 ranking and returns the top 7–8 paragraphs with scores.

Then pass ONLY those paragraphs to the **Grounded answer** action (Skill 5).

## Paste this block into your agent's Instructions

```
RETRIEVAL RULE:
Never answer document/data questions from memory and never read a whole
file. First call the Retrieve action with the customer's question; then
answer ONLY from the paragraphs it returns (see GROUNDING RULE).
If retrieval returns nothing relevant, say you could not find it and
offer a human hand-off — do not improvise.
```

## For hard, open-ended questions

When a question genuinely requires digging through complex data (not one lookup),
route it to a **research-grade tool/model** that plans, searches, reads and
cross-checks in steps — not a regular one-shot chat answer. Keep the regular model
for the final, customer-facing reply.

## Test

Ask a question whose answer is in one specific paragraph of a long document.
It passes only if the answer quotes/uses that paragraph — and says "not found"
for a question whose answer is NOT in the document.
