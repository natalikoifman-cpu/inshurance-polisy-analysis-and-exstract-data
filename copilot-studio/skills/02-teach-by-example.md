# Skill 2 — Teach by example (correct AND incorrect)

**Problem it fixes:** customers never use the exact words you expect. The difference
between a good and a bad agent is not the model — it is whether you taught it with
examples, including examples of what a WRONG answer looks like.

## How it works

For every detail the agent must extract, and for every kind of answer it gives, provide:

1. **A definition** — what this thing means.
2. **How it sounds in a real sentence** — customers say "הביטוח שלי לרכב" not
   "policy type: motor". Map the everyday phrasing to the value you need.
3. **A correct example** of the extraction/answer.
4. **An incorrect example** — and why it is wrong.
5. **Negative guidance** — what must NOT appear in the answer.

## Paste this pattern into your agent's Instructions (fill with your own field)

```
FIELD: topic
Definition: what the customer needs — one of: quote, claim, billing, coverage, complaint.
How customers say it:
- "כמה זה יעלה לי" / "הצעת מחיר" → quote
- "הייתה לי תאונה" / "נגנב לי" / "רוצה להגיש תביעה" → claim
- "חייבתם אותי פעמיים" / "ההוראת קבע" → billing
- "האם אני מכוסה ל..." / "מה כלול בפוליסה" → coverage
CORRECT: "נגנב לי האופניים מהחניה" → topic = claim
INCORRECT: "נגנב לי האופניים מהחניה" → topic = coverage
  (wrong: the customer is reporting an event, not asking what is covered)
NEVER: do not output a topic that is not on the list; if unclear — ask.
```

Do this for every field and every answer type. Five well-chosen examples beat a
thousand words of instructions.

## Rule of thumb

If the agent got something wrong once, do not write a longer explanation — **add that
exact case as an incorrect example** with the right answer next to it.

## Test

Feed the agent 3 real customer sentences from last week's chats. It passes only if it
maps all 3 to the values a human colleague would choose.
