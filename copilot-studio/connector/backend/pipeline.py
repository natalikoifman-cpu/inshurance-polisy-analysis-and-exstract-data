"""Deterministic pipeline stages — everything that must NOT be done by an LLM.

Implements the "engineered agent" funnel:
  keywords -> enrichment (Hebrew prefixes, plurals, synonyms) -> BM25 -> top paragraphs
plus the intake-form check and rule-based routing.

Pure standard library. Same input always produces the same output.
"""

from __future__ import annotations

import math
import re
import unicodedata

# --- text normalization -----------------------------------------------------

_HEBREW_PREFIXES = ("וכש", "כשה", "שה", "וה", "וב", "ול", "ומ", "וש",
                    "ה", "ב", "ל", "מ", "ש", "כ", "ו")
_TOKEN_RE = re.compile(r"[\w֐-׿']+", re.UNICODE)

# function words that only produce false matches (never useful as search keywords)
_STOPWORDS = {
    # Hebrew
    "על", "של", "את", "עם", "מה", "מהי", "מהו", "אם", "או", "גם", "כל", "לא",
    "יש", "אין", "זה", "זו", "זאת", "איך", "מתי", "איפה", "למה", "כמה", "האם",
    "אני", "הוא", "היא", "אתה", "אתם", "אנחנו", "הם", "הן", "כי", "אבל", "רק",
    "עוד", "כבר", "בין", "עד", "אחרי", "לפני", "אצל", "כדי", "אז", "שלי", "שלך",
    # English
    "the", "a", "an", "is", "are", "was", "were", "be", "what", "how", "when",
    "where", "why", "which", "who", "of", "in", "on", "at", "for", "to", "and",
    "or", "not", "no", "my", "your", "our", "do", "does", "did", "can", "i",
    "we", "you", "it", "this", "that", "there",
}


def normalize(text: str) -> str:
    """Lowercase, strip niqqud and normalize unicode."""
    text = unicodedata.normalize("NFKC", text or "")
    text = "".join(ch for ch in text if not (0x0591 <= ord(ch) <= 0x05C7))
    return text.lower()


def tokenize(text: str) -> list[str]:
    return _TOKEN_RE.findall(normalize(text))


def strip_hebrew_prefix(token: str) -> str:
    """ו/ה/ב/ל/מ/ש/כ prefixes: 'והביטוח' -> 'ביטוח'. Longest prefix first."""
    for pref in _HEBREW_PREFIXES:
        if token.startswith(pref) and len(token) - len(pref) >= 3:
            return token[len(pref):]
    return token


def _hebrew_variants(token: str) -> set[str]:
    variants = {token, strip_hebrew_prefix(token)}
    base = strip_hebrew_prefix(token)
    # plural/suffix families: ביטוח -> ביטוחים, פוליסה -> פוליסות
    for suffix in ("ים", "ות", "י", "ה", "ת"):
        if base.endswith(suffix) and len(base) - len(suffix) >= 3:
            variants.add(base[: -len(suffix)])
    if len(base) >= 3:
        variants.add(base + "ים")
        variants.add(base + "ות")
    return variants


def enrich_keywords(query: str, synonyms: dict[str, list[str]] | None = None) -> list[str]:
    """Expand query tokens with morphological variants and caller-supplied synonyms.

    Deterministic: output is sorted. `synonyms` maps a base word to equivalents,
    e.g. {"רכב": ["אוטו", "מכונית"]}.
    """
    syn = {normalize(k): [normalize(v) for v in vals] for k, vals in (synonyms or {}).items()}
    enriched: set[str] = set()
    for token in tokenize(query):
        if token in _STOPWORDS:
            continue
        variants = {v for v in _hebrew_variants(token) if v not in _STOPWORDS}
        enriched |= variants
        for variant in list(variants):
            for base, equivalents in syn.items():
                if variant == base or variant in equivalents:
                    enriched.add(base)
                    enriched.update(equivalents)
    return sorted(v for v in enriched if len(v) >= 2)


# --- paragraph splitting and BM25 ------------------------------------------

def split_paragraphs(text: str, max_len: int = 1200) -> list[str]:
    """Split on blank lines / newlines; long paragraphs are chunked by sentence."""
    raw = [p.strip() for p in re.split(r"\n\s*\n|\r\n\s*\r\n", text or "") if p.strip()]
    if not raw:
        raw = [p.strip() for p in (text or "").splitlines() if p.strip()]
    paragraphs: list[str] = []
    for para in raw:
        if len(para) <= max_len:
            paragraphs.append(para)
            continue
        chunk = ""
        for sentence in re.split(r"(?<=[.!?؟])\s+", para):
            if len(chunk) + len(sentence) > max_len and chunk:
                paragraphs.append(chunk.strip())
                chunk = ""
            chunk += sentence + " "
        if chunk.strip():
            paragraphs.append(chunk.strip())
    return paragraphs


class BM25:
    """Plain BM25 (Okapi) over tokenized paragraphs — no dependencies."""

    def __init__(self, paragraphs: list[str], k1: float = 1.5, b: float = 0.75):
        self.paragraphs = paragraphs
        self.k1, self.b = k1, b
        self.docs = [[strip_hebrew_prefix(t) for t in tokenize(p)] for p in paragraphs]
        self.doc_lens = [len(d) for d in self.docs]
        self.avg_len = (sum(self.doc_lens) / len(self.docs)) if self.docs else 0.0
        self.doc_freq: dict[str, int] = {}
        for doc in self.docs:
            for term in set(doc):
                self.doc_freq[term] = self.doc_freq.get(term, 0) + 1

    def _idf(self, term: str) -> float:
        n, df = len(self.docs), self.doc_freq.get(term, 0)
        return math.log(1 + (n - df + 0.5) / (df + 0.5))

    def search(self, keywords: list[str], top_k: int = 8) -> list[dict]:
        terms = [strip_hebrew_prefix(normalize(k)) for k in keywords]
        scored = []
        for i, doc in enumerate(self.docs):
            if not doc:
                continue
            score = 0.0
            for term in terms:
                tf = doc.count(term)
                if tf == 0:
                    continue
                norm = self.k1 * (1 - self.b + self.b * self.doc_lens[i] / (self.avg_len or 1))
                score += self._idf(term) * tf * (self.k1 + 1) / (tf + norm)
            if score > 0:
                scored.append({"index": i, "score": round(score, 4), "text": self.paragraphs[i]})
        scored.sort(key=lambda item: (-item["score"], item["index"]))
        return scored[:top_k]


def retrieve(question: str, document_text: str, top_k: int = 8,
             synonyms: dict[str, list[str]] | None = None) -> dict:
    """The full funnel: question -> enriched keywords -> BM25 -> top paragraphs."""
    paragraphs = split_paragraphs(document_text)
    keywords = enrich_keywords(question, synonyms)
    hits = BM25(paragraphs).search(keywords, top_k=top_k) if paragraphs else []
    return {
        "total_paragraphs": len(paragraphs),
        "keywords_used": keywords,
        "paragraphs": hits,
        "found": bool(hits),
    }


# --- intake form check ------------------------------------------------------

def check_intake(required_fields: list[dict], filled: dict[str, str]) -> dict:
    """Given the required-field definitions and the fields known so far, report
    completeness and the next question to ask. Field definition:
    {"name": "policy_id", "question": "מה מספר הפוליסה?", "allowed": [...](optional)}
    """
    missing, invalid = [], []
    for field in required_fields:
        name = field.get("name", "")
        value = str(filled.get(name, "") or "").strip()
        if not value or value.lower() in ("null", "none", "unknown", "לא ידוע"):
            missing.append(field)
        elif field.get("allowed") and value not in field["allowed"]:
            invalid.append({"field": name, "value": value, "allowed": field["allowed"]})
    next_question = None
    if missing:
        first = missing[0]
        next_question = first.get("question") or f"מה הערך של {first.get('name')}?"
    return {
        "complete": not missing and not invalid,
        "filled": {k: v for k, v in filled.items() if str(v or "").strip()},
        "missing": [f.get("name") for f in missing],
        "invalid": invalid,
        "next_question": next_question,
    }


# --- deterministic routing --------------------------------------------------

_OPS = {
    "eq": lambda a, b: normalize(str(a)) == normalize(str(b)),
    "ne": lambda a, b: normalize(str(a)) != normalize(str(b)),
    "in": lambda a, b: normalize(str(a)) in [normalize(str(x)) for x in (b if isinstance(b, list) else [b])],
    "contains": lambda a, b: normalize(str(b)) in normalize(str(a)),
    "gt": lambda a, b: float(a) > float(b),
    "lt": lambda a, b: float(a) < float(b),
}


def route(rules: list[dict], fields: dict) -> dict:
    """First rule whose conditions ALL hold wins. A rule with no conditions is a
    catch-all. Same input -> same route, always."""
    for i, rule in enumerate(rules):
        conditions = rule.get("when", [])
        try:
            matched = all(
                _OPS[c.get("op", "eq")](fields.get(c.get("field", ""), ""), c.get("value"))
                for c in conditions
            )
        except (KeyError, TypeError, ValueError):
            matched = False
        if matched:
            return {"route": rule.get("route", "unknown"), "rule_index": i,
                    "matched_conditions": conditions}
    return {"route": "no_match", "rule_index": -1, "matched_conditions": []}
