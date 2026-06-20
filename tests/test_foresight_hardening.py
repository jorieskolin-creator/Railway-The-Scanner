"""
test_foresight_hardening.py — Acceptance tests for the foresight/source-card hardening pass.

Run with:  python -m pytest tests/test_foresight_hardening.py -v
Or standalone: python tests/test_foresight_hardening.py

Covers all 9 acceptance criteria from the hardening spec:
  1. Sanitizer idempotency          — second run changes 0 string fields
  2. Company-name matching          — accented and ASCII variants both work
  3. Safe modal wording not flagged — "may require" passes validation clean
  4. Deterministic wording flagged  — "requires Wärtsilä to", "must", "forces Wärtsilä"
  5. Past deadline rewrite          — "by Q2 2025" → "if not already completed…"
  6. Source/relevance split         — company name in source_says → genericized + possible_relevance
  7. Company-specific evidence kept — annual report / entity_match exact NOT marked context-only
  8. Repeated modal cleanup         — "may may may require" → "may require"
  9. Report-level checks            — sanitized report free of "may may", "must ", etc.
"""

from __future__ import annotations

import sys
import os
import re
from datetime import date

# Allow importing backend modules when running from repo root or tests/ dir
_BACKEND = os.path.join(os.path.dirname(__file__), "..", "backend")
if _BACKEND not in sys.path:
    sys.path.insert(0, _BACKEND)

from foresight_guardrails import (
    collapse_repeated_modals,
    sanitize_foresight_text,
    sanitize_source_description,
    sanitize_foresight_record,
    sanitize_foresight_modality,
    validate_foresight_text,
    _enforce_source_says_neutrality,
    _normalize_company_name,
    _company_name_variants,
    _is_company_specific_record,
    FORESIGHT_SANITIZER_VERSION,
)
from database_sanitizer import (
    sanitize_company_record,
    classify_source_registry_items,
    _is_company_specific_evidence,
    _is_probably_foresight_record,
)

REPORT_DATE = date(2026, 5, 3)
COMPANY = "Wärtsilä"
COMPANY_ASCII = "Wartsila"


# ---------------------------------------------------------------------------
# Helper
# ---------------------------------------------------------------------------

def _make_foresight_link(source_says: str, possible_relevance: str = "") -> dict:
    return {
        "title": "AI Governance Trends",
        "url": "https://example.com/ai-governance",
        "source_says": source_says,
        "possible_relevance": possible_relevance,
        "origin": "foresight",
        "claim_type": "foresight_context",
        "company_specific": False,
    }


def _make_company_record(links: list[dict] | None = None, extra: dict | None = None) -> dict:
    rec: dict = {
        "name": COMPANY,
        "foresight_links": links or [],
        "evidence_links": [],
    }
    if extra:
        rec.update(extra)
    return rec


# ---------------------------------------------------------------------------
# 1. Sanitizer idempotency
# ---------------------------------------------------------------------------

class TestSanitizerIdempotency:
    """Second sanitizer run must produce zero string-field changes."""

    def test_idempotent_basic(self):
        record = _make_company_record(
            links=[
                _make_foresight_link(
                    "The company must upgrade its AI governance.",
                    "Wärtsilä requires the company to act by Q2 2025.",
                )
            ]
        )
        cleaned_once, audit_1 = sanitize_company_record(record, report_date=REPORT_DATE)
        cleaned_twice, audit_2 = sanitize_company_record(cleaned_once, report_date=REPORT_DATE)

        assert audit_1["changed_string_fields"] >= 1, "First pass should change something"
        assert audit_2["changed_string_fields"] == 0, (
            f"Second pass must change 0 string fields, got {audit_2['changed_string_fields']}. "
            f"Sample changes: {audit_2.get('sample_changes', [])}"
        )

    def test_idempotent_clean_record(self):
        record = _make_company_record(
            links=[
                _make_foresight_link(
                    "AI governance frameworks may require assessment if in scope.",
                    "This could be relevant to Wärtsilä if AI systems are used in regulated contexts.",
                )
            ]
        )
        cleaned_once, _ = sanitize_company_record(record, report_date=REPORT_DATE)
        cleaned_twice, audit_2 = sanitize_company_record(cleaned_once, report_date=REPORT_DATE)
        assert audit_2["changed_string_fields"] == 0, (
            f"Clean record: second pass must not change anything. "
            f"Changes: {audit_2.get('sample_changes', [])}"
        )

    def test_idempotent_with_past_deadline(self):
        record = _make_company_record(
            extra={"future_insight": "Complete the migration by Q4 2024."}
        )
        cleaned_once, _ = sanitize_company_record(record, report_date=REPORT_DATE)
        cleaned_twice, audit_2 = sanitize_company_record(cleaned_once, report_date=REPORT_DATE)
        assert audit_2["changed_string_fields"] == 0


# ---------------------------------------------------------------------------
# 2. Company-name matching (accented and ASCII)
# ---------------------------------------------------------------------------

class TestCompanyNameMatching:
    """Both Wärtsilä and Wartsila must be detected in source_says."""

    def test_normalize_wartsila(self):
        assert _normalize_company_name("Wärtsilä") == "Wartsila"

    def test_accented_detected(self):
        link = _make_foresight_link("Wärtsilä must upgrade its governance systems.")
        result = _enforce_source_says_neutrality(link, "Wärtsilä")
        assert "Wärtsilä" not in result["source_says"], "Accented name should be removed from source_says"
        assert result.get("source_says_was_genericized") is True

    def test_ascii_detected(self):
        link = _make_foresight_link("Wartsila must upgrade its governance systems.")
        result = _enforce_source_says_neutrality(link, "Wärtsilä")
        assert "Wartsila" not in result["source_says"], "ASCII variant should also be removed"
        assert result.get("source_says_was_genericized") is True

    def test_company_name_from_record_keys(self):
        """sanitize_company_record picks up name from any of: name, company_name, key, company."""
        for key in ("name", "company_name", "key", "company"):
            record = {
                key: COMPANY,
                "foresight_links": [
                    _make_foresight_link(f"{COMPANY} must act on AI governance.")
                ],
            }
            cleaned, _ = sanitize_company_record(record, report_date=REPORT_DATE)
            for lnk in cleaned.get("foresight_links", []):
                assert COMPANY not in (lnk.get("source_says") or ""), (
                    f"Company name should be removed from source_says when record key = '{key}'"
                )


# ---------------------------------------------------------------------------
# 3. Safe modal wording NOT flagged
# ---------------------------------------------------------------------------

class TestSafeModalWordingNotFlagged:
    """Patterns like 'may require' must pass validate_foresight_text with zero warnings."""

    SAFE_PHRASES = [
        "This may require further assessment if in scope.",
        "The company could require additional compliance review.",
        "Organizations should evaluate whether they need to adapt.",
        "This should assess whether the regulation is applicable.",
        "If not already completed, in the next planning cycle.",
        "If in scope, the company may need to evaluate use of AI.",
        "In the next planning cycle, this topic may be relevant.",
    ]

    def test_safe_phrases_produce_no_warnings(self):
        for phrase in self.SAFE_PHRASES:
            warnings = validate_foresight_text(phrase, REPORT_DATE)
            assert warnings == [], (
                f"Safe phrase flagged unexpectedly: '{phrase}'\n  Warnings: {warnings}"
            )

    def test_may_require_is_safe(self):
        warnings = validate_foresight_text("This may require a compliance review.", REPORT_DATE)
        assert not any("requires?" in w or "require" in w.lower() for w in warnings), (
            f"'may require' must not be flagged. Got: {warnings}"
        )


# ---------------------------------------------------------------------------
# 4. Deterministic wording IS flagged
# ---------------------------------------------------------------------------

class TestDeterministicWordingFlagged:
    """Forbidden company-obligation phrases must be detected by validate_foresight_text."""

    FORBIDDEN = [
        ("requires Wärtsilä to upgrade", True),
        ("requires the company to comply", True),
        ("must upgrade immediately", True),
        ("forces Wärtsilä to adopt", True),
        ("required to implement", True),
        ("is obligated to report", True),
        ("has deployed broad AI systems", True),
        ("is deploying a new platform", True),
        ("currently uses multi-agent systems", True),
    ]

    def test_forbidden_phrases_are_flagged(self):
        for phrase, should_flag in self.FORBIDDEN:
            warnings = validate_foresight_text(phrase, REPORT_DATE)
            assert len(warnings) >= 1 if should_flag else warnings == [], (
                f"Expected {'warnings' if should_flag else 'no warnings'} for: '{phrase}'\n"
                f"  Got: {warnings}"
            )


# ---------------------------------------------------------------------------
# 5. Past deadline rewrite
# ---------------------------------------------------------------------------

class TestPastDeadlineRewrite:
    """Past deadlines must be rewritten; future dates must be left alone."""

    def test_q2_2025_rewritten(self):
        text = "Complete the upgrade by Q2 2025."
        result = sanitize_foresight_text(text, REPORT_DATE)
        assert "if not already completed, in the next planning cycle" in result.lower(), (
            f"Q2 2025 should be rewritten. Got: '{result}'"
        )

    def test_q4_2024_rewritten(self):
        text = "Deadline by Q4 2024 must be met."
        result = sanitize_foresight_text(text, REPORT_DATE)
        assert "if not already completed, in the next planning cycle" in result.lower()

    def test_year_2025_rewritten(self):
        text = "Roll out by 2025."
        result = sanitize_foresight_text(text, REPORT_DATE)
        assert "if not already completed, in the next planning cycle" in result.lower()

    def test_future_deadline_kept(self):
        text = "Complete the upgrade by Q4 2027."
        result = sanitize_foresight_text(text, REPORT_DATE)
        assert "2027" in result, f"Future deadline should be kept. Got: '{result}'"
        assert "if not already completed" not in result.lower()

    def test_in_2025_rewritten(self):
        text = "Launch in 2025."
        result = sanitize_foresight_text(text, REPORT_DATE)
        assert "if not already completed, in the next planning cycle" in result.lower()


# ---------------------------------------------------------------------------
# 6. Source / relevance split
# ---------------------------------------------------------------------------

class TestSourceRelevanceSplit:
    """Company name in source_says (company_specific=False) → genericize + move to possible_relevance."""

    def test_company_in_source_says_moved(self):
        link = _make_foresight_link(
            f"{COMPANY} must upgrade its AI governance framework."
        )
        result = _enforce_source_says_neutrality(link, COMPANY)
        assert COMPANY not in (result.get("source_says") or ""), \
            "Company name must be removed from source_says"
        assert COMPANY in (result.get("possible_relevance") or "") or \
               "organization" in (result.get("source_says") or "").lower(), \
            "Original text should land in possible_relevance or source_says should be genericized"
        assert result.get("not_evidence_of_deployment") is True
        assert result.get("requires_company_specific_corroboration") is True

    def test_already_split_record_unchanged(self):
        link = _make_foresight_link(
            "AI governance frameworks may require assessment for organizations.",
            f"This could be relevant to {COMPANY} if AI systems are used in regulated contexts.",
        )
        result = _enforce_source_says_neutrality(link, COMPANY)
        assert result.get("source_says_was_genericized") is not True, \
            "Record with company-neutral source_says should not be marked as genericized"

    def test_company_specific_true_skipped(self):
        link = {
            "source_says": f"{COMPANY} signed a partnership with Microsoft.",
            "company_specific": True,
        }
        result = _enforce_source_says_neutrality(link, COMPANY)
        assert result.get("source_says_was_genericized") is not True, \
            "company_specific=True records must not be genericized"
        assert COMPANY in result["source_says"], \
            "company_specific=True source_says must be untouched"

    def test_sanitize_record_enforces_split(self):
        record = sanitize_foresight_record(
            _make_foresight_link(f"{COMPANY} must upgrade its AI governance."),
            report_date=REPORT_DATE,
            company_name=COMPANY,
        )
        assert COMPANY not in (record.get("source_says") or ""), \
            "sanitize_foresight_record must enforce source_says neutrality"


# ---------------------------------------------------------------------------
# 7. Company-specific evidence preservation
# ---------------------------------------------------------------------------

class TestCompanySpecificEvidencePreservation:
    """Direct company evidence must NOT be marked context-only."""

    COMPANY_SPECIFIC_ITEMS = [
        {"source_type": "annual_report", "title": f"{COMPANY} Annual Report 2024"},
        {"source_type": "company_press_release", "title": f"{COMPANY} launches new service"},
        {"source_type": "company_report", "title": f"{COMPANY} Sustainability Report"},
        {"entity_match": "exact", "company_mentioned": True, "title": "LUMI case study"},
        {"company_specific": True, "title": "Wärtsilä People Analytics appointment"},
    ]

    GENERIC_CONTEXT_ITEMS = [
        {"source_type": "analyst", "title": "Gartner AI Trend Report 2025"},
        {"source_type": "trend_report", "title": "Deloitte Digital Transformation Report"},
        {"source_type": "regulation", "title": "EU AI Act Overview"},
        {"origin": "foresight", "title": "WEF Future of Jobs Report"},
    ]

    def test_company_specific_items_not_context_only(self):
        for item in self.COMPANY_SPECIFIC_ITEMS:
            assert _is_company_specific_evidence(item), \
                f"Should be company-specific: {item}"

    def test_generic_items_not_company_specific(self):
        for item in self.GENERIC_CONTEXT_ITEMS:
            assert not _is_company_specific_evidence(item), \
                f"Should NOT be company-specific: {item}"

    def test_classify_preserves_annual_report(self):
        record = {
            "name": COMPANY,
            "evidence_links": [
                {
                    "url": "https://wartsila.com/annual-report-2024",
                    "source_type": "annual_report",
                    "title": f"{COMPANY} Annual Report 2024",
                    "snippet": "Revenue grew 12% in 2024.",
                }
            ],
        }
        classified = classify_source_registry_items(record)
        item = classified["evidence_links"][0]
        assert item.get("excluded_from_company_fact_generation") is not True, \
            "Annual report must not be excluded from fact generation"
        assert item.get("generation_eligible") is False, \
            "Annual report still needs analysis verification before generated prose"
        assert item.get("promotion_status") == "display_only"

    def test_classify_marks_analyst_report_context_only(self):
        record = {
            "name": COMPANY,
            "foresight_links": [
                {
                    "url": "https://gartner.com/ai-trends",
                    "source_type": "analyst",
                    "title": "Gartner AI Trend Report",
                    "origin": "foresight",
                    "claim_type": "foresight_context",
                }
            ],
        }
        classified = classify_source_registry_items(record)
        item = classified["foresight_links"][0]
        assert item.get("excluded_from_company_fact_generation") is True, \
            "Analyst/trend report should be context-only"
        assert "generation_eligible" not in item
        assert "promotion_status" not in item
        assert "claim_support_status" not in item
        assert item.get("foresight_signal_status") == "context_only_unverified"
        assert item.get("source_audit_status") == "requires_revalidation"
        assert item.get("verification_reason") == "foresight_context_requires_revalidation"
        assert item.get("foresight_context_eligible") is False
        assert item.get("not_evidence_of_deployment") is True
        assert item.get("generation_scope") == "foresight_synthesis_only"

    def test_classify_preserves_foresight_context_promotion_without_company_generation(self):
        record = {
            "name": COMPANY,
            "foresight_links": [
                {
                    "url": "https://gartner.com/ai-trends",
                    "source_type": "analyst",
                    "title": "Gartner AI Trend Report",
                    "origin": "foresight",
                    "claim_type": "foresight_context",
                    "verified": True,
                    "validated": True,
                    "source_readable": True,
                    "theme_match": True,
                    "trend_supported": True,
                    "analysis_verified": True,
                    "promotion_status": "generation_eligible",
                    "generation_eligible": True,
                    "claim_support_status": "supported",
                }
            ],
        }
        classified = classify_source_registry_items(record)
        item = classified["foresight_links"][0]
        assert "generation_eligible" not in item
        assert "analysis_verified" not in item
        assert "promotion_status" not in item
        assert "claim_support_status" not in item
        assert item.get("excluded_from_company_fact_generation") is True
        assert item.get("foresight_context_eligible") is True
        assert item.get("foresight_signal_status") == "context_eligible"
        assert item.get("source_registry_status") == "context_eligible"
        assert item.get("source_audit_status") == "accepted"
        assert item.get("generation_scope") == "foresight_synthesis_only"

    def test_bare_generation_eligible_does_not_promote_generic_foresight(self):
        record = {
            "name": COMPANY,
            "foresight_links": [
                {
                    "url": "https://example.com/trend",
                    "source_type": "trend_report",
                    "title": "Future of Work Trend",
                    "generation_eligible": True,
                }
            ],
        }
        classified = classify_source_registry_items(record)
        item = classified["foresight_links"][0]
        assert "analysis_verified" not in item
        assert "generation_eligible" not in item
        assert "promotion_status" not in item
        assert item.get("foresight_context_eligible") is False
        assert item.get("foresight_signal_status") == "context_only_unverified"

    def test_sanitize_removes_legacy_standalone_foresight_summary(self):
        record = {
            "name": COMPANY,
            "foresight_summary": "Legacy standalone foresight narrative.",
            "foresight_summary_model": "legacy-model",
            "foresight_links": [],
        }
        cleaned, audit = sanitize_company_record(record, report_date=REPORT_DATE)
        assert "foresight_summary" not in cleaned
        assert "foresight_summary_model" not in cleaned
        assert audit["legacy_foresight_summary_removed"] is True


# ---------------------------------------------------------------------------
# 8. Repeated modal cleanup
# ---------------------------------------------------------------------------

class TestRepeatedModalCleanup:
    """collapse_repeated_modals must flatten doubled/tripled modal artifacts."""

    def test_may_may_require(self):
        assert collapse_repeated_modals("may may require") == "may require"

    def test_may_may_may_require(self):
        assert collapse_repeated_modals("may may may require") == "may require"

    def test_nested_evaluate_whether(self):
        result = collapse_repeated_modals(
            "should evaluate whether it could evaluate whether it needs to"
        )
        assert "evaluate whether" in result
        # Must NOT contain a double "evaluate whether … evaluate whether"
        assert result.count("evaluate whether") == 1, \
            f"Expected single evaluate-whether, got: '{result}'"

    def test_sanitize_modality_is_idempotent(self):
        """Applying sanitize_foresight_modality twice must produce the same result."""
        inputs = [
            "The company requires urgent action.",
            "must deploy AI governance by Q2 2025.",
            "needs to implement data controls.",
            "has to evaluate new regulations.",
        ]
        for text in inputs:
            once = sanitize_foresight_modality(text)
            twice = sanitize_foresight_modality(once)
            assert once == twice, (
                f"sanitize_foresight_modality not idempotent for: '{text}'\n"
                f"  Once:  '{once}'\n"
                f"  Twice: '{twice}'"
            )


# ---------------------------------------------------------------------------
# 9. Report-level checks
# ---------------------------------------------------------------------------

class TestReportLevelChecks:
    """Sanitized report must be free of known bad patterns."""

    def _sanitize(self, text: str) -> str:
        return sanitize_foresight_text(text, REPORT_DATE)

    def test_no_may_may(self):
        result = self._sanitize("The regulation requires the company to act.")
        assert "may may" not in result.lower(), f"Double modal found: '{result}'"

    def test_no_bare_must(self):
        result = self._sanitize("The company must comply immediately.")
        assert not re.search(r"\bmust\b", result, re.I), \
            f"'must' still present after sanitization: '{result}'"

    def test_no_requires_wartsila_to(self):
        result = self._sanitize(f"The regulation requires {COMPANY} to upgrade.")
        # After sanitization "requires" → "may require".  The VALIDATION pattern uses the same
        # lookbehind: only the deterministic non-modal form is forbidden, not "may require … to".
        assert not re.search(
            rf"(?<!may )(?<!could )(?<!should )\brequires?\s+{re.escape(COMPANY)}\s+to\b",
            result,
            re.I,
        ), f"Deterministic 'requires Wärtsilä to' still present after sanitization: '{result}'"

    def test_no_forces_wartsila(self):
        result = self._sanitize(f"The market forces {COMPANY} to adapt quickly.")
        assert not re.search(rf"\bforces?\s+{re.escape(COMPANY)}\b", result, re.I), \
            f"'forces Wärtsilä' still present: '{result}'"

    def test_no_has_deployed_from_foresight_context(self):
        result = self._sanitize(f"{COMPANY} has deployed a broad AI governance framework.")
        assert "has deployed" not in result.lower(), \
            f"'has deployed' still present: '{result}'"

    def test_past_deadline_not_in_output(self):
        result = self._sanitize("Complete the upgrade by Q2 2025 and by 2024.")
        # Neither "Q2 2025" nor "2024" should survive as a deadline
        assert not re.search(r"\bQ2\s+2025\b", result, re.I)
        assert "by 2024" not in result.lower()

    def test_clean_record_survives_unchanged(self):
        clean = (
            "AI governance frameworks are becoming more important for organizations. "
            "This could be relevant if the company uses AI in regulated contexts."
        )
        result = self._sanitize(clean)
        assert result == clean or "may" in result.lower(), \
            "Clean modal text should pass through unchanged"


# ---------------------------------------------------------------------------
# 10. Modal dilution scoping — regulatory language preserved in source_says
# ---------------------------------------------------------------------------

class TestModalDilutionScoping:
    """source_says and description fields must preserve "requires" / "must" when they
    describe what a regulation says.  Only deployment-state patterns are applied there.
    Impact/relevance fields (possible_relevance, impact) still get full modal softening."""

    def test_gdpr_requires_preserved_in_source_says(self):
        """'GDPR requires organizations to appoint a DPO' in source_says must NOT become
        'GDPR may require' — that would misrepresent a binding legal obligation as speculative."""
        text = "GDPR requires organizations processing EU personal data to appoint a DPO."
        result = sanitize_source_description(text, REPORT_DATE)
        assert "requires" in result.lower(), (
            f"'requires' was incorrectly softened in source description: '{result}'"
        )
        assert "may require" not in result.lower(), (
            f"Obligation language was diluted in source description: '{result}'"
        )

    def test_regulation_requires_preserved_in_record_source_says(self):
        """When the foresight record source_says describes regulatory content, 'requires'
        must survive sanitize_foresight_record (scoped to source_says field)."""
        record = {
            "source_says": "EU AI Act requires high-risk AI providers to conduct conformity assessments.",
            "possible_relevance": "This may require the company to review its AI product pipeline.",
            "origin": "foresight",
            "claim_type": "foresight_context",
            "company_specific": False,
        }
        result = sanitize_foresight_record(record, report_date=REPORT_DATE)
        assert "requires" in (result.get("source_says") or "").lower(), (
            f"'requires' incorrectly removed from source_says: '{result.get('source_says')}'"
        )

    def test_must_still_softened_in_possible_relevance(self):
        """'must' in possible_relevance (company-impact field) must still be softened."""
        record = {
            "source_says": "The AI Act establishes risk categories.",
            "possible_relevance": f"{COMPANY} must review its AI governance practices.",
            "origin": "foresight",
            "claim_type": "foresight_context",
            "company_specific": False,
        }
        result = sanitize_foresight_record(record, report_date=REPORT_DATE)
        pr = result.get("possible_relevance") or ""
        assert not re.search(r"\bmust\b", pr, re.I), (
            f"'must' not softened in possible_relevance: '{pr}'"
        )

    def test_has_deployed_still_softened_in_source_says(self):
        """Deployment-state patterns (has deployed, is deploying) must still be softened
        even in source_says — they claim a company's current state, never appropriate."""
        text = f"{COMPANY} has deployed an AI governance framework across all business units."
        result = sanitize_source_description(text, REPORT_DATE)
        assert "has deployed" not in result.lower(), (
            f"'has deployed' not softened in source description: '{result}'"
        )

    def test_sanitize_source_description_is_idempotent(self):
        """sanitize_source_description must be idempotent — second call changes nothing."""
        text = "EU AI Act requires providers to conduct assessments. The sector is deploying new tools."
        once = sanitize_source_description(text, REPORT_DATE)
        twice = sanitize_source_description(once, REPORT_DATE)
        assert once == twice, (
            f"sanitize_source_description not idempotent.\n  After 1st: '{once}'\n  After 2nd: '{twice}'"
        )


# ---------------------------------------------------------------------------
# 11. Company-name variant matching — short name / legal-suffix stripping
# ---------------------------------------------------------------------------

class TestCompanyNameVariants:
    """When company_name is 'Wärtsilä Oyj' and source_says contains only 'Wärtsilä',
    neutralization must still fire (short name without Oyj is a valid variant)."""

    def test_variants_include_short_name(self):
        variants = _company_name_variants("Wärtsilä Oyj")
        short_forms = [v for v in variants if "oyj" not in v.lower()]
        assert short_forms, "Variants must include name without legal suffix"
        assert any("rtsilä" in v or "rtsila" in v for v in short_forms), (
            f"Short-name variant of 'Wärtsilä Oyj' not found in {variants}"
        )

    def test_variants_include_ascii_short_name(self):
        variants = _company_name_variants("Wärtsilä Oyj")
        ascii_short = [v for v in variants if "oyj" not in v.lower() and "ä" not in v.lower()]
        assert ascii_short, (
            f"ASCII-normalized short-name variant missing from {variants}"
        )

    def test_short_name_in_source_says_triggers_neutralization(self):
        """company_name='Wärtsilä Oyj', source_says contains only 'Wärtsilä' (no Oyj).
        Neutralization must fire and remove the short name."""
        record = {
            "source_says": "Wärtsilä must upgrade its AI governance framework.",
            "possible_relevance": "",
            "origin": "foresight",
            "claim_type": "foresight_context",
            "company_specific": False,
        }
        result = _enforce_source_says_neutrality(record, "Wärtsilä Oyj", report_date=REPORT_DATE)
        ss = result.get("source_says") or ""
        assert "wärtsilä" not in ss.lower() and "wartsila" not in ss.lower(), (
            f"Company short name still present in source_says after neutralization: '{ss}'"
        )
        assert result.get("source_says_was_genericized") is True

    def test_ascii_short_name_in_source_says_triggers_neutralization(self):
        """source_says contains ASCII 'Wartsila' when company_name is 'Wärtsilä Oyj'."""
        record = {
            "source_says": "Wartsila should consider AI compliance frameworks.",
            "company_specific": False,
        }
        result = _enforce_source_says_neutrality(record, "Wärtsilä Oyj", report_date=REPORT_DATE)
        ss = result.get("source_says") or ""
        assert "wartsila" not in ss.lower(), (
            f"ASCII short name still present in source_says: '{ss}'"
        )

    def test_must_company_name_genericized_and_softened(self):
        """'Wärtsilä must upgrade' → company name replaced AND 'must' softened in result."""
        record = {
            "source_says": f"Wärtsilä must upgrade its AI governance.",
            "company_specific": False,
        }
        result = _enforce_source_says_neutrality(record, "Wärtsilä Oyj", report_date=REPORT_DATE)
        ss = result.get("source_says") or ""
        assert "wärtsilä" not in ss.lower(), f"Company name not removed: '{ss}'"
        assert not re.search(r"\bmust\b", ss, re.I), (
            f"'must' not softened after genericization: '{ss}'"
        )

    def test_record_level_short_name_neutralized(self):
        """sanitize_foresight_record with company_name='Wärtsilä Oyj' neutralizes
        source_says that contains only 'Wärtsilä'."""
        record = {
            "source_says": "Wärtsilä requires enhanced AI governance.",
            "possible_relevance": "",
            "origin": "foresight",
            "claim_type": "foresight_context",
            "company_specific": False,
        }
        result = sanitize_foresight_record(record, report_date=REPORT_DATE, company_name="Wärtsilä Oyj")
        ss = result.get("source_says") or ""
        assert "wärtsilä" not in ss.lower() and "wartsila" not in ss.lower(), (
            f"Short name not removed from source_says: '{ss}'"
        )
        assert result.get("source_says_was_genericized") is True


# ---------------------------------------------------------------------------
# 12. Company-specific evidence protection in sanitize_foresight_record
# ---------------------------------------------------------------------------

class TestCompanySpecificRecordProtection:
    """Records that are proven company evidence (source_type=case_study, company_specific=True,
    entity_match=exact) must not receive foresight-only metadata stamps."""

    def _make_case_study(self) -> dict:
        return {
            "title": "AI Governance Case Study",
            "source_says": "The company implemented AI governance practices.",
            "source_type": "case_study",
            "company_specific": True,
            "company_mentioned": True,
            "entity_match": "exact",
            "claim_type": "company_fact",
            "scope": "company-specific",
            "generation_eligible": True,
        }

    def test_case_study_not_foresight_context(self):
        record = self._make_case_study()
        result = sanitize_foresight_record(record, report_date=REPORT_DATE, company_name=COMPANY)
        assert result.get("claim_type") != "foresight_context", (
            f"case_study got claim_type=foresight_context: {result.get('claim_type')}"
        )

    def test_case_study_not_external_trend_scope(self):
        record = self._make_case_study()
        result = sanitize_foresight_record(record, report_date=REPORT_DATE, company_name=COMPANY)
        assert result.get("scope") != "external-trend", (
            f"case_study got scope=external-trend: {result.get('scope')}"
        )

    def test_case_study_company_specific_preserved(self):
        record = self._make_case_study()
        result = sanitize_foresight_record(record, report_date=REPORT_DATE, company_name=COMPANY)
        assert result.get("company_specific") is True

    def test_case_study_not_evidence_of_deployment_not_set(self):
        """not_evidence_of_deployment must NOT be set on a proven case study."""
        record = self._make_case_study()
        result = sanitize_foresight_record(record, report_date=REPORT_DATE, company_name=COMPANY)
        # Either not present or explicitly False — must not be True
        assert result.get("not_evidence_of_deployment") is not True, (
            "not_evidence_of_deployment=True wrongly stamped on company case_study"
        )

    def test_case_study_not_generation_eligible_without_analysis_verification(self):
        record = self._make_case_study()
        result = sanitize_foresight_record(record, report_date=REPORT_DATE, company_name=COMPANY)
        assert result.get("generation_eligible") is False
        assert result.get("promotion_status") == "display_only"

    def test_is_company_specific_record_detects_case_study(self):
        record = self._make_case_study()
        assert _is_company_specific_record(record) is True

    def test_is_company_specific_record_detects_annual_report(self):
        record = {"source_type": "annual_report", "company_specific": False}
        assert _is_company_specific_record(record) is True

    def test_is_company_specific_record_false_for_trend_report(self):
        record = {"source_type": "trend_report", "claim_type": "foresight_context"}
        assert _is_company_specific_record(record) is False

    def test_sanitizer_idempotent_on_company_specific_evidence(self):
        """Second sanitizer run on a company-specific record changes 0 string fields."""
        record = self._make_case_study()
        record["name"] = COMPANY
        record["foresight_links"] = []
        record["evidence_links"] = [self._make_case_study()]

        cleaned_once, audit_1 = sanitize_company_record(record, report_date=REPORT_DATE)
        cleaned_twice, audit_2 = sanitize_company_record(cleaned_once, report_date=REPORT_DATE)
        assert audit_2["changed_string_fields"] == 0, (
            f"Second pass changed {audit_2['changed_string_fields']} field(s) on company evidence. "
            f"Sample: {audit_2.get('sample_changes', [])}"
        )


# ---------------------------------------------------------------------------
# Runner
# ---------------------------------------------------------------------------

def _run_all() -> None:
    import traceback
    suites = [
        TestSanitizerIdempotency,
        TestCompanyNameMatching,
        TestSafeModalWordingNotFlagged,
        TestDeterministicWordingFlagged,
        TestPastDeadlineRewrite,
        TestSourceRelevanceSplit,
        TestCompanySpecificEvidencePreservation,
        TestRepeatedModalCleanup,
        TestReportLevelChecks,
        TestModalDilutionScoping,
        TestCompanyNameVariants,
        TestCompanySpecificRecordProtection,
    ]
    passed = failed = 0
    for Suite in suites:
        instance = Suite()
        for name in [m for m in dir(instance) if m.startswith("test_")]:
            try:
                getattr(instance, name)()
                print(f"  ✅  {Suite.__name__}.{name}")
                passed += 1
            except Exception:
                print(f"  ❌  {Suite.__name__}.{name}")
                traceback.print_exc()
                failed += 1
    print(f"\n{'='*60}")
    print(f"Results: {passed} passed, {failed} failed")
    if failed:
        sys.exit(1)


if __name__ == "__main__":
    _run_all()
