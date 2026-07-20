# Skill 6 — Escalate to a human (cleanly)

**Problem it fixes:** an agent that bluffs when it is stuck destroys trust in one
message. Knowing when to stop is a feature, not a failure.

## When the agent must hand off

- The answer is not in the retrieved paragraphs (Skill 5 said "not found").
- The customer is angry, mentions legal action, or asks for a complaint.
- Money movement, cancellation, or personal-data changes are requested.
- The intake loop asked the same question twice and still has no answer.
- The customer explicitly asks for a person.

## Paste this block into your agent's Instructions

```
ESCALATION RULE:
When any hand-off condition is met, stop trying to answer. Do this instead:
1. Say clearly that you are transferring to a human — never pretend.
   "אני מעביר אותך לנציג/ה — זה נושא שדורש טיפול אישי."
2. Hand the human a SUMMARY, not a transcript: the completed intake form,
   what was already answered, and the exact open question.
3. Never promise what the human will decide ("הנציג בטח יאשר לך").
Escalating is success, not failure. Bluffing is the failure.
```

## Test

Tell the agent "אני רוצה לבטל את הפוליסה ולתבוע אתכם". It passes only if it hands
off immediately with a summary — without arguing and without inventing policy terms.
