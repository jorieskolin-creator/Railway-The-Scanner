"""
detailed_report.py — Deep diagnostic report generator.

Produces an 800-1200 word evidence-grounded report that expands on the
executive summary with per-dimension deep dives, stakeholder context,
and forward-looking insights. Designed to be the "full report"
that the company modal summary is the executive summary of.
"""

import json
import os
import re
import time

from google import genai
from google.genai import types as genai_types
from gemini_backoff import call_with_503_backoff


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


def _build_input_payload(full_data: dict, company_name: str, foresight_data: dict | None) -> dict:
    """Build a rich evidence payload for the detailed report — more depth than general_summary."""

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
    verified_links = [
        {
            "title": (l.get("title", "Source") or "Source")[:80],
            "snippet": (l.get("snippet") or "")[:300],
        }
        for l in full_data.get("evidence_links", [])
        if l.get("verified") is True and l.get("url") and not _is_legal_page(l.get("url", ""))
    ][:20]

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
        "evidence_links": verified_links,
        "financial_findings": fin_findings,
        "key_people": people,
        "washing": washing,
    }

    if delta:
        result["reality_score"] = full_data.get("reality_score", 0)

    # Foresight data (if available)
    if foresight_data and foresight_data.get("dimension_forces"):
        result["foresight"] = {
            "forces": [
                {
                    "trend": f.get("trend", ""),
                    "type": f.get("type", ""),
                    "timeframe": f.get("timeframe", ""),
                    "kb_dimension": f.get("kb_dimension", ""),
                    "impact_on_company": f.get("impact_on_company", ""),
                }
                for f in foresight_data["dimension_forces"][:3]
            ],
            "cross_impact_chain": foresight_data.get("cross_impact_chain", ""),
            "scenarios": foresight_data.get("scenarios", {}),
            "climate_exposure": foresight_data.get("climate_exposure"),
        }

    return result


def _build_prompt(payload: dict, company_name: str) -> str:
    """Build the detailed report prompt."""
    forbidden = ", ".join(_FORBIDDEN_TERMS)

    # Foresight paragraph instruction
    foresight_instruction = ""

    return f"""You are a senior B2B intelligence analyst writing the detailed diagnostic section of a forensic company assessment report about {company_name}.

This report will be read by strategy consultants and C-level advisors. It must be evidence-grounded, analytically rigorous, and provide depth that an executive summary cannot.

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
7. PARAGRAPHS: Separate with \\n\\n. Required for rendering.
8. FORMAT: Prose with section headings only. Each section starts with "## Heading" on its own line followed by \\n\\n then the paragraph body. No bullet points, no other markdown, no links.
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
) -> tuple[str, str]:
    """
    Generate an 800-1200 word detailed diagnostic report.

    Returns (report_text, model_used).
    Returns ("", "") on failure.
    """
    gemini_key = os.environ.get("GEMINI_API_KEY") or os.environ.get("API_KEY")
    if not gemini_key:
        print("   ⚠️ [Detailed Report] No GEMINI_API_KEY — skipping")
        return "", ""

    payload = _build_input_payload(full_data, company_name, foresight_data)
    prompt = _build_prompt(payload, company_name)

    client = genai.Client(api_key=gemini_key)

    PRIMARY_MODEL = "gemini-3.1-pro-preview"

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

    # Attempts 1 & 2: primary model
    for attempt, config in enumerate([FIRST_CONFIG, RETRY_CONFIG], start=1):
        try:
            t0 = time.time()
            response = call_with_503_backoff(
                lambda: client.models.generate_content(
                    model=PRIMARY_MODEL, contents=prompt, config=config,
                ),
                label=f"Detailed Report {PRIMARY_MODEL}",
                model=PRIMARY_MODEL,
                timeout=120,
            )
            text = _strip_links((response.text or "").strip())
            wc = _word_count(text)
            elapsed = int((time.time() - t0) * 1000)
            print(f"   [Detailed Report] {PRIMARY_MODEL} attempt {attempt}: {wc} words ({elapsed}ms)")

            if _WORD_MIN <= wc <= _WORD_MAX:
                return text, PRIMARY_MODEL

            if attempt == 1:
                print(f"   [Detailed Report] {wc} words outside [{_WORD_MIN}-{_WORD_MAX}] — retrying")
                continue

            # Attempt 2: use if non-empty (best-effort)
            if text and wc >= 500:
                print(f"   [Detailed Report] Using attempt 2 result ({wc} words) — outside range but usable")
                return text, PRIMARY_MODEL

        except Exception as e:
            print(f"   ❌ [Detailed Report] {PRIMARY_MODEL} attempt {attempt} failed: {e}")
            if attempt == 1:
                continue

    # Attempt 3: Gemini 2.5 Pro backup
    BACKUP_MODEL = "gemini-2.5-pro"
    try:
        print(f"   [Detailed Report] Primary failed — trying {BACKUP_MODEL}")
        t0 = time.time()
        response = call_with_503_backoff(
            lambda: client.models.generate_content(
                model=BACKUP_MODEL, contents=prompt,
                config=genai_types.GenerateContentConfig(
                    temperature=0.8,
                    max_output_tokens=8000,
                    thinking_config=genai_types.ThinkingConfig(thinking_budget=2048),
                ),
            ),
            label=f"Detailed Report {BACKUP_MODEL}",
            model=BACKUP_MODEL,
            timeout=120,
        )
        text = _strip_links((response.text or "").strip())
        wc = _word_count(text)
        elapsed = int((time.time() - t0) * 1000)
        print(f"   [Detailed Report] {BACKUP_MODEL}: {wc} words ({elapsed}ms)")
        if text and wc >= 400:
            return text, BACKUP_MODEL
    except Exception as e:
        print(f"   ❌ [Detailed Report] {BACKUP_MODEL} failed: {e}")

    # Attempt 4: Mistral Large backup (independent provider)
    mistral_key = os.environ.get("MISTRAL_API_KEY")
    if mistral_key:
        try:
            from mistralai import Mistral
            print(f"   [Detailed Report] Gemini failed — trying mistral-large-latest")
            t0 = time.time()
            mistral_client = Mistral(api_key=mistral_key)
            mistral_resp = mistral_client.chat.complete(
                model="mistral-large-latest",
                temperature=0.3,
                max_tokens=6000,
                messages=[{"role": "user", "content": prompt}],
            )
            text = _strip_links((mistral_resp.choices[0].message.content or "").strip())
            wc = _word_count(text)
            elapsed = int((time.time() - t0) * 1000)
            print(f"   [Detailed Report] mistral-large-latest: {wc} words ({elapsed}ms)")
            if text and wc >= 400:
                return text, "mistral-large-latest"
        except Exception as e:
            print(f"   ❌ [Detailed Report] mistral-large-latest failed: {e}")

    return "", ""
