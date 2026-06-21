"""Regression coverage for Observatory source and verification boundaries."""

from __future__ import annotations

import os
import sys


_BACKEND = os.path.join(os.path.dirname(__file__), "..", "backend")
if _BACKEND not in sys.path:
    sys.path.insert(0, _BACKEND)

import foresight_observatory as observatory  # noqa: E402


def _signal(title: str, eligible: bool, **extra) -> dict:
    signal = {
        "title": title,
        "description": f"Evidence for {title}",
        "source_url": f"https://example.com/{title.lower().replace(' ', '-')}",
        "fp_theme": "Business",
        "foresight_context_eligible": eligible,
        "foresight_signal_status": "context_eligible" if eligible else "not_context_eligible",
        "source_readable": True,
        "theme_match": True,
        "trend_supported": True,
        "source_credibility": "high",
    }
    signal.update(extra)
    return signal


def test_central_kb_aggregate_requires_explicit_context_eligibility():
    aggregate = observatory._aggregate_kb_signals({
        "business": {
            "signals": [
                _signal("Eligible force", True),
                _signal("Legacy shortcut", False, generation_eligible=True),
                _signal("Dead force", True, source_audit_status="source_dead"),
            ]
        }
    })

    assert aggregate["total_records"] == 3
    assert aggregate["eligible_records"] == 1
    assert aggregate["excluded_records"] == 2
    titles = [item["title"] for items in aggregate["by_category"].values() for item in items]
    assert titles == ["Eligible force"]


def test_company_foresight_is_filtered_without_mutating_source_records():
    companies = [{
        "company_name": "Example Oy",
        "foresight_links": [
            _signal("Company context", True),
            _signal("Display-only legacy", False, promotion_status="display_only"),
        ],
    }]

    filtered, stats = observatory._filter_company_foresight(companies)

    assert len(filtered[0]["foresight_links"]) == 1
    assert stats["eligible_records"] == 1
    assert stats["excluded_records"] == 1
    assert len(companies[0]["foresight_links"]) == 2


def test_verification_failure_is_unavailable_not_verified(monkeypatch):
    def fail(*args, **kwargs):
        raise TimeoutError("verification timed out")

    monkeypatch.setattr(observatory, "call_json_model_chain", fail)
    result = observatory._verify_with_models("Essay", "Sources", "Global")

    assert result["passed"] is False
    assert result["status"] == "unavailable"
    assert "timed out" in result["error"]


def test_observatory_prompts_do_not_request_stakeholder_analysis():
    prompt = observatory._build_finland_prompt(
        "Global context",
        {"company_count": 2, "detailed_count": 2},
        {"by_category": {}},
        "[FINDING-1] Evidence-linked portfolio pattern",
    )

    lowered = prompt.lower()
    assert "stakeholder" not in lowered
    assert "linkedin" not in lowered
    assert "key_people" not in lowered


def test_manifest_and_confidence_separate_source_layers():
    data = {
        "all_kb": {"business": {"signals": [_signal("Eligible force", True, updated_at="2026-06-01")] }},
        "kb_aggregate": {
            "theme_count": 1,
            "total_records": 2,
            "eligible_records": 1,
            "excluded_records": 1,
            "exclusion_reasons": {"not_context_eligible": 1},
        },
        "dashboard_data": [{"companyName": "A"}, {"companyName": "B"}],
        "company_details": [{"company_name": "A", "updated_at": "2026-06-02"}],
        "company_foresight_stats": {
            "total_records": 3,
            "eligible_records": 2,
            "excluded_records": 1,
            "exclusion_reasons": {"not_context_eligible": 1},
        },
    }

    manifest = observatory._build_input_manifest(data)
    confidence = observatory._build_confidence_summary(
        data,
        [{"status": "verified"}, {"status": "failed"}, {"status": "unavailable"}],
    )

    assert manifest["central_foresight_kb"]["records_eligible"] == 1
    assert manifest["company_portfolio"]["foresight_records_excluded"] == 1
    assert manifest["source_boundaries"]["company_source_ledger"] == "not read by Observatory"
    assert confidence["macro_force_evidence"]["score"] == 50
    assert confidence["company_portfolio_pattern"]["score"] == 50
    assert confidence["strategic_interpretation"]["status"] == "failed"
