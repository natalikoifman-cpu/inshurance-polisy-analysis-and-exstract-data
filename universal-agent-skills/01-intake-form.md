# Skill 1 — Intake form: ask until the form is full

**Problem it fixes:** an agent that guesses missing details is an agent that is
wrong. A clerk never processes a half-empty form — neither should your agent.

Works for any use case: a support ticket, a price quote, a clinic appointment,
a delivery inquiry, an HR request — only the field names change.

## How it works

1. Decide **up front** which details are mandatory before the agent may act
   (typically 3–6 fields).
2. The agent extracts what the user already wrote.
3. For **every missing field** it asks one short, targeted question.
4. The flow continues **only when the form is complete**. No guessing, ever.

## Paste this block into your agent's instructions (fill the placeholders)

```
INTAKE RULE (always first):
Before answering any request, fill this form from what the user wrote:
- {field_1}            e.g. full_name
- {field_2}            e.g. customer_or_order_id
- topic (one of: {topic_1}, {topic_2}, {topic_3}, other)
- {field_4}            e.g. product / service / department
- description (what happened / what is needed)

If a field is missing, DO NOT guess and DO NOT answer yet.
Reply with: what you already understood + ONE short question for the
most important missing field. Example:
"Got it — this is about {topic} for {product}. To help, I also need
your {missing_field} — what is it?"
Repeat until all fields are filled. Only then continue.
Never invent a value for a field the user did not provide.
```

### Filled examples for three different use cases

| Use case | Required fields |
|---|---|
| Webshop support | `full_name, order_number, topic (delivery/return/payment/product), product, description` |
| Clinic bookings | `patient_name, patient_id, appointment_type, preferred_dates, referring_doctor` |
| IT helpdesk | `employee_name, device_or_system, topic (access/bug/hardware/how-to), urgency, description` |

## With a backend action

Call your **Check intake form** action with the required fields and the
conversation text. It should return `complete: true/false`, the filled fields,
the list of missing ones, and a ready-to-send `next_question`. Loop until
`complete` is true.

## Good vs. bad

| | |
|---|---|
| ❌ Bad | User: "How much will it cost?" → Agent quotes a random price for a guessed product |
| ✅ Good | Agent: "Happy to help with a quote. Which product is this for — and for how many users?" |

## Test

Ask your agent a question while leaving out an obvious detail. It passes only if
it asks for the missing detail instead of answering.
