"""
Evidence-gated Buying Potential & Insight generation.

This module is intentionally separate from the normal scan pipeline. It reads
stored Scanner output plus a service portfolio KB, generates a sales-facing
account insight, and returns standalone JSON/HTML artifacts.
"""

from __future__ import annotations

import ast
import hashlib
import html
import io
import json
import os
import re
import time
from datetime import datetime
from typing import Any

import requests
from pypdf import PdfReader

from llm_clients import (
    call_json_model_chain,
    parse_json_object,
)
from model_policy import (
    BUYING_MODEL_MODE,
    buying_gate_chain as policy_buying_gate_chain,
    buying_generation_chain as policy_buying_generation_chain,
    json_repair_chain as policy_json_repair_chain,
    policy_for,
)
from source_quality import is_generation_eligible_source
from knowledge_base import REMOTE_KNOWLEDGE_URL as DEFAULT_MODERNIZATION_TACTICS_URL


SERVICE_KB_CACHE_PREFIX = "service_portfolio/vivicta_services.extracted"
SERVICE_KB_TEXT_LIMIT = 65000
BUYING_MODERNIZATION_TACTICS_URL = "https://3bk7txu1jiixlgmr.public.blob.vercel-storage.com/tactics.json"
ALLOWED_GATES = {
    "allowed_as_fact",
    "allowed_as_hypothesis",
    "allowed_as_discovery_question",
    "internal_only",
    "blocked",
}
EVIDENCE_BASIS_PREFIXES = ("SRC-", "FIN-", "CED-", "LEDGER-", "EVD-")
SIGNAL_MAP_LIMIT = 10
BUYING_GATE_PACKET_CHAR_LIMIT = 80000
SIGNAL_LANE_QUOTAS = {
    "activation_hypothesis": 3,
    "account_fact": 2,
    "financial_capacity": 1,
    "external_pressure": 2,
    "contradiction_or_gap": 1,
    "strategic_context": 1,
}
_modernization_tactics_cache = {"url": None, "items": None}

RELIABILITY_STATUS_SCALE = [
    {
        "code": "A",
        "label": "Ready for Discovery",
        "summary": "Strongest internal discovery basis; still not confirmed buying intent.",
        "allowed_use": "Use for disciplined sales discovery and proposal-prep framing with evidence boundaries.",
        "publication_status": "sales_prep_ready",
        "tone": "green",
    },
    {
        "code": "B",
        "label": "Cautious Hypothesis",
        "summary": "Usable as a sales hypothesis when discovery validates timing, owner, and consequence.",
        "allowed_use": "Use for internal sales planning and cautious discovery questions.",
        "publication_status": "sales_prep_ready",
        "tone": "cyan",
    },
    {
        "code": "C",
        "label": "Question-Led Draft",
        "summary": "Evidence or input is constrained; treat output as questions and watch themes.",
        "allowed_use": "Use internally only as discovery prompts and qualification hypotheses.",
        "publication_status": "internal_review_required",
        "tone": "amber",
    },
    {
        "code": "D",
        "label": "Draft - Do Not Use",
        "summary": "Evidence/input is too weak or guardrails are unresolved for sales use.",
        "allowed_use": "Store for audit and internal thinking only. Do not use customer-facing.",
        "publication_status": "draft_do_not_use",
        "tone": "rose",
    },
]


BUYER_ROLE_LENSES = {
    "CEO / Managing Director": {
        "owned_outcome": "growth, strategic execution, credibility, and scalable operating model",
        "safe_question": "Where do you see the biggest execution constraint as the company moves through its next growth phase?",
    },
    "Board / Chair": {
        "owned_outcome": "risk, governance, capital allocation, and resilience",
        "safe_question": "Which governance or resilience topics are becoming more important as external pressure increases?",
    },
    "CFO / Finance Lead": {
        "owned_outcome": "margin, cost visibility, cash discipline, and investment prioritization",
        "safe_question": "Where would better visibility help most: cost control, investment prioritization, or margin protection?",
    },
    "COO / Operations Lead": {
        "owned_outcome": "delivery reliability, capacity, quality, and operating-model scalability",
        "safe_question": "Where is operational scaling creating the most pressure today: planning, supplier coordination, quality, or delivery visibility?",
    },
    "CIO / CTO": {
        "owned_outcome": "architecture, platforms, data, integrations, cloud, and secure digital foundations",
        "safe_question": "Which parts of the digital backbone need to scale first if business complexity continues to increase?",
    },
    "CISO / Security Lead": {
        "owned_outcome": "cybersecurity, resilience, identity, compliance, and secure technology operations",
        "safe_question": "Which security or resilience requirements are becoming harder to manage as the operating environment changes?",
    },
    "Product / Engineering Lead": {
        "owned_outcome": "product lifecycle, software/data complexity, architecture, and technology governance",
        "safe_question": "How are product-data, lifecycle, and architecture requirements changing as products become more digital or connected?",
    },
    "Procurement / Supply Chain Lead": {
        "owned_outcome": "supplier reliability, traceability, partner coordination, and cost transparency",
        "safe_question": "Where does supplier or partner coordination create the most need for better data or process visibility?",
    },
    "HR / Workforce Lead": {
        "owned_outcome": "skills, adoption, ways of working, workforce capability, and culture",
        "safe_question": "Which capability or adoption gaps could slow transformation if they are not addressed early?",
    },
    "Strategy / Growth Lead": {
        "owned_outcome": "growth opportunities, customer relevance, transformation roadmap, and competitive position",
        "safe_question": "Which growth or market-pressure themes should be validated before defining the next transformation priorities?",
    },
}


ROLE_KEYWORDS = (
    ("CISO / Security Lead", ("cyber", "security", "nis2", "dora", "iam", "soc", "resilience", "sovereign")),
    ("CIO / CTO", ("cloud", "data", "platform", "architecture", "integration", "erp", "sap", "ai", "digital", "automation")),
    ("Product / Engineering Lead", ("product", "engineering", "lifecycle", "software", "autonomy", "vehicle", "r&d", "technology")),
    ("COO / Operations Lead", ("production", "capacity", "operations", "delivery", "quality", "scaling", "ramp", "manufacturing")),
    ("Procurement / Supply Chain Lead", ("supplier", "supply", "procurement", "partner", "subcontractor", "traceability")),
    ("CFO / Finance Lead", ("margin", "profit", "revenue", "cost", "finance", "funding", "investment", "equity", "cash")),
    ("HR / Workforce Lead", ("workforce", "hiring", "talent", "skills", "training", "culture", "people")),
    ("CEO / Managing Director", ("growth", "strategy", "market", "expansion", "partnership", "defence", "defense")),
)


class BuyingPotentialError(RuntimeError):
    """Raised when a buying-potential report cannot be created safely."""

    def __init__(self, message: str, status_code: int = 400):
        super().__init__(message)
        self.status_code = status_code


ACRONYM_FIXES = (
    "AI", "API", "ERP", "SAP", "CRM", "PLM", "MES", "MOM", "OT",
    "CFO", "CEO", "COO", "CIO", "CTO", "CISO", "GTP", "GTT",
)


def _clean_generated_text(value: Any) -> str:
    text = html.unescape(str(value or ""))
    if re.match(r"^https?://\S+$", text.strip()):
        return text.strip()
    text = re.sub(r"!\[([^\]]*)\]\([^)]*\)", r"\1", text)
    text = re.sub(r"\[([^\]]+)\]\((?:https?://)?[^)]*\)", r"\1", text)
    text = re.sub(r"https?://\S+", " ", text)
    text = re.sub(r"<[^>]+>", " ", text)
    text = re.sub(r"\s+", " ", text).strip()
    if not text:
        return ""
    text = re.sub(r"\s+([,.;:!?])", r"\1", text)
    text = re.sub(r"[,;:]\s*\.", ".", text)
    text = re.sub(r"\.{2,}", ".", text)
    text = re.sub(r"([!?])\.", r"\1", text)
    text = re.sub(r"\bThe external pressure is\s+", "External pressure: ", text, flags=re.I)
    text = re.sub(r"\bThe account-specific question is whether\s+", "Account-specific question: whether ", text, flags=re.I)
    text = re.sub(r"\bwhether\s+if\b", "whether", text, flags=re.I)
    text = re.sub(r"\bremains\s+strongly([.!?])", r"remains strong\1", text, flags=re.I)
    text = re.sub(r"\bat\s+the\s+pace([.!?])", r"at the required pace\1", text, flags=re.I)
    for acronym in ACRONYM_FIXES:
        text = re.sub(rf"\b{re.escape(acronym.lower())}\b", acronym, text, flags=re.I)
    return text.strip()


def _restore_company_case(text: Any, company_name: Any = "") -> str:
    cleaned = _clean_generated_text(text)
    company = str(company_name or "").strip()
    if not company:
        return cleaned
    pattern = re.compile(rf"\b{re.escape(company)}\b", re.I)
    cleaned = pattern.sub(company, cleaned)
    legal_forms = ("Oy", "Ab", "Oyj", "Ltd", "Inc", "GmbH")
    for token in legal_forms:
        cleaned = re.sub(rf"\b{token.lower()}\b", token, cleaned, flags=re.I)
    return cleaned


def _polish_generated_text_tree(value: Any, company_name: Any = "", parent_key: str = "") -> Any:
    if isinstance(value, list):
        return [_polish_generated_text_tree(item, company_name, parent_key) for item in value]
    if isinstance(value, dict):
        return {
            key: _polish_generated_text_tree(item, company_name, str(key))
            for key, item in value.items()
        }
    if isinstance(value, str):
        key_l = str(parent_key or "").lower()
        if "url" in key_l or key_l in {"id", "source", "evidence_basis"}:
            return value
        return _restore_company_case(value, company_name)
    return value


def _clip(value: Any, limit: int = 900) -> str:
    text = _clean_generated_text(value)
    if len(text) <= limit:
        return text
    if re.match(r"^https?://\S+$", text):
        return text[:limit]
    candidate = text[:limit].rstrip()
    min_sentence = max(80, int(limit * 0.45))
    sentence_cuts = [m.end() for m in re.finditer(r"[.!?](?:\s|$)", candidate) if m.end() >= min_sentence]
    if sentence_cuts:
        return candidate[:sentence_cuts[-1]].strip()
    soft_cuts = [m.end() for m in re.finditer(r"[;:](?:\s|$)", candidate) if m.end() >= min_sentence]
    used_soft_cut = False
    if soft_cuts:
        candidate = candidate[:soft_cuts[-1]].rstrip(" ;:")
        used_soft_cut = True
    else:
        last_clause = max(candidate.rfind(","), candidate.rfind(";"), candidate.rfind(":"))
        trailing = candidate[last_clause + 1:].strip() if last_clause >= 0 else candidate
        dangling_markers = (
            "but", "while", "although", "because", "which", "that", "where",
            "could", "would", "should", "may", "might", "can", "will",
            "could limit", "would limit", "may limit", "might limit",
            "risks", "requires", "depends", "suggests",
        )
        trailing_l = trailing.lower().strip(" .,:;-")
        if last_clause >= min_sentence and (
            len(trailing.split()) <= 4
            or any(trailing_l == marker or trailing_l.endswith(f" {marker}") for marker in dangling_markers)
        ):
            candidate = candidate[:last_clause].rstrip(" ,;:")
    if not used_soft_cut:
        word_cut = candidate.rfind(" ")
        if word_cut >= min_sentence:
            candidate = candidate[:word_cut].rstrip()
    candidate = re.sub(r"\b(?:and|or|with|to|for|of|in|the|a|an)$", "", candidate, flags=re.I).rstrip()
    candidate = re.sub(
        r"(?:\b(?:but|while|although|because|which|that|where|could|would|should|may|might|can|will)\b\.?)$",
        "",
        candidate,
        flags=re.I,
    ).rstrip()
    if re.search(r"\b(?:remains?|becomes?|appears?|seems?)\s+\w+ly$", candidate, flags=re.I):
        last_clause = max(candidate.rfind(","), candidate.rfind(";"), candidate.rfind(":"))
        if last_clause >= min_sentence:
            candidate = candidate[:last_clause].rstrip(" ,;:")
    candidate = candidate.rstrip(" ,;:-")
    if candidate and not re.search(r"[.!?]$", candidate):
        candidate += "."
    return candidate


def _clip_label(value: Any, limit: int = 120) -> str:
    """Clip compact labels without producing mid-word fragments or fake sentences."""
    text = _clean_generated_text(value)
    if len(text) <= limit:
        return text
    candidate = text[:limit + 1]
    word_cut = candidate.rfind(" ")
    if word_cut >= max(24, int(limit * 0.55)):
        candidate = candidate[:word_cut]
    candidate = candidate.rstrip(" ,;:-.")
    return f"{candidate}..." if candidate else ""


def _list(value: Any) -> list:
    return value if isinstance(value, list) else []


def _dict(value: Any) -> dict:
    return value if isinstance(value, dict) else {}


def _dedupe_text_items(items: Any, *, limit: int = 12, clip_limit: int = 520) -> list[str]:
    values: list[str] = []
    seen: set[str] = set()
    for item in _list(items):
        text = _clip(item, clip_limit)
        key = re.sub(r"[^a-z0-9]+", " ", text.lower()).strip()
        if not text or not key or key in seen:
            continue
        seen.add(key)
        values.append(text)
        if len(values) >= limit:
            break
    return values


def _safe_int(value: Any, default: int = 0) -> int:
    try:
        return int(float(value))
    except Exception:
        return default


def _clean_env(value: str | None) -> str:
    if not value:
        return ""
    if "=" in value:
        value = value.split("=", 1)[1]
    return value.strip().strip('"').strip("'")


def _tactic_text(item: dict) -> str:
    return " ".join(
        str(item.get(key) or "")
        for key in (
            "category",
            "problem_pattern",
            "what",
            "concept",
            "resource_label",
            "solution_mechanism",
            "case_study",
        )
    )


def _normalize_modernization_tactic(item: Any) -> dict | None:
    if not isinstance(item, dict):
        return None
    concept = _clip(item.get("concept") or item.get("resource_label"), 160)
    mechanism = _clip(item.get("solution_mechanism"), 220)
    problem = _clip(item.get("problem_pattern") or item.get("what"), 220)
    category = _clip(item.get("category"), 80)
    if not concept and mechanism:
        concept = mechanism
    if not concept or not mechanism:
        return None
    return {
        "category": category,
        "concept": concept,
        "problem_pattern": problem,
        "solution_mechanism": mechanism,
        "case_study": _clip(item.get("case_study"), 220),
        "resource_url": _clip(item.get("resource_url"), 500),
        "match_text": _tactic_text(item).lower(),
    }


def _load_modernization_tactics() -> list[dict]:
    url_candidates = [
        _clean_env(os.environ.get("BUYING_MODERNIZATION_TACTICS_URL")),
        BUYING_MODERNIZATION_TACTICS_URL,
        DEFAULT_MODERNIZATION_TACTICS_URL,
    ]
    for url in [item for item in url_candidates if item]:
        if _modernization_tactics_cache.get("url") == url and isinstance(_modernization_tactics_cache.get("items"), list):
            return _modernization_tactics_cache["items"]
        try:
            response = requests.get(url, timeout=4)
            if response.status_code != 200:
                continue
            data = response.json()
            raw_items = data if isinstance(data, list) else _list(_dict(data).get("tactics"))
            items = [item for item in (_normalize_modernization_tactic(raw) for raw in raw_items) if item]
            if items:
                _modernization_tactics_cache["url"] = url
                _modernization_tactics_cache["items"] = items
                return items
        except Exception as exc:
            print(f"   ⚠️ [Buying Potential] Modernization tactics fetch failed from {url}: {exc}")
    _modernization_tactics_cache["url"] = "fallback_empty"
    _modernization_tactics_cache["items"] = []
    return []


def _keyword_set(text: Any) -> set[str]:
    words = {
        word.lower()
        for word in re.findall(r"[a-zA-ZåäöÅÄÖ0-9]{4,}", str(text or ""))
    }
    stop = {
        "this", "that", "with", "from", "into", "where", "which", "would", "could", "should",
        "company", "customer", "business", "pressure", "evidence", "public", "validate", "need",
        "readiness", "service", "services", "modernization", "solution", "solutions",
    }
    return {word for word in words if word not in stop}


def _modernization_lens_text(tactic: dict) -> str:
    concept = _clip(tactic.get("concept"), 150)
    mechanism = _clip(tactic.get("solution_mechanism"), 180)
    problem = _clip(tactic.get("problem_pattern"), 180)
    if problem:
        return f"{concept}: use {mechanism} as a validation lens where the account signal suggests this pattern: {problem}"
    return f"{concept}: use {mechanism} as a validation lens after the account signal is confirmed"


def _match_modernization_tactics(text: Any, tactics: list[dict], *, limit: int = 3) -> list[dict]:
    query = _keyword_set(text)
    if not query or not tactics:
        return []
    scored = []
    for tactic in tactics:
        tactic_words = _keyword_set(tactic.get("match_text") or _tactic_text(tactic))
        overlap = sorted(query & tactic_words)
        # A single generic keyword is not an explicit account-to-tactic bridge.
        if len(overlap) < 2:
            continue
        scored.append((len(overlap), tactic.get("category") or "", tactic.get("concept") or "", overlap[:8], tactic))
    scored.sort(key=lambda item: (-item[0], item[1], item[2]))
    matches = []
    seen = set()
    for _score, _category, _concept, overlap, tactic in scored:
        key = tactic.get("concept")
        if key in seen:
            continue
        seen.add(key)
        matches.append({
            "tactic_id": tactic.get("tactic_id") or tactic.get("id") or tactic.get("concept"),
            "category": tactic.get("category"),
            "concept": tactic.get("concept"),
            "problem_pattern": tactic.get("problem_pattern"),
            "solution_mechanism": tactic.get("solution_mechanism"),
            "case_study": tactic.get("case_study"),
            "resource_url": tactic.get("resource_url"),
            "match_terms": overlap,
            "lens": _modernization_lens_text(tactic),
        })
        if len(matches) >= limit:
            break
    return matches


def _buying_model_mode(mode: str | None = None) -> str:
    mode = _clean_env(mode or os.environ.get("SCANNER_BUYING_MODEL_MODE") or BUYING_MODEL_MODE or "quality").lower()
    return mode if mode in {"economy", "balanced", "quality", "board_grade"} else "quality"


def buying_generation_chain(mode: str | None = None) -> tuple[str, ...]:
    return policy_buying_generation_chain(_buying_model_mode(mode))


def buying_gate_chain(mode: str | None = None) -> tuple[str, ...]:
    return policy_buying_gate_chain(_buying_model_mode(mode))


def service_kb_extraction_chain(mode: str | None = None) -> tuple[str, ...]:
    return policy_json_repair_chain(_buying_model_mode(mode))


def service_kb_repair_chain(mode: str | None = None) -> tuple[str, ...]:
    return policy_json_repair_chain(_buying_model_mode(mode))


def buying_report_repair_chain(mode: str | None = None) -> tuple[str, ...]:
    return policy_json_repair_chain(_buying_model_mode(mode))


def _latest_blob(storage, prefix: str, suffixes: tuple[str, ...] | None = None) -> dict | None:
    if not storage or not prefix:
        return None
    blobs = storage._list(prefix=prefix, limit=50) or []
    if suffixes:
        lowered = tuple(s.lower() for s in suffixes)
        blobs = [
            blob for blob in blobs
            if str(blob.get("pathname", "")).lower().endswith(lowered)
        ]
    if not blobs:
        return None
    blobs.sort(key=lambda x: x.get("uploadedAt", ""), reverse=True)
    return blobs[0]


def _get_blob_json(storage, blob: dict | None) -> dict | None:
    if not storage or not blob or not blob.get("url"):
        return None
    data = storage._get(blob["url"])
    return data if isinstance(data, dict) else None


def _fetch_blob_bytes(blob: dict) -> bytes:
    url = blob.get("url")
    if not url:
        raise BuyingPotentialError("Service KB Blob entry has no URL.", 409)
    resp = requests.get(f"{url}?t={int(time.time())}", timeout=45)
    if resp.status_code != 200:
        raise BuyingPotentialError(f"Service KB PDF fetch failed ({resp.status_code}).", 409)
    return resp.content


def _extract_pdf_text(pdf_bytes: bytes) -> str:
    try:
        reader = PdfReader(io.BytesIO(pdf_bytes))
        chunks = []
        for idx, page in enumerate(reader.pages, start=1):
            text = page.extract_text() or ""
            if text.strip():
                chunks.append(f"\n\n--- PAGE {idx} ---\n{text}")
        extracted = "\n".join(chunks).strip()
    except Exception as exc:
        raise BuyingPotentialError(f"Service KB PDF could not be parsed: {exc}", 409)
    if len(extracted) < 800:
        raise BuyingPotentialError("Service KB PDF parsed, but extracted text was too short to structure safely.", 409)
    return extracted


def _service_kb_extraction_prompt(pdf_text: str) -> str:
    return f"""
Extract a structured Vivicta Service Portfolio Knowledge Base from this PDF text.

Use ONLY the PDF text. Do not invent services, proof points, personas, claims, dates, client names, or capabilities.
If a field is not clearly present, use an empty array.

Return ONLY valid JSON:
{{
  "version": "pdf-extracted",
  "owner": "Vivicta",
  "services": [
    {{
      "name": "...",
      "category": "...",
      "buyer_personas": [],
      "pain_triggers": [],
      "evidence_patterns": [],
      "proof_points": [],
      "safe_wording": [],
      "do_not_claim": [],
      "disqualifiers": []
    }}
  ],
  "extraction_notes": []
}}

Field rules:
- services[].name is required and must be a real service/offer from the PDF.
- safe_wording must be cautious customer-facing wording.
- do_not_claim must include overclaims the sales report must avoid.
- disqualifiers must include conditions where the service should not be recommended.

PDF text:
{pdf_text[:SERVICE_KB_TEXT_LIMIT]}
""".strip()


def _service_kb_repair_prompt(raw_json: str, pdf_text: str) -> str:
    return f"""
Repair this malformed Service Portfolio KB JSON.

Return ONLY valid JSON matching this schema:
{{
  "version": "pdf-extracted",
  "owner": "Vivicta",
  "services": [
    {{
      "name": "...",
      "category": "...",
      "buyer_personas": [],
      "pain_triggers": [],
      "evidence_patterns": [],
      "proof_points": [],
      "safe_wording": [],
      "do_not_claim": [],
      "disqualifiers": []
    }}
  ],
  "extraction_notes": []
}}

Rules:
- Preserve only services and wording supported by the PDF/source JSON.
- Fix missing commas, quotes, dangling text, markdown fences, and trailing comments.
- Do not add new services or claims.

Malformed JSON:
{raw_json[:35000]}

PDF excerpt for missing context:
{pdf_text[:18000]}
""".strip()


SERVICE_SECTION_LABELS = [
    "Service name",
    "Service category",
    "Buyer personas",
    "Pain triggers",
    "Evidence patterns that make it relevant",
    "Why",
    "What",
    "How",
    "Prerequisites",
    "Needed customer roles",
    "Needed Vivicta/provider roles",
    "Key value promises",
    "Proof points",
    "Safe wording",
    "Do-not-claim rules",
    "Disqualifiers",
]


def _extract_labeled_block(section: str, label: str) -> str:
    labels = "|".join(re.escape(item) for item in SERVICE_SECTION_LABELS)
    pattern = rf"(?ims)^{re.escape(label)}\s*$\s*(.*?)(?=^(?:{labels})\s*$|^5\.\d+\s+|\Z)"
    match = re.search(pattern, section)
    return (match.group(1) if match else "").strip()


def _split_kb_items(value: str, limit: int = 10) -> list[str]:
    if not value:
        return []
    items: list[str] = []
    current = ""
    for raw_line in value.splitlines():
        line = raw_line.strip()
        if not line:
            continue
        bullet_match = re.match(r"^[•\-]\s*(.+)$", line)
        if bullet_match:
            if current:
                items.append(_clip(current, 280))
            current = bullet_match.group(1).strip()
            continue
        if current:
            current += " " + line
        else:
            current = line
    if current:
        items.append(_clip(current, 280))
    if len(items) <= 1 and "\n" not in value:
        sentences = re.split(r"(?<=[.!?])\s+", value.strip())
        items = [_clip(sentence, 280) for sentence in sentences if sentence.strip()]
    return [item for item in items if item][:limit]


def _fallback_service_kb_from_pdf(pdf_text: str) -> dict:
    parts = re.split(r"(?m)^5\.\d+\s+(.+?)\s*$", pdf_text)
    services = []
    for idx in range(1, len(parts), 2):
        section_title = _clip(parts[idx], 180)
        section = parts[idx + 1] if idx + 1 < len(parts) else ""
        name_block = _extract_labeled_block(section, "Service name")
        category_block = _extract_labeled_block(section, "Service category")
        name = _split_kb_items(name_block, 1)[0] if _split_kb_items(name_block, 1) else section_title
        category_items = _split_kb_items(category_block, 2)
        services.append({
            "name": name,
            "category": category_items[0] if category_items else "",
            "buyer_personas": _split_kb_items(_extract_labeled_block(section, "Buyer personas"), 12),
            "pain_triggers": _split_kb_items(_extract_labeled_block(section, "Pain triggers"), 12),
            "evidence_patterns": _split_kb_items(_extract_labeled_block(section, "Evidence patterns that make it relevant"), 12),
            "proof_points": _split_kb_items(_extract_labeled_block(section, "Proof points"), 10),
            "safe_wording": _split_kb_items(_extract_labeled_block(section, "Safe wording"), 8),
            "do_not_claim": _split_kb_items(_extract_labeled_block(section, "Do-not-claim rules"), 8),
            "disqualifiers": _split_kb_items(_extract_labeled_block(section, "Disqualifiers"), 8),
        })
    kb = {
        "version": "pdf-deterministic-fallback",
        "owner": "Vivicta",
        "services": services,
        "extraction_notes": ["Structured deterministically from labeled PDF sections after model JSON parse failed."],
    }
    return _validate_service_kb(kb)


def _validate_service_kb(kb: Any) -> dict:
    if not isinstance(kb, dict):
        raise BuyingPotentialError("Service Portfolio KB is not a JSON object.", 409)
    services = kb.get("services")
    if not isinstance(services, list) or not services:
        raise BuyingPotentialError("Service Portfolio KB must contain a non-empty 'services' array.", 409)
    valid = [svc for svc in services if isinstance(svc, dict) and svc.get("name")]
    if not valid:
        raise BuyingPotentialError("Service Portfolio KB services need at least a 'name' field.", 409)
    for svc in valid:
        for key in ("buyer_personas", "pain_triggers", "evidence_patterns", "proof_points", "safe_wording", "do_not_claim", "disqualifiers"):
            if not isinstance(svc.get(key), list):
                svc[key] = _list(svc.get(key)) if svc.get(key) else []
    kb["services"] = valid
    return kb


def _structure_service_kb_from_pdf(storage, pdf_blob: dict, pdf_text: str) -> dict:
    try:
        raw, model_used = call_json_model_chain(
            _service_kb_extraction_prompt(pdf_text),
            service_kb_extraction_chain(),
            max_tokens=12000,
            timeout=120,
            validate_json=True,
        )
    except Exception as model_exc:
        print(f"   ⚠️ [Buying Potential] Service KB model extraction failed; using deterministic PDF fallback: {model_exc}")
        kb = _fallback_service_kb_from_pdf(pdf_text)
        kb["source_pdf_pathname"] = pdf_blob.get("pathname")
        kb["source_pdf_url"] = pdf_blob.get("url")
        kb["source_pdf_uploaded_at"] = pdf_blob.get("uploadedAt")
        kb["extracted_at"] = datetime.utcnow().isoformat()
        kb["extraction_model"] = f"deterministic_pdf_fallback_after_model_failure: {str(model_exc)[:180]}"
        kb["extracted_text_chars"] = len(pdf_text)
        storage._put(f"{SERVICE_KB_CACHE_PREFIX}.json", kb)
        storage._prune_old_versions(SERVICE_KB_CACHE_PREFIX, keep=3)
        return kb
    try:
        kb = _validate_service_kb(parse_json_object(raw))
    except Exception as parse_exc:
        print(f"   ⚠️ [Buying Potential] Service KB model JSON parse failed; attempting repair: {parse_exc}")
        try:
            repair_raw, repair_model = call_json_model_chain(
                _service_kb_repair_prompt(raw, pdf_text),
                service_kb_repair_chain(),
                max_tokens=12000,
                timeout=90,
                validate_json=True,
            )
            kb = _validate_service_kb(parse_json_object(repair_raw))
            model_used = f"{model_used} -> repaired by {repair_model}"
        except Exception as repair_exc:
            print(f"   ⚠️ [Buying Potential] Service KB repair failed; using deterministic PDF fallback: {repair_exc}")
            kb = _fallback_service_kb_from_pdf(pdf_text)
            model_used = f"{model_used} -> deterministic_pdf_fallback"
    kb["source_pdf_pathname"] = pdf_blob.get("pathname")
    kb["source_pdf_url"] = pdf_blob.get("url")
    kb["source_pdf_uploaded_at"] = pdf_blob.get("uploadedAt")
    kb["extracted_at"] = datetime.utcnow().isoformat()
    kb["extraction_model"] = model_used
    kb["extracted_text_chars"] = len(pdf_text)
    storage._put(f"{SERVICE_KB_CACHE_PREFIX}.json", kb)
    storage._prune_old_versions(SERVICE_KB_CACHE_PREFIX, keep=3)
    return kb


def load_service_portfolio_kb(storage=None) -> dict:
    """Load and cache the Vivicta Service KB from BLOB2 PDF or extracted JSON."""
    if not storage or not storage._load_token():
        raise BuyingPotentialError("BLOB2_READ_WRITE_TOKEN is required for Buying Insight Service KB access.", 409)
    service_prefix = _clean_env(os.environ.get("SERVICE_KB_BLOB_PREFIX"))
    if not service_prefix:
        raise BuyingPotentialError("SERVICE_KB_BLOB_PREFIX is required and must point to the Service KB PDF/prefix in BLOB2.", 409)

    cache_blob = _latest_blob(storage, SERVICE_KB_CACHE_PREFIX, (".json",))
    pdf_blob = _latest_blob(storage, service_prefix, (".pdf",))
    cached_kb = _get_blob_json(storage, cache_blob)
    if cached_kb and (not pdf_blob or str(cache_blob.get("uploadedAt", "")) >= str(pdf_blob.get("uploadedAt", ""))):
        try:
            return _validate_service_kb(cached_kb)
        except BuyingPotentialError as cache_exc:
            if not pdf_blob:
                raise
            print(f"   ⚠️ [Buying Potential] Cached Service KB is invalid; re-extracting from PDF: {cache_exc}")
    if not pdf_blob:
        if cached_kb:
            return _validate_service_kb(cached_kb)
        raise BuyingPotentialError(f"No Service KB PDF found in BLOB2 with prefix '{service_prefix}'.", 409)

    try:
        pdf_text = _extract_pdf_text(_fetch_blob_bytes(pdf_blob))
        return _structure_service_kb_from_pdf(storage, pdf_blob, pdf_text)
    except Exception as exc:
        if cached_kb:
            print(f"   ⚠️ [Buying Potential] Service KB PDF extraction failed; using cached JSON: {exc}")
            return _validate_service_kb(cached_kb)
        if isinstance(exc, BuyingPotentialError):
            raise
        raise BuyingPotentialError(f"Service KB PDF extraction failed: {str(exc)[:240]}", 409)


def validate_service_portfolio_kb(storage=None) -> dict:
    """Validate Service KB config/cache/PDF and return admin-facing diagnostics."""
    started = time.time()
    if not storage or not storage._load_token():
        raise BuyingPotentialError("BLOB2_READ_WRITE_TOKEN is required for Buying Insight Service KB access.", 409)
    service_prefix = _clean_env(os.environ.get("SERVICE_KB_BLOB_PREFIX"))
    if not service_prefix:
        raise BuyingPotentialError("SERVICE_KB_BLOB_PREFIX is required and must point to the Service KB PDF/prefix in BLOB2.", 409)

    cache_blob = _latest_blob(storage, SERVICE_KB_CACHE_PREFIX, (".json",))
    pdf_blob = _latest_blob(storage, service_prefix, (".pdf",))
    cached_kb = _get_blob_json(storage, cache_blob)
    cached_valid = False
    cached_service_count = 0
    if cached_kb:
        try:
            cached_validated = _validate_service_kb(cached_kb)
            cached_valid = True
            cached_service_count = len(_list(cached_validated.get("services")))
        except BuyingPotentialError:
            cached_valid = False

    kb = load_service_portfolio_kb(storage)
    return {
        "ok": True,
        "service_prefix": service_prefix,
        "service_count": len(_list(kb.get("services"))),
        "cache": {
            "exists": bool(cache_blob),
            "valid": cached_valid,
            "service_count": cached_service_count,
            "pathname": cache_blob.get("pathname") if cache_blob else None,
            "uploaded_at": cache_blob.get("uploadedAt") if cache_blob else None,
        },
        "pdf": {
            "exists": bool(pdf_blob),
            "pathname": pdf_blob.get("pathname") if pdf_blob else None,
            "uploaded_at": pdf_blob.get("uploadedAt") if pdf_blob else None,
        },
        "kb_metadata": {
            "version": kb.get("version"),
            "owner": kb.get("owner"),
            "source_pdf_pathname": kb.get("source_pdf_pathname"),
            "source_pdf_uploaded_at": kb.get("source_pdf_uploaded_at"),
            "extracted_at": kb.get("extracted_at"),
            "extraction_model": kb.get("extraction_model"),
            "extracted_text_chars": kb.get("extracted_text_chars"),
        },
        "elapsed_ms": int((time.time() - started) * 1000),
    }


def _source_id(idx: int) -> str:
    return f"SRC-{idx:02d}"


def _company_evidence_records(company_evidence: dict | None) -> tuple[list[dict], list[dict]]:
    if not isinstance(company_evidence, dict):
        return [], []
    records = _list(company_evidence.get("records"))
    strongest = []
    blocked = []
    for idx, rec in enumerate(records, start=1):
        if not isinstance(rec, dict):
            continue
        item = {
            "id": rec.get("id") or f"CED-{idx:02d}",
            "title": _clip(rec.get("title") or rec.get("url"), 160),
            "url": rec.get("url"),
            "source_category": rec.get("source_category"),
            "source_type": rec.get("source_type"),
            "target_fields": rec.get("target_fields") or [],
            "credibility": "official" if rec.get("entity_verified") else "unknown",
            "quote": _clip(rec.get("source_quote") or rec.get("text_excerpt"), 480),
            "claim_support_status": rec.get("claim_support_status"),
            "promotion_status": rec.get("promotion_status"),
            "analysis_verified": rec.get("analysis_verified") is True,
            "generation_eligible": rec.get("buying_evidence_eligible") is True,
            "verification_reason": rec.get("verification_reason"),
            "wrong_entity": rec.get("wrong_entity") is True,
            "allowed_as_fact": rec.get("allowed_as_fact") is True,
            "allowed_as_hypothesis": rec.get("allowed_as_hypothesis") is True,
            "allowed_as_discovery_question": rec.get("allowed_as_discovery_question") is True,
            "internal_only": rec.get("internal_only") is True,
            "blocked": rec.get("blocked") is True,
            "source_freshness": rec.get("source_freshness"),
            "fetched_at": rec.get("fetched_at"),
            "independence": rec.get("independence") or rec.get("source_independence"),
            "entity_match": rec.get("entity_match") or ("exact" if rec.get("entity_verified") else "unknown"),
            "source_database": "company_evidence",
            "sales_use": rec.get("sales_use"),
        }
        if item["generation_eligible"]:
            strongest.append(item)
        else:
            blocked.append(item)
    return strongest[:30], blocked[:30]


def _company_evidence_stakeholders(company_evidence: dict | None) -> list[dict]:
    if not isinstance(company_evidence, dict):
        return []
    candidates = []
    for rec in _list(company_evidence.get("records")):
        if not isinstance(rec, dict):
            continue
        if rec.get("provider_record_type") != "stakeholder_candidate" and rec.get("source_category") != "stakeholder_candidate":
            continue
        stakeholder = _dict(rec.get("stakeholder"))
        name = stakeholder.get("name")
        role = stakeholder.get("role")
        if not name or not role:
            continue
        candidates.append({
            "name": name,
            "role": role,
            "insight": _clip(rec.get("source_quote") or rec.get("text_excerpt"), 420),
            "confidence": rec.get("extraction_confidence") or 50,
            "source": stakeholder.get("public_profile_url") or rec.get("url"),
            "evidence_basis": stakeholder.get("evidence_basis") or rec.get("source_quote") or rec.get("title") or rec.get("url"),
            "source_database": "company_evidence",
            "provider": rec.get("provider"),
            "allowed_use": stakeholder.get("allowed_use") or rec.get("sales_use") or "allowed_as_discovery_question",
            "matched_demand_groups": stakeholder.get("matched_demand_groups") or rec.get("demand_signal_ids") or [],
            "linked_service_ids": stakeholder.get("linked_service_ids") or rec.get("linked_service_ids") or [],
            "stakeholder_role_match_reason": stakeholder.get("stakeholder_role_match_reason") or rec.get("stakeholder_role_match_reason"),
            "safety_notes": stakeholder.get("safety_notes") or rec.get("safety_notes") or [],
            "do_not_say": stakeholder.get("do_not_say") or rec.get("do_not_say") or [],
        })
    return candidates[:12]


def _stakeholder_sort_score(item: dict) -> tuple[int, int, str]:
    role = str(item.get("role") or item.get("position") or "").lower()
    allowed = str(item.get("allowed_use") or item.get("sales_use") or "").lower()
    source = str(item.get("source_database") or item.get("source") or "").lower()
    score = 0
    if "chief" in role or re.search(r"\b(cxo|ceo|cfo|coo|cio|cto|ciso|cdo|chro)\b", role):
        score += 40
    if any(token in role for token in ("director", "head of", "vp", "vice president", "board", "chair")):
        score += 25
    if "allowed_as_discovery_question" in allowed or "allowed_as_hypothesis" in allowed:
        score += 15
    if "company_evidence" in source:
        score += 10
    if "company_source_ledger" in source:
        score += 5
    confidence = _safe_int(item.get("confidence"), 0)
    return (-score, -confidence, str(item.get("name") or "").lower())


def _is_historical_stakeholder_role(role: Any) -> bool:
    text = str(role or "").lower()
    return bool(re.search(r"\b(retired|ret\.|former|ex-|emeritus|previous|past)\b", text))


def _simple_stakeholder_item(
    name: Any,
    role: Any,
    *,
    source_database: str,
    source: Any = None,
    evidence_basis: Any = None,
    allowed_use: Any = None,
    confidence: Any = None,
) -> dict | None:
    name_text = _clip(name, 120)
    role_text = _clip(role, 160)
    if not name_text or not role_text:
        return None
    if len(name_text.split()) < 2:
        return None
    if _is_historical_stakeholder_role(role_text):
        return None
    allowed = _normalize_gate(allowed_use, "allowed_as_discovery_question")
    if allowed == "blocked":
        return None
    return {
        "name": name_text,
        "position": role_text,
        "source_database": source_database,
        "source": _clip(source, 220),
        "evidence_basis": _clip(evidence_basis or source or source_database, 260),
        "allowed_use": allowed,
        "confidence": _safe_int(confidence, 45),
    }


def _add_simple_stakeholder(target: list[dict], seen: set[str], item: dict | None) -> None:
    if not item:
        return
    key = re.sub(r"\s+", " ", str(item.get("name") or "").strip().lower())
    if not key or key in seen:
        return
    seen.add(key)
    target.append(item)


def _stakeholder_name_list(
    company_data: dict,
    company_evidence: dict | None = None,
    company_source_ledger: dict | None = None,
    *,
    limit: int = 6,
) -> list[dict]:
    people: list[dict] = []
    seen: set[str] = set()

    for rec in _list(company_evidence.get("records") if isinstance(company_evidence, dict) else None):
        if not isinstance(rec, dict) or rec.get("blocked") is True or rec.get("wrong_entity") is True:
            continue
        stakeholder = _dict(rec.get("stakeholder"))
        if stakeholder:
            _add_simple_stakeholder(
                people,
                seen,
                _simple_stakeholder_item(
                    stakeholder.get("name"),
                    stakeholder.get("role"),
                    source_database="company_evidence",
                    source=stakeholder.get("public_profile_url") or rec.get("url"),
                    evidence_basis=stakeholder.get("evidence_basis") or rec.get("source_quote") or rec.get("title"),
                    allowed_use=stakeholder.get("allowed_use") or rec.get("sales_use"),
                    confidence=rec.get("extraction_confidence"),
                ),
            )
        coresignal_fields = _dict(rec.get("coresignal_fields"))
        for executive in _list(coresignal_fields.get("key_executives")):
            if not isinstance(executive, dict):
                continue
            _add_simple_stakeholder(
                people,
                seen,
                _simple_stakeholder_item(
                    executive.get("name") or executive.get("member_full_name") or executive.get("full_name"),
                    executive.get("title") or executive.get("member_position_title") or executive.get("role"),
                    source_database="company_evidence",
                    source=rec.get("url"),
                    evidence_basis=rec.get("title") or "Coresignal key executives in Company Evidence DB",
                    allowed_use="allowed_as_discovery_question",
                    confidence=rec.get("extraction_confidence"),
                ),
            )

    for rec in _list(company_source_ledger.get("records") if isinstance(company_source_ledger, dict) else None):
        if not isinstance(rec, dict) or rec.get("blocked") is True or rec.get("wrong_entity") is True:
            continue
        stakeholder = _dict(rec.get("stakeholder"))
        _add_simple_stakeholder(
            people,
            seen,
            _simple_stakeholder_item(
                stakeholder.get("name"),
                stakeholder.get("role"),
                source_database="company_source_ledger",
                source=stakeholder.get("public_profile_url") or rec.get("url"),
                evidence_basis=stakeholder.get("evidence_basis") or rec.get("source_quote") or rec.get("title"),
                allowed_use=stakeholder.get("allowed_use") or rec.get("sales_use"),
                confidence=rec.get("extraction_confidence"),
            ),
        )

    if len(people) < limit:
        for person in _list(company_data.get("key_people")):
            if not isinstance(person, dict):
                continue
            _add_simple_stakeholder(
                people,
                seen,
                _simple_stakeholder_item(
                    person.get("name"),
                    person.get("role") or person.get("title"),
                    source_database="scanner_key_people",
                    source=person.get("source") or person.get("source_url"),
                    evidence_basis=person.get("source") or person.get("source_url") or "Stored Scanner stakeholder compatibility data",
                    allowed_use="allowed_as_discovery_question" if (person.get("source") or person.get("source_url")) else "internal_only",
                    confidence=person.get("confidence") or person.get("confidence_score"),
                ),
            )
            if len(people) >= limit:
                break

    people.sort(key=_stakeholder_sort_score)
    return people[:limit]


def _company_source_ledger_records(company_source_ledger: dict | None) -> tuple[list[dict], list[dict]]:
    if not isinstance(company_source_ledger, dict):
        return [], []
    strongest = []
    blocked = []
    for idx, rec in enumerate(_list(company_source_ledger.get("records")), start=1):
        if not isinstance(rec, dict):
            continue
        item = {
            "id": rec.get("id") or f"LEDGER-{idx:03d}",
            "title": _clip(rec.get("title") or rec.get("url"), 160),
            "url": rec.get("url"),
            "source_type": rec.get("source_type") or rec.get("source_category"),
            "credibility": rec.get("source_credibility") or "unknown",
            "quote": _clip(rec.get("source_quote") or rec.get("snippet"), 480),
            "claim_support_status": rec.get("claim_support_status"),
            "promotion_status": rec.get("promotion_status"),
            "analysis_verified": rec.get("analysis_verified") is True,
            "generation_eligible": rec.get("generation_eligible") is True,
            "buying_evidence_eligible": rec.get("buying_evidence_eligible") is True,
            "allowed_as_fact": rec.get("allowed_as_fact") is True,
            "allowed_as_hypothesis": rec.get("allowed_as_hypothesis") is True,
            "allowed_as_discovery_question": rec.get("allowed_as_discovery_question") is True,
            "internal_only": rec.get("internal_only") is True,
            "blocked": rec.get("blocked") is True,
            "source_freshness": rec.get("source_freshness"),
            "last_checked": rec.get("last_checked"),
            "independence": rec.get("independence") or rec.get("source_independence"),
            "entity_match": rec.get("entity_match") or ("exact" if rec.get("entity_verified") else "wrong" if rec.get("wrong_entity") else "unknown"),
            "source_database": "company_source_ledger",
            "origins": rec.get("origins", []),
            "wrong_entity": rec.get("wrong_entity") is True,
            "verification_reason": rec.get("verification_reason"),
        }
        if item["buying_evidence_eligible"] or item["allowed_as_fact"]:
            strongest.append(item)
        else:
            blocked.append(item)
    return strongest[:30], blocked[:30]


def _company_source_ledger_recovery_records(company_source_ledger: dict | None, limit: int = 24) -> list[dict]:
    if not isinstance(company_source_ledger, dict):
        return []
    records: list[dict] = []
    for idx, rec in enumerate(_list(company_source_ledger.get("records")), start=1):
        if not isinstance(rec, dict):
            continue
        if rec.get("blocked") is True or rec.get("wrong_entity") is True:
            continue
        usable = (
            rec.get("buying_evidence_eligible") is True
            or rec.get("allowed_as_fact") is True
            or rec.get("allowed_as_hypothesis") is True
            or rec.get("allowed_as_discovery_question") is True
            or rec.get("usable_for_buying_insight") is True
        )
        if not usable:
            continue
        records.append({
            "id": rec.get("id") or f"LEDGER-{idx:03d}",
            "title": _clip(rec.get("title") or rec.get("url"), 180),
            "url": rec.get("url"),
            "quote": _clip(rec.get("source_quote") or rec.get("snippet") or rec.get("summary"), 520),
            "source_type": rec.get("source_type") or rec.get("source_category"),
            "gate": _source_item_gate(rec, "allowed_as_discovery_question"),
            "buying_evidence_eligible": rec.get("buying_evidence_eligible") is True,
            "allowed_as_fact": rec.get("allowed_as_fact") is True,
            "allowed_as_hypothesis": rec.get("allowed_as_hypothesis") is True,
            "allowed_as_discovery_question": rec.get("allowed_as_discovery_question") is True,
            "internal_only": rec.get("internal_only") is True,
            "source_freshness": rec.get("source_freshness"),
            "last_checked": rec.get("last_checked"),
            "verification_reason": rec.get("verification_reason"),
            "origins": rec.get("origins", []),
        })
        if len(records) >= limit:
            break
    return records


def _extract_evidence(
    company_data: dict,
    company_evidence: dict | None = None,
    company_source_ledger: dict | None = None,
) -> tuple[list[dict], list[dict]]:
    raw_links = _list(company_data.get("evidence_links")) + _list(company_data.get("foresight_links"))
    strongest: list[dict] = []
    blocked: list[dict] = []
    for idx, link in enumerate(raw_links, start=1):
        if not isinstance(link, dict):
            continue
        item = {
            "id": _source_id(idx),
            "title": _clip(link.get("title") or link.get("source") or link.get("url"), 160),
            "url": link.get("url"),
            "source_type": link.get("source_type") or link.get("claim_type") or link.get("scope"),
            "credibility": link.get("source_credibility") or link.get("credibility") or "unknown",
            "quote": _clip(link.get("source_quote") or link.get("snippet") or link.get("description"), 480),
            "claim_support_status": link.get("claim_support_status"),
            "promotion_status": link.get("promotion_status"),
            "analysis_verified": link.get("analysis_verified") is True,
            "generation_eligible": is_generation_eligible_source(link),
            "verification_reason": link.get("verification_reason"),
            "wrong_entity": link.get("wrong_entity") is True,
            "source_freshness": link.get("source_freshness") or link.get("freshness"),
            "fetched_at": link.get("fetched_at") or link.get("last_checked"),
            "independence": link.get("independence") or link.get("source_independence"),
            "entity_match": link.get("entity_match") or ("exact" if link.get("entity_verified") else "wrong" if link.get("wrong_entity") else "unknown"),
        }
        if item["generation_eligible"]:
            strongest.append(item)
        else:
            blocked.append(item)
    company_strongest, company_blocked = _company_evidence_records(company_evidence)
    ledger_strongest, ledger_blocked = _company_source_ledger_records(company_source_ledger)
    return (ledger_strongest + company_strongest + strongest)[:36], (ledger_blocked + company_blocked + blocked)[:36]


def _financial_evidence(company_data: dict) -> list[dict]:
    items = []
    for key in ("financial_findings", "financial_health_findings", "financial_evidence"):
        for idx, item in enumerate(_list(company_data.get(key)), start=1):
            if not isinstance(item, dict):
                continue
            if not (item.get("source_url") or item.get("url")):
                continue
            items.append({
                "id": f"FIN-{len(items) + 1:02d}",
                "title": _clip(item.get("source_title") or item.get("title") or key, 160),
                "url": item.get("source_url") or item.get("url"),
                "quote": _clip(item.get("evidence_snippet") or item.get("snippet") or item.get("finding"), 480),
                "metric": item.get("metric") or item.get("label") or item.get("finding_type"),
                "value": item.get("value"),
                "year": item.get("year"),
                "generation_eligible": bool(item.get("generation_eligible") or item.get("analysis_verified")),
                "source_freshness": item.get("source_freshness") or item.get("freshness"),
                "entity_match": item.get("entity_match") or item.get("entity_scope") or "unknown",
                "independence": item.get("independence") or "unknown",
            })
    return items[:12]


def _is_independent_source_item(item: dict) -> bool:
    independence = str(item.get("independence") or "").strip().lower()
    if independence:
        return independence in {"independent", "third_party", "third-party", "external", "official_registry"}
    source_type = str(item.get("source_type") or item.get("source_category") or item.get("credibility") or "").lower()
    return any(token in source_type for token in ("registry", "filing", "government", "authority", "financial database", "news", "media", "analyst"))


def _derive_evidence_sufficiency(evidence: list[dict], financial_sources: list[dict], stored: dict | None = None) -> dict:
    stored = _dict(stored)
    stored_status = str(stored.get("status") or "")
    normalized_stored_status = {
        "low_promoted_evidence": "partial",
        "source_register_incomplete": "partial",
    }.get(stored_status, stored_status)
    eligible = [item for item in evidence if isinstance(item, dict) and item.get("generation_eligible") is True]
    independent = [item for item in eligible if _is_independent_source_item(item)]
    fresh = [
        item for item in eligible
        if str(item.get("source_freshness") or "").lower() in {"fresh", "current", "recent"}
        or bool(item.get("fetched_at") or item.get("last_checked"))
    ]
    exact_entity = [
        item for item in eligible
        if str(item.get("entity_match") or "").lower() in {"exact", "verified", "canonical", "canonical_legal_entity"}
    ]
    claim_types = {
        str(item.get("source_type") or item.get("source_category") or "unknown").strip().lower()
        for item in eligible
    }
    stored_complete = normalized_stored_status == "complete"
    if not eligible and not financial_sources:
        status = "insufficient"
    elif (
        len(eligible) >= 4
        and len(independent) >= 1
        and len(claim_types) >= 2
        and (len(fresh) >= 1 or stored_complete)
        and (len(exact_entity) >= 1 or stored_complete)
    ):
        status = "complete"
    else:
        status = "partial"
    if normalized_stored_status in {"insufficient", "partial", "complete"}:
        status_order = {"insufficient": 0, "partial": 1, "complete": 2}
        if status_order[normalized_stored_status] < status_order[status]:
            status = normalized_stored_status
    return {
        **stored,
        "status": status,
        "generation_eligible_count": len(eligible),
        "independent_source_count": len(independent),
        "fresh_source_count": len(fresh),
        "exact_entity_count": len(exact_entity),
        "claim_type_coverage": len(claim_types),
        "financial_source_count": len(financial_sources),
        "confidence_boundary": (
            "Evidence supports disciplined discovery use."
            if status == "complete"
            else "Evidence is partial; preserve question-led or hypothesis wording."
            if status == "partial"
            else "Evidence is insufficient for customer-facing use."
        ),
    }


def _infer_buyer_roles(text: str, limit: int = 3) -> list[str]:
    text_l = str(text or "").lower()
    roles = []
    for role, keywords in ROLE_KEYWORDS:
        if any(keyword in text_l for keyword in keywords):
            roles.append(role)
    if not roles:
        roles = ["CEO / Managing Director", "Strategy / Growth Lead"]
    return roles[:limit]


def _buyer_role_concern(roles: list[str], pressure: str) -> str:
    parts = []
    for role in roles[:3]:
        lens = BUYER_ROLE_LENSES.get(role, {})
        outcome = lens.get("owned_outcome")
        if outcome:
            parts.append(f"{role}: {outcome}")
    if not parts:
        return _clip(pressure, 360)
    return _clip("; ".join(parts), 520)


def _infer_pressure(text: str) -> tuple[str, str, str]:
    text_l = str(text or "").lower()
    if any(term in text_l for term in ("industrial ai", "enterprise-ai", "enterprise ai", "agentic ai", "ai readiness", "ai-enabled")):
        return (
            "Industrial AI and governed-readiness pressure",
            "Industrial AI is moving toward governed, adaptive operations, increasing the importance of reliable data, OT/IT integration, architecture, and accountable operating ownership.",
            "Without a shared readiness view, the company may struggle to prioritize scalable AI-enabled opportunities or distinguish useful investment from isolated experimentation.",
        )
    if any(term in text_l for term in ("product passport", "digital passport", "product traceability", "lifecycle traceability", "energy productivity")):
        return (
            "Energy-productivity and product-traceability pressure",
            "Energy productivity and digital-product-passport expectations may increase the value of measurable efficiency, governed lifecycle data, and auditable product traceability.",
            "If the pressure becomes material, fragmented product and operational data could weaken investment prioritization, compliance readiness, or the ability to convert sustainability activity into measurable business outcomes.",
        )
    if any(word in text_l for word in ("security", "cyber", "nis2", "dora", "compliance", "regulation", "csrd", "sovereign")):
        return (
            "Regulatory, security, and resilience pressure",
            "Regulatory or security exposure may increase the need for clearer controls, evidence, resilience, and responsibility structures.",
            "Compliance and resilience pressure can become a business risk if evidence, ownership, and controls remain fragmented.",
        )
    if any(word in text_l for word in ("growth", "revenue", "ramp", "capacity", "production", "scale", "scaling")):
        return (
            "Scaling and operating-model pressure",
            "Growth or capacity movement may increase pressure on planning, coordination, quality, delivery reliability, and management visibility.",
            "Growth can become harder to convert into resilient execution if processes, data, and accountability structures do not scale with demand.",
        )
    if any(word in text_l for word in ("partner", "supplier", "subcontract", "procurement", "supply")):
        return (
            "Partner and supplier coordination pressure",
            "Partner-led delivery or supplier dependency may increase the need for shared visibility, governance, and reliable coordination routines.",
            "Weak coordination can create delivery friction, quality variance, margin leakage, or slower response to customer demand.",
        )
    if re.search(r"\bai\b", text_l) or any(
        word in text_l for word in ("autonomy", "data", "software", "platform", "digital", "cloud", "erp", "sap")
    ):
        return (
            "Digital, data, and architecture pressure",
            "Digital or technology-adjacent signals may create pressure for stronger data architecture, platform governance, integration, and secure foundations.",
            "Technology complexity can outgrow ad hoc governance, making architecture, lifecycle data, and operating ownership more important.",
        )
    if any(word in text_l for word in ("margin", "cost", "profit", "cash", "investment", "funding")):
        return (
            "Financial control and investment-prioritization pressure",
            "Financial movement may create a need for clearer cost visibility, investment prioritization, and margin-protection logic.",
            "Commercial momentum can become less valuable if the company cannot see where investments, costs, and operational constraints are accumulating.",
        )
    return (
        "Strategic transformation pressure",
        "The account signal may indicate a need to validate how strategy, operating model, technology, and execution capacity fit together.",
        "A weak link between ambition and operating reality can create delayed transformation, unfocused investment, or missed commercial timing.",
    )


def _infer_timing(text: str) -> str:
    text_l = str(text or "").lower()
    if re.search(r"\b20\d{2}\b|recent|latest|new|launched|announced|signed|investment|growth|ramp", text_l):
        return "Recent or dated evidence suggests this may be worth validating now or soon, but timing should be confirmed in discovery."
    return "Timing is not proven; use as a monitoring or soft-discovery signal unless a current trigger is confirmed."


PLAY_TYPE_LABELS = {
    "current_status": "Current-status play",
    "foresight_driven": "Foresight-driven play",
    "proactive_opportunity": "Proactive opportunity play",
}

CONTEXT_TYPE_LABELS = {
    "company_only": "Account need signal",
    "market_watch": "Market pressure signal",
    "combined_activation": "Need-emergence signal",
}

ARCHETYPE_TERMS = ("Thriving", "Modernizing", "Cash Cow", "At Risk")


def _normalize_play_type(value: Any) -> str:
    token = re.sub(r"[^a-z0-9]+", "_", str(value or "").strip().lower()).strip("_")
    if token in {"current", "current_state", "current_status", "status"}:
        return "current_status"
    if token in {"foresight", "future", "future_pressure", "foresight_driven"}:
        return "foresight_driven"
    if token in {"proactive", "opportunity", "proactive_opportunity"}:
        return "proactive_opportunity"
    return "proactive_opportunity"


def _strip_archetype_terms(value: Any) -> Any:
    if isinstance(value, list):
        return [_strip_archetype_terms(item) for item in value]
    if isinstance(value, dict):
        return {key: _strip_archetype_terms(item) for key, item in value.items()}
    if value is None or isinstance(value, str):
        text = str(value or "")
    else:
        return value
    replacements = {
        "Thriving": "commercially strong",
        "Modernizing": "modernizing",
        "Cash Cow": "financially capable but operationally under-proven",
        "At Risk": "financially and operationally pressured",
    }
    for label, replacement in replacements.items():
        text = re.sub(rf"\b{re.escape(label)}\b", replacement, text)
        text = re.sub(rf"\b{re.escape(label.upper())}\b", replacement, text)
    return text


def _normalize_context_type(value: Any, play_type: Any = None) -> str:
    token = re.sub(r"[^a-z0-9]+", "_", str(value or "").strip().lower()).strip("_")
    if token in {"company", "company_only", "company_context", "current_status"}:
        return "company_only"
    if token in {"market", "market_watch", "market_pressure", "foresight", "foresight_driven"}:
        return "market_watch"
    if token in {"combined", "combined_activation", "activation"}:
        return "combined_activation"
    normalized_play_type = _normalize_play_type(play_type)
    if normalized_play_type == "current_status":
        return "company_only"
    if normalized_play_type == "foresight_driven":
        return "market_watch"
    return "combined_activation"


def _normalize_priority_band(value: Any, readiness_band: str = "") -> str:
    token = str(value or "").strip().lower()
    if token.startswith("priority a") or "high-priority" in token or token == "validate now":
        return "Priority A - validate now"
    if token.startswith("priority b") or "trigger" in token or "qualified" in token:
        return "Priority B - validate when trigger appears"
    if token.startswith("priority c") or "monitor" in token or "watch" in token:
        return "Priority C - monitor"
    if token.startswith("reject") or "insufficient" in token:
        return "Reject - insufficient account connection"
    return _priority_band(readiness_band)


def _source_item_gate(item: dict, fallback: str = "allowed_as_discovery_question") -> str:
    """Preserve source-level sales/evidence gates instead of promoting them in Buying Insight."""
    if not isinstance(item, dict):
        return fallback
    explicit = item.get("gate") or item.get("allowed_use") or item.get("sales_use")
    gate = _normalize_gate(explicit, "")
    if gate:
        return gate
    if item.get("blocked") or item.get("wrong_entity"):
        return "blocked"
    if item.get("internal_only"):
        return "internal_only"
    if item.get("allowed_as_fact"):
        return "allowed_as_fact"
    if item.get("allowed_as_hypothesis"):
        return "allowed_as_hypothesis"
    if item.get("allowed_as_discovery_question"):
        return "allowed_as_discovery_question"
    if item.get("generation_eligible") or item.get("buying_evidence_eligible"):
        return "allowed_as_hypothesis"
    return fallback


def _status_scale() -> list[dict]:
    return [item.copy() for item in RELIABILITY_STATUS_SCALE]


def _status_template(code: str) -> dict:
    code = str(code or "C").upper()
    for item in RELIABILITY_STATUS_SCALE:
        if item["code"] == code:
            return item.copy()
    return RELIABILITY_STATUS_SCALE[2].copy()


def _compute_reliability_status(report: dict, payload: dict, gate_review: dict | None, *, low_evidence: bool) -> dict:
    verified_count = len(payload.get("verified_evidence") or [])
    financial_count = len(payload.get("financial_sources") or [])
    signal_count = len(_list(payload.get("account_signal_map")))
    plays = [item for item in _list(report.get("recommended_sales_plays")) if isinstance(item, dict)]
    eligible_play_count = 0
    strong_play_count = 0
    blocked_play_count = 0
    question_only_count = 0

    for play in plays:
        gate = _normalize_gate(play.get("gate"), "allowed_as_hypothesis")
        readiness = str(play.get("readiness_band") or "")
        classification = str(play.get("play_classification") or "")
        if readiness in {"Validate now", "Validate when trigger appears"} and gate in {"allowed_as_fact", "allowed_as_hypothesis", "allowed_as_discovery_question"}:
            eligible_play_count += 1
        if readiness == "Validate now" and gate in {"allowed_as_fact", "allowed_as_hypothesis"}:
            strong_play_count += 1
        if gate in {"allowed_as_discovery_question", "internal_only"}:
            question_only_count += 1
        if gate == "blocked" or classification == "do_not_use":
            blocked_play_count += 1

    evidence_confidence = _dict(report.get("evidence_confidence"))
    do_not_say_count = len(_list(evidence_confidence.get("do_not_say") or evidence_confidence.get("do_not_use_claims")))
    gate_blocked_count = len(_list(_dict(gate_review).get("blocked_claims"))) if gate_review else 0
    gate_status = str(_dict(gate_review).get("status") or ("approved" if _dict(gate_review).get("approved") is True else "")).lower()
    gate_unavailable = gate_status == "unavailable"
    gate_changes_required = gate_status == "changes_required"
    deterministic_fallback = any(
        "deterministic fallback" in str(note).lower()
        for note in _list(report.get("model_notes")) + _list(report.get("missing_input_warnings"))
    )
    sufficiency = _dict(payload.get("evidence_sufficiency"))
    sufficiency_status = str(sufficiency.get("status") or "partial")
    independent_source_count = _safe_int(sufficiency.get("independent_source_count"), 0)
    general_summary_check = _dict(_dict(payload.get("scanner_outputs")).get("general_summary_fact_check"))
    general_summary_verified = general_summary_check.get("verified") is True
    general_summary_false_claims = _list(general_summary_check.get("false_or_unsupported_claims"))

    reasons: list[str] = []
    code = "B"
    if verified_count == 0 and financial_count == 0:
        reasons.append("No generation-eligible evidence or financial references were available.")
    elif low_evidence:
        reasons.append("Evidence coverage is constrained; customer-facing wording must remain question-led.")
    else:
        reasons.append(f"{verified_count} generation-eligible evidence item(s) and {financial_count} financial reference(s) support internal analysis.")
    if signal_count == 0:
        reasons.append("No account signal map was available.")
    if not plays:
        reasons.append("No recommended sales plays were generated.")
    elif eligible_play_count == 0:
        reasons.append("No sales play reached a discovery-ready gate.")
    if question_only_count:
        reasons.append(f"{question_only_count} play(s) are question-led or internal-only.")
    if blocked_play_count or gate_blocked_count or do_not_say_count:
        reasons.append("Guardrails contain blocked or do-not-say material that must not be used customer-facing.")
    if gate_unavailable:
        reasons.append("Independent semantic gate review was unavailable; deterministic evidence controls remain authoritative.")
    elif gate_changes_required:
        reasons.append("Independent semantic gate review required bounded wording or gate corrections, which were applied.")
    if deterministic_fallback:
        reasons.append("One or more generated sections used deterministic fallback.")
    if sufficiency_status != "complete":
        reasons.append(f"Scanner evidence sufficiency is {sufficiency_status}; independence, freshness, entity certainty, or claim coverage remains constrained.")
    if independent_source_count == 0:
        reasons.append("No independent generation-eligible source was available; discovery must remain question-led.")
    if not general_summary_verified:
        reasons.append("General Summary fact-check was skipped, unavailable, or not fully verified.")
    if general_summary_false_claims:
        reasons.append("General Summary fact-check retained unsupported-claim warnings that require internal review.")

    if (verified_count == 0 and financial_count == 0 and eligible_play_count == 0) or not plays:
        code = "D"
    elif blocked_play_count >= max(1, len(plays)) or (gate_blocked_count >= 3 and low_evidence):
        code = "D"
    elif low_evidence or eligible_play_count == 0 or question_only_count == len(plays) or gate_unavailable or deterministic_fallback:
        code = "C"
    elif strong_play_count >= 1 and verified_count >= 4 and financial_count >= 1 and not gate_blocked_count:
        code = "A"
    else:
        code = "B"

    if (not general_summary_verified or general_summary_false_claims) and code in {"A", "B"}:
        code = "C"
    elif sufficiency_status == "partial" and code == "A":
        code = "B"
    elif sufficiency_status == "insufficient" and code in {"A", "B"}:
        code = "C"
    if independent_source_count == 0 and code in {"A", "B"}:
        code = "C"
    if _safe_int(_dict(gate_review).get("deterministic_removal_count"), 0) and code in {"A", "B"}:
        code = "C"

    status = _status_template(code)
    status["reasons"] = list(dict.fromkeys(reason for reason in reasons if reason))[:8]
    status["scale"] = _status_scale()
    status["current"] = True
    return status


def _readiness_band(score: int, *, has_signal: bool, has_service: bool, has_question: bool, gate: str = "") -> str:
    gate = _normalize_gate(gate, "allowed_as_hypothesis")
    if gate == "blocked" or not has_signal:
        return "Insufficient account connection"
    if not has_service:
        return "Monitor"
    if gate in {"allowed_as_discovery_question", "internal_only"}:
        return "Validate when trigger appears" if has_question else "Monitor"
    if score >= 70 and has_question:
        return "Validate now"
    if score >= 45:
        return "Validate when trigger appears"
    return "Monitor"


def _has_current_timing_basis(signal: dict | None, play: dict | None = None) -> bool:
    signal = _dict(signal)
    play = _dict(play)
    explicit = " ".join(
        str(value or "")
        for value in (
            signal.get("timing_basis"),
            signal.get("sales_validation_window"),
            play.get("timing_basis"),
            play.get("trigger"),
        )
    ).lower()
    if not explicit.strip():
        return False
    current_markers = ("current", "already", "now", "this quarter", "this year", "active", "near-term", "near term")
    unsupported_markers = ("until customer timing is confirmed", "when a trigger appears", "if the pressure develops")
    return any(marker in explicit for marker in current_markers) and not any(marker in explicit for marker in unsupported_markers)


def _priority_band(readiness_band: str) -> str:
    return {
        "Validate now": "Priority A - validate now",
        "Validate when trigger appears": "Priority B - validate when trigger appears",
        "Monitor": "Priority C - monitor",
        "Insufficient account connection": "Reject - insufficient account connection",
    }.get(str(readiness_band or ""), "Priority C - monitor")


def _evidence_coverage(*, has_signal: bool, has_service: bool, has_question: bool, low_evidence: bool = False) -> str:
    if low_evidence:
        return "Constrained evidence coverage"
    if has_signal and has_service and has_question:
        return "Sufficient for discovery"
    if has_signal and has_question:
        return "Account signal present; service fit still needs validation"
    if has_signal:
        return "Account signal present; discovery framing incomplete"
    return "Insufficient account-specific evidence"


def _confidence_boundary(gate: str, evidence_coverage: str) -> str:
    gate = _normalize_gate(gate, "allowed_as_hypothesis")
    if gate == "allowed_as_fact":
        return "Source-supported fact boundary; do not extend beyond the cited evidence."
    if gate == "allowed_as_hypothesis":
        return f"Hypothesis boundary: {evidence_coverage}. Use as demand-creation reasoning, not proof of intent."
    if gate == "allowed_as_discovery_question":
        return f"Question-only boundary: {evidence_coverage}. Ask before asserting."
    if gate == "internal_only":
        return "Internal sales-prep boundary; do not use as customer-facing claim without stronger evidence."
    return "Blocked boundary; do not use in customer-facing or proposal logic."


def _activation_pair_text(pair: dict) -> str:
    return _clip(
        " | ".join(
            str(pair.get(key) or "")
            for key in ("theme", "why", "what_to_do", "what", "what_progress_shows")
            if pair.get(key)
        ),
        1200,
    )


def _need_hypothesis_from_signal(signal: dict) -> dict:
    text = _clip(
        " | ".join(
            str(signal.get(key) or "")
            for key in ("theme", "why", "what", "account_movement", "commercial_pressure")
            if signal.get(key)
        ),
        1400,
    )
    pressure_label, pressure, consequence = _infer_pressure(text)
    play_type = _normalize_play_type(signal.get("sales_play_type") or signal.get("play_type") or signal.get("theme_type"))
    current_condition = _clip(signal.get("why") or signal.get("account_movement") or text, 420)
    explicit_need = _dict(signal.get("need_hypothesis"))
    explicit_pressure = _first_nonempty(
        explicit_need.get("future_pressure"),
        signal.get("external_pressure"),
        signal.get("foresight_pressure"),
        limit=520,
    )
    if play_type == "current_status" and not explicit_pressure:
        future_pressure = "No separate external pressure is established for this current-status hypothesis."
    else:
        future_pressure = _clip(explicit_pressure or pressure, 520)
    structural_exposure = _clip(signal.get("theme") or pressure_label, 260)
    likely_business_consequence = _clip(signal.get("business_consequence") or consequence, 420)
    what_to_propose = _clip(
        signal.get("what_to_do")
        or signal.get("what")
        or "Validate whether the pressure is real, owned, and commercially relevant before proposing a service path.",
        420,
    )
    progress_signal = _clip(signal.get("what_progress_shows"), 420)
    why_now_or_soon = _clip(signal.get("timing_relevance") or _infer_timing(text), 360)
    context_type = _normalize_context_type(signal.get("context_type"), play_type)
    company_context = _clip(current_condition, 520)
    market_context = _clip(
        explicit_need.get("market_context")
        or future_pressure,
        520,
    )
    if play_type == "current_status":
        combined_activation = (
            f"Account signal: {current_condition}. Sales hypothesis: validate the response direction "
            f"before this becomes an operating or financial constraint. Potential response to validate: {what_to_propose}."
        )
    elif play_type == "foresight_driven":
        combined_activation = (
            f"External pressure: {market_context}. Account-specific question: whether this connects to "
            f"the current condition: {current_condition}. Potential response to validate: {what_to_propose}."
        )
    else:
        combined_activation = (
            f"Proactive opportunity: test whether this account condition becomes more commercially relevant "
            f"as the market pressure develops. Account condition: {current_condition}. Market pressure: "
            f"{market_context}. Potential response to validate: {what_to_propose}."
        )
    return {
        "future_pressure": future_pressure,
        "current_condition": current_condition,
        "structural_exposure": structural_exposure,
        "likely_business_consequence": likely_business_consequence,
        "why_now_or_soon": why_now_or_soon,
        "what_to_propose": what_to_propose,
        "what_progress_shows": progress_signal,
        "discovery_question": "Where would this pressure become most visible if it starts affecting priorities, operating capacity, or investment choices?",
        "disqualifier": "Do not advance this as a sales play if the customer confirms no owner, pressure, timing, or business consequence.",
        "company_context": company_context,
        "market_context": market_context,
        "combined_activation_hypothesis": _clip(combined_activation, 700),
        "falsifier": "The hypothesis weakens if the customer confirms the condition is already controlled, the market pressure is not relevant, or there is no owner, timing, or business consequence.",
        "context_type": context_type,
    }


def _normalize_need_hypothesis(need: Any, fallback_signal: dict, play_type: Any = None) -> dict:
    normalized = _dict(need).copy()
    fallback = _need_hypothesis_from_signal(fallback_signal)
    for key, value in fallback.items():
        normalized[key] = _clip(normalized.get(key) or value, 700)
    normalized["context_type"] = _normalize_context_type(normalized.get("context_type"), play_type or fallback_signal.get("play_type"))
    if normalized["context_type"] == "company_only" and not normalized.get("market_context"):
        normalized["market_context"] = "No separate market pressure is required for this company-derived need hypothesis."
    if normalized["context_type"] == "market_watch" and not normalized.get("company_context"):
        normalized["company_context"] = "Account-specific exposure is not established; keep as market watch until discovery validates relevance."
    combined_text = str(normalized.get("combined_activation_hypothesis") or "")
    if (
        not combined_text
        or re.search(r"\bmay make\s+[A-Z]", combined_text)
        or re.search(r"^If .{80,}, then .{80,}", combined_text)
    ):
        normalized["combined_activation_hypothesis"] = _clip(
            fallback.get("combined_activation_hypothesis")
            or f"{normalized.get('company_context')} + {normalized.get('market_context')} may create a need to {normalized.get('what_to_propose')}",
            700,
        )
    if not normalized.get("falsifier"):
        normalized["falsifier"] = fallback["falsifier"]
    if not normalized.get("what_progress_shows"):
        normalized["what_progress_shows"] = _clip(
            fallback.get("what_progress_shows")
            or "Progress should be visible as a clearer owner, validated pressure, and agreed evidence of whether the response direction is worth pursuing.",
            700,
        )
    return normalized


def _response_directions_from_play(play: dict, need: dict) -> dict:
    service = _clip(play.get("service_or_offer") or play.get("service") or "Relevant Vivicta capability area", 180)
    potential_need = _clip(
        need.get("combined_activation_hypothesis")
        or need.get("what_to_propose")
        or play.get("buying_hypothesis")
        or play.get("why_relevant"),
        700,
    )
    directions: list[str] = []
    other_capabilities = [
        "Customer-owned operating ownership and change capacity",
        "Specialist domain expertise where the need touches industrial engineering, PLM, MES/MOM, OT, regulation, or sector-specific quality assurance",
    ]
    return {
        "potential_need": potential_need,
        "evolving_modernization_directions": directions,
        "vivicta_potential_contribution": _clip(
            f"{service} may be relevant if the account signal and need hypothesis are confirmed in discovery.",
            360,
        ),
        "other_capabilities_may_be_required": other_capabilities,
    }


def _normalize_discovery_play_title(value: Any, fallback: Any = "") -> str:
    title = _clip(value or fallback or "Activation theme", 120)
    title = re.sub(r"\s+discovery\s+play\.?$", "", title, flags=re.I).strip()
    title = title.rstrip(" .,:;-–—")
    return f"{title} discovery play" if title else "Discovery play"


def _canonical_service_id(service: dict) -> str:
    explicit = str(service.get("service_id") or service.get("id") or service.get("code") or "").strip()
    if explicit:
        return explicit
    name = str(service.get("name") or "").strip()
    slug = re.sub(r"[^A-Za-z0-9]+", "-", name).strip("-").upper()
    return f"SVC-{slug[:48]}" if slug else ""


def _service_fit_hints(text: str, services: list[dict], limit: int = 4) -> list[dict]:
    text_l = str(text or "").lower()
    matches = []
    for svc in services:
        if not isinstance(svc, dict):
            continue
        haystack = " ".join(
            [
                str(svc.get("name") or ""),
                str(svc.get("category") or ""),
                " ".join(str(x) for x in _list(svc.get("buyer_personas"))),
                " ".join(str(x) for x in _list(svc.get("pain_triggers"))),
                " ".join(str(x) for x in _list(svc.get("evidence_patterns"))),
            ]
        ).lower()
        overlap = sorted({word for word in re.findall(r"[a-zA-ZåäöÅÄÖ0-9]{4,}", text_l) if word in haystack})
        if overlap:
            matches.append({
                "service_id": _canonical_service_id(svc),
                "service": svc.get("name") or "Service",
                "category": svc.get("category"),
                "match_terms": overlap[:8],
                "match_status": "candidate_only",
                "proof_points": _list(svc.get("proof_points"))[:3],
                "safe_wording": _list(svc.get("safe_wording"))[:3] or svc.get("safe_wording"),
                "do_not_claim": _list(svc.get("do_not_claim"))[:3],
                "disqualifiers": _list(svc.get("disqualifiers"))[:3],
            })
    matches.sort(key=lambda item: (-len(_list(item.get("match_terms"))), str(item.get("service_id") or item.get("service") or "")))
    return matches[:limit]


def _signal_from_text(
    signal_id: str,
    *,
    source: str,
    text: str,
    evidence_basis: list[str],
    services: list[dict],
    confidence: int = 45,
    gate: str = "allowed_as_hypothesis",
    quote: str = "",
    origin_kind: str = "generated_context",
    signal_lane: str = "strategic_context",
    parent_evidence_ids: list[str] | None = None,
    parent_foresight_ids: list[str] | None = None,
    independent_source_count: int = 0,
    source_freshness: str = "",
) -> dict:
    pressure, movement_pressure, consequence = _infer_pressure(text)
    roles = _infer_buyer_roles(text)
    service_hints = _service_fit_hints(text, services)
    return {
        "id": signal_id,
        "source": source,
        "origin_kind": origin_kind,
        "signal_lane": signal_lane,
        "evidence_basis": [str(item) for item in evidence_basis[:6]],
        "parent_evidence_ids": [str(item) for item in (parent_evidence_ids or [])[:8]],
        "parent_foresight_ids": [str(item) for item in (parent_foresight_ids or [])[:8]],
        "independent_source_count": max(0, _safe_int(independent_source_count, 0)),
        "source_freshness": _clip(source_freshness, 80),
        "account_movement": _clip(text, 420),
        "commercial_pressure": movement_pressure,
        "pressure_type": pressure,
        "business_consequence": consequence,
        "buyer_roles": roles,
        "buyer_role_concern": _buyer_role_concern(roles, movement_pressure),
        "timing_relevance": _infer_timing(text),
        "service_fit_hints": service_hints,
        "confidence": max(0, min(100, _safe_int(confidence, 45))),
        "gate": _normalize_gate(gate, "allowed_as_hypothesis"),
        "quote": _clip(quote or text, 420),
        "disqualifiers": [
            "No confirmed buyer priority or active project",
            "No clear owner for the pressure",
            "Evidence only supports discovery-question wording",
        ],
    }


def _build_account_signal_map(
    payload_seed: dict,
    services: list[dict],
    *,
    limit: int = SIGNAL_MAP_LIMIT,
) -> list[dict]:
    candidates: dict[str, list[dict]] = {lane: [] for lane in SIGNAL_LANE_QUOTAS}
    valid_evidence_ids = {
        str(item.get("id"))
        for item in _list(payload_seed.get("verified_evidence")) + _list(payload_seed.get("financial_sources"))
        if isinstance(item, dict) and item.get("id")
    }
    valid_foresight_ids = {
        str(item.get("id") or item.get("force_id"))
        for item in _list(payload_seed.get("foresight_forces"))
        if isinstance(item, dict) and (item.get("id") or item.get("force_id"))
    }

    def add_candidate(
        lane: str,
        source: str,
        text: str,
        basis: list[str],
        confidence: int = 45,
        gate: str = "allowed_as_hypothesis",
        quote: str = "",
        extra: dict | None = None,
    ):
        text = _clip(text, 900)
        if not text:
            return None
        metadata = extra.copy() if isinstance(extra, dict) else {}
        parent_evidence_ids = [
            str(item) for item in _list(metadata.pop("parent_evidence_ids", []))
            if str(item) in valid_evidence_ids
        ]
        parent_foresight_ids = [
            str(item) for item in _list(metadata.pop("parent_foresight_ids", []))
            if str(item) in valid_foresight_ids
        ]
        signal = _signal_from_text(
            "",
            source=source,
            text=text,
            evidence_basis=basis,
            services=services,
            confidence=confidence,
            gate=gate,
            quote=quote,
            origin_kind=str(metadata.pop("origin_kind", source)),
            signal_lane=lane,
            parent_evidence_ids=parent_evidence_ids,
            parent_foresight_ids=parent_foresight_ids,
            independent_source_count=_safe_int(metadata.pop("independent_source_count", 0), 0),
            source_freshness=str(metadata.pop("source_freshness", "") or ""),
        )
        signal.update(metadata)
        candidates.setdefault(lane, []).append(signal)
        return signal

    gate_rank = {
        "allowed_as_fact": 0,
        "allowed_as_hypothesis": 1,
        "allowed_as_discovery_question": 2,
        "internal_only": 3,
        "blocked": 4,
    }

    def candidate_rank(signal: dict) -> tuple:
        freshness = str(signal.get("source_freshness") or "").lower()
        freshness_rank = 0 if freshness in {"fresh", "current", "recent"} else 1 if freshness else 2
        return (
            gate_rank.get(_normalize_gate(signal.get("gate")), 3),
            0 if signal.get("independent_source_count") else 1,
            freshness_rank,
            -_safe_int(signal.get("confidence"), 0),
            str((signal.get("evidence_basis") or [""])[0]),
            str(signal.get("account_movement") or "").lower(),
        )

    def dedupe(items: list[dict]) -> list[dict]:
        result = []
        seen = set()
        for item in sorted(items, key=candidate_rank):
            basis = "|".join(sorted(str(value) for value in _list(item.get("evidence_basis")) if value))
            normalized = re.sub(r"[^a-z0-9]+", " ", str(item.get("account_movement") or "").lower()).strip()
            key = basis or normalized[:220]
            if not key or key in seen:
                continue
            seen.add(key)
            result.append(item)
        return result

    scanner_outputs = _dict(payload_seed.get("scanner_outputs"))
    activation_pairs = [
        item for item in _list(scanner_outputs.get("activation_pairs"))
        if isinstance(item, dict) and (item.get("why") or item.get("what") or item.get("theme"))
    ]
    if not activation_pairs:
        whys = _list(scanner_outputs.get("why"))
        whats = _list(scanner_outputs.get("what"))
        for index in range(min(3, max(len(whys), len(whats)))):
            activation_pairs.append({
                "theme_type": "proactive_opportunity",
                "sales_use": "proactive_opportunity",
                "theme": f"Activation pair {index + 1}",
                "why": whys[index] if index < len(whys) else "",
                "what": whats[index] if index < len(whats) else "",
                "what_to_do": whats[index] if index < len(whats) else "",
                "what_progress_shows": "",
            })

    for idx, pair in enumerate(activation_pairs[:3], start=1):
        play_type = _normalize_play_type(pair.get("sales_use") or pair.get("theme_type"))
        what_to_do = _clip(pair.get("what_to_do") or pair.get("what"), 700)
        what_progress_shows = _clip(pair.get("what_progress_shows"), 700)
        text = _activation_pair_text(pair)
        inferred_pair_pressure = _infer_pressure(text)[1] if play_type != "current_status" else ""
        signal = add_candidate(
            "activation_hypothesis",
            "scanner_activation_pair",
            text,
            [f"ACT-{idx:02d}"],
            58 if play_type != "proactive_opportunity" else 52,
            "allowed_as_hypothesis",
            quote=pair.get("why") or pair.get("theme") or text,
            extra={
                "origin_kind": "scanner_activation_pair",
                "parent_evidence_ids": _list(pair.get("parent_evidence_ids")),
                "parent_foresight_ids": _list(pair.get("parent_foresight_ids")),
                "independent_source_count": pair.get("independent_source_count"),
                "source_freshness": pair.get("source_freshness"),
                "activation_pair_index": idx,
                "pair_index": idx,
                "theme_type": play_type,
                "sales_play_type": play_type,
                "play_type": play_type,
                "play_type_label": PLAY_TYPE_LABELS.get(play_type, "Sales play"),
                "theme": _clip(pair.get("theme"), 180),
                "why": _clip(pair.get("why"), 700),
                "what": what_to_do,
                "what_to_do": what_to_do,
                "what_progress_shows": what_progress_shows,
                "account_movement": _clip(pair.get("why") or pair.get("theme") or text, 420),
                "commercial_pressure": _clip(pair.get("why") or text, 500),
                "business_consequence": _clip(what_progress_shows or _infer_pressure(text)[2], 500),
                "timing_relevance": (
                    "Current-status validation should be tested in near-term discovery."
                    if play_type == "current_status"
                    else "External pressure makes this worth validating as a forward-looking account theme."
                    if play_type == "foresight_driven"
                    else "Use as a proactive opportunity hypothesis until customer timing is confirmed."
                ),
                "pressure_horizon": _clip(pair.get("pressure_horizon") or pair.get("horizon"), 120),
                "sales_validation_window": _clip(pair.get("sales_validation_window"), 120),
                "timing_basis": _clip(pair.get("timing_basis"), 320),
                "external_pressure": _clip(
                    pair.get("external_pressure")
                    or pair.get("future_pressure")
                    or pair.get("foresight_pressure")
                    or inferred_pair_pressure,
                    520,
                ),
                "financial_scope": _clip(pair.get("financial_scope"), 320),
                "wording_boundary": _clip(pair.get("wording_boundary"), 320),
                "prohibited_claims": _dedupe_text_items(
                    _list(pair.get("prohibited_claims")) + _list(pair.get("do_not_say")),
                    limit=6,
                    clip_limit=320,
                ),
            },
        )
        if signal:
            signal["need_hypothesis"] = _need_hypothesis_from_signal(signal)

    for item in _list(payload_seed.get("verified_evidence")):
        if not isinstance(item, dict):
            continue
        text = _first_nonempty(item.get("title"), item.get("quote"), item.get("url"), limit=700)
        gate = _source_item_gate(item)
        confidence = 65 if gate == "allowed_as_fact" else 58 if gate == "allowed_as_hypothesis" else 42
        if not item.get("generation_eligible") and gate not in {"allowed_as_fact", "allowed_as_hypothesis"}:
            confidence = min(confidence, 35)
        item_id = str(item.get("id") or "")
        add_candidate(
            "account_fact",
            "verified_evidence",
            text,
            [item_id],
            confidence,
            gate,
            item.get("quote"),
            {
                "origin_kind": "account_evidence",
                "parent_evidence_ids": [item_id],
                "independent_source_count": 1 if _is_independent_source_item(item) else 0,
                "source_freshness": item.get("source_freshness") or item.get("fetched_at") or item.get("last_checked"),
                "generation_eligible": item.get("generation_eligible") is True,
            },
        )

    for item in _list(payload_seed.get("financial_sources"))[:5]:
        if not isinstance(item, dict):
            continue
        text = _first_nonempty(
            f"{item.get('metric') or 'Financial signal'} {item.get('value') or ''} {item.get('year') or ''}",
            item.get("quote"),
            item.get("title"),
            limit=700,
        )
        gate = _source_item_gate(item, "allowed_as_hypothesis" if item.get("generation_eligible") else "allowed_as_discovery_question")
        item_id = str(item.get("id") or "")
        add_candidate(
            "financial_capacity",
            "financial_evidence",
            text,
            [item_id],
            65 if gate in {"allowed_as_fact", "allowed_as_hypothesis"} else 45,
            gate,
            item.get("quote"),
            {
                "origin_kind": "financial_evidence",
                "parent_evidence_ids": [item_id],
                "independent_source_count": 1 if _is_independent_source_item(item) else 0,
                "source_freshness": item.get("source_freshness") or item.get("year"),
                "generation_eligible": item.get("generation_eligible") is True,
                "entity_scope": item.get("entity_scope") or item.get("scope") or item.get("financial_scope"),
            },
        )

    for key, source in (("ai_diagnosis", "scanner_ai_diagnosis"), ("domain_force_dossier", "scanner_domain_force")):
        add_candidate("strategic_context", source, scanner_outputs.get(key), ["SCANNER"], 45, "allowed_as_hypothesis", extra={"origin_kind": source})

    for item in _list(scanner_outputs.get("why"))[:3]:
        if isinstance(item, dict):
            text = _first_nonempty(item.get("title"), item.get("text"), item.get("rationale"), limit=700)
        else:
            text = item
        add_candidate("strategic_context", "scanner_why", text, ["SCANNER-WHY"], 45, "allowed_as_discovery_question", extra={"origin_kind": "scanner_why"})

    for force in _list(payload_seed.get("foresight_forces"))[:5]:
        if not isinstance(force, dict):
            continue
        text = _first_nonempty(force.get("description"), force.get("theme"), force.get("dimension"), limit=700)
        force_id = str(force.get("id") or force.get("force_id") or "")
        add_candidate(
            "external_pressure",
            "foresight_force",
            text,
            [force_id or "FORESIGHT"],
            _safe_int(force.get("confidence"), 45),
            "allowed_as_hypothesis",
            extra={
                "origin_kind": "foresight_force",
                "parent_foresight_ids": [force_id] if force_id else [],
                "source_freshness": force.get("source_freshness") or force.get("updated_at"),
                "pressure_horizon": force.get("horizon") or force.get("time_horizon") or force.get("timeframe"),
                "geography": force.get("geography"),
                "domain_relevance": force.get("domain_relevance"),
            },
        )

    evidence_trust = _dict(scanner_outputs.get("evidence_trust"))
    gap_items = _list(scanner_outputs.get("evidence_absence_signals")) + _list(evidence_trust.get("false_or_unsupported_claims"))
    for item in gap_items[:8]:
        text = _first_nonempty(
            item.get("claim") if isinstance(item, dict) else item,
            item.get("reason") if isinstance(item, dict) else "",
            limit=700,
        )
        add_candidate(
            "contradiction_or_gap",
            "scanner_evidence_gap",
            text,
            ["SCANNER-GAP"],
            38,
            "allowed_as_discovery_question",
            extra={"origin_kind": "evidence_gap", "selection_reason": "Preserves a falsifier or missing-evidence boundary."},
        )

    for lane in list(candidates):
        candidates[lane] = dedupe(candidates[lane])

    selected: list[dict] = []
    selected_keys: set[str] = set()

    def select_from(lane: str, count: int, reason: str) -> None:
        for candidate in candidates.get(lane, []):
            if len(selected) >= limit or count <= 0:
                break
            key = "|".join(str(item) for item in _list(candidate.get("evidence_basis"))) or str(candidate.get("account_movement"))
            if key in selected_keys:
                continue
            candidate["selection_reason"] = candidate.get("selection_reason") or reason
            selected.append(candidate)
            selected_keys.add(key)
            count -= 1

    for lane, quota in SIGNAL_LANE_QUOTAS.items():
        select_from(lane, min(quota, max(0, limit - len(selected))), f"Reserved {lane.replace('_', ' ')} lane.")

    for lane in ("account_fact", "strategic_context", "financial_capacity", "external_pressure", "contradiction_or_gap"):
        select_from(lane, max(0, limit - len(selected)), "Filled unused capacity with the next eligible governed signal.")

    if not selected:
        company = _dict(payload_seed.get("company"))
        fallback = _signal_from_text(
            "",
            source="minimal_company_context",
            text=f"{company.get('name') or 'The account'} has limited stored evidence. Treat any buying-potential logic as discovery-only until stronger evidence is available.",
            evidence_basis=["LOW-EVIDENCE"],
            services=services,
            confidence=25,
            gate="allowed_as_discovery_question",
            origin_kind="minimal_company_context",
            signal_lane="contradiction_or_gap",
        )
        fallback["selection_reason"] = "No eligible lane candidates were available."
        selected.append(fallback)

    for index, signal in enumerate(selected[:limit], start=1):
        signal["id"] = f"SIG-{index:02d}"
        signal["need_hypothesis"] = signal.get("need_hypothesis") or _need_hypothesis_from_signal(signal)
    return selected[:limit]


def _build_input_payload(
    company_data: dict,
    service_kb: dict,
    company_evidence: dict | None = None,
    company_source_ledger: dict | None = None,
) -> dict:
    evidence, blocked = _extract_evidence(company_data, company_evidence, company_source_ledger)
    financial_sources = _financial_evidence(company_data)
    ai_verification = _dict(company_data.get("ai_verification"))
    false_claims = _list(ai_verification.get("false_claims")) + _list(ai_verification.get("unsupported_claims"))
    key_people = []
    for person in _list(company_data.get("key_people"))[:10]:
        if isinstance(person, dict):
            insights = _dict(person.get("linkedin_insights"))
            if insights.get("still_at_company") is False:
                continue
            insight_bits = [
                person.get("relevance"),
                person.get("summary"),
                person.get("insight"),
                insights.get("headline"),
                insights.get("summary"),
            ]
            topics = _list(insights.get("topics"))
            key_people.append({
                "name": person.get("name"),
                "role": person.get("role") or person.get("title"),
                "insight": _clip(" | ".join(str(x) for x in insight_bits if x), 520),
                "confidence": person.get("confidence") or person.get("confidence_score"),
                "source": person.get("source") or person.get("source_url"),
                "evidence_basis": insights.get("verified_role") or person.get("source") or person.get("source_url") or "Stored Scanner stakeholder compatibility data",
                "source_database": "scanner_key_people",
                "allowed_use": "internal_only" if not person.get("source") and not person.get("source_url") else "allowed_as_discovery_question",
                "matched_demand_groups": topics[:6],
                "stakeholder_role_match_reason": person.get("relevance") or insights.get("verified_role"),
                "safety_notes": ["Moved from normal Company Summary to Buying Insight; validate current role before customer-facing use."],
                "do_not_say": ["Do not imply buying intent from role/title alone."],
            })
    seen_people = {str(person.get("name") or "").lower() for person in key_people if person.get("name")}
    for person in _company_evidence_stakeholders(company_evidence):
        key = str(person.get("name") or "").lower()
        if key and key not in seen_people:
            key_people.append(person)
            seen_people.add(key)
        if len(key_people) >= 16:
            break

    strategic_foresight = _dict(company_data.get("strategic_foresight"))
    forces = []
    for force_index, force in enumerate(_list(strategic_foresight.get("dimension_forces"))[:10], start=1):
        if isinstance(force, dict):
            forces.append({
                "id": force.get("id") or force.get("force_id") or f"FORESIGHT-{force_index:02d}",
                "force_id": force.get("force_id") or force.get("id") or f"FORESIGHT-{force_index:02d}",
                "theme": force.get("theme") or force.get("force") or force.get("name"),
                "dimension": force.get("dimension"),
                "description": _clip(force.get("description") or force.get("rationale") or force.get("signal"), 500),
                "confidence": force.get("confidence"),
                "credibility": force.get("source_credibility") or force.get("credibility"),
                "horizon": force.get("horizon") or force.get("time_horizon") or force.get("timeframe"),
                "geography": force.get("geography") or force.get("region"),
                "domain_relevance": force.get("domain_relevance") or force.get("relevance"),
                "source_freshness": force.get("source_freshness") or force.get("updated_at"),
                "source_ids": _list(force.get("source_ids") or force.get("evidence_ids"))[:8],
            })

    services = []
    for svc in _list(service_kb.get("services"))[:24]:
        service = {
            "name": svc.get("name"),
            "category": svc.get("category"),
            "buyer_personas": _list(svc.get("buyer_personas"))[:8],
            "pain_triggers": _list(svc.get("pain_triggers"))[:10],
            "evidence_patterns": _list(svc.get("evidence_patterns"))[:10],
            "proof_points": _list(svc.get("proof_points"))[:8],
            "safe_wording": _list(svc.get("safe_wording"))[:8] or svc.get("safe_wording"),
            "do_not_claim": _list(svc.get("do_not_claim"))[:8],
            "disqualifiers": _list(svc.get("disqualifiers"))[:8],
        }
        service["service_id"] = _canonical_service_id({**svc, **service})
        services.append(service)
    modernization_tactics = [
        {
            "category": tactic.get("category"),
            "concept": tactic.get("concept"),
            "problem_pattern": tactic.get("problem_pattern"),
            "solution_mechanism": tactic.get("solution_mechanism"),
            "case_study": tactic.get("case_study"),
            "resource_url": tactic.get("resource_url"),
        }
        for tactic in _load_modernization_tactics()[:30]
    ]
    service_owner = _clip(service_kb.get("owner") or "Vivicta", 120)
    company_name = _clip(company_data.get("companyName") or company_data.get("company_name"), 120)
    self_scan = bool(
        service_owner
        and company_name
        and service_owner.lower() in company_name.lower()
    )
    self_scan_context = {
        "is_self_scan": self_scan,
        "service_kb_owner": service_owner,
        "framing": (
            "Use this as internal sales/proposal strategy validation. Do not frame the account as buying its own services."
            if self_scan else ""
        ),
    }

    payload = {
        "company": {
            "name": company_name,
            "url": company_data.get("companyUrl") or company_data.get("url"),
            "domain": company_data.get("domain"),
            "archetype": company_data.get("archetype"),
            "intent_score": company_data.get("intent_score"),
            "reality_score": company_data.get("reality_score"),
            "confidence_score": company_data.get("confidence_score"),
            "financial_index": company_data.get("financial_index"),
            "favi_score": company_data.get("favi_score"),
            "adaptation_segment": company_data.get("adaptation_segment"),
        },
        "scanner_outputs": {
            "general_summary": _clip(company_data.get("general_summary"), 1800),
            "general_summary_fact_check": {
                "verified": company_data.get("general_summary_verified") is True,
                "status": "verified" if company_data.get("general_summary_verified") is True else "unverified_or_skipped",
                "false_or_unsupported_claims": _list(company_data.get("general_summary_false_claims"))[:12],
            },
            "ai_diagnosis": _clip(company_data.get("summary_text"), 2200),
            "strategic_synthesis": _clip(company_data.get("strategic_synthesis"), 5200),
            "evidence_based_risks": _list(company_data.get("evidence_based_risks"))[:6],
            "activation_pairs": _list(company_data.get("activation_pairs"))[:4],
            "why": _list(company_data.get("why_proposals"))[:4],
            "what": _list(company_data.get("what_proposals"))[:4],
            "what_progress": _list(company_data.get("what_progress_proposals"))[:4],
            "pitch": _clip(company_data.get("pitch_text"), 900),
            "domain_force_dossier": _clip(company_data.get("domain_force_dossier"), 1800),
            "evidence_absence_signals": _list(company_data.get("evidence_absence_signals"))[:12],
            "evidence_trust": {
                "confidence_score": company_data.get("confidence_score"),
                "hallucination_flags": _list(company_data.get("hallucination_flags"))[:12],
                "false_or_unsupported_claims": false_claims[:12],
                "evidence_cleaned_at": company_data.get("evidence_cleaned_at"),
                "evidence_cleaning_stats": company_data.get("evidence_cleaning_stats"),
            },
        },
        "verified_evidence": evidence,
        "financial_sources": financial_sources,
        "blocked_or_display_only_sources": blocked,
        "company_evidence_database": {
            "available": isinstance(company_evidence, dict),
            "generated_at": company_evidence.get("generated_at") if isinstance(company_evidence, dict) else None,
            "stats": company_evidence.get("stats", {}) if isinstance(company_evidence, dict) else {},
            "warnings": company_evidence.get("warnings", []) if isinstance(company_evidence, dict) else [],
            "provider_status": company_evidence.get("provider_status", {}) if isinstance(company_evidence, dict) else {},
        },
        "company_source_ledger": {
            "available": isinstance(company_source_ledger, dict),
            "generated_at": company_source_ledger.get("generated_at") if isinstance(company_source_ledger, dict) else None,
            "stats": company_source_ledger.get("stats", {}) if isinstance(company_source_ledger, dict) else {},
            "warnings": company_source_ledger.get("warnings", []) if isinstance(company_source_ledger, dict) else [],
        },
        "source_ledger_recovery_records": _company_source_ledger_recovery_records(company_source_ledger),
        "stakeholders": key_people,
        "stakeholder_name_list": _stakeholder_name_list(company_data, company_evidence, company_source_ledger),
        "foresight_forces": forces,
        "services": services,
        "modernization_tactics": modernization_tactics,
        "self_scan_context": self_scan_context,
        "evidence_sufficiency": _derive_evidence_sufficiency(
            evidence,
            financial_sources,
            _dict(company_data.get("evidence_sufficiency")),
        ),
    }
    payload["account_signal_map"] = _build_account_signal_map(payload, services)
    payload["required_sales_spine"] = _required_sales_spine(payload)
    payload["hypothesis_registry"] = _build_hypothesis_registry(payload)
    _synchronize_hypothesis_contract(payload)
    payload["account_signal_lanes"] = {
        lane: [_compact_signal(signal) for signal in payload["account_signal_map"] if signal.get("signal_lane") == lane]
        for lane in SIGNAL_LANE_QUOTAS
    }
    payload["buyer_role_lens"] = BUYER_ROLE_LENSES
    return payload


def _required_sales_spine(payload: dict) -> list[dict]:
    required = []
    for signal in _list(payload.get("account_signal_map")):
        if not isinstance(signal, dict) or signal.get("source") != "scanner_activation_pair":
            continue
        signal_id = str(signal.get("id") or "").strip()
        if not signal_id:
            continue
        hypothesis_id = f"HYP-{len(required) + 1:02d}"
        signal["hypothesis_id"] = hypothesis_id
        required.append({
            "id": signal_id,
            "hypothesis_id": hypothesis_id,
            "activation_pair_index": signal.get("activation_pair_index"),
            "play_type": _normalize_play_type(signal.get("sales_play_type") or signal.get("theme_type")),
            "theme": _clip(signal.get("theme") or signal.get("account_movement"), 180),
            "gate": _normalize_gate(signal.get("gate"), "allowed_as_hypothesis"),
            "why": _clip(signal.get("why") or signal.get("commercial_pressure"), 520),
            "what_to_do": _clip(signal.get("what_to_do") or signal.get("what"), 520),
            "what_progress_shows": _clip(signal.get("what_progress_shows") or signal.get("business_consequence"), 420),
            "buyer_roles": _list(signal.get("buyer_roles"))[:5],
            "need_hypothesis": signal.get("need_hypothesis") if isinstance(signal.get("need_hypothesis"), dict) else _need_hypothesis_from_signal(signal),
            "service_fit_hints": _list(signal.get("service_fit_hints"))[:4],
        })
        if len(required) >= 3:
            break
    return required


def _weakest_gate(gates: list[str]) -> str:
    rank = {
        "allowed_as_fact": 0,
        "allowed_as_hypothesis": 1,
        "allowed_as_discovery_question": 2,
        "internal_only": 3,
        "blocked": 4,
    }
    normalized = [_normalize_gate(gate, "allowed_as_discovery_question") for gate in gates if gate]
    return max(normalized, key=lambda gate: rank.get(gate, 3)) if normalized else "allowed_as_discovery_question"


def _signal_overlap(left: dict, right: dict) -> int:
    def words(item: dict) -> set[str]:
        text = " ".join(
            str(item.get(key) or "")
            for key in ("theme", "why", "account_movement", "commercial_pressure", "business_consequence")
        ).lower()
        return {word for word in re.findall(r"[a-z0-9åäö]{4,}", text) if word not in {"this", "that", "with", "from", "should", "could", "would"}}
    return len(words(left) & words(right))


def _build_hypothesis_registry(payload: dict) -> list[dict]:
    signals = [item for item in _list(payload.get("account_signal_map")) if isinstance(item, dict)]
    activations = [item for item in signals if item.get("signal_lane") == "activation_hypothesis"][:3]
    registry = []
    for index, activation in enumerate(activations, start=1):
        hypothesis_id = str(activation.get("hypothesis_id") or f"HYP-{index:02d}")
        activation["hypothesis_id"] = hypothesis_id
        supporting = []
        for lane in ("account_fact", "financial_capacity", "external_pressure", "contradiction_or_gap"):
            lane_items = [item for item in signals if item.get("signal_lane") == lane]
            if lane_items:
                activation_parents = set(_list(activation.get("parent_foresight_ids"))) | set(_list(activation.get("parent_evidence_ids")))

                def support_rank(item: dict) -> tuple:
                    item_parents = set(_list(item.get("parent_foresight_ids"))) | set(_list(item.get("parent_evidence_ids")))
                    return (
                        -len(activation_parents & item_parents),
                        -_signal_overlap(activation, item),
                        str(item.get("id") or ""),
                    )

                ranked = sorted(lane_items, key=support_rank)
                best = ranked[0]
                best_parents = set(_list(best.get("parent_foresight_ids"))) | set(_list(best.get("parent_evidence_ids")))
                if _signal_overlap(activation, best) > 0 or bool(activation_parents & best_parents):
                    supporting.append(best)
        for signal in supporting:
            links = _list(signal.get("linked_hypothesis_ids"))
            if hypothesis_id not in links:
                links.append(hypothesis_id)
            signal["linked_hypothesis_ids"] = links[:3]
        need = _normalize_need_hypothesis(activation.get("need_hypothesis"), activation, activation.get("play_type"))
        external = next((item for item in supporting if item.get("signal_lane") == "external_pressure"), {})
        financial = next((item for item in supporting if item.get("signal_lane") == "financial_capacity"), {})
        contradiction = next((item for item in supporting if item.get("signal_lane") == "contradiction_or_gap"), {})
        gates = [activation.get("gate")] + [item.get("gate") for item in supporting]
        parent_evidence = list(dict.fromkeys(
            str(parent)
            for item in [activation] + supporting
            for parent in _list(item.get("parent_evidence_ids"))
            if parent
        ))
        parent_foresight = list(dict.fromkeys(
            str(parent)
            for item in [activation] + supporting
            for parent in _list(item.get("parent_foresight_ids"))
            if parent
        ))
        maximum_gate = _weakest_gate(gates)
        authoritative_pressure = _first_nonempty(
            activation.get("external_pressure"),
            need.get("future_pressure"),
            external.get("account_movement"),
            limit=520,
        )
        financial_scope = _clip(activation.get("financial_scope"), 320)
        if not financial_scope and financial:
            entity_scope = _clip(financial.get("entity_scope"), 120)
            if financial.get("generation_eligible") is not True or _normalize_gate(financial.get("gate")) not in {"allowed_as_fact", "allowed_as_hypothesis"}:
                financial_scope = _clip(
                    "Financial context is entity-scoped and question-only; it must not support group-level margin, budget, or capacity assertions."
                    + (f" Recorded scope: {entity_scope}." if entity_scope else ""),
                    320,
                )
        wording_boundary = _clip(activation.get("wording_boundary"), 320) or _confidence_boundary(
            maximum_gate,
            "Use only the current HYP/SIG lineage and its governed evidence parents",
        )
        registry.append({
            "hypothesis_id": hypothesis_id,
            "activation_signal_id": activation.get("id"),
            "activation_pair_index": activation.get("activation_pair_index"),
            "play_type": _normalize_play_type(activation.get("play_type")),
            "theme": _clip(activation.get("theme") or activation.get("account_movement"), 180),
            "current_condition": _clip(need.get("current_condition"), 520),
            "external_pressure": authoritative_pressure,
            "causal_bridge": _clip(need.get("combined_activation_hypothesis"), 700),
            "likely_business_consequence": _clip(need.get("likely_business_consequence"), 520),
            "pressure_horizon": _clip(activation.get("pressure_horizon") or external.get("pressure_horizon"), 120),
            "sales_validation_window": _clip(activation.get("sales_validation_window") or "Validate through discovery when an owner, consequence, and timing trigger are confirmed.", 220),
            "timing_basis": _clip(activation.get("timing_basis") or external.get("timing_relevance"), 360),
            "counter_hypothesis": _clip(contradiction.get("account_movement") or "The apparent pressure may already be controlled or may not be commercially material for this account.", 420),
            "falsifier": _clip(need.get("falsifier"), 520),
            "maximum_permitted_gate": maximum_gate,
            "financial_scope": financial_scope,
            "wording_boundary": wording_boundary,
            "prohibited_claims": _dedupe_text_items(activation.get("prohibited_claims"), limit=6, clip_limit=320),
            "parent_signal_ids": [item.get("id") for item in supporting if item.get("id")],
            "parent_evidence_ids": parent_evidence[:10],
            "parent_foresight_ids": parent_foresight[:10],
            "independent_source_count": sum(_safe_int(item.get("independent_source_count"), 0) for item in supporting),
            "service_candidates": _list(activation.get("service_fit_hints"))[:4],
        })
    return registry


def _synchronize_hypothesis_contract(payload: dict) -> None:
    """Make each HYP/SIG record authoritative across every downstream section payload."""
    registry_by_signal = {
        str(item.get("activation_signal_id")): item
        for item in _list(payload.get("hypothesis_registry"))
        if isinstance(item, dict) and item.get("activation_signal_id")
    }
    signals_by_id = {
        str(item.get("id")): item
        for item in _list(payload.get("account_signal_map"))
        if isinstance(item, dict) and item.get("id")
    }
    for signal_id, record in registry_by_signal.items():
        signal = signals_by_id.get(signal_id)
        if not signal:
            continue
        signal["hypothesis_id"] = record.get("hypothesis_id")
        signal["external_pressure"] = _clip(record.get("external_pressure"), 520)
        signal["pressure_horizon"] = _clip(record.get("pressure_horizon"), 120)
        signal["financial_scope"] = _clip(record.get("financial_scope"), 320)
        signal["wording_boundary"] = _clip(record.get("wording_boundary"), 320)
        signal["prohibited_claims"] = _list(record.get("prohibited_claims"))[:6]
        need = _normalize_need_hypothesis(signal.get("need_hypothesis"), signal, signal.get("play_type"))
        if record.get("external_pressure") and _normalize_play_type(signal.get("play_type")) != "current_status":
            need["future_pressure"] = _clip(record.get("external_pressure"), 520)
            need["market_context"] = _clip(record.get("external_pressure"), 520)
        if record.get("likely_business_consequence"):
            need["likely_business_consequence"] = _clip(record.get("likely_business_consequence"), 520)
        signal["need_hypothesis"] = need

    for item in _list(payload.get("required_sales_spine")):
        if not isinstance(item, dict):
            continue
        signal = signals_by_id.get(str(item.get("id") or ""))
        record = registry_by_signal.get(str(item.get("id") or ""))
        if not signal or not record:
            continue
        item["hypothesis_id"] = record.get("hypothesis_id")
        item["need_hypothesis"] = _dict(signal.get("need_hypothesis")).copy()
        item["external_pressure"] = record.get("external_pressure")
        item["pressure_horizon"] = record.get("pressure_horizon")
        item["financial_scope"] = record.get("financial_scope")
        item["wording_boundary"] = record.get("wording_boundary")
        item["prohibited_claims"] = _list(record.get("prohibited_claims"))[:6]


def _compact_signal(signal: dict) -> dict:
    return {
        "id": signal.get("id"),
        "hypothesis_id": signal.get("hypothesis_id"),
        "source": signal.get("source"),
        "origin_kind": signal.get("origin_kind"),
        "signal_lane": signal.get("signal_lane"),
        "selection_reason": signal.get("selection_reason"),
        "parent_evidence_ids": _list(signal.get("parent_evidence_ids"))[:8],
        "parent_foresight_ids": _list(signal.get("parent_foresight_ids"))[:8],
        "independent_source_count": signal.get("independent_source_count"),
        "source_freshness": signal.get("source_freshness"),
        "pressure_horizon": signal.get("pressure_horizon"),
        "external_pressure": _clip(signal.get("external_pressure"), 520),
        "financial_scope": _clip(signal.get("financial_scope"), 320),
        "wording_boundary": _clip(signal.get("wording_boundary"), 320),
        "prohibited_claims": _list(signal.get("prohibited_claims"))[:6],
        "sales_validation_window": signal.get("sales_validation_window"),
        "timing_basis": signal.get("timing_basis"),
        "activation_pair_index": signal.get("activation_pair_index"),
        "theme": _clip(signal.get("theme") or signal.get("account_movement"), 180),
        "gate": _normalize_gate(signal.get("gate"), "allowed_as_hypothesis"),
        "why": _clip(signal.get("why") or signal.get("commercial_pressure"), 420),
        "what_to_do": _clip(signal.get("what_to_do") or signal.get("what"), 420),
        "what_progress_shows": _clip(signal.get("what_progress_shows") or signal.get("business_consequence"), 360),
        "buyer_roles": _list(signal.get("buyer_roles"))[:4],
        "service_fit_hints": _list(signal.get("service_fit_hints"))[:3],
        "need_hypothesis": signal.get("need_hypothesis") if isinstance(signal.get("need_hypothesis"), dict) else {},
    }


def _section_payload(section: str, payload: dict) -> dict:
    scanner_outputs = _dict(payload.get("scanner_outputs"))
    opening_section = section == "snapshot_thesis"
    account_signals = [
        _compact_signal(signal)
        for signal in _list(payload.get("account_signal_map"))
        if isinstance(signal, dict)
    ][:SIGNAL_MAP_LIMIT]
    compact = {
        "company": payload.get("company"),
        "required_sales_spine": _list(payload.get("required_sales_spine"))[:3],
        "account_signal_map": account_signals,
        "account_signal_lanes": payload.get("account_signal_lanes"),
        "hypothesis_registry": _list(payload.get("hypothesis_registry"))[:3],
        "evidence_sufficiency": payload.get("evidence_sufficiency"),
        "scanner_outputs": {
            "general_summary": _clip(scanner_outputs.get("general_summary"), 1800 if opening_section else 900),
            "general_summary_fact_check": scanner_outputs.get("general_summary_fact_check"),
            # The opening qualification thesis needs the financial implication,
            # horizons, and invalidation boundary that often sit near the end.
            "strategic_synthesis": _clip(scanner_outputs.get("strategic_synthesis"), 5200 if opening_section else 1500),
            "evidence_based_risks": _list(scanner_outputs.get("evidence_based_risks"))[:5],
            "activation_pairs": _list(scanner_outputs.get("activation_pairs"))[:3],
            "why": _list(scanner_outputs.get("why"))[:3],
            "what": _list(scanner_outputs.get("what"))[:3],
            "what_progress": _list(scanner_outputs.get("what_progress"))[:3],
        },
        "self_scan_context": payload.get("self_scan_context"),
        "buyer_role_lens": payload.get("buyer_role_lens"),
    }
    if section in {"snapshot_thesis", "pressure_evidence", "why_now"}:
        compact["verified_evidence"] = _list(payload.get("verified_evidence"))[:8]
        compact["financial_sources"] = _list(payload.get("financial_sources"))[:4]
        compact["source_ledger_recovery_records"] = _list(payload.get("source_ledger_recovery_records"))[:6]
    if section in {"pain_to_solution", "sales_plays"}:
        compact["services"] = _list(payload.get("services"))[:14]
        compact["modernization_tactics"] = _list(payload.get("modernization_tactics"))[:18]
        compact["source_ledger_recovery_records"] = _list(payload.get("source_ledger_recovery_records"))[:8]
    if section == "stakeholders":
        compact["stakeholders"] = _list(payload.get("stakeholders"))[:12]
        compact["stakeholder_name_list"] = _list(payload.get("stakeholder_name_list"))[:12]
    return compact


def _sales_play_service_candidates(signal: dict, payload: dict, limit: int = 6) -> list[dict]:
    services_by_name: dict[str, dict] = {}
    for service in _list(payload.get("services")):
        if not isinstance(service, dict):
            continue
        name = str(service.get("name") or "").strip()
        if name:
            services_by_name[name.lower()] = service
        service_id = str(service.get("service_id") or "").strip()
        if service_id:
            services_by_name[service_id.lower()] = service

    candidates: list[dict] = []
    seen: set[str] = set()
    for hint in _list(signal.get("service_fit_hints")):
        if not isinstance(hint, dict):
            continue
        name = str(hint.get("service") or "").strip()
        service_id = str(hint.get("service_id") or "").strip()
        if not name or name.lower() in seen:
            continue
        source = services_by_name.get(service_id.lower()) or services_by_name.get(name.lower(), {})
        candidates.append({
            "service_id": source.get("service_id") or service_id,
            "name": name,
            "category": source.get("category") or hint.get("category"),
            "match_terms": _list(hint.get("match_terms"))[:8],
            "safe_wording": _list(source.get("safe_wording"))[:2] or _list(hint.get("safe_wording"))[:2],
            "proof_points": _list(source.get("proof_points"))[:2] or _list(hint.get("proof_points"))[:2],
            "disqualifiers": _list(source.get("disqualifiers"))[:2] or _list(hint.get("disqualifiers"))[:2],
        })
        seen.add(name.lower())
        if len(candidates) >= limit:
            break
    return candidates


def _sales_play_analysis_prompt(signal: dict, payload: dict) -> str:
    compact = {
        "company": _dict(payload.get("company")),
        "signal": _compact_signal(signal),
        "service_candidates": _sales_play_service_candidates(signal, payload),
        "account_thesis": _clip(_dict(payload.get("scanner_outputs")).get("strategic_synthesis"), 900),
    }
    return f"""
Create the analytical fields for exactly one Buying Insight sales play.

Rules:
- Return ONLY valid JSON using the exact schema below.
- Preserve account_signal_id exactly as {signal.get('id')}.
- Use only the supplied signal and candidate services. Do not browse or invent facts, services, projects, owners, budgets, or timing.
- Choose one exact candidate service ID and name only when the signal-to-service bridge is commercially coherent. Otherwise return empty service_id and service_or_offer.
- Keep service_fit_rationale concise and explain the causal bridge, not keyword overlap.
- Keep the customer wording hypothesis-led and suitable for discovery.
- Do not repeat the account thesis, full WHY/WHAT, scores, gates, evidence, or standard report metadata; Scanner assembles those deterministically.

Required JSON schema:
{BUYING_SALES_PLAY_ANALYSIS_SCHEMA}

Stored input:
{json.dumps(compact, ensure_ascii=False)}
""".strip()


def _is_likely_json_truncated(raw: str) -> bool:
    text = (raw or "").strip()
    if not text:
        return True
    if text.startswith("```"):
        text = text.split("\n", 1)[-1].rsplit("```", 1)[0].strip()
    return not re.search(r"[\}\]]\s*$", text)


def _pressure_signal_has_renderable_basis(item: dict, known_signal_ids: set[str]) -> bool:
    if not isinstance(item, dict):
        return False
    basis_values = []
    basis = item.get("evidence_basis")
    if isinstance(basis, list):
        basis_values.extend(str(value).strip() for value in basis if str(value).strip())
    elif basis:
        basis_values.append(str(basis).strip())
    text_ids = _text_signal_ids(json.dumps(item, ensure_ascii=False))
    if any(value in known_signal_ids for value in basis_values + text_ids):
        return True
    if any(value.startswith(EVIDENCE_BASIS_PREFIXES + ("FOR-", "ABS-", "SCANNER", "ACT-")) for value in basis_values):
        return True
    return False


def _generation_prompt(payload: dict) -> str:
    return f"""
You are creating an Admin-only Evidence-Gated Buying Potential & Insight report.

This is NOT confirmed buying intent. It is a falsifiable need-hypothesis engine based on already stored Scanner data.
Use only the JSON payload below. Do not browse the web. Do not invent services, clients, personal data, dates, financials, partnerships, ownership details, or buying signals.

Evidence contract:
- allowed_as_fact: only when supported by verified_evidence, financial_sources marked generation_eligible, or explicitly labeled Scanner internal metrics.
- allowed_as_hypothesis: plausible interpretation from Scanner diagnosis, pressure, foresight, or service-pattern fit.
- allowed_as_discovery_question: useful but weak or uncertain signal that should be asked, not asserted.
- internal_only: useful for sales prep but not safe for customer-facing wording.
- blocked: unsupported, wrong-entity, sensitive, or overclaiming material.
- Say "buying hypothesis", "need hypothesis", or "demand creation potential"; never "confirmed buying intent", unless explicit evidence exists in the payload.
- Stakeholder notes must remain professional, role-relevant, and source-bound. No private-life profiling.
- Numeric scores are internal compatibility diagnostics only. They must not be used as proof that a need exists, as customer-facing evidence, or as the main reason for a proposal.
- Separate company-derived needs from market-derived pressures, then explain only the combined activation hypotheses that have a clear causal bridge.

Return ONLY valid JSON with this schema:
{{
  "account_snapshot": {{"text": "...", "gate": "allowed_as_fact|allowed_as_hypothesis|allowed_as_discovery_question|internal_only|blocked", "evidence_basis": ["SRC-01"]}},
  "buying_potential_thesis": {{"text": "...", "gate": "allowed_as_hypothesis", "confidence": 0}},
  "pressure_signals": [{{"signal_type": "...", "description": "...", "gate": "...", "confidence": 0, "evidence_basis": []}}],
  "why_now": [{{"trigger": "...", "interpretation": "...", "gate": "...", "confidence": 0, "evidence_basis": []}}],
  "stakeholder_map": [{{"name": "...", "role": "...", "likely_buying_committee_role": "...", "relevance_rationale": "...", "linked_pressure_themes": [], "linked_service_themes": [], "evidence_basis": "...", "confidence": 0, "allowed_use": "internal_only|allowed_as_discovery_question|allowed_as_hypothesis", "do_not_say": [], "safety_notes": []}}],
  "pain_to_solution_map": [{{"evidence": "...", "interpreted_pressure": "...", "service": "...", "proof_point": "...", "safe_wording": "...", "gate": "...", "confidence": 0, "disqualifiers": []}}],
  "recommended_sales_plays": [{{"service_or_offer": "...", "need_hypothesis": {{"future_pressure": "...", "current_condition": "...", "structural_exposure": "...", "likely_business_consequence": "...", "why_now_or_soon": "...", "what_to_propose": "...", "what_progress_shows": "...", "discovery_question": "...", "disqualifier": "...", "company_context": "...", "market_context": "...", "combined_activation_hypothesis": "...", "falsifier": "...", "context_type": "company_only|market_watch|combined_activation"}}, "response_directions": {{"potential_need": "...", "evolving_modernization_directions": [], "vivicta_potential_contribution": "...", "other_capabilities_may_be_required": []}}, "readiness_band": "Validate now|Validate when trigger appears|Monitor|Insufficient account connection", "evidence_coverage": "...", "confidence_boundary": "...", "priority_band": "...", "why_relevant": "...", "target_stakeholder": "...", "opening_angle": "...", "executive_angle": "...", "technical_angle": "...", "discovery_questions": ["..."], "do_not_say": ["..."], "confidence": 0, "hypothesis_strength": 0, "score_label": "Internal compatibility score", "overclaim_risk": "low|medium|high", "evidence_basis": [], "disqualifiers": []}}],
  "sales_spine": {{"account_thesis": "", "strategic_synthesis_basis": "", "top_buying_hypotheses": [], "risk_themes": [], "self_scan_note": ""}},
  "evidence_confidence": {{"strongest_evidence": [], "verified_facts": [], "safe_hypotheses": [], "use_only_as_question": [], "weak_or_missing_evidence": [], "do_not_use_claims": [], "do_not_say": [], "sensitive_claims": [], "claims_as_questions_only": []}},
  "missing_input_warnings": [],
  "model_notes": []
}}

Payload:
{json.dumps(payload, ensure_ascii=False)}
""".strip()


BUYING_SECTION_SCHEMAS = {
    "snapshot_thesis": """
{
  "qualification_reasoning": {
    "account_thesis": "...",
    "current_position": "...",
    "verified_strengths": ["..."],
    "evidence_boundaries": ["..."],
    "structural_exposure": "...",
    "strategic_tension": "...",
    "external_pressure": "...",
    "likely_business_consequence": "...",
    "emerging_need": "...",
    "ability_to_act": "...",
    "validation_question": "...",
    "falsifier": "...",
    "causal_sequence": "...",
    "evidence_basis": ["SIG-01"]
  },
  "model_notes": []
}
""".strip(),
    "why_now": """
{
  "why_now": [{"trigger": "...", "why_it_matters_now": "...", "buyer_relevance": "...", "safe_discovery_question": "...", "gate": "allowed_as_hypothesis|allowed_as_discovery_question|internal_only|blocked", "confidence": 0, "evidence_basis": ["SIG-01"]}],
  "model_notes": []
}
""".strip(),
    "pain_to_solution": """
{
  "pain_to_solution_map": [{"account_signal_id": "SIG-01", "evidence": "...", "interpreted_pressure": "...", "buyer_lens": "...", "service": "...", "why_this_service": "...", "proof_point": "...", "safe_wording": "...", "missing_evidence": "...", "gate": "allowed_as_hypothesis|allowed_as_discovery_question|internal_only|blocked", "confidence": 0, "disqualifiers": []}],
  "model_notes": []
}
""".strip(),
    "sales_plays": """
{
  "recommended_sales_plays": [{"play_name": "...", "linked_signal_ids": ["SIG-01"], "activation_pair_index": 1, "play_type": "current_status|foresight_driven|proactive_opportunity", "service_or_offer": "...", "need_hypothesis": {"future_pressure": "...", "current_condition": "...", "structural_exposure": "...", "likely_business_consequence": "...", "why_now_or_soon": "...", "what_to_propose": "...", "what_progress_shows": "...", "discovery_question": "...", "disqualifier": "...", "company_context": "...", "market_context": "...", "combined_activation_hypothesis": "...", "falsifier": "...", "context_type": "company_only|market_watch|combined_activation"}, "response_directions": {"potential_need": "...", "evolving_modernization_directions": [], "vivicta_potential_contribution": "...", "other_capabilities_may_be_required": []}, "readiness_band": "Validate now|Validate when trigger appears|Monitor|Insufficient account connection", "evidence_coverage": "...", "confidence_boundary": "...", "priority_band": "...", "primary_buyer": "...", "secondary_buyers": [], "trigger": "...", "account_thesis": "...", "buying_hypothesis": "...", "trigger_to_offer_bridge": "...", "business_hypothesis": "...", "why_relevant": "...", "target_stakeholder": "...", "first_meeting_objective": "...", "opening_angle": "...", "executive_angle": "...", "technical_angle": "...", "safe_customer_wording": "...", "discovery_questions": ["..."], "discovery_questions_by_role": [{"role": "...", "questions": ["..."]}], "proof_point": "...", "do_not_say": ["..."], "qualification_upgrade_criteria": [], "confidence": 0, "play_score": 0, "hypothesis_strength": 0, "score_label": "Internal compatibility score", "play_classification": "strong_account_play|qualified_discovery_play|monitor_soft_discovery|weak_signal|do_not_use", "overclaim_risk": "low|medium|high", "evidence_basis": ["SIG-01"], "disqualifiers": []}],
  "model_notes": []
}
""".strip(),
    "pressure_evidence": """
{
  "pressure_signals": [{"signal_type": "...", "trigger": "...", "description": "...", "business_consequence": "...", "buyer_role_concern": "...", "gate": "allowed_as_hypothesis|allowed_as_discovery_question|internal_only|blocked", "confidence": 0, "evidence_basis": ["SIG-01"]}],
  "evidence_confidence": {"strongest_evidence": [], "verified_facts": [], "safe_hypotheses": [], "use_only_as_question": [], "weak_or_missing_evidence": [], "do_not_use_claims": [], "do_not_say": [], "sensitive_claims": [], "claims_as_questions_only": []},
  "missing_input_warnings": [],
  "model_notes": []
}
""".strip(),
    "stakeholders": """
{
  "stakeholder_map": [{"name": "...", "role": "...", "likely_buying_committee_role": "...", "relevance_rationale": "...", "linked_pressure_themes": [], "linked_service_themes": [], "evidence_basis": "...", "confidence": 0, "allowed_use": "internal_only|allowed_as_discovery_question|allowed_as_hypothesis", "do_not_say": [], "safety_notes": []}],
  "model_notes": []
}
""".strip(),
}


BUYING_SALES_PLAY_ANALYSIS_SCHEMA = """
{
  "account_signal_id": "SIG-01",
  "hypothesis_id": "HYP-01",
  "service_id": "Exact candidate service ID or empty string",
  "service_or_offer": "Exact candidate service name or empty string",
  "service_fit_rationale": "...",
  "primary_buyer": "...",
  "secondary_buyers": [],
  "first_meeting_objective": "...",
  "safe_customer_wording": "...",
  "discovery_questions_by_role": [{"role": "...", "questions": ["..."]}],
  "disqualifiers": [],
  "do_not_say": []
}
""".strip()


def _strict_object(properties: dict[str, dict]) -> dict:
    return {
        "type": "object",
        "properties": properties,
        "required": list(properties),
        "additionalProperties": False,
    }


_STRING = {"type": "string"}
_NUMBER = {"type": "number"}
_STRING_LIST = {"type": "array", "items": _STRING}
_GATE = {
    "type": "string",
    "enum": ["allowed_as_fact", "allowed_as_hypothesis", "allowed_as_discovery_question", "internal_only", "blocked"],
}

_EVIDENCE_CONFIDENCE_SCHEMA = _strict_object({
    "strongest_evidence": _STRING_LIST,
    "verified_facts": _STRING_LIST,
    "safe_hypotheses": _STRING_LIST,
    "use_only_as_question": _STRING_LIST,
    "weak_or_missing_evidence": _STRING_LIST,
    "do_not_use_claims": _STRING_LIST,
    "do_not_say": _STRING_LIST,
    "sensitive_claims": _STRING_LIST,
    "claims_as_questions_only": _STRING_LIST,
})

_QUALIFICATION_SCHEMA = _strict_object({
    "account_thesis": _STRING,
    "current_position": _STRING,
    "verified_strengths": _STRING_LIST,
    "evidence_boundaries": _STRING_LIST,
    "structural_exposure": _STRING,
    "strategic_tension": _STRING,
    "external_pressure": _STRING,
    "likely_business_consequence": _STRING,
    "emerging_need": _STRING,
    "ability_to_act": _STRING,
    "validation_question": _STRING,
    "falsifier": _STRING,
    "causal_sequence": _STRING,
    "evidence_basis": _STRING_LIST,
})

_BUYING_SALES_PLAY_JSON_SCHEMA = _strict_object({
    "account_signal_id": _STRING,
    "hypothesis_id": _STRING,
    "service_id": _STRING,
    "service_or_offer": _STRING,
    "service_fit_rationale": _STRING,
    "primary_buyer": _STRING,
    "secondary_buyers": _STRING_LIST,
    "first_meeting_objective": _STRING,
    "safe_customer_wording": _STRING,
    "discovery_questions_by_role": {
        "type": "array",
        "items": _strict_object({"role": _STRING, "questions": _STRING_LIST}),
    },
    "disqualifiers": _STRING_LIST,
    "do_not_say": _STRING_LIST,
})

BUYING_SECTION_JSON_SCHEMAS = {
    "snapshot_thesis": _strict_object({
        "qualification_reasoning": _QUALIFICATION_SCHEMA,
        "model_notes": _STRING_LIST,
    }),
    "why_now": _strict_object({
        "why_now": {
            "type": "array",
            "items": _strict_object({
                "trigger": _STRING,
                "why_it_matters_now": _STRING,
                "buyer_relevance": _STRING,
                "safe_discovery_question": _STRING,
                "gate": _GATE,
                "confidence": _NUMBER,
                "evidence_basis": _STRING_LIST,
            }),
        },
        "model_notes": _STRING_LIST,
    }),
    "pain_to_solution": _strict_object({
        "pain_to_solution_map": {
            "type": "array",
            "items": _strict_object({
                "account_signal_id": _STRING,
                "evidence": _STRING,
                "interpreted_pressure": _STRING,
                "buyer_lens": _STRING,
                "service": _STRING,
                "why_this_service": _STRING,
                "proof_point": _STRING,
                "safe_wording": _STRING,
                "missing_evidence": _STRING,
                "gate": _GATE,
                "confidence": _NUMBER,
                "disqualifiers": _STRING_LIST,
            }),
        },
        "model_notes": _STRING_LIST,
    }),
    "pressure_evidence": _strict_object({
        "pressure_signals": {
            "type": "array",
            "items": _strict_object({
                "signal_type": _STRING,
                "trigger": _STRING,
                "description": _STRING,
                "business_consequence": _STRING,
                "buyer_role_concern": _STRING,
                "gate": _GATE,
                "confidence": _NUMBER,
                "evidence_basis": _STRING_LIST,
            }),
        },
        "evidence_confidence": _EVIDENCE_CONFIDENCE_SCHEMA,
        "missing_input_warnings": _STRING_LIST,
        "model_notes": _STRING_LIST,
    }),
    "stakeholders": _strict_object({
        "stakeholder_map": {
            "type": "array",
            "items": _strict_object({
                "name": _STRING,
                "role": _STRING,
                "likely_buying_committee_role": _STRING,
                "relevance_rationale": _STRING,
                "linked_pressure_themes": _STRING_LIST,
                "linked_service_themes": _STRING_LIST,
                "evidence_basis": _STRING,
                "confidence": _NUMBER,
                "allowed_use": {
                    "type": "string",
                    "enum": ["internal_only", "allowed_as_discovery_question", "allowed_as_hypothesis"],
                },
                "do_not_say": _STRING_LIST,
                "safety_notes": _STRING_LIST,
            }),
        },
        "model_notes": _STRING_LIST,
    }),
}

_BUYING_GATE_DECISION_SCHEMA = _strict_object({
    "status": {
        "type": "string",
        "enum": ["approved", "changes_required", "unavailable"],
    },
    "reviewed_claim_ids": _STRING_LIST,
    "claim_decisions": {
        "type": "array",
        "items": _strict_object({
            "claim_id": _STRING,
            "decision": {
                "type": "string",
                "enum": ["downgrade", "rewrite", "remove", "reanalysis_required"],
            },
            "new_gate": _GATE,
            "unsafe_text": _STRING,
            "reason": _STRING,
        }),
    },
    "notes": _STRING_LIST,
})

_BUYING_GATE_REPAIR_SCHEMA = _strict_object({
    "repairs": {
        "type": "array",
        "items": _strict_object({
            "claim_id": _STRING,
            "object_id": _STRING,
            "path": _STRING,
            "action": {"type": "string", "enum": ["replace", "remove", "reanalysis_required"]},
            "replacement": _STRING,
            "reason": _STRING,
        }),
    },
    "notes": _STRING_LIST,
})

_BUYING_GATE_REANALYSIS_SCHEMA = _strict_object({
    "repairs": {
        "type": "array",
        "items": _strict_object({
            "claim_id": _STRING,
            "object_id": _STRING,
            "path": _STRING,
            "action": {"type": "string", "enum": ["replace", "remove"]},
            "replacement": _STRING,
            "reason": _STRING,
        }),
    },
    "notes": _STRING_LIST,
})

_BUYING_LANGUAGE_QA_SCHEMA = _strict_object({
    "repairs": {
        "type": "array",
        "items": _strict_object({
            "qa_id": _STRING,
            "path": _STRING,
            "action": {"type": "string", "enum": ["replace", "remove"]},
            "replacement": _STRING,
            "reason": _STRING,
        }),
    },
    "notes": _STRING_LIST,
})

# Backward-compatible name retained for tests and integrations that inspect the schema.
_BUYING_GATE_REVIEW_SCHEMA = _BUYING_GATE_DECISION_SCHEMA


BUYING_SECTION_INSTRUCTIONS = {
    "snapshot_thesis": (
        "Create one shared Qualification Reasoning Spine for the opening of the report. Start from verified account "
        "condition and Scanner activation pairs, then reason in this order: current position, structural exposure, "
        "strategic tension, external pressure, likely business consequence, emerging need, ability to act, validation "
        "question, and falsifier. Use the complete Strategic Synthesis as the analytical backbone and General Summary "
        "only for factual context. Do not list services, buyer roles, raw SIG contracts, WHY/WHAT field labels, or source "
        "records. Account thesis and causal_sequence must be fluent executive prose that integrates the 2-3 active "
        "signals rather than repeating them. Evidence gaps define uncertainty; they are not proof that a need exists."
    ),
    "why_now": (
        "Create Timing Triggers & Urgency Signals. Each item must cite a SIG id, explain what changed, "
        "why it may matter now or soon, who probably cares, and include one safe discovery question."
    ),
    "pain_to_solution": (
        "Create Potential Response Directions. Each fit must start with an account_signal_map item, then map "
        "account evidence to interpreted pressure, buyer lens, Vivicta service, proof point, safe wording, "
        "missing evidence, confidence boundary, and disqualifiers. Service KB is only an offer catalog; do not "
        "recommend a service from Service KB pain triggers alone."
    ),
    "sales_plays": (
        "Create Recommended Growth Plays. Each play must map to one scanner_activation_pair SIG id when available, "
        "preserve activation_pair_index, and classify play_type as current_status, foresight_driven, or proactive_opportunity. "
        "Each play must include a need_hypothesis with future_pressure, current_condition, structural_exposure, "
        "likely_business_consequence, why_now_or_soon, what_to_propose, discovery_question, disqualifier, "
        "company_context, market_context, combined_activation_hypothesis, falsifier, and context_type "
        "(company_only, market_watch, or combined_activation). WHY is why the customer challenge matters; "
        "WHAT_TO_DO is the customer outcome or deliverable state to validate next, not the consulting method used to create it; "
        "WHAT_PROGRESS_SHOWS is the observable proof of progress. "
        "Each play must also name account thesis, buying hypothesis, trigger-to-offer bridge, primary/secondary buyers, "
        "first meeting objective, discovery questions by buyer role, qualification upgrade criteria, disqualifiers, "
        "safe customer-facing wording, do_not_say notes, readiness_band, evidence_coverage, confidence_boundary, "
        "priority_band, and response_directions. Keep play_score/hypothesis_strength only as internal compatibility fields. "
        "Use modernization_tactics as an Evolving Modernization support layer only after an account SIG exists: map 1-3 "
        "relevant tactics into response_directions.evolving_modernization_directions, but never use a tactic as the reason "
        "for the play and never expose internal TAC codes. "
        "Do not create a separate conversation-angles section or a delivery plan."
    ),
    "pressure_evidence": (
        "Create Commercial Pressure Signals from required_sales_spine first. Produce one complete pressure signal "
        "per required SIG and do not add extra signals unless there is a non-empty SIG/source evidence basis. "
        "Keep evidence_confidence buckets short and conservative; deterministic post-processing will finalize "
        "verified facts, weak/missing evidence, question-only claims, and do-not-say guardrails. "
        "Do not create empty pressure descriptions or blank evidence_basis values."
    ),
    "stakeholders": (
        "Create a rich Buying Committee & Influence Map. Use named stakeholders only when source-bound and "
        "role-relevant; otherwise use role-level buying committee guidance from buyer_role_lens. Each card must "
        "explain likely committee role, relevance rationale, linked pressure/service themes, evidence basis, "
        "allowed-use boundary, confidence, and do_not_say safety notes. Keep it professional and no private-life profiling."
    ),
}


BUYING_SECTION_KEYS = {
    "snapshot_thesis": ("qualification_reasoning", "account_snapshot", "sales_spine", "buying_potential_thesis"),
    "why_now": ("why_now",),
    "pain_to_solution": ("pain_to_solution_map",),
    "sales_plays": ("recommended_sales_plays",),
    "pressure_evidence": ("pressure_signals", "evidence_confidence", "missing_input_warnings"),
    "stakeholders": ("stakeholder_map",),
}


def _section_prompt(section: str, payload: dict) -> str:
    compact_payload = _section_payload(section, payload)
    return f"""
You are creating one section of an Admin-only Evidence-Gated Buying Potential & Insight report.

Section: {section}
Task: {BUYING_SECTION_INSTRUCTIONS[section]}

This is NOT confirmed buying intent. It is a falsifiable need-hypothesis engine based on already stored Scanner data.
Use only the JSON payload below. Do not browse the web. Do not invent services, clients, personal data, dates, financials, partnerships, ownership details, or buying signals.

Evidence contract:
- allowed_as_fact: only when supported by verified_evidence, financial_sources marked generation_eligible, or explicitly labeled Scanner internal metrics.
- allowed_as_hypothesis: plausible interpretation from Scanner diagnosis, pressure, foresight, Company Evidence DB, or service-pattern fit.
- allowed_as_discovery_question: useful but weak or uncertain signal that should be asked, not asserted.
- internal_only: useful for sales prep but not safe for customer-facing wording.
- blocked: unsupported, wrong-entity, sensitive, or overclaiming material.
- Say "buying hypothesis", "need hypothesis", or "demand creation potential"; never "confirmed buying intent", unless explicit evidence exists in the payload.
- Stakeholder notes must remain professional, role-relevant, and source-bound. No private-life profiling.
- Strategic Synthesis and scanner_activation_pair signals are the sales spine. Preserve each pair's why, what_to_do, and what_progress_shows logic.
- WHY is why the customer challenge matters. WHAT_TO_DO is the customer outcome or deliverable state to validate next. WHAT_PROGRESS_SHOWS is observable proof of progress. Do not turn WHAT_TO_DO into consulting-method/HOW language or a pre-scoped implementation plan.
- General Summary is factual context only. AI Diagnosis and evidence_based_risks are internal reasoning and discovery framing, not customer-facing facts.
- Numeric scores are internal compatibility diagnostics only. They must not be used as proof that a need exists, as customer-facing evidence, or as the main reason for a proposal.
- Every recommended play should map to one activation pair when any activation-pair SIG exists.
- Every service recommendation must cite at least one account_signal_map SIG id. Service KB pain triggers may refine wording, but cannot be the only reason for a recommendation.
- Evolving Modernization tactics are support lenses only. Use modernization_tactics after an account SIG exists, map at most 1-3 relevant tactics to the response direction, and never show TAC codes or let a tactic create demand by itself.
- Every recommended play must separate company_context, market_context, combined_activation_hypothesis, falsifier, and response_directions. Response directions are not delivery scope, staffing, pricing, schedule, or implementation design.
- If a bridge from account signal to service is weak, use allowed_as_discovery_question or internal_only.
- Do not output empty pressure descriptions or generic buyer targets such as "relevant owner".
- required_sales_spine is authoritative. For required sections, preserve one complete item for each SIG in required_sales_spine unless the SIG is explicitly unavailable.
- Do not add extra pressure_signals unless they have a non-empty evidence_basis from a SIG or source identifier.

Return ONLY valid JSON with this exact section schema:
{BUYING_SECTION_SCHEMAS[section]}

Task-specific payload:
{json.dumps(compact_payload, ensure_ascii=False)}
""".strip()


QUALIFICATION_REASONING_FIELDS = (
    "account_thesis",
    "current_position",
    "structural_exposure",
    "strategic_tension",
    "external_pressure",
    "likely_business_consequence",
    "emerging_need",
    "ability_to_act",
    "validation_question",
    "falsifier",
    "causal_sequence",
)


def _clean_reasoning_sentence(value: Any, limit: int = 900) -> str:
    text = _clean_generated_text(value)
    text = re.sub(r"\S+\.(?:jpe?g|png|gif|webp|svg)\b", " ", text, flags=re.I)
    text = re.sub(r"\s+", " ", text).strip(" -;")
    return _clip(text, limit)


def _reasoning_list(value: Any, limit: int = 4) -> list[str]:
    items: list[str] = []
    for raw in _list(value):
        text = _clean_reasoning_sentence(raw, 520)
        if text and text not in items:
            items.append(text)
        if len(items) >= limit:
            break
    return items


def _qualification_reasoning_fallback(payload: dict) -> dict:
    company = _dict(payload.get("company"))
    company_name = str(company.get("name") or company.get("companyName") or "The company").strip()
    scanner_outputs = _dict(payload.get("scanner_outputs"))
    signals = [
        signal for signal in _list(payload.get("account_signal_map"))
        if isinstance(signal, dict) and signal.get("source") == "scanner_activation_pair"
    ][:3]
    if not signals:
        signals = [signal for signal in _list(payload.get("account_signal_map")) if isinstance(signal, dict)][:3]

    whys = [_clean_reasoning_sentence(signal.get("why"), 520) for signal in signals]
    whys = [item for item in whys if item]
    outcomes = [
        _clean_reasoning_sentence(signal.get("what_to_do") or signal.get("what"), 520)
        for signal in signals
    ]
    outcomes = [item for item in outcomes if item]
    progress = [
        _clean_reasoning_sentence(signal.get("what_progress_shows") or signal.get("business_consequence"), 420)
        for signal in signals
    ]
    progress = [item for item in progress if item]
    summary = _clean_reasoning_sentence(scanner_outputs.get("general_summary"), 900)
    synthesis = _clean_reasoning_sentence(scanner_outputs.get("strategic_synthesis"), 1500)
    boundary_items = _reasoning_list(scanner_outputs.get("evidence_based_risks"), 3)
    signal_ids = [str(signal.get("id")) for signal in signals if signal.get("id")]

    tension = whys[0] if whys else f"The current evidence does not yet establish how {company_name}'s stated priorities translate into operating reality."
    pressure = whys[1] if len(whys) > 1 else "External change may increase the value of validating whether current capabilities remain sufficient."
    emerging_need = " ".join(outcomes[:2]) or "A decision-ready view of the relevant capability, ownership, and progress expectations may be needed before priorities are set."
    consequence = progress[0] if progress else "Without validation, investment choices may remain disconnected from the pressure the company is preparing to absorb."
    causal = _clip(" ".join(item for item in (tension, pressure, consequence, emerging_need) if item), 1700)
    return {
        "account_thesis": _clip(synthesis or causal, 1100),
        "current_position": summary or f"{company_name} has an active Scanner record, but its current position requires qualification from the stored evidence.",
        "verified_strengths": [],
        "evidence_boundaries": boundary_items or ["The available evidence does not confirm ownership, timing, or a material business consequence."],
        "structural_exposure": tension,
        "strategic_tension": tension,
        "external_pressure": pressure,
        "likely_business_consequence": consequence,
        "emerging_need": emerging_need,
        "ability_to_act": "Ability and willingness to act should be established from current priorities, ownership, and investment capacity in discovery.",
        "validation_question": "Where would this pressure first become visible in priorities, operating performance, or investment decisions?",
        "falsifier": "The hypothesis weakens if the relevant capability is already controlled or the pressure has no material owner, timing, or business consequence.",
        "causal_sequence": causal,
        "evidence_basis": signal_ids,
    }


def _normalize_qualification_reasoning(value: Any, payload: dict, *, fill_missing: bool = True) -> dict:
    raw = _dict(value)
    fallback = _qualification_reasoning_fallback(payload)
    result: dict[str, Any] = {}
    for field in QUALIFICATION_REASONING_FIELDS:
        cleaned = _clean_reasoning_sentence(raw.get(field), 1700 if field == "causal_sequence" else 1100)
        result[field] = cleaned or (fallback.get(field, "") if fill_missing else "")
    result["verified_strengths"] = _reasoning_list(raw.get("verified_strengths"), 3)
    result["evidence_boundaries"] = _reasoning_list(raw.get("evidence_boundaries"), 4)
    if fill_missing and not result["evidence_boundaries"]:
        result["evidence_boundaries"] = fallback["evidence_boundaries"]
    basis = [str(item).strip() for item in _list(raw.get("evidence_basis")) if str(item).strip()]
    result["evidence_basis"] = basis or (fallback["evidence_basis"] if fill_missing else [])
    return result


def _qualification_reasoning_issues(reasoning: dict, company_name: str = "") -> list[str]:
    issues: list[str] = []
    required_lengths = {
        "account_thesis": 80,
        "current_position": 70,
        "structural_exposure": 45,
        "strategic_tension": 55,
        "external_pressure": 55,
        "likely_business_consequence": 45,
        "emerging_need": 55,
        "ability_to_act": 35,
        "validation_question": 35,
        "falsifier": 35,
        "causal_sequence": 140,
    }
    for field, minimum in required_lengths.items():
        if len(str(reasoning.get(field) or "").strip()) < minimum:
            issues.append(f"{field}:too_short")
    if not _reasoning_list(reasoning.get("evidence_boundaries"), 1):
        issues.append("evidence_boundaries:missing")
    combined = json.dumps(reasoning, ensure_ascii=False)
    if re.search(r"(?:https?://|!\[|<[^>]+>|\b[\w.-]+\.(?:jpe?g|png|gif|webp|svg)\b)", combined, re.I):
        issues.append("public_text:page_or_markup_debris")
    if re.search(r"\b(?:WHAT_TO_DO|WHAT_PROGRESS_SHOWS)\b|SIG-\d{2}\s*[—:-]\s*WHY\b", combined, re.I):
        issues.append("public_text:raw_activation_contract")
    if company_name:
        account_text = f"{reasoning.get('account_thesis', '')} {reasoning.get('current_position', '')}".lower()
        if company_name.lower() not in account_text:
            issues.append("account_context:not_company_specific")
    return sorted(set(issues))


def _qualification_repair_prompt(reasoning: dict, payload: dict, issues: list[str]) -> str:
    compact = _section_payload("snapshot_thesis", payload)
    return f"""
Rewrite one Qualification Reasoning Spine using stored Scanner data only.

This is a focused no-web quality repair, not a new research task.
Problems detected: {json.dumps(issues, ensure_ascii=False)}

Rules:
- Return ONLY valid JSON using the exact snapshot_thesis schema.
- Preserve evidence boundaries and do not strengthen claims, gates, financial scope, ownership, timing, or buying intent.
- Reason in sequence: verified current position -> structural exposure -> strategic tension -> external pressure -> likely business consequence -> emerging need -> ability to act -> validation question -> falsifier.
- Integrate the active signals into fluent executive prose. Do not serialize SIG/WHY/WHAT fields or list services and buyer roles.
- Evidence absence defines a validation boundary; it is not proof of weakness or demand.
- Remove page navigation, filenames, URLs, markup, and source-record fragments.

Current reasoning:
{json.dumps(reasoning, ensure_ascii=False)}

Stored payload:
{json.dumps(compact, ensure_ascii=False)}

Required schema:
{BUYING_SECTION_SCHEMAS['snapshot_thesis']}
""".strip()


def _compose_qualification_sections(reasoning: dict, payload: dict) -> dict:
    boundaries = _reasoning_list(reasoning.get("evidence_boundaries"), 3)
    strengths = _reasoning_list(reasoning.get("verified_strengths"), 3)
    first_paragraph = " ".join(
        item for item in (_clean_reasoning_sentence(reasoning.get("current_position"), 1000), " ".join(strengths)) if item
    )
    second_paragraph = " ".join(boundaries)
    qualification_implication = _clean_reasoning_sentence(reasoning.get("structural_exposure"), 720)
    if qualification_implication:
        implication_clause = qualification_implication.rstrip(".")
        implication_clause = implication_clause[0].lower() + implication_clause[1:] if len(implication_clause) > 1 else implication_clause
        second_paragraph = f"{second_paragraph} The qualification issue is whether {implication_clause}.".strip()

    thesis_first = " ".join(
        item for item in (
            _clean_reasoning_sentence(reasoning.get("strategic_tension"), 720),
            _clean_reasoning_sentence(reasoning.get("external_pressure"), 720),
        ) if item
    )
    thesis_second = " ".join(
        item for item in (
            _clean_reasoning_sentence(reasoning.get("likely_business_consequence"), 620),
            _clean_reasoning_sentence(reasoning.get("emerging_need"), 720),
        ) if item
    )
    thesis_text = " ".join(item for item in (thesis_first, thesis_second) if item)
    thesis_rationale = " ".join(
        item for item in (
            _clean_reasoning_sentence(reasoning.get("ability_to_act"), 520),
            _clean_reasoning_sentence(reasoning.get("validation_question"), 520),
            _clean_reasoning_sentence(reasoning.get("falsifier"), 520),
        ) if item
    )
    basis = [str(item) for item in _list(reasoning.get("evidence_basis")) if str(item).strip()]
    risk_themes = _dedupe_text_items(
        boundaries + _reasoning_list(_dict(payload.get("scanner_outputs")).get("evidence_based_risks"), 3),
        limit=5,
        clip_limit=240,
    )
    return {
        "qualification_reasoning": reasoning,
        "account_snapshot": {
            "text": _clip(f"{first_paragraph}\n\n{second_paragraph}".strip(), 1900),
            "paragraphs": [item for item in (first_paragraph, second_paragraph) if item],
            "gate": "allowed_as_hypothesis",
            "evidence_basis": basis,
        },
        "buying_potential_thesis": {
            "text": _clip(thesis_text, 2100),
            "paragraphs": [item for item in (thesis_first, thesis_second) if item],
            "rationale": _clip(thesis_rationale, 1300),
            "gate": "allowed_as_hypothesis",
            "confidence": 50,
            "evidence_basis": basis,
            "overclaim_risk": "medium",
        },
        "sales_spine": {
            "account_thesis": _clip(reasoning.get("account_thesis"), 1500),
            "strategic_synthesis_basis": _clip(reasoning.get("causal_sequence"), 2000),
            "risk_themes": risk_themes[:5],
        },
    }


def _finalize_qualification_section(
    section_data: dict,
    payload: dict,
    *,
    model_mode: str | None = None,
) -> tuple[dict, dict]:
    reasoning = _normalize_qualification_reasoning(
        section_data.get("qualification_reasoning"),
        payload,
        fill_missing=False,
    )
    company = _dict(payload.get("company"))
    company_name = str(company.get("name") or company.get("companyName") or "")
    initial_issues = _qualification_reasoning_issues(reasoning, company_name)
    repair_model = ""
    if initial_issues:
        try:
            raw, repair_model = call_json_model_chain(
                _qualification_repair_prompt(reasoning, payload, initial_issues),
                buying_generation_chain(model_mode),
                max_tokens=9000,
                timeout=120,
                reasoning_effort="high",
                text_verbosity="medium",
                json_schema=BUYING_SECTION_JSON_SCHEMAS["snapshot_thesis"],
                json_schema_name="buying_qualification_repair",
                log_label="Buying Qualification Repair",
                validate_json=True,
                required_fields=("qualification_reasoning",),
            )
            repaired = parse_json_object(raw)
            reasoning = _normalize_qualification_reasoning(
                repaired.get("qualification_reasoning"),
                payload,
                fill_missing=False,
            )
        except Exception as exc:
            print(f"   ⚠️ [Buying Potential] Qualification reasoning repair unavailable: {exc}")
    remaining_issues = _qualification_reasoning_issues(reasoning, company_name)
    if remaining_issues:
        fallback = _normalize_qualification_reasoning(_qualification_reasoning_fallback(payload), payload)
        for field in QUALIFICATION_REASONING_FIELDS:
            if f"{field}:too_short" in remaining_issues:
                reasoning[field] = fallback[field]
        if any(issue.startswith("public_text:") for issue in remaining_issues):
            for field in QUALIFICATION_REASONING_FIELDS:
                if re.search(r"(?:https?://|!\[|<[^>]+>|WHAT_TO_DO|WHAT_PROGRESS_SHOWS|SIG-\d{2}\s*[—:-]\s*WHY)", str(reasoning.get(field) or ""), re.I):
                    reasoning[field] = fallback[field]
        if "account_context:not_company_specific" in remaining_issues:
            reasoning["account_thesis"] = fallback["account_thesis"]
            reasoning["current_position"] = fallback["current_position"]
        if not reasoning.get("evidence_boundaries"):
            reasoning["evidence_boundaries"] = fallback["evidence_boundaries"]
        if not reasoning.get("evidence_basis"):
            reasoning["evidence_basis"] = fallback["evidence_basis"]
        remaining_issues = _qualification_reasoning_issues(reasoning, company_name)

    result = _compose_qualification_sections(reasoning, payload)
    result["model_notes"] = _list(section_data.get("model_notes"))
    quality = {
        "initial_issues": initial_issues,
        "remaining_issues": remaining_issues,
        "repair_model": repair_model,
        "status": "clean" if not initial_issues else "repaired" if not remaining_issues else "deterministic_completion",
    }
    result["qualification_reasoning_quality"] = quality
    print(
        f"   🧠 [Buying Potential] Qualification reasoning: status={quality['status']}, "
        f"initial_issues={len(initial_issues)}, remaining_issues={len(remaining_issues)}, "
        f"repair_model={repair_model or 'none'}"
    )
    if initial_issues:
        result["model_notes"].append(
            f"Opening qualification reasoning quality path: {quality['status']}; issues={', '.join(initial_issues)}."
        )
    return result, quality


def _section_repair_prompt(section: str, raw_json: str) -> str:
    return f"""
Repair this Buying Potential section into valid JSON.

Rules:
- Return ONLY JSON. No markdown.
- Do not add new claims, people, dates, financial values, partnerships, sources, or services.
- Preserve the original meaning where possible.
- If a field is malformed or incomplete, repair it conservatively or use an empty array/string.
- Every gate must be one of: allowed_as_fact, allowed_as_hypothesis, allowed_as_discovery_question, internal_only, blocked.
- Confidence fields must be integers from 0 to 100.

Required schema:
{BUYING_SECTION_SCHEMAS[section]}

Malformed JSON:
{str(raw_json or '')[:42000]}
""".strip()


def _report_repair_prompt(raw_json: str) -> str:
    return f"""
Repair this Buying Potential report into valid JSON.

Rules:
- Return ONLY JSON. No markdown.
- Do not add new claims, people, dates, financial values, partnerships, sources, or services.
- Preserve the original meaning where possible.
- If a field is malformed or incomplete, repair it conservatively or use an empty array/string.
- Every gate must be one of: allowed_as_fact, allowed_as_hypothesis, allowed_as_discovery_question, internal_only, blocked.
- Item-level confidence fields must be integers from 0 to 100.

Required top-level schema:
{{
  "account_snapshot": {{"text": "", "gate": "allowed_as_hypothesis", "evidence_basis": []}},
  "buying_potential_thesis": {{"text": "", "gate": "allowed_as_hypothesis", "confidence": 0}},
  "pressure_signals": [],
  "why_now": [],
  "stakeholder_map": [],
  "pain_to_solution_map": [],
  "recommended_sales_plays": [],
  "evidence_confidence": {{"strongest_evidence": [], "weak_or_missing_evidence": [], "do_not_use_claims": [], "sensitive_claims": [], "claims_as_questions_only": []}},
  "missing_input_warnings": [],
  "model_notes": []
}}

Malformed JSON:
{str(raw_json or '')[:42000]}
""".strip()


def _gate_claim_packet_item(path: str, value: Any, parent: dict | None = None) -> dict | None:
    parent = _dict(parent)
    text = _clip(value, 720)
    if not text:
        return None
    signal_ids = _item_signal_ids(parent) or _text_signal_ids(text)
    hypothesis_ids = [str(item) for item in _list(parent.get("linked_hypothesis_ids")) if item]
    if parent.get("hypothesis_id"):
        hypothesis_ids.append(str(parent.get("hypothesis_id")))
    evidence_basis = [str(item) for item in _list(parent.get("evidence_basis")) if item]
    object_id = (signal_ids + hypothesis_ids + [path.split("[", 1)[0]])[0]
    claim_key = f"{path}|{object_id}".encode("utf-8")
    return {
        "claim_id": f"CLM-{hashlib.sha256(claim_key).hexdigest()[:12].upper()}",
        "path": path,
        "object_id": object_id,
        "text": text,
        "gate": _normalize_gate(parent.get("gate"), "allowed_as_hypothesis"),
        "signal_ids": signal_ids[:3],
        "hypothesis_ids": list(dict.fromkeys(hypothesis_ids))[:3],
        "evidence_ids": list(dict.fromkeys(evidence_basis))[:8],
    }


def _build_gate_review_packet(report: dict, payload: dict) -> dict:
    claims: list[dict] = []

    def add(path: str, value: Any, parent: dict | None = None):
        item = _gate_claim_packet_item(path, value, parent)
        if item:
            claims.append(item)

    for section in ("account_snapshot", "buying_potential_thesis"):
        item = _dict(report.get(section))
        add(f"{section}.text", item.get("text"), item)
    spine = _dict(report.get("sales_spine"))
    add("sales_spine.account_thesis", spine.get("account_thesis"), spine)
    add("sales_spine.strategic_synthesis_basis", spine.get("strategic_synthesis_basis"), spine)
    for index, item in enumerate(_list(spine.get("top_buying_hypotheses"))[:3]):
        parent = _dict(item)
        hypothesis_value = parent.get("hypothesis") or parent.get("combined_activation_hypothesis") or item
        hypothesis_path = (
            f"sales_spine.top_buying_hypotheses[{index}].hypothesis"
            if parent.get("hypothesis")
            else f"sales_spine.top_buying_hypotheses[{index}].combined_activation_hypothesis"
            if parent.get("combined_activation_hypothesis")
            else f"sales_spine.top_buying_hypotheses[{index}]"
        )
        add(
            hypothesis_path,
            hypothesis_value,
            parent,
        )

    section_fields = {
        "pressure_signals": ("trigger", "description", "business_consequence"),
        "why_now": ("trigger", "why_it_matters_now", "safe_discovery_question"),
        "pain_to_solution_map": ("interpreted_pressure", "why_this_service", "safe_wording"),
        "recommended_sales_plays": (
            "buying_hypothesis", "trigger_to_offer_bridge", "safe_customer_wording",
            "first_meeting_objective", "proof_point",
        ),
    }
    for section, fields in section_fields.items():
        for index, item in enumerate(_list(report.get(section))[:6]):
            parent = _dict(item)
            for field in fields:
                add(f"{section}[{index}].{field}", parent.get(field), parent)
            need = _dict(parent.get("need_hypothesis"))
            for field in ("current_condition", "future_pressure", "combined_activation_hypothesis"):
                add(f"{section}[{index}].need_hypothesis.{field}", need.get(field), {**parent, **need})

    cited_ids = {
        evidence_id
        for claim in claims
        for evidence_id in _list(claim.get("evidence_ids"))
        if evidence_id
    }
    evidence_index: list[dict] = []
    evidence_sources = (
        _list(payload.get("verified_evidence"))
        + _list(payload.get("financial_sources"))
        + _list(payload.get("source_ledger_recovery_records"))
    )
    for raw in evidence_sources:
        item = _dict(raw)
        evidence_id = str(item.get("id") or item.get("source_id") or "").strip()
        if not evidence_id or (cited_ids and evidence_id not in cited_ids):
            continue
        evidence_index.append({
            "id": evidence_id,
            "gate": _source_item_gate(item),
            "entity_scope": item.get("entity_scope") or item.get("scope"),
            "generation_eligible": bool(item.get("generation_eligible") or item.get("buying_evidence_eligible")),
            "independent_source_count": _safe_int(item.get("independent_source_count"), 0),
            "excerpt": _first_nonempty(
                item.get("source_quote"), item.get("quote"), item.get("snippet"),
                item.get("claim"), item.get("finding"), item.get("description"), limit=420,
            ),
        })
        if len(evidence_index) >= 48:
            break

    signal_index = [_compact_signal(item) for item in _list(payload.get("account_signal_map")) if isinstance(item, dict)][:10]
    service_bridges = []
    for index, play in enumerate(_list(report.get("recommended_sales_plays"))[:6]):
        play = _dict(play)
        service_bridges.append({
            "path": f"recommended_sales_plays[{index}]",
            "signal_ids": _item_signal_ids(play),
            "service_id": play.get("service_id"),
            "service_name": play.get("service_or_offer"),
            "bridge": _clip(play.get("service_fit_rationale") or play.get("trigger_to_offer_bridge"), 420),
            "gate": _normalize_gate(play.get("gate"), "allowed_as_hypothesis"),
        })

    packet = {
        "review_scope": "Evidence safety, claim strength, service bridges, and customer-facing wording only.",
        "allowed_use_boundary": _dict(report.get("reliability_status")).get("allowed_use")
        or "Internal sales hypothesis; customer-facing claims require evidence-gate compliance.",
        "evidence_sufficiency": payload.get("evidence_sufficiency"),
        "required_sales_spine": _list(payload.get("required_sales_spine"))[:3],
        "hypothesis_contracts": [
            {
                "hypothesis_id": item.get("hypothesis_id"),
                "activation_signal_id": item.get("activation_signal_id"),
                "external_pressure": _clip(item.get("external_pressure"), 420),
                "financial_scope": _clip(item.get("financial_scope"), 280),
                "wording_boundary": _clip(item.get("wording_boundary"), 280),
                "prohibited_claims": _list(item.get("prohibited_claims"))[:6],
                "maximum_permitted_gate": item.get("maximum_permitted_gate"),
            }
            for item in _list(payload.get("hypothesis_registry"))[:3]
            if isinstance(item, dict)
        ],
        "claims": claims,
        "signals": signal_index,
        "evidence_index": evidence_index,
        "service_bridges": service_bridges,
    }
    return packet


def _gate_prompt_for_packet(packet: dict) -> str:
    return f"""
Review this compact Buying Insight gate packet. This is a bounded semantic safety check, not a second analysis.
Return only JSON matching the supplied schema.

Review rules:
- Review only evidence safety, claim strength, service bridges, and customer-facing wording.
- Do not assess originality, analytical depth, writing style, or whether a different hypothesis would be better.
- Factual claims require fact-grade eligible evidence with correct entity scope.
- Return every supplied claim_id in reviewed_claim_ids exactly once. claim_decisions contains flagged claims only.
- Use downgrade only when the existing sentence is already safe and only its usage gate is too permissive.
- If sentence wording is unsafe or too strong, use rewrite, remove, or reanalysis_required. A gate downgrade alone is not a wording correction.
- Use reanalysis_required only when simply narrowing/removing the sentence would break the linked SIG/HYP causal chain.
- Copy the exact unsafe wording into unsafe_text for rewrite, remove, and reanalysis_required decisions.
- Weak or incomplete support may be hypothesis/question only; unsupported specifics require a claim decision.
- Service recommendations require an existing SIG/HYP bridge and an exact service ID/name from the packet.
- Use status=approved when claim_decisions is empty; changes_required when one or more decisions are supplied.
- Use status=unavailable only when the packet cannot be evaluated. Do not invent evidence or IDs.

Compact gate packet:
{json.dumps(packet, ensure_ascii=False)}
""".strip()


def _gate_prompt(report: dict, payload: dict) -> str:
    return _gate_prompt_for_packet(_build_gate_review_packet(report, payload))


def _gate_claim_lookup(packet: dict) -> dict[str, dict]:
    return {
        str(item.get("claim_id")): item
        for item in _list(packet.get("claims"))
        if isinstance(item, dict) and item.get("claim_id")
    }


def _gate_decision_is_complete(review: dict, packet: dict) -> tuple[bool, str]:
    if str(review.get("status") or "").lower() == "unavailable":
        return False, "Verifier returned unavailable."
    claim_lookup = _gate_claim_lookup(packet)
    reviewed = [str(item) for item in _list(review.get("reviewed_claim_ids")) if item]
    if set(reviewed) != set(claim_lookup):
        missing = sorted(set(claim_lookup) - set(reviewed))
        return False, f"Claim coverage incomplete; missing {', '.join(missing[:6])}."
    seen: set[str] = set()
    for raw in _list(review.get("claim_decisions")):
        item = _dict(raw)
        claim_id = str(item.get("claim_id") or "")
        if claim_id not in claim_lookup or claim_id in seen:
            return False, "Claim decisions contain an unknown or duplicate claim_id."
        seen.add(claim_id)
        decision = str(item.get("decision") or "")
        unsafe_text = _clean_generated_text(item.get("unsafe_text"))
        if decision in {"rewrite", "remove", "reanalysis_required"} and not unsafe_text:
            return False, f"{claim_id} requires exact unsafe_text."
        if decision in {"rewrite", "remove", "reanalysis_required"} and unsafe_text != _clean_generated_text(claim_lookup[claim_id].get("text")):
            return False, f"{claim_id} unsafe_text must exactly match the reviewed claim text."
        if decision == "downgrade" and unsafe_text:
            return False, f"{claim_id} cannot use downgrade while unsafe_text remains."
        if decision == "downgrade":
            gate_rank = {
                "blocked": 0,
                "internal_only": 1,
                "allowed_as_discovery_question": 2,
                "allowed_as_hypothesis": 3,
                "allowed_as_fact": 4,
            }
            current_gate = _normalize_gate(claim_lookup[claim_id].get("gate"), "allowed_as_hypothesis")
            new_gate = _normalize_gate(item.get("new_gate"), current_gate)
            if gate_rank.get(new_gate, 0) >= gate_rank.get(current_gate, 0):
                return False, f"{claim_id} downgrade must select a stricter gate."
    expected_status = "changes_required" if seen else "approved"
    if str(review.get("status") or "").lower() != expected_status:
        return False, f"Gate status must be {expected_status}."
    return True, ""


def _gate_context_for_claims(packet: dict, claim_ids: list[str]) -> dict:
    claim_lookup = _gate_claim_lookup(packet)
    claims = []
    seen: set[str] = set()
    for raw_claim_id in claim_ids:
        claim_id = str(raw_claim_id or "")
        if claim_id and claim_id in claim_lookup and claim_id not in seen:
            seen.add(claim_id)
            claims.append(claim_lookup[claim_id])
    linked_hypotheses = {
        hypothesis_id
        for claim in claims
        for hypothesis_id in _list(claim.get("hypothesis_ids"))
        if hypothesis_id
    }
    linked_signals = {
        signal_id
        for claim in claims
        for signal_id in _list(claim.get("signal_ids"))
        if signal_id
    }
    evidence_ids = {
        evidence_id
        for claim in claims
        for evidence_id in _list(claim.get("evidence_ids"))
        if evidence_id
    }
    return {
        "allowed_use_boundary": packet.get("allowed_use_boundary"),
        "claims": claims,
        "hypothesis_contracts": [
            item for item in _list(packet.get("hypothesis_contracts"))
            if isinstance(item, dict)
            and (not linked_hypotheses or item.get("hypothesis_id") in linked_hypotheses)
        ],
        "signals": [
            item for item in _list(packet.get("signals"))
            if isinstance(item, dict)
            and (not linked_signals or item.get("id") in linked_signals)
        ],
        "evidence_index": [
            item for item in _list(packet.get("evidence_index"))
            if isinstance(item, dict) and item.get("id") in evidence_ids
        ],
    }


def _gate_decision_batches(packet: dict, *, max_claims: int = 30) -> list[dict]:
    claims = [item for item in _list(packet.get("claims")) if isinstance(item, dict)]
    if not claims:
        return [{**packet, "claims": []}]
    batches: list[dict] = []
    for offset in range(0, len(claims), max_claims):
        batch_claims = [dict(item) for item in claims[offset:offset + max_claims]]
        claim_ids = [str(item.get("claim_id") or "") for item in batch_claims]
        context = _gate_context_for_claims(packet, claim_ids)
        batch_signal_ids = {
            linked for claim in batch_claims for linked in _list(claim.get("signal_ids"))
        }
        batch = {
            "review_scope": packet.get("review_scope"),
            "allowed_use_boundary": packet.get("allowed_use_boundary"),
            "evidence_sufficiency": packet.get("evidence_sufficiency"),
            "required_sales_spine": packet.get("required_sales_spine"),
            "hypothesis_contracts": context.get("hypothesis_contracts"),
            "claims": batch_claims,
            "signals": context.get("signals"),
            "evidence_index": context.get("evidence_index"),
            "service_bridges": [
                item for item in _list(packet.get("service_bridges"))
                if isinstance(item, dict)
                and any(signal_id in batch_signal_ids for signal_id in _list(item.get("signal_ids")))
            ],
        }
        serialized = json.dumps(batch, ensure_ascii=False)
        if len(serialized) > BUYING_GATE_PACKET_CHAR_LIMIT:
            for item in batch["claims"]:
                item["text"] = _clip(item.get("text"), 420)
            batch["evidence_index"] = _list(batch.get("evidence_index"))[:24]
            for item in batch["evidence_index"]:
                item["excerpt"] = _clip(item.get("excerpt"), 260)
        batches.append(batch)
    return batches


def _gate_repair_prompt(packet: dict, decisions: list[dict]) -> str:
    claim_ids = [str(item.get("claim_id") or "") for item in decisions]
    context = _gate_context_for_claims(packet, claim_ids)
    return f"""
Repair only the flagged Buying Insight sentences below. Return only JSON matching the supplied schema.

Rules:
- This is wording calibration, not new strategic analysis.
- Preserve the linked SIG/HYP meaning and evidence boundary. Add no new facts, services, evidence, timing, owners, or causal claims.
- Produce one repair per supplied claim_id.
- Use replace with a complete, fluent, narrower sentence when the supported meaning can be retained.
- Use remove when no safe supported wording remains.
- Use reanalysis_required only when replacement/removal would break the hypothesis's causal reasoning.
- Keep object_id and path exactly as supplied.

Flagged decisions:
{json.dumps(decisions, ensure_ascii=False)}

Bounded context:
{json.dumps(context, ensure_ascii=False)}
""".strip()


def _stable_contract_ids(value: Any) -> set[str]:
    return set(re.findall(r"\b(?:SIG|HYP|EVD|SRC|FIN|CED|LEDGER)-[A-Z0-9._-]+\b", str(value or ""), re.I))


def _gate_repair_contract_issue(packet: dict, raw: dict) -> str:
    claim = _gate_claim_lookup(packet).get(str(raw.get("claim_id") or ""))
    if not claim:
        return "Repair references an unknown claim_id."
    if str(raw.get("path") or "") != str(claim.get("path") or ""):
        return "Repair path does not match the reviewed claim."
    if str(raw.get("object_id") or "") != str(claim.get("object_id") or ""):
        return "Repair object_id does not match the reviewed claim."
    action = str(raw.get("action") or "")
    replacement = _clean_generated_text(raw.get("replacement"))
    if action == "replace" and len(replacement) < 20:
        return "Replacement is missing or too short."
    if action != "replace":
        return ""
    allowed_ids = _stable_contract_ids(claim.get("text"))
    allowed_ids.update(str(item) for item in _list(claim.get("signal_ids")))
    allowed_ids.update(str(item) for item in _list(claim.get("hypothesis_ids")))
    allowed_ids.update(str(item) for item in _list(claim.get("evidence_ids")))
    added_ids = _stable_contract_ids(replacement) - allowed_ids
    if added_ids:
        return f"Replacement introduced unsupported IDs: {', '.join(sorted(added_ids))}."
    linked_hypotheses = set(str(item) for item in _list(claim.get("hypothesis_ids")))
    for contract in _list(packet.get("hypothesis_contracts")):
        contract = _dict(contract)
        if linked_hypotheses and str(contract.get("hypothesis_id") or "") not in linked_hypotheses:
            continue
        for prohibited in _list(contract.get("prohibited_claims")):
            if _blocked_claim_matches(replacement, str(prohibited)):
                return "Replacement repeats a prohibited hypothesis-contract claim."
    return ""


def _gate_repair_batches(
    packet: dict,
    decisions: list[dict],
    *,
    max_items: int = 5,
    max_chars: int = 24000,
) -> list[list[dict]]:
    claim_lookup = _gate_claim_lookup(packet)

    def sort_key(decision: dict) -> tuple[str, str, str]:
        claim = claim_lookup.get(str(decision.get("claim_id") or ""), {})
        lineage_values = (
            _list(claim.get("hypothesis_ids"))
            + _list(claim.get("signal_ids"))
            + [claim.get("object_id")]
        )
        lineage = next((str(item) for item in lineage_values if item), "")
        section = str(claim.get("path") or "").split(".", 1)[0].split("[", 1)[0]
        return str(lineage), section, str(claim.get("path") or "")

    ordered = sorted((_dict(item) for item in decisions), key=sort_key)
    batches: list[list[dict]] = []
    current: list[dict] = []
    for decision in ordered:
        candidate = current + [decision]
        exceeds_items = len(candidate) > max(1, max_items)
        exceeds_chars = len(_gate_repair_prompt(packet, candidate)) > max_chars
        if current and (exceeds_items or exceeds_chars):
            batches.append(current)
            current = [decision]
        else:
            current = candidate
    if current:
        batches.append(current)
    return batches


def _gate_reanalysis_prompt(packet: dict, repairs: list[dict]) -> str:
    claim_ids = [str(item.get("claim_id") or "") for item in repairs]
    context = _gate_context_for_claims(packet, claim_ids)
    return f"""
Perform focused no-web reanalysis for only the flagged Buying Insight sentences. Return only JSON matching the schema.

Rules:
- Rebuild the sentence from the supplied current payload and HYP/SIG contract only.
- Preserve the existing causal chain: company condition, external pressure, consequence, and validation boundary.
- Do not add evidence, services, timing, owners, or claims not present in the bounded context.
- Produce one replace or remove action per claim_id. Keep object_id and path exactly as supplied.
- Use a complete reader-facing sentence. This is section-scoped recovery, not a rewrite of the report.

Items requiring reanalysis:
{json.dumps(repairs, ensure_ascii=False)}

Bounded context:
{json.dumps(context, ensure_ascii=False)}
""".strip()


def _normalize_gate_review(review: Any, *, model: str = "", unavailable_note: str = "") -> dict:
    normalized = _dict(review).copy()
    status = str(normalized.get("status") or "").strip().lower()
    if status not in {"approved", "changes_required", "unavailable"}:
        status = "approved" if normalized.get("approved") is True else "unavailable"
    normalized["status"] = status
    normalized["approved"] = status == "approved"
    normalized["model"] = model or normalized.get("model")
    normalized["required_downgrades"] = _list(normalized.get("required_downgrades"))[:20]
    normalized["field_actions"] = _list(normalized.get("field_actions"))[:20]
    normalized["blocked_claims"] = _list(normalized.get("blocked_claims"))[:20]
    normalized["reviewed_claim_ids"] = _list(normalized.get("reviewed_claim_ids"))[:80]
    normalized["claim_decisions"] = _list(normalized.get("claim_decisions"))[:40]
    normalized["unresolved_claim_ids"] = _list(normalized.get("unresolved_claim_ids"))[:40]
    normalized["deterministic_removal_count"] = _safe_int(normalized.get("deterministic_removal_count"), 0)
    normalized["notes"] = _list(normalized.get("notes"))[:10]
    if unavailable_note:
        normalized["notes"].append(_clip(unavailable_note, 360))
    return normalized


def _call_gate_stage(
    prompt: str,
    chain: Any,
    *,
    policy: dict,
    schema: dict,
    schema_name: str,
    log_label: str,
    validator=None,
) -> tuple[dict, str]:
    errors: list[str] = []
    for model in tuple(chain or ()):
        try:
            raw, used_model = call_json_model_chain(
                prompt,
                (model,),
                max_tokens=_safe_int(policy.get("max_output_tokens"), 5000),
                timeout=_safe_int(policy.get("timeout_seconds"), 90),
                reasoning_effort=str(policy.get("effort") or "low"),
                text_verbosity=str(policy.get("verbosity") or "low"),
                json_schema=schema,
                json_schema_name=schema_name,
                log_label=log_label,
                validate_json=True,
                required_fields=tuple(schema.get("required") or ()),
            )
            parsed = parse_json_object(raw)
            if validator:
                valid, reason = validator(parsed)
                if not valid:
                    raise ValueError(reason or "Gate stage semantic contract failed.")
            return parsed, used_model
        except Exception as exc:
            errors.append(f"{model}: {str(exc)[:220]}")
            print(f"   ⚠️ [{log_label}] {model} fallback: {str(exc)[:220]}", flush=True)
    raise ValueError("; ".join(errors) or f"{log_label} had no configured model.")


def _gate_parent_path(path: str) -> str:
    if path.startswith("account_snapshot."):
        return "account_snapshot"
    if path.startswith("buying_potential_thesis."):
        return "buying_potential_thesis"
    match = re.match(r"^([a-zA-Z_]+\[\d+\])", path)
    return match.group(1) if match else path.split(".", 1)[0]


def _normalize_repair_action(raw: dict, claim: dict, *, default_reason: str = "") -> dict:
    action = str(raw.get("action") or "remove").lower()
    if action not in {"replace", "remove", "reanalysis_required"}:
        action = "remove"
    replacement = _clean_generated_text(raw.get("replacement")) if action == "replace" else ""
    if action == "replace" and len(replacement) < 20:
        action = "reanalysis_required"
        replacement = ""
    return {
        "claim_id": claim.get("claim_id"),
        "object_id": claim.get("object_id"),
        "path": claim.get("path"),
        "action": action,
        "replacement": replacement,
        "reason": _clip(raw.get("reason") or default_reason, 300),
    }


def _run_gate_review_workflow(report: dict, payload: dict, model_mode: str | None = None) -> dict:
    packet = _build_gate_review_packet(report, payload)
    decision_batches = _gate_decision_batches(packet)
    print(
        f"   🔐 [Buying Potential Gate Decision] batches={len(decision_batches)} "
        f"claims={len(_list(packet.get('claims')))}",
        flush=True,
    )
    decision_policy = policy_for("buying_insight_gate_decision")
    decision_started = time.time()
    reviewed_claim_ids: list[str] = []
    decisions: list[dict] = []
    decision_notes: list[str] = []
    decision_models: list[str] = []
    for batch_index, decision_batch in enumerate(decision_batches, start=1):
        batch_json = json.dumps(decision_batch, ensure_ascii=False)
        print(
            f"   🔐 [Buying Potential Gate Decision] batch={batch_index}/{len(decision_batches)} "
            f"packet_chars={len(batch_json)} approx_tokens={max(1, len(batch_json) // 4)} "
            f"claims={len(_list(decision_batch.get('claims')))}",
            flush=True,
        )
        batch_review, batch_model = _call_gate_stage(
            _gate_prompt_for_packet(decision_batch),
            decision_policy.get("chain") or buying_gate_chain(model_mode),
            policy=decision_policy,
            schema=_BUYING_GATE_DECISION_SCHEMA,
            schema_name="buying_insight_gate_decision",
            log_label=f"Buying Insight Gate Decision {batch_index}/{len(decision_batches)}",
            validator=lambda value, current_batch=decision_batch: _gate_decision_is_complete(_dict(value), current_batch),
        )
        reviewed_claim_ids.extend(str(item) for item in _list(batch_review.get("reviewed_claim_ids")) if item)
        decisions.extend(_dict(item) for item in _list(batch_review.get("claim_decisions")))
        decision_notes.extend(str(item) for item in _list(batch_review.get("notes")) if item)
        decision_models.append(batch_model)
    decision_model = ", ".join(dict.fromkeys(decision_models))
    claim_lookup = _gate_claim_lookup(packet)
    field_actions: list[dict] = []
    downgrades: list[dict] = []
    blocked_claims: list[str] = []
    notes = decision_notes
    rewrite_decisions: list[dict] = []
    reanalysis_items: list[dict] = []

    for decision in decisions:
        claim = claim_lookup.get(str(decision.get("claim_id") or ""))
        if not claim:
            continue
        kind = str(decision.get("decision") or "")
        reason = _clip(decision.get("reason"), 300)
        if kind == "downgrade":
            downgrades.append({
                "claim_id": claim.get("claim_id"),
                "path": _gate_parent_path(str(claim.get("path") or "")),
                "reason": reason,
                "new_gate": _normalize_gate(decision.get("new_gate"), "allowed_as_discovery_question"),
            })
        elif kind == "remove":
            field_actions.append(_normalize_repair_action(
                {"action": "remove", "reason": reason}, claim, default_reason=reason,
            ))
            blocked_claims.append(_clean_generated_text(decision.get("unsafe_text") or claim.get("text")))
        elif kind == "rewrite":
            rewrite_decisions.append(decision)
        elif kind == "reanalysis_required":
            reanalysis_items.append(_normalize_repair_action(
                {"action": "reanalysis_required", "reason": reason}, claim, default_reason=reason,
            ))

    repair_models: list[str] = []
    if rewrite_decisions:
        repair_policy = policy_for("buying_insight_gate_repair")
        repair_batches = _gate_repair_batches(
            packet,
            rewrite_decisions,
            max_items=_safe_int(repair_policy.get("max_batch_items"), 5),
            max_chars=_safe_int(repair_policy.get("max_batch_chars"), 24000),
        )
        print(
            f"   ✍️ [Buying Potential Gate Repair] batches={len(repair_batches)} "
            f"claims={len(rewrite_decisions)} primary={tuple(repair_policy.get('chain') or ())[:1]}",
            flush=True,
        )
        for batch_index, repair_batch in enumerate(repair_batches, start=1):
            expected_ids = {str(item.get("claim_id")) for item in repair_batch}

            def repair_complete(value: dict, current_ids=expected_ids) -> tuple[bool, str]:
                repairs = [_dict(item) for item in _list(_dict(value).get("repairs"))]
                repaired_ids = {str(item.get("claim_id")) for item in repairs}
                if repaired_ids != current_ids:
                    return False, "Repair coverage does not match flagged claim IDs."
                for raw_repair in repairs:
                    issue = _gate_repair_contract_issue(packet, raw_repair)
                    if issue:
                        return False, issue
                return True, ""

            repair_prompt = _gate_repair_prompt(packet, repair_batch)
            print(
                f"   ✍️ [Buying Potential Gate Repair] batch={batch_index}/{len(repair_batches)} "
                f"packet_chars={len(repair_prompt)} approx_tokens={max(1, len(repair_prompt) // 4)} "
                f"claims={len(repair_batch)}",
                flush=True,
            )
            try:
                repaired, repair_model = _call_gate_stage(
                    repair_prompt,
                    repair_policy.get("chain"),
                    policy=repair_policy,
                    schema=_BUYING_GATE_REPAIR_SCHEMA,
                    schema_name="buying_insight_gate_wording_repair",
                    log_label=f"Buying Insight Gate Wording Repair {batch_index}/{len(repair_batches)}",
                    validator=repair_complete,
                )
                repair_models.append(repair_model)
                notes.extend(_list(repaired.get("notes")))
                for raw in _list(repaired.get("repairs")):
                    raw = _dict(raw)
                    claim = claim_lookup.get(str(raw.get("claim_id") or ""))
                    if not claim:
                        continue
                    action = _normalize_repair_action(raw, claim)
                    if action["action"] == "reanalysis_required":
                        reanalysis_items.append(action)
                    else:
                        field_actions.append(action)
                        if action["action"] == "remove":
                            blocked_claims.append(_clean_generated_text(claim.get("text")))
            except Exception as exc:
                notes.append(
                    f"Bounded wording repair batch {batch_index} was unavailable; "
                    f"its unsafe wording was removed: {str(exc)[:160]}"
                )
                for decision in repair_batch:
                    claim = claim_lookup.get(str(decision.get("claim_id") or ""))
                    if not claim:
                        continue
                    field_actions.append(_normalize_repair_action(
                        {"action": "remove", "reason": decision.get("reason")}, claim,
                    ))
                    blocked_claims.append(_clean_generated_text(claim.get("text")))

    reanalysis_models: list[str] = []
    if reanalysis_items:
        reanalysis_policy = dict(policy_for("buying_insight_gate_reanalysis"))
        if any(
            str(item.get("path") or "").startswith(("account_snapshot", "buying_potential_thesis", "sales_spine"))
            for item in reanalysis_items
        ):
            reanalysis_policy["effort"] = "high"
        expected_ids = {str(item.get("claim_id")) for item in reanalysis_items}

        def reanalysis_complete(value: dict) -> tuple[bool, str]:
            repaired_ids = {str(item.get("claim_id")) for item in _list(_dict(value).get("repairs"))}
            return (repaired_ids == expected_ids, "Focused reanalysis coverage does not match flagged claim IDs.")

        try:
            rebuilt, reanalysis_model = _call_gate_stage(
                _gate_reanalysis_prompt(packet, reanalysis_items),
                reanalysis_policy.get("chain"),
                policy=reanalysis_policy,
                schema=_BUYING_GATE_REANALYSIS_SCHEMA,
                schema_name="buying_insight_gate_focused_reanalysis",
                log_label="Buying Insight Gate Focused Reanalysis",
                validator=reanalysis_complete,
            )
            reanalysis_models.append(reanalysis_model)
            notes.extend(_list(rebuilt.get("notes")))
            reanalysis_items = []
            for raw in _list(rebuilt.get("repairs")):
                raw = _dict(raw)
                claim = claim_lookup.get(str(raw.get("claim_id") or ""))
                if claim:
                    action = _normalize_repair_action(raw, claim)
                    if action["action"] in {"replace", "remove"}:
                        field_actions.append(action)
                    if action["action"] == "remove":
                        blocked_claims.append(_clean_generated_text(claim.get("text")))
        except Exception as exc:
            notes.append(f"Focused reanalysis unavailable; unsafe wording was removed deterministically: {str(exc)[:180]}")

    resolved_ids = {str(item.get("claim_id")) for item in field_actions}
    resolved_ids.update(str(item.get("claim_id")) for item in downgrades)
    flagged_ids = {str(item.get("claim_id")) for item in decisions}
    unresolved_ids = sorted(flagged_ids - resolved_ids)
    deterministic_removals = 0
    for claim_id in unresolved_ids:
        claim = claim_lookup.get(claim_id)
        if not claim:
            continue
        field_actions.append(_normalize_repair_action(
            {"action": "remove", "reason": "Unresolved flagged wording removed by final deterministic safety check."},
            claim,
        ))
        blocked_claims.append(_clean_generated_text(claim.get("text")))
        deterministic_removals += 1

    status = "changes_required" if decisions else "approved"
    result = _normalize_gate_review({
        "status": status,
        "reviewed_claim_ids": list(dict.fromkeys(reviewed_claim_ids)),
        "claim_decisions": decisions,
        "required_downgrades": downgrades,
        "field_actions": field_actions,
        "blocked_claims": list(dict.fromkeys(item for item in blocked_claims if item)),
        "unresolved_claim_ids": [],
        "deterministically_removed_claim_ids": unresolved_ids if deterministic_removals else [],
        "deterministic_removal_count": deterministic_removals,
        "notes": notes,
        "decision_model": decision_model,
        "repair_models": repair_models,
        "reanalysis_models": reanalysis_models,
    }, model=decision_model)
    print(
        f"   🔐 [Buying Potential Gate] status={status} decision_model={decision_model} "
        f"decisions={len(decisions)} actions={len(field_actions)} downgrades={len(downgrades)} "
        f"deterministic_removals={deterministic_removals} "
        f"elapsed_ms={int((time.time() - decision_started) * 1000)}",
        flush=True,
    )
    return result


def _normalize_gate(value: Any, fallback: str = "allowed_as_hypothesis") -> str:
    gate = str(value or "").strip().lower()
    return gate if gate in ALLOWED_GATES else fallback


def _apply_gate_field_actions(report: dict, actions: Any) -> tuple[list[dict], list[dict]]:
    allowed_sections = {
        "account_snapshot",
        "buying_potential_thesis",
        "sales_spine",
        "pressure_signals",
        "why_now",
        "pain_to_solution_map",
        "recommended_sales_plays",
        "stakeholder_map",
        "evidence_confidence",
    }
    allowed_fields = {
        "text", "statement", "rationale", "account_thesis", "strategic_synthesis_basis",
        "description", "trigger", "interpretation", "business_consequence", "why_it_matters_now",
        "safe_discovery_question", "safe_customer_wording", "opening_angle", "executive_angle",
        "technical_angle", "buying_hypothesis", "business_hypothesis", "why_relevant",
        "trigger_to_offer_bridge", "proof_point", "first_meeting_objective", "service_fit_rationale",
        "why_this_service", "need_hypothesis",
        "current_condition", "future_pressure", "market_context", "company_context",
        "combined_activation_hypothesis", "likely_business_consequence", "what_to_propose",
        "top_buying_hypotheses", "hypothesis",
    }
    applied: list[dict] = []
    rejected: list[dict] = []
    for raw_action in _list(actions):
        action = _dict(raw_action)
        path = str(action.get("path") or "").strip()
        operation = str(action.get("action") or "").strip().lower()
        tokens = re.findall(r"([a-zA-Z_]+)|\[(\d+)\]", path)
        rebuilt_path = "".join(
            (f".{name}" if offset and name else name) if name else f"[{index}]"
            for offset, (name, index) in enumerate(tokens)
        )
        if not tokens or rebuilt_path != path or operation not in {"replace", "remove"}:
            rejected.append({**action, "rejection_reason": "Invalid or unsupported field action path."})
            continue
        section = tokens[0][0]
        named_tokens = [name for name, _index in tokens[1:] if name]
        if section not in allowed_sections or any(field not in allowed_fields for field in named_tokens):
            rejected.append({**action, "rejection_reason": "Field is outside the verifier allowlist."})
            continue
        current: Any = report
        owner: dict | None = None
        parent: Any = None
        target_key: Any = None
        resolution_failed = False
        for name, index_text in tokens:
            parent = current
            target_key = name if name else _safe_int(index_text, -1)
            if name:
                if not isinstance(current, dict) or name not in current:
                    resolution_failed = True
                    break
                current = current[name]
            else:
                index = _safe_int(index_text, -1)
                if not isinstance(current, list) or not (0 <= index < len(current)):
                    resolution_failed = True
                    break
                current = current[index]
                if isinstance(current, dict):
                    owner = current
        if resolution_failed or parent is None:
            rejected.append({**action, "rejection_reason": "Target object does not exist."})
            continue
        if owner is None and isinstance(report.get(section), dict):
            owner = report.get(section)
        object_id = str(action.get("object_id") or "").strip()
        owner = _dict(owner)
        target_ids = set(_item_signal_ids(owner) + _text_signal_ids(json.dumps(owner, ensure_ascii=False)))
        target_ids.update(str(value) for value in _list(owner.get("linked_hypothesis_ids")) if value)
        if owner.get("hypothesis_id"):
            target_ids.add(str(owner.get("hypothesis_id")))
        claim_id = str(action.get("claim_id") or "").strip()
        if not claim_id and object_id and object_id not in target_ids and object_id not in {section, str(owner.get("id") or "")}:
            rejected.append({**action, "rejection_reason": "object_id does not match the targeted report object."})
            continue
        if isinstance(parent, dict):
            original_value = parent.get(target_key)
        elif isinstance(parent, list) and isinstance(target_key, int) and 0 <= target_key < len(parent):
            original_value = parent[target_key]
        else:
            rejected.append({**action, "rejection_reason": "Resolved target is not writable."})
            continue
        original_text = _clean_generated_text(original_value)
        replacement = "" if operation == "remove" else _clean_generated_text(action.get("replacement"))
        if isinstance(parent, dict):
            parent[target_key] = replacement
        else:
            parent[target_key] = replacement
        applied.append({
            "claim_id": claim_id,
            "object_id": object_id,
            "path": path,
            "action": operation,
            "original_text": original_text,
            "replacement": replacement,
            "reason": _clip(action.get("reason"), 260),
        })
    return applied, rejected


def _propagate_gate_field_actions(report: dict, applied_actions: list[dict]) -> int:
    """Apply an approved correction to repeated render occurrences of the same claim."""
    replacements = []
    for action in applied_actions:
        original = _clean_generated_text(action.get("original_text"))
        if len(original) < 18:
            continue
        replacements.append((original, _clean_generated_text(action.get("replacement"))))
    if not replacements:
        return 0
    public_keys = (
        "account_snapshot", "buying_potential_thesis", "sales_spine", "pressure_signals",
        "why_now", "pain_to_solution_map", "recommended_sales_plays", "stakeholder_map",
    )
    changed = 0

    def replace(value: Any) -> Any:
        nonlocal changed
        if isinstance(value, dict):
            return {key: replace(item) for key, item in value.items()}
        if isinstance(value, list):
            return [replace(item) for item in value]
        if not isinstance(value, str):
            return value
        result = value
        for original, replacement in replacements:
            pattern = re.compile(re.escape(original), re.I)
            result, count = pattern.subn(replacement, result)
            changed += count
        return _clean_generated_text(result)

    for key in public_keys:
        if key in report:
            report[key] = replace(report[key])
    return changed


def _normalized_claim_words(value: Any) -> list[str]:
    stop = {
        "the", "and", "that", "this", "with", "from", "into", "for", "are", "was",
        "were", "has", "have", "had", "not", "but", "may", "could", "would", "should",
    }
    return [
        word for word in re.findall(r"[a-z0-9åäö]+", _clean_generated_text(value).lower())
        if len(word) > 2 and word not in stop
    ]


def _blocked_claim_matches(candidate: str, blocked_claim: str) -> bool:
    candidate_words = _normalized_claim_words(candidate)
    blocked_words = _normalized_claim_words(blocked_claim)
    if len(blocked_words) < 3 or len(candidate_words) < 3:
        return False
    candidate_norm = " ".join(candidate_words)
    blocked_norm = " ".join(blocked_words)
    if blocked_norm in candidate_norm or (len(candidate_words) >= 6 and candidate_norm in blocked_norm):
        return True
    candidate_set = set(candidate_words)
    blocked_set = set(blocked_words)
    intersection = len(candidate_set & blocked_set)
    union = len(candidate_set | blocked_set)
    smaller = min(len(candidate_set), len(blocked_set))
    return (
        intersection >= 4
        and smaller > 0
        and intersection / smaller >= 0.80
        and (union == 0 or intersection / union >= 0.55)
    )


def _remove_blocked_claims_from_public_sections(report: dict, blocked_claims: list[str]) -> int:
    claims = [_clean_generated_text(claim) for claim in blocked_claims if len(_clean_generated_text(claim)) >= 18]
    if not claims:
        return 0
    public_keys = (
        "account_snapshot", "buying_potential_thesis", "sales_spine", "pressure_signals",
        "why_now", "pain_to_solution_map", "recommended_sales_plays", "stakeholder_map",
    )
    removed = 0

    def clean(value: Any) -> Any:
        nonlocal removed
        if isinstance(value, dict):
            return {key: clean(item) for key, item in value.items()}
        if isinstance(value, list):
            cleaned = [clean(item) for item in value]
            return [item for item in cleaned if item not in (None, "", [], {})]
        if not isinstance(value, str):
            return value
        result = value
        for claim in claims:
            pattern = re.compile(re.escape(claim), re.I)
            result, count = pattern.subn("", result)
            removed += count
            if count:
                continue
            sentences = re.split(r"(?<=[.!?])\s+", result)
            kept = []
            for sentence in sentences:
                if _blocked_claim_matches(sentence, claim):
                    removed += 1
                    continue
                kept.append(sentence)
            result = " ".join(kept)
        return _clean_generated_text(result)

    for key in public_keys:
        if key in report:
            report[key] = clean(report[key])
    return removed


_LANGUAGE_PUBLIC_KEYS = (
    "account_snapshot",
    "buying_potential_thesis",
    "sales_spine",
    "pressure_signals",
    "why_now",
    "pain_to_solution_map",
    "recommended_sales_plays",
    "stakeholder_map",
)

_LANGUAGE_PROSE_FIELDS = {
    "text", "account_thesis", "strategic_synthesis_basis", "hypothesis", "rationale",
    "description", "trigger", "business_consequence", "buyer_role_concern",
    "why_it_matters_now", "safe_discovery_question", "interpreted_pressure", "buyer_lens",
    "why_this_service", "proof_point", "safe_wording", "missing_evidence",
    "buying_hypothesis", "business_hypothesis", "why_relevant", "trigger_to_offer_bridge",
    "first_meeting_objective", "safe_customer_wording", "opening_angle", "executive_angle",
    "technical_angle", "relevance_rationale", "current_condition", "future_pressure",
    "market_context", "company_context", "combined_activation_hypothesis",
    "likely_business_consequence", "what_to_propose", "what_to_do", "what_progress_shows",
    "validation_question", "falsifier", "service_fit_rationale",
}

_LANGUAGE_TITLE_FIELDS = {
    "play_name", "signal_type", "pressure_type", "theme", "service_or_offer", "service",
}

_LANGUAGE_LIST_FIELDS = {
    "paragraphs", "top_buying_hypotheses", "discovery_questions", "qualification_upgrade_criteria",
}


def _public_path_tokens(path: str) -> list[tuple[str, str]]:
    return re.findall(r"([a-zA-Z_]+)|\[(\d+)\]", str(path or ""))


def _public_path_value(report: dict, path: str) -> Any:
    current: Any = report
    for name, index_text in _public_path_tokens(path):
        if name:
            if not isinstance(current, dict) or name not in current:
                return None
            current = current[name]
        else:
            index = _safe_int(index_text, -1)
            if not isinstance(current, list) or not (0 <= index < len(current)):
                return None
            current = current[index]
    return current


def _set_public_path_value(report: dict, path: str, value: Any) -> bool:
    tokens = _public_path_tokens(path)
    if not tokens or tokens[0][0] not in _LANGUAGE_PUBLIC_KEYS:
        return False
    current: Any = report
    for offset, (name, index_text) in enumerate(tokens):
        final = offset == len(tokens) - 1
        key: Any = name if name else _safe_int(index_text, -1)
        if isinstance(current, dict) and isinstance(key, str) and key in current:
            if final:
                current[key] = value
                return True
            current = current[key]
        elif isinstance(current, list) and isinstance(key, int) and 0 <= key < len(current):
            if final:
                current[key] = value
                return True
            current = current[key]
        else:
            return False
    return False


def _language_issue_codes(path: str, value: Any) -> list[str]:
    text = _clean_generated_text(value)
    if not text:
        return []
    field = next((name for name, _index in reversed(_public_path_tokens(path)) if name), "")
    issues: list[str] = []
    lower = text.lower()
    if re.search(r"\bwhether\s+(?:because|if)\b", text, re.I):
        issues.append("malformed_whether_clause")
    if re.search(r"\b(?:and|or|because|whether|if|while|although|but|given that|with|to|for|of)\s*[.!?]?$", text, re.I):
        issues.append("trailing_connector")
    if re.search(r"\bgiven that no separate external pressure\s*[.!?]?$", lower):
        issues.append("incomplete_causal_clause")
    if text.count("(") != text.count(")") or text.count("[") != text.count("]"):
        issues.append("unbalanced_delimiter")
    if re.match(r"^(?:sales hypothesis|account-specific question|model fallback|deterministic fallback)\s*:", text, re.I):
        issues.append("internal_prefix")
    if field in _LANGUAGE_TITLE_FIELDS and (text.endswith("...") or re.search(r"\b(?:and|or|with|to|for|of)\s*$", text, re.I)):
        issues.append("truncated_title")
    if field in _LANGUAGE_PROSE_FIELDS and len(text) >= 28 and not re.search(r"[.!?]$", text):
        issues.append("missing_sentence_ending")
    return list(dict.fromkeys(issues))


def _language_public_items(report: dict) -> list[dict]:
    items: list[dict] = []

    def visit(value: Any, path: str, parent_field: str = ""):
        if isinstance(value, dict):
            for key, child in value.items():
                child_path = f"{path}.{key}" if path else str(key)
                visit(child, child_path, str(key))
            return
        if isinstance(value, list):
            for index, child in enumerate(value):
                visit(child, f"{path}[{index}]", parent_field)
            return
        if not isinstance(value, str):
            return
        field = next((name for name, _index in reversed(_public_path_tokens(path)) if name), parent_field)
        if field not in _LANGUAGE_PROSE_FIELDS and field not in _LANGUAGE_TITLE_FIELDS and parent_field not in _LANGUAGE_LIST_FIELDS:
            return
        issues = _language_issue_codes(path, value)
        if not issues:
            return
        qa_key = f"{path}|{_clean_generated_text(value)}".encode("utf-8")
        items.append({
            "qa_id": f"LQA-{hashlib.sha256(qa_key).hexdigest()[:12].upper()}",
            "path": path,
            "text": _clean_generated_text(value),
            "issues": issues,
        })

    for key in _LANGUAGE_PUBLIC_KEYS:
        if key in report:
            visit(report[key], key, key)
    return items


def _deterministic_language_cleanup(path: str, value: Any) -> str:
    text = _clean_generated_text(value)
    if not text:
        return ""
    text = re.sub(
        r"\b(The qualification (?:issue|question) is) whether because\b",
        r"\1 that",
        text,
        flags=re.I,
    )
    text = re.sub(r"\bwhether\s+if\b", "whether", text, flags=re.I)
    text = re.sub(r"^(?:Sales hypothesis|Model fallback|Deterministic fallback)\s*:\s*", "", text, flags=re.I)
    text = re.sub(r"^Account-specific question\s*:\s*whether\s+", "Validate whether ", text, flags=re.I)
    field = next((name for name, _index in reversed(_public_path_tokens(path)) if name), "")
    issues = _language_issue_codes(path, text)
    if field in _LANGUAGE_PROSE_FIELDS and issues == ["missing_sentence_ending"]:
        text = text.rstrip(" ,;:-") + "."
    return _clean_generated_text(text)


def _language_qa_prompt(items: list[dict]) -> str:
    return f"""
Repair only the grammar and sentence completeness of these final Buying Insight fields.
Return only JSON matching the supplied schema.

Rules:
- Preserve each sentence's meaning, evidence boundary, entity scope, uncertainty, linked IDs, and allowed-use posture.
- Do not add facts, evidence, services, timing, owners, competitors, causal claims, or strategic analysis.
- Produce exactly one repair per qa_id and keep path unchanged.
- Use replace for a complete fluent correction. Use remove only when the fragment cannot be repaired without inventing meaning.
- Titles must be concise noun phrases. Prose must be a complete reader-facing sentence.
- Remove internal generation labels and do not emit markdown.

Flagged final fields:
{json.dumps(items, ensure_ascii=False)}
""".strip()


def _language_qa_batches(items: list[dict], *, max_items: int = 8, max_chars: int = 20000) -> list[list[dict]]:
    batches: list[list[dict]] = []
    current: list[dict] = []
    for item in items:
        candidate = current + [item]
        if current and (len(candidate) > max_items or len(_language_qa_prompt(candidate)) > max_chars):
            batches.append(current)
            current = [item]
        else:
            current = candidate
    if current:
        batches.append(current)
    return batches


def _prune_empty_public_labels(report: dict) -> int:
    removable_fields = {"safe_wording", "safe_customer_wording", "opening_angle", "executive_angle", "technical_angle"}
    removed = 0

    def prune(value: Any) -> Any:
        nonlocal removed
        if isinstance(value, dict):
            cleaned = {}
            for key, child in value.items():
                if key in removable_fields and not _clean_generated_text(child):
                    removed += 1
                    continue
                cleaned[key] = prune(child)
            return cleaned
        if isinstance(value, list):
            return [prune(item) for item in value]
        return value

    for key in _LANGUAGE_PUBLIC_KEYS:
        if key in report:
            report[key] = prune(report[key])
    return removed


def _run_final_language_qa(report: dict, gate_review: dict | None = None, *, run_model: bool = True) -> dict:
    initial_items = _language_public_items(report)
    for item in initial_items:
        cleaned = _deterministic_language_cleanup(item["path"], item["text"])
        _set_public_path_value(report, item["path"], cleaned)

    remaining = _language_public_items(report)
    model_repairs = 0
    model_failures = 0
    qa_models: list[str] = []
    qa_notes: list[str] = []
    if remaining and run_model:
        policy = policy_for("buying_insight_language_qa")
        batches = _language_qa_batches(
            remaining,
            max_items=_safe_int(policy.get("max_batch_items"), 8),
            max_chars=_safe_int(policy.get("max_batch_chars"), 20000),
        )
        for batch_index, batch in enumerate(batches, start=1):
            expected = {str(item.get("qa_id")): item for item in batch}

            def language_complete(value: dict, current=expected) -> tuple[bool, str]:
                repairs = [_dict(item) for item in _list(_dict(value).get("repairs"))]
                repair_ids = {str(item.get("qa_id")) for item in repairs}
                if repair_ids != set(current):
                    return False, "Language QA coverage does not match flagged field IDs."
                for repair in repairs:
                    original = current[str(repair.get("qa_id"))]
                    if str(repair.get("path") or "") != str(original.get("path") or ""):
                        return False, "Language QA path does not match the flagged field."
                    replacement = _clean_generated_text(repair.get("replacement"))
                    if str(repair.get("action") or "") == "replace":
                        if len(replacement) < 8:
                            return False, "Language QA replacement is empty or too short."
                        if _stable_contract_ids(replacement) - _stable_contract_ids(original.get("text")):
                            return False, "Language QA introduced a new stable evidence or lineage ID."
                return True, ""

            try:
                fixed, used_model = _call_gate_stage(
                    _language_qa_prompt(batch),
                    policy.get("chain"),
                    policy=policy,
                    schema=_BUYING_LANGUAGE_QA_SCHEMA,
                    schema_name="buying_insight_final_language_qa",
                    log_label=f"Buying Insight Final Language QA {batch_index}/{len(batches)}",
                    validator=language_complete,
                )
                qa_models.append(used_model)
                qa_notes.extend(str(item) for item in _list(fixed.get("notes")) if item)
                for repair in _list(fixed.get("repairs")):
                    repair = _dict(repair)
                    replacement = "" if repair.get("action") == "remove" else _clean_generated_text(repair.get("replacement"))
                    if _set_public_path_value(report, str(repair.get("path") or ""), replacement):
                        model_repairs += 1
            except Exception as exc:
                model_failures += 1
                qa_notes.append(f"Final language QA batch {batch_index} was unavailable: {str(exc)[:160]}")

    unresolved = _language_public_items(report)
    deterministically_removed = 0
    for item in unresolved:
        text = _clean_generated_text(_public_path_value(report, item["path"]))
        complete_sentences = re.findall(r"[^.!?]+[.!?]", text)
        fallback = " ".join(sentence.strip() for sentence in complete_sentences).strip()
        if fallback and not _language_issue_codes(item["path"], fallback):
            _set_public_path_value(report, item["path"], fallback)
        else:
            _set_public_path_value(report, item["path"], "")
            deterministically_removed += 1

    empty_fields_removed = _prune_empty_public_labels(report)
    blocked_recurrences_removed = _remove_blocked_claims_from_public_sections(
        report,
        [_clean_generated_text(item) for item in _list(_dict(gate_review).get("blocked_claims")) if _clean_generated_text(item)],
    )
    final_issues = _language_public_items(report)
    status = "clean" if not initial_items else "repaired" if not final_issues else "warning"
    report["language_quality"] = {
        "status": status,
        "initial_issue_count": len(initial_items),
        "model_repair_count": model_repairs,
        "model_failure_count": model_failures,
        "deterministic_removal_count": deterministically_removed,
        "empty_field_count": empty_fields_removed,
        "blocked_recurrence_removal_count": blocked_recurrences_removed,
        "remaining_issue_count": len(final_issues),
        "models": list(dict.fromkeys(qa_models)),
        "notes": qa_notes[:8],
    }
    if initial_items or empty_fields_removed or blocked_recurrences_removed:
        evidence = _dict(report.get("evidence_confidence"))
        quality_notes = _list(evidence.get("report_quality_notes"))
        quality_notes.append(
            f"Final language QA repaired {model_repairs} field(s), removed {deterministically_removed} unresolved fragment(s), "
            f"and suppressed {empty_fields_removed} empty public field(s)."
        )
        evidence["report_quality_notes"] = list(dict.fromkeys(quality_notes))[:12]
        report["evidence_confidence"] = evidence
    print(
        f"   ✨ [Buying Potential Language QA] status={status} initial={len(initial_items)} "
        f"model_repairs={model_repairs} removed={deterministically_removed} remaining={len(final_issues)}",
        flush=True,
    )
    return report


def _has_basis(item: dict) -> bool:
    basis = item.get("evidence_basis")
    if isinstance(basis, list):
        return any(str(x).startswith(EVIDENCE_BASIS_PREFIXES) for x in basis)
    return bool(basis)


def _item_signal_ids(item: dict) -> list[str]:
    values = []
    for key in ("account_signal_id", "linked_signal_ids", "source_signal_ids", "evidence_basis"):
        value = item.get(key)
        if isinstance(value, list):
            values.extend(str(part) for part in value)
        elif value:
            values.append(str(value))
    return sorted({value for value in values if value.startswith("SIG-")})


def _has_signal_reference(item: dict, known_signal_ids: set[str]) -> bool:
    signal_ids = _item_signal_ids(item)
    return any(signal_id in known_signal_ids for signal_id in signal_ids)


def _known_service_names(payload: dict) -> set[str]:
    names = set()
    for svc in _list(payload.get("services")):
        if isinstance(svc, dict):
            for key in ("name", "service_id"):
                value = str(svc.get(key) or "").strip().lower()
                if value:
                    names.add(value)
    return names


def _service_match_exists(item: dict, service_names: set[str]) -> bool:
    if not service_names:
        return False
    explicit = {
        str(item.get(key) or "").strip().lower()
        for key in ("service_id", "service", "service_or_offer")
        if str(item.get(key) or "").strip()
    }
    return any(value in service_names for value in explicit)


def _classify_play_score(score: int) -> str:
    if score >= 80:
        return "strong_account_play"
    if score >= 60:
        return "qualified_discovery_play"
    if score >= 40:
        return "monitor_soft_discovery"
    if score >= 20:
        return "weak_signal"
    return "do_not_use"


def _signal_evidence_quality(signal: dict | None) -> int:
    signal = _dict(signal)
    if not signal:
        return 0
    base = {
        "allowed_as_fact": 18,
        "allowed_as_hypothesis": 14,
        "allowed_as_discovery_question": 9,
        "internal_only": 5,
        "blocked": 0,
    }.get(_normalize_gate(signal.get("gate")), 5)
    parent_count = len(_list(signal.get("parent_evidence_ids"))) + len(_list(signal.get("parent_foresight_ids")))
    independence = min(4, 2 * _safe_int(signal.get("independent_source_count"), 0))
    freshness = 2 if str(signal.get("source_freshness") or "").lower() in {"fresh", "current", "recent"} else 0
    return min(25, base + min(3, parent_count) + independence + freshness)


def _score_play(
    play: dict,
    *,
    has_signal: bool,
    has_service: bool,
    has_question: bool,
    signal: dict | None = None,
) -> int:
    evidence_strength = _signal_evidence_quality(signal) if signal else (25 if has_signal else 0)
    business_pressure = 20 if _clip(play.get("business_hypothesis") or play.get("why_relevant"), 80) else 5
    buyer_relevance = 15 if _clip(play.get("primary_buyer") or play.get("target_stakeholder"), 80) else 0
    timing_strength = 15 if _has_current_timing_basis(signal, play) else 7 if _clip(play.get("trigger"), 80) else 3
    vivicta_fit = 15 if has_service else 0
    safe_outreach = 10 if has_question else 0
    return evidence_strength + business_pressure + buyer_relevance + timing_strength + vivicta_fit + safe_outreach


def _looks_unsafe_claim(text: Any) -> bool:
    token = str(text or "").lower()
    if any(
        phrase in token
        for phrase in (
            "do not",
            "don't",
            "wrong-entity",
            "wrong entity",
            "blocked",
            "unsupported",
            "false",
            "fabrication",
            "confirmed buying intent",
            "active deal",
            "active project",
            "guarantee",
            "guarantees",
            "roi",
            "group revenue",
            "poor ai maturity",
            "has deployed ai",
            "has deployed data-platform",
            "has deployed automation",
            "has deployed ml",
            "has a confirmed modernization program",
        )
    ):
        return True
    return any(
        token.startswith(prefix)
        for prefix in (
            "present the hypotheses as established facts",
            "present hypotheses as established facts",
            "claim a current buying project exists",
            "claim current buying intent",
            "state that competitors are definitively ahead",
            "state competitors are definitively ahead",
            "say that competitors are definitively ahead",
            "assert that ",
            "assert ",
        )
    )


def _humanize_report_quality_note(note: Any) -> str:
    text = _clean_generated_text(note)
    lower = text.lower()
    if not text:
        return ""
    if re.search(r"\bBuying Insight section\(s\) required JSON repair\b", text):
        return text
    if re.search(r"\bBuying Insight section\(s\) used deterministic fallback\b", text):
        return text
    if re.search(r"\bcompleted \d+ conversation field\(s\)\b", text):
        return text
    if "deterministic fallback" in lower:
        return "One generated section used deterministic fallback; treat the affected content as draft guidance."
    if any(marker in lower for marker in ("malformed", "json", "parse failed", "invalid json", "original parse error")):
        return "Some generated sections required JSON repair before saving; keep normal evidence guardrails in use."
    if "repair failed" in lower:
        return "One generated section could not be fully repaired; review affected wording before reuse."
    return text


def _previous_buying_report(previous_report: Any) -> dict:
    if not isinstance(previous_report, dict):
        return {}
    report = previous_report.get("report") if isinstance(previous_report.get("report"), dict) else previous_report
    return report if isinstance(report, dict) else {}


def _previous_conversation_directions_by_signal(previous_report: Any) -> dict[str, dict]:
    previous = _previous_buying_report(previous_report)
    by_signal: dict[str, dict] = {}
    for item in _list(previous.get("pain_to_solution_map")):
        if not isinstance(item, dict):
            continue
        for signal_id in _item_signal_ids(item):
            if signal_id and signal_id not in by_signal:
                by_signal[signal_id] = item
    return by_signal


def _looks_like_context_not_fact(text: Any) -> bool:
    lower = str(text or "").lower()
    return any(
        marker in lower
        for marker in (
            "coresignal public professional profile candidate",
            "public professional profile candidate",
            "matched demand groups",
            "stakeholder role relevance",
            "linkedin profile candidate",
            "people profile candidate",
        )
    )


def _summarize_contact_source_text(text: Any) -> str:
    raw = str(text or "")
    lower = raw.lower()
    if not any(marker in lower for marker in ("yhteystiedot", "contact", "postiosoite", "käyntiosoite", "kayntiosoite", "laskutusosoitteet")):
        return ""
    compact = re.sub(r"[#*_`]+", " ", raw)
    compact = re.sub(r"\s+", " ", compact).strip()
    labeled_address_match = re.search(
        r"(?:Käyntiosoite|Kayntiosoite|Visiting address)\s+(.{5,100}?)(?:\s+(?:Postiosoite|Laskutusosoitteet|Sähköpostiosoitteet|Puhelin|Phone|Y-tunnus|Business ID)\b|$)",
        compact,
        re.I,
    )
    compact_for_entity = re.sub(r"\b(?:Yhteystiedot|Contact details|Contacts|Contact)\b", " ", compact, flags=re.I)
    compact_for_entity = re.sub(r"\s+", " ", compact_for_entity).strip()
    company_match = re.search(
        r"\b(?:Oy\s+)?[A-ZÅÄÖ][A-Za-zÅÄÖåäö0-9& .'-]{2,70}\s+(?:Oy|Oyj|Ab|Ltd|Inc|GmbH)\b",
        compact_for_entity,
    )
    bid_match = re.search(r"\b(?:Y-tunnus|Business ID)\s*[:\-]?\s*([0-9]{7}-[0-9])\b", compact, re.I)
    address_match = re.search(
        r"\b([A-ZÅÄÖ][A-Za-zÅÄÖåäö .'-]+(?:tie|katu|kuja|polku|väylä|vayla)\s+\d+[A-Za-z]?(?:,\s*[A-ZÅÄÖ][A-Za-zÅÄÖåäö .'-]+)?)\b",
        compact,
        re.I,
    )
    parts: list[str] = []
    if company_match:
        parts.append(company_match.group(0).strip())
    if bid_match:
        parts.append(f"business ID {bid_match.group(1)}")
    address = ""
    if labeled_address_match:
        address = labeled_address_match.group(1).strip()
    elif address_match:
        address = address_match.group(1).strip()
    if address:
        parts.append(f"visiting address {address}")
    if not parts:
        return ""
    return "Official contact material lists " + ", ".join(parts) + "."


def _clean_fact_statement(text: Any, *, limit: int = 260) -> str:
    if _looks_like_context_not_fact(text):
        return ""
    contact_summary = _summarize_contact_source_text(text)
    if contact_summary:
        return _clip(contact_summary, limit)
    cleaned = re.sub(r"[#*_`]+", " ", str(text or ""))
    cleaned = re.sub(r"\b(?:Skip to content|Avaa valikko|Menu|Search)\b", " ", cleaned, flags=re.I)
    cleaned = _clean_generated_text(cleaned)
    if _looks_like_context_not_fact(cleaned):
        return ""
    return _clip(cleaned, limit)


def _is_meaningful_text(*values: Any, min_len: int = 18) -> bool:
    return any(len(_clip(value, 400)) >= min_len for value in values)


def _safe_guardrail_items(items: list[Any]) -> tuple[list[str], list[str]]:
    do_not_say: list[str] = []
    question_only: list[str] = []
    for raw in items:
        text = _clip(raw, 520)
        if not text:
            continue
        if _looks_unsafe_claim(text):
            do_not_say.append(text)
        else:
            question_only.append(text)
    return list(dict.fromkeys(do_not_say)), list(dict.fromkeys(question_only))


def _looks_like_verified_fact(text: str) -> bool:
    clean = str(text or "").strip()
    if not clean:
        return False
    lower = clean.lower()
    fact_markers = (
        "legal entity",
        "business id",
        "y-tunnus",
        "located in",
        "company material states",
        "company material describes",
        "available evidence points to",
        "according to",
        "recorded 2025 revenue",
        "operating profit",
        "operating margin",
        "signed new",
        "order book",
        "backlog",
        "cooperation agreement",
        "final assembly",
        "verified product",
        "is a finnish",
        "listed as",
    )
    question_markers = (
        "whether ",
        "how ",
        "what ",
        "where ",
        "ask",
        "do not assert",
        "stakeholder role relevance",
        "planned or deployed",
        "already exist",
    )
    return any(marker in lower for marker in fact_markers) and not any(marker in lower for marker in question_markers)


def _looks_question_led(text: Any) -> bool:
    clean = str(text or "").strip()
    if not clean:
        return False
    lower = clean.lower()
    return (
        clean.endswith("?")
        or lower.startswith(("how ", "what ", "where ", "which ", "whether ", "can ", "could ", "is ", "are ", "does ", "do "))
        or "?" in clean
        or "use as question" in lower
        or "ask " in lower
    )


def _evidence_bucket_text(item: Any, *, limit: int = 520) -> str:
    if isinstance(item, dict):
        for key in (
            "hypothesis",
            "statement",
            "text",
            "claim",
            "question",
            "safe_wording",
            "summary",
            "description",
            "reason",
            "title",
        ):
            text = _clip(item.get(key), limit)
            if text:
                return text
        return ""
    text = str(item or "").strip()
    if not text:
        return ""
    if text.startswith("{") and any(marker in text.lower() for marker in ("source", "url", "snippet", "quote", "evidence")):
        try:
            parsed = ast.literal_eval(text)
            if isinstance(parsed, dict):
                return _evidence_bucket_text(parsed, limit=limit)
        except Exception:
            return ""
    return _clip(text, limit)


def _clean_evidence_confidence_buckets(evidence_confidence: dict) -> dict:
    """Keep facts, hypotheses, questions, and blocked claims in separate buckets."""
    evidence_confidence = _dict(evidence_confidence).copy()
    omitted_fragments: list[str] = []

    def bucket_items(key: str) -> list[str]:
        cleaned: list[str] = []
        for item in _list(evidence_confidence.get(key)):
            raw = str(item or "").strip()
            text = _evidence_bucket_text(item)
            if text:
                cleaned.append(text)
            elif raw:
                omitted_fragments.append(f"Omitted malformed evidence-confidence item from {key}.")
        return cleaned

    verified = bucket_items("verified_facts")
    questions = bucket_items("use_only_as_question")
    safe_hypotheses = bucket_items("safe_hypotheses")
    analysis_context = bucket_items("analysis_context")
    weak_or_missing: list[str] = []
    report_quality_notes = [
        _humanize_report_quality_note(item)
        for item in _list(evidence_confidence.get("report_quality_notes"))
        if _humanize_report_quality_note(item)
    ]
    report_quality_notes.extend(omitted_fragments)
    for item in _list(evidence_confidence.get("weak_or_missing_evidence")):
        text = str(item or "").strip()
        if not text:
            continue
        lower = text.lower()
        if any(marker in lower for marker in ("deterministic fallback", "malformed", "json", "section_", "parse failed", "repair failed")):
            note = _humanize_report_quality_note(text)
            if note:
                report_quality_notes.append(note)
            continue
        if "verified_for_analysis" in lower:
            context_text = text.replace(": verified_for_analysis", "").replace("verified_for_analysis", "").strip(" :-")
            if context_text and context_text.lower() not in {"source", "source label", "verified", "analysis"}:
                analysis_context.append(context_text)
            continue
        weak_or_missing.append(text)

    do_not_say, question_from_blocked = _safe_guardrail_items(
        _list(evidence_confidence.get("do_not_say"))
        + _list(evidence_confidence.get("do_not_use_claims"))
    )
    questions = list(dict.fromkeys(questions + question_from_blocked))
    routed_questions: list[str] = []
    for question in questions:
        if _looks_unsafe_claim(question):
            do_not_say.append(question)
        elif _looks_like_verified_fact(question):
            verified.append(question)
        elif _looks_question_led(question):
            routed_questions.append(question)
        else:
            safe_hypotheses.append(question)
    questions = routed_questions

    evidence_confidence["verified_facts"] = [
        item for item in list(dict.fromkeys(_clean_fact_statement(item, limit=520) for item in verified)) if item
    ][:12]
    evidence_confidence["safe_hypotheses"] = list(dict.fromkeys(safe_hypotheses))[:12]
    evidence_confidence["use_only_as_question"] = list(dict.fromkeys(questions))[:12]
    evidence_confidence["analysis_context"] = list(dict.fromkeys(analysis_context))[:12]
    evidence_confidence["weak_or_missing_evidence"] = list(dict.fromkeys(weak_or_missing))[:12]
    evidence_confidence["report_quality_notes"] = list(dict.fromkeys(report_quality_notes))[:12]
    evidence_confidence["do_not_say"] = list(dict.fromkeys(do_not_say))[:12]
    evidence_confidence["do_not_use_claims"] = evidence_confidence["do_not_say"]
    return evidence_confidence


def _signal_by_id(payload: dict) -> dict[str, dict]:
    return {
        str(signal.get("id")): signal
        for signal in _list(payload.get("account_signal_map"))
        if isinstance(signal, dict) and signal.get("id")
    }


def _activation_signal_ids(payload: dict) -> set[str]:
    return {
        signal_id
        for signal_id, signal in _signal_by_id(payload).items()
        if signal.get("source") == "scanner_activation_pair" or signal.get("activation_pair_index")
    }


def _word_count(value: Any) -> int:
    return len(re.findall(r"\b\w+\b", str(value or "")))


def _format_top_buying_hypothesis_item(item: Any) -> str:
    if isinstance(item, str) and item.strip().startswith("{"):
        try:
            parsed = ast.literal_eval(item)
            if isinstance(parsed, dict):
                item = parsed
        except Exception:
            pass
    if isinstance(item, dict):
        title = _first_nonempty(
            item.get("hypothesis"),
            item.get("title"),
            item.get("theme"),
            item.get("buying_hypothesis"),
            limit=140,
        )
        activation_pair = _first_nonempty(item.get("activation_pair"), item.get("signal_id"), item.get("id"), limit=40)
        rationale = _first_nonempty(
            item.get("why"),
            item.get("combined_activation_hypothesis"),
            item.get("company_context"),
            item.get("market_context"),
            item.get("what_to_do"),
            limit=420,
        )
        if not title and not rationale:
            return ""
        label = title or "Buying hypothesis"
        if activation_pair and activation_pair.lower() not in label.lower():
            label = f"{label} ({activation_pair})"
        return _clip(f"{label}: {rationale}" if rationale else label, 560)
    text = _clean_generated_text(item)
    if re.match(r"^\{['\"]?hypothesis['\"]?\s*:", text, re.I):
        return ""
    return _clip(text, 560)


def _activation_hypothesis_items(payload: dict, limit: int = 3) -> list[str]:
    items: list[str] = []
    for signal in _list(payload.get("account_signal_map")):
        if not isinstance(signal, dict):
            continue
        if signal.get("source") != "scanner_activation_pair" and not signal.get("activation_pair_index"):
            continue
        title = _first_nonempty(signal.get("theme"), signal.get("account_movement"), limit=140)
        signal_id = str(signal.get("id") or "").strip()
        why = _first_nonempty(signal.get("why"), signal.get("commercial_pressure"), signal.get("business_consequence"), limit=420)
        if not title and not why:
            continue
        label = title or "Buying hypothesis"
        if signal_id and signal_id.lower() not in label.lower():
            label = f"{label} ({signal_id})"
        items.append(_clip(f"{label}: {why}" if why else label, 560))
        if len(items) >= limit:
            break
    return list(dict.fromkeys(item for item in items if item))[:limit]


def _finalize_top_buying_hypotheses(items: list[Any], payload: dict) -> list[str]:
    formatted = [_format_top_buying_hypothesis_item(item) for item in _list(items)]
    formatted = [item for item in formatted if item]
    signals_by_id = _signal_by_id(payload)
    active_ids = sorted(_activation_signal_ids(payload))
    selected: list[str] = []
    used_ids: set[str] = set()

    for signal_id in active_ids:
        match = next((item for item in formatted if signal_id in _text_signal_ids(item)), "")
        if not match and signals_by_id.get(signal_id):
            match = _top_hypothesis_from_signal(signals_by_id[signal_id])
        if match:
            selected.append(match)
            used_ids.add(signal_id)

    for item in formatted:
        item_ids = set(_text_signal_ids(item))
        if item_ids and item_ids <= used_ids:
            continue
        if not item_ids and any(_similar_hypothesis(item, selected_item) for selected_item in selected):
            continue
        selected.append(item)
        used_ids.update(item_ids)
        if len(selected) >= max(3, len(active_ids)):
            break

    return list(dict.fromkeys(selected))[:max(3, len(active_ids))]


def _similar_hypothesis(left: Any, right: Any) -> bool:
    left_words = {
        word
        for word in re.findall(r"[a-zA-ZåäöÅÄÖ0-9]{5,}", str(left or "").lower())
        if word not in {"because", "scanner", "buying", "hypothesis", "evidence", "strength"}
    }
    right_words = {
        word
        for word in re.findall(r"[a-zA-ZåäöÅÄÖ0-9]{5,}", str(right or "").lower())
        if word not in {"because", "scanner", "buying", "hypothesis", "evidence", "strength"}
    }
    if not left_words or not right_words:
        return False
    overlap = len(left_words & right_words)
    return overlap >= 5 or overlap / max(1, min(len(left_words), len(right_words))) >= 0.6


def _normalize_top_buying_hypotheses(sales_spine: dict, payload: dict) -> dict:
    normalized = [
        item
        for item in (_format_top_buying_hypothesis_item(raw) for raw in _list(sales_spine.get("top_buying_hypotheses")))
        if item
    ]
    if not normalized:
        normalized = _activation_hypothesis_items(payload, limit=3)
    sales_spine["top_buying_hypotheses"] = _finalize_top_buying_hypotheses(normalized, payload)
    return sales_spine


def _fallback_stakeholder_map(payload: dict, signals_by_id: dict[str, dict], limit: int = 4) -> list[dict]:
    stakeholders: list[dict] = []
    seen: set[str] = set()
    for person in _list(payload.get("stakeholders")):
        if not isinstance(person, dict):
            continue
        name = _clip(person.get("name"), 100)
        role = _clip(person.get("role") or person.get("position") or person.get("title"), 120)
        if not name and not role:
            continue
        key = f"{name}|{role}".lower()
        if key in seen:
            continue
        seen.add(key)
        stakeholders.append({
            "name": name or "Source-bound stakeholder",
            "role": role or "Role to validate",
            "likely_buying_committee_role": _clip(person.get("stakeholder_role_match_reason") or person.get("insight") or "Potential buying-committee relevance; validate remit before outreach.", 260),
            "relevance_rationale": _clip(person.get("insight") or person.get("stakeholder_role_match_reason") or "Source-bound professional role context for sales preparation.", 420),
            "linked_pressure_themes": [str(x) for x in _list(person.get("matched_demand_groups"))[:6] if x],
            "linked_service_themes": [str(x) for x in _list(person.get("linked_service_ids"))[:5] if x],
            "evidence_basis": person.get("evidence_basis") or person.get("source") or "Stored Scanner stakeholder data",
            "confidence": _safe_int(person.get("confidence"), 45),
            "allowed_use": person.get("allowed_use") or "allowed_as_discovery_question",
            "do_not_say": _list(person.get("do_not_say"))[:4] or ["Do not imply buying intent from role/title alone."],
            "safety_notes": _list(person.get("safety_notes"))[:4] or ["Validate current role and decision remit before use."],
        })
        if len(stakeholders) >= limit:
            return stakeholders
    for signal in list(signals_by_id.values()):
        if not isinstance(signal, dict):
            continue
        for role in _list(signal.get("buyer_roles")) or ["Executive sponsor"]:
            role = _clip(role, 120)
            key = f"role|{role}".lower()
            if not role or key in seen:
                continue
            seen.add(key)
            stakeholders.append({
                "name": "Role-level target",
                "role": role,
                "likely_buying_committee_role": _clip(signal.get("buyer_role_concern") or "Likely role to validate in discovery.", 260),
                "relevance_rationale": _clip(signal.get("commercial_pressure") or signal.get("business_consequence") or signal.get("why"), 420),
                "linked_pressure_themes": [str(signal.get("pressure_type") or signal.get("theme") or "Account signal")],
                "linked_service_themes": [str(hint.get("service") or hint.get("category")) for hint in _list(signal.get("service_fit_hints"))[:4] if isinstance(hint, dict) and (hint.get("service") or hint.get("category"))],
                "evidence_basis": signal.get("id") or "Scanner account signal",
                "confidence": min(55, _safe_int(signal.get("confidence"), 45)),
                "allowed_use": "allowed_as_discovery_question",
                "do_not_say": ["Do not claim a named stakeholder exists without source-bound evidence."],
                "safety_notes": ["Role-level buying committee guidance generated from account signals."],
            })
            if len(stakeholders) >= limit:
                return stakeholders
    return stakeholders


def _role_level_stakeholder_map(signals_by_id: dict[str, dict], existing: list[dict] | None = None, limit: int = 3) -> list[dict]:
    stakeholders: list[dict] = []
    seen: set[str] = set()
    for item in _list(existing):
        if not isinstance(item, dict):
            continue
        role = _clip(item.get("role"), 140).lower()
        if role:
            seen.add(role)
    for signal in list(signals_by_id.values()):
        if not isinstance(signal, dict):
            continue
        for role in _list(signal.get("buyer_roles")):
            role_text = _clip(role, 120)
            if not role_text:
                continue
            role_key = role_text.lower()
            if any(role_key in existing_role or existing_role in role_key for existing_role in seen):
                continue
            seen.add(role_key)
            stakeholders.append({
                "name": "Role-level target",
                "role": role_text,
                "likely_buying_committee_role": _clip(signal.get("buyer_role_concern") or "Likely buying-committee role to validate in discovery.", 260),
                "relevance_rationale": _clip(
                    signal.get("commercial_pressure")
                    or signal.get("business_consequence")
                    or signal.get("why")
                    or "Role-level buying committee guidance generated from Scanner account signals.",
                    420,
                ),
                "linked_pressure_themes": [str(signal.get("theme") or signal.get("pressure_type") or signal.get("id") or "Account signal")],
                "linked_service_themes": [
                    str(hint.get("service") or hint.get("category"))
                    for hint in _list(signal.get("service_fit_hints"))[:4]
                    if isinstance(hint, dict) and (hint.get("service") or hint.get("category"))
                ],
                "evidence_basis": signal.get("id") or "Scanner account signal",
                "confidence": min(55, _safe_int(signal.get("confidence"), 45)),
                "allowed_use": "allowed_as_discovery_question",
                "do_not_say": ["Do not claim a named stakeholder exists without source-bound evidence."],
                "safety_notes": ["Use as role-level buying committee guidance; validate owner, remit, and timing before outreach."],
            })
            if len(stakeholders) >= limit:
                return stakeholders
    return stakeholders


CONVERSATION_REQUIRED_TEXT_FIELDS = (
    "interpreted_pressure",
    "buyer_lens",
    "why_this_service",
    "safe_wording",
    "missing_evidence",
    "proof_point",
)


def _conversation_missing_fields(item: Any) -> list[str]:
    if not isinstance(item, dict):
        return list(CONVERSATION_REQUIRED_TEXT_FIELDS)
    missing = [
        key
        for key in CONVERSATION_REQUIRED_TEXT_FIELDS
        if _word_count(item.get(key)) < 5
    ]
    if not _list(item.get("disqualifiers")):
        missing.append("disqualifiers")
    return missing


def _is_meaningful_conversation_direction(item: Any, known_signal_ids: set[str], *, require_complete: bool = False) -> bool:
    if not isinstance(item, dict):
        return False
    signal_ok = _has_signal_reference(item, known_signal_ids)
    if not signal_ok:
        return False
    service_text = _clip(item.get("service"), 120)
    content_score = sum(
        1
        for key in ("interpreted_pressure", "buyer_lens", "why_this_service", "safe_wording", "missing_evidence", "proof_point")
        if _word_count(item.get(key)) >= 5
    )
    if _normalize_gate(item.get("gate"), "allowed_as_discovery_question") == "blocked" and content_score == 0:
        return False
    if not service_text or content_score < 2:
        return False
    if require_complete and _conversation_missing_fields(item):
        return False
    return True


def _conversation_direction_from_signal(signal: dict) -> dict:
    service_hints = [hint for hint in _list(signal.get("service_fit_hints")) if isinstance(hint, dict)]
    hint = service_hints[0] if service_hints else {}
    service_name = _first_nonempty(
        hint.get("service"),
        hint.get("category"),
        "Discovery validation - offer to be selected after qualification",
        limit=220,
    )
    safe_wording = _first_nonempty(
        *_list(hint.get("safe_wording"))[:2],
        signal.get("what_to_do"),
        signal.get("what"),
        "Explore whether this account signal connects to a current priority, owner, and timing window.",
        limit=360,
    )
    proof_point = _first_nonempty(
        *_list(hint.get("proof_points"))[:2],
        signal.get("what_progress_shows"),
        "Generated from Scanner WHY/WHAT activation logic; validate before customer-facing use.",
        limit=320,
    )
    pressure = _first_nonempty(
        signal.get("commercial_pressure"),
        signal.get("business_consequence"),
        signal.get("why"),
        signal.get("account_movement"),
        limit=520,
    )
    return {
        "account_signal_id": signal.get("id"),
        "evidence": _clip(signal.get("account_movement") or signal.get("theme"), 280),
        "interpreted_pressure": pressure,
        "buyer_lens": _clip(
            signal.get("buyer_role_concern")
            or "; ".join(str(role) for role in _list(signal.get("buyer_roles"))[:4]),
            360,
        ),
        "service": service_name,
        "why_this_service": _clip(
            _first_nonempty(
                hint.get("fit_rationale"),
                hint.get("why_this_service"),
                f"{service_name} may be relevant only if the account signal is confirmed in discovery.",
                limit=420,
            ),
            420,
        ),
        "proof_point": proof_point,
        "safe_wording": safe_wording,
        "missing_evidence": "Customer confirmation, a named owner, timing, and business consequence are still needed before treating this as an active opportunity.",
        "gate": "allowed_as_discovery_question",
        "confidence": min(60, _safe_int(signal.get("confidence"), 45)),
        "disqualifiers": _list(hint.get("disqualifiers"))[:4] or _list(signal.get("disqualifiers"))[:4],
    }


def _complete_conversation_direction_from_signal(
    item: dict,
    signal: dict,
    previous_by_signal: dict[str, dict],
) -> tuple[dict, list[str]]:
    signal_id = str(signal.get("id") or "")
    completed = dict(item) if isinstance(item, dict) else {}
    deterministic = _conversation_direction_from_signal(signal)
    previous = previous_by_signal.get(signal_id) or {}
    recovered: list[str] = []

    completed["account_signal_id"] = completed.get("account_signal_id") or signal_id
    completed["gate"] = _normalize_gate(completed.get("gate"), "allowed_as_discovery_question")
    completed["confidence"] = min(60, _safe_int(completed.get("confidence"), _safe_int(deterministic.get("confidence"), 45)))

    for key in ("service", *CONVERSATION_REQUIRED_TEXT_FIELDS):
        if _word_count(completed.get(key)) >= 5 or (key == "service" and _clip(completed.get(key), 120)):
            continue
        previous_value = previous.get(key)
        if _word_count(previous_value) >= 5 or (key == "service" and _clip(previous_value, 120)):
            completed[key] = previous_value
            recovered.append(f"{key}:previous")
        else:
            completed[key] = deterministic.get(key)
            recovered.append(f"{key}:deterministic")

    if not _list(completed.get("disqualifiers")):
        previous_disqualifiers = _list(previous.get("disqualifiers"))
        completed["disqualifiers"] = previous_disqualifiers[:4] or _list(deterministic.get("disqualifiers"))[:4]
        recovered.append("disqualifiers:previous" if previous_disqualifiers else "disqualifiers:deterministic")

    if recovered:
        notes = _list(completed.get("recovery_notes"))
        notes.append(
            f"Conversation direction for {signal_id} completed missing fields from "
            f"{'previous same-company output and deterministic signal data' if any(':previous' in item for item in recovered) else 'deterministic signal data'}."
        )
        completed["recovery_notes"] = list(dict.fromkeys(notes))[:4]
        completed["completed_missing_fields"] = recovered
    return completed, recovered


def _add_missing_conversation_directions(
    report: dict,
    signals_by_id: dict[str, dict],
    activation_signal_ids: set[str],
    known_signal_ids: set[str],
    previous_report: dict | None = None,
) -> None:
    previous_by_signal = _previous_conversation_directions_by_signal(previous_report)
    items: list[dict] = []
    recovery_notes: list[str] = []
    seen_primary_ids: set[str] = set()
    for item in _list(report.get("pain_to_solution_map")):
        if not isinstance(item, dict):
            continue
        item_signal_ids = [signal_id for signal_id in _item_signal_ids(item) if signal_id in known_signal_ids]
        primary_signal_id = next((signal_id for signal_id in item_signal_ids if signal_id in activation_signal_ids), None)
        if primary_signal_id and primary_signal_id in seen_primary_ids:
            continue
        if primary_signal_id and signals_by_id.get(primary_signal_id):
            item, recovered = _complete_conversation_direction_from_signal(
                item,
                signals_by_id[primary_signal_id],
                previous_by_signal,
            )
            if recovered:
                recovery_notes.append(f"{primary_signal_id}: completed {len(recovered)} conversation field(s).")
        if _is_meaningful_conversation_direction(item, known_signal_ids, require_complete=bool(primary_signal_id)):
            items.append(item)
            if primary_signal_id:
                seen_primary_ids.add(primary_signal_id)

    present_ids = {
        signal_id
        for item in items
        for signal_id in _item_signal_ids(item)
        if signal_id in activation_signal_ids
    }
    for signal_id in sorted(activation_signal_ids):
        if signal_id in present_ids:
            continue
        signal = signals_by_id.get(signal_id)
        if not signal:
            continue
        items.append(_conversation_direction_from_signal(signal))
        present_ids.add(signal_id)
        recovery_notes.append(f"{signal_id}: deterministic conversation direction added.")
        if len(items) >= 4:
            break
    report["pain_to_solution_map"] = items[:4]
    if recovery_notes:
        existing_notes = _list(report.get("recovery_notes"))
        report["recovery_notes"] = list(dict.fromkeys(existing_notes + recovery_notes))[:12]


def _signal_label(signal: dict) -> str:
    signal_id = str(signal.get("id") or "")
    theme = _clip(signal.get("theme") or signal.get("pressure_type") or signal.get("account_movement"), 140)
    return f"{theme} ({signal_id})" if theme and signal_id else theme or signal_id


def _top_hypothesis_from_signal(signal: dict) -> str:
    label = _signal_label(signal)
    why = _clip(
        signal.get("why")
        or signal.get("commercial_pressure")
        or signal.get("account_movement")
        or signal.get("business_consequence"),
        460,
    )
    what = _clip(signal.get("what_to_do") or signal.get("what"), 260)
    if why and what and what.lower() not in why.lower():
        return f"{label}: {why} Next validation: {what}"
    return f"{label}: {why}" if why else label


def _pressure_signal_from_signal(signal: dict, ledger_records: list[dict] | None = None) -> dict:
    ledger_basis = [rec.get("id") for rec in _list(ledger_records)[:2] if isinstance(rec, dict) and rec.get("id")]
    signal_id = str(signal.get("id") or "")
    gate = _normalize_gate(signal.get("gate"), "allowed_as_hypothesis")
    if ledger_records and gate == "allowed_as_fact":
        gate = "allowed_as_hypothesis"
    return {
        "signal_type": _clip(signal.get("theme") or signal.get("pressure_type") or "Buying hypothesis pressure", 120),
        "trigger": _clip(f"{signal_id} activation pair: {signal.get('theme') or signal.get('account_movement') or signal_id}", 260),
        "description": _clip(
            signal.get("commercial_pressure")
            or signal.get("why")
            or signal.get("account_movement")
            or "Required activation signal recovered from Scanner structure.",
            620,
        ),
        "business_consequence": _clip(
            signal.get("business_consequence")
            or signal.get("what_progress_shows")
            or "Treat as a need hypothesis until discovery confirms owner, timing, and business consequence.",
            520,
        ),
        "buyer_role_concern": _clip(
            signal.get("buyer_role_concern")
            or "; ".join(str(role) for role in _list(signal.get("buyer_roles"))[:4])
            or "Buyer role to validate in discovery.",
            360,
        ),
        "gate": gate,
        "confidence": min(60, _safe_int(signal.get("confidence"), 45)),
        "evidence_basis": [signal_id] + ledger_basis,
    }


def _why_now_from_signal(signal: dict, ledger_records: list[dict] | None = None) -> dict:
    signal_id = str(signal.get("id") or "")
    ledger_basis = [rec.get("id") for rec in _list(ledger_records)[:2] if isinstance(rec, dict) and rec.get("id")]
    timing = _clip(signal.get("timing_relevance") or _infer_timing(_activation_pair_text(signal)), 300)
    why = _clip(
        signal.get("commercial_pressure")
        or signal.get("why")
        or signal.get("business_consequence")
        or "The activation signal should remain visible as a discovery theme.",
        560,
    )
    return {
        "trigger": _clip(f"{signal_id}: {signal.get('theme') or signal.get('account_movement') or 'Activation signal'}", 260),
        "why_it_matters_now": _clip(f"{timing} {why}", 720),
        "buyer_relevance": _clip(
            signal.get("buyer_role_concern")
            or "; ".join(str(role) for role in _list(signal.get("buyer_roles"))[:4])
            or "Validate the owner, timing, and business consequence in discovery.",
            420,
        ),
        "safe_discovery_question": _clip(
            _dict(signal.get("need_hypothesis")).get("discovery_question")
            or "Where would this pressure become visible if it starts affecting priorities, operating capacity, or investment choices?",
            320,
        ),
        "gate": _normalize_gate(signal.get("gate"), "allowed_as_hypothesis"),
        "confidence": min(60, _safe_int(signal.get("confidence"), 45)),
        "evidence_basis": [signal_id] + ledger_basis,
    }


def _ledger_match_records(payload: dict, signal: dict, limit: int = 4) -> list[dict]:
    terms_text = " ".join(
        _clip(signal.get(key), 500)
        for key in ("theme", "why", "what", "what_to_do", "account_movement", "commercial_pressure", "business_consequence")
        if signal.get(key)
    ).lower()
    terms = {
        term
        for term in re.findall(r"[a-zA-ZåäöÅÄÖ0-9]{4,}", terms_text)
        if term not in {
            "this", "that", "with", "from", "into", "while", "where", "what",
            "when", "need", "could", "would", "should", "company", "signal",
            "scanner", "current", "future", "evidence", "validate",
        }
    }
    matches: list[tuple[int, dict]] = []
    for rec in _list(payload.get("source_ledger_recovery_records")):
        if not isinstance(rec, dict):
            continue
        haystack = " ".join(str(rec.get(key) or "") for key in ("title", "quote", "source_type", "verification_reason")).lower()
        score = sum(1 for term in terms if term in haystack)
        if score <= 0:
            continue
        gate = _normalize_gate(rec.get("gate"), "allowed_as_discovery_question")
        if gate == "blocked":
            continue
        matches.append((score + (2 if rec.get("buying_evidence_eligible") else 0), rec))
    matches.sort(key=lambda item: item[0], reverse=True)
    return [rec for _, rec in matches[:limit]]


def _focused_reanalysis_prompt(section: str, signal: dict, payload: dict, ledger_records: list[dict] | None = None) -> str:
    compact_payload = {
        "company": payload.get("company"),
        "signal": signal,
        "strategic_synthesis": _dict(payload.get("scanner_outputs")).get("strategic_synthesis"),
        "activation_pairs": _dict(payload.get("scanner_outputs")).get("activation_pairs"),
        "services": _list(payload.get("services"))[:12],
        "ledger_records": _list(ledger_records)[:4],
    }
    schemas = {
        "top_buying_hypotheses": '{"item": "One concise buying hypothesis string preserving the SIG id."}',
        "pressure_signals": BUYING_SECTION_SCHEMAS["pressure_evidence"],
        "why_now": BUYING_SECTION_SCHEMAS["why_now"],
        "pain_to_solution_map": BUYING_SECTION_SCHEMAS["pain_to_solution"],
    }
    return f"""
Reconstruct one missing Buying Insight section item from existing stored data only.

Section: {section}
Rules:
- Return ONLY JSON.
- Do not browse web, invent facts, invent services, or upgrade evidence gates.
- Preserve the SIG id and use question/hypothesis wording if evidence is incomplete.
- Source Ledger records are source memory; use their gates as boundaries.
- If the item cannot be supported, return {{"unavailable": true, "reason": "..."}}.

Required JSON shape:
{schemas.get(section, '{"item": {}}')}

Stored payload:
{json.dumps(compact_payload, ensure_ascii=False)}
""".strip()


def _focused_reanalysis_item(
    section: str,
    signal: dict,
    payload: dict,
    *,
    ledger_records: list[dict] | None = None,
    model_mode: str | None = None,
) -> Any:
    try:
        raw, _model = call_json_model_chain(
            _focused_reanalysis_prompt(section, signal, payload, ledger_records),
            buying_generation_chain(model_mode),
            max_tokens=1400,
            timeout=45,
            validate_json=True,
        )
        parsed = parse_json_object(raw)
        if not isinstance(parsed, dict) or parsed.get("unavailable"):
            return None
        if section == "top_buying_hypotheses":
            return _format_top_buying_hypothesis_item(parsed.get("item"))
        if section in {"pressure_signals", "why_now", "pain_to_solution_map"}:
            values = _list(parsed.get(section))
            return values[0] if values and isinstance(values[0], dict) else None
    except Exception as exc:
        print(f"   ⚠️ [Buying Potential] Focused recovery failed for {section}/{signal.get('id')}: {exc}")
    return None


def _text_signal_ids(value: Any) -> list[str]:
    return sorted(set(re.findall(r"\bSIG-\d{2}\b", str(value or ""))))


def _section_signal_ids(items: Any) -> set[str]:
    ids: set[str] = set()
    for item in _list(items):
        if isinstance(item, dict):
            ids.update(_item_signal_ids(item))
            ids.update(_text_signal_ids(json.dumps(item, ensure_ascii=False)))
        else:
            ids.update(_text_signal_ids(item))
    return ids


def _previous_section_item(previous_report: dict, section: str, signal_id: str) -> Any:
    previous = _previous_buying_report(previous_report)
    if not previous:
        return None
    if section == "top_buying_hypotheses":
        for item in _list(_dict(previous.get("sales_spine")).get("top_buying_hypotheses")):
            if signal_id in _text_signal_ids(item):
                return item
        return None
    key = {
        "pressure_signals": "pressure_signals",
        "why_now": "why_now",
        "pain_to_solution_map": "pain_to_solution_map",
        "recommended_sales_plays": "recommended_sales_plays",
    }.get(section)
    if not key:
        return None
    for item in _list(previous.get(key)):
        if isinstance(item, dict) and signal_id in set(_item_signal_ids(item) + _text_signal_ids(json.dumps(item, ensure_ascii=False))):
            wording_only = item.copy()
            wording_only["linked_signal_ids"] = [signal_id]
            wording_only["evidence_basis"] = [signal_id]
            wording_only["gate"] = "allowed_as_discovery_question"
            wording_only.pop("parent_evidence_ids", None)
            wording_only.pop("parent_foresight_ids", None)
            wording_only.pop("source_url", None)
            wording_only["recovery_boundary"] = "Previous report wording only; current Scanner structure remains the evidence authority."
            return wording_only
    return None


def _sales_play_from_signal(
    signal: dict,
    sales_spine: dict,
    analysis: dict | None = None,
    service_candidates: list[dict] | None = None,
) -> dict:
    signal_id = str(signal.get("id") or "")
    play_type = _normalize_play_type(signal.get("sales_play_type") or signal.get("theme_type"))
    need = signal.get("need_hypothesis") if isinstance(signal.get("need_hypothesis"), dict) else _need_hypothesis_from_signal(signal)
    service_hints = [hint for hint in _list(signal.get("service_fit_hints")) if isinstance(hint, dict)]
    analysis = _dict(analysis)
    analysis_provided = bool(analysis)
    candidate_names = {
        str(item.get("name") or "").strip().lower(): str(item.get("name") or "").strip()
        for item in _list(service_candidates)
        if isinstance(item, dict) and str(item.get("name") or "").strip()
    }
    candidate_ids = {
        str(item.get("service_id") or "").strip().lower(): item
        for item in _list(service_candidates)
        if isinstance(item, dict) and str(item.get("service_id") or "").strip()
    }
    selected_service = str(analysis.get("service_or_offer") or "").strip()
    selected_service_id = str(analysis.get("service_id") or "").strip()
    selected_candidate = candidate_ids.get(selected_service_id.lower()) if selected_service_id else None
    service_name = str(selected_candidate.get("name") or "").strip() if selected_candidate else ""
    if not service_name and selected_service:
        service_name = candidate_names.get(selected_service.lower(), "")
        selected_candidate = next(
            (item for item in _list(service_candidates) if isinstance(item, dict) and str(item.get("name") or "").strip().lower() == selected_service.lower()),
            None,
        )
        selected_service_id = str(_dict(selected_candidate).get("service_id") or "")
    if not service_name and not analysis_provided and len(candidate_names) == 1:
        service_name = next(iter(candidate_names.values()))
        selected_candidate = next(
            (item for item in _list(service_candidates) if isinstance(item, dict) and str(item.get("name") or "").strip() == service_name),
            None,
        )
        selected_service_id = str(_dict(selected_candidate).get("service_id") or "")
    if not service_name and not candidate_names and len(service_hints) == 1:
        service_name = str(service_hints[0].get("service") or "").strip()
        selected_service_id = str(service_hints[0].get("service_id") or "")
    service_name = service_name or "Discovery validation - offer to be selected after qualification"
    readiness = "Validate now" if play_type == "current_status" and _has_current_timing_basis(signal) else "Validate when trigger appears"
    gate = _normalize_gate(signal.get("gate"), "allowed_as_discovery_question")
    coverage = "Recovered from required Scanner activation spine; validate owner, timing, and business consequence before use."
    buyer_roles = [str(role) for role in _list(signal.get("buyer_roles")) if str(role).strip()]
    primary_buyer = str(analysis.get("primary_buyer") or "").strip()
    if primary_buyer not in buyer_roles:
        primary_buyer = buyer_roles[0] if buyer_roles else "Role to validate"
    secondary_buyers = [
        str(role) for role in _list(analysis.get("secondary_buyers"))
        if str(role) in buyer_roles and str(role) != primary_buyer
    ][:3]
    if not secondary_buyers:
        secondary_buyers = [role for role in buyer_roles if role != primary_buyer][:3]
    role_questions = [
        {
            "role": str(item.get("role") or primary_buyer),
            "questions": [_clip(question, 320) for question in _list(item.get("questions"))[:3] if _clip(question, 320)],
        }
        for item in _list(analysis.get("discovery_questions_by_role"))
        if isinstance(item, dict) and _list(item.get("questions"))
    ][:4]
    default_question = need.get("discovery_question") or "Where would this pressure become visible if it starts to matter?"
    if not role_questions:
        role_questions = [{"role": primary_buyer, "questions": [default_question]}]
    discovery_questions = list(dict.fromkeys(
        question
        for item in role_questions
        for question in _list(item.get("questions"))
        if question
    ))[:5]
    safe_customer_wording = _clip(
        analysis.get("safe_customer_wording")
        or "This may be worth exploring as a forward-looking topic if it connects to a current priority or upcoming pressure.",
        420,
    )
    first_meeting_objective = _clip(
        analysis.get("first_meeting_objective")
        or "Validate whether the signal has a real owner, timing window, and business consequence before selecting an offer.",
        420,
    )
    service_fit_rationale = _clip(analysis.get("service_fit_rationale"), 420)
    play = {
        "play_name": _normalize_discovery_play_title(signal.get("theme"), "Activation theme"),
        "linked_signal_ids": [signal_id],
        "activation_pair_index": _safe_int(signal.get("activation_pair_index"), 0),
        "play_type": play_type,
        "hypothesis_id": signal.get("hypothesis_id"),
        "service_id": selected_service_id,
        "service_or_offer": service_name,
        "service_fit_rationale": service_fit_rationale,
        "need_hypothesis": need,
        "response_directions": _response_directions_from_play({"service_or_offer": service_name}, need),
        "readiness_band": readiness,
        "evidence_coverage": coverage,
        "confidence_boundary": _confidence_boundary(gate, coverage),
        "priority_band": _priority_band(readiness),
        "primary_buyer": primary_buyer,
        "secondary_buyers": secondary_buyers,
        "trigger": _clip(signal.get("account_movement") or signal.get("theme"), 260),
        "account_thesis": sales_spine.get("account_thesis"),
        "buying_hypothesis": _clip(signal.get("why") or signal.get("commercial_pressure"), 420),
        "trigger_to_offer_bridge": _clip(signal.get("what_to_do") or signal.get("what") or signal.get("business_consequence"), 420),
        "business_hypothesis": _clip(signal.get("business_consequence") or signal.get("commercial_pressure"), 420),
        "why_relevant": _clip(signal.get("commercial_pressure") or signal.get("why"), 420),
        "target_stakeholder": primary_buyer,
        "first_meeting_objective": first_meeting_objective,
        "opening_angle": safe_customer_wording,
        "executive_angle": safe_customer_wording,
        "technical_angle": "Validate the current process, data, integration, governance, or operating baseline before recommending technology.",
        "safe_customer_wording": safe_customer_wording,
        "discovery_questions": discovery_questions or [default_question],
        "discovery_questions_by_role": role_questions,
        "proof_point": _clip(signal.get("what_progress_shows") or "Generated from Scanner WHY/WHAT activation logic; validate before customer-facing use.", 360),
        "do_not_say": list(dict.fromkeys(
            [str(item) for item in _list(analysis.get("do_not_say")) if str(item).strip()]
            + ["Do not present this as a confirmed project, budget, or buying intent."]
        ))[:5],
        "qualification_upgrade_criteria": [
            "Customer confirms the pressure is real",
            "A named owner or decision forum exists",
            _clip(signal.get("what_progress_shows") or "Timing or business consequence is confirmed", 260),
        ],
        "confidence": min(55, _safe_int(signal.get("confidence"), 45)),
        "play_score": 55 if readiness == "Validate when trigger appears" else 65,
        "hypothesis_strength": 55 if readiness == "Validate when trigger appears" else 65,
        "score_label": "Internal compatibility score",
        "play_classification": "qualified_discovery_play",
        "overclaim_risk": "medium",
        "gate": gate,
        "evidence_basis": [signal_id],
        "disqualifiers": list(dict.fromkeys(
            [str(item) for item in _list(analysis.get("disqualifiers")) if str(item).strip()]
            + [str(item) for item in _list(signal.get("disqualifiers")) if str(item).strip()]
        ))[:5],
    }
    if service_fit_rationale and service_name != "Discovery validation - offer to be selected after qualification":
        play["response_directions"]["vivicta_potential_contribution"] = _clip(
            f"{service_name} may be relevant if discovery confirms this bridge: {service_fit_rationale}",
            420,
        )
    return play


def _sales_play_completeness_score(play: dict, signal_id: str, service_names: set[str]) -> int:
    score = 0
    linked_ids = _item_signal_ids(play)
    if linked_ids == [signal_id]:
        score += 8
    elif signal_id in linked_ids:
        score += 4
    service = str(play.get("service_or_offer") or "").strip().lower()
    if service in service_names:
        score += 5
    elif service and "selected after qualification" not in service:
        score += 1
    for key in (
        "buying_hypothesis",
        "trigger_to_offer_bridge",
        "first_meeting_objective",
        "safe_customer_wording",
        "proof_point",
    ):
        if _word_count(play.get(key)) >= 5:
            score += 1
    if _list(play.get("discovery_questions")) or _list(play.get("discovery_questions_by_role")):
        score += 2
    return score


def _enforce_one_primary_sales_play(report: dict, payload: dict) -> None:
    plays = [item for item in _list(report.get("recommended_sales_plays")) if isinstance(item, dict)]
    signals_by_id = _signal_by_id(payload)
    active_ids = sorted(
        _activation_signal_ids(payload),
        key=lambda signal_id: (
            _safe_int(signals_by_id.get(signal_id, {}).get("activation_pair_index"), 999),
            signal_id,
        ),
    )
    if not active_ids:
        unique: list[dict] = []
        seen: set[str] = set()
        for play in plays:
            signal_id = next(iter(_item_signal_ids(play)), "")
            dedupe_key = signal_id or str(play.get("service_or_offer") or play.get("play_name") or "").lower()
            if not dedupe_key or dedupe_key in seen:
                continue
            seen.add(dedupe_key)
            unique.append(play)
            if len(unique) >= 3:
                break
        report["recommended_sales_plays"] = unique
        return

    service_names = _known_service_names(payload)
    selected: list[dict] = []
    used_indices: set[int] = set()
    collapsed = 0
    sales_spine = _dict(report.get("sales_spine"))
    for signal_id in active_ids[:3]:
        candidates = [
            (index, play)
            for index, play in enumerate(plays)
            if index not in used_indices and signal_id in _item_signal_ids(play)
        ]
        if candidates:
            index, play = max(
                candidates,
                key=lambda item: _sales_play_completeness_score(item[1], signal_id, service_names),
            )
            used_indices.add(index)
            collapsed += max(0, len(candidates) - 1)
        else:
            signal = signals_by_id.get(signal_id)
            if not signal:
                continue
            play = _sales_play_from_signal(
                signal,
                sales_spine,
                service_candidates=_sales_play_service_candidates(signal, payload),
            )
        play["linked_signal_ids"] = [signal_id]
        play["evidence_basis"] = list(dict.fromkeys(
            [signal_id] + [str(item) for item in _list(play.get("evidence_basis")) if str(item) != signal_id]
        ))[:6]
        selected.append(play)

    report["recommended_sales_plays"] = selected
    if collapsed or len(plays) > len(selected):
        notes = _list(report.get("recovery_notes"))
        notes.append(
            f"recommended_sales_plays: reduced {len(plays)} candidate play(s) to "
            f"{len(selected)} primary activation-pair play(s)."
        )
        report["recovery_notes"] = list(dict.fromkeys(notes))[:20]


def _record_recovery(report: dict, section: str, signal_id: str, path: str, detail: str = "") -> None:
    recovered = report.setdefault("recovered_sections", [])
    recovered.append({"section": section, "signal_id": signal_id, "path": path, "detail": _clip(detail, 220)})
    paths = report.setdefault("recovery_path_by_section", {})
    paths.setdefault(section, {})[signal_id] = path
    if path.startswith("ledger"):
        report.setdefault("ledger_recovered_sections", []).append({"section": section, "signal_id": signal_id})
    if path == "previous_report":
        report.setdefault("previous_report_recovered_sections", []).append({"section": section, "signal_id": signal_id})
    note = f"{section}: {signal_id} recovered via {path.replace('_', ' ')}."
    if detail:
        note += f" {detail}"
    notes = _list(report.get("recovery_notes"))
    notes.append(note)
    report["recovery_notes"] = list(dict.fromkeys(notes))[:20]


def _mark_unavailable(report: dict, section: str, signal_id: str, reason: str) -> None:
    item = {"section": section, "signal_id": signal_id, "reason": _clip(reason, 260)}
    unavailable = _list(report.get("unavailable_required_items"))
    unavailable.append(item)
    report["unavailable_required_items"] = unavailable[:20]
    _record_recovery(report, section, signal_id, "unavailable", reason)


def _recover_required_spine_sections(report: dict, payload: dict, model_mode: str | None = None) -> None:
    signals_by_id = _signal_by_id(payload)
    active_ids = sorted(_activation_signal_ids(payload))
    active_signals = [signals_by_id[signal_id] for signal_id in active_ids if signals_by_id.get(signal_id)]
    if not active_signals:
        return
    previous_report = _previous_buying_report(payload.get("previous_buying_insight"))
    coverage: dict[str, dict] = {}

    def missing_for(section: str, items: Any) -> list[str]:
        present = _section_signal_ids(items)
        coverage[section] = {"required": active_ids, "present": sorted(present & set(active_ids))}
        return [signal_id for signal_id in active_ids if signal_id not in present]

    sales_spine = _dict(report.get("sales_spine"))
    top_items = [_format_top_buying_hypothesis_item(item) for item in _list(sales_spine.get("top_buying_hypotheses"))]
    top_items = [item for item in top_items if item]
    for signal_id in missing_for("sales_spine.top_buying_hypotheses", top_items):
        signal = signals_by_id.get(signal_id)
        if not signal:
            _mark_unavailable(report, "sales_spine.top_buying_hypotheses", signal_id, "Upstream activation signal missing.")
            continue
        item = _top_hypothesis_from_signal(signal)
        path = "current_structure"
        if _word_count(item) < 8:
            focused = _focused_reanalysis_item("top_buying_hypotheses", signal, payload, model_mode=model_mode)
            if focused:
                item = focused
                path = "focused_reanalysis"
            else:
                ledger_records = _ledger_match_records(payload, signal)
                ledger_item = _focused_reanalysis_item("top_buying_hypotheses", signal, payload, ledger_records=ledger_records, model_mode=model_mode) if ledger_records else None
                if ledger_item:
                    item = ledger_item
                    path = "ledger_reanalysis"
                else:
                    previous = _previous_section_item(previous_report, "top_buying_hypotheses", signal_id)
                    if previous:
                        item = _format_top_buying_hypothesis_item(previous)
                        path = "previous_report"
        if item:
            top_items.append(item)
            _record_recovery(report, "sales_spine.top_buying_hypotheses", signal_id, path)
        else:
            _mark_unavailable(report, "sales_spine.top_buying_hypotheses", signal_id, "No current, ledger, or previous-report basis could rebuild the hypothesis.")
    sales_spine["top_buying_hypotheses"] = _finalize_top_buying_hypotheses(top_items, payload)
    report["sales_spine"] = sales_spine

    for section, key, builder, min_words in (
        ("pressure_signals", "pressure_signals", _pressure_signal_from_signal, 10),
        ("why_now", "why_now", _why_now_from_signal, 12),
    ):
        items = [item for item in _list(report.get(key)) if isinstance(item, dict)]
        for signal_id in missing_for(section, items):
            signal = signals_by_id.get(signal_id)
            if not signal:
                _mark_unavailable(report, section, signal_id, "Upstream activation signal missing.")
                continue
            ledger_records = _ledger_match_records(payload, signal)
            item = builder(signal, [])
            path = "current_structure"
            item_text = json.dumps(item, ensure_ascii=False)
            if _word_count(item_text) < min_words:
                focused = _focused_reanalysis_item(section, signal, payload, model_mode=model_mode)
                if focused:
                    item = focused
                    path = "focused_reanalysis"
                else:
                    ledger_item = _focused_reanalysis_item(section, signal, payload, ledger_records=ledger_records, model_mode=model_mode) if ledger_records else None
                    if ledger_item:
                        item = ledger_item
                        path = "ledger_reanalysis"
                    else:
                        previous = _previous_section_item(previous_report, section, signal_id)
                        if previous:
                            item = previous
                            path = "previous_report"
            elif ledger_records and _word_count(signal.get("why")) < 8:
                item = builder(signal, ledger_records)
                path = "ledger_context"
            if item:
                items.append(item)
                _record_recovery(report, section, signal_id, path)
            else:
                _mark_unavailable(report, section, signal_id, "No current, ledger, or previous-report basis could rebuild the item.")
        report[key] = items[:max(6, len(items))]

    for section, key in (("pain_to_solution_map", "pain_to_solution_map"), ("recommended_sales_plays", "recommended_sales_plays")):
        items = [item for item in _list(report.get(key)) if isinstance(item, dict)]
        for signal_id in missing_for(section, items):
            signal = signals_by_id.get(signal_id)
            if not signal:
                _mark_unavailable(report, section, signal_id, "Upstream activation signal missing.")
                continue
            if section == "pain_to_solution_map":
                ledger_records = _ledger_match_records(payload, signal)
                item = _conversation_direction_from_signal(signal)
                path = "current_structure"
                if _conversation_missing_fields(item):
                    focused = _focused_reanalysis_item(section, signal, payload, model_mode=model_mode)
                    if focused:
                        item = focused
                        path = "focused_reanalysis"
                    else:
                        ledger_item = _focused_reanalysis_item(section, signal, payload, ledger_records=ledger_records, model_mode=model_mode) if ledger_records else None
                        if ledger_item:
                            item = ledger_item
                            path = "ledger_reanalysis"
                        else:
                            previous = _previous_section_item(previous_report, section, signal_id)
                            if previous:
                                item = previous
                                path = "previous_report"
            else:
                item = _sales_play_from_signal(signal, sales_spine)
                path = "current_structure"
            if item:
                items.append(item)
                _record_recovery(report, section, signal_id, path)
            else:
                _mark_unavailable(report, section, signal_id, "No current, ledger, or previous-report basis could rebuild the item.")
        report[key] = items[:max(6, len(items))]

    role_items = [item for item in _list(report.get("stakeholder_map")) if isinstance(item, dict)]
    role_ids = _section_signal_ids(role_items)
    if not (role_ids & set(active_ids)):
        additions = _role_level_stakeholder_map(signals_by_id, role_items, limit=2)
        if additions:
            report["stakeholder_map"] = role_items + additions
            _record_recovery(report, "stakeholder_map", "ROLE", "current_structure", "Role-level buyer lenses added from active SIGs.")
    coverage["stakeholder_map"] = {
        "required": active_ids,
        "present": sorted(_section_signal_ids(report.get("stakeholder_map")) & set(active_ids)),
    }
    report["section_coverage"] = coverage


def _enrich_short_opening_sections(report: dict, payload: dict) -> None:
    snapshot = _dict(report.get("account_snapshot"))
    thesis = _dict(report.get("buying_potential_thesis"))
    reasoning = _dict(report.get("qualification_reasoning"))
    if reasoning:
        composed = _compose_qualification_sections(reasoning, payload)
        if _word_count(snapshot.get("text")) < 70 or _qualification_reasoning_issues(reasoning):
            snapshot = _dict(composed.get("account_snapshot"))
        if _word_count(thesis.get("text")) < 70:
            thesis = _dict(composed.get("buying_potential_thesis"))

    # Never pad these executive sections with arbitrary source records. Raw
    # evidence remains available in Scanner; opening prose must come from the
    # governed qualification reasoning spine.
    snapshot["text"] = _clean_reasoning_sentence(snapshot.get("text"), 1900)
    snapshot["paragraphs"] = [
        _clean_reasoning_sentence(item, 1100)
        for item in _list(snapshot.get("paragraphs"))
        if _clean_reasoning_sentence(item, 1100)
    ]
    thesis["text"] = _clean_reasoning_sentence(thesis.get("text"), 2100)
    thesis["paragraphs"] = [
        _clean_reasoning_sentence(item, 1200)
        for item in _list(thesis.get("paragraphs"))
        if _clean_reasoning_sentence(item, 1200)
    ]
    thesis["rationale"] = _clean_reasoning_sentence(thesis.get("rationale"), 1300)
    report["account_snapshot"] = snapshot
    report["buying_potential_thesis"] = thesis


def _post_process_report(report: dict, payload: dict, gate_review: dict | None = None, model_mode: str | None = None) -> dict:
    verified_count = len(payload.get("verified_evidence") or [])
    financial_count = len(payload.get("financial_sources") or [])
    sufficiency_status = str(_dict(payload.get("evidence_sufficiency")).get("status") or "")
    low_evidence = sufficiency_status == "insufficient" or (verified_count < 2 and financial_count < 1)
    known_signal_ids = {str(signal.get("id")) for signal in _list(payload.get("account_signal_map")) if isinstance(signal, dict) and signal.get("id")}
    signals_by_id = _signal_by_id(payload)
    hypothesis_registry = [item for item in _list(payload.get("hypothesis_registry")) if isinstance(item, dict)]
    hypothesis_record_by_signal = {
        str(item.get("activation_signal_id")): item
        for item in hypothesis_registry
        if item.get("activation_signal_id")
    }
    hypothesis_by_signal = {
        str(item.get("activation_signal_id")): str(item.get("hypothesis_id"))
        for item in hypothesis_registry
        if item.get("activation_signal_id") and item.get("hypothesis_id")
    }
    activation_signal_ids = _activation_signal_ids(payload)
    service_names = _known_service_names(payload)
    scanner_outputs = _dict(payload.get("scanner_outputs"))
    self_scan_context = _dict(payload.get("self_scan_context"))
    modernization_tactics = [item for item in _list(payload.get("modernization_tactics")) if isinstance(item, dict)]
    previous_report = _previous_buying_report(payload.get("previous_buying_insight"))
    render_quality_notes: list[str] = []

    def fix_item(item: Any, default_gate: str = "allowed_as_hypothesis") -> Any:
        if not isinstance(item, dict):
            return item
        gate = _normalize_gate(item.get("gate"), default_gate)
        if gate == "allowed_as_fact" and not _has_basis(item):
            gate = "allowed_as_hypothesis" if not low_evidence else "allowed_as_discovery_question"
        item["gate"] = gate
        item["confidence"] = max(0, min(100, _safe_int(item.get("confidence"), 50)))
        signal_ids = _item_signal_ids(item) + _text_signal_ids(json.dumps(item, ensure_ascii=False))
        linked_hypotheses = _list(item.get("linked_hypothesis_ids"))
        if item.get("hypothesis_id"):
            linked_hypotheses.append(item.get("hypothesis_id"))
        for signal_id in signal_ids:
            hypothesis_id = hypothesis_by_signal.get(signal_id)
            if hypothesis_id:
                linked_hypotheses.append(hypothesis_id)
        linked_hypotheses = list(dict.fromkeys(str(value) for value in linked_hypotheses if value))
        if linked_hypotheses:
            item["linked_hypothesis_ids"] = linked_hypotheses[:3]
            item.setdefault("hypothesis_id", linked_hypotheses[0])
        return item

    for key in ("account_snapshot", "buying_potential_thesis"):
        if isinstance(report.get(key), dict):
            fix_item(report[key], "allowed_as_hypothesis")
            if key == "account_snapshot" and report[key].get("gate") == "allowed_as_fact":
                snapshot_text_l = str(report[key].get("text") or report[key].get("statement") or "").lower()
                if any(term in snapshot_text_l for term in ("proof", "hypothesis", "momentum", "maturity", "pressure", "demand-creation")):
                    report[key]["gate"] = "allowed_as_hypothesis"
            report[key] = _strip_archetype_terms(report[key])
    sales_spine = _dict(report.get("sales_spine"))
    if not sales_spine:
        activation_themes = [
            _clip(signal.get("theme") or signal.get("account_movement"), 180)
            for signal in _list(payload.get("account_signal_map"))
            if isinstance(signal, dict) and signal.get("source") == "scanner_activation_pair"
        ][:3]
        sales_spine = {
            "account_thesis": _clip(
                _dict(report.get("buying_potential_thesis")).get("text")
                or scanner_outputs.get("strategic_synthesis")
                or scanner_outputs.get("pitch"),
                900,
            ),
            "strategic_synthesis_basis": _clip(scanner_outputs.get("strategic_synthesis"), 1200),
            "top_buying_hypotheses": activation_themes,
            "risk_themes": [_clip(item, 240) for item in _list(scanner_outputs.get("evidence_based_risks"))[:4]],
            "self_scan_note": self_scan_context.get("framing") or "",
        }
    else:
        if not sales_spine.get("account_thesis"):
            sales_spine["account_thesis"] = _clip(
                _dict(report.get("buying_potential_thesis")).get("text")
                or scanner_outputs.get("strategic_synthesis")
                or scanner_outputs.get("pitch"),
                900,
            )
        if not sales_spine.get("strategic_synthesis_basis"):
            sales_spine["strategic_synthesis_basis"] = _clip(scanner_outputs.get("strategic_synthesis"), 1200)
        if not _list(sales_spine.get("risk_themes")):
            sales_spine["risk_themes"] = [_clip(item, 240) for item in _list(scanner_outputs.get("evidence_based_risks"))[:4]]
        if self_scan_context.get("is_self_scan"):
            sales_spine["self_scan_note"] = sales_spine.get("self_scan_note") or self_scan_context.get("framing")
    sales_spine = _normalize_top_buying_hypotheses(sales_spine, payload)
    sales_spine["risk_themes"] = _dedupe_text_items(sales_spine.get("risk_themes"), limit=5, clip_limit=240)
    all_hypothesis_ids = [str(item.get("hypothesis_id")) for item in hypothesis_registry if item.get("hypothesis_id")]
    if all_hypothesis_ids:
        sales_spine["linked_hypothesis_ids"] = all_hypothesis_ids[:3]
        if isinstance(report.get("buying_potential_thesis"), dict):
            report["buying_potential_thesis"]["linked_hypothesis_ids"] = all_hypothesis_ids[:3]
    report["sales_spine"] = _strip_archetype_terms(sales_spine)
    report["hypothesis_registry"] = hypothesis_registry
    for key in ("why_now",):
        report[key] = [
            fix_item(item)
            for item in _list(report.get(key))
            if isinstance(item, dict)
            and _is_meaningful_text(
                item.get("trigger"),
                item.get("why_it_matters_now"),
                item.get("interpretation"),
                item.get("safe_discovery_question"),
                min_len=12,
            )
        ]
    pressure_signals = []
    for item in _list(report.get("pressure_signals")):
        if not isinstance(item, dict) or not _clip(item.get("description") or item.get("business_consequence") or item.get("trigger"), 40):
            continue
        fixed = fix_item(item)
        linked_signal = next(
            (signals_by_id.get(signal_id) for signal_id in _item_signal_ids(fixed) if signals_by_id.get(signal_id)),
            None,
        )
        lineage = hypothesis_record_by_signal.get(str(_dict(linked_signal).get("id") or ""), {})
        if lineage.get("external_pressure") and _normalize_play_type(_dict(linked_signal).get("play_type")) != "current_status":
            fixed["description"] = _clip(lineage.get("external_pressure"), 520)
            fixed["business_consequence"] = _clip(
                lineage.get("likely_business_consequence") or fixed.get("business_consequence"),
                520,
            )
        if not _pressure_signal_has_renderable_basis(fixed, known_signal_ids):
            render_quality_notes.append(
                "Pressure signal removed before rendering because its evidence basis was empty or incomplete: "
                + _clip(fixed.get("signal_type") or fixed.get("trigger") or fixed.get("description"), 180)
                + "."
            )
            continue
        pressure_signals.append(fixed)
    report["pressure_signals"] = pressure_signals
    report["pain_to_solution_map"] = [fix_item(item) for item in _list(report.get("pain_to_solution_map")) if isinstance(item, dict)]
    _add_missing_conversation_directions(report, signals_by_id, activation_signal_ids, known_signal_ids, previous_report)
    report["pain_to_solution_map"] = [fix_item(item) for item in _list(report.get("pain_to_solution_map")) if isinstance(item, dict)]
    report["recommended_sales_plays"] = [fix_item(item) for item in _list(report.get("recommended_sales_plays")) if isinstance(item, dict)]
    _recover_required_spine_sections(report, payload, model_mode=model_mode)
    _enforce_one_primary_sales_play(report, payload)
    evidence_confidence = _dict(report.get("evidence_confidence"))
    evidence_confidence["verified_facts"] = _verified_fact_items_from_payload(payload)
    quality_notes = evidence_confidence.setdefault("report_quality_notes", [])
    quality_notes.extend(render_quality_notes)
    repaired_sections = [
        str(meta.get("section"))
        for meta in _list(report.get("section_generation"))
        if isinstance(meta, dict) and "repaired" in str(meta.get("parse_status") or "").lower()
    ]
    fallback_sections = [
        str(meta.get("section"))
        for meta in _list(report.get("section_generation"))
        if isinstance(meta, dict) and "fallback" in str(meta.get("parse_status") or "").lower()
    ]
    partial_recovery_sections = [
        str(meta.get("section"))
        for meta in _list(report.get("section_generation"))
        if isinstance(meta, dict) and "partial_recovery" in str(meta.get("parse_status") or "").lower()
    ]
    if repaired_sections:
        quality_notes.append(
            f"{len(repaired_sections)} Buying Insight section(s) required JSON repair before finalization: "
            + ", ".join(repaired_sections[:8])
            + "."
        )
    if fallback_sections:
        quality_notes.append(
            f"{len(fallback_sections)} Buying Insight section(s) used deterministic fallback: "
            + ", ".join(fallback_sections[:8])
            + "."
        )
    if partial_recovery_sections:
        quality_notes.append(
            f"{len(partial_recovery_sections)} truncated section(s) retained complete items and rebuilt missing SIG coverage: "
            + ", ".join(partial_recovery_sections[:8])
            + "."
        )
    for note in _list(report.get("recovery_notes")):
        note_text = _clip(note, 260)
        if note_text:
            quality_notes.append(note_text)
    for note in _list(report.get("model_notes")) + _list(report.get("missing_input_warnings")):
        note_text = _clip(note, 320)
        if not note_text:
            continue
        lower = note_text.lower()
        if any(marker in lower for marker in ("deterministic fallback", "malformed", "json", "parse failed", "repair failed", "generation failed")):
            quality_notes.append(note_text)
    weak_notes = evidence_confidence.setdefault("weak_or_missing_evidence", [])
    for item in _list(report.get("pain_to_solution_map")):
        if not isinstance(item, dict):
            continue
        missing = _clip(item.get("missing_evidence"), 360)
        if missing and missing.lower() not in {"n/a", "none"}:
            weak_notes.append(missing)
    for item in report.get("pain_to_solution_map", []):
        has_signal = _has_signal_reference(item, known_signal_ids)
        has_service = _service_match_exists(item, service_names)
        if not has_signal or not has_service:
            item["gate"] = "allowed_as_discovery_question" if item["gate"] != "blocked" else "blocked"
            item.setdefault("missing_evidence", "Needs a clearer account-specific signal and service-fit bridge before customer-facing use.")
            weak_notes.append(f"Opportunity-to-offer fit downgraded: {item.get('service') or 'unnamed service'} lacks {'signal reference' if not has_signal else 'Service KB match'}.")
    for play in report.get("recommended_sales_plays", []):
        play["discovery_questions"] = [str(item) for item in _list(play.get("discovery_questions"))[:5]]
        play["do_not_say"] = [str(item) for item in _list(play.get("do_not_say"))[:5]]
        play["evidence_basis"] = [str(item) for item in _list(play.get("evidence_basis"))[:6]]
        play["linked_signal_ids"] = [str(item) for item in _list(play.get("linked_signal_ids"))[:6]]
        play["disqualifiers"] = [str(item) for item in _list(play.get("disqualifiers"))[:5]]
        has_signal = _has_signal_reference(play, known_signal_ids)
        linked_signals = [signals_by_id.get(signal_id) for signal_id in _item_signal_ids(play) if signals_by_id.get(signal_id)]
        activation_signal = next((signal for signal in linked_signals if signal.get("id") in activation_signal_ids), None)
        hypothesis_record = hypothesis_record_by_signal.get(str(_dict(activation_signal).get("id") or ""), {})
        if activation_signal:
            play["activation_pair_index"] = _safe_int(play.get("activation_pair_index"), _safe_int(activation_signal.get("activation_pair_index"), 0))
            play["play_type"] = _normalize_play_type(play.get("play_type") or activation_signal.get("sales_play_type"))
            play["trigger"] = play.get("trigger") or activation_signal.get("account_movement")
            play["buying_hypothesis"] = play.get("buying_hypothesis") or activation_signal.get("why")
            play["trigger_to_offer_bridge"] = play.get("trigger_to_offer_bridge") or activation_signal.get("what_to_do") or activation_signal.get("what")
            play["proof_point"] = play.get("proof_point") or activation_signal.get("what_progress_shows")
            play["account_thesis"] = play.get("account_thesis") or sales_spine.get("account_thesis")
            if not isinstance(play.get("need_hypothesis"), dict):
                play["need_hypothesis"] = activation_signal.get("need_hypothesis") or _need_hypothesis_from_signal(activation_signal)
        else:
            play["play_type"] = _normalize_play_type(play.get("play_type"))
        has_service = _service_match_exists(play, service_names)
        has_question = bool(play["discovery_questions"])
        has_activation_signal = bool(activation_signal) or not activation_signal_ids
        if not has_signal or not has_service or not has_question or not has_activation_signal:
            play["gate"] = "allowed_as_discovery_question" if play.get("gate") != "blocked" else "blocked"
            play["overclaim_risk"] = "high" if not has_signal else play.get("overclaim_risk") or "medium"
            if not has_question:
                play["discovery_questions"].append("Which current pressure would make this topic worth exploring with you?")
            if not has_signal:
                play["do_not_say"].append("Do not present this as account-specific without a clearer account signal.")
            if not has_activation_signal:
                play["do_not_say"].append("Do not present this as a primary sales play unless it maps to a Scanner WHY/WHAT activation pair.")
            if not has_service:
                play["do_not_say"].append("Do not present this as a Vivicta service fit until the Service Portfolio match is validated.")
        play["account_thesis"] = play.get("account_thesis") or sales_spine.get("account_thesis") or _dict(report.get("buying_potential_thesis")).get("text")
        play["buying_hypothesis"] = play.get("buying_hypothesis") or play.get("business_hypothesis") or play.get("why_relevant")
        play["trigger_to_offer_bridge"] = play.get("trigger_to_offer_bridge") or play.get("why_relevant") or play.get("opening_angle")
        play["safe_customer_wording"] = play.get("safe_customer_wording") or play.get("opening_angle") or play.get("executive_angle")
        fallback_signal = {
            "play_type": play.get("play_type"),
            "theme": play.get("trigger"),
            "why": play.get("buying_hypothesis") or play.get("why_relevant"),
            "what": play.get("trigger_to_offer_bridge") or play.get("service_or_offer"),
            "what_to_do": play.get("trigger_to_offer_bridge") or play.get("service_or_offer"),
            "what_progress_shows": play.get("proof_point"),
            "account_movement": play.get("account_thesis"),
            "commercial_pressure": play.get("business_hypothesis"),
            "business_consequence": play.get("business_hypothesis"),
        }
        if activation_signal:
            fallback_signal = {**fallback_signal, **activation_signal}
        if hypothesis_record:
            fallback_signal["external_pressure"] = hypothesis_record.get("external_pressure")
            fallback_signal["business_consequence"] = hypothesis_record.get("likely_business_consequence")
            fallback_signal["pressure_horizon"] = hypothesis_record.get("pressure_horizon")
            play["financial_scope"] = _clip(hypothesis_record.get("financial_scope"), 320)
            play["wording_boundary"] = _clip(hypothesis_record.get("wording_boundary"), 320)
            play["prohibited_claims"] = _list(hypothesis_record.get("prohibited_claims"))[:6]
            play["do_not_say"] = _dedupe_text_items(
                _list(play.get("do_not_say")) + _list(hypothesis_record.get("prohibited_claims")),
                limit=6,
                clip_limit=360,
            )
        play["need_hypothesis"] = _normalize_need_hypothesis(play.get("need_hypothesis"), fallback_signal, play.get("play_type"))
        if hypothesis_record.get("external_pressure") and play.get("play_type") != "current_status":
            play["need_hypothesis"]["future_pressure"] = _clip(hypothesis_record.get("external_pressure"), 520)
            play["need_hypothesis"]["market_context"] = _clip(hypothesis_record.get("external_pressure"), 520)
        if hypothesis_record.get("likely_business_consequence"):
            play["need_hypothesis"]["likely_business_consequence"] = _clip(
                hypothesis_record.get("likely_business_consequence"),
                520,
            )
        role_questions = _list(play.get("discovery_questions_by_role"))
        if not role_questions:
            play["discovery_questions_by_role"] = [{
                "role": play.get("primary_buyer") or play.get("target_stakeholder") or "Executive sponsor",
                "questions": play["discovery_questions"][:3],
            }]
        if not _list(play.get("qualification_upgrade_criteria")):
            play["qualification_upgrade_criteria"] = [
                "Customer confirms the pressure is real",
                "A named owner or decision forum exists",
                "Timing, business consequence, or measurable priority is confirmed",
            ]
        score = _score_play(
            play,
            has_signal=has_signal,
            has_service=has_service,
            has_question=bool(play["discovery_questions"]),
            signal=activation_signal or (linked_signals[0] if linked_signals else None),
        )
        gate = _normalize_gate(play.get("gate"), "allowed_as_hypothesis")
        if low_evidence:
            score = min(score, 55)
        elif sufficiency_status == "partial":
            score = min(score, 65)
        if gate == "allowed_as_discovery_question":
            score = min(score, 55)
        elif gate == "internal_only":
            score = min(score, 40)
        elif gate == "blocked":
            score = min(score, 15)
        play["play_score"] = max(0, min(100, score))
        play["hypothesis_strength"] = play["play_score"]
        play["score_label"] = "Internal compatibility score"
        play["play_classification"] = _classify_play_score(play["play_score"])
        evidence_coverage = _evidence_coverage(
            has_signal=has_signal,
            has_service=has_service,
            has_question=bool(play["discovery_questions"]),
            low_evidence=low_evidence,
        )
        if sufficiency_status == "partial":
            evidence_coverage = "Partial governed evidence coverage; validate independence, freshness, entity scope, and claim coverage in discovery."
        readiness = _readiness_band(
            play["play_score"],
            has_signal=has_signal,
            has_service=has_service,
            has_question=bool(play["discovery_questions"]),
            gate=play.get("gate"),
        )
        if readiness == "Validate now" and not _has_current_timing_basis(activation_signal, play):
            readiness = "Validate when trigger appears"
            play["timing_boundary"] = "Current timing is not independently established; validate the trigger before treating this as immediate demand."
        play["readiness_band"] = readiness
        play["evidence_coverage"] = evidence_coverage
        play["confidence_boundary"] = _confidence_boundary(play.get("gate"), play["evidence_coverage"])
        play["priority_band"] = _priority_band(play["readiness_band"])
        play["response_directions"] = _dict(play.get("response_directions")) or _response_directions_from_play(play, play["need_hypothesis"])
        response_directions = play["response_directions"]
        default_response = _response_directions_from_play(play, play["need_hypothesis"])
        response_directions["potential_need"] = _clip(response_directions.get("potential_need") or default_response["potential_need"], 700)
        response_directions["evolving_modernization_directions"] = [
            str(item) for item in (_list(response_directions.get("evolving_modernization_directions")) or default_response["evolving_modernization_directions"])[:5]
        ]
        response_directions["vivicta_potential_contribution"] = _clip(
            response_directions.get("vivicta_potential_contribution") or default_response["vivicta_potential_contribution"],
            420,
        )
        response_directions["other_capabilities_may_be_required"] = [
            str(item) for item in (_list(response_directions.get("other_capabilities_may_be_required")) or default_response["other_capabilities_may_be_required"])[:6]
        ]
        play_match_text = " ".join(
            _clip(play.get(key), 700)
            for key in (
                "buying_hypothesis",
                "trigger_to_offer_bridge",
                "business_hypothesis",
                "why_relevant",
            )
            if play.get(key)
        )
        play_match_text += " " + " ".join(
            _clip(play["need_hypothesis"].get(key), 700)
            for key in (
                "future_pressure",
                "current_condition",
                "structural_exposure",
                "what_to_propose",
            )
            if play["need_hypothesis"].get(key)
        )
        tactic_matches = _match_modernization_tactics(play_match_text, modernization_tactics, limit=3) if has_signal else []
        if tactic_matches:
            response_directions["modernization_lenses"] = tactic_matches
            response_directions["evolving_modernization_directions"] = [
                match.get("lens") for match in tactic_matches if match.get("lens")
            ][:3]
        else:
            response_directions.pop("modernization_lenses", None)
            response_directions["evolving_modernization_directions"] = []
        if play["play_classification"] in {"weak_signal", "do_not_use"} and play.get("gate") != "blocked":
            play["gate"] = "internal_only"
            play["confidence_boundary"] = _confidence_boundary(play.get("gate"), play["evidence_coverage"])
        for text_key in (
            "play_name",
            "service_or_offer",
            "trigger",
            "account_thesis",
            "buying_hypothesis",
            "trigger_to_offer_bridge",
            "business_hypothesis",
            "why_relevant",
            "opening_angle",
            "executive_angle",
            "technical_angle",
            "safe_customer_wording",
            "proof_point",
        ):
            play[text_key] = _strip_archetype_terms(play.get(text_key))
        play["play_name"] = _normalize_discovery_play_title(
            play.get("play_name"),
            play.get("service_or_offer") or play.get("trigger"),
        )
        play["need_hypothesis"] = _strip_archetype_terms(play["need_hypothesis"])
        play["response_directions"] = _strip_archetype_terms(play["response_directions"])
    fixed_stakeholders = []
    for item in _list(report.get("stakeholder_map")):
        if not isinstance(item, dict):
            continue
        fixed = fix_item(item, "internal_only")
        fixed["relevance_rationale"] = _clip(
            fixed.get("relevance_rationale") or fixed.get("insight") or fixed.get("likely_buying_committee_role"),
            520,
        )
        fixed["linked_pressure_themes"] = [str(x) for x in _list(fixed.get("linked_pressure_themes"))[:8] if x]
        fixed["linked_service_themes"] = [str(x) for x in _list(fixed.get("linked_service_themes"))[:8] if x]
        fixed["do_not_say"] = [str(x) for x in _list(fixed.get("do_not_say"))[:5] if x]
        fixed["safety_notes"] = [str(x) for x in _list(fixed.get("safety_notes"))[:5] if x]
        if not fixed["do_not_say"]:
            fixed["do_not_say"] = ["Do not imply confirmed buying intent from role/title alone."]
        if not fixed["safety_notes"]:
            fixed["safety_notes"] = ["Use as sales-prep context; validate current role and remit before outreach."]
        basis_text = str(fixed.get("evidence_basis") or "").strip()
        if basis_text.lower().endswith("use only"):
            fixed["evidence_basis"] = basis_text + " as sales-prep context."
        fixed_stakeholders.append(fixed)
    if not fixed_stakeholders:
        fixed_stakeholders = [
            fix_item(item, "allowed_as_discovery_question")
            for item in _fallback_stakeholder_map(payload, signals_by_id)
            if isinstance(item, dict)
        ]
    if len(fixed_stakeholders) < 3:
        for item in _role_level_stakeholder_map(signals_by_id, fixed_stakeholders, limit=3 - len(fixed_stakeholders)):
            if isinstance(item, dict):
                fixed_stakeholders.append(fix_item(item, "allowed_as_discovery_question"))
    has_role_level_guidance = any(
        str(item.get("name") or "").lower() == "role-level target"
        or str(item.get("evidence_basis") or "").startswith("SIG-")
        for item in fixed_stakeholders
        if isinstance(item, dict)
    )
    if not has_role_level_guidance and len(fixed_stakeholders) < 6:
        for item in _role_level_stakeholder_map(signals_by_id, fixed_stakeholders, limit=min(2, 6 - len(fixed_stakeholders))):
            if isinstance(item, dict):
                fixed_stakeholders.append(fix_item(item, "allowed_as_discovery_question"))
    report["stakeholder_map"] = fixed_stakeholders
    extra_people = []
    extra_seen: set[str] = set()
    for person in _list(report.get("additional_stakeholder_names")) + _list(payload.get("stakeholder_name_list")):
        if not isinstance(person, dict):
            continue
        _add_simple_stakeholder(
            extra_people,
            extra_seen,
            _simple_stakeholder_item(
                person.get("name"),
                person.get("position") or person.get("role") or person.get("title"),
                source_database=person.get("source_database") or "buying_insight",
                source=person.get("source"),
                evidence_basis=person.get("evidence_basis"),
                allowed_use=person.get("allowed_use") or person.get("sales_use"),
                confidence=person.get("confidence"),
            ),
        )
    extra_people.sort(key=_stakeholder_sort_score)
    report["additional_stakeholder_names"] = extra_people[:6]
    report.pop("conversation_angles", None)

    if low_evidence:
        warnings = _list(report.get("missing_input_warnings"))
        warnings.append("Low promoted evidence: keep customer-facing wording cautious and question-led.")
        report["missing_input_warnings"] = warnings
        evidence_confidence.setdefault("weak_or_missing_evidence", []).append("Few generation-eligible source-register items were available.")
    if not _list(evidence_confidence.get("safe_hypotheses")):
        evidence_confidence["safe_hypotheses"] = [
            _clip(item.get("trigger") or item.get("description") or item.get("business_consequence"), 260)
            for item in _list(report.get("pressure_signals"))[:6]
            if isinstance(item, dict) and item.get("gate") == "allowed_as_hypothesis"
        ]
    evidence_confidence.setdefault("use_only_as_question", [])
    evidence_confidence["use_only_as_question"] = list(dict.fromkeys(
        [str(item) for item in _list(evidence_confidence.get("use_only_as_question"))]
        + [str(item) for item in _list(evidence_confidence.get("claims_as_questions_only"))]
        + [
            _clip(item.get("trigger") or item.get("description"), 260)
            for item in _list(report.get("pressure_signals"))[:6]
            if isinstance(item, dict) and item.get("gate") == "allowed_as_discovery_question"
        ]
    ))[:12]
    unsafe_claims, question_only_from_guardrails = _safe_guardrail_items(
        _list(evidence_confidence.get("do_not_say"))
        + _list(evidence_confidence.get("do_not_use_claims"))
    )
    evidence_confidence["use_only_as_question"] = list(dict.fromkeys(
        _list(evidence_confidence.get("use_only_as_question"))
        + question_only_from_guardrails
    ))[:12]
    false_or_unsupported = [
        _clip(item, 520)
        for item in _list(_dict(scanner_outputs.get("evidence_trust")).get("false_or_unsupported_claims"))
        if _clip(item, 520)
    ]
    unsafe_false, question_only_false = _safe_guardrail_items(false_or_unsupported)
    unsafe_claims.extend(unsafe_false)
    evidence_confidence["use_only_as_question"] = list(dict.fromkeys(
        _list(evidence_confidence.get("use_only_as_question"))
        + question_only_false
    ))[:12]
    evidence_confidence["do_not_say"] = list(dict.fromkeys(unsafe_claims))[:12]
    evidence_confidence["do_not_use_claims"] = evidence_confidence["do_not_say"]
    report["evidence_confidence"] = evidence_confidence

    if gate_review:
        applied_field_actions, rejected_field_actions = _apply_gate_field_actions(
            report,
            gate_review.get("field_actions"),
        )
        propagated_field_actions = _propagate_gate_field_actions(report, applied_field_actions)
        rejected_claim_ids = {
            str(item.get("claim_id")) for item in rejected_field_actions
            if isinstance(item, dict) and item.get("claim_id")
        }
        rejected_claim_text = [
            _clean_generated_text(item.get("unsafe_text"))
            for item in _list(gate_review.get("claim_decisions"))
            if isinstance(item, dict)
            and str(item.get("claim_id")) in rejected_claim_ids
            and _clean_generated_text(item.get("unsafe_text"))
        ]
        downgrades = _list(gate_review.get("required_downgrades"))
        for downgrade in downgrades:
            if not isinstance(downgrade, dict):
                continue
            new_gate = _normalize_gate(downgrade.get("new_gate"), "allowed_as_discovery_question")
            path = str(downgrade.get("path") or "")
            if path in {"account_snapshot", "buying_potential_thesis"} and isinstance(report.get(path), dict):
                report[path]["gate"] = new_gate
                continue
            match = re.match(r"^([a-zA-Z_]+)\[(\d+)\]", path)
            if match:
                key, index_text = match.groups()
                items = report.get(key)
                index = _safe_int(index_text, -1)
                if isinstance(items, list) and 0 <= index < len(items) and isinstance(items[index], dict):
                    items[index]["gate"] = new_gate
        blocked = _list(gate_review.get("blocked_claims")) + rejected_claim_text
        blocked_claims = [_clip(item, 520) for item in blocked[:12] if _clip(item, 520)]
        removed_public_claims = _remove_blocked_claims_from_public_sections(report, blocked_claims)
        blocked_guardrails, blocked_questions = _safe_guardrail_items(blocked_claims)
        if blocked_guardrails:
            evidence_confidence.setdefault("do_not_say", []).extend(blocked_guardrails)
        if blocked_questions:
            evidence_confidence.setdefault("use_only_as_question", []).extend(blocked_questions)
        report["gate_review"] = {
            "status": gate_review.get("status") or ("approved" if gate_review.get("approved") is True else "unavailable"),
            "approved": gate_review.get("approved") is True,
            "model": gate_review.get("model"),
            "decision_model": gate_review.get("decision_model") or gate_review.get("model"),
            "repair_models": _list(gate_review.get("repair_models")),
            "reanalysis_models": _list(gate_review.get("reanalysis_models")),
            "reviewed_claim_ids": _list(gate_review.get("reviewed_claim_ids"))[:80],
            "claim_decisions": _list(gate_review.get("claim_decisions"))[:40],
            "required_downgrades": downgrades[:20],
            "applied_field_actions": applied_field_actions[:20],
            "rejected_field_actions": rejected_field_actions[:20],
            "propagated_field_action_count": propagated_field_actions,
            "removed_public_claim_count": removed_public_claims,
            "deterministic_removal_count": _safe_int(gate_review.get("deterministic_removal_count"), 0),
            "deterministically_removed_claim_ids": _list(gate_review.get("deterministically_removed_claim_ids"))[:40],
            "blocked_claims": blocked[:20],
            "notes": _list(gate_review.get("notes"))[:10],
        }
        quality_notes = evidence_confidence.setdefault("report_quality_notes", [])
        if gate_review.get("status") == "unavailable":
            quality_notes.append(
                "Independent semantic Gate Review was unavailable; deterministic evidence controls were applied and reliability was capped at Question-Led Draft."
            )
        elif gate_review.get("status") == "changes_required":
            quality_notes.append(
                f"Independent semantic Gate Review applied {len(applied_field_actions)} field action(s), "
                f"propagated {propagated_field_actions} repeated occurrence(s), and applied {len(downgrades)} gate downgrade(s)."
            )
        if rejected_field_actions:
            quality_notes.append(
                f"{len(rejected_field_actions)} Gate Review action(s) could not target the requested field; "
                "their flagged wording was removed from reader-facing sections by the final safety check."
            )
        if _safe_int(gate_review.get("deterministic_removal_count"), 0):
            quality_notes.append(
                f"{_safe_int(gate_review.get('deterministic_removal_count'), 0)} unresolved flagged claim(s) "
                "were removed deterministically after bounded repair/reanalysis could not complete them."
            )
        clean_do_not, clean_questions = _safe_guardrail_items(_list(evidence_confidence.get("do_not_say")))
        evidence_confidence["do_not_say"] = clean_do_not[:12]
        evidence_confidence["use_only_as_question"] = list(dict.fromkeys(
            _list(evidence_confidence.get("use_only_as_question"))
            + clean_questions
        ))[:12]
        evidence_confidence["do_not_use_claims"] = evidence_confidence["do_not_say"]

    evidence_confidence = _clean_evidence_confidence_buckets(evidence_confidence)
    report["account_snapshot"] = _strip_archetype_terms(report.get("account_snapshot"))
    report["buying_potential_thesis"] = _strip_archetype_terms(report.get("buying_potential_thesis"))
    report["sales_spine"] = _strip_archetype_terms(report.get("sales_spine"))
    report["pressure_signals"] = _strip_archetype_terms(report.get("pressure_signals"))
    report["why_now"] = _strip_archetype_terms(report.get("why_now"))
    report["pain_to_solution_map"] = _strip_archetype_terms(report.get("pain_to_solution_map"))
    report["recommended_sales_plays"] = _strip_archetype_terms(report.get("recommended_sales_plays"))
    report["evidence_confidence"] = _strip_archetype_terms(evidence_confidence)
    _enrich_short_opening_sections(report, payload)

    # Retained only as a nullable compatibility field for older stored reports.
    # Reliability Status and governed evidence sufficiency are authoritative.
    report["overall_confidence"] = None
    report["report_type"] = "buying_potential"
    report["generated_at"] = datetime.utcnow().isoformat()
    report["safety_statement"] = "Need hypothesis and demand-creation potential, not confirmed buying intent. Customer-facing use must follow each insight gate."
    report["self_scan_context"] = self_scan_context
    report["source_counts"] = {
        "generation_eligible": verified_count,
        "financial_sources": financial_count,
        "blocked_or_display_only": len(payload.get("blocked_or_display_only_sources") or []),
    }
    report["quality_controls"] = {
        "reasoning_verification": {
            "general_summary_verified": _dict(scanner_outputs.get("general_summary_fact_check")).get("verified") is True,
            "general_summary_false_claim_count": len(_list(_dict(scanner_outputs.get("general_summary_fact_check")).get("false_or_unsupported_claims"))),
            "evidence_sufficiency": sufficiency_status or "unknown",
            "gate_review_status": gate_review.get("status") if gate_review else "unavailable",
            "gate_review_approved": gate_review.get("status") == "approved" if gate_review else None,
            "hypothesis_registry_count": len(hypothesis_registry),
        },
        "hygiene_repair": {
            "section_generation": _list(report.get("section_generation")),
            "report_quality_notes": _list(_dict(report.get("evidence_confidence")).get("report_quality_notes")),
        },
    }
    reliability_status = _compute_reliability_status(report, payload, gate_review, low_evidence=low_evidence)
    report["reliability_status"] = reliability_status
    report["publication_status"] = reliability_status.get("publication_status") or "internal_review_required"
    report["status_reasons"] = _list(reliability_status.get("reasons"))
    report["status_scale"] = _status_scale()
    return _polish_generated_text_tree(report, _dict(payload.get("company")).get("name"))


def _first_nonempty(*values: Any, limit: int = 900) -> str:
    for value in values:
        text = _clip(value, limit)
        if text:
            return text
    return ""


def _basis_from_payload(payload: dict, limit: int = 4) -> list[str]:
    basis = [item.get("id") for item in _list(payload.get("verified_evidence")) if isinstance(item, dict) and item.get("id")]
    basis.extend(item.get("id") for item in _list(payload.get("financial_sources")) if isinstance(item, dict) and item.get("id"))
    return [str(item) for item in basis[:limit]]


def _fact_text_from_source_item(item: dict, *, limit: int = 260) -> str:
    combined = " ".join(
        str(item.get(key) or "")
        for key in ("id", "title", "description", "source_quote", "snippet", "claim", "finding")
    )
    if _looks_like_context_not_fact(combined):
        return ""
    candidates = (
        item.get("fact"),
        item.get("statement"),
        item.get("claim"),
        item.get("source_quote"),
        item.get("quote"),
        item.get("snippet"),
        item.get("text_excerpt"),
        item.get("description"),
        item.get("finding"),
        item.get("title"),
        item.get("url"),
    )
    title = str(item.get("title") or "").strip().lower()
    for candidate in candidates:
        text = _clean_fact_statement(candidate, limit=limit)
        if not text:
            continue
        text_l = text.lower().strip()
        if text_l == title and len(text.split()) <= 6:
            continue
        if len(text.split()) <= 2 and not re.search(r"\d", text):
            continue
        return text
    return ""


def _verified_fact_items_from_payload(payload: dict, limit: int = 12) -> list[str]:
    facts: list[str] = []
    for item in _list(payload.get("verified_evidence")):
        if not isinstance(item, dict) or _source_item_gate(item) != "allowed_as_fact":
            continue
        text = _fact_text_from_source_item(item)
        if text:
            facts.append(f"{item.get('id')}: {text}" if item.get("id") else text)
    for item in _list(payload.get("financial_sources")):
        if not isinstance(item, dict):
            continue
        if not (item.get("generation_eligible") or _source_item_gate(item) == "allowed_as_fact"):
            continue
        metric_text = " ".join(str(part).strip() for part in (item.get("metric"), item.get("value"), item.get("year")) if str(part or "").strip())
        text = _first_nonempty(
            metric_text if metric_text and metric_text.lower() != "financial reference" else "",
            _clean_fact_statement(item.get("source_quote")),
            _clean_fact_statement(item.get("quote")),
            _clean_fact_statement(item.get("snippet")),
            _clean_fact_statement(item.get("title")),
            limit=260,
        )
        if text:
            facts.append(f"{item.get('id')}: {text}" if item.get("id") else text)
    return list(dict.fromkeys(facts))[:limit]


def _deterministic_buying_report(payload: dict, reason: str) -> dict:
    company = _dict(payload.get("company"))
    outputs = _dict(payload.get("scanner_outputs"))
    self_scan_context = _dict(payload.get("self_scan_context"))
    company_name = company.get("name") or "the company"
    basis = _basis_from_payload(payload)
    services = [svc for svc in _list(payload.get("services")) if isinstance(svc, dict)]
    stakeholders = [person for person in _list(payload.get("stakeholders")) if isinstance(person, dict)]
    account_signals = [signal for signal in _list(payload.get("account_signal_map")) if isinstance(signal, dict)]
    evidence = [item for item in _list(payload.get("verified_evidence")) if isinstance(item, dict)]
    financial = [item for item in _list(payload.get("financial_sources")) if isinstance(item, dict)]
    weak = [item for item in _list(payload.get("blocked_or_display_only_sources")) if isinstance(item, dict)]

    snapshot_text = _first_nonempty(
        outputs.get("general_summary"),
        f"{company_name} has stored Scanner analysis, but the Buying Insight generator could not return clean JSON.",
        limit=700,
    )
    thesis_seed = _first_nonempty(outputs.get("ai_diagnosis"), outputs.get("pitch"), limit=650)
    top_signals = account_signals[:3]
    signal_summary = "; ".join(_clip(signal.get("pressure_type") or signal.get("commercial_pressure"), 120) for signal in top_signals)
    thesis_text = (
        f"{company_name} shows cautious demand-creation potential where account-specific signals suggest {signal_summary or 'commercial pressure that needs discovery validation'}. "
        "This is a falsifiable need hypothesis, not confirmed buying intent."
    )
    if thesis_seed:
        thesis_text = f"{thesis_text} Scanner context: {_clip(thesis_seed, 520)}"
    activation_signals = [
        signal for signal in account_signals
        if signal.get("source") == "scanner_activation_pair" or signal.get("activation_pair_index")
    ]
    sales_spine = {
        "account_thesis": _clip(outputs.get("strategic_synthesis") or thesis_text, 900),
        "strategic_synthesis_basis": _clip(outputs.get("strategic_synthesis"), 1200),
        "top_buying_hypotheses": [
            _clip(signal.get("theme") or signal.get("why") or signal.get("account_movement"), 220)
            for signal in activation_signals[:3]
        ],
        "risk_themes": [_clip(item, 240) for item in _list(outputs.get("evidence_based_risks"))[:4]],
        "self_scan_note": self_scan_context.get("framing") or "",
    }

    pressure_signals = []
    for signal in account_signals[:5]:
        description = _first_nonempty(signal.get("commercial_pressure"), signal.get("business_consequence"), signal.get("account_movement"), limit=360)
        if not description:
            continue
        pressure_signals.append({
            "signal_type": signal.get("pressure_type") or "Account pressure signal",
            "trigger": _clip(signal.get("account_movement"), 180),
            "description": description,
            "business_consequence": _clip(signal.get("business_consequence"), 320),
            "buyer_role_concern": _clip(signal.get("buyer_role_concern"), 320),
            "gate": signal.get("gate") or "allowed_as_hypothesis",
            "confidence": _safe_int(signal.get("confidence"), 45),
            "evidence_basis": [signal.get("id")],
        })
    if not pressure_signals and thesis_seed:
        pressure_signals.append({
            "signal_type": "Scanner diagnosis pressure",
            "description": thesis_seed,
            "gate": "allowed_as_hypothesis",
            "confidence": 45,
            "evidence_basis": basis,
        })

    why_now = []
    for signal in account_signals[:4]:
        why_now.append({
            "trigger": _clip(signal.get("account_movement") or signal.get("pressure_type"), 160),
            "why_it_matters_now": _clip(signal.get("timing_relevance"), 260),
            "buyer_relevance": _clip(signal.get("buyer_role_concern"), 280),
            "safe_discovery_question": BUYER_ROLE_LENSES.get(_list(signal.get("buyer_roles"))[0] if _list(signal.get("buyer_roles")) else "CEO / Managing Director", {}).get("safe_question"),
            "gate": "allowed_as_discovery_question",
            "confidence": _safe_int(signal.get("confidence"), 45),
            "evidence_basis": [signal.get("id")],
        })

    stakeholder_map = []
    for person in stakeholders[:8]:
        matched_groups = [str(x) for x in _list(person.get("matched_demand_groups"))[:6] if x]
        service_themes = [str(x) for x in _list(person.get("linked_service_ids"))[:5] if x]
        role_reason = person.get("stakeholder_role_match_reason") or "Professional role may be relevant to account qualification."
        stakeholder_map.append({
            "name": person.get("name"),
            "role": person.get("role"),
            "likely_buying_committee_role": _clip(role_reason, 260),
            "relevance_rationale": _clip(person.get("insight") or role_reason, 420),
            "linked_pressure_themes": matched_groups,
            "linked_service_themes": service_themes,
            "evidence_basis": person.get("evidence_basis") or person.get("source") or "Stored Scanner stakeholder compatibility data",
            "confidence": _safe_int(person.get("confidence"), 45),
            "allowed_use": person.get("allowed_use") or "internal_only",
            "do_not_say": _list(person.get("do_not_say"))[:4] or ["Do not imply this person has confirmed buying intent."],
            "safety_notes": _list(person.get("safety_notes"))[:4] or ["Source-bound professional role context only; validate current remit before use."],
        })
    if not stakeholder_map:
        for signal in account_signals[:4]:
            role = (_list(signal.get("buyer_roles")) or ["CEO / Managing Director"])[0]
            stakeholder_map.append({
                "name": "Role-level target",
                "role": role,
                "likely_buying_committee_role": _clip(signal.get("buyer_role_concern"), 260),
                "relevance_rationale": _clip(signal.get("commercial_pressure") or signal.get("business_consequence"), 360),
                "linked_pressure_themes": [signal.get("pressure_type")] if signal.get("pressure_type") else [],
                "linked_service_themes": _list(signal.get("service_fit_hints"))[:5],
                "evidence_basis": signal.get("id"),
                "confidence": _safe_int(signal.get("confidence"), 40),
                "allowed_use": "allowed_as_discovery_question",
                "do_not_say": ["Do not claim a named stakeholder exists without source-bound evidence."],
                "safety_notes": ["Role-level buying committee guidance generated from account signals."],
            })

    pain_to_solution = []
    sales_plays = []
    for signal in account_signals[:3]:
        for hint in _list(signal.get("service_fit_hints"))[:1]:
            if not isinstance(hint, dict):
                continue
            service_name = hint.get("service") or hint.get("category") or "Service"
            play_type = _normalize_play_type(signal.get("sales_play_type") or signal.get("theme_type"))
            safe_wording = _first_nonempty(*_list(hint.get("safe_wording"))[:2], f"Explore whether {service_name} is relevant.", limit=260)
            trigger = _first_nonempty(signal.get("commercial_pressure"), signal.get("account_movement"), limit=260)
            buyer_roles = _list(signal.get("buyer_roles"))
            primary_buyer = buyer_roles[0] if buyer_roles else "CEO / Managing Director"
            safe_question = BUYER_ROLE_LENSES.get(primary_buyer, {}).get("safe_question") or "Which pressure should be validated first?"
            score = _score_play({}, has_signal=True, has_service=True, has_question=True)
            classification = _classify_play_score(score)
            disqualifiers = _list(hint.get("disqualifiers"))[:4] or _list(signal.get("disqualifiers"))[:4]
            proof_point = _first_nonempty(*_list(hint.get("proof_points"))[:2], "Validate proof point before customer-facing use.", limit=240)
            do_not_claim = _list(hint.get("do_not_claim"))[:3]
            need_hypothesis = signal.get("need_hypothesis") or _need_hypothesis_from_signal(signal)
            fallback_play = {
                "service_or_offer": service_name,
                "buying_hypothesis": _clip(signal.get("why") or signal.get("commercial_pressure"), 300),
                "why_relevant": trigger,
                "trigger_to_offer_bridge": _clip(signal.get("what_to_do") or signal.get("what") or trigger, 300),
                "proof_point": _clip(signal.get("what_progress_shows") or proof_point, 300),
            }
            readiness_band = _readiness_band(score, has_signal=True, has_service=True, has_question=True, gate="allowed_as_discovery_question")
            evidence_coverage = _evidence_coverage(has_signal=True, has_service=True, has_question=True)
            pain_to_solution.append({
                "account_signal_id": signal.get("id"),
                "evidence": _clip(signal.get("account_movement"), 260),
                "interpreted_pressure": trigger,
                "buyer_lens": _clip(signal.get("buyer_role_concern"), 280),
                "service": service_name,
                "why_this_service": f"{service_name} may be relevant if the account pressure is confirmed in discovery.",
                "proof_point": proof_point,
                "safe_wording": safe_wording,
                "missing_evidence": "Customer confirmation or stronger project evidence is needed before treating this as an active opportunity.",
                "gate": "allowed_as_discovery_question",
                "confidence": min(65, _safe_int(signal.get("confidence"), 45)),
                "disqualifiers": disqualifiers,
            })
            sales_plays.append({
                "play_name": _normalize_discovery_play_title(service_name),
                "linked_signal_ids": [signal.get("id")],
                "activation_pair_index": signal.get("activation_pair_index"),
                "play_type": play_type,
                "service_or_offer": service_name,
                "need_hypothesis": need_hypothesis,
                "response_directions": _response_directions_from_play(fallback_play, need_hypothesis),
                "readiness_band": readiness_band,
                "evidence_coverage": evidence_coverage,
                "confidence_boundary": _confidence_boundary("allowed_as_discovery_question", evidence_coverage),
                "priority_band": _priority_band(readiness_band),
                "primary_buyer": primary_buyer,
                "secondary_buyers": buyer_roles[1:4],
                "trigger": _clip(signal.get("account_movement"), 220),
                "account_thesis": sales_spine.get("account_thesis"),
                "buying_hypothesis": _clip(signal.get("why") or signal.get("commercial_pressure"), 300),
                "trigger_to_offer_bridge": _clip(signal.get("what_to_do") or signal.get("what") or trigger, 300),
                "business_hypothesis": _clip(signal.get("business_consequence"), 300),
                "why_relevant": trigger,
                "target_stakeholder": primary_buyer,
                "first_meeting_objective": "Validate whether the account signal is creating a real business pressure, owner, and timing window.",
                "opening_angle": safe_wording,
                "executive_angle": safe_wording,
                "technical_angle": "Validate the current architecture, data, automation, security, or operating-model baseline before proposing a solution.",
                "safe_customer_wording": safe_wording,
                "discovery_questions": [safe_question],
                "discovery_questions_by_role": [{"role": primary_buyer, "questions": [safe_question]}],
                "proof_point": _clip(signal.get("what_progress_shows") or proof_point, 300),
                "do_not_say": do_not_claim + [
                    "Do not state confirmed buying intent or imply a project exists unless the customer confirms it.",
                    "Do not present service fit as fact when it is based on Scanner interpretation or weak public signals.",
                ],
                "qualification_upgrade_criteria": [
                    "Customer confirms the pressure",
                    "A public source confirms a project or program",
                    "A stakeholder or procurement signal identifies ownership or timing",
                ],
                "confidence": min(65, _safe_int(signal.get("confidence"), 45)),
                "play_score": score,
                "hypothesis_strength": score,
                "score_label": "Internal compatibility score",
                "play_classification": classification,
                "overclaim_risk": "medium",
                "evidence_basis": [signal.get("id")],
                "disqualifiers": disqualifiers,
            })
            if len(sales_plays) >= 3:
                break
        if len(sales_plays) >= 3:
            break
    if not sales_plays and services[:1]:
        svc = services[0]
        service_name = svc.get("name") or svc.get("category") or "Service"
        safe_wording = _first_nonempty(*_list(svc.get("safe_wording"))[:2], f"Explore whether {service_name} is relevant.", limit=260)
        pain_to_solution.append({
            "account_signal_id": "",
            "evidence": "No strong account-signal-to-service bridge was available.",
            "interpreted_pressure": "Discovery-only service relevance; validate account pressure before use.",
            "buyer_lens": "Role relevance must be validated before outreach.",
            "service": service_name,
            "why_this_service": "Potential fit is not account-specific yet.",
            "proof_point": _first_nonempty(*_list(svc.get("proof_points"))[:2], "Validate proof point before customer-facing use.", limit=240),
            "safe_wording": safe_wording,
            "missing_evidence": "Needs a clear account signal and buyer owner.",
            "gate": "allowed_as_discovery_question",
            "confidence": 25,
            "disqualifiers": _list(svc.get("disqualifiers"))[:4],
        })
        weak_need = {
            "future_pressure": "No reliable future pressure has been confirmed for this service area.",
            "current_condition": "Stored evidence is too thin for an account-specific need hypothesis.",
            "structural_exposure": "Unknown until discovery identifies a real owner, pressure, or operating constraint.",
            "likely_business_consequence": "No business consequence should be asserted before account validation.",
            "why_now_or_soon": "Timing is not proven; use only as a monitoring or soft-discovery theme.",
            "what_to_propose": "Validate whether the service area is relevant before proposing an offer.",
            "discovery_question": "Is this service area connected to any current priority, constraint, or future pressure?",
            "disqualifier": "Do not advance if the customer confirms no current or emerging pressure in this area.",
            "company_context": "Account-specific exposure is not established.",
            "market_context": "Market relevance is not established for this account.",
            "combined_activation_hypothesis": "No combined activation hypothesis should be asserted until account pressure and service relevance are validated.",
            "falsifier": "The hypothesis is rejected if no account owner, pressure, timing, or business consequence is confirmed.",
            "context_type": "market_watch",
        }
        weak_readiness = "Insufficient account connection"
        weak_coverage = "Insufficient account-specific evidence"
        sales_plays.append({
            "play_name": f"{service_name} monitoring play",
            "linked_signal_ids": [],
            "activation_pair_index": None,
            "play_type": "proactive_opportunity",
            "service_or_offer": service_name,
            "need_hypothesis": weak_need,
            "response_directions": _response_directions_from_play({"service_or_offer": service_name}, weak_need),
            "readiness_band": weak_readiness,
            "evidence_coverage": weak_coverage,
            "confidence_boundary": _confidence_boundary("allowed_as_discovery_question", weak_coverage),
            "priority_band": _priority_band(weak_readiness),
            "primary_buyer": "Role to validate",
            "secondary_buyers": [],
            "trigger": "No strong account-specific trigger available.",
            "account_thesis": sales_spine.get("account_thesis"),
            "buying_hypothesis": "Service fit is weak until account pressure is confirmed.",
            "trigger_to_offer_bridge": "Validate account pressure before mapping to an offer.",
            "business_hypothesis": "Service fit is weak until account pressure is confirmed.",
            "why_relevant": "Potential fit exists only as a monitoring or discovery theme.",
            "target_stakeholder": "Validate buyer role before use.",
            "first_meeting_objective": "Confirm whether this service area is relevant at all.",
            "opening_angle": safe_wording,
            "executive_angle": safe_wording,
            "technical_angle": "Validate the current architecture, data, automation, security, or workplace baseline before proposing a solution.",
            "safe_customer_wording": safe_wording,
            "discovery_questions": [
                "Which current transformation, modernization, or operating-model pressure is most visible internally right now?",
                f"Where would {service_name} be most useful if the pressure signal is confirmed?",
            ],
            "discovery_questions_by_role": [{"role": "Role to validate", "questions": [
                "Which current transformation, modernization, or operating-model pressure is most visible internally right now?",
                f"Where would {service_name} be most useful if the pressure signal is confirmed?",
            ]}],
            "do_not_say": [
                "Do not state confirmed buying intent or imply a project exists unless the customer confirms it.",
                "Do not present service fit as fact when it is based on Scanner interpretation or weak public signals.",
            ],
            "qualification_upgrade_criteria": ["Find a current account-specific signal", "Identify a buyer owner", "Confirm timing or pain"],
            "confidence": 25,
            "play_score": 25,
            "hypothesis_strength": 25,
            "score_label": "Internal compatibility score",
            "play_classification": "weak_signal",
            "overclaim_risk": "high",
            "evidence_basis": basis,
            "disqualifiers": _list(svc.get("disqualifiers"))[:4],
        })

    strongest = [
        f"{item.get('id')}: {item.get('title') or item.get('quote') or item.get('url')}"
        for item in (evidence + financial)[:8]
    ]
    weak_notes: list[str] = []
    analysis_context: list[str] = []
    for item in weak[:12]:
        title = _clip(item.get("title") or item.get("url"), 180)
        reason = _clip(item.get("verification_reason") or item.get("promotion_status") or "not generation eligible", 160)
        if not title:
            continue
        if "verified_for_analysis" in reason.lower():
            analysis_context.append(title)
        else:
            weak_notes.append(f"{title}: {reason}")

    return {
        "account_snapshot": {"text": snapshot_text, "gate": "allowed_as_hypothesis", "evidence_basis": basis},
        "sales_spine": sales_spine,
        "buying_potential_thesis": {"text": thesis_text[:1200], "gate": "allowed_as_hypothesis", "confidence": 42},
        "pressure_signals": pressure_signals,
        "why_now": why_now,
        "stakeholder_map": stakeholder_map,
        "additional_stakeholder_names": _list(payload.get("stakeholder_name_list"))[:6],
        "pain_to_solution_map": pain_to_solution,
        "recommended_sales_plays": sales_plays,
        "evidence_confidence": {
            "strongest_evidence": strongest,
            "verified_facts": [],
            "safe_hypotheses": [
                _clip(signal.get("why") or signal.get("commercial_pressure") or signal.get("account_movement"), 260)
                for signal in activation_signals[:3]
            ],
            "use_only_as_question": ["Service-fit and timing signals should be used as discovery questions unless independently verified."],
            "analysis_context": analysis_context,
            "weak_or_missing_evidence": weak_notes,
            "report_quality_notes": ["Generative report JSON was malformed; deterministic fallback used."],
            "do_not_use_claims": _list(_dict(outputs.get("evidence_trust")).get("false_or_unsupported_claims"))[:10],
            "do_not_say": _list(_dict(outputs.get("evidence_trust")).get("false_or_unsupported_claims"))[:10],
            "sensitive_claims": [],
            "claims_as_questions_only": ["Service-fit and timing signals should be used as discovery questions unless independently verified."],
        },
        "overall_confidence": None,
        "missing_input_warnings": [
            "Generated with deterministic fallback after malformed model JSON.",
            _clip(reason, 220),
        ],
        "model_notes": ["Deterministic fallback used; no new web search or external enrichment was performed."],
    }


def _parse_or_repair_report(
    raw: str,
    payload: dict,
    model_mode: str | None = None,
    stage_callback=None,
) -> tuple[dict, str]:
    try:
        report = parse_json_object(raw)
        if not isinstance(report, dict):
            raise ValueError("Report JSON root was not an object")
        return report, "parsed"
    except Exception as parse_exc:
        if stage_callback:
            stage_callback("generate_report", 58, "Repairing malformed Buying Insight JSON")
        try:
            repair_raw, repair_model = call_json_model_chain(
                _report_repair_prompt(raw),
                buying_report_repair_chain(model_mode),
                max_tokens=5200,
                timeout=90,
                validate_json=True,
            )
            report = parse_json_object(repair_raw)
            if not isinstance(report, dict):
                raise ValueError("Repaired report JSON root was not an object")
            notes = _list(report.get("model_notes"))
            notes.append(f"Malformed report JSON repaired by {repair_model}. Original parse error: {str(parse_exc)[:160]}")
            report["model_notes"] = notes
            return report, f"repaired by {repair_model}"
        except Exception as repair_exc:
            if stage_callback:
                stage_callback("generate_report", 62, "Using cautious fallback report after JSON repair failed")
            reason = f"Report JSON parse failed ({parse_exc}); repair failed ({repair_exc})"
            return _deterministic_buying_report(payload, reason), "deterministic_fallback"


def _section_fallback(section: str, payload: dict, reason: str) -> dict:
    fallback = _deterministic_buying_report(payload, reason)
    section_data = {key: fallback.get(key) for key in BUYING_SECTION_KEYS[section] if key in fallback}
    section_data["model_notes"] = [reason]
    return section_data


def _section_token_limit(section: str) -> int:
    policy = policy_for("buying_insight_generation")
    if section == "snapshot_thesis":
        return int(_dict(policy.get("qualification")).get("max_output_tokens") or 9000)
    if section == "pressure_evidence":
        return int(_dict(policy.get("cleanup")).get("max_output_tokens") or 5000)
    return int(_dict(policy.get("per_signal")).get("max_output_tokens") or 6000)


def _section_generation_controls(section: str) -> tuple[str, str]:
    policy = policy_for("buying_insight_generation")
    policy_key = "qualification" if section == "snapshot_thesis" else "cleanup" if section == "pressure_evidence" else "per_signal"
    controls = _dict(policy.get(policy_key))
    return str(controls.get("effort") or "medium"), str(controls.get("verbosity") or "medium")


def _salvage_json_array_items(raw: str, key: str) -> list[dict]:
    match = re.search(rf'"{re.escape(key)}"\s*:\s*\[', raw or "")
    if not match:
        return []
    decoder = json.JSONDecoder()
    position = match.end()
    items: list[dict] = []
    while position < len(raw):
        while position < len(raw) and raw[position] in " \r\n\t,":
            position += 1
        if position >= len(raw) or raw[position] == "]":
            break
        try:
            item, position = decoder.raw_decode(raw, position)
        except Exception:
            break
        if isinstance(item, dict):
            items.append(item)
    return items


def _salvage_truncated_section(section: str, raw: str) -> dict | None:
    array_key = {
        "why_now": "why_now",
        "pain_to_solution": "pain_to_solution_map",
        "pressure_evidence": "pressure_signals",
        "stakeholders": "stakeholder_map",
    }.get(section)
    if not array_key:
        return None
    result: dict[str, Any] = {array_key: _salvage_json_array_items(raw, array_key), "model_notes": []}
    if section == "pressure_evidence":
        result.update({
            "evidence_confidence": {},
            "overall_confidence": None,
            "missing_input_warnings": [],
        })
    return result


def _generate_sales_plays_by_signal(
    payload: dict,
    sales_spine: dict,
    *,
    model_mode: str | None = None,
    stage_callback=None,
    progress: int = 60,
) -> tuple[dict, dict]:
    signals_by_id = _signal_by_id(payload)
    active_ids = sorted(
        _activation_signal_ids(payload),
        key=lambda signal_id: (
            _safe_int(signals_by_id.get(signal_id, {}).get("activation_pair_index"), 999),
            signal_id,
        ),
    )
    signals = [signals_by_id[signal_id] for signal_id in active_ids if signals_by_id.get(signal_id)]
    if not signals:
        signals = [signal for signal in _list(payload.get("account_signal_map")) if isinstance(signal, dict)][:3]

    plays: list[dict] = []
    signal_results: list[dict] = []
    for index, signal in enumerate(signals[:3], start=1):
        signal_id = str(signal.get("id") or f"SIG-{index:02d}")
        candidates = _sales_play_service_candidates(signal, payload)
        analysis: dict = {}
        model_used = "deterministic_signal_assembly"
        status = "deterministic_fallback"
        error = ""
        if stage_callback:
            stage_callback("generate_report", progress, f"Generating primary sales play for {signal_id}")
        try:
            model_chain = buying_generation_chain(model_mode)
            raw, model_used = call_json_model_chain(
                _sales_play_analysis_prompt(signal, payload),
                model_chain,
                max_tokens=_section_token_limit("sales_plays"),
                timeout=100,
                reasoning_effort="medium",
                text_verbosity="medium",
                json_schema=_BUYING_SALES_PLAY_JSON_SCHEMA,
                json_schema_name=f"buying_sales_play_{signal_id.lower().replace('-', '_')}",
                log_label=f"Buying Sales Play {signal_id}",
                validate_json=True,
                required_fields=("account_signal_id",),
            )
            analysis = parse_json_object(raw)
            if str(analysis.get("account_signal_id") or "") != signal_id:
                raise ValueError(f"Sales play returned wrong signal id: {analysis.get('account_signal_id')}")
            status = "parsed" if model_used == model_chain[0] else "parsed_after_model_escalation"
        except Exception as exc:
            error = str(exc)[:220]
            print(f"   ⚠️ [Buying Potential] Primary sales play {signal_id} used deterministic assembly: {error}")
        plays.append(_sales_play_from_signal(signal, sales_spine, analysis, candidates))
        signal_results.append({
            "signal_id": signal_id,
            "model": model_used,
            "status": status,
            "error": error,
        })

    fallback_count = sum(1 for item in signal_results if item.get("status") == "deterministic_fallback")
    escalation_count = sum(1 for item in signal_results if item.get("status") == "parsed_after_model_escalation")
    meta = {
        "section": "sales_plays",
        "model": ", ".join(dict.fromkeys(str(item.get("model")) for item in signal_results if item.get("model"))),
        "parse_status": (
            "per_signal_with_deterministic_fallback"
            if fallback_count
            else "per_signal_complete_with_model_escalation"
            if escalation_count
            else "per_signal_complete"
        ),
        "signal_results": signal_results,
        "fallback_count": fallback_count,
        "model_escalation_count": escalation_count,
        "token_limit_per_signal": _section_token_limit("sales_plays"),
    }
    print(
        f"   ✅ [Buying Potential] Section sales_plays: per_signal={len(plays)}, "
        f"fallbacks={fallback_count}, escalations={escalation_count}, models={meta['model']}"
    )
    return {"recommended_sales_plays": plays, "model_notes": []}, meta


def _parse_or_repair_section(
    section: str,
    raw: str,
    payload: dict,
    model_mode: str | None = None,
    stage_callback=None,
) -> tuple[dict, str]:
    try:
        parsed = parse_json_object(raw)
        if not isinstance(parsed, dict):
            raise ValueError("Section JSON root was not an object")
        return parsed, "parsed"
    except Exception as parse_exc:
        likely_truncated = _is_likely_json_truncated(raw)
        tail = _clip((raw or "")[-180:], 180)
        print(
            f"   ⚠️ [Buying Potential] Section JSON parse failed ({section}); attempting repair: {parse_exc} "
            f"(raw_chars={len(raw or '')}, likely_truncated={likely_truncated}, tail={tail!r})"
        )
        if likely_truncated:
            salvaged = _salvage_truncated_section(section, raw)
            if salvaged is not None:
                salvaged_count = sum(len(value) for value in salvaged.values() if isinstance(value, list))
                print(
                    f"   ♻️ [Buying Potential] Section {section} kept {salvaged_count} complete item(s); "
                    "missing required SIG items will be rebuilt from the current Scanner spine."
                )
                return salvaged, "truncated_partial_recovery"
        if stage_callback:
            stage_callback("generate_report", 55, f"Repairing malformed Buying Insight section: {section}")
        try:
            repair_raw, repair_model = call_json_model_chain(
                _section_repair_prompt(section, raw),
                buying_report_repair_chain(model_mode),
                max_tokens=min(5000, _section_token_limit(section)),
                timeout=60,
                reasoning_effort="low",
                text_verbosity="low",
                json_schema=BUYING_SECTION_JSON_SCHEMAS.get(section),
                json_schema_name=f"buying_{section}_repair",
                log_label=f"Buying {section} JSON Repair",
                validate_json=True,
            )
            parsed = parse_json_object(repair_raw)
            if not isinstance(parsed, dict):
                raise ValueError("Repaired section JSON root was not an object")
            notes = _list(parsed.get("model_notes"))
            notes.append(
                f"Malformed section JSON repaired by {repair_model}. "
                f"Original parse error: {str(parse_exc)[:120]}; raw_chars={len(raw or '')}; "
                f"likely_truncated={likely_truncated}."
            )
            parsed["model_notes"] = notes
            print(f"   ✅ [Buying Potential] Section {section} JSON repaired by {repair_model}")
            return parsed, f"repaired by {repair_model}"
        except Exception as repair_exc:
            reason = f"Section '{section}' JSON parse failed ({parse_exc}); repair failed ({repair_exc})"
            print(f"   ❌ [Buying Potential] Section {section} repair failed; using deterministic fallback: {repair_exc}")
            return _section_fallback(section, payload, reason), "section_deterministic_fallback"


def _generate_buying_section(
    section: str,
    payload: dict,
    *,
    model_mode: str | None = None,
    stage_callback=None,
    progress: int = 50,
) -> tuple[dict, dict]:
    if stage_callback:
        stage_callback("generate_report", progress, f"Generating Buying Insight section: {section}")
    try:
        token_limit = _section_token_limit(section)
        reasoning_effort, text_verbosity = _section_generation_controls(section)
        raw, model_used = call_json_model_chain(
            _section_prompt(section, payload),
            buying_generation_chain(model_mode),
            max_tokens=token_limit,
            timeout=120 if section == "snapshot_thesis" else 100,
            reasoning_effort=reasoning_effort,
            text_verbosity=text_verbosity,
            json_schema=BUYING_SECTION_JSON_SCHEMAS.get(section),
            json_schema_name=f"buying_{section}",
            log_label=f"Buying {section}",
            validate_json=True,
            required_fields=tuple(BUYING_SECTION_JSON_SCHEMAS.get(section, {}).get("properties", {})),
        )
        print(
            f"   🧾 [Buying Potential] Section {section} raw output: "
            f"model={model_used}, raw_chars={len(raw or '')}, token_limit={token_limit}, "
            f"likely_truncated={_is_likely_json_truncated(raw)}"
        )
        section_data, parse_status = _parse_or_repair_section(section, raw, payload, model_mode, stage_callback)
        qualification_quality = None
        if section == "snapshot_thesis":
            section_data, qualification_quality = _finalize_qualification_section(
                section_data,
                payload,
                model_mode=model_mode,
            )
        print(f"   ✅ [Buying Potential] Section {section}: model={model_used}, parse_status={parse_status}")
        meta = {
            "section": section,
            "model": model_used,
            "parse_status": parse_status,
            "raw_chars": len(raw or ""),
            "token_limit": token_limit,
            "reasoning_effort": reasoning_effort,
            "text_verbosity": text_verbosity,
            "structured_output": section in BUYING_SECTION_JSON_SCHEMAS,
            "likely_truncated": _is_likely_json_truncated(raw),
        }
        if qualification_quality is not None:
            meta["qualification_reasoning_quality"] = qualification_quality
        return section_data, meta
    except Exception as exc:
        reason = f"Section '{section}' generation failed: {exc}"
        print(f"   ❌ [Buying Potential] Section {section}: generation failed, deterministic fallback used: {exc}")
        fallback = _section_fallback(section, payload, reason)
        qualification_quality = None
        if section == "snapshot_thesis":
            fallback, qualification_quality = _finalize_qualification_section(
                {"qualification_reasoning": _qualification_reasoning_fallback(payload), "model_notes": [reason]},
                payload,
                model_mode=model_mode,
            )
        meta = {
            "section": section,
            "model": "deterministic_fallback",
            "parse_status": "section_generation_failed",
            "error": str(exc)[:220],
        }
        if qualification_quality is not None:
            meta["qualification_reasoning_quality"] = qualification_quality
        return fallback, meta


def _assemble_section_report(
    payload: dict,
    *,
    model_mode: str | None = None,
    stage_callback=None,
) -> dict:
    sections = (
        ("snapshot_thesis", 48),
        ("why_now", 52),
        ("pain_to_solution", 56),
        ("sales_plays", 60),
        ("pressure_evidence", 64),
        ("stakeholders", 68),
    )
    report: dict[str, Any] = {}
    generation_meta = []
    notes = []
    for section, progress in sections:
        if section == "sales_plays":
            section_data, meta = _generate_sales_plays_by_signal(
                payload,
                _dict(report.get("sales_spine")),
                model_mode=model_mode,
                stage_callback=stage_callback,
                progress=progress,
            )
        else:
            section_data, meta = _generate_buying_section(
                section,
                payload,
                model_mode=model_mode,
                stage_callback=stage_callback,
                progress=progress,
            )
        report.update({key: value for key, value in section_data.items() if key in BUYING_SECTION_KEYS[section]})
        generation_meta.append(meta)
        notes.extend(_list(section_data.get("model_notes")))
        if meta.get("error"):
            notes.append(f"{section}: {meta['error']}")

    report.setdefault("account_snapshot", {})
    report.setdefault("sales_spine", {})
    report.setdefault("buying_potential_thesis", {})
    report.setdefault("why_now", [])
    report.setdefault("pain_to_solution_map", [])
    report.setdefault("recommended_sales_plays", [])
    report.setdefault("pressure_signals", [])
    report.setdefault("stakeholder_map", [])
    report.setdefault("additional_stakeholder_names", [])
    report.setdefault("evidence_confidence", {})
    report.setdefault("overall_confidence", None)
    report.setdefault("missing_input_warnings", [])
    report["section_generation"] = generation_meta
    report["generation_parse_status"] = "section_assembled"
    report["generation_model"] = ", ".join(
        f"{meta.get('section')}:{meta.get('model')}" for meta in generation_meta
    )
    report["model_notes"] = notes
    return report


def generate_buying_potential_report(
    company_data: dict,
    service_kb: dict,
    *,
    company_evidence: dict | None = None,
    company_source_ledger: dict | None = None,
    previous_report: dict | None = None,
    stage_callback=None,
    model_mode: str | None = None,
) -> tuple[dict, str]:
    if not isinstance(company_data, dict) or not company_data.get("companyName"):
        raise BuyingPotentialError("Company data missing or invalid.", 404)
    if stage_callback:
        stage_callback("build_payload", 35, "Building Buying Insight prompt payload")
    payload = _build_input_payload(company_data, service_kb, company_evidence, company_source_ledger)
    if stage_callback:
        stage_callback("generate_report", 45, "Generating Buying Insight hypothesis sections")
    report = _assemble_section_report(payload, model_mode=model_mode, stage_callback=stage_callback)
    report["model_mode"] = _buying_model_mode(model_mode)

    gate_review = None
    if stage_callback:
        stage_callback("gate_review", 70, "Reviewing sales evidence gates")
    try:
        gate_review = _run_gate_review_workflow(report, payload, model_mode=model_mode)
    except Exception as exc:
        gate_review = _normalize_gate_review(
            {"status": "unavailable"},
            unavailable_note=f"Gate model unavailable; deterministic post-processing applied: {str(exc)[:160]}",
        )
        print(f"   ⚠️ [Buying Potential Gate] unavailable: {str(exc)[:240]}", flush=True)

    if previous_report:
        payload["previous_buying_insight"] = previous_report
    report = _post_process_report(report, payload, gate_review, model_mode=model_mode)
    report["account_signal_map"] = _list(payload.get("account_signal_map"))[:12]
    report["buyer_role_lens_version"] = "v1"
    report["companyName"] = company_data.get("companyName")
    report["companyUrl"] = company_data.get("companyUrl")
    report = _polish_generated_text_tree(report, report.get("companyName"))
    report = _run_final_language_qa(report, gate_review)
    if stage_callback:
        stage_callback("render_html", 82, "Rendering Buying Insight HTML")
    html_report = render_buying_potential_html(report)
    return report, html_report


def _render_list(items: list, renderer) -> str:
    if not items:
        return '<p class="muted">No items generated.</p>'
    return "\n".join(renderer(item) for item in items)


def _gate_badge(gate: str) -> str:
    gate = _normalize_gate(gate)
    label = gate.replace("_", " ").title()
    return f'<span class="gate gate-{html.escape(gate)}">{html.escape(label)}</span>'


def _esc(value: Any) -> str:
    return html.escape(str(value or ""))


def _render_prose_paragraphs(paragraphs: Any, fallback: Any = "", class_name: str = "") -> str:
    values = [
        _clean_reasoning_sentence(item, 1800)
        for item in _list(paragraphs)
        if _clean_reasoning_sentence(item, 1800)
    ]
    if not values and fallback:
        values = [part.strip() for part in re.split(r"\n\s*\n", str(fallback)) if part.strip()]
    class_attr = f' class="{html.escape(class_name)}"' if class_name else ""
    return "".join(f"<p{class_attr}>{_esc(value)}</p>" for value in values)


def render_buying_potential_html(report: dict) -> str:
    company = report.get("companyName") or "Company"
    report = _polish_generated_text_tree(report, company)
    generated = report.get("generated_at", "")[:10]
    confidence = report.get("overall_confidence", 0)
    snapshot = _dict(report.get("account_snapshot"))
    thesis = _dict(report.get("buying_potential_thesis"))
    sales_spine = _dict(report.get("sales_spine"))
    evidence = _dict(report.get("evidence_confidence"))
    self_scan = _dict(report.get("self_scan_context"))
    sufficiency_status = str(
        _dict(_dict(report.get("quality_controls")).get("reasoning_verification")).get("evidence_sufficiency")
        or ""
    ).lower()
    reliability = _dict(report.get("reliability_status"))
    if not reliability.get("code"):
        reliability = _status_template("B" if _safe_int(confidence, 0) >= 45 else "C")
        reliability["reasons"] = _list(report.get("status_reasons"))
    status_scale = _list(report.get("status_scale")) or _list(reliability.get("scale")) or _status_scale()
    current_status_code = str(reliability.get("code") or "C").upper()

    def overall_stance():
        if sufficiency_status == "complete":
            return "Evidence stance: strong discovery basis"
        if sufficiency_status == "partial":
            return "Evidence stance: constrained / question-led"
        if sufficiency_status == "insufficient":
            return "Evidence stance: draft / do not use"
        # Compatibility for reports saved before deterministic sufficiency/status.
        score = _safe_int(confidence, 0)
        if score >= 70:
            return "Evidence stance: strong discovery basis"
        if score >= 45:
            return "Evidence stance: cautious hypothesis basis"
        return "Evidence stance: constrained / question-led"

    def simple_items(items, limit=10, empty_label="N/A - no separate items for this run.", formatter=None):
        values = []
        for item in _list(items)[:limit]:
            text = formatter(item) if formatter else (_format_top_buying_hypothesis_item(item) if isinstance(item, dict) else _clip(item, 520))
            text = _clip(text, 520)
            if text:
                values.append(text)
        rendered = "".join(f"<li>{_esc(x)}</li>" for x in values)
        return rendered or f"<li>{_esc(empty_label)}</li>"

    def status_scale_html():
        chips = []
        for status in status_scale:
            if not isinstance(status, dict):
                continue
            code = str(status.get("code") or "").upper()
            tone = re.sub(r"[^a-z0-9_-]+", "", str(status.get("tone") or "cyan").lower())
            active = code == current_status_code
            chips.append(
                f"""<div class="status-chip status-{_esc(tone)} {'active' if active else ''}">
                  <span class="status-code">{_esc(code)}</span>
                  <span class="status-label">{_esc(status.get('label'))}</span>
                </div>"""
            )
        reasons = "".join(f"<li>{_esc(reason)}</li>" for reason in _list(reliability.get("reasons"))[:4])
        return f"""
        <div class="status-panel">
          <div class="status-head">
            <div>
              <div class="status-kicker">Reliability Status</div>
              <strong>{_esc(current_status_code)} — {_esc(reliability.get('label'))}</strong>
            </div>
            <span class="pill status-publication">{_esc(str(report.get('publication_status') or reliability.get('publication_status') or '').replace('_', ' '))}</span>
          </div>
          <div class="status-scale">{''.join(chips)}</div>
          <p class="status-summary">{_esc(reliability.get('summary'))}</p>
          <p class="status-use"><b>Current use boundary:</b> {_esc(reliability.get('allowed_use'))}</p>
          {f'<ul class="status-reasons">{reasons}</ul>' if reasons else ''}
        </div>"""

    signal_lookup = {
        str(signal.get("id")): signal
        for signal in _list(report.get("account_signal_map"))
        if isinstance(signal, dict) and signal.get("id")
    }

    def signal_display_label(signal_id):
        signal_id = str(signal_id or "").strip()
        if not signal_id:
            return ""
        signal = signal_lookup.get(signal_id)
        if not signal:
            return signal_id
        label = ""
        for candidate in (
            signal.get("theme"),
            _dict(signal.get("need_hypothesis")).get("structural_exposure"),
            signal.get("pressure_type"),
            signal.get("account_movement"),
        ):
            label = _clip_label(candidate, 80)
            if label:
                break
        label = re.sub(r"\s+", " ", label or "").strip(" .:-")
        if not label:
            return signal_id
        if signal_id.lower() in label.lower():
            return label
        return f"{label} ({signal_id})"

    def combined_need_items(limit=5):
        items = []
        seen = set()

        def add_item(raw):
            text = _restore_company_case(_clip(raw, 460), company)
            if len(text) < 24:
                return
            key = re.sub(r"\W+", " ", text.lower()).strip()[:140]
            if key in seen:
                return
            seen.add(key)
            items.append(text)

        for play in _list(report.get("recommended_sales_plays")):
            if not isinstance(play, dict):
                continue
            need = _dict(play.get("need_hypothesis"))
            add_item(need.get("combined_activation_hypothesis") or play.get("buying_hypothesis"))
            if len(items) >= limit:
                break

        if len(items) < limit:
            for signal in _list(report.get("account_signal_map")):
                if not isinstance(signal, dict):
                    continue
                need = _dict(signal.get("need_hypothesis"))
                add_item(need.get("combined_activation_hypothesis") or signal.get("business_consequence"))
                if len(items) >= limit:
                    break

        return simple_items(items, limit)

    def pressure_card(item):
        return f"""
        <article class="card">
          <div class="card-top"><strong>{_esc(item.get('signal_type'))}</strong>{_gate_badge(item.get('gate'))}</div>
          <p><b>Trigger:</b> {_esc(item.get('trigger'))}</p>
          <p>{_esc(item.get('description'))}</p>
          <p><b>Business consequence:</b> {_esc(item.get('business_consequence'))}</p>
          <p><b>Buyer concern:</b> {_esc(item.get('buyer_role_concern'))}</p>
          <small>Evidence: {_esc(', '.join(item.get('evidence_basis') or []))} · Boundary: {_esc(item.get('gate'))}</small>
        </article>"""

    def why_card(item):
        return f"""
        <article class="card">
          <div class="card-top"><strong>{_esc(item.get('trigger'))}</strong>{_gate_badge(item.get('gate'))}</div>
          <p>{_esc(item.get('why_it_matters_now') or item.get('interpretation'))}</p>
          <p><b>Buyer relevance:</b> {_esc(item.get('buyer_relevance'))}</p>
          <p><b>Discovery question:</b> {_esc(item.get('safe_discovery_question'))}</p>
          <small>Use boundary: {_esc(item.get('gate'))}</small>
        </article>"""

    def stakeholder_card(item):
        pressure = "".join(f"<span class=\"pill tiny\">{_esc(x)}</span>" for x in _list(item.get("linked_pressure_themes"))[:6])
        services = "".join(f"<span class=\"pill tiny service\">{_esc(x)}</span>" for x in _list(item.get("linked_service_themes"))[:6])
        do_not_say = "".join(f"<li>{_esc(x)}</li>" for x in _list(item.get("do_not_say"))[:4])
        safety = "".join(f"<li>{_esc(x)}</li>" for x in _list(item.get("safety_notes"))[:4])
        return f"""
        <article class="card stakeholder-card">
          <div class="card-top"><strong>{_esc(item.get('name'))}</strong>{_gate_badge(item.get('allowed_use'))}</div>
          <div class="role-line">{_esc(item.get('role'))}</div>
          <p><b>Likely buying-committee role:</b> {_esc(item.get('likely_buying_committee_role'))}</p>
          <p><b>Relevance rationale:</b> {_esc(item.get('relevance_rationale'))}</p>
          <p><b>Evidence basis:</b> {_esc(item.get('evidence_basis'))}</p>
          <div class="theme-row">{pressure or '<span class="pill tiny muted-pill">No linked pressure theme</span>'}</div>
          <div class="theme-row">{services or '<span class="pill tiny muted-pill">No linked service theme</span>'}</div>
          <h3>Do Not Say</h3>
          <ul>{do_not_say or '<li>Do not imply confirmed buying intent from role/title alone.</li>'}</ul>
          <h3>Safety Notes</h3>
          <ul>{safety or '<li>Use as sales-prep context; validate current role and remit before outreach.</li>'}</ul>
          <small>Allowed use: {_esc(item.get('allowed_use'))} · Treat role relevance as discovery guidance</small>
        </article>"""

    def extra_stakeholder_rows(items):
        rows = []
        for item in _list(items)[:6]:
            if not isinstance(item, dict):
                continue
            rows.append(
                "<tr>"
                f"<td>{_esc(item.get('name'))}</td>"
                f"<td>{_esc(item.get('position') or item.get('role') or item.get('title'))}</td>"
                f"<td>{_esc(item.get('source_database'))}</td>"
                f"<td>{_esc(item.get('allowed_use'))}</td>"
                "</tr>"
            )
        if not rows:
            return ""
        return (
            '<div class="simple-list-block">'
            "<h3>Additional Names To Review</h3>"
            '<p class="muted">Source-bound people from Company Evidence DB, Source Ledger, or stored Scanner compatibility data. Use as sales-prep context only.</p>'
            "<table><thead><tr><th>Name</th><th>Position</th><th>Source</th><th>Allowed Use</th></tr></thead>"
            f"<tbody>{''.join(rows)}</tbody></table></div>"
        )

    def solution_card(item):
        disq = ", ".join(item.get("disqualifiers") or [])
        account_signal_label = signal_display_label(item.get("account_signal_id"))
        return f"""
        <article class="card">
          <div class="card-top"><strong>{_esc(item.get('service'))}</strong>{_gate_badge(item.get('gate'))}</div>
          <p><b>Account signal:</b> {_esc(account_signal_label)}</p>
          <p><b>Pressure:</b> {_esc(item.get('interpreted_pressure'))}</p>
          <p><b>Buyer lens:</b> {_esc(item.get('buyer_lens'))}</p>
          <p><b>Why this response direction:</b> {_esc(item.get('why_this_service'))}</p>
          <p><b>Safe wording:</b> {_esc(item.get('safe_wording'))}</p>
          <p><b>Missing evidence:</b> {_esc(item.get('missing_evidence'))}</p>
          <small>Proof: {_esc(item.get('proof_point'))} · Disqualifiers: {_esc(disq)}</small>
        </article>"""

    def play_card(item):
        need = _dict(item.get("need_hypothesis"))
        response = _dict(item.get("response_directions"))
        questions = "".join(f"<li>{_esc(q)}</li>" for q in _list(item.get("discovery_questions"))[:5])
        role_questions = "".join(
            f"<li><b>{_esc(_dict(row).get('role'))}:</b> {_esc('; '.join(_list(_dict(row).get('questions'))[:3]))}</li>"
            for row in _list(item.get("discovery_questions_by_role"))[:4]
            if isinstance(row, dict)
        )
        do_not_say = "".join(f"<li>{_esc(q)}</li>" for q in _list(item.get("do_not_say"))[:5])
        upgrades = "".join(f"<li>{_esc(q)}</li>" for q in _list(item.get("qualification_upgrade_criteria"))[:5])
        modernization = "".join(f"<li>{_esc(q)}</li>" for q in _list(response.get("evolving_modernization_directions"))[:5])
        other_capabilities = "".join(f"<li>{_esc(q)}</li>" for q in _list(response.get("other_capabilities_may_be_required"))[:6])
        do_not_say_html = f"<h3>Do Not Say</h3><ul>{do_not_say}</ul>" if do_not_say else ""
        return f"""
        <article class="card">
          <div class="card-top"><strong>{_esc(item.get('play_name') or item.get('service_or_offer'))}</strong><span class="risk">Risk: {_esc(item.get('overclaim_risk'))}</span></div>
          <div class="theme-row"><span class="pill tiny service">{_esc(PLAY_TYPE_LABELS.get(_normalize_play_type(item.get('play_type')), item.get('play_type')))}</span><span class="pill tiny">{_esc(CONTEXT_TYPE_LABELS.get(_normalize_context_type(need.get('context_type'), item.get('play_type')), 'Need hypothesis'))}</span><span class="pill tiny">Activation pair {_esc(item.get('activation_pair_index') or 'n/a')}</span></div>
          <p><b>Potential offer area:</b> {_esc(item.get('service_or_offer'))}</p>
          <p><b>Discovery readiness:</b> {_esc(item.get('readiness_band'))}</p>
          <p><b>Priority:</b> {_esc(item.get('priority_band'))}</p>
          <p><b>Evidence coverage:</b> {_esc(item.get('evidence_coverage'))}</p>
          <p><b>Confidence boundary:</b> {_esc(item.get('confidence_boundary'))}</p>
          <p><b>Primary buyer:</b> {_esc(item.get('primary_buyer') or item.get('target_stakeholder'))}</p>
          <p><b>Trigger:</b> {_esc(item.get('trigger'))}</p>
          <p><b>Account thesis:</b> {_esc(item.get('account_thesis'))}</p>
          <p><b>Buying hypothesis:</b> {_esc(item.get('buying_hypothesis'))}</p>
          <p><b>Trigger-to-offer bridge:</b> {_esc(item.get('trigger_to_offer_bridge'))}</p>
          <p><b>Business hypothesis:</b> {_esc(item.get('business_hypothesis') or item.get('why_relevant'))}</p>
          <h3>Why This Might Matter</h3>
          <p><b>Account signal:</b> {_esc(need.get('company_context'))}</p>
          <p><b>Market pressure:</b> {_esc(need.get('market_context'))}</p>
          <p><b>Where need may emerge:</b> {_esc(need.get('combined_activation_hypothesis'))}</p>
          <p><b>Future pressure:</b> {_esc(need.get('future_pressure'))}</p>
          <p><b>Current condition:</b> {_esc(need.get('current_condition'))}</p>
          <p><b>Structural exposure:</b> {_esc(need.get('structural_exposure'))}</p>
          <p><b>Likely consequence:</b> {_esc(need.get('likely_business_consequence'))}</p>
          <p><b>Why now or soon:</b> {_esc(need.get('why_now_or_soon'))}</p>
          <p><b>What to validate or explore:</b> {_esc(need.get('what_to_propose'))}</p>
          <p><b>What progress would show:</b> {_esc(need.get('what_progress_shows'))}</p>
          <p><b>Falsifier:</b> {_esc(need.get('falsifier'))}</p>
          <h3>Possible Conversation Direction</h3>
          <p><b>Potential need:</b> {_esc(response.get('potential_need'))}</p>
          <p><b>Vivicta potential contribution:</b> {_esc(response.get('vivicta_potential_contribution'))}</p>
          <h3>Modernization Lens</h3>
          <ul>{modernization or '<li>No response directions generated.</li>'}</ul>
          <h3>Other Capabilities To Check</h3>
          <ul>{other_capabilities or '<li>No adjacent capability notes generated.</li>'}</ul>
          <p><b>First meeting objective:</b> {_esc(item.get('first_meeting_objective'))}</p>
          <p><b>Opening angle:</b> {_esc(item.get('opening_angle'))}</p>
          <p><b>Executive angle:</b> {_esc(item.get('executive_angle'))}</p>
          <p><b>Technical angle:</b> {_esc(item.get('technical_angle'))}</p>
          <p><b>Safe customer-facing wording:</b> {_esc(item.get('safe_customer_wording'))}</p>
          <p><b>Proof point:</b> {_esc(item.get('proof_point'))}</p>
          <h3>Discovery Questions</h3>
          <ul>{questions or '<li>No questions generated.</li>'}</ul>
          <h3>Questions By Buyer Role</h3>
          <ul>{role_questions or '<li>No role-specific questions generated.</li>'}</ul>
          <h3>Qualification Upgrade Criteria</h3>
          <ul>{upgrades or '<li>No upgrade criteria generated.</li>'}</ul>
          {do_not_say_html}
          <small>Signals: {_esc(', '.join(item.get('linked_signal_ids') or item.get('evidence_basis') or []))} · Classification: {_esc(item.get('play_classification'))} · Internal score retained in JSON only</small>
        </article>"""

    def hypothesis_items(context_type, limit=6):
        items = []
        seen = set()
        def add_item(raw):
            text = _restore_company_case(_clip(raw, 420), company)
            if len(text) < 24:
                return
            if re.search(r"\bResponse direction\b", text, re.I):
                return
            if re.search(r"\b(?:and|or|with|to)\.$", text, re.I):
                return
            if re.search(r"\bNo items generated\b", text, re.I):
                return
            key = re.sub(r"\W+", " ", text.lower()).strip()[:120]
            if key in seen:
                return
            seen.add(key)
            items.append(text)
        for play in _list(report.get("recommended_sales_plays")):
            if not isinstance(play, dict):
                continue
            need = _dict(play.get("need_hypothesis"))
            if _normalize_context_type(need.get("context_type"), play.get("play_type")) != context_type:
                continue
            if context_type == "company_only":
                add_item(need.get("company_context") or need.get("current_condition") or play.get("buying_hypothesis"))
            elif context_type == "market_watch":
                add_item(need.get("market_context") or need.get("future_pressure") or play.get("trigger"))
            else:
                add_item(
                    need.get("combined_activation_hypothesis")
                    or play.get("buying_hypothesis")
                    or need.get("what_to_propose")
                )
        if len(items) < limit:
            for signal in _list(report.get("account_signal_map")):
                if not isinstance(signal, dict):
                    continue
                signal_context = _normalize_context_type(
                    _dict(signal.get("need_hypothesis")).get("context_type"),
                    signal.get("sales_play_type") or signal.get("play_type") or signal.get("theme_type"),
                )
                if signal_context != context_type:
                    continue
                need = _dict(signal.get("need_hypothesis"))
                if context_type == "company_only":
                    add_item(need.get("company_context") or signal.get("why") or signal.get("account_movement"))
                elif context_type == "market_watch":
                    add_item(need.get("market_context") or signal.get("commercial_pressure") or signal.get("why"))
                else:
                    add_item(
                        need.get("combined_activation_hypothesis")
                        or signal.get("business_consequence")
                        or signal.get("what")
                    )
                if len(items) >= limit:
                    break
        return simple_items(items, limit)

    return f"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1" />
  <title>{_esc(company)} — Buying Hypothesis & Insight</title>
  <style>
    :root {{ --bg:#07111f; --panel:#101b2e; --line:#26344d; --text:#e8eef8; --muted:#8ea0bb; --cyan:#67e8f9; --green:#6ee7b7; --amber:#facc15; --rose:#fb7185; --violet:#a78bfa; }}
    * {{ box-sizing:border-box; }} body {{ margin:0; background:linear-gradient(180deg,#050b15,#0b1628); color:var(--text); font-family:Inter,ui-sans-serif,system-ui,-apple-system,BlinkMacSystemFont,"Segoe UI",sans-serif; line-height:1.55; }}
    main {{ max-width:1180px; margin:0 auto; padding:42px 28px 70px; }}
    header {{ border:1px solid var(--line); background:rgba(16,27,46,.9); border-radius:16px; padding:30px; margin-bottom:24px; }}
    .eyebrow {{ color:var(--cyan); text-transform:uppercase; letter-spacing:.18em; font:700 12px ui-monospace,monospace; }}
    h1 {{ font-size:clamp(34px,6vw,66px); line-height:.95; margin:12px 0; letter-spacing:-.04em; }}
    h2 {{ margin:34px 0 12px; color:#fff; font-size:22px; }} h3 {{ margin:0 0 8px; }}
    .lede {{ max-width:850px; color:#c8d3e5; font-size:18px; }}
    .meta {{ display:flex; flex-wrap:wrap; gap:10px; margin-top:18px; }}
    .pill,.gate,.risk {{ display:inline-flex; align-items:center; gap:6px; border:1px solid var(--line); border-radius:999px; padding:5px 10px; color:var(--muted); font:700 11px ui-monospace,monospace; text-transform:uppercase; letter-spacing:.12em; }}
    .status-panel {{ margin-top:22px; border:1px solid rgba(142,160,187,.2); border-radius:12px; padding:14px; background:rgba(7,17,31,.52); }}
    .status-head {{ display:flex; justify-content:space-between; align-items:flex-start; gap:14px; margin-bottom:12px; }}
    .status-kicker {{ color:var(--muted); font:800 10px ui-monospace,monospace; letter-spacing:.14em; text-transform:uppercase; margin-bottom:2px; }}
    .status-scale {{ display:grid; grid-template-columns:repeat(4,minmax(0,1fr)); gap:8px; }}
    .status-chip {{ border:1px solid rgba(142,160,187,.18); border-radius:10px; padding:9px 10px; opacity:.46; background:rgba(16,27,46,.52); }}
    .status-chip.active {{ opacity:1; box-shadow:0 0 0 1px currentColor inset; }}
    .status-code {{ display:block; font:900 14px ui-monospace,monospace; letter-spacing:.08em; }}
    .status-label {{ display:block; font:700 11px ui-monospace,monospace; letter-spacing:.08em; text-transform:uppercase; margin-top:2px; }}
    .status-green {{ color:var(--green); }} .status-cyan {{ color:var(--cyan); }} .status-amber {{ color:var(--amber); }} .status-rose {{ color:var(--rose); }}
    .status-summary,.status-use {{ margin:10px 0 0; color:#c8d3e5; }} .status-reasons {{ margin-top:10px; color:var(--muted); }} .status-publication {{ white-space:nowrap; }}
    .tiny {{ font-size:9px; padding:4px 7px; margin:2px 4px 2px 0; }} .service {{ color:var(--cyan); border-color:rgba(103,232,249,.28); }} .muted-pill {{ opacity:.65; }}
    .gate-allowed_as_fact {{ color:var(--green); border-color:rgba(110,231,183,.5); }} .gate-allowed_as_hypothesis {{ color:var(--cyan); border-color:rgba(103,232,249,.45); }}
    .gate-allowed_as_discovery_question {{ color:var(--amber); border-color:rgba(250,204,21,.45); }} .gate-internal_only {{ color:var(--violet); border-color:rgba(167,139,250,.45); }} .gate-blocked {{ color:var(--rose); border-color:rgba(251,113,133,.45); }}
    section {{ border:1px solid var(--line); background:rgba(16,27,46,.74); border-radius:14px; padding:24px; margin:20px 0; }}
    .grid {{ display:grid; grid-template-columns:repeat(2,minmax(0,1fr)); gap:14px; }} .grid-3 {{ grid-template-columns:repeat(3,minmax(0,1fr)); }}
    .card {{ background:rgba(7,17,31,.72); border:1px solid rgba(142,160,187,.18); border-radius:12px; padding:16px; }} .compact {{ padding:13px; }}
    .card-top {{ display:flex; justify-content:space-between; align-items:flex-start; gap:12px; margin-bottom:8px; }}
    .source-basis {{ display:flex; justify-content:space-between; align-items:center; gap:18px; margin:4px 0 18px; border:1px solid rgba(103,232,249,.22); border-radius:12px; padding:14px 16px; background:rgba(7,17,31,.55); }}
    .scanner-link {{ display:inline-flex; align-items:center; justify-content:center; min-height:38px; white-space:nowrap; border:1px solid rgba(103,232,249,.45); color:var(--cyan); border-radius:999px; padding:8px 14px; font:800 11px ui-monospace,monospace; letter-spacing:.12em; text-transform:uppercase; text-decoration:none; }}
    .scanner-link:hover {{ background:rgba(103,232,249,.1); }}
    small,.muted {{ color:var(--muted); }} table {{ width:100%; border-collapse:collapse; overflow:hidden; border-radius:10px; }} th,td {{ padding:11px; border-bottom:1px solid rgba(142,160,187,.16); text-align:left; vertical-align:top; }} th {{ color:var(--muted); font:700 11px ui-monospace,monospace; text-transform:uppercase; letter-spacing:.12em; }}
    ul {{ margin:8px 0 0 18px; padding:0; }} li {{ margin:5px 0; }} .warning {{ border-color:rgba(250,204,21,.4); background:rgba(250,204,21,.08); }} .danger {{ border-color:rgba(251,113,133,.4); background:rgba(251,113,133,.08); }}
    .role-line {{ color:var(--green); font:800 12px ui-monospace,monospace; text-transform:uppercase; letter-spacing:.12em; margin:-2px 0 12px; }} .theme-row {{ display:flex; flex-wrap:wrap; gap:4px; margin:8px 0; }}
    .simple-list-block {{ margin-top:18px; border-top:1px solid rgba(142,160,187,.18); padding-top:18px; }}
    @media (max-width:820px) {{ main {{ padding:24px 14px 48px; }} .grid,.grid-3,.status-scale {{ grid-template-columns:1fr; }} .card-top,.status-head,.source-basis {{ flex-direction:column; align-items:flex-start; }} }}
  </style>
</head>
<body>
<main>
  <header>
    <div class="eyebrow">Buying Hypothesis & Insight · Admin Artifact</div>
    <h1>{_esc(company)}</h1>
    <p class="lede">{_esc(report.get('safety_statement'))}</p>
    <div class="meta">
      <span class="pill">Generated {_esc(generated)}</span>
      <span class="pill">Status {_esc(current_status_code)} — {_esc(reliability.get('label'))}</span>
      <span class="pill">{_esc(overall_stance())}</span>
      <span class="pill">Sources {_esc(_dict(report.get('source_counts')).get('generation_eligible', 0))} eligible</span>
      <span class="pill">Financial refs {_esc(_dict(report.get('source_counts')).get('financial_sources', 0))}</span>
    </div>
    {status_scale_html()}
  </header>

  <section>
    <h2>Account Context & Qualification Snapshot</h2>
    {_render_prose_paragraphs(snapshot.get('paragraphs'), snapshot.get('text'))}
    {_gate_badge(snapshot.get('gate'))}
  </section>

  <section>
    <h2>Account Need Hypothesis</h2>
    {_render_prose_paragraphs(thesis.get('paragraphs'), thesis.get('text'))}
    {_render_prose_paragraphs([], thesis.get('rationale'), 'muted')}
    {_gate_badge(thesis.get('gate'))}
  </section>

  <section>
    <h2>Sales Spine</h2>
    {f'<article class="card warning"><p>{_esc(self_scan.get("framing"))}</p></article>' if self_scan.get("is_self_scan") else ''}
    <h3>Account Thesis</h3>
    {_render_prose_paragraphs([], sales_spine.get('account_thesis'))}
    <h3>Why This May Become Relevant</h3>
    {_render_prose_paragraphs([], sales_spine.get('strategic_synthesis_basis'))}
    <div class="grid grid-3">
      <div class="card compact"><h3>Top Buying Hypotheses</h3><ul>{simple_items(sales_spine.get('top_buying_hypotheses'), 5, "N/A - no separate top buying hypotheses were generated.", _format_top_buying_hypothesis_item)}</ul></div>
      <div class="card compact"><h3>Risk Themes For Discovery</h3><ul>{simple_items(sales_spine.get('risk_themes'), 5)}</ul></div>
      <div class="card compact"><h3>Where Need May Emerge</h3><ul>{combined_need_items(5)}</ul></div>
    </div>
  </section>

  <section><h2>Pressure & Demand Hypothesis Signals</h2><div class="grid">{_render_list(_list(report.get('pressure_signals')), pressure_card)}</div></section>
  <section><h2>Why This May Matter Now</h2><div class="grid">{_render_list(_list(report.get('why_now')), why_card)}</div></section>

  <section>
    <h2>Buying Committee & Influence Map</h2>
    <div class="grid">{_render_list(_list(report.get('stakeholder_map')), stakeholder_card)}</div>
    {extra_stakeholder_rows(report.get('additional_stakeholder_names'))}
  </section>

  <section><h2>Possible Conversation Directions</h2><div class="grid">{_render_list(_list(report.get('pain_to_solution_map')), solution_card)}</div></section>
  <section><h2>Recommended Sales Plays</h2><div class="grid">{_render_list(_list(report.get('recommended_sales_plays')), play_card)}</div></section>


  <section class="warning">
    <h2>Evidence & Sales Guardrails</h2>
    <div class="source-basis">
      <p class="muted"><b>Evidence basis:</b> derived from Scanner Company Summary, Company Evidence DB, Source Ledger, Foresight Knowledge Base, Market Intelligence, and Buying Insight generation gates. Source-level audit stays in Scanner; this report presents the sales hypothesis and guardrails.</p>
      <a class="scanner-link" href="https://the-scanner.up.railway.app/" target="_blank" rel="noopener noreferrer">Open Scanner</a>
    </div>
    <div class="grid grid-3">
      <div><h3>Use Only As Question</h3><ul>{simple_items(evidence.get('use_only_as_question') or evidence.get('claims_as_questions_only'), 12)}</ul></div>
      <div><h3>Weak / Missing Evidence</h3><ul>{simple_items(evidence.get('weak_or_missing_evidence'), 12, "N/A - no weak or missing evidence items were separated in this run.")}</ul></div>
      <div><h3>Report Quality Notes</h3><ul>{simple_items(evidence.get('report_quality_notes'), 12, "N/A - no generation or repair notes for this run.")}</ul></div>
      <div><h3>Do Not Say</h3><ul>{simple_items(evidence.get('do_not_say') or evidence.get('do_not_use_claims'), 12, "N/A - no blocked customer-facing claims were generated.")}</ul></div>
      <div><h3>Sensitive Claims</h3><ul>{simple_items(evidence.get('sensitive_claims'), 12, "N/A - no sensitive-claim notes were generated.")}</ul></div>
    </div>
  </section>
</main>
</body>
</html>"""
