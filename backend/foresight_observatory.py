"""
Foresight Observatory — Three-Layer Strategic Analysis Engine
=============================================================
Synthesizes existing Scanner data into macro-level strategic intelligence:
  1. Global Force Landscape — futurologist's briefing on converging forces
  2. Finnish Economy Force Map — how forces reshape Finnish business
  3. Domain Force Dossiers — per-domain analysis with timescale mapping

Model pipeline:
  - GPT-5.5 Pro: Layer 1 and Layer 2 strategic analysis
  - Claude Opus 4.8: Layer 3 domain dossiers
  - GPT-5.5 high / Sonnet 4.6: Independent verification

All data comes from existing DB — no new scanning or web search required.
"""

import json
import hashlib
import os
import re
import time
import statistics
from collections import Counter, defaultdict
from datetime import datetime

import anthropic

from llm_clients import (
    GPT_PRO_MODEL,
    GPT_REASONING_MODEL,
    call_json_model_chain,
    call_openai_text,
    parse_json_object,
)
from foresight_guardrails import build_foresight_guardrails, sanitize_foresight_text, validate_foresight_text, sanitize_future_insight
from storage import BlobStorage
from market_intelligence import (
    compute_market_intelligence, THEME_CATEGORIES, ALL_CATEGORIES, KB_DIMENSIONS,
)
from analyze_scores import run_analysis, classify_archetype


# ═══════════════════════════════════════════════════════════════════════════════
# SYSTEM PROMPT — Shared across all three outputs
# ═══════════════════════════════════════════════════════════════════════════════

SYSTEM_ROLE = """You are a synthesis of three expert perspectives: a futurologist who reads weak signals, a market analyst who grounds insight in evidence, and a strategic designer who frames actionable futures.

CRITICAL FRAMING RULE — Technology is context, never conclusion:
- Frame every force through the lens of organizational thinking, adaptability, and future-readiness.
- NEVER say "companies need AI/cloud/digital/technology" — instead describe what thinking capabilities, organizational structures, or strategic postures enable companies to create their own race.
- The ultimate strategic question is not "how do we respond to these forces?" but "how do we create conditions where we define the game rather than react to it?"
- Focus on: thinking and adaptability, future-drivenness, innovative thinking, organizational learning, what companies need to STOP doing, creating "Own Race" vs. participating reactively.

EVIDENCE RULE — Every claim must trace to the data provided. Include specific numbers when available (e.g., "60% of Finnish industrial companies sit in Cash Cow archetype"). Do not invent statistics.
EVIDENCE TRACE RULE — When pre-computed findings include evidence IDs, preserve the relevant IDs in parentheses after material claims so the interpretation remains auditable."""

OBSERVATORY_DOSSIER_VERSION = "observatory_dossier_v1"
OBSERVATORY_L1_MODEL = GPT_PRO_MODEL
OBSERVATORY_L2_MODEL = GPT_PRO_MODEL
OBSERVATORY_L3_MODEL = "claude-opus-4-8"


# ═══════════════════════════════════════════════════════════════════════════════
# DATA AGGREGATION
# ═══════════════════════════════════════════════════════════════════════════════

def _aggregate_kb_signals(all_kb: dict) -> dict:
    """Aggregate all KB signals preserving full source context for LLM synthesis.

    Keeps full descriptions, lifecycle stages, momentum, confidence, and source
    credibility so the LLM can ground its analysis in actual source content
    rather than just metadata labels.
    """
    by_category = defaultdict(list)
    by_timeframe = defaultdict(int)
    by_force_type = defaultdict(int)
    by_lifecycle = defaultdict(int)
    by_momentum = defaultdict(int)
    by_credibility = defaultdict(int)
    by_theme_momentum = defaultdict(lambda: {"accelerating": 0, "stable": 0, "decelerating": 0, "total": 0})
    total = 0
    total_records = 0
    excluded_records = 0
    exclusion_reasons = Counter()

    for theme_slug, theme_data in all_kb.items():
        signals = theme_data.get("signals", [])
        for sig in signals:
            total_records += 1
            eligible, reason = _is_eligible_foresight_context(sig)
            if not eligible:
                excluded_records += 1
                exclusion_reasons[reason] += 1
                continue
            fp_theme = sig.get("fp_theme", "")
            category = THEME_CATEGORIES.get(fp_theme, "Unknown")
            evidence_id = _evidence_id("KB", sig.get("source_url"), sig.get("title"), fp_theme)
            by_category[category].append({
                "evidence_id": evidence_id,
                "theme": fp_theme,
                "dimension": sig.get("kb_dimension", ""),
                "dimensions_affected": sig.get("kb_dimensions_affected", []),
                "title": sig.get("title", ""),
                "description": sig.get("description", ""),  # Full description, not truncated
                "force_type": sig.get("force_type", ""),
                "timeframe": sig.get("timeframe", ""),
                "credibility": sig.get("source_credibility", "medium"),
                "confidence": sig.get("confidence", "medium"),
                "lifecycle_stage": sig.get("lifecycle_stage", ""),
                "trend_momentum": sig.get("trend_momentum", ""),
                "industries_affected": sig.get("industries_affected", []),
                "source_url": sig.get("source_url", ""),
                "source_count": sig.get("source_count", 0),
                "foresight_context_eligible": sig.get("foresight_context_eligible") is True,
                "theme_match": sig.get("theme_match") is True,
                "trend_supported": sig.get("trend_supported") is True,
                "source_readable": sig.get("source_readable") is not False,
                "source_registry_status": sig.get("source_registry_status", ""),
            })
            by_timeframe[sig.get("timeframe", "unknown")] += 1
            by_force_type[sig.get("force_type", "unknown")] += 1
            by_lifecycle[sig.get("lifecycle_stage", "unknown")] += 1
            momentum = sig.get("trend_momentum", "unknown")
            by_momentum[momentum] += 1
            by_credibility[sig.get("source_credibility", "unknown")] += 1
            total += 1

            # Per-theme momentum split (for the Theme Momentum chart in Future Demand).
            if fp_theme:
                if momentum == "accelerating":
                    by_theme_momentum[fp_theme]["accelerating"] += 1
                elif momentum == "decelerating":
                    by_theme_momentum[fp_theme]["decelerating"] += 1
                elif momentum == "stable":
                    by_theme_momentum[fp_theme]["stable"] += 1
                by_theme_momentum[fp_theme]["total"] += 1

    # Count accelerating vs decelerating
    accelerating = by_force_type.get("Probable/Emerging", 0) + by_force_type.get("Plausible/Maturing", 0)
    decelerating = by_force_type.get("Weakening/Legacy", 0) + by_force_type.get("Weakening/Declining", 0)

    # Top-15 themes by total signal count, with momentum breakdown.
    theme_momentum_list = sorted(
        (
            {"theme": theme, **counts}
            for theme, counts in by_theme_momentum.items()
        ),
        key=lambda x: x["total"],
        reverse=True,
    )[:15]

    return {
        "total_signals": total,
        "total_records": total_records,
        "eligible_records": total,
        "excluded_records": excluded_records,
        "exclusion_reasons": dict(exclusion_reasons),
        "by_category": dict(by_category),
        "by_timeframe": dict(by_timeframe),
        "by_force_type": dict(by_force_type),
        "by_lifecycle": dict(by_lifecycle),
        "by_momentum": dict(by_momentum),
        "by_credibility": dict(by_credibility),
        "by_theme_momentum": {t: dict(c) for t, c in by_theme_momentum.items()},
        "theme_momentum_top": theme_momentum_list,
        "accelerating": accelerating,
        "decelerating": decelerating,
        "theme_count": len(all_kb),
        "high_confidence_count": by_credibility.get("high", 0),
    }


def _evidence_id(prefix: str, *parts) -> str:
    raw = "|".join(str(part or "") for part in parts)
    return f"{prefix}-{hashlib.sha1(raw.encode('utf-8')).hexdigest()[:10].upper()}"


def _is_eligible_foresight_context(record: dict) -> tuple[bool, str]:
    """Use only explicit, readable foresight context; never infer eligibility."""
    if not isinstance(record, dict):
        return False, "invalid_record"
    if record.get("foresight_context_eligible") is not True:
        return False, "not_context_eligible"
    status = str(record.get("foresight_signal_status") or record.get("source_audit_status") or "").lower()
    if any(token in status for token in ("dead", "unreadable", "excluded", "rejected")):
        return False, status or "excluded"
    if record.get("source_readable") is False:
        return False, "source_unreadable"
    return True, "eligible"


def _filter_company_foresight(company_details: list) -> tuple[list, dict]:
    """Create an Observatory-only view with explicit company foresight context."""
    filtered = []
    total = eligible = 0
    reasons = Counter()
    for company in company_details:
        item = dict(company)
        kept = []
        for link in company.get("foresight_links") or []:
            total += 1
            ok, reason = _is_eligible_foresight_context(link)
            if ok:
                kept.append(link)
                eligible += 1
            else:
                reasons[reason] += 1
        item["foresight_links"] = kept
        filtered.append(item)
    return filtered, {
        "total_records": total,
        "eligible_records": eligible,
        "excluded_records": total - eligible,
        "exclusion_reasons": dict(reasons),
    }


def _load_all_data(storage: BlobStorage):
    """Load all data needed for observatory generation."""
    print("   [Observatory] Loading all data...")

    # 1. KB signals
    all_kb = storage.get_all_foresight_kb()
    kb_aggregate = _aggregate_kb_signals(all_kb)
    print(f"   [Observatory] KB: {kb_aggregate['total_signals']} signals across {kb_aggregate['theme_count']} themes")

    # 2. Dashboard + company details
    dashboard_data = storage.retrieve_latest_data("index/summary_list") or []
    company_details = []
    for item in dashboard_data:
        detail = storage.get_company_data(item.get("companyName", ""))
        if detail:
            company_details.append(detail)
    company_details, company_foresight_stats = _filter_company_foresight(company_details)
    print(
        f"   [Observatory] Companies: {len(dashboard_data)} summary, {len(company_details)} detailed; "
        f"company foresight {company_foresight_stats['eligible_records']}/{company_foresight_stats['total_records']} eligible"
    )

    # 3. Market intelligence
    analysis_result = run_analysis(dashboard_data) if dashboard_data else {}
    mi = compute_market_intelligence(dashboard_data, company_details, analysis_result) if dashboard_data else {}

    return {
        "all_kb": all_kb,
        "kb_aggregate": kb_aggregate,
        "dashboard_data": dashboard_data,
        "company_details": company_details,
        "company_foresight_stats": company_foresight_stats,
        "market_intel": mi,
    }


def _latest_snapshot_date(records: list) -> str | None:
    candidates = []
    for record in records:
        if not isinstance(record, dict):
            continue
        for key in ("updated_at", "scan_date", "scanned_at", "timestamp", "generated_at", "date"):
            value = record.get(key)
            if value:
                candidates.append(str(value))
    return max(candidates) if candidates else None


def _build_input_manifest(data: dict) -> dict:
    kb = data["kb_aggregate"]
    company_stats = data["company_foresight_stats"]
    kb_signals = [
        signal
        for theme_data in (data.get("all_kb") or {}).values()
        for signal in (theme_data.get("signals") or [])
        if isinstance(signal, dict)
    ]
    return {
        "generated_at": datetime.utcnow().isoformat(),
        "contract_version": "observatory_evidence_v1",
        "central_foresight_kb": {
            "themes_loaded": kb.get("theme_count", 0),
            "latest_signal_snapshot": _latest_snapshot_date(kb_signals),
            "records_total": kb.get("total_records", 0),
            "records_eligible": kb.get("eligible_records", 0),
            "records_excluded": kb.get("excluded_records", 0),
            "exclusion_reasons": kb.get("exclusion_reasons", {}),
            "eligibility_contract": "foresight_context_eligible=true",
        },
        "company_portfolio": {
            "summary_records": len(data.get("dashboard_data") or []),
            "detailed_records": len(data.get("company_details") or []),
            "latest_snapshot_date": _latest_snapshot_date(data.get("company_details") or []),
            "foresight_records_total": company_stats.get("total_records", 0),
            "foresight_records_eligible": company_stats.get("eligible_records", 0),
            "foresight_records_excluded": company_stats.get("excluded_records", 0),
            "foresight_exclusion_reasons": company_stats.get("exclusion_reasons", {}),
        },
        "market_intelligence": {"recomputed_from_current_company_snapshot": True},
        "source_boundaries": {
            "macro_context": "independent central Foresight KB",
            "portfolio_patterns": "stored Company Summary data",
            "company_evidence_db": "not read directly by Observatory",
            "company_source_ledger": "not read by Observatory",
            "people_and_influence": "not analyzed by Observatory; Buying Insight owns this layer",
        },
    }


def _build_structured_findings(kb_aggregate: dict, mi: dict) -> list:
    findings = []
    all_signals = [
        signal
        for signals in kb_aggregate.get("by_category", {}).values()
        for signal in signals
    ]
    all_signals.sort(
        key=lambda signal: (
            signal.get("credibility") == "high",
            signal.get("trend_momentum") == "accelerating",
            signal.get("source_count", 0),
        ),
        reverse=True,
    )
    for signal in all_signals[:6]:
        findings.append({
            "finding_id": _evidence_id("FINDING", signal.get("evidence_id"), signal.get("title")),
            "finding_type": "macro_force",
            "statement": signal.get("title") or "Unnamed macro force",
            "evidence_ids": [signal.get("evidence_id")],
            "confidence_basis": "eligible central KB signal",
        })

    for gap in (mi.get("gap_patterns") or [])[:5]:
        statement = (
            f"{gap.get('dimension', 'Unknown dimension')} has an average intent-reality gap "
            f"of {gap.get('avg_gap', 'n/a')} across {gap.get('company_count', 0)} companies."
        )
        findings.append({
            "finding_id": _evidence_id("FINDING", "portfolio_gap", statement),
            "finding_type": "portfolio_pattern",
            "statement": statement,
            "evidence_ids": [_evidence_id("PORTFOLIO", gap.get("dimension"), gap.get("company_count"))],
            "confidence_basis": "computed company-portfolio aggregate",
        })
    return findings


def _format_structured_findings(findings: list) -> str:
    if not findings:
        return "No pre-computed findings available."
    return "\n".join(
        f"- [{item['finding_id']}] {item['statement']} Evidence: {', '.join(item['evidence_ids'])}"
        for item in findings
    )


def _top_macro_evidence(kb_aggregate: dict, per_category: int = 5) -> list[dict]:
    """Select a bounded, evidence-ID-preserving macro packet for analysis."""
    selected = []
    for category, signals in (kb_aggregate.get("by_category") or {}).items():
        ranked = sorted(
            signals,
            key=lambda signal: (
                signal.get("credibility") == "high",
                signal.get("trend_momentum") == "accelerating",
                signal.get("source_count", 0),
            ),
            reverse=True,
        )[:per_category]
        for signal in ranked:
            selected.append({
                "evidence_id": signal.get("evidence_id"),
                "category": category,
                "theme": signal.get("theme"),
                "title": signal.get("title"),
                "description": signal.get("description"),
                "force_type": signal.get("force_type"),
                "timeframe": signal.get("timeframe"),
                "lifecycle_stage": signal.get("lifecycle_stage"),
                "trend_momentum": signal.get("trend_momentum"),
                "source_credibility": signal.get("credibility"),
                "source_url": signal.get("source_url"),
                "dimensions_affected": signal.get("dimensions_affected") or [],
                "industries_affected": signal.get("industries_affected") or [],
            })
    return selected


def _build_l1_evidence_dossier(
    kb_aggregate: dict,
    mi: dict,
    structured_findings: list,
    input_manifest: dict,
) -> dict:
    """Compile the locked macro-evidence input for Layer 1."""
    macro_evidence = _top_macro_evidence(kb_aggregate)
    macro_ids = {item.get("evidence_id") for item in macro_evidence if item.get("evidence_id")}
    macro_findings = [
        finding for finding in structured_findings
        if finding.get("finding_type") == "macro_force"
        and any(evidence_id in macro_ids for evidence_id in finding.get("evidence_ids") or [])
    ]
    macro_cross_impact = []
    for signal in macro_evidence:
        dimensions = signal.get("dimensions_affected") or []
        if len(dimensions) >= 2:
            macro_cross_impact.append({
                "theme": signal.get("theme"),
                "dimensions": dimensions,
                "evidence_ids": [signal.get("evidence_id")],
                "candidate_type": "macro_force_convergence",
            })

    return {
        "schema_version": OBSERVATORY_DOSSIER_VERSION,
        "layer": "L1_global_force_landscape",
        "locked_at": datetime.utcnow().isoformat(),
        "evidence_contract": {
            "allowed_claim_types": ["macro_force", "cross_impact", "strategic_inference"],
            "company_specific_claims_allowed": False,
            "material_claims_require_evidence_ids": True,
            "strategic_inference_must_be_labelled": True,
        },
        "input_manifest": input_manifest.get("central_foresight_kb", {}),
        "signal_summary": {
            "total_eligible": kb_aggregate.get("eligible_records", 0),
            "themes": kb_aggregate.get("theme_count", 0),
            "accelerating": kb_aggregate.get("accelerating", 0),
            "decelerating": kb_aggregate.get("decelerating", 0),
            "by_timeframe": kb_aggregate.get("by_timeframe", {}),
            "by_force_type": kb_aggregate.get("by_force_type", {}),
            "by_lifecycle": kb_aggregate.get("by_lifecycle", {}),
            "by_momentum": kb_aggregate.get("by_momentum", {}),
        },
        "macro_evidence": macro_evidence,
        "cross_impact_candidates": macro_cross_impact[:30],
        "precomputed_findings": macro_findings,
        "quality_notes": {
            "excluded_kb_records": kb_aggregate.get("excluded_records", 0),
            "exclusion_reasons": kb_aggregate.get("exclusion_reasons", {}),
            "freshness_gap": "Use source dates when present; absence of a date lowers certainty.",
        },
    }


def _allowed_evidence_ids(dossier: dict) -> set[str]:
    ids: set[str] = set()

    def collect(value):
        if isinstance(value, dict):
            if value.get("evidence_id"):
                ids.add(str(value["evidence_id"]))
            if value.get("finding_id"):
                ids.add(str(value["finding_id"]))
            ids.update(str(item) for item in value.get("evidence_ids") or [] if item)
            for child in value.values():
                collect(child)
        elif isinstance(value, list):
            for child in value:
                collect(child)

    collect(dossier)
    return ids


def _extract_layer_claims(analysis: str, dossier: dict, layer: str) -> list[dict]:
    """Convert narrative output into a bounded evidence-ID-backed handoff."""
    allowed_ids = _allowed_evidence_ids(dossier)
    prompt = f"""Extract the major claims from this {layer} strategic analysis for use by the next Observatory layer.

Return JSON only:
{{"claims": [{{"claim_id": "{layer}-CLAIM-1", "statement": "...", "claim_type": "macro_evidence|portfolio_evidence|strategic_inference", "evidence_ids": ["..."], "confidence": 0, "limitations": "..."}}]}}

Rules:
- Return at most 10 claims.
- Evidence IDs must come from this allowed list: {json.dumps(sorted(allowed_ids))}.
- A strategic inference must still cite the evidence IDs it interprets.
- Drop claims that cannot be tied to an allowed evidence ID.
- Confidence is 0-100.

ANALYSIS:
{analysis}
"""
    try:
        raw, model_used = call_json_model_chain(
            prompt,
            (GPT_REASONING_MODEL, "claude-sonnet-4-6"),
            max_tokens=2600,
            timeout=120,
            reasoning_effort="high",
        )
        parsed = parse_json_object(raw)
        claims = []
        for index, item in enumerate(parsed.get("claims") or []):
            if not isinstance(item, dict) or not str(item.get("statement") or "").strip():
                continue
            evidence_ids = [eid for eid in item.get("evidence_ids") or [] if eid in allowed_ids]
            if not evidence_ids:
                continue
            claims.append({
                "claim_id": item.get("claim_id") or f"{layer}-CLAIM-{index + 1}",
                "statement": str(item["statement"]).strip(),
                "claim_type": item.get("claim_type") or "strategic_inference",
                "evidence_ids": evidence_ids,
                "confidence": max(0, min(100, int(item.get("confidence") or 50))),
                "limitations": str(item.get("limitations") or "").strip(),
                "extraction_model": model_used,
            })
        if claims:
            return claims[:10]
    except Exception as exc:
        print(f"   [Observatory] {layer} claim extraction unavailable: {exc}")

    # Deterministic fallback keeps the layer handoff structured and auditable.
    fallback = []
    for index, finding in enumerate(dossier.get("precomputed_findings") or []):
        evidence_ids = [eid for eid in finding.get("evidence_ids") or [] if eid in allowed_ids]
        if evidence_ids:
            fallback.append({
                "claim_id": f"{layer}-FALLBACK-{index + 1}",
                "statement": finding.get("statement"),
                "claim_type": finding.get("finding_type") or "strategic_inference",
                "evidence_ids": evidence_ids,
                "confidence": 50,
                "limitations": "Deterministic handoff because model claim extraction was unavailable.",
                "extraction_model": "deterministic",
            })
    return fallback[:10]


def _build_l2_evidence_dossier(
    mi: dict,
    l1_claims: list[dict],
    structured_findings: list,
    input_manifest: dict,
) -> dict:
    """Compile Finnish portfolio evidence and validated L1 claims for Layer 2."""
    portfolio_findings = [
        finding for finding in structured_findings
        if finding.get("finding_type") == "portfolio_pattern"
    ]
    market_health = [
        {**item, "evidence_id": _evidence_id("PORTFOLIO-HEALTH", item.get("domain"))}
        for item in (mi.get("market_health") or [])
    ]
    archetype_flow = [
        {**item, "evidence_id": _evidence_id("PORTFOLIO-ARCHETYPE", item.get("domain"))}
        for item in (mi.get("archetype_flow") or [])
    ]
    gap_patterns = [
        {**item, "evidence_id": _evidence_id("PORTFOLIO-GAP", item.get("dimension"), item.get("company_count"))}
        for item in (mi.get("gap_patterns") or [])
    ]
    top_foresights = [
        {**item, "evidence_id": _evidence_id("PORTFOLIO-FORESIGHT", item.get("theme"), item.get("count"))}
        for item in (mi.get("top_foresights") or [])[:15]
    ]
    favi_segments = {
        segment: {
            "evidence_id": _evidence_id("PORTFOLIO-FAVI", segment),
            "count": count,
        }
        for segment, count in (mi.get("favi_segments") or {}).items()
    }
    cross_impact = [
        {**item, "evidence_id": _evidence_id(
            "PORTFOLIO-CROSS-IMPACT",
            item.get("theme") or item.get("source_theme"),
            item.get("dimension") or item.get("target_dimension"),
        )}
        for item in (mi.get("cross_impact") or [])[:30]
    ]
    return {
        "schema_version": OBSERVATORY_DOSSIER_VERSION,
        "layer": "L2_finnish_economy_force_map",
        "locked_at": datetime.utcnow().isoformat(),
        "evidence_contract": {
            "macro_input": "structured L1 claims only; the L1 essay is intentionally excluded",
            "portfolio_claims_require_computed_metrics": True,
            "material_claims_require_evidence_ids": True,
            "strategic_inference_must_be_labelled": True,
        },
        "input_manifest": input_manifest.get("company_portfolio", {}),
        "validated_l1_claims": l1_claims,
        "market_health": market_health,
        "archetype_flow": archetype_flow,
        "favi_segments": favi_segments,
        "gap_patterns": gap_patterns,
        "top_foresights": top_foresights,
        "cross_impact": cross_impact,
        "company_count": mi.get("company_count", 0),
        "detailed_count": mi.get("detailed_count", 0),
        "precomputed_findings": portfolio_findings,
    }


def _build_l3_evidence_dossier(
    domain: str,
    domain_config: dict,
    domain_companies: list[dict],
    kb_signals: list[dict],
    market_health: dict | None,
    l1_claims: list[dict],
    l2_claims: list[dict],
    structured_findings: list[dict],
) -> dict:
    """Compile one domain's evidence and upstream claim handoffs for Opus."""
    company_metrics = []
    for company in domain_companies:
        name = company.get("company_name") or company.get("companyName") or "Unknown"
        archetype = company.get("archetype")
        if not archetype and company.get("reality_score") is not None:
            comparison_score = company.get("financial_index")
            if comparison_score is None:
                comparison_score = company.get("intent_score")
            if comparison_score is not None:
                archetype = classify_archetype(
                    float(company["reality_score"]),
                    float(comparison_score),
                )
        company_metrics.append({
            "evidence_id": _evidence_id("DOMAIN-COMPANY", domain, name),
            "company_name": name,
            "reality_score": company.get("reality_score"),
            "intent_score": company.get("intent_score"),
            "financial_index": company.get("financial_index"),
            "archetype": archetype,
            "why": company.get("why"),
            "what": company.get("what"),
        })

    domain_signals = []
    for signal in kb_signals[:20]:
        domain_signals.append({
            "evidence_id": signal.get("evidence_id") or _evidence_id(
                "DOMAIN-FORESIGHT", domain, signal.get("source_url"), signal.get("title")
            ),
            "title": signal.get("title"),
            "description": signal.get("description"),
            "semantic_snippet": signal.get("semantic_snippet"),
            "theme": signal.get("fp_theme") or signal.get("theme"),
            "timeframe": signal.get("timeframe") or signal.get("time_horizon"),
            "force_type": signal.get("force_type"),
            "lifecycle_stage": signal.get("lifecycle_stage") or signal.get("s_curve_phase"),
            "trend_momentum": signal.get("trend_momentum") or signal.get("signal_direction"),
            "source_credibility": signal.get("source_credibility"),
            "source_url": signal.get("source_url"),
        })

    relevant_findings = [
        finding for finding in structured_findings
        if domain.lower() in json.dumps(finding, ensure_ascii=False).lower()
    ]
    return {
        "schema_version": OBSERVATORY_DOSSIER_VERSION,
        "layer": "L3_domain_force_dossier",
        "domain": domain,
        "locked_at": datetime.utcnow().isoformat(),
        "evidence_contract": {
            "upstream_context": "structured L1/L2 claims only; upstream essays are excluded",
            "company_metrics_are_portfolio_evidence": True,
            "foresight_signals_are_macro_context_not_company_facts": True,
            "material_claims_require_evidence_ids": True,
            "strategic_inference_must_be_labelled": True,
        },
        "domain_configuration": {
            "fp_themes": domain_config.get("fp_themes") or [],
            "priority_dimensions": domain_config.get("priority_dimensions") or [],
        },
        "validated_l1_claims": l1_claims,
        "validated_l2_claims": l2_claims,
        "market_health": {
            **(market_health or {}),
            "evidence_id": _evidence_id("DOMAIN-HEALTH", domain),
        },
        "company_metrics": company_metrics,
        "domain_foresight_signals": domain_signals,
        "precomputed_findings": relevant_findings,
    }


def _build_confidence_summary(data: dict, verifications: list) -> dict:
    kb = data["kb_aggregate"]
    total_kb = max(kb.get("total_records", 0), 1)
    macro_score = round(100 * kb.get("eligible_records", 0) / total_kb)

    summary_count = len(data.get("dashboard_data") or [])
    detail_count = len(data.get("company_details") or [])
    portfolio_score = round(100 * detail_count / max(summary_count, 1)) if summary_count else 0

    statuses = [v.get("status", "unavailable") for v in verifications if isinstance(v, dict)]
    verified = statuses.count("verified")
    failed = statuses.count("failed")
    unavailable = statuses.count("unavailable")
    interpretation_score = round(100 * verified / max(len(statuses), 1)) if statuses else 0
    interpretation_status = "verified" if statuses and failed == 0 and unavailable == 0 else "mixed"
    if not statuses or unavailable == len(statuses):
        interpretation_status = "unavailable"
    elif failed:
        interpretation_status = "failed"

    return {
        "macro_force_evidence": {
            "score": macro_score,
            "status": "strong" if macro_score >= 70 else "limited",
            "basis": f"{kb.get('eligible_records', 0)}/{kb.get('total_records', 0)} central KB records eligible",
        },
        "company_portfolio_pattern": {
            "score": portfolio_score,
            "status": "strong" if portfolio_score >= 70 else "limited",
            "basis": f"{detail_count}/{summary_count} portfolio companies have detailed records",
        },
        "strategic_interpretation": {
            "score": interpretation_score,
            "status": interpretation_status,
            "basis": f"{verified} verified, {failed} failed, {unavailable} unavailable outputs",
        },
    }


# ═══════════════════════════════════════════════════════════════════════════════
# PROMPT BUILDERS
# ═══════════════════════════════════════════════════════════════════════════════

def _format_top_signals_per_category(by_category: dict, per_cat: int = 5) -> str:
    """Format the top signals per category with full source context for the LLM.

    Selects the most important signals (high credibility + accelerating momentum)
    and includes full descriptions so the LLM can ground synthesis in actual content.
    """
    blocks = []
    for cat in ALL_CATEGORIES:
        signals = by_category.get(cat, [])
        if not signals:
            continue

        # Prioritize: high credibility → accelerating momentum → Probable/Emerging type
        def signal_priority(s):
            cred = {"high": 3, "medium": 2, "low": 1}.get(s.get("credibility", ""), 1)
            momentum = {"accelerating": 3, "stable": 2, "decelerating": 1}.get(s.get("trend_momentum", ""), 1)
            force = {"Probable/Emerging": 3, "Plausible/Maturing": 2, "Preposterous/Black Swan": 2}.get(s.get("force_type", ""), 1)
            return cred + momentum + force

        sorted_signals = sorted(signals, key=signal_priority, reverse=True)
        top = sorted_signals[:per_cat]

        lines = [f"\n### {cat} ({len(signals)} signals total, top {len(top)} shown):"]
        for s in top:
            lifecycle = s.get("lifecycle_stage", "")
            momentum = s.get("trend_momentum", "")
            confidence = s.get("confidence", "")
            source_count = s.get("source_count", 0)
            dims = s.get("dimensions_affected") or [s.get("dimension", "")]
            industries = s.get("industries_affected", [])

            line = f"- [{s.get('evidence_id', 'NO-ID')}] **{s['title']}** [{s.get('force_type', '?')} | {s.get('timeframe', '?')}]\n"
            line += f"  Description: {s.get('description', 'N/A')}\n"
            line += f"  Lifecycle: {lifecycle} | Momentum: {momentum} | Credibility: {s.get('credibility', '?')} ({source_count} verified sources)\n"
            if dims:
                line += f"  Dimensions affected: {', '.join(dims)}\n"
            if industries:
                line += f"  Industries affected: {', '.join(industries[:4])}"
            lines.append(line)

        blocks.append("\n".join(lines))

    return "\n\n".join(blocks)


def _build_global_prompt(
    kb_aggregate: dict,
    cross_impact: list,
    top_foresights: list,
    structured_findings: str = "",
    evidence_dossier=None,
) -> str:
    """Build prompt for Output 1: Global Force Landscape."""
    # Rich signal evidence — top signals per category with full descriptions
    signal_evidence = _format_top_signals_per_category(kb_aggregate["by_category"], per_cat=5)

    # Top themes by cross-impact density
    cross_summary = ""
    if evidence_dossier is not None:
        cross_summary = "\n".join(
            f"- {item.get('theme')}: {', '.join(item.get('dimensions') or [])} "
            f"(Evidence: {', '.join(item.get('evidence_ids') or [])})"
            for item in (evidence_dossier.get("cross_impact_candidates") or [])
        ) or "No evidence-linked macro convergence candidate met the dossier rule."
    elif cross_impact:
        theme_counts = Counter()
        for cell in cross_impact:
            theme_counts[cell["theme"]] += cell["count"]
        top_cross = theme_counts.most_common(8)
        cross_summary = "\n".join(f"- {t}: {c} cross-dimensional signals" for t, c in top_cross)

    # Top foresights
    foresight_summary = ""
    if evidence_dossier is not None:
        foresight_summary = "Excluded from Layer 1. Company-portfolio prevalence belongs to the Layer 2 dossier."
    elif top_foresights:
        foresight_summary = "\n".join(
            f"- {f['theme']} ({f['category']}): {f['count']} companies affected ({f['pct']}%)"
            for f in top_foresights[:10]
        )

    locked_dossier = json.dumps(evidence_dossier or {}, ensure_ascii=False, indent=2)

    return f"""{SYSTEM_ROLE}

TASK: Write a Global Force Landscape briefing — a futurologist's synthesis of the forces reshaping business globally.

LOCKED LAYER 1 EVIDENCE DOSSIER ({OBSERVATORY_DOSSIER_VERSION}):
{locked_dossier}

DOSSIER RULE:
- Treat the dossier above as the authoritative evidence boundary.
- Use the summaries below only as readable views of the same dossier, not as additional evidence.
- Cite evidence IDs after material claims.
- Clearly distinguish macro evidence from strategic inference.

DATA PROVIDED:

Signal Overview:
- Total validated signals: {kb_aggregate['total_signals']}
- Themes covered: {kb_aggregate['theme_count']} of 30
- High-credibility signals (3+ verified sources): {kb_aggregate.get('high_confidence_count', 0)}
- Accelerating forces: {kb_aggregate['accelerating']}
- Decelerating forces: {kb_aggregate['decelerating']}

Timeframe distribution:
{json.dumps(kb_aggregate['by_timeframe'], indent=2)}

Force type distribution:
{json.dumps(kb_aggregate['by_force_type'], indent=2)}

Signal lifecycle distribution (adoption curve position):
{json.dumps(kb_aggregate.get('by_lifecycle', {}), indent=2)}

Signal momentum distribution:
{json.dumps(kb_aggregate.get('by_momentum', {}), indent=2)}

DETAILED SIGNAL EVIDENCE BY CATEGORY:
(Top signals per category with full source context — use these descriptions to ground your synthesis in actual evidence, not just signal titles)
{signal_evidence}

Cross-impact density (theme x dimension interactions):
{cross_summary}

Top foresight themes affecting companies:
{foresight_summary}

PRE-COMPUTED EVIDENCE-LINKED FINDINGS:
{structured_findings}

ANALYTICAL STRUCTURE (Popper's Foresight Framework):

Section 1 — ANTICIPATION: What forces are converging? Which weak signals are strengthening? What established patterns are decelerating? Synthesize across categories — don't list themes, weave them into a narrative about interconnected forces.

Section 2 — PARTICIPATION: Where are organizations already adapting? What patterns emerge from how companies engage with these forces? What does the cross-impact data reveal about which organizational dimensions are most under pressure?

Section 3 — FUTURES LITERACY (Scenario Pathways):

This section uses Summary/Scenario Writing methodology — the production of plausible future accounts based on a creative combination of the data above, factual patterns, and reasoned hypotheses. Do NOT simply list three separate scenarios. Instead, identify 2-3 FORCE CLUSTERS — groups of forces from the data that naturally co-occur and amplify each other — and build branching narratives from them.

For each force cluster:
1. Name the cluster and identify which specific forces from the data compose it (cite the themes/categories)
2. Describe the PLAUSIBILITY CONDITIONS — what specific signals would need to strengthen for this cluster to become dominant? Reference actual timeframe and force_type data.
3. Write a SHORT BRANCHING NARRATIVE: "If this cluster accelerates, organizations that [specific posture] will [outcome], while those that [opposite posture] will [different outcome]."
4. Identify CROSS-FORCE DEPENDENCIES — how does this cluster interact with or block other clusters? ("If cluster A accelerates, it makes cluster B more likely because...")

Frame the three clusters as:
- A convergence scenario (multiple Probable/Emerging forces compound)
- A drift scenario (Plausible/Maturing forces stall while weak signals continue building)
- A disruption scenario (Preposterous/Black Swan forces combine with one accelerating cluster)

End with: What would an organization with genuine futures literacy do differently from one that merely tracks trends? Frame this as a thinking capability, not a technology adoption question.

OUTPUT FORMAT:
- 900-1100 words
- Three sections with headers (## Anticipation, ## Participation, ## Futures Literacy)
- Analytical essay prose — NO bullet points
- Written as a futurologist would present to a strategy board
- End with implications for organizational thinking, never technology adoption"""


def _build_finland_prompt(
    global_analysis: str,
    mi: dict,
    kb_aggregate: dict = None,
    structured_findings: str = "",
    *,
    l1_claims=None,
    evidence_dossier=None,
) -> str:
    """Build prompt for Output 2: Finnish Economy Force Map."""
    # Market health summary
    health_summary = ""
    if mi.get("market_health"):
        health_summary = "\n".join(
            f"- {h['domain']}: health={h['score']}/100, reality={h['avg_reality']}, financial={h['avg_financial']}, gap={h['avg_gap']}, {h['company_count']} companies"
            for h in mi["market_health"]
        )

    # Archetype distribution
    arch_summary = ""
    if mi.get("archetype_flow"):
        arch_summary = "\n".join(
            f"- {a['domain']}: Thriving={a['thriving']}, Modernizing={a['modernizing']}, Cash Cow={a['cash_cow']}, At Risk={a['at_risk']} (total={a['total']})"
            for a in mi["archetype_flow"]
        )

    # FAVI segments
    favi_summary = json.dumps(mi.get("favi_segments", {}), indent=2)

    # Gap patterns
    gap_summary = ""
    if mi.get("gap_patterns"):
        gap_summary = "\n".join(
            f"- {g['dimension']}: avg_intent={g['avg_intent']}, avg_reality={g['avg_reality']}, gap={g['avg_gap']} ({g['company_count']} companies)"
            for g in mi["gap_patterns"]
        )

    # Top foresights
    foresight_summary = ""
    if mi.get("top_foresights"):
        foresight_summary = "\n".join(
            f"- {f['theme']} ({f['category']}): {f['count']} companies ({f['pct']}%)"
            for f in mi["top_foresights"][:10]
        )

    # KB signal evidence — top 5 per category with full descriptions
    signal_evidence = ""
    if kb_aggregate and kb_aggregate.get("by_category"):
        signal_evidence = _format_top_signals_per_category(kb_aggregate["by_category"], per_cat=5)
    if evidence_dossier is not None:
        signal_evidence = "Macro context is supplied through the validated_l1_claims in the locked dossier."

    if l1_claims is not None:
        global_context = json.dumps(l1_claims, ensure_ascii=False, indent=2)
        global_context_label = "STRUCTURED L1 CLAIM HANDOFF (the L1 essay is intentionally excluded)"
    else:
        # Compatibility path for older callers and tests.
        global_context = global_analysis
        global_context_label = "GLOBAL CONTEXT"
    locked_dossier = json.dumps(evidence_dossier or {}, ensure_ascii=False, indent=2)

    return f"""{SYSTEM_ROLE}

TASK: Write a Finnish Economy Force Map — a deep strategic analysis of how global forces specifically reshape the Finnish business landscape.

TONE CALIBRATION — Balanced Strategic Assessment:
- Maintain intellectual honesty: celebrate genuine strength as clearly as you identify genuine weakness.
- If the data shows domains performing well, say so with conviction. Do not default to pessimism or manufacture risks.
- Only flag risks and challenges that are clearly grounded in the actual data provided. If a domain is thriving, acknowledge it.
- The goal is accurate strategic assessment, not alarm. A balanced view earns more credibility than a uniformly critical one.
- Positive framing is valid: strong archetype distributions, healthy financial indices, and narrow gaps are genuine competitive advantages worth highlighting.

ANALYTICAL LENS — Strategic Asymmetry Mapping:
Your task is NOT to summarize the data. Your task is to find the ASYMMETRIES — the places where the data contradicts expectations, where strength in one dimension masks vulnerability in another, where forces that seem separate are actually the same force arriving through different channels. Think like a strategist reading the data for what it reveals that the data itself does not say out loud.

LOCKED LAYER 2 EVIDENCE DOSSIER ({OBSERVATORY_DOSSIER_VERSION}):
{locked_dossier}

DOSSIER RULE:
- Treat this dossier as the authoritative Layer 2 evidence boundary.
- Use only the structured L1 claims below; do not infer from or reproduce the full Layer 1 essay.
- Cite evidence IDs after material claims and label strategic inference.

{global_context_label}:
{global_context}

SOURCE SIGNAL EVIDENCE (top validated signals per category — use these to ground your analysis in actual source content, cite specific signals when making claims):
{signal_evidence}

FINNISH DATA:

Market Health by Domain:
{health_summary}

Archetype Distribution by Domain:
{arch_summary}

Foresight-Adjusted Vulnerability Index segment distribution (across all companies):
{favi_summary}

Gap Patterns (Intent vs Reality per KB Dimension):
{gap_summary}

Top Foresight Themes Affecting Finnish Companies:
{foresight_summary}

Total companies analyzed: {mi.get('company_count', 0)}
Companies with detailed data: {mi.get('detailed_count', 0)}

PRE-COMPUTED EVIDENCE-LINKED FINDINGS:
{structured_findings}

ANALYTICAL STRUCTURE (4 sections):

Section 1 — THE FINNISH FORCE MAP: Strategic Asymmetries in the Finnish Landscape

Combine the Finnish positioning data with the cross-domain convergence patterns into ONE integrated analysis. Do NOT first describe where Finland stands and then separately describe cross-domain patterns. Instead, identify the 3-4 most important STRATEGIC ASYMMETRIES in the Finnish data — places where:
- A domain appears healthy by one metric but vulnerable by another
- Forces converge on multiple domains simultaneously in ways that create compound exposure
- A gap between intent and reality in one domain amplifies pressure in a connected domain
- Organizational capability is concentrated in well-served dimensions but weak where pressure is greatest

For each asymmetry, explain WHY it matters strategically — not just what the numbers show, but what the numbers MEAN for Finnish industry's future competitive position. Use specific signal evidence to connect these asymmetries to the global forces identified in Layer 1.

SO WHAT: End this section with a single paragraph answering: "What is the one pattern in this data that a Finnish board should lose sleep over, and what is the one pattern they should take genuine confidence from?"

Section 2 — THE ADAPTABILITY GAP: Running Someone Else's Race

Use the gap patterns (intent vs reality) to analyze where Finnish companies claim one thing but do another. Go beyond listing the gaps — interpret what the PATTERN of gaps reveals about Finnish organizational thinking.

Key analytical questions:
- When companies overestimate intent and underestimate operational reality, what does this tell us about how Finnish leadership constructs strategy?
- Are the gap patterns random, or do they reveal a SYSTEMATIC bias in how Finnish companies see themselves?
- Which gaps are the most dangerous — not because they are the largest, but because they create the most strategic exposure when combined with the force convergence patterns from Section 1?
- What should Finnish companies STOP doing based on these gaps?

SO WHAT: What does the gap pattern reveal about whether Finnish industry is running its own race or someone else's?

Section 3 — THREE HORIZONS FOR FINLAND:
- Now (6-12 months): What must Finnish industry address immediately based on the data? Be specific — name the domains and the forces.
- Prepare (12-24 months): What capabilities need building? Connect to the asymmetries and gaps identified above.
- Position (24-36 months): What "Own Race" opportunities exist? Where could Finnish industry define new competitive terrain rather than compete on existing terrain?

For each horizon, identify not just WHAT to do but which organizational capability, governance mechanism, or operating-model change must carry it.

SO WHAT: Which horizon carries the highest cost of inaction, and why?

Section 4 — THE CONTRARIAN SIGNAL: Finland's Dangerous Assumption

This section applies the Genius Forecasting method — insight-driven analysis that requires deep pattern recognition to see what conventional strategic thinking misses.

In 2-3 paragraphs, identify:
1. THE DANGEROUS ASSUMPTION: What is the single most consequential assumption Finnish industry is currently making that the data suggests is wrong? Look for: dimensions where confidence is high but evidence is weak, domains where all companies cluster in the same archetype (groupthink signal), forces with high credibility signals that Finnish companies are systematically ignoring, or gap patterns that suggest an entire sector is reading the map wrong.
2. THE CONTRARIAN HYPOTHESIS: Formulate a specific, defensible contrarian view about Finnish industry's strategic direction: "The conventional wisdom in Finnish business is [X], but the data suggests [Y] because [specific evidence from the data above]."
3. THE IMPLICATION: If this contrarian view is correct, what should Finnish industry collectively STOP doing that it currently considers essential? What should it START doing that currently seems marginal or irrelevant?

Frame this as strategic foresight, not prediction. The value is in expanding the range of futures that Finnish decision-makers consider plausible.

OUTPUT FORMAT:
- 1800-2400 words
- Four sections with headers
- Analytical essay prose — NO bullet points
- Deep strategic analysis, not data reporting — every paragraph must contain an insight, not just a finding
- Grounded in actual company data patterns (cite specific numbers)
- Reference specific KB signals by name when supporting claims about forces"""


def _build_domain_prompt(
    domain: str, domain_config: dict, domain_companies: list,
    kb_signals: list, market_health: dict,
    global_analysis: str, finland_analysis: str, structured_findings: str = "",
    *, upstream_claims=None, evidence_dossier=None,
) -> str:
    """Build prompt for Output 3: Domain Force Dossier."""
    # Company stats
    n = len(domain_companies)
    archetypes = Counter()
    favi_segments = Counter()
    reality_scores = []
    financial_scores = []

    for c in domain_companies:
        reality = c.get("reality_score")
        financial = c.get("financial_index")
        if reality is not None:
            reality_scores.append(float(reality))
            arch = classify_archetype(
                float(reality),
                float(financial) if financial is not None else float(c.get("intent_score", 0))
            )
            archetypes[arch] += 1
        if financial is not None:
            financial_scores.append(float(financial))

    avg_reality = round(statistics.mean(reality_scores), 1) if reality_scores else 0
    avg_financial = round(statistics.mean(financial_scores), 1) if financial_scores else 0

    arch_str = ", ".join(f"{k}={v}" for k, v in sorted(archetypes.items()))

    # Foresight links from domain companies
    domain_forces = []
    for c in domain_companies:
        for link in (c.get("foresight_links") or []):
            domain_forces.append({
                "theme": link.get("fp_theme", ""),
                "dimension": link.get("kb_dimension", ""),
                "timeframe": link.get("timeframe", ""),
                "force_type": link.get("force_type", ""),
                "title": link.get("title", ""),
            })

    # KB signals for this domain's themes — full context
    kb_summary = []
    for sig in kb_signals[:20]:
        lifecycle = sig.get("lifecycle_stage", "")
        momentum = sig.get("trend_momentum", "")
        credibility = sig.get("source_credibility", "")
        desc = sig.get("description", "")
        line = f"- **{sig.get('title', '?')}** [{sig.get('force_type', '?')} | {sig.get('timeframe', '?')}]"
        if desc:
            line += f"\n  {desc}"
        if lifecycle or momentum:
            line += f"\n  Lifecycle: {lifecycle} | Momentum: {momentum} | Credibility: {credibility}"
        kb_summary.append(line)

    # Forces by timeframe
    forces_by_tf = defaultdict(list)
    for f in domain_forces:
        forces_by_tf[f["timeframe"]].append(f"{f['theme']}: {f['title']}")

    health_score = market_health.get("score", "N/A") if market_health else "N/A"
    if upstream_claims is not None:
        broader_context = json.dumps(upstream_claims, ensure_ascii=False, indent=2)
        broader_context_rule = (
            "Use only the structured, evidence-linked L1/L2 claims in the locked dossier. "
            "The full upstream essays are intentionally excluded."
        )
    else:
        broader_context = f"Layer 1:\n{global_analysis}\n\nLayer 2:\n{finland_analysis}"
        broader_context_rule = "Use the supplied Layer 1 and Layer 2 analyses as broader context."
    locked_dossier = json.dumps(evidence_dossier or {}, ensure_ascii=False, indent=2)

    return f"""{SYSTEM_ROLE}

TASK: Write a Domain Force Dossier for {domain}.

TONE CALIBRATION — Let the Data Drive the Tone:
- Let the actual numbers set the tone. If the domain has strong reality scores, high Thriving ratios, or healthy financials, the analysis should reflect genuine confidence — not forced criticism.
- Only identify risks that are clearly evidenced in the data. Do not speculate about threats that the numbers do not support.
- Celebrate what is working: strong archetype distributions, narrow intent-reality gaps, and good market health are real competitive advantages. Say so clearly.
- The goal is an honest, data-grounded assessment — positive where the data warrants, concerned where the data warrants.
- A domain with 4 of 5 companies Thriving is genuinely strong. Do not undermine that finding with hypothetical future risks unless the signal evidence specifically supports those risks.

BROADER CONTEXT — How to use prior analysis layers:
{broader_context_rule}
- From Layer 1, identify 1-2 global forces most directly relevant to {domain} and reference how they manifest in this specific domain.
- From Layer 2, use the Finnish positioning insights for {domain} specifically — do not repeat general Finnish findings.
- Build on their conclusions but add domain-specific depth that the broader analyses could not provide.

LOCKED LAYER 3 EVIDENCE DOSSIER ({OBSERVATORY_DOSSIER_VERSION}):
{locked_dossier}

STRUCTURED UPSTREAM CLAIM HANDOFF:
{broader_context}

DOSSIER RULE:
- Treat the locked dossier as the authoritative evidence boundary.
- Cite evidence IDs after material claims.
- Treat foresight signals as macro context, never proof that a company has acted.
- Label strategic inference and state important evidence gaps.

DOMAIN DATA:

Companies: {n}
Archetype distribution: {arch_str}
Average reality score: {avg_reality}/100
Average financial index: {avg_financial}/100
Market health score: {health_score}/100
FP themes mapped: {', '.join(domain_config.get('fp_themes', []))}
Priority dimensions: {', '.join(domain_config.get('priority_dimensions', []))}

Forces identified in domain companies ({len(domain_forces)} total):
- Near-term (6-12mo): {len(forces_by_tf.get('6-12mo', []))} forces
- Medium-term (12-24mo): {len(forces_by_tf.get('12-24mo', []))} forces
- Long-term (24-36mo): {len(forces_by_tf.get('24-36mo', []))} forces

KB SIGNAL EVIDENCE for this domain's themes (full descriptions — use these to ground your analysis in actual source content):
{chr(10).join(kb_summary)}

PRE-COMPUTED EVIDENCE-LINKED FINDINGS:
{structured_findings}

ANALYTICAL STRUCTURE:

Section 1 — DOMAIN REALITY CHECK: Current state summary. What does the archetype distribution and average scores reveal about this domain's collective thinking capacity? Be specific with numbers.

Section 2 — FORCES BY TIMESCALE:
- Next 12 months (Act Now): Forces already in motion. What organizational thinking shifts are urgent? What must companies STOP doing immediately?
- 12-36 months (Prepare): Forces building momentum. What capabilities need to be built? What assumptions need challenging?
- 24-60 months (Position): Emerging forces that will reshape the domain. What "Own Race" opportunities exist?

Section 3 — THE OWN RACE OPPORTUNITY: What would it mean for companies in {domain} to define their own race rather than react? Specific strategic scenarios.

Section 4 — CONTRARIAN SIGNAL (Genius Forecasting):

This section applies the Genius Forecasting method — the preparation of insight-driven forecasts that require both deep domain expertise and creative pattern recognition to see what conventional analysis misses.

In 2-3 paragraphs, identify:
1. THE UNDERESTIMATED FORCE: What does the data suggest that most strategists in {domain} would dismiss or overlook? Look for: forces with high credibility but low company engagement, Black Swan/Preposterous signals that connect to accelerating trends, or dimensions where the gap is extreme but nobody is talking about it.
2. THE CONTRARIAN HYPOTHESIS: Formulate a specific, defensible contrarian view: "The conventional wisdom in {domain} is [X], but the data suggests [Y] because [specific evidence]." This should challenge assumptions about what {domain} companies think their future looks like.
3. THE IMPLICATION: If the contrarian view is correct, what should organizations in this domain STOP doing that they currently consider essential? What should they START doing that currently seems marginal or irrelevant?

Frame this as strategic foresight, not prediction. The value is in expanding the range of futures that decision-makers consider plausible.

OUTPUT FORMAT:
- 700-900 words
- Four sections with headers
- Analytical essay prose — NO bullet points
- Reference actual data from above"""


# ═══════════════════════════════════════════════════════════════════════════════
# LLM GENERATION + VERIFICATION
# ═══════════════════════════════════════════════════════════════════════════════

def _generate_sonnet(prompt: str, max_tokens: int = 5000) -> tuple[str, int]:
    """Generate text via Sonnet 4.6. Returns (text, word_count)."""
    anthropic_key = os.environ.get("ANTHROPIC_API_KEY", "").strip()
    if not anthropic_key:
        raise ValueError("ANTHROPIC_API_KEY not configured")

    client = anthropic.Anthropic(api_key=anthropic_key)
    t0 = time.time()

    response = client.messages.create(
        model="claude-sonnet-4-6",
        max_tokens=max_tokens,
        thinking={"type": "adaptive"},
        extra_body={"output_config": {"effort": "medium"}},
        messages=[{"role": "user", "content": prompt}],
    )

    text_parts = []
    for block in response.content:
        if block.type == "text":
            text_parts.append(block.text)
    text = "\n".join(text_parts).strip()

    # Strip markdown links (keep text only)
    text = re.sub(r'\[([^\]]+)\]\(https?://[^)]+\)', r'\1', text)

    wc = len(text.split())
    elapsed = int((time.time() - t0) * 1000)
    print(f"   [Observatory] Sonnet 4.6: {wc} words ({elapsed}ms)")
    return text, wc


def _generate_opus(prompt: str, max_tokens: int = 16000) -> tuple[str, int]:
    """Generate text via Opus 4.8. Returns (text, word_count).
    Used for Domain Dossiers (Layer 3) where analytical depth matters most."""
    anthropic_key = os.environ.get("ANTHROPIC_API_KEY", "").strip()
    if not anthropic_key:
        raise ValueError("ANTHROPIC_API_KEY not configured")

    client = anthropic.Anthropic(api_key=anthropic_key)
    t0 = time.time()

    response = client.messages.create(
        model="claude-opus-4-8",
        max_tokens=max_tokens,
        thinking={"type": "adaptive"},
        extra_body={"output_config": {"effort": "xhigh"}},
        messages=[{"role": "user", "content": prompt}],
    )

    text_parts = []
    for block in response.content:
        if block.type == "text":
            text_parts.append(block.text)
    text = "\n".join(text_parts).strip()

    # Strip markdown links (keep text only)
    text = re.sub(r'\[([^\]]+)\]\(https?://[^)]+\)', r'\1', text)

    wc = len(text.split())
    elapsed = int((time.time() - t0) * 1000)
    print(f"   [Observatory] Opus 4.8: {wc} words ({elapsed}ms)")
    return text, wc


def _generate_gpt_pro(prompt: str, max_tokens: int = 16000) -> tuple[str, int, str]:
    """Generate L1/L2 with GPT-5.5 Pro and bounded quality fallbacks."""
    attempts = (
        (OBSERVATORY_L1_MODEL, None, 480),
        (GPT_REASONING_MODEL, "xhigh", 300),
    )
    errors = []
    for model, effort, timeout in attempts:
        try:
            t0 = time.time()
            text = call_openai_text(
                prompt,
                model=model,
                max_output_tokens=max_tokens,
                timeout=timeout,
                max_retries=1,
                reasoning_effort=effort,
            )
            text = re.sub(r'\[([^\]]+)\]\(https?://[^)]+\)', r'\1', text).strip()
            wc = len(text.split())
            elapsed = int((time.time() - t0) * 1000)
            print(f"   [Observatory] {model}{f' ({effort})' if effort else ''}: {wc} words ({elapsed}ms)")
            return text, wc, model
        except Exception as exc:
            errors.append(f"{model}: {exc}")
            print(f"   [Observatory] {model} generation unavailable: {exc}")

    try:
        text, wc = _generate_sonnet(prompt, max_tokens=min(max_tokens, 8000))
        return text, wc, "claude-sonnet-4-6"
    except Exception as exc:
        errors.append(f"claude-sonnet-4-6: {exc}")
        raise ValueError("All Observatory L1/L2 generation models failed: " + " | ".join(errors))


def _verify_with_models(essay: str, source_data_summary: str, output_type: str, signal_evidence: str = "") -> dict:
    """Verify essay via GPT/Claude. Returns verification result.

    When signal_evidence is provided, adds a claim-level cross-validation check:
    verifies that narrative claims trace back to actual KB signals rather than
    being fabricated by the generation model.
    """
    signal_check = ""
    signal_response_fields = ""
    if signal_evidence:
        signal_check = f"""6. CLAIM-SIGNAL CROSS-VALIDATION: Compare specific claims, trends, and force descriptions in the essay against the KB SIGNAL EVIDENCE below. Flag any claims that:
   a) Cite a signal title or trend that does NOT appear in the evidence
   b) Attribute statistics or data points not found in any signal description
   c) Describe a force's direction (accelerating/decelerating) opposite to what the signals show
   Only flag clear fabrications — reasonable synthesis and inference from multiple signals is expected and acceptable.

KB SIGNAL EVIDENCE (the signals that were provided to the generation model):
{signal_evidence[:8000]}

"""
        signal_response_fields = ', "unsupported_claims": ["claim 1", ...], "signal_cross_validation_passed": true/false'

    prompt = f"""You are a quality assurance reviewer for strategic foresight analysis.

TASK: Verify the following {output_type} essay against its source data.

CHECK LIST:
1. DATA ACCURACY: Do any numbers or statistics cited in the essay contradict the source data? List specific mismatches.
2. ANTI-TECHNOLOGY FRAMING: Does the essay frame technology as the key message anywhere? Flag any sentences that say "companies need AI/cloud/digital/technology" rather than focusing on thinking, adaptability, and organizational capability.
3. STRUCTURE: Are all required sections present? Estimate the word count.
4. EVIDENCE GROUNDING: Does the essay make claims not supported by the provided data?
5. TONE BALANCE (for Finnish Economy Force Map and Domain Dossiers only): Does the essay manufacture or exaggerate risks that the data does not support? If the data shows strong performance (high Thriving ratios, healthy scores), does the essay acknowledge that strength clearly? Flag any cases where the tone is unjustifiably negative relative to the actual data.
6. EVIDENCE-ID TRACE: Do material claims cite evidence IDs that actually exist in the locked dossier? Flag missing, unknown, or mismatched IDs. Distinguish direct evidence from strategic inference.
{signal_check}
LOCKED SOURCE DOSSIER:
{source_data_summary[:24000]}

ESSAY TO VERIFY:
{essay}

RESPOND IN JSON FORMAT ONLY:
{{"passed": true/false, "word_count_estimate": N, "issues": ["issue 1", ...], "tech_framing_violations": ["sentence 1", ...], "data_mismatches": ["mismatch 1", ...], "evidence_id_issues": ["issue 1", ...]{signal_response_fields}}}"""

    try:
        result, model_used = call_json_model_chain(
            prompt,
            (GPT_REASONING_MODEL, "claude-sonnet-4-6"),
            max_tokens=2600,
            timeout=120,
            reasoning_effort="high",
        )
        parsed = result if isinstance(result, dict) else parse_json_object(result)
        parsed.setdefault("verification_model", model_used)
        parsed["status"] = "verified" if parsed.get("passed") is True else "failed"
        return parsed
    except Exception as e:
        print(f"   [Observatory] Verification failed (non-blocking): {e}")
        return {
            "passed": False,
            "status": "unavailable",
            "issues": [],
            "error": str(e),
        }


def _generate_with_verification(
    prompt: str,
    source_summary: str,
    output_type: str,
    use_opus: bool = False,
    signal_evidence: str = "",
    use_gpt_pro: bool = False,
) -> tuple[str, dict]:
    """Generate via Claude, then verify via GPT/Claude. Returns (text, verification).
    use_opus=True uses Opus 4.8 for deeper analytical reasoning (Layer 3).
    signal_evidence: when provided, enables claim-level cross-validation against KB signals."""
    if use_gpt_pro:
        text, wc, generation_model = _generate_gpt_pro(prompt)
    else:
        generate_fn = _generate_opus if use_opus else _generate_sonnet
        text, wc = generate_fn(prompt)
        generation_model = OBSERVATORY_L3_MODEL if use_opus else "claude-sonnet-4-6"

    if wc < 50:
        raise ValueError(f"Generation too short: {wc} words")

    verification = _verify_with_models(text, source_summary, output_type, signal_evidence=signal_evidence)
    verification["generation_model"] = generation_model

    # Log cross-validation results if signal evidence was checked
    if signal_evidence and verification.get("unsupported_claims"):
        n_claims = len(verification["unsupported_claims"])
        print(f"   [Observatory] Cross-validation: {n_claims} unsupported claim(s) flagged in {output_type}")
        for claim in verification["unsupported_claims"][:3]:
            print(f"      ⚠️ {claim[:120]}")

    # If verification finds critical issues and text is regeneratable, try once more
    needs_regen = False
    correction_parts = []

    if not verification.get("passed", True) and verification.get("tech_framing_violations"):
        violations = verification["tech_framing_violations"]
        print(f"   [Observatory] Verification found {len(violations)} tech-framing violations — regenerating...")
        correction_parts.append(f"Your previous attempt contained technology-centric framing. These specific sentences violated the anti-technology rule: {json.dumps(violations)}. Rewrite to focus on organizational thinking, adaptability, and future-readiness instead.")
        needs_regen = True

    if verification.get("unsupported_claims") and not verification.get("signal_cross_validation_passed", True):
        unsupported = verification["unsupported_claims"]
        print(f"   [Observatory] Cross-validation failed: {len(unsupported)} fabricated claims — regenerating...")
        correction_parts.append(f"Your previous attempt contained claims not supported by the provided KB signal evidence. These claims could not be traced to any signal: {json.dumps(unsupported[:5])}. Remove or replace these with claims that are directly grounded in the signal evidence provided.")
        needs_regen = True

    if needs_regen:
        correction = "\n\nCRITICAL CORRECTION: " + " ".join(correction_parts)
        if use_gpt_pro:
            text, wc, generation_model = _generate_gpt_pro(prompt + correction)
        else:
            text, wc = generate_fn(prompt + correction)
        verification = _verify_with_models(text, source_summary, output_type, signal_evidence=signal_evidence)
        verification["generation_model"] = generation_model

    return text, verification


# ═══════════════════════════════════════════════════════════════════════════════
# MAIN ORCHESTRATOR
# ═══════════════════════════════════════════════════════════════════════════════

def generate_observatory(progress_callback=None):
    """
    Generate the full Foresight Observatory (three layers).
    Returns the complete observatory data structure.

    progress_callback(step_name, current_index, total_steps) is called after each step.
    """
    storage = BlobStorage()

    def progress(step, idx, total):
        print(f"   [Observatory] Step {idx+1}/{total}: {step}")
        if progress_callback:
            progress_callback(step, idx, total)

    # --- Load all data ---
    data = _load_all_data(storage)
    kb_agg = data["kb_aggregate"]
    mi = data["market_intel"]
    input_manifest = _build_input_manifest(data)
    structured_findings = _build_structured_findings(kb_agg, mi)
    findings_block = _format_structured_findings(structured_findings)
    l1_dossier = _build_l1_evidence_dossier(kb_agg, mi, structured_findings, input_manifest)

    # Import domain config
    from strategic_foresight import DOMAIN_TO_FORESIGHT

    # Count active domains (domains with companies)
    domain_companies = defaultdict(list)
    for c in data["company_details"]:
        domain = c.get("domain") or "Unknown"
        if domain != "Unknown":
            domain_companies[domain].append(c)

    active_domains = [d for d in sorted(domain_companies.keys()) if len(domain_companies[d]) >= 2]
    total_steps = 2 + len(active_domains)  # Global + Finland + N domains

    # --- Pre-compute signal evidence for cross-validation ---
    signal_evidence = _format_top_signals_per_category(kb_agg["by_category"], per_cat=5)

    # --- Output 1: Global Force Landscape ---
    progress("Global Force Landscape", 0, total_steps)
    global_prompt = _build_global_prompt(
        kb_agg,
        mi.get("cross_impact", []),
        mi.get("top_foresights", []),
        findings_block,
        evidence_dossier=l1_dossier,
    )
    global_source = json.dumps(l1_dossier, ensure_ascii=False)
    global_text, global_verify = _generate_with_verification(
        global_prompt,
        global_source,
        "Global Force Landscape",
        signal_evidence=signal_evidence,
        use_gpt_pro=True,
    )
    l1_claims = _extract_layer_claims(global_text, l1_dossier, "L1")

    # Extract key convergences from the text (first 3 sentences that mention convergence/force)
    key_convergences = []
    for sentence in re.split(r'[.!?]+', global_text):
        sentence = sentence.strip()
        if any(word in sentence.lower() for word in ["converg", "amplif", "intersect", "compound"]):
            if len(sentence) > 20:
                key_convergences.append(sentence[:150])
        if len(key_convergences) >= 3:
            break

    global_analysis = {
        "text": global_text,
        "force_count": kb_agg["total_signals"],
        "accelerating_forces": kb_agg["accelerating"],
        "key_convergences": key_convergences,
        "structured_claims": l1_claims,
        "evidence_dossier_version": OBSERVATORY_DOSSIER_VERSION,
        "analysis_model": global_verify.get("generation_model", OBSERVATORY_L1_MODEL),
        "verification": global_verify,
    }

    # --- Output 2: Finnish Economy Force Map ---
    progress("Finnish Economy Force Map", 1, total_steps)
    l2_dossier = _build_l2_evidence_dossier(mi, l1_claims, structured_findings, input_manifest)
    finland_prompt = _build_finland_prompt(
        "",
        mi,
        kb_agg,
        findings_block,
        l1_claims=l1_claims,
        evidence_dossier=l2_dossier,
    )
    finland_source = json.dumps(l2_dossier, ensure_ascii=False)
    finland_text, finland_verify = _generate_with_verification(
        finland_prompt,
        finland_source,
        "Finnish Economy Force Map",
        use_gpt_pro=True,
    )
    l2_claims = _extract_layer_claims(finland_text, l2_dossier, "L2")

    # Extract top adaptability gaps
    top_gaps = []
    for g in mi.get("gap_patterns", [])[:3]:
        top_gaps.append(f"{g['dimension']}: gap={g['avg_gap']}")

    finland_analysis = {
        "text": finland_text,
        "domains_analyzed": len(mi.get("market_health", [])),
        "favi_distribution": mi.get("favi_segments", {}),
        "top_adaptability_gaps": top_gaps,
        "l1_claims_consumed": len(l1_claims),
        "structured_claims": l2_claims,
        "evidence_dossier_version": OBSERVATORY_DOSSIER_VERSION,
        "analysis_model": finland_verify.get("generation_model", OBSERVATORY_L2_MODEL),
        "verification": finland_verify,
    }

    # --- Output 3: Domain Force Dossiers ---
    domain_dossiers = []
    l3_dossiers = {}
    for i, domain in enumerate(active_domains):
        progress(f"Domain: {domain}", 2 + i, total_steps)

        config = DOMAIN_TO_FORESIGHT.get(domain, DOMAIN_TO_FORESIGHT.get("Other", {}))
        companies = domain_companies[domain]

        # Get KB signals for this domain's themes
        kb_signals = []
        for theme in config.get("fp_themes", []):
            for theme_slug, theme_data in data["all_kb"].items():
                for sig in theme_data.get("signals", []):
                    eligible, _ = _is_eligible_foresight_context(sig)
                    if sig.get("fp_theme") == theme and eligible:
                        kb_signals.append(sig)

        # Find market health for this domain
        mh = None
        for h in mi.get("market_health", []):
            if h["domain"] == domain:
                mh = h
                break

        l3_dossier = _build_l3_evidence_dossier(
            domain, config, companies, kb_signals, mh,
            l1_claims, l2_claims, structured_findings,
        )
        l3_dossiers[domain] = l3_dossier
        domain_prompt = _build_domain_prompt(
            domain, config, companies, kb_signals, mh,
            "", "", findings_block,
            upstream_claims={"layer_1": l1_claims, "layer_2": l2_claims},
            evidence_dossier=l3_dossier,
        )
        source = json.dumps(l3_dossier, ensure_ascii=False)
        # Build domain-specific signal evidence for cross-validation
        domain_signal_evidence = "\n".join(
            f"- **{s.get('title', 'N/A')}** [{s.get('force_type', '?')} | {s.get('timeframe', '?')}]\n  Description: {s.get('description', 'N/A')}"
            for s in kb_signals[:20]
        )
        text, verify = _generate_with_verification(domain_prompt, source, f"Domain Dossier: {domain}", use_opus=True, signal_evidence=domain_signal_evidence)

        # Extract forces by timescale
        forces_by_ts = {"act_now": [], "prepare": [], "position": []}
        for sig in kb_signals:
            tf = sig.get("timeframe", "")
            title = sig.get("title", "")[:80]
            if "6-12" in tf:
                forces_by_ts["act_now"].append(title)
            elif "12-24" in tf:
                forces_by_ts["prepare"].append(title)
            elif "24-36" in tf:
                forces_by_ts["position"].append(title)

        # Limit to top 5 per timescale
        for k in forces_by_ts:
            forces_by_ts[k] = forces_by_ts[k][:5]

        dossier = {
            "domain": domain,
            "text": text,
            "company_count": len(companies),
            "archetype_distribution": dict(Counter(
                classify_archetype(
                    float(c["reality_score"]),
                    float(c.get("financial_index") or c.get("intent_score", 0))
                )
                for c in companies if c.get("reality_score") is not None
            )),
            "forces_by_timescale": forces_by_ts,
            "analysis_model": verify.get("generation_model", OBSERVATORY_L3_MODEL),
            "evidence_dossier_version": OBSERVATORY_DOSSIER_VERSION,
            "verification": verify,
        }
        domain_dossiers.append(dossier)

    # --- Assemble final result ---
    all_verifications = [global_verify, finland_verify] + [item["verification"] for item in domain_dossiers]
    result = {
        "generated_at": datetime.utcnow().isoformat(),
        "model": "gpt-5.5-pro (Layers 1-2) + claude-opus-4-8 (Layer 3)",
        "verification_model": "gpt-5.5 high / claude-sonnet-4-6",
        "company_count": len(data["dashboard_data"]),
        "domain_count": len(active_domains),
        "input_manifest": input_manifest,
        "evidence_dossiers": {
            "schema_version": OBSERVATORY_DOSSIER_VERSION,
            "layer_1": l1_dossier,
            "layer_2": l2_dossier,
            "layer_3": l3_dossiers,
        },
        "layer_claims": {"layer_1": l1_claims, "layer_2": l2_claims},
        "structured_findings": structured_findings,
        "confidence": _build_confidence_summary(data, all_verifications),
        "global_analysis": global_analysis,
        "finland_analysis": finland_analysis,
        "domain_dossiers": domain_dossiers,
        # Top-15 themes with momentum breakdown — drives the Theme Momentum chart
        # in Market Intelligence > Future Demand. Additive; safe for old clients.
        "theme_momentum": kb_agg.get("theme_momentum_top", []),
    }

    # Save to storage
    url = storage.save_observatory(result)
    print(f"   [Observatory] Saved observatory to storage: {url}")

    return result


# FORESIGHT_OBSERVATORY_DEADLINE_GUARDRAIL: generated observatory text is passed through sanitize_foresight_text; prompts should avoid deterministic must/requires/forces wording and past deadlines.
