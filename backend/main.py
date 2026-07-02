
from fastapi import FastAPI, HTTPException, BackgroundTasks, Response, Request, UploadFile, File, Form
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, HTMLResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel
from typing import Optional, List
import os
import re
import uuid
import json
import requests
import io
import time
import threading
import concurrent.futures
from datetime import datetime, date
from urllib.parse import urlparse
from bs4 import BeautifulSoup
from pypdf import PdfReader
import anthropic

from scanner_one import analyze_intent
from scanner_two import (
    x_ray_search, extract_json_from_text, financial_health_search,
    compute_financial_index, migrate_financial_sub_scores,
    prepare_historical_revalidation_candidate, historical_source_can_revalidate,
    collapse_active_evidence,
)
from storage import BlobStorage
from seed_manager import run_batch_seed, scrape_strategic_content
from finland_list import TOP_100_COMPANIES, get_company_metadata
from analyze_scores import run_analysis
from market_intelligence import compute_market_intelligence
from knowledge_base import get_evolving_framework
from reality_check import validate_summary, validate_pitch, ai_fact_check_summary, clean_summary, purge_false_evidence, _call_openai_text, sanitize_inference_wording
from general_summary import generate_general_summary, fact_check_general_summary
from source_quality import (
    manage_evidence_sources,
    assess_source_scope,
    annotate_evidence_links,
    is_generation_eligible_source,
    is_analysis_premise_source,
    source_analysis_support_mode,
    is_scoped_context_source,
    is_foresight_context_eligible_source,
    infer_source_type,
    assess_source_credibility,
    compute_claim_tier,
)
from contradiction_detector import detect_contradictions
from prompts import PROMPT_VERSION, build_shared_guardrails
from database_sanitizer import sanitize_database
from foresight_guardrails import sanitize_foresight_collection, sanitize_report_foresight_fields
from domain_classifier import infer_domain
from evidence_cleaner import CLEAN_EVIDENCE_MODEL_CHAIN, clean_company_evidence
from llm_clients import call_anthropic_text, call_json_model_chain, call_openai_text
from buying_potential import BuyingPotentialError, generate_buying_potential_report, load_service_portfolio_kb, validate_service_portfolio_kb
from company_evidence import CompanyEvidenceError, build_company_evidence_database
from company_source_ledger import CompanySourceLedgerError, build_company_source_ledger
from market_readiness_report import build_market_readiness_report, render_market_readiness_html
from evidence_graph import attach_evidence_graph, build_evidence_manifest, evidence_prompt_packet
from evidence_absence import build_evidence_absence_signals, format_absence_signals_for_prompt
from evidence_contracts import build_classification_namespaces

# VERIFICATION LOG: v5.4.0 - Extended Thinking + Search Budget Upgrade
print("Starting Adaptability Scanner Backend (v5.4.0 - Extended Thinking + Search Budget Upgrade)...", flush=True)

ARCHETYPE_LABELS = ("Thriving", "Modernizing", "Cash Cow", "At Risk")


def strip_archetype_labels_from_text(value):
    """Keep archetype labels out of written diagnosis/summary prose."""
    if isinstance(value, list):
        return [strip_archetype_labels_from_text(item) for item in value]
    if isinstance(value, dict):
        return {key: strip_archetype_labels_from_text(item) for key, item in value.items()}
    text = str(value or "")
    replacements = {
        "Thriving": "financially and operationally strong profile",
        "Modernizing": "modernizing but financially constrained profile",
        "Cash Cow": "financially capable but operationally under-proven profile",
        "At Risk": "financially and operationally pressured profile",
    }
    for label, replacement in replacements.items():
        text = re.sub(rf"\b{re.escape(label)}\b", replacement, text)
        text = re.sub(rf"\b{re.escape(label.upper())}\b", replacement, text)
    return text


def _strip_tac_codes(text: str) -> str:
    """Remove legacy TAC-xxx / DIM-xxx internal codes from summary text.
    Handles three forms that may appear in stored data:
      1. "(TAC-007)"  — parenthesized code from original model output
      2. "TAC-007"   — bare code without parentheses
      3. "()"        — orphaned empty parens left by a previous strip pass
    """
    if not text:
        return text
    # Pass 1: remove parenthesized codes "(TAC-007)" / "(DIM-003)"
    text = re.sub(r'\s*\(\s*(?:TAC|DIM)-\d{3,4}\s*\)', '', text)
    # Pass 2: remove bare codes not wrapped in parentheses
    text = re.sub(r'\b(?:TAC|DIM)-\d{3,4}\b', '', text)
    # Pass 3: remove orphaned empty parens left by earlier strip passes
    text = re.sub(r'\s*\(\s*\)', '', text)
    # Collapse any double-spaces introduced by removal
    text = re.sub(r'  +', ' ', text)
    return text.strip()

def _apply_deterministic_corrections(text: str, detailed_claims: list) -> str:
    """Apply literal string replacements for wrong_value → correct_value pairs.

    LLMs are unreliable at precise numeric substitution (e.g. replacing "61.8%"
    with "60.8%"). This function does a deterministic pass after the LLM
    correction to guarantee exact values are applied.

    Only replaces when both wrong_value and correct_value are non-empty strings
    and the wrong_value actually appears in the text.
    """
    if not text or not detailed_claims:
        return text
    replacements_made = []
    for dc in detailed_claims:
        wrong = dc.get("wrong_value", "")
        correct = dc.get("correct_value", "")
        if wrong and correct and wrong != correct and wrong in text:
            text = text.replace(wrong, correct)
            replacements_made.append(f"{wrong} → {correct}")
    if replacements_made:
        print(f"   🔧 [Deterministic Fix] Applied {len(replacements_made)} numeric replacement(s): {', '.join(replacements_made)}")
    return text


def _activation_list(value) -> list[str]:
    """Normalize activation WHY/WHAT aliases into a compact string list."""
    if value is None:
        return []
    if isinstance(value, str):
        cleaned = value.strip()
        return [cleaned] if cleaned else []
    if isinstance(value, dict):
        for key in ("text", "proposal", "description", "summary", "claim"):
            if value.get(key):
                return _activation_list(value.get(key))
        bits = [str(v).strip() for v in value.values() if isinstance(v, str) and v.strip()]
        return [" — ".join(bits)] if bits else []
    if isinstance(value, list):
        items: list[str] = []
        for item in value:
            items.extend(_activation_list(item))
        return [item for item in items if item][:3]
    return []


_ACTIVATION_THEME_TYPES = {
    "current_status",
    "foresight_driven",
    "proactive_opportunity",
}


def _normalize_sales_use(value: str) -> str:
    token = re.sub(r"[^a-z0-9]+", "_", str(value or "").strip().lower()).strip("_")
    if token in {"current", "current_state", "current_status", "status"}:
        return "current_status"
    if token in {"foresight", "future", "future_pressure", "foresight_driven"}:
        return "foresight_driven"
    if token in {"proactive", "opportunity", "proactive_opportunity", "discovery"}:
        return "proactive_opportunity"
    return "current_status"


def _clip_sentence(text: str, limit: int = 520) -> str:
    """Trim activation prose without cutting words into ragged fragments."""
    cleaned = re.sub(r"\s+", " ", str(text or "")).strip()
    if len(cleaned) <= limit:
        return cleaned
    return cleaned[:limit].rsplit(" ", 1)[0].rstrip(".,;:") + "."


def _activation_pair_candidates(payload: dict) -> list[dict]:
    """Normalize typed activation pair aliases from current and future prompts."""
    candidates = _first_activation_value(payload, (
        "activation_pairs", "why_what_pairs", "pairs", "themes",
    ), [])
    pairs: list[dict] = []
    if isinstance(candidates, list):
        for item in candidates:
            if not isinstance(item, dict):
                continue
            why = _activation_list(_first_activation_value(item, ("why", "why_proposal", "reason"), ""))
            what_to_do = _activation_list(_first_activation_value(item, (
                "what_to_do", "what", "what_proposal", "what_action", "activity", "deliverable",
            ), ""))
            progress = _activation_list(_first_activation_value(item, (
                "what_progress_shows", "progress_proof", "progress_signal", "observable_progress",
                "success_evidence", "outcome",
            ), ""))
            what = what_to_do
            if not why or not what:
                continue
            theme_type = _normalize_sales_use(item.get("theme_type") or item.get("sales_use") or item.get("type"))
            if theme_type not in _ACTIVATION_THEME_TYPES:
                theme_type = "current_status"
            label = str(item.get("theme") or item.get("label") or item.get("title") or theme_type.replace("_", " ")).strip()
            pairs.append({
                "theme_type": theme_type,
                "sales_use": _normalize_sales_use(item.get("sales_use") or theme_type),
                "theme": label,
                "why": _clip_sentence(why[0]),
                "what": _clip_sentence(what[0]),
                "what_to_do": _clip_sentence(what[0]),
                "what_progress_shows": _clip_sentence(progress[0]) if progress else "",
            })
    return pairs


def _ensure_activation_pairs(payload: dict, prior: dict | None = None) -> list[dict]:
    """Return 2-3 paired WHY/WHAT records while preserving legacy arrays.

    The model prompt requires typed pairs, but older responses and stored data
    expose separate why_proposals / what_proposals arrays. This function keeps
    both contracts coherent and prevents a thin-evidence scan from collapsing to
    a single generic sales trigger.
    """
    prior = prior or {}
    pairs = _activation_pair_candidates(payload)
    if not pairs:
        whys = _activation_list(_first_activation_value(payload, (
            "why_proposals", "why", "why_items", "why_points", "why_questions",
        ), prior.get("why_proposals", [])))
        whats = _activation_list(_first_activation_value(payload, (
            "what_proposals", "what", "what_items", "what_points", "what_actions",
        ), prior.get("what_proposals", [])))
        progress_items = _activation_list(_first_activation_value(payload, (
            "what_progress_proposals", "what_progress", "progress_proposals", "progress_items",
        ), prior.get("what_progress_proposals", [])))
        count = min(max(len(whys), len(whats)), 3)
        if count:
            theme_cycle = ["current_status", "foresight_driven", "proactive_opportunity"]
            for idx in range(count):
                why = whys[idx] if idx < len(whys) else whys[-1]
                what = whats[idx] if idx < len(whats) else whats[-1]
                progress = progress_items[idx] if idx < len(progress_items) else ""
                pairs.append({
                    "theme_type": theme_cycle[idx],
                    "sales_use": theme_cycle[idx],
                    "theme": theme_cycle[idx].replace("_", " "),
                    "why": _clip_sentence(why),
                    "what": _clip_sentence(what),
                    "what_to_do": _clip_sentence(what),
                    "what_progress_shows": _clip_sentence(progress),
                })

    # If the model returns only one pair, add a second bounded discovery pair
    # rather than silently weakening the sales-trigger surface.
    if len(pairs) == 1:
        synthesis = str(_first_activation_value(payload, (
            "strategic_synthesis", "synthesis", "strategic_activation",
            "strategic_summary", "activation_summary",
        ), prior.get("strategic_synthesis", "")) or "")
        basis = "the strongest current diagnostic and foresight pressure"
        if synthesis:
            basis = _clip_sentence(synthesis, 180).rstrip(".")
        pairs.append({
            "theme_type": "proactive_opportunity",
            "sales_use": "proactive_opportunity",
            "theme": "validated next step",
            "why": (
                f"A second sales trigger should validate whether {basis} is material "
                "enough to create an executive priority, rather than assuming the gap is already proven."
            ),
            "what": (
                "Run a bounded discovery conversation to identify the business owner, the pressure, and the first "
                "operating or commercial outcome worth testing."
            ),
            "what_to_do": (
                "Run a bounded discovery conversation to identify the business owner, the pressure, and the first "
                "operating or commercial outcome worth testing."
            ),
            "what_progress_shows": (
                "Progress is visible when discovery identifies a named owner, a concrete business pressure, "
                "and one measurable operating or commercial outcome to test first."
            ),
        })

    return pairs[:3]


def _first_activation_value(payload: dict, keys: tuple[str, ...], default=None):
    for key in keys:
        value = payload.get(key)
        if value not in (None, "", [], {}):
            return value
    return default


def _parse_activation_json(text: str) -> dict | None:
    """Parse activation output even when the model wraps JSON in prose/fences."""
    if not text:
        return None
    candidates: list[str] = []
    extracted = extract_json_from_text(text)
    if extracted:
        candidates.append(extracted)
    stripped = re.sub(r"```(?:json)?\s*|\s*```", "", text.strip(), flags=re.IGNORECASE)
    if stripped and stripped not in candidates:
        candidates.append(stripped)

    # Brace-balanced fallback: extract the first complete JSON object.
    start = stripped.find("{")
    if start >= 0:
        depth = 0
        in_string = False
        escape = False
        for idx, ch in enumerate(stripped[start:], start=start):
            if escape:
                escape = False
                continue
            if ch == "\\":
                escape = True
                continue
            if ch == '"':
                in_string = not in_string
                continue
            if in_string:
                continue
            if ch == "{":
                depth += 1
            elif ch == "}":
                depth -= 1
                if depth == 0:
                    candidates.append(stripped[start:idx + 1])
                    break

    for candidate in candidates:
        try:
            parsed = json.loads(candidate, strict=False)
            if isinstance(parsed, dict):
                return parsed
        except Exception:
            continue
    return None


def _normalize_activation_payload(payload: dict, prior: dict | None = None) -> dict:
    """Accept current and legacy activation aliases without changing meaning."""
    prior = prior or {}
    synthesis = _first_activation_value(payload, (
        "strategic_synthesis", "synthesis", "strategic_activation",
        "strategic_summary", "activation_summary",
    ), prior.get("strategic_synthesis", ""))
    pitch = _first_activation_value(payload, (
        "commercial_pitch", "pitch", "pitch_text", "activation_pitch",
    ), prior.get("pitch", ""))
    activation_pairs = _ensure_activation_pairs(payload, prior)
    why = [pair["why"] for pair in activation_pairs]
    what = [pair.get("what_to_do") or pair["what"] for pair in activation_pairs]
    progress = [pair.get("what_progress_shows") for pair in activation_pairs if pair.get("what_progress_shows")]
    return {
        "strategic_synthesis": sanitize_inference_wording(str(synthesis or "").strip()),
        "pitch": sanitize_inference_wording(str(pitch or "").strip()),
        "activation_pairs": sanitize_inference_wording(activation_pairs),
        "why_proposals": sanitize_inference_wording(_activation_list(why)),
        "what_proposals": sanitize_inference_wording(_activation_list(what)),
        "what_progress_proposals": sanitize_inference_wording(_activation_list(progress)),
    }


def _plain_text_activation_fallback(text: str, prior: dict | None = None) -> dict:
    """Last-resort non-empty activation when a model returned useful prose, not JSON."""
    prior = prior or {}
    cleaned = re.sub(r"```(?:json)?|```", "", text or "", flags=re.IGNORECASE).strip()
    cleaned = re.sub(r"\n{3,}", "\n\n", cleaned)
    if not cleaned:
        return _normalize_activation_payload({}, prior)
    paragraphs = [p.strip(" -•\t") for p in re.split(r"\n\s*\n|\n[-•]\s*", cleaned) if p.strip()]
    first = paragraphs[0] if paragraphs else cleaned
    second = paragraphs[1] if len(paragraphs) > 1 else first
    third = paragraphs[2] if len(paragraphs) > 2 else second
    return _normalize_activation_payload({
        "strategic_synthesis": prior.get("strategic_synthesis") or first[:1800],
        "commercial_pitch": prior.get("pitch") if prior.get("pitch") not in {"", "Pitch Unavailable"} else third[:900],
        "why_proposals": prior.get("why_proposals") or [first[:500]],
        "what_proposals": prior.get("what_proposals") or [second[:500]],
        "what_progress_proposals": prior.get("what_progress_proposals") or [],
    }, prior)


def _build_contradictions_block(verified_links: list, company_name: str) -> str:
    """Run contradiction detection on the verified-evidence pool feeding the
    AI Diagnosis synthesis prompt and return a prompt-ready block.

    Returns an empty string when no quantitative disagreements are found OR
    when the detector fails (it is failure-safe internally). On a hit, the
    return value is a plain-text section starting with
    "### CONTRADICTIONS DETECTED" that the synthesis prompt's SCOPE &
    PROPORTIONALITY rules then force the model to address both sides of.
    """
    if not verified_links:
        return ""
    # The detector wants {title, snippet, scope}. Enrich each verified link
    # with the scope flag derived from URL/title/snippet — same heuristic
    # used elsewhere so the narrow/broad classification stays consistent.
    enriched = []
    for l in verified_links[:25]:
        title = (l.get("title") or "")[:80]
        snippet = (l.get("source_quote") or l.get("snippet") or "")[:350]
        enriched.append({
            "title": title,
            "snippet": snippet,
            "scope": assess_source_scope(l.get("url", ""), title, snippet),
        })
    try:
        contradictions = detect_contradictions(enriched, company_name or "the company")
    except Exception as e:
        # The detector should be failure-safe but defend in depth — synthesis
        # must never fail because of an optional pre-pass.
        print(f"   ⚠️ [Contradictions] Pre-synthesis pass failed: {e}")
        return ""
    if not contradictions:
        return ""

    lines = ["### CONTRADICTIONS DETECTED",
             "Quantitative disagreements between evidence items on the same topic.",
             "You MUST address EACH item below in the prose: cite both sides with their respective scope (narrow/broad) and source titles. Silently picking one side is a rule violation."]
    for idx, c in enumerate(contradictions, 1):
        lines.append(
            f"{idx}. Topic: {c.get('topic', '')}\n"
            f"   - [{c.get('scope_a', 'broad')}] {c.get('source_a_title', '')}: {c.get('claim_a', '')}\n"
            f"   - [{c.get('scope_b', 'broad')}] {c.get('source_b_title', '')}: {c.get('claim_b', '')}"
        )
    return "\n".join(lines) + "\n"


def _analysis_evidence_links(links: list) -> list:
    """Sources allowed to feed generated analysis, not merely live URLs."""
    selected = []
    for link in links or []:
        if not is_analysis_premise_source(link):
            continue
        enriched = dict(link)
        mode = source_analysis_support_mode(enriched)
        enriched["analysis_support_mode"] = mode
        enriched["analysis_premise"] = True
        enriched["can_support_hard_fact"] = mode == "hard_fact"
        if mode == "scoped_context":
            enriched.setdefault("bounded_use_note", "Use for bounded diagnosis/synthesis context only; not proof of a precise hard fact.")
        selected.append(enriched)
    return selected


def _company_evidence_db_scanner_candidates(company_evidence: dict | None, limit: int = 20) -> tuple[list[dict], dict]:
    """
    Convert Company Evidence DB records into neutral Scanner source candidates.

    These are source-acquisition hints only. Scanner must re-check URL/content/entity/
    claim support before any candidate can influence diagnosis or summaries.
    """
    stats = {
        "candidates": 0,
        "evaluated": 0,
        "eligible_before_limit": 0,
        "cached_extracts": 0,
        "governed_admitted": 0,
        "skipped": 0,
        "skip_reasons": {},
        "jury_counts": {},
        "limit": limit,
    }
    if not isinstance(company_evidence, dict):
        return [], stats

    def skip(reason: str):
        stats["skipped"] += 1
        stats["skip_reasons"][reason] = stats["skip_reasons"].get(reason, 0) + 1

    jury_terms = {
        "ARCHITECTURAL JURY (Engineering Reality)": (
            "architecture", "engineering", "developer", "api", "integration",
            "application", "platform", "cloud", "systems", "technical",
        ),
        "AI & DATA JURY (Future Readiness)": (
            "ai", "artificial intelligence", "data", "analytics", "automation",
            "cyber", "security", "technology", "digital", "cloud",
        ),
        "SERVICE PORTFOLIO JURY (Delivery Reality)": (
            "service", "services", "solution", "solutions", "delivery",
            "customer", "case", "reference", "support", "managed",
        ),
        "STRATEGIC JURY (Business Model, Procurement & Customer Experience)": (
            "strategy", "about", "news", "press", "investor", "acquisition",
            "partnership", "procurement", "tender", "customer experience",
            "sustainability", "financial", "annual",
        ),
        "CULTURAL JURY (Talent & Psychological Safety)": (
            "career", "careers", "jobs", "hiring", "culture", "values",
            "people", "employee", "workplace", "talent", "hr",
        ),
    }

    def jury_hint(record: dict, candidate: dict, cached_excerpt: str) -> str:
        token = " ".join(str(value or "") for value in (
            record.get("title"), record.get("source_title"), record.get("source_type"),
            record.get("source_category"), record.get("provider_record_type"),
            record.get("reference_context"), candidate.get("url"), cached_excerpt[:500],
        )).lower()
        best = ("", 0)
        for jury_name, terms in jury_terms.items():
            score = sum(1 for term in terms if term in token)
            if score > best[1]:
                best = (jury_name, score)
        return best[0]

    def seed_score(record: dict, candidate: dict, cached_excerpt: str, hinted_jury: str) -> int:
        token = " ".join(str(value or "") for value in (
            candidate.get("url"), record.get("source_type"), record.get("source_category"),
            record.get("origin_relation"), record.get("publisher_independence"),
            record.get("entity_match_status"), record.get("verification_reason"),
        )).lower()
        score = 0
        if candidate.get("url"):
            score += 10
        if cached_excerpt:
            score += 30
            if len(cached_excerpt) > 350:
                score += 8
        if hinted_jury:
            score += 12
        if "official" in token or record.get("official_domain_source") is True:
            score += 18
        if "official_page_outbound_reference" in token:
            score += 12
        if any(word in token for word in ("exact", "verified", "entity_verified", "canonical")):
            score += 12
        if any(word in token for word in ("service", "technology", "about", "newsroom", "press", "financial", "careers")):
            score += 8
        if "independent_external_evidence" in token:
            score += 10
        if any(word in token for word in ("display_only", "unsupported", "unknown")):
            score -= 3
        return score

    def select_diverse(candidates_in: list[dict]) -> list[dict]:
        ranked = sorted(candidates_in, key=lambda item: item.get("scanner_seed_score", 0), reverse=True)
        selected = []
        selected_keys = set()
        per_jury = {}

        def add(item: dict) -> bool:
            key = item.get("_dedupe_key")
            if not key or key in selected_keys or len(selected) >= limit:
                return False
            jury = item.get("scanner_jury_hint") or "unknown"
            if per_jury.get(jury, 0) >= 6:
                return False
            selected.append(item)
            selected_keys.add(key)
            per_jury[jury] = per_jury.get(jury, 0) + 1
            return True

        # First guarantee breadth where the Evidence DB has candidates.
        for jury_name in jury_terms:
            for item in ranked:
                if item.get("scanner_jury_hint") == jury_name and add(item):
                    break
        # Then fill by score, while preventing one jury from crowding out all others.
        for item in ranked:
            add(item)
            if len(selected) >= limit:
                break

        return selected

    eligible_candidates = []
    seen = set()
    for record in company_evidence.get("records") or []:
        stats["evaluated"] += 1
        if not isinstance(record, dict):
            skip("invalid_record")
            continue

        provider_record_type = str(record.get("provider_record_type") or "").lower()
        source_category = str(record.get("source_category") or "").lower()
        source_type = str(record.get("source_type") or "").lower()
        sales_use = str(record.get("sales_use") or "").lower()
        entity_status = str(record.get("entity_match_status") or "").lower()
        origin_relation = str(record.get("origin_relation") or "").lower()
        publisher_independence = str(record.get("publisher_independence") or "").lower()

        if record.get("blocked") is True:
            skip("blocked")
            continue
        if record.get("wrong_entity") is True or entity_status == "wrong":
            skip("wrong_entity")
            continue
        if record.get("page_not_found") is True or record.get("soft_404") is True:
            skip("soft_404")
            continue
        if record.get("is_stale") is True:
            skip("stale")
            continue
        if provider_record_type == "stakeholder_candidate" or source_category == "stakeholder_candidate":
            skip("stakeholder_only")
            continue
        if record.get("internal_only") is True or "retired" in str(record.get("stakeholder_role_match_reason") or "").lower():
            skip("retired_person")
            continue
        if sales_use in {"internal_only", "blocked"} or source_type.startswith("coresignal_stakeholder"):
            skip("sales_only")
            continue
        if publisher_independence in {"unsafe", "unsafe_or_irrelevant"}:
            skip("unsafe_external_reference")
            continue
        if record.get("context_only") is True and origin_relation != "official_page_outbound_reference":
            skip("context_only")
            continue

        url = str(record.get("url") or record.get("source_url") or "").strip()
        cached_excerpt = (
            record.get("source_quote")
            or record.get("snippet")
            or record.get("text_excerpt")
            or record.get("markdown_excerpt")
            or record.get("extracted_text")
            or ""
        )
        cached_excerpt = re.sub(r"\s+", " ", str(cached_excerpt)).strip()[:900]
        if not url and not cached_excerpt:
            skip("no_url_or_cached_text")
            continue

        dedupe_key = (url.rstrip("/").lower() if url else str(record.get("content_hash") or cached_excerpt[:120]).lower())
        if dedupe_key in seen:
            skip("duplicate")
            continue
        seen.add(dedupe_key)

        # Deliberately do not copy generation_eligible, promotion_status,
        # allowed_as_fact, or analysis_verified from the Company Evidence DB.
        governed_admission = bool(
            cached_excerpt
            and record.get("blocked") is not True
            and record.get("wrong_entity") is not True
            and record.get("page_not_found") is not True
            and record.get("soft_404") is not True
            and record.get("is_stale") is not True
            and provider_record_type != "stakeholder_candidate"
            and source_category != "stakeholder_candidate"
            and sales_use not in {"internal_only", "blocked"}
        )
        candidate = {
            "title": record.get("title") or record.get("source_title") or url or "Company Evidence DB source",
            "url": url,
            "snippet": cached_excerpt,
            "source_quote": cached_excerpt,
            "cached_excerpt": cached_excerpt,
            "source_type": record.get("source_type") or "company_evidence_candidate",
            "source_category": record.get("source_category"),
            "source_database": "company_evidence_db",
            "provider": record.get("provider"),
            "provider_record_type": record.get("provider_record_type"),
            "origin_relation": record.get("origin_relation"),
            "originating_company_page": record.get("originating_company_page"),
            "external_host": record.get("external_host"),
            "publisher_type": record.get("publisher_type"),
            "publisher_independence": record.get("publisher_independence"),
            "reference_context": record.get("reference_context"),
            "company_claim_vs_external_evidence_boundary": record.get("company_claim_vs_external_evidence_boundary"),
            "fetched_at": record.get("fetched_at") or record.get("last_checked") or record.get("generated_at"),
            "content_hash": record.get("content_hash"),
            "original_entity_match_status": record.get("entity_match_status"),
            "original_verification_reason": record.get("verification_reason"),
            "evidence_admission_status": "governed_candidate_admitted" if governed_admission else "candidate_needs_revalidation",
            "evidence_admission_reason": (
                "active_current_scoped_cached_company_evidence"
                if governed_admission else "scanner_must_revalidate_before_use"
            ),
        }
        hint = jury_hint(record, candidate, cached_excerpt)
        candidate["scanner_jury_hint"] = hint
        candidate["scanner_seed_score"] = seed_score(record, candidate, cached_excerpt, hint)
        candidate["_dedupe_key"] = dedupe_key
        eligible_candidates.append(candidate)
        stats["eligible_before_limit"] += 1
        if cached_excerpt:
            stats["cached_extracts"] += 1
        if governed_admission:
            stats["governed_admitted"] += 1

    candidates = select_diverse(eligible_candidates)
    stats["candidates"] = len(candidates)
    stats["selected"] = len(candidates)
    stats["limit_reached"] = len(eligible_candidates) > len(candidates)
    stats["cached_extracts"] = sum(1 for candidate in candidates if candidate.get("cached_excerpt"))
    stats["governed_admitted"] = sum(
        1 for candidate in candidates
        if candidate.get("evidence_admission_status") == "governed_candidate_admitted"
    )
    for candidate in candidates:
        jury = candidate.get("scanner_jury_hint") or "unknown"
        stats["jury_counts"][jury] = stats["jury_counts"].get(jury, 0) + 1
        candidate.pop("_dedupe_key", None)

    return candidates, stats


def _financial_findings_to_evidence_links(financial_findings: list, company_name: str, company_domain: str = "") -> list:
    """Convert source-bearing financial findings into evidence-ledger candidates."""
    candidates = []
    seen = set()
    for finding in financial_findings or []:
        if not isinstance(finding, dict):
            continue
        url = (finding.get("source_url") or "").strip()
        snippet = (finding.get("evidence_snippet") or "").strip()
        if not (url.startswith("http") and snippet and len(snippet) > 20):
            continue
        key = url.rstrip("/").lower()
        if key in seen:
            continue
        seen.add(key)
        title = (finding.get("source_title") or finding.get("dimension") or "Financial source").strip()
        source_type = infer_source_type(url, title, snippet)
        credibility = assess_source_credibility(url, source_type, company_domain)
        link = {
            "title": title,
            "url": url,
            "snippet": snippet,
            "source_quote": snippet[:420],
            "verified": True,
            "url_verified": True,
            "is_recent": True,
            "source_type": source_type,
            "source_credibility": credibility,
            "origin": "financial_findings",
            "claim_type": "financial_fact",
            "financial_dimension": finding.get("dimension"),
            "financial_assessment": finding.get("assessment"),
            "entity_scope": finding.get("entity_scope") or "unknown",
            "canonical_company": company_name,
        }
        link["claim_tier"] = compute_claim_tier(link)
        candidates.append(link)
    return candidates


def _evidence_sufficiency_state(evidence_links: list, financial_links: list) -> dict:
    """Summarize whether the source register is strong enough for normal prose."""
    financial_urls = {
        (link.get("url") or link.get("source_url") or "").rstrip("/").lower()
        for link in financial_links or []
        if isinstance(link, dict)
    }
    promoted_total = sum(1 for link in evidence_links or [] if is_generation_eligible_source(link))
    promoted_financial = sum(1 for link in financial_links or [] if is_generation_eligible_source(link))
    promoted_nonfinancial = sum(
        1
        for link in evidence_links or []
        if is_generation_eligible_source(link)
        and (link.get("url") or link.get("source_url") or "").rstrip("/").lower() not in financial_urls
    )
    scoped_context_total = sum(
        1
        for link in evidence_links or []
        if source_analysis_support_mode(link) == "scoped_context"
    )
    scoped_context_nonfinancial = sum(
        1
        for link in evidence_links or []
        if source_analysis_support_mode(link) == "scoped_context"
        and (link.get("url") or link.get("source_url") or "").rstrip("/").lower() not in financial_urls
    )
    label = ""
    status = "complete"
    if promoted_total == 0 and promoted_financial == 0:
        status = "insufficient"
        label = "Insufficient promoted evidence"
    elif promoted_total < 3:
        status = "low_promoted_evidence"
        label = "Low promoted evidence"
    elif promoted_total < 5:
        status = "source_register_incomplete"
        label = "Source-register incomplete"
    return {
        "status": status,
        "label": label,
        "promoted_company_sources": promoted_total,
        "promoted_nonfinancial_sources": promoted_nonfinancial,
        "promoted_financial_sources": promoted_financial,
        "promoted_total_sources": promoted_total,
        "scoped_context_sources": scoped_context_total,
        "scoped_context_nonfinancial_sources": scoped_context_nonfinancial,
    }


_AI_READINESS_TERMS = (
    "ai readiness",
    "artificial intelligence",
    "machine learning",
    "ml",
    "generative ai",
    "genai",
    "data platform",
    "data governance",
    "developer portal",
    "api",
    "cloud",
    "automation",
    "autonomous",
)


def _intent_dimension_score(intent_breakdown: list, dimension_name: str) -> Optional[int]:
    """Return the Scanner 1 ambition/intent score for a named KB dimension."""
    target = (dimension_name or "").strip().lower()
    for item in intent_breakdown or []:
        if not isinstance(item, dict):
            continue
        name = str(item.get("name") or item.get("dimension") or item.get("category") or "").strip().lower()
        if name == target:
            try:
                return int(round(float(item.get("score"))))
            except Exception:
                return None
    return None


def _is_ai_readiness_evidence_item(item: dict) -> bool:
    if not isinstance(item, dict):
        return False
    haystack = " ".join(
        str(item.get(k) or "")
        for k in (
            "name", "dimension", "category", "kb_dimension", "query", "title",
            "snippet", "source_quote", "description", "claim_type",
        )
    ).lower()
    return any(term in haystack for term in _AI_READINESS_TERMS)


def _ai_readiness_evidence_state(intent_breakdown: list, evidence_links: list, checklist: list) -> dict:
    """Detect high AI ambition without enough promoted AI capability evidence.

    This does not override scores. It gives prompts and reports a provenance note
    so ambition/readiness numbers are not mistaken for externally verified AI
    capability when the source register is thin.
    """
    ai_intent_score = _intent_dimension_score(intent_breakdown, "AI Readiness")
    promoted_ai_sources = [
        link for link in evidence_links or []
        if is_generation_eligible_source(link) and _is_ai_readiness_evidence_item(link)
    ]
    promoted_ai_markers = [
        item for item in checklist or []
        if is_generation_eligible_source(item) and _is_ai_readiness_evidence_item(item)
    ]
    promoted_count = len(promoted_ai_sources) + len(promoted_ai_markers)
    warning = ""
    status = "not_applicable"
    if ai_intent_score is not None:
        status = "supported" if promoted_count >= 2 else "lightly_supported" if promoted_count == 1 else "thin"
        if ai_intent_score >= 80 and promoted_count == 0:
            warning = (
                f"AI Readiness ambition is high ({ai_intent_score}/100), but no generation-eligible "
                "AI capability evidence was found. Treat this as narrative/intent unless source-backed "
                "evidence proves deployed AI, data-platform, API, automation, or ML capability."
            )
        elif ai_intent_score >= 80 and promoted_count == 1:
            warning = (
                f"AI Readiness ambition is high ({ai_intent_score}/100), but promoted AI capability "
                "evidence is light. Use cautious wording and distinguish autonomy/R&D signals from "
                "deployed enterprise AI capability."
            )
    return {
        "dimension": "AI Readiness",
        "intent_score": ai_intent_score,
        "promoted_evidence_count": promoted_count,
        "status": status,
        "warning": warning,
    }


def _ai_readiness_prompt_note(state: dict) -> str:
    warning = (state or {}).get("warning")
    if not warning:
        return ""
    return (
        "AI READINESS EVIDENCE NOTE:\n"
        f"{warning}\n"
        "Do not describe AI Readiness as confirmed capability unless the evidence packet contains "
        "generation-eligible proof. Autonomy-adjacent partnerships or R&D signals may be discussed "
        "only as scoped signals, not as proof of deployed enterprise AI."
    )


def _build_fact_check_scan_payload(data: dict) -> dict:
    """Current-run payload for verification; never exposes cross-scan trends."""
    data = data or {}
    evidence_links = data.get("evidence_links") or []
    checklist = data.get("reality_checklist") or []
    dimensions = {}
    for item in checklist[:80]:
        if not isinstance(item, dict):
            continue
        dim = item.get("category") or item.get("dimension") or "Unknown"
        entry = dimensions.setdefault(dim, {"found": 0, "not_found": 0, "negative": 0, "generation_eligible": 0})
        if item.get("found") is True:
            entry["found"] += 1
        elif item.get("found") is False:
            entry["not_found"] += 1
        if item.get("sentiment") == "negative":
            entry["negative"] += 1
        if is_generation_eligible_source(item):
            entry["generation_eligible"] += 1

    evidence_counts = {
        "total": len(evidence_links),
        "generation_eligible": sum(1 for item in evidence_links if is_generation_eligible_source(item)),
        "scoped_context": sum(1 for item in evidence_links if is_scoped_context_source(item)),
        "analysis_verified": sum(1 for item in evidence_links if item.get("analysis_verified") is True),
        "display_only": sum(1 for item in evidence_links if item.get("promotion_status") == "display_only"),
        "unjudged_due_timeout": sum(
            1
            for item in evidence_links
            if item.get("promotion_status") == "unjudged_due_timeout"
            or item.get("source_use_status") == "unjudged_due_timeout"
        ),
        "excluded": sum(1 for item in evidence_links if item.get("promotion_status") == "excluded"),
        "wrong_entity": sum(1 for item in evidence_links if item.get("wrong_entity") is True or item.get("entity_match_status") == "wrong"),
        "unknown_entity": sum(1 for item in evidence_links if item.get("entity_match_status") in {"unknown", "weak"}),
    }

    sample_findings = []
    for item in checklist[:30]:
        if not isinstance(item, dict):
            continue
        sample_findings.append({
            "category": item.get("category") or item.get("dimension"),
            "finding": item.get("query") or item.get("name") or item.get("title") or item.get("finding"),
            "found": item.get("found"),
            "sentiment": item.get("sentiment"),
            "source_type": item.get("source_type"),
            "promotion_status": item.get("promotion_status"),
            "source_use_status": item.get("source_use_status"),
            "jury_status": item.get("jury_status"),
            "support_level": item.get("support_level"),
            "analysis_verified": item.get("analysis_verified"),
            "generation_eligible": is_generation_eligible_source(item),
            "scoped_context": is_scoped_context_source(item),
        })

    financial_findings = []
    for item in (data.get("financial_findings") or [])[:20]:
        if not isinstance(item, dict):
            continue
        financial_findings.append({
            "dimension": item.get("dimension") or item.get("category"),
            "finding": item.get("finding") or item.get("summary") or item.get("claim"),
            "metric": item.get("metric") or item.get("label"),
            "value": item.get("value") or item.get("amount"),
            "evidence_snippet": item.get("evidence_snippet") or item.get("source_quote") or item.get("snippet"),
            "source_title": item.get("source_title") or item.get("title"),
            "source_url": item.get("source_url") or item.get("url"),
            "analysis_verified": item.get("analysis_verified"),
            "generation_eligible": item.get("generation_eligible"),
        })

    foresight_forces = []
    foresight_data = data.get("strategic_foresight") or {}
    if isinstance(foresight_data, dict):
        raw_forces = (
            foresight_data.get("forces")
            or foresight_data.get("dimension_forces")
            or foresight_data.get("strategic_forces")
            or []
        )
        for force in raw_forces[:10]:
            if not isinstance(force, dict):
                continue
            foresight_forces.append({
                "theme": force.get("theme") or force.get("force") or force.get("title"),
                "description": force.get("description") or force.get("summary") or force.get("implication"),
                "time_horizon": force.get("time_horizon") or force.get("horizon"),
                "company_relevance": force.get("company_relevance") or force.get("relevance") or force.get("why_it_matters"),
                "source_ids": force.get("source_ids") or force.get("evidence_ids") or [],
                "claim_type": "foresight_context",
                "company_specific": False,
            })

    foresight_source_counts = {
        "total": len(data.get("foresight_links") or []),
        "context_eligible": sum(
            1 for item in (data.get("foresight_links") or [])
            if isinstance(item, dict) and is_foresight_context_eligible_source(item)
        ),
    }

    return {
        "companyName": data.get("companyName"),
        "intent_score": data.get("intent_score"),
        "reality_score": data.get("reality_score"),
        "reality_gap": data.get("reality_gap"),
        "confidence_score": data.get("confidence_score"),
        "archetype": data.get("archetype"),
        "washing_flag": data.get("washing_flag"),
        "washing_type": data.get("washing_type"),
        "evidence_counts": evidence_counts,
        "dimension_evidence_counts": dimensions,
        "financial_payload": {
            "financial_index": data.get("financial_index"),
            "financial_confidence": data.get("financial_confidence"),
            "financial_sub_scores": data.get("financial_sub_scores") or {},
            "financial_findings": financial_findings,
        },
        "foresight_context": {
            "forces": foresight_forces,
            "source_counts": foresight_source_counts,
            "verification_rule": (
                "Macro context is verified against Foresight KB sources. Company relevance is a modal "
                "strategic inference and is not proof of company action or deployment."
            ),
        },
        "evidence_absence_signals": data.get("evidence_absence_signals") or [],
        "evidence_absence_contract": (
            "Absence-of-promoted-evidence signals are deterministic Scanner evidence-state findings. "
            "They support cautious proof-gap reasoning, not claims that a capability is absent."
        ),
        "evidence_based_risks": data.get("evidence_based_risks") or [],
        "sample_reality_findings": sample_findings,
        "evidence_graph_version": data.get("evidence_graph_version"),
        "current_claim_ids": [
            item.get("claim_id") for item in (data.get("claim_graph") or [])
            if isinstance(item, dict) and item.get("phase") == "jury_reality"
        ],
        "temporal_contract": "current_scan_only",
        "prohibited_reasoning": [
            "cross-scan score trends",
            "cross-scan intent-reality gap changes",
            "changes in Scanner confidence, diagnosis, archetype or classification",
        ],
    }


def _fallback_foresight_from_links(company_name: str, foresight_links: list, domain: str = "") -> dict | None:
    """Retain only links already satisfying the foresight-context contract."""
    retained = []
    seen = set()
    for link in _safe_dict_list(foresight_links, "fallback_foresight_links"):
        url = (link.get("url") or link.get("source_url") or "").strip()
        if not url or url in seen:
            continue
        if not is_foresight_context_eligible_source(link):
            continue
        seen.add(url)
        retained.append({
            "theme": link.get("theme") or link.get("dimension") or link.get("source_type") or "Retained foresight source",
            "title": link.get("title") or link.get("source_title") or url,
            "source_url": url,
            "source_title": link.get("source_title") or link.get("title"),
            "source_quote": link.get("source_quote") or link.get("snippet") or link.get("description") or "",
            "claim_type": "foresight_context",
            "company_specific": False,
            "foresight_context_eligible": True,
            "foresight_signal_status": "context_eligible",
            "source_audit_status": "accepted",
            "evidence_status": "foresight_context_retained_link",
            "context_only": True,
        })
        if len(retained) >= 8:
            break
    if not retained:
        return None
    return {
        "company_name": company_name,
        "domain": domain,
        "kb_first": False,
        "fallback_from_links": True,
        "insufficient_evidence": True,
        "quality_label": "foresight_context_retained_no_current_synthesis",
        "summary": (
            "Current foresight synthesis did not produce generation-eligible forces. "
            "Previously eligible foresight-context links are retained as macro context."
        ),
        "method_note": (
            "These retained links are not promoted into fresh strategic foresight claims. "
            "They preserve source continuity without becoming company-fact evidence."
        ),
        "dimension_forces": retained,
    }


def _shared_prompt_guardrails(include_diagnostic: bool = True, include_foresight: bool = True, include_claim_tier: bool = False) -> str:
    """Shared evidence/diagnosis/foresight guardrails injected into main.py prompts."""
    return build_shared_guardrails(include_diagnostic=include_diagnostic, include_foresight=include_foresight, include_claim_tier=include_claim_tier)


def _sanitize_company_foresight_fields(data: dict, domain: str = "") -> dict:
    """Sanitize stored/generated foresight fields before saving or re-rendering."""
    return sanitize_report_foresight_fields(data or {}, domain=domain)



def _safe_dict_list(items, label: str = "items") -> list:
    """Return only dict items from a list-like value before downstream .get() access."""
    if not isinstance(items, list):
        return []
    clean = []
    dropped = 0
    for item in items:
        if isinstance(item, dict):
            clean.append(item)
        else:
            dropped += 1
    if dropped:
        print(f"   ⚠️ [Source Hygiene] Dropped {dropped} non-dict item(s) from {label}")
    return clean


def _strip_callables_and_filter_source_lists(data, label: str = "company_data"):
    """Recursively remove callable/function objects and filter source-card lists.

    Prevents Refresh Database / General Summary from failing with:
        'function' object has no attribute 'get'
    when malformed generated or legacy source-card lists contain non-dict objects.
    """
    SOURCE_LIST_FIELDS = {
        "evidence_links",
        "foresight_links",
        "foresight_evidence",
        "deep_dive_findings",
        "source_cards",
        "dimension_forces",
        "future_insights",
        "history",
    }
    if callable(data):
        print(f"   ⚠️ [Data Hygiene] Dropped callable/function at {label}")
        return None
    if isinstance(data, dict):
        cleaned = {}
        for key, value in data.items():
            child = _strip_callables_and_filter_source_lists(value, f"{label}.{key}")
            if child is None and callable(value):
                continue  # dropped callable — skip key
            if key in SOURCE_LIST_FIELDS:
                if isinstance(child, list):
                    before = len(child)
                    child = [x for x in child if isinstance(x, dict)]
                    dropped = before - len(child)
                    if dropped:
                        print(f"   ⚠️ [Data Hygiene] Dropped {dropped} non-dict item(s) from {label}.{key}")
                else:
                    child = []
            cleaned[key] = child  # preserve legitimate None values
        return cleaned
    if isinstance(data, list):
        cleaned = []
        for idx, item in enumerate(data):
            if callable(item):
                print(f"   ⚠️ [Data Hygiene] Dropped callable/function at {label}[{idx}]")
                continue
            child = _strip_callables_and_filter_source_lists(item, f"{label}[{idx}]")
            cleaned.append(child)
        return cleaned
    return data



def _find_callables(obj, path="root", found=None):
    """Return paths to callable/function objects inside a nested payload."""
    if found is None:
        found = []
    if callable(obj):
        found.append(path)
        return found
    if isinstance(obj, dict):
        for key, value in obj.items():
            _find_callables(value, f"{path}.{key}", found)
    elif isinstance(obj, list):
        for idx, value in enumerate(obj):
            _find_callables(value, f"{path}[{idx}]", found)
    return found


_JSON_SAFE_DROPPED = object()  # sentinel: callable was removed, distinct from legitimate None

def _make_json_safe_payload(obj, path="root"):
    """Drop callables and stringify non-serializable objects before save.

    Uses _JSON_SAFE_DROPPED sentinel so dict keys / list items whose legitimate
    value is None are preserved rather than silently deleted.
    """
    if callable(obj):
        print(f"   ⚠️ [JSON Safe] Dropped callable/function at {path}")
        return _JSON_SAFE_DROPPED
    if isinstance(obj, dict):
        out = {}
        for key, value in obj.items():
            safe = _make_json_safe_payload(value, f"{path}.{key}")
            if safe is not _JSON_SAFE_DROPPED:
                out[key] = safe
        return out
    if isinstance(obj, list):
        out = []
        for idx, value in enumerate(obj):
            safe = _make_json_safe_payload(value, f"{path}[{idx}]")
            if safe is not _JSON_SAFE_DROPPED:
                out.append(safe)
        return out
    try:
        json.dumps(obj)
        return obj
    except TypeError:
        print(f"   ⚠️ [JSON Safe] Converted non-serializable object at {path}: {type(obj).__name__}")
        return str(obj)

def clean_env_var(value):
    if not value: return ""
    if "=" in value:
        try: value = value.split("=", 1)[1]
        except: pass
    return value.strip().strip('"').strip("'")


# --- AUTHENTICATION ---
def verify_admin(request: Request):
    """Validate Authorization: Bearer <secret> header against ADMIN_SECRET env var."""
    admin_secret = clean_env_var(os.environ.get("ADMIN_SECRET", ""))
    if not admin_secret:
        raise HTTPException(status_code=503, detail="Admin access not configured on server.")
    auth_header = request.headers.get("authorization", "")
    if not auth_header.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="Authorization required.")
    token = auth_header[7:]
    if token != admin_secret:
        raise HTTPException(status_code=401, detail="Invalid credentials.")


def verify_guest_or_admin(request: Request) -> str:
    """Allow access if Bearer token matches ADMIN_SECRET or GUEST_SECRET. Returns role string."""
    admin_secret = clean_env_var(os.environ.get("ADMIN_SECRET", ""))
    guest_secret = clean_env_var(os.environ.get("GUEST_SECRET", ""))
    auth_header = request.headers.get("authorization", "")
    if not auth_header.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="Authorization required.")
    token = auth_header[7:]
    if admin_secret and token == admin_secret:
        return "admin"
    if guest_secret and token == guest_secret:
        return "guest"
    raise HTTPException(status_code=401, detail="Invalid credentials.")


# --- LOGIN RATE LIMITING ---
_login_attempts: dict = {}  # {ip: {"count": int, "locked_until": float}}

def _check_rate_limit(ip: str):
    """Check if IP is rate-limited. Raises 429 if locked out."""
    now = time.time()
    # Clean up expired entries
    expired = [k for k, v in _login_attempts.items() if v.get("locked_until", 0) < now - 1800]
    for k in expired:
        del _login_attempts[k]

    entry = _login_attempts.get(ip)
    if entry and entry.get("locked_until", 0) > now:
        remaining = int((entry["locked_until"] - now) / 60) + 1
        raise HTTPException(status_code=429, detail=f"Too many failed attempts. Try again in {remaining} minutes.")

def _record_failed_attempt(ip: str):
    """Record a failed login attempt. Lock out after 5 failures."""
    now = time.time()
    entry = _login_attempts.get(ip, {"count": 0})
    entry["count"] = entry.get("count", 0) + 1
    if entry["count"] >= 5:
        entry["locked_until"] = now + 1800  # 30 minutes
    _login_attempts[ip] = entry

def _clear_attempts(ip: str):
    """Clear failed attempts on successful login."""
    _login_attempts.pop(ip, None)

app = FastAPI()

# --- MAINTENANCE MODE ---
# Set MAINTENANCE_MODE=true in Railway env vars to block all user access.
# Remove (or set to empty/false) to restore normal operation.
# No code deployment needed — just toggle the env var and Railway restarts.
_MAINTENANCE_HTML = """<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>Maintenance</title>
  <style>
    body {
      margin: 0; display: flex; align-items: center; justify-content: center;
      min-height: 100vh; background: #1e1b4b; color: #f8fafc;
      font-family: 'Inter', -apple-system, sans-serif;
    }
    h1 { font-size: 2.5rem; font-weight: 700; letter-spacing: 0.15em; opacity: 0.85; }
  </style>
</head>
<body><h1>MAINTENANCE</h1></body>
</html>"""

@app.middleware("http")
async def maintenance_middleware(request: Request, call_next):
    if os.environ.get("MAINTENANCE_MODE", "").lower() in ("true", "1", "yes"):
        return HTMLResponse(_MAINTENANCE_HTML, status_code=503)
    return await call_next(request)

# --- CORS: Restrict to known origins ---
_allowed_origins = os.environ.get(
    "ALLOWED_ORIGINS",
    "https://diagnostic-engine.up.railway.app"
).split(",")
app.add_middleware(
    CORSMiddleware,
    allow_origins=[o.strip() for o in _allowed_origins],
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)

storage = BlobStorage()
buying_storage = BlobStorage(
    token_env="BLOB2_READ_WRITE_TOKEN",
    url_envs=("VITE_BLOB2_URL", "BLOB2_URL"),
    label="BuyingStorage",
)


# --- AUTH LOGIN ENDPOINT ---
class LoginRequest(BaseModel):
    password: str

@app.post("/auth/login")
async def auth_login(body: LoginRequest, request: Request):
    """Validate password and return role (admin/guest). Rate-limited: 5 failures = 30 min lockout."""
    ip = request.client.host if request.client else "unknown"
    _check_rate_limit(ip)

    admin_secret = clean_env_var(os.environ.get("ADMIN_SECRET", ""))
    guest_secret = clean_env_var(os.environ.get("GUEST_SECRET", ""))
    pw = body.password

    if admin_secret and pw == admin_secret:
        _clear_attempts(ip)
        return {"role": "admin"}
    if guest_secret and pw == guest_secret:
        _clear_attempts(ip)
        return {"role": "guest"}

    _record_failed_attempt(ip)
    entry = _login_attempts.get(ip, {})
    remaining = 5 - entry.get("count", 0)
    if remaining <= 0:
        raise HTTPException(status_code=429, detail="Too many failed attempts. Locked out for 30 minutes.")
    raise HTTPException(status_code=401, detail=f"Invalid password. {remaining} attempts remaining.")


# --- SERVER-SIDE BATCH STATE ---
_batch_state = {
    "is_running": False,
    "current": 0,
    "total": 0,
    "current_name": "",
    "started_at": None,
    "completed_at": None,
    "failed": [],
}
_batch_lock = threading.Lock()
_batch_stop_event = threading.Event()

app.mount("/components", StaticFiles(directory="../components"), name="components")
app.mount("/services", StaticFiles(directory="../services"), name="services")

# --- FILE PROCESSING HELPERS ---
def extract_text_from_uploaded_content(filename: str, content: bytes) -> str:
    filename = (filename or "uploaded_document").lower()
    text = ""
    
    try:
        if filename.endswith(".pdf"):
            pdf = PdfReader(io.BytesIO(content))
            for page in pdf.pages:
                text += page.extract_text() + "\n"
            print(f"   [File Parser] Extracted {len(text)} chars from PDF: {filename}")
            
        elif filename.endswith(".html") or filename.endswith(".htm"):
            soup = BeautifulSoup(content, 'html.parser')
            # Remove scripts and styles
            for script in soup(["script", "style"]):
                script.decompose()
            text = soup.get_text(separator=' ')
            print(f"   [File Parser] Extracted {len(text)} chars from HTML: {filename}")
        
        else:
            # Fallback for text files
            text = content.decode('utf-8', errors='ignore')
            
    except Exception as e:
        print(f"   [File Parser Error] Could not parse {filename}: {e}")
        return ""
        
    return text

def extract_text_from_file(file: UploadFile) -> str:
    return extract_text_from_uploaded_content(file.filename, file.file.read())

def materialize_uploads(files: Optional[List[UploadFile]]) -> list:
    """Copy UploadFile payloads before the request lifecycle ends."""
    materialized = []
    for file in files or []:
        try:
            materialized.append({
                "filename": file.filename or "uploaded_document",
                "content": file.file.read(),
            })
        finally:
            try:
                file.file.close()
            except Exception:
                pass
    return materialized

# --- ROUTES ---

@app.get("/App.tsx")
async def get_app_tsx(): return FileResponse("../App.tsx", media_type="application/javascript")
@app.get("/index.tsx")
async def get_index_tsx(): return FileResponse("../index.tsx", media_type="application/javascript")
@app.get("/types.ts")
async def get_types_ts(): return FileResponse("../types.ts", media_type="application/javascript")
@app.get("/constants.ts")
async def get_constants_ts(): return FileResponse("../constants.ts", media_type="application/javascript")
@app.get("/")
async def read_root(): return FileResponse("../index.html")

@app.get("/api/health")
def health_check():
    token = clean_env_var(os.environ.get("BLOB_READ_WRITE_TOKEN"))
    return {
        "status": "Adaptability Scanner API Online (v5.2.0)", 
        "timestamp": datetime.utcnow().isoformat(),
        "storage_connected": bool(token)
    }

@app.get("/api/find-url")
async def find_company_url(name: str):
    """Find the official homepage URL using GPT/Claude web search."""
    prompt = f"What is the official website homepage URL for the organization '{name}'? Return ONLY the URL string (e.g. https://www.example.com). Do not include markdown or explanations."
    for model_name, provider in (("gpt-5.5", "openai"), ("claude-sonnet-4-6", "anthropic")):
        try:
            if provider == "anthropic":
                text = call_anthropic_text(prompt, model=model_name, max_tokens=120, timeout=45, web_search=True, max_searches=2)
            else:
                text = call_openai_text(prompt, model=model_name, max_output_tokens=120, timeout=45, web_search=True, search_context_size="low")
            url = (text or "").strip().strip("'").strip('"')
            if "](" in url:
                url = url.split("](", 1)[1].strip(")")
            match = re.search(r"https?://[^\s)>'\"]+", url)
            if match:
                return {"url": match.group(0).rstrip(".,")}
        except Exception as err:
            print(f"   [API] Find URL {model_name} failed: {err}")
    return {"url": ""}

@app.get("/dashboard-data")
async def get_dashboard_data(response: Response):
    response.headers["Cache-Control"] = "no-cache, no-store, must-revalidate"
    data = storage.retrieve_latest_data("index/summary_list")
    if data:
        from domain_classifier import normalize_domain
        # Deduplicate by companyName (case-insensitive), keeping the last (newest) entry
        seen = {}
        deduped = []
        for item in data:
            key = item.get("companyName", "").lower()
            if key in seen:
                deduped[seen[key]] = item  # overwrite older entry with newer one
            else:
                seen[key] = len(deduped)
                deduped.append(item)
        if len(deduped) < len(data):
            print(f"   [Dashboard] Deduplicated index: {len(data)} -> {len(deduped)} entries")
            data = deduped
            # Persist the cleaned index so duplicates don't come back
            storage.save_global_index_direct(data)
        for item in data:
            if item.get("domain"):
                item["domain"] = normalize_domain(item["domain"])
    return data or []

@app.get("/api/fp-themes")
async def get_fp_themes(request: Request):
    """Return the 30 Strategic Foresight themes with descriptions and source counts. Guest or Admin."""
    verify_guest_or_admin(request)
    from strategic_foresight import build_strategic_foresight_theme_registry

    registry = build_strategic_foresight_theme_registry()
    return {
        "categories": [c["name"] for c in registry.get("categories", [])],
        "themes": [
            {
                "name": t.get("name", ""),
                "category": t.get("category", ""),
                "description": t.get("description", ""),
                "source_count": t.get("source_count", 0),
                "source_domains": t.get("source_domains", []),
            }
            for t in registry.get("themes", [])
        ],
        "registry_version": registry.get("registry_version"),
        "refresh_rules": registry.get("refresh_rules", {}),
    }

@app.get("/api/fp-themes/{theme_slug}/signals")
async def get_theme_signals(theme_slug: str, request: Request):
    """Return harvested KB signals for a specific FP theme. Admin-only."""
    verify_admin(request)
    data = storage.get_foresight_kb(theme_slug)
    if not data:
        return {"signals": [], "theme_slug": theme_slug}
    signals = data.get("signals", [])
    # Return compact signal data for display
    return {
        "theme_slug": theme_slug,
        "signal_count": len(signals),
        "signals": [{
            "title": s.get("title", ""),
            "description": s.get("description", ""),
            "source_url": s.get("source_url", ""),
            "force_type": s.get("force_type", ""),
            "timeframe": s.get("timeframe", ""),
            "lifecycle_stage": s.get("lifecycle_stage", ""),
            "trend_momentum": s.get("trend_momentum", ""),
            "confidence": s.get("confidence", ""),
            "source_credibility": s.get("source_credibility", ""),
            "source_count": s.get("source_count", 0),
            "kb_dimension": s.get("kb_dimension", ""),
            "validated": s.get("validated", False),
        } for s in signals],
    }

@app.get("/api/analyze")
async def analyze_scores(response: Response):
    """Run statistical analysis across all company scores (correlations, distributions, archetypes)."""
    response.headers["Cache-Control"] = "no-cache, no-store, must-revalidate"
    data = storage.retrieve_latest_data("index/summary_list")
    if not data:
        raise HTTPException(status_code=404, detail="No dashboard data found.")
    return run_analysis(data)

@app.get("/api/market-intelligence")
async def market_intelligence(response: Response):
    """Aggregate cross-company data into strategic market intelligence views."""
    response.headers["Cache-Control"] = "no-cache, no-store, must-revalidate"
    # 1. Load dashboard summary data
    dashboard_data = storage.retrieve_latest_data("index/summary_list")
    if not dashboard_data:
        raise HTTPException(status_code=404, detail="No dashboard data found.")

    # 2. Load detailed data for each company (needed for foresight_links, proposals)
    company_details = []
    for item in dashboard_data:
        name = item.get("companyName")
        if name:
            detail = storage.get_company_data(name)
            if detail:
                company_details.append(detail)

    # 3. Run statistical analysis (reuse existing)
    analysis_result = run_analysis(dashboard_data)

    # 4. Compute market intelligence
    return compute_market_intelligence(dashboard_data, company_details, analysis_result)

@app.get("/company/{company_name}")
async def get_company_details(company_name: str, response: Response):
    response.headers["Cache-Control"] = "no-cache, no-store, must-revalidate"
    data = storage.get_company_data(company_name)
    if not data:
        raise HTTPException(status_code=404, detail="Company report not found in Vercel Blob.")
    # Clean legacy TAC-xxx / DIM-xxx codes that may exist in pre-rule stored summaries
    if data.get("summary_text"):
        data["summary_text"] = _strip_tac_codes(data["summary_text"])
    return data

@app.get("/admin/company-list")
async def get_company_list(request: Request):
    verify_admin(request)
    return {"companies": TOP_100_COMPANIES}

@app.post("/admin/reset-database")
async def reset_database(request: Request):
    verify_admin(request)
    count = storage.nuke_everything()
    return {"status": "Database Wiped", "deleted_count": count}

@app.delete("/admin/company/{company_name}")
async def delete_company(company_name: str, request: Request):
    """Delete a single company's data from blob storage and global index."""
    verify_admin(request)
    result = storage.delete_company(company_name)
    return {
        "status": "deleted",
        "company": company_name,
        "deleted_files": result["deleted_files"],
        "removed_from_index": result["removed_from_index"]
    }

@app.post("/admin/dedup-company/{company_name}")
async def dedup_company(company_name: str, request: Request):
    """Remove duplicate entries for a company from the global index, keeping one."""
    verify_admin(request)
    removed = storage.remove_duplicate_from_index(company_name)
    return {"status": "deduped", "company": company_name, "duplicates_removed": removed}

@app.post("/admin/cleanup-index")
async def cleanup_index(request: Request):
    """Remove orphaned entries from the summary index (companies without data files)."""
    verify_admin(request)
    removed = storage.cleanup_orphaned_index_entries()
    return {"status": "cleaned", "removed": removed, "removed_count": len(removed)}

@app.get("/admin/review-data")
async def review_data(request: Request):
    """Returns all stored companies with summary previews and hallucination flags for admin review."""
    verify_admin(request)
    companies = storage.get_all_companies_summary()
    return {"companies": companies, "total": len(companies)}

_buying_potential_jobs = {}
_buying_potential_jobs_lock = threading.Lock()
_company_evidence_jobs = {}
_company_evidence_jobs_lock = threading.Lock()
_company_source_ledger_jobs = {}
_company_source_ledger_jobs_lock = threading.Lock()
_market_readiness_state = {
    "is_running": False,
    "started_at": None,
    "completed_at": None,
    "current_step": None,
    "progress": 0,
    "result": None,
    "error": None,
    "metadata": None,
}
_market_readiness_lock = threading.Lock()


def _update_market_readiness_state(**fields):
    with _market_readiness_lock:
        _market_readiness_state.update(fields)


def _run_market_readiness_generation():
    started = time.time()

    def set_stage(step: str, progress: int):
        _update_market_readiness_state(
            is_running=True,
            current_step=step,
            progress=progress,
            elapsed_ms=int((time.time() - started) * 1000),
        )
        print(f"   🔄 [Market Readiness] {step} ({progress}%)", flush=True)

    try:
        set_stage("Loading stored Scanner artifacts", 12)
        report_json = build_market_readiness_report(storage, buying_storage=buying_storage)
        set_stage("Rendering HTML report", 72)
        html_report = render_market_readiness_html(report_json)
        set_stage("Saving report artifacts", 88)
        metadata = storage.save_market_readiness_report(report_json, html_report)
        if not metadata:
            raise RuntimeError("Market Readiness report generated but could not be saved.")
        _update_market_readiness_state(
            is_running=False,
            current_step=None,
            progress=100,
            result={"status": "completed"},
            error=None,
            metadata=metadata,
            completed_at=datetime.utcnow().isoformat(),
            elapsed_ms=int((time.time() - started) * 1000),
        )
        print(f"   ✅ [Market Readiness] Completed in {int((time.time() - started) * 1000)}ms", flush=True)
    except Exception as exc:
        print(f"   ❌ [Market Readiness] Failed: {exc}", flush=True)
        import traceback
        traceback.print_exc()
        _update_market_readiness_state(
            is_running=False,
            current_step=None,
            progress=100,
            result={"error": str(exc)},
            error=f"{type(exc).__name__}: {str(exc)[:240]}",
            completed_at=datetime.utcnow().isoformat(),
            elapsed_ms=int((time.time() - started) * 1000),
        )


@app.post("/admin/generate-market-readiness-report")
async def generate_market_readiness_report_endpoint(request: Request):
    """Start the Admin-only Market Readiness and Emerging Demand Outlook report."""
    verify_admin(request)
    with _market_readiness_lock:
        if _market_readiness_state["is_running"]:
            raise HTTPException(status_code=409, detail="Market Readiness report generation already in progress.")
        _market_readiness_state.update({
            "is_running": True,
            "started_at": datetime.utcnow().isoformat(),
            "completed_at": None,
            "current_step": "Initializing",
            "progress": 0,
            "result": None,
            "error": None,
            "metadata": None,
            "elapsed_ms": 0,
        })
    thread = threading.Thread(target=_run_market_readiness_generation, daemon=True)
    thread.start()
    return {"status": "started"}


@app.get("/admin/market-readiness-status")
async def market_readiness_report_status(request: Request):
    verify_admin(request)
    with _market_readiness_lock:
        state = dict(_market_readiness_state)
    if not state.get("metadata"):
        latest = storage.get_market_readiness_report()
        if latest.get("exists"):
            state["metadata"] = latest.get("metadata")
    return state


@app.get("/admin/market-readiness-report")
async def get_market_readiness_report_endpoint(request: Request):
    verify_admin(request)
    return storage.get_market_readiness_report()


def _append_company_evidence_log(job_id: str, message: str):
    with _company_evidence_jobs_lock:
        job = _company_evidence_jobs.get(job_id)
        if not job:
            return
        logs = job.setdefault("logs", [])
        logs.append({"ts": datetime.utcnow().isoformat(), "message": message})
        if len(logs) > 120:
            del logs[:-120]


def _update_company_evidence_job(job_id: str, **fields):
    with _company_evidence_jobs_lock:
        job = _company_evidence_jobs.get(job_id)
        if not job:
            return
        job.update(fields)


def _run_company_evidence_job(job_id: str, company_name: str):
    started = time.time()

    def set_stage(stage: str, progress: int, message: str):
        elapsed_ms = int((time.time() - started) * 1000)
        _update_company_evidence_job(job_id, status="running", stage=stage, progress=progress, elapsed_ms=elapsed_ms)
        _append_company_evidence_log(job_id, message)
        print(f"   🔄 [Company Evidence] {company_name} | {stage} | {message} ({elapsed_ms}ms)", flush=True)

    try:
        set_stage("load_company", 8, "Loading stored company data")
        company_data = storage.get_company_data(company_name)
        if not company_data:
            raise CompanyEvidenceError(f"Company '{company_name}' not found in database.", 404)
        resolved_company = company_data.get("companyName") or company_name
        _update_company_evidence_job(job_id, company=resolved_company)

        def stage_callback(stage: str, progress: int, message: str):
            set_stage(stage, progress, message)

        evidence_json, html_report = build_company_evidence_database(company_data, stage_callback=stage_callback)
        set_stage("save_database", 88, "Saving Company Evidence DB JSON/HTML to BLOB2")
        metadata = buying_storage.save_company_evidence_database(resolved_company, evidence_json, html_report)
        if not metadata:
            raise CompanyEvidenceError("Company Evidence DB generated but could not be saved.", 500)
        set_stage("update_index", 96, "Company Evidence DB metadata saved")
        _update_company_evidence_job(
            job_id,
            status="completed",
            stage="completed",
            progress=100,
            metadata=metadata,
            stats=evidence_json.get("stats", {}),
            warnings=evidence_json.get("warnings", []),
            completed_at=datetime.utcnow().isoformat(),
            elapsed_ms=int((time.time() - started) * 1000),
        )
        _append_company_evidence_log(job_id, "Company Evidence DB completed")
        print(f"   ✅ [Company Evidence] {resolved_company} completed in {int((time.time() - started) * 1000)}ms", flush=True)
    except CompanyEvidenceError as exc:
        _update_company_evidence_job(
            job_id,
            status="failed",
            error=str(exc),
            error_status_code=exc.status_code,
            progress=100,
            completed_at=datetime.utcnow().isoformat(),
            elapsed_ms=int((time.time() - started) * 1000),
        )
        _append_company_evidence_log(job_id, f"Company Evidence DB failed: {exc}")
        print(f"   ❌ [Company Evidence] {company_name} failed: {exc}", flush=True)
    except Exception as exc:
        _update_company_evidence_job(
            job_id,
            status="failed",
            error=f"{type(exc).__name__}: {str(exc)[:240]}",
            progress=100,
            completed_at=datetime.utcnow().isoformat(),
            elapsed_ms=int((time.time() - started) * 1000),
        )
        _append_company_evidence_log(job_id, f"Company Evidence DB failed: {exc}")
        print(f"   ❌ [Company Evidence] {company_name}: {exc}", flush=True)


def _append_company_source_ledger_log(job_id: str, message: str):
    with _company_source_ledger_jobs_lock:
        job = _company_source_ledger_jobs.get(job_id)
        if not job:
            return
        logs = job.setdefault("logs", [])
        logs.append({"ts": datetime.utcnow().isoformat(), "message": message})
        if len(logs) > 120:
            del logs[:-120]


def _update_company_source_ledger_job(job_id: str, **fields):
    with _company_source_ledger_jobs_lock:
        job = _company_source_ledger_jobs.get(job_id)
        if not job:
            return
        job.update(fields)


def _run_company_source_ledger_job(job_id: str, company_name: str):
    started = time.time()

    def set_stage(stage: str, progress: int, message: str):
        elapsed_ms = int((time.time() - started) * 1000)
        _update_company_source_ledger_job(job_id, status="running", stage=stage, progress=progress, elapsed_ms=elapsed_ms)
        _append_company_source_ledger_log(job_id, message)
        print(f"   🔄 [Source Ledger] {company_name} | {stage} | {message} ({elapsed_ms}ms)", flush=True)

    try:
        set_stage("load_company", 8, "Loading stored Scanner company data")
        company_data = storage.get_company_data(company_name)
        if not company_data:
            raise CompanySourceLedgerError(f"Company '{company_name}' not found in database.", 404)

        resolved_company = company_data.get("companyName") or company_name
        _update_company_source_ledger_job(job_id, company=resolved_company)

        set_stage("load_company_evidence", 24, "Loading Company Evidence DB from BLOB2")
        evidence_lookup = buying_storage.get_company_evidence_database(resolved_company)
        company_evidence = evidence_lookup.get("database") if evidence_lookup.get("exists") else None
        if company_evidence:
            stats = company_evidence.get("stats", {})
            _append_company_source_ledger_log(job_id, f"Company Evidence DB loaded: {stats.get('candidate_sources', 0)} candidates")
        else:
            _append_company_source_ledger_log(job_id, "No Company Evidence DB found; combining Scanner data only")

        set_stage("load_buying_insight", 36, "Loading Buying Insight source references if available")
        buying_lookup = buying_storage.get_buying_potential_report(resolved_company)
        buying_report = buying_lookup.get("report") if buying_lookup.get("exists") else None
        if buying_report:
            _append_company_source_ledger_log(job_id, "Buying Insight report loaded for source-reference merge")

        def stage_callback(stage: str, progress: int, message: str):
            set_stage(stage, progress, message)

        ledger_json, html_report = build_company_source_ledger(
            company_data,
            company_evidence=company_evidence,
            buying_potential=buying_report,
            stage_callback=stage_callback,
        )
        set_stage("save_ledger", 86, "Saving Company Source Ledger JSON/HTML to BLOB2")
        metadata = buying_storage.save_company_source_ledger(resolved_company, ledger_json, html_report)
        if not metadata:
            raise CompanySourceLedgerError("Company Source Ledger generated but could not be saved.", 500)

        set_stage("update_index", 95, "Company Source Ledger metadata saved")
        _update_company_source_ledger_job(
            job_id,
            status="completed",
            stage="completed",
            progress=100,
            metadata=metadata,
            stats=ledger_json.get("stats", {}),
            warnings=ledger_json.get("warnings", []),
            completed_at=datetime.utcnow().isoformat(),
            elapsed_ms=int((time.time() - started) * 1000),
        )
        _append_company_source_ledger_log(job_id, "Company Source Ledger completed")
        print(f"   ✅ [Source Ledger] {resolved_company} completed in {int((time.time() - started) * 1000)}ms", flush=True)
    except CompanySourceLedgerError as exc:
        _update_company_source_ledger_job(
            job_id,
            status="failed",
            error=str(exc),
            error_status_code=exc.status_code,
            progress=100,
            completed_at=datetime.utcnow().isoformat(),
            elapsed_ms=int((time.time() - started) * 1000),
        )
        _append_company_source_ledger_log(job_id, f"Company Source Ledger failed: {exc}")
        print(f"   ❌ [Source Ledger] {company_name} failed: {exc}", flush=True)
    except Exception as exc:
        _update_company_source_ledger_job(
            job_id,
            status="failed",
            error=f"{type(exc).__name__}: {str(exc)[:240]}",
            progress=100,
            completed_at=datetime.utcnow().isoformat(),
            elapsed_ms=int((time.time() - started) * 1000),
        )
        _append_company_source_ledger_log(job_id, f"Company Source Ledger failed: {exc}")
        print(f"   ❌ [Source Ledger] {company_name}: {exc}", flush=True)


def _append_buying_job_log(job_id: str, message: str):
    with _buying_potential_jobs_lock:
        job = _buying_potential_jobs.get(job_id)
        if not job:
            return
        logs = job.setdefault("logs", [])
        logs.append({"ts": datetime.utcnow().isoformat(), "message": message})
        if len(logs) > 120:
            del logs[:-120]


def _update_buying_job(job_id: str, **fields):
    with _buying_potential_jobs_lock:
        job = _buying_potential_jobs.get(job_id)
        if not job:
            return
        job.update(fields)


def _run_buying_potential_job(job_id: str, company_name: str):
    started = time.time()

    def set_stage(stage: str, progress: int, message: str):
        elapsed_ms = int((time.time() - started) * 1000)
        _update_buying_job(job_id, status="running", stage=stage, progress=progress, elapsed_ms=elapsed_ms)
        _append_buying_job_log(job_id, message)
        print(f"   🔄 [Buying Potential] {company_name} | {stage} | {message} ({elapsed_ms}ms)", flush=True)

    try:
        set_stage("load_company", 8, "Loading stored company data")
        company_data = storage.get_company_data(company_name)
        if not company_data:
            raise BuyingPotentialError(f"Company '{company_name}' not found in database.", 404)

        resolved_company = company_data.get("companyName") or company_name
        _update_buying_job(job_id, company=resolved_company)
        set_stage("load_service_kb", 22, "Loading and validating Service KB from BLOB2")
        service_kb = load_service_portfolio_kb(buying_storage)
        _append_buying_job_log(job_id, f"Service KB ready: {len(service_kb.get('services') or [])} services")
        evidence_lookup = buying_storage.get_company_evidence_database(resolved_company)
        company_evidence = evidence_lookup.get("database") if evidence_lookup.get("exists") else None
        if company_evidence:
            stats = company_evidence.get("stats", {})
            _append_buying_job_log(job_id, f"Company Evidence DB loaded: {stats.get('buying_evidence_eligible', 0)} eligible records")
        else:
            _append_buying_job_log(job_id, "No Company Evidence DB found; using Scanner data only")
        ledger_lookup = buying_storage.get_company_source_ledger(resolved_company)
        company_source_ledger = ledger_lookup.get("ledger") if ledger_lookup.get("exists") else None
        if company_source_ledger:
            stats = company_source_ledger.get("stats", {})
            _append_buying_job_log(job_id, f"Company Source Ledger loaded: {stats.get('buying_eligible', 0)} buying-eligible records")
        else:
            _append_buying_job_log(job_id, "No Company Source Ledger found")

        def stage_callback(stage: str, progress: int, message: str):
            set_stage(stage, progress, message)

        report_json, html_report = generate_buying_potential_report(
            company_data,
            service_kb,
            company_evidence=company_evidence,
            company_source_ledger=company_source_ledger,
            stage_callback=stage_callback,
        )

        set_stage("save_artifacts", 90, "Saving Buying Insight JSON/HTML artifacts to BLOB2")
        metadata = buying_storage.save_buying_potential_report(
            resolved_company,
            report_json,
            html_report,
            index_storage=storage,
        )
        if not metadata:
            raise BuyingPotentialError("Buying Potential report generated but could not be saved.", 500)
        set_stage("update_index", 96, "Buying Insight metadata saved")
        _update_buying_job(
            job_id,
            status="completed",
            stage="completed",
            progress=100,
            metadata=metadata,
            confidence=report_json.get("overall_confidence"),
            source_counts=report_json.get("source_counts", {}),
            warnings=report_json.get("missing_input_warnings", []),
            completed_at=datetime.utcnow().isoformat(),
            elapsed_ms=int((time.time() - started) * 1000),
        )
        _append_buying_job_log(job_id, "Buying Insight completed")
        print(f"   ✅ [Buying Potential] {resolved_company} completed in {int((time.time() - started) * 1000)}ms", flush=True)
    except BuyingPotentialError as exc:
        _update_buying_job(
            job_id,
            status="failed",
            error=str(exc),
            error_status_code=exc.status_code,
            progress=100,
            completed_at=datetime.utcnow().isoformat(),
            elapsed_ms=int((time.time() - started) * 1000),
        )
        _append_buying_job_log(job_id, f"Buying Insight failed: {exc}")
        print(f"   ❌ [Buying Potential] {company_name} failed: {exc}", flush=True)
    except Exception as exc:
        _update_buying_job(
            job_id,
            status="failed",
            error=f"{type(exc).__name__}: {str(exc)[:240]}",
            progress=100,
            completed_at=datetime.utcnow().isoformat(),
            elapsed_ms=int((time.time() - started) * 1000),
        )
        _append_buying_job_log(job_id, f"Buying Insight failed: {exc}")
        print(f"   ❌ [Buying Potential] {company_name}: {exc}", flush=True)


@app.post("/admin/buying-potential/{company_name}")
async def create_buying_potential(company_name: str, request: Request):
    """Start a background job to create/regenerate a sales-facing Buying Potential report."""
    verify_admin(request)
    job_id = str(uuid.uuid4())
    with _buying_potential_jobs_lock:
        for job in _buying_potential_jobs.values():
            if job.get("status") in {"queued", "running"}:
                raise HTTPException(
                    status_code=409,
                    detail=f"Buying Insight is already running for {job.get('company') or 'another company'}.",
                )
        _buying_potential_jobs[job_id] = {
            "job_id": job_id,
            "status": "queued",
            "stage": "queued",
            "progress": 0,
            "company": company_name,
            "error": None,
            "metadata": None,
            "confidence": None,
            "source_counts": {},
            "warnings": [],
            "logs": [{"ts": datetime.utcnow().isoformat(), "message": f"Queued Buying Insight for {company_name}"}],
            "created_at": datetime.utcnow().isoformat(),
            "completed_at": None,
            "elapsed_ms": 0,
        }
    thread = threading.Thread(target=_run_buying_potential_job, args=(job_id, company_name), daemon=True)
    thread.start()
    return {"job_id": job_id, "status": "started", "company": company_name}


@app.post("/admin/company-evidence/{company_name}")
async def create_company_evidence(company_name: str, request: Request):
    """Start a background job to build a BLOB2 Company Evidence Database for one company."""
    verify_admin(request)
    job_id = str(uuid.uuid4())
    with _company_evidence_jobs_lock:
        for job in _company_evidence_jobs.values():
            if job.get("status") in {"queued", "running"}:
                raise HTTPException(
                    status_code=409,
                    detail=f"Company Evidence DB is already running for {job.get('company') or 'another company'}.",
                )
        _company_evidence_jobs[job_id] = {
            "job_id": job_id,
            "status": "queued",
            "stage": "queued",
            "progress": 0,
            "company": company_name,
            "error": None,
            "metadata": None,
            "stats": {},
            "warnings": [],
            "logs": [{"ts": datetime.utcnow().isoformat(), "message": f"Queued Company Evidence DB for {company_name}"}],
            "created_at": datetime.utcnow().isoformat(),
            "completed_at": None,
            "elapsed_ms": 0,
        }
    thread = threading.Thread(target=_run_company_evidence_job, args=(job_id, company_name), daemon=True)
    thread.start()
    return {"job_id": job_id, "status": "started", "company": company_name}


@app.get("/admin/company-evidence-status/{job_id}")
async def get_company_evidence_status(job_id: str, request: Request):
    """Return progress for a Company Evidence Database background job."""
    verify_admin(request)
    with _company_evidence_jobs_lock:
        job = _company_evidence_jobs.get(job_id)
        if not job:
            raise HTTPException(status_code=404, detail="Company Evidence DB job not found")
        return dict(job)


@app.get("/admin/company-evidence/{company_name}")
async def get_company_evidence(company_name: str, request: Request):
    """Return stored Company Evidence DB metadata/database for one company, if available."""
    verify_admin(request)
    return buying_storage.get_company_evidence_database(company_name)


@app.get("/admin/company-evidence-index")
async def company_evidence_index(request: Request):
    """Return the Company Evidence DB artifact index for Admin review."""
    verify_admin(request)
    return {"reports": buying_storage.get_company_evidence_index()}


@app.post("/admin/company-source-ledger/{company_name}")
async def create_company_source_ledger(company_name: str, request: Request):
    """Start a background job to combine reusable company source memory in BLOB2."""
    verify_admin(request)
    job_id = str(uuid.uuid4())
    with _company_source_ledger_jobs_lock:
        for job in _company_source_ledger_jobs.values():
            if job.get("status") in {"queued", "running"}:
                raise HTTPException(
                    status_code=409,
                    detail=f"Company Source Ledger is already running for {job.get('company') or 'another company'}.",
                )
        _company_source_ledger_jobs[job_id] = {
            "job_id": job_id,
            "status": "queued",
            "stage": "queued",
            "progress": 0,
            "company": company_name,
            "error": None,
            "metadata": None,
            "stats": {},
            "warnings": [],
            "logs": [{"ts": datetime.utcnow().isoformat(), "message": f"Queued Company Source Ledger for {company_name}"}],
            "created_at": datetime.utcnow().isoformat(),
            "completed_at": None,
            "elapsed_ms": 0,
        }
    thread = threading.Thread(target=_run_company_source_ledger_job, args=(job_id, company_name), daemon=True)
    thread.start()
    return {"job_id": job_id, "status": "started", "company": company_name}


@app.get("/admin/company-source-ledger-status/{job_id}")
async def get_company_source_ledger_status(job_id: str, request: Request):
    """Return progress for a Company Source Ledger background job."""
    verify_admin(request)
    with _company_source_ledger_jobs_lock:
        job = _company_source_ledger_jobs.get(job_id)
        if not job:
            raise HTTPException(status_code=404, detail="Company Source Ledger job not found")
        return dict(job)


@app.get("/admin/company-source-ledger/{company_name}")
async def get_company_source_ledger(company_name: str, request: Request):
    """Return stored Company Source Ledger metadata/database for one company, if available."""
    verify_admin(request)
    return buying_storage.get_company_source_ledger(company_name)


@app.get("/admin/company-source-ledger-index")
async def company_source_ledger_index(request: Request):
    """Return the Company Source Ledger artifact index for Admin review."""
    verify_admin(request)
    return {"reports": buying_storage.get_company_source_ledger_index()}


@app.get("/admin/buying-potential-status/{job_id}")
async def get_buying_potential_status(job_id: str, request: Request):
    """Return progress for a Buying Potential background job."""
    verify_admin(request)
    with _buying_potential_jobs_lock:
        job = _buying_potential_jobs.get(job_id)
        if not job:
            raise HTTPException(status_code=404, detail="Buying Potential job not found")
        return dict(job)


@app.post("/admin/buying-potential-service-kb/validate")
async def validate_buying_potential_service_kb(request: Request):
    """Validate BLOB2 Service KB config/cache/PDF before running a Buying Insight job."""
    verify_admin(request)
    try:
        return validate_service_portfolio_kb(buying_storage)
    except BuyingPotentialError as exc:
        raise HTTPException(status_code=exc.status_code, detail=str(exc))
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Service KB validation failed: {str(exc)[:240]}")

@app.get("/admin/buying-potential/{company_name}")
async def get_buying_potential(company_name: str, request: Request):
    """Return stored Buying Potential metadata/report for one company, if available."""
    verify_admin(request)
    return buying_storage.get_buying_potential_report(company_name)

@app.get("/admin/buying-potential-index")
async def buying_potential_index(request: Request):
    """Return the Buying Potential artifact index for Admin review."""
    verify_admin(request)
    return {"reports": buying_storage.get_buying_potential_index()}


_evidence_clean_state = {
    "is_running": False,
    "started_at": None,
    "completed_at": None,
    "current_company": None,
    "current": 0,
    "total": 0,
    "result": None,
    "errors": [],
}
_evidence_clean_lock = threading.Lock()


def _clean_evidence_for_company(company_name: str, data: dict) -> dict:
    result = clean_company_evidence(
        company_name,
        data,
        model_chain=CLEAN_EVIDENCE_MODEL_CHAIN,
    )
    safe_data = _make_json_safe_payload(data, f"{company_name}.clean_evidence")
    storage.save_company_data(company_name, safe_data)
    return result


def _run_clean_evidence_all(companies: list[dict]):
    results = []
    errors = []
    try:
        for idx, company in enumerate(companies, start=1):
            name = company.get("companyName", "")
            if not name:
                continue
            with _evidence_clean_lock:
                _evidence_clean_state["current"] = idx
                _evidence_clean_state["current_company"] = name
            try:
                data = storage.get_company_data(name)
                if not data:
                    raise ValueError("Company data not found")
                result = _clean_evidence_for_company(name, data)
                results.append(result)
            except Exception as exc:
                error = {"company": name, "error": str(exc)[:240]}
                errors.append(error)
                with _evidence_clean_lock:
                    _evidence_clean_state["errors"] = errors[-20:]
                print(f"   ❌ [Clean Evidence] {name}: {exc}")
        with _evidence_clean_lock:
            _evidence_clean_state["is_running"] = False
            _evidence_clean_state["completed_at"] = datetime.utcnow().isoformat()
            _evidence_clean_state["current_company"] = None
            _evidence_clean_state["result"] = {
                "status": "done",
                "total": len(companies),
                "cleaned": len(results),
                "failed": len(errors),
                "results": results,
                "errors": errors,
            }
    except Exception as exc:
        with _evidence_clean_lock:
            _evidence_clean_state["is_running"] = False
            _evidence_clean_state["completed_at"] = datetime.utcnow().isoformat()
            _evidence_clean_state["result"] = {"status": "failed", "error": str(exc)}


@app.post("/admin/clean-evidence/{company_name}")
async def clean_evidence_single(company_name: str, request: Request):
    """Revalidate stored evidence metadata for one company without regenerating prose."""
    verify_admin(request)
    data = storage.get_company_data(company_name)
    if not data:
        raise HTTPException(status_code=404, detail=f"Company '{company_name}' not found in database.")
    return _clean_evidence_for_company(company_name, data)


@app.post("/admin/clean-evidence-all")
async def clean_evidence_all(request: Request):
    """Start background evidence cleaning for all stored companies."""
    verify_admin(request)
    companies = storage.get_all_companies_summary()
    with _evidence_clean_lock:
        if _evidence_clean_state["is_running"]:
            raise HTTPException(status_code=409, detail="Evidence cleaning already in progress.")
        _evidence_clean_state["is_running"] = True
        _evidence_clean_state["started_at"] = datetime.utcnow().isoformat()
        _evidence_clean_state["completed_at"] = None
        _evidence_clean_state["current_company"] = "Starting..."
        _evidence_clean_state["current"] = 0
        _evidence_clean_state["total"] = len(companies)
        _evidence_clean_state["result"] = None
        _evidence_clean_state["errors"] = []
    thread = threading.Thread(target=_run_clean_evidence_all, args=(companies,), daemon=True)
    thread.start()
    return {"status": "started", "total": len(companies)}


@app.get("/admin/clean-evidence-status")
async def clean_evidence_status(request: Request):
    """Return evidence cleaning progress."""
    verify_admin(request)
    with _evidence_clean_lock:
        return dict(_evidence_clean_state)

@app.post("/admin/sanitize-foresight-records")
async def sanitize_foresight_records(
    request: Request,
    dry_run: bool = True,
    write: bool = False,
    post_clean: bool = False,
    company: str | None = None,
    report_date: str | None = None,
):
    """Dry-run or apply foresight/source-card sanitation for stored company records.

    This endpoint is intentionally dry-run-first. The frontend calls it first with
    dry_run=true/write=false to return an audit report. Only when the admin confirms
    should it be called with dry_run=false/write=true.
    """
    verify_admin(request)

    try:
        parsed_report_date = date.fromisoformat(report_date) if report_date else date.today()
    except ValueError:
        raise HTTPException(status_code=400, detail="report_date must use YYYY-MM-DD format")

    report = sanitize_database(
        company=company,
        dry_run=dry_run or not write,
        write=write,
        post_clean=post_clean,
        report_date=parsed_report_date,
    )
    return report

def _recheck_company_data(company_name: str, data: dict) -> dict:
    """
    Shared per-company recheck logic: heuristic check → pitch check → AI fact-check → auto-clean.
    Used by both single-company recheck and recheck-all to guarantee identical procedure.

    Returns dict with summary_check, pitch_check, ai_verification, recommendation,
    and cleaning results if auto-clean was performed.
    """
    # Refresh evidence metadata before any validation step.  Companies last scanned
    # before commit b857b1b may have stale or missing claim_tier / source_label —
    # annotate_evidence_links normalises in place so all downstream steps (validate_summary,
    # ai_fact_check_summary, generate_general_summary) and the final storage.save_company_data()
    # all see current metadata.
    company_domain = (data.get("companyUrl") or data.get("domain") or "").strip()
    annotate_evidence_links(data.get("evidence_links") or [], company_domain, company_name)

    # Step 1: Heuristic validation (summary + pitch)
    summary_result = validate_summary(
        summary_text=data.get("summary_text", ""),
        company_name=company_name,
        key_people=data.get("key_people", []),
        evidence_links=data.get("evidence_links", []),
        confidence_score=data.get("confidence_score"),
    )
    pitch_result = validate_pitch(
        pitch_text=data.get("pitch_text", ""),
        company_name=company_name,
    )

    # Step 2: AI-powered fact-check (GPT-5.4 / Claude provider-separated verification)
    ai_result = ai_fact_check_summary(
        summary_text=data.get("summary_text", ""),
        evidence_links=data.get("evidence_links", []),
        company_name=company_name,
        pitch_text=data.get("pitch_text", ""),
        scan_payload=_build_fact_check_scan_payload(data),
        foresight_links=data.get("foresight_links", []),
    )

    # Step 3: Persist AI verification result back to stored data
    data["ai_verification"] = {
        "verified": ai_result["verified"],
        "fact_check_skipped": ai_result.get("fact_check_skipped", False),
        "false_claims": ai_result["false_claims"],
        "false_claims_detailed": ai_result.get("false_claims_detailed", []),
        "model": ai_result["fact_check_model"],
        "time_ms": ai_result["fact_check_time_ms"],
    }
    if ai_result.get("fact_check_skipped"):
        print(f"   ⚠️ [Admin Recheck] All fact-check models failed — summary stored as UNVERIFIED")
    callable_paths = _find_callables(data, f"{company_name}.pre_save")
    if callable_paths:
        print(f"   ⚠️ [Data Hygiene] Callable objects before save: {callable_paths[:30]}")
    data = _make_json_safe_payload(data, f"{company_name}.pre_save")
    storage.save_company_data(company_name, data)
    storage.update_global_index_field(company_name, "ai_verified", ai_result["verified"])

    # Step 4: Auto-clean if AI found false claims
    # Prefer corrected summary from fact-checker (inline correction) over separate clean
    clean_result = None
    if not ai_result["verified"] and not ai_result.get("fact_check_skipped") and ai_result["false_claims"]:
        # Purge contaminated evidence sources (parity with Deep Scan pipeline)
        rc_detailed_claims = ai_result.get("false_claims_detailed", [])
        print(f"   🗑️ [Admin Recheck] Removing contaminated evidence from stored data...")
        purge_result = purge_false_evidence(
            data,
            ai_result["false_claims"],
            detailed_claims=rc_detailed_claims,
        )

        corrected_summary = ai_result.get("corrected_summary", "")
        corrected_pitch = ai_result.get("corrected_pitch", "")
        _kp_before_rc = len(data.get("key_people") or [])
        if corrected_summary:
            # Fact-checker produced corrected summary — apply directly
            corrected_summary = _apply_deterministic_corrections(corrected_summary, rc_detailed_claims)
            data["summary_text"] = _strip_tac_codes(corrected_summary)
            if corrected_pitch:
                corrected_pitch = _apply_deterministic_corrections(corrected_pitch, rc_detailed_claims)
                data["pitch_text"] = corrected_pitch
            data["ai_verification"]["verified"] = True
            data["ai_verification"]["false_claims"] = []
            data["ai_verification"]["note"] = "Corrected by independent GPT/Claude fact-checker inline"
            data["summary_cleaned"] = True
            # Clear stale washing flags (parity with Deep Scan pipeline)
            data["washing_flag"] = False
            data["washing_type"] = None
            print(f"   ✅ [Admin Recheck] Cleared washing flags (false claims corrected by fact-checker)")

            # Verify key_people survived the correction — it is a compatibility
            # field used by Admin/manual workflows and Buying Insight inputs.
            _kp_after_rc = len(data.get("key_people") or [])
            if _kp_after_rc != _kp_before_rc:
                print(f"   ⚠️ [Admin Recheck] key_people compatibility count changed during correction: {_kp_before_rc} → {_kp_after_rc} stakeholder(s)")
            elif _kp_after_rc == 0:
                print(f"   ℹ️ [Admin Recheck] key_people compatibility field is empty after correction")
            else:
                print(f"   ✅ [Admin Recheck] key_people compatibility field preserved: {_kp_after_rc} stakeholder(s)")

            callable_paths = _find_callables(data, f"{company_name}.pre_save")

            if callable_paths:

                print(f"   ⚠️ [Data Hygiene] Callable objects before save: {callable_paths[:30]}")

            data = _make_json_safe_payload(data, f"{company_name}.pre_save")

            storage.save_company_data(company_name, data, post_clean=True)
            storage.update_global_index_field(company_name, "ai_verified", True)
            clean_result = {"action": "cleaned", "claims_removed": len(ai_result["false_claims"]), "re_verified": True, "remaining_issues": []}
            print(f"   ✅ [Admin Recheck] Applied corrected summary from fact-checker for {company_name}")
        else:
            # Fallback: separate clean
            clean_result = _clean_company_data(company_name, data)

    # Step 5: Regenerate General Summary (runs after any cleaning so evidence is purged/verified)
    try:
        gs_start = time.time()
        data = _strip_callables_and_filter_source_lists(data, f"{company_name}.pre_general_summary")
        gs_text, gs_model = generate_general_summary(data, company_name)
        if gs_text:
            wc = len(gs_text.split())
            print(f"   ✅ [General Summary] {wc} words, model={gs_model} ({int((time.time()-gs_start)*1000)}ms)")

            # Fact-check General Summary against its own input payload
            data = _strip_callables_and_filter_source_lists(data, f"{company_name}.pre_general_summary_factcheck")
            gs_check = fact_check_general_summary(gs_text, data, company_name)
            if gs_check.get("corrected_summary"):
                gs_text = gs_check["corrected_summary"]
                gs_model = gs_check["corrected_model"] or gs_model
                print(f"   🔄 [GS Fact-Check] Replaced with corrected summary ({gs_check['check_time_ms']}ms)")

            data["general_summary"] = gs_text
            data["general_summary_model"] = gs_model
            data["general_summary_verified"] = gs_check.get("verified", True)
            data["general_summary_false_claims"] = [
                fc.get("claim", "") for fc in gs_check.get("false_claims", [])
            ]
        else:
            print(f"   ⚠️ [General Summary] Generation returned empty — skipping field")
    except Exception as gs_err:
        import traceback
        print(f"   ⚠️ [General Summary] Failed: {gs_err} — skipping, recheck continues")
        print(traceback.format_exc())

    # Save updated data with General Summary
    callable_paths = _find_callables(data, f"{company_name}.pre_save")
    if callable_paths:
        print(f"   ⚠️ [Data Hygiene] Callable objects before save: {callable_paths[:30]}")
    data = _make_json_safe_payload(data, f"{company_name}.pre_save")
    storage.save_company_data(company_name, data)

    # Step 6: Determine recommendation
    if ai_result.get("fact_check_skipped"):
        recommendation = "Deep scan recommended — fact-check could not run, summary is unverified"
    elif summary_result["risk"] == "high":
        recommendation = "Refresh database — high hallucination risk, full rescan needed"
    elif clean_result and clean_result.get("action") == "cleaned" and clean_result.get("re_verified"):
        recommendation = "OK — cleaned successfully"
    elif clean_result and clean_result.get("action") == "cleaned" and not clean_result.get("re_verified"):
        recommendation = "Deep scan recommended — cleaned but still has unverified claims"
    elif clean_result and clean_result.get("action") == "no_change":
        recommendation = "Deep scan recommended — cleaning could not resolve false claims"
    elif clean_result and clean_result.get("action") == "failed":
        recommendation = "Refresh database — cleaning failed, full rescan needed"
    elif not ai_result["verified"]:
        recommendation = "Deep scan recommended — false claims found by fact-check"
    elif summary_result["risk"] == "medium":
        recommendation = "Review manually"
    else:
        recommendation = "OK"

    result = {
        "company": company_name,
        "summary_check": summary_result,
        "pitch_check": pitch_result,
        "ai_verification": ai_result,
        "recommendation": recommendation,
    }

    if clean_result:
        result["clean_action"] = clean_result.get("action", "skipped")
        result["claims_removed"] = clean_result.get("claims_removed", 0)
        result["re_verified"] = clean_result.get("re_verified", False)
        result["remaining_issues"] = clean_result.get("remaining_issues", [])

    return result


@app.post("/admin/recheck-company/{company_name}")
async def recheck_company(company_name: str, request: Request):
    """
    Run reality check on an already-stored company's summary.
    Same procedure as recheck-all: heuristic check → AI fact-check → auto-clean if needed.
    Returns recommendation on whether deep scan or full refresh is needed.
    """
    verify_admin(request)
    data = storage.get_company_data(company_name)
    if not data:
        raise HTTPException(status_code=404, detail=f"Company '{company_name}' not found in database.")

    return _recheck_company_data(company_name, data)

@app.post("/admin/save-company-edit/{company_name}")
async def save_company_edit(company_name: str, request: Request):
    """
    Save manual edits to a company's data.
    Accepts a JSON body with editable fields (summary_text, pitch_text, general_summary).
    Manual edits are treated like normal data — the next scan will overwrite them.
    """
    verify_admin(request)
    data = storage.get_company_data(company_name)
    if not data:
        raise HTTPException(status_code=404, detail=f"Company '{company_name}' not found in database.")

    body = await request.json()

    # Only allow editing specific safe fields
    EDITABLE_FIELDS = ["summary_text", "pitch_text", "general_summary"]
    changes = {}
    for field in EDITABLE_FIELDS:
        if field in body and body[field] is not None:
            old_val = data.get(field, "")
            new_val = body[field].strip()
            if new_val != old_val:
                data[field] = new_val
                changes[field] = True

    if not changes:
        return {"status": "no_change", "company": company_name}

    # Mark as manually edited (informational only — no special protection)
    data["last_manual_edit"] = datetime.utcnow().isoformat() + "Z"
    data["last_manual_edit_fields"] = list(changes.keys())

    # Save to storage and update detailsUrl in global index so the
    # company modal fetches the edited version (not the stale old blob).
    callable_paths = _find_callables(data, f"{company_name}.pre_save")
    if callable_paths:
        print(f"   ⚠️ [Data Hygiene] Callable objects before save: {callable_paths[:30]}")
    data = _make_json_safe_payload(data, f"{company_name}.pre_save")
    new_url = storage.save_company_data(company_name, data)
    if new_url:
        storage.update_global_index_fields(company_name, {"detailsUrl": new_url})
    print(f"   ✏️ [Manual Edit] Saved edits for '{company_name}': {list(changes.keys())}")

    return {"status": "saved", "company": company_name, "changed_fields": list(changes.keys())}

@app.get("/admin/get-company/{company_name}")
async def get_company_for_edit(company_name: str, request: Request):
    """Returns full company data for inline editing in the admin review panel."""
    verify_admin(request)
    data = storage.get_company_data(company_name)
    if not data:
        raise HTTPException(status_code=404, detail=f"Company '{company_name}' not found in database.")
    return data

@app.post("/admin/recalculate-financial")
async def recalculate_financial_index(request: Request):
    """
    Recalculate financial_index from stored sub_scores for ALL companies.
    Compares computed value against the GLOBAL INDEX (landing page) value,
    not the company file, to catch any out-of-sync state.
    """
    verify_admin(request)

    # 1. Load global index — this is what the landing page actually reads
    index_data = storage.retrieve_latest_data("index/summary_list")
    if not index_data or not isinstance(index_data, list):
        print(f"   [Recalc] ERROR: Could not load global index. Got: {type(index_data)}")
        return {"status": "error", "detail": "Could not load global index"}

    index_lookup = {item.get("companyName"): item for item in index_data}
    print(f"   [Recalc] Loaded global index with {len(index_data)} entries")

    updated = 0
    skipped = 0
    changes = []

    for name, index_entry in index_lookup.items():
        if not name:
            continue
        data = storage.get_company_data(name)
        if not data:
            skipped += 1
            continue

        sub_scores = migrate_financial_sub_scores(data.get("financial_sub_scores"))
        if not sub_scores or not isinstance(sub_scores, dict):
            skipped += 1
            continue

        has_real = any(v is not None for v in sub_scores.values())
        if not has_real:
            skipped += 1
            continue

        data["financial_sub_scores"] = sub_scores
        new_index = compute_financial_index(sub_scores)
        old_index_in_file = data.get("financial_index")
        old_index_on_landing = index_entry.get("financial_index")

        # Update company file if it differs
        if new_index != old_index_in_file:
            data["financial_index"] = new_index
            callable_paths = _find_callables(data, f"{name}.pre_save")
            if callable_paths:
                print(f"   ⚠️ [Data Hygiene] Callable objects before save: {callable_paths[:30]}")
            data = _make_json_safe_payload(data, f"{name}.pre_save")
            storage.save_company_data(name, data)

        # Compare against LANDING PAGE value — this is what matters
        if new_index != old_index_on_landing:
            index_entry["financial_index"] = new_index
            changes.append({
                "company": name,
                "old": old_index_on_landing,
                "new": new_index,
                "delta": new_index - (old_index_on_landing or 0),
                "sub_scores": sub_scores
            })
            updated += 1
            print(f"   [Recalc] {name}: landing={old_index_on_landing} file={old_index_in_file} -> {new_index}")
        else:
            skipped += 1

    # 2. Write updated global index back in one shot
    if changes:
        print(f"   [Recalc] Writing global index with {updated} updates...")
        with storage._index_lock:
            new_url = storage._put("index/summary_list.json", index_data)
            print(f"   [Recalc] Wrote updated index: {new_url}")
            if new_url:
                storage._prune_old_versions("index/summary_list", keep=3)
            else:
                print(f"   [Recalc] ERROR: _put returned None - write failed!")
    else:
        print(f"   [Recalc] No changes needed — all landing page scores already match.")

    changes.sort(key=lambda c: abs(c["delta"]), reverse=True)

    return {
        "status": "done",
        "updated": updated,
        "skipped": skipped,
        "total": len(index_lookup),
        "changes": changes
    }


def _refresh_proposals_for_company(company_name: str, data: dict) -> dict:
    """
    Re-generate WHY & WHAT proposals from existing scan data.
    Uses a lightweight, focused prompt — no full rescan needed.
    Returns dict with status, old/new proposals.
    """
    import time as _time

    # Gather existing analysis context from stored data
    knowledge_context = get_evolving_framework()
    intent_breakdown = json.dumps(data.get("intent_breakdown", {}))
    reality_checklist = data.get("reality_checklist", [])
    summary_text = data.get("summary_text", "")
    pitch_text = data.get("pitch_text", "")
    reality_score = data.get("reality_score", 0)
    evidence_links = _analysis_evidence_links(data.get("evidence_links", []))
    evidence_list = '\n'.join([
        f"[{l.get('claim_tier','D')}]{'['+l.get('entity_scope','')+']' if l.get('entity_scope','unknown') != 'unknown' else ''} {l.get('title','Unknown')}: {l.get('source_quote') or l.get('snippet','')}"
        for l in evidence_links[:20]
    ])
    proposal_guardrails = _shared_prompt_guardrails(include_diagnostic=True, include_foresight=True, include_claim_tier=True)

    # Build the weakest dimensions from reality checklist
    negative_findings = [
        item for item in reality_checklist
        if item.get("sentiment") == "negative" and is_generation_eligible_source(item)
    ]
    negative_summary = "\n".join([f"- {item.get('query', '')}: {item.get('snippet', 'No detail')}" for item in negative_findings[:6]])

    prompt = f"""You are a strategic consulting advisor analyzing {company_name}.

### KNOWLEDGE BASE (KB)
{knowledge_context}

### SHARED EVIDENCE / DIAGNOSIS GUARDRAILS
{proposal_guardrails}

### EXISTING ANALYSIS CONTEXT
Company: {company_name}
Reality Score: {reality_score}/100
Archetype is audit-only and intentionally omitted from this writing context. Do not write labels such as Thriving, Modernizing, Cash Cow, or At Risk in WHY or WHAT.

Intent Breakdown (Scanner 1):
{intent_breakdown}

Key Negative Findings (Scanner 2):
{negative_summary}

Evidence Sources:
{evidence_list}

Existing Summary:
{summary_text}

Existing Pitch:
{pitch_text}

### YOUR TASK
Generate exactly 2 paired activation items based on the analysis above.

WHY — customer value and consequence:
  - Each WHY must connect a detected customer challenge/problem to end-value, future strategy, cost of inaction, risk, or missed opportunity.
  - Tone: Executive-level, outcome-oriented. Not technical jargon.
  - Length: 2-3 sentences each.

WHAT_TO_DO — proposed activity or deliverable:
  - Each WHAT_TO_DO must propose a concrete activity, diagnostic, workshop, analysis, decision model, roadmap, readiness assessment, or deliverable.
  - It must answer: "What could be done next?"
  - Tone: Tangible, verifiable, grounded in this company's context.
  - Length: 1-2 sentences each.

WHAT_PROGRESS_SHOWS — observable proof of progress:
  - Describe what progress would look like after the activity.
  - This can be qualitative or measurable, but do not invent numeric KPI targets.
  - Length: 1-2 sentences each.

Rules:
  - WHY1 pairs with WHAT_TO_DO1 and WHAT_PROGRESS_SHOWS1. WHY2 pairs with WHAT_TO_DO2 and WHAT_PROGRESS_SHOWS2.
  - Draw ONLY from the weakest KB dimensions visible in the analysis.
  - Never generic — each must reference something specific from the evidence or diagnosis.
  - These are proposals, not promises — frame as validation or discovery where evidence is narrow.
  - When evidence confidence is low or narrow, frame as "validate whether..." rather than "fix...".
  - Do not use strong KB labels unless the evidence threshold in the shared guardrails is met.

### GUARDRAILS
- NEVER fabricate evidence, people, events, partnerships, or metrics not present in the analysis above.
- NEVER invent temporal claims ("recently announced", "just launched", "this quarter").

### OUTPUT
Respond with valid JSON only:
{{"activation_pairs": [{{"theme_type": "current_status", "sales_use": "current_status", "theme": "...", "why": "...", "what": "same as what_to_do", "what_to_do": "...", "what_progress_shows": "..."}}, {{"theme_type": "foresight_driven", "sales_use": "foresight_driven", "theme": "...", "why": "...", "what": "same as what_to_do", "what_to_do": "...", "what_progress_shows": "..."}}], "why_proposals": ["WHY texts"], "what_proposals": ["WHAT_TO_DO texts"], "what_progress_proposals": ["WHAT_PROGRESS_SHOWS texts"]}}"""

    old_why = data.get("why_proposals", [])
    old_what = data.get("what_proposals", [])

    # --- Model chain: GPT-5.5 → Claude Sonnet 4.6 ---
    response_text = ""
    model_used = ""
    start = _time.time()

    for _model in ("gpt-5.5", "claude-sonnet-4-6"):
        try:
            print(f"   🔄 [Refresh Proposals] {company_name}: Attempting {_model}...")
            response_text, model_used = call_json_model_chain(prompt, (_model,), max_tokens=2200, timeout=75)
            ms = int((_time.time() - start) * 1000)
            print(f"   ✅ [Refresh Proposals] {company_name}: {model_used} complete ({ms}ms)")
            break
        except Exception as model_err:
            ms = int((_time.time() - start) * 1000)
            print(f"   ❌ [Refresh Proposals] {company_name}: {_model} failed ({ms}ms): {model_err}")

    if not response_text:
        return {"company": company_name, "status": "failed", "reason": "All models failed or no API key."}

    payload = extract_json_from_text(response_text)
    if not payload:
        return {"company": company_name, "status": "failed", "reason": "Could not parse JSON from model response."}

    result_json = json.loads(payload, strict=False)
    new_why = strip_archetype_labels_from_text(result_json.get("why_proposals", []))
    new_what = strip_archetype_labels_from_text(result_json.get("what_proposals", []))
    normalized_activation = _normalize_activation_payload(result_json, {
        "why_proposals": old_why,
        "what_proposals": old_what,
        "what_progress_proposals": data.get("what_progress_proposals", []),
        "activation_pairs": data.get("activation_pairs", []),
    })
    new_why = normalized_activation.get("why_proposals") or new_why
    new_what = normalized_activation.get("what_proposals") or new_what
    new_progress = normalized_activation.get("what_progress_proposals") or []
    new_pairs = normalized_activation.get("activation_pairs") or []

    if not new_why or not new_what or not isinstance(new_why, list) or not isinstance(new_what, list):
        return {"company": company_name, "status": "failed", "reason": "Model returned invalid proposal structure."}

    # Save updated proposals
    data["why_proposals"] = new_why
    data["what_proposals"] = new_what
    data["what_progress_proposals"] = new_progress
    if new_pairs:
        data["activation_pairs"] = new_pairs
    callable_paths = _find_callables(data, f"{company_name}.pre_save")
    if callable_paths:
        print(f"   ⚠️ [Data Hygiene] Callable objects before save: {callable_paths[:30]}")
    data = _make_json_safe_payload(data, f"{company_name}.pre_save")
    storage.save_company_data(company_name, data)

    ms = int((_time.time() - start) * 1000)
    print(f"   ✅ [Refresh Proposals] {company_name}: Saved new WHY/WHAT proposals ({model_used}, {ms}ms)")

    return {
        "company": company_name,
        "status": "refreshed",
        "model_used": model_used,
        "old_why": old_why,
        "old_what": old_what,
        "new_why": new_why,
        "new_what": new_what,
        "new_what_progress": new_progress,
        "new_activation_pairs": new_pairs,
    }


@app.post("/admin/refresh-proposals/{company_name}")
async def refresh_proposals_single(company_name: str, request: Request):
    """Re-generate WHY & WHAT proposals for a single company from existing data."""
    verify_admin(request)
    data = storage.get_company_data(company_name)
    if not data:
        raise HTTPException(status_code=404, detail=f"Company '{company_name}' not found in database.")
    return _refresh_proposals_for_company(company_name, data)


@app.post("/admin/refresh-proposals-all")
async def refresh_proposals_all(request: Request):
    """Re-generate WHY & WHAT proposals for ALL companies from existing data."""
    verify_admin(request)
    companies = storage.get_all_companies_summary()
    results = []
    refreshed = 0
    failed = 0

    for company in companies:
        name = company.get("companyName", "")
        if not name:
            continue
        data = storage.get_company_data(name)
        if not data:
            continue

        result = _refresh_proposals_for_company(name, data)
        results.append(result)
        if result["status"] == "refreshed":
            refreshed += 1
        else:
            failed += 1

    return {
        "status": "done",
        "total": len(results),
        "refreshed": refreshed,
        "failed": failed,
        "results": results,
    }


@app.post("/admin/enrich-stakeholders/{company_name}")
async def enrich_stakeholders_single(company_name: str, request: Request):
    """Re-run LinkedIn enrichment for a single company's key_people from existing data."""
    verify_admin(request)
    data = storage.get_company_data(company_name)
    if not data:
        raise HTTPException(status_code=404, detail=f"Company '{company_name}' not found in database.")

    key_people = data.get("key_people", [])
    if not key_people:
        return {"company": company_name, "status": "skipped", "reason": "No key_people stored for this company."}

    from stakeholder_intel import enrich_key_people
    start = time.time()
    enriched = enrich_key_people(company_name, key_people)
    ms = int((time.time() - start) * 1000)

    # Filter out people who have left the company
    departed = [p for p in enriched if p.get("linkedin_insights", {}).get("still_at_company") is False]
    if departed:
        departed_names = ", ".join(p.get("name", "?") for p in departed)
        print(f"   🚫 [Stakeholder Filter] Removing {len(departed)} departed stakeholder(s): {departed_names}")
        enriched = [p for p in enriched if p.get("linkedin_insights", {}).get("still_at_company") is not False]

    data["key_people"] = enriched
    callable_paths = _find_callables(data, f"{company_name}.pre_save")
    if callable_paths:
        print(f"   ⚠️ [Data Hygiene] Callable objects before save: {callable_paths[:30]}")
    data = _make_json_safe_payload(data, f"{company_name}.pre_save")
    storage.save_company_data(company_name, data)

    found_count = sum(1 for p in enriched if p.get("linkedin_insights", {}).get("found"))
    return {
        "company": company_name,
        "status": "enriched",
        "total_people": len(enriched),
        "found_insights": found_count,
        "duration_ms": ms,
        "people": [
            {
                "name": p.get("name"),
                "role": p.get("role"),
                "found": p.get("linkedin_insights", {}).get("found", False),
                "summary": p.get("linkedin_insights", {}).get("summary", ""),
            }
            for p in enriched
        ],
    }


@app.post("/admin/enrich-stakeholders-all")
async def enrich_stakeholders_all(request: Request):
    """Re-run LinkedIn enrichment for ALL companies' key_people."""
    verify_admin(request)
    companies = storage.get_all_companies_summary()
    results = []
    enriched_count = 0
    skipped_count = 0

    for company in companies:
        name = company.get("companyName", "")
        if not name:
            continue
        data = storage.get_company_data(name)
        if not data:
            continue

        key_people = data.get("key_people", [])
        if not key_people:
            results.append({"company": name, "status": "skipped", "reason": "No key_people"})
            skipped_count += 1
            continue

        try:
            from stakeholder_intel import enrich_key_people
            enriched = enrich_key_people(name, key_people)

            # Filter out people who have left the company
            departed = [p for p in enriched if p.get("linkedin_insights", {}).get("still_at_company") is False]
            if departed:
                departed_names = ", ".join(p.get("name", "?") for p in departed)
                print(f"   🚫 [Stakeholder Filter] Removing {len(departed)} departed stakeholder(s) from {name}: {departed_names}")
                enriched = [p for p in enriched if p.get("linkedin_insights", {}).get("still_at_company") is not False]

            data["key_people"] = enriched
            callable_paths = _find_callables(data, f"{name}.pre_save")
            if callable_paths:
                print(f"   ⚠️ [Data Hygiene] Callable objects before save: {callable_paths[:30]}")
            data = _make_json_safe_payload(data, f"{name}.pre_save")
            storage.save_company_data(name, data)

            found = sum(1 for p in enriched if p.get("linkedin_insights", {}).get("found"))
            results.append({"company": name, "status": "enriched", "total": len(enriched), "found": found})
            enriched_count += 1
        except Exception as e:
            results.append({"company": name, "status": "failed", "reason": str(e)})

    return {
        "status": "done",
        "total": len(results),
        "enriched": enriched_count,
        "skipped": skipped_count,
        "results": results,
    }


@app.post("/admin/recheck-all")
async def recheck_all_companies(request: Request):
    """Run reality check on ALL stored companies. Flags issues and auto-cleans them."""
    verify_admin(request)
    companies = storage.get_all_companies_summary()
    flagged = []
    cleaned_results = []
    clean = 0

    for company in companies:
        name = company.get("companyName", "")
        if not name:
            continue
        data = storage.get_company_data(name)
        if not data:
            continue

        result = _recheck_company_data(name, data)

        summary_risk = result["summary_check"]["risk"]
        ai_verified = result["ai_verification"]["verified"]
        is_flagged = summary_risk in ("medium", "high") or not ai_verified

        if is_flagged:
            flag_entry = {
                "companyName": name,
                "risk": summary_risk,
                "score": result["summary_check"]["score"],
                "flags": result["summary_check"]["flags"],
                "ai_verified": ai_verified,
                "false_claims": result["ai_verification"]["false_claims"],
                "recommendation": result["recommendation"],
                "summary_preview": (data.get("summary_text", "") or "")[:200],
            }

            if result.get("clean_action"):
                flag_entry["clean_action"] = result["clean_action"]
                flag_entry["claims_removed"] = result.get("claims_removed", 0)
                flag_entry["re_verified"] = result.get("re_verified", False)
                if result["clean_action"] == "cleaned":
                    cleaned_results.append({
                        "companyName": name,
                        "claims_removed": result["claims_removed"],
                        "re_verified": result["re_verified"],
                    })

            flagged.append(flag_entry)
        else:
            clean += 1

    return {
        "total_checked": len(companies),
        "clean": clean,
        "flagged_count": len(flagged),
        "flagged": flagged,
        "auto_cleaned": len(cleaned_results),
        "cleaned": cleaned_results,
    }


def _clean_company_data(company_name: str, data: dict) -> dict:
    """
    Shared cleaning logic for admin recheck: purge false evidence, full re-synthesis, re-verify, save.
    Uses the SAME synthesis prompt, Knowledge Base, tone, and instructions as the Deep Scanning pipeline
    to ensure 100% consistency between scan-time and cleaning-time AI Diagnosis and Pitch generation.

    Returns dict with action result (skipped/failed/cleaned).
    """
    ai_verification = data.get("ai_verification", {})
    # Support both new field name (false_claims) and legacy (unsupported_claims)
    false_claims = ai_verification.get("false_claims") or ai_verification.get("unsupported_claims", [])
    detailed_claims = ai_verification.get("false_claims_detailed") or ai_verification.get("unsupported_claims_detailed", [])

    if ai_verification.get("verified", True) or not false_claims:
        return {
            "company": company_name,
            "action": "skipped",
            "reason": "No false claims found — summary is already verified.",
        }

    print(f"   🧹 [Clean] Cleaning {len(false_claims)} false claim(s) from {company_name} using full re-synthesis (same as Deep Scan)...")

    # Step 1: Purge contaminated evidence sources BEFORE re-synthesis
    purge_result = purge_false_evidence(data, false_claims, detailed_claims=detailed_claims)

    # Step 2: Build the SAME synthesis prompt used in Deep Scanning
    # This ensures 100% identical logic, tone, Knowledge Base terminology, and instructions.
    knowledge_context = get_evolving_framework()
    intent_narrative = json.dumps(data.get("intent_breakdown", {}))
    clean_evidence_links = data.get("evidence_links", [])
    clean_verified = _analysis_evidence_links(clean_evidence_links)
    evidence_list = '\n'.join([
        f"[{l.get('claim_tier','D')}]{'['+l.get('entity_scope','')+']' if l.get('entity_scope','unknown') != 'unknown' else ''} {l.get('title','Unknown')}: {l.get('source_quote') or l.get('snippet','')}"
        for l in clean_verified[:20]
    ])
    # Run contradiction detection on the same evidence the synthesizer will see.
    # If quantitative disagreements exist (e.g. narrow HR thesis vs broad
    # company interview), surface them as a structured block so the prompt's
    # SCOPE & PROPORTIONALITY rules force the model to address both sides.
    contradictions_block = _build_contradictions_block(clean_verified, company_name)

    confidence = data.get("confidence_score", 50)
    fin_score = data.get("financial_index")
    fin_for_arch = fin_score if fin_score is not None else data.get("intent_score", 0)
    washing_flag = data.get("washing_flag", False)
    washing_type = data.get("washing_type")

    washing_context = ""
    if washing_flag:
        washing_context = f"CRITICAL: The forensic scanner DETECTED {washing_type or 'WASHING'}. High ambition narrative but low operational evidence."

    financial_context = ""
    if fin_score is not None:
        findings = data.get("financial_findings", [])
        findings_text = '; '.join(
            f.get('assessment', '') if isinstance(f, dict) else ''
            for f in findings[:5]
        ) if findings else 'No specific findings'
        financial_context = f"FINANCIAL HEALTH CONTEXT (do NOT cite numeric scores — the index is displayed separately): {findings_text}. You may reference financial health qualitatively (e.g. 'declining revenue', 'strong profitability') but never mention specific index numbers."

    doc_context = ""
    uploaded_doc_names = data.get("uploaded_docs", [])
    if uploaded_doc_names:
        doc_context = f"NOTE: Analysis includes confidential internal documents: {', '.join(uploaded_doc_names)}. Treat this as high-fidelity intent data."

    foresight_context = ""
    stored_foresight = data.get("strategic_foresight") or {}
    if stored_foresight:
        try:
            from strategic_foresight import format_foresight_for_synthesis
            company_domain = (data.get("domain") or data.get("industry") or "").strip()
            foresight_context = format_foresight_for_synthesis(stored_foresight, company_name, company_domain)
        except Exception as fs_err:
            print(f"   ⚠️ [Clean Re-Synthesis] Foresight context unavailable: {fs_err}")

    # Build false claims text with verified corrections for the re-synthesis prompt
    _detail_map = {}
    for dc in detailed_claims:
        _detail_map[dc.get("claim", "")] = dc.get("correction", "")
    _claims_lines = []
    for c in false_claims:
        line = f"- {c}"
        corr = _detail_map.get(c, "")
        if corr:
            line += f"\n  \u2192 VERIFIED CORRECTION: {corr}"
        _claims_lines.append(line)
    _false_claims_text = chr(10).join(_claims_lines)

    # Same synthesis prompt as Deep Scanning (main.py scan pipeline)
    synthesis_guardrails = _shared_prompt_guardrails(include_diagnostic=True, include_foresight=True, include_claim_tier=True)
    synthesis_prompt = f"""You are a senior organizational analyst conducting a forensic modernization assessment.
You are conservative and evidence-bound. You would rather say "insufficient evidence" than risk a wrong claim. Your credibility depends on never overstating.
Your task: synthesize scanner evidence into a precise, evidence-grounded diagnosis using the Knowledge Base framework below.

### SHARED EVIDENCE / DIAGNOSIS / FORESIGHT GUARDRAILS
{synthesis_guardrails}

METHODOLOGY: Read ALL evidence first. Score ONLY what evidence supports. Write ONLY what scores justify.
If evidence is thin, say so — a short honest analysis beats a long fabricated one.

### ABSOLUTE RULES (repeated throughout for emphasis)
1. NEVER fabricate findings — if evidence is thin, SAY SO.
2. NEVER invent organizational or people problems (leadership gaps, succession issues, morale claims, unfilled roles).
3. Every claim must trace to specific evidence in the SCAN DATA below. No evidence = no claim.
4. Do NOT extrapolate small signals into sweeping assessments (e.g., a Jira mention in a job ad does NOT reveal corporate strategy).
5. SCOPE & SOURCE QUALIFICATION — CRITICAL:
   - NARROW SCOPE: When an evidence item describes a limited population (a master's thesis, a single-department survey, a regional pilot, a study of N participants), you MUST include a scope qualifier in the same sentence (e.g., "in a survey of HR personnel", "in a 100-respondent study", "according to a master's thesis"). Never present a narrow-scope statistic as a company-wide truth.
   - ANTI-EXTRAPOLATION: NEVER extrapolate a narrow-scope finding into a macro-trend or section-level diagnosis. If a survey only covers the HR department, you are FORBIDDEN from writing sweeping claims like "enterprise adoption lags significantly", "the company struggles with AI", "internal landscape presents a material vulnerability", "AI Readiness remains highly fragmented", or "Pilot Purgatory across the firm". State the narrow fact exactly as it is, scoped to its actual population, without macro-commentary.
   - SELF-REPORTED DATA: If evidence comes from the company's own representatives or publications and provides CONCRETE operational metrics (e.g., "thousands of weekly users", "30% downtime reduction"), you must ingest and report these metrics, but qualify the source ("The company reports..."). Do not treat them as independently verified, but DO NOT omit them. Marketing fluff without concrete numbers ("AI-first company") may be skipped.
   - ENTITY SCOPE: If evidence carries entity_scope="local_legal_entity", "subsidiary", "former_name", or "parent_owner", qualify it in the same sentence and do not present it as unqualified company-wide evidence. Parent/owner evidence supports acquisition or ownership context only. Local legal-entity financial data supports that legal entity unless group-level evidence corroborates it.
6. BALANCING CONTRADICTIONS & PROPORTIONALITY — CRITICAL:
   - If a narrow-scope independent source contradicts a broad self-reported metric on the same topic, briefly state both side-by-side (e.g., "While the company reports thousands of weekly users on its internal AI tool, a departmental survey of HR personnel found only 9% report frequent use").
   - PROPORTIONALITY: You are diagnosing a multi-billion euro enterprise. Keep narrow departmental statistics confined to a single contextualised sentence within the relevant section. Do not let a single departmental survey dominate the technology, AI Readiness, or operational narrative of a global firm. Address the contradiction factually, weight it against company-wide evidence, and move on.
7. ANTI-EXTRAPOLATION ENFORCEMENT — Reject any sentence that takes a finding from a narrow-scope source (single department, single region, study of N participants, master's thesis) and uses generalising language to apply it to the company as a whole. Generalising language includes phrases that turn the finding into a corporate-wide diagnosis: any wording that expands the population beyond what the source covers. The conceptual rule overrides any specific phrase list.
8. PRECISION CLAIM GATE — CRITICAL:
   - Do NOT write exact financial numbers, order backlog, revenue, margin, guidance, growth %, order value, ownership %, headcount, customer count, or investment amount unless that exact value appears in generation-eligible evidence above or in the explicit financial context.
   - Do NOT name partnerships, joint ventures, named customers/suppliers, technology cooperation partners, product launches, or acquisition details unless the source title or source quote explicitly contains the named parties.
   - Do NOT write future dates, launch dates, deadlines, roadmap dates, or "announced in <month/year>" unless the source quote contains the same date or year.
   - Ownership percentages require official registry, company, investor, stock-exchange, or audited financial-report evidence. Otherwise write only broad ownership context.
   - Never use prior knowledge, training data, or plausible industry memory to add precision. If the evidence packet lacks the value/name/date, omit it.
   - When evidence is sparse, write "public evidence is limited" or "insufficient verified public evidence" instead of filling the gap with precise-sounding claims.

### PRIMARY FRAMEWORK (KNOWLEDGE BASE — MANDATORY)
{knowledge_context}

You MUST use the terminology and criteria defined in the Knowledge Base above.
Every claim in the summary MUST trace back to specific evidence AND map to a Knowledge Base dimension.
Do NOT invent categories — use ONLY Knowledge Base vocabulary.
NEVER use internal codes (TAC-xxx, DIM-xxx, etc.) — always write the full concept name.
Do NOT write adaptability archetype labels in diagnostic_summary, evidence_based_risks, or any prose.

### SCAN DATA
Target: {company_name}
Financial Health Index: {fin_for_arch} ({"actual financial index" if fin_score is not None else "fallback: using intent score"})
Summary Confidence: {confidence}/100

Evidence Found: {evidence_list}
Intent Data: {intent_narrative}
{doc_context}
{washing_context}
{financial_context}
{foresight_context}

{contradictions_block}
### ANALYSIS PROTOCOL

STEP 1 — REALITY SCORE (evaluate before writing anything):
Score each Knowledge Base dimension (0-20) based on VERIFIED EVIDENCE ONLY:
  - Operating Model: Evidence of modern practices (DevOps, platform teams, stream-aligned)? Or legacy/waterfall/silos?
  - Strategy: Clear digital/modernization strategy with visible execution? Or vague/absent?
  - Culture: Learning culture, psychological safety? Evidence of open communication or blameless post-mortems? Or hierarchy/control/fear?
  - Service Portfolio: Product-led, API-first, composable architecture? Or monolithic/legacy?
  - AI Readiness: Concrete AI/ML adoption with measurable outcomes? Or just buzzwords/pilot purgatory?

Scoring rules:
  - No evidence for a dimension = score 10 (neutral). Never score generously without evidence.
  - Evidence of aspiration (website claims, press releases) without execution evidence = score 8-12, not higher.
  - Sum the 5 dimensions → 'reality_score' (0-100).
  - Do NOT evaluate intent_score or financial_index — those are immutable pipeline outputs.

STEP 2 — ARCHETYPE AUDIT ONLY (mechanical, from scores):
For internal audit only, derive an 'archetype' JSON field from YOUR reality_score + Financial Health Index ({fin_for_arch}):
  - reality_score >= 50 AND Financial Health Index >= 50 → "Thriving"
  - reality_score >= 50 AND Financial Health Index < 50 → "Modernizing"
  - reality_score < 50 AND Financial Health Index >= 50 → "Cash Cow"
  - reality_score < 50 AND Financial Health Index < 50 → "At Risk"
This is a mechanical derivation for log comparison only. NEVER mention the archetype label in diagnostic_summary, pitch, why_proposals, what_proposals, or evidence_based_risks.

STEP 3 — AI DIAGNOSIS SUMMARY:
Write a diagnostic narrative that reads like an expert assessment, not a template:
  - Structure: exactly 3-4 core paragraphs, each covering a distinct KB dimension (e.g. overall position
    & operating model / strategy & culture / service portfolio & AI readiness / financial context).
    Separate paragraphs with \n\n in the JSON string — this is required for rendering.
  - Do not add foresight implications here. External pressures belong only in the later Strategic Synthesis / Activation call.
  - Target length: 250-375 words.
  - First sentence: State the company's current-reality position in plain business language without using an archetype label.
  - Body: Map 3-4 key findings to specific KB dimensions. For each finding, connect the evidence
    to the KB pattern it reveals.
  - Use KB terminology naturally — but only where the evidence supports the pattern.
  - If confidence < 50, state it: "Limited public evidence constrains this assessment."
  - If washing detected, name it: "{washing_type or 'Modernization Washing'}" with the specific gap.
  - Do NOT cite numeric scores in the text — scores display separately in the UI.
  - Do NOT fabricate findings. If evidence is sparse, acknowledge gaps rather than inventing patterns.
  - PRECISION CLAIMS: exact euro amounts, percentages, order books, dates, named partnerships, named product launches, named customers/suppliers, and ownership shares are allowed only when the same value/name/date appears in generation-eligible evidence or explicit financial context above. If not, use cautious non-specific wording or state that public evidence is limited.
  - TEMPORAL PRECISION: When evidence contains the same metric for different periods (e.g. Q1 vs full-year, 2024 vs 2025), always use the most recent FULL-YEAR / ANNUAL figure — not interim or quarterly snapshots. If only a quarterly figure is available, label it explicitly (e.g. "as of Q1 2025"). Never present a quarterly number as if it were the annual result. Do not write that a metric is unpublished or unavailable if the evidence or financial context contains that metric for the latest annual period.
  - ORGANIZATIONAL CLAIMS PROHIBITION: NEVER claim or imply leadership gaps, unfilled executive roles, succession planning issues, management vacancies, or organizational restructuring unless the evidence EXPLICITLY states this. The absence of a person in scanner results does NOT mean the role is vacant — it means the scanner didn't find them. Do NOT invent risk factors about people, roles, or organizational structure to add analytical depth. If the evidence doesn't mention an organizational problem, the problem does not exist for this analysis.
  - Do NOT write these archetype labels in prose: Thriving, Modernizing, Cash Cow, At Risk.
  - CRITICAL SEPARATION: This section is strictly neutral diagnosis. Do NOT use sales language, calls-to-action, persuasion, or proposal framing here.
  - EVIDENCE DIMENSION BOUNDARY: Financial evidence may establish financial condition, capacity or investment context only. It cannot establish operating-model maturity, architecture, AI/data capability, service delivery, strategy execution or culture. Diagnose those dimensions only from promoted non-financial jury evidence; otherwise state that evidence is insufficient.
  - HISTORICAL EVIDENCE: Previous links and snippets are discovery inputs only. Use them only after current URL, entity, content and claim-support validation.
  - FORESIGHT CONTRACT: Validate foresight material as company-neutral macro context. Any connection to this company is a separate, clearly modal inference grounded in company evidence; foresight is never proof of current company behavior.

STEP 4 — PITCH (90-110 words):
A direct cold-outreach message to a C-level executive at {company_name}:
  - Open with a specific insight from the analysis that would make them stop and read.
  - Derive 1-2 CONCRETE PROPOSALS targeting the company's weakest KB dimensions.
  - The proposals must feel specific to THIS company's situation, not generic consulting templates.
  - Do NOT use "Forensic Modernization Audit" or any generic service names.
  - Tone: Professional, direct, evidence-led. Every claim in the pitch must be verifiable from the scan data. The recipient should feel understood, not sold to.
  - Structure as exactly 3 short paragraphs separated by \n\n (opening hook / proposals / closing CTA).

STEP 5 — EVIDENCE-BASED RISKS:
Generate 2-4 neutral risk bullets in a JSON array named evidence_based_risks.
  - Each risk must be diagnostic, evidence-backed, and free of sales/promotional language.
  - No solutions or CTA in this section; just what risk exists and why.

STEP 6 — IMPACT PROPOSALS (derived from Steps 1-4):
Generate 1-2 paired WHY / WHAT_TO_DO / WHAT_PROGRESS_SHOWS items.
If evidence strongly supports 2 distinct weaknesses, produce 2 pairs. If evidence is limited, produce 1 pair. One strong evidence-based proposal beats two fabricated ones.

WHY — customer value and consequence:
  - Each WHY must connect a detected customer challenge/problem to end-value, future strategy, cost of inaction, risk, or missed opportunity.
  - Tone: Executive-level, outcome-oriented. Not technical jargon.
  - Length: 1-2 sentences each.

WHAT_TO_DO — proposed activity or deliverable:
  - Each WHAT_TO_DO must propose a concrete activity, diagnostic, workshop, analysis, decision model, roadmap, readiness assessment, or deliverable.
  - It must answer: "What could be done next?"
  - Tone: Tangible, verifiable, grounded in this company's context.
  - Length: 1-2 sentences each.

WHAT_PROGRESS_SHOWS — observable proof of progress:
  - Describe what progress would look like after the activity.
  - This can be qualitative or measurable, but do not invent numeric KPI targets.
  - Length: 1-2 sentences each.

Rules:
  - WHY1 pairs with WHAT_TO_DO1 and WHAT_PROGRESS_SHOWS1. If 2 pairs: WHY2 pairs with WHAT_TO_DO2 and WHAT_PROGRESS_SHOWS2.
  - Draw ONLY from the weakest KB dimensions scored in Step 1 that have ACTUAL EVIDENCE.
  - Never generic — each must reference something specific from the evidence or diagnosis.
  - These are proposals, not promises — frame as validation or discovery where evidence is narrow.

### GUARDRAILS
- NEVER fabricate evidence, people, events, partnerships, or metrics not present in the scan data.
- NEVER invent temporal claims ("recently announced", "just launched", "this quarter").
- NEVER add precise figures, dates, product names, ownership percentages, or partner names from model memory. The evidence packet must contain them.
- NEVER extrapolate small signals into sweeping assessments (e.g., a tool mentioned in a job ad does NOT reveal corporate strategy).
- If a KB dimension has no supporting evidence, score it neutrally and acknowledge the gap — do not speculate.
- Every factual claim in the summary must be traceable to a specific piece of evidence above.
- Every risk must be traceable to promoted evidence from the relevant non-financial jury or to an explicit structured financial fact. Do not create a non-financial risk from financial performance alone.

### OUTPUT
Respond with valid JSON only. Required fields:
{{"diagnostic_summary": "...", "evidence_based_risks": ["..."], "commercial_pitch": "...", "activation_pairs": [{{"theme_type": "current_status|foresight_driven|proactive_opportunity", "sales_use": "current_status|foresight_driven|proactive_opportunity", "theme": "...", "why": "...", "what": "same as what_to_do", "what_to_do": "...", "what_progress_shows": "..."}}], "why_proposals": ["WHY texts"], "what_proposals": ["WHAT_TO_DO texts"], "what_progress_proposals": ["WHAT_PROGRESS_SHOWS texts"], "reality_score": <int 0-100>, "archetype": "<Thriving|Modernizing|Cash Cow|At Risk>"}}
Note: diagnostic_summary must contain \n\n between paragraphs (3-4 core paragraphs plus one optional final foresight implication paragraph). commercial_pitch must also contain \n\n between its 3 paragraphs.
The archetype field is audit-only and must not appear in diagnostic_summary, commercial_pitch, why_proposals, what_proposals, or evidence_based_risks.
Note: why_proposals contains WHY texts, what_proposals contains WHAT_TO_DO texts, and what_progress_proposals contains WHAT_PROGRESS_SHOWS texts. Preserve the same order across all three arrays.

### CRITICAL CONTEXT — FACT-CHECK CORRECTIONS
The previous synthesis contained false claims detected by an independent fact-checker.
The following claims were FALSE and must NOT appear in your output:
{_false_claims_text}

Where a VERIFIED CORRECTION is provided above, use that exact corrected fact instead of the false claim.
For numeric/qualifier errors, use the exact verified value — do not round, approximate, or hedge.
The contaminated evidence sources have been removed. You are now working with CLEANED evidence only.
Generate a completely fresh synthesis. Do NOT reference or re-introduce any of the false claims listed above.

CRITICAL: Do not just rephrase the false claims. You must act as if the concepts mentioned in the false claims above do not exist for this company at all — unless a VERIFIED CORRECTION is provided, in which case use that exact correction. If the cleaned evidence does not explicitly state a fact, remain silent on that topic."""

    # Step 3: Full GPT-led re-synthesis with Claude fallback.
    resynth_start = time.time()
    resynth_text = ""
    resynth_model_used = ""

    # Primary: GPT 5.5
    try:
        print(f"   🔄 [Clean Re-Synthesis] Attempting gpt-5.5...")
        resynth_text = _call_openai_text(synthesis_prompt, model="gpt-5.5", max_output_tokens=12000, timeout=120)
        resynth_model_used = "gpt-5.5"
        resynth_ms = int((time.time() - resynth_start) * 1000)
        print(f"   ✅ [Clean Re-Synthesis] GPT 5.5 complete ({resynth_ms}ms)")
    except Exception as gpt_err:
        print(f"   ❌ [Clean Re-Synthesis] GPT 5.5 failed: {gpt_err}")

    # Fallback: Claude Sonnet 4.6 (independent provider — survives OpenAI outages)
    anthropic_key = os.environ.get("ANTHROPIC_API_KEY", "").strip()
    if not resynth_text and anthropic_key:
        try:
            print(f"   🔄 [Clean Re-Synthesis] Falling back to Claude Sonnet 4.6...")
            m_start = time.time()
            sonnet_client = anthropic.Anthropic(api_key=anthropic_key)
            sonnet_resp = sonnet_client.messages.create(
                model="claude-sonnet-4-6",
                max_tokens=4000,
                messages=[{"role": "user", "content": synthesis_prompt}],
            )
            for block in sonnet_resp.content:
                if getattr(block, 'type', None) == 'text':
                    resynth_text = block.text
                    break
            resynth_model_used = "claude-sonnet-4-6"
            resynth_ms = int((time.time() - m_start) * 1000)
            print(f"   ✅ [Clean Re-Synthesis] Claude Sonnet 4.6 complete ({resynth_ms}ms)")
        except Exception as sonnet_err:
            resynth_ms = int((time.time() - resynth_start) * 1000)
            print(f"   ❌ [Clean Re-Synthesis] Claude Sonnet 4.6 failed: {sonnet_err} ({resynth_ms}ms)")

    # Step 4: Parse re-synthesis result and apply
    if resynth_text:
        resynth_payload = extract_json_from_text(resynth_text)
        if resynth_payload:
            resynth_json = json.loads(resynth_payload, strict=False)
            new_summary = resynth_json.get("diagnostic_summary", resynth_json.get("summary", ""))
            new_pitch = resynth_json.get("commercial_pitch", resynth_json.get("pitch", ""))

            if new_summary:
                new_summary = _apply_deterministic_corrections(new_summary, detailed_claims)
                data["summary_text"] = _strip_tac_codes(strip_archetype_labels_from_text(new_summary))
            if new_pitch:
                new_pitch = _apply_deterministic_corrections(new_pitch, detailed_claims)
                data["pitch_text"] = strip_archetype_labels_from_text(new_pitch)
            new_risks = resynth_json.get("evidence_based_risks", [])
            if isinstance(new_risks, list) and new_risks:
                data["evidence_based_risks"] = strip_archetype_labels_from_text(new_risks)
            new_why = resynth_json.get("why_proposals", [])
            new_what = resynth_json.get("what_proposals", [])
            normalized_activation = _normalize_activation_payload(resynth_json, {
                "why_proposals": data.get("why_proposals", []),
                "what_proposals": data.get("what_proposals", []),
                "what_progress_proposals": data.get("what_progress_proposals", []),
                "activation_pairs": data.get("activation_pairs", []),
            })
            if new_why and isinstance(new_why, list):
                data["why_proposals"] = strip_archetype_labels_from_text(normalized_activation.get("why_proposals") or new_why)
            if new_what and isinstance(new_what, list):
                data["what_proposals"] = strip_archetype_labels_from_text(normalized_activation.get("what_proposals") or new_what)
            if normalized_activation.get("what_progress_proposals"):
                data["what_progress_proposals"] = strip_archetype_labels_from_text(normalized_activation["what_progress_proposals"])
            if normalized_activation.get("activation_pairs"):
                data["activation_pairs"] = strip_archetype_labels_from_text(normalized_activation["activation_pairs"])

            # Update reality score if the re-synthesis re-evaluated it
            resynth_reality = resynth_json.get("reality_score")
            if resynth_reality is not None and isinstance(resynth_reality, (int, float)):
                resynth_reality = max(0, min(100, int(resynth_reality)))
                data["reality_score"] = resynth_reality
                data["reality_gap"] = data.get("intent_score", 0) - resynth_reality

            # --- ARCHETYPE RECALCULATION (same as scan pipeline) ---
            reality = data.get("reality_score", 0)
            if reality >= 50:
                expected_archetype = "Thriving" if fin_for_arch >= 50 else "Modernizing"
            else:
                expected_archetype = "Cash Cow" if fin_for_arch >= 50 else "At Risk"

            old_archetype = data.get("archetype")
            data["archetype"] = expected_archetype

            # Verify archetype from re-synthesis matches deterministic rule
            resynth_archetype = resynth_json.get("archetype")
            if resynth_archetype and resynth_archetype != expected_archetype:
                print(f"   ⚖️ [Clean Re-Synthesis] Archetype audit '{resynth_archetype}' differs from deterministic '{expected_archetype}'")

            if old_archetype and old_archetype != expected_archetype:
                print(f"   ⚖️ [Clean] Archetype '{old_archetype}' → corrected to '{expected_archetype}' (scores: Reality={reality}, Financial={fin_for_arch})")

            # Re-evaluate washing flag
            if reality >= data.get("intent_score", 0):
                data["washing_flag"] = False
                data["washing_type"] = None

            # Re-verify the re-synthesized summary with the independent fact-checker
            print(f"   🔄 [Clean] Re-verifying re-synthesized summary with independent fact-check...")
            re_verify = ai_fact_check_summary(
                summary_text=data["summary_text"],
                evidence_links=data.get("evidence_links", []),
                company_name=company_name,
                scan_payload=_build_fact_check_scan_payload(data),
                foresight_links=data.get("foresight_links", []),
            )

            data["ai_verification"] = {
                "verified": re_verify["verified"],
                "false_claims": re_verify["false_claims"],
                "false_claims_detailed": re_verify.get("false_claims_detailed", []),
                "model": re_verify["fact_check_model"],
                "time_ms": re_verify["fact_check_time_ms"],
            }
            data["summary_cleaned"] = {
                "was_cleaned": True,
                "method": "full_re_synthesis",
                "claims_removed": len(false_claims),
                "original_claims": false_claims,
                "clean_model": resynth_model_used,
                "clean_time_ms": resynth_ms,
                "evidence_purged": purge_result,
            }

            # Clear washing flags if re-verification passes
            if re_verify["verified"]:
                data["washing_flag"] = False
                data["washing_type"] = None
                print(f"   ✅ [Clean] Cleared washing flags (false claims removed, fresh synthesis)")

            # Regenerate General Summary from cleaned data
            try:
                gs_start = time.time()
                data = _strip_callables_and_filter_source_lists(data, f"{company_name}.pre_general_summary")
                gs_text, gs_model = generate_general_summary(data, company_name)
                if gs_text:
                    wc = len(gs_text.split())
                    print(f"   ✅ [General Summary] {wc} words, model={gs_model} ({int((time.time()-gs_start)*1000)}ms)")

                    # Fact-check General Summary against its own input payload
                    data = _strip_callables_and_filter_source_lists(data, f"{company_name}.pre_general_summary_factcheck")
                    gs_check = fact_check_general_summary(gs_text, data, company_name)
                    if gs_check.get("corrected_summary"):
                        gs_text = gs_check["corrected_summary"]
                        gs_model = gs_check["corrected_model"] or gs_model
                        print(f"   🔄 [GS Fact-Check] Replaced with corrected summary ({gs_check['check_time_ms']}ms)")

                    data["general_summary"] = gs_text
                    data["general_summary_model"] = gs_model
                    data["general_summary_verified"] = gs_check.get("verified", True)
                    data["general_summary_false_claims"] = [
                        fc.get("claim", "") for fc in gs_check.get("false_claims", [])
                    ]
                else:
                    print(f"   ⚠️ [General Summary] Generation returned empty — skipping field")
            except Exception as gs_err:
                import traceback
                print(f"   ⚠️ [General Summary] Failed: {gs_err} — skipping, clean continues")
                print(traceback.format_exc())

            callable_paths = _find_callables(data, f"{company_name}.pre_save")

            if callable_paths:

                print(f"   ⚠️ [Data Hygiene] Callable objects before save: {callable_paths[:30]}")

            data = _make_json_safe_payload(data, f"{company_name}.pre_save")

            saved_url = storage.save_company_data(company_name, data, post_clean=True)
            storage.update_global_index_fields(company_name, {
                "ai_verified": re_verify["verified"],
                "archetype": expected_archetype,
                "reality_score": data.get("reality_score", 0),
                "reality_gap": data.get("reality_gap", 0),
                "detailsUrl": saved_url,
            })

            print(f"   ✅ [Clean] {company_name}: Full re-synthesis from cleaned evidence. Re-verified: {re_verify['verified']}")

            return {
                "company": company_name,
                "action": "cleaned",
                "claims_removed": len(false_claims),
                "original_claims": false_claims,
                "re_verified": re_verify["verified"],
                "remaining_issues": re_verify["false_claims"],
                "clean_time_ms": resynth_ms,
                "verify_time_ms": re_verify.get("fact_check_time_ms", 0),
            }

    # Re-synthesis failed — save with false claims flagged
    resynth_ms = int((time.time() - resynth_start) * 1000)
    print(f"   ⚠️ [Clean Re-Synthesis] Could not re-synthesize — saving with false claims flagged")
    data["summary_cleaned"] = {
        "was_cleaned": False,
        "method": "full_re_synthesis",
        "claims_removed": 0,
        "original_claims": false_claims,
        "clean_model": resynth_model_used or "none",
        "clean_time_ms": resynth_ms,
        "reason": "Re-synthesis failed — GPT/Claude models unavailable",
    }
    callable_paths = _find_callables(data, f"{company_name}.pre_save")
    if callable_paths:
        print(f"   ⚠️ [Data Hygiene] Callable objects before save: {callable_paths[:30]}")
    data = _make_json_safe_payload(data, f"{company_name}.pre_save")
    storage.save_company_data(company_name, data)

    return {
        "company": company_name,
        "action": "failed",
        "reason": "Re-synthesis failed — GPT/Claude models unavailable",
        "original_claims": false_claims,
        "clean_time_ms": resynth_ms,
        "verify_time_ms": 0,
    }


@app.post("/admin/clean-summary/{company_name}")
async def clean_company_summary(company_name: str, request: Request):
    """
    Clean false claims from a company's summary using full re-synthesis.

    If AI fact-check found false_claims (verified=false), this endpoint
    purges contaminated evidence and performs a full GPT/Claude re-synthesis
    using the same prompt, Knowledge Base, tone, and instructions as the
    Deep Scanning pipeline. Then re-verifies with fact-check.
    """
    verify_admin(request)
    data = storage.get_company_data(company_name)
    if not data:
        raise HTTPException(status_code=404, detail=f"Company '{company_name}' not found in database.")

    return _clean_company_data(company_name, data)


@app.post("/admin/clean-all-unverified")
async def clean_all_unverified(request: Request):
    """
    Clean false claims from ALL unverified companies in the database.
    Iterates through all companies, finds those with ai_verified=false,
    and attempts to clean their summaries using the shared cleaning logic.
    """
    verify_admin(request)
    companies = storage.get_all_companies_summary()
    cleaned = []
    skipped = 0
    failed = []

    for company in companies:
        name = company.get("companyName", "")
        if not name:
            continue

        # Only process unverified companies
        if company.get("ai_verified", True):
            skipped += 1
            continue

        data = storage.get_company_data(name)
        if not data:
            continue

        print(f"   🧹 [Clean All] Processing {name}...")
        result = _clean_company_data(name, data)

        if result["action"] == "skipped":
            skipped += 1
        elif result["action"] == "failed":
            failed.append({"companyName": name, "reason": result.get("reason", "unknown")})
        elif result["action"] == "cleaned":
            cleaned.append({
                "companyName": name,
                "claims_removed": result["claims_removed"],
                "re_verified": result["re_verified"],
            })

    return {
        "total_checked": len(companies),
        "cleaned": len(cleaned),
        "skipped": skipped,
        "failed": len(failed),
        "results": cleaned,
        "failures": failed,
    }


# --- SERVER-SIDE BATCH REFRESH ---

def _run_batch_background():
    """Background thread: runs the full batch scan, updating _batch_state in-place."""
    batch_progress = {
        "current": 0,
        "total": 0,
        "current_name": "",
        "failed": [],
    }

    def on_progress(summary_list):
        """Called by run_batch_seed after each company save."""
        with _batch_lock:
            _batch_state["current"] = batch_progress["current"]
            _batch_state["current_name"] = batch_progress["current_name"]
            _batch_state["failed"] = batch_progress["failed"]

    try:
        run_batch_seed(
            real_xray_mode=True,
            limit=None,
            progress_callback=on_progress,
            max_workers=5,
            batch_progress=batch_progress,
            stop_event=_batch_stop_event,
        )
    except Exception as e:
        print(f"   [Batch Background] Fatal error: {e}")
    finally:
        with _batch_lock:
            _batch_state["is_running"] = False
            _batch_state["completed_at"] = datetime.utcnow().isoformat()
            _batch_state["current"] = batch_progress["current"]
            _batch_state["current_name"] = ""
            _batch_state["failed"] = batch_progress["failed"]


@app.post("/batch-refresh")
def start_batch_refresh(request: Request):
    """Starts a full database refresh in a background thread. Returns immediately."""
    verify_admin(request)

    with _batch_lock:
        if _batch_state["is_running"]:
            raise HTTPException(status_code=409, detail="Batch refresh already in progress.")
        _batch_stop_event.clear()
        _batch_state["is_running"] = True
        _batch_state["current"] = 0
        _batch_state["total"] = len(TOP_100_COMPANIES)
        _batch_state["current_name"] = "Starting..."
        _batch_state["started_at"] = datetime.utcnow().isoformat()
        _batch_state["completed_at"] = None
        _batch_state["failed"] = []

    thread = threading.Thread(target=_run_batch_background, daemon=True)
    thread.start()

    return {"status": "started", "total": len(TOP_100_COMPANIES)}


@app.post("/batch-refresh/stop")
def stop_batch_refresh(request: Request):
    """Signals the running batch to stop after the current company finishes."""
    verify_admin(request)
    _batch_stop_event.set()
    return {"status": "stop_requested"}


@app.get("/batch-status")
def get_batch_status():
    """Returns current batch refresh progress. No auth required (read-only)."""
    with _batch_lock:
        return dict(_batch_state)


# --- FORESIGHT KNOWLEDGE BASE ENDPOINTS ---

_foresight_kb_state = {
    "is_running": False,
    "started_at": None,
    "completed_at": None,
    "current_theme": None,
    "current": 0,
    "total": 0,
    "result": None,
}
_foresight_kb_lock = threading.Lock()


def _run_foresight_kb_harvest():
    """Background worker for foresight KB refresh."""
    try:
        from foresight_harvester import harvest_all_themes

        completed_themes = 0

        def progress_cb(theme, index, total, status):
            nonlocal completed_themes
            with _foresight_kb_lock:
                _foresight_kb_state["total"] = total
                _foresight_kb_state["current_status"] = status
                if status == "completed":
                    completed_themes += 1
                    _foresight_kb_state["current_theme"] = theme
                    _foresight_kb_state["current"] = completed_themes
                elif completed_themes == 0:
                    _foresight_kb_state["current_theme"] = "Queueing themes..."

        result = harvest_all_themes(progress_callback=progress_cb)

        with _foresight_kb_lock:
            _foresight_kb_state["result"] = result
            _foresight_kb_state["completed_at"] = datetime.utcnow().isoformat()
            _foresight_kb_state["is_running"] = False
            _foresight_kb_state["current_theme"] = None
    except Exception as e:
        print(f"   ❌ [Foresight KB] Harvest failed: {e}")
        with _foresight_kb_lock:
            _foresight_kb_state["is_running"] = False
            _foresight_kb_state["result"] = {"error": str(e)}
            _foresight_kb_state["completed_at"] = datetime.utcnow().isoformat()


@app.post("/admin/refresh-foresight-kb")
def refresh_foresight_kb(request: Request):
    """
    Starts a full Foresight Knowledge Base harvest in a background thread.
    Scans all 30 FP themes for megatrend signals, validates, and stores.
    Returns immediately — poll /admin/foresight-kb-status for progress.
    """
    verify_admin(request)

    with _foresight_kb_lock:
        if _foresight_kb_state["is_running"]:
            raise HTTPException(status_code=409, detail="Foresight KB harvest already in progress.")
        _foresight_kb_state["is_running"] = True
        _foresight_kb_state["current"] = 0
        _foresight_kb_state["total"] = 30
        _foresight_kb_state["current_theme"] = "Starting..."
        _foresight_kb_state["current_status"] = "starting"
        _foresight_kb_state["started_at"] = datetime.utcnow().isoformat()
        _foresight_kb_state["completed_at"] = None
        _foresight_kb_state["result"] = None

    thread = threading.Thread(target=_run_foresight_kb_harvest, daemon=True)
    thread.start()

    return {"status": "started", "total_themes": 30}


@app.get("/admin/foresight-kb-status")
def foresight_kb_status():
    """Returns current Foresight KB harvest progress and stored KB summary. No auth required (read-only)."""
    with _foresight_kb_lock:
        harvest_state = dict(_foresight_kb_state)

    # Also include stored KB metadata
    try:
        kb_meta = storage.get_foresight_kb_status()
    except Exception:
        kb_meta = None

    return {"harvest": harvest_state, "kb": kb_meta}


# --- Foresight KB Verify/Repair (URL hygiene + context eligibility without full harvest) ---
_foresight_cleanup_state = {
    "is_running": False,
    "started_at": None,
    "completed_at": None,
    "current_theme": None,
    "current": 0,
    "total": 0,
    "result": None,
}
_foresight_cleanup_lock = threading.Lock()


def _run_foresight_kb_cleanup():
    """Background worker for Foresight KB context verification/metadata repair."""
    try:
        from foresight_harvester import cleanup_foresight_kb

        def progress_cb(theme, index, total, status):
            with _foresight_cleanup_lock:
                _foresight_cleanup_state["current_theme"] = theme
                _foresight_cleanup_state["current"] = index + 1
                _foresight_cleanup_state["total"] = total

        result = cleanup_foresight_kb(progress_callback=progress_cb)

        with _foresight_cleanup_lock:
            _foresight_cleanup_state["result"] = result
            _foresight_cleanup_state["completed_at"] = datetime.utcnow().isoformat()
            _foresight_cleanup_state["is_running"] = False
            _foresight_cleanup_state["current_theme"] = None
    except Exception as e:
        print(f"   ❌ [Foresight KB Repair] Failed: {e}")
        with _foresight_cleanup_lock:
            _foresight_cleanup_state["is_running"] = False
            _foresight_cleanup_state["result"] = {"error": str(e)}
            _foresight_cleanup_state["completed_at"] = datetime.utcnow().isoformat()


@app.post("/admin/cleanup-foresight-kb")
def cleanup_foresight_kb_endpoint(request: Request):
    """
    Re-verifies stored KB URLs and repairs foresight-context eligibility metadata.
    Does NOT harvest new signals.
    Returns immediately — poll /admin/foresight-kb-cleanup-status for progress.
    """
    verify_admin(request)

    with _foresight_cleanup_lock:
        if _foresight_cleanup_state["is_running"]:
            raise HTTPException(status_code=409, detail="Foresight KB repair already in progress.")
        _foresight_cleanup_state["is_running"] = True
        _foresight_cleanup_state["current"] = 0
        _foresight_cleanup_state["total"] = 30
        _foresight_cleanup_state["current_theme"] = "Starting repair..."
        _foresight_cleanup_state["started_at"] = datetime.utcnow().isoformat()
        _foresight_cleanup_state["completed_at"] = None
        _foresight_cleanup_state["result"] = None

    # Also block if a full harvest is running
    with _foresight_kb_lock:
        if _foresight_kb_state["is_running"]:
            with _foresight_cleanup_lock:
                _foresight_cleanup_state["is_running"] = False
            raise HTTPException(status_code=409, detail="Cannot repair while KB harvest is running.")

    thread = threading.Thread(target=_run_foresight_kb_cleanup, daemon=True)
    thread.start()

    return {"status": "started", "message": "Foresight KB verify/repair started in background"}


@app.get("/admin/foresight-kb-cleanup-status")
def foresight_kb_cleanup_status():
    """Returns current Foresight KB verify/repair progress."""
    with _foresight_cleanup_lock:
        return dict(_foresight_cleanup_state)


# --- FORESIGHT OBSERVATORY ENDPOINTS ---

_observatory_state = {
    "is_running": False,
    "started_at": None,
    "completed_at": None,
    "current_step": None,
    "current": 0,
    "total": 0,
    "result": None,
}
_observatory_lock = threading.Lock()


def _run_observatory_generation():
    """Background worker for observatory generation."""
    try:
        from foresight_observatory import generate_observatory

        def progress_cb(step_name, index, total):
            with _observatory_lock:
                _observatory_state["current_step"] = step_name
                _observatory_state["current"] = index + 1
                _observatory_state["total"] = total

        result = generate_observatory(progress_callback=progress_cb)

        with _observatory_lock:
            _observatory_state["result"] = {"status": "completed", "domain_count": result.get("domain_count", 0)}
            _observatory_state["completed_at"] = datetime.utcnow().isoformat()
            _observatory_state["is_running"] = False
            _observatory_state["current_step"] = None
    except Exception as e:
        print(f"   [Observatory] Generation failed: {e}")
        import traceback
        traceback.print_exc()
        with _observatory_lock:
            _observatory_state["is_running"] = False
            _observatory_state["result"] = {"error": str(e)}
            _observatory_state["completed_at"] = datetime.utcnow().isoformat()


@app.post("/admin/generate-observatory")
def generate_observatory_endpoint(request: Request):
    """
    Starts Foresight Observatory generation in a background thread.
    Generates three layers of strategic analysis using GPT/Claude verification.
    Returns immediately — poll /admin/observatory-status for progress.
    """
    verify_admin(request)

    with _observatory_lock:
        if _observatory_state["is_running"]:
            raise HTTPException(status_code=409, detail="Observatory generation already in progress.")
        _observatory_state["is_running"] = True
        _observatory_state["current"] = 0
        _observatory_state["total"] = 0
        _observatory_state["current_step"] = "Initializing..."
        _observatory_state["started_at"] = datetime.utcnow().isoformat()
        _observatory_state["completed_at"] = None
        _observatory_state["result"] = None

    thread = threading.Thread(target=_run_observatory_generation, daemon=True)
    thread.start()

    return {"status": "started"}


@app.get("/admin/observatory-status")
def observatory_status():
    """Returns current Observatory generation progress. No auth required (read-only)."""
    with _observatory_lock:
        return dict(_observatory_state)


@app.get("/api/observatory")
async def get_observatory(response: Response):
    """Returns the cached observatory analysis. No auth required (read-only)."""
    response.headers["Cache-Control"] = "no-cache, no-store, must-revalidate"
    data = storage.get_observatory()
    if not data:
        raise HTTPException(status_code=404, detail="No observatory data found. Generate one first.")
    return data


# ═══════════════════════════════════════════════════════════════════════════════
# CROSS-IMPACT MATRIX INTERPRETATION
# Generates a structured prose summary of the Theme x Dimension Cross-Impact
# Matrix. Stored in its own blob; independent of the Observatory regen path.
# ═══════════════════════════════════════════════════════════════════════════════

_cross_impact_state = {
    "is_running": False,
    "started_at": None,
    "completed_at": None,
    "current_step": None,
    "result": None,
}
_cross_impact_lock = threading.Lock()


def _run_cross_impact_generation():
    """Background worker for cross-impact interpretation generation."""
    try:
        with _cross_impact_lock:
            _cross_impact_state["current_step"] = "Loading data"

        dashboard_data = storage.retrieve_latest_data("index/summary_list") or []
        company_details = []
        for item in dashboard_data:
            name = item.get("companyName")
            if not name:
                continue
            detail = storage.get_company_data(name)
            if detail:
                company_details.append(detail)

        if not company_details:
            raise ValueError("No company data available — generate market intelligence first")

        from cross_impact_interpretation import generate_cross_impact_interpretation
        from market_intelligence import THEME_CATEGORIES
        theme_descriptions = {
            theme: f"Foresight signals related to {theme} ({category})."
            for theme, category in THEME_CATEGORIES.items()
        }

        with _cross_impact_lock:
            _cross_impact_state["current_step"] = "Generating interpretation"

        payload = generate_cross_impact_interpretation(
            company_details=company_details,
            dashboard_data=dashboard_data,
            theme_descriptions=theme_descriptions,
        )

        with _cross_impact_lock:
            _cross_impact_state["current_step"] = "Saving"

        storage.save_cross_impact_interpretation(payload)

        with _cross_impact_lock:
            _cross_impact_state["result"] = {"status": "completed"}
            _cross_impact_state["completed_at"] = datetime.utcnow().isoformat()
            _cross_impact_state["is_running"] = False
            _cross_impact_state["current_step"] = None
    except Exception as e:
        print(f"   [CrossImpact] Generation failed: {e}")
        import traceback
        traceback.print_exc()
        with _cross_impact_lock:
            _cross_impact_state["is_running"] = False
            _cross_impact_state["result"] = {"error": str(e)}
            _cross_impact_state["completed_at"] = datetime.utcnow().isoformat()
            _cross_impact_state["current_step"] = None


@app.post("/admin/generate-cross-impact-interpretation")
def generate_cross_impact_endpoint(request: Request):
    """Starts Cross-Impact Matrix Interpretation generation in a background thread.
    Poll /admin/cross-impact-interpretation-status for progress."""
    verify_admin(request)

    with _cross_impact_lock:
        if _cross_impact_state["is_running"]:
            return {"status": "already_running"}
        _cross_impact_state["is_running"] = True
        _cross_impact_state["current_step"] = "Initializing..."
        _cross_impact_state["started_at"] = datetime.utcnow().isoformat()
        _cross_impact_state["completed_at"] = None
        _cross_impact_state["result"] = None

    thread = threading.Thread(target=_run_cross_impact_generation, daemon=True)
    thread.start()
    return {"status": "started"}


@app.get("/admin/cross-impact-interpretation-status")
def cross_impact_status():
    """Polls the cross-impact generation status."""
    with _cross_impact_lock:
        return dict(_cross_impact_state)


@app.get("/api/cross-impact-interpretation")
async def get_cross_impact_interpretation(response: Response):
    """Returns the cached Cross-Impact Matrix Interpretation. Read-only."""
    response.headers["Cache-Control"] = "no-cache, no-store, must-revalidate"
    data = storage.get_cross_impact_interpretation()
    if not data:
        raise HTTPException(status_code=404, detail="No cross-impact interpretation found. Generate one first.")
    return data


PIPELINE_FRESH_DAYS = 14
PIPELINE_READINESS_SNAPSHOT_TTL_SECONDS = 15 * 60


def _parse_pipeline_dt(value):
    if not value:
        return None
    if isinstance(value, datetime):
        return value.replace(tzinfo=None)
    text = str(value).strip()
    if not text:
        return None
    try:
        return datetime.fromisoformat(text.replace("Z", "+00:00")).replace(tzinfo=None)
    except Exception:
        return None


def _pipeline_item(state: str, label: str, detail: str = "", updated_at=None, stats=None, recommended_action: str = "") -> dict:
    return {
        "state": state,
        "label": label,
        "detail": detail,
        "updated_at": updated_at,
        "stats": stats or {},
        "recommended_action": recommended_action,
    }


def _index_by_company(items: list[dict]) -> dict[str, dict]:
    return {
        str(item.get("companyName") or item.get("company_name") or "").strip().lower(): item
        for item in (items or [])
        if isinstance(item, dict) and (item.get("companyName") or item.get("company_name"))
    }


def _newer_than(left, right) -> bool:
    left_dt = _parse_pipeline_dt(left)
    right_dt = _parse_pipeline_dt(right)
    return bool(left_dt and right_dt and left_dt > right_dt)


def _days_old(value):
    dt = _parse_pipeline_dt(value)
    if not dt:
        return None
    return max(0, (datetime.utcnow() - dt).days)


def _running_company_job(jobs: dict, company_name: str) -> dict | None:
    target = str(company_name or "").strip().lower()
    for job in jobs.values():
        if job.get("status") in {"queued", "running"} and str(job.get("company") or "").strip().lower() == target:
            return job
    return None


def _first_running_company_job(lock, jobs: dict, company_name: str) -> dict | None:
    with lock:
        job = _running_company_job(jobs, company_name)
        return dict(job) if job else None


def _global_running_item(state: dict, running_label: str):
    if state.get("is_running"):
        return _pipeline_item(
            "running",
            running_label,
            state.get("current_theme") or state.get("current_step") or state.get("current_status") or "Running",
            state.get("started_at"),
            {"current": state.get("current"), "total": state.get("total")},
            "Wait for this run to complete before regenerating dependent artifacts.",
        )
    return None


def _pipeline_indexes() -> dict:
    company_evidence_index = buying_storage.get_company_evidence_index()
    source_ledger_index = buying_storage.get_company_source_ledger_index()
    buying_index = buying_storage.get_buying_potential_index()
    return {
        "company_evidence": _index_by_company(company_evidence_index),
        "source_ledger": _index_by_company(source_ledger_index),
        "buying_insight": _index_by_company(buying_index),
    }


def _pipeline_company_source_stamps(company: dict, indexes: dict) -> dict:
    name = company.get("companyName") or company.get("company_name") or ""
    key = str(name).strip().lower()
    evidence_meta = indexes.get("company_evidence", {}).get(key) or {}
    ledger_meta = indexes.get("source_ledger", {}).get(key) or {}
    buying_meta = indexes.get("buying_insight", {}).get(key) or {}
    return {
        "scan": company.get("lastUpdated") or company.get("uploaded_at"),
        "evidence_verified": company.get("evidence_cleaned_at"),
        "company_evidence_db": evidence_meta.get("updated_at"),
        "source_ledger": ledger_meta.get("updated_at"),
        "buying_insight": buying_meta.get("updated_at"),
    }


def _pipeline_snapshot_is_fresh(snapshot: dict | None) -> bool:
    if not isinstance(snapshot, dict):
        return False
    generated_at = _parse_pipeline_dt(snapshot.get("generated_at"))
    if not generated_at:
        return False
    age_seconds = max(0, (datetime.utcnow() - generated_at).total_seconds())
    return age_seconds <= PIPELINE_READINESS_SNAPSHOT_TTL_SECONDS


def _get_pipeline_snapshot() -> dict | None:
    try:
        return storage.get_pipeline_readiness_index()
    except Exception as exc:
        print(f"   [Pipeline Readiness] Snapshot read failed: {exc}")
        return None


def _save_pipeline_snapshot(snapshot: dict) -> dict:
    try:
        storage.save_pipeline_readiness_index(snapshot)
    except Exception as exc:
        print(f"   [Pipeline Readiness] Snapshot save failed: {exc}")
    return snapshot


def _find_company_for_pipeline_readiness(company_name: str) -> dict | None:
    if not company_name:
        return None
    detail = storage.get_company_data(company_name)
    if isinstance(detail, dict) and detail:
        return detail
    summaries = storage.get_all_companies_summary()
    target = str(company_name or "").strip().lower()
    for summary in summaries:
        if str(summary.get("companyName") or "").strip().lower() == target:
            detail = storage.get_company_data(summary.get("companyName"))
            return {**summary, **detail} if isinstance(detail, dict) else summary
    return None


def _build_pipeline_global_readiness() -> dict:
    try:
        kb_meta = storage.get_foresight_kb_status()
    except Exception:
        kb_meta = {"theme_count": 0, "total_signals": 0, "last_updated": None, "themes": []}
    try:
        observatory = storage.get_observatory()
    except Exception:
        observatory = None
    try:
        cross_impact = storage.get_cross_impact_interpretation()
    except Exception:
        cross_impact = None

    kb_running = _global_running_item(_current_foresight_kb_state(), "Foresight KB")
    if kb_running:
        foresight_kb = kb_running
    elif not kb_meta or not kb_meta.get("theme_count"):
        foresight_kb = _pipeline_item("missing", "Foresight KB", "No harvested foresight KB themes found.", None, kb_meta or {}, "Harvest KB before refreshing foresight-dependent analysis.")
    else:
        age = _days_old(kb_meta.get("last_updated"))
        stale = age is not None and age > PIPELINE_FRESH_DAYS
        foresight_kb = _pipeline_item(
            "warning" if stale else "ready",
            "Foresight KB",
            f"{kb_meta.get('theme_count', 0)} theme(s), {kb_meta.get('total_signals', 0)} signal(s)." + (f" Last harvest {age} day(s) ago." if age is not None else ""),
            kb_meta.get("last_updated"),
            kb_meta,
            "Harvest KB if current foresight context matters." if stale else "",
        )

    obs_running = _global_running_item(_current_observatory_state(), "Observatory")
    observatory_updated = observatory.get("generated_at") if isinstance(observatory, dict) else None
    if obs_running:
        observatory_item = obs_running
    elif not observatory:
        observatory_item = _pipeline_item("missing", "Observatory", "No Observatory analysis found.", None, recommended_action="Generate Observatory after Foresight KB is ready.")
    else:
        stale = _newer_than(kb_meta.get("last_updated") if kb_meta else None, observatory_updated)
        observatory_item = _pipeline_item(
            "warning" if stale else "ready",
            "Observatory",
            "Observatory is older than the Foresight KB." if stale else "Observatory analysis is available.",
            observatory_updated,
            {"model": observatory.get("model")},
            "Regenerate Observatory after KB harvest." if stale else "",
        )

    cross_running = _global_running_item(_current_cross_impact_state(), "Matrix Interpretation")
    cross_updated = cross_impact.get("generated_at") if isinstance(cross_impact, dict) else None
    if cross_running:
        cross_item = cross_running
    elif not cross_impact:
        cross_item = _pipeline_item("missing", "Matrix Interpretation", "No Cross-Impact Matrix Interpretation found.", None, recommended_action="Generate the Matrix Interpretation after Observatory/Foresight context is ready.")
    else:
        stale = _newer_than(kb_meta.get("last_updated") if kb_meta else None, cross_updated) or _newer_than(observatory_updated, cross_updated)
        cross_item = _pipeline_item(
            "warning" if stale else "ready",
            "Matrix Interpretation",
            "Matrix Interpretation is older than Observatory or Foresight KB." if stale else "Matrix Interpretation is available.",
            cross_updated,
            {"model": cross_impact.get("model")},
            "Refresh the Matrix Interpretation." if stale else "",
        )

    return {
        "foresight_kb": foresight_kb,
        "observatory": observatory_item,
        "cross_impact": cross_item,
    }


def _current_foresight_kb_state() -> dict:
    with _foresight_kb_lock:
        return dict(_foresight_kb_state)


def _current_observatory_state() -> dict:
    with _observatory_lock:
        return dict(_observatory_state)


def _current_cross_impact_state() -> dict:
    with _cross_impact_lock:
        return dict(_cross_impact_state)


def _company_pipeline_readiness(company: dict, indexes: dict) -> dict:
    name = company.get("companyName") or company.get("company_name") or ""
    latest_scan = company.get("lastUpdated") or company.get("uploaded_at")
    evidence_stats = company.get("evidence_cleaning_stats") or {}
    activation_pairs = company.get("activation_pairs") or []
    why = company.get("why_proposals") or []
    what = company.get("what_proposals") or []
    key = str(name).strip().lower()

    evidence_job = _first_running_company_job(_company_evidence_jobs_lock, _company_evidence_jobs, name)
    ledger_job = _first_running_company_job(_company_source_ledger_jobs_lock, _company_source_ledger_jobs, name)
    buying_job = _first_running_company_job(_buying_potential_jobs_lock, _buying_potential_jobs, name)

    if latest_scan:
        scan = _pipeline_item("ready", "Scan", "Latest Scanner record is available.", latest_scan)
    else:
        scan = _pipeline_item("missing", "Scan", "No stored Scanner scan timestamp found.", None, recommended_action="Run or refresh Scanner first.")

    analysis_verified = evidence_stats.get("analysis_verified")
    total_sources = evidence_stats.get("total_sources")
    unsupported = evidence_stats.get("unsupported")
    wrong_entity = evidence_stats.get("wrong_entity")
    if company.get("evidence_cleaned_at"):
        if analysis_verified:
            evidence_verified = _pipeline_item(
                "ready",
                "Evidence Verified",
                f"{analysis_verified} analysis-verified source(s).",
                company.get("evidence_cleaned_at"),
                evidence_stats,
            )
        else:
            evidence_verified = _pipeline_item(
                "warning",
                "Evidence Verified",
                "Evidence was checked, but no analysis-verified sources were recorded.",
                company.get("evidence_cleaned_at"),
                evidence_stats,
                "Run Verify Evidence or rescan if this company should have stronger public evidence.",
            )
    elif total_sources:
        evidence_verified = _pipeline_item(
            "warning",
            "Evidence Verified",
            f"{total_sources} source(s) present, verification freshness unknown.",
            None,
            evidence_stats,
            "Run Verify Evidence before relying on downstream artifacts.",
        )
    else:
        evidence_verified = _pipeline_item(
            "missing",
            "Evidence Verified",
            "No evidence verification metadata found.",
            None,
            evidence_stats,
            "Run Verify Evidence after scan evidence is available.",
        )
    if (unsupported or 0) > 0 or (wrong_entity or 0) > 0:
        evidence_verified["state"] = "warning"
        evidence_verified["detail"] += f" Unsupported: {unsupported or 0}; wrong entity: {wrong_entity or 0}."

    evidence_meta = indexes["company_evidence"].get(key)
    if evidence_job:
        company_evidence_db = _pipeline_item("running", "Evidence DB", evidence_job.get("stage") or "Running", evidence_job.get("created_at"), evidence_job.get("stats"), "Wait for Evidence DB to complete.")
    elif evidence_meta:
        evidence_updated = evidence_meta.get("updated_at")
        stale = _newer_than(latest_scan, evidence_updated)
        company_evidence_db = _pipeline_item(
            "warning" if stale else "ready",
            "Evidence DB",
            "Evidence DB is older than the latest scan." if stale else "Company Evidence DB is available.",
            evidence_updated,
            evidence_meta.get("stats"),
            "Rebuild Company Evidence DB so Buying Insight can use fresh source candidates." if stale else "",
        )
    else:
        company_evidence_db = _pipeline_item("missing", "Evidence DB", "No Company Evidence DB artifact found.", None, recommended_action="Build Evidence DB before high-confidence Buying Insight.")

    ledger_meta = indexes["source_ledger"].get(key)
    ledger_upstream = max([dt for dt in (latest_scan, evidence_meta.get("updated_at") if evidence_meta else None) if dt], default=None)
    if ledger_job:
        source_ledger = _pipeline_item("running", "Source Ledger", ledger_job.get("stage") or "Running", ledger_job.get("created_at"), ledger_job.get("stats"), "Wait for Source Ledger to complete.")
    elif ledger_meta:
        ledger_updated = ledger_meta.get("updated_at")
        stale = _newer_than(ledger_upstream, ledger_updated)
        source_ledger = _pipeline_item(
            "warning" if stale else "ready",
            "Source Ledger",
            "Source Ledger is older than scan/Evidence DB." if stale else "Source Ledger is available.",
            ledger_updated,
            ledger_meta.get("stats"),
            "Combine Evidence again to refresh traceability." if stale else "",
        )
    else:
        source_ledger = _pipeline_item("missing", "Source Ledger", "No Source Ledger artifact found.", None, recommended_action="Combine Evidence after Evidence DB is ready.")

    pair_count = len(activation_pairs) if isinstance(activation_pairs, list) else 0
    why_count = len(why) if isinstance(why, list) else 0
    what_count = len(what) if isinstance(what, list) else 0
    if pair_count >= 2:
        activation_status = _pipeline_item("ready", "Activation Pairs", f"{pair_count} WHY/WHAT activation pair(s) available.", latest_scan, {"activation_pairs": pair_count})
    elif pair_count == 1 or (why_count and what_count):
        activation_status = _pipeline_item("warning", "Activation Pairs", "Activation logic exists but may be thin or legacy-shaped.", latest_scan, {"activation_pairs": pair_count, "why": why_count, "what": what_count}, "Refresh WHY/WHAT if Buying Insight needs a stronger sales spine.")
    else:
        activation_status = _pipeline_item("missing", "Activation Pairs", "No activation-pair logic found.", latest_scan, {"activation_pairs": pair_count, "why": why_count, "what": what_count}, "Refresh WHY/WHAT before creating Buying Insight.")

    buying_meta = indexes["buying_insight"].get(key)
    buying_upstream = max([
        dt for dt in (
            latest_scan,
            evidence_meta.get("updated_at") if evidence_meta else None,
            ledger_meta.get("updated_at") if ledger_meta else None,
        )
        if dt
    ], default=None)
    if buying_job:
        buying_insight = _pipeline_item("running", "Buying Insight", buying_job.get("stage") or "Running", buying_job.get("created_at"), buying_job.get("source_counts"), "Wait for Buying Insight to complete.")
    elif buying_meta:
        buying_updated = buying_meta.get("updated_at")
        stale = _newer_than(buying_upstream, buying_updated)
        buying_insight = _pipeline_item(
            "warning" if stale else "ready",
            "Buying Insight",
            "Buying Insight is older than one or more upstream artifacts." if stale else f"Buying Insight available, confidence {buying_meta.get('confidence', 'n/a')}.",
            buying_updated,
            buying_meta.get("source_counts"),
            "Regenerate Buying Insight after refreshing upstream evidence." if stale else "",
        )
        buying_insight["confidence"] = buying_meta.get("confidence")
    else:
        buying_insight = _pipeline_item("missing", "Buying Insight", "No Buying Insight artifact found.", None, recommended_action="Generate Buying Insight when prerequisites are acceptable.")

    readiness = {
        "scan": scan,
        "evidence_verified": evidence_verified,
        "company_evidence_db": company_evidence_db,
        "source_ledger": source_ledger,
        "activation_pairs": activation_status,
        "buying_insight": buying_insight,
    }
    readiness["_source_stamps"] = _pipeline_company_source_stamps(company, indexes)
    return readiness


def _overlay_company_running_readiness(company_name: str, readiness: dict | None) -> dict:
    item = dict(readiness or {})
    evidence_job = _first_running_company_job(_company_evidence_jobs_lock, _company_evidence_jobs, company_name)
    ledger_job = _first_running_company_job(_company_source_ledger_jobs_lock, _company_source_ledger_jobs, company_name)
    buying_job = _first_running_company_job(_buying_potential_jobs_lock, _buying_potential_jobs, company_name)
    if evidence_job:
        item["company_evidence_db"] = _pipeline_item("running", "Evidence DB", evidence_job.get("stage") or "Running", evidence_job.get("created_at"), evidence_job.get("stats"), "Wait for Evidence DB to complete.")
    if ledger_job:
        item["source_ledger"] = _pipeline_item("running", "Source Ledger", ledger_job.get("stage") or "Running", ledger_job.get("created_at"), ledger_job.get("stats"), "Wait for Source Ledger to complete.")
    if buying_job:
        item["buying_insight"] = _pipeline_item("running", "Buying Insight", buying_job.get("stage") or "Running", buying_job.get("created_at"), buying_job.get("source_counts"), "Wait for Buying Insight to complete.")
    return item


def _overlay_pipeline_running_state(snapshot: dict) -> dict:
    data = dict(snapshot or {})
    global_items = dict(data.get("global") or {})
    kb_running = _global_running_item(_current_foresight_kb_state(), "Foresight KB")
    obs_running = _global_running_item(_current_observatory_state(), "Observatory")
    cross_running = _global_running_item(_current_cross_impact_state(), "Matrix Interpretation")
    if kb_running:
        global_items["foresight_kb"] = kb_running
    if obs_running:
        global_items["observatory"] = obs_running
    if cross_running:
        global_items["cross_impact"] = cross_running
    data["global"] = global_items
    companies = dict(data.get("companies") or {})
    for company_name, readiness in list(companies.items()):
        companies[company_name] = _overlay_company_running_readiness(company_name, readiness)
    data["companies"] = companies
    data["served_at"] = datetime.utcnow().isoformat()
    return data


def _build_pipeline_readiness_snapshot() -> dict:
    company_summaries = storage.get_all_companies_summary()
    companies = []
    for summary in company_summaries:
        name = summary.get("companyName")
        detail = storage.get_company_data(name) if name else None
        companies.append({**summary, **detail} if isinstance(detail, dict) else summary)
    indexes = _pipeline_indexes()
    return {
        "generated_at": datetime.utcnow().isoformat(),
        "fresh_days": PIPELINE_FRESH_DAYS,
        "snapshot_ttl_seconds": PIPELINE_READINESS_SNAPSHOT_TTL_SECONDS,
        "global": _build_pipeline_global_readiness(),
        "companies": {
            company.get("companyName"): _company_pipeline_readiness(company, indexes)
            for company in companies
            if company.get("companyName")
        },
    }


def _refresh_pipeline_company_in_snapshot(snapshot: dict | None, company_name: str) -> dict:
    data = snapshot if isinstance(snapshot, dict) else _build_pipeline_readiness_snapshot()
    company = _find_company_for_pipeline_readiness(company_name)
    if company:
        indexes = _pipeline_indexes()
        canonical = company.get("companyName") or company.get("company_name") or company_name
        companies = dict(data.get("companies") or {})
        companies[canonical] = _company_pipeline_readiness(company, indexes)
        data["companies"] = companies
    data["generated_at"] = datetime.utcnow().isoformat()
    return _save_pipeline_snapshot(data)


def _refresh_pipeline_global_in_snapshot(snapshot: dict | None) -> dict:
    data = snapshot if isinstance(snapshot, dict) else _build_pipeline_readiness_snapshot()
    data["global"] = _build_pipeline_global_readiness()
    data["generated_at"] = datetime.utcnow().isoformat()
    return _save_pipeline_snapshot(data)


@app.get("/admin/pipeline-readiness")
async def pipeline_readiness(request: Request):
    """Return advisory readiness/freshness state for Scanner admin pipeline stages.

    Default mode serves a materialized snapshot and overlays live running-job
    status. Use refresh=company&company=... or refresh=global/all after
    state-changing admin actions.
    """
    verify_admin(request)
    query = request.query_params
    refresh = str(query.get("refresh") or "").strip().lower()
    company_name = str(query.get("company") or "").strip()
    force = str(query.get("force") or "").strip().lower() in {"1", "true", "yes"}

    snapshot = _get_pipeline_snapshot()
    if refresh == "company" and company_name:
        snapshot = _refresh_pipeline_company_in_snapshot(snapshot, company_name)
    elif refresh == "global":
        snapshot = _refresh_pipeline_global_in_snapshot(snapshot)
    elif force or refresh == "all" or not _pipeline_snapshot_is_fresh(snapshot):
        snapshot = _save_pipeline_snapshot(_build_pipeline_readiness_snapshot())

    return _overlay_pipeline_running_state(snapshot or {})


# --- MAIN SCAN ENDPOINT (MULTIPART) ---


# --- ASYNC SCAN JOB STATE ---
_scan_jobs = {}
_scan_jobs_lock = threading.Lock()

def _append_scan_log(job_id: str, message: str):
    with _scan_jobs_lock:
        job = _scan_jobs.get(job_id)
        if not job:
            return
        logs = job.setdefault("logs", [])
        logs.append({"ts": datetime.utcnow().isoformat(), "message": message})
        if len(logs) > 200:
            del logs[:-200]

def _update_scan_job(job_id: str, **fields):
    with _scan_jobs_lock:
        job = _scan_jobs.get(job_id)
        if not job:
            return
        job.update(fields)

def _run_scan_job(job_id: str, companyName: str, companyUrl: Optional[str], reportText: str, saveToDatabase: bool, files: list):
    try:
        _update_scan_job(job_id, status="running", phase="scan_pipeline", progress=10)
        _append_scan_log(job_id, f"Started scan for {companyName}")
        result = _execute_scan_pipeline(job_id=job_id, companyName=companyName, companyUrl=companyUrl, reportText=reportText, saveToDatabase=saveToDatabase, files=files)
        _update_scan_job(job_id, status="completed", phase="completed", progress=100, result=result, completed_at=datetime.utcnow().isoformat())
        _append_scan_log(job_id, "Scan completed")
    except HTTPException as e:
        _update_scan_job(job_id, status="failed", phase="failed", error=e.detail, progress=100, completed_at=datetime.utcnow().isoformat())
        _append_scan_log(job_id, f"Scan failed: {e.detail}")
    except Exception as e:
        _update_scan_job(job_id, status="failed", phase="failed", error=str(e), progress=100, completed_at=datetime.utcnow().isoformat())
        _append_scan_log(job_id, f"Scan failed: {e}")

@app.post("/scan")
def submit_scan_job(
    request: Request,
    companyName: str = Form(...),
    companyUrl: Optional[str] = Form(None),
    reportText: str = Form(""),
    saveToDatabase: bool = Form(True),
    files: List[UploadFile] = File(None)
):
    verify_admin(request)
    materialized_files = materialize_uploads(files)
    job_id = str(uuid.uuid4())
    with _scan_jobs_lock:
        _scan_jobs[job_id] = {
            "job_id": job_id,
            "status": "queued",
            "phase": "queued",
            "progress": 0,
            "logs": [{"ts": datetime.utcnow().isoformat(), "message": f"Queued scan for {companyName}"}],
            "result": None,
            "error": None,
            "created_at": datetime.utcnow().isoformat(),
            "completed_at": None,
            "company_name": companyName,
        }

    thread = threading.Thread(target=_run_scan_job, args=(job_id, companyName, companyUrl, reportText, saveToDatabase, materialized_files), daemon=True)
    thread.start()
    return {"job_id": job_id, "status": "queued"}

@app.get("/scan-status/{job_id}")
def get_scan_status(job_id: str, request: Request):
    verify_admin(request)
    with _scan_jobs_lock:
        job = _scan_jobs.get(job_id)
        if not job:
            raise HTTPException(status_code=404, detail="Scan job not found")
        return dict(job)

# --- MAIN SCAN PIPELINE (SYNC, USED BY BACKGROUND JOB) ---
def _execute_scan_pipeline(
    job_id: Optional[str] = None,
    companyName: str = "",
    companyUrl: Optional[str] = None,
    reportText: str = "",
    saveToDatabase: bool = True,
    files: Optional[list] = None
):
    """
    Hybrid Forensic Scan: Accepts Text + URLs + Uploaded Documents (PDF/HTML).
    Requires admin authentication.
    """
    try:
        scan_start = time.time()
        # Global scan time budget (seconds). All phases must fit within this window.
        # Live scans run as background jobs, so the internal budget can be
        # longer than a request/response timeout. Keep a protected reserve for
        # final outputs so difficult scans do not starve AI Diagnosis,
        # WHY/WHAT, Strategic Pitch, General Summary, and storage.
        # Budget allocation:
        #   Reality check: capped so it cannot consume the core synthesis slot
        #   Financial:     120s (parallel with reality, non-blocking)
        #   No cross-scan score analysis: historical runs seed URLs only
        #   Synthesis:     protected core budget with adaptive fallback chain
        #   Final outputs: protected reserve for fact-check, General Summary,
        #                  save/index; Detailed Report is optional when time is tight.
        SCAN_TOTAL_BUDGET = 900
        CORE_SYNTHESIS_RESERVE = 220
        FINAL_OUTPUT_RESERVE = 120
        REALITY_PHASE_MAX_BUDGET = 420
        DETAIL_REPORT_FULL_MIN = 120
        DETAIL_REPORT_COMPACT_MIN = 75
        print(f"🚀 --- LIVE SCAN INITIATED FOR: {companyName} (budget: {SCAN_TOTAL_BUDGET}s) ---")
        if job_id:
            _update_scan_job(job_id, phase="initializing", progress=5)
            _append_scan_log(job_id, "Initializing scan pipeline")

        def _scan_elapsed() -> float:
            return time.time() - scan_start

        def _scan_remaining() -> float:
            return max(0.0, SCAN_TOTAL_BUDGET - _scan_elapsed())

        def _remaining_after_final_reserve() -> float:
            return max(0.0, _scan_remaining() - FINAL_OUTPUT_RESERVE)

        def _remaining_after_synthesis_reserves() -> float:
            return max(0.0, _scan_remaining() - CORE_SYNTHESIS_RESERVE - FINAL_OUTPUT_RESERVE)

        def _budget_snapshot() -> dict:
            return {
                "elapsed": int(_scan_elapsed()),
                "remaining": int(_scan_remaining()),
                "after_final_reserve": int(_remaining_after_final_reserve()),
                "after_core_and_final_reserve": int(_remaining_after_synthesis_reserves()),
                "core_synthesis_reserve": CORE_SYNTHESIS_RESERVE,
                "final_output_reserve": FINAL_OUTPUT_RESERVE,
            }

        def _log_budget_state(label: str) -> None:
            snap = _budget_snapshot()
            print(
                f"   ⏱️ [Budget] {label}: elapsed={snap['elapsed']}s | "
                f"remaining={snap['remaining']}s | after_final_reserve={snap['after_final_reserve']}s | "
                f"after_core+final_reserve={snap['after_core_and_final_reserve']}s"
            )

        # Model pipeline: Sonnet 4.6 + Web Search (forensic) | GPT/Claude financial search | Opus/GPT/Sonnet synthesis.

        # --- PROCESS FILES ---
        combined_text = reportText if reportText else ""
        uploaded_doc_names = []
        
        if files:
            print(f"   [Upload] Processing {len(files)} uploaded documents...")
            for file in files:
                filename = file.get("filename", "uploaded_document")
                doc_text = extract_text_from_uploaded_content(filename, file.get("content", b""))
                if doc_text:
                    combined_text += f"\n\n--- INTERNAL DOCUMENT: {filename} ---\n{doc_text}"
                    uploaded_doc_names.append(filename)
        
        # --- STEP 1: SCANNER 1 (Intent) ---
        if job_id:
            _update_scan_job(job_id, phase="intent_analysis", progress=20)
            _append_scan_log(job_id, "Running intent analysis")
        target_url = companyUrl
        company_subsidiaries = None

        if not target_url:
            for c in TOP_100_COMPANIES:
                if c['name'].lower() == companyName.lower():
                    target_url = c['url']
                    company_subsidiaries = c.get('subsidiaries')
                    break
        else:
            # Also check for subsidiaries even when URL is provided
            for c in TOP_100_COMPANIES:
                if c['name'].lower() == companyName.lower():
                    company_subsidiaries = c.get('subsidiaries')
                    break

        if not combined_text or len(combined_text) < 100:
            if target_url: combined_text = scrape_strategic_content(target_url, companyName, subsidiaries=company_subsidiaries)
            else: combined_text = scrape_strategic_content(None, companyName)

        print(f"   🔄 [Scanner 1] Analyzing intent for {companyName}...")
        s1_start = time.time()
        intent_results = analyze_intent(combined_text)
        print(f"   ✅ [Scanner 1] Intent analysis complete in {int((time.time() - s1_start) * 1000)}ms → Score: {intent_results['score']}/100")

        # --- LOAD PREVIOUS SOURCE CACHE ---
        # Previous Scanner outputs are not longitudinal company evidence. Model,
        # prompt and gate changes make old scores, diagnoses and classifications
        # incomparable. Only source URLs/content may be reused, and Scanner 2 must
        # reset and freshly verify their trust state before analysis.
        previous_context = None
        existing_data = None
        try:
            existing_data = storage.get_company_data(companyName)
            if existing_data and existing_data.get("lastUpdated"):
                previous_context = {
                    "last_scanned": existing_data.get("lastUpdated"),
                    "previous_evidence": existing_data.get("evidence_links", [])[:24],
                    "scanner_evidence_quarantine": existing_data.get("scanner_evidence_quarantine", [])[:30],
                    "context_contract": "source_discovery_only",
                }
                print(
                    f"   [History] Previous source cache found (latest: {existing_data.get('lastUpdated')}). "
                    "Scores, summaries and diagnoses excluded from current reasoning."
                )
            else:
                print(f"   [History] No previous scan found. Running first-time analysis.")
        except Exception as e:
            print(f"   [History] Could not load previous data (non-critical): {e}")

        company_evidence_candidates = []
        company_evidence_cached_extracts = 0
        try:
            evidence_lookup = buying_storage.get_company_evidence_database(companyName)
            if evidence_lookup.get("exists"):
                company_evidence_candidates, evidence_stats = _company_evidence_db_scanner_candidates(
                    evidence_lookup.get("database"),
                    limit=20,
                )
                company_evidence_cached_extracts = int(evidence_stats.get("cached_extracts", 0) or 0)
                if company_evidence_candidates:
                    if previous_context is None:
                        previous_context = {}
                    previous_context["company_evidence_db_candidates"] = company_evidence_candidates
                    jury_counts = evidence_stats.get("jury_counts") or {}
                    previous_context["company_evidence_db_jury_counts"] = jury_counts
                    previous_context["company_evidence_db_missing_juries"] = [
                        jury for jury in (
                            "ARCHITECTURAL JURY (Engineering Reality)",
                            "STRATEGIC JURY (Business Model, Procurement & Customer Experience)",
                            "CULTURAL JURY (Talent & Psychological Safety)",
                            "AI & DATA JURY (Future Readiness)",
                            "SERVICE PORTFOLIO JURY (Delivery Reality)",
                        )
                        if not jury_counts.get(jury)
                    ]
                    skipped = evidence_stats.get("skipped", 0)
                    skip_reasons = evidence_stats.get("skip_reasons", {})
                    print(
                        f"   [Company Evidence DB] Loaded {evidence_stats.get('candidates', 0)} ranked candidate source(s) "
                        f"from {evidence_stats.get('eligible_before_limit', 0)} eligible / "
                        f"{evidence_stats.get('evaluated', 0)} evaluated record(s), "
                        f"{evidence_stats.get('cached_extracts', 0)} cached extract(s), "
                        f"{evidence_stats.get('governed_admitted', 0)} governed admission(s), "
                        f"{skipped} skipped; "
                        f"jury coverage {len(jury_counts)}/5."
                    )
                    if evidence_stats.get("limit_reached"):
                        print(
                            f"   [Company Evidence DB] Selection capped at top {evidence_stats.get('limit')} "
                            "after score/diversity ranking."
                        )
                    if skip_reasons:
                        print(f"   [Company Evidence DB] Skipped reasons: {skip_reasons}")
                    if previous_context["company_evidence_db_missing_juries"]:
                        print(
                            "   [Company Evidence DB] Seed coverage missing jury lane(s): "
                            + ", ".join(jury.split(" JURY")[0] for jury in previous_context["company_evidence_db_missing_juries"])
                        )
                else:
                    skipped = evidence_stats.get("skipped", 0)
                    print(f"   [Company Evidence DB] Database found, but no Scanner-safe candidate sources available ({skipped} skipped).")
            else:
                print("   [Company Evidence DB] No source database found; Scanner will use normal search only.")
        except Exception as e:
            print(f"   [Company Evidence DB] Optional lookup skipped: {e}")

        # --- STEP 2: SCANNER 2 (Reality) + FINANCIAL HEALTH (Parallel) ---
        if job_id:
            _update_scan_job(job_id, phase="reality_and_financial", progress=45)
            _append_scan_log(job_id, "Running reality and financial analysis")
        # Allocate time: reserve protected time for core synthesis and final outputs.
        # Without this cap, difficult evidence searches can consume the entire live
        # scan budget and force AI Diagnosis / WHY-WHAT / Pitch to be skipped.
        _log_budget_state("before reality search")
        elapsed_so_far = time.time() - scan_start
        reality_budget = int(
            SCAN_TOTAL_BUDGET
            - elapsed_so_far
            - CORE_SYNTHESIS_RESERVE
            - FINAL_OUTPUT_RESERVE
        )
        reality_budget = max(180, min(REALITY_PHASE_MAX_BUDGET, reality_budget))
        reality_outer_timeout = reality_budget + 10  # small buffer over internal budget
        scanner_research_loops = 2
        evidence_jury_counts = (previous_context or {}).get("company_evidence_db_jury_counts", {}) if isinstance(previous_context, dict) else {}
        evidence_missing_juries = (previous_context or {}).get("company_evidence_db_missing_juries", []) if isinstance(previous_context, dict) else []
        if (
            len(company_evidence_candidates) >= 14
            and company_evidence_cached_extracts >= 10
            and len(evidence_jury_counts) >= 5
            and not evidence_missing_juries
        ):
            scanner_research_loops = 1
            previous_context["company_evidence_db_strong_coverage"] = True
            print(
                f"   [Company Evidence DB] Strong jury-aware cached source coverage detected "
                f"({len(company_evidence_candidates)} candidates, {company_evidence_cached_extracts} cached extracts) "
                "→ limiting live re-search loops."
            )
        elif company_evidence_candidates:
            print(
                f"   [Company Evidence DB] Cached source coverage is partial "
                f"({len(company_evidence_candidates)} candidates, {company_evidence_cached_extracts} cached extracts, "
                f"{len(evidence_jury_counts)}/5 juries) → keeping targeted live discovery active."
            )
        # Get employee count for stakeholder seniority filtering
        _scan_meta = get_company_metadata(companyName) or {}
        _employee_count = _scan_meta.get("employees")

        print(
            f"   🔄 [Scanner 2 + Financial] Starting parallel execution for {companyName} "
            f"(reality budget: {reality_budget}s; core synthesis reserve: {CORE_SYNTHESIS_RESERVE}s; "
            f"final reserve: {FINAL_OUTPUT_RESERVE}s)..."
        )
        s2_start = time.time()

        with concurrent.futures.ThreadPoolExecutor(max_workers=2) as executor:
            reality_future = executor.submit(
                x_ray_search, companyName, target_url,
                model_id=None, intent_score=intent_results["score"],
                previous_context=previous_context, max_research_loops=scanner_research_loops,
                subsidiaries=company_subsidiaries,
                time_budget=reality_budget,
                employees=_employee_count
            )
            financial_future = executor.submit(
                financial_health_search, companyName, target_url
            )

            try:
                reality_results = reality_future.result(timeout=reality_outer_timeout)
            except (concurrent.futures.TimeoutError, Exception) as e:
                reality_elapsed = int(time.time() - s2_start)
                print(
                    f"   ❌ [Scanner 2] Reality search failed for {companyName}: "
                    f"{type(e).__name__}: {repr(e)} "
                    f"(elapsed={reality_elapsed}s, internal_budget={reality_budget}s, "
                    f"outer_timeout={reality_outer_timeout}s, scan_budget={_budget_snapshot()})"
                )
                # Prior Scanner conclusions are not longitudinal company facts.
                # Historical URLs already entered Scanner 2 as candidate seeds;
                # never resurrect an old score, checklist, diagnosis, or verdict.
                print("   ⚠️ [Scanner 2] Current reality unavailable; previous scans remain source-discovery cache only.")
                reality_results = {
                    "score": 50,
                    "checklist": [],
                    "links": [],
                    "key_people": [],
                    "is_simulated": True,
                    "confidence_score": 0,
                    "washing_flag": False,
                    "current_reality_unavailable": True,
                    "history_reuse_policy": "source_urls_only",
                }

            # Financial search is non-blocking. Prior Scanner financial scores are
            # not a valid substitute for a failed current lookup.
            try:
                financial_results = financial_future.result(timeout=120)
            except Exception as e:
                print(f"   [Financial Health] Failed (non-critical): {e}")
                financial_results = {
                    "financial_index": None,
                    "sub_scores": {"revenue_trend": None, "profitability": None, "financial_stability": None, "investment_signal": None},
                    "financial_findings": [],
                    "financial_confidence": 0,
                    "is_simulated": True,
                    "current_financial_unavailable": True,
                    "history_reuse_policy": "disabled",
                }

        if (
            reality_results.get("is_simulated")
            and not reality_results.get("links")
            and (
                (previous_context and previous_context.get("previous_evidence"))
                or company_evidence_candidates
            )
        ):
            previous_links = []
            prior_quarantine_urls = set()
            for item in (previous_context or {}).get("scanner_evidence_quarantine", []) if previous_context else []:
                value = str((item or {}).get("canonical_url") or (item or {}).get("url") or "").strip()
                if not value:
                    continue
                parsed = urlparse(value if "://" in value else f"https://{value}")
                host = parsed.netloc.lower().removeprefix("www.")
                path = re.sub(r"/+", "/", parsed.path or "/").rstrip("/") or "/"
                prior_quarantine_urls.add(f"{host}{path}".lower())
            if previous_context:
                for ev in previous_context.get("previous_evidence", []) or []:
                    ev_url = str(ev.get("url") or "").strip()
                    parsed = urlparse(ev_url if "://" in ev_url else f"https://{ev_url}")
                    ev_key = f"{parsed.netloc.lower().removeprefix('www.')}{(re.sub(r'/+', '/', parsed.path or '/').rstrip('/') or '/')}".lower()
                    if ev_key in prior_quarantine_urls:
                        continue
                    if historical_source_can_revalidate(ev):
                        previous_links.append(prepare_historical_revalidation_candidate(ev))
            if previous_links:
                previous_links = collapse_active_evidence(previous_links)[:8]
                print(
                    f"   🔄 [Scanner 2] Empty live reality result recovered with "
                    f"{len(previous_links)} historical URL candidate(s) with all prior verdicts reset."
                )
                reality_results = {
                    "score": existing_data.get("reality_score", 50) if existing_data else 50,
                    "checklist": existing_data.get("reality_checklist", []) if existing_data else [],
                    "links": previous_links,
                    "key_people": [],
                    "is_simulated": False,
                    "confidence_score": max(25, int((existing_data or {}).get("confidence_score", 0) or 0)),
                    "washing_flag": (existing_data or {}).get("washing_flag", False),
                    "washing_type": (existing_data or {}).get("washing_type"),
                    "historical_revalidation_pending": True,
                    "forensic_fallback": True,
                    "forensic_fallback_reason": "empty_live_result_reused_previous_evidence",
                }
            elif company_evidence_candidates:
                seeded_links = []
                for idx, candidate in enumerate(company_evidence_candidates[:8], start=1):
                    url = candidate.get("url") or ""
                    if not url:
                        continue
                    seeded_links.append({
                        "title": candidate.get("title") or candidate.get("source_title") or f"Company Evidence DB source {idx}",
                        "snippet": candidate.get("snippet") or candidate.get("source_quote") or candidate.get("cached_excerpt") or "",
                        "url": url,
                        "source": candidate.get("source") or urlparse(url).netloc.replace("www.", ""),
                        "source_type": "company_evidence_db",
                        "source_database": "company_evidence_db",
                        "source_status": "candidate_revalidation_pending",
                        "forensic_seed_source": True,
                    })
                if seeded_links:
                    print(
                        f"   🧭 [Scanner 2] Empty live reality result recovered with "
                        f"{len(seeded_links)} Company Evidence DB source seed(s)."
                    )
                    reality_results = {
                        "score": 50,
                        "checklist": [],
                        "links": seeded_links,
                        "key_people": [],
                        "is_simulated": False,
                        "confidence_score": 30,
                        "washing_flag": False,
                        "washing_type": None,
                        "forensic_fallback": True,
                        "forensic_fallback_reason": "empty_live_result_used_company_evidence_db",
                    }

        print(f"   ✅ [Scanner 2] Reality check complete in {int((time.time() - s2_start) * 1000)}ms → Score: {reality_results['score']}/100, Confidence: {reality_results.get('confidence_score', 0)}/100")
        if financial_results.get("financial_index") is not None:
            print(f"   ✅ [Financial Health] Index: {financial_results['financial_index']}/100, Confidence: {financial_results['financial_confidence']}/100")

        # --- EMPTY REALITY DETECTION ---
        # All models failed → Scanner 2 returned score=0, confidence=0 (is_simulated placeholder).
        # Flag for downstream degradation so synthesis doesn't treat zeros as real evidence.
        _s2_score = reality_results.get("score", 0)
        _s2_conf = reality_results.get("confidence_score", 0)
        _s2_empty = (_s2_score == 0 and _s2_conf == 0)
        if _s2_empty:
            print(f"   ⚠️ [Scanner 2] Empty result detected (score=0, confidence=0) — will flag scan_quality='empty_reality'")

        # Cross-scan score drift is intentionally not analyzed. Scanner telemetry
        # can change after model/prompt revisions and is not a company trend.
        reality_results["cross_scan_reasoning"] = "disabled"

        gap = intent_results["score"] - reality_results["score"]
        confidence = reality_results.get("confidence_score", 50)
        washing_flag = reality_results.get("washing_flag", False)
        washing_type = reality_results.get("washing_type", None)
        key_people = reality_results.get("key_people", [])
        # Preserve previous key_people if new scan returned empty
        if not key_people and existing_data:
            prev_kp = existing_data.get("key_people", [])
            if prev_kp:
                key_people = prev_kp
                print(f"   [Key People] Preserved {len(prev_kp)} stakeholder(s) from previous scan (new scan returned none)")

        # --- KEY PEOPLE NAME VALIDATION ---
        # Filter out stakeholders with incomplete names (e.g., first name only,
        # placeholders like "Pirja (Last Name Not Specified)").
        # This catches both newly extracted and preserved-from-previous-scan entries.
        from scanner_two import _validate_key_people
        key_people = _validate_key_people(key_people, companyName, employees=_employee_count)

        stakeholder_enrichment_skipped = "moved_to_buying_insight"
        if key_people:
            print(f"   ⏭ [Stakeholder Intel] Live enrichment skipped for {len(key_people)} stakeholder(s) — moved to Buying Insight")
            for person in key_people:
                if isinstance(person, dict):
                    person.setdefault("stakeholder_intelligence_mode", "lightweight_scan_record")

            # Keep a cheap compatibility filter for old enriched records.
            departed = [p for p in key_people if p.get("linkedin_insights", {}).get("still_at_company") is False]
            if departed:
                departed_names = ", ".join(p.get("name", "?") for p in departed)
                print(f"   🚫 [Stakeholder Filter] Removing {len(departed)} departed stakeholder(s): {departed_names}")
                key_people = [p for p in key_people if p.get("linkedin_insights", {}).get("still_at_company") is not False]

        # --- LOGIC CONSISTENCY CHECK ---
        # CRITICAL FIX: "Washing" is impossible if Reality >= Intent.
        # It implies they are doing MORE than they say.
        if reality_results["score"] >= intent_results["score"]:
            if washing_flag:
                print(f"   [Logic Override] Washing Flag set to FALSE. Reality ({reality_results['score']}) >= Intent ({intent_results['score']}). Reality exceeds narrative.")
            washing_flag = False
            washing_type = None
        
        # Also impossible if Ambition is very low (e.g. < 40)
        if intent_results["score"] < 40:
             if washing_flag:
                print(f"   [Logic Override] Washing Flag set to FALSE. Intent ({intent_results['score']}) is too low to qualify as Washing.")
             washing_flag = False
             washing_type = None

        # --- EARLY DOMAIN RESOLUTION (needed for foresight before full metadata step) ---
        meta_early = get_company_metadata(companyName) or {}
        company_domain = meta_early.get("domain", "Other")
        if company_domain == "Other":
            from domain_classifier import infer_domain
            _cached = existing_data.get("domain") if existing_data else None
            company_domain = infer_domain(companyName, target_url or "", reality_results, financial_results, cached_domain=_cached)

        # --- EARLY ARCHETYPE (needed for foresight before synthesis step) ---
        fin_score_early = financial_results.get("financial_index")
        fin_for_archetype_early = fin_score_early if fin_score_early is not None else intent_results["score"]
        if reality_results["score"] >= 50:
            archetype = "Thriving" if fin_for_archetype_early >= 50 else "Modernizing"
        else:
            archetype = "Cash Cow" if fin_for_archetype_early >= 50 else "At Risk"

        # Freeze the current-reality classification before foresight and
        # activation consume it. Later synthesis may record a score observation,
        # but it must not silently rewrite the reality baseline after downstream
        # reasoning has already used it.
        frozen_classification_snapshot = {
            "reality_score": reality_results.get("score"),
            "financial_health": fin_score_early,
            "financial_health_for_archetype": fin_for_archetype_early,
            "adaptability_archetype": archetype,
            "classification_version": "scanner_current_reality_pre_foresight_v1",
            "frozen_at": datetime.utcnow().isoformat(),
            "source": "scanner2_plus_financial_before_foresight",
        }
        reality_results["frozen_classification_snapshot"] = frozen_classification_snapshot

        # --- STEP 2.7: STRATEGIC FORESIGHT ---
        foresight_data = None
        foresight_links = []
        try:
            from strategic_foresight import (
                generate_foresight, validate_foresight_urls,
                extract_foresight_links, retry_failed_sources, enforce_source_quality_gate,
            )
            foresight_start = time.time()
            _log_budget_state("before foresight")
            foresight_fallback_headroom = _remaining_after_synthesis_reserves()
            allow_registry_fallback = foresight_fallback_headroom >= 90
            foresight_fallback_budget = 0
            foresight_fallback_deadline = None
            if allow_registry_fallback:
                foresight_fallback_budget = 120 if foresight_fallback_headroom >= 180 else 90
                foresight_fallback_deadline = time.time() + foresight_fallback_budget
                print(
                    f"   ⏱️ [Foresight] Registry fallback budget: {foresight_fallback_budget}s "
                    f"(protected reserves remain after fallback headroom={int(foresight_fallback_headroom)}s)"
                )
            else:
                print(
                    f"   ⏭️ [Foresight] Registry fallback disabled — only "
                    f"{int(foresight_fallback_headroom)}s available before protected synthesis/final reserves."
                )
            foresight_data = generate_foresight(
                company_name=companyName,
                domain=company_domain,
                archetype=archetype,
                reality_score=reality_results["score"],
                financial_index=financial_results.get("financial_index"),
                top_findings=reality_results.get("checklist", [])[:8],
                allow_registry_fallback=allow_registry_fallback,
                fallback_budget_sec=foresight_fallback_budget,
                fallback_deadline_ts=foresight_fallback_deadline,
            )
            foresight_ms = int((time.time() - foresight_start) * 1000)
            if callable(foresight_data):
                print("   ⚠️ [Foresight] generate_foresight returned callable/function; suppressing foresight for this scan")
                foresight_data = None
            if foresight_data and not isinstance(foresight_data, dict):
                print(f"   ⚠️ [Foresight] Expected dict, got {type(foresight_data).__name__}; suppressing foresight for this scan")
                foresight_data = None
            if foresight_data:
                foresight_data["dimension_forces"] = _safe_dict_list(foresight_data.get("dimension_forces", []), "foresight_data.dimension_forces")
                n_forces = len(foresight_data.get('dimension_forces', []))
                is_kb_first = foresight_data.get("kb_first", False)
                path_label = "KB-first" if is_kb_first else "search"
                print(f"   🔮 [Foresight] Complete: {n_forces} forces via {path_label} path ({foresight_ms}ms)")

                if is_kb_first:
                    # --- KB-FIRST PATH: URLs already verified, sources pre-validated ---
                    # Skip URL verification, source retry, and quality gate.
                    # Go straight to link extraction. Central KB signals already
                    # use the company-neutral foresight-context contract; the
                    # former company-focused reality_check_foresight pass was a
                    # residual analysis layer and could incorrectly demote them.
                    foresight_data["insufficient_evidence"] = False
                    foresight_links = extract_foresight_links(foresight_data, company_name=companyName, domain=company_domain)
                    foresight_links = _safe_dict_list(foresight_links, "foresight_links")
                    passes_gate = True
                    print(f"   📚 [Foresight KB-First] Skipped URL verify + source retry (pre-validated)")
                else:
                    # --- FULL SEARCH PATH: need validation pipeline ---
                    # STEP 2.7.1: URL verification (parallel HEAD/GET probes)
                    foresight_data, fs_verified, fs_dead = validate_foresight_urls(foresight_data)

                    # STEP 2.7.2: SOURCE RETRY LOOP
                    if fs_verified == 0 or (fs_dead > 0 and fs_verified < n_forces):
                        foresight_data, newly_verified = retry_failed_sources(foresight_data, domain=company_domain)
                        fs_verified += newly_verified

                    # STEP 2.7.2b: SOURCE-CLAIM RELEVANCE VALIDATION
                    try:
                        from source_claim_validator import validate_foresight_claims
                        validate_foresight_claims(
                            foresight_data,
                            max_checks=6,
                            company_name=companyName,
                            company_domain=company_domain,
                        )
                    except Exception as scv_err:
                        print(f"   ⚠️ [Foresight Source-Claim] Validation skipped: {scv_err}")

                    # STEP 2.7.3: QUALITY GATE
                    foresight_data, passes_gate = enforce_source_quality_gate(foresight_data)

                    # Extract validated sources into foresight_links
                    foresight_links = extract_foresight_links(foresight_data, company_name=companyName, domain=company_domain)
                    foresight_links = _safe_dict_list(foresight_links, "foresight_links")

                if not passes_gate:
                    print(f"   ⚠️ [Foresight] Insufficient evidence — foresight flagged as low-trust")
            else:
                print(f"   ⚠️ [Foresight] Returned no data ({foresight_ms}ms)")
        except Exception as foresight_err:
            import traceback
            print(f"   ⚠️ [Foresight] Failed (non-fatal): {foresight_err}")
            print(traceback.format_exc())

        # --- STEP 2.7.5: MERGE DB FORESIGHT LINKS ---
        # Reuse only links that satisfy the current foresight-context contract.
        foresight_links = _safe_dict_list(foresight_links, "foresight_links before DB merge")
        if existing_data and existing_data.get("foresight_links"):
            existing_fl = _safe_dict_list(existing_data.get("foresight_links", []), "existing_data.foresight_links")
            new_urls = {l.get("url") for l in foresight_links if isinstance(l, dict) and l.get("url")}
            merged_count = 0
            for efl in existing_fl:
                if (efl.get("url")
                    and efl["url"] not in new_urls
                    and is_foresight_context_eligible_source(efl)):
                    foresight_links.append(efl)
                    new_urls.add(efl["url"])
                    merged_count += 1
            if merged_count:
                print(f"   📎 [Foresight Links] Merged {merged_count} eligible context links from previous scan")

        foresight_links = sanitize_foresight_collection(foresight_links, domain=company_domain)  # final foresight card sanitation
        if not foresight_data and foresight_links:
            foresight_data = _fallback_foresight_from_links(companyName, foresight_links, domain=company_domain)
            if foresight_data:
                print("   📎 [Foresight] Current synthesis unavailable; retained validated foresight links as context-only fallback")

        from scanner_two import get_clean_domain as _get_clean_domain
        _company_site_domain = _get_clean_domain(target_url) if target_url else ""
        financial_evidence_links = _financial_findings_to_evidence_links(
            financial_results.get("financial_findings", []),
            companyName,
            _company_site_domain,
        )
        if financial_evidence_links:
            try:
                from source_claim_validator import validate_source_claims
                validate_source_claims(
                    financial_evidence_links,
                    max_checks=min(4, len(financial_evidence_links)),
                    max_workers=2,
                    company_name=companyName,
                    company_domain=_company_site_domain,
                )
            except Exception as fin_ev_err:
                print(f"   ⚠️ [Financial Evidence] Source validation skipped: {fin_ev_err}")
            financial_evidence_links = annotate_evidence_links(financial_evidence_links, _company_site_domain, companyName)

        # --- STEP 3: SYNTHESIS ---
        if job_id:
            _update_scan_job(job_id, phase="synthesis", progress=75)
            _append_scan_log(job_id, "Generating synthesis")
        summary = "AI Summary Unavailable"
        pitch = "Pitch Unavailable"
        strategic_synthesis = ""
        generation_company_manifest = []
        generation_manifest = []
        evidence_absence_signals = []
        evidence_absence_context = ""
        evidence_based_risks = []
        activation_pairs = []
        why_proposals = []
        what_proposals = []
        what_progress_proposals = []
        scan_quality = "empty_reality" if _s2_empty else "complete"  # downgraded if phases are skipped due to time

        anthropic_key = clean_env_var(os.environ.get("ANTHROPIC_API_KEY"))
        has_synth_key = anthropic_key
        if has_synth_key:
            try:
                claude_client = anthropic.Anthropic(api_key=anthropic_key) if anthropic_key else None
                intent_narrative = json.dumps(intent_results["breakdown"])
                synthesis_source_pool = company_evidence_candidates + (reality_results.get("links") or []) + financial_evidence_links
                verified_links = _analysis_evidence_links(synthesis_source_pool)

                # Freeze the current-reality evidence packet before generation.
                # Stable EVD IDs are the contract between diagnosis and audit;
                # the verifier later receives this same canonical manifest.
                generation_company_manifest = build_evidence_manifest(verified_links, [])
                evidence_list = evidence_prompt_packet(
                    generation_company_manifest,
                    kind="company_evidence",
                    limit=24,
                )
                low_evidence_context = ""
                _sufficiency_preview = _evidence_sufficiency_state(synthesis_source_pool, financial_evidence_links)
                if _sufficiency_preview.get("status") != "complete":
                    low_evidence_context = (
                        f"LOW-EVIDENCE MODE: {_sufficiency_preview['label']}. "
                        f"Promoted hard-fact source-register evidence is limited "
                        f"({_sufficiency_preview['promoted_company_sources']} source(s); "
                        f"{_sufficiency_preview['promoted_financial_sources']} financial source(s); "
                        f"{_sufficiency_preview.get('scoped_context_sources', 0)} scoped-context source(s)). "
                        "You may use verified structured financial findings and scoped context for bounded diagnosis, "
                        "but must state when hard public proof is limited and avoid precise company claims unless "
                        "explicitly supported by hard-fact source text."
                    )
                ai_readiness_evidence = _ai_readiness_evidence_state(
                    intent_results.get("breakdown", []),
                    synthesis_source_pool,
                    reality_results.get("checklist", []),
                )
                ai_readiness_context = _ai_readiness_prompt_note(ai_readiness_evidence)
                evidence_absence_signals = build_evidence_absence_signals(
                    intent_results.get("breakdown", []),
                    synthesis_source_pool,
                    reality_results.get("checklist", []),
                    foresight_links,
                    financial_evidence_links,
                )
                evidence_absence_context = format_absence_signals_for_prompt(evidence_absence_signals)
                # Run contradiction detection on the same evidence pool. Block
                # is empty when no quantitative disagreements are found, so the
                # injected slot stays harmless on clean cases.
                contradictions_block = _build_contradictions_block(verified_links, companyName)
                knowledge_context = get_evolving_framework()

                doc_context = ""
                if uploaded_doc_names:
                    doc_context = f"NOTE: Analysis includes confidential internal documents: {', '.join(uploaded_doc_names)}. Treat this as high-fidelity intent data."

                # --- ARCHETYPE LOGIC (Reality vs Financial) ---
                # Uses Reality Score and Financial Index to classify companies.
                # Fallback: if Financial Index is unavailable, use Intent Score.
                fin_score = financial_results.get("financial_index")
                fin_for_archetype = fin_score if fin_score is not None else intent_results["score"]
                if reality_results["score"] >= 50:
                    archetype = "Thriving" if fin_for_archetype >= 50 else "Modernizing"
                else:
                    archetype = "Cash Cow" if fin_for_archetype >= 50 else "At Risk"

                # Logic-aware Context
                washing_context = ""
                if washing_flag:
                    washing_context = f"CRITICAL: The forensic scanner DETECTED {washing_type or 'WASHING'}. High ambition narrative but low operational evidence."

                # Financial context
                financial_context = ""
                if financial_results.get("financial_index") is not None:
                    fi = financial_results
                    findings = fi.get('financial_findings', [])
                    findings_text = '; '.join(
                        f.get('assessment', '') if isinstance(f, dict) else ''
                        for f in findings[:5]
                    ) if findings else 'No specific findings'
                    financial_context = f"FINANCIAL HEALTH CONTEXT (do NOT cite numeric scores — the index is displayed separately): {findings_text}. You may reference financial health qualitatively (e.g. 'declining revenue', 'strong profitability') but never mention specific index numbers."

                # Previous scans are source-discovery caches only. Their scores,
                # diagnoses and classifications never enter company reasoning.

                # Build constraint-driven context for pitch and proposals
                _neg_items = [
                    item for item in reality_results.get("checklist", [])
                    if item.get("sentiment") == "negative" and is_generation_eligible_source(item)
                ]
                negative_summary = "\n".join([f"- {item.get('query', '')}: {item.get('snippet', 'No detail')}" for item in _neg_items[:6]])
                negative_findings_context = f"\nKey Negative Findings (Scanner 2):\n{negative_summary}" if negative_summary else ""

                scan_synthesis_guardrails = _shared_prompt_guardrails(include_diagnostic=True, include_foresight=False, include_claim_tier=True)
                synthesis_prompt = f"""You are a senior organizational analyst conducting a forensic modernization assessment.
You are conservative and evidence-bound. You would rather say "insufficient evidence" than risk a wrong claim. Your credibility depends on never overstating.
Your task: synthesize CURRENT company evidence into a precise, evidence-grounded diagnosis using the Knowledge Base framework below.
This phase establishes current reality only. Do not use macro foresight, stakeholder discovery, prior Scanner scores, prior diagnoses, or cross-scan trends.

### SHARED EVIDENCE / DIAGNOSIS / FORESIGHT GUARDRAILS
{scan_synthesis_guardrails}

METHODOLOGY: Read ALL evidence first. Score ONLY what evidence supports. Write ONLY what scores justify.
If evidence is thin, say so — a short honest analysis beats a long fabricated one.

### ABSOLUTE RULES (repeated throughout for emphasis)
1. NEVER fabricate findings — if evidence is thin, SAY SO.
2. NEVER invent organizational or people problems (leadership gaps, succession issues, morale claims, unfilled roles).
3. Every claim must trace to specific evidence in the SCAN DATA below. No evidence = no claim.
   End each factual premise with its stable evidence ID, for example [EVD-ABC123DEF456].
   A diagnostic conclusion may be new reasoning, but it must cite the premise IDs used to reach it.
   Evidence marked mode=scoped_context may support bounded interpretation, positioning, proof gaps,
   and capability context only. It cannot prove precise hard facts, outcomes, deployments, employee
   counts, revenue, or performance unless the same record is marked mode=hard_fact.
4. Do NOT extrapolate small signals into sweeping assessments (e.g., a Jira mention in a job ad does NOT reveal corporate strategy).
5. SCOPE & SOURCE QUALIFICATION — CRITICAL:
   - NARROW SCOPE: When an evidence item describes a limited population (a master's thesis, a single-department survey, a regional pilot, a study of N participants), you MUST include a scope qualifier in the same sentence (e.g., "in a survey of HR personnel", "in a 100-respondent study", "according to a master's thesis"). Never present a narrow-scope statistic as a company-wide truth.
   - ANTI-EXTRAPOLATION: NEVER extrapolate a narrow-scope finding into a macro-trend or section-level diagnosis. If a survey only covers the HR department, you are FORBIDDEN from writing sweeping claims like "enterprise adoption lags significantly", "the company struggles with AI", "internal landscape presents a material vulnerability", "AI Readiness remains highly fragmented", or "Pilot Purgatory across the firm". State the narrow fact exactly as it is, scoped to its actual population, without macro-commentary.
   - SELF-REPORTED DATA: If evidence comes from the company's own representatives or publications and provides CONCRETE operational metrics (e.g., "thousands of weekly users", "30% downtime reduction"), you must ingest and report these metrics, but qualify the source ("The company reports..."). Do not treat them as independently verified, but DO NOT omit them. Marketing fluff without concrete numbers ("AI-first company") may be skipped.
   - ENTITY SCOPE: If evidence carries entity_scope="local_legal_entity", "subsidiary", "former_name", or "parent_owner", qualify it in the same sentence and do not present it as unqualified company-wide evidence. Parent/owner evidence supports acquisition or ownership context only. Local legal-entity financial data supports that legal entity unless group-level evidence corroborates it.
6. BALANCING CONTRADICTIONS & PROPORTIONALITY — CRITICAL:
   - If a narrow-scope independent source contradicts a broad self-reported metric on the same topic, briefly state both side-by-side (e.g., "While the company reports thousands of weekly users on its internal AI tool, a departmental survey of HR personnel found only 9% report frequent use").
   - PROPORTIONALITY: You are diagnosing a multi-billion euro enterprise. Keep narrow departmental statistics confined to a single contextualised sentence within the relevant section. Do not let a single departmental survey dominate the technology, AI Readiness, or operational narrative of a global firm. Address the contradiction factually, weight it against company-wide evidence, and move on.
7. ANTI-EXTRAPOLATION ENFORCEMENT — Reject any sentence that takes a finding from a narrow-scope source (single department, single region, study of N participants, master's thesis) and uses generalising language to apply it to the company as a whole. Generalising language includes phrases that turn the finding into a corporate-wide diagnosis: any wording that expands the population beyond what the source covers. The conceptual rule overrides any specific phrase list.

### PRIMARY FRAMEWORK (KNOWLEDGE BASE — MANDATORY)
{knowledge_context}

You MUST use the terminology, archetypes, and criteria defined in the Knowledge Base above.
Every claim in the summary MUST trace back to specific evidence AND map to a Knowledge Base dimension.
Do NOT invent categories — use ONLY Knowledge Base vocabulary.
NEVER use internal codes (TAC-xxx, DIM-xxx, etc.) — always write the full concept name.

### SCAN DATA
Target: {companyName}
Financial Health Index: {fin_for_archetype} ({"actual financial index" if fin_score is not None else "fallback: using intent score"})
Summary Confidence: {confidence}/100

Evidence Found: {evidence_list}
Evidence modes: mode=hard_fact can support factual premises. mode=scoped_context can support bounded diagnosis/synthesis premises only; phrase it as "public company material indicates", "the source supports positioning/context", or "hard proof remains limited".
Intent Data: {intent_narrative}
{doc_context}
{washing_context}
{financial_context}
{low_evidence_context}
{ai_readiness_context}
{evidence_absence_context}
{negative_findings_context}

{contradictions_block}
### ANALYSIS PROTOCOL

STEP 1 — REALITY SCORE (evaluate before writing anything):
Score each Knowledge Base dimension (0-20) based on VERIFIED EVIDENCE ONLY:
  - Operating Model: Evidence of modern practices (DevOps, platform teams, stream-aligned)? Or legacy/waterfall/silos?
  - Strategy: Clear digital/modernization strategy with visible execution? Or vague/absent?
  - Culture: Learning culture, psychological safety? Evidence of open communication or blameless post-mortems? Or hierarchy/control/fear?
  - Service Portfolio: Product-led, API-first, composable architecture? Or monolithic/legacy?
  - AI Readiness: Concrete AI/ML adoption with measurable outcomes? Or just buzzwords/pilot purgatory?

Scoring rules:
  - No evidence for a dimension = score 10 (neutral). Never score generously without evidence.
  - Evidence of aspiration (website claims, press releases) without execution evidence = score 8-12, not higher.
  - Sum the 5 dimensions → 'reality_score' (0-100).
  - Do NOT evaluate intent_score or financial_index — those are immutable pipeline outputs.

STEP 2 — ARCHETYPE AUDIT ONLY (mechanical, from scores):
For internal audit only, derive an 'archetype' JSON field from YOUR reality_score + Financial Health Index ({fin_for_archetype}):
  - reality_score >= 50 AND Financial Health Index >= 50 → "Thriving"
  - reality_score >= 50 AND Financial Health Index < 50 → "Modernizing"
  - reality_score < 50 AND Financial Health Index >= 50 → "Cash Cow"
  - reality_score < 50 AND Financial Health Index < 50 → "At Risk"
This is a mechanical derivation for log comparison only. NEVER mention the archetype label in diagnostic_summary or evidence_based_risks.

STEP 3 — AI DIAGNOSIS SUMMARY:
Write a diagnostic narrative that reads like an expert assessment, not a template:
  - Structure: exactly 3-4 core paragraphs, each covering a distinct KB dimension (e.g. overall position
    & operating model / strategy & culture / service portfolio & AI readiness / financial context).
    Separate paragraphs with \n\n in the JSON string — this is required for rendering.
  - Do not add foresight implications here. External pressures belong only in Strategic Synthesis.
  - Target length: 250-375 words.
  - First sentence: State the company's current-reality position in plain business language without using an archetype label.
  - Body: Map 3-4 key findings to specific KB dimensions. For each finding, connect the evidence
    to the KB pattern it reveals (e.g., "Job postings emphasize hierarchical reporting lines,
    characteristic of Digital Taylorism in the Operating Model dimension").
  - Use KB terminology naturally: "Digital Taylorism", "Flow Efficiency", "Pilot Purgatory",
    "Modernization Washing" — but only where the evidence supports the pattern.
  - CLAIM-TIER WORDING: Evidence items are prefixed [A]-[E] as reading aids for you. Choose verbs that match the tier — for company self-claims use "states"/"positions itself as", for weak signals use "suggests"/"may indicate", for independent verification use "reported"/"confirmed". NEVER write [A], [B], [C], [D], [E] or the word "tier" in the output text.
  - If confidence < 50, state it: "Limited public evidence constrains this assessment."
  - If washing detected, name it: "{washing_type or 'Modernization Washing'}" with the specific gap.
  - Do NOT cite numeric scores in the text — scores display separately in the UI.
  - Cite the stable [EVD-*] premise IDs after every material factual or diagnostic sentence.
  - Do NOT fabricate findings. If evidence is sparse, acknowledge gaps rather than inventing patterns.
  - TEMPORAL PRECISION: When evidence contains the same metric for different periods (e.g. Q1 vs full-year, 2024 vs 2025), always use the most recent FULL-YEAR / ANNUAL figure — not interim or quarterly snapshots. If only a quarterly figure is available, label it explicitly (e.g. "as of Q1 2025"). Never present a quarterly number as if it were the annual result. Do not write that a metric is unpublished or unavailable if the evidence or financial context contains that metric for the latest annual period.
  - ORGANIZATIONAL CLAIMS PROHIBITION: NEVER claim or imply leadership gaps, unfilled executive roles, succession planning issues, management vacancies, or organizational restructuring unless the evidence EXPLICITLY states this. The absence of a person in scanner results does NOT mean the role is vacant — it means the scanner didn't find them. Do NOT invent risk factors about people, roles, or organizational structure to add analytical depth. If the evidence doesn't mention an organizational problem, the problem does not exist for this analysis.
  - Do NOT write these archetype labels in prose: Thriving, Modernizing, Cash Cow, At Risk.
  - CRITICAL SEPARATION: This section is strictly neutral diagnosis. Do NOT use sales language, calls-to-action, persuasion, or proposal framing here.

STEP 4 — EVIDENCE-BASED RISKS:
Generate 2-4 neutral risk bullets in a JSON array named evidence_based_risks.
  - Each risk must be diagnostic, evidence-backed, and free of sales/promotional language.
  - No solutions or CTA in this section; just what risk exists and why.
  - These risks become the locked input contract for the later Strategic Activation call.

### GUARDRAILS
- NEVER fabricate evidence, people, events, partnerships, or metrics not present in the scan data.
- NEVER invent temporal claims ("recently announced", "just launched", "this quarter").
- NEVER extrapolate small signals into sweeping assessments (e.g., a tool mentioned in a job ad does NOT reveal corporate strategy).
- If a KB dimension has no supporting evidence, score it neutrally and acknowledge the gap — do not speculate.
- Every factual claim in the summary must be traceable to a specific piece of evidence above.

### OUTPUT
Respond with valid JSON only. Required fields:
{{"diagnostic_summary": "...", "evidence_based_risks": ["..."], "reality_score": <int 0-100>, "archetype": "<Thriving|Modernizing|Cash Cow|At Risk>"}}
Note: diagnostic_summary must contain \n\n between 3-4 current-reality paragraphs. The archetype field is audit-only and must not appear in diagnostic_summary or evidence_based_risks. Do not add a foresight paragraph.
Do NOT generate strategic_synthesis, commercial_pitch, why_proposals, or what_proposals in this call. Those are generated later from the locked diagnosis, Knowledge Base, and foresight context."""

                # --- Diagnosis synthesis with fallback chain: Opus 4.8 → GPT 5.5 → Sonnet 4.6 ---
                # Opus is the judge — it does ONE thing: synthesize. Fallbacks degrade gracefully.
                # Opus uses Extended Thinking to reason through conflicting intent vs. reality
                # evidence before generating the synthesis — exactly what a "judge" model should do.
                response_text = ""
                synth_start = time.time()
                _log_budget_state("before synthesis")
                synth_remaining = SCAN_TOTAL_BUDGET - (time.time() - scan_start)
                # Dynamic per-attempt timeout: share remaining budget across
                # fallback attempts while protecting final report/export phases.
                _synth_available = max(0, int(synth_remaining) - FINAL_OUTPUT_RESERVE)
                if _synth_available >= 360:
                    OPUS_SYNTH_TIMEOUT = 120
                    GPT_SYNTH_TIMEOUT = 120
                    SONNET_SYNTH_TIMEOUT = 90
                elif _synth_available >= 240:
                    OPUS_SYNTH_TIMEOUT = 90
                    GPT_SYNTH_TIMEOUT = 90
                    SONNET_SYNTH_TIMEOUT = 60
                elif _synth_available >= 180:
                    OPUS_SYNTH_TIMEOUT = 75
                    GPT_SYNTH_TIMEOUT = 75
                    SONNET_SYNTH_TIMEOUT = 50
                elif _synth_available >= 120:
                    OPUS_SYNTH_TIMEOUT = 60
                    GPT_SYNTH_TIMEOUT = 45
                    SONNET_SYNTH_TIMEOUT = 40
                elif _synth_available >= 75:
                    OPUS_SYNTH_TIMEOUT = 0
                    GPT_SYNTH_TIMEOUT = 45
                    SONNET_SYNTH_TIMEOUT = 35
                else:
                    OPUS_SYNTH_TIMEOUT = 0
                    GPT_SYNTH_TIMEOUT = 0
                    SONNET_SYNTH_TIMEOUT = max(25, min(35, _synth_available - 5))

                _use_opus = OPUS_SYNTH_TIMEOUT > 0
                _use_gpt = GPT_SYNTH_TIMEOUT > 0
                _use_sonnet = SONNET_SYNTH_TIMEOUT > 0
                _synth_chain_budget = min(
                    _synth_available,
                    OPUS_SYNTH_TIMEOUT + GPT_SYNTH_TIMEOUT + SONNET_SYNTH_TIMEOUT,
                )

                def _synthesis_budget_left() -> int:
                    return max(0, int(SCAN_TOTAL_BUDGET - (time.time() - scan_start) - FINAL_OUTPUT_RESERVE))

                print(
                    f"   [Synthesis] Time remaining: {int(synth_remaining)}s | "
                    f"protected reserve: {FINAL_OUTPUT_RESERVE}s | "
                    f"chain budget: {_synth_chain_budget}s | "
                    f"timeouts: opus={OPUS_SYNTH_TIMEOUT}s,gpt={GPT_SYNTH_TIMEOUT}s,sonnet={SONNET_SYNTH_TIMEOUT}s | "
                    f"mode: {'full' if _use_opus else 'gpt/sonnet' if _use_gpt else 'sonnet-only' if _use_sonnet else 'skip'}"
                )

                if not _use_sonnet:
                    scan_quality = "partial_synthesis"
                    print(
                        f"   ⏭ [Synthesis] Only {_synth_available}s available before final-output reserve "
                        f"— skipping all synthesis models, scan stored as partial"
                    )
                else:
                    def _synth_opus():
                        return claude_client.messages.create(
                            model="claude-opus-4-8",
                            max_tokens=16000,
                            thinking={"type": "adaptive"},
                            extra_body={"output_config": {"effort": "xhigh"}},
                            messages=[{"role": "user", "content": synthesis_prompt}]
                        )

                    if _use_opus:
                        try:
                            _opus_timeout = min(
                                OPUS_SYNTH_TIMEOUT,
                                max(30, _synthesis_budget_left() - 10),
                            )
                            print(f"   🔄 [Synthesis] Attempting claude-opus-4-8 with Extended Thinking (timeout: {_opus_timeout}s)...")
                            synth_pool = concurrent.futures.ThreadPoolExecutor(max_workers=1)
                            synth_future = synth_pool.submit(_synth_opus)
                            try:
                                response = synth_future.result(timeout=_opus_timeout)
                                # Extended thinking response: [ThinkingBlock, TextBlock]
                                # Extract the text block (skip thinking blocks)
                                response_text = ""
                                if response.content:
                                    for block in response.content:
                                        if getattr(block, 'type', None) == 'text':
                                            response_text = block.text
                                            break
                                print(f"   ✅ [Synthesis] claude-opus-4-8 + Extended Thinking succeeded in {int((time.time() - synth_start) * 1000)}ms")
                            except concurrent.futures.TimeoutError:
                                synth_pool.shutdown(wait=False)
                                print(f"   ⏰ [Synthesis] claude-opus-4-8 timed out after {_opus_timeout}s")
                                raise Exception(f"Opus timeout after {_opus_timeout}s")
                            synth_pool.shutdown(wait=False)
                        except Exception as opus_err:
                            print(f"   ❌ [Synthesis] Opus 4.8 failed: {opus_err}")
                    else:
                        print(
                            f"   ⏭ [Synthesis] Skipping Opus 4.8 — {_synth_available}s synthesis budget "
                            f"reserved for shorter fallback path"
                        )

                    # Fallback 1: GPT 5.5 (text-only synthesis from verified evidence)
                    if not response_text and _use_gpt and _synthesis_budget_left() > 30:
                        try:
                            _gpt_timeout = min(
                                GPT_SYNTH_TIMEOUT,
                                max(30, _synthesis_budget_left() - 10),
                            )
                            print(f"   🔄 [Synthesis] Falling back to GPT 5.5 (timeout: {_gpt_timeout}s)...")
                            gpt_start = time.time()

                            def _synth_gpt():
                                return _call_openai_text(
                                    synthesis_prompt,
                                    model="gpt-5.5",
                                    max_output_tokens=12000,
                                    timeout=_gpt_timeout,
                                )

                            gpt_pool = concurrent.futures.ThreadPoolExecutor(max_workers=1)
                            gpt_future = gpt_pool.submit(_synth_gpt)
                            try:
                                response_text = gpt_future.result(timeout=_gpt_timeout)
                            except concurrent.futures.TimeoutError:
                                gpt_pool.shutdown(wait=False)
                                print(f"   ⏰ [Synthesis] GPT 5.5 timed out after {_gpt_timeout}s")
                                raise Exception(f"GPT 5.5 timeout after {_gpt_timeout}s")
                            gpt_pool.shutdown(wait=False)
                            print(f"   ✅ [Synthesis] GPT 5.5 succeeded in {int((time.time() - gpt_start) * 1000)}ms")
                        except Exception as gpt_err:
                            print(f"   ❌ [Synthesis] GPT 5.5 failed: {gpt_err}")

                    # Fallback 2: Claude Sonnet 4.6 (independent provider — survives OpenAI outages)
                    if not response_text and _use_sonnet and _synthesis_budget_left() > 20:
                        try:
                            _sonnet_timeout = min(
                                SONNET_SYNTH_TIMEOUT,
                                max(25, _synthesis_budget_left() - 10),
                            )
                            print(f"   🔄 [Synthesis] Falling back to Claude Sonnet 4.6 (timeout: {_sonnet_timeout}s)...")
                            m_start = time.time()

                            _sonnet_system = (
                                "CRITICAL: You are a CONSERVATIVE analyst. When in doubt, say 'insufficient evidence.' "
                                "Do NOT extrapolate. Do NOT infer organizational problems. Do NOT sound deeper than "
                                "your evidence supports. A boring, accurate summary beats an impressive, fabricated one. "
                                "NEVER invent leadership gaps, morale issues, succession problems, or strategic conclusions "
                                "from minor details like tool names in job ads."
                            )

                            def _synth_sonnet():
                                return claude_client.messages.create(
                                    model="claude-sonnet-4-6",
                                    max_tokens=4000,
                                    system=_sonnet_system,
                                    messages=[{"role": "user", "content": synthesis_prompt}],
                                )

                            m_pool = concurrent.futures.ThreadPoolExecutor(max_workers=1)
                            m_future = m_pool.submit(_synth_sonnet)
                            try:
                                sonnet_resp = m_future.result(timeout=_sonnet_timeout)
                                for block in sonnet_resp.content:
                                    if getattr(block, 'type', None) == 'text':
                                        response_text = block.text
                                        break
                                print(f"   ✅ [Synthesis] Claude Sonnet 4.6 succeeded in {int((time.time() - m_start) * 1000)}ms")
                            except concurrent.futures.TimeoutError:
                                m_pool.shutdown(wait=False)
                                print(f"   ⏰ [Synthesis] Claude Sonnet 4.6 timed out after {_sonnet_timeout}s")
                            m_pool.shutdown(wait=False)
                        except Exception as sonnet_err:
                            print(f"   ❌ [Synthesis] All models failed. Last error: {sonnet_err}")

                if not response_text and scan_quality == "complete":
                    scan_quality = "partial_synthesis"

                if response_text:
                    payload = extract_json_from_text(response_text)
                    if payload:
                         json_data = json.loads(payload, strict=False)
                         summary = strip_archetype_labels_from_text(sanitize_inference_wording(json_data.get("diagnostic_summary", json_data.get("summary", summary))))
                         pitch = strip_archetype_labels_from_text(sanitize_inference_wording(json_data.get("commercial_pitch", json_data.get("pitch", pitch)) or pitch))
                         evidence_based_risks = strip_archetype_labels_from_text(sanitize_inference_wording(json_data.get("evidence_based_risks", evidence_based_risks) or evidence_based_risks))
                         why_proposals = strip_archetype_labels_from_text(sanitize_inference_wording(json_data.get("why_proposals", why_proposals) or why_proposals))
                         what_proposals = strip_archetype_labels_from_text(sanitize_inference_wording(json_data.get("what_proposals", what_proposals) or what_proposals))

                         # --- SYNTHESIS SCORE OBSERVATION ---
                         # Synthesis may disagree with Scanner 2, but the current
                         # reality score is frozen before foresight. Record the
                         # disagreement for audit/recheck instead of rewriting the
                         # score after downstream reasoning has consumed it.
                         opus_reality = json_data.get("reality_score")
                         opus_intent = json_data.get("intent_score")
                         scanner2_reality = reality_results["score"]
                         scanner_intent = intent_results["score"]

                         if opus_reality is not None and isinstance(opus_reality, (int, float)):
                             opus_reality = max(0, min(100, int(opus_reality)))
                             delta_r = abs(opus_reality - scanner2_reality)
                             reality_results["scanner2_reality_score"] = scanner2_reality
                             reality_results["opus_reality_score"] = opus_reality

                             if delta_r <= 5:
                                 print(f"   ✅ [Score Verify] Reality: Scanner 2 {scanner2_reality} ≈ synthesis {opus_reality} (delta {delta_r}). Frozen score kept: {scanner2_reality}")
                             else:
                                 print(f"   ⚖️ [Score Verify] Reality: Scanner 2 {scanner2_reality} vs synthesis {opus_reality} (delta {delta_r}). Frozen score kept; re-audit recommended.")

                             reality_results["score_verification"] = "frozen_before_foresight"
                             reality_results["synthesis_score_observation"] = {
                                 "observed_reality_score": opus_reality,
                                 "delta_from_frozen_score": delta_r,
                                 "recommended_reaudit": delta_r > 5,
                             }

                         # Intent score is IMMUTABLE — Scanner 1's website analysis is the
                         # sole source of intent. Opus may disagree, but overriding intent
                         # would destroy the integrity delta (intent vs reality gap).
                         # Log Opus's opinion for diagnostics only.
                         if opus_intent is not None and isinstance(opus_intent, (int, float)):
                             opus_intent = max(0, min(100, int(opus_intent)))
                             delta_i = abs(opus_intent - scanner_intent)
                             intent_results["opus_intent_score"] = opus_intent
                             if delta_i <= 5:
                                 print(f"   ✅ [Score Verify] Intent: Scanner 1 {scanner_intent} ≈ Opus {opus_intent} (delta {delta_i}). Keeping Scanner 1 score.")
                             else:
                                 print(f"   ℹ️ [Score Verify] Intent: Scanner 1 {scanner_intent} vs Opus {opus_intent} (delta {delta_i}). Keeping Scanner 1 score (immutable).")
                                 gap = intent_results["score"] - reality_results["score"]

                         # --- ARCHETYPE AUDIT ---
                         # The model's archetype is retained only as a drift signal.
                         # Written diagnosis/summary prose must not contain archetype labels.
                         opus_archetype = json_data.get("archetype")
                         final_reality = reality_results["score"]
                         expected_archetype = ("Thriving" if fin_for_archetype >= 50 else "Modernizing") if final_reality >= 50 else ("Cash Cow" if fin_for_archetype >= 50 else "At Risk")

                         if opus_archetype and opus_archetype != expected_archetype:
                             print(f"   ⚖️ [Archetype Audit] Model wrote '{opus_archetype}' but frozen scores dictate '{expected_archetype}' (reality={final_reality}, financial={fin_for_archetype}). Written prose keeps archetype labels out.")
                         elif opus_archetype:
                             print(f"   ✅ [Archetype Audit] Model archetype '{opus_archetype}' matches frozen score-derived '{expected_archetype}'")
                         summary = strip_archetype_labels_from_text(summary)
                         evidence_based_risks = strip_archetype_labels_from_text(evidence_based_risks)
                         pitch = strip_archetype_labels_from_text(pitch)
                         why_proposals = strip_archetype_labels_from_text(why_proposals)
                         what_proposals = strip_archetype_labels_from_text(what_proposals)

                         # --- STRATEGIC ACTIVATION (WHY / WHAT / PITCH) ---
                         # This is deliberately split from diagnosis. The first
                         # call is the strategic judge; this second call activates
                         # only the locked diagnosis, risks, evidence, and KB.
                         _activation_left = _synthesis_budget_left()
                         if summary and _activation_left > 60:
                             generation_graph_input = {
                                 "evidence_links": verified_links,
                                 "foresight_links": foresight_links,
                                 "strategic_foresight": foresight_data or {},
                                 "evidence_absence_signals": evidence_absence_signals,
                             }
                             attach_evidence_graph(generation_graph_input)
                             generation_manifest = generation_graph_input.get("evidence_manifest", [])
                             activation_evidence_packet = evidence_prompt_packet(generation_manifest, limit=40)
                             activation_classification_context = build_classification_namespaces(
                                 adaptability_archetype=expected_archetype,
                                 foresight_force_profile={
                                     "source_contract": "strategic_foresight_macro_context",
                                     "evidence_kind": "foresight_context",
                                     "company_fact_eligible": False,
                                     "not_evidence_of_company_action": True,
                                 },
                             )
                             foresight_context = ""
                             if foresight_data:
                                 try:
                                     from strategic_foresight import format_foresight_for_synthesis
                                     foresight_context = format_foresight_for_synthesis(
                                         foresight_data,
                                         companyName,
                                         company_domain,
                                     )
                                 except Exception as fs_err:
                                     print(f"   ⚠️ [Activation] Foresight context unavailable: {type(fs_err).__name__}: {fs_err}")
                             activation_prompt = f"""You are writing the Strategic Synthesis and Activation layer for {companyName}.
The AI Diagnosis below is LOCKED. The Knowledge Base is the mandatory baseline. You may turn the diagnosis into WHY/WHAT proposals and a concise pitch, but you may not introduce new facts.

### KNOWLEDGE BASE BASELINE — MANDATORY
{knowledge_context}

### LOCKED DIAGNOSTIC CONTRACT
Reality score: {reality_results["score"]}
Intent score: {intent_results["score"]}
Financial context: {financial_context or "No financial interpretation available."}
Washing / contradiction context: {washing_context or "No washing flag detected."}
Archetype is audit-only and intentionally omitted from this writing context. Do not write labels such as Thriving, Modernizing, Cash Cow, or At Risk in strategic_synthesis, pitch, WHY, or WHAT.

Diagnosis:
{summary}

Evidence-based risks:
{json.dumps(evidence_based_risks, ensure_ascii=False)}

### CLASSIFICATION NAMESPACES
{json.dumps(activation_classification_context, ensure_ascii=False)}

### IMMUTABLE EVIDENCE MANIFEST
{activation_evidence_packet}
{negative_findings_context}
{foresight_context}
{evidence_absence_context}

### ACTIVATION RULES
1. Use the Knowledge Base terminology and proposal logic as the baseline.
2. Ground every WHY/WHAT/Pitch claim in the locked diagnosis, risks, or evidence packet above.
3. Do NOT add new facts, people, metrics, events, client names, partnerships, or timelines.
4. Always produce 2-3 paired WHY/WHAT items. If evidence is thin, frame pairs as validation or discovery triggers instead of omitting them.
5. WHY1 must pair with WHAT1, WHY2 with WHAT2, and WHY3 with WHAT3 if present.
6. Keep activation suggestive but evidence-bound: proposals, not promises.
7. Write strategic_synthesis as the explicit reasoning bridge between CURRENT COMPANY REALITY and EXTERNAL FORESIGHT PRESSURES.
8. A macro pressure never proves that the company has acted. Use modal language such as "may increase pressure", "creates a question", or "could affect".
9. Preserve contradictions between current-reality premises; explain what they may mean instead of averaging them away.
10. Do not compare Scanner outputs across scans or discuss Scanner performance.
11. Cite company premises with [EVD-*] and macro premises with [FOR-*]. Every material synthesis sentence must cite the IDs it uses.
12. If you use an absence-of-promoted-evidence signal, phrase it as an evidence/proof gap, never as proof that the capability is absent.
13. If partial/contextual support exists, say what is partially supported and what remains unconfirmed; do not write absolute "no source verifies" wording for a broader capability.
14. Treat evidence limits as one qualifier, not the whole thesis. Spend most of the synthesis on bounded interpretation, priorities, and pressure-readiness fit.

### STRATEGIC SYNTHESIS
Write 300-500 words. This is the deepest company-specific reasoning layer, not a sales pitch.
It must include:
- Current adaptation thesis: the company's present position in one clear opening sentence.
- 2-3 strategic tensions grounded in the locked diagnosis, risks, financial context, or evidence gaps.
- At least 2 external foresight pressure links, each connected to a current company readiness or proof condition.
- At least 1 financial-capacity implication.
- At least 2 time horizons (for example: "next 6-12 months" and "12-36 months").
- 1 uncertainty or invalidation condition that would change the thesis.
Do not use section headings inside strategic_synthesis. Write coherent executive prose.

### PITCH
Write a direct cold-outreach message to a C-level executive at {companyName}.
- 90-110 words.
- Exactly 3 short paragraphs separated by \\n\\n.
- Opening hook / concrete proposal / closing CTA.
- No generic service names.

### WHY / WHAT
Generate 2-3 paired WHY/WHAT items every time. Do not collapse to one pair because evidence is thin.
- WHY = why this matters for the customer: connect a detected challenge/problem to end-value, future strategy, risk, cost of inaction, or missed opportunity.
- WHAT_TO_DO = the concrete activity, diagnostic, workshop, analysis, decision model, roadmap, readiness assessment, or deliverable to propose next. It must be action-oriented.
- WHAT_PROGRESS_SHOWS = observable proof of progress after the activity. It may be qualitative or measurable, but do not invent numeric KPI targets unless already evidenced.
- The legacy "what" field must contain the same action-oriented text as WHAT_TO_DO for backward compatibility.
- Each WHY/WHAT field: 1-2 sentences.
- Each pair must have exactly one theme_type:
  - current_status: current evidence, operating-model, financial, proof, or portfolio reality.
  - foresight_driven: external pressure combined with current readiness or uncertainty.
  - proactive_opportunity: a bounded hypothesis or discovery trigger to validate, not a claim.
- Each pair must also include sales_use with the same allowed values: current_status, foresight_driven, proactive_opportunity.
- If evidence is thin, frame the pair as a validation/discovery trigger instead of omitting it.
- WHY1 pairs with WHAT1, WHY2 with WHAT2, and WHY3 with WHAT3 if present.

### OUTPUT
Respond with valid JSON only:
{{"strategic_synthesis": "...", "commercial_pitch": "...", "activation_pairs": [{{"theme_type": "current_status", "sales_use": "current_status", "theme": "...", "why": "...", "what": "same as what_to_do", "what_to_do": "...", "what_progress_shows": "..."}}, {{"theme_type": "foresight_driven", "sales_use": "foresight_driven", "theme": "...", "why": "...", "what": "same as what_to_do", "what_to_do": "...", "what_progress_shows": "..."}}], "why_proposals": ["WHY texts only"], "what_proposals": ["WHAT_TO_DO texts only"], "what_progress_proposals": ["WHAT_PROGRESS_SHOWS texts only"]}}"""

                             activation_text = ""
                             activation_timeout = min(95, max(50, _activation_left - 25))
                             try:
                                 print(f"   🔄 [Activation] Generating WHY/WHAT/Pitch from locked diagnosis via gpt-5.5 (timeout: {activation_timeout}s)...")
                                 act_start = time.time()

                                 def _activation_gpt():
                                     return _call_openai_text(
                                         activation_prompt,
                                         model="gpt-5.5",
                                         max_output_tokens=5000,
                                         timeout=activation_timeout,
                                     )

                                 act_pool = concurrent.futures.ThreadPoolExecutor(max_workers=1)
                                 act_future = act_pool.submit(_activation_gpt)
                                 try:
                                     activation_text = act_future.result(timeout=activation_timeout)
                                 except concurrent.futures.TimeoutError:
                                     act_pool.shutdown(wait=False)
                                     print(f"   ⏰ [Activation] GPT 5.5 timed out after {activation_timeout}s")
                                     raise Exception(f"GPT activation timeout after {activation_timeout}s")
                                 act_pool.shutdown(wait=False)
                                 print(f"   ✅ [Activation] GPT 5.5 complete ({int((time.time() - act_start) * 1000)}ms)")
                             except Exception as act_gpt_err:
                                 print(f"   ❌ [Activation] GPT 5.5 failed: {act_gpt_err}")

                             if not activation_text and _synthesis_budget_left() > 50:
                                 sonnet_activation_timeout = min(75, max(45, _synthesis_budget_left() - 20))
                                 try:
                                     print(f"   🔄 [Activation] Falling back to Claude Sonnet 4.6 (timeout: {sonnet_activation_timeout}s)...")
                                     act_start = time.time()

                                     def _activation_sonnet():
                                         return claude_client.messages.create(
                                             model="claude-sonnet-4-6",
                                             max_tokens=4500,
                                             system="You are a conservative activation writer. Use only the locked diagnosis, evidence packet, and Knowledge Base. Return JSON only.",
                                             messages=[{"role": "user", "content": activation_prompt}],
                                         )

                                     act_pool = concurrent.futures.ThreadPoolExecutor(max_workers=1)
                                     act_future = act_pool.submit(_activation_sonnet)
                                     try:
                                         sonnet_resp = act_future.result(timeout=sonnet_activation_timeout)
                                         for block in sonnet_resp.content:
                                             if getattr(block, 'type', None) == 'text':
                                                 activation_text = block.text
                                                 break
                                     except concurrent.futures.TimeoutError:
                                         act_pool.shutdown(wait=False)
                                         print(f"   ⏰ [Activation] Claude Sonnet 4.6 timed out after {sonnet_activation_timeout}s")
                                     act_pool.shutdown(wait=False)
                                     if activation_text:
                                         print(f"   ✅ [Activation] Claude Sonnet 4.6 complete ({int((time.time() - act_start) * 1000)}ms)")
                                 except Exception as act_sonnet_err:
                                     print(f"   ❌ [Activation] Claude Sonnet 4.6 failed: {act_sonnet_err}")

                             if activation_text:
                                 prior_activation = {
                                     "strategic_synthesis": strategic_synthesis,
                                     "pitch": "" if pitch == "Pitch Unavailable" else pitch,
                                     "activation_pairs": activation_pairs,
                                     "why_proposals": why_proposals,
                                     "what_proposals": what_proposals,
                                     "what_progress_proposals": what_progress_proposals,
                                 }
                                 activation_json = _parse_activation_json(activation_text)
                                 if not activation_json and _synthesis_budget_left() > 25:
                                     try:
                                         repair_timeout = min(35, max(18, _synthesis_budget_left() - 10))
                                         print(f"   🔧 [Activation] JSON parse failed; trying cheap JSON repair (timeout: {repair_timeout}s)...")
                                         repair_prompt = f"""Convert the following Strategic Activation output into STRICT JSON only.
Do not add facts. Preserve the meaning. Required keys:
{{"strategic_synthesis": "...", "commercial_pitch": "...", "activation_pairs": [{{"theme_type": "current_status|foresight_driven|proactive_opportunity", "sales_use": "current_status|foresight_driven|proactive_opportunity", "theme": "...", "why": "...", "what": "same as what_to_do", "what_to_do": "concrete activity or deliverable", "what_progress_shows": "observable proof of progress"}}], "why_proposals": ["..."], "what_proposals": ["what_to_do texts"], "what_progress_proposals": ["what_progress_shows texts"]}}

TEXT TO REPAIR:
{activation_text[:7000]}"""
                                         repaired_text = call_openai_text(
                                             repair_prompt,
                                             model="gpt-5.4",
                                             max_output_tokens=2600,
                                             timeout=repair_timeout,
                                         )
                                         activation_json = _parse_activation_json(repaired_text)
                                     except Exception as repair_err:
                                         print(f"   ⚠️ [Activation] JSON repair failed: {type(repair_err).__name__}: {repair_err}")

                                 if activation_json:
                                     activation_norm = _normalize_activation_payload(activation_json, prior_activation)
                                 else:
                                     print("   ⚠️ [Activation] Could not parse activation JSON — using plain-text fallback")
                                     activation_norm = _plain_text_activation_fallback(activation_text, prior_activation)
                                 activation_norm = strip_archetype_labels_from_text(activation_norm)

                                 if activation_norm.get("pitch"):
                                     pitch = activation_norm["pitch"]
                                 if activation_norm.get("strategic_synthesis"):
                                     strategic_synthesis = activation_norm["strategic_synthesis"]
                                 if activation_norm.get("activation_pairs"):
                                     activation_pairs = activation_norm["activation_pairs"]
                                 if activation_norm.get("why_proposals"):
                                     why_proposals = activation_norm["why_proposals"]
                                 if activation_norm.get("what_proposals"):
                                     what_proposals = activation_norm["what_proposals"]
                                 if activation_norm.get("what_progress_proposals"):
                                     what_progress_proposals = activation_norm["what_progress_proposals"]
                                 if pitch or why_proposals or what_proposals or strategic_synthesis:
                                     print(
                                         f"   ✅ [Activation] Grounded activation complete "
                                         f"(why={len(why_proposals)}, what={len(what_proposals)}, pitch={'yes' if pitch and pitch != 'Pitch Unavailable' else 'no'}, synthesis={'yes' if strategic_synthesis else 'no'})"
                                     )
                         elif summary:
                             print(f"   ⏭ [Activation] Budget < 60s before final reserve ({_activation_left}s) — skipping WHY/WHAT/Pitch")

                    else:
                         summary = response_text

            except Exception as e:
                print(f"   [Synthesis Error] {e}")
        else:
            print(f"   [Synthesis] WARNING: No ANTHROPIC_API_KEY found. Summary unavailable.")

        new_timestamp = datetime.utcnow().isoformat()
        result_id = str(uuid.uuid4())

        # Build rolling history chart (reuse existing_data loaded earlier)
        # Enriched: each entry now carries summary + scores for multi-scan lookback
        history = []
        if existing_data:
            history = existing_data.get("history", [])
            history.append({
                "date": existing_data.get("lastUpdated"),
                "gap": existing_data.get("reality_gap", 0),
                "intent_score": existing_data.get("intent_score"),
                "reality_score": existing_data.get("reality_score"),
                "confidence_score": existing_data.get("confidence_score"),
                "washing_type": existing_data.get("washing_type"),
                "summary_text": (existing_data.get("summary_text", "") or "")[:2500],
                "findings": existing_data.get("reality_checklist", [])[:10],
                "evidence": existing_data.get("evidence_links", [])[:8],
                "foresight_evidence": existing_data.get("foresight_links", [])[:5],
            })
            if len(history) > 12: history = history[-12:]

        # --- COMPANY METADATA (from finland_list.py) ---
        meta = get_company_metadata(companyName) or {}
        company_domain = meta.get("domain", "Other")
        company_revenue = meta.get("revenue_avg_3yr", None)
        company_employees = meta.get("employees", None)
        company_ownership = meta.get("ownership", None)
        company_hq = meta.get("hq_country", None)

        # --- DOMAIN CLASSIFICATION (if not set by static list) ---
        # If the company is in finland_list with a real domain, trust it.
        # Otherwise infer from scan evidence so "Other" stays rare.
        if company_domain == "Other":
            _cached = existing_data.get("domain") if existing_data else None
            company_domain = infer_domain(companyName, target_url or "", reality_results, financial_results, cached_domain=_cached)

        # --- FINAL ARCHETYPE (computed from finalized scores) ---
        fin_score = financial_results.get("financial_index")
        fin_for_archetype = fin_score if fin_score is not None else intent_results["score"]
        if reality_results["score"] >= 50:
            archetype = "Thriving" if fin_for_archetype >= 50 else "Modernizing"
        else:
            archetype = "Cash Cow" if fin_for_archetype >= 50 else "At Risk"
        classification_context = build_classification_namespaces(
            adaptability_archetype=archetype,
            foresight_force_profile={
                "source_contract": "strategic_foresight_macro_context",
                "evidence_kind": "foresight_context",
                "active_force_count": len((foresight_data or {}).get("forces") or [])
                + len((foresight_data or {}).get("dimension_forces") or []),
                "company_fact_eligible": False,
                "not_evidence_of_company_action": True,
            },
        )

        # --- SOURCE MANAGEMENT: Merge new evidence with existing, cap at 30 ---
        existing_evidence = existing_data.get("evidence_links", []) if existing_data else []
        source_mgmt = manage_evidence_sources(
            existing_links=existing_evidence,
            new_links=(reality_results.get("links") or []) + financial_evidence_links,
            company_domain=_company_site_domain,
            company_name=companyName,
        )
        managed_evidence_links = source_mgmt["links"]
        evidence_sufficiency = _evidence_sufficiency_state(managed_evidence_links, financial_evidence_links)
        ai_readiness_evidence = _ai_readiness_evidence_state(
            intent_results.get("breakdown", []),
            managed_evidence_links,
            reality_results.get("checklist", []),
        )
        final_evidence_absence_signals = build_evidence_absence_signals(
            intent_results.get("breakdown", []),
            managed_evidence_links,
            reality_results.get("checklist", []),
            foresight_links,
            financial_evidence_links,
        )
        def _quarantine_key(item):
            value = str((item or {}).get("canonical_url") or (item or {}).get("url") or "").strip()
            if not value:
                return ""
            parsed = urlparse(value if "://" in value else f"https://{value}")
            host = parsed.netloc.lower().removeprefix("www.")
            path = re.sub(r"/+", "/", parsed.path or "/").rstrip("/") or "/"
            return f"{host}{path}".lower()

        scanner_evidence_quarantine = []
        seen_quarantine = set()
        for item in (existing_data or {}).get("scanner_evidence_quarantine", []) + reality_results.get("scanner_evidence_quarantine", []):
            if not isinstance(item, dict):
                continue
            key = _quarantine_key(item)
            if not key or key in seen_quarantine:
                continue
            cleaned = dict(item)
            cleaned.setdefault("canonical_url", key)
            scanner_evidence_quarantine.append(cleaned)
            seen_quarantine.add(key)
        scanner_evidence_quarantine = scanner_evidence_quarantine[-30:]

        full_data = {
            "id": result_id,
            "companyName": companyName,
            "companyUrl": target_url,
            "domain": company_domain,
            "revenue_avg_3yr": company_revenue,
            "employees": company_employees,
            "ownership": company_ownership,
            "hq_country": company_hq,
            "intent_score": intent_results["score"],
            "reality_score": reality_results["score"],
            "reality_gap": gap,
            "confidence_score": confidence,
            "archetype": archetype,
            "frozen_classification_snapshot": reality_results.get("frozen_classification_snapshot", {}),
            "classification_context": classification_context,
            "adaptability_archetype": classification_context["adaptability_archetype"],
            "favi_vulnerability_segment": classification_context["favi_vulnerability_segment"],
            "adaptation_capacity_segment": classification_context["adaptation_capacity_segment"],
            "foresight_force_profile": classification_context["foresight_force_profile"],
            "washing_flag": washing_flag,
            "washing_type": washing_type,
            "intent_breakdown": intent_results["breakdown"],
            "metrics": intent_results.get("metrics", {}),
            "reality_checklist": reality_results["checklist"],
            "evidence_links": managed_evidence_links,
            "forensic_acquisition_health": reality_results.get("forensic_acquisition_health", {}),
            "registry_context": reality_results.get("registry_context", {}),
            "scanner_evidence_quarantine": scanner_evidence_quarantine,
            "evidence_gold_standard": source_mgmt["gold_standard"],
            "evidence_sufficiency": evidence_sufficiency,
            "low_evidence_mode": evidence_sufficiency.get("status") != "complete",
            "low_evidence_label": evidence_sufficiency.get("label", ""),
            "dimension_evidence_notes": {
                "ai_readiness": ai_readiness_evidence,
                "absence_signals": final_evidence_absence_signals,
            },
            "evidence_absence_signals": final_evidence_absence_signals,
            "ai_readiness_evidence_note": ai_readiness_evidence.get("warning", ""),
            "key_people": key_people,
            "stakeholder_enrichment_skipped": stakeholder_enrichment_skipped,
            "stakeholder_intelligence_mode": "buying_insight_only",
            "summary_text": _strip_tac_codes(summary),
            "strategic_synthesis": _strip_tac_codes(strategic_synthesis),
            "pitch_text": pitch,
            "evidence_based_risks": evidence_based_risks,
            "activation_pairs": activation_pairs,
            "why_proposals": why_proposals,
            "what_proposals": what_proposals,
            "what_progress_proposals": what_progress_proposals,
            "lastUpdated": new_timestamp,
            "history": history,
            "is_simulated": reality_results.get("is_simulated", False),
            "uploaded_docs": uploaded_doc_names,
            "financial_index": financial_results.get("financial_index"),
            "financial_sub_scores": financial_results.get("sub_scores", {}),
            "financial_findings": financial_results.get("financial_findings", []),
            "financial_confidence": financial_results.get("financial_confidence", 0),
            "strategic_foresight": foresight_data,
            "foresight_links": foresight_links,
            "scan_quality": scan_quality,
            # Exact bibliography supplied to generation. Verification uses this
            # frozen packet instead of reconstructing a different bibliography.
            "generation_evidence_manifest": generation_manifest or generation_company_manifest,
        }

        # One immutable manifest is shared by generation, verification and the
        # report. Canonical duplicate resolution ensures that a successful
        # current verification outranks a transient failed retrieval.
        attach_evidence_graph(full_data)

        # --- REALITY CHECK: Hallucination Detection ---
        summary_check = validate_summary(
            summary_text=summary,
            company_name=companyName,
            key_people=key_people,
            evidence_links=managed_evidence_links,
            confidence_score=confidence,
        )
        pitch_check = validate_pitch(pitch_text=pitch, company_name=companyName)

        full_data["hallucination_flags"] = summary_check["flags"] + pitch_check.get("flags", [])
        full_data["quality_notes"] = summary_check.get("quality_notes", [])
        full_data["hallucination_risk"] = summary_check["risk"]
        full_data["hallucination_score"] = summary_check["score"]

        if summary_check["risk"] == "high":
            print(f"   ⚠️ [Reality Check] HIGH hallucination risk (score {summary_check['score']}/100)! Flags: {summary_check['flags']}")
            print(f"   ℹ️ [Reality Check] Heuristic is diagnostic only — proceeding to AI fact-check for independent verification.")
        elif summary_check["risk"] == "medium":
            print(f"   ⚠️ [Reality Check] MEDIUM hallucination risk (score {summary_check['score']}/100). Flags: {summary_check['flags']}")
        else:
            print(f"   ✅ [Reality Check] Summary OK (score {summary_check['score']}/100)")

        # --- AI FACT-CHECK: Always runs in Deep Scan (heuristic is diagnostic only) ---
        # General Summary remains the strict factual layer. AI Diagnosis, risks and
        # Strategic Synthesis are inference layers: the verifier may flag unsupported
        # premises or weak reasoning, but it must not purge valid evidence or replace
        # the whole analysis with a factual correction.
        _fc_remaining = SCAN_TOTAL_BUDGET - (time.time() - scan_start)
        if _fc_remaining < 60:
            print(f"   ⏭ [Fact-Check] Budget < 60s remaining ({int(_fc_remaining)}s) — skipping, summary stored as unverified")
            fact_check = {
                "verified": False,
                "fact_check_skipped": True,
                "false_claims": [],
                "false_claims_detailed": [],
                "audit_notes": [],
                "audit_notes_detailed": [],
                "fact_check_model": "skipped_budget",
                "fact_check_time_ms": 0,
            }
            if scan_quality == "complete":
                scan_quality = "unverified"
        else:
            print(f"   🔄 [Fact-Check] Independent GPT/Claude fact-check ({int(_fc_remaining)}s remaining)...")
            fact_check = ai_fact_check_summary(
                summary_text=full_data["summary_text"],
                evidence_links=managed_evidence_links,
                company_name=companyName,
                pitch_text=full_data.get("pitch_text", ""),
                scan_payload=_build_fact_check_scan_payload(full_data),
                analysis_sections={
                    "evidence_based_risks": full_data.get("evidence_based_risks") or [],
                    "strategic_synthesis": full_data.get("strategic_synthesis") or "",
                },
                document_label="AI Diagnosis, Evidence-Based Risks, and Strategic Synthesis",
                foresight_links=full_data.get("foresight_links", []),
                evidence_manifest=full_data.get("generation_evidence_manifest", []),
                claim_graph=full_data.get("claim_graph", []),
            )
        full_data["ai_verification"] = {
            "verified": fact_check["verified"],
            "verification_status": fact_check.get("verification_status", "unavailable"),
            "fact_check_skipped": fact_check.get("fact_check_skipped", False),
            "false_claims": fact_check["false_claims"],
            "false_claims_detailed": fact_check.get("false_claims_detailed", []),
            "audit_notes": fact_check.get("audit_notes", []),
            "audit_notes_detailed": fact_check.get("audit_notes_detailed", []),
            "model": fact_check["fact_check_model"],
            "time_ms": fact_check["fact_check_time_ms"],
            "evidence_handoff_failures": fact_check.get("evidence_handoff_failures", []),
        }

        if fact_check.get("fact_check_skipped"):
            # --- SKIPPED: Budget exhausted or all fact-check models failed. Summary is unverified. ---
            _fc_skip_reason = "budget exhausted" if fact_check.get("fact_check_model") == "skipped_budget" else "all models failed"
            print(f"   ⚠️ [Fact-Check] Skipped ({_fc_skip_reason}) — summary stored as UNVERIFIED ({fact_check['fact_check_time_ms']}ms)")
            if scan_quality == "complete":
                scan_quality = "unverified"
        elif fact_check.get("verification_status") == "failed_handoff" or fact_check.get("evidence_handoff_failures"):
            # The verifier did not receive one or more evidence records cited by
            # generation. This is a pipeline integrity failure, not proof that the
            # claim or its source is fabricated. Preserve both without correction
            # or purging and make the unverified state explicit for the report.
            handoff_failures = fact_check.get("evidence_handoff_failures", [])
            print(
                f"   ⚠️ [Fact-Check] Evidence handoff failed for "
                f"{len(handoff_failures)} reference(s) — preserving analysis and sources as UNVERIFIED"
            )
            full_data["ai_verification"]["verified"] = False
            full_data["ai_verification"]["verification_status"] = "failed_handoff"
            full_data["ai_verification"]["note"] = (
                "Verification could not resolve every cited evidence record. "
                "No claim correction or evidence purge was applied."
            )
            if scan_quality == "complete":
                scan_quality = "unverified"
        elif fact_check["verified"] or fact_check.get("false_claims"):
            if fact_check["verified"]:
                # --- CLEAN: No false claims found. Original synthesis stands. Done. ---
                audit_count = len(fact_check.get("audit_notes", []))
                if audit_count:
                    print(
                        f"   ✅ [Fact-Check] Summary verified with {audit_count} non-blocking audit note(s) "
                        f"({fact_check['fact_check_time_ms']}ms)"
                    )
                else:
                    print(f"   ✅ [Fact-Check] Summary verified — no false claims ({fact_check['fact_check_time_ms']}ms)")
            else:
                # AI Diagnosis, risks, and Strategic Synthesis are inference
                # outputs. They should be audited for supported premises and
                # reasoning quality, but they must not be replaced wholesale by a
                # strict factual correction. General Summary remains the hard
                # factual correction layer.
                print(f"   ⚠️ [Fact-Check] {len(fact_check['false_claims'])} inference audit issue(s) found ({fact_check['fact_check_time_ms']}ms):")
                for claim in fact_check["false_claims"]:
                    print(f"      - {claim}")
                corrected_summary = fact_check.get("corrected_summary", "")
                corrected_sections = fact_check.get("corrected_sections") or {}
                corrected_pitch = fact_check.get("corrected_pitch", "") or ""
                if corrected_summary:
                    print(
                        f"   🧭 [Fact-Check] Applying corrected public AI Diagnosis "
                        f"({len(corrected_summary.split())} words); original retained in audit metadata"
                    )
                    full_data["summary_text"] = _strip_tac_codes(sanitize_inference_wording(corrected_summary))
                if isinstance(corrected_sections, dict):
                    if corrected_sections.get("strategic_synthesis"):
                        full_data["strategic_synthesis"] = _strip_tac_codes(
                            sanitize_inference_wording(corrected_sections["strategic_synthesis"])
                        )
                    if corrected_sections.get("evidence_based_risks"):
                        full_data["evidence_based_risks"] = sanitize_inference_wording(
                            corrected_sections["evidence_based_risks"]
                        )
                if corrected_pitch:
                    full_data["pitch_text"] = sanitize_inference_wording(corrected_pitch)
                full_data["re_synthesis"] = {
                    "was_resynthesized": bool(corrected_summary or corrected_sections or corrected_pitch),
                    "false_claims_found": fact_check["false_claims"],
                    "false_claims_detailed": fact_check.get("false_claims_detailed", []),
                    "audit_notes": fact_check.get("audit_notes", []),
                    "audit_notes_detailed": fact_check.get("audit_notes_detailed", []),
                    "suggested_corrected_summary": _strip_tac_codes(corrected_summary) if corrected_summary else "",
                    "suggested_corrected_pitch": corrected_pitch,
                    "suggested_corrected_sections": corrected_sections,
                    "evidence_purged": {"removed": 0, "skipped": True, "reason": "public_text_corrected_without_source_purge"},
                    "resynth_model": fact_check.get("fact_check_model", ""),
                    "resynth_time_ms": fact_check.get("fact_check_time_ms", 0),
                    "method": "public_correction_applied" if (corrected_summary or corrected_sections or corrected_pitch) else "inference_audit_preserved_original",
                }
                full_data["ai_verification"]["verified"] = False
                full_data["ai_verification"]["verification_status"] = (
                    fact_check.get("verification_status") or "inference_audit_requires_review"
                )
                full_data["ai_verification"]["note"] = (
                    "Blocking inference issues were corrected in public-facing fields when a correction was available. "
                    "Audit metadata preserves the original finding trail."
                )
                if scan_quality == "complete":
                    scan_quality = "unverified"
        else:
            # Defensive fallback for an inconclusive verifier response. Older
            # pipeline code used to purge evidence and rewrite the whole diagnosis
            # here; inference outputs must instead be preserved and marked
            # unverified for review.
            print(f"   ⚠️ [Fact-Check] Inconclusive inference audit ({fact_check['fact_check_time_ms']}ms); preserving analysis and sources")
            full_data["re_synthesis"] = {
                "was_resynthesized": False,
                "false_claims_found": fact_check.get("false_claims", []),
                "false_claims_detailed": fact_check.get("false_claims_detailed", []),
                "evidence_purged": {"removed": 0, "skipped": True, "reason": "inference_audit_inconclusive"},
                "resynth_model": fact_check.get("fact_check_model", ""),
                "resynth_time_ms": fact_check.get("fact_check_time_ms", 0),
                "method": "inference_audit_inconclusive_preserved_original",
            }
            full_data["ai_verification"]["verified"] = False
            full_data["ai_verification"]["verification_status"] = (
                fact_check.get("verification_status") or "inference_audit_inconclusive"
            )
            full_data["ai_verification"]["note"] = (
                "Inference verification was inconclusive. Analysis and evidence were preserved; "
                "no corrective rewrite or evidence purge was applied."
            )
            if scan_quality == "complete":
                scan_quality = "unverified"

        # --- STEP 3.5: GENERAL SUMMARY ---
        # General Summary: Plain-language narrative (225-275 words) from already-verified data.
        # Runs after fact-check so evidence_links are already purged/verified.
        # Failure is non-fatal — old records without these fields are handled gracefully.
        gs_text, gs_model = "", ""
        _gs_remaining = SCAN_TOTAL_BUDGET - (time.time() - scan_start)
        gs_ms = 0
        if _gs_remaining < 45:
            print(f"   ⏭ [General Summary] Budget < 45s remaining ({int(_gs_remaining)}s) — skipping")
            if scan_quality == "complete":
                scan_quality = "partial_summary"
        else:
            try:
                gs_start = time.time()
                gs_text, gs_model = generate_general_summary(full_data, companyName)
                gs_ms = int((time.time() - gs_start) * 1000)
            except Exception as gs_err:
                print(f"   ⚠️ [General Summary] Failed: {gs_err} — skipping")

        if gs_text:
            wc = len(gs_text.split())
            print(f"   ✅ [General Summary] {wc} words, model={gs_model} ({gs_ms}ms)")

            # Fact-check General Summary against its own input payload
            gs_check = fact_check_general_summary(gs_text, full_data, companyName)
            if gs_check.get("corrected_summary"):
                gs_text = gs_check["corrected_summary"]
                gs_model = gs_check["corrected_model"] or gs_model
                print(f"   🔄 [GS Fact-Check] Replaced with corrected summary ({gs_check['check_time_ms']}ms)")

            full_data["general_summary"] = gs_text
            full_data["general_summary_model"] = gs_model
            full_data["general_summary_verified"] = gs_check.get("verified", True)
            full_data["general_summary_false_claims"] = [
                fc.get("claim", "") for fc in gs_check.get("false_claims", [])
            ]
        else:
            print(f"   ⚠️ [General Summary] Generation returned empty — skipping field")

        # --- STEP 3.6: DETAILED FORENSIC REPORT (meta-synthesis) ---
        # Runs AFTER General Summary so it can synthesize all
        # prior analyses into a unified 800-1200 word diagnostic. No new searches —
        # works entirely from data already in memory (General Summary, AI Diagnosis,
        # structured foresight evidence, Why/What proposals, all metrics and evidence).
        dr_text, dr_model = "", ""
        dr_ms = 0
        prior_summaries = {}
        _dr_remaining = SCAN_TOTAL_BUDGET - (time.time() - scan_start)
        full_data["detailed_report_status"] = "pending"
        if _dr_remaining < DETAIL_REPORT_COMPACT_MIN:
            full_data["detailed_report_status"] = "skipped"
            full_data["detailed_report_skip_reason"] = "detailed_report_skipped_budget"
            print(
                f"   ⏭ [Detailed Report] Budget < {DETAIL_REPORT_COMPACT_MIN}s remaining "
                f"({int(_dr_remaining)}s) — skipping"
            )
            if scan_quality == "complete":
                scan_quality = "partial_report"
        else:
            try:
                from detailed_report import (
                    complete_detailed_report_draft,
                    detailed_report_quality,
                    generate_detailed_report,
                )
                dr_start = time.time()
                dr_compact = _dr_remaining < DETAIL_REPORT_FULL_MIN
                if dr_compact:
                    dr_time_budget = max(45, min(110, int(_dr_remaining) - 45))
                else:
                    # Full reports need room for Opus plus one GPT completion pass,
                    # while preserving time for fact-check, save and job finalization.
                    dr_time_budget = max(90, min(230, int(_dr_remaining) - 110))
                if dr_compact:
                    print(
                        f"   📝 [Detailed Report] Moderate budget ({int(_dr_remaining)}s) "
                        f"— attempting compact report"
                    )
                else:
                    print(
                        f"   📝 [Detailed Report] Full budget available ({int(_dr_remaining)}s) "
                        f"— attempting full report"
                    )

                # Collect all prior analyses for meta-synthesis
                if full_data.get("general_summary"):
                    prior_summaries["general_summary"] = full_data["general_summary"]
                if full_data.get("summary_text"):
                    prior_summaries["ai_summary"] = full_data["summary_text"]
                if full_data.get("strategic_synthesis"):
                    prior_summaries["strategic_synthesis"] = full_data["strategic_synthesis"]
                why_what_parts = []
                if full_data.get("why_proposals"):
                    why_what_parts.append("Why: " + "; ".join(full_data["why_proposals"]))
                if full_data.get("what_proposals"):
                    why_what_parts.append("What: " + "; ".join(full_data["what_proposals"]))
                if why_what_parts:
                    prior_summaries["why_what"] = " | ".join(why_what_parts)

                dr_text, dr_model = generate_detailed_report(
                    full_data, companyName, foresight_data,
                    prior_summaries=prior_summaries if prior_summaries else None,
                    compact=dr_compact,
                    time_budget_seconds=dr_time_budget,
                )
                dr_ms = int((time.time() - dr_start) * 1000)
            except Exception as dr_err:
                dr_ms = 0
                full_data["detailed_report_status"] = "failed"
                full_data["detailed_report_skip_reason"] = str(dr_err)[:240]
                print(f"   ⚠️ [Detailed Report] Failed: {dr_err} — skipping")

        if dr_text:
            # The report verifier must see the exact bibliography used by the
            # report generator and a claim graph that includes the report itself.
            full_data["detailed_report"] = dr_text
            attach_evidence_graph(full_data)
            _dr_fc_remaining = SCAN_TOTAL_BUDGET - (time.time() - scan_start)
            if _dr_fc_remaining >= 55:
                print(f"   🔍 [Detailed Report Fact-Check] Section-level verification ({int(_dr_fc_remaining)}s remaining)...")
                dr_check = ai_fact_check_summary(
                    summary_text=dr_text,
                    evidence_links=managed_evidence_links,
                    company_name=companyName,
                    scan_payload=_build_fact_check_scan_payload(full_data),
                    document_label="Detailed Forensic Report",
                    foresight_links=full_data.get("foresight_links", []),
                    evidence_manifest=full_data.get("evidence_manifest", []),
                    claim_graph=full_data.get("claim_graph", []),
                )
                if dr_check.get("corrected_summary"):
                    dr_text = dr_check["corrected_summary"]
                    full_data["detailed_report"] = dr_text
                full_data["detailed_report_verification"] = {
                    "verified": dr_check.get("verified", False),
                    "verification_status": dr_check.get("verification_status", "unavailable"),
                    "fact_check_skipped": dr_check.get("fact_check_skipped", False),
                    "false_claims": dr_check.get("false_claims", []),
                    "evidence_handoff_failures": dr_check.get("evidence_handoff_failures", []),
                    "model": dr_check.get("fact_check_model", ""),
                }
            else:
                full_data["detailed_report_verification"] = {
                    "verified": False,
                    "fact_check_skipped": True,
                    "reason": "insufficient_budget",
                }

            dr_quality = detailed_report_quality(dr_text, compact=dr_compact)
            if not dr_quality.get("complete"):
                print(
                    "   ⚠️ [Detailed Report] Post-check completeness failed: "
                    f"words={dr_quality.get('word_count')}, missing={dr_quality.get('missing')}"
                )
                _dr_repair_remaining = SCAN_TOTAL_BUDGET - (time.time() - scan_start)
                if _dr_repair_remaining >= 65:
                    try:
                        repaired_text, repair_model, repair_quality = complete_detailed_report_draft(
                            full_data,
                            companyName,
                            foresight_data,
                            dr_text,
                            prior_summaries=prior_summaries if prior_summaries else None,
                            compact=dr_compact,
                            time_budget_seconds=max(35, min(95, int(_dr_repair_remaining) - 35)),
                        )
                        if repaired_text and repair_quality.get("complete"):
                            dr_text = repaired_text
                            dr_model = f"{dr_model}+{repair_model}" if dr_model else repair_model
                            dr_quality = repair_quality
                            full_data["detailed_report"] = dr_text
                    except Exception as repair_err:
                        print(f"   ⚠️ [Detailed Report] Post-check repair failed: {repair_err}")
                if not dr_quality.get("complete"):
                    full_data.pop("detailed_report", None)
                    full_data["detailed_report_model"] = dr_model
                    full_data["detailed_report_status"] = "incomplete"
                    full_data["detailed_report_skip_reason"] = "detailed_report_incomplete_after_repair"
                    full_data["detailed_report_quality"] = dr_quality
                    if scan_quality == "complete":
                        scan_quality = "partial_report"
                    dr_text = ""

            if dr_text:
                full_data["detailed_report"] = dr_text
                full_data["detailed_report_model"] = dr_model
                full_data["detailed_report_status"] = "generated"
                full_data["detailed_report_quality"] = dr_quality
                full_data.pop("detailed_report_skip_reason", None)
                wc = len(dr_text.split())
                print(f"   ✅ [Detailed Report] {wc} words, model={dr_model} ({dr_ms}ms)")
            else:
                print("   ⚠️ [Detailed Report] Incomplete after repair — not rendering as generated")
        else:
            if full_data.get("detailed_report_status") == "pending":
                full_data["detailed_report_status"] = "empty"
                full_data["detailed_report_skip_reason"] = "detailed_report_empty"
                if scan_quality == "complete":
                    scan_quality = "partial_report"
            print(f"   ⚠️ [Detailed Report] Generation returned empty — skipping field")

        # Sync final scan quality before save.
        full_data["scan_quality"] = scan_quality
        # Refresh the graph after General Summary and Detailed Report are added.
        attach_evidence_graph(full_data)

        # --- STEP 4: SAVE ---
        if saveToDatabase:
            print(f"   🔄 [Storage] Saving report to Vercel Blob...")
            save_start = time.time()
            # If data was cleaned via re-synthesis, use strict pruning (keep=1)
            # to ensure false data versions are truly gone from the database
            was_cleaned = full_data.get("re_synthesis", {}).get("was_resynthesized", False)
            callable_paths = _find_callables(full_data, f"{companyName}.pre_save")
            if callable_paths:
                print(f"   ⚠️ [Data Hygiene] Callable objects before save: {callable_paths[:30]}")
            full_data = _make_json_safe_payload(full_data, f"{companyName}.pre_save")
            saved_url = storage.save_company_data(companyName, full_data, post_clean=was_cleaned)
            print(f"   ✅ [Storage] Saved in {int((time.time() - save_start) * 1000)}ms → {saved_url}")

            summary_entry = {
                "id": result_id,
                "companyName": companyName,
                "domain": company_domain,
                "revenue_avg_3yr": company_revenue,
                "employees": company_employees,
                "ownership": company_ownership,
                "hq_country": company_hq,
                "confidence_score": confidence,
                "intent_score": intent_results["score"],
                "reality_score": reality_results["score"],
                "reality_gap": gap,
                "lastUpdated": new_timestamp,
                "detailsUrl": saved_url,
                "has_docs": len(uploaded_doc_names) > 0,
                "financial_index": financial_results.get("financial_index"),
                "archetype": archetype,
                "ai_verified": full_data.get("ai_verification", {}).get("verified", True),
                "scan_quality": full_data.get("scan_quality", "complete"),
            }
            # Update index
            storage.update_global_index(summary_entry)

        if job_id:
            _update_scan_job(job_id, phase="finalizing", progress=95)
            _append_scan_log(job_id, "Finalizing scan result")

        total_ms = int((time.time() - scan_start) * 1000)
        total_sec = round(total_ms / 1000, 1)
        _quality_label = f" | Quality: {scan_quality}" if scan_quality != "complete" else ""
        print(f"✅ --- SCAN COMPLETE: {companyName} --- Total: {total_sec}s | Intent: {intent_results['score']}/100 | Reality: {reality_results['score']}/100 | Gap: {gap} | Archetype: {archetype} | Confidence: {confidence}/100{_quality_label} ---")

        return {"success": True, "data": full_data}

    except Exception as e:
        elapsed = round(time.time() - scan_start, 1) if 'scan_start' in locals() else 0
        print(f"❌ --- SCAN FAILED: {companyName} --- After {elapsed}s | Error: {str(e)} ---")
        raise HTTPException(status_code=500, detail=str(e))
