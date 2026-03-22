"""
Market Intelligence Aggregation Engine
=======================================
Aggregates cross-company data into strategic intelligence views:
  1. TOP10 Foresights (future demand signals from FP themes)
  2. TOP10 Root Causes / Methods (clustered why/what proposals)
  3. Gap Pattern Analysis (per-dimension intent vs reality gaps)
  4. Archetype Distribution by Domain
  5. Theme x Dimension Cross-Impact Matrix
  6. Industry Foresight Radar (per-domain theme category scores)
  7. Market Health Gauge (composite per-domain health score)
  8. Foresight Coverage Meter (database maturity indicator)
  9. Most Vulnerable Companies (from regression residuals + archetypes)
  10. Demand Intensity per KB Dimension
  11. Foresight-Adjusted Vulnerability Index (FAVI)

Uses existing data structures — no new scanning or API calls required
(except optional Gemini clustering for proposal analysis).
"""

import json
import math
import statistics
from collections import Counter, defaultdict

from analyze_scores import run_analysis, classify_archetype
from foresight_vulnerability import compute_favi_index


# ═══════════════════════════════════════════════════════════════════════════════
# THEME CATEGORY MAPPING
# ═══════════════════════════════════════════════════════════════════════════════

THEME_CATEGORIES = {
    "Artificial Intelligence": "Technology & Digital",
    "Data & Digitalisation": "Technology & Digital",
    "Communication & Media": "Technology & Digital",
    "Business": "Economy & Business",
    "Finance & Ownership": "Economy & Business",
    "Global Economy": "Economy & Business",
    "Digital & Sharing Economies": "Economy & Business",
    "Services": "Economy & Business",
    "Industry & Manufacturing": "Industry & Infrastructure",
    "Construction & Urbanisation": "Industry & Infrastructure",
    "Energy": "Industry & Infrastructure",
    "Freight & Logistics": "Industry & Infrastructure",
    "Private & Public Transport": "Industry & Infrastructure",
    "Climate Change": "Environment & Resources",
    "Sustainability & Recycling": "Environment & Resources",
    "Nature": "Environment & Resources",
    "Food": "Environment & Resources",
    "Water & Oceans": "Environment & Resources",
    "Public Governance": "Society & Governance",
    "Geopolitics": "Society & Governance",
    "Security & Safety": "Society & Governance",
    "Values & Ethics": "Society & Governance",
    "Society": "Society & Governance",
    "Health & Wellbeing": "People & Well-being",
    "Education & Knowledge": "People & Well-being",
    "Work & Income": "People & Well-being",
    "Management & HR": "People & Well-being",
    "Leisure & Lifestyles": "People & Well-being",
    "Sciences & Research": "Science & Demographics",
    "Population & Demographics": "Science & Demographics",
}

ALL_CATEGORIES = [
    "Technology & Digital",
    "Economy & Business",
    "Industry & Infrastructure",
    "Environment & Resources",
    "Society & Governance",
    "People & Well-being",
    "Science & Demographics",
]

KB_DIMENSIONS = [
    "Operating Model",
    "Service Portfolio",
    "Values",
    "Strategy",
    "AI Readiness",
]


# ═══════════════════════════════════════════════════════════════════════════════
# 1. TOP10 FORESIGHTS
# ═══════════════════════════════════════════════════════════════════════════════

def compute_top_foresights(company_details):
    """Count FP theme occurrences across all companies' foresight_links."""
    theme_counts = Counter()
    theme_companies = defaultdict(set)

    for company in company_details:
        name = company.get("companyName", "?")
        foresight_links = company.get("foresight_links") or []
        for link in foresight_links:
            theme = link.get("fp_theme", "").strip()
            if theme and theme in THEME_CATEGORIES:
                theme_counts[theme] += 1
                theme_companies[theme].add(name)

    total_signals = sum(theme_counts.values()) or 1
    top = []
    for theme, count in theme_counts.most_common(10):
        top.append({
            "theme": theme,
            "category": THEME_CATEGORIES.get(theme, "Unknown"),
            "count": count,
            "pct": round(count / total_signals * 100, 1),
            "example_companies": sorted(list(theme_companies[theme]))[:5],
        })
    return top


# ═══════════════════════════════════════════════════════════════════════════════
# 2. TOP10 ROOT CAUSES / METHODS
# ═══════════════════════════════════════════════════════════════════════════════

def compute_top_causes(company_details):
    """Extract and cluster why_proposals and what_proposals across companies.

    Uses keyword-based clustering (no LLM call required).
    Groups proposals by recurring strategic patterns.
    """
    why_items = []
    what_items = []

    for company in company_details:
        name = company.get("companyName", "?")
        for proposal in (company.get("why_proposals") or []):
            if proposal and isinstance(proposal, str) and len(proposal) > 10:
                why_items.append({"text": proposal, "company": name})
        for proposal in (company.get("what_proposals") or []):
            if proposal and isinstance(proposal, str) and len(proposal) > 10:
                what_items.append({"text": proposal, "company": name})

    # Simple keyword-based clustering
    CLUSTERS = {
        "Digital Transformation": ["digital", "digitali", "automation", "automate"],
        "AI & Data Strategy": ["ai ", "artificial intelligence", "machine learning", "data-driven", "data platform"],
        "Platform & API Modernization": ["platform", "api", "saas", "cloud", "microservice"],
        "Customer-Centric Innovation": ["customer", "user experience", "client", "personali"],
        "Operational Excellence": ["operating model", "operational", "efficiency", "lean", "agile", "devops"],
        "Sustainability & ESG": ["sustainab", "esg", "climate", "carbon", "circular", "green"],
        "Talent & Culture": ["talent", "culture", "employee", "workforce", "people", "hr"],
        "Business Model Innovation": ["business model", "revenue model", "monetiz", "recurring revenue"],
        "Strategic Positioning": ["strategy", "competitive", "market position", "differenti", "growth"],
        "Supply Chain & Operations": ["supply chain", "logistics", "procurement", "manufacturing"],
    }

    cluster_results = []
    for cluster_name, keywords in CLUSTERS.items():
        matched_why = []
        matched_what = []
        for item in why_items:
            text_lower = item["text"].lower()
            if any(kw in text_lower for kw in keywords):
                matched_why.append(item)
        for item in what_items:
            text_lower = item["text"].lower()
            if any(kw in text_lower for kw in keywords):
                matched_what.append(item)

        total = len(matched_why) + len(matched_what)
        if total > 0:
            examples = []
            for m in (matched_why + matched_what)[:6]:
                examples.append({"text": m["text"][:300], "company": m["company"]})
            cluster_results.append({
                "cluster_label": cluster_name,
                "count": total,
                "why_count": len(matched_why),
                "what_count": len(matched_what),
                "examples": examples,
            })

    cluster_results.sort(key=lambda x: x["count"], reverse=True)
    return cluster_results[:10]


# ═══════════════════════════════════════════════════════════════════════════════
# 3. GAP PATTERN ANALYSIS
# ═══════════════════════════════════════════════════════════════════════════════

def compute_gap_patterns(company_details):
    """Per KB dimension, compute average intent vs reality gap."""
    dim_scores = defaultdict(lambda: {"intent_vals": [], "reality_vals": []})

    for company in company_details:
        reality = company.get("reality_score")
        breakdown = company.get("intent_breakdown") or []
        for dim_score in breakdown:
            dim_name = dim_score.get("name", "")
            score = dim_score.get("score")
            if dim_name and score is not None:
                dim_scores[dim_name]["intent_vals"].append(float(score))
                # Use company-level reality as proxy per dimension
                if reality is not None:
                    dim_scores[dim_name]["reality_vals"].append(float(reality))

    patterns = []
    for dim in KB_DIMENSIONS:
        data = dim_scores.get(dim)
        if not data or not data["intent_vals"]:
            continue
        avg_intent = statistics.mean(data["intent_vals"])
        avg_reality = statistics.mean(data["reality_vals"]) if data["reality_vals"] else 0
        patterns.append({
            "dimension": dim,
            "avg_intent": round(avg_intent, 1),
            "avg_reality": round(avg_reality, 1),
            "avg_gap": round(avg_intent - avg_reality, 1),
            "company_count": len(data["intent_vals"]),
        })

    patterns.sort(key=lambda x: x["avg_gap"], reverse=True)
    return patterns


# ═══════════════════════════════════════════════════════════════════════════════
# 4. ARCHETYPE DISTRIBUTION BY DOMAIN
# ═══════════════════════════════════════════════════════════════════════════════

def compute_archetype_flow(dashboard_data):
    """Cross-tabulate archetype x domain."""
    domain_archetypes = defaultdict(lambda: {"Thriving": 0, "Modernizing": 0, "Cash Cow": 0, "At Risk": 0, "total": 0})

    for company in dashboard_data:
        domain = company.get("domain") or "Unknown"
        reality = company.get("reality_score")
        financial = company.get("financial_index")
        if reality is None:
            continue
        archetype = classify_archetype(
            float(reality),
            float(financial) if financial is not None else float(company.get("intent_score", 0))
        )
        domain_archetypes[domain][archetype] += 1
        domain_archetypes[domain]["total"] += 1

    result = []
    for domain, counts in sorted(domain_archetypes.items(), key=lambda x: x[1]["total"], reverse=True):
        result.append({
            "domain": domain,
            "thriving": counts["Thriving"],
            "modernizing": counts["Modernizing"],
            "cash_cow": counts["Cash Cow"],
            "at_risk": counts["At Risk"],
            "total": counts["total"],
        })
    return result


# ═══════════════════════════════════════════════════════════════════════════════
# 5. THEME x DIMENSION CROSS-IMPACT MATRIX
# ═══════════════════════════════════════════════════════════════════════════════

def compute_cross_impact(company_details):
    """Build theme x dimension signal count matrix from foresight_links."""
    matrix = defaultdict(lambda: defaultdict(int))
    theme_totals = Counter()

    for company in company_details:
        for link in (company.get("foresight_links") or []):
            theme = link.get("fp_theme", "").strip()
            dimension = link.get("kb_dimension", "").strip()
            if theme and dimension and theme in THEME_CATEGORIES:
                matrix[theme][dimension] += 1
                theme_totals[theme] += 1

    # Return as flat list sorted by total signal count
    result = []
    for theme, count in theme_totals.most_common(15):
        for dim in KB_DIMENSIONS:
            cell_count = matrix[theme].get(dim, 0)
            if cell_count > 0:
                result.append({
                    "theme": theme,
                    "dimension": dim,
                    "count": cell_count,
                })

    return result


# ═══════════════════════════════════════════════════════════════════════════════
# 6. INDUSTRY FORESIGHT RADAR
# ═══════════════════════════════════════════════════════════════════════════════

def compute_industry_radar(company_details, dashboard_data):
    """Per domain, count signals per theme category (7 categories)."""
    # Map company names to domains
    company_domains = {}
    for c in dashboard_data:
        name = c.get("companyName", "")
        domain = c.get("domain") or "Unknown"
        company_domains[name.lower()] = domain

    domain_categories = defaultdict(lambda: defaultdict(int))
    domain_totals = defaultdict(int)

    for company in company_details:
        name = company.get("companyName", "?")
        domain = company_domains.get(name.lower(), "Unknown")
        for link in (company.get("foresight_links") or []):
            theme = link.get("fp_theme", "").strip()
            cat = THEME_CATEGORIES.get(theme)
            if cat:
                domain_categories[domain][cat] += 1
                domain_totals[domain] += 1

    # Return top domains by signal count
    result = []
    for domain, total in sorted(domain_totals.items(), key=lambda x: x[1], reverse=True)[:8]:
        scores = {}
        for cat in ALL_CATEGORIES:
            scores[cat] = domain_categories[domain].get(cat, 0)
        # Normalize to 0-100 scale (relative to max in this domain)
        max_val = max(scores.values()) or 1
        normalized = {cat: round(val / max_val * 100) for cat, val in scores.items()}
        result.append({
            "domain": domain,
            "raw_scores": scores,
            "normalized_scores": normalized,
            "total_signals": total,
        })

    return result


# ═══════════════════════════════════════════════════════════════════════════════
# 7. MARKET HEALTH GAUGE
# ═══════════════════════════════════════════════════════════════════════════════

def compute_market_health(dashboard_data):
    """Composite health score per domain."""
    domains = defaultdict(list)
    for c in dashboard_data:
        domain = c.get("domain") or "Unknown"
        reality = c.get("reality_score")
        financial = c.get("financial_index")
        gap = c.get("reality_gap")
        if reality is None:
            continue
        domains[domain].append({
            "reality": float(reality),
            "financial": float(financial) if financial is not None else None,
            "gap": float(gap) if gap is not None else None,
        })

    result = []
    for domain, companies in sorted(domains.items(), key=lambda x: len(x[1]), reverse=True):
        n = len(companies)
        avg_reality = statistics.mean([c["reality"] for c in companies])
        fin_vals = [c["financial"] for c in companies if c["financial"] is not None]
        avg_financial = statistics.mean(fin_vals) if fin_vals else 0
        gap_vals = [abs(c["gap"]) for c in companies if c["gap"] is not None]
        avg_gap = statistics.mean(gap_vals) if gap_vals else 0

        # Count archetypes for thriving percentage
        thriving = sum(1 for c in companies
                       if c["reality"] >= 50 and c.get("financial") is not None and c["financial"] >= 50)
        thriving_pct = (thriving / n * 100) if n > 0 else 0

        # Composite: reality(40%) + financial(30%) + low_gap(15%) + thriving(15%)
        score = (avg_reality * 0.4 +
                 avg_financial * 0.3 +
                 max(0, 100 - avg_gap) * 0.15 +
                 thriving_pct * 0.15)

        result.append({
            "domain": domain,
            "score": round(score, 1),
            "avg_reality": round(avg_reality, 1),
            "avg_financial": round(avg_financial, 1),
            "avg_gap": round(avg_gap, 1),
            "thriving_pct": round(thriving_pct, 1),
            "company_count": n,
        })

    result.sort(key=lambda x: x["score"], reverse=True)
    return result


# ═══════════════════════════════════════════════════════════════════════════════
# 8. FORESIGHT COVERAGE METER
# ═══════════════════════════════════════════════════════════════════════════════

def compute_foresight_coverage(company_details):
    """How many of the 30 FP themes have strong signals?"""
    theme_company_count = defaultdict(set)

    for company in company_details:
        name = company.get("companyName", "?")
        for link in (company.get("foresight_links") or []):
            theme = link.get("fp_theme", "").strip()
            if theme and theme in THEME_CATEGORIES:
                theme_company_count[theme].add(name)

    themes_detail = []
    covered = 0
    for theme in THEME_CATEGORIES.keys():
        companies = theme_company_count.get(theme, set())
        signal_count = len(companies)
        if signal_count >= 3:
            covered += 1
        themes_detail.append({
            "theme": theme,
            "category": THEME_CATEGORIES[theme],
            "signal_count": signal_count,
        })

    themes_detail.sort(key=lambda x: x["signal_count"], reverse=True)

    return {
        "covered": covered,
        "total": 30,
        "pct": round(covered / 30 * 100, 1),
        "themes_detail": themes_detail,
    }


# ═══════════════════════════════════════════════════════════════════════════════
# 9. MOST VULNERABLE COMPANIES
# ═══════════════════════════════════════════════════════════════════════════════

def compute_vulnerable_companies(dashboard_data, analysis_result):
    """Identify most vulnerable companies using regression residuals + archetypes + gaps."""
    if not analysis_result or "error" in analysis_result:
        return []

    # Get residuals from reality vs financial regression
    residuals_data = {}
    rf = analysis_result.get("reality_vs_financial", {})
    for r in rf.get("residuals", []):
        residuals_data[r["name"]] = r["residual"]

    # Also compute residuals for ALL companies (not just top 5 from analyze)
    all_residuals = {}
    paired = []
    for c in dashboard_data:
        reality = c.get("reality_score")
        financial = c.get("financial_index")
        if reality is not None and financial is not None:
            paired.append((c.get("companyName", "?"), float(reality), float(financial)))

    if len(paired) >= 5:
        realities = [p[1] for p in paired]
        financials = [p[2] for p in paired]
        mx, my = statistics.mean(realities), statistics.mean(financials)
        sx = statistics.stdev(realities) if len(realities) > 1 else 1
        sy = statistics.stdev(financials) if len(financials) > 1 else 1
        if sx > 0 and sy > 0:
            from analyze_scores import pearson
            r_p, _ = pearson(realities, financials)
            if r_p is not None:
                slope = r_p * (sy / sx)
                intercept = my - slope * mx
                for name, reality, financial in paired:
                    predicted = slope * reality + intercept
                    all_residuals[name] = round(financial - predicted, 1)

    # Build vulnerability scores
    vulnerability = []
    max_gap = max((abs(c.get("reality_gap") or 0) for c in dashboard_data), default=1) or 1
    max_residual = max((abs(v) for v in all_residuals.values()), default=1) or 1

    for c in dashboard_data:
        name = c.get("companyName", "?")
        reality = c.get("reality_score")
        financial = c.get("financial_index")
        gap = c.get("reality_gap") or 0
        domain = c.get("domain") or "Unknown"

        if reality is None:
            continue

        archetype = classify_archetype(
            float(reality),
            float(financial) if financial is not None else float(c.get("intent_score", 0))
        )

        residual = all_residuals.get(name, 0)
        at_risk = 1.0 if archetype == "At Risk" else (0.5 if archetype == "Cash Cow" else 0.0)

        # Normalize components to 0-1
        residual_norm = max(0, -residual) / max_residual  # Only negative residuals contribute
        gap_norm = max(0, gap) / max_gap  # Only positive gaps (washing) contribute

        # Composite vulnerability
        vuln_score = (residual_norm * 0.4) + (gap_norm * 0.3) + (at_risk * 0.3)

        reasons = []
        if residual < -5:
            reasons.append(f"Financial underperforms prediction by {abs(residual):.0f}pts")
        if gap > 10:
            reasons.append(f"Reality gap of {gap:.0f} (washing risk)")
        if archetype == "At Risk":
            reasons.append("At Risk archetype (low reality + low financial)")
        elif archetype == "Cash Cow":
            reasons.append("Cash Cow archetype (profitable but operationally weak)")

        if vuln_score > 0.05:  # Only include companies with some vulnerability signal
            vulnerability.append({
                "name": name,
                "domain": domain,
                "vulnerability_score": round(vuln_score * 100, 1),
                "residual": residual,
                "gap": round(gap, 1),
                "archetype": archetype,
                "reality_score": round(float(reality), 1),
                "financial_index": round(float(financial), 1) if financial is not None else None,
                "reasons": reasons,
            })

    vulnerability.sort(key=lambda x: x["vulnerability_score"], reverse=True)
    return vulnerability[:30]


# ═══════════════════════════════════════════════════════════════════════════════
# 10. DEMAND INTENSITY PER KB DIMENSION
# ═══════════════════════════════════════════════════════════════════════════════

def compute_demand_intensity(company_details, dashboard_data):
    """Average gap per KB dimension — bigger gap = bigger demand."""
    dim_gaps = defaultdict(list)

    for company in company_details:
        reality = company.get("reality_score")
        breakdown = company.get("intent_breakdown") or []
        for dim_score in breakdown:
            dim_name = dim_score.get("name", "")
            score = dim_score.get("score")
            if dim_name and score is not None and reality is not None:
                gap = float(score) - float(reality)
                dim_gaps[dim_name].append(gap)

    result = []
    for dim in KB_DIMENSIONS:
        gaps = dim_gaps.get(dim, [])
        if not gaps:
            continue
        result.append({
            "dimension": dim,
            "avg_gap": round(statistics.mean(gaps), 1),
            "max_gap": round(max(gaps), 1),
            "companies_with_gap": sum(1 for g in gaps if g > 0),
            "total_companies": len(gaps),
        })

    result.sort(key=lambda x: x["avg_gap"], reverse=True)
    return result


# ═══════════════════════════════════════════════════════════════════════════════
# MAIN ORCHESTRATOR
# ═══════════════════════════════════════════════════════════════════════════════

def compute_market_intelligence(dashboard_data, company_details, analysis_result):
    """Compute all market intelligence aggregations.

    Args:
        dashboard_data: Lightweight summary list (from index/summary_list)
        company_details: Full detailed data per company (with foresight_links, proposals, etc.)
        analysis_result: Output from run_analysis() (statistical correlations, residuals)

    Returns:
        Structured dict with all market intelligence sections.
    """
    print(f"\n   [Market Intel] Computing market intelligence for {len(dashboard_data)} companies...")
    print(f"   [Market Intel] {len(company_details)} companies with detailed data available")

    result = {
        "company_count": len(dashboard_data),
        "detailed_count": len(company_details),
    }

    # 1. TOP10 Foresights
    result["top_foresights"] = compute_top_foresights(company_details)
    print(f"   [Market Intel] TOP10 Foresights: {len(result['top_foresights'])} themes identified")

    # 2. TOP10 Root Causes
    result["top_causes"] = compute_top_causes(company_details)
    print(f"   [Market Intel] TOP10 Root Causes: {len(result['top_causes'])} clusters identified")

    # 3. Gap Patterns
    result["gap_patterns"] = compute_gap_patterns(company_details)
    print(f"   [Market Intel] Gap patterns: {len(result['gap_patterns'])} dimensions analyzed")

    # 4. Archetype Flow
    result["archetype_flow"] = compute_archetype_flow(dashboard_data)
    print(f"   [Market Intel] Archetype flow: {len(result['archetype_flow'])} domains mapped")

    # 5. Cross-Impact Matrix
    result["cross_impact"] = compute_cross_impact(company_details)
    print(f"   [Market Intel] Cross-impact matrix: {len(result['cross_impact'])} cells populated")

    # 6. Industry Foresight Radar
    result["industry_radar"] = compute_industry_radar(company_details, dashboard_data)
    print(f"   [Market Intel] Industry radar: {len(result['industry_radar'])} domains profiled")

    # 7. Market Health Gauge
    result["market_health"] = compute_market_health(dashboard_data)
    print(f"   [Market Intel] Market health: {len(result['market_health'])} domain gauges computed")

    # 8. Foresight Coverage
    result["foresight_coverage"] = compute_foresight_coverage(company_details)
    print(f"   [Market Intel] Foresight coverage: {result['foresight_coverage']['covered']}/30 themes covered")

    # 9. Vulnerable Companies
    result["vulnerable_companies"] = compute_vulnerable_companies(dashboard_data, analysis_result)
    print(f"   [Market Intel] Vulnerable companies: {len(result['vulnerable_companies'])} identified")

    # 10. Demand Intensity
    result["demand_intensity"] = compute_demand_intensity(company_details, dashboard_data)
    print(f"   [Market Intel] Demand intensity: {len(result['demand_intensity'])} dimensions scored")

    # 11. Foresight-Adjusted Vulnerability Index (FAVI)
    favi_result = compute_favi_index(
        company_details, dashboard_data,
        result["vulnerable_companies"], result["top_foresights"]
    )
    result["favi_companies"] = favi_result["favi_companies"]
    result["favi_segments"] = favi_result["favi_segments"]
    result["favi_domain_pressure"] = favi_result["favi_domain_pressure"]
    favi_with_score = [c for c in favi_result["favi_companies"] if c["favi_score"] > 0]
    print(f"   [Market Intel] FAVI: {len(favi_with_score)} companies scored, segments: {favi_result['favi_segments']}")

    print(f"   [Market Intel] ✅ Market intelligence computation complete\n")
    return result
