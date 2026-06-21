"""
evidence_cleaner.py — Admin workflow for migrating stored evidence to the
analysis-verification model.

This is intentionally separate from scans and summary regeneration.  It updates
source metadata only, so old reports become safer inputs for future generation
without unexpectedly rewriting narrative fields.
"""

from __future__ import annotations

from copy import deepcopy
from datetime import datetime
from typing import Any

import requests

from source_claim_validator import CLEAN_EVIDENCE_MODEL_CHAIN, validate_source_claims
from source_quality import annotate_evidence_links


EVIDENCE_CLEANING_VERSION = "analysis-verification-v2-foresight-context"
_HEADERS = {"User-Agent": "Mozilla/5.0 (compatible; AdaptabilityScanner/1.0)"}


def _get_url(link: dict) -> str:
    return str(link.get("url") or link.get("source_url") or "").strip()


def _get_claim_text(link: dict) -> str:
    return str(
        link.get("snippet")
        or link.get("description")
        or link.get("summary")
        or link.get("claim")
        or link.get("title")
        or ""
    ).strip()


def _is_foresight_link(link: dict, field: str = "") -> bool:
    source_type = str(link.get("source_type") or link.get("origin") or "").lower()
    claim_type = str(link.get("claim_type") or "").lower()
    return field == "foresight_links" or source_type in {
        "foresight", "foresight_report", "trend_report", "regulatory_context",
        "industry_report", "policy_report",
    } or claim_type == "foresight_context"


def _get_foresight_claim_text(link: dict) -> str:
    """Return the macro claim, excluding company-fit interpretation fields."""
    return str(
        link.get("source_says")
        or link.get("semantic_snippet")
        or link.get("trend")
        or link.get("description")
        or link.get("summary")
        or link.get("title")
        or ""
    ).strip()


def _verify_url(url: str, timeout: int = 6) -> tuple[bool, str]:
    if not url or not url.startswith("http"):
        return False, "missing_url"
    try:
        resp = requests.get(url, headers=_HEADERS, timeout=timeout, allow_redirects=True, stream=True)
        try:
            status = int(resp.status_code)
        finally:
            resp.close()
        if 200 <= status < 400:
            return True, "url_verified"
        return False, f"http_{status}"
    except Exception as exc:
        return False, f"url_error:{str(exc)[:80]}"


def _prepare_link_for_cleaning(link: dict, field: str) -> bool:
    """Normalize old evidence while preserving the old scanner interpretation."""
    if not isinstance(link, dict):
        return False

    changed = False
    foresight_context = _is_foresight_link(link, field)
    claim = _get_foresight_claim_text(link) if foresight_context else _get_claim_text(link)
    old_snippet = str(link.get("snippet") or "").strip()
    if old_snippet and not link.get("legacy_snippet"):
        link["legacy_snippet"] = old_snippet
        changed = True
    if foresight_context and old_snippet and old_snippet != claim:
        link.setdefault("company_fit_interpretation", old_snippet)
        changed = True
    if claim and link.get("snippet") != claim:
        # validate_source_claims reads `snippet` as its claim. For foresight this
        # must be the macro claim, never the stored company-fit interpretation.
        link["snippet"] = claim
        changed = True
    if foresight_context:
        link["source_type"] = link.get("source_type") or "foresight"
        link["claim_type"] = "foresight_context"
        link["company_specific"] = False
        link["requires_company_specific_corroboration"] = True
        link["not_evidence_of_deployment"] = True
        link["modal_language_required"] = True
        # Remove inherited company-entity verdicts. Repair verifies the macro
        # claim; company fit is a separate downstream inference.
        for key in (
            "wrong_entity", "wrong_entity_true", "entity_match_status",
            "content_entity_match_status", "entity_scope", "generation_eligible",
            "analysis_verified", "promotion_status", "entity_verified",
            "entity_match_reason", "content_entity_match_reason",
            "entity_name_found", "canonical_source_host",
        ):
            if key in link:
                link.pop(key, None)
        changed = True

    url = _get_url(link)
    if url and not link.get("url"):
        link["url"] = url
        changed = True

    url_ok, url_reason = _verify_url(url)
    if link.get("verified") is not url_ok:
        link["verified"] = url_ok
        changed = True
    if link.get("url_verified") is not url_ok:
        link["url_verified"] = url_ok
        changed = True
    if not url_ok:
        link["claim_support_status"] = "unavailable" if url else "unchecked"
        link["content_verified"] = False
        link["content_verification_reason"] = url_reason
        changed = True
    return changed


def _evidence_lists(data: dict) -> list[tuple[str, list]]:
    fields = []
    for field in ("evidence_links", "foresight_links"):
        value = data.get(field)
        if isinstance(value, list):
            fields.append((field, value))
    return fields


def _build_stats(data: dict) -> dict[str, Any]:
    stats = {
        "total_sources": 0,
        "url_verified": 0,
        "dead_or_unavailable": 0,
        "content_verified": 0,
        "analysis_verified": 0,
        "generation_eligible": 0,
        "display_only": 0,
        "excluded": 0,
        "unsupported": 0,
        "unchecked": 0,
        "wrong_entity": 0,
        "unknown_entity": 0,
        "entity_verified": 0,
        "entity_failed": 0,
        "claim_failed": 0,
        "quote_mismatch": 0,
        "verification_conflict": 0,
        "source_quotes": 0,
        "by_field": {},
    }
    for field, links in _evidence_lists(data):
        field_stats = {
            "total_sources": 0,
            "analysis_verified": 0,
            "display_only": 0,
            "excluded": 0,
            "unsupported": 0,
            "unchecked": 0,
            "wrong_entity": 0,
            "unknown_entity": 0,
            "entity_verified": 0,
            "entity_failed": 0,
            "claim_failed": 0,
            "quote_mismatch": 0,
            "verification_conflict": 0,
        }
        for link in links:
            if not isinstance(link, dict):
                continue
            stats["total_sources"] += 1
            field_stats["total_sources"] += 1
            if link.get("verified") is True or link.get("url_verified") is True:
                stats["url_verified"] += 1
            else:
                stats["dead_or_unavailable"] += 1
            if link.get("content_verified") is True:
                stats["content_verified"] += 1
            if link.get("analysis_verified") is True:
                stats["analysis_verified"] += 1
                field_stats["analysis_verified"] += 1
            if link.get("promotion_status") == "generation_eligible":
                stats["generation_eligible"] += 1
            elif link.get("promotion_status") == "display_only":
                stats["display_only"] += 1
                field_stats["display_only"] += 1
            elif link.get("promotion_status") == "excluded":
                stats["excluded"] += 1
                field_stats["excluded"] += 1
            if link.get("claim_support_status") == "unsupported":
                stats["unsupported"] += 1
                field_stats["unsupported"] += 1
            if link.get("claim_support_status") == "unchecked":
                stats["unchecked"] += 1
                field_stats["unchecked"] += 1
            entity_status = str(link.get("entity_match_status") or "").lower()
            content_entity_status = str(link.get("content_entity_match_status") or entity_status or "").lower()
            if link.get("wrong_entity") is True or entity_status == "wrong" or content_entity_status == "wrong":
                stats["wrong_entity"] += 1
                field_stats["wrong_entity"] += 1
            elif entity_status in {"", "unknown", "weak"}:
                stats["unknown_entity"] += 1
                field_stats["unknown_entity"] += 1
            if link.get("entity_verified") is True:
                stats["entity_verified"] += 1
                field_stats["entity_verified"] += 1
            elif content_entity_status in {"wrong", "weak", "unknown", ""} and link.get("company_specific") is not False:
                stats["entity_failed"] += 1
                field_stats["entity_failed"] += 1
            if link.get("claim_support_status") in {"unsupported", "partial", "unavailable"}:
                stats["claim_failed"] += 1
                field_stats["claim_failed"] += 1
            if link.get("quote_mismatch") is True:
                stats["quote_mismatch"] += 1
                field_stats["quote_mismatch"] += 1
            if link.get("verification_conflict") is True:
                stats["verification_conflict"] += 1
                field_stats["verification_conflict"] += 1
            if link.get("source_quote"):
                stats["source_quotes"] += 1
        stats["by_field"][field] = field_stats
    return stats


def clean_company_evidence(company_name: str, data: dict, *, model_chain=None) -> dict:
    """Clean current evidence_links + foresight_links in-place and return audit stats."""
    if not isinstance(data, dict):
        raise ValueError("company data must be a dict")

    before = deepcopy({field: links for field, links in _evidence_lists(data)})
    company_domain = str(data.get("companyUrl") or data.get("domain") or "").strip()
    changed = False

    for field, links in _evidence_lists(data):
        clean_links = [link for link in links if isinstance(link, dict)]
        if len(clean_links) != len(links):
            data[field] = clean_links
            links = clean_links
            changed = True
        for link in links:
            changed = _prepare_link_for_cleaning(link, field) or changed
        annotate_evidence_links(links, company_domain, company_name)

    all_links = []
    for _, links in _evidence_lists(data):
        all_links.extend([link for link in links if isinstance(link, dict)])

    validation = validate_source_claims(
        all_links,
        max_checks=None,
        max_workers=3,
        model_chain=model_chain or CLEAN_EVIDENCE_MODEL_CHAIN,
        company_name=company_name,
        company_domain=company_domain,
    )

    for _, links in _evidence_lists(data):
        annotate_evidence_links(links, company_domain, company_name)

    # Generic company-evidence annotation is intentionally not the final gate
    # for foresight. Restore the company-neutral context contract after source
    # validation so Verify Evidence cannot turn a macro source into company proof.
    for field, links in _evidence_lists(data):
        for link in links:
            if not _is_foresight_link(link, field):
                continue
            supported = str(link.get("claim_support_status") or "").lower() == "supported"
            readable = link.get("content_verified") is True and bool(link.get("source_quote"))
            eligible = bool(
                (link.get("verified") is True or link.get("url_verified") is True)
                and readable
                and supported
                and link.get("theme_match", True) is not False
            )
            for key in (
                "wrong_entity", "wrong_entity_true", "entity_match_status",
                "content_entity_match_status", "entity_scope", "generation_eligible",
                "analysis_verified", "promotion_status", "entity_verified",
                "entity_match_reason", "content_entity_match_reason",
                "entity_name_found", "canonical_source_host",
            ):
                link.pop(key, None)
            link["claim_type"] = "foresight_context"
            link["company_specific"] = False
            link["requires_company_specific_corroboration"] = True
            link["not_evidence_of_deployment"] = True
            link["modal_language_required"] = True
            link["source_readable"] = readable
            link["trend_supported"] = supported
            link["trend_support_status"] = (
                "supported" if supported else link.get("claim_support_status", "unchecked")
            )
            link["foresight_context_eligible"] = eligible
            link["foresight_signal_status"] = "context_eligible" if eligible else "not_context_eligible"
            if eligible:
                audit_status = "accepted"
            elif not (link.get("verified") is True or link.get("url_verified") is True):
                audit_status = "source_dead_or_unavailable"
            elif not readable:
                audit_status = "source_unreadable"
            elif not supported:
                audit_status = "trend_not_supported"
            else:
                audit_status = "theme_mismatch"
            link["source_audit_status"] = audit_status
            link["verification_reason"] = (
                "macro_source_supports_foresight_claim; company_fit_requires_separate_corroboration"
                if eligible else link.get("content_verification_reason") or "macro_claim_not_supported"
            )

    cleaned_at = datetime.utcnow().isoformat()
    stats = _build_stats(data)
    data["evidence_cleaned_at"] = cleaned_at
    data["evidence_cleaning_version"] = EVIDENCE_CLEANING_VERSION
    data["evidence_cleaning_stats"] = {
        **stats,
        "validation": validation,
        "model_chain": list(model_chain or CLEAN_EVIDENCE_MODEL_CHAIN),
    }

    after = {field: links for field, links in _evidence_lists(data)}
    changed = changed or before != after

    return {
        "company": company_name,
        "status": "cleaned",
        "changed": changed,
        "cleaned_at": cleaned_at,
        "cleaning_version": EVIDENCE_CLEANING_VERSION,
        "stats": data["evidence_cleaning_stats"],
    }
