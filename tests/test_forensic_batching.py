"""Regression tests for parallel forensic acquisition and jury analysis."""

from __future__ import annotations

import os
import sys

_BACKEND = os.path.join(os.path.dirname(__file__), "..", "backend")
if _BACKEND not in sys.path:
    sys.path.insert(0, _BACKEND)

import scanner_two  # noqa: E402


def _finding(url: str, category: str, **extra):
    item = {
        "url": url,
        "title": "Evidence",
        "category": category,
        "found": True,
        "snippet": "A source-supported excerpt with enough content for revalidation.",
    }
    item.update(extra)
    return item


def test_parallel_forensic_juries_cover_all_source_registries_once():
    assert len(scanner_two.FORENSIC_JURIES) == 5
    assert len(set(scanner_two.FORENSIC_JURIES)) == 5
    assert set(scanner_two.FORENSIC_JURIES) == set(scanner_two.JURY_SOURCE_TARGETS)


def test_coverage_score_rewards_verified_diverse_balanced_sources():
    sparse = scanner_two._compute_forensic_coverage([
        _finding(
            "https://example.fi/news/one",
            "ARCHITECTURAL JURY (Engineering Reality)",
        )
    ], "example.fi")
    strong = scanner_two._compute_forensic_coverage([
        _finding(
            "https://example.fi/news/one",
            "ARCHITECTURAL JURY (Engineering Reality)",
            seed_url_verified=True,
            is_recent=True,
        ),
        _finding(
            "https://oecd.org/report/two",
            "STRATEGIC JURY (Business Model, Procurement & Customer Experience)",
            seed_url_verified=True,
            is_recent=True,
        ),
        _finding(
            "https://aws.amazon.com/customers/example",
            "AI & DATA JURY (Future Readiness)",
            seed_url_verified=True,
            is_recent=True,
        ),
    ], "example.fi")
    assert strong["score"] > sparse["score"]
    assert strong["official_sources"] == 1
    assert strong["independent_sources"] == 2
    assert len(strong["jury_coverage"]) == 3


def test_forensic_timing_has_separate_discovery_and_postprocess_budgets():
    assert scanner_two.FORENSIC_ACQUISITION_CEILING_SECONDS == 135
    assert scanner_two.FORENSIC_EXTRACTION_RECOVERY_CEILING_SECONDS == 35
    assert scanner_two.FORENSIC_JURY_ANALYSIS_CEILING_SECONDS == 180
    assert scanner_two.FORENSIC_POSTPROCESS_RESERVE_SECONDS == 30


def test_each_acquisition_prompt_is_jury_scoped_and_excludes_people_search():
    for jury in scanner_two.FORENSIC_JURIES:
        prompt = scanner_two._build_jury_acquisition_prompt(
            "Example Oy", "example.fi", jury, "Target entity: Example Oy"
        )
        assert f"This lane covers only: {jury}" in prompt
        assert "Do not search for people" in prompt
        assert "verbatim_quote" in prompt
        assert "copied word-for-word" in prompt
        other_juries = [name for name in scanner_two.FORENSIC_JURIES if name != jury]
        assert all(f"\n    {other}" not in prompt for other in other_juries)


def test_vivicta_acquisition_prompt_includes_rebrand_aliases():
    aliases = scanner_two._rebrand_alias_block("Vivicta", "vivicta.com")
    prompt = scanner_two._build_jury_acquisition_prompt(
        "Vivicta",
        "vivicta.com",
        "STRATEGIC JURY (Business Model, Procurement & Customer Experience)",
        "Target entity: Vivicta",
        aliases,
    )

    assert "Tietoevry Tech Services" in prompt
    assert "Agilitas" in prompt
    assert "carve-out" in prompt


def test_quote_matching_handles_punctuation_without_accepting_paraphrase():
    quote = "Example Oy launched an AI-enabled customer-service platform in 2026."
    extracted = "Example Oy launched an AI enabled customer service platform in 2026."
    unrelated = "Example Oy discussed its annual report and general sustainability targets."

    assert scanner_two._quote_matches_packet(quote, extracted) is True
    assert scanner_two._quote_matches_packet(quote, unrelated) is False


def test_service_seed_routes_to_service_jury_not_ai_data():
    routed = scanner_two._route_seed_findings([
        _finding(
            "https://example.fi/customer-story",
            "",
            source_type="technology service customer story",
            source_category="service_reference",
        )
    ])
    service = "SERVICE PORTFOLIO JURY (Delivery Reality)"
    ai_data = "AI & DATA JURY (Future Readiness)"
    assert len(routed[service]) == 1
    assert routed[ai_data] == []


def test_zero_fresh_sources_caps_coverage_and_reports_missing_juries():
    evidence = [
        _finding(
            f"https://example.fi/source-{index}",
            jury,
            seed_url_verified=True,
            cached_excerpt="Readable cached source text",
            source_status="seed_revalidated",
        )
        for index, jury in enumerate(scanner_two.FORENSIC_JURIES)
    ]
    coverage = scanner_two._compute_forensic_coverage(evidence, "example.fi")
    assert coverage["fresh_sources"] == 0
    assert coverage["score"] <= 55
    assert coverage["missing_juries"] == []


def test_failed_jury_keeps_only_revalidated_seed_as_pending():
    jury = "ARCHITECTURAL JURY (Engineering Reality)"
    good = _finding(
        "https://example.fi/engineering", jury,
        seed_url_verified=True, seed_entity_mentioned=True,
    )
    weak = _finding("https://example.fi/unclear", jury, seed_url_verified=False)
    result = scanner_two._merge_forensic_jury_results(
        {"confidence_score": 40, "findings": [good, weak]},
        [{"jury": jury, "status": "analysis_failed", "provider": "gpt-5.5", "data": None}],
        {jury: [good, weak]},
    )
    assert [item["url"] for item in result["findings"]] == [good["url"]]
    assert result["findings"][0]["source_status"] == "seeded_analysis_pending"
    assert result["findings"][0]["analysis_verified"] is False


def test_jury_cannot_promote_url_outside_supplied_packets():
    jury = "STRATEGIC JURY (Business Model, Procurement & Customer Experience)"
    supplied = _finding("https://example.fi/annual-report", jury)
    invented = _finding("https://unrelated.example/invented", jury)
    result = scanner_two._merge_forensic_jury_results(
        {"confidence_score": 20, "findings": []},
        [{
            "jury": jury,
            "status": "completed",
            "provider": "gpt-5.5",
            "packet_urls": [supplied["url"]],
            "data": {
                "findings": [invented],
                "rejected_evidence": [],
                "confidence_score": 90,
                "reality_score_adjustment": 0,
            },
        }],
        {jury: []},
    )
    assert result["findings"] == []
    assert result["rejected_evidence"][0]["url"] == invented["url"]


def test_jury_requires_source_bound_quote_and_support_level():
    jury = "ARCHITECTURAL JURY (Engineering Reality)"
    supplied = _finding(
        "https://example.fi/engineering", jury,
        snippet="Example Oy publishes a developer API for shipment status integration.",
    )
    result = scanner_two._merge_forensic_jury_results(
        {"confidence_score": 20, "findings": []},
        [{
            "jury": jury,
            "status": "completed",
            "provider": "gpt-5.5",
            "packet_urls": [supplied["url"]],
            "packets": [supplied],
            "data": {
                "findings": [{
                    "url": supplied["url"],
                    "supported_claim": "Example Oy publishes a shipment status API.",
                    "source_quote": supplied["snippet"],
                    "support_level": "exact",
                    "title": "Developer API",
                    "found": True,
                }],
                "confidence_score": 80,
                "reality_score_adjustment": 0,
            },
        }],
        {jury: []},
    )
    assert result["findings"][0]["support_level"] == "exact"
    assert result["findings"][0]["analysis_verified"] is True
    assert result["findings"][0]["claim_support_status"] == "supported"


def test_jury_preserves_preverified_entity_and_content_verdicts():
    jury = "SERVICE PORTFOLIO JURY (Delivery Reality)"
    supplied = _finding(
        "https://example.fi/services/managed-cloud", jury,
        snippet="Example Oy provides managed cloud operations and application support services.",
        verified=True,
        url_verified=True,
        content_readable=True,
        content_verified=True,
        entity_verified=True,
        entity_match_status="exact",
        content_entity_match_status="exact",
        entity_scope="canonical_group",
        entity_mentioned=True,
    )
    result = scanner_two._merge_forensic_jury_results(
        {"confidence_score": 20, "findings": []},
        [{
            "jury": jury,
            "status": "completed",
            "provider": "gpt-5.5",
            "packet_urls": [supplied["url"]],
            "packets": [supplied],
            "data": {
                "findings": [{
                    "url": supplied["url"],
                    "supported_claim": "Example Oy provides managed cloud operations and application support services.",
                    "source_quote": supplied["snippet"],
                    "support_level": "exact",
                    "title": "Managed cloud services",
                    "found": True,
                }],
                "confidence_score": 80,
                "reality_score_adjustment": 0,
            },
        }],
        {jury: []},
    )
    finding = result["findings"][0]
    assert finding["entity_match_status"] == "exact"
    assert finding["content_entity_match_status"] == "exact"
    assert finding["entity_scope"] == "canonical_group"
    assert finding["entity_verified"] is True
    assert finding["content_verified"] is True


def test_jury_uses_current_quote_match_as_content_verification():
    jury = "SERVICE PORTFOLIO JURY (Delivery Reality)"
    supplied = _finding(
        "https://example.fi/services/support", jury,
        snippet="",
        fetched_excerpt="Example Oy provides lifecycle support for its installed systems.",
        verified=True,
        url_verified=True,
        content_readable=True,
        content_verified=False,
        entity_verified=True,
        entity_match_status="exact",
        content_entity_match_status="exact",
        entity_scope="canonical_group",
        entity_mentioned=True,
        forensic_seed_source=True,
    )
    result = scanner_two._merge_forensic_jury_results(
        {"confidence_score": 20, "findings": []},
        [{
            "jury": jury,
            "status": "completed",
            "provider": "gpt-5.5",
            "packet_urls": [supplied["url"]],
            "packets": [supplied],
            "data": {
                "findings": [{
                    "url": supplied["url"],
                    "supported_claim": "Example Oy provides lifecycle support for its installed systems.",
                    "source_quote": supplied["fetched_excerpt"],
                    "support_level": "exact",
                    "title": "Lifecycle support",
                    "found": True,
                }],
                "confidence_score": 80,
            },
        }],
        {jury: []},
    )
    finding = result["findings"][0]
    assert finding["content_verified"] is True
    assert finding["entity_verified"] is True
    assert finding["claim_support_status"] == "supported"


def test_jury_can_support_claim_from_source_content_when_model_quote_is_poor():
    jury = "SERVICE PORTFOLIO JURY (Delivery Reality)"
    supplied = _finding(
        "https://example.fi/services/data-centers", jury,
        snippet="Example Oy is a global market leader with unmatched delivery impact.",
        fetched_excerpt=(
            "Example Oy provides data center lifecycle services, including modernization, "
            "maintenance, installation support, and service agreements for data center operators."
        ),
        verified=True,
        url_verified=True,
        content_readable=True,
        content_verified=False,
        entity_verified=True,
        entity_match_status="exact",
        content_entity_match_status="exact",
        entity_scope="canonical_group",
        entity_mentioned=True,
    )
    result = scanner_two._merge_forensic_jury_results(
        {"confidence_score": 20, "findings": []},
        [{
            "jury": jury,
            "status": "completed",
            "provider": "gpt-5.5",
            "packet_urls": [supplied["url"]],
            "packets": [supplied],
            "data": {
                "findings": [{
                    "url": supplied["url"],
                    "supported_claim": "Example Oy provides data center lifecycle services.",
                    "source_quote": "Example Oy is a global market leader with unmatched delivery impact.",
                    "support_level": "exact",
                    "title": "Data center services",
                    "found": True,
                }],
                "confidence_score": 80,
            },
        }],
        {jury: []},
    )

    finding = result["findings"][0]
    assert finding["support_method"] == "source_content_passage"
    assert finding["content_verified"] is True
    assert "data center lifecycle services" in finding["source_quote"]
    assert "market leader" not in finding["source_quote"]


def test_jury_salvages_verified_source_by_promoting_narrow_source_claim():
    jury = "SERVICE PORTFOLIO JURY (Delivery Reality)"
    supplied = _finding(
        "https://example.fi/services/lifecycle", jury,
        snippet="Example Oy is a market-leading AI service operator with four service tiers.",
        fetched_excerpt=(
            "Example Oy offers lifecycle services, maintenance, modernization, "
            "and support for industrial customers."
        ),
        verified=True,
        url_verified=True,
        content_readable=True,
        content_verified=True,
        entity_verified=True,
        entity_match_status="exact",
        content_entity_match_status="exact",
        entity_scope="canonical_group",
        entity_mentioned=True,
        source_type="official_service_evidence",
    )
    result = scanner_two._merge_forensic_jury_results(
        {"confidence_score": 20, "findings": []},
        [{
            "jury": jury,
            "status": "completed",
            "provider": "gpt-5.5",
            "packet_urls": [supplied["url"]],
            "packets": [supplied],
            "data": {
                "findings": [{
                    "url": supplied["url"],
                    "supported_claim": "Example Oy operates four AI-enabled service tiers across all industrial customers.",
                    "source_quote": supplied["snippet"],
                    "support_level": "exact",
                    "title": "Lifecycle services",
                    "found": True,
                }],
                "confidence_score": 80,
            },
        }],
        {jury: []},
    )

    finding = result["findings"][0]
    assert finding["claim_narrowed_from_jury"] is True
    assert finding["support_method"].startswith("source_derived_narrow_claim")
    assert finding["claim_support_status"] == "supported"
    assert finding["analysis_verified"] is True
    assert "lifecycle services" in finding["supported_claim"]
    assert "four AI-enabled" in finding["original_jury_claim"]


def test_non_document_asset_urls_are_rejected_before_jury_analysis(monkeypatch):
    def fake_verify_url(url, *_args, **_kwargs):
        return {
            "verified": True,
            "content_readable": True,
            "company_mentioned": True,
            "content_excerpt": "Example Oy service icon asset.",
        }

    monkeypatch.setattr(scanner_two, "_verify_url", fake_verify_url)
    gated, stats = scanner_two._prevalidate_acquisition_results([
        {
            "jury": "SERVICE PORTFOLIO JURY (Delivery Reality)",
            "sources": [{
                "url": "https://example.fi/assets/service-icon.png",
                "snippet": "Example Oy service icon asset.",
            }],
        }
    ], "Example Oy", recovery_seconds=0)

    assert gated[0]["sources"] == []
    assert stats["non_document_asset"] == 1
    assert gated[0]["pre_jury_rejected"][0]["reason"] == "non_document_asset"


def test_generic_landing_page_is_context_only_even_if_jury_calls_it_exact():
    jury = "CULTURAL JURY (Talent & Psychological Safety)"
    supplied = _finding(
        "https://example.fi/careers", jury,
        snippet="Example Oy careers page describes opportunities to join the company.",
        generic_context_page=True,
    )
    result = scanner_two._merge_forensic_jury_results(
        {"confidence_score": 20, "findings": []},
        [{
            "jury": jury,
            "status": "completed",
            "provider": "gpt-5.5",
            "packet_urls": [supplied["url"]],
            "packets": [supplied],
            "data": {
                "findings": [{
                    "url": supplied["url"],
                    "supported_claim": "Example Oy has a careers page.",
                    "source_quote": supplied["snippet"],
                    "support_level": "exact",
                    "title": "Careers",
                    "found": True,
                }],
                "confidence_score": 60,
                "reality_score_adjustment": 0,
            },
        }],
        {jury: []},
    )
    assert result["findings"][0]["support_level"] == "context_only"
    assert result["findings"][0]["analysis_verified"] is False


def test_jury_rejects_claim_that_exceeds_its_exact_quote():
    jury = "AI & DATA JURY (Future Readiness)"
    supplied = _finding(
        "https://example.fi/news/platform", jury,
        snippet="Example Oy published a developer API for shipment status integration.",
    )
    result = scanner_two._merge_forensic_jury_results(
        {"confidence_score": 20, "findings": []},
        [{
            "jury": jury,
            "status": "completed",
            "provider": "gpt-5.5",
            "packet_urls": [supplied["url"]],
            "packets": [supplied],
            "data": {
                "findings": [{
                    "url": supplied["url"],
                    "supported_claim": "Example Oy deployed 500 AI agents across every business unit.",
                    "source_quote": supplied["snippet"],
                    "support_level": "exact",
                    "title": "Platform update",
                    "found": True,
                }],
                "confidence_score": 80,
                "reality_score_adjustment": 0,
            },
        }],
        {jury: []},
    )
    assert result["findings"] == []
    assert "exceeded" in result["rejected_evidence"][0]["reason"]


def test_jury_rejects_bundled_multi_assertion_claim():
    jury = "STRATEGIC JURY (Business Model, Procurement & Customer Experience)"
    supplied = _finding(
        "https://example.fi/news/investment", jury,
        snippet="Example Oy announced a 20 million euro factory investment in 2026.",
    )
    result = scanner_two._merge_forensic_jury_results(
        {"confidence_score": 20, "findings": []},
        [{
            "jury": jury,
            "status": "completed",
            "provider": "gpt-5.5",
            "packet_urls": [supplied["url"]],
            "packets": [supplied],
            "data": {
                "findings": [{
                    "url": supplied["url"],
                    "supported_claim": "Example Oy announced a 20 million euro factory investment in 2026 and achieved 15 percent productivity growth.",
                    "source_quote": supplied["snippet"],
                    "support_level": "exact",
                    "title": "Factory investment",
                    "found": True,
                }],
                "confidence_score": 80,
            },
        }],
        {jury: []},
    )

    assert result["findings"] == []
    assert "bundled" in result["rejected_evidence"][0]["reason"]


def test_promoted_coverage_ignores_partial_and_unverified_findings():
    jury = "SERVICE PORTFOLIO JURY (Delivery Reality)"
    exact = _finding(
        "https://example.fi/reference", jury,
        verified=True, analysis_verified=True, support_level="exact",
        claim_support_status="supported", source_status="fresh_discovery",
    )
    partial = _finding(
        "https://example.fi/services", jury,
        verified=True, analysis_verified=False, support_level="partial",
        claim_support_status="partial", source_status="fresh_discovery",
    )
    coverage = scanner_two._compute_promoted_evidence_coverage([exact, partial], "example.fi")
    assert coverage["promoted_findings"] == 1


def test_historical_display_only_is_reset_to_fresh_revalidation_candidate():
    old = {
        "url": "https://example.fi/news/platform",
        "title": "Old platform finding",
        "snippet": "Example Oy previously described a platform programme.",
        "source_quote": "Example Oy previously described a platform programme.",
        "promotion_status": "display_only",
        "generation_eligible": True,
        "analysis_verified": True,
        "claim_support_status": "supported",
        "support_level": "exact",
        "entity_match_status": "exact",
        "url_verified": True,
    }
    candidate = scanner_two.prepare_historical_revalidation_candidate(old)

    assert candidate["promotion_status"] == "candidate_revalidation_pending"
    assert candidate["generation_eligible"] is False
    assert candidate["analysis_verified"] is False
    assert candidate["entity_match_status"] == "pending"
    assert candidate["snippet"] == ""
    assert candidate["source_quote"] == ""
    assert candidate["cached_excerpt"] == ""
    assert "previously described" in candidate["semantic_hint"]
    assert candidate["verification_history"][0]["promotion_status"] == "display_only"


def test_historical_display_only_can_revalidate_but_hard_failures_cannot():
    assert scanner_two.historical_source_can_revalidate({
        "url": "https://example.fi/report",
        "promotion_status": "display_only",
        "url_verified": True,
    }) is True
    assert scanner_two.historical_source_can_revalidate({
        "url": "https://example.fi/missing",
        "promotion_status": "display_only",
        "source_status": "soft_404",
    }) is False
    assert scanner_two.historical_source_can_revalidate({
        "url": "https://other.example/report",
        "wrong_entity_true": True,
    }) is False


def test_new_canonical_verdict_replaces_old_active_record_and_keeps_audit_history():
    old = scanner_two.prepare_historical_revalidation_candidate({
        "url": "https://www.example.fi/news/platform/?tracking=old",
        "title": "Old finding",
        "promotion_status": "display_only",
        "url_verified": True,
        "claim_support_status": "unsupported",
    })
    current = {
        "url": "https://example.fi/news/platform",
        "title": "Current verified finding",
        "snippet": "Example Oy launched the platform in 2026.",
        "url_verified": True,
        "content_readable": True,
        "analysis_verified": True,
        "claim_support_status": "supported",
        "support_level": "exact",
        "promotion_status": "generation_eligible",
        "historical_revalidation_pending": False,
    }

    collapsed = scanner_two.collapse_active_evidence([old, current])

    assert len(collapsed) == 1
    assert collapsed[0]["title"] == "Current verified finding"
    assert collapsed[0]["promotion_status"] == "generation_eligible"
    assert any(
        item.get("promotion_status") == "display_only"
        for item in collapsed[0]["verification_history"]
    )


def test_pre_jury_gate_keeps_source_when_acquisition_snippet_is_only_a_hint(monkeypatch):
    monkeypatch.delenv("FIRECRAWL_API_KEY", raising=False)
    jury = "AI & DATA JURY (Future Readiness)"
    source = _finding(
        "https://example.fi/news/platform",
        jury,
        snippet="Example Oy deployed a named AI platform across every business unit.",
    )

    monkeypatch.setattr(
        scanner_two,
        "_verify_url",
        lambda *_args, **_kwargs: {
            "verified": True,
            "company_mentioned": True,
            "content_readable": True,
            "content_excerpt": (
                "Example Oy published a general corporate news page about sustainability "
                "and annual reporting without any artificial intelligence platform claim."
            ),
            "extracted_date": None,
        },
    )

    lanes, stats = scanner_two._prevalidate_acquisition_results(
        [{"jury": jury, "provider": "gemini", "status": "completed", "sources": [source]}],
        "Example Oy",
    )

    assert len(lanes[0]["sources"]) == 1
    assert stats["quote_not_found"] == 1
    assert lanes[0]["sources"][0]["passage_hint_status"] == "snippet_not_found_in_source_content"
    assert lanes[0]["pre_jury_rejected"] == []


def test_pre_jury_gate_selectively_recovers_passage_with_firecrawl(monkeypatch):
    jury = "AI & DATA JURY (Future Readiness)"
    quote = "Example Oy launched a governed data platform for operational analytics in May 2026."
    source = _finding("https://example.fi/news/data-platform", jury, snippet=quote)
    monkeypatch.setenv("FIRECRAWL_API_KEY", "test-key")
    monkeypatch.setattr(
        scanner_two,
        "_verify_url",
        lambda *_args, **_kwargs: {
            "verified": True,
            "company_mentioned": True,
            "content_readable": True,
            "content_excerpt": "Example Oy general newsroom navigation and cookie preferences.",
            "extracted_date": None,
        },
    )
    monkeypatch.setattr(
        scanner_two,
        "_firecrawl_forensic_extract",
        lambda *_args, **_kwargs: {
            "ok": True,
            "reason": "",
            "text": f"Company update\n\n{quote}\n\nThe platform supports governed reporting.",
        },
    )

    lanes, stats = scanner_two._prevalidate_acquisition_results(
        [{"jury": jury, "provider": "gemini", "status": "completed", "sources": [source]}],
        "Example Oy",
    )

    assert len(lanes[0]["sources"]) == 1
    assert lanes[0]["sources"][0]["extraction_provider"] == "firecrawl"
    assert lanes[0]["sources"][0]["quote_match_method"].startswith("firecrawl_")
    assert stats["firecrawl_recovered"] == 1
    assert stats["quote_not_found"] == 0


def test_firecrawl_does_not_rescue_wrong_entity_packet(monkeypatch):
    jury = "STRATEGIC JURY (Business Model, Procurement & Customer Experience)"
    source = _finding(
        "https://unrelated.example/news",
        jury,
        snippet="Another company announced a new operating model and investment programme.",
    )
    calls = []
    monkeypatch.setenv("FIRECRAWL_API_KEY", "test-key")
    monkeypatch.setattr(
        scanner_two,
        "_verify_url",
        lambda *_args, **_kwargs: {
            "verified": True,
            "company_mentioned": False,
            "content_readable": True,
            "content_excerpt": "Another company announced a new operating model.",
            "extracted_date": None,
        },
    )
    monkeypatch.setattr(
        scanner_two,
        "_firecrawl_forensic_extract",
        lambda *args, **kwargs: calls.append((args, kwargs)),
    )

    lanes, stats = scanner_two._prevalidate_acquisition_results(
        [{"jury": jury, "provider": "gemini", "status": "completed", "sources": [source]}],
        "Example Oy",
    )

    assert lanes[0]["sources"] == []
    assert stats["wrong_entity"] == 1
    assert calls == []
