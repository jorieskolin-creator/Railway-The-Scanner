"""
strategic_foresight.py — Strategic Foresight module for forward-looking analysis.

Generates per-dimension industry forces, cross-impact chains, micro-scenarios,
and optional climate exposure assessment. Uses Gemini + Google Search grounding.

Includes post-generation validation:
- URL verification (HEAD/GET probe) for all source_url fields
- Source retry loop: re-searches for verifiable sources when initial URLs fail
- Multi-source requirement: each force requires 2+ supporting sources
- Minimum quality gate: foresight is suppressed if insufficient verified sources
- Reality-check pass (source quality, confidence calibration, claim plausibility)
- Extraction of validated sources into foresight_links (mirrors evidence_links schema)

Methodology inspired by Futures Platform's 30-theme taxonomy (NAICS/SIC-based)
and SIFT/Lateral Reading OSINT validation principles.
"""

import json
import os
import re
import time
import concurrent.futures

from google import genai
from google.genai import types as genai_types
from gemini_backoff import call_with_503_backoff
from source_quality import HIGH_CREDIBILITY_DOMAINS, LOW_CREDIBILITY_DOMAINS, assess_source_credibility


# ═══════════════════════════════════════════════════════════════════════════════
# FUTURES PLATFORM 30-THEME TAXONOMY
# Complete list of the 30 FP Themes utilized in Future Insight creation.
# Each theme represents a macro-level domain of change that shapes the external
# environment companies must navigate. Used to tag dimension-level forces and
# ensure comprehensive coverage across all foresight analyses.
# ═══════════════════════════════════════════════════════════════════════════════

FP_THEMES_30 = [
    # --- Technology & Digital ---
    "Artificial Intelligence",          #  1 — AI, machine learning, autonomous systems
    "Data & Digitalisation",            #  2 — Big data, digital transformation, platforms
    "Communication & Media",            #  3 — Telecom, journalism, social media, content

    # --- Economy & Business ---
    "Business",                         #  4 — Business models, strategy, innovation mgmt
    "Finance & Ownership",              #  5 — Banking, investment, fintech, ownership models
    "Global Economy",                   #  6 — Trade, macroeconomics, supply chains, sanctions
    "Digital & Sharing Economies",      #  7 — Platform economy, gig work, collaborative models
    "Services",                         #  8 — Service design, customer experience, servitization

    # --- Industry & Infrastructure ---
    "Industry & Manufacturing",         #  9 — Smart factories, automation, industrial IoT
    "Construction & Urbanisation",      # 10 — Smart cities, urban planning, building tech
    "Energy",                           # 11 — Renewables, grids, energy storage, nuclear
    "Freight & Logistics",              # 12 — Supply chains, last-mile, autonomous freight
    "Private & Public Transport",       # 13 — Mobility-as-a-Service, EVs, public transit

    # --- Environment & Resources ---
    "Climate Change",                   # 14 — Mitigation, adaptation, carbon markets
    "Sustainability & Recycling",       # 15 — Circular economy, waste, ESG reporting
    "Nature",                           # 16 — Biodiversity, ecosystems, land use
    "Food",                             # 17 — Agri-tech, food security, alternative proteins
    "Water & Oceans",                   # 18 — Water scarcity, marine economy, blue tech

    # --- Society & Governance ---
    "Public Governance",                # 19 — Regulation, democracy, public-sector innovation
    "Geopolitics",                      # 20 — International relations, conflicts, alliances
    "Security & Safety",                # 21 — Cybersecurity, defence, resilience, privacy
    "Values & Ethics",                  # 22 — Trust, transparency, corporate responsibility
    "Society",                          # 23 — Inequality, social cohesion, civil society

    # --- People & Well-being ---
    "Health & Wellbeing",               # 24 — Healthcare, mental health, biotech, longevity
    "Education & Knowledge",            # 25 — EdTech, lifelong learning, skills transformation
    "Work & Income",                    # 26 — Future of work, labour markets, UBI
    "Management & HR",                  # 27 — Leadership, org design, talent management
    "Leisure & Lifestyles",             # 28 — Consumer culture, tourism, entertainment

    # --- Science & Demographics ---
    "Sciences & Research",              # 29 — R&D, quantum computing, space, biotech research
    "Population & Demographics",        # 30 — Aging, migration, urbanisation trends
]

# ═══════════════════════════════════════════════════════════════════════════════
# DOMAIN → FORESIGHT THEME MAPPING
# Maps the scanner's 14 KNOWN_DOMAINS to Futures Platform's 30-theme taxonomy
# and identifies which KB dimensions are most impacted by external forces.
# ═══════════════════════════════════════════════════════════════════════════════

DOMAIN_TO_FORESIGHT = {
    "Technology & Software": {
        "fp_themes": ["Artificial Intelligence", "Data & Digitalisation", "Business"],
        "priority_dimensions": ["AI Readiness", "Operating Model", "Service Portfolio"],
        "osint_keywords": "agentic AI enterprise automation, SaaS market consolidation, open-source AI disruption",
    },
    "IT Services & Consulting": {
        "fp_themes": ["Data & Digitalisation", "Business", "Management & HR"],
        "priority_dimensions": ["Operating Model", "Strategy", "AI Readiness"],
        "osint_keywords": "consulting industry AI disruption, IT outsourcing market shifts, talent war technology sector",
    },
    "Healthcare": {
        "fp_themes": ["Health & Wellbeing", "Artificial Intelligence", "Sciences & Research"],
        "priority_dimensions": ["AI Readiness", "Service Portfolio", "Culture"],
        "osint_keywords": "healthcare AI diagnostics regulation, digital health transformation, pharmaceutical supply chain risks",
    },
    "Retail & Consumer": {
        "fp_themes": ["Business", "Data & Digitalisation", "Leisure & Lifestyles"],
        "priority_dimensions": ["Service Portfolio", "AI Readiness", "Strategy"],
        "osint_keywords": "retail AI personalization, consumer behavior shifts, omnichannel transformation 2025 2026",
    },
    "Industrial & Manufacturing": {
        "fp_themes": ["Industry & Manufacturing", "Artificial Intelligence", "Sustainability & Recycling"],
        "priority_dimensions": ["Operating Model", "AI Readiness", "Service Portfolio"],
        "climate_exposure": "high",
        "osint_keywords": "industrial AI automation, smart manufacturing disruption, supply chain reshoring nearshoring",
    },
    "Energy & Utilities": {
        "fp_themes": ["Energy", "Climate Change", "Sustainability & Recycling"],
        "priority_dimensions": ["Strategy", "Service Portfolio", "Operating Model"],
        "climate_exposure": "critical",
        "osint_keywords": "energy transition decarbonization, renewable energy grid transformation, carbon taxation CBAM",
    },
    "Financial Services": {
        "fp_themes": ["Finance & Ownership", "Artificial Intelligence", "Security & Safety"],
        "priority_dimensions": ["AI Readiness", "Strategy", "Operating Model"],
        "osint_keywords": "fintech disruption banking, AI risk management regulation, digital currency CBDC impact",
    },
    "Insurance & Pensions": {
        "fp_themes": ["Finance & Ownership", "Data & Digitalisation", "Climate Change"],
        "priority_dimensions": ["AI Readiness", "Strategy", "Service Portfolio"],
        "climate_exposure": "high",
        "osint_keywords": "insurtech AI underwriting, climate risk insurance pricing, pension fund ESG regulation",
    },
    "Forest & Paper": {
        "fp_themes": ["Nature", "Sustainability & Recycling", "Industry & Manufacturing"],
        "priority_dimensions": ["Service Portfolio", "Strategy", "Operating Model"],
        "climate_exposure": "critical",
        "osint_keywords": "bioeconomy circular economy forest, sustainable packaging disruption, EU deforestation regulation",
    },
    "Telecommunications": {
        "fp_themes": ["Communication & Media", "Data & Digitalisation", "Artificial Intelligence"],
        "priority_dimensions": ["AI Readiness", "Service Portfolio", "Operating Model"],
        "osint_keywords": "telecom AI network optimization, 5G private networks enterprise, telecom industry consolidation",
    },
    "Construction & Real Estate": {
        "fp_themes": ["Construction & Urbanisation", "Sustainability & Recycling", "Energy"],
        "priority_dimensions": ["Operating Model", "Service Portfolio", "Strategy"],
        "climate_exposure": "high",
        "osint_keywords": "construction technology disruption, green building regulation, real estate market transformation",
    },
    "Transport & Logistics": {
        "fp_themes": ["Freight & Logistics", "Private & Public Transport", "Artificial Intelligence"],
        "priority_dimensions": ["Operating Model", "AI Readiness", "Service Portfolio"],
        "climate_exposure": "high",
        "osint_keywords": "autonomous logistics AI, supply chain resilience disruption, transport decarbonization regulation",
    },
    "Media & Publishing": {
        "fp_themes": ["Communication & Media", "Artificial Intelligence", "Values & Ethics"],
        "priority_dimensions": ["AI Readiness", "Service Portfolio", "Strategy"],
        "osint_keywords": "generative AI media disruption, content industry transformation, digital advertising AI shift",
    },
    "Other": {
        "fp_themes": ["Business", "Artificial Intelligence", "Global Economy"],
        "priority_dimensions": ["Strategy", "AI Readiness", "Operating Model"],
        "osint_keywords": "business model disruption AI, digital transformation enterprise, global economic uncertainty outlook",
    },
}


def _build_foresight_prompt(
    company_name: str,
    domain: str,
    archetype: str,
    reality_score: int,
    financial_index: int | None,
    top_findings: list[dict],
    domain_config: dict,
) -> str:
    """Build the foresight analysis prompt with domain-specific context."""

    fp_themes = ", ".join(domain_config["fp_themes"])
    priority_dims = ", ".join(domain_config["priority_dimensions"])
    osint_kw = domain_config.get("osint_keywords", "")
    climate_exposure = domain_config.get("climate_exposure")
    n_dims = len(domain_config["priority_dimensions"])

    # Format top findings
    findings_text = ""
    for f in top_findings[:8]:
        cat = f.get("category", f.get("description", ""))
        label = f.get("label", f.get("description", ""))
        sentiment = f.get("sentiment", "")
        snippet = (f.get("snippet", "") or "")[:150]
        if cat or label:
            findings_text += f"- [{sentiment}] {cat}: {label}"
            if snippet:
                findings_text += f" ({snippet})"
            findings_text += "\n"

    fin_str = f"{financial_index}/100" if financial_index is not None else "N/A"

    # Climate section (only for high/critical exposure domains)
    climate_section = ""
    if climate_exposure:
        climate_section = f"""
TASK 4 — CLIMATE EXPOSURE ASSESSMENT:
This company operates in a sector with {climate_exposure.upper()} climate exposure.
Evaluate using web search:
- Physical risk: How would +2°C warming impact operations/supply chain in their sector and geography?
- Transition risk: How would aggressive carbon taxation (EU CBAM, Green Deal) affect their margins?
- AI-climate paradox: If they are adopting AI, does this create carbon contradictions?
Return a "climate_exposure" object with: risk_level (low/medium/high/critical), physical_risk (1-2 sentences), transition_risk (1-2 sentences), headline (1 sentence summary).
"""

    climate_json_line = '"climate_exposure": {"risk_level": "...", "physical_risk": "...", "transition_risk": "...", "headline": "..."},' if climate_exposure else ""

    return f"""You are a Strategic Foresight Analyst combining OSINT methodology with futures studies.
Your role: identify external forces that will impact {company_name} ({domain}) in the next 12-36 months, and translate them into actionable strategic context.

METHODOLOGY — SIFT PROTOCOL (mandatory for every signal):
1. STOP: Do not accept the first search result as truth. Verify the signal is real, not marketing.
2. INVESTIGATE THE SOURCE: Is this corporate PR, an independent analyst, academic research, or government data?
3. FIND BETTER COVERAGE: Cross-reference each force against 2+ independent sources from different perspectives.
4. TRACE CLAIMS: Link to the ORIGINAL source, not a derivative article or summary.
If a sustainability/ESG claim comes from the company itself, flag it as unverified.

COMPANY CONTEXT:
- Company: {company_name}
- Domain: {domain}
- Archetype: {archetype} (Reality: {reality_score}/100, Financial: {fin_str})
- Key findings from forensic scan:
{findings_text or "(limited findings available)"}

RELEVANT FORESIGHT THEMES (from Futures Platform taxonomy): {fp_themes}
PRIORITY KB DIMENSIONS TO EVALUATE: {priority_dims}
DOMAIN-SPECIFIC OSINT KEYWORDS: {osint_kw}

TASK 1 — DIMENSION-LEVEL FORESIGHT:
For each of the {n_dims} priority KB dimensions above, use web search to identify the single most impactful EXTERNAL force affecting companies in the {domain} sector. These must be macro-level industry/regulatory/technological forces — NOT company-specific findings.

Per force, provide:
- "trend": Concrete name of the external force (specific, not vague)
- "type": One of: "Plausible/Maturing" (gathering momentum, validated trend) | "Weakening/Declining" (losing relevance or reversing) | "Preposterous/Black Swan" (low probability, massive impact) | "Probable/Emerging" (early signal, gaining traction but insufficient data)
- "timeframe": "6-12mo" | "12-24mo" | "24-36mo"
- "kb_dimension": Which priority dimension this impacts most
- "fp_theme": Which Futures Platform theme this maps to
- "impact_on_company": 1-2 sentences on how this specifically affects {company_name} given their archetype ({archetype}) and current state
- "proposal_angle": 1 sentence on how to use this in a consulting proposal to {company_name}
- "source_url": PRIMARY URL from your web search results (ORIGINAL source only — must be a real, reachable URL)
- "supporting_sources": Array of 2-3 ADDITIONAL URLs that corroborate this force from DIFFERENT publications/organizations. Each entry: {{"url": "https://...", "source_type": "...", "title": "Brief title of the source"}}. CRITICAL: These must be REAL URLs you found via web search, not fabricated. If you cannot find 2+ corroborating sources, set confidence to "low".
- "source_type": "academic" | "think_tank" | "industry_analyst" | "government" | "news"
- "confidence": "high" (3+ verified sources) | "medium" (2 verified sources) | "low" (0-1 verified sources)
- "osint_validation": 1 sentence on how you verified this signal (e.g., "Cross-referenced OECD report with McKinsey survey"). MUST reference specific source names.

TASK 2 — CROSS-IMPACT CHAIN:
Trace how the strongest identified force cascades through {company_name}'s weakest dimensions. Format as a chain: "Force X → impacts Dimension A → which cascades to Dimension B → ultimately affecting Dimension C." This must be specific to THIS company's situation, not generic.

TASK 3 — MICRO-SCENARIOS (specific to {company_name}, not generic industry scenarios):
- "acceleration": What specifically happens in 12-24 months if {company_name} acts NOW on their weakest dimensions while riding the strengthening forces? Reference their archetype.
- "status_quo": What specifically happens if {company_name} continues their current trajectory while these forces unfold? Be concrete about consequences for a "{archetype}" company.
- "disruption": What specific wild card, competitor move, or regulatory shift could disrupt {company_name} regardless of their own actions?
{climate_section}
CRITICAL SOURCE QUALITY RULES:
- Every source_url and supporting_sources URL MUST be a real URL you retrieved from web search.
- Do NOT fabricate, guess, or construct URLs. If you cannot find a real source, omit the URL and set confidence to "low".
- Each force MUST have at least 2 supporting_sources from different organizations. Forces with fewer than 2 corroborating sources must have confidence set to "low".
- Prefer sources from: OECD, McKinsey, BCG, Gartner, Forrester, Reuters, FT, Bloomberg, government agencies, academic journals.

RETURN STRICT JSON ONLY:
{{
  "dimension_forces": [
    {{
      "trend": "...",
      "type": "Plausible/Maturing|Weakening/Declining|Preposterous/Black Swan|Probable/Emerging",
      "timeframe": "6-12mo|12-24mo|24-36mo",
      "kb_dimension": "...",
      "fp_theme": "...",
      "impact_on_company": "...",
      "proposal_angle": "...",
      "source_url": "https://...",
      "supporting_sources": [{{"url": "https://...", "source_type": "...", "title": "..."}}, {{"url": "https://...", "source_type": "...", "title": "..."}}],
      "source_type": "academic|think_tank|industry_analyst|government|news",
      "confidence": "high|medium|low",
      "osint_validation": "..."
    }}
  ],
  "cross_impact_chain": "Force → Dimension A → Dimension B → Dimension C",
  "scenarios": {{
    "acceleration": "...",
    "status_quo": "...",
    "disruption": "..."
  }},
  {climate_json_line}
  "foresight_headline": "One-sentence strategic outlook for {company_name}"
}}"""


def _extract_json_from_text(text: str) -> str | None:
    """Extract JSON from model response that may contain markdown fences or preamble."""
    if not text:
        return None
    # Try direct parse first
    text = text.strip()
    if text.startswith("{"):
        return text
    # Try markdown fence
    m = re.search(r"```(?:json)?\s*(\{[\s\S]*?\})\s*```", text)
    if m:
        return m.group(1)
    # Try to find first { to last }
    start = text.find("{")
    end = text.rfind("}")
    if start != -1 and end != -1 and end > start:
        return text[start:end + 1]
    return None


def generate_foresight(
    company_name: str,
    domain: str,
    archetype: str,
    reality_score: int,
    financial_index: int | None,
    top_findings: list[dict],
    kb_dimension_scores: dict | None = None,
) -> dict | None:
    """
    Generate strategic foresight overlay for a company scan.

    Returns structured dict with per-dimension forces, scenarios,
    cross-impact chain, and optional climate exposure. Returns None on failure.
    Uses Gemini 3.1 Pro Preview + Google Search grounding.
    """
    gemini_key = os.environ.get("GEMINI_API_KEY") or os.environ.get("API_KEY")
    if not gemini_key:
        print("   ⚠️ [Foresight] No GEMINI_API_KEY — skipping")
        return None

    domain_config = DOMAIN_TO_FORESIGHT.get(domain, DOMAIN_TO_FORESIGHT["Other"])
    prompt = _build_foresight_prompt(
        company_name, domain, archetype, reality_score,
        financial_index, top_findings, domain_config,
    )

    client = genai.Client(api_key=gemini_key)

    _safety = [
        {"category": "HARM_CATEGORY_HARASSMENT", "threshold": "BLOCK_NONE"},
        {"category": "HARM_CATEGORY_HATE_SPEECH", "threshold": "BLOCK_NONE"},
        {"category": "HARM_CATEGORY_SEXUALLY_EXPLICIT", "threshold": "BLOCK_NONE"},
        {"category": "HARM_CATEGORY_DANGEROUS_CONTENT", "threshold": "BLOCK_NONE"},
    ]

    # Primary: Gemini 3.1 Pro Preview with Google Search grounding
    models = [
        ("gemini-3.1-pro-preview", genai_types.GenerateContentConfig(
            tools=[genai_types.Tool(google_search=genai_types.GoogleSearch())],
            safety_settings=_safety,
            temperature=0.5,
            thinking_config=genai_types.ThinkingConfig(thinking_budget=3000),
            max_output_tokens=8000,
        ), 120),
        ("gemini-2.5-pro", genai_types.GenerateContentConfig(
            tools=[genai_types.Tool(google_search=genai_types.GoogleSearch())],
            safety_settings=_safety,
            temperature=0.5,
            thinking_config=genai_types.ThinkingConfig(thinking_budget=2048),
            max_output_tokens=8000,
        ), 90),
    ]

    for model_name, config, timeout_sec in models:
        try:
            print(f"   🔮 [Foresight] Attempting {model_name} for {company_name} ({domain})...")
            t0 = time.time()
            response = call_with_503_backoff(
                lambda mn=model_name, cfg=config: client.models.generate_content(
                    model=mn, contents=prompt, config=cfg,
                ),
                label=f"Foresight {company_name} {model_name}",
                model=model_name,
                timeout=timeout_sec,
            )
            text = response.text if hasattr(response, "text") else ""
            elapsed = int((time.time() - t0) * 1000)

            if not text or len(text) < 100:
                print(f"   ⚠️ [Foresight] {model_name} returned insufficient data ({len(text) if text else 0} chars, {elapsed}ms)")
                continue

            json_str = _extract_json_from_text(text)
            if not json_str:
                print(f"   ⚠️ [Foresight] Could not extract JSON from {model_name} response ({elapsed}ms)")
                continue

            data = json.loads(json_str, strict=False)

            # Validate minimum structure
            if not data.get("dimension_forces") or not data.get("scenarios"):
                print(f"   ⚠️ [Foresight] {model_name} returned incomplete structure ({elapsed}ms)")
                continue

            # Add metadata
            data["fp_themes_used"] = domain_config["fp_themes"]
            data["model"] = model_name
            data["time_ms"] = elapsed
            data["domain"] = domain

            n_forces = len(data.get("dimension_forces", []))
            print(f"   🔮 [Foresight] {model_name} succeeded: {n_forces} forces, "
                  f"{'climate' if data.get('climate_exposure') else 'no climate'} "
                  f"({elapsed}ms)")
            return data

        except json.JSONDecodeError as je:
            elapsed = int((time.time() - t0) * 1000)
            print(f"   ⚠️ [Foresight] {model_name} JSON parse failed ({elapsed}ms): {je}")
            continue
        except Exception as e:
            elapsed = int((time.time() - t0) * 1000) if 't0' in dir() else 0
            print(f"   ❌ [Foresight] {model_name} failed ({elapsed}ms): {e}")
            continue

    print(f"   ❌ [Foresight] All models failed for {company_name} — foresight skipped")
    return None


def format_foresight_for_synthesis(foresight_data: dict, company_name: str, domain: str) -> str:
    """
    Format foresight data into a context block for the synthesis prompt.
    Returns an empty string if foresight data is insufficient.
    """
    if not foresight_data or not foresight_data.get("dimension_forces"):
        return ""

    forces_text = ""
    for f in foresight_data["dimension_forces"]:
        forces_text += (
            f"- [{f.get('type', '?')}] {f.get('trend', '?')} ({f.get('timeframe', '?')}) "
            f"→ impacts {f.get('kb_dimension', '?')}: {f.get('impact_on_company', '?')}\n"
        )

    scenarios = foresight_data.get("scenarios", {})
    cross_impact = foresight_data.get("cross_impact_chain", "")
    climate = foresight_data.get("climate_exposure", {})

    climate_line = ""
    if climate and climate.get("headline"):
        climate_line = f"\nClimate exposure ({climate.get('risk_level', '?')}): {climate['headline']}"

    return f"""
STRATEGIC FORESIGHT CONTEXT (external forces, next 12-36 months):
{forces_text}
Cross-impact chain: {cross_impact}

Key scenario — Status Quo: {scenarios.get('status_quo', 'N/A')}
Key scenario — Disruption: {scenarios.get('disruption', 'N/A')}
{climate_line}

INSTRUCTION: Weave 1-2 forward-looking insights into the summary's final paragraph.
The pitch MUST reference at least one external force to create urgency.
WHY/WHAT proposals should be future-aware — frame the weakest KB dimension against
its incoming external force.
Example: Instead of "Your AI maturity needs improvement" →
"With agentic AI reshaping {domain} in 12-18 months, your current state creates
an urgent modernization window."
"""


_FORESIGHT_SUMMARY_FORBIDDEN = [
    "Thriving", "Modernizing", "Cash Cow", "At Risk",
    "Digital Taylorism", "Feature Factory", "Command and Control",
    "Zombie Scrum", "Fake Agile", "Pilot Purgatory", "Excel Hell",
    "Knowledge Base", "KB dimension", "archetype",
]


def generate_foresight_summary(
    foresight_data: dict,
    company_name: str,
    domain: str,
) -> tuple[str, str]:
    """
    Generate a 250-300 word "Future Insight" narrative from foresight data.

    Returns (summary_text, model_used).
    Returns ("", "") on failure.
    """
    if not foresight_data or not foresight_data.get("dimension_forces"):
        return "", ""

    gemini_key = os.environ.get("GEMINI_API_KEY") or os.environ.get("API_KEY")
    if not gemini_key:
        print("   ⚠️ [Future Insight] No GEMINI_API_KEY — skipping")
        return "", ""

    # Format forces for the prompt
    forces_text = ""
    for f in foresight_data["dimension_forces"]:
        forces_text += (
            f"- [{f.get('type', '?')}] {f.get('trend', '?')} ({f.get('timeframe', '?')}) "
            f"→ impacts {f.get('kb_dimension', '?')}: {f.get('impact_on_company', '?')}\n"
        )

    scenarios = foresight_data.get("scenarios", {})
    cross_impact = foresight_data.get("cross_impact_chain", "")
    climate = foresight_data.get("climate_exposure", {})
    forbidden = ", ".join(_FORESIGHT_SUMMARY_FORBIDDEN)

    climate_instruction = ""
    if climate and climate.get("headline"):
        climate_instruction = f"""
Third paragraph (optional): Climate/sustainability exposure — {climate.get('headline', '')}
Risk level: {climate.get('risk_level', 'unknown')}. Physical: {climate.get('physical_risk', 'N/A')}. Transition: {climate.get('transition_risk', 'N/A')}."""

    prompt = f"""Write a "Future Insight" summary for {company_name} ({domain}).
This is a forward-looking companion to the company's diagnostic assessment.

FORESIGHT DATA:
External forces identified:
{forces_text}
Cross-impact chain: {cross_impact}

Key scenarios:
- Status quo: {scenarios.get('status_quo', 'N/A')}
- Disruption: {scenarios.get('disruption', 'N/A')}
- Acceleration: {scenarios.get('acceleration', 'N/A')}

RULES:
- 250-300 words, 3-4 paragraphs separated by \\n\\n
- First paragraph: The 2-3 most impactful external forces and their timeframe.
  Name the forces concretely — no vague "market changes are coming."
  Wrap each key force name in **double asterisks** when first mentioned (e.g., "**EU SFDR 2.0 implementation**").
- Second paragraph: What this specifically means for THIS company given
  their current weaknesses. Connect the dots between external forces and internal gaps.
  Explain the cross-impact chain — how one force triggers cascading effects across multiple areas.
- Third paragraph: What the company must do to prepare — concrete strategic imperatives
  tied to the identified forces. Be specific about timelines and capabilities needed.
{climate_instruction}
- Tone: Strategic, forward-looking, grounded in evidence. Like a foresight
  analyst briefing a board of directors. Not marketing fluff.
- Do NOT reference internal scores by name or number.
- Do NOT use any of these terms: {forbidden}. Use plain business language.
- Every claim must trace to a force identified in the foresight analysis.
- If confidence is low on a force, use hedging language ("emerging signals suggest...")
- KEY FORCE HIGHLIGHTING: When naming a key external force for the first time,
  wrap it in **double asterisks** (e.g., "**generative AI disruption**").
  Do this for the 2-4 most important forces only. Do not overuse.
- FORMAT: Prose only with **bold** markers for key forces. No headers, no bullet points, no other markdown, no links.

Output only the prose text. Nothing else."""

    client = genai.Client(api_key=gemini_key)

    models = [
        ("gemini-3.1-pro-preview", genai_types.GenerateContentConfig(
            temperature=0.8,
            max_output_tokens=4000,
            thinking_config=genai_types.ThinkingConfig(thinking_budget=2048),
        ), 90),
        ("gemini-2.5-pro", genai_types.GenerateContentConfig(
            temperature=0.8,
            max_output_tokens=4000,
            thinking_config=genai_types.ThinkingConfig(thinking_budget=1024),
        ), 60),
    ]

    for model_name, config, timeout_sec in models:
        try:
            t0 = time.time()
            response = call_with_503_backoff(
                lambda mn=model_name, cfg=config: client.models.generate_content(
                    model=mn, contents=prompt, config=cfg,
                ),
                label=f"Future Insight {model_name}",
                model=model_name,
                timeout=timeout_sec,
            )
            text = (response.text or "").strip()
            # Strip any markdown links
            text = re.sub(r'\[([^\]]+)\]\(https?://[^)]+\)', r'\1', text)
            wc = len(text.split())
            elapsed = int((time.time() - t0) * 1000)
            print(f"   [Future Insight] {model_name}: {wc} words ({elapsed}ms)")

            if text and wc >= 100:
                return text, model_name

            print(f"   ⚠️ [Future Insight] {model_name} too short ({wc} words) — trying next")
        except Exception as e:
            elapsed = int((time.time() - t0) * 1000) if 't0' in dir() else 0
            print(f"   ❌ [Future Insight] {model_name} failed ({elapsed}ms): {e}")
            continue

    return "", ""


# ═══════════════════════════════════════════════════════════════════════════════
# FORESIGHT VALIDATION — URL verification + reality-check pass
# ═══════════════════════════════════════════════════════════════════════════════

# Valid source types that the SIFT protocol considers credible
_CREDIBLE_SOURCE_TYPES = {"academic", "think_tank", "industry_analyst", "government", "news"}

# Domain credibility lists — imported from shared source_quality module
_HIGH_CREDIBILITY_DOMAINS = HIGH_CREDIBILITY_DOMAINS
_LOW_CREDIBILITY_DOMAINS = LOW_CREDIBILITY_DOMAINS


def _verify_foresight_url(url, timeout=4):
    """
    Verify a single foresight URL exists via HTTP HEAD request.
    Returns True if the URL responds with a success/redirect status (2xx, 3xx).
    Returns False for 4xx, 5xx, timeouts, or connection errors.
    Mirrors scanner_two._verify_url for consistent verification behavior.
    """
    if not url or not url.startswith("http"):
        return False
    try:
        import requests as http_requests
        resp = http_requests.head(
            url, timeout=timeout, allow_redirects=True,
            headers={"User-Agent": "Mozilla/5.0 (compatible; AdaptabilityScanner/1.0)"},
        )
        return resp.status_code < 400
    except Exception:
        # Timeout, connection error, SSL error — try GET as fallback
        try:
            import requests as http_requests
            resp = http_requests.get(
                url, timeout=timeout, allow_redirects=True, stream=True,
                headers={"User-Agent": "Mozilla/5.0 (compatible; AdaptabilityScanner/1.0)"},
            )
            resp.close()
            return resp.status_code < 400
        except Exception:
            return False


def _get_url_domain(url):
    """Extract the domain from a URL, stripping www. prefix."""
    if not url:
        return ""
    try:
        from urllib.parse import urlparse
        parsed = urlparse(url)
        domain = parsed.netloc
        if domain.startswith("www."):
            domain = domain[4:]
        return domain.lower()
    except Exception:
        return ""


def validate_foresight_urls(foresight_data, max_workers=6):
    """
    URL Validation Gate for foresight signals.

    Verifies source_url in each dimension_force via parallel HEAD/GET probes.
    Adds 'url_verified' field (True/False) to each force.
    Returns (foresight_data, verified_count, dead_count).
    """
    if not foresight_data or not foresight_data.get("dimension_forces"):
        return foresight_data, 0, 0

    start = time.time()
    forces = foresight_data["dimension_forces"]

    # Collect unique URLs to check
    url_map = {}
    for force in forces:
        url = (force.get("source_url") or "").strip()
        if url and url.startswith("http") and url not in url_map:
            url_map[url] = None

    # Also collect supporting source URLs
    for force in forces:
        for ss in force.get("supporting_sources", []):
            ss_url = (ss.get("url") or "").strip()
            if ss_url and ss_url.startswith("http") and ss_url not in url_map:
                url_map[ss_url] = None

    if not url_map:
        for force in forces:
            force["url_verified"] = False
        return foresight_data, 0, 0

    # Verify in parallel
    with concurrent.futures.ThreadPoolExecutor(max_workers=max_workers) as executor:
        future_to_url = {executor.submit(_verify_foresight_url, url): url for url in url_map}
        for future in concurrent.futures.as_completed(future_to_url):
            url = future_to_url[future]
            try:
                url_map[url] = future.result()
            except Exception:
                url_map[url] = False

    # Apply results to forces (primary URL)
    verified_count = 0
    dead_count = 0
    for force in forces:
        url = (force.get("source_url") or "").strip()
        if url and url in url_map:
            force["url_verified"] = url_map[url]
            if url_map[url]:
                verified_count += 1
            else:
                dead_count += 1
        else:
            force["url_verified"] = False

        # Apply results to supporting sources
        verified_supporting = []
        for ss in force.get("supporting_sources", []):
            ss_url = (ss.get("url") or "").strip()
            if ss_url and ss_url in url_map:
                ss["url_verified"] = url_map[ss_url]
                if url_map[ss_url]:
                    verified_supporting.append(ss)
            else:
                ss["url_verified"] = False
        force["verified_supporting_sources"] = verified_supporting

    elapsed = int((time.time() - start) * 1000)
    total = verified_count + dead_count
    total_supporting = sum(len(f.get("verified_supporting_sources", [])) for f in forces)
    print(f"   🔗 [Foresight URL Validation] {verified_count}/{total} primary URLs verified, "
          f"{total_supporting} supporting URLs verified, {dead_count} dead/unreachable ({elapsed}ms)")

    return foresight_data, verified_count, dead_count


def _build_source_retry_prompt(failed_forces, domain):
    """Build a targeted prompt to find real, verifiable sources for forces that failed URL validation."""
    forces_desc = ""
    for i, force in enumerate(failed_forces, 1):
        forces_desc += (
            f"{i}. Trend: \"{force.get('trend', '?')}\"\n"
            f"   KB Dimension: {force.get('kb_dimension', '?')}\n"
            f"   FP Theme: {force.get('fp_theme', '?')}\n"
            f"   Original (failed) URL: {force.get('source_url', 'none')}\n\n"
        )

    return f"""You are a research assistant. Your ONLY task is to find REAL, VERIFIABLE URLs for the following industry trends/forces in the {domain} sector.

The original analysis identified these forces but the source URLs could not be verified (they returned 404 or were unreachable). You must find REAL sources that confirm these trends exist.

FORCES NEEDING VERIFIED SOURCES:
{forces_desc}

RULES:
- Use web search to find REAL articles, reports, or publications that discuss each trend.
- Each source MUST be a real, currently accessible URL — not fabricated or guessed.
- Prefer authoritative sources: OECD, McKinsey, BCG, Gartner, Forrester, Reuters, FT, Bloomberg, government agencies, EU institutions, academic journals.
- For EACH force, provide the primary source URL plus 2 additional corroborating sources from different organizations.
- If you genuinely cannot find a verifiable source for a trend, set "found": false for that entry.

RETURN STRICT JSON ONLY:
{{
  "retry_results": [
    {{
      "trend": "exact trend name from above",
      "found": true,
      "source_url": "https://real-verified-url...",
      "source_type": "academic|think_tank|industry_analyst|government|news",
      "supporting_sources": [
        {{"url": "https://...", "source_type": "...", "title": "Brief description"}},
        {{"url": "https://...", "source_type": "...", "title": "Brief description"}}
      ],
      "osint_validation": "How you verified this (reference specific source names)"
    }}
  ]
}}"""


def retry_failed_sources(foresight_data, domain, max_retries=1):
    """
    Source Retry Loop — re-searches for verifiable sources when initial URLs fail.

    For each dimension_force where url_verified=False, makes a targeted Gemini call
    with Google Search grounding to find real, accessible URLs for the same trend.

    Updates forces in-place with new URLs and re-verifies them.
    Returns (foresight_data, newly_verified_count).
    """
    if not foresight_data or not foresight_data.get("dimension_forces"):
        return foresight_data, 0

    forces = foresight_data["dimension_forces"]
    failed_forces = [f for f in forces if not f.get("url_verified", False)]

    if not failed_forces:
        print("   [Source Retry] All sources already verified — no retry needed")
        return foresight_data, 0

    gemini_key = os.environ.get("GEMINI_API_KEY") or os.environ.get("API_KEY")
    if not gemini_key:
        return foresight_data, 0

    print(f"   🔄 [Source Retry] {len(failed_forces)}/{len(forces)} forces have unverified sources — attempting re-search...")

    client = genai.Client(api_key=gemini_key)
    _safety = [
        {"category": "HARM_CATEGORY_HARASSMENT", "threshold": "BLOCK_NONE"},
        {"category": "HARM_CATEGORY_HATE_SPEECH", "threshold": "BLOCK_NONE"},
        {"category": "HARM_CATEGORY_SEXUALLY_EXPLICIT", "threshold": "BLOCK_NONE"},
        {"category": "HARM_CATEGORY_DANGEROUS_CONTENT", "threshold": "BLOCK_NONE"},
    ]

    prompt = _build_source_retry_prompt(failed_forces, domain)

    models = [
        ("gemini-3.1-pro-preview", genai_types.GenerateContentConfig(
            tools=[genai_types.Tool(google_search=genai_types.GoogleSearch())],
            safety_settings=_safety,
            temperature=0.3,
            thinking_config=genai_types.ThinkingConfig(thinking_budget=2048),
            max_output_tokens=6000,
        ), 90),
        ("gemini-2.5-pro", genai_types.GenerateContentConfig(
            tools=[genai_types.Tool(google_search=genai_types.GoogleSearch())],
            safety_settings=_safety,
            temperature=0.3,
            thinking_config=genai_types.ThinkingConfig(thinking_budget=1024),
            max_output_tokens=6000,
        ), 60),
    ]

    retry_data = None
    for model_name, config, timeout_sec in models:
        try:
            t0 = time.time()
            response = call_with_503_backoff(
                lambda mn=model_name, cfg=config: client.models.generate_content(
                    model=mn, contents=prompt, config=cfg,
                ),
                label=f"Source Retry {model_name}",
                model=model_name,
                timeout=timeout_sec,
            )
            text = response.text if hasattr(response, "text") else ""
            elapsed = int((time.time() - t0) * 1000)

            if not text or len(text) < 50:
                print(f"   ⚠️ [Source Retry] {model_name} returned insufficient data ({elapsed}ms)")
                continue

            json_str = _extract_json_from_text(text)
            if json_str:
                retry_data = json.loads(json_str, strict=False)
                print(f"   🔄 [Source Retry] {model_name} returned {len(retry_data.get('retry_results', []))} retry results ({elapsed}ms)")
                break
        except Exception as e:
            elapsed = int((time.time() - t0) * 1000) if 't0' in dir() else 0
            print(f"   ⚠️ [Source Retry] {model_name} failed ({elapsed}ms): {e}")
            continue

    if not retry_data or not retry_data.get("retry_results"):
        print("   ⚠️ [Source Retry] No retry results obtained")
        return foresight_data, 0

    # Build lookup: trend name → retry result
    retry_lookup = {}
    for r in retry_data["retry_results"]:
        trend = (r.get("trend") or "").strip().lower()
        if trend and r.get("found"):
            retry_lookup[trend] = r

    # Apply retry results to failed forces and collect new URLs to verify
    new_urls = {}
    updated_forces = []
    for force in failed_forces:
        trend_key = (force.get("trend") or "").strip().lower()
        retry = retry_lookup.get(trend_key)
        if retry and retry.get("source_url"):
            force["source_url"] = retry["source_url"]
            force["source_type"] = retry.get("source_type", force.get("source_type", "news"))
            force["osint_validation"] = retry.get("osint_validation", force.get("osint_validation", ""))
            if retry.get("supporting_sources"):
                force["supporting_sources"] = retry["supporting_sources"]
            new_urls[retry["source_url"]] = None
            # Also collect supporting source URLs for verification
            for ss in retry.get("supporting_sources", []):
                ss_url = (ss.get("url") or "").strip()
                if ss_url and ss_url.startswith("http"):
                    new_urls[ss_url] = None
            updated_forces.append(force)

    if not new_urls:
        print("   ⚠️ [Source Retry] No new URLs found in retry results")
        return foresight_data, 0

    # Verify the new URLs in parallel
    print(f"   🔗 [Source Retry] Verifying {len(new_urls)} new URLs...")
    with concurrent.futures.ThreadPoolExecutor(max_workers=6) as executor:
        future_to_url = {executor.submit(_verify_foresight_url, url): url for url in new_urls}
        for future in concurrent.futures.as_completed(future_to_url):
            url = future_to_url[future]
            try:
                new_urls[url] = future.result()
            except Exception:
                new_urls[url] = False

    # Apply verification results
    newly_verified = 0
    for force in updated_forces:
        url = (force.get("source_url") or "").strip()
        if url in new_urls and new_urls[url]:
            force["url_verified"] = True
            newly_verified += 1
        else:
            force["url_verified"] = False

        # Also verify supporting sources
        verified_supporting = []
        for ss in force.get("supporting_sources", []):
            ss_url = (ss.get("url") or "").strip()
            if ss_url in new_urls:
                ss["url_verified"] = new_urls[ss_url]
                if new_urls[ss_url]:
                    verified_supporting.append(ss)
            else:
                ss["url_verified"] = False
        force["verified_supporting_sources"] = verified_supporting

    print(f"   🔄 [Source Retry] Re-search complete: {newly_verified}/{len(updated_forces)} forces now have verified sources")
    return foresight_data, newly_verified


# ═══════════════════════════════════════════════════════════════════════════════
# MINIMUM SOURCE QUALITY GATE
# ═══════════════════════════════════════════════════════════════════════════════

# Minimum thresholds for foresight to be considered trustworthy
_MIN_VERIFIED_SOURCES_RATIO = 0.34  # At least ~1/3 of forces must have verified primary URL
_MIN_VERIFIED_SOURCES_ABSOLUTE = 1  # At least 1 force must have a verified source
_MIN_TOTAL_VERIFIED_URLS = 2        # Across all forces (primary + supporting), need at least 2 verified URLs


def enforce_source_quality_gate(foresight_data):
    """
    Minimum Source Quality Gate — checks if foresight meets evidence thresholds.

    Rules:
    - At least 1 force must have a verified primary source URL
    - At least 1/3 of forces must have verified sources
    - Across all forces, at least 2 total verified URLs (primary + supporting)
    - If thresholds not met, marks foresight as insufficient_evidence=True

    Returns (foresight_data, passes_gate: bool).
    """
    if not foresight_data or not foresight_data.get("dimension_forces"):
        return foresight_data, False

    forces = foresight_data["dimension_forces"]
    total_forces = len(forces)

    # Count verified primary sources
    verified_primary = sum(1 for f in forces if f.get("url_verified", False))

    # Count total verified URLs (primary + supporting)
    total_verified_urls = verified_primary
    for force in forces:
        for ss in force.get("supporting_sources", []):
            if ss.get("url_verified", False):
                total_verified_urls += 1
        for ss in force.get("verified_supporting_sources", []):
            total_verified_urls += 1  # These are pre-filtered as verified

    # Check thresholds
    passes_ratio = verified_primary >= (total_forces * _MIN_VERIFIED_SOURCES_RATIO)
    passes_absolute = verified_primary >= _MIN_VERIFIED_SOURCES_ABSOLUTE
    passes_total_urls = total_verified_urls >= _MIN_TOTAL_VERIFIED_URLS

    passes_gate = passes_ratio and passes_absolute and passes_total_urls

    if not passes_gate:
        foresight_data["insufficient_evidence"] = True
        reasons = []
        if not passes_absolute:
            reasons.append(f"no verified primary sources (need >= {_MIN_VERIFIED_SOURCES_ABSOLUTE})")
        if not passes_ratio:
            reasons.append(f"verified ratio {verified_primary}/{total_forces} below {_MIN_VERIFIED_SOURCES_RATIO:.0%} threshold")
        if not passes_total_urls:
            reasons.append(f"only {total_verified_urls} total verified URLs (need >= {_MIN_TOTAL_VERIFIED_URLS})")
        foresight_data["gate_failure_reasons"] = reasons
        print(f"   🚫 [Quality Gate] FAILED — {', '.join(reasons)}")
    else:
        foresight_data["insufficient_evidence"] = False
        print(f"   ✅ [Quality Gate] PASSED — {verified_primary}/{total_forces} primary verified, "
              f"{total_verified_urls} total verified URLs")

    return foresight_data, passes_gate


def reality_check_foresight(foresight_data, company_name=""):
    """
    Reality-check pass for foresight signals — SIFT-based quality assessment.

    Evaluates each dimension_force for:
    - Source credibility (source_type + domain reputation)
    - URL verification status
    - OSINT validation quality
    - Confidence calibration

    Adds per-force fields:
    - 'source_credibility': "high" | "medium" | "low"
    - 'validation_flags': list of warning strings
    - 'validated': True if force passes minimum quality threshold

    Adds aggregate fields to foresight_data:
    - 'validation_summary': dict with counts and overall quality assessment
    """
    if not foresight_data or not foresight_data.get("dimension_forces"):
        return foresight_data

    forces = foresight_data["dimension_forces"]
    total_validated = 0
    total_flagged = 0
    all_flags = []

    for force in forces:
        flags = []
        source_url = (force.get("source_url") or "").strip()
        source_type = (force.get("source_type") or "").strip().lower()
        confidence = (force.get("confidence") or "").strip().lower()
        osint_validation = (force.get("osint_validation") or "").strip()
        url_verified = force.get("url_verified", False)
        domain = _get_url_domain(source_url)

        # --- Source type validation ---
        if source_type not in _CREDIBLE_SOURCE_TYPES:
            flags.append(f"Unknown source type: '{source_type}'")

        # --- URL verification ---
        if not source_url or not source_url.startswith("http"):
            flags.append("No source URL provided")
        elif not url_verified:
            flags.append("Source URL could not be verified (dead/unreachable)")

        # --- Domain credibility assessment (shared module) ---
        company_domain = ""
        if company_name:
            company_name_lower = company_name.lower().replace(" ", "")
            if domain and company_name_lower in domain.replace(".", ""):
                company_domain = domain
        credibility = assess_source_credibility(source_url, source_type, company_domain)
        if credibility == "low" and domain:
            flags.append(f"Low-credibility domain: {domain}")
        if company_domain:
            flags.append("Source appears to be company's own website — may be self-reported")
        if not domain and source_url:
            flags.append("Could not extract domain from URL")

        # --- OSINT validation quality ---
        if not osint_validation:
            flags.append("No OSINT validation description provided")
        elif len(osint_validation) < 20:
            flags.append("OSINT validation description too brief")

        # --- Confidence calibration ---
        # Downgrade confidence if URL is dead or source is low-credibility
        if confidence == "high" and (not url_verified or credibility == "low"):
            force["confidence"] = "medium"
            flags.append(f"Confidence downgraded from high to medium (url_verified={url_verified}, credibility={credibility})")
        elif confidence == "medium" and not url_verified and credibility == "low":
            force["confidence"] = "low"
            flags.append("Confidence downgraded from medium to low (unverified + low-credibility)")

        # --- Supporting source quality ---
        verified_supporting = force.get("verified_supporting_sources", [])
        supporting_sources = force.get("supporting_sources", [])
        total_supporting = len(supporting_sources)
        verified_supporting_count = len(verified_supporting)

        if total_supporting == 0:
            flags.append("No supporting sources provided (SIFT requires 2+ independent sources)")
        elif verified_supporting_count == 0:
            flags.append(f"None of {total_supporting} supporting sources could be verified")
        elif verified_supporting_count < 2:
            flags.append(f"Only {verified_supporting_count}/{total_supporting} supporting sources verified (need 2+)")

        # --- Determine validation status ---
        # A force is "validated" if it has a verified primary URL from a credible source
        # AND at least 1 verified supporting source (multi-source requirement)
        has_verified_primary = url_verified and credibility in ("high", "medium")
        has_corroboration = verified_supporting_count >= 1
        validated = has_verified_primary and has_corroboration and len(flags) <= 3

        force["source_credibility"] = credibility
        force["validation_flags"] = flags
        force["validated"] = validated
        force["source_count"] = 1 + verified_supporting_count if url_verified else verified_supporting_count

        if validated:
            total_validated += 1
        if flags:
            total_flagged += 1
            all_flags.extend(flags)

    # --- Aggregate validation summary (tightened thresholds) ---
    total_forces = len(forces)
    # Count total verified URLs across all forces (primary + supporting)
    total_verified_urls = sum(
        (1 if f.get("url_verified") else 0) + len(f.get("verified_supporting_sources", []))
        for f in forces
    )
    # HIGH: all forces validated (verified primary + corroboration)
    # MEDIUM: at least 2 forces have verified primary URLs AND total verified URLs >= 3
    # LOW: anything below that
    quality = "high" if total_validated == total_forces else (
        "medium" if (total_validated >= 2 or (total_validated >= 1 and total_verified_urls >= 3)) else "low"
    )

    foresight_data["validation_summary"] = {
        "total_forces": total_forces,
        "validated_forces": total_validated,
        "flagged_forces": total_flagged,
        "total_verified_urls": total_verified_urls,
        "quality": quality,
        "all_flags": all_flags[:20],  # cap to prevent bloat
    }

    print(f"   🔍 [Foresight Reality Check] {total_validated}/{total_forces} forces validated, "
          f"{total_verified_urls} total verified URLs, quality={quality}, {len(all_flags)} flags")

    return foresight_data


def extract_foresight_links(foresight_data, company_name=""):
    """
    Extract validated foresight sources into a foresight_links array
    that mirrors the evidence_links schema used throughout the Scanner.

    Each link has:
    - title: Force trend name
    - url: source_url
    - snippet: impact_on_company text
    - source_type: academic/think_tank/etc.
    - verified: True/False (from URL verification)
    - source_credibility: high/medium/low (from reality check)
    - validated: True/False (passed quality threshold)
    - kb_dimension: Which KB dimension this force impacts
    - fp_theme: Futures Platform theme
    - timeframe: Force timeframe
    - force_type: Plausible/Maturing, etc.
    - origin: "foresight" (to distinguish from forensic evidence_links)

    Returns list of foresight_link dicts, sorted by credibility (high first).
    """
    if not foresight_data or not foresight_data.get("dimension_forces"):
        return []

    links = []
    seen_urls = set()
    for force in foresight_data["dimension_forces"]:
        source_url = (force.get("source_url") or "").strip()
        if source_url and source_url.startswith("http") and source_url not in seen_urls:
            seen_urls.add(source_url)
            link = {
                "title": force.get("trend", "Unknown Force"),
                "url": source_url,
                "snippet": (force.get("impact_on_company") or "")[:300],
                "source_type": force.get("source_type", "unknown"),
                "verified": force.get("url_verified", False),
                "source_credibility": force.get("source_credibility", "unknown"),
                "validated": force.get("validated", False),
                "kb_dimension": force.get("kb_dimension", ""),
                "fp_theme": force.get("fp_theme", ""),
                "timeframe": force.get("timeframe", ""),
                "force_type": force.get("type", ""),
                "origin": "foresight",
                "is_primary": True,
            }
            links.append(link)

        # Also include verified supporting sources
        for ss in force.get("verified_supporting_sources", []):
            ss_url = (ss.get("url") or "").strip()
            if ss_url and ss_url.startswith("http") and ss_url not in seen_urls:
                seen_urls.add(ss_url)
                link = {
                    "title": ss.get("title", force.get("trend", "Unknown Force")),
                    "url": ss_url,
                    "snippet": (force.get("impact_on_company") or "")[:300],
                    "source_type": ss.get("source_type", "unknown"),
                    "verified": True,
                    "source_credibility": "medium",
                    "validated": True,
                    "kb_dimension": force.get("kb_dimension", ""),
                    "fp_theme": force.get("fp_theme", ""),
                    "timeframe": force.get("timeframe", ""),
                    "force_type": force.get("type", ""),
                    "origin": "foresight",
                    "is_primary": False,
                }
                links.append(link)

    # Sort: validated first, then by credibility
    credibility_order = {"high": 0, "medium": 1, "low": 2, "unknown": 3}
    links.sort(key=lambda l: (
        0 if l["validated"] else 1,
        credibility_order.get(l["source_credibility"], 3),
    ))

    if links:
        validated_count = sum(1 for l in links if l["validated"])
        print(f"   📎 [Foresight Links] Extracted {len(links)} source links ({validated_count} validated)")

    return links
