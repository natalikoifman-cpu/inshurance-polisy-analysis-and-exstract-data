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

# --- memory: novelty gate, operations, context-aware retrieval ---------------

def _keyword_set(text: str) -> set[str]:
    return {strip_hebrew_prefix(t) for t in tokenize(text)
            if t not in _STOPWORDS and len(t) >= 2}


def novelty(fact_text: str, memories: list[dict]) -> tuple[float, int]:
    """Surprisal gate: 1.0 = completely new, 0.0 = already known.
    Overlap coefficient against the most similar existing memory.
    Returns (novelty_score, index_of_most_similar_memory or -1)."""
    fact_keys = _keyword_set(fact_text)
    if not fact_keys:
        return 0.0, -1
    best_sim, best_idx = 0.0, -1
    for i, memory in enumerate(memories):
        mem_keys = _keyword_set(memory.get("text", ""))
        if not mem_keys:
            continue
        sim = len(fact_keys & mem_keys) / min(len(fact_keys), len(mem_keys))
        if sim > best_sim:
            best_sim, best_idx = sim, i
    return round(1.0 - best_sim, 4), best_idx


def decide_operations(candidate_facts: list[str], memories: list[dict],
                      threshold: float = 0.3) -> list[dict]:
    """ADD / UPDATE / NOOP per candidate fact, by novelty against the store.
    novelty < threshold -> NOOP (already known); threshold..0.7 -> UPDATE the
    most similar memory; >= 0.7 -> ADD. DELETE happens in consolidation."""
    operations = []
    for fact in candidate_facts:
        fact = str(fact or "").strip()
        if not fact:
            continue
        score, similar_idx = novelty(fact, memories)
        if score < threshold:
            operations.append({"op": "NOOP", "fact": fact, "novelty": score,
                               "reason": "already known / below novelty threshold"})
        elif score < 0.7 and similar_idx >= 0:
            operations.append({"op": "UPDATE", "fact": fact, "novelty": score,
                               "target_id": memories[similar_idx].get("id"),
                               "reason": "extends or corrects an existing memory"})
        else:
            operations.append({"op": "ADD", "fact": fact, "novelty": score,
                               "reason": "new information"})
    return operations


def apply_operations(memories: list[dict], operations: list[dict],
                     customer_state: str | None = None) -> list[dict]:
    """Apply ADD/UPDATE (NOOP skipped) and return the updated memory list.
    New ids continue the highest numeric m<N> suffix — deterministic."""
    updated = [dict(m) for m in memories]
    next_num = 1 + max((int(m["id"][1:]) for m in updated
                        if re.fullmatch(r"m\d+", str(m.get("id", "")))), default=0)
    by_id = {m.get("id"): m for m in updated}
    for op in operations:
        if op["op"] == "ADD":
            entry = {"id": f"m{next_num}", "text": op["fact"]}
            if customer_state:
                entry["state"] = customer_state
            updated.append(entry)
            next_num += 1
        elif op["op"] == "UPDATE" and op.get("target_id") in by_id:
            target = by_id[op["target_id"]]
            target["text"] = op["fact"]
            if customer_state:
                target["state"] = customer_state
    return updated


def memory_retrieve(memories: list[dict], query: str,
                    customer_state: str | None = None, top_k: int = 6,
                    synonyms: dict[str, list[str]] | None = None) -> dict:
    """Context-aware retrieval: ~70% text relevance (BM25) + ~30% state match,
    then associative expansion — memories sharing keywords with the top hits
    join at a 20%-decayed score. Deterministic."""
    texts = [m.get("text", "") for m in memories]
    keywords = enrich_keywords(query, synonyms)
    hits = BM25(texts).search(keywords, top_k=len(texts)) if texts else []
    max_score = max((h["score"] for h in hits), default=0.0) or 1.0
    scored: dict[int, dict] = {}
    for hit in hits:
        memory = memories[hit["index"]]
        text_score = hit["score"] / max_score
        if customer_state and memory.get("state"):
            state_score = 1.0 if memory["state"] == customer_state else 0.0
            blended = 0.7 * text_score + 0.3 * state_score
        else:
            blended = text_score
        scored[hit["index"]] = {"memory": memory, "score": round(blended, 4),
                                "via": "match"}
    # associative expansion from the top 3 direct hits
    top_direct = sorted(scored.items(), key=lambda kv: (-kv[1]["score"], kv[0]))[:3]
    for idx, entry in top_direct:
        anchor_keys = _keyword_set(memories[idx].get("text", ""))
        for j, memory in enumerate(memories):
            if j in scored:
                continue
            if anchor_keys & _keyword_set(memory.get("text", "")):
                scored[j] = {"memory": memory,
                             "score": round(entry["score"] * 0.8, 4),
                             "via": f"association with {memories[idx].get('id')}"}
    results = sorted(scored.values(), key=lambda e: (-e["score"], str(e["memory"].get("id"))))
    return {"memories": results[:top_k], "keywords_used": keywords,
            "total_memories": len(memories), "found": bool(results)}


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
