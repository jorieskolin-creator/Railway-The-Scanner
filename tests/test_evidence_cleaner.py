"""
Regression tests for the admin Clean Evidence migration workflow.

Run with: python -m pytest tests/test_evidence_cleaner.py -v
"""

from __future__ import annotations

import os
import sys

_BACKEND = os.path.join(os.path.dirname(__file__), "..", "backend")
if _BACKEND not in sys.path:
    sys.path.insert(0, _BACKEND)

import evidence_cleaner  # noqa: E402
from evidence_cleaner import clean_company_evidence  # noqa: E402


def test_cleaner_promotes_supported_sources_and_preserves_legacy_snippet(monkeypatch):
    monkeypatch.setattr(evidence_cleaner, "_verify_url", lambda url, timeout=6: (True, "url_verified"))

    def fake_validate(links, **kwargs):
        for link in links:
            link["claim_support_status"] = "supported"
            link["content_verified"] = True
            link["content_relevance_score"] = 88
            link["source_quote"] = "The company deployed a new cloud platform."
        return {"checked": len(links), "irrelevant": 0, "flagged_urls": [], "elapsed_ms": 1}

    monkeypatch.setattr(evidence_cleaner, "validate_source_claims", fake_validate)
    data = {
        "companyName": "Example",
        "companyUrl": "https://example.com",
        "evidence_links": [{
            "url": "https://example.com/news",
            "snippet": "The company deployed a new cloud platform.",
            "source_credibility": "high",
            "entity_scope": "canonical_group",
        }],
        "foresight_links": [],
    }

    result = clean_company_evidence("Example", data)

    link = data["evidence_links"][0]
    assert link["legacy_snippet"] == "The company deployed a new cloud platform."
    assert link["verified"] is True
    assert link["url_verified"] is True
    assert link["analysis_verified"] is True
    assert link["promotion_status"] == "generation_eligible"
    assert link["generation_eligible"] is True
    assert data["evidence_cleaned_at"]
    assert result["stats"]["analysis_verified"] == 1


def test_cleaner_keeps_live_irrelevant_sources_display_only(monkeypatch):
    monkeypatch.setattr(evidence_cleaner, "_verify_url", lambda url, timeout=6: (True, "url_verified"))

    def fake_validate(links, **kwargs):
        for link in links:
            link["claim_support_status"] = "unsupported"
            link["content_verified"] = True
            link["content_relevance_score"] = 8
            link["source_quote"] = "The page discusses milk and butter prices."
        return {"checked": len(links), "irrelevant": len(links), "flagged_urls": [], "elapsed_ms": 1}

    monkeypatch.setattr(evidence_cleaner, "validate_source_claims", fake_validate)
    data = {
        "companyName": "Example",
        "evidence_links": [{
            "url": "https://example.com/food",
            "snippet": "The company modernized cloud services.",
            "source_credibility": "high",
        }],
    }

    clean_company_evidence("Example", data)

    link = data["evidence_links"][0]
    assert link["analysis_verified"] is False
    assert link["promotion_status"] == "display_only"
    assert link["generation_eligible"] is False
    assert link["verification_reason"] == "claim_unsupported"
    assert data["evidence_cleaning_stats"]["unsupported"] == 1


def test_cleaner_excludes_dead_urls_without_promoting(monkeypatch):
    monkeypatch.setattr(evidence_cleaner, "_verify_url", lambda url, timeout=6: (False, "http_404"))
    monkeypatch.setattr(
        evidence_cleaner,
        "validate_source_claims",
        lambda links, **kwargs: {"checked": 0, "irrelevant": 0, "flagged_urls": [], "elapsed_ms": 1},
    )
    data = {
        "companyName": "Example",
        "evidence_links": [{
            "url": "https://example.com/missing",
            "snippet": "The company modernized cloud services.",
            "source_credibility": "high",
        }],
    }

    clean_company_evidence("Example", data)

    link = data["evidence_links"][0]
    assert link["verified"] is False
    assert link["url_verified"] is False
    assert link["analysis_verified"] is False
    assert link["promotion_status"] == "excluded"
    assert link["verification_reason"] == "url_not_verified"


def test_cleaner_processes_foresight_links_with_summary_claims(monkeypatch):
    monkeypatch.setattr(evidence_cleaner, "_verify_url", lambda url, timeout=6: (True, "url_verified"))

    def fake_validate(links, **kwargs):
        assert links[0]["snippet"] == "Demand for platform automation is rising."
        links[0]["claim_support_status"] = "supported"
        links[0]["content_verified"] = True
        links[0]["content_relevance_score"] = 80
        links[0]["source_quote"] = "Demand for platform automation is rising."
        return {"checked": 1, "irrelevant": 0, "flagged_urls": [], "elapsed_ms": 1}

    monkeypatch.setattr(evidence_cleaner, "validate_source_claims", fake_validate)
    data = {
        "companyName": "Example",
        "foresight_links": [{
            "url": "https://example.com/platform",
            "summary": "Demand for platform automation is rising.",
            "source_credibility": "medium",
        }],
    }

    clean_company_evidence("Example", data)

    link = data["foresight_links"][0]
    assert link["source_type"] == "foresight"
    assert link["analysis_verified"] is True
    assert data["evidence_cleaning_stats"]["by_field"]["foresight_links"]["analysis_verified"] == 1
