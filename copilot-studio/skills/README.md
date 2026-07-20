# Agent-Building Skills (for people who don't know AI)

This folder is a **skill library** for building chat agents in Microsoft Copilot Studio
(or any chat-agent platform). It is written for sales, marketing and customer-service
teams — **no prompting knowledge or AI background needed**.

The skills come from one core idea (from our internal guide "Why the agent fails"):

> **The model is ~20% of the result. The pipeline around it is ~80%.**
> A good agent is not "a strong model + paste everything at it".
> A good agent collects the details first, sees only a small relevant slice of data,
> is taught with examples of right AND wrong answers, and proves its consistency.

## The skills

| # | Skill | What it fixes |
|---|-------|---------------|
| 1 | [Intake form](01-intake-form.md) | The agent guesses missing details → it now **asks until the form is full** |
| 2 | [Teach by example](02-teach-by-example.md) | Vague answers → the agent learns from **correct AND incorrect** examples |
| 3 | [Deterministic routing](03-deterministic-routing.md) | The AI "decides" the flow → **rules decide**, the AI only talks |
| 4 | [Smart retrieval](04-smart-retrieval.md) | Dumping a whole file on the model → it sees only **7–8 relevant paragraphs** |
| 5 | [Grounded answer](05-grounded-answer.md) | Made-up answers → answer **only from the retrieved text**, and prove consistency |
| 6 | [Escalate to a human](06-escalate-to-human.md) | The agent bluffs when stuck → clean hand-off with a summary |
| 7 | [Tone and brand](07-tone-and-brand.md) | Robot voice → one consistent company voice, in Hebrew and English |
| 8 | [Privacy and safety](08-privacy-and-safety.md) | Leaking personal/insurance data → clear do/don't rules |
| 9 | [Memory and facts](09-memory-and-facts.md) | Every chat starts from zero → conversations become **clean facts** (ADD/UPDATE/NOOP) |
| 10 | [Surprisal gate](10-surprisal-gate.md) | Memory drowns in noise → **only new information** gets stored |
| 11 | [Memory upkeep](11-memory-upkeep.md) | Stale, contradictory memory → periodic "sleep" **compresses episodes into stable facts** |
| 12 | [Context-aware retrieval](12-context-aware-retrieval.md) | Word-matching only → retrieval weighs the **customer's state** and pulls in **related** memories |

Skills 1–8 come from *"Why the agent fails"*; skills 9–12 come from the companion
guide *"Architecting AI Memory"* (LLM amnesia → structured memory → brain-inspired
memory: surprisal gate, affective routing, sleep consolidation, associative
retrieval).

## Role packs (paste-ready)

Each file under [`roles/`](roles/) is a **complete instruction set** you can paste into
the *Instructions* box of a Copilot Studio agent:

- [`roles/sales-assistant.md`](roles/sales-assistant.md)
- [`roles/marketing-assistant.md`](roles/marketing-assistant.md)
- [`roles/customer-service-assistant.md`](roles/customer-service-assistant.md)

There is also [`00-master-agent-instructions.md`](00-master-agent-instructions.md) —
a role-neutral master version combining skills 1–8.

## How to use (3 minutes)

1. Open [copilotstudio.microsoft.com](https://copilotstudio.microsoft.com) → **Create → New agent**.
2. Open the role pack that matches your team, copy everything inside the code block,
   and paste it into the agent's **Instructions**.
3. Connect the custom connector from [`../connector/`](../connector/) so the agent can
   run the pipeline (intake check, retrieval, grounded answers) as real actions.
4. Test with the checklist at the bottom of each role pack. If the agent guesses,
   invents, or answers differently each run — a skill is missing. Find it in the table
   above and paste the missing block.

## The one rule behind all of this

**The model is the LAST station, not the first.** Collect details → filter data in
code → only then let the model answer, with the question, the relevant paragraphs,
and the instruction "answer only from what is written here".
