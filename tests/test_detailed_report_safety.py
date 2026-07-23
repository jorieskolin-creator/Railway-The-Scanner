"""Focused regression tests for Detailed Report evidence and removal safety."""

from __future__ import annotations

import os
import sys

_BACKEND = os.path.join(os.path.dirname(__file__), "..", "backend")
if _BACKEND not in sys.path:
    sys.path.insert(0, _BACKEND)

import detailed_report  # noqa: E402
import reality_check  # noqa: E402


def _report_with_words(words_per_section: int = 160) -> str:
    sections = ["Key Findings", "Operating Model", "Strategy", "AI Readiness", "Verdict"]
    return "\n\n".join(
        f"## {heading}\n\n" + " ".join(f"{heading.replace(' ', '').lower()}{index}" for index in range(words_per_section)) + "."
        for heading in sections
    )


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


def test_financial_fact_support_must_exist_within_one_period_record():
    payload = {
        "financial_payload": {
            "financial_fact_manifest": [
                {
                    "assessment": "Revenue was EUR 91.6 million in FY2025.",
                    "periods": ["FY2025"],
                    "entity_scope": "local_legal_entity",
                },
                {
                    "assessment": "Operating profit was EUR 11.1 million with a 21.8% margin in FY2024.",
                    "periods": ["FY2024"],
                    "entity_scope": "local_legal_entity",
                },
            ]
        }
    }

    assert reality_check._is_supported_structured_financial_payload_claim(
        "Revenue was EUR 91.6 million in FY2025.", payload
    )
    assert not reality_check._is_supported_structured_financial_payload_claim(
        "Revenue was EUR 91.6 million and operating profit was EUR 11.1 million in FY2025.",
        payload,
    )


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


def test_condensed_verified_report_can_publish_below_preferred_800_words():
    report = _report_with_words(156)

    strict = detailed_report.detailed_report_quality(report)
    condensed = detailed_report.detailed_report_quality(report, allow_condensed=True)

    assert strict["complete"] is False
    assert "word_count_below_800" in strict["missing"]
    assert condensed["complete"] is True
    assert condensed["condensed"] is True


def test_damage_threshold_selects_targeted_repair_below_twenty_percent():
    original = _report_with_words(180)
    repaired = original.replace("strategy179.", "")

    impact = detailed_report.detailed_report_repair_impact(original, repaired)

    assert impact["removed_word_ratio"] < 0.20
    assert impact["repair_mode"] == "targeted_paragraph_repair"


def test_damage_threshold_selects_regeneration_above_twenty_percent():
    original = _report_with_words(180)
    _, sections = detailed_report._split_report_sections(original)
    sections[1]["content"] = "Short supported operating statement."
    sections[2]["content"] = "Short supported strategy statement."
    repaired = detailed_report._assemble_report_sections("", sections)

    impact = detailed_report.detailed_report_repair_impact(original, repaired)

    assert impact["removed_word_ratio"] > 0.20
    assert impact["repair_mode"] == "full_regeneration"


def test_targeted_repair_preserves_unaffected_sections(monkeypatch):
    report = """## Key Findings

Supported opening remains.

## Operating Model

Supported operating context remains.

## Strategy

The transition became awkward after correction.

## AI Readiness

Supported AI context remains.

## Verdict

Validation remains necessary."""
    monkeypatch.setattr(
        detailed_report,
        "call_anthropic_text",
        lambda *_args, **_kwargs: '{"sections":[{"heading":"Strategy","content":"The corrected strategy paragraph now reads fluently."}]}',
    )

    repaired, model = detailed_report.repair_detailed_report_sections(
        report,
        affected_sections=["Strategy"],
        claim_issues=[],
    )

    assert model == detailed_report.CLAUDE_SONNET_MODEL
    assert "Supported opening remains." in repaired
    assert "Supported operating context remains." in repaired
    assert "Supported AI context remains." in repaired
    assert "The corrected strategy paragraph now reads fluently." in repaired


def test_detailed_report_language_qa_removes_adjacent_duplicate_words():
    polished = detailed_report.polish_detailed_report_text(
        "The manufacturer manufacturer expanded its supported portfolio."
    )

    assert polished == "The manufacturer expanded its supported portfolio."


def test_final_language_review_repairs_only_flagged_paragraphs(monkeypatch):
    text = """## Key Findings

Supported context remains unchanged.

Profitable growth, profitable growth, creates a clear identity, and a clear identity, for the offer.

## Verdict

Validation remains necessary."""
    monkeypatch.setattr(
        detailed_report,
        "call_anthropic_text",
        lambda *_args, **_kwargs: (
            '{"repairs":[{"item_id":"P003","text":'
            '"Profitable growth creates a clear identity for the offer."}]}'
        ),
    )

    reviewed, metadata = detailed_report.final_public_language_review(
        text,
        time_budget_seconds=20,
    )

    assert "Supported context remains unchanged." in reviewed
    assert "Profitable growth creates a clear identity for the offer." in reviewed
    assert metadata["status"] == "repaired"
    assert metadata["repaired_paragraph_count"] == 1


def test_final_language_review_rejects_repairs_that_change_numbers(monkeypatch):
    text = "Revenue revenue reached EUR 91.6 million in 2025."
    monkeypatch.setattr(
        detailed_report,
        "call_anthropic_text",
        lambda *_args, **_kwargs: (
            '{"repairs":[{"item_id":"P001","text":'
            '"Revenue reached EUR 92.0 million in 2025."}]}'
        ),
    )

    reviewed, metadata = detailed_report.final_public_language_review(
        text,
        time_budget_seconds=20,
    )

    assert "91.6" in reviewed
    assert "92.0" not in reviewed
    assert metadata["status"] == "clean"


def test_unavailable_verifier_cannot_publish_as_generated_verified():
    status = detailed_report.detailed_report_publication_status(
        {"complete": True},
        {"verified": False, "verification_status": "unavailable"},
    )

    assert status == "internal_draft_verification_pending"


def test_clean_verified_report_publishes_as_generated_verified():
    status = detailed_report.detailed_report_publication_status(
        {"complete": True},
        {"verified": True, "verification_status": "verified"},
    )

    assert status == "generated_verified"


def test_safe_condensation_has_specific_verified_publication_status():
    status = detailed_report.detailed_report_publication_status(
        {"complete": True, "condensed": False},
        {"verified": True, "deterministic_removals": 2},
        {"repair_mode": "safe_condensation", "recovery_status": "succeeded"},
    )

    assert status == "verified_condensed_after_removals"


def test_unresolved_material_claims_are_withheld():
    status = detailed_report.detailed_report_publication_status(
        {"complete": True},
        {
            "verified": False,
            "unresolved_blocking_claims": ["Unsupported current deployment claim."],
        },
    )

    assert status == "withheld_material_claims"


def test_claim_action_manifest_preserves_prohibited_current_run_proposition():
    report = """## Key Findings

Supported context remains.

## Operating Model

The company has deployed enterprise AI across every operation.

## Strategy

Supported strategy remains.

## AI Readiness

Validation is required.

## Verdict

Further evidence is needed."""
    manifest = detailed_report.build_detailed_report_claim_action_manifest(
        report,
        [{
            "claim_id": "DR-AI-1",
            "claim": "The company has deployed enterprise AI across every operation.",
            "error_type": "Fabrication",
            "correction": "Current evidence does not establish enterprise-wide AI deployment.",
            "source_ids": ["EVD-1"],
        }],
    )

    assert manifest[0]["claim_id"] == "DR-AI-1"
    assert manifest[0]["section"] == "Operating Model"
    assert manifest[0]["issue_type"] == "false_fact"
    assert manifest[0]["approved_narrowing"].startswith("Current evidence")
    assert manifest[0]["current_run_only"] is True


def test_safe_paragraph_removal_clears_claim_sentence_matching_missed():
    report = """## Key Findings

Supported context remains.

## Operating Model

Supplier concentration across internationally distributed partners creates a proven operational bottleneck for the company. The paragraph depends on that unsupported proposition.

## Strategy

Supported strategy remains.

## AI Readiness

Validation is required.

## Verdict

Further evidence is needed."""
    claims = [{
        "claim": "The company's internationally distributed supplier base proves a supplier-concentration operational bottleneck."
    }]

    cleaned, removed = detailed_report.remove_flagged_report_paragraphs(report, claims)

    assert removed == 1
    assert "operational bottleneck" not in cleaned
    assert "## Verdict" in cleaned
