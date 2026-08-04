# Skill 9 — Remember the user: from conversations to clean facts

**Problem it fixes:** every conversation starts from zero. The user said
yesterday they are vegetarian — and today the agent recommends chicken. Losing
preferences, contradicting earlier answers, eroding trust (the "context cliff").

**The wrong fixes:** dumping the whole chat history into every request (huge
cost, tens of thousands of wasted tokens per question) or naive RAG over raw
transcripts (arbitrary chunks, no understanding). Memory is not a transcript —
it is a **small list of clean, structured facts**.

## How it works (the memory pipeline)

1. **Extract** — after a conversation, a small model distills the *facts worth
   keeping*: preferences, decisions, open issues, corrections.
   "I'm vegetarian" → fact: `user is vegetarian`. Small, clean, reusable.
2. **Compare** — each candidate fact is compared to the most similar existing
   memories (in code).
3. **Decide** — one of four operations per fact:
   - **ADD** — genuinely new information
   - **UPDATE** — extends or corrects an existing memory ("moved from Boston to Austin")
   - **DELETE** — the old fact is now wrong
   - **NOOP** — already known or not worth keeping (most things!)
4. **Store facts, never transcripts.** A memory looks like:
   `{"id": "m12", "text": "prefers email over phone", "state": "calm"}`

## With a backend action

Call an **Extract memory facts** action with the conversation text and the
user's existing memories. A small, cheap model pulls the **structured, salient**
information out of the free text and leaves the unstructured noise behind. Each
fact comes back as:

```json
{"text": "prefers email over phone", "category": "preference", "salience": 0.7}
```

- `category` — preference / decision / correction / commitment / problem /
  profile, so downstream flows can treat a commitment differently from a mild
  preference.
- `salience` — 0–1, how much this matters for future conversations. Set
  `min_salience` (e.g. 0.5) to keep only the clearly important facts.

The response contains the operations list (ADD/UPDATE/NOOP, each with a reason
and novelty score) and the updated memory list — store it wherever your agent
keeps state (database table, CRM field, platform variable store).

## Paste this block into your agent's instructions

```
MEMORY RULE:
- At the start of a conversation with an identified user, load their
  memory facts and use them: greet with context, do not re-ask what is
  already known ("I have on file that you prefer email — still true?").
- Never contradict a stored fact without confirming the change with the
  user first; when they correct you — that correction IS the new fact.
- At the end of the conversation, call the Extract memory facts action
  and save the updated list. Store FACTS, never raw chat transcripts.
- Memory is per-user only. Never load or mention another user's
  memories (see PRIVACY RULE).
```

## Good vs. bad

| | |
|---|---|
| ❌ Bad | Day 2: "How about the chicken alfredo?" (forgot the user is vegetarian) |
| ✅ Good | Day 2: "Following your vegetarian preference — I have two suggestions..." |

## Test

Tell the agent a preference, end the chat, start a new one. It passes only if
the preference is remembered — and if repeating the same preference produces
**NOOP**, not a duplicate memory.
