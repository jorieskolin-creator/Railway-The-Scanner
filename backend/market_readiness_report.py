"""
Market Readiness and Emerging Demand Outlook
===========================================
Admin-only portfolio report assembled from existing Scanner artifacts.

This module intentionally does not rescan companies or call LLMs. The v1 report
is deterministic: it reorganizes stored evidence, market intelligence, FAVI,
Observatory, and Buying Insight metadata into a business-readable outlook.
"""

from __future__ import annotations

import html
import re
import statistics
from collections import Counter, defaultdict
from datetime import datetime
from typing import Any

from analyze_scores import run_analysis
from market_intelligence import compute_market_intelligence


THEME_LABELS = {
    "Values": "Values and Commitments",
    "Strategy": "Strategy and Priorities",
    "Operating Model": "Operating Model and Execution",
    "AI Readiness": "Data and AI Readiness",
    "Service Portfolio": "Services and Revenue Portfolio",
}

FAVI_SEGMENT_LABELS = {
    "Critical": "Priority Risk",
    "Sleepwalking": "Emerging Risk",
    "Wounded but Sheltered": "Current Recovery Need",
    "Safe Harbor": "Well Positioned",
}

SAMPLE_LIMITED_THRESHOLD = 5


def _now_iso() -> str:
    return datetime.utcnow().isoformat()


def _clean_text(value: Any, limit: int | None = None) -> str:
    text = re.sub(r"\s+", " ", str(value or "")).strip()
    if limit and len(text) > limit:
        truncated = text[:limit]
        boundary = max(truncated.rfind(". "), truncated.rfind("; "), truncated.rfind(", "))
        if boundary > int(limit * 0.55):
            truncated = truncated[: boundary + 1]
        text = truncated.rstrip(" ,;:") + "..."
    return text


def _num(value: Any, default: float = 0.0) -> float:
    try:
        if value is None or value == "":
            return default
        return float(value)
    except Exception:
        return default


def _avg(values: list[float]) -> float:
    clean = [float(v) for v in values if v is not None]
    return round(statistics.mean(clean), 1) if clean else 0.0


def _sample_confidence(count: int) -> str:
    if count <= 0:
        return "No sample"
    if count < SAMPLE_LIMITED_THRESHOLD:
        return "Directional result - limited sample"
    if count < 12:
        return "Moderate sample"
    return "Broad sample"


def _domain(company: dict) -> str:
    return company.get("domain") or company.get("industry") or "Unknown"


def _company_name(company: dict) -> str:
    return company.get("companyName") or company.get("company_name") or "Unknown company"


def _infer_challenge_dimension(text: str) -> str:
    lower = text.lower()
    if any(token in lower for token in ["ai", "data", "analytics", "algorithm", "automation"]):
        return "AI Readiness"
    if any(token in lower for token in ["service", "portfolio", "offering", "revenue", "customer"]):
        return "Service Portfolio"
    if any(token in lower for token in ["operating", "execution", "delivery", "process", "supply", "capacity"]):
        return "Operating Model"
    if any(token in lower for token in ["value", "sustainability", "responsibility", "commitment", "esg"]):
        return "Values"
    return "Strategy"


def _extract_company_challenge_items(company: dict) -> list[dict]:
    name = _company_name(company)
    domain = _domain(company)
    items: list[dict] = []

    for risk in company.get("evidence_based_risks") or []:
        if isinstance(risk, dict):
            text = risk.get("risk") or risk.get("theme") or risk.get("claim") or risk.get("description")
        else:
            text = risk
        text = _clean_text(text, 240)
        if len(text) > 20:
            items.append({
                "company": name,
                "domain": domain,
                "dimension": _infer_challenge_dimension(text),
                "text": text,
                "basis": "Observed today",
            })

    for item in company.get("reality_checklist") or []:
        if not isinstance(item, dict) or item.get("sentiment") != "negative":
            continue
        text = _clean_text(item.get("query") or item.get("finding") or item.get("evidence") or "", 240)
        if len(text) > 20:
            items.append({
                "company": name,
                "domain": domain,
                "dimension": _infer_challenge_dimension(text),
                "text": text,
                "basis": "Observed today",
            })

    for pair in company.get("activation_pairs") or []:
        if not isinstance(pair, dict):
            continue
        pair_type = str(pair.get("theme_type") or pair.get("sales_play_type") or "").lower()
        if pair_type and pair_type not in {"current_status", "current-status"}:
            continue
        text = _clean_text(pair.get("why") or pair.get("theme") or "", 240)
        if len(text) > 20:
            items.append({
                "company": name,
                "domain": domain,
                "dimension": _infer_challenge_dimension(text),
                "text": text,
                "basis": "Observed today",
            })

    if not items:
        summary = _clean_text(company.get("general_summary") or company.get("summary_text") or "", 220)
        if summary:
            items.append({
                "company": name,
                "domain": domain,
                "dimension": _infer_challenge_dimension(summary),
                "text": summary,
                "basis": "Observed today",
            })
    return items


def _cluster_challenge_patterns(items: list[dict]) -> list[dict]:
    clusters = {
        "Strategy proof and prioritization": ["strategy", "priority", "position", "growth", "focus", "proof"],
        "Operating model and execution capacity": ["operating", "execution", "delivery", "capacity", "process", "flow"],
        "Data and AI readiness": ["ai", "data", "analytics", "automation", "digital"],
        "Services and revenue portfolio": ["service", "portfolio", "offering", "revenue", "customer"],
        "Values, commitments and compliance": ["value", "sustainability", "responsibility", "commitment", "esg", "regulat"],
        "Financial capacity and investment discipline": ["financial", "profit", "margin", "debt", "investment", "cash"],
        "Market adaptation and competitiveness": ["market", "competitive", "demand", "customer", "adapt"],
        "Technology modernization": ["platform", "cloud", "architecture", "system", "integration"],
        "Supply chain and resilience": ["supply", "logistics", "resilience", "supplier", "inventory"],
        "Talent and organizational capability": ["talent", "people", "workforce", "skill", "culture"],
    }
    result = []
    for label, keywords in clusters.items():
        matched = []
        companies = set()
        domains = set()
        dimensions = Counter()
        for item in items:
            lower = item["text"].lower()
            if any(keyword in lower for keyword in keywords):
                matched.append(item)
                companies.add(item["company"])
                domains.add(item["domain"])
                dimensions[item["dimension"]] += 1
        if matched:
            result.append({
                "label": label,
                "company_prevalence": len(companies),
                "signal_count": len(matched),
                "domains": sorted(domains)[:8],
                "primary_dimension": dimensions.most_common(1)[0][0] if dimensions else "Strategy",
                "examples": matched[:4],
                "conclusion_label": "Observed today",
            })
    result.sort(key=lambda item: (item["company_prevalence"], item["signal_count"]), reverse=True)
    return result[:10]


def _build_theme_challenges(challenge_items: list[dict], market_data: dict) -> list[dict]:
    by_dimension = defaultdict(list)
    for item in challenge_items:
        by_dimension[item["dimension"]].append(item)

    gap_by_dim = {
        row.get("dimension"): row
        for row in market_data.get("gap_patterns") or []
        if isinstance(row, dict)
    }
    output = []
    for raw_dim, label in THEME_LABELS.items():
        rows = by_dimension.get(raw_dim, [])
        companies = sorted({row["company"] for row in rows})
        domains = sorted({row["domain"] for row in rows if row["domain"] != "Other"})
        gap = gap_by_dim.get(raw_dim, {})
        output.append({
            "theme": label,
            "scanner_dimension": raw_dim,
            "conclusion_label": "Observed today",
            "observed_today": f"{len(companies)} companies show at least one relevant current-condition signal." if companies else "No strong current-condition pattern was isolated from stored evidence.",
            "recurring_challenges": [row["text"] for row in rows[:5]],
            "domain_concentration": domains[:6],
            "why_it_matters": _theme_why_it_matters(raw_dim),
            "companies_may_need_now": _theme_need_now(raw_dim),
            "may_be_needed_next": _theme_need_next(raw_dim),
            "confidence": _sample_confidence(len(companies)),
            "sample_size": len(companies),
            "gap_proxy": gap,
        })
    return output


def _theme_why_it_matters(dim: str) -> str:
    return {
        "Values": "Commitments increasingly influence permission to operate, buyer trust, and partner eligibility.",
        "Strategy": "Unclear priorities make it harder to choose where modernization investment should create value.",
        "Operating Model": "Execution constraints can turn good strategic intent into slow delivery, bottlenecks, or fragile scaling.",
        "AI Readiness": "AI value depends on usable data, governance, and workflow readiness, not only experimentation.",
        "Service Portfolio": "Portfolio clarity affects how companies convert capabilities into repeatable revenue and defensible customer value.",
    }.get(dim, "The pattern may affect future competitiveness and investment focus.")


def _theme_need_now(dim: str) -> str:
    return {
        "Values": "clarify commitments, evidence, and compliance-critical operating practices",
        "Strategy": "validate priorities, trade-offs, and proof points for execution",
        "Operating Model": "map delivery bottlenecks and practical capacity constraints",
        "AI Readiness": "assess data foundations, governance, and AI-ready workflows",
        "Service Portfolio": "connect offering logic to customer problems, delivery capability, and measurable value",
    }.get(dim, "validate the current constraint before designing a response")


def _theme_need_next(dim: str) -> str:
    return {
        "Values": "stronger evidence of responsible operations and transparent reporting",
        "Strategy": "a clearer line of sight from ambition to investment and measurable outcomes",
        "Operating Model": "more repeatable operating routines and scalable execution models",
        "AI Readiness": "a governed AI adoption path tied to business processes and data quality",
        "Service Portfolio": "more modular, measurable, and future-ready service/revenue models",
    }.get(dim, "a practical modernization path tied to external pressure")


def _build_domain_scorecards(dashboard_data: list[dict], market_data: dict, challenge_patterns: list[dict]) -> list[dict]:
    favi_by_name = {row.get("name"): row for row in market_data.get("favi_companies") or []}
    market_health_by_domain = {row.get("domain"): row for row in market_data.get("market_health") or []}
    pattern_by_domain = defaultdict(list)
    for pattern in challenge_patterns:
        for domain in pattern.get("domains") or []:
            pattern_by_domain[domain].append(pattern["label"])

    buckets = defaultdict(list)
    for company in dashboard_data:
        domain = _domain(company)
        if domain == "Other":
            continue
        buckets[domain].append(company)

    scorecards = []
    for domain, companies in sorted(buckets.items(), key=lambda kv: len(kv[1]), reverse=True):
        favi_rows = [favi_by_name.get(_company_name(c)) for c in companies if favi_by_name.get(_company_name(c))]
        health = market_health_by_domain.get(domain, {})
        avg_reality = _avg([_num(c.get("reality_score")) for c in companies])
        avg_financial = _avg([_num(c.get("financial_index")) for c in companies if c.get("financial_index") is not None])
        avg_pressure = _avg([_num(row.get("fps")) for row in favi_rows])
        avg_gap = _avg([_num(row.get("readiness_gap")) for row in favi_rows])
        segments = Counter(_favi_segment(row) for row in favi_rows if row)
        sample_conf = _sample_confidence(len(companies))
        top_patterns = pattern_by_domain.get(domain, [])[:4]
        likely_need = _likely_domain_need(top_patterns, avg_pressure, avg_gap)
        scorecards.append({
            "domain": domain,
            "company_count": len(companies),
            "sample_confidence": sample_conf,
            "current_operating_position": _operating_position(avg_reality, avg_financial),
            "avg_reality": avg_reality,
            "avg_financial": avg_financial,
            "strongest_capability": _strongest_capability(avg_reality, avg_financial, health),
            "challenge_patterns": top_patterns,
            "vulnerability_distribution": dict(segments),
            "financial_ability_to_respond": _ability_to_act(avg_financial),
            "external_pressures": _domain_external_pressures(domain, market_data),
            "likely_next_need": likely_need,
            "discovery_questions": _domain_discovery_questions(likely_need),
            "conclusion_label": "Observed today",
        })
    return scorecards


def _favi_segment(row: dict) -> str:
    segment = row.get("segment") or row.get("adaptation_segment") or ""
    for source, replacement in FAVI_SEGMENT_LABELS.items():
        if source.lower() in str(segment).lower():
            return replacement
    favi = _num(row.get("favi_score"))
    current = _num(row.get("current_vulnerability"))
    if favi >= 50 and current >= 40:
        return "Priority Risk"
    if favi >= 50:
        return "Emerging Risk"
    if current >= 40:
        return "Current Recovery Need"
    return "Well Positioned"


def _operating_position(avg_reality: float, avg_financial: float) -> str:
    if avg_reality >= 55 and avg_financial >= 55:
        return "Operationally and financially well positioned"
    if avg_financial >= 55:
        return "Financially able, but current operating proof should be validated"
    if avg_reality >= 55:
        return "Operationally credible, but financial room to respond may be narrower"
    return "Current readiness appears constrained"


def _strongest_capability(avg_reality: float, avg_financial: float, health: dict) -> str:
    if avg_financial >= avg_reality and avg_financial >= 50:
        return "financial capacity to fund focused change"
    if avg_reality >= 50:
        return "current operating evidence and execution base"
    if health.get("thriving_pct", 0) >= 40:
        return "subset of strong performers inside the domain"
    return "no single capability dominates across the available sample"


def _ability_to_act(avg_financial: float) -> str:
    if avg_financial >= 60:
        return "High"
    if avg_financial >= 45:
        return "Moderate"
    if avg_financial > 0:
        return "Constrained"
    return "Unknown"


def _domain_external_pressures(domain: str, market_data: dict) -> list[str]:
    rows = [row for row in market_data.get("industry_radar") or [] if row.get("domain") == domain]
    if not rows:
        return []
    raw = rows[0].get("raw_scores") or {}
    ranked = sorted(raw.items(), key=lambda kv: kv[1], reverse=True)
    return [name for name, count in ranked[:3] if count]


def _likely_domain_need(patterns: list[str], pressure: float, gap: float) -> str:
    if patterns:
        return f"Validate whether {patterns[0].lower()} is becoming a practical modernization priority."
    if pressure >= 50 and gap >= 25:
        return "Validate where future pressure is exposing capability or execution gaps."
    if pressure >= 50:
        return "Monitor external pressure and prepare a focused readiness conversation."
    return "Keep as a monitoring segment until clearer account-level triggers appear."


def _domain_discovery_questions(likely_need: str) -> list[str]:
    return [
        f"Where is this issue already visible in decisions, operations, or customer expectations: {likely_need}",
        "What evidence would show that the need is urgent enough to prioritize now?",
        "What would disprove the need or move it into a longer-term monitoring track?",
    ]


def _build_future_demand(market_data: dict, cross_impact: dict | None) -> dict:
    themes = market_data.get("top_foresights") or []
    cross_rows = market_data.get("cross_impact") or []
    concentration = market_data.get("foresight_coverage") or {}
    industry = market_data.get("industry_radar") or []
    interpretation = cross_impact or {}
    arenas = []
    for theme in themes[:5]:
        affected_domains = []
        for row in industry:
            raw = row.get("raw_scores") or {}
            category = theme.get("category")
            if category and raw.get(category, 0) > 0:
                affected_domains.append(row.get("domain"))
        top_dim = _top_dimension_for_theme(theme.get("theme"), cross_rows)
        arenas.append({
            "external_change": theme.get("theme"),
            "capability_affected": THEME_LABELS.get(top_dim, top_dim or "Strategy and Priorities"),
            "potential_emerging_need": _emerging_need_phrase(theme.get("theme"), top_dim),
            "affected_domains": [d for d in affected_domains if d and d != "Other"][:6],
            "horizon": "12-24 months" if theme.get("count", 0) >= 3 else "24-36 months",
            "confidence": "Moderate" if theme.get("count", 0) >= 3 else "Directional",
            "conclusion_label": "External change pressure",
        })
    return {
        "top_foresight_themes": themes[:10],
        "cross_impact_matrix": cross_rows,
        "cross_impact_interpretation": interpretation,
        "concentration_vs_volume": concentration,
        "industry_domain_exposure": industry,
        "emerging_demand_arenas": arenas,
        "excluded_legacy_widgets": ["momentum_chart", "economy_force_map"],
    }


def _top_dimension_for_theme(theme: str, cross_rows: list[dict]) -> str:
    counts = Counter()
    for row in cross_rows:
        if row.get("theme") == theme:
            counts[row.get("dimension")] += int(row.get("count") or 0)
    return counts.most_common(1)[0][0] if counts else "Strategy"


def _emerging_need_phrase(theme: str, dimension: str) -> str:
    dim_label = THEME_LABELS.get(dimension, dimension or "Strategy and Priorities").lower()
    return f"Assess how {str(theme or 'external pressure').lower()} may change requirements for {dim_label}."


def _build_observatory_synthesis(observatory: dict | None) -> dict:
    if not isinstance(observatory, dict):
        return {
            "global_change_landscape": [],
            "finnish_market_implications": "",
            "time_windows": {},
            "cross_domain_implications": [],
            "domain_briefs": [],
            "warnings": ["No Observatory artifact was available."],
        }
    finland_text = _clean_text((observatory.get("finland_analysis") or {}).get("text") or "", 1600)
    force_clusters = []
    for item in (observatory.get("global_force_clusters") or observatory.get("force_clusters") or [])[:7]:
        if isinstance(item, dict):
            force_clusters.append({
                "title": item.get("title") or item.get("cluster") or item.get("name"),
                "summary": _clean_text(item.get("summary") or item.get("description"), 360),
            })
    if not force_clusters:
        for theme in (observatory.get("theme_momentum") or [])[:7]:
            if isinstance(theme, dict):
                force_clusters.append({
                    "title": theme.get("theme"),
                    "summary": "Tracked foresight signal cluster in the Observatory data.",
                })
    domain_briefs = []
    for dossier in (observatory.get("domain_dossiers") or [])[:12]:
        if not isinstance(dossier, dict):
            continue
        text = _clean_text(dossier.get("summary") or dossier.get("text") or dossier.get("domain_reality_check") or "", 520)
        domain_briefs.append({
            "domain": dossier.get("domain"),
            "current_position": text,
            "act_now": _clean_text(dossier.get("act_now") or "", 240),
            "prepare": _clean_text(dossier.get("prepare") or "", 240),
            "position": _clean_text(dossier.get("position") or "", 240),
            "confidence": dossier.get("confidence") or "Directional",
        })
    return {
        "global_change_landscape": force_clusters,
        "finnish_market_implications": finland_text,
        "time_windows": {
            "act_now_6_12_months": "Issues already affecting decisions, execution, evidence expectations, or customer conversations.",
            "prepare_12_24_months": "Capabilities and investment choices that should be developed before pressure becomes urgent.",
            "position_24_36_months": "Longer-term strategic choices, portfolio shifts, and resilience moves.",
        },
        "cross_domain_implications": _cross_domain_implications(force_clusters),
        "domain_briefs": domain_briefs,
        "finnish_economy_force_map": {
            "status": "Moved to Observatory context",
            "source": "Observatory Finland analysis",
        },
        "generated_at": observatory.get("generated_at"),
    }


def _cross_domain_implications(force_clusters: list[dict]) -> list[str]:
    implications = []
    for cluster in force_clusters[:6]:
        title = cluster.get("title")
        if title:
            implications.append(f"{title}: validate which domains need readiness, portfolio, or execution changes.")
    return implications


def _build_emerging_need_hypotheses(market_data: dict, domain_scorecards: list[dict], challenge_patterns: list[dict]) -> list[dict]:
    company_rankings = ((market_data.get("opportunity_map") or {}).get("company_rankings") or [])[:50]
    by_domain = {row["domain"]: row for row in domain_scorecards}
    hypotheses = []
    for arena in _build_future_demand(market_data, None).get("emerging_demand_arenas", [])[:5]:
        domains = arena.get("affected_domains") or []
        domain = domains[0] if domains else None
        domain_card = by_domain.get(domain, {}) if domain else {}
        matched_companies = [row for row in company_rankings if not domain or row.get("domain") == domain][:5]
        urgency = _need_urgency(matched_companies, arena)
        ability = domain_card.get("financial_ability_to_respond") or "Unknown"
        current_condition = domain_card.get("likely_next_need") or (challenge_patterns[0]["label"] if challenge_patterns else "current capability pattern requires validation")
        hypotheses.append({
            "observed_current_condition": current_condition,
            "external_trigger": arena.get("external_change"),
            "business_consequence": "If the pressure becomes material, companies may need to reprioritize operating practices, data readiness, portfolio logic, or execution capacity.",
            "potential_emerging_need": arena.get("potential_emerging_need"),
            "affected_domains_or_segments": domains,
            "buyer_role_relevance": ["Business leadership", "Operations leadership", "Digital/Data leadership"],
            "timing": arena.get("horizon"),
            "need_urgency": urgency,
            "ability_to_act": ability,
            "evidence_confidence": arena.get("confidence"),
            "validation_question": f"Where is {str(arena.get('external_change') or 'this pressure').lower()} already changing priorities, customer expectations, or execution constraints?",
            "disconfirming_evidence": "The hypothesis weakens if affected companies show no practical exposure, no operating constraint, or no budget/ownership path.",
            "conclusion_label": "Emerging need hypothesis",
        })
    return hypotheses


def _need_urgency(company_rows: list[dict], arena: dict) -> str:
    avg_score = _avg([_num(row.get("opportunity_score")) for row in company_rows])
    if avg_score >= 60:
        return "High - validate now"
    if avg_score >= 40 or arena.get("confidence") == "Moderate":
        return "Medium - validate when trigger appears"
    return "Monitor"


def _build_executive_summary(report: dict) -> dict:
    metadata = report.get("metadata", {})
    top_domains = [row["domain"] for row in report.get("domain_scorecards", [])[:3]]
    top_needs = report.get("emerging_need_hypotheses", [])[:5]
    top_patterns = report.get("strategic_challenge_patterns", [])[:3]
    return {
        "market_position_today": (
            f"The portfolio contains {metadata.get('company_count', 0)} scanned companies across "
            f"{metadata.get('domain_count', 0)} ranked business domains. "
            f"Highest-volume domains in this view are {', '.join(top_domains) if top_domains else 'not yet clear'}."
        ),
        "what_is_changing": (
            "External pressure is represented through the existing foresight KB, cross-impact matrix, "
            "FAVI scoring, and Observatory synthesis. The report treats these as pressure signals, not as proof of buying intent."
        ),
        "vulnerability_views": {
            "current_vs_future": "FAVI labels are translated into business language: Priority Risk, Emerging Risk, Current Recovery Need, and Well Positioned.",
            "ability_vs_exposure": "Ability to act is shown separately from need urgency so financial capacity is not confused with demand.",
        },
        "top_emerging_demand_areas": top_needs,
        "executive_implication": {
            "now": "Use current challenge patterns to choose where discovery should start.",
            "12_24_months": "Track domains where external pressure intersects with current capability gaps.",
            "24_36_months": "Position offers around repeatable modernization capabilities, not isolated one-off fixes.",
        },
        "recurring_challenge_patterns": [p.get("label") for p in top_patterns],
    }


def build_market_readiness_report(storage, buying_storage=None) -> dict:
    dashboard_data = storage.retrieve_latest_data("index/summary_list") or []
    if not dashboard_data:
        raise ValueError("No dashboard data found for Market Readiness report.")

    company_details = []
    warnings = []
    for item in dashboard_data:
        name = item.get("companyName")
        if not name:
            continue
        detail = storage.get_company_data(name)
        if isinstance(detail, dict):
            company_details.append({**item, **detail})
        else:
            company_details.append(item)
            warnings.append(f"Detailed company data missing for {name}; using index fields only.")

    analysis_result = run_analysis(dashboard_data)
    market_data = compute_market_intelligence(dashboard_data, company_details, analysis_result)
    observatory = storage.get_observatory()
    cross_impact = storage.get_cross_impact_interpretation()

    challenge_items = []
    for company in company_details:
        challenge_items.extend(_extract_company_challenge_items(company))
    challenge_patterns = _cluster_challenge_patterns(challenge_items)
    theme_challenges = _build_theme_challenges(challenge_items, market_data)
    domain_scorecards = _build_domain_scorecards(dashboard_data, market_data, challenge_patterns)
    future_demand = _build_future_demand(market_data, cross_impact)
    observatory_synthesis = _build_observatory_synthesis(observatory)
    emerging_needs = _build_emerging_need_hypotheses(market_data, domain_scorecards, challenge_patterns)

    report = {
        "report_key": "latest",
        "title": "Market Readiness and Emerging Demand Outlook",
        "status": "ready",
        "generated_at": _now_iso(),
        "method_note": (
            "This Admin-only report identifies conditions from which a need is likely to emerge before "
            "that need has become an explicit priority, formal requirement, or detectable buying signal."
        ),
        "governance_statement": (
            "Emerging needs are hypotheses derived from observed company conditions and external change pressures. "
            "They are not evidence of confirmed buying intent and require discovery validation."
        ),
        "metadata": {
            "company_count": len(dashboard_data),
            "company_detail_count": len(company_details),
            "domain_count": len({row.get("domain") for row in dashboard_data if row.get("domain") and row.get("domain") != "Other"}),
            "generated_from": [
                "summary_list",
                "company_details",
                "market_intelligence",
                "favi",
                "observatory",
                "cross_impact_interpretation",
            ],
        },
        "warnings": warnings + observatory_synthesis.get("warnings", []),
        "executive_summary": {},
        "current_market_challenges": theme_challenges,
        "strategic_challenge_patterns": challenge_patterns,
        "domain_scorecards": domain_scorecards,
        "future_demand_analysis": future_demand,
        "strategic_observatory": observatory_synthesis,
        "emerging_need_hypotheses": emerging_needs,
        "appendices": {
            "top_50_company_rankings": ((market_data.get("opportunity_map") or {}).get("company_rankings") or [])[:50],
            "domain_rankings": [
                row for row in ((market_data.get("opportunity_map") or {}).get("domain_rankings") or [])
                if row.get("domain") != "Other"
            ],
            "market_health": [row for row in (market_data.get("market_health") or []) if row.get("domain") != "Other"],
            "favi_companies": market_data.get("favi_companies") or [],
            "raw_limitations": [
                "True five-theme reality scores are not yet persisted; v1 uses stored evidence/risk/diagnosis proxies.",
                "Small domains are directional and should not be compared as statistically robust market conclusions.",
            ],
        },
    }
    report["executive_summary"] = _build_executive_summary(report)
    return report


def _esc(value: Any) -> str:
    return html.escape(str(value or ""))


def _list_html(items: list[Any], empty: str = "No items available.") -> str:
    clean = [item for item in items if item]
    if not clean:
        return f"<p class=\"muted\">{_esc(empty)}</p>"
    return "<ul>" + "".join(f"<li>{_esc(item)}</li>" for item in clean) + "</ul>"


def _badge(label: str) -> str:
    return f"<span class=\"badge\">{_esc(label)}</span>"


def render_market_readiness_html(report: dict) -> str:
    generated = report.get("generated_at") or _now_iso()
    meta = report.get("metadata") or {}
    executive = report.get("executive_summary") or {}
    warning_html = _list_html(report.get("warnings") or [], "No generation warnings.")

    theme_cards = ""
    for item in report.get("current_market_challenges") or []:
        theme_cards += f"""
        <article class="card">
          <div class="eyebrow">{_esc(item.get('conclusion_label'))}</div>
          <h3>{_esc(item.get('theme'))}</h3>
          <p>{_esc(item.get('observed_today'))}</p>
          <p><strong>Why it matters:</strong> {_esc(item.get('why_it_matters'))}</p>
          <p><strong>May need now:</strong> {_esc(item.get('companies_may_need_now'))}</p>
          <p><strong>May be needed next:</strong> {_esc(item.get('may_be_needed_next'))}</p>
          <div class="meta-line">{_badge(item.get('confidence'))} {_badge(f"sample {item.get('sample_size', 0)}")}</div>
          {_list_html(item.get('recurring_challenges') or [], 'No recurring challenge text isolated.')}
        </article>
        """

    domain_rows = "".join(
        f"<tr><td>{_esc(row.get('domain'))}</td><td>{_esc(row.get('company_count'))}</td>"
        f"<td>{_esc(row.get('sample_confidence'))}</td><td>{_esc(row.get('current_operating_position'))}</td>"
        f"<td>{_esc(row.get('financial_ability_to_respond'))}</td>"
        f"<td>{_esc('; '.join(f'{k}: {v}' for k, v in (row.get('vulnerability_distribution') or {}).items()))}</td>"
        f"<td>{_esc(row.get('likely_next_need'))}</td></tr>"
        for row in report.get("domain_scorecards") or []
    )

    arena_rows = "".join(
        f"<tr><td>{_esc(row.get('external_change'))}</td><td>{_esc(row.get('capability_affected'))}</td>"
        f"<td>{_esc(row.get('potential_emerging_need'))}</td><td>{_esc(', '.join(row.get('affected_domains') or []))}</td>"
        f"<td>{_esc(row.get('horizon'))}</td><td>{_esc(row.get('confidence'))}</td></tr>"
        for row in (report.get("future_demand_analysis") or {}).get("emerging_demand_arenas") or []
    )

    hypotheses = ""
    for item in report.get("emerging_need_hypotheses") or []:
        hypotheses += f"""
        <article class="card">
          <div class="eyebrow">{_esc(item.get('conclusion_label'))}</div>
          <h3>{_esc(item.get('potential_emerging_need'))}</h3>
          <p><strong>Observed condition:</strong> {_esc(item.get('observed_current_condition'))}</p>
          <p><strong>External trigger:</strong> {_esc(item.get('external_trigger'))}</p>
          <p><strong>Business consequence:</strong> {_esc(item.get('business_consequence'))}</p>
          <p><strong>Validation question:</strong> {_esc(item.get('validation_question'))}</p>
          <p><strong>Disconfirming evidence:</strong> {_esc(item.get('disconfirming_evidence'))}</p>
          <div class="meta-line">{_badge(item.get('timing'))} {_badge(item.get('need_urgency'))} {_badge(f"ability: {item.get('ability_to_act')}")}</div>
        </article>
        """

    observatory = report.get("strategic_observatory") or {}
    force_html = "".join(
        f"<li><strong>{_esc(item.get('title'))}</strong>: {_esc(item.get('summary'))}</li>"
        for item in observatory.get("global_change_landscape") or []
    )
    domain_briefs = "".join(
        f"<tr><td>{_esc(row.get('domain'))}</td><td>{_esc(row.get('current_position'))}</td><td>{_esc(row.get('confidence'))}</td></tr>"
        for row in observatory.get("domain_briefs") or []
    )

    patterns = "".join(
        f"<tr><td>{_esc(row.get('label'))}</td><td>{_esc(row.get('company_prevalence'))}</td>"
        f"<td>{_esc(row.get('primary_dimension'))}</td><td>{_esc(', '.join(row.get('domains') or []))}</td></tr>"
        for row in report.get("strategic_challenge_patterns") or []
    )

    css = """
    body{margin:0;background:#0f172a;color:#e2e8f0;font-family:Inter,ui-sans-serif,system-ui,-apple-system,BlinkMacSystemFont,"Segoe UI",sans-serif;line-height:1.55}
    .page{max-width:1180px;margin:0 auto;padding:42px 28px 80px}
    .hero{border-bottom:1px solid rgba(148,163,184,.25);padding-bottom:28px;margin-bottom:28px}
    .kicker,.eyebrow{font-size:11px;text-transform:uppercase;letter-spacing:.16em;color:#67e8f9;font-weight:700}
    h1{font-size:40px;margin:8px 0 14px;color:#fff;letter-spacing:0}
    h2{font-size:24px;margin:34px 0 14px;color:#fff}
    h3{font-size:17px;margin:4px 0 10px;color:#f8fafc}
    p{margin:8px 0;color:#cbd5e1}
    .muted{color:#94a3b8}
    .grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(260px,1fr));gap:14px}
    .card{border:1px solid rgba(148,163,184,.2);background:rgba(15,23,42,.72);border-radius:8px;padding:16px}
    .callout{border:1px solid rgba(103,232,249,.3);background:rgba(8,145,178,.1);border-radius:8px;padding:16px;margin:18px 0}
    .warning{border-color:rgba(251,191,36,.35);background:rgba(180,83,9,.1)}
    .meta-line{display:flex;flex-wrap:wrap;gap:6px;margin-top:12px}
    .badge{display:inline-flex;border:1px solid rgba(148,163,184,.28);border-radius:999px;padding:2px 7px;font-size:10px;text-transform:uppercase;color:#bae6fd}
    table{width:100%;border-collapse:collapse;margin:12px 0 20px;font-size:13px}
    th,td{border-bottom:1px solid rgba(148,163,184,.18);text-align:left;padding:9px 8px;vertical-align:top}
    th{color:#93c5fd;font-size:11px;text-transform:uppercase;letter-spacing:.08em}
    ul{padding-left:19px;color:#cbd5e1}
    li{margin:5px 0}
    .footer{margin-top:46px;padding-top:18px;border-top:1px solid rgba(148,163,184,.18);font-size:11px;color:#64748b}
    """

    return f"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1" />
  <title>{_esc(report.get('title'))}</title>
  <style>{css}</style>
</head>
<body>
  <main class="page">
    <header class="hero">
      <div class="kicker">Admin-only strategic intelligence</div>
      <h1>{_esc(report.get('title'))}</h1>
      <p>{_esc(report.get('method_note'))}</p>
      <div class="meta-line">{_badge(f"{meta.get('company_count', 0)} companies")} {_badge(f"{meta.get('domain_count', 0)} domains")} {_badge(f"Generated {generated[:10]}")}</div>
    </header>

    <section class="callout">
      <div class="eyebrow">Governance</div>
      <p>{_esc(report.get('governance_statement'))}</p>
    </section>

    <section>
      <h2>Executive Summary</h2>
      <div class="grid">
        <article class="card"><div class="eyebrow">Observed today</div><h3>Market Position Today</h3><p>{_esc(executive.get('market_position_today'))}</p></article>
        <article class="card"><div class="eyebrow">External change pressure</div><h3>What Is Changing</h3><p>{_esc(executive.get('what_is_changing'))}</p></article>
        <article class="card"><div class="eyebrow">Validation required</div><h3>Executive Implication</h3><p><strong>Now:</strong> {_esc((executive.get('executive_implication') or {}).get('now'))}</p><p><strong>12-24 months:</strong> {_esc((executive.get('executive_implication') or {}).get('12_24_months'))}</p><p><strong>24-36 months:</strong> {_esc((executive.get('executive_implication') or {}).get('24_36_months'))}</p></article>
      </div>
    </section>

    <section>
      <h2>Current Market Challenges Across Five Themes</h2>
      <div class="grid">{theme_cards}</div>
    </section>

    <section>
      <h2>Top 10 Recurring Strategic Challenge Patterns</h2>
      <table><thead><tr><th>Pattern</th><th>Companies</th><th>Primary Theme</th><th>Domains</th></tr></thead><tbody>{patterns}</tbody></table>
    </section>

    <section>
      <h2>Current Status by Business Domain</h2>
      <table><thead><tr><th>Domain</th><th>N</th><th>Confidence</th><th>Current Position</th><th>Ability to Act</th><th>Vulnerability View</th><th>Likely Next Need</th></tr></thead><tbody>{domain_rows}</tbody></table>
    </section>

    <section>
      <h2>Market Intelligence: Future Demand Analysis</h2>
      <p class="muted">This section keeps the foresight, cross-impact, concentration, and domain-exposure views. Economy context is handled in the Observatory section.</p>
      <table><thead><tr><th>External Change</th><th>Capability Affected</th><th>Potential Emerging Need</th><th>Affected Domains</th><th>Horizon</th><th>Confidence</th></tr></thead><tbody>{arena_rows}</tbody></table>
    </section>

    <section>
      <h2>Strategic Observatory</h2>
      <div class="grid">
        <article class="card"><div class="eyebrow">Global change landscape</div><ul>{force_html or '<li>No force clusters available.</li>'}</ul></article>
        <article class="card"><div class="eyebrow">Finnish market implications</div><p>{_esc(observatory.get('finnish_market_implications'))}</p></article>
      </div>
      <table><thead><tr><th>Domain</th><th>Compact Brief</th><th>Confidence</th></tr></thead><tbody>{domain_briefs}</tbody></table>
    </section>

    <section>
      <h2>Emerging Need Hypotheses</h2>
      <div class="grid">{hypotheses}</div>
    </section>

    <section class="callout warning">
      <div class="eyebrow">Warnings and limitations</div>
      {warning_html}
      {_list_html((report.get('appendices') or {}).get('raw_limitations') or [])}
    </section>

    <footer class="footer">Generated by The Scanner. Admin-only advisory analysis; not evidence of confirmed buying intent.</footer>
  </main>
</body>
</html>"""
