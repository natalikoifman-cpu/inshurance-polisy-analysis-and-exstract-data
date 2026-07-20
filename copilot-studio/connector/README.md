# Agent Skills custom connector (for Copilot Studio)

One connector, all the skills: intake-form checking, rule routing, smart retrieval
(keyword enrichment + BM25), grounded answers, and consistency verification —
so any Copilot Studio agent your teams build gets the engineered pipeline as
ready-made **actions** instead of hoping the model "figures it out".

```
Copilot Studio agent
   │  (actions)
   ▼
Custom connector  ──  apiDefinition.swagger.json + apiProperties.json  (this folder)
   │  (HTTPS + function key)
   ▼
Azure Function App  ──  backend/  (Python; deterministic code + Azure OpenAI only for the last step)
```

## Actions

| Action | Endpoint | Skill | AI used? |
|---|---|---|---|
| List agent skills | `GET /skills` | playbook | No |
| Check intake form | `POST /intake/check` | 1 | Small model for extraction (optional) |
| Route request by rules | `POST /route` | 3 | **No — pure rules** |
| Retrieve relevant paragraphs | `POST /retrieve` | 4 | **No — keyword enrichment + BM25 in code** |
| Grounded answer | `POST /answer` | 5 | Strong model, sees ONLY the paragraphs |
| Verify answer consistency | `POST /verify` | 5 | Runs the answer twice, compares |
| Ask (full pipeline) | `POST /ask` | 1+4+5 | One call for simple flows |
| Score surprisal | `POST /memory/surprisal` | 10 | Model predicts the next intent vs. what was said — expected input skips memory |
| Extract memory facts | `POST /memory/extract` | 9+10 | Small model distills salient structured facts (category + salience); **novelty gate + ADD/UPDATE/NOOP in code** |
| Consolidate memories | `POST /memory/consolidate` | 11 | Model compresses episodes to stable facts (run on a schedule) |
| Retrieve memories | `POST /memory/retrieve` | 12 | **No — state-aware scoring + associative expansion in code**; optional `rank_mode: its` re-ranks by uncertainty reduction |

### How memory storage works

The connector is **stateless by design**: memory actions take the customer's
current memory list and return the updated one. Your agent stores that list where
your data already lives — a Dataverse table, a CRM field, or a SharePoint list —
keyed by customer id. This keeps customer data in your governed store (see
skill 8) instead of inside the connector. Typical flow:

1. Conversation starts, identity verified → load the customer's memory JSON.
2. Call **Retrieve memories** (query + customer state) → personalize the answer.
3. Conversation ends → call **Extract memory facts** → save `updated_memories`.
4. Nightly Power Automate flow → **Consolidate memories** for active customers.

## Part A — Deploy the backend (once, by IT / whoever deployed the policy-analysis function)

1. Create (or reuse) a **Python 3.12 Azure Function App**.
2. Deploy the [`backend/`](backend/) folder (VS Code Azure Functions extension →
   *Deploy to Function App*, or `func azure functionapp publish <APP-NAME>` from
   inside `backend/`).
3. In the Function App → **Environment variables**, set (same values as the existing
   insurance-policy-analysis project):
   - `AZURE_OPENAI_ENDPOINT` — e.g. `https://<resource>.openai.azure.com`
   - `AZURE_OPENAI_API_KEY`
   - `AZURE_OPENAI_DEPLOYMENT` — your chat deployment name (e.g. `gpt-4o`)
4. Copy a **function key**: Function App → *App keys*.
5. Sanity check in a browser/curl:
   `https://<APP-NAME>.azurewebsites.net/api/skills?code=<FUNCTION-KEY>`
   should return the JSON skill list. (`/route` and `/retrieve` work even
   without the OpenAI settings — they are pure code.)

## Part B — Create the custom connector (once per environment)

1. Edit `apiDefinition.swagger.json`: replace
   `REPLACE-WITH-YOUR-FUNCTION-APP.azurewebsites.net` with your Function App host.
2. Go to [make.powerapps.com](https://make.powerapps.com) → your environment →
   **Custom connectors** → **New custom connector → Import an OpenAPI file** →
   choose `apiDefinition.swagger.json`.
3. Security is already defined (API key in header `x-functions-key`) — just click
   through and **Create connector**.
4. **Test tab** → create a connection using the function key from Part A →
   run *ListSkills*. You should see the twelve skills.

## Part C — Use it in Copilot Studio (every agent builder)

1. In [copilotstudio.microsoft.com](https://copilotstudio.microsoft.com), open your
   agent → **Tools** (or **Actions**) → **Add a tool** → **Connector** → pick
   **Agent Skills Pipeline** → add the actions you need (at minimum:
   *Check intake form*, *Retrieve relevant paragraphs*, *Grounded answer*).
2. Paste a role pack from [`../skills/roles/`](../skills/roles/) into the agent's
   **Instructions** — the instructions tell the agent WHEN to call each action.
3. Test with the checklist at the bottom of the role pack, including asking the
   same question twice (or wiring the *Verify answer consistency* action into a
   test topic).

## Extending

- **New department, same skills:** create a new agent, paste a role pack, reuse the
  same connector. Nothing to redeploy.
- **Domain synonyms:** pass a `synonyms` object to *Retrieve* / *Ask*
  (e.g. `{"רכב": ["אוטו", "מכונית"]}`) — this is where retrieval quality grows.
- **Custom intake forms:** pass `required_fields` with your own field names,
  questions and allowed values; the default is the 5-field insurance intake.
- **New endpoints:** add a function in `backend/function_app.py`, mirror it in the
  swagger, re-import the connector (*Update from OpenAPI file*).
