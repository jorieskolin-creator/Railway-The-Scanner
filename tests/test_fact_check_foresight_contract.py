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


def test_valid_abs_premise_is_not_reported_as_source_handoff(monkeypatch):
    premise_id = "ABS-AI-READINESS-1234"

    def fake_call(_prompt, **_kwargs):
        return json.dumps({
            "false_claims": [{
                "section": "Strategic Synthesis",
                "claim": "The promoted evidence set does not currently verify enterprise AI maturity.",
                "claim_class": "strategic_inference",
                "error_type": "Evidence Handoff Failure",
                "support_level": "context_only",
                "missing_or_contradicted_premise": premise_id,
                "reasoning": "The ABS identifier was treated as a missing source.",
                "correction": "",
                "source_ids": [premise_id],
            }],
            "summary_accurate": False,
        })

    monkeypatch.setattr(reality_check, "_call_openai_text", fake_call)
    result = reality_check._provider_fact_check(
        "The promoted evidence set does not currently verify enterprise AI maturity.",
        "ExampleCo",
        scan_payload={
            "evidence_absence_signals": [{
                "signal_id": premise_id,
                "dimension": "AI Readiness",
                "gap_status": "not_verified",
                "safe_wording": "The promoted evidence set does not currently verify enterprise AI maturity.",
            }],
        },
    )

    assert result["false_claims"] == []


def test_unresolved_abs_reference_is_premise_mismatch_not_handoff(monkeypatch):
    calls = []

    def fake_call(_prompt, **_kwargs):
        calls.append(1)
        return json.dumps({
            "false_claims": [{
                "section": "Strategic Synthesis",
                "claim": "A proof gap remains unresolved.",
                "claim_class": "strategic_inference",
                "error_type": "Evidence Handoff Failure",
                "support_level": "unsupported",
                "missing_or_contradicted_premise": "ABS-NOT-IN-MANIFEST",
                "reasoning": "The premise identifier cannot be resolved.",
                "correction": "",
                "source_ids": ["ABS-NOT-IN-MANIFEST"],
            }],
            "summary_accurate": False,
        })

    monkeypatch.setattr(reality_check, "_call_openai_text", fake_call)
    result = reality_check._provider_fact_check(
        "A proof gap remains unresolved.",
        "ExampleCo",
        scan_payload={"evidence_absence_signals": []},
    )

    assert len(calls) == 1, "Premise mismatch must not trigger wording correction"
    assert result["false_claims"][0]["error_type"] == "Premise Reference Mismatch"
    assert result["evidence_handoff_failure"] is False


def test_mixed_handoff_and_correction_preserves_correction_status(monkeypatch):
    blocked_claim = "ExampleCo has deployed enterprise AI across all operations."

    monkeypatch.setattr(reality_check, "_provider_fact_check", lambda *_args, **_kwargs: {
        "false_claims": [
            {
                "claim": blocked_claim,
                "section": "AI Diagnosis",
                "error_type": "Fabrication",
                "claim_class": "external_company_fact",
                "support_level": "unsupported",
                "reasoning": "No source supports this deployment claim.",
                "correction": "The available evidence does not establish enterprise-wide AI deployment.",
                "source_ids": [],
                "source_indices": [],
                "source_urls": [],
                "claim_id": "FC-BLOCKED",
            },
            {
                "claim": "A separate cited source was not present in the handoff.",
                "section": "Evidence-Based Risks",
                "error_type": "Evidence Handoff Failure",
                "claim_class": "external_company_fact",
                "support_level": "unsupported",
                "missing_or_contradicted_premise": "EVD-MISSING",
                "reasoning": "The source record was absent.",
                "source_ids": ["EVD-MISSING"],
                "source_indices": [],
                "source_urls": [],
                "claim_id": "FC-HANDOFF",
            },
        ],
        "corrected_summary": "The available evidence does not establish enterprise-wide AI deployment.",
        "corrected_pitch": "",
        "corrected_sections": {},
        "model": "test-verifier+test-corrector",
        "time_ms": 1,
    })

    result = reality_check.ai_fact_check_summary(
        blocked_claim,
        [],
        "ExampleCo",
    )

    assert result["verification_status"] == "corrected_with_handoff_warnings"
    assert result["corrected_summary"].startswith("The available evidence")
    assert result["corrected_claim_ids"] == ["FC-BLOCKED"]
    assert result["unresolved_handoff_ids"] == ["EVD-MISSING"]


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


def test_wording_precision_mismatch_is_non_blocking():
    issue_type, blocking = reality_check._classify_fact_check_issue({
        "claim": "Vivicta covers enterprise applications and cloud services.",
        "claim_class": "external_company_fact",
        "error_type": "Contradiction",
        "support_level": "partial",
        "reasoning": "The supported source lists 'enterprise application' singular, not 'enterprise applications'.",
    }, scan_payload={})

    assert issue_type == "wording_precision_note"
    assert blocking is False


def test_structured_financial_payload_support_is_non_blocking():
    issue_type, blocking = reality_check._classify_fact_check_issue({
        "claim": "Vivicta reported operating profit of 3.2 million euros.",
        "claim_class": "structured_financial_payload_fact",
        "error_type": "Contradiction",
        "support_level": "unsupported",
        "reasoning": "The displayed source snippet does not repeat operating profit.",
    }, scan_payload={"financial_payload": {"operating_profit": "3.2 million euros"}})

    assert issue_type == "structured_payload_supported"
    assert blocking is False


def test_cross_scan_scanner_observation_remains_blocking():
    issue_type, blocking = reality_check._classify_fact_check_issue({
        "claim": "The intent gap widened across previous scans.",
        "claim_class": "internal_scanner_observation",
        "error_type": "Prohibited Scanner Observation",
        "support_level": "unsupported",
        "reasoning": "This compares current output with previous scans.",
    }, scan_payload={})

    assert issue_type == "blocking_false_claim"
    assert blocking is True


def test_unsafe_proof_gap_wording_is_rewritten_locally():
    text = (
        "The gap is proof that global digital and AI signals translate into "
        "ABB Oy's own operating model."
    )

    cleaned = reality_check.sanitize_inference_wording(text)

    assert "gap is proof that" not in cleaned.lower()
    assert (
        "the unresolved question is whether global digital and AI signals "
        "translate into ABB Oy's own operating model"
    ) in cleaned


def test_unsafe_proof_gap_wording_requires_non_blocking_calibration():
    issue_type, blocking = reality_check._classify_fact_check_issue({
        "claim": (
            "The gap is proof that global digital and AI signals translate "
            "into ABB Oy's own operating model."
        ),
        "claim_class": "strategic_inference",
        "error_type": "Fabrication",
        "support_level": "context_only",
        "reasoning": "The sentence overstates a proof gap as proof of company action.",
    }, scan_payload={})

    assert issue_type == "wording_calibration"
    assert blocking is False


def test_generation_time_absence_manifest_remains_resolvable():
    premises = reality_check._build_fact_check_premise_pack({
        "generation_premise_manifest": [{
            "signal_id": "ABS-GENERATION",
            "dimension": "AI readiness",
            "gap_status": "proof_gap",
            "safe_wording": "The promoted evidence does not currently confirm deployed AI capability.",
        }],
        "evidence_absence_signals": [],
    })

    assert [item["premise_id"] for item in premises] == ["ABS-GENERATION"]


def test_unsupported_causal_attribution_is_wording_calibration():
    issue_type, blocking = reality_check._classify_fact_check_issue({
        "claim": "Profitability was driven by military demand.",
        "claim_class": "strategic_inference",
        "error_type": "Misattribution",
        "support_level": "unsupported",
        "reasoning": "The causal driver is not established by the available evidence.",
        "correction": "The available evidence records profitability and military demand separately.",
    }, scan_payload={})

    assert issue_type == "wording_calibration"
    assert blocking is False


def test_unsupported_present_company_capability_remains_blocking():
    issue_type, blocking = reality_check._classify_fact_check_issue({
        "claim": "The company has deployed enterprise AI across its factories.",
        "claim_class": "external_company_fact",
        "error_type": "Fabrication",
        "support_level": "unsupported",
        "reasoning": "No eligible source supports a current deployment.",
        "correction": "The promoted evidence does not currently verify an enterprise AI deployment.",
    }, scan_payload={})

    assert issue_type == "blocking_false_claim"
    assert blocking is True


def test_concrete_false_event_blocks_even_if_model_calls_it_inference():
    issue_type, blocking = reality_check._classify_fact_check_issue({
        "claim": "The company has signed a 2026 acquisition agreement.",
        "claim_class": "strategic_inference",
        "error_type": "Misattribution",
        "support_level": "unsupported",
        "reasoning": "The cited sources do not contain that agreement and the event did not happen.",
        "correction": "",
    }, scan_payload={})

    assert issue_type == "blocking_false_claim"
    assert blocking is True


def test_detailed_report_fact_check_preserves_successful_batches(monkeypatch):
    calls = []

    def fake_check(*_args, **kwargs):
        text = kwargs["summary_text"]
        calls.append(text)
        if "## Verdict" in text:
            return {
                "verified": False,
                "verification_status": "unavailable",
                "fact_check_skipped": True,
                "fact_check_model": "all_models_failed",
            }
        return {
            "verified": True,
            "verification_status": "verified",
            "fact_check_skipped": False,
            "fact_check_model": "gpt-5.4",
        }

    monkeypatch.setattr(reality_check, "ai_fact_check_summary", fake_check)
    report = "\n\n".join(
        f"## {heading}\n\nSupported {heading.lower()} analysis."
        for heading in ("Key Findings", "Operating Model", "Strategy", "AI Readiness", "Verdict")
    )

    result = reality_check.ai_fact_check_detailed_report(
        report,
        [],
        "Example Oy",
        time_budget_seconds=85,
    )

    assert len(calls) == 2
    assert result["verification_status"] == "partially_unavailable"
    assert result["verification_partial"] is True
    assert result["verified"] is False
    assert result["batch_results"][0]["status"] == "verified"
    assert result["unavailable_sections"] == ["AI Readiness", "Verdict"]


def test_detailed_report_fact_check_keeps_calibration_out_of_false_claims(monkeypatch):
    calibration = {
        "claim": "Growth was driven by one customer segment.",
        "issue_type": "wording_calibration",
        "correction": "Growth coincided with reported demand in that customer segment.",
    }

    monkeypatch.setattr(
        reality_check,
        "ai_fact_check_summary",
        lambda *_args, **_kwargs: {
            "verified": False,
            "verification_status": "wording_calibration_required",
            "fact_check_skipped": False,
            "fact_check_model": "gpt-5.4",
            "false_claims": [],
            "false_claims_detailed": [],
            "wording_adjustments": [calibration["claim"]],
            "wording_adjustments_detailed": [calibration],
        },
    )

    result = reality_check.ai_fact_check_detailed_report(
        "## Key Findings\n\nGrowth was driven by one customer segment.",
        [],
        "Example Oy",
        time_budget_seconds=45,
    )

    assert result["verification_status"] == "wording_calibration_required"
    assert result["false_claims"] == []
    assert result["wording_adjustments"] == [calibration["claim"]]
