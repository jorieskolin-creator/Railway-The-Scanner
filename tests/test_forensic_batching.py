"""Regression tests for seeded, two-batch Scanner 2 forensic discovery."""

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


def test_batches_cover_all_five_juries_once():
    flattened = [
        jury
        for juries in scanner_two.FORENSIC_DISCOVERY_BATCHES.values()
        for jury in juries
    ]
    assert len(flattened) == 5
    assert len(set(flattened)) == 5
    assert set(flattened) == set(scanner_two.JURY_SOURCE_TARGETS)


def test_batch_prompt_is_scoped_and_has_no_stakeholder_discovery():
    prompt = scanner_two._build_forensic_discovery_prompt(
        search_name="Example Oy",
        domain_hint="example.fi",
        batch_name="batch_1",
        coverage={"score": 72},
        seed_findings=[],
    )
    lowered = prompt.lower()
    assert "architectural jury" in lowered
    assert "ai & data jury" in lowered
    assert "service portfolio jury" in lowered
    assert "strategic jury" not in lowered
    assert "cultural jury" not in lowered
    assert "key_people" not in lowered
    assert "identify key" not in lowered
    assert "do not search for people" in lowered
    assert "mandatory fresh-discovery lane" in lowered


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


def test_batch_timeout_preserves_seeded_baseline():
    seed = _finding(
        "https://example.fi/news/seed",
        "STRATEGIC JURY (Business Model, Procurement & Customer Experience)",
        source_status="seed_revalidated",
    )
    result = scanner_two._merge_forensic_batch_results(
        {
            "findings": [seed],
            "confidence_score": 35,
            "reality_score_adjustment": 0,
            "key_people": [{"name": "Legacy compatibility only"}],
        },
        [
            {"name": "batch_1", "status": "batch_timeout", "data": None},
            {"name": "batch_2", "status": "batch_timeout", "data": None},
        ],
    )
    assert [item["url"] for item in result["findings"]] == [seed["url"]]
    assert result["key_people"] == []
    assert result["confidence_score"] == 35
    assert all(item["status"] == "batch_timeout" for item in result["forensic_batch_status"])


def test_forensic_timing_has_separate_discovery_and_postprocess_budgets():
    assert scanner_two.FORENSIC_DISCOVERY_CEILING_SECONDS == 150
    assert scanner_two.FORENSIC_POSTPROCESS_RESERVE_SECONDS == 30
    assert 75 + 75 == scanner_two.FORENSIC_DISCOVERY_CEILING_SECONDS
