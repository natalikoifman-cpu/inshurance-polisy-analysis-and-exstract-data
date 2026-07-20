# Skill 10 — The surprisal gate: store only what's new

**Problem it fixes:** a memory that stores everything drowns in its own noise.
"בסדר", "תודה", small talk, repeated details — if it all goes in, retrieval gets
worse every day and costs grow forever ("bloat").

## How it works (predictive coding, in plain words)

The brain doesn't record the expected — it records the **surprising**. The system
predicts what the customer will say; input that matches the prediction (politeness,
filler, facts already known) is **rejected**. Only information that deviates from
what we already know crosses the gate and is written to memory.

- "עברנו לשרת Svelte" → surprising → **kept**
- "בסדר, תודה" → expected → **rejected**
- The same preference said a third time → already known → **rejected (NOOP)**

In the deck's measurements this cut memory noise by ~40% at write time.

## With the custom connector — two gates, use one or both

**Gate A — novelty against memory (fast, no model).** Built into **Extract memory
facts** (`/memory/extract`): each candidate fact gets a `novelty` score (0–1)
computed in code against the existing memories. Below the threshold (default 0.3)
→ NOOP. Tune `novelty_threshold` per agent: raise it for chatty consumer bots,
lower it for high-stakes flows.

**Gate B — predictive surprisal (the mnemos SurprisalGate).** The **Score
surprisal** action (`/memory/surprisal`) uses a language model the way the brain
uses predictive coding: it **predicts the user's next intent** from the
conversation history, compares the prediction with what the user *actually* said,
and returns a `surprisal` score:

- The agent asked "מה מספר הפוליסה?" and got a number → surprisal ≈ 0.1 → skip.
- Instead the customer said "בעצם אני רוצה לבטל את הפוליסה" → surprisal ≈ 0.9 → remember.

Call it before extraction (`keep=false` → don't even extract), or set
`surprisal_mode: "llm"` on **Extract memory facts** with `conversation_history`
and the gate runs automatically — expected messages skip memory entirely, and the
response's `gate` object shows the predicted intent and reason, so you can audit
every rejection.

Gate B catches what Gate A can't: input that is *routine given the conversation*
even though it never appeared in memory before. Gate A catches repeats across
conversations. Together they implement "filter at the intake stage" — the model
sorts the content before storage instead of passively recording everything.

## Paste this block into your agent's Instructions

```
MEMORY WRITE FILTER:
Not everything the customer says deserves to be remembered. Save a fact
ONLY if it would change how a colleague handles the NEXT conversation:
preferences, decisions, corrections, commitments, open problems.
Never save: greetings, small talk, politeness, information that is
already in the memory list, or details of this conversation's mechanics.
When in doubt — do not save. A small, clean memory beats a big, noisy one.
```

## Test

Run a conversation that is 90% small talk with one real decision in the middle.
It passes only if exactly ONE new fact is stored.
