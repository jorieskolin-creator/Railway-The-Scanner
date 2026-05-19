from __future__ import annotations

import os
import sys


_BACKEND = os.path.join(os.path.dirname(__file__), "..", "backend")
if _BACKEND not in sys.path:
    sys.path.insert(0, _BACKEND)

from entity_identity import (
    ENTITY_SCOPE_CANONICAL_GROUP,
    ENTITY_SCOPE_FORMER_NAME,
    ENTITY_SCOPE_LOCAL_LEGAL_ENTITY,
    ENTITY_SCOPE_PARENT_OWNER,
    apply_entity_scope,
    build_entity_context_block,
)
from foresight_vulnerability import compute_favi_for_company


def test_vivicta_entity_scope_classification():
    local = apply_entity_scope(
        {
            "title": "Vivicta Oy financial statement",
            "snippet": "Vivicta Oy reported operating profit in Finland.",
            "url": "https://example.com/vivicta-oy",
        },
        "Vivicta",
    )
    assert local["entity_scope"] == ENTITY_SCOPE_LOCAL_LEGAL_ENTITY
    assert "group-wide" in local["scope_warning"]

    predecessor = apply_entity_scope(
        {
            "title": "Tietoevry Tech Services becomes Vivicta",
            "snippet": "The former Tietoevry Tech Services business now operates as Vivicta.",
        },
        "Vivicta",
    )
    assert predecessor["entity_scope"] == ENTITY_SCOPE_FORMER_NAME

    owner = apply_entity_scope(
        {
            "title": "Agilitas closes buy-out",
            "snippet": "Agilitas completed the buy-out of Vivicta.",
        },
        "Vivicta",
    )
    assert owner["entity_scope"] == ENTITY_SCOPE_PARENT_OWNER

    canonical = apply_entity_scope(
        {
            "title": "Vivicta launches AI Navigator",
            "snippet": "Vivicta describes its Nordic AI maturity study.",
        },
        "Vivicta",
    )
    assert canonical["entity_scope"] == ENTITY_SCOPE_CANONICAL_GROUP


def test_entity_context_block_includes_scope_policy():
    block = build_entity_context_block("Vivicta")
    assert "Tietoevry Tech Services" in block
    assert "Vivicta Oy" in block
    assert "Agilitas" in block
    assert "entity_scope" in block


def test_favi_does_not_collapse_to_zero_when_current_risk_exists():
    company = {
        "companyName": "Vivicta",
        "reality_score": 68,
        "intent_breakdown": [
            {"name": "Operating Model", "score": 100},
            {"name": "Strategy", "score": 100},
            {"name": "AI Readiness", "score": 100},
        ],
        "financial_sub_scores": {
            "profitability": 19,
            "financial_stability": 15,
            "investment_signal": 21,
        },
        "reality_checklist": [
            {"found": True, "sentiment": "negative", "category": "Operating Model"},
            {"found": True, "sentiment": "negative", "category": "Culture"},
        ],
        "foresight_links": [
            {
                "validated": True,
                "is_priority_dimension": True,
                "source_credibility": "high",
                "force_type": "Probable/Emerging",
                "timeframe": "12-24mo",
                "kb_dimension": "Operating Model",
                "fp_theme": "Data & Digitalisation",
            },
            {
                "validated": True,
                "is_priority_dimension": True,
                "source_credibility": "medium",
                "force_type": "Plausible/Maturing",
                "timeframe": "12-24mo",
                "kb_dimension": "Strategy",
                "fp_theme": "Business",
            },
            {
                "validated": True,
                "is_priority_dimension": True,
                "source_credibility": "medium",
                "force_type": "Plausible/Maturing",
                "timeframe": "12-24mo",
                "kb_dimension": "AI Readiness",
                "fp_theme": "Data & Digitalisation",
            },
        ],
    }

    result = compute_favi_for_company(company, {"Data & Digitalisation"}, current_vulnerability=32)
    assert result["intent_readiness_gap"] == 0.0
    assert result["readiness_gap"] > 0.0
    assert result["readiness_gap_source"] == "current_vulnerability_adjusted"
    assert result["favi_score"] > 0.0
