"""Deterministic source parser/router for forensic evidence packages.

Model acquisition should scout candidate URLs; this module turns fetched or
cached source content into normalized source objects before Jury reasoning. It
does not browse, call models, or promote claims.
"""

from __future__ import annotations

import hashlib
import re
from urllib.parse import urlparse

from evidence_contracts import PARSED_SOURCE


CONTENT_FIELDS = (
    "fetched_excerpt",
    "cached_excerpt",
    "text_excerpt",
    "markdown_excerpt",
    "page_text",
    "parsed_text",
    "extracted_text",
    "source_quote",
    "provider_quote_candidate",
)

ASSET_EXTENSIONS = (
    ".avif",
    ".bmp",
    ".css",
    ".gif",
    ".ico",
    ".jpeg",
    ".jpg",
    ".js",
    ".png",
    ".svg",
    ".webp",
    ".woff",
    ".woff2",
)


def clean_text(value: object, limit: int | None = None) -> str:
    text = re.sub(r"\s+", " ", str(value or "")).strip()
    if limit and len(text) > limit:
        return text[:limit].rstrip()
    return text


def canonical_url(url: str) -> str:
    parsed = urlparse(url or "")
    if not parsed.scheme or not parsed.netloc:
        return ""
    host = parsed.netloc.lower()
    if host.startswith("www."):
        host = host[4:]
    path = (parsed.path or "/").rstrip("/") or "/"
    return f"{parsed.scheme.lower()}://{host}{path}".lower()


def host(url: str) -> str:
    value = urlparse(url or "").netloc.lower()
    return value[4:] if value.startswith("www.") else value


def _word_limit(text: str, max_words: int) -> str:
    words = clean_text(text).split()
    if len(words) <= max_words:
        return " ".join(words)
    return " ".join(words[:max_words]).rstrip(" ,.;:") + "..."


def _content_hash(text: str) -> str:
    cleaned = clean_text(text, 20000)
    if not cleaned:
        return ""
    return hashlib.sha1(cleaned.encode("utf-8")).hexdigest()[:16]


def source_text(record: dict, limit: int = 16000) -> str:
    for field in CONTENT_FIELDS:
        text = clean_text(record.get(field) or "", limit)
        if text:
            return text
    # Fresh discovery snippets are only routing hints; historical/governed
    # records may have a snippet as the only cached text.
    if record.get("source_status") != "fresh_discovery":
        return clean_text(record.get("snippet") or record.get("semantic_snippet") or "", limit)
    return ""


def source_format(record: dict, text: str = "") -> str:
    content_type = str(record.get("content_type") or record.get("mime_type") or "").lower()
    path = urlparse(str(record.get("url") or "")).path.lower()
    if content_type.startswith("image/") or path.endswith(ASSET_EXTENSIONS):
        return "asset"
    if "pdf" in content_type or path.endswith(".pdf"):
        return "pdf"
    if "\t" in text or text.count("|") >= 8:
        return "table_or_registry"
    if text:
        return "html_or_text"
    return "unknown"


def _sentences(text: str) -> list[str]:
    parts = re.split(r"(?<=[.!?])\s+", clean_text(text))
    return [part.strip() for part in parts if len(part.strip()) >= 35]


def parsed_source_summary(record: dict, text: str, max_chars: int = 420) -> str:
    title = clean_text(record.get("title") or record.get("label") or "", 160)
    publisher = host(str(record.get("url") or ""))
    sentences = _sentences(text)
    lead = sentences[0] if sentences else clean_text(text, 220)
    prefix = " - ".join(part for part in (title, publisher) if part)
    if prefix and lead:
        summary = f"{prefix}: {lead}"
    else:
        summary = prefix or lead
    return clean_text(summary, max_chars)


def semantic_snippet(record: dict, text: str, min_words: int = 80, max_words: int = 150) -> str:
    raw = clean_text(record.get("semantic_snippet") or record.get("snippet") or "")
    if raw:
        return _word_limit(raw, max_words)
    sentences = _sentences(text)
    snippet = " ".join(sentences[:4]) if sentences else clean_text(text, 900)
    words = snippet.split()
    if len(words) < min_words and len(text.split()) > len(words):
        snippet = clean_text(text, 1200)
    return _word_limit(snippet, max_words)


def _paragraph_chunks(text: str) -> list[tuple[int, str]]:
    raw = re.split(r"(?:\n\s*\n|(?<=\.)\s{2,})", str(text or ""))
    chunks = []
    offset = 0
    for part in raw:
        cleaned = clean_text(part)
        idx = text.find(part, offset) if part else -1
        if idx < 0:
            idx = offset
        offset = idx + len(part)
        if len(cleaned) >= 80:
            if len(cleaned) > 1400:
                step = 700
                for start in range(0, len(cleaned), step):
                    window = cleaned[start : start + 1100]
                    if len(window) >= 80:
                        chunks.append((idx + start, window))
                    if start + 1100 >= len(cleaned):
                        break
            else:
                chunks.append((idx, cleaned))
    if not chunks and clean_text(text):
        cleaned = clean_text(text)
        for start in range(0, len(cleaned), 900):
            chunk = cleaned[start : start + 1200]
            if len(chunk) >= 80:
                chunks.append((start, chunk))
    return chunks


def _score_chunk(chunk: str, query_terms: tuple[str, ...]) -> int:
    lowered = chunk.lower()
    score = 0
    for term in query_terms:
        term_l = str(term or "").lower().strip()
        if not term_l:
            continue
        if term_l in lowered:
            score += 4 if " " in term_l or "-" in term_l else 2
    if any(word in lowered for word in ("states", "lists", "describes", "provides", "offers", "reports")):
        score += 3
    if any(word in lowered for word in ("result", "customer", "service", "governance", "strategy", "delivery")):
        score += 2
    return score


def _expand_passage(text: str, start: int, chunk: str, min_chars: int, max_chars: int) -> str:
    cleaned = clean_text(text)
    if len(chunk) >= min_chars:
        return clean_text(chunk, max_chars)
    center = max(0, start)
    before = max(0, center - ((min_chars - len(chunk)) // 2 + 200))
    after = min(len(cleaned), center + len(chunk) + ((min_chars - len(chunk)) // 2 + 400))
    return clean_text(cleaned[before:after], max_chars)


def select_support_passages(
    text: str,
    query_terms: tuple[str, ...] = (),
    max_passages: int = 4,
    min_chars: int = 600,
    max_chars: int = 1200,
) -> list[dict]:
    cleaned = clean_text(text)
    if not cleaned:
        return []
    chunks = _paragraph_chunks(cleaned)
    scored = [
        (_score_chunk(chunk, query_terms), idx, start, chunk)
        for idx, (start, chunk) in enumerate(chunks)
    ]
    scored.sort(key=lambda item: (item[0], -item[1]), reverse=True)
    selected = []
    seen = set()
    for score, idx, start, chunk in scored:
        if len(selected) >= max_passages:
            break
        passage = _expand_passage(cleaned, start, chunk, min_chars, max_chars)
        fingerprint = hashlib.sha1(passage[:220].encode("utf-8")).hexdigest()[:10]
        if fingerprint in seen:
            continue
        seen.add(fingerprint)
        selected.append(
            {
                "passage_id": f"PASS-{fingerprint}",
                "passage_contract": "source_passage/v1",
                "text": passage,
                "selection_reason": "query_match" if score > 0 else "lead_context",
                "score": score,
                "char_start": start,
                "char_count": len(passage),
            }
        )
    return selected


def _extraction_quality(fmt: str, text: str, passages: list[dict], warnings: list[str]) -> str:
    if fmt == "asset" or "empty_content" in warnings:
        return "unusable"
    if len(text) >= 2500 and len(passages) >= 2:
        return "rich"
    if len(text) >= 700 and passages:
        return "usable"
    if text:
        return "thin"
    return "unknown"


def parse_source(record: dict, jury_name: str = "", query_terms: tuple[str, ...] = ()) -> dict:
    text = source_text(record, 18000)
    fmt = source_format(record, text)
    warnings = []
    if fmt == "asset":
        warnings.append("asset_or_binary")
    if not text:
        warnings.append("empty_content")
    elif len(text) < 300:
        warnings.append("short_content")
    if fmt == "pdf":
        warnings.append("pdf_text")
    if fmt == "table_or_registry":
        warnings.append("table_or_registry")

    passages = []
    if text and fmt != "asset":
        passages = select_support_passages(text, query_terms=query_terms)

    key = canonical_url(str(record.get("url") or "")) or str(record.get("url") or "")
    content_hash = _content_hash(text)
    parsed_source_id = f"PRS-{hashlib.sha1((key + content_hash).encode('utf-8')).hexdigest()[:10]}"
    source_id = f"SRC-{hashlib.sha1((key or content_hash or repr(sorted(record.items()))).encode('utf-8')).hexdigest()[:10]}"
    return {
        "parser_contract": PARSED_SOURCE,
        "source_id": source_id,
        "parsed_source_id": parsed_source_id,
        "canonical_url": key,
        "source_format": fmt,
        "content_hash": content_hash,
        "extraction_quality": _extraction_quality(fmt, text, passages, warnings),
        "content_text": text,
        "content_excerpt": clean_text(text, 4500),
        "parsed_source_summary": parsed_source_summary(record, text),
        "semantic_snippet": semantic_snippet(record, text),
        "support_passages": passages,
        "support_passage_ids": [item.get("passage_id") for item in passages if item.get("passage_id")],
        "source_limitations": list(warnings),
        "parse_warnings": warnings,
        "parser_stage": "deterministic_source_parser_v1",
        "parser_route": fmt,
        "jury_context": jury_name,
    }
