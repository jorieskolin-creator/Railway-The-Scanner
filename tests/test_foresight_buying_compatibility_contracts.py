"""Foresight KB-first, Observatory, Buying Insight, General Summary & Source Ledger contracts.

Card 2B proves deterministic cross-feature compatibility and trust boundaries across:
  1. KB-first (`kb_first=True`) and registry-fallback (`kb_first=False`,
     `registry_fallback=True`) `generate_foresight()` outputs flowing into
     `extract_foresight_links()`, `foresight_observatory._filter_company_foresight()`,
     and `buying_potential._build_input_payload()` / `_build_account_signal_map()`,
     while preserving legacy `theme` / `dimension` / `description` fields intact.
  2. Failed new Foresight synthesis preserving only eligible prior `foresight_links`
     via `is_foresight_context_eligible_source()` and context-only
     `_fallback_foresight_from_links()`.
  3. Strict non-promotion of Foresight links into `general_summary._build_input_payload()`
     company facts (`evidence_links`, `attributed_company_context`, `key_findings`) or
     `company_source_ledger` Scanner seeds (`usable_as_scanner_seed`,
     `scanner_seed_links`, `ledger_scanner_seed_links()`).

All tests run hermetically under `tests/conftest.py` with socket/DNS access blocked
and zero live provider or network calls.
"""

from __future__ import annotations

import ast
import copy
import json
import os
from pathlib import Path
import sys


_ROOT = Path(__file__).resolve().parents[1]
_BACKEND = _ROOT / "backend"
if str(_BACKEND) not in sys.path:
    sys.path.insert(0, str(_BACKEND))

import buying_potential  # noqa: E402
from company_source_ledger import (  # noqa: E402
    build_company_source_ledger,
    ledger_scanner_seed_links,
)
from database_sanitizer import classify_source_registry_items  # noqa: E402
import evidence_contracts as contracts  # noqa: E402
from foresight_guardrails import sanitize_foresight_collection  # noqa: E402
import foresight_harvester  # noqa: E402
import foresight_observatory as observatory  # noqa: E402
import general_summary  # noqa: E402
from source_quality import (  # noqa: E402
    is_analysis_premise_source,
    is_foresight_context_eligible_source,
    is_generation_eligible_source,
)
import strategic_foresight  # noqa: E402


_MAIN_PATH = _BACKEND / "main.py"


def _load_main_foresight_fallback_helpers() -> dict:
    """Load `_safe_dict_list` and `_fallback_foresight_from_links` from `backend/main.py` AST.

    Avoids importing `backend/main.py` at module top level (which calls `load_dotenv()`
    and instantiates `BlobStorage()`).
    """
    tree = ast.parse(_MAIN_PATH.read_text(encoding="utf-8"))
    wanted = {"_safe_dict_list", "_fallback_foresight_from_links"}
    nodes = [
        node
        for node in tree.body
        if isinstance(node, ast.FunctionDef) and node.name in wanted
    ]
    assert len(nodes) == 2
    namespace = {
        "is_foresight_context_eligible_source": is_foresight_context_eligible_source,
    }
    exec(compile(ast.Module(body=nodes, type_ignores=[]), str(_MAIN_PATH), "exec"), namespace, namespace)
    return namespace


def _service_kb_fixture() -> dict:
    return {
        "owner": "Vivicta",
        "services": [
            {
                "name": "Industrial Data & AI Readiness Architecture",
                "category": "AI & Data Platform",
                "buyer_personas": ["Chief Digital Officer", "VP Engineering"],
                "pain_triggers": ["telemetry auditability", "connected fleet governance"],
                "evidence_patterns": ["API telemetry", "platform governance"],
                "proof_points": ["Delivered Nordic industrial telemetry governance blueprints."],
                "safe_wording": ["Validate whether telemetry auditability affects fleet roadmap."],
                "do_not_claim": ["Never assert customer AI deployment failure as fact."],
                "disqualifiers": ["Telemetry governance already certified across all regions."],
            }
        ],
    }


def _verified_company_evidence_link() -> dict:
    return {
        "title": "Konecranes expands TRUCONNECT connected crane services",
        "url": "https://www.konecranes.com/press/truconnect-connected-services-2026",
        "source": "konecranes.com",
        "snippet": "Konecranes expanded remote telemetry and API-enabled predictive maintenance across industrial crane fleets.",
        "source_quote": (
            "Konecranes expanded remote telemetry, API integration, and predictive "
            "maintenance services across connected industrial crane fleets in 2026."
        ),
        "verified": True,
        "url_verified": True,
        "content_verified": True,
        "analysis_verified": True,
        "entity_verified": True,
        "entity_scope": "canonical_group",
        "entity_match": "exact",
        "entity_match_status": "exact",
        "content_entity_match_status": "exact",
        "claim_support_status": "supported",
        "promotion_status": "generation_eligible",
        "generation_eligible": True,
        "company_specific": True,
        "claim_type": "company_fact",
        "scope": "company-specific",
        "source_credibility": "high",
        "source_type": "official_press",
        "claim_tier": "A",
        "is_recent": True,
        "last_checked": "2026-11-05T10:00:00+00:00",
    }


def _kb_signal_fixture() -> dict:
    return {
        "title": "Industrial autonomous crane telemetry regulation",
        "description": (
            "European industrial safety authorities are tightening auditability and "
            "sensor-lineage rules for automated lifting and material-handling telemetry."
        ),
        "source_url": "https://www.oecd.org/ai/industrial-telemetry-2026",
        "source_type": "government",
        "source_credibility": "high",
        "url_verified": True,
        "source_readable": True,
        "trend_supported": True,
        "theme_match": True,
        "foresight_context_eligible": True,
        "foresight_signal_status": "context_eligible",
        "source_audit_status": "accepted",
        "trend_support_status": "supported",
        "foresight_evidence_version": "2026-09-v2",
        "fp_theme": "Artificial Intelligence",
        "time_horizon": "6-12mo",
        "lifecycle_stage": "go_to_market",
        "trend_momentum": "accelerating",
        "action_timing": "act_now",
        "supporting_sources": [
            {
                "url": "https://www.weforum.org/publications/industrial-ai-telemetry-2026",
                "source_type": "think_tank",
                "title": "WEF Industrial AI Telemetry Governance",
            }
        ],
    }


def _kb_signals_fixture() -> list[dict]:
    primary = _kb_signal_fixture()
    second = {
        **copy.deepcopy(primary),
        "title": "Connected industrial equipment data-sharing mandates",
        "description": (
            "EU industrial data-access regulations expand standardized telemetry "
            "portability obligations across connected machinery fleets."
        ),
        "source_url": "https://ec.europa.eu/digital/connected-industrial-data-2026",
        "fp_theme": "Data & Digitalisation",
        "supporting_sources": [],
    }
    third = {
        **copy.deepcopy(primary),
        "title": "Modular predictive maintenance safety standards",
        "description": (
            "International standards bodies are codifying predictive-maintenance "
            "interface verification for heavy industrial equipment."
        ),
        "source_url": "https://www.iso.org/standard/predictive-maintenance-2026",
        "fp_theme": "Industry & Manufacturing",
        "supporting_sources": [],
    }
    return [primary, second, third]


def _kb_synthesis_model_json() -> str:
    return json.dumps({
        "dimension_forces": [
            {
                "trend": "Industrial autonomous crane telemetry regulation",
                "type": "Probable/Emerging",
                "timeframe": "6-12mo",
                "kb_dimension": "AI Readiness",
                "fp_theme": "Artificial Intelligence",
                "impact_on_company": (
                    "Konecranes Oyj could face stricter sensor-lineage expectations across "
                    "connected crane maintenance tenders if machinery telemetry rules tighten."
                ),
                "proposal_angle": (
                    "Konecranes Oyj should evaluate whether TRUCONNECT telemetry pipelines "
                    "capture auditable model and sensor lineage."
                ),
                "source_url": "https://www.oecd.org/ai/industrial-telemetry-2026",
                "supporting_sources": [
                    {
                        "url": "https://www.weforum.org/publications/industrial-ai-telemetry-2026",
                        "source_type": "think_tank",
                        "title": "WEF Industrial AI Telemetry Governance",
                    }
                ],
                "source_type": "government",
                "confidence": "high",
                "lifecycle_stage": "go_to_market",
                "trend_momentum": "accelerating",
                "action_timing": "act_now",
                "osint_validation": "Cross-referenced OECD industrial AI guidance with WEF manufacturing report.",
            }
        ],
        "cross_impact_chain": "Telemetry auditability -> AI Readiness -> Operating Model -> Service Portfolio",
        "scenarios": {
            "acceleration": "Standardizing telemetry lineage early supports regulated European service tenders.",
            "status_quo": "Fragmented plant-floor telemetry slows tender qualification as audit rules mature.",
            "disruption": "Mandatory third-party telemetry certification reshapes lifecycle service contracts.",
        },
        "foresight_headline": "Auditable machinery telemetry is emerging as an industrial service differentiator.",
    })


# ═══════════════════════════════════════════════════════════════════════════════
# 1. KB-FIRST & REGISTRY-FALLBACK FLOW INTO LINKS, OBSERVATORY & BUYING INSIGHT
# ═══════════════════════════════════════════════════════════════════════════════

def test_kb_first_generate_foresight_flows_into_links_observatory_and_buying_insight(monkeypatch):
    kb_signals = _kb_signals_fixture()
    kb_sig = kb_signals[0]
    monkeypatch.setattr(
        foresight_harvester,
        "get_kb_signals_for_themes",
        lambda _themes: copy.deepcopy(kb_signals),
    )
    monkeypatch.setattr(strategic_foresight, "_verify_foresight_url", lambda _url, timeout=6: True)
    monkeypatch.setattr(
        strategic_foresight,
        "call_json_model_chain",
        lambda *_args, **_kwargs: (_kb_synthesis_model_json(), "gpt-6.1-sol"),
    )
    monkeypatch.setattr(buying_potential, "_load_modernization_tactics", lambda: [])

    foresight_data = strategic_foresight.generate_foresight(
        company_name="Konecranes Oyj",
        domain="Industrial & Manufacturing",
        archetype="Thriving",
        reality_score=64,
        financial_index=72,
        top_findings=[],
    )

    assert foresight_data is not None
    assert foresight_data["kb_first"] is True
    raw_force = foresight_data["dimension_forces"][0]
    # Confirm KB-first force carries KB-first field names (and lacks legacy `theme`/`dimension`/`description`).
    assert raw_force["trend"] == "Industrial autonomous crane telemetry regulation"
    assert raw_force["fp_theme"] == "Artificial Intelligence"
    assert raw_force["kb_dimension"] == "AI Readiness"
    assert "Konecranes Oyj" in raw_force["impact_on_company"]
    assert raw_force["_kb_description"] == kb_sig["description"]
    assert "theme" not in raw_force
    assert "dimension" not in raw_force
    assert "description" not in raw_force

    # Flow into `extract_foresight_links()`:
    links = strategic_foresight.extract_foresight_links(
        copy.deepcopy(foresight_data),
        company_name="Konecranes Oyj",
        domain="Industrial & Manufacturing",
    )
    assert len(links) == 2
    primary_link = next(item for item in links if item["is_primary"] is True)
    assert primary_link["evidence_contract"] == contracts.COMPANY_FORESIGHT_INTERPRETATION
    assert primary_link["origin"] == "foresight"
    assert primary_link["claim_type"] == "foresight_context"
    assert primary_link["company_specific"] is False
    assert primary_link["not_evidence_of_deployment"] is True
    assert primary_link["excluded_from_company_fact_generation"] is True
    # Company-neutral `_kb_description` populates `source_says` / `snippet`, never `impact_on_company`.
    assert primary_link["source_says"] == kb_sig["description"]
    assert "Konecranes" not in primary_link["source_says"]
    assert "Konecranes Oyj" in primary_link["possible_relevance"]
    assert primary_link["source_readable"] is True
    assert is_foresight_context_eligible_source(primary_link) is True
    assert observatory._is_eligible_foresight_context(primary_link) == (True, "eligible")

    # Flow into Observatory `_filter_company_foresight()`:
    company_data = {
        "companyName": "Konecranes Oyj",
        "companyUrl": "https://www.konecranes.com/",
        "domain": "Industrial & Manufacturing",
        "archetype": "Thriving",
        "intent_score": 78,
        "reality_score": 64,
        "confidence_score": 80,
        "financial_index": 72,
        "summary_text": "Telemetry services are established while cross-plant AI lineage needs discovery.",
        "strategic_synthesis": "Standardize connected-fleet telemetry governance ahead of regulatory audits.",
        "activation_pairs": [
            {
                "theme_type": "foresight_driven",
                "sales_use": "foresight_driven",
                "theme": "Telemetry lineage readiness",
                "why": "Upcoming machinery telemetry auditability rules could affect connected crane tenders.",
                "what": "Validate sensor and model lineage architecture across TRUCONNECT units.",
                "parent_foresight_ids": ["FORESIGHT-01"],
            }
        ],
        "evidence_links": [_verified_company_evidence_link()],
        "strategic_foresight": foresight_data,
        "foresight_links": links,
    }
    filtered_companies, obs_stats = observatory._filter_company_foresight([company_data])
    assert obs_stats["eligible_records"] == 2
    assert obs_stats["excluded_records"] == 0
    assert len(filtered_companies[0]["foresight_links"]) == 2

    # Flow into Buying Insight `_build_input_payload()` and `_build_account_signal_map()`:
    bp_payload = buying_potential._build_input_payload(company_data, _service_kb_fixture())
    assert len(bp_payload["foresight_forces"]) == 1
    mapped_force = bp_payload["foresight_forces"][0]
    assert mapped_force["id"] == "FORESIGHT-01"
    assert mapped_force["theme"] == "Industrial autonomous crane telemetry regulation"
    assert mapped_force["dimension"] == "AI Readiness"
    assert mapped_force["description"] == kb_sig["description"]
    assert mapped_force["domain_relevance"] == raw_force["impact_on_company"]
    assert mapped_force["source_says"] == kb_sig["description"]
    assert "Konecranes" not in mapped_force["source_says"]
    assert mapped_force["horizon"] == "6-12mo"
    assert mapped_force["credibility"] == "high"
    assert mapped_force["company_specific"] is False
    assert mapped_force["requires_company_specific_corroboration"] is True
    assert mapped_force["not_evidence_of_deployment"] is True

    external_signals = [
        sig for sig in bp_payload["account_signal_map"]
        if sig.get("signal_lane") == "external_pressure"
    ]
    assert len(external_signals) == 1
    ext_sig = external_signals[0]
    assert ext_sig["source"] == "foresight_force"
    assert ext_sig["gate"] == "allowed_as_hypothesis"
    assert kb_sig["description"] in ext_sig["account_movement"]
    assert ext_sig["company_specific"] is False
    assert ext_sig["requires_company_specific_corroboration"] is True
    assert ext_sig["not_evidence_of_deployment"] is True
    assert ext_sig["source_says"] == kb_sig["description"]
    assert ext_sig["parent_foresight_ids"] == ["FORESIGHT-01"]

    # Foresight links must stay out of `verified_evidence` (`account_fact` lane):
    verified_urls = {item["url"] for item in bp_payload["verified_evidence"]}
    blocked_urls = {item["url"] for item in bp_payload["blocked_or_display_only_sources"]}
    assert verified_urls == {"https://www.konecranes.com/press/truconnect-connected-services-2026"}
    assert primary_link["url"] in blocked_urls
    assert all(
        sig.get("source") != "foresight_force"
        for sig in bp_payload["account_signal_map"]
        if sig.get("signal_lane") == "account_fact"
    )


def test_registry_fallback_and_legacy_foresight_shapes_preserve_non_destructive_aliases(monkeypatch):
    # Empty KB -> `generate_foresight` uses curated registry fallback (`kb_first=False`, `registry_fallback=True`).
    monkeypatch.setattr(foresight_harvester, "get_kb_signals_for_themes", lambda _themes: [])
    monkeypatch.setattr(strategic_foresight, "_verify_foresight_url", lambda _url, timeout=4: True)
    monkeypatch.setattr(
        strategic_foresight,
        "call_openai_text",
        lambda *_args, **_kwargs: _kb_synthesis_model_json(),
    )
    monkeypatch.setattr(buying_potential, "_load_modernization_tactics", lambda: [])

    registry_data = strategic_foresight.generate_foresight(
        company_name="Konecranes Oyj",
        domain="Industrial & Manufacturing",
        archetype="Thriving",
        reality_score=64,
        financial_index=72,
        top_findings=[],
        allow_registry_fallback=True,
        fallback_budget_sec=90,
    )
    assert registry_data is not None
    assert registry_data["kb_first"] is False
    assert registry_data["registry_fallback"] is True
    reg_force = registry_data["dimension_forces"][0]
    assert "_kb_description" not in reg_force

    # Combine a legacy force (explicit `theme`/`dimension`/`description`/`domain_relevance`/`source_says`
    # plus conflicting KB-first alias keys) with the registry-fallback force to verify non-destructive precedence.
    legacy_force = {
        "id": "FORESIGHT-LEGACY-01",
        "theme": "Legacy Explicit Theme",
        "dimension": "Operating Model",
        "description": "Legacy explicit force description must win without modification.",
        "domain_relevance": "Legacy explicit domain relevance.",
        "source_says": "Legacy explicit neutral passage.",
        "horizon": "12-24mo",
        "confidence": 68,
        "credibility": "high",
        "company_specific": False,
        "requires_company_specific_corroboration": True,
        "excluded_from_company_fact_generation": True,
        "not_evidence_of_deployment": True,
        # Conflicting KB-first alias keys must NOT overwrite legacy keys:
        "trend": "Must Not Override Legacy Theme",
        "fp_theme": "Must Not Override FP Theme",
        "kb_dimension": "AI Readiness",
        "_kb_description": "Must Not Override Legacy Description",
        "impact_on_company": "Must Not Override Legacy Relevance",
    }

    company_data = {
        "companyName": "Konecranes Oyj",
        "companyUrl": "https://www.konecranes.com/",
        "domain": "Industrial & Manufacturing",
        "strategic_foresight": {
            "kb_first": False,
            "dimension_forces": [legacy_force, reg_force],
        },
        "evidence_links": [_verified_company_evidence_link()],
    }

    bp_payload = buying_potential._build_input_payload(company_data, _service_kb_fixture())
    assert len(bp_payload["foresight_forces"]) == 2

    mapped_legacy, mapped_registry = bp_payload["foresight_forces"]
    assert mapped_legacy["theme"] == "Legacy Explicit Theme"
    assert mapped_legacy["dimension"] == "Operating Model"
    assert mapped_legacy["description"] == "Legacy explicit force description must win without modification."
    assert mapped_legacy["domain_relevance"] == "Legacy explicit domain relevance."
    assert mapped_legacy["source_says"] == "Legacy explicit neutral passage."

    assert mapped_registry["theme"] == "Industrial autonomous crane telemetry regulation"
    assert mapped_registry["dimension"] == "AI Readiness"
    assert mapped_registry["description"] == reg_force["impact_on_company"]
    assert mapped_registry["domain_relevance"] == reg_force["impact_on_company"]
    # Registry-fallback has no `_kb_description`; `source_says` stays empty rather than
    # leaking company-specific `impact_on_company` into a neutral quote slot.
    assert mapped_registry["source_says"] == ""

    # Direct `_build_account_signal_map()` call with raw un-normalized KB-first & legacy forces:
    direct_signals = buying_potential._build_account_signal_map(
        {"foresight_forces": [legacy_force, reg_force]},
        [],
    )
    ext_movements = [
        sig["account_movement"]
        for sig in direct_signals
        if sig.get("signal_lane") == "external_pressure"
    ]
    assert len(ext_movements) == 2
    assert ext_movements[0] == "Legacy explicit force description must win without modification."
    assert ext_movements[1] == reg_force["impact_on_company"]


def test_registry_fallback_extract_foresight_links_fails_closed_until_claim_validated(monkeypatch):
    monkeypatch.setattr(foresight_harvester, "get_kb_signals_for_themes", lambda _themes: [])
    monkeypatch.setattr(strategic_foresight, "_verify_foresight_url", lambda _url, timeout=4: True)
    monkeypatch.setattr(
        strategic_foresight,
        "call_openai_text",
        lambda *_args, **_kwargs: _kb_synthesis_model_json(),
    )
    monkeypatch.setattr(general_summary, "detect_contradictions", lambda _links, _company: [])

    registry_data = strategic_foresight.generate_foresight(
        company_name="Konecranes Oyj",
        domain="Industrial & Manufacturing",
        archetype="Thriving",
        reality_score=64,
        financial_index=72,
        allow_registry_fallback=True,
        fallback_budget_sec=90,
    )
    assert registry_data is not None
    assert registry_data["kb_first"] is False
    assert registry_data["registry_fallback"] is True

    # 1. Before `validate_foresight_claims` establishes `source_readable` + `trend_supported`,
    # `extract_foresight_links` fails closed (`foresight_context_eligible=False`).
    unvalidated_links = strategic_foresight.extract_foresight_links(
        copy.deepcopy(registry_data),
        company_name="Konecranes Oyj",
        domain="Industrial & Manufacturing",
    )
    unvalidated_primary = next(item for item in unvalidated_links if item["is_primary"] is True)
    assert unvalidated_primary["validated"] is False
    assert unvalidated_primary["foresight_context_eligible"] is False
    assert is_foresight_context_eligible_source(unvalidated_primary) is False
    assert observatory._is_eligible_foresight_context(unvalidated_primary) == (
        False,
        "not_context_eligible",
    )

    # 2. Once `validate_foresight_claims` audits readability + macro trend support on the
    # registry-fallback force, `extract_foresight_links` preserves those verdicts for
    # Observatory & prior-link retention while still blocking company-fact / seed promotion.
    claim_validated_data = copy.deepcopy(registry_data)
    claim_validated_data["dimension_forces"][0].update({
        "source_readable": True,
        "trend_supported": True,
        "theme_match": True,
        "foresight_context_eligible": True,
        "foresight_signal_status": "context_eligible",
        "source_audit_status": "accepted",
    })
    validated_links = strategic_foresight.extract_foresight_links(
        claim_validated_data,
        company_name="Konecranes Oyj",
        domain="Industrial & Manufacturing",
    )
    validated_primary = next(item for item in validated_links if item["is_primary"] is True)
    assert validated_primary["validated"] is True
    assert validated_primary["source_readable"] is True
    assert is_foresight_context_eligible_source(validated_primary) is True
    assert observatory._is_eligible_foresight_context(validated_primary) == (True, "eligible")
    assert is_generation_eligible_source(validated_primary) is False

    gs_payload = general_summary._build_input_payload(
        {
            "companyName": "Konecranes Oyj",
            "evidence_links": [_verified_company_evidence_link(), validated_primary],
            "foresight_links": validated_links,
        },
        "Konecranes Oyj",
    )
    assert len(gs_payload["evidence_links"]) == 1
    assert gs_payload["attributed_company_context"] == []

    ledger, _ = build_company_source_ledger({
        "companyName": "Konecranes Oyj",
        "companyUrl": "https://www.konecranes.com/",
        "foresight_links": validated_links,
    })
    assert ledger["stats"]["scanner_seed_eligible"] == 0
    assert ledger_scanner_seed_links(ledger) == []


# ═══════════════════════════════════════════════════════════════════════════════
# 2. FAILED NEW FORESIGHT SYNTHESIS PRESERVING ELIGIBLE PRIOR `foresight_links`
# ═══════════════════════════════════════════════════════════════════════════════

def test_failed_new_foresight_synthesis_preserves_only_eligible_prior_links_via_context_fallback(monkeypatch):
    helpers = _load_main_foresight_fallback_helpers()
    _safe_dict_list = helpers["_safe_dict_list"]
    _fallback_foresight_from_links = helpers["_fallback_foresight_from_links"]

    # Step 1: Produce authentic Scan-1 KB-first `foresight_links`
    kb_signals = _kb_signals_fixture()
    kb_sig = kb_signals[0]
    monkeypatch.setattr(
        foresight_harvester,
        "get_kb_signals_for_themes",
        lambda _themes: copy.deepcopy(kb_signals),
    )
    monkeypatch.setattr(strategic_foresight, "_verify_foresight_url", lambda _url, timeout=6: True)
    monkeypatch.setattr(
        strategic_foresight,
        "call_json_model_chain",
        lambda *_args, **_kwargs: (_kb_synthesis_model_json(), "gpt-6.1-sol"),
    )
    monkeypatch.setattr(buying_potential, "_load_modernization_tactics", lambda: [])

    scan1_foresight = strategic_foresight.generate_foresight(
        company_name="Konecranes Oyj",
        domain="Industrial & Manufacturing",
        archetype="Thriving",
        reality_score=64,
        financial_index=72,
    )
    scan1_links = strategic_foresight.extract_foresight_links(
        scan1_foresight,
        company_name="Konecranes Oyj",
        domain="Industrial & Manufacturing",
    )
    eligible_primary = next(item for item in scan1_links if item["is_primary"] is True)
    assert is_foresight_context_eligible_source(eligible_primary) is True

    # Mix eligible Scan-1 link with dead, unreadable, non-context-eligible, and company-specific links.
    dead_prior = dict(
        eligible_primary,
        url="https://www.oecd.org/ai/retired-dead-link",
        source_audit_status="source_dead",
    )
    unreadable_prior = dict(
        eligible_primary,
        url="https://www.oecd.org/ai/paywalled-unreadable-link",
        source_readable=False,
    )
    display_only_prior = dict(
        eligible_primary,
        url="https://www.oecd.org/ai/display-only-link",
        foresight_context_eligible=False,
        foresight_signal_status="not_context_eligible",
    )
    company_fact_misplaced = dict(
        eligible_primary,
        url="https://www.konecranes.com/press/company-specific-release",
        claim_type="company_fact",
        company_specific=True,
    )
    existing_data = {
        "foresight_links": [
            eligible_primary,
            copy.deepcopy(eligible_primary),  # duplicate URL must dedupe
            dead_prior,
            unreadable_prior,
            display_only_prior,
            company_fact_misplaced,
        ]
    }

    # Step 2: Scan 2 fails new Foresight synthesis (`generate_foresight` -> None when KB empty and fallback disabled)
    monkeypatch.setattr(foresight_harvester, "get_kb_signals_for_themes", lambda _themes: [])
    scan2_foresight = strategic_foresight.generate_foresight(
        company_name="Konecranes Oyj",
        domain="Industrial & Manufacturing",
        archetype="Thriving",
        reality_score=64,
        financial_index=72,
        allow_registry_fallback=False,
        fallback_budget_sec=0,
    )
    assert scan2_foresight is None

    # Execute exact prior-link merge + sanitation + context-only fallback sequence:
    foresight_links: list[dict] = []
    existing_fl = _safe_dict_list(existing_data.get("foresight_links", []), "existing_data.foresight_links")
    new_urls = {l.get("url") for l in foresight_links if isinstance(l, dict) and l.get("url")}
    for efl in existing_fl:
        if (
            efl.get("url")
            and efl["url"] not in new_urls
            and is_foresight_context_eligible_source(efl)
        ):
            foresight_links.append(efl)
            new_urls.add(efl["url"])

    foresight_links = sanitize_foresight_collection(
        foresight_links,
        domain="Industrial & Manufacturing",
        company_name="Konecranes Oyj",
    )
    fallback_data = _fallback_foresight_from_links(
        "Konecranes Oyj",
        foresight_links,
        domain="Industrial & Manufacturing",
    )

    assert [item["url"] for item in foresight_links] == [eligible_primary["url"]]
    assert fallback_data is not None
    assert fallback_data["kb_first"] is False
    assert fallback_data["fallback_from_links"] is True
    assert fallback_data["insufficient_evidence"] is True
    assert fallback_data["quality_label"] == "foresight_context_retained_no_current_synthesis"
    assert len(fallback_data["dimension_forces"]) == 1

    retained_force = fallback_data["dimension_forces"][0]
    assert retained_force["source_url"] == eligible_primary["url"]
    assert retained_force["title"] == "Industrial autonomous crane telemetry regulation"
    assert retained_force["source_quote"] == kb_sig["description"]
    assert retained_force["claim_type"] == "foresight_context"
    assert retained_force["company_specific"] is False
    assert retained_force["foresight_context_eligible"] is True
    assert retained_force["context_only"] is True

    # Retained fallback record stays compatible with both Observatory and Buying Insight:
    fallback_company = {
        "companyName": "Konecranes Oyj",
        "companyUrl": "https://www.konecranes.com/",
        "domain": "Industrial & Manufacturing",
        "evidence_links": [_verified_company_evidence_link()],
        "strategic_foresight": fallback_data,
        "foresight_links": foresight_links,
    }
    obs_filtered, obs_stats = observatory._filter_company_foresight([fallback_company])
    assert obs_stats["eligible_records"] == 1
    assert len(obs_filtered[0]["foresight_links"]) == 1

    bp_payload = buying_potential._build_input_payload(fallback_company, _service_kb_fixture())
    assert len(bp_payload["foresight_forces"]) == 1
    assert bp_payload["foresight_forces"][0]["description"] == kb_sig["description"]
    assert bp_payload["foresight_forces"][0]["source_says"] == kb_sig["description"]
    assert bp_payload["foresight_forces"][0]["company_specific"] is False
    ext_lanes = bp_payload["account_signal_lanes"]["external_pressure"]
    assert len(ext_lanes) == 1
    assert ext_lanes[0]["gate"] == "allowed_as_hypothesis"
    assert ext_lanes[0]["company_specific"] is False

    # Fail-closed check: if only ineligible links exist, `_fallback_foresight_from_links` returns None.
    assert _fallback_foresight_from_links(
        "Konecranes Oyj",
        [dead_prior, unreadable_prior, display_only_prior, company_fact_misplaced],
        domain="Industrial & Manufacturing",
    ) is None


# ═══════════════════════════════════════════════════════════════════════════════
# 3. STRICT NON-PROMOTION INTO `general_summary` FACTS OR `company_source_ledger` SEEDS
# ═══════════════════════════════════════════════════════════════════════════════

def test_foresight_links_never_promote_into_general_summary_facts_or_ledger_scanner_seeds(monkeypatch):
    kb_signals = _kb_signals_fixture()
    monkeypatch.setattr(
        foresight_harvester,
        "get_kb_signals_for_themes",
        lambda _themes: copy.deepcopy(kb_signals),
    )
    monkeypatch.setattr(strategic_foresight, "_verify_foresight_url", lambda _url, timeout=6: True)
    monkeypatch.setattr(
        strategic_foresight,
        "call_json_model_chain",
        lambda *_args, **_kwargs: (_kb_synthesis_model_json(), "gpt-6.1-sol"),
    )
    monkeypatch.setattr(general_summary, "detect_contradictions", lambda _links, _company: [])

    foresight_data = strategic_foresight.generate_foresight(
        company_name="Konecranes Oyj",
        domain="Industrial & Manufacturing",
        archetype="Thriving",
        reality_score=64,
        financial_index=72,
    )
    kb_links = strategic_foresight.extract_foresight_links(
        foresight_data,
        company_name="Konecranes Oyj",
        domain="Industrial & Manufacturing",
    )
    primary_foresight_link = next(item for item in kb_links if item["is_primary"] is True)

    # Confirm source-quality gates reject extracted Foresight links for company-fact or premise promotion.
    assert is_generation_eligible_source(primary_foresight_link) is False
    assert is_analysis_premise_source(primary_foresight_link) is False

    # Even if a stored `foresight_links` entry carries stale `generation_eligible=True` /
    # `analysis_verified=True` flags, registry classification strips them, and even if an
    # extracted `foresight_link` is misplaced inside `evidence_links` or `reality_checklist`,
    # `general_summary._build_input_payload()` excludes it from every company-fact slot.
    polluted_foresight_link = dict(
        primary_foresight_link,
        url="https://www.gartner.com/en/articles/industrial-ai-trends-2026",
        title="Gartner Macro Industrial AI Outlook",
        source_type="analyst",
        analysis_verified=True,
        promotion_status="generation_eligible",
        generation_eligible=True,
        claim_support_status="supported",
    )
    company_fact_link = _verified_company_evidence_link()

    classified_record = classify_source_registry_items({
        "companyName": "Konecranes Oyj",
        "companyUrl": "https://www.konecranes.com/",
        "evidence_links": [
            company_fact_link,
            copy.deepcopy(primary_foresight_link),  # misplaced Foresight link inside `evidence_links`
        ],
        "reality_checklist": [
            {
                "category": "AI Readiness",
                "title": primary_foresight_link["title"],
                "description": primary_foresight_link["source_says"],
                "url": primary_foresight_link["url"],
                "found": True,
                "sentiment": "neutral",
                **copy.deepcopy(primary_foresight_link),
            }
        ],
        "strategic_foresight": foresight_data,
        "foresight_links": kb_links + [polluted_foresight_link],
        "financial_index": 72,
    })

    gs_payload = general_summary._build_input_payload(classified_record, "Konecranes Oyj")
    assert [item["title"] for item in gs_payload["evidence_links"]] == [company_fact_link["title"]]
    assert gs_payload["attributed_company_context"] == []
    assert gs_payload["key_findings"] == []
    serialized_gs = json.dumps(gs_payload)
    assert "oecd.org" not in serialized_gs
    assert "weforum.org" not in serialized_gs
    assert "gartner.com" not in serialized_gs
    assert primary_foresight_link["title"] not in serialized_gs

    # Source Ledger contract: even when `foresight_links` have `generation_eligible=True` and
    # `analysis_verified=True` BEFORE sanitizer cleanup, `origin="foresight"` forces
    # `context_only=True` and `usable_as_scanner_seed=False`, keeping `scanner_seed_links == []`
    # and excluding all Foresight URLs from `ledger_scanner_seed_links()`.
    raw_ledger_input = {
        "companyName": "Konecranes Oyj",
        "companyUrl": "https://www.konecranes.com/",
        "evidence_links": [company_fact_link],
        "foresight_links": kb_links + [polluted_foresight_link],
    }
    ledger, _html = build_company_source_ledger(raw_ledger_input)

    assert ledger["ledger_scope"] == "buying_insight_only"
    assert ledger["scanner_seed_links"] == []
    foresight_records = [rec for rec in ledger["records"] if "foresight" in rec["origins"]]
    assert len(foresight_records) == 3
    assert all(rec["context_only"] is True for rec in foresight_records)
    assert all(rec["usable_as_scanner_seed"] is False for rec in foresight_records)

    legacy_seeds = ledger_scanner_seed_links(ledger)
    assert [seed["url"] for seed in legacy_seeds] == [
        "https://www.konecranes.com/press/truconnect-connected-services-2026"
    ]
