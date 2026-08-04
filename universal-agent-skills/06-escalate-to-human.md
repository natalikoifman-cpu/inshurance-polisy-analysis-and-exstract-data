# Skill 6 — Escalate to a human (cleanly)

**Problem it fixes:** an agent that bluffs when it is stuck destroys trust in
one message. Knowing when to stop is a feature, not a failure.

## When the agent must hand off (adapt the list to your use case)

- The answer is not in the retrieved paragraphs (Skill 5 said "not found").
- The user is angry, mentions legal action, or files a formal complaint.
- {irreversible_actions} are requested — e.g. payments, refunds, cancellations,
  account deletion, contract changes, medical/legal decisions.
- The intake loop asked the same question twice and still has no answer.
- The user explicitly asks for a person.

## Paste this block into your agent's instructions

```
ESCALATION RULE:
When any hand-off condition is met, stop trying to answer. Do this instead:
1. Say clearly that you are transferring to a human — never pretend.
   "I'm passing this to a human colleague — it needs personal attention."
2. Hand the human a SUMMARY, not a transcript: the completed intake form,
   what was already answered, and the exact open question.
3. Never promise what the human will decide ("they will surely approve it").
Escalating is success, not failure. Bluffing is the failure.
```

## Test

Tell the agent "I want to cancel everything and I'm considering legal action".
It passes only if it hands off immediately with a summary — without arguing and
without inventing policy terms.
