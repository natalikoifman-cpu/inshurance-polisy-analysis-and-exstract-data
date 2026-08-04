# Skill 12 — Context-aware memory retrieval: state + associations

**Problem it fixes:** plain retrieval matches words only. But "the system is
DOWN!!" from a panicking user and a calm research question can use similar words
while needing completely different memories. And a question about product X
should also surface what's *connected* to X — even when the user didn't say the
connected words.

## How it works

Two additions on top of text relevance (both computed in code):

1. **State-aware scoring (affective routing).** Memories are tagged with the
   user's state when saved (`calm`, `frustrated`, `crisis`, `research`).
   At retrieval time the score blends **~70% text relevance + ~30% state
   match** — an urgent query prefers memories from urgent contexts (the last
   outage, the previous escalation), a calm query prefers the how-to answers.
2. **Associative expansion (activation spreading).** Retrieval is not a point
   lookup: the top matches "light up" their neighbors — memories sharing
   entities or keywords — which join the results at a **decayed score
   (~20% decay)**. Ask about the user's subscription and the linked add-ons and
   open ticket come along, without flooding the context with unrelated facts.

Retrieved memories are **mutable**: recalling one is the moment to check it —
if the user's answer contradicts it, update it right there (Skill 11).

3. **Uncertainty-reduction re-ranking for high-stakes questions (optional).**
   Word similarity can still fool retrieval: a memory can share every word with
   the question and inform nothing. An **information-theoretic score** asks a
   different question of each candidate: *how much does knowing this fact reduce
   the uncertainty about the right answer or action?* A fact that changes what
   you can offer ("their plan has no premium support") outranks a fact that
   merely shares words ("asked a question about support once"). Use it when the
   answer has consequences — quotes, cancellations, medical/legal flows — and
   keep the fast deterministic mode for everyday chat.

## With a backend action

Call a **Retrieve memories** action with the query, the user's memory list, and
optionally `user_state`. Scoring and association expansion are deterministic
code — same input, same memories out, provable like everything else.

For high-stakes questions add a re-rank mode: the deterministic candidates are
re-ranked by the model's uncertainty-reduction score, and each result carries
its score so you can see why it won. If the model isn't configured, fall back to
the fast ranking and say so in a `note` — never fail silently.

## Paste this block into your agent's instructions

```
MEMORY RETRIEVAL RULE:
- Before answering an identified user, call the Retrieve memories
  action with their question AND their current state (calm / frustrated /
  crisis) — judge the state from their wording, e.g. "urgent", "!!",
  "same problem again" = frustrated or crisis.
- Use the returned memories to personalize the answer; mention at most
  the 1-2 most relevant facts, never recite the whole list.
- An urgent user gets continuity first ("I see this is the second time
  this month — I'm sorry about that") — not a fresh-start questionnaire.
- If a retrieved memory seems outdated, confirm it before relying on it.
```

## Test

Save memories from one calm and one urgent past conversation. Ask a similar
question twice — once phrased calmly, once as an emergency. It passes only if
the urgent phrasing surfaces the urgent-context memory first, and both runs are
identical when repeated.
