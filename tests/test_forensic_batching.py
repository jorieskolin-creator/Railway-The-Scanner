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
    assert scanner_two.FORENSIC_JURY_ANALYSIS_CEILING_SECONDS == 95
    assert scanner_two.FORENSIC_POSTPROCESS_RESERVE_SECONDS == 30


def test_each_acquisition_prompt_is_jury_scoped_and_excludes_people_search():
    for jury in scanner_two.FORENSIC_JURIES:
        prompt = scanner_two._build_jury_acquisition_prompt(
            "Example Oy", "example.fi", jury, "Target entity: Example Oy"
        )
        assert f"This lane covers only: {jury}" in prompt
        assert "Do not search for people" in prompt
        other_juries = [name for name in scanner_two.FORENSIC_JURIES if name != jury]
        assert all(f"\n    {other}" not in prompt for other in other_juries)


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
