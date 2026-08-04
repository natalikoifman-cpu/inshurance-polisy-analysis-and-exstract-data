# Skill 3 — Deterministic routing: rules decide, the AI talks

**Problem it fixes:** "if the request type is X go to flow A, if Y go to flow B"
— when the AI makes these decisions it guesses, and every run can go a different
way. Rules written as rules are checked, testable, free, and always the same.

## How it works

Once the intake form is complete (Skill 1), the NEXT STEP is chosen by plain
conditions — platform topic/branch nodes, or a **Route** action in your backend.
The AI never picks the branch; it only handles the conversation inside the
branch.

## Example rule set (swap in your own topics)

```
if topic = {defect}                     → returns/repair flow (collect photos, open ticket)
if topic = {pricing} and {segment} = business → business-quote flow
if topic = {pricing} and {segment} = private  → standard-quote flow
if topic = {billing}                    → billing flow (verify identity first)
if topic = {complaint}                  → escalate to human (Skill 6)
otherwise                               → clarify topic (Skill 1 question loop)
```

The same shape fits any use case: a clinic routes
`appointment_type = urgent → same-day flow`; an IT helpdesk routes
`topic = access → identity-verification flow`.

## With a backend action

Call your **Route request** action with the filled form and your rule list.
Rules are JSON like:

```json
[
  {"when": [{"field": "topic", "op": "eq", "value": "defect"}], "route": "returns"},
  {"when": [{"field": "topic", "op": "eq", "value": "pricing"},
             {"field": "segment", "op": "eq", "value": "business"}], "route": "business_quote"},
  {"when": [], "route": "clarify"}
]
```

The same input ALWAYS returns the same route — that is the point.

## Paste this block into your agent's instructions

```
ROUTING RULE:
You never decide the business flow yourself. After the intake form is
complete, call the Route action (or follow the flow conditions) and
continue ONLY in the branch it returns. If no rule matches, ask a
clarifying question — do not pick a branch by feeling.
```

## Test

Run the same request 3 times. It passes only if it lands in the same branch all
3 times.
