"""
Regression tests for targeted second-opinion evidence verification.

Run with: python -m pytest tests/test_source_claim_validator_second_opinion.py -v
"""

from __future__ import annotations

import json
import os
import sys

_BACKEND = os.path.join(os.path.dirname(__file__), "..", "backend")
if _BACKEND not in sys.path:
    sys.path.insert(0, _BACKEND)

import source_claim_validator as scv  # noqa: E402


def _entity_match(status: str) -> dict:
    return {
        "entity_match_status": status,
        "entity_match_reason": f"test_{status}",
        "canonical_source_host": "example.com",
        "entity_scope": "canonical_group" if status in {"exact", "scoped"} else "unknown",
    }


def _patch_entity(monkeypatch, status: str) -> None:
    monkeypatch.setattr(scv, "assess_entity_match", lambda *args, **kwargs: _entity_match(status))


def _model_response(score: int, supported: bool, quote: str = "", reason: str = "") -> str:
    return json.dumps({
        "score": score,
        "claim_supported": supported,
        "entity_match_status": "exact",
        "entity_name_found": "Example Corp",
        "supporting_quote": quote,
        "unsupported_reason": "" if supported else (reason or "Not supported"),
        "reason": reason,
    })


def test_supported_primary_with_missing_quote_triggers_sonnet_and_blocks_disagreement(monkeypatch):
    monkeypatch.setattr(
        scv,
        "_fetch_page_text",
        lambda url: "Example Corp announced a platform modernization program for customer operations.",
    )
    _patch_entity(monkeypatch, "exact")
    calls = []

    def fake_chain(prompt, model_chain, **kwargs):
        model = model_chain[0]
        calls.append(model)
        if model == "gpt-5.4":
            return _model_response(88, True, quote="", reason="Source supports the claim."), model
        return _model_response(20, False, quote="", reason="The source does not support the claim."), model

    monkeypatch.setattr(scv, "call_json_model_chain", fake_chain)

    result = scv._check_relevance(
        "https://example.com/news",
        "Example Corp modernized its customer operations platform.",
        model_chain=("gpt-5.4", "claude-sonnet-4-6"),
        company_name="Example Corp",
        company_domain="https://example.com",
        source_item={"url": "https://example.com/news", "source_credibility": "high"},
    )

    assert calls == ["gpt-5.4", "claude-sonnet-4-6"]
    assert result["claim_support_status"] == "unsupported"
    assert result["verification_reviewed_by"] == "claude-sonnet-4-6"
    assert result["verification_disagreement"] is True
    assert result["verification_review_reason"] == "gpt_supported_but_evidence_controls_are_weak"


def test_high_value_unsupported_primary_can_be_promoted_by_safe_second_opinion(monkeypatch):
    monkeypatch.setattr(
        scv,
        "_fetch_page_text",
        lambda url: "Example Corp reported 2024 revenue of 10 million euros in its annual report.",
    )
    _patch_entity(monkeypatch, "exact")
    calls = []

    def fake_chain(prompt, model_chain, **kwargs):
        model = model_chain[0]
        calls.append(model)
        if model == "gpt-5.4":
            return _model_response(25, False, quote="", reason="The source does not support the claim."), model
        return _model_response(
            91,
            True,
            quote="Example Corp reported 2024 revenue of 10 million euros in its annual report.",
            reason="The page supports the revenue claim.",
        ), model

    monkeypatch.setattr(scv, "call_json_model_chain", fake_chain)

    result = scv._check_relevance(
        "https://example.com/annual-report",
        "Example Corp reported 2024 revenue of 10 million euros.",
        model_chain=("gpt-5.4", "claude-sonnet-4-6"),
        company_name="Example Corp",
        company_domain="https://example.com",
        source_item={"url": "https://example.com/annual-report", "source_type": "annual_report"},
    )

    assert calls == ["gpt-5.4", "claude-sonnet-4-6"]
    assert result["claim_support_status"] == "supported"
    assert result["relevant"] is True
    assert result["verification_model"] == "claude-sonnet-4-6"
    assert result["verification_disagreement"] is True
    assert result["primary_verification_model"] == "gpt-5.4"
    assert result["secondary_verification_model"] == "claude-sonnet-4-6"


def test_high_value_disagreement_stays_unsupported_without_entity_proof(monkeypatch):
    monkeypatch.setattr(
        scv,
        "_fetch_page_text",
        lambda url: "The article describes generic cloud transformation trends without naming the target.",
    )
    _patch_entity(monkeypatch, "unknown")

    def fake_chain(prompt, model_chain, **kwargs):
        model = model_chain[0]
        if model == "gpt-5.4":
            return _model_response(30, False, quote="", reason="The source does not mention Example Corp."), model
        return _model_response(
            82,
            True,
            quote="The article describes generic cloud transformation trends.",
            reason="The source is generally related.",
        ), model

    monkeypatch.setattr(scv, "call_json_model_chain", fake_chain)

    result = scv._check_relevance(
        "https://credible-news.example/cloud",
        "Example Corp modernized its cloud platform.",
        model_chain=("gpt-5.4", "claude-sonnet-4-6"),
        company_name="Example Corp",
        company_domain="https://example.com",
        source_item={"url": "https://credible-news.example/cloud", "source_credibility": "high"},
    )

    assert result["claim_support_status"] == "unsupported"
    assert result["entity_verified"] is False
    assert result["verification_reviewed_by"] == "claude-sonnet-4-6"
    assert result["verification_review_reason"] == "high_value_source_not_supported_by_primary"


def test_wrong_entity_short_circuits_before_model_review(monkeypatch):
    monkeypatch.setattr(
        scv,
        "_fetch_page_text",
        lambda url: "A different Example company reported a platform modernization program.",
    )
    _patch_entity(monkeypatch, "wrong")

    def fail_if_called(*args, **kwargs):
        raise AssertionError("model verifier should not be called for deterministic wrong entity")

    monkeypatch.setattr(scv, "call_json_model_chain", fail_if_called)

    result = scv._check_relevance(
        "https://wrong-example.com/news",
        "Example Corp modernized its platform.",
        model_chain=("gpt-5.4", "claude-sonnet-4-6"),
        company_name="Example Corp",
        company_domain="https://example.com",
        source_item={"url": "https://wrong-example.com/news", "source_credibility": "high"},
    )

    assert result["claim_support_status"] == "unsupported"
    assert result["entity_verified"] is False
    assert result["verification_conflict"] is True
    assert result["quote_mismatch"] is True


def test_fetch_page_text_default_timeout_is_twelve_seconds():
    assert scv._fetch_page_text.__defaults__[0] == 12
