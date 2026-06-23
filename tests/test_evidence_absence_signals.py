"""Regression tests for absence-of-promoted-evidence signals."""

from __future__ import annotations

import os
import sys


_BACKEND = os.path.join(os.path.dirname(__file__), "..", "backend")
if _BACKEND not in sys.path:
    sys.path.insert(0, _BACKEND)

import evidence_absence  # noqa: E402
import evidence_graph  # noqa: E402


def test_high_ai_intent_without_promoted_evidence_creates_proof_gap_signal():
    signals = evidence_absence.build_evidence_absence_signals(
        intent_breakdown=[{"name": "AI Readiness", "score": 88}],
        evidence_links=[{
            "title": "ABB AI story",
            "snippet": "AI and automation ambitions",
            "promotion_status": "display_only",
            "generation_eligible": False,
        }],
        checklist=[],
    )

    assert len(signals) == 1
    signal = signals[0]
    assert signal["signal_type"] == "absence_of_promoted_evidence"
    assert signal["dimension"] == "AI Readiness"
    assert signal["gap_status"] == "unverified_intent"
    assert signal["promoted_evidence_count"] == 0
    assert "not as evidence that the capability is absent" in signal["safe_wording"]
    assert "Do not say the capability is absent" in signal["do_not_say"]


def test_promoted_matching_evidence_suppresses_absence_signal():
    signals = evidence_absence.build_evidence_absence_signals(
        intent_breakdown=[{"name": "AI Readiness", "score": 82}],
        evidence_links=[
            {
                "title": "AI platform evidence",
                "source_quote": "The company reports a deployed AI data platform.",
                "url": "https://example.com/ai-platform",
                "url_verified": True,
                "content_verified": True,
                "entity_verified": True,
                "entity_match_status": "exact",
                "content_entity_match_status": "exact",
                "entity_scope": "target_company",
                "promotion_status": "generation_eligible",
                "generation_eligible": True,
                "analysis_verified": True,
                "claim_support_status": "supported",
            },
            {
                "title": "Automation evidence",
                "source_quote": "The company reports automation in operations.",
                "url": "https://example.com/automation",
                "url_verified": True,
                "content_verified": True,
                "entity_verified": True,
                "entity_match_status": "exact",
                "content_entity_match_status": "exact",
                "entity_scope": "target_company",
                "promotion_status": "generation_eligible",
                "generation_eligible": True,
                "analysis_verified": True,
                "claim_support_status": "supported",
            },
        ],
        checklist=[],
    )

    assert signals == []


def test_absence_signals_are_claim_graph_records_not_company_facts():
    signal = evidence_absence.build_evidence_absence_signals(
        intent_breakdown=[{"name": "Service Portfolio", "score": 91}],
        evidence_links=[{
            "title": "Service page",
            "snippet": "Service portfolio and customer delivery",
            "promotion_status": "display_only",
        }],
    )[0]
    data = {
        "evidence_links": [],
        "foresight_links": [],
        "evidence_absence_signals": [signal],
    }

    evidence_graph.attach_evidence_graph(data)

    claims = [
        row for row in data["claim_graph"]
        if row.get("claim_type") == "absence_of_promoted_evidence"
    ]
    assert len(claims) == 1
    assert claims[0]["phase"] == "evidence_absence"
    assert claims[0]["support"] == "deterministic_evidence_state"
    assert claims[0]["scope"] == "scanner_evidence_state"
    assert claims[0]["citation_status"] == "not_source_claim"
