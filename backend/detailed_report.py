"""
detailed_report.py — Deep diagnostic report generator.

Produces an 800-1200 word evidence-grounded report that expands on the
executive summary with per-dimension deep dives, stakeholder context,
and (optionally) strategic foresight. Designed to be the "full report"
that the company modal summary is the executive summary of.
"""

import json
import os
import re
import time

import anthropic
from google import genai
from google.genai import types as genai_types
from gemini_backoff import call_with_503_backoff
from gemini_client import get_gemini_client
from source_quality import assess_source_scope


_FORBIDDEN_TERMS = [
    "Thriving", "Modernizing", "Cash Cow", "At Risk",
    "Digital Taylorism", "Feature Factory", "Command and Control",
    "Zombie Scrum", "Fake Agile", "Modernization Washing", "AI Washing",
    "Agile Washing", "Platform Engineering", "Flow Efficiency",
    "Psychological Safety", "Value Streams", "Data Mesh",
    "Knowledge Base", "KB dimension", "archetype",
    "Pilot Purgatory", "Excel Hell", "Kapulanvaihto", "Portinvartijuus",
]

_WORD_MIN = 800
_WORD_MAX = 1200


def _build_input_payload(full_data: dict, company_name: str, foresight_data: dict | None, prior_summaries: dict | None = None) -> dict:
    """Build a rich evidence payload for the detailed report — more depth than general_summary.

    When prior_summaries is provided, the report acts as a meta-synthesis of all
    previously generated analyses (General Summary, AI Diagnosis, Foresight Insight,
    metrics, etc.) rather than duplicating analysis from raw evidence.
    """

    # Legal/ToS exclusion (same pattern as general_summary.py)
    _LEGAL_PATH_SEGMENTS = (
        "terms", "legal", "disclaimer", "privacy", "cookie", "tos", "eula",
        "tietosuoja", "kayttoehdot", "ehdot", "yksityisyys", "rekisteriseloste",
        "gdpr", "oikeudellinen", "vastuuvapaus",
    )

    def _is_legal_page(url: str) -> bool:
        return any(seg in url.lower() for seg in _LEGAL_PATH_SEGMENTS)

    # Delta from history
    history = full_data.get("history") or []
    delta = None
    if history:
        prev = history[-1]
        prev_reality = prev.get("reality_score")
        prev_financial = prev.get("financial_index")
        prev_date = (prev.get("lastUpdated") or "")[:10]
        parts = []
        cur_reality = full_data.get("reality_score", 0)
        cur_financial = full_data.get("financial_index")
        if prev_reality is not None:
            diff = cur_reality - prev_reality
            direction = "improved" if diff > 0 else ("declined" if diff < 0 else "unchanged")
            parts.append(f"Reality Score {direction} from {prev_reality} to {cur_reality}")
        if prev_financial is not None and cur_financial is not None:
            fdiff = cur_financial - prev_financial
            fdirection = "improved" if fdiff > 0 else ("declined" if fdiff < 0 else "unchanged")
            parts.append(f"Financial Health {fdirection} from {prev_financial} to {cur_financial}")
        if parts:
            delta = {"previous_scan_date": prev_date, "changes": parts}

    # Evidence — richer than general_summary (20 sources, 300-char snippets)
    # Includes both forensic evidence_links and validated foresight_links.
    # Each item carries a "scope" flag derived from the URL/title/snippet.
    # "narrow" means the source describes a limited population (master's
    # thesis, single-department survey, regional pilot). The prompt's SCOPE
    # QUALIFICATION rule keys off this so the model adds qualifiers like "in
    # a survey of HR personnel" rather than presenting departmental findings
    # as company-wide truths.
    verified_links = []
    for l in full_data.get("evidence_links", []):
        if not (l.get("verified") is True and l.get("url") and not _is_legal_page(l.get("url", ""))):
            continue
        title = (l.get("title", "Source") or "Source")[:80]
        snippet = (l.get("snippet") or "")[:300]
        verified_links.append({
            "title": title,
            "snippet": snippet,
            "scope": assess_source_scope(l.get("url", ""), title, snippet),
        })
        if len(verified_links) >= 20:
            break

    # Add validated foresight sources (enriches the evidence pool with forward-looking sources)
    foresight_links = []
    for l in full_data.get("foresight_links", []):
        if not (l.get("verified") is True and l.get("validated") is True):
            continue
        title = (l.get("title", "Foresight Source") or "Foresight Source")[:80]
        snippet = (l.get("snippet") or "")[:300]
        foresight_links.append({
            "title": title,
            "snippet": snippet,
            "origin": "foresight",
            "source_type": l.get("source_type", ""),
            "scope": assess_source_scope(l.get("url", ""), title, snippet),
        })
        if len(foresight_links) >= 5:
            break

    # Key findings — more detail (12 items)
    key_findings = [
        {
            "category": item.get("category", ""),
            "description": item.get("description") or item.get("label", ""),
            "title": ((item.get("title") or item.get("category", "")) or "")[:80],
            "sentiment": item.get("sentiment", ""),
        }
        for item in full_data.get("reality_checklist", [])
        if item.get("found") and item.get("url") and not _is_legal_page(item.get("url", ""))
    ][:12]

    # Financial — sub-scores plus findings
    fin_findings = [str(f) for f in (full_data.get("financial_findings") or [])[:5]]
    fin_sub = full_data.get("financial_sub_scores", {})

    # Key people with enrichment data
    people = []
    for p in (full_data.get("key_people") or [])[:6]:
        entry = {"name": p.get("name", ""), "role": p.get("role", "")}
        if p.get("headline"):
            entry["headline"] = p["headline"]
        if p.get("career_moves"):
            entry["career_moves"] = p["career_moves"][:2]
        if p.get("topics"):
            entry["topics"] = p["topics"][:3]
        if entry["name"]:
            people.append(entry)

    # Washing
    washing = None
    if full_data.get("washing_flag"):
        washing = {
            "type": full_data.get("washing_type", "Unknown"),
            "gap": full_data.get("reality_gap", 0),
        }

    result = {
        "company_name": company_name,
        "domain": full_data.get("domain", "Other"),
        "delta": delta,
        "financial_index": full_data.get("financial_index"),
        "financial_sub_scores": fin_sub,
        "key_findings": key_findings,
        "evidence_links": verified_links + foresight_links,
        "financial_findings": fin_findings,
        "key_people": people,
        "washing": washing,
    }

    if delta:
        result["reality_score"] = full_data.get("reality_score", 0)

    # Foresight data (if available) — pass rich context for evidence-grounded narrative
    if foresight_data and foresight_data.get("dimension_forces"):
        result["foresight"] = {
            "forces": [
                {
                    "trend": f.get("trend", ""),
                    "type": f.get("type", ""),
                    "timeframe": f.get("timeframe", ""),
                    "kb_dimension": f.get("kb_dimension", ""),
                    "fp_theme": f.get("fp_theme", ""),
                    "impact_on_company": f.get("impact_on_company", ""),
                    "proposal_angle": f.get("proposal_angle", ""),
                    "confidence": f.get("confidence", ""),
                    "source_type": f.get("source_type", ""),
                    "lifecycle_stage": f.get("lifecycle_stage", ""),
                    "trend_momentum": f.get("trend_momentum", ""),
                    "action_timing": f.get("action_timing", ""),
                }
                for f in foresight_data["dimension_forces"][:4]
            ],
            "cross_impact_chain": foresight_data.get("cross_impact_chain", ""),
            "scenarios": foresight_data.get("scenarios", {}),
            "climate_exposure": foresight_data.get("climate_exposure"),
        }

    # Prior summaries — when available, the detailed report synthesizes these
    # instead of duplicating the analysis work they already performed.
    if prior_summaries:
        result["prior_analyses"] = {}
        if prior_summaries.get("general_summary"):
            result["prior_analyses"]["general_summary"] = prior_summaries["general_summary"]
        if prior_summaries.get("foresight_summary"):
            result["prior_analyses"]["foresight_summary"] = prior_summaries["foresight_summary"]
        if prior_summaries.get("ai_summary"):
            result["prior_analyses"]["ai_diagnosis"] = prior_summaries["ai_summary"]
        if prior_summaries.get("why_what"):
            result["prior_analyses"]["why_and_what"] = prior_summaries["why_what"]

    return result


def _build_prompt(payload: dict, company_name: str) -> str:
    """Build the detailed report prompt."""
    forbidden = ", ".join(_FORBIDDEN_TERMS)

    # Foresight paragraph instruction
    foresight_instruction = ""
    if payload.get("foresight"):
        foresight_instruction = """
SECTION — heading "Strategic Foresight" (~100-150 words)
Based on the foresight data in the evidence below:
- Name the 1-2 most impactful external forces and their timeframe
- Explain how these forces specifically affect THIS company given their current weaknesses
- Reference the cross-impact chain if it connects 2+ dimensions
- Mention the most relevant scenario (status quo or disruption)
Use plain, forward-looking language."""

    # Meta-synthesis instruction when prior analyses are available
    synthesis_instruction = ""
    if payload.get("prior_analyses"):
        available = ", ".join(k.replace("_", " ").title() for k in payload["prior_analyses"])
        synthesis_instruction = f"""
IMPORTANT — META-SYNTHESIS APPROACH:
This report has access to previously generated analyses: {available}.
Your job is to SYNTHESIZE and DEEPEN these prior analyses into a coherent, unified diagnostic narrative.
Do NOT simply repeat what they say — instead, find the through-lines, contradictions, and deeper implications
that emerge when all analyses are read together. Add analytical depth that individual summaries could not provide.
Use the prior analyses as your PRIMARY input; the raw evidence data is provided for fact-checking and detail only.
"""

    return f"""You are a senior B2B intelligence analyst writing the detailed diagnostic section of a forensic company assessment report about {company_name}.

This report will be read by strategy consultants and C-level advisors. It must be evidence-grounded, analytically rigorous, and provide depth that an executive summary cannot.
{synthesis_instruction}
STRUCTURED INPUT DATA:
{json.dumps(payload, ensure_ascii=False, indent=2)}

REPORT STRUCTURE ({_WORD_MIN}-{_WORD_MAX} words, 5-7 sections separated by \\n\\n):

Each section MUST begin with a heading line in the format: ## Heading Text
The heading line must be on its own line, followed by \\n\\n, then the paragraph body.
Choose concise, descriptive heading text for each section (2-4 words).

SECTION 1 — heading "Key Findings" (~100-150 words)
State the company's market position, financial health trajectory, and the central tension the assessment reveals. Do NOT use archetype labels — describe the position in plain business language.
If delta data is present: open with the trend story (what changed since the previous assessment and why).
If no delta: open with the single most significant strategic observation.

SECTIONS 2-4 — DIMENSION DEEP DIVES (~150-200 words each)
Choose a heading that names the dimension in plain business language (e.g., "## Technology & Infrastructure", "## Sustainability Strategy", "## Workforce & Operations").
For the 3 dimensions where evidence is richest (strongest or weakest):
- Cite 2-3 specific evidence findings by referencing their source titles naturally in the text (e.g., "According to their engineering blog..." or "A review of recent job postings reveals...")
- Explain what the evidence reveals about the company's maturity in this area
- Connect to business implications — what this means for competitiveness or risk
- If a weakness: describe what improvement would look like in concrete terms
Only cover dimensions with genuine evidence. If evidence is thin for a dimension, skip it — depth over breadth.

SECTION 5 — heading "Leadership & Stakeholders" (~100-150 words)
If key_people data is available in the evidence:
- Name the identified modernization stakeholders and their roles
- What their backgrounds or career moves suggest about the company's strategic direction
- Any notable observations (e.g., recent leadership changes, domain expertise patterns)
If no stakeholder data is available: skip this section entirely. Do not fabricate.
{foresight_instruction}

FINAL SECTION — heading "Verdict" (~100-150 words)
- If washing data indicates a gap between stated ambitions and operational reality, name it explicitly
- If rescan delta is available, note persistent patterns or meaningful trajectory changes
- State the single most critical action this company should consider
- If overall evidence confidence is limited, explicitly acknowledge the constraint

MANDATORY RULES:
1. WORD COUNT: {_WORD_MIN}-{_WORD_MAX} words. Count carefully. This is non-negotiable.
2. EVIDENCE-FIRST: Every analytical claim must reference a specific finding or source from the data above. No evidence = no claim.
3. PLAIN LANGUAGE: Do not use any of these terms: {forbidden}. Use descriptive business language instead.
4. NO SCORES: Do not mention score names, score numbers, or internal metrics in prose.
5. SOURCE REFERENCES: When citing evidence, use source titles naturally in text. Do NOT include URLs — those display separately.
6. FORBIDDEN ACTIONS: Do not fabricate findings, invent people problems, assume leadership gaps, or extrapolate small signals into sweeping assessments.
7. SCOPE QUALIFICATION — CRITICAL: Each item in evidence_links carries a "scope" field with two possible values: "broad" (default — applies to the company at large) and "narrow" (the source describes a limited population: a master's thesis, a single-department survey, a regional pilot, a study of N participants).
   - When citing a number or finding from a "narrow" source, you MUST include a scope qualifier in the same sentence: "in a survey of HR personnel", "in a 100-respondent study", "in a Vaasa-based pilot", "according to a master's thesis". Never present a narrow-scope statistic as a company-wide truth.
   - If two evidence items make conflicting quantitative claims about the same topic (one "narrow", one "broad"), cite both with their scope side by side and let the reader weigh them — never silently pick one. Do not let a single narrow-scope finding define the company's stance on a topic when broader-scope evidence exists in the same payload.
   - This rule overrides the temptation to lead with the most striking number. A narrow-scope finding is interesting context, not a portfolio-level conclusion.
8. PARAGRAPHS: Separate with \\n\\n. Required for rendering.
9. FORMAT: Prose with section headings only. Each section starts with "## Heading" on its own line followed by \\n\\n then the paragraph body. No bullet points, no other markdown, no links.
9. TEMPORAL PRECISION: Use the most recent annual figures, not quarterly snapshots presented as annual results.
10. ORGANIZATIONAL CLAIMS: NEVER claim leadership gaps, unfilled roles, or organizational problems unless the evidence explicitly states them.

Output only the prose text. Nothing else."""


def _word_count(text: str) -> int:
    return len(text.split())


def _strip_links(text: str) -> str:
    """Remove any [label](url) markdown links the model sneaks in."""
    return re.sub(r'\[([^\]]+)\]\(https?://[^)]+\)', r'\1', text)


def generate_detailed_report(
    full_data: dict,
    company_name: str,
    foresight_data: dict | None = None,
    prior_summaries: dict | None = None,
) -> tuple[str, str]:
    """
    Generate an 800-1200 word detailed diagnostic report.

    When prior_summaries is provided (general_summary, foresight_summary, ai_summary,
    why_what), the report acts as a meta-synthesis of all previously generated
    analyses rather than duplicating analysis from raw evidence. No new searches
    are performed — it works entirely from data already in memory.

    Model chain: Sonnet 4.6 → Gemini 3.1 Pro → Gemini 2.5 Pro.
    Returns (report_text, model_used).
    Returns ("", "") on failure.
    """
    payload = _build_input_payload(full_data, company_name, foresight_data, prior_summaries)
    prompt = _build_prompt(payload, company_name)

    # --- Attempt 1: Claude Sonnet 4.6 (primary) ---
    anthropic_key = os.environ.get("ANTHROPIC_API_KEY", "").strip()
    if anthropic_key:
        SONNET_MODEL = "claude-sonnet-4-6"
        try:
            print(f"   📝 [Detailed Report] Attempting {SONNET_MODEL}...")
            t0 = time.time()
            claude_client = anthropic.Anthropic(api_key=anthropic_key)
            response = claude_client.messages.create(
                model=SONNET_MODEL,
                max_tokens=8000,
                thinking={
                    "type": "enabled",
                    "budget_tokens": 5000,
                },
                messages=[{"role": "user", "content": prompt}],
            )
            # Extract text from response blocks (skip thinking blocks)
            text_parts = []
            for block in response.content:
                if block.type == "text":
                    text_parts.append(block.text)
            text = _strip_links("\n".join(text_parts).strip())
            wc = _word_count(text)
            elapsed = int((time.time() - t0) * 1000)
            print(f"   📝 [Detailed Report] {SONNET_MODEL}: {wc} words ({elapsed}ms)")

            if text and wc >= _WORD_MIN:
                return text, SONNET_MODEL
            elif text and wc >= 500:
                print(f"   [Detailed Report] Sonnet result usable ({wc} words) — below target but acceptable")
                return text, SONNET_MODEL
            else:
                print(f"   ⚠️ [Detailed Report] Sonnet too short ({wc} words) — falling back to Gemini")
        except Exception as e:
            elapsed = int((time.time() - t0) * 1000) if 't0' in dir() else 0
            print(f"   ❌ [Detailed Report] {SONNET_MODEL} failed ({elapsed}ms): {e}")

    # --- Attempt 2-3: Gemini 3.1 Pro Preview (backup) ---
    try:
        client = get_gemini_client()
    except ValueError:
        print("   ⚠️ [Detailed Report] No GEMINI_API_KEY — skipping Gemini fallbacks")
        return "", ""
    GEMINI_PRIMARY = "gemini-3.1-pro-preview"

    FIRST_CONFIG = genai_types.GenerateContentConfig(
        temperature=0.8,
        max_output_tokens=8000,
        thinking_config=genai_types.ThinkingConfig(thinking_budget=3000),
    )
    RETRY_CONFIG = genai_types.GenerateContentConfig(
        temperature=0.8,
        max_output_tokens=8000,
        thinking_config=genai_types.ThinkingConfig(thinking_budget=1024),
    )

    for attempt, config in enumerate([FIRST_CONFIG, RETRY_CONFIG], start=1):
        try:
            t0 = time.time()
            response = call_with_503_backoff(
                lambda: client.models.generate_content(
                    model=GEMINI_PRIMARY, contents=prompt, config=config,
                ),
                label=f"Detailed Report {GEMINI_PRIMARY}",
                model=GEMINI_PRIMARY,
                timeout=120,
            )
            text = _strip_links((response.text or "").strip())
            wc = _word_count(text)
            elapsed = int((time.time() - t0) * 1000)
            print(f"   [Detailed Report] {GEMINI_PRIMARY} attempt {attempt}: {wc} words ({elapsed}ms)")

            if _WORD_MIN <= wc <= _WORD_MAX:
                return text, GEMINI_PRIMARY

            if attempt == 1:
                print(f"   [Detailed Report] {wc} words outside [{_WORD_MIN}-{_WORD_MAX}] — retrying")
                continue

            if text and wc >= 500:
                print(f"   [Detailed Report] Using attempt 2 result ({wc} words) — outside range but usable")
                return text, GEMINI_PRIMARY

        except Exception as e:
            print(f"   ❌ [Detailed Report] {GEMINI_PRIMARY} attempt {attempt} failed: {e}")
            if attempt == 1:
                continue

    # --- Attempt 4: Gemini 2.5 Pro (second backup) ---
    GEMINI_BACKUP = "gemini-2.5-pro"
    try:
        print(f"   [Detailed Report] Primary models failed — trying {GEMINI_BACKUP}")
        t0 = time.time()
        response = call_with_503_backoff(
            lambda: client.models.generate_content(
                model=GEMINI_BACKUP, contents=prompt,
                config=genai_types.GenerateContentConfig(
                    temperature=0.8,
                    max_output_tokens=8000,
                    thinking_config=genai_types.ThinkingConfig(thinking_budget=2048),
                ),
            ),
            label=f"Detailed Report {GEMINI_BACKUP}",
            model=GEMINI_BACKUP,
            timeout=120,
        )
        text = _strip_links((response.text or "").strip())
        wc = _word_count(text)
        elapsed = int((time.time() - t0) * 1000)
        print(f"   [Detailed Report] {GEMINI_BACKUP}: {wc} words ({elapsed}ms)")
        if text and wc >= 400:
            return text, GEMINI_BACKUP
    except Exception as e:
        print(f"   ❌ [Detailed Report] {GEMINI_BACKUP} failed: {e}")

    return "", ""
