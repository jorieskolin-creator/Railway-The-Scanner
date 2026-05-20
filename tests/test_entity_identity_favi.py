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
    ENTITY_SCOPE_WRONG_ENTITY,
    assess_entity_match,
    apply_entity_scope,
    build_entity_context_block,
)
from foresight_vulnerability import compute_current_vulnerability_score, compute_favi_for_company, compute_favi_index


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


def test_rebase_same_name_wrong_domain_is_wrong_entity():
    result = assess_entity_match(
        {
            "title": "About Rebase Consultancy",
            "url": "https://www.rebaseconsultancy.com/about.html",
            "snippet": "Jason Myers founded Rebase Consultancy.",
        },
        "Rebase Consulting",
        "https://rebase.fi",
        page_text="Jason Myers founded Rebase Consultancy to advise clients.",
    )

    assert result["entity_match_status"] == "wrong"
    assert result["entity_scope"] == ENTITY_SCOPE_WRONG_ENTITY


def test_rebase_target_domain_is_exact_even_with_short_brand_text():
    result = assess_entity_match(
        {
            "title": "Rebase careers",
            "url": "https://rebase.fi/careers",
            "snippet": "Rebase describes its hiring process.",
        },
        "Rebase Consulting",
        "https://rebase.fi",
    )

    assert result["entity_match_status"] == "exact"
    assert result["entity_scope"] == ENTITY_SCOPE_CANONICAL_GROUP


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


def _foresight_link(dim="Operating Model"):
    return {
        "validated": True,
        "is_priority_dimension": True,
        "source_credibility": "high",
        "force_type": "Probable/Emerging",
        "timeframe": "12-24mo",
        "kb_dimension": dim,
        "fp_theme": "Data & Digitalisation",
    }


def test_current_vulnerability_score_is_continuous_for_non_ranked_companies():
    score = compute_current_vulnerability_score(
        {
            "companyName": "Steady Co",
            "reality_score": 82,
            "reality_gap": 0,
            "confidence_score": 80,
            "financial_index": 70,
            "reality_checklist": [],
        },
        max_gap=50,
    )
    assert score > 0
    assert score < 40


def test_favi_index_exposes_cutoffs_and_all_company_current_vulnerability():
    company_details = [
        {
            "companyName": "Ranked Co",
            "domain": "IT",
            "reality_score": 52,
            "reality_gap": 35,
            "confidence_score": 62,
            "financial_index": 45,
            "washing_flag": True,
            "financial_sub_scores": {"profitability": 10, "financial_stability": 10, "investment_signal": 10},
            "intent_breakdown": [{"name": "Operating Model", "score": 60}, {"name": "Strategy", "score": 60}],
            "reality_checklist": [
                {"found": True, "sentiment": "negative"},
                {"found": True, "sentiment": "negative"},
            ],
            "foresight_links": [_foresight_link("Operating Model"), _foresight_link("Strategy"), _foresight_link("AI Readiness")],
        },
        {
            "companyName": "Continuous Co",
            "domain": "IT",
            "reality_score": 84,
            "reality_gap": 0,
            "confidence_score": 78,
            "financial_index": 72,
            "financial_sub_scores": {"profitability": 18, "financial_stability": 16, "investment_signal": 14},
            "intent_breakdown": [{"name": "Operating Model", "score": 90}, {"name": "Strategy", "score": 90}],
            "reality_checklist": [],
            "foresight_links": [_foresight_link("Operating Model"), _foresight_link("Strategy"), _foresight_link("AI Readiness")],
        },
        {
            "companyName": "Checklist Co",
            "domain": "Services",
            "reality_score": 76,
            "reality_gap": 12,
            "confidence_score": 70,
            "financial_index": 64,
            "financial_sub_scores": {"profitability": 15, "financial_stability": 14, "investment_signal": 12},
            "intent_breakdown": [{"name": "Operating Model", "score": 80}, {"name": "Strategy", "score": 78}],
            "reality_checklist": [
                {"found": True, "sentiment": "negative"},
                {"found": True, "sentiment": "negative"},
                {"found": True, "sentiment": "negative"},
            ],
            "foresight_links": [_foresight_link("Operating Model"), _foresight_link("Strategy"), _foresight_link("AI Readiness")],
        },
        {
            "companyName": "Low Signal Co",
            "domain": "Services",
            "reality_score": 95,
            "reality_gap": 0,
            "confidence_score": 90,
            "financial_index": 90,
            "financial_sub_scores": {"profitability": 22, "financial_stability": 22, "investment_signal": 20},
            "intent_breakdown": [{"name": "Operating Model", "score": 95}, {"name": "Strategy", "score": 96}],
            "reality_checklist": [],
            "foresight_links": [_foresight_link("Operating Model"), _foresight_link("Strategy"), _foresight_link("AI Readiness")],
        },
    ]
    dashboard_data = [
        {
            "companyName": c["companyName"],
            "domain": c["domain"],
            "reality_score": c["reality_score"],
            "reality_gap": c["reality_gap"],
            "financial_index": c["financial_index"],
            "confidence_score": c["confidence_score"],
        }
        for c in company_details
    ]
    vulnerable_companies = [{"name": "Ranked Co", "vulnerability_score": 100}]

    result = compute_favi_index(
        company_details,
        dashboard_data,
        vulnerable_companies,
        [{"theme": "Data & Digitalisation"}],
    )

    assert result["favi_cutoffs"]["source"].startswith("percentile")
    assert sum(result["favi_segments"].values()) == len(company_details)

    by_name = {c["name"]: c for c in result["favi_companies"]}
    assert by_name["Ranked Co"]["current_vulnerability"] > by_name["Continuous Co"]["current_vulnerability"]
    assert by_name["Continuous Co"]["current_vulnerability"] > 0
    assert by_name["Checklist Co"]["current_vulnerability"] > by_name["Continuous Co"]["current_vulnerability"]
