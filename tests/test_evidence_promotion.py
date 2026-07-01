"""
Regression tests for analysis-verified evidence promotion.

Run with: python -m pytest tests/test_evidence_promotion.py -v
"""

from __future__ import annotations

import os
import sys

_BACKEND = os.path.join(os.path.dirname(__file__), "..", "backend")
if _BACKEND not in sys.path:
    sys.path.insert(0, _BACKEND)

from source_quality import (  # noqa: E402
    ANALYSIS_SUPPORT_UNSUPPORTED_FOR_CLAIM,
    PROMOTION_DISPLAY_ONLY,
    PROMOTION_EXCLUDED,
    PROMOTION_GENERATION_ELIGIBLE,
    PROMOTION_SCOPED_CONTEXT,
    PROMOTION_UNJUDGED_TIMEOUT,
    SOURCE_CONTEXT_SCOPED,
    SOURCE_CONTEXT_VALID_CONTEXT_UNUSED,
    apply_analysis_verification,
    is_analysis_premise_source,
    is_generation_eligible_source,
    is_scoped_context_source,
    manage_evidence_sources,
    source_analysis_support_mode,
)


def _source(**extra):
    base = {
        "title": "Example technology modernization source",
        "url": "https://www.reuters.com/technology/example-modernization",
        "snippet": "The company deployed a new cloud platform for customer operations.",
        "verified": True,
        "url_verified": True,
        "source_credibility": "high",
        "source_type": "news",
        "entity_scope": "canonical_group",
        "content_verified": True,
        "claim_support_status": "supported",
        "content_relevance_score": 82,
        "source_quote": "The company deployed a new cloud platform for customer operations.",
    }
    base.update(extra)
    return base


def test_supported_content_is_verified_for_analysis():
    link = apply_analysis_verification(_source(), company_name="Example")

    assert link["analysis_verified"] is True
    assert link["promotion_status"] == PROMOTION_GENERATION_ELIGIBLE
    assert link["generation_eligible"] is True
    assert link["verification_reason"] == "verified_for_analysis"
    assert link["analysis_verified_at"]
    assert is_generation_eligible_source(link) is True


def test_live_but_irrelevant_content_is_display_only():
    link = apply_analysis_verification(
        _source(
            snippet="The page discusses milk and butter retail prices.",
            content_relevance_score=12,
            claim_support_status="unsupported",
            source_quote="The page discusses milk and butter retail prices.",
        ),
        company_name="Example",
    )

    assert link["analysis_verified"] is False
    assert link["promotion_status"] == PROMOTION_DISPLAY_ONLY
    assert link["generation_eligible"] is False
    assert link["verification_reason"] == "unsupported_for_this_claim"
    assert is_generation_eligible_source(link) is False


def test_dead_url_is_excluded_not_promoted():
    link = apply_analysis_verification(
        _source(verified=False, url_verified=False, content_verified=False),
        company_name="Example",
    )

    assert link["analysis_verified"] is False
    assert link["promotion_status"] == PROMOTION_EXCLUDED
    assert link["verification_reason"] == "url_not_verified"


def test_wrong_entity_is_excluded_even_with_supported_content():
    link = apply_analysis_verification(
        _source(wrong_entity=True, entity_match="wrong"),
        company_name="Example",
    )

    assert link["analysis_verified"] is False
    assert link["promotion_status"] == PROMOTION_EXCLUDED
    assert link["verification_reason"] in {"wrong_entity_true", "wrong_entity"}


def test_local_legal_entity_can_be_promoted_with_scope_metadata():
    link = apply_analysis_verification(
        _source(entity_scope="local_legal_entity", scope_warning="Local legal entity evidence"),
        company_name="Example",
    )

    assert link["analysis_verified"] is True
    assert link["promotion_status"] == PROMOTION_GENERATION_ELIGIBLE
    assert link["entity_scope"] == "local_legal_entity"
    assert link["scope_warning"]


def test_unchecked_content_stays_visible_but_not_generation_eligible():
    link = apply_analysis_verification(
        _source(
            content_verified=False,
            claim_support_status="unchecked",
            content_relevance_score=None,
            source_quote="",
        ),
        company_name="Example",
    )

    assert link["analysis_verified"] is False
    assert link["promotion_status"] == PROMOTION_DISPLAY_ONLY
    assert link["verification_reason"] == "content_unavailable"


def test_same_name_wrong_domain_is_excluded_for_rebase_collision():
    link = apply_analysis_verification(
        _source(
            title="About Rebase Consultancy",
            url="https://www.rebaseconsultancy.com/about.html",
            snippet="Jason Myers founded Rebase Consultancy to advise clients on operating models.",
            source_quote="Jason Myers founded Rebase Consultancy to advise clients on operating models.",
            source_type="company_website",
        ),
        company_domain="https://rebase.fi",
        company_name="Rebase Consulting",
    )

    assert link["wrong_entity"] is True
    assert link["entity_match_status"] == "wrong"
    assert link["analysis_verified"] is False
    assert link["promotion_status"] == PROMOTION_EXCLUDED
    assert is_generation_eligible_source(link) is False


def test_target_domain_source_can_be_promoted_even_when_page_uses_short_brand():
    link = apply_analysis_verification(
        _source(
            title="Rebase technical careers",
            url="https://rebase.fi/careers",
            snippet="Rebase describes its modern engineering hiring process.",
            source_quote="Rebase describes its modern engineering hiring process.",
            source_type="company_website",
        ),
        company_domain="https://rebase.fi",
        company_name="Rebase Consulting",
    )

    assert link["entity_match_status"] == "exact"
    assert link["entity_scope"] == "canonical_group"
    assert link["analysis_verified"] is True
    assert is_generation_eligible_source(link) is True


def test_supported_content_with_unknown_entity_is_not_generation_eligible():
    link = apply_analysis_verification(
        _source(
            title="Modern engineering practices",
            url="https://example-news.com/engineering",
            snippet="A company adopted modern engineering practices.",
            source_quote="A company adopted modern engineering practices.",
        ),
        company_domain="https://rebase.fi",
        company_name="Rebase Consulting",
    )

    assert link["entity_match_status"] == "unknown"
    assert link["analysis_verified"] is False
    assert link["promotion_status"] == PROMOTION_DISPLAY_ONLY
    assert link["verification_reason"] in {"unknown_entity", "target_entity_not_verified"}


def test_verification_conflict_blocks_supported_status_from_bad_finder_page():
    link = apply_analysis_verification(
        _source(
            title="Vivicta Oy - Vivicta - yritystiedot, päättäjät, taloustiedot | Finder",
            url="https://www.finder.fi/IT-konsultointi+IT-palvelut/Vivicta+Oy/Espoo/yhteystiedot/3692841",
            snippet="Vivicta Oy financial profile shows revenue and decision-makers.",
            source_quote="Katso Finderista yhtiön Ålands Turist & Konferens yritystiedot, päättäjät ja taloustiedot.",
            claim_support_status="supported",
            content_relevance_score=83,
            content_entity_match_status="unknown",
            content_entity_match_reason="target_entity_not_detected",
            entity_verified=False,
            verification_conflict=True,
            quote_mismatch=True,
            claim_irrelevant=True,
            content_verification_reason="The page appears to be for Ålands Turist & Konferens rather than Vivicta Oy.",
        ),
        company_domain="https://www.vivicta.com",
        company_name="Vivicta",
    )

    assert link["analysis_verified"] is False
    assert link["generation_eligible"] is False
    assert link["promotion_status"] == PROMOTION_DISPLAY_ONLY
    assert link["verification_reason"] in {"verification_conflict", "entity_or_quote_mismatch", "target_entity_not_verified"}
    assert is_generation_eligible_source(link) is False


def test_scoped_legal_entity_financial_source_can_be_generation_eligible():
    link = apply_analysis_verification(
        _source(
            title="Vivicta Oy taloustiedot",
            url="https://www.finder.fi/it-palvelut/vivicta-oy",
            snippet="Vivicta Oy reported 2025 local legal-entity revenue of 10.2 million euros.",
            source_quote="Vivicta Oy reported 2025 revenue of 10.2 million euros.",
            entity_scope="local_legal_entity",
            content_entity_match_status="scoped",
            content_entity_match_reason="mentions_legal_entity:Vivicta Oy",
            entity_verified=True,
            claim_support_status="supported",
            claim_irrelevant=False,
            verification_conflict=False,
            quote_mismatch=False,
        ),
        company_domain="https://www.vivicta.com",
        company_name="Vivicta",
    )

    assert link["analysis_verified"] is True
    assert link["generation_eligible"] is True
    assert link["promotion_status"] == PROMOTION_GENERATION_ELIGIBLE
    assert link["entity_scope"] == "local_legal_entity"
    assert link["scope_warning"]


def test_exact_content_verdict_overrides_unknown_display_entity_match():
    link = apply_analysis_verification(
        _source(
            title="Palvelut | ABB",
            url="https://new.abb.com/service/fi",
            snippet="ABB provides lifecycle services for installed industrial systems.",
            source_quote="ABB provides lifecycle services for installed industrial systems.",
            entity_scope="local_legal_entity",
            entity_match_status="unknown",
            content_entity_match_status="exact",
            content_entity_match_reason="target entity found in current page content",
            entity_verified=True,
            claim_support_status="supported",
            claim_irrelevant=False,
            verification_conflict=False,
            quote_mismatch=False,
        ),
        company_domain="https://abb.com",
        company_name="ABB Oy",
    )

    assert link["entity_match_status"] == "exact"
    assert link["content_entity_match_status"] == "exact"
    assert link["analysis_verified"] is True
    assert link["generation_eligible"] is True
    assert is_generation_eligible_source(link) is True


def test_official_company_source_repairs_legacy_low_credibility_without_claim_bypass():
    link = apply_analysis_verification(
        _source(
            title="Palvelut | ABB",
            url="https://new.abb.com/service/fi",
            snippet="ABB states that it provides lifecycle services for installed industrial systems.",
            source_quote="ABB states that it provides lifecycle services for installed industrial systems.",
            source_type="company_website",
            source_credibility="low",
            content_entity_match_status="exact",
            entity_match_status="exact",
            entity_verified=True,
            claim_support_status="supported",
        ),
        company_domain="https://abb.com",
        company_name="ABB Oy",
    )

    assert link["source_credibility"] == "medium"
    assert link["source_authenticity"] == "official_company_source"
    assert link["independence"] == "company-reported"
    assert link["attribution_required"] is True
    assert link["claim_tier"] == "C"
    assert link["analysis_verified"] is True
    assert link["generation_eligible"] is True


def test_official_company_source_still_requires_supported_content_for_fact_promotion():
    link = apply_analysis_verification(
        _source(
            title="Palvelut | ABB",
            url="https://new.abb.com/service/fi",
            snippet="ABB service landing page.",
            source_quote="ABB service landing page.",
            source_type="company_website",
            source_credibility="low",
            content_entity_match_status="exact",
            entity_match_status="exact",
            entity_verified=True,
            claim_support_status="unsupported",
        ),
        company_domain="https://abb.com",
        company_name="ABB Oy",
    )

    assert link["source_credibility"] == "medium"
    assert link["analysis_verified"] is False
    assert link["generation_eligible"] is False
    assert link["promotion_status"] == PROMOTION_SCOPED_CONTEXT
    assert link["source_use_status"] == "scoped_context"
    assert link["verification_reason"] == "official_scoped_context_claim_overreach"


def test_content_verdict_never_overrides_explicit_wrong_entity():
    link = apply_analysis_verification(
        _source(
            entity_scope="wrong_entity",
            entity_match_status="wrong",
            content_entity_match_status="exact",
            entity_verified=True,
            wrong_entity=True,
        ),
        company_domain="https://abb.com",
        company_name="ABB Oy",
    )

    assert link["analysis_verified"] is False
    assert link["generation_eligible"] is False
    assert link["promotion_status"] == PROMOTION_EXCLUDED
    assert is_generation_eligible_source(link) is False


def test_official_partial_support_becomes_scoped_context_not_fact_proof():
    link = apply_analysis_verification(
        _source(
            title="ABB service",
            url="https://new.abb.com/service/fi",
            snippet="ABB presents service offerings for installed industrial systems.",
            source_quote="ABB presents lifecycle service offerings for installed industrial systems.",
            source_type="service_page",
            source_credibility="medium",
            content_entity_match_status="exact",
            entity_match_status="exact",
            entity_verified=True,
            entity_scope="canonical_group",
            claim_support_status="partial",
            content_verified=True,
            content_readable=True,
        ),
        company_domain="https://abb.com",
        company_name="ABB Oy",
    )

    assert link["analysis_verified"] is False
    assert link["generation_eligible"] is False
    assert link["promotion_status"] == PROMOTION_SCOPED_CONTEXT
    assert link["source_use_status"] == "scoped_context"
    assert link["verification_reason"] == "scoped_context_partial_support"
    assert is_scoped_context_source(link) is True
    assert is_generation_eligible_source(link) is False


def test_official_unsupported_service_page_becomes_scoped_context_when_claim_overreaches():
    link = apply_analysis_verification(
        _source(
            title="ABB service",
            url="https://new.abb.com/service/fi",
            snippet="ABB service landing page.",
            source_quote="ABB service landing page.",
            source_type="service_page",
            source_credibility="medium",
            content_entity_match_status="exact",
            entity_match_status="exact",
            entity_verified=True,
            entity_scope="canonical_group",
            claim_support_status="unsupported",
            content_verified=True,
            content_readable=True,
        ),
        company_domain="https://abb.com",
        company_name="ABB Oy",
    )

    assert link["analysis_verified"] is False
    assert link["generation_eligible"] is False
    assert link["promotion_status"] == PROMOTION_SCOPED_CONTEXT
    assert link["source_use_status"] == "scoped_context"
    assert link["verification_reason"] == "official_scoped_context_claim_overreach"
    assert link["original_claim_support_status"] == "unsupported"
    assert link["claim_support_status"] == ANALYSIS_SUPPORT_UNSUPPORTED_FOR_CLAIM
    assert link["source_context_status"] == SOURCE_CONTEXT_SCOPED
    assert is_scoped_context_source(link) is True


def test_third_party_unsupported_context_stays_display_only_not_scoped():
    link = apply_analysis_verification(
        _source(
            title="Generic service page",
            url="https://example.com/service",
            snippet="A generic service landing page.",
            source_quote="A generic service landing page.",
            source_type="service_page",
            source_credibility="medium",
            content_entity_match_status="exact",
            entity_match_status="exact",
            entity_verified=True,
            entity_scope="canonical_group",
            claim_support_status="unsupported",
            content_verified=True,
            content_readable=True,
        ),
        company_domain="https://abb.com",
        company_name="ABB Oy",
    )

    assert link["analysis_verified"] is False
    assert link["generation_eligible"] is False
    assert link["promotion_status"] == PROMOTION_DISPLAY_ONLY
    assert link["source_use_status"] == "unsupported_for_this_claim"
    assert is_scoped_context_source(link) is False


def test_valid_company_context_with_overstrong_claim_becomes_scoped_context():
    link = apply_analysis_verification(
        _source(
            title="Analyst page about Example",
            url="https://analyst.example.com/reports/example",
            snippet="Generated claim says Example has company-wide AI operating outcomes.",
            source_quote="Example describes data and automation services for enterprise modernization.",
            source_type="analyst_report",
            source_credibility="medium",
            content_entity_match_status="exact",
            entity_match_status="exact",
            entity_verified=True,
            entity_scope="canonical_group",
            claim_support_status="unsupported_for_this_claim",
            claim_overreach_status="claim_too_strong_source_valid",
            source_context_status="valid_company_context",
            content_verified=True,
            content_readable=True,
        ),
        company_domain="https://example.com",
        company_name="Example",
    )

    assert link["analysis_verified"] is False
    assert link["generation_eligible"] is False
    assert link["promotion_status"] == PROMOTION_SCOPED_CONTEXT
    assert link["source_use_status"] == "scoped_context"
    assert link["verification_reason"] == "valid_source_claim_overreach"
    assert link["original_claim_support_status"] == ANALYSIS_SUPPORT_UNSUPPORTED_FOR_CLAIM
    assert link["source_context_status"] == SOURCE_CONTEXT_SCOPED
    assert is_scoped_context_source(link) is True


def test_claim_overreach_does_not_poison_valid_company_context():
    link = apply_analysis_verification(
        _source(
            title="ABB official developer context",
            url="https://developer.eu.mybuildings.abb.com/",
            snippet="Generated claim overreaches into a company-wide platform assertion.",
            source_quote="ABB Smart Buildings developer portal and API documentation.",
            source_type="official_technology_or_developer_page",
            source_credibility="medium",
            content_entity_match_status="exact",
            entity_match_status="exact",
            entity_verified=True,
            entity_scope="canonical_group",
            claim_support_status="unsupported",
            content_verified=True,
            content_readable=True,
        ),
        company_domain="https://abb.com",
        company_name="ABB Oy",
    )

    assert link["analysis_verified"] is False
    assert link["generation_eligible"] is False
    assert link["claim_support_status"] == ANALYSIS_SUPPORT_UNSUPPORTED_FOR_CLAIM
    assert link["promotion_status"] == PROMOTION_SCOPED_CONTEXT
    assert link["source_context_status"] == SOURCE_CONTEXT_SCOPED
    assert link["source_use_status"] == "scoped_context"
    assert is_scoped_context_source(link) is True


def test_non_official_claim_overreach_can_remain_valid_context_unused():
    link = apply_analysis_verification(
        _source(
            title="Independent ABB context",
            url="https://www.reuters.com/business/abb-context",
            snippet="Generated claim asks this source to prove a too-specific operating model.",
            source_quote="The source discusses ABB in relevant company context.",
            source_type="news",
            source_credibility="high",
            content_entity_match_status="exact",
            entity_match_status="exact",
            entity_verified=True,
            entity_scope="canonical_group",
            claim_support_status="unsupported",
            content_verified=True,
            content_readable=True,
        ),
        company_domain="https://abb.com",
        company_name="ABB Oy",
    )

    assert link["analysis_verified"] is False
    assert link["generation_eligible"] is False
    assert link["claim_support_status"] == ANALYSIS_SUPPORT_UNSUPPORTED_FOR_CLAIM
    assert link["source_context_status"] == SOURCE_CONTEXT_VALID_CONTEXT_UNUSED
    assert is_analysis_premise_source(link) is True
    assert source_analysis_support_mode(link) == "scoped_context"


def test_scoped_context_is_analysis_premise_not_hard_fact():
    link = apply_analysis_verification(
        _source(
            title="ABB service page",
            url="https://new.abb.com/service/fi",
            snippet="ABB describes service capabilities for customers.",
            source_quote="ABB describes service capabilities for customers.",
            source_type="service_page",
            source_credibility="medium",
            content_entity_match_status="exact",
            entity_match_status="exact",
            entity_verified=True,
            entity_scope="canonical_group",
            claim_support_status="unsupported",
            content_verified=True,
            content_readable=True,
        ),
        company_domain="https://abb.com",
        company_name="ABB Oy",
    )

    assert link["generation_eligible"] is False
    assert is_generation_eligible_source(link) is False
    assert is_analysis_premise_source(link) is True
    assert source_analysis_support_mode(link) == "scoped_context"


def test_unjudged_timeout_is_distinct_from_display_only_and_coverage():
    link = apply_analysis_verification(
        _source(
            title="ABB service",
            url="https://new.abb.com/service/fi",
            snippet="ABB service landing page.",
            source_quote="ABB service landing page.",
            source_type="service_page",
            content_entity_match_status="exact",
            entity_match_status="exact",
            entity_verified=True,
            entity_scope="canonical_group",
            claim_support_status="unjudged_due_timeout",
            content_verified=True,
            content_readable=True,
        ),
        company_domain="https://abb.com",
        company_name="ABB Oy",
    )

    assert link["analysis_verified"] is False
    assert link["generation_eligible"] is False
    assert link["promotion_status"] == PROMOTION_UNJUDGED_TIMEOUT
    assert link["source_use_status"] == "unjudged_due_timeout"
    assert is_scoped_context_source(link) is False
    assert is_generation_eligible_source(link) is False


def test_source_manager_duplicate_keeps_current_lineage_over_legacy_metadata():
    existing = [_source(
        url="https://www.example.com/services",
        title="Old service source",
        promotion_status=PROMOTION_DISPLAY_ONLY,
        generation_eligible=False,
        claim_support_status="unsupported",
        source_context_status="valid_context_unused",
        source_quote="Old quote",
    )]
    current = _source(
        url="https://www.example.com/services",
        title="Current service source",
        promotion_status=PROMOTION_SCOPED_CONTEXT,
        generation_eligible=False,
        claim_support_status="context_only",
        source_context_status=SOURCE_CONTEXT_SCOPED,
        source_quote="Current parsed passage",
        source_id="SRC-current",
        parsed_source_id="PRS-current",
        evidence_package_id="PKG-current",
        support_passage_ids=["PASS-current"],
        proposition_ids=["PROP-current"],
        parser_route="direct_html",
        extraction_quality="medium",
    )

    result = manage_evidence_sources(
        existing_links=existing,
        new_links=[current],
        company_domain="https://www.example.com",
        company_name="Example",
    )

    merged = result["links"][0]
    assert merged["title"] == "Current service source"
    assert merged["source_id"] == "SRC-current"
    assert merged["parsed_source_id"] == "PRS-current"
    assert merged["evidence_package_id"] == "PKG-current"
    assert merged["support_passage_ids"] == ["PASS-current"]
    assert merged["proposition_ids"] == ["PROP-current"]
    assert merged["claim_support_relationship"]["source_id"] == "SRC-current"
    assert merged["claim_support_relationship"]["final_decision"] == "analysis_premise"
