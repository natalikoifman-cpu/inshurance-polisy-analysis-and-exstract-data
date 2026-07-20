"""Azure OpenAI helper — the LLM is called ONLY here, and only as the last stage.

Configuration (application settings on the Function App, same style as the
existing insurance-policy-analysis project):
  AZURE_OPENAI_ENDPOINT    e.g. https://myresource.openai.azure.com
  AZURE_OPENAI_API_KEY
  AZURE_OPENAI_DEPLOYMENT  chat deployment name (e.g. gpt-4o)
  AZURE_OPENAI_API_VERSION optional, default 2024-06-01
"""

from __future__ import annotations

import json
import os
import urllib.error
import urllib.request


class LLMNotConfigured(RuntimeError):
    pass


def _settings() -> dict:
    endpoint = os.environ.get("AZURE_OPENAI_ENDPOINT", "").rstrip("/")
    key = os.environ.get("AZURE_OPENAI_API_KEY", "")
    deployment = os.environ.get("AZURE_OPENAI_DEPLOYMENT", "")
    if not (endpoint and key and deployment):
        raise LLMNotConfigured(
            "Set AZURE_OPENAI_ENDPOINT, AZURE_OPENAI_API_KEY and "
            "AZURE_OPENAI_DEPLOYMENT on the Function App."
        )
    version = os.environ.get("AZURE_OPENAI_API_VERSION", "2024-06-01")
    return {"endpoint": endpoint, "key": key, "deployment": deployment, "version": version}


def chat(system: str, user: str, temperature: float = 0.0, max_tokens: int = 1000) -> str:
    cfg = _settings()
    url = (f"{cfg['endpoint']}/openai/deployments/{cfg['deployment']}"
           f"/chat/completions?api-version={cfg['version']}")
    body = json.dumps({
        "messages": [{"role": "system", "content": system},
                     {"role": "user", "content": user}],
        "temperature": temperature,
        "max_tokens": max_tokens,
    }).encode("utf-8")
    request = urllib.request.Request(
        url, data=body,
        headers={"Content-Type": "application/json", "api-key": cfg["key"]},
    )
    try:
        with urllib.request.urlopen(request, timeout=120) as response:
            payload = json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as err:
        raise RuntimeError(f"Azure OpenAI error {err.code}: {err.read().decode('utf-8', 'ignore')[:500]}")
    return payload["choices"][0]["message"]["content"]


def extract_fields(required_fields: list[dict], conversation_text: str) -> dict:
    """Small-model job: pull the required fields out of what the user wrote.
    Returns {} for anything not explicitly present — guessing is forbidden."""
    field_lines = []
    for field in required_fields:
        line = f'- "{field.get("name")}": {field.get("description", field.get("question", ""))}'
        if field.get("allowed"):
            line += f' (one of: {", ".join(field["allowed"])})'
        if field.get("example_correct"):
            line += f' | correct: {field["example_correct"]}'
        if field.get("example_incorrect"):
            line += f' | INCORRECT (do not do this): {field["example_incorrect"]}'
        field_lines.append(line)
    system = (
        "You extract form fields from a customer conversation.\n"
        "Fields to extract:\n" + "\n".join(field_lines) + "\n"
        "Rules: output ONLY a JSON object with these field names. If a field is not "
        "explicitly stated by the user, set it to \"\" — NEVER guess or infer missing "
        "values. Do not add fields. Do not add commentary.\n"
        'Example output: {"full_name": "דנה לוי", "policy_id": "", "topic": "claim"}'
    )
    raw = chat(system, conversation_text, temperature=0.0, max_tokens=500)
    raw = raw.strip().removeprefix("```json").removeprefix("```").removesuffix("```").strip()
    try:
        data = json.loads(raw)
        return data if isinstance(data, dict) else {}
    except json.JSONDecodeError:
        return {}


def _parse_json_list(raw: str) -> list:
    raw = raw.strip().removeprefix("```json").removeprefix("```").removesuffix("```").strip()
    try:
        data = json.loads(raw)
        return data if isinstance(data, list) else []
    except json.JSONDecodeError:
        return []


def _parse_json_obj(raw: str) -> dict:
    raw = raw.strip().removeprefix("```json").removeprefix("```").removesuffix("```").strip()
    try:
        data = json.loads(raw)
        return data if isinstance(data, dict) else {}
    except json.JSONDecodeError:
        return {}


def extract_facts(conversation_text: str, existing_memories: list[dict]) -> list[dict]:
    """Small-model job (Mem0-style salient extraction): distill a conversation
    into STRUCTURED candidate facts — {text, category, salience} — leaving the
    unstructured noise behind. The novelty gate (code, not LLM) decides
    afterwards what actually gets stored."""
    known = "\n".join(f"- {m.get('text', '')}" for m in existing_memories) or "- (none)"
    system = (
        "You distill a customer conversation into short, clean, SALIENT memory facts.\n"
        "A fact is worth keeping ONLY if it would change how a colleague handles the\n"
        "NEXT conversation: preferences, decisions, corrections, commitments, open\n"
        "problems, life/policy changes.\n"
        "NEVER output: greetings, small talk, politeness, one-off conversation\n"
        "mechanics, or facts already in the known list below.\n"
        "For each fact output:\n"
        '- "text": one short self-contained sentence, in the customer\'s language.\n'
        '- "category": one of preference / decision / correction / commitment /\n'
        "  problem / profile.\n"
        '- "salience": 0.0-1.0 — how much this matters for future conversations\n'
        "  (a policy decision ~0.9; a mild preference ~0.6; a passing detail ~0.3).\n"
        'CORRECT: {"text": "מעדיף תקשורת במייל ולא בטלפון", "category": "preference", "salience": 0.7}\n'
        'INCORRECT: {"text": "הלקוח אמר תודה ושיהיה יום טוב", ...} (small talk — never output)\n'
        "Already known facts:\n" + known + "\n"
        "Output ONLY a JSON array of these objects. If nothing is worth keeping, output []."
    )
    facts = []
    for item in _parse_json_list(chat(system, conversation_text, temperature=0.0, max_tokens=800)):
        if isinstance(item, str) and item.strip():
            facts.append({"text": item.strip(), "category": "profile", "salience": 0.5})
        elif isinstance(item, dict) and str(item.get("text", "")).strip():
            try:
                salience = min(1.0, max(0.0, float(item.get("salience", 0.5))))
            except (TypeError, ValueError):
                salience = 0.5
            facts.append({"text": str(item["text"]).strip(),
                          "category": str(item.get("category", "profile")),
                          "salience": salience})
    return facts


def score_surprisal(conversation_history: str, latest_message: str) -> dict:
    """mnemos SurprisalGate: predict the user's next intent from the history,
    compare the prediction with what the user ACTUALLY said, and return a
    surprisal score — only surprising input deserves memory."""
    system = (
        "You are the surprisal gate of an agent's memory (predictive coding).\n"
        "Step 1: from the conversation history ALONE, predict the user's most\n"
        "likely next message or intent.\n"
        "Step 2: compare your prediction with what the user actually said.\n"
        "Step 3: output a surprisal score between 0.0 and 1.0:\n"
        "- 0.0-0.2 fully expected: greetings, thanks, confirmations, an answer in\n"
        "  the expected format to a question the agent just asked.\n"
        "- 0.3-0.6 partly new: a detail or preference not predictable from the\n"
        "  history.\n"
        "- 0.7-1.0 genuinely surprising: a change of plan, a correction of a known\n"
        "  fact, a new problem, an unexpected request.\n"
        'CORRECT: history asks "מה מספר הפוליסה?" and the user answers with a\n'
        'number -> surprisal ~0.1 (expected). The user instead says "בעצם אני רוצה\n'
        'לבטל את הפוליסה" -> surprisal ~0.9.\n'
        'Output ONLY JSON: {"predicted_intent": "...", "surprisal": 0.0, "reason": "..."}'
    )
    user = (f"Conversation history:\n{conversation_history or '(empty)'}\n\n"
            f"User's actual latest message:\n{latest_message}")
    result = _parse_json_obj(chat(system, user, temperature=0.0, max_tokens=300))
    try:
        surprisal = min(1.0, max(0.0, float(result.get("surprisal"))))
    except (TypeError, ValueError):
        surprisal = 0.5  # unparseable -> uncertain -> lean toward keeping
    return {"predicted_intent": str(result.get("predicted_intent", "")),
            "surprisal": surprisal,
            "reason": str(result.get("reason", ""))}


def its_rerank(query: str, candidates: list[dict]) -> dict:
    """Memanto-style ITS (Information Theoretic Score): rate each candidate
    memory by how much it REDUCES UNCERTAINTY about answering the query —
    not by surface word similarity. Returns {memory_id: its_score}."""
    listing = json.dumps([{"id": c.get("id"), "text": c.get("text")}
                          for c in candidates], ensure_ascii=False)
    system = (
        "You rank an agent's memories by INFORMATION VALUE for the current query.\n"
        "For each memory, score 0.0-1.0: how much does knowing this fact reduce\n"
        "the uncertainty about the right way to answer/act on the query?\n"
        "- A fact that changes the answer or the required action: 0.8-1.0.\n"
        "- A fact that adds useful context but does not change the action: 0.4-0.7.\n"
        "- A fact that merely shares words with the query without informing the\n"
        "  decision: 0.0-0.3 — word overlap alone is worth nothing.\n"
        'CORRECT: query "להוסיף נהג צעיר לפוליסה" + memory "לרכב יש כיסוי צד ג\'\n'
        'בלבד" -> high (changes what can be offered). Memory "שאל בעבר שאלה על\n'
        'נהגים" -> low (similar words, no decision value).\n'
        'Output ONLY JSON: [{"id": "...", "its": 0.0}, ...] — one entry per memory.'
    )
    user = f"Query: {query}\n\nMemories:\n{listing}"
    scores = {}
    for item in _parse_json_list(chat(system, user, temperature=0.0, max_tokens=600)):
        if isinstance(item, dict) and item.get("id") is not None:
            try:
                scores[str(item["id"])] = min(1.0, max(0.0, float(item.get("its"))))
            except (TypeError, ValueError):
                continue
    return scores


def consolidate_memories(memories: list[dict]) -> list[dict]:
    """Sleep-daemon job: compress episodic memories into few stable facts,
    resolve contradictions (newer wins), drop noise. Returns the new list."""
    listing = json.dumps(memories, ensure_ascii=False)
    system = (
        "You are the memory consolidation step (the 'sleep' phase) of an agent.\n"
        "Input: a JSON list of memory objects {id, text, state?} in chronological\n"
        "order (earlier first). Produce the SMALLEST list of stable facts that\n"
        "preserves everything a colleague would need next conversation.\n"
        "Rules:\n"
        "- Merge repeated episodes into one semantic fact (e.g. three billing\n"
        "  complaints -> one fact about a recurring billing issue).\n"
        "- On contradictions, keep ONLY the newer fact (later in the list).\n"
        "- Drop small talk, one-off mechanics, and anything no longer useful.\n"
        "- Keep each fact's original id when unchanged; merged/new facts get the\n"
        "  id of the newest memory they came from. Keep 'state' when present.\n"
        "- Never invent information that is not in the input.\n"
        "Output ONLY a JSON array of {id, text, state?} objects."
    )
    result = _parse_json_list(chat(system, listing, temperature=0.0, max_tokens=1200))
    cleaned = [m for m in result
               if isinstance(m, dict) and str(m.get("text", "")).strip()]
    return cleaned if cleaned else memories


def grounded_answer(question: str, paragraphs: list[str], language: str = "he",
                    extra_instructions: str = "") -> str:
    """Strong-model job: answer ONLY from the retrieved paragraphs."""
    not_found = ("לא מצאתי תשובה לזה במסמכים — מומלץ להעביר לנציג אנושי."
                 if language == "he" else
                 "I could not find the answer in the documents — please hand off to a human.")
    numbered = "\n\n".join(f"[{i + 1}] {p}" for i, p in enumerate(paragraphs))
    system = (
        "You answer customer questions using ONLY the numbered paragraphs provided.\n"
        "Rules:\n"
        "- Use only facts that appear word-for-word or unambiguously in the paragraphs.\n"
        f"- If the answer is not in the paragraphs, reply exactly: \"{not_found}\"\n"
        "- Reference the paragraph number(s) you used, e.g. [2].\n"
        "- Never estimate prices, amounts, dates or conditions not present in the text.\n"
        "- Answer in the customer's language (Hebrew if the question is in Hebrew).\n"
        "- 2-4 short sentences.\n"
        "CORRECT example: \"לפי הפוליסה, נזקי צנרת מכוסים עד 20,000 ₪ [2].\"\n"
        "INCORRECT example: \"בדרך כלל נזקי צנרת מכוסים בסביבות 20-30 אלף ₪.\" (guessing)\n"
        + (f"\nAdditional instructions:\n{extra_instructions}" if extra_instructions else "")
    )
    user = f"Question: {question}\n\nParagraphs:\n{numbered}"
    return chat(system, user, temperature=0.0, max_tokens=800)
