"""
Evidence-gated Buying Potential & Insight generation.

This module is intentionally separate from the normal scan pipeline. It reads
stored Scanner output plus a service portfolio KB, generates a sales-facing
account insight, and returns standalone JSON/HTML artifacts.
"""

from __future__ import annotations

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
    CLAUDE_HAIKU_MODEL,
    CLAUDE_OPUS_MODEL,
    CLAUDE_SONNET_MODEL,
    GPT_REASONING_MODEL,
    GPT_VERIFIER_MODEL,
    call_json_model_chain,
    parse_json_object,
)
from source_quality import is_generation_eligible_source


SERVICE_KB_CACHE_PREFIX = "service_portfolio/vivicta_services.extracted"
SERVICE_KB_TEXT_LIMIT = 65000
ALLOWED_GATES = {
    "allowed_as_fact",
    "allowed_as_hypothesis",
    "allowed_as_discovery_question",
    "internal_only",
    "blocked",
}
EVIDENCE_BASIS_PREFIXES = ("SRC-", "FIN-", "CED-", "LEDGER-", "EVD-")


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


def _clip(value: Any, limit: int = 900) -> str:
    text = re.sub(r"\s+", " ", str(value or "")).strip()
    return text[:limit]


def _list(value: Any) -> list:
    return value if isinstance(value, list) else []


def _dict(value: Any) -> dict:
    return value if isinstance(value, dict) else {}


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


def _dedupe_chain(models: tuple[str, ...]) -> tuple[str, ...]:
    seen = set()
    cleaned = []
    for model in models:
        model = _clean_env(model)
        if model and model not in seen:
            seen.add(model)
            cleaned.append(model)
    return tuple(cleaned)


def _buying_model_mode(mode: str | None = None) -> str:
    return "quality"


def _haiku_model() -> str:
    return _clean_env(os.environ.get("CLAUDE_HAIKU_MODEL") or CLAUDE_HAIKU_MODEL)


def buying_generation_chain(mode: str | None = None) -> tuple[str, ...]:
    return _dedupe_chain((CLAUDE_OPUS_MODEL, GPT_REASONING_MODEL, CLAUDE_SONNET_MODEL))


def buying_gate_chain(mode: str | None = None) -> tuple[str, ...]:
    return _dedupe_chain((GPT_VERIFIER_MODEL, CLAUDE_SONNET_MODEL))


def service_kb_extraction_chain(mode: str | None = None) -> tuple[str, ...]:
    return _dedupe_chain((GPT_REASONING_MODEL, CLAUDE_SONNET_MODEL, _haiku_model()))


def service_kb_repair_chain(mode: str | None = None) -> tuple[str, ...]:
    return _dedupe_chain((GPT_VERIFIER_MODEL, CLAUDE_SONNET_MODEL, _haiku_model()))


def buying_report_repair_chain(mode: str | None = None) -> tuple[str, ...]:
    return _dedupe_chain((GPT_VERIFIER_MODEL, CLAUDE_SONNET_MODEL, _haiku_model()))


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
            })
    return items[:12]


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
    if any(word in text_l for word in ("ai", "autonomy", "data", "software", "platform", "digital", "cloud", "erp", "sap")):
        return (
            "Digital, data, and architecture pressure",
            "Digital or technology-adjacent signals may create pressure for stronger data architecture, platform governance, integration, and secure foundations.",
            "Technology complexity can outgrow ad hoc governance, making architecture, lifecycle data, and operating ownership more important.",
        )
    if any(word in text_l for word in ("security", "cyber", "nis2", "dora", "compliance", "regulation", "csrd", "sovereign")):
        return (
            "Regulatory, security, and resilience pressure",
            "Regulatory or security exposure may increase the need for clearer controls, evidence, resilience, and responsibility structures.",
            "Compliance and resilience pressure can become a business risk if evidence, ownership, and controls remain fragmented.",
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
    "company_only": "Company-derived need hypothesis",
    "market_watch": "Market-derived pressure hypothesis",
    "combined_activation": "Combined activation hypothesis",
}


def _normalize_play_type(value: Any) -> str:
    token = re.sub(r"[^a-z0-9]+", "_", str(value or "").strip().lower()).strip("_")
    if token in {"current", "current_state", "current_status", "status"}:
        return "current_status"
    if token in {"foresight", "future", "future_pressure", "foresight_driven"}:
        return "foresight_driven"
    if token in {"proactive", "opportunity", "proactive_opportunity"}:
        return "proactive_opportunity"
    return "proactive_opportunity"


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
            for key in ("theme", "why", "what")
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
    future_pressure = _clip(pressure if play_type != "current_status" else signal.get("commercial_pressure") or pressure, 420)
    structural_exposure = _clip(signal.get("theme") or pressure_label, 260)
    likely_business_consequence = _clip(signal.get("business_consequence") or consequence, 420)
    what_to_propose = _clip(signal.get("what") or "Validate whether the pressure is real, owned, and commercially relevant before proposing a service path.", 420)
    why_now_or_soon = _clip(signal.get("timing_relevance") or _infer_timing(text), 360)
    context_type = _normalize_context_type(signal.get("context_type"), play_type)
    company_context = _clip(current_condition, 520)
    market_context = _clip(future_pressure if play_type != "current_status" else signal.get("commercial_pressure") or pressure, 520)
    combined_activation = _clip(
        f"If {current_condition}, then {future_pressure} may make {what_to_propose} commercially relevant.",
        700,
    )
    return {
        "future_pressure": future_pressure,
        "current_condition": current_condition,
        "structural_exposure": structural_exposure,
        "likely_business_consequence": likely_business_consequence,
        "why_now_or_soon": why_now_or_soon,
        "what_to_propose": what_to_propose,
        "discovery_question": "Where would this pressure become most visible if it starts affecting priorities, operating capacity, or investment choices?",
        "disqualifier": "Do not advance this as a sales play if the customer confirms no owner, pressure, timing, or business consequence.",
        "company_context": company_context,
        "market_context": market_context,
        "combined_activation_hypothesis": combined_activation,
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
    if not normalized.get("combined_activation_hypothesis"):
        normalized["combined_activation_hypothesis"] = _clip(
            f"{normalized.get('company_context')} + {normalized.get('market_context')} may create a need to {normalized.get('what_to_propose')}",
            700,
        )
    if not normalized.get("falsifier"):
        normalized["falsifier"] = fallback["falsifier"]
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
    directions = [
        "Validate the current operating baseline before proposing a solution.",
        "Identify whether process, governance, data, integration, technology, or capability development is the binding constraint.",
        "Use Evolving Modernization as a completeness lens, not as a pre-scoped delivery plan.",
    ]
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
                "service": svc.get("name") or svc.get("category") or "Service",
                "category": svc.get("category"),
                "match_terms": overlap[:8],
                "proof_points": _list(svc.get("proof_points"))[:3],
                "safe_wording": _list(svc.get("safe_wording"))[:3] or svc.get("safe_wording"),
                "do_not_claim": _list(svc.get("do_not_claim"))[:3],
                "disqualifiers": _list(svc.get("disqualifiers"))[:3],
            })
        if len(matches) >= limit:
            break
    return matches


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
) -> dict:
    pressure, movement_pressure, consequence = _infer_pressure(text)
    roles = _infer_buyer_roles(text)
    service_hints = _service_fit_hints(text, services)
    return {
        "id": signal_id,
        "source": source,
        "evidence_basis": [str(item) for item in evidence_basis[:6]],
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
    limit: int = 10,
) -> list[dict]:
    signals = []

    def add_signal(
        source: str,
        text: str,
        basis: list[str],
        confidence: int = 45,
        gate: str = "allowed_as_hypothesis",
        quote: str = "",
        extra: dict | None = None,
    ):
        if len(signals) >= limit:
            return None
        text = _clip(text, 900)
        if not text:
            return None
        signal_id = f"SIG-{len(signals) + 1:02d}"
        signal = _signal_from_text(
            signal_id,
            source=source,
            text=text,
            evidence_basis=basis,
            services=services,
            confidence=confidence,
            gate=gate,
            quote=quote,
        )
        if extra:
            signal.update(extra)
        signals.append(signal)
        return signal

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
                "theme": f"Activation pair {index + 1}",
                "why": whys[index] if index < len(whys) else "",
                "what": whats[index] if index < len(whats) else "",
            })

    for idx, pair in enumerate(activation_pairs[:3], start=1):
        play_type = _normalize_play_type(pair.get("theme_type"))
        text = _activation_pair_text(pair)
        signal = add_signal(
            "scanner_activation_pair",
            text,
            [f"ACT-{idx:02d}"],
            58 if play_type != "proactive_opportunity" else 52,
            "allowed_as_hypothesis",
            quote=pair.get("why") or pair.get("theme") or text,
            extra={
                "activation_pair_index": idx,
                "pair_index": idx,
                "theme_type": play_type,
                "sales_play_type": play_type,
                "play_type": play_type,
                "play_type_label": PLAY_TYPE_LABELS.get(play_type, "Sales play"),
                "theme": _clip(pair.get("theme"), 180),
                "why": _clip(pair.get("why"), 700),
                "what": _clip(pair.get("what"), 700),
                "account_movement": _clip(pair.get("why") or pair.get("theme") or text, 420),
                "commercial_pressure": _clip(pair.get("why") or text, 500),
                "business_consequence": _clip(pair.get("what") or _infer_pressure(text)[2], 500),
                "timing_relevance": (
                    "Current-status validation should be tested in near-term discovery."
                    if play_type == "current_status"
                    else "External pressure makes this worth validating as a forward-looking account theme."
                    if play_type == "foresight_driven"
                    else "Use as a proactive opportunity hypothesis until customer timing is confirmed."
                ),
            },
        )
        if signal:
            signal["need_hypothesis"] = _need_hypothesis_from_signal(signal)

    for item in _list(payload_seed.get("verified_evidence"))[:8]:
        if not isinstance(item, dict):
            continue
        text = _first_nonempty(item.get("title"), item.get("quote"), item.get("url"), limit=700)
        add_signal("verified_evidence", text, [item.get("id")], 60 if item.get("generation_eligible") else 45, "allowed_as_hypothesis", item.get("quote"))

    for item in _list(payload_seed.get("financial_sources"))[:5]:
        if not isinstance(item, dict):
            continue
        text = _first_nonempty(
            f"{item.get('metric') or 'Financial signal'} {item.get('value') or ''} {item.get('year') or ''}",
            item.get("quote"),
            item.get("title"),
            limit=700,
        )
        add_signal("financial_evidence", text, [item.get("id")], 65 if item.get("generation_eligible") else 50, "allowed_as_hypothesis", item.get("quote"))

    for key, source in (("ai_diagnosis", "scanner_ai_diagnosis"), ("pitch", "scanner_pitch"), ("domain_force_dossier", "scanner_domain_force")):
        add_signal(source, scanner_outputs.get(key), ["SCANNER"], 45, "allowed_as_hypothesis")

    for item in _list(scanner_outputs.get("why"))[:3]:
        if isinstance(item, dict):
            text = _first_nonempty(item.get("title"), item.get("text"), item.get("rationale"), limit=700)
        else:
            text = item
        add_signal("scanner_why", text, ["SCANNER-WHY"], 45, "allowed_as_discovery_question")

    for force in _list(payload_seed.get("foresight_forces"))[:5]:
        if not isinstance(force, dict):
            continue
        text = _first_nonempty(force.get("description"), force.get("theme"), force.get("dimension"), limit=700)
        add_signal("foresight_force", text, ["FORESIGHT"], _safe_int(force.get("confidence"), 45), "allowed_as_hypothesis")

    if not signals:
        company = _dict(payload_seed.get("company"))
        add_signal(
            "minimal_company_context",
            f"{company.get('name') or 'The account'} has limited stored evidence. Treat any buying-potential logic as discovery-only until stronger evidence is available.",
            ["LOW-EVIDENCE"],
            25,
            "allowed_as_discovery_question",
        )
    return signals[:limit]


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
    for force in _list(strategic_foresight.get("dimension_forces"))[:10]:
        if isinstance(force, dict):
            forces.append({
                "theme": force.get("theme") or force.get("force") or force.get("name"),
                "dimension": force.get("dimension"),
                "description": _clip(force.get("description") or force.get("rationale") or force.get("signal"), 500),
                "confidence": force.get("confidence"),
                "credibility": force.get("source_credibility") or force.get("credibility"),
            })

    services = []
    for svc in _list(service_kb.get("services"))[:24]:
        services.append({
            "name": svc.get("name"),
            "category": svc.get("category"),
            "buyer_personas": _list(svc.get("buyer_personas"))[:8],
            "pain_triggers": _list(svc.get("pain_triggers"))[:10],
            "evidence_patterns": _list(svc.get("evidence_patterns"))[:10],
            "proof_points": _list(svc.get("proof_points"))[:8],
            "safe_wording": _list(svc.get("safe_wording"))[:8] or svc.get("safe_wording"),
            "do_not_claim": _list(svc.get("do_not_claim"))[:8],
            "disqualifiers": _list(svc.get("disqualifiers"))[:8],
        })
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
            "ai_diagnosis": _clip(company_data.get("summary_text"), 2200),
            "strategic_synthesis": _clip(company_data.get("strategic_synthesis"), 2600),
            "evidence_based_risks": _list(company_data.get("evidence_based_risks"))[:6],
            "activation_pairs": _list(company_data.get("activation_pairs"))[:4],
            "why": _list(company_data.get("why_proposals"))[:4],
            "what": _list(company_data.get("what_proposals"))[:4],
            "pitch": _clip(company_data.get("pitch_text"), 900),
            "domain_force_dossier": _clip(company_data.get("domain_force_dossier"), 1800),
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
        "stakeholders": key_people,
        "stakeholder_name_list": _stakeholder_name_list(company_data, company_evidence, company_source_ledger),
        "foresight_forces": forces,
        "services": services,
        "self_scan_context": self_scan_context,
    }
    payload["account_signal_map"] = _build_account_signal_map(payload, services)
    payload["buyer_role_lens"] = BUYER_ROLE_LENSES
    return payload


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
  "recommended_sales_plays": [{{"service_or_offer": "...", "need_hypothesis": {{"future_pressure": "...", "current_condition": "...", "structural_exposure": "...", "likely_business_consequence": "...", "why_now_or_soon": "...", "what_to_propose": "...", "discovery_question": "...", "disqualifier": "...", "company_context": "...", "market_context": "...", "combined_activation_hypothesis": "...", "falsifier": "...", "context_type": "company_only|market_watch|combined_activation"}}, "response_directions": {{"potential_need": "...", "evolving_modernization_directions": [], "vivicta_potential_contribution": "...", "other_capabilities_may_be_required": []}}, "readiness_band": "Validate now|Validate when trigger appears|Monitor|Insufficient account connection", "evidence_coverage": "...", "confidence_boundary": "...", "priority_band": "...", "why_relevant": "...", "target_stakeholder": "...", "opening_angle": "...", "executive_angle": "...", "technical_angle": "...", "discovery_questions": ["..."], "do_not_say": ["..."], "confidence": 0, "hypothesis_strength": 0, "score_label": "Internal compatibility score", "overclaim_risk": "low|medium|high", "evidence_basis": [], "disqualifiers": []}}],
  "sales_spine": {{"account_thesis": "", "strategic_synthesis_basis": "", "top_buying_hypotheses": [], "risk_themes": [], "self_scan_note": ""}},
  "evidence_confidence": {{"strongest_evidence": [], "verified_facts": [], "safe_hypotheses": [], "use_only_as_question": [], "weak_or_missing_evidence": [], "do_not_use_claims": [], "do_not_say": [], "sensitive_claims": [], "claims_as_questions_only": []}},
  "overall_confidence": 0,
  "missing_input_warnings": [],
  "model_notes": []
}}

Payload:
{json.dumps(payload, ensure_ascii=False)}
""".strip()


BUYING_SECTION_SCHEMAS = {
    "snapshot_thesis": """
{
  "account_snapshot": {"text": "...", "gate": "allowed_as_fact|allowed_as_hypothesis|allowed_as_discovery_question|internal_only|blocked", "evidence_basis": ["SRC-01"]},
  "sales_spine": {"account_thesis": "...", "strategic_synthesis_basis": "...", "top_buying_hypotheses": [], "risk_themes": [], "self_scan_note": ""},
  "buying_potential_thesis": {"text": "...", "rationale": "...", "top_pressure_themes": [], "top_buyer_roles": [], "service_families": [], "gate": "allowed_as_hypothesis", "confidence": 0, "evidence_basis": ["SIG-01"], "overclaim_risk": "low|medium|high"},
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
  "recommended_sales_plays": [{"play_name": "...", "linked_signal_ids": ["SIG-01"], "activation_pair_index": 1, "play_type": "current_status|foresight_driven|proactive_opportunity", "service_or_offer": "...", "need_hypothesis": {"future_pressure": "...", "current_condition": "...", "structural_exposure": "...", "likely_business_consequence": "...", "why_now_or_soon": "...", "what_to_propose": "...", "discovery_question": "...", "disqualifier": "...", "company_context": "...", "market_context": "...", "combined_activation_hypothesis": "...", "falsifier": "...", "context_type": "company_only|market_watch|combined_activation"}, "response_directions": {"potential_need": "...", "evolving_modernization_directions": [], "vivicta_potential_contribution": "...", "other_capabilities_may_be_required": []}, "readiness_band": "Validate now|Validate when trigger appears|Monitor|Insufficient account connection", "evidence_coverage": "...", "confidence_boundary": "...", "priority_band": "...", "primary_buyer": "...", "secondary_buyers": [], "trigger": "...", "account_thesis": "...", "buying_hypothesis": "...", "trigger_to_offer_bridge": "...", "business_hypothesis": "...", "why_relevant": "...", "target_stakeholder": "...", "first_meeting_objective": "...", "opening_angle": "...", "executive_angle": "...", "technical_angle": "...", "safe_customer_wording": "...", "discovery_questions": ["..."], "discovery_questions_by_role": [{"role": "...", "questions": ["..."]}], "proof_point": "...", "do_not_say": ["..."], "qualification_upgrade_criteria": [], "confidence": 0, "play_score": 0, "hypothesis_strength": 0, "score_label": "Internal compatibility score", "play_classification": "strong_account_play|qualified_discovery_play|monitor_soft_discovery|weak_signal|do_not_use", "overclaim_risk": "low|medium|high", "evidence_basis": ["SIG-01"], "disqualifiers": []}],
  "model_notes": []
}
""".strip(),
    "pressure_evidence": """
{
  "pressure_signals": [{"signal_type": "...", "trigger": "...", "description": "...", "business_consequence": "...", "buyer_role_concern": "...", "gate": "allowed_as_hypothesis|allowed_as_discovery_question|internal_only|blocked", "confidence": 0, "evidence_basis": ["SIG-01"]}],
  "evidence_confidence": {"strongest_evidence": [], "verified_facts": [], "safe_hypotheses": [], "use_only_as_question": [], "weak_or_missing_evidence": [], "do_not_use_claims": [], "do_not_say": [], "sensitive_claims": [], "claims_as_questions_only": []},
  "overall_confidence": 0,
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


BUYING_SECTION_INSTRUCTIONS = {
    "snapshot_thesis": (
        "Create the account context and Account Opportunity Hypothesis. Start from account_signal_map, "
        "especially scanner_activation_pair signals. Use strategic_synthesis as the sales spine, General Summary "
        "only for factual context, and AI Diagnosis / evidence_based_risks only for internal sales reasoning. "
        "The thesis must name the account movement, commercial pressure, likely buyer roles, service families, "
        "confidence boundary, overclaim risk, and top 2-3 buying hypotheses."
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
        "(company_only, market_watch, or combined_activation). WHY is the reason the pressure may matter; WHAT is "
        "a validation or response direction, not a ready-made solution. "
        "Each play must also name account thesis, buying hypothesis, trigger-to-offer bridge, primary/secondary buyers, "
        "first meeting objective, discovery questions by buyer role, qualification upgrade criteria, disqualifiers, "
        "safe customer-facing wording, do_not_say notes, readiness_band, evidence_coverage, confidence_boundary, "
        "priority_band, and response_directions. Keep play_score/hypothesis_strength only as internal compatibility fields. "
        "Do not create a separate conversation-angles section or a delivery plan."
    ),
    "pressure_evidence": (
        "Summarize Commercial Pressure Signals and evidence confidence. Use account_signal_map first and keep "
        "verified facts, safe hypotheses, question-only claims, and do-not-say items in separate buckets. "
        "Do not create empty pressure descriptions. Be precise about what is strong, weak, question-only, "
        "sensitive, or blocked."
    ),
    "stakeholders": (
        "Create a rich Buying Committee & Influence Map. Use named stakeholders only when source-bound and "
        "role-relevant; otherwise use role-level buying committee guidance from buyer_role_lens. Each card must "
        "explain likely committee role, relevance rationale, linked pressure/service themes, evidence basis, "
        "allowed-use boundary, confidence, and do_not_say safety notes. Keep it professional and no private-life profiling."
    ),
}


BUYING_SECTION_KEYS = {
    "snapshot_thesis": ("account_snapshot", "sales_spine", "buying_potential_thesis"),
    "why_now": ("why_now",),
    "pain_to_solution": ("pain_to_solution_map",),
    "sales_plays": ("recommended_sales_plays",),
    "pressure_evidence": ("pressure_signals", "evidence_confidence", "overall_confidence", "missing_input_warnings"),
    "stakeholders": ("stakeholder_map",),
}


def _section_prompt(section: str, payload: dict) -> str:
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
- Strategic Synthesis and scanner_activation_pair signals are the sales spine. Preserve each pair's why/what logic.
- WHY is the reason the pressure may matter; WHAT is the validation or response direction. Do not turn WHAT into a pre-scoped solution.
- General Summary is factual context only. AI Diagnosis and evidence_based_risks are internal reasoning and discovery framing, not customer-facing facts.
- Numeric scores are internal compatibility diagnostics only. They must not be used as proof that a need exists, as customer-facing evidence, or as the main reason for a proposal.
- Every recommended play should map to one activation pair when any activation-pair SIG exists.
- Every service recommendation must cite at least one account_signal_map SIG id. Service KB pain triggers may refine wording, but cannot be the only reason for a recommendation.
- Every recommended play must separate company_context, market_context, combined_activation_hypothesis, falsifier, and response_directions. Response directions are not delivery scope, staffing, pricing, schedule, or implementation design.
- If a bridge from account signal to service is weak, use allowed_as_discovery_question or internal_only.
- Do not output empty pressure descriptions or generic buyer targets such as "relevant owner".

Return ONLY valid JSON with this exact section schema:
{BUYING_SECTION_SCHEMAS[section]}

Payload:
{json.dumps(payload, ensure_ascii=False)}
""".strip()


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
{_clip(raw_json, 42000)}
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
- overall_confidence and all confidence fields must be integers from 0 to 100.

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
  "overall_confidence": 0,
  "missing_input_warnings": [],
  "model_notes": []
}}

Malformed JSON:
{_clip(raw_json, 42000)}
""".strip()


def _gate_prompt(report: dict, payload: dict) -> str:
    return f"""
Review this Buying Potential report for evidence safety. Use only the payload.
Return JSON with:
{{
  "approved": true,
  "required_downgrades": [{{"path": "pressure_signals[0]", "reason": "...", "new_gate": "allowed_as_hypothesis|allowed_as_discovery_question|internal_only|blocked"}}],
  "blocked_claims": ["..."],
  "confidence_adjustment": 0,
  "notes": ["..."]
}}

Rules:
- Factual claims need generation-eligible evidence or verified structured financial evidence.
- Weak evidence can be hypothesis/question only.
- Unsupported exact dates, financial numbers, ownership percentages, partnerships, and named implementations must be blocked.
- Internal Scanner observations are allowed only as internal metrics, not external market facts.
- Service recommendations must be grounded in account_signal_map. A Service KB pain trigger alone is not enough.
- Downgrade or block sales plays that lack a linked SIG id, activation_pair_index when activation-pair SIGs exist, buyer-role concern, service match, safe discovery question, qualification criteria, safe customer wording, or disqualifier.
- Downgrade sales plays that use scores as proof, skip company/market context separation, or turn response directions into delivery scope.
- Evidence confidence must keep verified facts, safe hypotheses, question-only claims, and do-not-say claims separate.
- Empty pressure signals, generic buyer targets, and unsupported service-fit leaps should be downgraded.

Report:
{json.dumps(report, ensure_ascii=False)}

Payload:
{json.dumps(payload, ensure_ascii=False)}
""".strip()


def _normalize_gate(value: Any, fallback: str = "allowed_as_hypothesis") -> str:
    gate = str(value or "").strip().lower()
    return gate if gate in ALLOWED_GATES else fallback


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
            for key in ("name", "category"):
                value = str(svc.get(key) or "").strip().lower()
                if value:
                    names.add(value)
    return names


def _service_match_exists(item: dict, service_names: set[str]) -> bool:
    if not service_names:
        return False
    text = " ".join(
        str(item.get(key) or "")
        for key in (
            "service",
            "service_or_offer",
            "why_relevant",
            "opening_angle",
            "safe_customer_wording",
            "trigger_to_offer_bridge",
            "proof_point",
        )
    ).lower()
    return any(name and name in text for name in service_names)


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


def _score_play(play: dict, *, has_signal: bool, has_service: bool, has_question: bool) -> int:
    evidence_strength = 25 if has_signal else 0
    business_pressure = 20 if _clip(play.get("business_hypothesis") or play.get("why_relevant"), 80) else 5
    buyer_relevance = 15 if _clip(play.get("primary_buyer") or play.get("target_stakeholder"), 80) else 0
    timing_strength = 15 if _clip(play.get("trigger"), 80) else 5
    vivicta_fit = 15 if has_service else 0
    safe_outreach = 10 if has_question else 0
    return evidence_strength + business_pressure + buyer_relevance + timing_strength + vivicta_fit + safe_outreach


def _looks_unsafe_claim(text: Any) -> bool:
    token = str(text or "").lower()
    return any(
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
        )
    )


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


def _post_process_report(report: dict, payload: dict, gate_review: dict | None = None) -> dict:
    verified_count = len(payload.get("verified_evidence") or [])
    financial_count = len(payload.get("financial_sources") or [])
    low_evidence = verified_count < 2 and financial_count < 1
    known_signal_ids = {str(signal.get("id")) for signal in _list(payload.get("account_signal_map")) if isinstance(signal, dict) and signal.get("id")}
    signals_by_id = _signal_by_id(payload)
    activation_signal_ids = _activation_signal_ids(payload)
    service_names = _known_service_names(payload)
    scanner_outputs = _dict(payload.get("scanner_outputs"))
    self_scan_context = _dict(payload.get("self_scan_context"))

    def fix_item(item: Any, default_gate: str = "allowed_as_hypothesis") -> Any:
        if not isinstance(item, dict):
            return item
        gate = _normalize_gate(item.get("gate"), default_gate)
        if gate == "allowed_as_fact" and not _has_basis(item):
            gate = "allowed_as_hypothesis" if not low_evidence else "allowed_as_discovery_question"
        item["gate"] = gate
        item["confidence"] = max(0, min(100, _safe_int(item.get("confidence"), 50)))
        return item

    for key in ("account_snapshot", "buying_potential_thesis"):
        if isinstance(report.get(key), dict):
            fix_item(report[key], "allowed_as_hypothesis")
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
    report["sales_spine"] = sales_spine
    for key in ("why_now",):
        report[key] = [fix_item(item) for item in _list(report.get(key)) if isinstance(item, dict)]
    report["pressure_signals"] = [
        fix_item(item)
        for item in _list(report.get("pressure_signals"))
        if isinstance(item, dict) and _clip(item.get("description") or item.get("business_consequence") or item.get("trigger"), 40)
    ]
    report["pain_to_solution_map"] = [fix_item(item) for item in _list(report.get("pain_to_solution_map")) if isinstance(item, dict)]
    report["recommended_sales_plays"] = [fix_item(item) for item in _list(report.get("recommended_sales_plays")) if isinstance(item, dict)]
    evidence_confidence = _dict(report.get("evidence_confidence"))
    weak_notes = evidence_confidence.setdefault("weak_or_missing_evidence", [])
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
        if activation_signal:
            play["activation_pair_index"] = _safe_int(play.get("activation_pair_index"), _safe_int(activation_signal.get("activation_pair_index"), 0))
            play["play_type"] = _normalize_play_type(play.get("play_type") or activation_signal.get("sales_play_type"))
            play["trigger"] = play.get("trigger") or activation_signal.get("account_movement")
            play["buying_hypothesis"] = play.get("buying_hypothesis") or activation_signal.get("why")
            play["trigger_to_offer_bridge"] = play.get("trigger_to_offer_bridge") or activation_signal.get("what")
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
            "account_movement": play.get("account_thesis"),
            "commercial_pressure": play.get("business_hypothesis"),
            "business_consequence": play.get("business_hypothesis"),
        }
        play["need_hypothesis"] = _normalize_need_hypothesis(play.get("need_hypothesis"), fallback_signal, play.get("play_type"))
        role_questions = _list(play.get("discovery_questions_by_role"))
        if not role_questions:
            play["discovery_questions_by_role"] = [{
                "role": play.get("primary_buyer") or play.get("target_stakeholder") or "Executive sponsor",
                "questions": play["discovery_questions"][:3],
            }]
        score = _score_play(play, has_signal=has_signal, has_service=has_service, has_question=bool(play["discovery_questions"]))
        play["play_score"] = max(0, min(100, _safe_int(play.get("play_score"), score)))
        play["hypothesis_strength"] = play["play_score"]
        play["score_label"] = "Internal compatibility score"
        play["play_classification"] = play.get("play_classification") or _classify_play_score(play["play_score"])
        evidence_coverage = _evidence_coverage(
            has_signal=has_signal,
            has_service=has_service,
            has_question=bool(play["discovery_questions"]),
            low_evidence=low_evidence,
        )
        readiness = _readiness_band(
            play["play_score"],
            has_signal=has_signal,
            has_service=has_service,
            has_question=bool(play["discovery_questions"]),
            gate=play.get("gate"),
        )
        play["readiness_band"] = play.get("readiness_band") or readiness
        play["evidence_coverage"] = play.get("evidence_coverage") or evidence_coverage
        play["confidence_boundary"] = play.get("confidence_boundary") or _confidence_boundary(play.get("gate"), play["evidence_coverage"])
        play["priority_band"] = play.get("priority_band") or _priority_band(play["readiness_band"])
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
        if play["play_classification"] in {"weak_signal", "do_not_use"} and play.get("gate") != "blocked":
            play["gate"] = "internal_only"
            play["confidence_boundary"] = _confidence_boundary(play.get("gate"), play["evidence_coverage"])
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
        fixed_stakeholders.append(fixed)
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
    if not _list(evidence_confidence.get("verified_facts")):
        evidence_confidence["verified_facts"] = [
            str(item) for item in _list(evidence_confidence.get("strongest_evidence"))[:8]
            if str(item).strip()
        ]
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
    unsafe_claims = [
        str(item) for item in _list(evidence_confidence.get("do_not_say"))
        + _list(evidence_confidence.get("do_not_use_claims"))
        if _looks_unsafe_claim(item)
    ]
    unsafe_claims.extend(str(item) for item in _list(_dict(scanner_outputs.get("evidence_trust")).get("false_or_unsupported_claims")) if item)
    evidence_confidence["do_not_say"] = list(dict.fromkeys(unsafe_claims))[:12]
    evidence_confidence["do_not_use_claims"] = evidence_confidence["do_not_say"]
    report["evidence_confidence"] = evidence_confidence

    if gate_review:
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
        blocked = _list(gate_review.get("blocked_claims"))
        blocked_claims = [str(item) for item in blocked[:12] if item]
        if blocked_claims:
            evidence_confidence.setdefault("do_not_say", []).extend(blocked_claims)
        report["gate_review"] = {
            "approved": gate_review.get("approved") is True,
            "required_downgrades": downgrades[:20],
            "blocked_claims": blocked[:20],
            "notes": _list(gate_review.get("notes"))[:10],
        }
        evidence_confidence["do_not_say"] = list(dict.fromkeys(
            [
                str(item)
                for item in _list(evidence_confidence.get("do_not_say"))
                if _looks_unsafe_claim(item)
            ]
            + blocked_claims
        ))[:12]
        evidence_confidence["do_not_use_claims"] = evidence_confidence["do_not_say"]

    report["overall_confidence"] = max(0, min(100, _safe_int(report.get("overall_confidence"), 45)))
    if low_evidence:
        report["overall_confidence"] = min(report["overall_confidence"], 55)
    report["report_type"] = "buying_potential"
    report["generated_at"] = datetime.utcnow().isoformat()
    report["safety_statement"] = "Need hypothesis and demand-creation potential, not confirmed buying intent. Customer-facing use must follow each insight gate."
    report["self_scan_context"] = self_scan_context
    report["source_counts"] = {
        "generation_eligible": verified_count,
        "financial_sources": financial_count,
        "blocked_or_display_only": len(payload.get("blocked_or_display_only_sources") or []),
    }
    return report


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
    for signal in account_signals[:6]:
        for hint in _list(signal.get("service_fit_hints"))[:2]:
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
                "play_name": f"{service_name} discovery play",
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
                "trigger_to_offer_bridge": _clip(signal.get("what") or trigger, 300),
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
                "proof_point": proof_point,
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
            if len(sales_plays) >= 5:
                break
        if len(sales_plays) >= 5:
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
    weak_notes = [
        f"{item.get('title') or item.get('url')}: {item.get('verification_reason') or item.get('promotion_status') or 'not generation eligible'}"
        for item in weak[:8]
    ]

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
            "verified_facts": strongest,
            "safe_hypotheses": [
                _clip(signal.get("why") or signal.get("commercial_pressure") or signal.get("account_movement"), 260)
                for signal in activation_signals[:3]
            ],
            "use_only_as_question": ["Service-fit and timing signals should be used as discovery questions unless independently verified."],
            "weak_or_missing_evidence": weak_notes + ["Generative report JSON was malformed; deterministic fallback used."],
            "do_not_use_claims": _list(_dict(outputs.get("evidence_trust")).get("false_or_unsupported_claims"))[:10],
            "do_not_say": _list(_dict(outputs.get("evidence_trust")).get("false_or_unsupported_claims"))[:10],
            "sensitive_claims": [],
            "claims_as_questions_only": ["Service-fit and timing signals should be used as discovery questions unless independently verified."],
        },
        "overall_confidence": 42,
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
    if section in {"pain_to_solution", "sales_plays", "pressure_evidence"}:
        return 2600
    return 1800


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
        if stage_callback:
            stage_callback("generate_report", 55, f"Repairing malformed Buying Insight section: {section}")
        try:
            repair_raw, repair_model = call_json_model_chain(
                _section_repair_prompt(section, raw),
                buying_report_repair_chain(model_mode),
                max_tokens=_section_token_limit(section),
                timeout=60,
            )
            parsed = parse_json_object(repair_raw)
            if not isinstance(parsed, dict):
                raise ValueError("Repaired section JSON root was not an object")
            notes = _list(parsed.get("model_notes"))
            notes.append(f"Malformed section JSON repaired by {repair_model}. Original parse error: {str(parse_exc)[:160]}")
            parsed["model_notes"] = notes
            return parsed, f"repaired by {repair_model}"
        except Exception as repair_exc:
            reason = f"Section '{section}' JSON parse failed ({parse_exc}); repair failed ({repair_exc})"
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
        raw, model_used = call_json_model_chain(
            _section_prompt(section, payload),
            buying_generation_chain(model_mode),
            max_tokens=_section_token_limit(section),
            timeout=75,
        )
        section_data, parse_status = _parse_or_repair_section(section, raw, payload, model_mode, stage_callback)
        meta = {"section": section, "model": model_used, "parse_status": parse_status}
        return section_data, meta
    except Exception as exc:
        reason = f"Section '{section}' generation failed: {exc}"
        return _section_fallback(section, payload, reason), {
            "section": section,
            "model": "deterministic_fallback",
            "parse_status": "section_generation_failed",
            "error": str(exc)[:220],
        }


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
    report.setdefault("overall_confidence", 45)
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
        gate_raw, gate_model = call_json_model_chain(_gate_prompt(report, payload), buying_gate_chain(model_mode), max_tokens=1800, timeout=60)
        gate_review = parse_json_object(gate_raw)
        gate_review["model"] = gate_model
    except Exception as exc:
        gate_review = {"approved": False, "notes": [f"Gate model unavailable; deterministic post-processing applied: {str(exc)[:160]}"]}

    report = _post_process_report(report, payload, gate_review)
    report["account_signal_map"] = _list(payload.get("account_signal_map"))[:12]
    report["buyer_role_lens_version"] = "v1"
    report["companyName"] = company_data.get("companyName")
    report["companyUrl"] = company_data.get("companyUrl")
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


def render_buying_potential_html(report: dict) -> str:
    company = report.get("companyName") or "Company"
    generated = report.get("generated_at", "")[:10]
    confidence = report.get("overall_confidence", 0)
    snapshot = _dict(report.get("account_snapshot"))
    thesis = _dict(report.get("buying_potential_thesis"))
    sales_spine = _dict(report.get("sales_spine"))
    evidence = _dict(report.get("evidence_confidence"))
    self_scan = _dict(report.get("self_scan_context"))

    def overall_stance(value):
        score = _safe_int(value, 0)
        if score >= 70:
            return "Evidence stance: strong discovery basis"
        if score >= 45:
            return "Evidence stance: cautious hypothesis basis"
        if score > 0:
            return "Evidence stance: constrained / question-led"
        return "Evidence stance: not scored"

    def simple_items(items, limit=10):
        return "".join(f"<li>{_esc(x)}</li>" for x in _list(items)[:limit]) or "<li>No items generated.</li>"

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
        return f"""
        <article class="card">
          <div class="card-top"><strong>{_esc(item.get('service'))}</strong>{_gate_badge(item.get('gate'))}</div>
          <p><b>Account signal:</b> {_esc(item.get('account_signal_id'))}</p>
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
          <h3>Need Hypothesis</h3>
          <p><b>Company context:</b> {_esc(need.get('company_context'))}</p>
          <p><b>Market context:</b> {_esc(need.get('market_context'))}</p>
          <p><b>Combined activation hypothesis:</b> {_esc(need.get('combined_activation_hypothesis'))}</p>
          <p><b>Future pressure:</b> {_esc(need.get('future_pressure'))}</p>
          <p><b>Current condition:</b> {_esc(need.get('current_condition'))}</p>
          <p><b>Structural exposure:</b> {_esc(need.get('structural_exposure'))}</p>
          <p><b>Likely consequence:</b> {_esc(need.get('likely_business_consequence'))}</p>
          <p><b>Why now or soon:</b> {_esc(need.get('why_now_or_soon'))}</p>
          <p><b>What to validate or explore:</b> {_esc(need.get('what_to_propose'))}</p>
          <p><b>Falsifier:</b> {_esc(need.get('falsifier'))}</p>
          <h3>Potential Response Directions</h3>
          <p><b>Potential need:</b> {_esc(response.get('potential_need'))}</p>
          <p><b>Vivicta potential contribution:</b> {_esc(response.get('vivicta_potential_contribution'))}</p>
          <h3>Evolving Modernization Directions</h3>
          <ul>{modernization or '<li>No response directions generated.</li>'}</ul>
          <h3>Other Capabilities May Be Required</h3>
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
          <h3>Do Not Say</h3>
          <ul>{do_not_say or '<li>No guardrails generated.</li>'}</ul>
          <small>Signals: {_esc(', '.join(item.get('linked_signal_ids') or item.get('evidence_basis') or []))} · Classification: {_esc(item.get('play_classification'))} · Internal score retained in JSON only</small>
        </article>"""

    def hypothesis_items(context_type, limit=6):
        items = []
        for play in _list(report.get("recommended_sales_plays")):
            if not isinstance(play, dict):
                continue
            need = _dict(play.get("need_hypothesis"))
            if _normalize_context_type(need.get("context_type"), play.get("play_type")) != context_type:
                continue
            text = _clip(
                need.get("combined_activation_hypothesis")
                or need.get("company_context")
                or need.get("market_context")
                or play.get("buying_hypothesis"),
                420,
            )
            if text:
                items.append(text)
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
    .tiny {{ font-size:9px; padding:4px 7px; margin:2px 4px 2px 0; }} .service {{ color:var(--cyan); border-color:rgba(103,232,249,.28); }} .muted-pill {{ opacity:.65; }}
    .gate-allowed_as_fact {{ color:var(--green); border-color:rgba(110,231,183,.5); }} .gate-allowed_as_hypothesis {{ color:var(--cyan); border-color:rgba(103,232,249,.45); }}
    .gate-allowed_as_discovery_question {{ color:var(--amber); border-color:rgba(250,204,21,.45); }} .gate-internal_only {{ color:var(--violet); border-color:rgba(167,139,250,.45); }} .gate-blocked {{ color:var(--rose); border-color:rgba(251,113,133,.45); }}
    section {{ border:1px solid var(--line); background:rgba(16,27,46,.74); border-radius:14px; padding:24px; margin:20px 0; }}
    .grid {{ display:grid; grid-template-columns:repeat(2,minmax(0,1fr)); gap:14px; }} .grid-3 {{ grid-template-columns:repeat(3,minmax(0,1fr)); }}
    .card {{ background:rgba(7,17,31,.72); border:1px solid rgba(142,160,187,.18); border-radius:12px; padding:16px; }} .compact {{ padding:13px; }}
    .card-top {{ display:flex; justify-content:space-between; align-items:flex-start; gap:12px; margin-bottom:8px; }}
    small,.muted {{ color:var(--muted); }} table {{ width:100%; border-collapse:collapse; overflow:hidden; border-radius:10px; }} th,td {{ padding:11px; border-bottom:1px solid rgba(142,160,187,.16); text-align:left; vertical-align:top; }} th {{ color:var(--muted); font:700 11px ui-monospace,monospace; text-transform:uppercase; letter-spacing:.12em; }}
    ul {{ margin:8px 0 0 18px; padding:0; }} li {{ margin:5px 0; }} .warning {{ border-color:rgba(250,204,21,.4); background:rgba(250,204,21,.08); }} .danger {{ border-color:rgba(251,113,133,.4); background:rgba(251,113,133,.08); }}
    .role-line {{ color:var(--green); font:800 12px ui-monospace,monospace; text-transform:uppercase; letter-spacing:.12em; margin:-2px 0 12px; }} .theme-row {{ display:flex; flex-wrap:wrap; gap:4px; margin:8px 0; }}
    .simple-list-block {{ margin-top:18px; border-top:1px solid rgba(142,160,187,.18); padding-top:18px; }}
    @media (max-width:820px) {{ main {{ padding:24px 14px 48px; }} .grid,.grid-3 {{ grid-template-columns:1fr; }} .card-top {{ flex-direction:column; }} }}
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
      <span class="pill">{_esc(overall_stance(confidence))}</span>
      <span class="pill">Sources {_esc(_dict(report.get('source_counts')).get('generation_eligible', 0))} eligible</span>
      <span class="pill">Financial refs {_esc(_dict(report.get('source_counts')).get('financial_sources', 0))}</span>
    </div>
  </header>

  <section>
    <h2>Account Context & Qualification Snapshot</h2>
    <p>{_esc(snapshot.get('text'))}</p>
    {_gate_badge(snapshot.get('gate'))}
  </section>

  <section>
    <h2>Account Need Hypothesis</h2>
    <p>{_esc(thesis.get('text'))}</p>
    <p class="muted">{_esc(thesis.get('rationale'))}</p>
    {_gate_badge(thesis.get('gate'))}
  </section>

  <section>
    <h2>Sales Spine</h2>
    {f'<article class="card warning"><p>{_esc(self_scan.get("framing"))}</p></article>' if self_scan.get("is_self_scan") else ''}
    <p><b>Account thesis:</b> {_esc(sales_spine.get('account_thesis'))}</p>
    <p><b>Strategic synthesis basis:</b> {_esc(sales_spine.get('strategic_synthesis_basis'))}</p>
    <div class="grid grid-3">
      <div class="card compact"><h3>Top Buying Hypotheses</h3><ul>{simple_items(sales_spine.get('top_buying_hypotheses'), 5)}</ul></div>
      <div class="card compact"><h3>Risk Themes For Discovery</h3><ul>{simple_items(sales_spine.get('risk_themes'), 5)}</ul></div>
      <div class="card compact"><h3>Internal Note</h3><p>{_esc(sales_spine.get('self_scan_note'))}</p></div>
    </div>
  </section>

  <section>
    <h2>Need Hypothesis Spine</h2>
    <div class="grid grid-3">
      <div class="card compact"><h3>Company-Derived Need Hypotheses</h3><ul>{hypothesis_items('company_only')}</ul></div>
      <div class="card compact"><h3>Market-Derived Pressure Hypotheses</h3><ul>{hypothesis_items('market_watch')}</ul></div>
      <div class="card compact"><h3>Combined Activation Hypotheses</h3><ul>{hypothesis_items('combined_activation')}</ul></div>
    </div>
  </section>

  <section><h2>Commercial Pressure Signals</h2><div class="grid">{_render_list(_list(report.get('pressure_signals')), pressure_card)}</div></section>
  <section><h2>Timing Triggers & Urgency Signals</h2><div class="grid">{_render_list(_list(report.get('why_now')), why_card)}</div></section>

  <section>
    <h2>Buying Committee & Influence Map</h2>
    <div class="grid">{_render_list(_list(report.get('stakeholder_map')), stakeholder_card)}</div>
    {extra_stakeholder_rows(report.get('additional_stakeholder_names'))}
  </section>

  <section><h2>Potential Response Directions</h2><div class="grid">{_render_list(_list(report.get('pain_to_solution_map')), solution_card)}</div></section>
  <section><h2>Recommended Growth Plays</h2><div class="grid">{_render_list(_list(report.get('recommended_sales_plays')), play_card)}</div></section>


  <section class="warning">
    <h2>Evidence, Confidence & Sales Guardrails</h2>
    <div class="grid grid-3">
      <div><h3>Verified Facts</h3><ul>{simple_items(evidence.get('verified_facts') or evidence.get('strongest_evidence'), 12)}</ul></div>
      <div><h3>Safe Hypotheses</h3><ul>{simple_items(evidence.get('safe_hypotheses'), 12)}</ul></div>
      <div><h3>Use Only As Question</h3><ul>{simple_items(evidence.get('use_only_as_question') or evidence.get('claims_as_questions_only'), 12)}</ul></div>
      <div><h3>Weak Or Missing</h3><ul>{simple_items(evidence.get('weak_or_missing_evidence'), 12)}</ul></div>
      <div><h3>Do Not Say</h3><ul>{simple_items(evidence.get('do_not_say') or evidence.get('do_not_use_claims'), 12)}</ul></div>
      <div><h3>Sensitive Claims</h3><ul>{simple_items(evidence.get('sensitive_claims'), 12)}</ul></div>
    </div>
  </section>
</main>
</body>
</html>"""
