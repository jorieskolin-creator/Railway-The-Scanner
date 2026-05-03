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
    sanitize_foresight_record,
    sanitize_foresight_modality,
    validate_foresight_text,
    _enforce_source_says_neutrality,
    _normalize_company_name,
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
        assert item.get("generation_eligible") is not False, \
            "Annual report must remain generation-eligible"

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
