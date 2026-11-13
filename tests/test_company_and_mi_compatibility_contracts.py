"""Golden company record, global index, and Market Intelligence compatibility contracts.

Card 2A freezes current deterministic read and serialization behavior across:
  1. Primary Blob master record (`companies/{slug}.json`) and global index row
     (`index/summary_list.json`, including Buying Insight availability flags),
     plus strict separation from compact PostgreSQL stage checkpoints.
  2. Direct deterministic aggregations in `backend/market_intelligence.py`:
     - `compute_top_foresights()`
     - `compute_cross_impact()`
     - `compute_domain_dimension_matrix()`
     - `compute_industry_radar()`
     - `compute_foresight_coverage()`
     - `compute_market_intelligence()`
  3. Current foresight-link eligibility divergence between direct Market
     Intelligence (`/api/market-intelligence`), FAVI (`foresight_vulnerability.py`),
     and Foresight Observatory (`foresight_observatory._filter_company_foresight`),
     so any future hardening changes those rules deliberately rather than by accident.

All contracts execute under `tests/conftest.py` with socket/DNS access blocked and
without LLM/provider calls.
"""

from __future__ import annotations

import copy
from datetime import datetime, timezone
import json
import os
import sys


_BACKEND = os.path.join(os.path.dirname(__file__), "..", "backend")
if _BACKEND not in sys.path:
    sys.path.insert(0, _BACKEND)

from analyze_scores import run_analysis  # noqa: E402
from company_source_ledger import build_company_source_ledger  # noqa: E402
from evidence_graph import attach_evidence_graph  # noqa: E402
import foresight_observatory as observatory  # noqa: E402
import general_summary  # noqa: E402
from market_intelligence import (  # noqa: E402
    ALL_CATEGORIES,
    KB_DIMENSIONS,
    THEME_CATEGORIES,
    compute_cross_impact,
    compute_domain_dimension_matrix,
    compute_foresight_coverage,
    compute_industry_radar,
    compute_market_intelligence,
    compute_top_foresights,
    shape_cross_impact_for_prompt,
)
from scan_diagnosis_checkpoint import (  # noqa: E402
    build_diagnosis_checkpoint,
    build_diagnosis_input_projection,
)
from scan_diagnosis_snapshot import build_diagnosis_input_snapshot  # noqa: E402
from scan_foresight_checkpoint import (  # noqa: E402
    build_foresight_checkpoint,
    build_foresight_input_projection,
    empty_foresight_result,
)
from scan_foresight_snapshot import build_foresight_input_snapshot  # noqa: E402
from scan_intent_checkpoint import (  # noqa: E402
    build_intent_checkpoint,
    build_intent_input_projection,
)
from scan_intent_snapshot import build_intent_input_snapshot  # noqa: E402
from scan_jury_candidates import prepare_jury_source_candidate  # noqa: E402
from scan_jury_checkpoint import (  # noqa: E402
    JURY_CHECKPOINT_LANES,
    build_jury_checkpoint,
    build_jury_input_projection,
)
from scan_jury_snapshot import build_jury_input_snapshot  # noqa: E402
from scan_checkpoints import build_material_ingestion_checkpoint  # noqa: E402
from scan_materials import prepare_scan_materials  # noqa: E402
from scan_retrievals import prepare_source_retrieval  # noqa: E402
from scan_synthesis_checkpoint import (  # noqa: E402
    build_synthesis_checkpoint,
    build_synthesis_input_projection,
)
from scan_synthesis_snapshot import build_synthesis_input_snapshot  # noqa: E402
from scan_verification_checkpoint import (  # noqa: E402
    build_verification_checkpoint,
    build_verification_input_projection,
    empty_verification_result,
)
from scan_verification_snapshot import build_verification_input_snapshot  # noqa: E402
from storage import BlobStorage  # noqa: E402


MASTER_COMPANY_CONTRACT_FIELDS = {
    "id",
    "companyName",
    "companyUrl",
    "domain",
    "revenue_avg_3yr",
    "employees",
    "ownership",
    "hq_country",
    "intent_score",
    "reality_score",
    "reality_gap",
    "confidence_score",
    "archetype",
    "washing_flag",
    "washing_type",
    "intent_breakdown",
    "reality_checklist",
    "financial_index",
    "financial_sub_scores",
    "financial_findings",
    "general_summary",
    "general_summary_verified",
    "general_summary_verification",
    "summary_text",
    "detailed_report",
    "detailed_report_status",
    "detailed_report_verification",
    "strategic_synthesis",
    "pitch_text",
    "evidence_based_risks",
    "activation_pairs",
    "why_proposals",
    "what_proposals",
    "evidence_links",
    "strategic_foresight",
    "foresight_links",
    "key_people",
    "ai_verification",
    "history",
    "evidence_manifest",
    "claim_graph",
    "evidence_graph_version",
}

INDEX_ONLY_FORBIDDEN_MASTER_NARRATIVE_FIELDS = {
    "general_summary",
    "summary_text",
    "detailed_report",
    "strategic_synthesis",
    "pitch_text",
    "why_proposals",
    "what_proposals",
    "activation_pairs",
    "evidence_links",
    "strategic_foresight",
    "foresight_links",
    "key_people",
    "ai_verification",
    "history",
}


def _intent_breakdown(
    *,
    operating_model: int,
    service_portfolio: int,
    values: int,
    strategy: int,
    business_model: int,
    ai_readiness: int,
) -> list[dict]:
    """Build the 6-category Scanner-1 breakdown stored on `companies/{slug}.json`.

    Note: `"Business Model"` is present in stored company records (`Scanner1Category`)
    while `market_intelligence.KB_DIMENSIONS` intentionally evaluates only the 5 KB
    dimensions (`Operating Model`, `Service Portfolio`, `Values`, `Strategy`,
    `AI Readiness`).
    """
    return [
        {"name": "Operating Model", "score": operating_model, "goodCount": 4, "badCount": 1},
        {"name": "Service Portfolio", "score": service_portfolio, "goodCount": 5, "badCount": 1},
        {"name": "Values", "score": values, "goodCount": 3, "badCount": 1},
        {"name": "Strategy", "score": strategy, "goodCount": 5, "badCount": 0},
        {"name": "Business Model", "score": business_model, "goodCount": 4, "badCount": 1},
        {"name": "AI Readiness", "score": ai_readiness, "goodCount": 6, "badCount": 2},
    ]


def _foresight_link(
    *,
    title: str,
    url: str,
    fp_theme: str,
    kb_dimension: str,
    force_type: str = "Probable/Emerging",
    timeframe: str = "12-24mo",
    source_credibility: str = "high",
    validated: bool = True,
    is_priority_dimension: bool = True,
    foresight_context_eligible: bool = True,
    foresight_signal_status: str = "context_eligible",
    source_audit_status: str = "accepted",
    source_readable: bool = True,
    **extra,
) -> dict:
    record = {
        "title": title,
        "url": url,
        "snippet": f"Company-neutral trend description for {title}.",
        "source_says": f"Company-neutral trend description for {title}.",
        "possible_relevance": f"Modal strategic hypothesis on how {title} could affect priorities.",
        "source_type": "think_tank",
        "verified": True,
        "url_verified": True,
        "source_readable": source_readable,
        "source_credibility": source_credibility,
        "validated": validated,
        "foresight_context_eligible": foresight_context_eligible,
        "foresight_signal_status": foresight_signal_status,
        "source_audit_status": source_audit_status,
        "kb_dimension": kb_dimension,
        "fp_theme": fp_theme,
        "timeframe": timeframe,
        "force_type": force_type,
        "lifecycle_stage": "accelerating",
        "trend_momentum": "rising",
        "action_timing": "act_now",
        "origin": "foresight",
        "is_primary": True,
        "company_specific": False,
        "claim_type": "foresight_context",
        "scope": "external-trend",
        "not_evidence_of_deployment": True,
        "is_priority_dimension": is_priority_dimension,
    }
    record.update(extra)
    return record


def _build_golden_company_record() -> dict:
    """Representative serialized `companies/{slug}.json` master contract."""
    foresight_links = [
        _foresight_link(
            title="Industrial autonomous crane telemetry regulation",
            url="https://oecd.example.org/reports/autonomous-industrial-telemetry",
            fp_theme="Artificial Intelligence",
            kb_dimension="AI Readiness",
            force_type="Probable/Emerging",
            timeframe="6-12mo",
            source_credibility="high",
        ),
        _foresight_link(
            title="Modular factory automation & predictive maintenance standards",
            url="https://wef.example.org/publications/modular-factory-maintenance",
            fp_theme="Industry & Manufacturing",
            kb_dimension="Operating Model",
            force_type="Plausible/Maturing",
            timeframe="12-24mo",
            source_credibility="high",
        ),
        _foresight_link(
            title="Connected equipment lifecycle data-sharing mandates",
            url="https://eu.example.org/digital/connected-equipment-data-act",
            fp_theme="Data & Digitalisation",
            kb_dimension="Service Portfolio",
            force_type="Probable/Emerging",
            timeframe="12-24mo",
            source_credibility="medium",
        ),
    ]

    record = {
        "id": "scan-golden-konecranes-001",
        "companyName": "Konecranes Oyj",
        "companyUrl": "https://www.konecranes.com/",
        "domain": "Industrial & Manufacturing",
        "revenue_avg_3yr": 3960.0,
        "employees": 16600,
        "ownership": "Public (Nasdaq Helsinki)",
        "hq_country": "Finland",
        "intent_score": 78,
        "reality_score": 64,
        "reality_gap": 14,
        "confidence_score": 80,
        "current_reality_unavailable": False,
        "reality_score_valid": True,
        "classification_valid": True,
        "archetype": "Thriving",
        "frozen_classification_snapshot": {
            "archetype": "Thriving",
            "reality_score": 64,
            "financial_index": 72,
        },
        "classification_context": {
            "adaptability_archetype": "Wealthy Under Pressure",
            "favi_vulnerability_segment": "Sleepwalking",
            "adaptation_capacity_segment": "High Capacity",
            "foresight_force_profile": "Probable/Emerging",
        },
        "adaptability_archetype": "Wealthy Under Pressure",
        "favi_vulnerability_segment": "Sleepwalking",
        "adaptation_capacity_segment": "High Capacity",
        "foresight_force_profile": "Probable/Emerging",
        "washing_flag": False,
        "washing_type": None,
        "intent_breakdown": _intent_breakdown(
            operating_model=74,
            service_portfolio=80,
            values=70,
            strategy=78,
            business_model=92,
            ai_readiness=88,
        ),
        "metrics": {
            "friction_index": 22,
            "vibrancy_score": 71,
            "total_good_mentions": 27,
            "total_bad_mentions": 6,
        },
        "reality_checklist": [
            {
                "category": "Operating Model",
                "found": True,
                "query": "Konecranes platform engineering value streams",
                "source_type": "official",
                "sentiment": "positive",
                "is_recent": True,
            },
            {
                "category": "AI Readiness",
                "found": True,
                "query": "Konecranes AI data governance legacy ERP bottlenecks",
                "source_type": "news",
                "sentiment": "negative",
                "is_recent": True,
            },
        ],
        "evidence_links": [
            {
                "title": "Konecranes expands TRUCONNECT digital service platform",
                "url": "https://www.konecranes.com/press/truconnect-service-platform-2026",
                "source": "konecranes.com",
                "snippet": "Konecranes expanded remote telemetry and lifecycle maintenance services across industrial crane fleets.",
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
                "last_checked": "2026-11-01T09:00:00+00:00",
            },
            {
                "title": "General Nordic manufacturing roundtable recap",
                "url": "https://industry-forum.example.com/nordic-roundtable-2026",
                "source": "industry-forum.example.com",
                "snippet": "Panelists discussed general automation trends in Nordic heavy industry.",
                "source_quote": "Panelists discussed general automation trends without company-specific deployment commitments.",
                "verified": True,
                "url_verified": True,
                "content_verified": True,
                "analysis_verified": False,
                "entity_verified": False,
                "entity_scope": "sector_context",
                "entity_match_status": "weak",
                "claim_support_status": "unsupported",
                "promotion_status": "display_only",
                "generation_eligible": False,
                "company_specific": False,
                "claim_type": "sector_context",
                "scope": "sector",
                "source_credibility": "medium",
                "source_type": "news",
                "claim_tier": "C",
                "is_recent": True,
                "last_checked": "2026-11-01T09:00:00+00:00",
            },
        ],
        "evidence_gold_standard": True,
        "evidence_sufficiency": {
            "score": 4.0,
            "status": "complete",
            "label": "Sufficient corroborated company evidence",
            "promoted_company_sources": 1,
            "promoted_nonfinancial_sources": 1,
            "promoted_financial_sources": 1,
            "promoted_total_sources": 2,
        },
        "low_evidence_mode": False,
        "low_evidence_label": "",
        "dimension_evidence_notes": {
            "ai_readiness": {
                "dimension": "AI Readiness",
                "intent_score": 88,
                "promoted_evidence_count": 1,
                "status": "supported",
                "warning": "",
            },
            "absence_signals": [],
        },
        "generation_premise_manifest": [],
        "evidence_absence_signals": [],
        "ai_readiness_evidence_note": "",
        "key_people": [
            {
                "name": "Anders Svensson",
                "role": "President and CEO",
                "relevance": "Executive sponsor for industrial service growth and operating-model execution.",
                "linkedin_insights": {
                    "found": True,
                    "headline": "President and CEO at Konecranes",
                    "verified_role": "President and CEO",
                    "still_at_company": True,
                    "summary": "Focuses on lifecycle services, industrial productivity, and disciplined capital allocation.",
                    "career_moves": ["Joined Konecranes as President and CEO"],
                    "topics": ["Industrial services", "Operational excellence"],
                    "notable_posts": [],
                    "model_used": None,
                },
            },
            {
                "name": "Minna Aila",
                "role": "EVP, Corporate Affairs & Brand",
                "relevance": "Governance, sustainability disclosure, and stakeholder communications.",
            },
        ],
        "stakeholder_enrichment_skipped": False,
        "stakeholder_intelligence_mode": "buying_insight_only",
        "general_summary": (
            "Konecranes Oyj operates connected industrial lifting equipment and lifecycle "
            "maintenance services from Finland across global manufacturing and port customers.\n\n"
            "Filed financial findings indicate stable multi-year revenue around EUR 3.96 billion "
            "with solid operating profitability and ongoing digital service investment.\n\n"
            "Public evidence confirms remote telemetry and API-enabled maintenance offerings, while "
            "enterprise-wide autonomous AI deployment depth across legacy plant systems remains an "
            "open verification question."
        ),
        "general_summary_model": "claude-sonnet-5-5",
        "general_summary_generation": {
            "model": "claude-sonnet-5-5",
            "fallback_used": False,
            "generation_time_ms": 4120,
        },
        "general_summary_verified": True,
        "general_summary_status": "generated_verified",
        "general_summary_false_claims": [],
        "general_summary_verification": {
            "initial_status": "verified",
            "final_status": "verified",
            "repaired_claim_ids": [],
            "final_resolution_claim_ids": [],
            "final_removed_claim_ids": [],
            "unresolved_claims": [],
            "model": "gpt-6.1",
            "correction_model": "",
            "time_ms": 1890,
        },
        "summary_text": (
            "Konecranes pairs strong lifecycle service execution with ambitious AI Readiness "
            "messaging, leaving a moderate 14-point gap where plant-floor data platform integration "
            "must catch up with commercial telemetry claims."
        ),
        "ai_diagnosis_status": "completed",
        "detailed_report": (
            "## Executive Diagnostic\n"
            "Konecranes demonstrates corroborated strength in connected crane maintenance services "
            "while facing rising regulatory and data-sharing pressure across European industrial fleets.\n\n"
            "## Evidence & Readiness Assessment\n"
            "Telemetry and API service proof is documented, whereas cross-plant AI operating-model "
            "standardization warrants targeted discovery."
        ),
        "detailed_report_model": "claude-sonnet-5-5",
        "detailed_report_status": "published_verified",
        "detailed_report_quality": {
            "complete": True,
            "word_count": 812,
            "missing": [],
        },
        "detailed_report_verification": {
            "verified": True,
            "verification_status": "verified",
            "fact_check_skipped": False,
            "verification_partial": False,
            "unavailable_sections": [],
            "unresolved_blocking_claims": [],
        },
        "strategic_synthesis": (
            "Konecranes can convert connected-fleet telemetry leadership into durable recurring "
            "service margin if it standardizes data platform governance and operating-model handoffs "
            "before EU industrial data-sharing rules tighten."
        ),
        "pitch_text": (
            "Validate how Konecranes can unify telemetry data platform governance and operating-model "
            "ownership across service regions."
        ),
        "evidence_based_risks": [
            "AI Readiness ambition outpaces publicly corroborated plant-floor data platform integration.",
            "Upcoming connected-equipment data-sharing obligations could strain regional service handoffs.",
        ],
        "activation_pairs": [
            {
                "pair_id": "ACT-01",
                "theme_type": "current_status",
                "why": "Unify data-driven AI platform governance so connected crane telemetry scales cleanly.",
                "what": "Run an API and cloud data platform architecture audit across regional service units.",
                "what_progress": "Agreed target architecture and telemetry API ownership across two pilot units.",
            },
            {
                "pair_id": "ACT-02",
                "theme_type": "foresight_pressure",
                "why": "Strengthen operating model agility before connected-equipment compliance deadlines.",
                "what": "Design cross-functional DevOps and agile delivery routines for digital service teams.",
                "what_progress": "Defined stream-aligned team boundaries and release governance cadence.",
            },
        ],
        "why_proposals": [
            "Unify data-driven AI platform governance so connected crane telemetry scales cleanly.",
            "Strengthen operating model agility before connected-equipment compliance deadlines.",
        ],
        "what_proposals": [
            "Run an API and cloud data platform architecture audit across regional service units.",
            "Design cross-functional DevOps and agile delivery routines for digital service teams.",
        ],
        "what_progress_proposals": [
            "Agreed target architecture and telemetry API ownership across two pilot units.",
            "Defined stream-aligned team boundaries and release governance cadence.",
        ],
        "activation_generation": {
            "status": "completed",
            "provider": "anthropic",
            "synthesis_parse_status": "ok",
            "activation_parse_status": "ok",
        },
        "lastUpdated": "2026-11-10T10:15:00+00:00",
        "history": [
            {
                "date": "2026-08-15T08:30:00+00:00",
                "gap": 18,
                "intent_score": 76,
                "reality_score": 58,
                "confidence_score": 75,
                "washing_type": None,
                "summary_text": "Prior scan recorded expanding connected-service telemetry with moderate execution lag.",
                "findings": [
                    {
                        "category": "Service Portfolio",
                        "found": True,
                        "sentiment": "positive",
                    }
                ],
                "evidence": [
                    {
                        "title": "Konecranes lifecycle services overview",
                        "url": "https://www.konecranes.com/services/lifecycle",
                    }
                ],
                "foresight_evidence": [
                    {
                        "title": "Industrial autonomous crane telemetry regulation",
                        "url": "https://oecd.example.org/reports/autonomous-industrial-telemetry",
                        "fp_theme": "Artificial Intelligence",
                    }
                ],
            }
        ],
        "is_simulated": False,
        "uploaded_docs": ["konecranes_annual_review_2025.pdf"],
        "financial_index": 72,
        "financial_sub_scores": {
            "profitability": 19,
            "financial_stability": 18,
            "investment_signal": 20,
        },
        "financial_findings": [
            {
                "dimension": "profitability",
                "assessment": "Operating margin remained resilient supported by industrial service mix.",
                "source_title": "Konecranes Financial Statements Bulletin",
                "source_url": "https://investors.konecranes.com/reports/financial-statements-2025",
                "evidence_snippet": "Comparable EBITA margin improved on higher industrial service volume.",
            }
        ],
        "financial_confidence": 85,
        "strategic_foresight": {
            "kb_first": True,
            "dimension_forces": [
                {
                    "id": "FORCE-AI-01",
                    "trend": "Industrial autonomous crane telemetry regulation",
                    "theme": "Artificial Intelligence",
                    "fp_theme": "Artificial Intelligence",
                    "dimension": "AI Readiness",
                    "kb_dimension": "AI Readiness",
                    "type": "Probable/Emerging",
                    "timeframe": "6-12mo",
                    "description": "Industrial safety regulators are tightening telemetry auditability for automated lifting equipment.",
                    "impact_on_company": "Could require standardized model and sensor lineage across TRUCONNECT service deployments.",
                    "source_url": "https://oecd.example.org/reports/autonomous-industrial-telemetry",
                    "source_type": "think_tank",
                    "url_verified": True,
                    "source_credibility": "high",
                    "foresight_context_eligible": True,
                    "foresight_signal_status": "context_eligible",
                    "source_audit_status": "accepted",
                    "company_specific": False,
                    "not_evidence_of_deployment": True,
                }
            ],
            "cross_impact_chain": [
                {
                    "from_theme": "Artificial Intelligence",
                    "to_dimension": "AI Readiness",
                    "mechanism": "Telemetry audit rules raise integration requirements on fleet data platforms.",
                }
            ],
            "scenarios": [
                {
                    "name": "Regulated Connected Fleet",
                    "horizon": "12-24mo",
                    "summary": "Customers require auditable telemetry interfaces in service tenders.",
                }
            ],
            "climate_exposure": {
                "level": "moderate",
                "summary": "Electrified port and industrial crane retrofits sustain lifecycle demand.",
            },
        },
        "foresight_links": foresight_links,
        "ai_verification": {
            "verified": True,
            "verification_status": "verified",
            "fact_check_skipped": False,
            "false_claims": [],
            "false_claims_detailed": [],
            "audit_notes": [],
            "audit_notes_detailed": [],
            "model": "gpt-6.1",
            "time_ms": 2340,
            "evidence_handoff_failures": [],
            "premise_reference_failures": [],
            "corrected_claim_ids": [],
            "unresolved_handoff_ids": [],
        },
        "hallucination_flags": [],
        "quality_notes": [],
        "hallucination_risk": "low",
        "hallucination_score": 0,
        "scan_quality": "complete",
        "generation_evidence_manifest": [],
    }
    attach_evidence_graph(record)
    return record


def _build_golden_index_row(
    company_record: dict,
    *,
    details_url: str = "https://blob.example.invalid/companies/konecranes_oyj.json",
    include_buying_potential_flags: bool = True,
) -> dict:
    """Matching `index/summary_list.json` row produced by scan save + Buying Insight."""
    row = {
        "id": company_record["id"],
        "companyName": company_record["companyName"],
        "domain": company_record["domain"],
        "revenue_avg_3yr": company_record["revenue_avg_3yr"],
        "employees": company_record["employees"],
        "ownership": company_record["ownership"],
        "hq_country": company_record["hq_country"],
        "confidence_score": company_record["confidence_score"],
        "intent_score": company_record["intent_score"],
        "reality_score": company_record["reality_score"],
        "reality_score_valid": company_record["reality_score_valid"],
        "classification_valid": company_record["classification_valid"],
        "current_reality_unavailable": company_record["current_reality_unavailable"],
        "reality_gap": company_record["reality_gap"],
        "lastUpdated": company_record["lastUpdated"],
        "detailsUrl": details_url,
        "has_docs": bool(company_record.get("uploaded_docs")),
        "financial_index": company_record["financial_index"],
        "archetype": company_record["archetype"],
        "ai_verified": company_record["ai_verification"]["verified"],
        "scan_quality": company_record["scan_quality"],
    }
    if include_buying_potential_flags:
        row.update({
            "buying_potential_available": True,
            "buying_potential_updated_at": "2026-11-11T14:20:00+00:00",
            "buying_potential_confidence": 78,
        })
    return row


def _build_golden_portfolio() -> tuple[list[dict], list[dict]]:
    """Deterministic 5-company portfolio exercising all MI, FAVI, cohort, and Observatory paths.

    Why 5 companies:
      - `analyze_scores.run_analysis` requires >= 3 companies, and Pearson regression
        residuals in both `run_analysis` and `compute_vulnerable_companies` require >= 5.
      - `foresight_vulnerability.compute_segment_thresholds` uses dynamic portfolio
        percentiles (`percentile:favi=p50,current=p50`) only when population >= 4.
      - `compute_foresight_coverage` marks an FP theme `covered` only when >= 3
        distinct companies carry that theme.
      - Including `"Swedbank"` (`https://www.swedbank.se/`) exercises curated Sweden
        cohort matching inside `compute_market_intelligence`.

    Foresight-link eligibility classes embedded across the 5 companies:
      1. Explicitly Observatory-eligible (`foresight_context_eligible=True`,
         `source_audit_status="accepted"`, `source_readable=True`, `validated=True`)
      2. Audit-dead link (`foresight_context_eligible=True`,
         `source_audit_status="source_dead"`, `validated=True`, `is_priority_dimension=False`)
      3. Legacy display-only link (`foresight_context_eligible=False`,
         `foresight_signal_status="not_context_eligible"`, `promotion_status="display_only"`,
         `validated=True`, `is_priority_dimension=True`)
      4. Unreadable source link (`foresight_context_eligible=True`,
         `source_readable=False`, `validated=True`, `is_priority_dimension=True`)
      5. Unvalidated rejected link (`foresight_context_eligible=False`,
         `foresight_signal_status="rejected"`, `source_audit_status="rejected"`,
         `validated=False`, `is_priority_dimension=True`)
    """
    konecranes = _build_golden_company_record()

    valmet = {
        "id": "scan-golden-valmet-002",
        "companyName": "Valmet Oyj",
        "companyUrl": "https://www.valmet.com/",
        "domain": "Industrial & Manufacturing",
        "revenue_avg_3yr": 5400.0,
        "employees": 19000,
        "ownership": "Public (Nasdaq Helsinki)",
        "hq_country": "Finland",
        "intent_score": 74,
        "reality_score": 44,
        "reality_gap": 30,
        "confidence_score": 72,
        "current_reality_unavailable": False,
        "reality_score_valid": True,
        "classification_valid": True,
        "archetype": "Cash Cow",
        "washing_flag": True,
        "washing_type": "AI Washing",
        "intent_breakdown": _intent_breakdown(
            operating_model=72,
            service_portfolio=76,
            values=68,
            strategy=74,
            business_model=70,
            ai_readiness=80,
        ),
        "reality_checklist": [
            {"category": "AI Readiness", "found": True, "sentiment": "negative", "is_recent": True},
            {"category": "Operating Model", "found": True, "sentiment": "negative", "is_recent": True},
        ],
        "financial_index": 66,
        "financial_sub_scores": {"profitability": 17, "financial_stability": 16, "investment_signal": 15},
        "why_proposals": [
            "Close the AI and machine learning execution gap across industrial process lines.",
        ],
        "what_proposals": [
            "Consolidate cloud data platform pipelines before scaling customer automation claims.",
        ],
        "foresight_links": [
            _foresight_link(
                title="Autonomous process-industry control loops",
                url="https://vtt.example.fi/process-ai-control-2026",
                fp_theme="Artificial Intelligence",
                kb_dimension="Operating Model",
                force_type="Probable/Emerging",
                timeframe="12-24mo",
                source_credibility="high",
            ),
            _foresight_link(
                title="Industrial digital twin interoperability rules",
                url="https://iso.example.org/digital-twin-interoperability",
                fp_theme="Data & Digitalisation",
                kb_dimension="Strategy",
                force_type="Plausible/Maturing",
                timeframe="12-24mo",
                source_credibility="medium",
            ),
            # Class 2: stale positive context flag overridden by dead audit status.
            _foresight_link(
                title="Retired carbon border transitional guidance",
                url="https://dead-source.example.org/cbam-guidance-old",
                fp_theme="Climate Change",
                kb_dimension="Values",
                force_type="Weakening/Declining",
                timeframe="24-36mo",
                source_credibility="medium",
                validated=True,
                is_priority_dimension=False,
                foresight_context_eligible=True,
                foresight_signal_status="context_eligible",
                source_audit_status="source_dead",
            ),
        ],
        "ai_verification": {"verified": True},
        "scan_quality": "complete",
        "lastUpdated": "2026-11-10T10:20:00+00:00",
        "uploaded_docs": [],
    }

    elisa = {
        "id": "scan-golden-elisa-003",
        "companyName": "Elisa Oyj",
        "companyUrl": "https://elisa.fi/",
        "domain": "Telecom & Digital Services",
        "revenue_avg_3yr": 2180.0,
        "employees": 5700,
        "ownership": "Public (Nasdaq Helsinki)",
        "hq_country": "Finland",
        "intent_score": 82,
        "reality_score": 70,
        "reality_gap": 12,
        "confidence_score": 84,
        "current_reality_unavailable": False,
        "reality_score_valid": True,
        "classification_valid": True,
        "archetype": "Modernizing",
        "washing_flag": False,
        "washing_type": None,
        "intent_breakdown": _intent_breakdown(
            operating_model=80,
            service_portfolio=84,
            values=76,
            strategy=82,
            business_model=79,
            ai_readiness=88,
        ),
        "reality_checklist": [
            {"category": "AI Readiness", "found": True, "sentiment": "positive", "is_recent": True},
        ],
        "financial_index": 45,
        "financial_sub_scores": {"profitability": 14, "financial_stability": 11, "investment_signal": 12},
        "why_proposals": [
            "Improve recurring revenue monetization from autonomous network SaaS platforms.",
        ],
        "what_proposals": [
            "Standardize customer personalization APIs across enterprise telecom services.",
        ],
        "foresight_links": [
            _foresight_link(
                title="Telco autonomous network assurance benchmarks",
                url="https://etsi.example.org/autonomous-networks-2026",
                fp_theme="Artificial Intelligence",
                kb_dimension="AI Readiness",
                force_type="Probable/Emerging",
                timeframe="6-12mo",
                source_credibility="high",
            ),
            # Class 3: legacy display-only link lacking explicit context eligibility,
            # but still carrying `validated=True` and `is_priority_dimension=True`.
            _foresight_link(
                title="Legacy display-only edge cloud brief",
                url="https://legacy.example.org/edge-cloud-brief",
                fp_theme="Data & Digitalisation",
                kb_dimension="AI Readiness",
                force_type="Probable/Emerging",
                timeframe="6-12mo",
                source_credibility="high",
                validated=True,
                is_priority_dimension=True,
                foresight_context_eligible=False,
                foresight_signal_status="not_context_eligible",
                promotion_status="display_only",
            ),
        ],
        "ai_verification": {"verified": True},
        "scan_quality": "complete",
        "lastUpdated": "2026-11-10T10:25:00+00:00",
        "uploaded_docs": [],
    }

    swedbank = {
        "id": "scan-golden-swedbank-004",
        "companyName": "Swedbank",
        "companyUrl": "https://www.swedbank.se/",
        "domain": "Financial Services",
        "revenue_avg_3yr": 6300.0,
        "employees": 17200,
        "ownership": "Public (Nasdaq Stockholm)",
        "hq_country": "Sweden",
        "intent_score": 76,
        "reality_score": 58,
        "reality_gap": 18,
        "confidence_score": 79,
        "current_reality_unavailable": False,
        "reality_score_valid": True,
        "classification_valid": True,
        "archetype": "Thriving",
        "washing_flag": False,
        "washing_type": None,
        "intent_breakdown": _intent_breakdown(
            operating_model=74,
            service_portfolio=78,
            values=72,
            strategy=80,
            business_model=75,
            ai_readiness=76,
        ),
        "reality_checklist": [
            {"category": "Strategy", "found": True, "sentiment": "positive", "is_recent": True},
            {"category": "Operating Model", "found": True, "sentiment": "negative", "is_recent": True},
        ],
        "financial_index": 74,
        "financial_sub_scores": {"profitability": 20, "financial_stability": 19, "investment_signal": 17},
        "why_proposals": [
            "Modernize core platform microservice boundaries for regulated retail banking.",
        ],
        "what_proposals": [
            "Automate digital compliance workflows across Nordic lending operations.",
        ],
        "foresight_links": [
            _foresight_link(
                title="Nordic retail banking open-finance supervisory expectations",
                url="https://eba.example.eu/open-finance-supervision-2026",
                fp_theme="Finance & Ownership",
                kb_dimension="Strategy",
                force_type="Plausible/Maturing",
                timeframe="12-24mo",
                source_credibility="high",
            ),
            # Class 4: unreadable source body (`source_readable=False`) despite `validated=True`.
            _foresight_link(
                title="Paywalled supervisory governance annex",
                url="https://bis.example.org/unreadable-governance-annex",
                fp_theme="Public Governance",
                kb_dimension="Operating Model",
                force_type="Probable/Emerging",
                timeframe="6-12mo",
                source_credibility="high",
                validated=True,
                is_priority_dimension=True,
                foresight_context_eligible=True,
                foresight_signal_status="context_eligible",
                source_audit_status="accepted",
                source_readable=False,
            ),
        ],
        "ai_verification": {"verified": True},
        "scan_quality": "complete",
        "lastUpdated": "2026-11-10T10:30:00+00:00",
        "uploaded_docs": [],
    }

    outokumpu = {
        "id": "scan-golden-outokumpu-005",
        "companyName": "Outokumpu Oyj",
        "companyUrl": "https://www.outokumpu.com/",
        "domain": "Industrial & Manufacturing",
        "revenue_avg_3yr": 6100.0,
        "employees": 8500,
        "ownership": "Public (Nasdaq Helsinki)",
        "hq_country": "Finland",
        "intent_score": 68,
        "reality_score": 38,
        "reality_gap": 30,
        "confidence_score": 60,
        "current_reality_unavailable": False,
        "reality_score_valid": True,
        "classification_valid": True,
        "archetype": "At Risk",
        "washing_flag": True,
        "washing_type": "Modernization Washing",
        "intent_breakdown": _intent_breakdown(
            operating_model=66,
            service_portfolio=64,
            values=72,
            strategy=70,
            business_model=65,
            ai_readiness=68,
        ),
        "reality_checklist": [
            {"category": "Operating Model", "found": True, "sentiment": "negative", "is_recent": True},
            {"category": "Values", "found": True, "sentiment": "negative", "is_recent": True},
            {"category": "AI Readiness", "found": True, "sentiment": "negative", "is_recent": True},
        ],
        "financial_index": 34,
        "financial_sub_scores": {"profitability": 8, "financial_stability": 9, "investment_signal": 8},
        "why_proposals": [
            "Align decarbonization sustainability commitments with plant supply chain execution.",
        ],
        "what_proposals": [
            "Stabilize manufacturing procurement and energy efficiency telemetry baselines.",
        ],
        "foresight_links": [
            # Class 5: rejected / unvalidated link (`validated=False`, `foresight_context_eligible=False`).
            _foresight_link(
                title="Unverified industrial hydrogen grid blog post",
                url="https://blog.example.net/unverified-hydrogen-grid",
                fp_theme="Energy",
                kb_dimension="Operating Model",
                force_type="Probable/Emerging",
                timeframe="6-12mo",
                source_credibility="low",
                validated=False,
                is_priority_dimension=True,
                foresight_context_eligible=False,
                foresight_signal_status="rejected",
                source_audit_status="rejected",
            ),
        ],
        "ai_verification": {"verified": False},
        "scan_quality": "unverified",
        "lastUpdated": "2026-11-10T10:35:00+00:00",
        "uploaded_docs": [],
    }

    company_details = [konecranes, valmet, elisa, swedbank, outokumpu]
    dashboard_data = []
    for idx, company in enumerate(company_details):
        slug = f"company_{idx + 1}"
        row = _build_golden_index_row(
            company,
            details_url=f"https://blob.example.invalid/companies/{slug}.json",
            include_buying_potential_flags=(company["companyName"] == "Konecranes Oyj"),
        )
        dashboard_data.append(row)

    return dashboard_data, company_details


def _new_hermetic_blob_storage(monkeypatch) -> BlobStorage:
    """Instantiate BlobStorage with empty env so `__init__` performs zero network I/O."""
    monkeypatch.setenv("BLOB_READ_WRITE_TOKEN", "")
    monkeypatch.setenv("VITE_BLOB_URL", "")
    monkeypatch.setenv("BLOB_URL", "")
    return BlobStorage()


# ═══════════════════════════════════════════════════════════════════════════════
# 1. GOLDEN `companies/{slug}.json` & `index/summary_list.json` CONTRACTS
# ═══════════════════════════════════════════════════════════════════════════════

def test_golden_company_record_json_roundtrip_and_master_field_contract(monkeypatch):
    storage = _new_hermetic_blob_storage(monkeypatch)
    golden = _build_golden_company_record()

    assert MASTER_COMPANY_CONTRACT_FIELDS.issubset(golden.keys())
    assert storage.sanitize_filename(golden["companyName"]) == "konecranes_oyj"
    assert storage.sanitize_filename("Lassila & Tikanoja Oyj") == "lassila_and_tikanoja_oyj"
    assert storage.sanitize_filename("Länsförsäkringar") == "lansforsakringar"

    with_transient_callable = copy.deepcopy(golden)
    with_transient_callable["_transient_hook"] = lambda: "must_be_dropped"
    with_transient_callable["tuple_probe"] = ("a", lambda: "drop_me", "b")

    safe_payload = storage._make_json_safe(with_transient_callable, "companies/konecranes_oyj.json")
    serialized = json.dumps(safe_payload)
    deserialized = json.loads(serialized)

    assert "_transient_hook" not in deserialized
    assert deserialized["tuple_probe"] == ["a", "b"]
    # Legitimate None values must survive `_make_json_safe` (distinct from `_DROPPED`).
    assert "washing_type" in deserialized and deserialized["washing_type"] is None

    for key in MASTER_COMPANY_CONTRACT_FIELDS:
        assert deserialized[key] == golden[key]

    manifest_ids = {item["evidence_id"] for item in deserialized["evidence_manifest"]}
    assert any(eid.startswith("EVD-") for eid in manifest_ids)
    assert any(eid.startswith("FOR-") for eid in manifest_ids)
    assert deserialized["evidence_graph_version"] == "1.1"
    assert len(deserialized["history"]) == 1
    assert deserialized["history"][0]["gap"] == 18

    # Downstream deterministic readers remain compatible with the deserialized master record:
    monkeypatch.setattr(general_summary, "detect_contradictions", lambda _links, _company: [])
    gs_payload = general_summary._build_input_payload(deserialized, deserialized["companyName"])
    assert "key_people" not in gs_payload
    assert [e["title"] for e in gs_payload["evidence_links"]] == [
        "Konecranes expands TRUCONNECT digital service platform"
    ]
    assert "assessment" not in gs_payload["financial_findings"][0]

    ledger, _ledger_html = build_company_source_ledger(deserialized)
    assert ledger["ledger_scope"] == "buying_insight_only"
    assert ledger["scanner_seed_links"] == []
    foresight_ledger_rows = [r for r in ledger["records"] if "foresight" in r["origins"]]
    assert len(foresight_ledger_rows) == 3
    assert all(r["context_only"] is True for r in foresight_ledger_rows)
    assert all(r["usable_as_scanner_seed"] is False for r in foresight_ledger_rows)


def test_global_index_projection_and_buying_insight_flag_contract(monkeypatch):
    storage = _new_hermetic_blob_storage(monkeypatch)
    golden_company = _build_golden_company_record()
    initial_scan_row = _build_golden_index_row(
        golden_company,
        include_buying_potential_flags=False,
    )

    assert INDEX_ONLY_FORBIDDEN_MASTER_NARRATIVE_FIELDS.isdisjoint(initial_scan_row.keys())

    # Fake in-memory Blob backing store to test `update_global_index` and
    # `update_global_index_fields` deterministically without network access.
    stored_versions: list[list[dict]] = []

    def fake_list(prefix, limit=10):
        assert prefix == "index/summary_list"
        if not stored_versions:
            return []
        return [{"url": "mem://index/latest.json", "uploadedAt": "2026-11-10T10:15:00Z"}]

    def fake_get(url):
        assert url == "mem://index/latest.json"
        return copy.deepcopy(stored_versions[-1])

    def fake_put(pathname, data):
        assert pathname == "index/summary_list.json"
        stored_versions.append( json.loads(json.dumps(storage._make_json_safe(data, pathname))) )
        return f"mem://index/v{len(stored_versions)}.json"

    monkeypatch.setattr(storage, "_list", fake_list)
    monkeypatch.setattr(storage, "_get", fake_get)
    monkeypatch.setattr(storage, "_put", fake_put)
    monkeypatch.setattr(storage, "_prune_old_versions", lambda *_a, **_kw: None)

    # 1. Initial scan insert
    assert storage.update_global_index(initial_scan_row) == "mem://index/v1.json"
    assert len(stored_versions[-1]) == 1
    assert stored_versions[-1][0]["companyName"] == "Konecranes Oyj"

    # 2. Buying Insight merges availability metadata onto the existing index row
    updated_url = storage.update_global_index_fields(
        "konecranes oyj",  # case-insensitive match
        {
            "buying_potential_available": True,
            "buying_potential_updated_at": "2026-11-11T14:20:00+00:00",
            "buying_potential_confidence": 78,
        },
    )
    assert updated_url == "mem://index/v2.json"
    assert len(stored_versions[-1]) == 1
    merged_row = stored_versions[-1][0]
    assert merged_row["buying_potential_available"] is True
    assert merged_row["buying_potential_updated_at"] == "2026-11-11T14:20:00+00:00"
    assert merged_row["buying_potential_confidence"] == 78
    assert merged_row["reality_score"] == 64
    assert INDEX_ONLY_FORBIDDEN_MASTER_NARRATIVE_FIELDS.isdisjoint(merged_row.keys())

    # 3. Freeze current case-insensitive full-row replacement behavior on rescan:
    # `update_global_index` deduplicates case-insensitively (`current_index[i] = summary_entry`),
    # which means uncopied `buying_potential_*` flags are overwritten if a rescan caller
    # constructs a fresh `summary_entry` without preserving them (Phase 6 reconciliation target).
    rescan_row = dict(initial_scan_row, companyName="KONECRANES OYJ", reality_score=67, reality_gap=11)
    assert storage.update_global_index(rescan_row) == "mem://index/v3.json"
    assert len(stored_versions[-1]) == 1
    assert stored_versions[-1][0]["companyName"] == "KONECRANES OYJ"
    assert stored_versions[-1][0]["reality_score"] == 67
    assert "buying_potential_available" not in stored_versions[-1][0]


def test_compact_durable_checkpoints_never_contain_or_replace_company_master_fields():
    run_id = "run-card-2a-lineage"
    recorded_at = datetime(2026, 11, 10, 10, 0, tzinfo=timezone.utc)
    materials = prepare_scan_materials(
        run_id=run_id,
        report_text="Connected industrial lifting services and API telemetry.",
        uploads=[],
    ).materials
    mat_cp = build_material_ingestion_checkpoint(run_id=run_id, attempt=1, materials=materials)

    intent_retrieval = prepare_source_retrieval(
        run_id=run_id,
        stage_name="intent",
        attempt=1,
        retrieval_origin="company_url",
        source_url="https://www.konecranes.com/strategy",
        retrieved_at=recorded_at,
        http_status=200,
        content_type="text/html",
        response_content=b"<html><body>Industrial service strategy</body></html>",
        extracted_text="Industrial service strategy",
        parser_route="html_text_v1",
        readability_status="readable",
        retrieval_outcome="succeeded",
    )
    intent_snap = build_intent_input_snapshot(
        run_id=run_id,
        attempt=1,
        materials=materials,
        retrievals=[intent_retrieval],
    )
    intent_proj = build_intent_input_projection(
        snapshot=intent_snap,
        materials=materials,
        retrievals=[intent_retrieval],
    )
    intent_cp = build_intent_checkpoint(
        projection=intent_proj,
        intent_result={
            "score": 78,
            "breakdown": [],
            "metrics": {
                "friction_index": 22,
                "vibrancy_score": 71,
                "total_good_mentions": 27,
                "total_bad_mentions": 6,
            },
        },
    )

    jury_candidate = prepare_jury_source_candidate(
        run_id=run_id,
        attempt=1,
        candidate_origin="company_evidence_db",
        source_url="https://www.konecranes.com/press/truconnect",
        recorded_at=recorded_at,
        cached_extract_text="Connected crane maintenance telemetry.",
        parser_route="cached_text_v1",
    )
    jury_snap = build_jury_input_snapshot(
        run_id=run_id,
        attempt=1,
        intent_checkpoint=intent_cp,
        candidates=[jury_candidate],
        retrievals=[],
    )
    jury_proj = build_jury_input_projection(
        snapshot=jury_snap,
        intent_checkpoint=intent_cp,
        candidates=[jury_candidate],
        retrievals=[],
    )
    jury_cp = build_jury_checkpoint(
        projection=jury_proj,
        jury_result={
            "juries": [
                {
                    "jury": lane,
                    "findings": [],
                    "rejected_evidence": [],
                    "contradictions": [],
                    "missing_coverage": [],
                    "confidence_score": 75,
                    "reality_score_adjustment": 0,
                }
                for lane in JURY_CHECKPOINT_LANES
            ]
        },
    )

    diag_snap = build_diagnosis_input_snapshot(run_id=run_id, attempt=1, jury_checkpoint=jury_cp)
    diag_proj = build_diagnosis_input_projection(snapshot=diag_snap, jury_checkpoint=jury_cp)
    diag_cp = build_diagnosis_checkpoint(
        projection=diag_proj,
        diagnosis_result={
            "diagnostic_summary": "Compact operational lineage summary.",
            "evidence_based_risks": [],
            "reality_score": 64,
            "archetype": "Thriving",
        },
    )

    fore_snap = build_foresight_input_snapshot(run_id=run_id, attempt=1, diagnosis_checkpoint=diag_cp)
    fore_proj = build_foresight_input_projection(snapshot=fore_snap, diagnosis_checkpoint=diag_cp)
    fore_cp = build_foresight_checkpoint(
        projection=fore_proj,
        foresight_result=empty_foresight_result(),
    )

    synth_snap = build_synthesis_input_snapshot(run_id=run_id, attempt=1, foresight_checkpoint=fore_cp)
    synth_proj = build_synthesis_input_projection(snapshot=synth_snap, foresight_checkpoint=fore_cp)
    synth_cp = build_synthesis_checkpoint(
        projection=synth_proj,
        synthesis_result={"strategic_synthesis": "Compact synthesis lineage."},
    )

    ver_snap = build_verification_input_snapshot(run_id=run_id, attempt=1, synthesis_checkpoint=synth_cp)
    ver_proj = build_verification_input_projection(snapshot=ver_snap, synthesis_checkpoint=synth_cp)
    ver_cp = build_verification_checkpoint(
        projection=ver_proj,
        verification_result=empty_verification_result(),
    )

    forbidden_blob_master_keys = {
        "companyName",
        "companyUrl",
        "general_summary",
        "summary_text",
        "detailed_report",
        "why_proposals",
        "what_proposals",
        "activation_pairs",
        "foresight_links",
        "key_people",
        "ai_verification",
        "history",
        "buying_potential_available",
    }
    checkpoints = [mat_cp, intent_cp, jury_cp, diag_cp, fore_cp, synth_cp, ver_cp]
    for cp in checkpoints:
        assert cp.gate_result["status"] == "passed"
        assert forbidden_blob_master_keys.isdisjoint(cp.snapshot.keys())


# ═══════════════════════════════════════════════════════════════════════════════
# 2. DIRECT DETERMINISTIC `market_intelligence.py` CONTRACTS
# ═══════════════════════════════════════════════════════════════════════════════

def test_compute_top_foresights_deterministic_contract():
    _, company_details = _build_golden_portfolio()

    # Add edge-case company with duplicate theme on same company, padded whitespace,
    # an unknown theme outside `THEME_CATEGORIES`, and a malformed non-dict item.
    edge_company = {
        "companyName": "Edge Case Ab",
        "foresight_links": [
            _foresight_link(
                title="AI force 1",
                url="https://edge.example.com/ai-1",
                fp_theme="  Artificial Intelligence  ",
                kb_dimension="AI Readiness",
            ),
            _foresight_link(
                title="AI force 2 on same company",
                url="https://edge.example.com/ai-2",
                fp_theme="Artificial Intelligence",
                kb_dimension="Strategy",
            ),
            _foresight_link(
                title="Unmapped custom theme",
                url="https://edge.example.com/custom",
                fp_theme="Quantum Teleportation",
                kb_dimension="Strategy",
            ),
            "malformed-non-dict-entry",
        ],
    }

    top = compute_top_foresights(company_details + [edge_company])

    # 11 valid-theme links in golden portfolio + 2 valid AI links on Edge Case Ab = 13 total signals.
    assert top[0] == {
        "theme": "Artificial Intelligence",
        "category": "Technology & Digital",
        "count": 5,
        "pct": round(5 / 13 * 100, 1),
        "example_companies": ["Edge Case Ab", "Elisa Oyj", "Konecranes Oyj", "Valmet Oyj"],
    }
    assert top[1] == {
        "theme": "Data & Digitalisation",
        "category": "Technology & Digital",
        "count": 3,
        "pct": round(3 / 13 * 100, 1),
        "example_companies": ["Elisa Oyj", "Konecranes Oyj", "Valmet Oyj"],
    }
    themes_returned = {item["theme"] for item in top}
    assert "Quantum Teleportation" not in themes_returned
    assert len(top) == 7


def test_compute_cross_impact_deterministic_contract():
    _, company_details = _build_golden_portfolio()
    cells = compute_cross_impact(company_details)

    assert cells == [
        {"theme": "Artificial Intelligence", "dimension": "Operating Model", "count": 1},
        {"theme": "Artificial Intelligence", "dimension": "AI Readiness", "count": 2},
        {"theme": "Data & Digitalisation", "dimension": "Service Portfolio", "count": 1},
        {"theme": "Data & Digitalisation", "dimension": "Strategy", "count": 1},
        {"theme": "Data & Digitalisation", "dimension": "AI Readiness", "count": 1},
        {"theme": "Industry & Manufacturing", "dimension": "Operating Model", "count": 1},
        {"theme": "Climate Change", "dimension": "Values", "count": 1},
        {"theme": "Finance & Ownership", "dimension": "Strategy", "count": 1},
        {"theme": "Public Governance", "dimension": "Operating Model", "count": 1},
        {"theme": "Energy", "dimension": "Operating Model", "count": 1},
    ]

    # Subtle current contract: a non-empty `kb_dimension` outside `KB_DIMENSIONS`
    # (e.g., `"Business Model"`) increments `theme_totals[theme]` for Top-15 ranking
    # without emitting a cell outside `KB_DIMENSIONS`.
    extra = [
        {
            "companyName": "Ranking Probe Oy",
            "foresight_links": [
                _foresight_link(
                    title="Services BM 1",
                    url="https://probe.example.com/1",
                    fp_theme="Services",
                    kb_dimension="Business Model",
                ),
                _foresight_link(
                    title="Services BM 2",
                    url="https://probe.example.com/2",
                    fp_theme="Services",
                    kb_dimension="Business Model",
                ),
                _foresight_link(
                    title="Services Strategy 3",
                    url="https://probe.example.com/3",
                    fp_theme="Services",
                    kb_dimension="Strategy",
                ),
            ],
        }
    ]
    probe_cells = compute_cross_impact(company_details + extra)
    assert all(c["dimension"] in KB_DIMENSIONS for c in probe_cells)
    # `Services` has theme_total=3 (tied at top with AI=3 and Data=3), emitting only its Strategy cell.
    services_cells = [c for c in probe_cells if c["theme"] == "Services"]
    assert services_cells == [{"theme": "Services", "dimension": "Strategy", "count": 1}]

    shaped = shape_cross_impact_for_prompt(cells)
    assert shaped[0] == {
        "theme": "Artificial Intelligence",
        "total_density": 3,
        "dimensions": {
            "Operating Model": 1,
            "Service Portfolio": 0,
            "Values": 0,
            "Strategy": 0,
            "AI Readiness": 2,
        },
        "apply_shape_label": False,  # total_density=3 < min_total_for_label=8
    }


def test_compute_domain_dimension_matrix_deterministic_contract():
    dashboard_data, company_details = _build_golden_portfolio()

    # Verify case-insensitive domain join from `dashboard_data` and subtle current
    # rule: `compute_domain_dimension_matrix` checks `kb_dimension in KB_DIMENSIONS`
    # even when `fp_theme` is outside `THEME_CATEGORIES`, and falls back to `"Unknown"`
    # if a company is absent from `dashboard_data` despite having `company["domain"]`.
    mixed_case_dashboard = [
        dict(row, companyName=row["companyName"].upper()) for row in dashboard_data
    ]
    orphan_detail = {
        "companyName": "Unindexed Detail Co",
        "domain": "Telecom & Digital Services",
        "foresight_links": [
            _foresight_link(
                title="Unmapped theme with valid KB dimension",
                url="https://orphan.example.com/unmapped-theme",
                fp_theme="Unmapped Custom Theme",
                kb_dimension="Values",
            )
        ],
    }

    matrix = compute_domain_dimension_matrix(
        company_details + [orphan_detail],
        mixed_case_dashboard,
    )

    assert matrix == [
        {"domain": "Industrial & Manufacturing", "dimension": "Operating Model", "count": 3},
        {"domain": "Industrial & Manufacturing", "dimension": "Service Portfolio", "count": 1},
        {"domain": "Industrial & Manufacturing", "dimension": "Values", "count": 1},
        {"domain": "Industrial & Manufacturing", "dimension": "Strategy", "count": 1},
        {"domain": "Industrial & Manufacturing", "dimension": "AI Readiness", "count": 1},
        {"domain": "Telecom & Digital Services", "dimension": "AI Readiness", "count": 2},
        {"domain": "Financial Services", "dimension": "Operating Model", "count": 1},
        {"domain": "Financial Services", "dimension": "Strategy", "count": 1},
        {"domain": "Unknown", "dimension": "Values", "count": 1},
    ]


def test_compute_industry_radar_deterministic_contract():
    dashboard_data, company_details = _build_golden_portfolio()
    radar = compute_industry_radar(company_details, dashboard_data)

    assert [entry["domain"] for entry in radar] == [
        "Industrial & Manufacturing",
        "Telecom & Digital Services",
        "Financial Services",
    ]

    ind = radar[0]
    assert ind["total_signals"] == 7
    assert set(ind["raw_scores"].keys()) == set(ALL_CATEGORIES)
    assert ind["raw_scores"] == {
        "Technology & Digital": 4,       # 2 AI + 2 Data & Digitalisation
        "Economy & Business": 0,
        "Industry & Infrastructure": 2,  # 1 Industry & Manufacturing + 1 Energy
        "Environment & Resources": 1,    # 1 Climate Change
        "Society & Governance": 0,
        "People & Well-being": 0,
        "Science & Demographics": 0,
    }
    assert ind["normalized_scores"] == {
        "Technology & Digital": 100,
        "Economy & Business": 0,
        "Industry & Infrastructure": 50,
        "Environment & Resources": 25,
        "Society & Governance": 0,
        "People & Well-being": 0,
        "Science & Demographics": 0,
    }

    telco = radar[1]
    assert telco["total_signals"] == 2
    assert telco["raw_scores"]["Technology & Digital"] == 2
    assert telco["normalized_scores"]["Technology & Digital"] == 100

    fin = radar[2]
    assert fin["total_signals"] == 2
    assert fin["raw_scores"]["Economy & Business"] == 1
    assert fin["raw_scores"]["Society & Governance"] == 1
    assert fin["normalized_scores"]["Economy & Business"] == 100
    assert fin["normalized_scores"]["Society & Governance"] == 100


def test_compute_foresight_coverage_deterministic_contract():
    _, company_details = _build_golden_portfolio()

    # Add a second `"Finance & Ownership"` link on Swedbank itself to prove
    # `signal_count` counts DISTINCT companies (still 1, not 2), and `covered`
    # requires >= 3 distinct companies per theme.
    duplicated_same_company = copy.deepcopy(company_details)
    duplicated_same_company[3]["foresight_links"].append(
        _foresight_link(
            title="Second Finance & Ownership signal on same bank",
            url="https://eba.example.eu/second-finance-signal",
            fp_theme="Finance & Ownership",
            kb_dimension="Values",
        )
    )

    coverage = compute_foresight_coverage(duplicated_same_company)

    assert len(THEME_CATEGORIES) == 30
    assert coverage["total"] == 30
    assert coverage["covered"] == 2  # Artificial Intelligence (3 companies) + Data & Digitalisation (3 companies)
    assert coverage["pct"] == 6.7
    assert len(coverage["themes_detail"]) == 30

    by_theme = {row["theme"]: row for row in coverage["themes_detail"]}
    assert by_theme["Artificial Intelligence"] == {
        "theme": "Artificial Intelligence",
        "category": "Technology & Digital",
        "signal_count": 3,
    }
    assert by_theme["Data & Digitalisation"] == {
        "theme": "Data & Digitalisation",
        "category": "Technology & Digital",
        "signal_count": 3,
    }
    assert by_theme["Finance & Ownership"]["signal_count"] == 1
    assert by_theme["Population & Demographics"]["signal_count"] == 0


def test_compute_market_intelligence_end_to_end_golden_contract(monkeypatch):
    monkeypatch.delenv("FAVI_SEGMENT_PERCENTILE", raising=False)
    monkeypatch.delenv("CURRENT_VULN_SEGMENT_PERCENTILE", raising=False)

    dashboard_data, company_details = _build_golden_portfolio()
    frozen_dashboard = copy.deepcopy(dashboard_data)
    frozen_details = copy.deepcopy(company_details)

    analysis_result = run_analysis(dashboard_data)
    assert "error" not in analysis_result
    assert analysis_result["n"] == 5
    assert len(analysis_result["reality_vs_financial"]["residuals"]) == 5

    mi = compute_market_intelligence(dashboard_data, company_details, analysis_result)
    mi_repeat = compute_market_intelligence(
        copy.deepcopy(frozen_dashboard),
        copy.deepcopy(frozen_details),
        copy.deepcopy(analysis_result),
    )

    # Pure determinism & caller non-mutation
    assert mi == mi_repeat
    assert dashboard_data == frozen_dashboard
    assert company_details == frozen_details

    assert mi["company_count"] == 5
    assert mi["detailed_count"] == 5
    assert [t["theme"] for t in mi["top_foresights"][:2]] == [
        "Artificial Intelligence",
        "Data & Digitalisation",
    ]
    assert len(mi["top_causes"]) >= 4
    # `gap_patterns` and `demand_intensity` include only the 5 `KB_DIMENSIONS` (never `"Business Model"`).
    assert {g["dimension"] for g in mi["gap_patterns"]} == set(KB_DIMENSIONS)
    assert {d["dimension"] for d in mi["demand_intensity"]} == set(KB_DIMENSIONS)

    archetypes_by_domain = {row["domain"]: row for row in mi["archetype_flow"]}
    assert archetypes_by_domain["Industrial & Manufacturing"] == {
        "domain": "Industrial & Manufacturing",
        "thriving": 1,
        "modernizing": 0,
        "cash_cow": 1,
        "at_risk": 1,
        "total": 3,
    }
    assert archetypes_by_domain["Telecom & Digital Services"]["modernizing"] == 1
    assert archetypes_by_domain["Financial Services"]["thriving"] == 1

    assert mi["foresight_coverage"]["covered"] == 2
    assert len(mi["vulnerable_companies"]) >= 3
    vuln_names = [v["name"] for v in mi["vulnerable_companies"]]
    assert "Outokumpu Oyj" in vuln_names
    assert "Valmet Oyj" in vuln_names

    assert mi["favi_cutoffs"]["source"] == "percentile:favi=p50,current=p50"
    assert sum(mi["favi_segments"].values()) == 5
    favi_by_name = {c["name"]: c for c in mi["favi_companies"]}
    # Konecranes has 3 validated priority links across 3 dimensions -> geometric FAVI.
    assert favi_by_name["Konecranes Oyj"]["formula"] == "geometric"
    assert favi_by_name["Konecranes Oyj"]["dimensions_covered"] == 3
    # Outokumpu's sole link has `validated=False` -> FPS=0.0 and additive fallback,
    # while `foresight_count` still records `len(foresight_links) == 1`.
    assert favi_by_name["Outokumpu Oyj"]["formula"] == "additive"
    assert favi_by_name["Outokumpu Oyj"]["fps"] == 0.0
    assert favi_by_name["Outokumpu Oyj"]["foresight_count"] == 1

    assert len(mi["opportunity_map"]["company_rankings"]) == 5
    assert len(mi["opportunity_map"]["domain_rankings"]) == 3
    # Opportunity score equals FAVI `demand_creation_potential`; proposal clusters are context only.
    for row in mi["opportunity_map"]["company_rankings"]:
        expected_dcp = favi_by_name[row["name"]]["demand_creation_potential"]
        assert row["opportunity_score"] == expected_dcp
        assert row["demand_creation_potential"] == expected_dcp

    sweden = mi["market_cohorts"]["sweden_summary"]
    assert sweden["matched_count"] == 1
    assert sweden["matched_companies"][0]["scanned_name"] == "Swedbank"
    assert [r["name"] for r in sweden["opportunities"]] == ["Swedbank"]
    assert [r["name"] for r in sweden["favi_companies"]] == ["Swedbank"]


# ═══════════════════════════════════════════════════════════════════════════════
# 3. EXPLICIT MARKET INTELLIGENCE vs. OBSERVATORY ELIGIBILITY CONTRACT
# ═══════════════════════════════════════════════════════════════════════════════

def test_market_intelligence_vs_observatory_foresight_eligibility_contract(monkeypatch):
    """Freeze the current 3-way foresight eligibility behavior before future MI hardening.

    Current production behavior across the exact same `companies/{slug}.json` records:
      - `/api/market-intelligence` passes raw `company_details` into
        `compute_market_intelligence()`. All theme/dimension counters in
        `market_intelligence.py` count every dict link with a mapped `fp_theme` /
        `kb_dimension`, regardless of `foresight_context_eligible`,
        `foresight_signal_status`, `source_audit_status`, `source_readable`,
        `validated`, or `is_priority_dimension`.
      - Inside `compute_market_intelligence()`, `foresight_vulnerability.compute_fps()`
        filters only on `validated` (default True) and `is_priority_dimension`
        (default True), still ignoring `foresight_context_eligible`,
        `source_audit_status`, and `source_readable`.
      - `foresight_observatory._load_all_data()` pre-filters `company_details` via
        `_filter_company_foresight()`, requiring `foresight_context_eligible is True`,
        no `dead`/`unreadable`/`excluded`/`rejected` token in either
        `foresight_signal_status` or `source_audit_status`, and
        `source_readable is not False` before invoking `compute_market_intelligence()`.
    """
    monkeypatch.delenv("FAVI_SEGMENT_PERCENTILE", raising=False)
    monkeypatch.delenv("CURRENT_VULN_SEGMENT_PERCENTILE", raising=False)

    dashboard_data, raw_company_details = _build_golden_portfolio()
    analysis_result = run_analysis(dashboard_data)

    filtered_company_details, obs_stats = observatory._filter_company_foresight(raw_company_details)

    # Caller records are never mutated by Observatory filtering.
    assert sum(len(c["foresight_links"]) for c in raw_company_details) == 11
    assert obs_stats == {
        "total_records": 11,
        "eligible_records": 7,
        "excluded_records": 4,
        "exclusion_reasons": {
            "source_dead": 1,          # Valmet: Climate Change (`source_audit_status="source_dead"`)
            "not_context_eligible": 2,  # Elisa display_only + Outokumpu rejected
            "source_unreadable": 1,    # Swedbank: Public Governance (`source_readable=False`)
        },
    }

    # 1. `compute_top_foresights` divergence:
    raw_top = {row["theme"]: row["count"] for row in compute_top_foresights(raw_company_details)}
    obs_top = {row["theme"]: row["count"] for row in compute_top_foresights(filtered_company_details)}
    assert raw_top == {
        "Artificial Intelligence": 3,
        "Data & Digitalisation": 3,
        "Industry & Manufacturing": 1,
        "Climate Change": 1,
        "Finance & Ownership": 1,
        "Public Governance": 1,
        "Energy": 1,
    }
    assert obs_top == {
        "Artificial Intelligence": 3,
        "Data & Digitalisation": 2,
        "Industry & Manufacturing": 1,
        "Finance & Ownership": 1,
    }

    # 2. `compute_cross_impact` divergence:
    assert sum(c["count"] for c in compute_cross_impact(raw_company_details)) == 11
    assert sum(c["count"] for c in compute_cross_impact(filtered_company_details)) == 7

    # 3. `compute_domain_dimension_matrix` divergence:
    assert sum(c["count"] for c in compute_domain_dimension_matrix(raw_company_details, dashboard_data)) == 11
    assert sum(c["count"] for c in compute_domain_dimension_matrix(filtered_company_details, dashboard_data)) == 7

    # 4. `compute_industry_radar` divergence:
    raw_radar = {r["domain"]: r["total_signals"] for r in compute_industry_radar(raw_company_details, dashboard_data)}
    obs_radar = {r["domain"]: r["total_signals"] for r in compute_industry_radar(filtered_company_details, dashboard_data)}
    assert raw_radar == {
        "Industrial & Manufacturing": 7,
        "Telecom & Digital Services": 2,
        "Financial Services": 2,
    }
    assert obs_radar == {
        "Industrial & Manufacturing": 5,
        "Telecom & Digital Services": 1,
        "Financial Services": 1,
    }

    # 5. `compute_foresight_coverage` divergence (`Data & Digitalisation` crosses the >=3 threshold):
    raw_cov = compute_foresight_coverage(raw_company_details)
    obs_cov = compute_foresight_coverage(filtered_company_details)
    assert raw_cov["covered"] == 2
    assert raw_cov["pct"] == 6.7
    assert obs_cov["covered"] == 1
    assert obs_cov["pct"] == 3.3

    # 6. Full `compute_market_intelligence` + FAVI divergence:
    raw_mi = compute_market_intelligence(dashboard_data, raw_company_details, analysis_result)
    obs_mi = compute_market_intelligence(dashboard_data, filtered_company_details, analysis_result)

    raw_favi = {c["name"]: c for c in raw_mi["favi_companies"]}
    obs_favi = {c["name"]: c for c in obs_mi["favi_companies"]}

    # Swedbank's unreadable `Public Governance` link has `validated=True` and
    # `is_priority_dimension=True`, so raw MI feeds it into FAVI (`fps=86.2`, 2 dims),
    # whereas Observatory strips it first (`fps=74.9`, 1 dim).
    assert raw_favi["Swedbank"]["foresight_count"] == 2
    assert raw_favi["Swedbank"]["dimensions_covered"] == 2
    assert obs_favi["Swedbank"]["foresight_count"] == 1
    assert obs_favi["Swedbank"]["dimensions_covered"] == 1
    assert raw_favi["Swedbank"]["fps"] > obs_favi["Swedbank"]["fps"]

    # Elisa's legacy `display_only` link (`foresight_context_eligible=False`, `validated=True`)
    # stays in raw MI (`foresight_count=2`) and is removed by Observatory (`foresight_count=1`).
    assert raw_favi["Elisa Oyj"]["foresight_count"] == 2
    assert obs_favi["Elisa Oyj"]["foresight_count"] == 1

    # Outokumpu's `validated=False` link already had `fps=0.0` in raw FAVI,
    # but still inflated `foresight_count` (1 -> 0) and `score_confidence` before Observatory filtering.
    assert raw_favi["Outokumpu Oyj"]["fps"] == 0.0
    assert raw_favi["Outokumpu Oyj"]["foresight_count"] == 1
    assert obs_favi["Outokumpu Oyj"]["foresight_count"] == 0
    assert raw_favi["Outokumpu Oyj"]["score_confidence"]["score"] > obs_favi["Outokumpu Oyj"]["score_confidence"]["score"]


def test_company_index_casing_and_unavailable_reality_mi_contracts(monkeypatch):
    """Freeze two subtle cross-record edge contracts in `compute_market_intelligence`:

    1. Case-sensitivity asymmetry across MI joins: `compute_domain_dimension_matrix`
       and `compute_industry_radar` match `companyName` case-insensitively (`.lower()`),
       whereas `compute_favi_index` and `compute_opportunity_map` currently join
       `dashboard_data["companyName"]` to `company_details["companyName"]` with
       exact case-sensitive keys (`favi_by_company.get(name, {})`).
    2. Partial scan with `current_reality_unavailable=True` (`reality_score=None`):
       skipped by `run_analysis`, `compute_archetype_flow`, `compute_market_health`,
       `compute_vulnerable_companies`, and `compute_demand_intensity`, while
       `compute_gap_patterns` still includes its `intent_breakdown` scores in
       `intent_vals` without adding to `reality_vals`.
    """
    monkeypatch.delenv("FAVI_SEGMENT_PERCENTILE", raising=False)
    monkeypatch.delenv("CURRENT_VULN_SEGMENT_PERCENTILE", raising=False)

    dashboard_data, company_details = _build_golden_portfolio()

    # Append a 6th company whose current reality was unavailable (`reality_score=None`),
    # and alter `"Elisa Oyj"` -> `"ELISA OYJ"` in `dashboard_data` only.
    cautious_detail = {
        "id": "scan-golden-unavailable-006",
        "companyName": "Unavailable Reality Oy",
        "companyUrl": "https://unavailable.example.fi/",
        "domain": "Telecom & Digital Services",
        "revenue_avg_3yr": None,
        "employees": None,
        "ownership": None,
        "hq_country": "Finland",
        "intent_score": 90,
        "reality_score": None,
        "reality_gap": None,
        "confidence_score": 35,
        "current_reality_unavailable": True,
        "reality_score_valid": False,
        "classification_valid": False,
        "archetype": None,
        "intent_breakdown": _intent_breakdown(
            operating_model=90,
            service_portfolio=90,
            values=90,
            strategy=90,
            business_model=90,
            ai_readiness=90,
        ),
        "reality_checklist": [],
        "financial_index": None,
        "financial_sub_scores": {},
        "foresight_links": [],
        "ai_verification": {"verified": False},
        "scan_quality": "partial",
        "lastUpdated": "2026-11-10T10:40:00+00:00",
        "uploaded_docs": [],
    }
    cautious_index_row = _build_golden_index_row(
        cautious_detail,
        details_url="https://blob.example.invalid/companies/company_6.json",
        include_buying_potential_flags=False,
    )

    skewed_dashboard = [
        (dict(row, companyName="ELISA OYJ") if row["companyName"] == "Elisa Oyj" else dict(row))
        for row in dashboard_data
    ] + [cautious_index_row]
    extended_details = copy.deepcopy(company_details) + [cautious_detail]

    analysis_result = run_analysis(skewed_dashboard)
    # `run_analysis` skips `reality_score=None`, keeping n=5.
    assert analysis_result["n"] == 5

    mi = compute_market_intelligence(skewed_dashboard, extended_details, analysis_result)
    assert mi["company_count"] == 6
    assert mi["detailed_count"] == 6

    # `archetype_flow` and `demand_intensity` exclude `reality_score=None` (5 companies),
    # whereas `gap_patterns` counts `intent_breakdown` across all 6 companies.
    assert sum(row["total"] for row in mi["archetype_flow"]) == 5
    assert all(row["total_companies"] == 5 for row in mi["demand_intensity"])
    assert all(row["company_count"] == 6 for row in mi["gap_patterns"])

    # `industry_radar` resolves `"Elisa Oyj"` to `"Telecom & Digital Services"` via `.lower()`,
    # while `opportunity_map` misses `"Elisa Oyj"` under `"ELISA OYJ"` because of exact-case lookup.
    radar_by_domain = {r["domain"]: r["total_signals"] for r in mi["industry_radar"]}
    assert radar_by_domain["Telecom & Digital Services"] == 2
    opp_by_name = {r["name"]: r for r in mi["opportunity_map"]["company_rankings"]}
    assert opp_by_name["ELISA OYJ"]["opportunity_score"] == 0.0
    assert opp_by_name["ELISA OYJ"]["foresight_pressure"] == 0.0

