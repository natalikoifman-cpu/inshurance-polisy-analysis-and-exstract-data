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
