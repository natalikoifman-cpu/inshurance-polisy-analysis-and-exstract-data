# Skill 11 — Memory upkeep: the "sleep" that turns chats into knowledge

**Problem it fixes:** even a filtered memory accumulates dozens of episodic
fragments ("asked about billing on Tuesday", "asked again on Thursday"). Unmaintained
memory grows stale, contradictory, and slow. Systems that only ever ADD get worse
with time; maintained memory gets **better** with time.

## How it works (the sleep daemon, in plain words)

The brain consolidates during sleep: it replays the day's episodes, keeps the
lesson, discards the noise. Do the same on a schedule (nightly / weekly, in
quiet hours):

1. **Scan** the accumulated episodic memories per customer.
2. **Distill** them into a few stable, semantic facts —
   "3 billing complaints in May" + "asked for invoice copy twice" →
   `has recurring billing issues with direct debit; prefers written invoices`.
3. **Resolve contradictions** — when a new fact conflicts with an old one, the
   newer confirmed fact wins and the old one is deleted (like human memory
   reconsolidation: a recalled memory becomes editable and is re-checked).
4. **Delete** the leftovers that no longer earn their place.

The deck's measured effect: ~50 episodic conversation items compress to ~3 stable
facts (16.7×) — and retrieval quality goes UP because the noise is gone.

## With the custom connector

Call **Consolidate memories** (`/memory/consolidate`) with a customer's memory
list. It returns the compressed list plus what was merged/deleted and the
compression ratio. Schedule it from Power Automate (recurrence flow at night)
over customers active that day.

## Paste this block into your agent's Instructions

```
MEMORY UPKEEP RULE:
- Trust the consolidated facts over old raw episodes.
- When a customer contradicts a stored fact ("כבר לא גר בחיפה"), confirm
  once, then treat the NEW fact as truth — the old one must be deleted,
  not kept alongside.
- Never answer from a memory marked outdated/conflicting; verify with
  the customer instead.
```

## Test

Feed in 10 episodic memories where 3 describe the same billing problem and 2
contradict each other on the customer's city. It passes only if the output has one
billing fact, one city (the newer), and a compression ratio above 2×.
