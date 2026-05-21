"""
Regression tests for the General Summary business-snapshot payload and prompt.

Run with: python -m pytest tests/test_general_summary_snapshot.py -v
"""

from __future__ import annotations

import os
import sys
import types

_BACKEND = os.path.join(os.path.dirname(__file__), "..", "backend")
if _BACKEND not in sys.path:
    sys.path.insert(0, _BACKEND)

try:
    from google import genai as _genai  # noqa: F401
except Exception:
    google_stub = sys.modules.get("google") or types.ModuleType("google")
    genai_stub = types.ModuleType("google.genai")

    class _Client:
        def __init__(self, *args, **kwargs):
            pass

    class _GenerateContentConfig:
        def __init__(self, *args, **kwargs):
            pass

    class _ThinkingConfig:
        def __init__(self, *args, **kwargs):
            pass

    genai_stub.Client = _Client
    genai_stub.types = types.SimpleNamespace(
        GenerateContentConfig=_GenerateContentConfig,
        ThinkingConfig=_ThinkingConfig,
    )
    google_stub.genai = genai_stub
    sys.modules["google"] = google_stub
    sys.modules["google.genai"] = genai_stub

import general_summary  # noqa: E402


def _eligible_source(**extra):
    base = {
        "title": "ExampleCo confirms platform modernization",
        "url": "https://example.com/news/platform-modernization",
        "verified": True,
        "url_verified": True,
        "content_verified": True,
        "analysis_verified": True,
        "generation_eligible": True,
        "promotion_status": "generation_eligible",
        "claim_support_status": "supported",
        "entity_scope": "canonical_group",
        "entity_match_status": "exact",
        "source_credibility": "high",
        "source_type": "news",
        "claim_tier": "A",
        "is_recent": True,
        "source_quote": (
            "ExampleCo confirmed a platform modernization programme covering customer "
            "operations, delivery tooling, and internal cloud services across the company."
        ),
    }
    base.update(extra)
    return base


def test_general_summary_payload_excludes_key_people(monkeypatch):
    monkeypatch.setattr(general_summary, "detect_contradictions", lambda links, company: [])

    payload = general_summary._build_input_payload(
        {
            "key_people": [
                {"name": "Jane Example", "role": "Chief Technology Officer"},
                {"name": "John Example", "role": "Chief Executive Officer"},
            ],
            "evidence_links": [_eligible_source()],
            "financial_findings": [
                {
                    "dimension": "profitability",
                    "assessment": "Revenue was stable while operating profit narrowed.",
                    "source_title": "ExampleCo filed accounts",
                    "source_url": "https://example.com/investors/accounts",
                    "evidence_snippet": "Revenue was stable and operating profit narrowed in the latest filed accounts.",
                }
            ],
            "financial_index": 62,
        },
        "ExampleCo",
    )

    assert "key_people" not in payload
    assert "Jane Example" not in str(payload)
    assert len(payload["evidence_links"]) == 1
    assert payload["financial_findings"][0]["dimension"] == "profitability"


def test_general_summary_prompt_defines_business_snapshot_not_diagnosis(monkeypatch):
    monkeypatch.setattr(general_summary, "detect_contradictions", lambda links, company: [])
    payload = general_summary._build_input_payload(
        {"evidence_links": [_eligible_source()], "financial_index": 58},
        "ExampleCo",
    )

    prompt = general_summary._build_prompt(payload, "ExampleCo")

    assert "evidence-based business snapshot" in prompt
    assert "Financial paragraph goal" in prompt
    assert "open evidence question" in prompt
    assert "Named people belong in the Stakeholder List" in prompt
    assert "people and leadership" not in prompt
    assert "Do not include a stakeholder inventory" in prompt
    assert "250" in prompt and "320" in prompt

    fact_check_prompt = general_summary._build_fact_check_prompt(
        "Jane Example is listed as Chief Technology Officer.",
        payload,
        "ExampleCo",
    )
    assert "People lists belong in the Stakeholder List section" in fact_check_prompt


def test_general_summary_payload_keeps_only_generation_eligible_sources(monkeypatch):
    monkeypatch.setattr(general_summary, "detect_contradictions", lambda links, company: [])

    payload = general_summary._build_input_payload(
        {
            "evidence_links": [
                _eligible_source(title="Eligible source"),
                _eligible_source(
                    title="Display only source",
                    analysis_verified=False,
                    generation_eligible=False,
                    promotion_status="display_only",
                    claim_support_status="unsupported",
                    source_quote="This unrelated source discusses a different topic and should not be promoted.",
                ),
            ],
        },
        "ExampleCo",
    )

    titles = [item["title"] for item in payload["evidence_links"]]
    assert titles == ["Eligible source"]
