# Skill 2 — Teach by example (correct AND incorrect)

**Problem it fixes:** users never use the exact words you expect. The difference
between a good and a bad agent is not the model — it is whether you taught it
with examples, including examples of what a WRONG answer looks like.

## How it works

For every detail the agent must extract, and for every kind of answer it gives,
provide:

1. **A definition** — what this thing means.
2. **How it sounds in a real sentence** — users say "my package never arrived",
   not "topic: delivery". Map the everyday phrasing to the value you need.
3. **A correct example** of the extraction/answer.
4. **An incorrect example** — and why it is wrong.
5. **Negative guidance** — what must NOT appear in the answer.

## Paste this pattern into your agent's instructions (fill with your own field)

```
FIELD: topic
Definition: what the user needs — one of: {topic_1}, {topic_2}, {topic_3},
{topic_4}.
How users say it:
- "how much would it cost" / "do you have a discount" → {topic_1: pricing}
- "it arrived broken" / "stopped working after a week" → {topic_2: defect}
- "you charged me twice" / "the invoice is wrong" → {topic_3: billing}
- "where is my order" / "still hasn't shipped" → {topic_4: delivery}
CORRECT: "my package shows delivered but I never got it" → topic = delivery
INCORRECT: "my package shows delivered but I never got it" → topic = defect
  (wrong: the user is reporting a delivery problem, not a broken product)
NEVER: do not output a topic that is not on the list; if unclear — ask.
```

The same pattern works for any domain — swap the topics: a clinic maps
"I need to move my appointment" → `reschedule`; an HR bot maps "how many
vacation days do I have left" → `leave_balance`.

Do this for every field and every answer type. Five well-chosen examples beat a
thousand words of instructions.

## Rule of thumb

If the agent got something wrong once, do not write a longer explanation —
**add that exact case as an incorrect example** with the right answer next to it.

## Test

Feed the agent 3 real user sentences from last week's conversations. It passes
only if it maps all 3 to the values a human colleague would choose.
