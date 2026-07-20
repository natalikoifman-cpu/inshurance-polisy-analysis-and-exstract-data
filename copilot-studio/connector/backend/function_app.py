"""Agent Skills API — the backend of the Copilot Studio custom connector.

Endpoints (all under /api):
  GET  /skills        list the skill playbook
  POST /intake/check  Skill 1: fill the form, report missing fields + next question
  POST /route         Skill 3: deterministic rule routing
  POST /retrieve      Skill 4: keyword enrichment + BM25 -> top paragraphs
  POST /answer        Skill 5: grounded answer from supplied paragraphs only
  POST /verify        Skill 5: run the same answer twice, compare for consistency
  POST /ask           the whole pipeline in one call (intake -> retrieve -> answer)

Deterministic stages (route, retrieve, intake completeness) run with no LLM at
all. LLM stages need the AZURE_OPENAI_* application settings (see llm.py).
"""

import json
import logging

import azure.functions as func

import llm
import pipeline

app = func.FunctionApp(http_auth_level=func.AuthLevel.FUNCTION)

DEFAULT_REQUIRED_FIELDS = [
    {"name": "full_name", "question": "מה שמך המלא?",
     "description": "the customer's full name"},
    {"name": "customer_or_policy_id", "question": "מה מספר הפוליסה או תעודת הזהות שלך?",
     "description": "policy number or customer id"},
    {"name": "topic", "question": "במה מדובר — הצעת מחיר, תביעה, חיוב, כיסוי או תלונה?",
     "description": "what the customer needs",
     "allowed": ["quote", "claim", "billing", "coverage", "complaint", "other"],
     "example_correct": "'נגנב לי האופניים' -> claim",
     "example_incorrect": "'נגנב לי האופניים' -> coverage (it is an event report, not a coverage question)"},
    {"name": "product", "question": "לאיזה מוצר זה נוגע — רכב, דירה, בריאות, חיים או עסק?",
     "description": "which insurance product"},
    {"name": "description", "question": "ספר/י בקצרה מה קרה או מה צריך.",
     "description": "short free-text description of the need"},
]


def _json(req: func.HttpRequest) -> dict:
    try:
        return req.get_json()
    except ValueError:
        return {}


def _ok(payload: dict, status: int = 200) -> func.HttpResponse:
    return func.HttpResponse(json.dumps(payload, ensure_ascii=False),
                             status_code=status, mimetype="application/json")


def _error(message: str, status: int = 400) -> func.HttpResponse:
    return _ok({"error": message}, status)


@app.route(route="skills", methods=["GET"])
def skills(req: func.HttpRequest) -> func.HttpResponse:
    return _ok({"skills": [
        {"id": 1, "name": "Intake form", "action": "CheckIntakeForm",
         "rule": "Ask until the form is full — never guess a missing detail."},
        {"id": 2, "name": "Teach by example", "action": None,
         "rule": "Every field/answer gets a correct AND an incorrect example."},
        {"id": 3, "name": "Deterministic routing", "action": "RouteRequest",
         "rule": "Rules decide the flow; the AI only talks inside the branch."},
        {"id": 4, "name": "Smart retrieval", "action": "RetrieveParagraphs",
         "rule": "From the whole file to 7-8 relevant paragraphs, in code."},
        {"id": 5, "name": "Grounded answer", "action": "GroundedAnswer / VerifyConsistency",
         "rule": "Answer only from the retrieved text; prove consistency by re-running."},
        {"id": 6, "name": "Escalate to human", "action": None,
         "rule": "Hand off with a summary when stuck — never bluff."},
        {"id": 7, "name": "Tone and brand", "action": None,
         "rule": "One company voice: confirm, answer, next step."},
        {"id": 8, "name": "Privacy and safety", "action": None,
         "rule": "Verify identity first; minimum necessary data; one customer per chat."},
        {"id": 9, "name": "Memory and facts", "action": "ExtractMemoryFacts",
         "rule": "Distill conversations into salient structured facts (category + salience, "
                 "ADD/UPDATE/NOOP) — store facts, never transcripts."},
        {"id": 10, "name": "Surprisal gate", "action": "ScoreSurprisal / ExtractMemoryFacts",
         "rule": "Predict the user's next intent, compare with what they actually said — "
                 "only surprising input is stored; expected input is rejected at write time."},
        {"id": 11, "name": "Memory upkeep", "action": "ConsolidateMemories",
         "rule": "Periodically compress episodes into stable facts; newer facts win contradictions."},
        {"id": 12, "name": "Context-aware retrieval", "action": "RetrieveMemories",
         "rule": "Blend text relevance with customer state; related memories join via association; "
                 "rank_mode 'its' re-ranks by uncertainty reduction, not word overlap."},
    ]})


@app.route(route="intake/check", methods=["POST"])
def intake_check(req: func.HttpRequest) -> func.HttpResponse:
    body = _json(req)
    required = body.get("required_fields") or DEFAULT_REQUIRED_FIELDS
    known = body.get("known_fields") or {}
    conversation = body.get("conversation_text", "")

    extracted = {}
    if conversation:
        try:
            extracted = llm.extract_fields(required, conversation)
        except llm.LLMNotConfigured:
            logging.warning("LLM not configured — intake uses known_fields only")
        except RuntimeError as err:
            return _error(str(err), 502)

    merged = {**extracted, **known}  # explicitly confirmed values win
    result = pipeline.check_intake(required, merged)
    return _ok(result)


@app.route(route="route", methods=["POST"])
def route_request(req: func.HttpRequest) -> func.HttpResponse:
    body = _json(req)
    rules, fields = body.get("rules"), body.get("fields")
    if not isinstance(rules, list) or not isinstance(fields, dict):
        return _error("Body must contain 'rules' (list) and 'fields' (object).")
    return _ok(pipeline.route(rules, fields))


@app.route(route="retrieve", methods=["POST"])
def retrieve(req: func.HttpRequest) -> func.HttpResponse:
    body = _json(req)
    question = body.get("question", "").strip()
    document_text = body.get("document_text", "")
    if not question or not document_text:
        return _error("Body must contain 'question' and 'document_text'.")
    result = pipeline.retrieve(
        question, document_text,
        top_k=int(body.get("top_k", 8)),
        synonyms=body.get("synonyms") or {},
    )
    return _ok(result)


@app.route(route="answer", methods=["POST"])
def answer(req: func.HttpRequest) -> func.HttpResponse:
    body = _json(req)
    question = body.get("question", "").strip()
    paragraphs = body.get("paragraphs") or []
    if not question or not paragraphs:
        return _error("Body must contain 'question' and 'paragraphs' (non-empty list).")
    try:
        text = llm.grounded_answer(question, paragraphs,
                                   language=body.get("language", "he"),
                                   extra_instructions=body.get("extra_instructions", ""))
    except llm.LLMNotConfigured as err:
        return _error(str(err), 503)
    except RuntimeError as err:
        return _error(str(err), 502)
    return _ok({"answer": text, "paragraphs_used": len(paragraphs)})


@app.route(route="verify", methods=["POST"])
def verify(req: func.HttpRequest) -> func.HttpResponse:
    body = _json(req)
    question = body.get("question", "").strip()
    paragraphs = body.get("paragraphs") or []
    if not question or not paragraphs:
        return _error("Body must contain 'question' and 'paragraphs' (non-empty list).")
    try:
        first = llm.grounded_answer(question, paragraphs, language=body.get("language", "he"))
        second = llm.grounded_answer(question, paragraphs, language=body.get("language", "he"))
    except llm.LLMNotConfigured as err:
        return _error(str(err), 503)
    except RuntimeError as err:
        return _error(str(err), 502)
    return _ok({"consistent": first.strip() == second.strip(),
                "answer_1": first, "answer_2": second})


@app.route(route="memory/surprisal", methods=["POST"])
def memory_surprisal(req: func.HttpRequest) -> func.HttpResponse:
    """Skill 10 (mnemos SurprisalGate): predict the user's next intent from the
    history, compare with what they actually said, return a surprisal score.
    keep=false means the message is expected/routine — skip memory entirely."""
    body = _json(req)
    latest = body.get("latest_message", "").strip()
    if not latest:
        return _error("Body must contain 'latest_message'.")
    threshold = float(body.get("threshold", 0.3))
    try:
        result = llm.score_surprisal(body.get("conversation_history", ""), latest)
    except llm.LLMNotConfigured as err:
        return _error(str(err), 503)
    except RuntimeError as err:
        return _error(str(err), 502)
    result["threshold"] = threshold
    result["keep"] = result["surprisal"] >= threshold
    return _ok(result)


@app.route(route="memory/extract", methods=["POST"])
def memory_extract(req: func.HttpRequest) -> func.HttpResponse:
    """Skills 9+10: optional surprisal gate on the whole message -> salient
    structured facts (LLM) -> salience filter + novelty gate and
    ADD/UPDATE/NOOP decisions (code) -> updated memory list."""
    body = _json(req)
    memories = body.get("memories") or []
    conversation = body.get("conversation_text", "").strip()
    threshold = float(body.get("novelty_threshold", 0.3))
    min_salience = float(body.get("min_salience", 0.0))

    gate = None
    if body.get("surprisal_mode") == "llm":
        if not conversation:
            return _error("surprisal_mode 'llm' requires 'conversation_text'.")
        try:
            gate = llm.score_surprisal(body.get("conversation_history", ""), conversation)
        except llm.LLMNotConfigured as err:
            return _error(str(err), 503)
        except RuntimeError as err:
            return _error(str(err), 502)
        gate["threshold"] = threshold
        gate["keep"] = gate["surprisal"] >= threshold
        if not gate["keep"]:
            return _ok({"operations": [], "updated_memories": memories,
                        "stored": 0, "rejected_by_gate": 1, "gate": gate})

    candidates = body.get("candidate_facts")  # optional: skip the LLM step
    if candidates is None:
        if not conversation:
            return _error("Body must contain 'conversation_text' (or 'candidate_facts').")
        try:
            candidates = llm.extract_facts(conversation, memories)
        except llm.LLMNotConfigured as err:
            return _error(str(err), 503)
        except RuntimeError as err:
            return _error(str(err), 502)
    if min_salience > 0:
        candidates = [c for c in candidates
                      if not isinstance(c, dict)  # plain strings are trusted as-is
                      or float(c.get("salience", 1.0)) >= min_salience]
    operations = pipeline.decide_operations(candidates, memories, threshold)
    updated = pipeline.apply_operations(memories, operations,
                                        customer_state=body.get("customer_state"))
    payload = {"operations": operations, "updated_memories": updated,
               "stored": sum(1 for o in operations if o["op"] != "NOOP"),
               "rejected_by_gate": sum(1 for o in operations if o["op"] == "NOOP")}
    if gate is not None:
        payload["gate"] = gate
    return _ok(payload)


@app.route(route="memory/consolidate", methods=["POST"])
def memory_consolidate(req: func.HttpRequest) -> func.HttpResponse:
    """Skill 11: the sleep daemon — compress episodic memories into stable facts."""
    body = _json(req)
    memories = body.get("memories") or []
    if not memories:
        return _error("Body must contain 'memories' (non-empty list).")
    try:
        consolidated = llm.consolidate_memories(memories)
    except llm.LLMNotConfigured as err:
        return _error(str(err), 503)
    except RuntimeError as err:
        return _error(str(err), 502)
    before, after = len(memories), len(consolidated)
    kept_ids = {m.get("id") for m in consolidated}
    return _ok({"memories": consolidated, "before": before, "after": after,
                "compression_ratio": round(before / after, 2) if after else None,
                "removed_ids": [m.get("id") for m in memories
                                if m.get("id") not in kept_ids]})


@app.route(route="memory/retrieve", methods=["POST"])
def memory_retrieve(req: func.HttpRequest) -> func.HttpResponse:
    """Skill 12: state-aware + associative memory retrieval (pure code).
    rank_mode 'its' adds a Memanto-style re-rank: candidates are re-scored by
    how much each fact reduces uncertainty about the query — not word overlap."""
    body = _json(req)
    query = body.get("query", "").strip()
    memories = body.get("memories") or []
    if not query or not memories:
        return _error("Body must contain 'query' and 'memories' (non-empty list).")
    top_k = int(body.get("top_k", 6))
    rank_mode = body.get("rank_mode", "fast")
    fetch_k = top_k * 2 if rank_mode == "its" else top_k
    result = pipeline.memory_retrieve(
        memories, query,
        customer_state=body.get("customer_state"),
        top_k=fetch_k,
        synonyms=body.get("synonyms") or {},
    )
    result["rank_mode_used"] = "fast"
    if rank_mode == "its" and result["memories"]:
        try:
            its_scores = llm.its_rerank(query, [e["memory"] for e in result["memories"]])
        except llm.LLMNotConfigured:
            result["note"] = ("rank_mode 'its' requires the AZURE_OPENAI_* settings; "
                              "returned the deterministic ranking instead.")
            result["memories"] = result["memories"][:top_k]
            return _ok(result)
        except RuntimeError as err:
            return _error(str(err), 502)
        for entry in result["memories"]:
            entry["its_score"] = its_scores.get(str(entry["memory"].get("id")), 0.0)
        result["memories"] = sorted(
            result["memories"],
            key=lambda e: (-e.get("its_score", 0.0), -e["score"],
                           str(e["memory"].get("id"))))[:top_k]
        result["rank_mode_used"] = "its"
    return _ok(result)


@app.route(route="ask", methods=["POST"])
def ask(req: func.HttpRequest) -> func.HttpResponse:
    """The whole engineered pipeline in one call, for simple Copilot Studio flows."""
    body = _json(req)
    question = body.get("question", "").strip()
    document_text = body.get("document_text", "")
    if not question or not document_text:
        return _error("Body must contain 'question' and 'document_text'.")

    required = body.get("required_fields")  # optional intake gate
    if required:
        intake = pipeline.check_intake(required, body.get("known_fields") or {})
        if not intake["complete"]:
            return _ok({"status": "need_more_info", "intake": intake, "answer": None})

    retrieval = pipeline.retrieve(question, document_text,
                                  top_k=int(body.get("top_k", 8)),
                                  synonyms=body.get("synonyms") or {})
    if not retrieval["found"]:
        return _ok({"status": "not_found", "retrieval": retrieval,
                    "answer": "לא מצאתי תשובה לזה במסמכים — מומלץ להעביר לנציג אנושי."})
    try:
        text = llm.grounded_answer(question,
                                   [p["text"] for p in retrieval["paragraphs"]],
                                   language=body.get("language", "he"))
    except llm.LLMNotConfigured as err:
        return _error(str(err), 503)
    except RuntimeError as err:
        return _error(str(err), 502)
    return _ok({"status": "answered", "answer": text,
                "retrieval": {"total_paragraphs": retrieval["total_paragraphs"],
                              "paragraphs": retrieval["paragraphs"]}})
