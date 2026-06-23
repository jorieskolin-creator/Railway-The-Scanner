"""Deterministic absence-of-promoted-evidence signals.

These signals make a public proof gap explicit without letting models invent
it as prose.  They are not evidence that a capability is absent.  They only say
that the current promoted evidence set does not verify a capability or pressure
that the intent layer makes salient.
"""

from __future__ import annotations

import hashlib
import re
from typing import Iterable

from source_quality import is_generation_eligible_source, is_foresight_context_eligible_source


_DIMENSIONS = (
    {
        "dimension": "AI Readiness",
        "jury": "AI & DATA JURY (Future Readiness)",
        "terms": (
            "ai", "artificial intelligence", "machine learning", "ml", "genai",
            "generative ai", "data platform", "data governance", "developer portal",
            "api", "cloud", "automation", "autonomous", "analytics",
        ),
        "pressure_terms": ("ai", "artificial intelligence", "machine learning", "automation", "data"),
        "safe_topic": "AI, data-platform, API, automation, or ML capability",
    },
    {
        "dimension": "Service Portfolio",
        "jury": "SERVICE PORTFOLIO JURY (Delivery Reality)",
        "terms": (
            "service portfolio", "services", "customer service", "maintenance",
            "lifecycle", "support", "delivery", "customer experience", "cx",
            "managed service", "field service",
        ),
        "pressure_terms": ("service", "customer", "delivery", "lifecycle", "experience"),
        "safe_topic": "service-portfolio, customer-delivery, lifecycle, or support capability",
    },
    {
        "dimension": "Operating Model",
        "jury": "ARCHITECTURAL JURY (Engineering Reality)",
        "terms": (
            "operating model", "architecture", "platform", "integration", "devops",
            "software", "erp", "sap", "cloud", "developer", "api", "process",
        ),
        "pressure_terms": ("operating model", "architecture", "platform", "integration", "cloud"),
        "safe_topic": "operating-model, architecture, integration, platform, or cloud capability",
    },
    {
        "dimension": "Strategy",
        "jury": "STRATEGIC JURY (Business Model, Procurement & Customer Experience)",
        "terms": (
            "strategy", "business model", "investment", "partnership", "procurement",
            "market", "growth", "transformation", "digital strategy", "customer",
        ),
        "pressure_terms": ("strategy", "market", "investment", "procurement", "growth"),
        "safe_topic": "strategy, business-model, procurement, investment, or market-change evidence",
    },
    {
        "dimension": "Culture",
        "jury": "CULTURAL JURY (Talent & Psychological Safety)",
        "terms": (
            "culture", "talent", "career", "careers", "jobs", "hiring", "learning",
            "workforce", "hr", "psychological safety", "people",
        ),
        "pressure_terms": ("workforce", "talent", "skills", "learning", "culture"),
        "safe_topic": "culture, talent, workforce, hiring, or learning evidence",
    },
)


def _stable_signal_id(dimension: str, score: int | None, promoted: int, candidates: int) -> str:
    key = f"{dimension}|{score}|{promoted}|{candidates}"
    return f"ABS-{hashlib.sha256(key.encode('utf-8')).hexdigest()[:10].upper()}"


def _text(item: dict) -> str:
    return " ".join(
        str(item.get(key) or "")
        for key in (
            "name", "dimension", "category", "kb_dimension", "query", "title",
            "snippet", "source_quote", "semantic_snippet", "description", "claim_type",
            "source_type", "evidence_status", "jury", "finding",
        )
    ).lower()


def _matches(item: dict, terms: Iterable[str]) -> bool:
    haystack = _text(item)
    for term in terms:
        pattern = r"\b" + re.escape(term.lower()) + r"\b"
        if re.search(pattern, haystack):
            return True
    return False


def _dimension_score(intent_breakdown: list, dimension_name: str) -> int | None:
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


def _candidate_title(item: dict) -> str:
    return str(item.get("title") or item.get("name") or item.get("query") or item.get("url") or "").strip()


def _severity(score: int | None, promoted_count: int, candidate_count: int, foresight_pressure_count: int) -> str:
    if score is None:
        return "low"
    if promoted_count == 0 and score >= 80:
        return "high"
    if promoted_count == 0 and score >= 70:
        return "medium"
    if promoted_count <= 1 and score >= 85 and (candidate_count >= 3 or foresight_pressure_count >= 1):
        return "medium"
    return "low"


def build_evidence_absence_signals(
    intent_breakdown: list | None,
    evidence_links: list | None,
    checklist: list | None = None,
    foresight_links: list | None = None,
    financial_links: list | None = None,
) -> list[dict]:
    """Return deterministic public proof-gap signals.

    The output is intentionally conservative.  A signal is emitted only when
    intent/ambition is high enough that the absence of promoted evidence is
    diagnostically relevant.  It never states that the company lacks a
    capability.
    """
    evidence_links = [item for item in (evidence_links or []) if isinstance(item, dict)]
    checklist = [item for item in (checklist or []) if isinstance(item, dict)]
    foresight_links = [item for item in (foresight_links or []) if isinstance(item, dict)]
    financial_urls = {
        str(item.get("url") or item.get("source_url") or "").rstrip("/").lower()
        for item in (financial_links or [])
        if isinstance(item, dict)
    }
    signals = []
    for spec in _DIMENSIONS:
        score = _dimension_score(intent_breakdown or [], spec["dimension"])
        promoted_items = [
            item for item in evidence_links + checklist
            if is_generation_eligible_source(item) and _matches(item, spec["terms"])
            and str(item.get("url") or item.get("source_url") or "").rstrip("/").lower() not in financial_urls
        ]
        candidate_items = [
            item for item in evidence_links + checklist
            if _matches(item, spec["terms"])
        ]
        foresight_pressure = [
            item for item in foresight_links
            if is_foresight_context_eligible_source(item) and _matches(item, spec["pressure_terms"])
        ]
        promoted_count = len(promoted_items)
        candidate_count = len(candidate_items)
        if score is None:
            continue
        if score < 70 and promoted_count > 0:
            continue
        if score < 70 and candidate_count == 0 and not foresight_pressure:
            continue
        if promoted_count >= 2 and score < 90:
            continue

        gap_status = "verified_capability"
        if promoted_count == 0:
            gap_status = "unverified_intent"
        elif promoted_count == 1:
            gap_status = "scope_limited"

        if gap_status == "verified_capability":
            continue

        severity = _severity(score, promoted_count, candidate_count, len(foresight_pressure))
        signal_id = _stable_signal_id(spec["dimension"], score, promoted_count, candidate_count)
        safe_wording = (
            f"{spec['dimension']} intent is high ({score}/100), but the current promoted evidence set "
            f"contains {promoted_count} source(s) verifying {spec['safe_topic']}. Treat this as a public "
            "proof gap, not as evidence that the capability is absent."
        )
        signals.append({
            "signal_id": signal_id,
            "signal_type": "absence_of_promoted_evidence",
            "dimension": spec["dimension"],
            "jury": spec["jury"],
            "intent_score": score,
            "promoted_evidence_count": promoted_count,
            "candidate_source_count": candidate_count,
            "foresight_pressure_count": len(foresight_pressure),
            "gap_status": gap_status,
            "severity_hint": severity,
            "verification_contract": "evidence_state_not_company_fact",
            "safe_wording": safe_wording,
            "do_not_say": (
                "Do not say the capability is absent. Say only that promoted public evidence did not "
                "verify it in this scan."
            ),
            "usage": ["ai_diagnosis", "strategic_synthesis", "why_what", "pitch"],
            "candidate_titles": [_candidate_title(item) for item in candidate_items[:5] if _candidate_title(item)],
            "promoted_titles": [_candidate_title(item) for item in promoted_items[:5] if _candidate_title(item)],
            "foresight_pressure_titles": [
                _candidate_title(item) for item in foresight_pressure[:5] if _candidate_title(item)
            ],
        })
    return signals


def format_absence_signals_for_prompt(signals: list[dict] | None) -> str:
    if not signals:
        return ""
    lines = [
        "STRUCTURED ABSENCE-OF-PROMOTED-EVIDENCE SIGNALS:",
        "Use these as deterministic proof-gap premises, not as proof that a capability is absent.",
    ]
    for signal in signals[:8]:
        lines.append(
            f"- {signal.get('signal_id')}: {signal.get('safe_wording')} "
            f"Severity hint: {signal.get('severity_hint')}. "
            f"Do-not-say: {signal.get('do_not_say')}"
        )
    return "\n".join(lines)
