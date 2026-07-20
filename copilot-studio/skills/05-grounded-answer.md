# Skill 5 — Grounded answer: answer only from what is written, and prove it

**Problem it fixes:** the model writes beautiful, confident answers that are not in
your data. And when you ask the same thing twice you get two different answers —
you cannot put that in front of a customer.

## How it works

The model is the **last station**. It receives exactly three things:

1. The customer's question (with the completed intake form).
2. The 7–8 retrieved paragraphs (Skill 4) — nothing else.
3. The instruction: **"answer only from what is written here"**, plus one correct
   and one incorrect answer example, plus what NOT to include.

## Paste this block into your agent's Instructions

```
GROUNDING RULE:
When answering from documents/data:
- Use ONLY the paragraphs provided by the Retrieve action. No outside
  knowledge, no assumptions, no "probably".
- If the answer is not in the paragraphs, say exactly:
  "לא מצאתי תשובה לזה במסמכים שלנו — אעביר לנציג אנושי שיבדוק."
- Quote or reference the paragraph you used ("לפי סעיף הכיסוי בפוליסה...").
- Never state prices, coverage amounts, dates or legal terms that do not
  appear word-for-word in the paragraphs.
CORRECT: "לפי הפוליסה שלך, נזקי צנרת מכוסים עד 20,000 ₪ (סעיף 4ב)."
INCORRECT: "בדרך כלל נזקי צנרת מכוסים בסביבות 20-30 אלף ₪."
  (wrong: "בדרך כלל" — that is a guess, not your policy.)
```

## Prove it works (consistency check)

A right answer you cannot reproduce is a guess that got lucky.

- Run the **same question twice** and compare — identical answers prove the input
  is precise and nothing is being guessed. The connector's **Verify consistency**
  action (`/verify`) does this automatically and returns `consistent: true/false`.
- Keep a fixed list of 10–20 test questions with known answers. After ANY change to
  the agent (instructions, data, rules) run the list again — like software tests.
  A change that is not measured is a change you cannot trust.

## Test

Pick a question whose answer you know is in the data. Ask it twice.
It passes only if both answers match each other AND the document.
