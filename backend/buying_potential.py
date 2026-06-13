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


SERVICE_KB_EXTRACTION_CHAIN = (GPT_REASONING_MODEL, CLAUDE_SONNET_MODEL)
SERVICE_KB_CACHE_PREFIX = "service_portfolio/vivicta_services.extracted"
SERVICE_KB_TEXT_LIMIT = 65000
ALLOWED_GATES = {
    "allowed_as_fact",
    "allowed_as_hypothesis",
    "allowed_as_discovery_question",
    "internal_only",
    "blocked",
}


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
    value = _clean_env(mode or os.environ.get("BUYING_POTENTIAL_MODEL_MODE") or "testing").lower()
    return "quality" if value == "quality" else "testing"


def _haiku_model() -> str:
    return _clean_env(os.environ.get("CLAUDE_HAIKU_MODEL") or CLAUDE_HAIKU_MODEL)


def buying_generation_chain(mode: str | None = None) -> tuple[str, ...]:
    if _buying_model_mode(mode) == "quality":
        return _dedupe_chain((CLAUDE_OPUS_MODEL, GPT_REASONING_MODEL, CLAUDE_SONNET_MODEL))
    return _dedupe_chain((CLAUDE_SONNET_MODEL, _haiku_model(), GPT_REASONING_MODEL))


def buying_gate_chain(mode: str | None = None) -> tuple[str, ...]:
    if _buying_model_mode(mode) == "quality":
        return _dedupe_chain((GPT_VERIFIER_MODEL, CLAUDE_SONNET_MODEL))
    return _dedupe_chain((CLAUDE_SONNET_MODEL, _haiku_model()))


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
    raw, model_used = call_json_model_chain(
        _service_kb_extraction_prompt(pdf_text),
        SERVICE_KB_EXTRACTION_CHAIN,
        max_tokens=12000,
        timeout=120,
    )
    try:
        kb = _validate_service_kb(parse_json_object(raw))
    except Exception as parse_exc:
        print(f"   ⚠️ [Buying Potential] Service KB model JSON parse failed; attempting repair: {parse_exc}")
        try:
            repair_raw, repair_model = call_json_model_chain(
                _service_kb_repair_prompt(raw, pdf_text),
                (GPT_VERIFIER_MODEL, CLAUDE_SONNET_MODEL),
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


def _extract_evidence(company_data: dict) -> tuple[list[dict], list[dict]]:
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
    return strongest[:30], blocked[:30]


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


def _build_input_payload(company_data: dict, service_kb: dict) -> dict:
    evidence, blocked = _extract_evidence(company_data)
    financial_sources = _financial_evidence(company_data)
    ai_verification = _dict(company_data.get("ai_verification"))
    false_claims = _list(ai_verification.get("false_claims")) + _list(ai_verification.get("unsupported_claims"))
    key_people = []
    for person in _list(company_data.get("key_people"))[:10]:
        if isinstance(person, dict):
            key_people.append({
                "name": person.get("name"),
                "role": person.get("role") or person.get("title"),
                "insight": _clip(person.get("summary") or person.get("insight") or person.get("linkedin_insights"), 360),
                "confidence": person.get("confidence") or person.get("confidence_score"),
                "source": person.get("source") or person.get("source_url"),
            })

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

    return {
        "company": {
            "name": company_data.get("companyName") or company_data.get("company_name"),
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
        "stakeholders": key_people,
        "foresight_forces": forces,
        "services": services,
    }


def _generation_prompt(payload: dict) -> str:
    return f"""
You are creating an Admin-only Evidence-Gated Buying Potential & Insight report.

This is NOT confirmed buying intent. It is a sales-intelligence hypothesis based on already stored Scanner data.
Use only the JSON payload below. Do not browse the web. Do not invent services, clients, personal data, dates, financials, partnerships, ownership details, or buying signals.

Evidence contract:
- allowed_as_fact: only when supported by verified_evidence, financial_sources marked generation_eligible, or explicitly labeled Scanner internal metrics.
- allowed_as_hypothesis: plausible interpretation from Scanner diagnosis, pressure, foresight, or service-pattern fit.
- allowed_as_discovery_question: useful but weak or uncertain signal that should be asked, not asserted.
- internal_only: useful for sales prep but not safe for customer-facing wording.
- blocked: unsupported, wrong-entity, sensitive, or overclaiming material.
- Say "buying potential", never "confirmed buying intent", unless explicit evidence exists in the payload.
- Stakeholder notes must remain professional, role-relevant, and source-bound. No private-life profiling.

Return ONLY valid JSON with this schema:
{{
  "account_snapshot": {{"text": "...", "gate": "allowed_as_fact|allowed_as_hypothesis|allowed_as_discovery_question|internal_only|blocked", "evidence_basis": ["SRC-01"]}},
  "buying_potential_thesis": {{"text": "...", "gate": "allowed_as_hypothesis", "confidence": 0}},
  "pressure_signals": [{{"signal_type": "...", "description": "...", "gate": "...", "confidence": 0, "evidence_basis": []}}],
  "why_now": [{{"trigger": "...", "interpretation": "...", "gate": "...", "confidence": 0, "evidence_basis": []}}],
  "stakeholder_map": [{{"name": "...", "role": "...", "likely_buying_committee_role": "...", "evidence_basis": "...", "confidence": 0, "allowed_use": "internal_only|allowed_as_discovery_question|allowed_as_hypothesis"}}],
  "pain_to_solution_map": [{{"evidence": "...", "interpreted_pressure": "...", "service": "...", "proof_point": "...", "safe_wording": "...", "gate": "...", "confidence": 0, "disqualifiers": []}}],
  "recommended_sales_plays": [{{"service_or_offer": "...", "why_relevant": "...", "target_stakeholder": "...", "opening_angle": "...", "confidence": 0, "overclaim_risk": "low|medium|high", "evidence_basis": [], "disqualifiers": []}}],
  "conversation_angles": [{{"angle_type": "first_meeting|discovery_question|executive|technical|do_not_say", "text": "...", "gate": "...", "confidence": 0}}],
  "evidence_confidence": {{"strongest_evidence": [], "weak_or_missing_evidence": [], "do_not_use_claims": [], "sensitive_claims": [], "claims_as_questions_only": []}},
  "overall_confidence": 0,
  "missing_input_warnings": [],
  "model_notes": []
}}

Payload:
{json.dumps(payload, ensure_ascii=False)}
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
        return any(str(x).startswith(("SRC-", "FIN-")) for x in basis)
    return bool(basis)


def _post_process_report(report: dict, payload: dict, gate_review: dict | None = None) -> dict:
    verified_count = len(payload.get("verified_evidence") or [])
    financial_count = len(payload.get("financial_sources") or [])
    low_evidence = verified_count < 2 and financial_count < 1

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
    for key in ("pressure_signals", "why_now", "pain_to_solution_map", "recommended_sales_plays", "conversation_angles"):
        report[key] = [fix_item(item) for item in _list(report.get(key)) if isinstance(item, dict)]
    report["stakeholder_map"] = [
        fix_item(item, "internal_only") for item in _list(report.get("stakeholder_map")) if isinstance(item, dict)
    ]

    evidence_confidence = _dict(report.get("evidence_confidence"))
    if low_evidence:
        warnings = _list(report.get("missing_input_warnings"))
        warnings.append("Low promoted evidence: keep customer-facing wording cautious and question-led.")
        report["missing_input_warnings"] = warnings
        evidence_confidence.setdefault("weak_or_missing_evidence", []).append("Few generation-eligible source-register items were available.")
    report["evidence_confidence"] = evidence_confidence

    if gate_review:
        blocked = _list(gate_review.get("blocked_claims"))
        if blocked:
            evidence_confidence.setdefault("do_not_use_claims", []).extend(blocked[:12])
        report["gate_review"] = {
            "approved": gate_review.get("approved") is True,
            "required_downgrades": _list(gate_review.get("required_downgrades"))[:20],
            "blocked_claims": blocked[:20],
            "notes": _list(gate_review.get("notes"))[:10],
        }

    report["overall_confidence"] = max(0, min(100, _safe_int(report.get("overall_confidence"), 45)))
    if low_evidence:
        report["overall_confidence"] = min(report["overall_confidence"], 55)
    report["report_type"] = "buying_potential"
    report["generated_at"] = datetime.utcnow().isoformat()
    report["safety_statement"] = "Buying potential, not confirmed buying intent. Customer-facing use must follow each insight gate."
    report["source_counts"] = {
        "generation_eligible": verified_count,
        "financial_sources": financial_count,
        "blocked_or_display_only": len(payload.get("blocked_or_display_only_sources") or []),
    }
    return report


def generate_buying_potential_report(
    company_data: dict,
    service_kb: dict,
    *,
    stage_callback=None,
    model_mode: str | None = None,
) -> tuple[dict, str]:
    if not isinstance(company_data, dict) or not company_data.get("companyName"):
        raise BuyingPotentialError("Company data missing or invalid.", 404)
    if stage_callback:
        stage_callback("build_payload", 35, "Building Buying Insight prompt payload")
    payload = _build_input_payload(company_data, service_kb)
    if stage_callback:
        stage_callback("generate_report", 50, "Generating Buying Potential report")
    prompt = _generation_prompt(payload)
    raw, model_used = call_json_model_chain(prompt, buying_generation_chain(model_mode), max_tokens=5200, timeout=120)
    report = parse_json_object(raw)
    report["generation_model"] = model_used
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
    evidence = _dict(report.get("evidence_confidence"))

    def pressure_card(item):
        return f"""
        <article class="card">
          <div class="card-top"><strong>{_esc(item.get('signal_type'))}</strong>{_gate_badge(item.get('gate'))}</div>
          <p>{_esc(item.get('description'))}</p>
          <small>Confidence {_esc(item.get('confidence'))}/100 · Evidence: {_esc(', '.join(item.get('evidence_basis') or []))}</small>
        </article>"""

    def why_card(item):
        return f"""
        <article class="card">
          <div class="card-top"><strong>{_esc(item.get('trigger'))}</strong>{_gate_badge(item.get('gate'))}</div>
          <p>{_esc(item.get('interpretation'))}</p>
          <small>Confidence {_esc(item.get('confidence'))}/100</small>
        </article>"""

    def stakeholder_row(item):
        return f"<tr><td>{_esc(item.get('name'))}</td><td>{_esc(item.get('role'))}</td><td>{_esc(item.get('likely_buying_committee_role'))}</td><td>{_esc(item.get('allowed_use'))}</td><td>{_esc(item.get('confidence'))}/100</td></tr>"

    def solution_card(item):
        disq = ", ".join(item.get("disqualifiers") or [])
        return f"""
        <article class="card">
          <div class="card-top"><strong>{_esc(item.get('service'))}</strong>{_gate_badge(item.get('gate'))}</div>
          <p><b>Pressure:</b> {_esc(item.get('interpreted_pressure'))}</p>
          <p><b>Safe wording:</b> {_esc(item.get('safe_wording'))}</p>
          <small>Proof: {_esc(item.get('proof_point'))} · Disqualifiers: {_esc(disq)}</small>
        </article>"""

    def play_card(item):
        return f"""
        <article class="card">
          <div class="card-top"><strong>{_esc(item.get('service_or_offer'))}</strong><span class="risk">Risk: {_esc(item.get('overclaim_risk'))}</span></div>
          <p>{_esc(item.get('why_relevant'))}</p>
          <p><b>Opening angle:</b> {_esc(item.get('opening_angle'))}</p>
          <small>Target: {_esc(item.get('target_stakeholder'))} · Confidence {_esc(item.get('confidence'))}/100</small>
        </article>"""

    def angle_card(item):
        return f"""
        <article class="card compact">
          <div class="card-top"><strong>{_esc(item.get('angle_type'))}</strong>{_gate_badge(item.get('gate'))}</div>
          <p>{_esc(item.get('text'))}</p>
        </article>"""

    return f"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1" />
  <title>{_esc(company)} — Buying Potential & Insight</title>
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
    .gate-allowed_as_fact {{ color:var(--green); border-color:rgba(110,231,183,.5); }} .gate-allowed_as_hypothesis {{ color:var(--cyan); border-color:rgba(103,232,249,.45); }}
    .gate-allowed_as_discovery_question {{ color:var(--amber); border-color:rgba(250,204,21,.45); }} .gate-internal_only {{ color:var(--violet); border-color:rgba(167,139,250,.45); }} .gate-blocked {{ color:var(--rose); border-color:rgba(251,113,133,.45); }}
    section {{ border:1px solid var(--line); background:rgba(16,27,46,.74); border-radius:14px; padding:24px; margin:20px 0; }}
    .grid {{ display:grid; grid-template-columns:repeat(2,minmax(0,1fr)); gap:14px; }} .grid-3 {{ grid-template-columns:repeat(3,minmax(0,1fr)); }}
    .card {{ background:rgba(7,17,31,.72); border:1px solid rgba(142,160,187,.18); border-radius:12px; padding:16px; }} .compact {{ padding:13px; }}
    .card-top {{ display:flex; justify-content:space-between; align-items:flex-start; gap:12px; margin-bottom:8px; }}
    small,.muted {{ color:var(--muted); }} table {{ width:100%; border-collapse:collapse; overflow:hidden; border-radius:10px; }} th,td {{ padding:11px; border-bottom:1px solid rgba(142,160,187,.16); text-align:left; vertical-align:top; }} th {{ color:var(--muted); font:700 11px ui-monospace,monospace; text-transform:uppercase; letter-spacing:.12em; }}
    ul {{ margin:8px 0 0 18px; padding:0; }} li {{ margin:5px 0; }} .warning {{ border-color:rgba(250,204,21,.4); background:rgba(250,204,21,.08); }} .danger {{ border-color:rgba(251,113,133,.4); background:rgba(251,113,133,.08); }}
    @media (max-width:820px) {{ main {{ padding:24px 14px 48px; }} .grid,.grid-3 {{ grid-template-columns:1fr; }} .card-top {{ flex-direction:column; }} }}
  </style>
</head>
<body>
<main>
  <header>
    <div class="eyebrow">Buying Potential & Insight · Admin Artifact</div>
    <h1>{_esc(company)}</h1>
    <p class="lede">{_esc(report.get('safety_statement'))}</p>
    <div class="meta">
      <span class="pill">Generated {_esc(generated)}</span>
      <span class="pill">Confidence {_esc(confidence)}/100</span>
      <span class="pill">Sources {_esc(_dict(report.get('source_counts')).get('generation_eligible', 0))} eligible</span>
      <span class="pill">Financial refs {_esc(_dict(report.get('source_counts')).get('financial_sources', 0))}</span>
    </div>
  </header>

  <section>
    <h2>Account Snapshot</h2>
    <p>{_esc(snapshot.get('text'))}</p>
    {_gate_badge(snapshot.get('gate'))}
  </section>

  <section>
    <h2>Buying Potential Thesis</h2>
    <p>{_esc(thesis.get('text'))}</p>
    {_gate_badge(thesis.get('gate'))}
  </section>

  <section><h2>Pressure Signals</h2><div class="grid">{_render_list(_list(report.get('pressure_signals')), pressure_card)}</div></section>
  <section><h2>Why Now / Why Soon</h2><div class="grid">{_render_list(_list(report.get('why_now')), why_card)}</div></section>

  <section>
    <h2>Stakeholder Map</h2>
    <table><thead><tr><th>Name</th><th>Role</th><th>Likely Buying Committee Role</th><th>Allowed Use</th><th>Confidence</th></tr></thead>
    <tbody>{''.join(stakeholder_row(i) for i in _list(report.get('stakeholder_map')))}</tbody></table>
  </section>

  <section><h2>Pain-to-Solution Map</h2><div class="grid">{_render_list(_list(report.get('pain_to_solution_map')), solution_card)}</div></section>
  <section><h2>Recommended Sales Plays</h2><div class="grid">{_render_list(_list(report.get('recommended_sales_plays')), play_card)}</div></section>
  <section><h2>Suggested Conversation Angles</h2><div class="grid">{_render_list(_list(report.get('conversation_angles')), angle_card)}</div></section>

  <section class="warning">
    <h2>Evidence, Confidence & Do-Not-Use Claims</h2>
    <div class="grid grid-3">
      <div><h3>Strongest Evidence</h3><ul>{''.join(f'<li>{_esc(x)}</li>' for x in _list(evidence.get('strongest_evidence'))[:12])}</ul></div>
      <div><h3>Weak Or Missing</h3><ul>{''.join(f'<li>{_esc(x)}</li>' for x in _list(evidence.get('weak_or_missing_evidence'))[:12])}</ul></div>
      <div><h3>Do Not Use</h3><ul>{''.join(f'<li>{_esc(x)}</li>' for x in _list(evidence.get('do_not_use_claims'))[:12])}</ul></div>
    </div>
  </section>
</main>
</body>
</html>"""
