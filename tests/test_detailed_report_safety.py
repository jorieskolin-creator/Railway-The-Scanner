"""Focused regression tests for Detailed Report evidence and removal safety."""

from __future__ import annotations

import os
import sys

_BACKEND = os.path.join(os.path.dirname(__file__), "..", "backend")
if _BACKEND not in sys.path:
    sys.path.insert(0, _BACKEND)

import detailed_report  # noqa: E402


def test_detailed_report_payload_excludes_model_financial_assessment_prose():
    payload = detailed_report._build_input_payload(
        {
            "evidence_links": [],
            "financial_findings": [
                {
                    "dimension": "profitability",
                    "assessment": "The model inferred a group-wide margin decline.",
                    "source_title": "Filed accounts",
                    "source_url": "https://example.fi/accounts",
                    "evidence_snippet": "The local legal entity reported operating profit of EUR 4 million.",
                    "entity_scope": "local_legal_entity",
                },
                {
                    "dimension": "revenue",
                    "assessment": "Unsupported assessment without source text.",
                    "source_url": "https://example.fi/unsupported",
                    "evidence_snippet": "",
                },
            ],
        },
        "Example Oy",
        None,
    )

    assert len(payload["financial_findings"]) == 1
    assert "assessment" not in payload["financial_findings"][0]
    assert payload["financial_findings"][0]["entity_scope"] == "local_legal_entity"


def test_flagged_sentence_removal_is_fuzzy_and_preserves_report_headings():
    text = """# Detailed Forensic Report

## Key Findings

Example Oy has deployed enterprise AI across every operation. A supported fact remains.

## Operating Model

The operating model evidence is limited.

## Strategy

The strategy record is bounded.

## AI Readiness

Current public evidence does not establish deployment outcomes.

## Verdict

Further validation is required."""
    claims = [{"claim": "Enterprise AI has been deployed across all company operations."}]

    cleaned, removed = detailed_report.remove_flagged_report_sentences(text, claims)

    assert removed == 1
    assert "A supported fact remains." in cleaned
    assert "## Verdict" in cleaned
    assert detailed_report.flagged_report_claims_remaining(cleaned, claims) == []


def test_supported_sentence_survives_when_only_flagged_sentence_is_removed():
    text = """## Key Findings

Supported company context remains. Unsupported market certainty is stated here.

## Operating Model

Supported operating text.

## Strategy

Supported strategy text.

## AI Readiness

Supported readiness text.

## Verdict

Validation remains necessary."""

    cleaned, removed = detailed_report.remove_flagged_report_sentences(
        text,
        [{"claim": "Unsupported market certainty is stated here."}],
    )

    assert removed == 1
    assert "Supported company context remains." in cleaned
    assert "Unsupported market certainty" not in cleaned
