"""
Regression tests for Foresight KB foresight-context promotion.

Run with: python -m pytest tests/test_foresight_kb_evidence_promotion.py -v
"""

from __future__ import annotations

import os
import sys

_BACKEND = os.path.join(os.path.dirname(__file__), "..", "backend")
if _BACKEND not in sys.path:
    sys.path.insert(0, _BACKEND)

import foresight_harvester  # noqa: E402
import strategic_foresight  # noqa: E402


def _signal(**extra):
    base = {
        "title": "Platform automation adoption",
        "description": "Independent research says platform automation adoption is rising.",
        "semantic_snippet": (
            "Independent research indicates that platform automation adoption is rising across business "
            "operations as companies modernize workflows, integrate legacy systems, and shift repetitive "
            "processes toward governed digital platforms. The signal is relevant for organizations with "
            "complex operating models, fragmented data flows, and near-term modernization pressure."
        ),
        "source_url": "https://www.oecd.org/platform-automation",
        "url_verified": True,
        "verified": True,
        "source_credibility": "high",
        "source_type": "think_tank",
        "source_count": 2,
        "confidence": "medium",
        "validated": True,
    }
    base.update(extra)
    return base


def test_supported_foresight_signal_becomes_context_eligible(monkeypatch):
    monkeypatch.setattr(
        foresight_harvester,
        "_check_relevance",
        lambda *args, **kwargs: {
            "relevance_score": 86,
            "relevant": True,
            "claim_support_status": "supported",
            "content_verified": True,
            "source_quote": "Platform automation adoption is rising across sectors.",
            "reason": "The source directly supports the trend claim.",
            "verification_model": "gpt-5.4",
        },
    )

    data = foresight_harvester._verify_signal_content({"theme": "Automation", "signals": [_signal()]})
    sig = data["signals"][0]

    assert "analysis_verified" not in sig
    assert "generation_eligible" not in sig
    assert "promotion_status" not in sig
    assert "claim_support_status" not in sig
    assert sig["foresight_signal_status"] == "context_eligible"
    assert sig["source_audit_status"] == "accepted"
    assert sig["trend_support_status"] == "supported"
    assert sig["content_relevance_score"] == 86
    assert sig["source_quote"]
    assert sig["legacy_snippet"]
    assert sig["claim_type"] == "foresight_context"
    assert sig["company_specific"] is False
    assert sig["not_evidence_of_deployment"] is True
    assert sig["requires_company_specific_corroboration"] is True
    assert sig["foresight_context_eligible"] is True
    assert sig["theme_match"] is True
    assert sig["trend_supported"] is True
    assert sig["source_readable"] is True
    assert sig["generation_scope"] == "foresight_synthesis_only"
    assert sig["excluded_from_company_fact_generation"] is True
    assert sig["foresight_evidence_version"] == foresight_harvester.FORESIGHT_KB_EVIDENCE_VERSION


def test_high_credibility_partial_foresight_support_can_be_context_eligible(monkeypatch):
    monkeypatch.setattr(
        foresight_harvester,
        "_check_relevance",
        lambda *args, **kwargs: {
            "relevance_score": 42,
            "relevant": True,
            "claim_support_status": "partial",
            "content_verified": True,
            "source_quote": "The report describes rising automation pressure in industry.",
            "reason": "The source supports the trend direction, but not every KB wording detail.",
            "verification_model": "gpt-5.4",
        },
    )

    data = foresight_harvester._verify_signal_content({"theme": "Automation", "signals": [_signal()]})
    sig = data["signals"][0]

    assert sig["foresight_context_eligible"] is True
    assert "analysis_verified" not in sig
    assert "generation_eligible" not in sig
    assert "claim_support_status" not in sig
    assert sig["foresight_signal_status"] == "context_eligible"
    assert sig["trend_support_status"] == "supported"
    assert sig["foresight_context_support_status"] == "partial"
    assert sig["verification_reason"] == "foresight_context_verified"


def test_live_irrelevant_foresight_signal_stays_not_context_eligible(monkeypatch):
    monkeypatch.setattr(
        foresight_harvester,
        "_check_relevance",
        lambda *args, **kwargs: {
            "relevance_score": 12,
            "relevant": False,
            "claim_support_status": "unsupported",
            "content_verified": True,
            "source_quote": "The source discusses milk and butter prices.",
            "reason": "The source topic does not match the trend claim.",
            "verification_model": "gpt-5.4",
        },
    )

    data = foresight_harvester._verify_signal_content({"theme": "Automation", "signals": [_signal()]})
    sig = data["signals"][0]

    assert "analysis_verified" not in sig
    assert "generation_eligible" not in sig
    assert sig["foresight_context_eligible"] is False
    assert "promotion_status" not in sig
    assert "claim_support_status" not in sig
    assert sig["foresight_signal_status"] == "not_context_eligible"
    assert sig["source_audit_status"] == "trend_not_supported"
    assert sig["trend_support_status"] == "unsupported"
    assert sig["verification_reason"] == "claim_unsupported"


def test_kb_selection_uses_context_eligibility_not_company_generation(monkeypatch):
    class FakeStorage:
        def get_foresight_kb(self, slug):
            return {
                "theme": "Automation",
                "signals": [
                    _signal(title="Old validated signal", analysis_verified=False, generation_eligible=True),
                    _signal(
                        title="Promoted signal",
                        analysis_verified=False,
                        generation_eligible=False,
                        promotion_status="foresight_context_eligible",
                        claim_type="foresight_context",
                        foresight_context_eligible=True,
                        foresight_signal_status=foresight_harvester.ACTIVE_FORESIGHT_STATUS,
                        source_audit_status="accepted_active",
                        source_readable=True,
                    ),
                ],
            }

    monkeypatch.setattr(foresight_harvester, "BlobStorage", FakeStorage)

    signals = foresight_harvester.get_kb_signals_for_themes(["Automation"])

    assert [s["title"] for s in signals] == ["Promoted signal"]
    assert signals[0]["_from_kb"] is True


def test_kb_near_fit_fallback_obeys_context_eligibility(monkeypatch):
    class FakeStorage:
        def get_foresight_kb(self, slug):
            if slug == foresight_harvester._theme_to_slug("Requested"):
                return {"theme": "Requested", "signals": []}
            return {
                "theme": "Fallback",
                "signals": [
                    _signal(title="Fallback old", analysis_verified=False, generation_eligible=True),
                    _signal(
                        title="Fallback promoted",
                        analysis_verified=False,
                        generation_eligible=False,
                        promotion_status="foresight_context_eligible",
                        claim_type="foresight_context",
                        foresight_context_eligible=True,
                        foresight_signal_status=foresight_harvester.ACTIVE_FORESIGHT_STATUS,
                        source_audit_status="accepted_active",
                        source_readable=True,
                    ),
                ],
            }

    monkeypatch.setattr(foresight_harvester, "BlobStorage", FakeStorage)
    monkeypatch.setitem(foresight_harvester.THEME_FALLBACK_MAP, "Requested", ["Fallback"])

    signals = foresight_harvester.get_kb_signals_for_themes(["Requested"])

    assert [s["title"] for s in signals] == ["Fallback promoted"]
    assert signals[0]["_kb_fallback_from"] == "Fallback"


def test_prompt_separates_verified_quote_from_kb_interpretation():
    prompt = foresight_harvester.format_kb_signals_for_prompt([
        _signal(
            analysis_verified=True,
            generation_eligible=True,
            promotion_status="generation_eligible",
            source_quote="The verified source text describes platform automation adoption.",
            source_says="Scanner interpretation about strategic relevance.",
        )
    ])

    assert "Verified source quote: The verified source text" in prompt
    assert "KB interpretation (not a quote): Scanner interpretation" in prompt


def test_scan_time_foresight_gate_accepts_context_contract():
    force = {
        "trend": "AI governance fragmentation",
        "source_url": "https://www.oecd.org/ai-governance",
        "url_verified": True,
        "verified": True,
        "source_readable": True,
        "trend_supported": True,
        "theme_match": True,
        "source_credibility": "high",
        "confidence": "medium",
        "claim_type": "foresight_context",
        "company_specific": False,
        "source_audit_status": "accepted",
        "verified_supporting_sources": [{"url": "https://www.weforum.org/ai", "url_verified": True}],
    }

    assert strategic_foresight._force_generation_eligible_for_context(force) is True
    assert strategic_foresight._force_ai_diagnosis_eligible(force) is True


def test_scan_time_foresight_gate_rejects_old_company_evidence_shortcuts():
    force = {
        "trend": "AI governance fragmentation",
        "source_url": "https://www.oecd.org/ai-governance",
        "url_verified": True,
        "verified": True,
        "source_readable": False,
        "trend_supported": False,
        "theme_match": True,
        "source_credibility": "high",
        "confidence": "medium",
        "claim_type": "foresight_context",
        "company_specific": False,
        "source_audit_status": "trend_not_supported",
        "claim_support_status": "supported",
        "promotion_status": "checked_generation_eligible",
        "verified_supporting_sources": [{"url": "https://www.weforum.org/ai", "url_verified": True}],
    }

    assert strategic_foresight._force_generation_eligible_for_context(force) is False
    assert strategic_foresight._force_ai_diagnosis_eligible(force) is False


def test_strategic_foresight_theme_registry_exposes_refresh_contract():
    registry = strategic_foresight.build_strategic_foresight_theme_registry()

    assert registry["theme_count"] == 30
    assert registry["refresh_rules"]["stored_signal_contract"] == "foresight_context"
    assert registry["refresh_rules"]["company_metadata_allowed"] is False
    assert registry["refresh_rules"]["display_only_storage_policy"] == "replace_with_foresight_signal_status"
    ai_theme = next(t for t in registry["themes"] if t["name"] == "Artificial Intelligence")
    assert ai_theme["refresh_tactics"]
    assert all(t["validation_contract"] == "foresight_context" for t in ai_theme["refresh_tactics"])


def test_cleanup_foresight_kb_repairs_display_only_metadata_without_deleting(monkeypatch):
    saved = {}
    audit_saved = {}

    class FakeStorage:
        def get_all_foresight_kb(self):
            return {
                "business": {
                    "theme": "Business",
                    "signals": [
                        _signal(
                            title="Retained macro signal",
                            promotion_status="generation_eligible",
                            source_registry_status="generation_eligible",
                            analysis_verified=True,
                            generation_eligible=True,
                            company_name="Example Oy",
                            source_quote="Business model transformation pressure is rising.",
                        ),
                        _signal(
                            title="Display-only signal",
                            promotion_status="display_only",
                            foresight_context_eligible=False,
                            tags=["6-12mo", "go_to_market", "stable", "cred: high"],
                        ),
                    ],
                }
            }

        def save_foresight_kb(self, slug, data):
            saved[slug] = data
            return "blob://saved"

        def save_foresight_kb_audit(self, slug, data):
            audit_saved[slug] = data
            return "blob://audit"

    monkeypatch.setattr(foresight_harvester, "BlobStorage", FakeStorage)
    monkeypatch.setattr(strategic_foresight, "_verify_foresight_url", lambda *args, **kwargs: True)

    result = foresight_harvester.cleanup_foresight_kb()

    assert result["signals_before"] == 2
    assert result["signals_after"] == 2
    assert result["display_only_removed"] == 0
    assert result["metadata_repaired"] == 2
    assert result["rejected_audit"] == 0
    retained = next(s for s in saved["business"]["signals"] if s["title"] == "Retained macro signal")
    assert retained["title"] == "Retained macro signal"
    assert "promotion_status" not in retained
    assert "source_registry_status" not in retained
    assert retained["claim_type"] == "foresight_context"
    assert retained["company_specific"] is False
    assert retained["foresight_context_eligible"] is True
    assert retained["foresight_signal_status"] == foresight_harvester.ACTIVE_FORESIGHT_STATUS
    assert retained["source_audit_status"] == "accepted_active"
    assert "generation_eligible" not in retained
    assert "analysis_verified" not in retained
    assert "company_name" not in retained
    repaired_display_only = next(s for s in saved["business"]["signals"] if s["title"] == "Display-only signal")
    assert "promotion_status" not in repaired_display_only
    assert repaired_display_only["tags"] == ["6-12mo", "go_to_market", "stable", "cred: high"]
    assert repaired_display_only["foresight_context_eligible"] is True
    assert repaired_display_only["foresight_signal_status"] == foresight_harvester.ACTIVE_FORESIGHT_STATUS
    assert "business" not in audit_saved


def test_repair_dead_signal_moves_to_audit_without_overwriting_active_kb(monkeypatch):
    saved = {}
    audit_saved = {}

    class FakeStorage:
        def get_all_foresight_kb(self):
            return {
                "automation": {
                    "theme": "Automation",
                    "signals": [
                        _signal(
                            title="Dead macro signal",
                            source_url="https://example.invalid/dead",
                            url_verified=True,
                        )
                    ],
                }
            }

        def save_foresight_kb(self, slug, data):
            saved[slug] = data
            return "blob://saved"

        def save_foresight_kb_audit(self, slug, data):
            audit_saved[slug] = data
            return "blob://audit"

    monkeypatch.setattr(foresight_harvester, "BlobStorage", FakeStorage)
    monkeypatch.setattr(strategic_foresight, "_verify_foresight_url", lambda *args, **kwargs: False)

    result = foresight_harvester.cleanup_foresight_kb()

    assert result["signals_before"] == 1
    assert result["signals_after"] == 0
    assert result["themes_write_skipped"] == 1
    assert "automation" not in saved
    repaired = audit_saved["automation"]["candidates"][0]
    assert repaired["source_audit_status"] == "source_dead"
    assert repaired["foresight_context_eligible"] is False
    assert "promotion_status" not in repaired


def test_harvest_preserves_existing_active_when_new_batch_rejected(monkeypatch):
    saved = {}
    audit_saved = {}

    class FakeStorage:
        def save_foresight_registry(self, registry):
            return "blob://registry"

        def get_foresight_kb(self, slug):
            return {
                "theme": "Business",
                "signals": [
                    _signal(
                        title="Existing active signal",
                        source_url="https://www.oecd.org/existing-active",
                        foresight_context_eligible=True,
                        foresight_signal_status=foresight_harvester.ACTIVE_FORESIGHT_STATUS,
                        source_audit_status="accepted_active",
                        theme_match=True,
                        trend_supported=True,
                    )
                ],
            }

        def save_foresight_kb(self, slug, data):
            saved[slug] = data
            return "blob://saved"

        def save_foresight_kb_audit(self, slug, data):
            audit_saved[slug] = data
            return "blob://audit"

    monkeypatch.setattr(foresight_harvester, "BlobStorage", FakeStorage)
    monkeypatch.setattr(foresight_harvester, "_verify_foresight_url", lambda *args, **kwargs: True)
    monkeypatch.setattr(
        foresight_harvester,
        "_harvest_single_theme",
        lambda theme, queries: {
            "theme": theme,
            "signals": [
                _signal(
                    title="Unsupported new signal",
                    source_url="https://www.oecd.org/unsupported-new",
                    trend_supported=False,
                )
            ],
            "model": "test",
        },
    )
    monkeypatch.setattr(
        foresight_harvester,
        "_check_relevance",
        lambda *args, **kwargs: {
            "relevance_score": 10,
            "relevant": False,
            "claim_support_status": "unsupported",
            "content_verified": True,
            "source_quote": "Unrelated content.",
            "reason": "Does not support trend.",
            "verification_model": "test",
        },
    )
    monkeypatch.setattr(foresight_harvester.time, "sleep", lambda *_: None)

    result = foresight_harvester.harvest_all_themes(["Business"])

    assert result["failed"] == 0
    assert saved["business"]["signals"]
    titles = [s["title"] for s in saved["business"]["signals"]]
    assert titles == ["Existing active signal"]
    assert saved["business"]["merge_stats"]["new_active"] == 0
    assert audit_saved["business"]["candidates"][0]["title"] == "Unsupported new signal"
