"""
strategic_foresight.py — Strategic Foresight module for forward-looking analysis.

Generates per-dimension industry forces, cross-impact chains, micro-scenarios,
and optional climate exposure assessment. Uses Gemini + Google Search grounding.

Methodology inspired by Futures Platform's 30-theme taxonomy (NAICS/SIC-based)
and SIFT/Lateral Reading OSINT validation principles.
"""

import json
import os
import re
import time

from google import genai
from google.genai import types as genai_types
from gemini_backoff import call_with_503_backoff


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
- "type": One of: "Strengthening" (gathering momentum) | "Weakening" (declining) | "Wild Card" (low probability, massive impact) | "Weak Signal" (early, insufficient data)
- "timeframe": "6-12mo" | "12-24mo" | "24-36mo"
- "kb_dimension": Which priority dimension this impacts most
- "fp_theme": Which Futures Platform theme this maps to
- "impact_on_company": 1-2 sentences on how this specifically affects {company_name} given their archetype ({archetype}) and current state
- "proposal_angle": 1 sentence on how to use this in a consulting proposal to {company_name}
- "source_url": URL from your web search results (ORIGINAL source only)
- "source_type": "academic" | "think_tank" | "industry_analyst" | "government" | "news"
- "confidence": "high" | "medium" | "low"
- "osint_validation": 1 sentence on how you verified this signal (e.g., "Cross-referenced OECD report with McKinsey survey")

TASK 2 — CROSS-IMPACT CHAIN:
Trace how the strongest identified force cascades through {company_name}'s weakest dimensions. Format as a chain: "Force X → impacts Dimension A → which cascades to Dimension B → ultimately affecting Dimension C." This must be specific to THIS company's situation, not generic.

TASK 3 — MICRO-SCENARIOS (specific to {company_name}, not generic industry scenarios):
- "acceleration": What specifically happens in 12-24 months if {company_name} acts NOW on their weakest dimensions while riding the strengthening forces? Reference their archetype.
- "status_quo": What specifically happens if {company_name} continues their current trajectory while these forces unfold? Be concrete about consequences for a "{archetype}" company.
- "disruption": What specific wild card, competitor move, or regulatory shift could disrupt {company_name} regardless of their own actions?
{climate_section}
RETURN STRICT JSON ONLY:
{{
  "dimension_forces": [
    {{
      "trend": "...",
      "type": "Strengthening|Weakening|Wild Card|Weak Signal",
      "timeframe": "6-12mo|12-24mo|24-36mo",
      "kb_dimension": "...",
      "fp_theme": "...",
      "impact_on_company": "...",
      "proposal_angle": "...",
      "source_url": "https://...",
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
    Generate a 150-200 word "Future Insight" narrative from foresight data.

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
- 150-200 words, 2-3 paragraphs separated by \\n\\n
- First paragraph: The 1-2 most impactful external forces and their timeframe.
  Name the forces concretely — no vague "market changes are coming."
- Second paragraph: What this specifically means for THIS company given
  their current weaknesses. Connect the dots between external forces and internal gaps.
{climate_instruction}
- Tone: Strategic, forward-looking, grounded in evidence. Like a foresight
  analyst briefing a board of directors. Not marketing fluff.
- Do NOT reference internal scores by name or number.
- Do NOT use any of these terms: {forbidden}. Use plain business language.
- Every claim must trace to a force identified in the foresight analysis.
- If confidence is low on a force, use hedging language ("emerging signals suggest...")
- FORMAT: Plain prose only. No headers, no bullet points, no markdown, no links.

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
