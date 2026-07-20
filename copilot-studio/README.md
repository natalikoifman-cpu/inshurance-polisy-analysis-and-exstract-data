# Copilot Studio agent kit — skills + custom connector

A complete kit for building **reliable** chat assistants for the sales, marketing and
customer-service teams — designed for people with **no AI or prompting background**.

Based on our internal guide *"Why the agent fails — and what makes it work"*:
a good model is not a product. The chat sites work because of the invisible pipeline
around the model. This kit gives your agents that pipeline.

> **The model is ~20% of the result. The pipeline around it is ~80%.**

## What's inside

| Folder | What | Who uses it |
|---|---|---|
| [`skills/`](skills/) | 12 agent-building skills + paste-ready role packs (sales / marketing / customer service) | Anyone building an agent — just copy & paste |
| [`connector/`](connector/) | A Power Platform **custom connector** + Azure Functions backend that runs the pipeline (intake check, routing, BM25 retrieval, grounded answers, consistency verification, customer memory) | IT deploys once; every agent reuses it |

Skills 1–8 implement the engineered-agent pipeline (*"Why the agent fails"*).
Skills 9–12 add **customer memory** (*"Architecting AI Memory"*): conversations are
distilled into clean facts behind a novelty gate, consolidated on a schedule, and
retrieved with awareness of the customer's state — so the agent that helped a
customer yesterday actually remembers them today.

## The 10-minute path to a working agent

1. **IT, once:** deploy `connector/backend/` to an Azure Function App and import
   `connector/apiDefinition.swagger.json` as a custom connector
   (full steps in [`connector/README.md`](connector/README.md)).
2. **You:** create an agent in [Copilot Studio](https://copilotstudio.microsoft.com),
   paste the matching role pack from [`skills/roles/`](skills/roles/) into
   *Instructions*, and add the connector's actions as tools.
3. **Before launch:** run the quick tests at the bottom of the role pack —
   including asking the same question twice and checking you get the same answer.

## Why agents built this way work

| | Naive agent | With this kit |
|---|---|---|
| Missing customer detail | Guesses and continues | **Stops and asks until the form is full** |
| What the model sees | The whole file | **Only 7–8 relevant paragraphs** |
| Business-flow decisions | The AI "feels" a branch | **Rules decide — same input, same route** |
| Answer quality | Confident guesses | **Only from the documents, with the clause referenced** |
| Consistency | Different answer every run | **Identical — and provable via the Verify action** |
| Cost per answer | High (sends everything) | Low (sends a focused page) |
| Returning customer | Starts from zero every chat | **Remembered: clean facts, no re-asking, no contradictions** |
