"""Regression coverage for macro foresight and strategic-inference fact checking."""

from __future__ import annotations

import json
import os
import sys

_BACKEND = os.path.join(os.path.dirname(__file__), "..", "backend")
if _BACKEND not in sys.path:
    sys.path.insert(0, _BACKEND)

import reality_check  # noqa: E402


def _foresight_source():
    return {
        "title": "Industrial automation outlook",
        "url": "https://www.oecd.org/industrial-automation-outlook",
        "source_quote": (
            "Industrial automation and governed data platforms are becoming more important "
            "as manufacturers respond to productivity and resilience pressure."
        ),
        "semantic_snippet": "Macro pressure affecting industrial operating models over 12-24 months.",
        "claim_type": "foresight_context",
        "company_specific": False,
        "foresight_context_eligible": True,
        "source_audit_status": "accepted",
        "source_readable": True,
        "theme_match": True,
        "trend_supported": True,
        "url_verified": True,
        "theme": "Industry & Manufacturing",
        "source_tier": "tier1",
    }


def test_fact_checker_receives_foresight_as_separate_macro_evidence(monkeypatch):
    prompts = []

    def fake_call(prompt, **kwargs):
        prompts.append(prompt)
        return json.dumps({"false_claims": [], "summary_accurate": True})

    monkeypatch.setattr(reality_check, "_call_openai_text", fake_call)
    result = reality_check._provider_fact_check(
        "Automation pressure may increase the importance of governed data architecture.",
        "ABB Oy",
        evidence_links=[],
        foresight_links=[_foresight_source()],
    )

    assert result["false_claims"] == []
    assert "VERIFIED MACRO FORESIGHT CONTEXT SOURCES" in prompts[0]
    assert "not expected to appear verbatim" in prompts[0]
    assert "does not need to mention the company" in prompts[0]


def test_supported_strategic_inference_is_filtered_before_correction(monkeypatch):
    calls = []

    def fake_call(prompt, **kwargs):
        calls.append(prompt)
        return json.dumps({
            "false_claims": [{
                "section": "AI Diagnosis",
                "claim": "Automation pressure may increase ABB's need for governed data architecture.",
                "claim_class": "strategic_inference",
                "error_type": "Fabrication",
                "support_level": "partial",
                "missing_or_contradicted_premise": "",
                "reasoning": "The conclusion is not present verbatim in the scan payload.",
                "correction": "",
                "source_indices": [1],
            }],
            "summary_accurate": False,
        })

    monkeypatch.setattr(reality_check, "_call_openai_text", fake_call)
    result = reality_check._provider_fact_check(
        "Automation pressure may increase ABB's need for governed data architecture.",
        "ABB Oy",
        evidence_links=[],
        foresight_links=[_foresight_source()],
    )

    assert result["false_claims"] == []
    assert len(calls) == 1, "A supported inference must be filtered before Stage 2 correction"


def test_foresight_cannot_prove_present_company_deployment(monkeypatch):
    def fake_call(prompt, **kwargs):
        if "precise editorial editor" in prompt:
            return json.dumps({
                "corrected_summary": "No verified company deployment is established.",
                "corrected_pitch": "",
                "corrected_sections": {},
            })
        return json.dumps({
            "false_claims": [{
                "section": "AI Diagnosis",
                "claim": "ABB has deployed a governed AI platform across its operations.",
                "claim_class": "external_company_fact",
                "error_type": "Fabrication",
                "support_level": "unsupported",
                "missing_or_contradicted_premise": "No company evidence proves deployment.",
                "reasoning": "The macro source cannot prove present company behavior.",
                "correction": "",
                "source_indices": [1],
            }],
            "summary_accurate": False,
        })

    monkeypatch.setattr(reality_check, "_call_openai_text", fake_call)
    result = reality_check._provider_fact_check(
        "ABB has deployed a governed AI platform across its operations.",
        "ABB Oy",
        evidence_links=[],
        foresight_links=[_foresight_source()],
    )

    assert len(result["false_claims"]) == 1
    assert result["false_claims"][0]["claim_class"] == "external_company_fact"
