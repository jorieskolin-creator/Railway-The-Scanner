"""Regression tests for evidence lifecycle boundaries."""

from __future__ import annotations

import os
import sys


_BACKEND = os.path.join(os.path.dirname(__file__), "..", "backend")
if _BACKEND not in sys.path:
    sys.path.insert(0, _BACKEND)

import evidence_contracts as contracts  # noqa: E402
import foresight_harvester  # noqa: E402
import scanner_two  # noqa: E402
import strategic_foresight  # noqa: E402


def test_company_seed_contract_resets_trust_and_retains_old_verdict_only_as_history():
    candidate = scanner_two.prepare_historical_revalidation_candidate({
        "url": "https://example.fi/news/platform",
        "source_quote": "Example Oy previously described a platform.",
        "promotion_status": "generation_eligible",
        "analysis_verified": True,
        "entity_match_status": "exact",
    })

    assert candidate["evidence_contract"] == contracts.COMPANY_EVIDENCE_CANDIDATE
    assert candidate["generation_eligible"] is False
    assert candidate["analysis_verified"] is False
    assert candidate["entity_match_status"] == "pending"
    assert candidate["source_quote"] == ""
    assert candidate["verification_history"][0]["promotion_status"] == "generation_eligible"


def test_jury_handoff_uses_current_packet_verdict_and_reports_seed_lifecycle():
    jury = "AI & DATA JURY (Future Readiness)"
    url = "https://example.fi/news/platform"
    quote = "Example Oy launched a governed data platform in June 2026."
    seed = scanner_two.prepare_historical_revalidation_candidate({
        "url": url,
        "promotion_status": "display_only",
        "entity_match_status": "unknown",
    })
    seed.update({
        "category": jury,
        "seed_url_verified": True,
        "verified": True,
        "url_verified": True,
        "content_readable": True,
        "content_verified": True,
        "entity_verified": True,
        "entity_match_status": "exact",
        "content_entity_match_status": "exact",
        "entity_scope": "canonical_group",
        "fetched_excerpt": quote,
    })
    lane = {
        "jury": jury,
        "provider": "gpt-5.5",
        "status": "completed",
        "packet_urls": [url],
        "packets": [seed],
        "data": {
            "findings": [{
                "url": url,
                "supported_claim": quote,
                "source_quote": quote,
                "support_level": "exact",
            }],
            "rejected_evidence": [],
            "confidence_score": 80,
            "reality_score_adjustment": 2,
        },
    }

    result = scanner_two._merge_forensic_jury_results(
        {"confidence_score": 20}, [lane], {jury: [seed]}
    )
    finding = result["findings"][0]

    assert finding["evidence_contract"] == contracts.VERIFIED_COMPANY_EVIDENCE
    assert finding["entity_match_status"] == "exact"
    assert finding["claim_support_status"] == "supported"
    assert finding["source_status"] == "jury_accepted"
    assert result["seed_lifecycle"] == {
        "selected": 1,
        "url_revalidated": 1,
        "jury_accepted": 1,
        "promoted_to_verified": 1,
        "rejected": 0,
        "rejection_reasons": {},
    }


def test_central_foresight_contract_strips_company_verdicts_but_keeps_source_metadata():
    repaired = foresight_harvester._repair_central_kb_signal({
        "source_url": "https://oecd.org/report",
        "source_quote": "A macro trend is accelerating across industrial markets.",
        "source_credibility": "high",
        "url_verified": True,
        "content_verified": True,
        "generation_eligible": True,
        "analysis_verified": True,
        "entity_match_status": "wrong",
        "company_name": "Example Oy",
        "time_horizon": "12-24mo",
        "signal_direction": "accelerating",
    }, "Business", url_alive=True)

    assert repaired["evidence_contract"] == contracts.CENTRAL_FORESIGHT_SIGNAL
    assert repaired["claim_type"] == "foresight_context"
    assert repaired["company_specific"] is False
    assert repaired["url_verified"] is True
    assert repaired["time_horizon"] == "12-24mo"
    assert repaired["signal_direction"] == "accelerating"
    assert "generation_eligible" not in repaired
    assert "analysis_verified" not in repaired
    assert "entity_match_status" not in repaired
    assert "company_name" not in repaired


def test_company_foresight_copy_is_separate_and_does_not_mutate_central_force():
    force = {
        "trend": "Industrial AI regulation",
        "description": "Regulatory requirements for industrial AI are evolving.",
        "impact_on_company": "Example Oy may need to assess governance readiness.",
        "source_url": "https://ec.europa.eu/ai-policy",
        "source_type": "regulation",
        "url_verified": True,
        "foresight_context_eligible": True,
        "fp_theme": "Artificial Intelligence",
    }
    payload = {"dimension_forces": [dict(force)]}

    links = strategic_foresight.extract_foresight_links(
        payload, company_name="Example Oy", domain="Industrial & Manufacturing"
    )

    assert links[0]["evidence_contract"] == contracts.COMPANY_FORESIGHT_INTERPRETATION
    assert links[0]["source_says"] == force["description"]
    assert "Example Oy" in links[0]["possible_relevance"]
    assert links[0]["not_evidence_of_deployment"] is True
    assert "evidence_contract" not in force
