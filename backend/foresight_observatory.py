"""
Foresight Observatory — Three-Layer Strategic Analysis Engine
=============================================================
Synthesizes existing Scanner data into macro-level strategic intelligence:
  1. Global Force Landscape — futurologist's briefing on converging forces
  2. Finnish Economy Force Map — how forces reshape Finnish business
  3. Domain Force Dossiers — per-domain analysis with timescale mapping

Model pipeline:
  - Sonnet 4.6: Primary generation (narrative synthesis)
  - Gemini 3.1 Pro: Verification (fact-check, framing compliance, structure)

All data comes from existing DB — no new scanning or web search required.
"""

import json
import os
import re
import time
import statistics
from collections import Counter, defaultdict
from datetime import datetime

import anthropic
from google.genai import types as genai_types

from gemini_client import get_gemini_client
from gemini_backoff import call_with_503_backoff
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

EVIDENCE RULE — Every claim must trace to the data provided. Include specific numbers when available (e.g., "60% of Finnish industrial companies sit in Cash Cow archetype"). Do not invent statistics."""


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
    total = 0

    for theme_slug, theme_data in all_kb.items():
        signals = theme_data.get("signals", [])
        for sig in signals:
            fp_theme = sig.get("fp_theme", "")
            category = THEME_CATEGORIES.get(fp_theme, "Unknown")
            by_category[category].append({
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
            })
            by_timeframe[sig.get("timeframe", "unknown")] += 1
            by_force_type[sig.get("force_type", "unknown")] += 1
            by_lifecycle[sig.get("lifecycle_stage", "unknown")] += 1
            by_momentum[sig.get("trend_momentum", "unknown")] += 1
            by_credibility[sig.get("source_credibility", "unknown")] += 1
            total += 1

    # Count accelerating vs decelerating
    accelerating = by_force_type.get("Probable/Emerging", 0) + by_force_type.get("Plausible/Maturing", 0)
    decelerating = by_force_type.get("Weakening/Legacy", 0) + by_force_type.get("Weakening/Declining", 0)

    return {
        "total_signals": total,
        "by_category": dict(by_category),
        "by_timeframe": dict(by_timeframe),
        "by_force_type": dict(by_force_type),
        "by_lifecycle": dict(by_lifecycle),
        "by_momentum": dict(by_momentum),
        "by_credibility": dict(by_credibility),
        "accelerating": accelerating,
        "decelerating": decelerating,
        "theme_count": len(all_kb),
        "high_confidence_count": by_credibility.get("high", 0),
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
    print(f"   [Observatory] Companies: {len(dashboard_data)} summary, {len(company_details)} detailed")

    # 3. Market intelligence
    analysis_result = run_analysis(dashboard_data) if dashboard_data else {}
    mi = compute_market_intelligence(dashboard_data, company_details, analysis_result) if dashboard_data else {}

    return {
        "all_kb": all_kb,
        "kb_aggregate": kb_aggregate,
        "dashboard_data": dashboard_data,
        "company_details": company_details,
        "market_intel": mi,
    }


def _aggregate_stakeholder_dynamics(company_details: list, gap_patterns: list) -> dict:
    """Aggregate stakeholder data for MACTOR-lite analysis.

    Cross-references key_people topics with KB dimensions and foresight themes
    to identify which organizational roles champion or resist which forces.
    """
    # Role classification buckets
    ROLE_BUCKETS = {
        "C-Suite / Executive": ["ceo", "cfo", "cto", "cio", "coo", "chief", "president", "managing director", "vd"],
        "Board / Governance": ["board", "chairman", "chairwoman", "director", "non-executive"],
        "Technology / Innovation": ["technology", "innovation", "digital", "data", "architect", "engineering", "r&d"],
        "Strategy / Commercial": ["strategy", "commercial", "business development", "marketing", "sales", "growth"],
        "Operations / HR": ["operations", "hr", "human resources", "people", "talent", "supply chain", "logistics"],
    }

    def classify_role(role_str: str) -> str:
        role_lower = (role_str or "").lower()
        for bucket, keywords in ROLE_BUCKETS.items():
            if any(kw in role_lower for kw in keywords):
                return bucket
        return "Other"

    # Dimension keywords for topic matching
    DIM_KEYWORDS = {
        "Operating Model": ["agile", "lean", "operating model", "organization", "team", "autonomy", "devops", "process"],
        "Service Portfolio": ["product", "service", "saas", "platform", "portfolio", "offering", "revenue model"],
        "Values": ["culture", "values", "trust", "diversity", "inclusion", "wellbeing", "sustainability", "esg"],
        "Strategy": ["strategy", "growth", "market", "competitive", "vision", "transformation", "future"],
        "AI Readiness": ["ai", "artificial intelligence", "data", "machine learning", "automation", "digital", "cloud"],
    }

    # Aggregate: role_bucket → dimension affinity counts
    role_dim_affinity = defaultdict(lambda: defaultdict(int))
    role_counts = Counter()
    stakeholder_count = 0
    enriched_count = 0

    for company in company_details:
        for person in (company.get("key_people") or []):
            stakeholder_count += 1
            role = person.get("role", "")
            bucket = classify_role(role)
            role_counts[bucket] += 1

            insights = person.get("linkedin_insights") or {}
            if not insights.get("found"):
                continue
            enriched_count += 1

            topics = insights.get("topics") or []
            headline = (insights.get("headline") or "").lower()
            all_text = " ".join(topics).lower() + " " + headline

            for dim, keywords in DIM_KEYWORDS.items():
                if any(kw in all_text for kw in keywords):
                    role_dim_affinity[bucket][dim] += 1

    # Build the stakeholder-dimension matrix as readable text
    matrix_lines = []
    for bucket in ROLE_BUCKETS:
        if role_counts.get(bucket, 0) == 0:
            continue
        affinities = role_dim_affinity.get(bucket, {})
        if not affinities:
            matrix_lines.append(f"- {bucket} ({role_counts[bucket]} people): No dimension affinity signals detected")
            continue
        top_dims = sorted(affinities.items(), key=lambda x: x[1], reverse=True)
        dim_str = ", ".join(f"{d}={c}" for d, c in top_dims)
        matrix_lines.append(f"- {bucket} ({role_counts[bucket]} people): focus on {dim_str}")

    # Identify dimension gaps that lack stakeholder champions
    championed_dims = set()
    for bucket_affinities in role_dim_affinity.values():
        for dim, count in bucket_affinities.items():
            if count >= 2:
                championed_dims.add(dim)

    unchampioned = [dim for dim in KB_DIMENSIONS if dim not in championed_dims]

    # Identify potential alignment conflicts (C-suite vs Tech roles)
    conflicts = []
    csuite = role_dim_affinity.get("C-Suite / Executive", {})
    tech = role_dim_affinity.get("Technology / Innovation", {})
    for dim in KB_DIMENSIONS:
        cs_focus = csuite.get(dim, 0)
        tech_focus = tech.get(dim, 0)
        if cs_focus >= 2 and tech_focus == 0:
            conflicts.append(f"{dim}: strong C-Suite focus but absent from Technology/Innovation discourse")
        elif tech_focus >= 2 and cs_focus == 0:
            conflicts.append(f"{dim}: Technology/Innovation advocates but no C-Suite championship")

    return {
        "stakeholder_count": stakeholder_count,
        "enriched_count": enriched_count,
        "role_distribution": dict(role_counts),
        "matrix_text": "\n".join(matrix_lines),
        "unchampioned_dimensions": unchampioned,
        "alignment_conflicts": conflicts,
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

            line = f"- **{s['title']}** [{s.get('force_type', '?')} | {s.get('timeframe', '?')}]\n"
            line += f"  Description: {s.get('description', 'N/A')}\n"
            line += f"  Lifecycle: {lifecycle} | Momentum: {momentum} | Credibility: {s.get('credibility', '?')} ({source_count} verified sources)\n"
            if dims:
                line += f"  Dimensions affected: {', '.join(dims)}\n"
            if industries:
                line += f"  Industries affected: {', '.join(industries[:4])}"
            lines.append(line)

        blocks.append("\n".join(lines))

    return "\n\n".join(blocks)


def _build_global_prompt(kb_aggregate: dict, cross_impact: list, top_foresights: list) -> str:
    """Build prompt for Output 1: Global Force Landscape."""
    # Rich signal evidence — top signals per category with full descriptions
    signal_evidence = _format_top_signals_per_category(kb_aggregate["by_category"], per_cat=5)

    # Top themes by cross-impact density
    cross_summary = ""
    if cross_impact:
        theme_counts = Counter()
        for cell in cross_impact:
            theme_counts[cell["theme"]] += cell["count"]
        top_cross = theme_counts.most_common(8)
        cross_summary = "\n".join(f"- {t}: {c} cross-dimensional signals" for t, c in top_cross)

    # Top foresights
    foresight_summary = ""
    if top_foresights:
        foresight_summary = "\n".join(
            f"- {f['theme']} ({f['category']}): {f['count']} companies affected ({f['pct']}%)"
            for f in top_foresights[:10]
        )

    return f"""{SYSTEM_ROLE}

TASK: Write a Global Force Landscape briefing — a futurologist's synthesis of the forces reshaping business globally.

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


def _format_stakeholder_block(stakeholder_data: dict = None) -> str:
    """Format stakeholder data for inclusion in prompts."""
    if not stakeholder_data or stakeholder_data.get("enriched_count", 0) == 0:
        return "STAKEHOLDER DATA: Not available (insufficient LinkedIn enrichment data)."

    sd = stakeholder_data
    block = f"""STAKEHOLDER-DIMENSION AFFINITY DATA (from {sd['stakeholder_count']} key stakeholders across all companies, {sd['enriched_count']} with LinkedIn insights):

Role distribution: {json.dumps(sd['role_distribution'])}

Stakeholder-Dimension Matrix (which roles focus on which strategic dimensions):
{sd['matrix_text']}"""

    if sd.get("unchampioned_dimensions"):
        block += f"\n\nUnchampioned dimensions (under external pressure but lacking internal advocates): {', '.join(sd['unchampioned_dimensions'])}"

    if sd.get("alignment_conflicts"):
        block += f"\n\nAlignment conflicts detected:\n" + "\n".join(f"- {c}" for c in sd["alignment_conflicts"])

    return block


def _build_finland_prompt(global_analysis: str, mi: dict, stakeholder_data: dict = None, kb_aggregate: dict = None) -> str:
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

    return f"""{SYSTEM_ROLE}

TASK: Write a Finnish Economy Force Map — how global forces specifically reshape the Finnish business landscape.

TONE CALIBRATION — Balanced Strategic Assessment:
- Maintain intellectual honesty: celebrate genuine strength as clearly as you identify genuine weakness.
- If the data shows domains performing well, say so with conviction. Do not default to pessimism or manufacture risks.
- Only flag risks and challenges that are clearly grounded in the actual data provided. If a domain is thriving, acknowledge it.
- The goal is accurate strategic assessment, not alarm. A balanced view earns more credibility than a uniformly critical one.
- Positive framing is valid: strong archetype distributions, healthy financial indices, and narrow gaps are genuine competitive advantages worth highlighting.

GLOBAL CONTEXT (the analysis you are building upon):
{global_analysis}

SOURCE SIGNAL EVIDENCE (top validated signals per category — use these to ground your analysis in actual source content, cite specific signals when making claims):
{signal_evidence}

FINNISH DATA:

Market Health by Domain:
{health_summary}

Archetype Distribution by Domain:
{arch_summary}

FAVI Segment Distribution (across all companies):
{favi_summary}

Gap Patterns (Intent vs Reality per KB Dimension):
{gap_summary}

Top Foresight Themes Affecting Finnish Companies:
{foresight_summary}

Total companies analyzed: {mi.get('company_count', 0)}
Companies with detailed data: {mi.get('detailed_count', 0)}

{_format_stakeholder_block(stakeholder_data)}

ANALYTICAL STRUCTURE:

Section 1 — THE FINNISH POSITION: Where does Finnish industry stand in the global force landscape? Use the market health scores and archetype distributions to identify strengths and blind spots. Ground every claim in the numbers above. Reference specific signals from the evidence above when identifying which forces are most relevant to Finnish industry.

Section 2 — DOMAIN CONVERGENCE: Which forces cut across multiple Finnish domains? What systemic patterns emerge from the cross-domain data? Where are the unexpected connections? Cite specific signal evidence to support cross-domain patterns.

Section 3 — THE ADAPTABILITY GAP: Use the gap patterns (intent vs reality) to analyze where Finnish companies claim one thing but do another. What does this reveal about organizational thinking capacity? Are Finnish companies running someone else's race?

Section 4 — THREE HORIZONS FOR FINLAND:
- Now (6-12 months): What must Finnish industry address immediately based on the data?
- Prepare (12-24 months): What capabilities need building?
- Position (24-36 months): What "Own Race" opportunities exist?

Section 5 — STAKEHOLDER DYNAMICS (MACTOR-lite analysis):

This section applies Stakeholder Analysis methodology — systematically considering which organizational roles champion or resist specific strategic objectives, to identify potential alliances, conflicts, and intervention points.

Using the stakeholder-dimension affinity data above, analyze:
1. CHAMPIONS AND BLIND SPOTS: Which organizational dimensions have strong stakeholder champions (C-Suite, Technology, Strategy roles actively discussing them)? Which dimensions are "unchampioned" — under pressure from external forces but lacking internal advocates? This reveals where organizational thinking capacity is weakest.
2. ALIGNMENT CONFLICTS: Where do different stakeholder groups pull in different directions? For example, if Technology leaders focus on AI Readiness while C-Suite focuses on Strategy, what does this misalignment mean for the organization's ability to adapt coherently?
3. COALITION POTENTIAL: Based on the role-dimension patterns, where could natural alliances form across Finnish industry? What shared stakeholder dynamics suggest opportunities for cross-company or cross-domain collaboration?

Frame stakeholder dynamics through the lens of organizational thinking capacity — not as political maneuvering but as a diagnostic of whether Finnish companies have the internal alignment needed to run their own race.

OUTPUT FORMAT:
- 1800-2200 words
- Five sections with headers
- Analytical essay prose — NO bullet points
- Grounded in actual company data patterns (cite specific numbers)
- Reference specific KB signals by name when supporting claims about forces"""


def _aggregate_domain_stakeholders(domain_companies: list) -> dict:
    """Build domain-specific stakeholder summary from company key_people."""
    ROLE_BUCKETS = {
        "C-Suite / Executive": ["ceo", "cfo", "cto", "cio", "coo", "chief", "president", "managing director", "vd"],
        "Technology / Innovation": ["technology", "innovation", "digital", "data", "architect", "engineering", "r&d"],
        "Strategy / Commercial": ["strategy", "commercial", "business development", "marketing", "sales", "growth"],
        "Operations / HR": ["operations", "hr", "human resources", "people", "talent", "supply chain", "logistics"],
    }
    DIM_KEYWORDS = {
        "Operating Model": ["agile", "lean", "operating model", "organization", "team", "autonomy", "devops", "process"],
        "Service Portfolio": ["product", "service", "saas", "platform", "portfolio", "offering", "revenue model"],
        "Values": ["culture", "values", "trust", "diversity", "inclusion", "wellbeing", "sustainability", "esg"],
        "Strategy": ["strategy", "growth", "market", "competitive", "vision", "transformation", "future"],
        "AI Readiness": ["ai", "artificial intelligence", "data", "machine learning", "automation", "digital", "cloud"],
    }

    role_dim = defaultdict(lambda: defaultdict(int))
    role_counts = Counter()
    total = 0

    for company in domain_companies:
        for person in (company.get("key_people") or []):
            total += 1
            role_lower = (person.get("role") or "").lower()
            bucket = "Other"
            for bk, kws in ROLE_BUCKETS.items():
                if any(kw in role_lower for kw in kws):
                    bucket = bk
                    break
            role_counts[bucket] += 1

            insights = person.get("linkedin_insights") or {}
            if not insights.get("found"):
                continue
            all_text = " ".join(insights.get("topics") or []).lower() + " " + (insights.get("headline") or "").lower()
            for dim, kws in DIM_KEYWORDS.items():
                if any(kw in all_text for kw in kws):
                    role_dim[bucket][dim] += 1

    if total == 0:
        return None

    lines = []
    for bucket in ROLE_BUCKETS:
        if role_counts.get(bucket, 0) == 0:
            continue
        affs = role_dim.get(bucket, {})
        if affs:
            top = sorted(affs.items(), key=lambda x: x[1], reverse=True)
            lines.append(f"- {bucket} ({role_counts[bucket]}): focus on {', '.join(f'{d}={c}' for d, c in top)}")
        else:
            lines.append(f"- {bucket} ({role_counts[bucket]}): no dimension signals")

    return {"total": total, "summary": "\n".join(lines)} if lines else None


def _build_domain_prompt(
    domain: str, domain_config: dict, domain_companies: list,
    kb_signals: list, market_health: dict,
    global_analysis: str, finland_analysis: str,
    domain_stakeholders: dict = None,
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

    # Build stakeholder block outside the f-string (Python 3.11 can't nest f"""...""")
    stakeholder_block = ""
    if domain_stakeholders:
        stakeholder_block = (
            f"DOMAIN STAKEHOLDER DYNAMICS ({domain_stakeholders['total']} key people in this domain):\n"
            f"{domain_stakeholders['summary']}\n"
            f"Use this to identify whether this domain's leadership has the internal alignment to act on the forces identified above."
        )

    return f"""{SYSTEM_ROLE}

TASK: Write a Domain Force Dossier for {domain}.

TONE CALIBRATION — Let the Data Drive the Tone:
- Let the actual numbers set the tone. If the domain has strong reality scores, high Thriving ratios, or healthy financials, the analysis should reflect genuine confidence — not forced criticism.
- Only identify risks that are clearly evidenced in the data. Do not speculate about threats that the numbers do not support.
- Celebrate what is working: strong archetype distributions, narrow intent-reality gaps, and good market health are real competitive advantages. Say so clearly.
- The goal is an honest, data-grounded assessment — positive where the data warrants, concerned where the data warrants.
- A domain with 4 of 5 companies Thriving is genuinely strong. Do not undermine that finding with hypothetical future risks unless the signal evidence specifically supports those risks.

BROADER CONTEXT — How to use prior analysis layers:
The Global Force Landscape (Layer 1) and Finnish Economy Force Map (Layer 2) have been produced. Use them strategically:
- From Layer 1, identify 1-2 global forces most directly relevant to {domain} and reference how they manifest in this specific domain.
- From Layer 2, use the Finnish positioning insights for {domain} specifically — do not repeat general Finnish findings.
- Build on their conclusions but add domain-specific depth that the broader analyses could not provide.

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

{stakeholder_block}

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
        thinking={"type": "enabled", "budget_tokens": 3000},
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
    """Generate text via Opus 4.6. Returns (text, word_count).
    Used for Domain Dossiers (Layer 3) where analytical depth matters most."""
    anthropic_key = os.environ.get("ANTHROPIC_API_KEY", "").strip()
    if not anthropic_key:
        raise ValueError("ANTHROPIC_API_KEY not configured")

    client = anthropic.Anthropic(api_key=anthropic_key)
    t0 = time.time()

    response = client.messages.create(
        model="claude-opus-4-6",
        max_tokens=max_tokens,
        thinking={"type": "enabled", "budget_tokens": 8000},
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
    print(f"   [Observatory] Opus 4.6: {wc} words ({elapsed}ms)")
    return text, wc


def _verify_gemini(essay: str, source_data_summary: str, output_type: str) -> dict:
    """Verify essay via Gemini 3.1 Pro. Returns verification result."""
    prompt = f"""You are a quality assurance reviewer for strategic foresight analysis.

TASK: Verify the following {output_type} essay against its source data.

CHECK LIST:
1. DATA ACCURACY: Do any numbers or statistics cited in the essay contradict the source data? List specific mismatches.
2. ANTI-TECHNOLOGY FRAMING: Does the essay frame technology as the key message anywhere? Flag any sentences that say "companies need AI/cloud/digital/technology" rather than focusing on thinking, adaptability, and organizational capability.
3. STRUCTURE: Are all required sections present? Estimate the word count.
4. EVIDENCE GROUNDING: Does the essay make claims not supported by the provided data?
5. TONE BALANCE (for Finnish Economy Force Map and Domain Dossiers only): Does the essay manufacture or exaggerate risks that the data does not support? If the data shows strong performance (high Thriving ratios, healthy scores), does the essay acknowledge that strength clearly? Flag any cases where the tone is unjustifiably negative relative to the actual data.

SOURCE DATA:
{source_data_summary[:4000]}

ESSAY TO VERIFY:
{essay}

RESPOND IN JSON FORMAT ONLY:
{{"passed": true/false, "word_count_estimate": N, "issues": ["issue 1", ...], "tech_framing_violations": ["sentence 1", ...], "data_mismatches": ["mismatch 1", ...]}}"""

    try:
        client = get_gemini_client()
        response = call_with_503_backoff(
            lambda: client.models.generate_content(
                model="gemini-3.1-pro-preview",
                contents=prompt,
                config=genai_types.GenerateContentConfig(
                    temperature=0.0,
                    max_output_tokens=2000,
                    thinking_config=genai_types.ThinkingConfig(thinking_budget=2048),
                ),
            ),
            label=f"Observatory Verify {output_type}",
            timeout=60,
            model="gemini-3.1-pro-preview",
        )

        # Extract text from response
        text = ""
        if hasattr(response, "candidates") and response.candidates:
            for part in response.candidates[0].content.parts:
                if hasattr(part, "text") and part.text:
                    text += part.text

        # Parse JSON from response
        json_match = re.search(r'\{[\s\S]*\}', text)
        if json_match:
            return json.loads(json_match.group())

        return {"passed": True, "issues": [], "note": "Could not parse verification response"}

    except Exception as e:
        print(f"   [Observatory] Verification failed (non-blocking): {e}")
        return {"passed": True, "issues": [], "error": str(e)}


def _generate_with_verification(prompt: str, source_summary: str, output_type: str, use_opus: bool = False) -> tuple[str, dict]:
    """Generate via Claude, then verify via Gemini. Returns (text, verification).
    use_opus=True uses Opus 4.6 for deeper analytical reasoning (Layer 3)."""
    generate_fn = _generate_opus if use_opus else _generate_sonnet
    text, wc = generate_fn(prompt)

    if wc < 50:
        raise ValueError(f"Generation too short: {wc} words")

    verification = _verify_gemini(text, source_summary, output_type)

    # If verification finds critical issues and text is regeneratable, try once more
    if not verification.get("passed", True) and verification.get("tech_framing_violations"):
        violations = verification["tech_framing_violations"]
        print(f"   [Observatory] Verification found {len(violations)} tech-framing violations — regenerating...")
        correction = f"\n\nCRITICAL CORRECTION: Your previous attempt contained technology-centric framing. These specific sentences violated the anti-technology rule: {json.dumps(violations)}. Rewrite to focus on organizational thinking, adaptability, and future-readiness instead."
        text, wc = generate_fn(prompt + correction)
        verification = _verify_gemini(text, source_summary, output_type)

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

    # --- Output 1: Global Force Landscape ---
    progress("Global Force Landscape", 0, total_steps)
    global_prompt = _build_global_prompt(
        kb_agg,
        mi.get("cross_impact", []),
        mi.get("top_foresights", []),
    )
    global_source = f"Total signals: {kb_agg['total_signals']}, Themes: {kb_agg['theme_count']}, Accelerating: {kb_agg['accelerating']}, Decelerating: {kb_agg['decelerating']}"
    global_text, global_verify = _generate_with_verification(global_prompt, global_source, "Global Force Landscape")

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
        "verification": global_verify,
    }

    # --- Output 2: Finnish Economy Force Map ---
    progress("Finnish Economy Force Map", 1, total_steps)
    stakeholder_data = _aggregate_stakeholder_dynamics(data["company_details"], mi.get("gap_patterns", []))
    print(f"   [Observatory] Stakeholders: {stakeholder_data['stakeholder_count']} total, {stakeholder_data['enriched_count']} enriched, {len(stakeholder_data['alignment_conflicts'])} conflicts detected")
    finland_prompt = _build_finland_prompt(global_text, mi, stakeholder_data, kb_agg)
    finland_source = f"Companies: {mi.get('company_count', 0)}, Domains: {len(mi.get('market_health', []))}, FAVI segments: {json.dumps(mi.get('favi_segments', {}))}"
    finland_text, finland_verify = _generate_with_verification(finland_prompt, finland_source, "Finnish Economy Force Map")

    # Extract top adaptability gaps
    top_gaps = []
    for g in mi.get("gap_patterns", [])[:3]:
        top_gaps.append(f"{g['dimension']}: gap={g['avg_gap']}")

    finland_analysis = {
        "text": finland_text,
        "domains_analyzed": len(mi.get("market_health", [])),
        "favi_distribution": mi.get("favi_segments", {}),
        "top_adaptability_gaps": top_gaps,
        "verification": finland_verify,
    }

    # --- Output 3: Domain Force Dossiers ---
    domain_dossiers = []
    for i, domain in enumerate(active_domains):
        progress(f"Domain: {domain}", 2 + i, total_steps)

        config = DOMAIN_TO_FORESIGHT.get(domain, DOMAIN_TO_FORESIGHT.get("Other", {}))
        companies = domain_companies[domain]

        # Get KB signals for this domain's themes
        kb_signals = []
        for theme in config.get("fp_themes", []):
            for theme_slug, theme_data in data["all_kb"].items():
                for sig in theme_data.get("signals", []):
                    if sig.get("fp_theme") == theme:
                        kb_signals.append(sig)

        # Find market health for this domain
        mh = None
        for h in mi.get("market_health", []):
            if h["domain"] == domain:
                mh = h
                break

        # Aggregate domain-specific stakeholder dynamics
        dom_stakeholders = _aggregate_domain_stakeholders(companies)

        domain_prompt = _build_domain_prompt(
            domain, config, companies, kb_signals, mh,
            global_text, finland_text, dom_stakeholders,
        )
        source = f"Domain: {domain}, Companies: {len(companies)}, KB signals: {len(kb_signals)}"
        text, verify = _generate_with_verification(domain_prompt, source, f"Domain Dossier: {domain}", use_opus=True)

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
            "verification": verify,
        }
        domain_dossiers.append(dossier)

    # --- Assemble final result ---
    result = {
        "generated_at": datetime.utcnow().isoformat(),
        "model": "claude-sonnet-4-6 (L1-L2) + claude-opus-4-6 (L3)",
        "verification_model": "gemini-3.1-pro-preview",
        "company_count": len(data["dashboard_data"]),
        "domain_count": len(active_domains),
        "global_analysis": global_analysis,
        "finland_analysis": finland_analysis,
        "domain_dossiers": domain_dossiers,
    }

    # Save to storage
    url = storage.save_observatory(result)
    print(f"   [Observatory] Saved observatory to storage: {url}")

    return result
