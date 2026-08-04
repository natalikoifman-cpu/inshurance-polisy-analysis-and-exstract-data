# Universal Agent-Building Skills

A **platform-agnostic, domain-agnostic skill library** for building reliable AI
agents — for ANY use case: customer service, sales, operations, healthcare
bookings, e-commerce, internal knowledge, HR, logistics, education, and anything
else. No prompting knowledge or AI background needed.

Works with any agent platform: Microsoft Copilot Studio, OpenAI GPTs /
Assistants, Claude (skills / MCP tools), Dialogflow, Rasa, LangGraph, or your
own stack. Every skill is a copy-paste block plus an optional backend action —
wherever you see **"action"**, map it to your platform's equivalent:

| Platform | "Action" means |
|---|---|
| Copilot Studio | Custom connector action / topic node |
| OpenAI GPTs / Assistants | Action (OpenAPI) / function tool |
| Claude | Skill / MCP tool |
| Your own code | An API endpoint or function call |

The library comes from one core idea:

> **The model is ~20% of the result. The pipeline around it is ~80%.**
> A good agent is not "a strong model + paste everything at it".
> A good agent collects the details first, sees only a small relevant slice of
> data, is taught with examples of right AND wrong answers, and proves its
> consistency.

## The skills

| # | Skill | What it fixes |
|---|-------|---------------|
| 1 | [Intake form](01-intake-form.md) | The agent guesses missing details → it now **asks until the form is full** |
| 2 | [Teach by example](02-teach-by-example.md) | Vague answers → the agent learns from **correct AND incorrect** examples |
| 3 | [Deterministic routing](03-deterministic-routing.md) | The AI "decides" the flow → **rules decide**, the AI only talks |
| 4 | [Smart retrieval](04-smart-retrieval.md) | Dumping a whole file on the model → it sees only **7–8 relevant paragraphs** |
| 5 | [Grounded answer](05-grounded-answer.md) | Made-up answers → answer **only from the retrieved text**, and prove consistency |
| 6 | [Escalate to a human](06-escalate-to-human.md) | The agent bluffs when stuck → clean hand-off with a summary |
| 7 | [Tone and brand](07-tone-and-brand.md) | Robot voice → one consistent company voice, in any language |
| 8 | [Privacy and safety](08-privacy-and-safety.md) | Leaking personal data → clear do/don't rules |
| 9 | [Memory and facts](09-memory-and-facts.md) | Every chat starts from zero → conversations become **clean facts** (ADD/UPDATE/NOOP) |
| 10 | [Surprisal gate](10-surprisal-gate.md) | Memory drowns in noise → **only new information** gets stored |
| 11 | [Memory upkeep](11-memory-upkeep.md) | Stale, contradictory memory → periodic "sleep" **compresses episodes into stable facts** |
| 12 | [Context-aware retrieval](12-context-aware-retrieval.md) | Word-matching only → retrieval weighs the **user's state** and pulls in **related** memories |

## Adapting to YOUR use case (10 minutes)

Every skill contains `{placeholders}`. To specialize the library:

1. **Define your form** — pick the 3–6 fields your agent must know before acting
   (Skill 1). A clinic: `patient_name, appointment_type, preferred_date`. A
   webshop: `order_number, issue_type, product`.
2. **List your topics** — the finite set of request types you handle (Skill 2)
   and the routing rule per topic (Skill 3).
3. **Point retrieval at your data** — product sheets, help center, price list,
   protocols (Skills 4–5).
4. **Write your escalation triggers and privacy lines** (Skills 6, 8) — what
   must always reach a human, what must never be revealed.
5. Paste the filled blocks into your agent's instructions, or start from a
   [role pack](roles/).

## Role packs (paste-ready)

Each file under [`roles/`](roles/) is a **complete instruction set** for a common
role — copy, fill the placeholders, paste:

- [`roles/_template-role-pack.md`](roles/_template-role-pack.md) — **build your
  own role for any use case** (start here if none of the below fits)
- [`roles/customer-service-assistant.md`](roles/customer-service-assistant.md)
- [`roles/sales-assistant.md`](roles/sales-assistant.md)
- [`roles/marketing-assistant.md`](roles/marketing-assistant.md)

There is also
[`00-master-agent-instructions.md`](00-master-agent-instructions.md) — a
role-neutral master version combining skills 1–12.

## The one rule behind all of this

**The model is the LAST station, not the first.** Collect details → filter data
in code → only then let the model answer, with the question, the relevant
paragraphs, and the instruction "answer only from what is written here".

## Quality checklist for any agent you build

- [ ] The agent asks instead of guessing when a required detail is missing
- [ ] The same request always lands in the same flow branch
- [ ] Answers quote the retrieved text; "not found" is an allowed answer
- [ ] The same question asked twice returns the same answer
- [ ] Escalation triggers are written down and tested
- [ ] The agent refuses to reveal data before identity is verified
- [ ] Memory stores facts, never transcripts — and only surprising ones
