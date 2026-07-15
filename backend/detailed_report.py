"""
detailed_report.py — Deep diagnostic report generator.

Produces an 800-1200 word evidence-grounded report that expands on the
executive summary with per-dimension deep dives, stakeholder context,
and (optionally) strategic foresight. Designed to be the "full report"
that the company modal summary is the executive summary of.
"""

from __future__ import annotations

import json
import os
import re
import time

import anthropic
from llm_clients import call_openai_text
from model_policy import CLAUDE_OPUS_MODEL, CLAUDE_SONNET_MODEL, DETAILED_COMPLETION_MODEL, GPT_REASONING_MODEL
from source_quality import (
    assess_source_scope, is_generation_eligible_source,
    is_analysis_premise_source, source_analysis_support_mode,
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
_COMPACT_WORD_MIN = 500
_COMPACT_WORD_MAX = 750


def _section_headings(text: str) -> list[str]:
    return [
        match.group(1).strip()
        for match in re.finditer(r"(?m)^##\s+(.+?)\s*$", text or "")
    ]


def _ends_complete_sentence(text: str) -> bool:
    cleaned = re.sub(r"\s+", " ", str(text or "")).strip()
    if not cleaned:
        return False
    return bool(re.search(r'[\.\!\?][)"\']?$', cleaned))


def detailed_report_quality(text: str, compact: bool = False) -> dict:
    words = _word_count(text)
    headings = _section_headings(text)
    normalized = [heading.strip().lower() for heading in headings]
    required_min = _COMPACT_WORD_MIN if compact else _WORD_MIN
    missing = []
    if words < required_min:
        missing.append(f"word_count_below_{required_min}")
    if "key findings" not in normalized:
        missing.append("missing_key_findings")
    if "verdict" not in normalized:
        missing.append("missing_verdict")
    dimension_count = len([heading for heading in normalized if heading not in {"key findings", "verdict"}])
    if dimension_count < 3:
        missing.append("missing_three_dimension_sections")
    if not _ends_complete_sentence(text):
        missing.append("ends_mid_sentence")
    return {
        "complete": not missing,
        "word_count": words,
        "headings": headings,
        "dimension_section_count": dimension_count,
        "missing": missing,
        "required_min_words": required_min,
    }


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

    # Evidence packets keep routing hints separate from currently verified
    # content. Historical snippets help selection, but never prove a claim.
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
            and is_analysis_premise_source(l)
        ):
            continue
        title = (l.get("title", "Source") or "Source")[:80]
        semantic_hint = (l.get("semantic_hint") or l.get("snippet") or "")[:500]
        source_content = (
            l.get("fetched_excerpt") or l.get("normalized_content")
            or l.get("content_excerpt") or l.get("extracted_text")
            or l.get("cached_excerpt") or l.get("source_quote") or ""
        )[:2200]
        scoped_link = apply_entity_scope(dict(l), company_name)
        verified_links.append({
            "evidence_id": f"E{len(verified_links) + 1}",
            "title": title,
            "url": l.get("url", ""),
            "analysis_support_mode": source_analysis_support_mode(l),
            "can_support_hard_fact": is_generation_eligible_source(l),
            "source_context_status": l.get("source_context_status", ""),
            "claim_support_status": l.get("claim_support_status", ""),
            "source_use_status": l.get("source_use_status", ""),
            "semantic_hint": semantic_hint,
            "source_content": source_content,
            "snippet": source_content,
            "jury": l.get("category") or l.get("jury") or "",
            "support_level": l.get("support_level") or "",
            "scope": assess_source_scope(l.get("url", ""), title, source_content),
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
            "evidence_id": f"F{len(foresight_links) + 1}",
            "title": title,
            "url": l.get("url") or l.get("source_url") or "",
            "semantic_hint": snippet,
            "source_content": (l.get("source_quote") or l.get("semantic_snippet") or snippet)[:1800],
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
        and is_analysis_premise_source(item)
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
    eligible_forces = [
        f for f in (foresight_data.get("dimension_forces") or [])
        if isinstance(f, dict) and is_foresight_context_eligible_source({
            **f, "url": f.get("url") or f.get("source_url")
        })
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
Treat prior analyses as hypotheses and structural context, not factual authority. The promoted evidence
packets and structured financial payload are authoritative. Retain a prior conclusion only when current
evidence supports it.
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

FINAL SECTION — heading "Verdict" (~100-150 words)
- If washing data indicates a gap between stated ambitions and operational reality, name it explicitly
- If rescan delta is available, note persistent patterns or meaningful trajectory changes
- State the single most critical action this company should consider
- If overall evidence confidence is limited, explicitly acknowledge the constraint

MANDATORY RULES:
1. WORD COUNT: {_WORD_MIN}-{_WORD_MAX} words. Count carefully. This is non-negotiable.
2. EVIDENCE-FIRST: Every factual or diagnostic claim must be supported by a named evidence_id or structured financial field. `semantic_hint` is routing metadata only; support must come from `source_content`.
   - evidence_links with analysis_support_mode="hard_fact" may support direct factual claims.
   - evidence_links with analysis_support_mode="scoped_context" may support bounded interpretation, positioning, proof-gap analysis, service/capability context, and diagnostic premises only. Use cautious wording such as "public company material indicates" or "the source supports positioning/context"; do not treat it as independent proof of a precise outcome, deployment, metric, or performance claim.
3. PLAIN LANGUAGE: Do not use any of these terms: {forbidden}. Use descriptive business language instead.
4. NO SCORES: Do not mention score names, score numbers, or internal metrics in prose.
5. SOURCE REFERENCES: When citing evidence, use source titles naturally in text. Do NOT include URLs — those display separately.
6. FORBIDDEN ACTIONS: Do not fabricate findings, invent people problems, assume leadership gaps, or extrapolate small signals into sweeping assessments.
7. SCOPE & SOURCE QUALIFICATION — CRITICAL:
   - CLAIM TIER (INTERNAL — never write tier codes in output): Evidence items carry a "claim_tier" field for your calibration only. Choose verbs to match: company self-claims → "states"/"positions itself as"; filed accounts → "disclosed"/"announced"; independent verification → "reported"/"confirmed"; weak signals → "suggests"/"may indicate"; foresight/trend context → "could create risk/opportunity". NEVER write tier codes ([A], [B], [C], [D], [E]) or the word "tier" in the output.
   - NARROW SCOPE: When citing a finding from a narrow-scope source (master's thesis, single-department survey, regional pilot), you MUST include a scope qualifier in the same sentence. Never present a narrow-scope statistic as a company-wide truth.
   - ANTI-EXTRAPOLATION: NEVER extrapolate a narrow-scope finding into a macro-trend or section-level diagnosis.
   - SELF-REPORTED DATA: For company self-claims, qualify every assertion ("The company states...", "According to the company..."). Do not omit specific numbers when they are directly supported, but never present them as independently verified. Raw company-owned material is usable only through the scoped-context lane unless can_support_hard_fact=true.
   - ENTITY SCOPE: entity_scope="canonical_group" may support target-company claims. entity_scope="local_legal_entity", "subsidiary", "former_name", or "parent_owner" must be qualified in the same sentence and must not be promoted into unqualified company-wide evidence.
8. BALANCING CONTRADICTIONS & PROPORTIONALITY — CRITICAL:
   - If a top-level "contradictions" array is present in the payload, EACH listed contradiction MUST be addressed in the prose — cite both sides with their respective scope ("broad"/"narrow") and source titles, in the same sentence or adjacent sentences. Silently picking one side is a violation of this rule.
   - If a narrow-scope independent source contradicts a broad self-reported metric, briefly state both side-by-side (e.g., "Wärtsilä reports [X] company-wide users, though a departmental survey of HR found [Y]").
   - PROPORTIONALITY: You are diagnosing a multi-billion euro enterprise. Keep narrow departmental statistics confined to a single contextualised sentence within the relevant section. Do not let a single departmental survey dominate the technology, AI Readiness, or operational narrative of a global firm. Address the contradiction factually, weight it against company-wide evidence, and move on.
9. PARAGRAPHS: Separate with \\n\\n. Required for rendering.
10. FORMAT: Prose with section headings only. Each section starts with "## Heading" on its own line followed by \\n\\n then the paragraph body. No bullet points, no other markdown, no links.
11. TEMPORAL PRECISION: Use the most recent annual figures, not quarterly snapshots presented as annual results. Do not state that a financial metric is unpublished or unavailable if the payload contains that metric for the latest annual period.
12. ORGANIZATIONAL CLAIMS: NEVER claim leadership gaps, unfilled roles, or organizational problems unless the evidence explicitly states them.
13. FORESIGHT BOUNDARY: `foresight_context_sources` and the `foresight` object are external macro context only. Weave relevant pressure into the applicable dimension; do not create a standalone company-focused foresight-analysis section. They may never support a factual claim about current company deployment, conduct, compliance, capability, or intent.
14. FINANCIAL BOUNDARY: Financial evidence supports financial condition, capacity and investment claims only. It cannot prove architecture, operations, AI/data maturity, service delivery, strategy execution or culture. If non-financial promoted evidence is absent, state that the dimension is unresolved or omit it.
15. FORESIGHT FIT: Validate a foresight source against its macro trend claim. The connection to this company must be written separately as a cautious analytical inference ("may create pressure because..."). Do not require a macro source to mention the company, and do not present company fit as a sourced company fact.

Output only the prose text. Nothing else."""


def _build_completion_prompt(payload: dict, company_name: str, draft: str, compact: bool = False) -> str:
    target = f"{_COMPACT_WORD_MIN}-{_COMPACT_WORD_MAX}" if compact else f"{_WORD_MIN}-{_WORD_MAX}"
    return f"""You are repairing and completing a Detailed Forensic Report for {company_name}.

The previous draft is incomplete or below the required length. Produce a COMPLETE replacement report, not a continuation fragment.

STRICT OUTPUT REQUIREMENTS:
- {target} words.
- Exactly 5-7 sections.
- Required headings: ## Key Findings, at least three dimension deep-dive sections, and ## Verdict.
- Each section heading must be on its own line and start with "## ".
- End with a complete sentence.
- No bullets, no tables, no links, no URLs, no score names or score numbers.
- Do not use internal archetype or KB labels.
- Use only the structured payload as evidence. Treat the draft as reusable wording only, not as independent evidence.
- If a factual claim cannot be supported by the payload, narrow it, qualify it, or omit it.

STRUCTURED PAYLOAD:
{json.dumps(payload, ensure_ascii=False, indent=2)}

INCOMPLETE DRAFT TO REPAIR:
{draft}

Return only the completed report prose."""


def _word_count(text: str) -> int:
    return len(text.split())


def _strip_links(text: str) -> str:
    """Remove any [label](url) markdown links the model sneaks in."""
    return re.sub(r'\[([^\]]+)\]\(https?://[^)]+\)', r'\1', text)


def complete_detailed_report_draft(
    full_data: dict,
    company_name: str,
    foresight_data: dict | None,
    draft: str,
    *,
    prior_summaries: dict | None = None,
    compact: bool = False,
    time_budget_seconds: int = 80,
) -> tuple[str, str, dict]:
    """Use GPT to expand/repair a short or truncated Detailed Report draft."""
    payload = _build_input_payload(full_data, company_name, foresight_data, prior_summaries)
    prompt = _build_completion_prompt(payload, company_name, draft, compact=compact)
    timeout = max(30, min(100, int(time_budget_seconds or 80)))
    token_budget = 5000 if compact else 8000
    t0 = time.time()
    text = call_openai_text(
        prompt,
        model=DETAILED_COMPLETION_MODEL,
        max_output_tokens=token_budget,
        timeout=timeout,
    )
    text = _strip_links((text or "").strip())
    quality = detailed_report_quality(text, compact=compact)
    elapsed = int((time.time() - t0) * 1000)
    print(
        "   📝 [Detailed Report] GPT completion/repair: "
        f"{quality['word_count']} words, complete={quality['complete']}, model={DETAILED_COMPLETION_MODEL} ({elapsed}ms)"
    )
    return (text if quality["complete"] else ""), f"{DETAILED_COMPLETION_MODEL}-completion", quality


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

    Model chain: Sonnet 5 → GPT frontier → Opus premium fallback.
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
    target_min = _COMPACT_WORD_MIN if compact else _WORD_MIN
    token_budget = 5000 if compact else 8000
    started = time.time()
    total_budget = max(60, int(time_budget_seconds or (110 if compact else 180)))

    def remaining_timeout(*, reserve: int = 8, cap: int = 120, floor: int = 30) -> int:
        remaining = total_budget - int(time.time() - started) - reserve
        return max(floor, min(cap, remaining))

    def quality_ok(text: str) -> bool:
        quality = detailed_report_quality(text, compact=compact)
        if quality["complete"]:
            return True
        print(
            "   ⚠️ [Detailed Report] Completeness gate failed: "
            f"words={quality['word_count']}, missing={quality['missing']}, headings={quality['headings']}"
        )
        return False

    def complete_with_gpt(draft: str, reason: str) -> tuple[str, str]:
        if total_budget - int(time.time() - started) < 35:
            print(f"   ⏭ [Detailed Report] GPT completion skipped ({reason}); insufficient report budget")
            return "", ""
        try:
            print(f"   📝 [Detailed Report] Attempting {DETAILED_COMPLETION_MODEL} completion ({reason})...")
            t0 = time.time()
            completion_prompt = _build_completion_prompt(payload, company_name, draft, compact=compact)
            repaired = call_openai_text(
                completion_prompt,
                model=DETAILED_COMPLETION_MODEL,
                max_output_tokens=token_budget,
                timeout=remaining_timeout(reserve=5, cap=110, floor=30),
            )
            repaired = _strip_links((repaired or "").strip())
            wc = _word_count(repaired)
            elapsed = int((time.time() - t0) * 1000)
            print(f"   📝 [Detailed Report] {DETAILED_COMPLETION_MODEL} completion: {wc} words ({elapsed}ms)")
            if repaired and quality_ok(repaired):
                return repaired, f"{DETAILED_COMPLETION_MODEL}-completion"
            return "", ""
        except Exception as exc:
            elapsed = int((time.time() - t0) * 1000) if 't0' in dir() else 0
            print(f"   ❌ [Detailed Report] {DETAILED_COMPLETION_MODEL} completion failed ({elapsed}ms): {exc}")
            return "", ""

    anthropic_key = os.environ.get("ANTHROPIC_API_KEY", "").strip()
    if anthropic_key:
        # --- Attempt 1: Claude Sonnet 5 (standard long-form writer) ---
        SONNET_MODEL = CLAUDE_SONNET_MODEL
        try:
            print(f"   📝 [Detailed Report] Attempting {SONNET_MODEL}...")
            t0 = time.time()
            claude_client = anthropic.Anthropic(api_key=anthropic_key, timeout=remaining_timeout(reserve=20 if not compact else 10))
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

            if text and wc >= target_min and quality_ok(text):
                return text, SONNET_MODEL
            if text:
                repaired, repair_model = complete_with_gpt(text, f"{SONNET_MODEL}_incomplete_or_short")
                if repaired:
                    return repaired, f"{SONNET_MODEL}+{repair_model}"
            print(f"   ⚠️ [Detailed Report] Sonnet unusable ({wc} words) — falling back to {GPT_REASONING_MODEL}")
        except Exception as e:
            elapsed = int((time.time() - t0) * 1000) if 't0' in dir() else 0
            print(f"   ❌ [Detailed Report] {SONNET_MODEL} failed ({elapsed}ms): {e}")

    # --- Attempt 2: GPT frontier model (cross-provider backup) ---
    try:
        print(f"   📝 [Detailed Report] Attempting {GPT_REASONING_MODEL} fallback...")
        t0 = time.time()
        text = call_openai_text(
            prompt,
            model=GPT_REASONING_MODEL,
            max_output_tokens=token_budget,
            timeout=remaining_timeout(reserve=20 if not compact else 10, cap=110, floor=30),
        )
        text = _strip_links((text or "").strip())
        wc = _word_count(text)
        elapsed = int((time.time() - t0) * 1000)
        print(f"   📝 [Detailed Report] {GPT_REASONING_MODEL}: {wc} words ({elapsed}ms)")

        if text and wc >= target_min and quality_ok(text):
            return text, GPT_REASONING_MODEL
        if text:
            repaired, repair_model = complete_with_gpt(text, f"{GPT_REASONING_MODEL}_incomplete_or_short")
            if repaired:
                return repaired, f"{GPT_REASONING_MODEL}+{repair_model}"
        print(f"   ⚠️ [Detailed Report] GPT result too short ({wc} words) — falling back to Opus premium")
    except Exception as e:
        elapsed = int((time.time() - t0) * 1000) if 't0' in dir() else 0
        print(f"   ❌ [Detailed Report] {GPT_REASONING_MODEL} failed ({elapsed}ms): {e}")

    if anthropic_key:
        # --- Attempt 3: Claude Opus 4.8 premium fallback ---
        OPUS_MODEL = CLAUDE_OPUS_MODEL
        try:
            print(f"   📝 [Detailed Report] Attempting {OPUS_MODEL} premium fallback...")
            t0 = time.time()
            claude_client = anthropic.Anthropic(api_key=anthropic_key, timeout=remaining_timeout(reserve=5, cap=90, floor=30))
            response = claude_client.messages.create(
                model=OPUS_MODEL,
                max_tokens=token_budget,
                thinking={"type": "adaptive"},
                extra_body={"output_config": {"effort": "high"}},
                messages=[{"role": "user", "content": prompt}],
            )
            text_parts = []
            for block in response.content:
                if block.type == "text":
                    text_parts.append(block.text)
            text = _strip_links("\n".join(text_parts).strip())
            wc = _word_count(text)
            elapsed = int((time.time() - t0) * 1000)
            print(f"   📝 [Detailed Report] {OPUS_MODEL}: {wc} words ({elapsed}ms)")

            if text and wc >= target_min and quality_ok(text):
                return text, OPUS_MODEL
            if text:
                repaired, repair_model = complete_with_gpt(text, f"{OPUS_MODEL}_incomplete_or_short")
                if repaired:
                    return repaired, f"{OPUS_MODEL}+{repair_model}"
            print(f"   ⚠️ [Detailed Report] Opus unusable ({wc} words) — no usable detailed report")
        except Exception as e:
            elapsed = int((time.time() - t0) * 1000) if 't0' in dir() else 0
            print(f"   ❌ [Detailed Report] {OPUS_MODEL} failed ({elapsed}ms): {e}")

    return "", ""
