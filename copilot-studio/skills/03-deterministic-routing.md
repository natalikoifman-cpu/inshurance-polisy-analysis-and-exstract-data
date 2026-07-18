# Skill 3 — Deterministic routing: rules decide, the AI talks

**Problem it fixes:** "if the account type is X go to flow A, if Y go to flow B" —
when the AI makes these decisions it guesses, and every run can go a different way.
Rules written as rules are checked, testable, free, and always the same.

## How it works

Once the intake form is complete (Skill 1), the NEXT STEP is chosen by plain
conditions — in Copilot Studio use **Topics with conditions** (Add node → Add a
condition), or call the connector's **Route** action. The AI never picks the branch;
it only handles the conversation inside the branch.

## Example rule set

```
if topic = claim            → Claims topic (collect event details, open ticket)
if topic = quote and product = car   → Car-quote topic
if topic = quote and product = home  → Home-quote topic
if topic = billing          → Billing topic (verify identity first)
if topic = complaint        → Escalate to human (Skill 6)
otherwise                   → Clarify topic (Skill 1 question loop)
```

## With the custom connector

Call the **Route request** action (`/route`) with the filled form and your rule list.
Rules are JSON like:

```json
[
  {"when": [{"field": "topic", "op": "eq", "value": "claim"}], "route": "claims"},
  {"when": [{"field": "topic", "op": "eq", "value": "quote"},
             {"field": "product", "op": "eq", "value": "car"}], "route": "car_quote"},
  {"when": [], "route": "clarify"}
]
```

The same input ALWAYS returns the same route — that is the point.

## Paste this block into your agent's Instructions

```
ROUTING RULE:
You never decide the business flow yourself. After the intake form is
complete, call the Route action (or follow the topic conditions) and
continue ONLY in the branch it returns. If no rule matches, ask a
clarifying question — do not pick a branch by feeling.
```

## Test

Run the same request 3 times. It passes only if it lands in the same branch all
3 times.
