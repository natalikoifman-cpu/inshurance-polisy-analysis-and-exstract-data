# Skill 1 — Intake form: ask until the form is full

**Problem it fixes:** an agent that guesses missing details is an agent that is wrong.
A clerk never processes a half-empty form — neither should your agent.

## How it works

1. Decide **up front** which details are mandatory before the agent may act
   (for example 5 fields: full name, policy/order number, topic, product, what happened).
2. The agent extracts what the user already wrote.
3. For **every missing field** it asks one short, targeted question.
4. The flow continues **only when the form is complete**. No guessing, ever.

## Paste this block into your agent's Instructions

```
INTAKE RULE (always first):
Before answering any request, fill this form from what the user wrote:
- full_name
- customer_or_policy_id
- topic (one of: quote, claim, billing, coverage, complaint, other)
- product
- description (what happened / what is needed)

If a field is missing, DO NOT guess and DO NOT answer yet.
Reply with: what you already understood + ONE short question for the
most important missing field. Example:
"הבנתי שמדובר בפוליסת רכב ובשאלה על כיסוי. כדי לעזור אני צריך גם את
מספר הפוליסה — מה המספר?"
Repeat until all fields are filled. Only then continue.
Never invent a value for a field the user did not provide.
```

## With the custom connector

Call the **Check intake form** action (`/intake/check`) with the required fields and
the conversation text. It returns `complete: true/false`, the filled fields, the list
of missing ones, and a ready-to-send `next_question`. Loop until `complete` is true.

## Good vs. bad

| | |
|---|---|
| ❌ Bad | User: "כמה יעלה לי ביטוח?" → Agent quotes a random price for a guessed product |
| ✅ Good | Agent: "אשמח לעזור עם הצעת מחיר. לאיזה מוצר — רכב, דירה או בריאות? ומה גילך?" |

## Test

Ask your agent a question while leaving out an obvious detail. It passes only if it
asks for the missing detail instead of answering.
