# Skill 9 — Remember the customer: from conversations to clean facts

**Problem it fixes:** every conversation starts from zero. The customer said yesterday
they are vegetarian — and today the agent recommends chicken. Losing preferences,
contradicting earlier answers, eroding trust ("צוק ההקשר" — the context cliff).

**The wrong fixes:** dumping the whole chat history into every request (huge cost,
tens of thousands of wasted tokens per question) or naive RAG over raw transcripts
(arbitrary chunks, no understanding). Memory is not a transcript — it is a **small
list of clean, structured facts**.

## How it works (the memory pipeline)

1. **Extract** — after a conversation, a small model distills the *facts worth
   keeping*: preferences, decisions, open issues, corrections.
   "אני צמחוני" → fact: `customer is vegetarian`. Small, clean, reusable.
2. **Compare** — each candidate fact is compared to the most similar existing
   memories (in code).
3. **Decide** — one of four operations per fact:
   - **ADD** — genuinely new information
   - **UPDATE** — extends or corrects an existing memory ("moved from Haifa to Tel Aviv")
   - **DELETE** — the old fact is now wrong
   - **NOOP** — already known or not worth keeping (most things!)
4. **Store facts, never transcripts.** A memory looks like:
   `{"id": "m12", "text": "מעדיף תקשורת במייל, לא טלפון", "state": "calm"}`

## With the custom connector

Call **Extract memory facts** (`/memory/extract`) with the conversation text and the
customer's existing memories. A small, cheap model (Mem0-style salient extraction —
this is exactly the job models like gpt-4o-mini are right-sized for) pulls the
**structured, salient** information out of the free text and leaves the
unstructured noise behind. Each fact comes back as:

```json
{"text": "מעדיף תקשורת במייל ולא בטלפון", "category": "preference", "salience": 0.7}
```

- `category` — preference / decision / correction / commitment / problem / profile,
  so downstream flows can treat a commitment differently from a mild preference.
- `salience` — 0–1, how much this matters for future conversations. Set
  `min_salience` (e.g. 0.5) to keep only the clearly important facts.

The response contains the operations list (ADD/UPDATE/NOOP, each with a reason and
novelty score) and the updated memory list — store it wherever your agent keeps
state (Dataverse table, SharePoint list, CRM field).

## Paste this block into your agent's Instructions

```
MEMORY RULE:
- At the start of a conversation with an identified customer, load their
  memory facts and use them: greet with context, do not re-ask what is
  already known ("רשום אצלי שאת מעדיפה מייל — נכון גם הפעם?").
- Never contradict a stored fact without confirming the change with the
  customer first; when they correct you — that correction IS the new fact.
- At the end of the conversation, call the Extract memory facts action
  and save the updated list. Store FACTS, never raw chat transcripts.
- Memory is per-customer only. Never load or mention another customer's
  memories (see PRIVACY RULE).
```

## Good vs. bad

| | |
|---|---|
| ❌ Bad | Day 2: "מה דעתך על עוף באלפרדו?" (forgot the customer is vegetarian) |
| ✅ Good | Day 2: "בהמשך להעדפה הצמחונית שלך — יש לי שתי הצעות..." |

## Test

Tell the agent a preference, end the chat, start a new one. It passes only if the
preference is remembered — and if repeating the same preference produces **NOOP**,
not a duplicate memory.
