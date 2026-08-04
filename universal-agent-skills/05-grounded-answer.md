# Skill 5 — Grounded answer: answer only from what is written, and prove it

**Problem it fixes:** the model writes beautiful, confident answers that are not
in your data. And when you ask the same thing twice you get two different
answers — you cannot put that in front of a user.

## How it works

The model is the **last station**. It receives exactly three things:

1. The user's question (with the completed intake form).
2. The 7–8 retrieved paragraphs (Skill 4) — nothing else.
3. The instruction: **"answer only from what is written here"**, plus one
   correct and one incorrect answer example, plus what NOT to include.

## Paste this block into your agent's instructions

```
GROUNDING RULE:
When answering from documents/data:
- Use ONLY the paragraphs provided by the Retrieve action. No outside
  knowledge, no assumptions, no "probably".
- If the answer is not in the paragraphs, say exactly:
  "I could not find an answer to this in our documentation — I will pass
  this to a human colleague to check."
- Quote or reference the paragraph you used ("According to the returns
  policy, section 4...").
- Never state prices, amounts, dates, deadlines or terms that do not
  appear word-for-word in the paragraphs.
CORRECT: "According to the 2026 price list, the basic plan starts at
  $32/month (see 'Plans' section)."
INCORRECT: "It's usually around $30-40 a month, depending."
  (wrong: "usually" — that is a guess, not your data.)
```

## Prove it works (consistency check)

A right answer you cannot reproduce is a guess that got lucky.

- Run the **same question twice** and compare — identical answers prove the
  input is precise and nothing is being guessed. A **Verify consistency**
  action can do this automatically and return `consistent: true/false`.
- Keep a fixed list of 10–20 test questions with known answers. After ANY change
  to the agent (instructions, data, rules) run the list again — like software
  tests. A change that is not measured is a change you cannot trust.

## Test

Pick a question whose answer you know is in the data. Ask it twice.
It passes only if both answers match each other AND the document.
