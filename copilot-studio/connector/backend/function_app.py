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
