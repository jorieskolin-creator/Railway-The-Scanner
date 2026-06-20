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
from llm_clients import CLAUDE_OPUS_MODEL, call_openai_text
from source_quality import (
    assess_source_scope, is_generation_eligible_source,
    is_foresight_context_eligible_source,
)
from contradiction_detector import detect_contradictions
from entity_identity import apply_entity_scope


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
    previously generated analyses (General Summary, AI Diagnosis, structured foresight evidence,
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
    # Company evidence and macro foresight are kept in separate payload lanes.
    # Each item carries a "scope" flag derived from the URL/title/snippet.
    # "narrow" means the source describes a limited population (master's
    # thesis, single-department survey, regional pilot). The prompt's SCOPE
    # QUALIFICATION rule keys off this so the model adds qualifiers like "in
    # a survey of HR personnel" rather than presenting departmental findings
    # as company-wide truths.
    verified_links = []
    for l in full_data.get("evidence_links", []):
        if not (
            l.get("verified") is True
            and l.get("url")
            and not _is_legal_page(l.get("url", ""))
            and is_generation_eligible_source(l)
        ):
            continue
        title = (l.get("title", "Source") or "Source")[:80]
        snippet = (l.get("source_quote") or l.get("snippet") or "")[:300]
        scoped_link = apply_entity_scope(dict(l), company_name)
        verified_links.append({
            "title": title,
            "snippet": snippet,
            "scope": assess_source_scope(l.get("url", ""), title, snippet),
            "entity_scope": scoped_link.get("entity_scope", "unknown"),
            "scope_warning": scoped_link.get("scope_warning", ""),
        })
        if len(verified_links) >= 20:
            break

    # Add validated foresight sources (enriches the evidence pool with forward-looking sources)
    foresight_links = []
    for l in full_data.get("foresight_links", []):
        if not is_foresight_context_eligible_source(l):
            continue
        title = (l.get("title", "Foresight Source") or "Foresight Source")[:80]
        snippet = (l.get("snippet") or "")[:300]
        foresight_links.append({
            "title": title,
            "snippet": snippet,
            "origin": "foresight",
            "source_type": l.get("source_type", ""),
            "scope": "macro_context",
            "company_specific": False,
            "claim_type": "foresight_context",
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
        if item.get("found")
        and item.get("url")
        and is_generation_eligible_source(item)
        and not _is_legal_page(item.get("url", ""))
    ][:12]

    # Financial — sub-scores plus findings
    fin_findings = [str(f) for f in (full_data.get("financial_findings") or [])[:5]]
    fin_sub = full_data.get("financial_sub_scores", {})

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
        "foresight_context_sources": foresight_links,
        "financial_findings": fin_findings,
        "washing": washing,
    }

    # Surface quantitative contradictions across the evidence pool so the
    # prompt can force the model to address both sides instead of silently
    # picking the more dramatic narrow-scope finding. Failure-safe: returns
    # [] if provider calls are unavailable.
    contradictions = detect_contradictions(verified_links, company_name)
    if contradictions:
        result["contradictions"] = contradictions

    if delta:
        result["reality_score"] = full_data.get("reality_score", 0)

    # Foresight data (if available) — pass rich context for evidence-grounded narrative
    def _force_generation_eligible(force: dict) -> bool:
        if not isinstance(force, dict):
            return False
        # Newer foresight force records carry the same analysis-verification
        # fields as evidence links.  If those fields exist, enforce them.
        if any(k in force for k in ("analysis_verified", "promotion_status", "generation_eligible", "claim_support_status")):
            return is_generation_eligible_source({
                **force,
                "url": force.get("url") or force.get("source_url"),
                "verified": force.get("verified", force.get("url_verified")),
            })
        return False

    eligible_forces = [
        f for f in (foresight_data.get("dimension_forces") or [])
        if _force_generation_eligible(f)
    ] if foresight_data else []

    if eligible_forces:
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
                for f in eligible_forces[:4]
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
7. SCOPE & SOURCE QUALIFICATION — CRITICAL:
   - CLAIM TIER (INTERNAL — never write tier codes in output): Evidence items carry a "claim_tier" field for your calibration only. Choose verbs to match: company self-claims → "states"/"positions itself as"; filed accounts → "disclosed"/"announced"; independent verification → "reported"/"confirmed"; weak signals → "suggests"/"may indicate"; foresight/trend context → "could create risk/opportunity". NEVER write tier codes ([A], [B], [C], [D], [E]) or the word "tier" in the output.
   - NARROW SCOPE: When citing a finding from a narrow-scope source (master's thesis, single-department survey, regional pilot), you MUST include a scope qualifier in the same sentence. Never present a narrow-scope statistic as a company-wide truth.
   - ANTI-EXTRAPOLATION: NEVER extrapolate a narrow-scope finding into a macro-trend or section-level diagnosis.
   - SELF-REPORTED DATA: For company self-claims, qualify every assertion ("The company states...", "According to the company..."). Do not omit specific numbers, but never present them as independently verified.
   - ENTITY SCOPE: entity_scope="canonical_group" may support target-company claims. entity_scope="local_legal_entity", "subsidiary", "former_name", or "parent_owner" must be qualified in the same sentence and must not be promoted into unqualified company-wide evidence.
8. BALANCING CONTRADICTIONS & PROPORTIONALITY — CRITICAL:
   - If a top-level "contradictions" array is present in the payload, EACH listed contradiction MUST be addressed in the prose — cite both sides with their respective scope ("broad"/"narrow") and source titles, in the same sentence or adjacent sentences. Silently picking one side is a violation of this rule.
   - If a narrow-scope independent source contradicts a broad self-reported metric, briefly state both side-by-side (e.g., "Wärtsilä reports [X] company-wide users, though a departmental survey of HR found [Y]").
   - PROPORTIONALITY: You are diagnosing a multi-billion euro enterprise. Keep narrow departmental statistics confined to a single contextualised sentence within the relevant section. Do not let a single departmental survey dominate the technology, AI Readiness, or operational narrative of a global firm. Address the contradiction factually, weight it against company-wide evidence, and move on.
9. PARAGRAPHS: Separate with \\n\\n. Required for rendering.
10. FORMAT: Prose with section headings only. Each section starts with "## Heading" on its own line followed by \\n\\n then the paragraph body. No bullet points, no other markdown, no links.
11. TEMPORAL PRECISION: Use the most recent annual figures, not quarterly snapshots presented as annual results. Do not state that a financial metric is unpublished or unavailable if the payload contains that metric for the latest annual period.
12. ORGANIZATIONAL CLAIMS: NEVER claim leadership gaps, unfilled roles, or organizational problems unless the evidence explicitly states them.
13. FORESIGHT BOUNDARY: `foresight_context_sources` and the `foresight` object are external macro context only. They may support modal pressure analysis in the Strategic Foresight section, but never a factual claim about current company deployment, conduct, compliance, capability, or intent.

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
    compact: bool = False,
    time_budget_seconds: int | None = None,
) -> tuple[str, str]:
    """
    Generate an 800-1200 word detailed diagnostic report, or a compact
    500-750 word report when compact=True and scan budget is constrained.

    When prior_summaries is provided (general_summary, ai_summary, why_what),
    the report acts as a meta-synthesis of all previously generated
    analyses rather than duplicating analysis from raw evidence. No new searches
    are performed — it works entirely from data already in memory.

    Model chain: Opus 4.8 → GPT 5.5 → Sonnet 4.6.
    Returns (report_text, model_used).
    Returns ("", "") on failure.
    """
    payload = _build_input_payload(full_data, company_name, foresight_data, prior_summaries)
    prompt = _build_prompt(payload, company_name)
    if compact:
        prompt += (
            "\n\nCOMPACT MODE: The scan is near its final budget reserve. Produce a complete but "
            "shorter Detailed Report of 500-750 words. Keep the same evidence discipline, headings, "
            "and proportionality rules. Do not omit major verified contradictions or material risks."
        )
    target_min = 450 if compact else _WORD_MIN
    usable_min = 350 if compact else 500
    token_budget = 5000 if compact else 8000
    model_timeout = max(35, min(120, int(time_budget_seconds or (85 if compact else 120))))

    # --- Attempt 1: Claude Opus 4.8 (accuracy-first primary) ---
    anthropic_key = os.environ.get("ANTHROPIC_API_KEY", "").strip()
    if anthropic_key:
        OPUS_MODEL = CLAUDE_OPUS_MODEL
        try:
            print(f"   📝 [Detailed Report] Attempting {OPUS_MODEL}...")
            t0 = time.time()
            claude_client = anthropic.Anthropic(api_key=anthropic_key, timeout=model_timeout)
            response = claude_client.messages.create(
                model=OPUS_MODEL,
                max_tokens=token_budget,
                thinking={"type": "adaptive"},
                extra_body={"output_config": {"effort": "xhigh"}},
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
            print(f"   📝 [Detailed Report] {OPUS_MODEL}: {wc} words ({elapsed}ms)")

            if text and wc >= target_min:
                return text, OPUS_MODEL
            elif text and wc >= usable_min:
                print(f"   [Detailed Report] Opus result usable ({wc} words) — below target but acceptable")
                return text, OPUS_MODEL
            else:
                print(f"   ⚠️ [Detailed Report] Opus too short ({wc} words) — falling back to GPT 5.5")
        except Exception as e:
            elapsed = int((time.time() - t0) * 1000) if 't0' in dir() else 0
            print(f"   ❌ [Detailed Report] {OPUS_MODEL} failed ({elapsed}ms): {e}")

    # --- Attempt 2: GPT 5.5 (cross-provider backup) ---
    try:
        print("   📝 [Detailed Report] Attempting gpt-5.5 fallback...")
        t0 = time.time()
        text = call_openai_text(
            prompt,
            model="gpt-5.5",
            max_output_tokens=token_budget,
            timeout=model_timeout,
        )
        text = _strip_links((text or "").strip())
        wc = _word_count(text)
        elapsed = int((time.time() - t0) * 1000)
        print(f"   📝 [Detailed Report] gpt-5.5: {wc} words ({elapsed}ms)")

        if text and wc >= target_min:
            return text, "gpt-5.5"
        if text and wc >= usable_min:
            print(f"   [Detailed Report] GPT result usable ({wc} words) — below target but acceptable")
            return text, "gpt-5.5"
        print(f"   ⚠️ [Detailed Report] GPT result too short ({wc} words) — falling back to Sonnet")
    except Exception as e:
        elapsed = int((time.time() - t0) * 1000) if 't0' in dir() else 0
        print(f"   ❌ [Detailed Report] gpt-5.5 failed ({elapsed}ms): {e}")

    if anthropic_key:
        claude_client = anthropic.Anthropic(api_key=anthropic_key, timeout=model_timeout)
        # --- Attempt 3: Claude Sonnet 4.6 ---
        SONNET_MODEL = "claude-sonnet-4-6"
        try:
            print(f"   📝 [Detailed Report] Attempting {SONNET_MODEL} fallback...")
            t0 = time.time()
            response = claude_client.messages.create(
                model=SONNET_MODEL,
                max_tokens=token_budget,
                messages=[{"role": "user", "content": prompt}],
            )
            text_parts = []
            for block in response.content:
                if block.type == "text":
                    text_parts.append(block.text)
            text = _strip_links("\n".join(text_parts).strip())
            wc = _word_count(text)
            elapsed = int((time.time() - t0) * 1000)
            print(f"   📝 [Detailed Report] {SONNET_MODEL}: {wc} words ({elapsed}ms)")

            if text and wc >= target_min:
                return text, SONNET_MODEL
            elif text and wc >= usable_min:
                print(f"   [Detailed Report] Sonnet result usable ({wc} words) — below target but acceptable")
                return text, SONNET_MODEL
            else:
                print(f"   ⚠️ [Detailed Report] Sonnet too short ({wc} words) — no usable detailed report")
        except Exception as e:
            elapsed = int((time.time() - t0) * 1000) if 't0' in dir() else 0
            print(f"   ❌ [Detailed Report] {SONNET_MODEL} failed ({elapsed}ms): {e}")

    return "", ""
