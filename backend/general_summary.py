"""
general_summary.py — Plain-language General Summary generator + payload fact-check.

Assembles a 225-275 word narrative from already-computed structured scan data
(jury findings, financial findings, delta). No inline links are included —
the General Summary is plain prose only; all evidence links are already shown
in the AI Diagnosis section of the UI.

After generation, fact_check_general_summary() verifies every claim in the
output traces back to the input payload (catches training-data hallucinations).
If unsupported claims are found, the summary is re-generated with explicit
warnings about the false claims.
"""

import json
import os
import re
import time

from google import genai
from gemini_client import get_gemini_client
from google.genai import types as genai_types
from gemini_backoff import call_with_503_backoff
from source_quality import assess_source_scope
from contradiction_detector import detect_contradictions


def _extract_json(text: str) -> dict | None:
    """Extract the first valid JSON object from text, ignoring surrounding content.

    Handles common LLM output issues:
    - JSON wrapped in markdown code blocks
    - Explanatory text before or after the JSON
    - Thinking/reasoning content mixed with output
    - Multiple JSON objects (takes the first complete one)
    """
    if not text or not text.strip():
        return None

    # Strip markdown code blocks first
    cleaned = text.strip()
    if cleaned.startswith("```"):
        cleaned = cleaned.split("\n", 1)[-1].rsplit("```", 1)[0].strip()

    # Try direct parse first (fastest path)
    try:
        return json.loads(cleaned)
    except (json.JSONDecodeError, ValueError):
        pass

    # Find the first { and try to parse progressively larger substrings
    start = cleaned.find("{")
    if start == -1:
        return None

    # Walk from the end backwards to find the matching closing brace
    for end in range(len(cleaned), start, -1):
        if cleaned[end - 1] == "}":
            try:
                return json.loads(cleaned[start:end])
            except (json.JSONDecodeError, ValueError):
                continue

    return None


_FORBIDDEN_TERMS = [
    "Thriving", "Modernizing", "Cash Cow", "At Risk",
    "Digital Taylorism", "Feature Factory", "Command and Control",
    "Zombie Scrum", "Fake Agile", "Modernization Washing", "AI Washing",
    "Agile Washing", "Platform Engineering", "Flow Efficiency",
    "Psychological Safety", "Value Streams", "Data Mesh",
    "Knowledge Base", "KB dimension", "archetype",
]

_WORD_MIN = 225
_WORD_MAX = 275


def _build_input_payload(full_data: dict, company_name: str) -> dict:
    """Extract only the structured fields the model is allowed to reference."""

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

    # Legal/ToS URL patterns — these pages contain conditional/hypothetical
    # language that models consistently misread as factual past events.
    # Exclude them entirely from the evidence payload.
    _LEGAL_PATH_SEGMENTS = (
        "terms", "legal", "disclaimer", "privacy", "cookie", "tos", "eula",
        "tietosuoja", "kayttoehdot", "ehdot", "yksityisyys", "rekisteriseloste",
        "gdpr", "oikeudellinen", "vastuuvapaus",
    )

    def _is_legal_page(url: str) -> bool:
        url_lower = url.lower()
        return any(seg in url_lower for seg in _LEGAL_PATH_SEGMENTS)

    # Evidence snippets — titles and snippets only, NO URLs, NO legal pages.
    # Providing URLs caused the model to hallucinate wrong links in the output.
    # Legal/ToS pages are excluded because they contain conditional language
    # ("if the service is down, access is blocked") that models misread as
    # factual historical events.
    # All verified links are already shown in the AI Diagnosis section of the UI.
    #
    # Recency-first selection: recent sources (< 12 months) get priority.
    # Old sources only fill remaining slots if fewer than 18 recent ones exist.
    # This prevents outdated evidence (e.g. 2016 Cortana Analytics) from being
    # treated as current capabilities in the summary.
    #
    # Tiered snippet depth: top 8 sources get 500 chars (deep context for key
    # evidence), remaining get 350 chars (sufficient context without dilution).
    _all_verified = [
        l for l in full_data.get("evidence_links", [])
        if l.get("verified") is True and l.get("url") and not _is_legal_page(l.get("url", ""))
        and not l.get("wrong_entity")  # Exclude sources about a different company with same name
    ]
    _recent = [l for l in _all_verified if l.get("is_recent") is True]
    _older = [l for l in _all_verified if l.get("is_recent") is not True]
    # Fill with recent first; only pad with older if recent coverage is thin (<12)
    _all_evidence = _recent[:18]
    if len(_recent) < 12:
        remaining_slots = 18 - len(_all_evidence)
        if remaining_slots > 0 and _older:
            _all_evidence.extend(_older[:remaining_slots])

    # Each evidence item carries a "scope" flag derived from the source URL,
    # title, and snippet. "narrow" means the source describes a limited
    # population (master's thesis, single-department survey, pilot study). The
    # prompt's SCOPE QUALIFICATION rule keys off this flag so the model adds
    # qualifiers like "in a survey of HR personnel" instead of presenting
    # departmental findings as company-wide truths.
    verified_links = []
    for i, l in enumerate(_all_evidence):
        snippet_limit = 500 if i < 8 else 350
        title = (l.get("title", "Source") or "Source")[:80]
        snippet = (l.get("snippet") or "")[:snippet_limit]
        verified_links.append({
            "title": title,
            "snippet": snippet,
            "is_recent": l.get("is_recent", False),
            "scope": assess_source_scope(l.get("url", ""), title, snippet),
        })

    # Add validated foresight sources to the evidence pool (max 3, validated only)
    # Foresight sources always get deep snippets (500 chars) — forward-looking signals.
    foresight_sources = []
    for l in full_data.get("foresight_links", []):
        if not (l.get("verified") is True and l.get("validated") is True):
            continue
        title = (l.get("title", "Foresight") or "Foresight")[:80]
        snippet = (l.get("snippet") or "")[:500]
        foresight_sources.append({
            "title": title,
            "snippet": snippet,
            "is_recent": True,
            "scope": assess_source_scope(l.get("url", ""), title, snippet),
        })
        if len(foresight_sources) >= 3:
            break
    verified_links = verified_links + foresight_sources

    # Key jury findings — descriptions only, no URLs, no legal pages.
    key_findings = [
        {
            "description": item.get("description") or item.get("category", ""),
            "title": ((item.get("title") or item.get("category", "")) or "")[:80],
            "sentiment": item.get("sentiment", "neutral"),
        }
        for item in full_data.get("reality_checklist", [])
        if item.get("found") and item.get("url") and not _is_legal_page(item.get("url", ""))
    ][:12]

    # Financial findings — structured with all dimensions.
    # Preserves assessment + evidence_snippet as separate fields so the model
    # can distinguish the analyst's interpretation from the raw data point.
    # Excludes source_url (causes hallucinated links) but keeps source_title
    # for attribution context.
    raw_findings = full_data.get("financial_findings") or []
    fin_findings = []
    for f in raw_findings[:4]:
        if isinstance(f, dict):
            entry = {
                "dimension": f.get("dimension", ""),
                "assessment": f.get("assessment", ""),
            }
            snippet = f.get("evidence_snippet", "")
            if snippet:
                entry["evidence_snippet"] = snippet
            source_title = f.get("source_title", "")
            if source_title:
                entry["source_title"] = source_title
            fin_findings.append(entry)
        else:
            # Legacy fallback: plain string
            fin_findings.append(str(f))

    # Key people (max 4)
    people = [
        p.get("name", "") for p in (full_data.get("key_people") or [])[:4]
        if p.get("name")
    ]

    # Only include scores the model genuinely needs for context.
    # intent_score, confidence_score, and financial_sub_scores are intentionally
    # excluded: when present as JSON fields the model treats them as "facts to
    # report" and dumps them as a score list instead of writing a narrative.
    # financial_index is kept (single number) as context for financial findings.
    # reality_score is kept only when delta is present so the trend sentence
    # has a concrete number to anchor to.
    # Detect quantitative disagreements between evidence items (e.g. one
    # source says "9% of HR personnel use Tool X", another says "thousands
    # of weekly users"). When present, the prompt instructs the model to
    # cite both sides with their scope rather than silently picking one.
    # Failure-safe: returns [] if Gemini is unavailable.
    contradictions = detect_contradictions(verified_links, company_name)

    result: dict = {
        "company_name": company_name,
        "delta": delta,
        "financial_index": full_data.get("financial_index"),
        "key_findings": key_findings,
        "evidence_links": verified_links,
        "financial_findings": fin_findings,
        "key_people": people,
    }
    if contradictions:
        result["contradictions"] = contradictions
    if delta:
        result["reality_score"] = full_data.get("reality_score", 0)
    return result


def _build_prompt(payload: dict, company_name: str) -> str:
    forbidden = ", ".join(_FORBIDDEN_TERMS)
    return f"""You are writing the General Summary section of a forensic B2B intelligence report about {company_name}. Your role in this section is evidence reporter — not analyst, not advisor. You synthesise verified facts into readable prose, but you do not speculate, project, or editorialize.

This section differs from the AI Diagnosis section: the Diagnosis interprets and projects; this Summary strictly reports what the evidence shows.

EVIDENCE — the ONLY material you may reference. Every sentence you write must trace back to a specific item below. Do not supplement with outside knowledge, training data, or general industry assumptions:
{json.dumps(payload, ensure_ascii=False, indent=2)}

WRITING APPROACH:
Weave the evidence into a coherent narrative that gives a C-level reader an immediate, factual understanding of where this company stands today. Connect related findings to reveal patterns — do not list them. Stay in the present and past tense: report what has happened and what currently exists. Do not project future outcomes, consequences, or trajectories — that belongs in the AI Diagnosis section.

MANDATORY RULES:

1. WORD COUNT: The output MUST be between {_WORD_MIN} and {_WORD_MAX} words. Count carefully. This is non-negotiable.

2. FACTUAL DISCIPLINE — CRITICAL:
   - EVIDENCE-ONLY: Every factual claim must be directly traceable to a specific item in the EVIDENCE above. If you cannot point to the specific evidence item that supports a statement, do not write it. Never supplement with knowledge from outside the provided data.
   - Only write about things that actually happened or currently exist. Do not present hypothetical, conditional, or future-tense scenarios as facts.
   - Terms of Service, legal disclaimers, and privacy policies describe hypothetical situations ("if X happens, Y occurs") — these are NOT evidence of real events. If a snippet sounds like a legal clause, ignore it entirely.
   - Do not infer events from the absence of evidence. If something is not clearly documented as having occurred, do not write that it occurred.
   - Never invent, combine, or rename financial metrics.
   - CRITICAL: When citing year-over-year financial changes, distinguish between the BASE figure (previous period's actual value) and the CHANGE/DELTA (the difference). Example: if a source says "profit decreased by €153.7M" that means €153.7M is the CHANGE, NOT the previous profit. Always verify: does the number represent the actual metric or the year-over-year movement?
   - SOURCE RECENCY — CRITICAL: Each evidence source has an "is_recent" field. Sources with is_recent=false are OLDER than 12 months. You MUST apply these rules:
     (a) STRONGLY PREFER sources with is_recent=true. These reflect the company's current state.
     (b) NEVER present information from is_recent=false sources as current capabilities, current strategy, or current technology stack. Technologies and platforms mentioned in old sources may have been replaced or discontinued.
     (c) If you must reference an older source, explicitly qualify it as historical (e.g., "previously used," "historically relied on"). But prefer to OMIT old sources entirely if sufficient recent evidence exists.
     (d) If a snippet mentions a technology, product, or platform that was clearly from a different era (e.g., discontinued products, legacy platforms), do NOT include it regardless of is_recent value.

3. DATA HANDLING / META-METRICS: Do not reference the report's internal scoring metrics (e.g., "Reality Score," "Ambition Score," "Friction Index," "Integrity Delta") by name in the generated prose. These scores should inform your overall analytical tone, but the summary itself must focus strictly on the company's real-world financial, operational, and organizational data.

4. FORBIDDEN TERMS: Do not use any of these: {forbidden}. Use plain descriptive language instead.

5. NEUTRAL LANGUAGE — CRITICAL:
   - Do not use dramatic, editorial, or emotionally charged modifiers. Banned patterns include but are not limited to: "widening," "aggressively," "severe," "threatening," "forcing," "critical juncture," "operational friction," "at a crossroads."
   - Do not use "will," "threatens," "risks," or "forces" to describe future outcomes. Report only what has happened or is currently documented.
   - Use precise, neutral language. Prefer concrete descriptions over characterisations. Instead of "aggressively pivoting toward AI," write "has established a dedicated AI business unit and deployed Copilot to N users."

6. OPENING SENTENCE — MANDATORY:
   - If delta data is present: open with the most significant concrete change from the delta (e.g. "Since the previous assessment in [date], [company]'s [specific metric] has [moved from X to Y]").
   - If no delta: open with the single most significant observed fact from key_findings or evidence_links.
   - Never open with a number. Never open with a narrative characterisation — anchor on a specific, verifiable data point.

7. COVERAGE: Address at least 3 distinct dimensions if the evidence supports them — for example: financial health, operational or technology signals, people and leadership. Do not write the entire summary about a single event. If the evidence does not support a dimension, omit it rather than filling the gap with unsupported claims.

8. SCOPE & SOURCE QUALIFICATION — CRITICAL: 
   - NARROW SCOPE: Each item in evidence_links carries a "scope" field. When citing a finding from a "narrow" source, you MUST include a scope qualifier (e.g., "in a survey of HR personnel"). 
   - ANTI-EXTRAPOLATION: NEVER extrapolate a narrow-scope finding into a macro-trend. For example, if a survey only covers the HR department, you are forbidden from writing sweeping claims like "enterprise adoption lags," "the company struggles with AI," or "internal usage is low." State the narrow fact exactly as it is, without macro-commentary.
   - SELF-REPORTED DATA: If evidence comes from the company's own representatives, you must ingest and report these concrete metrics, but qualify them (e.g., "The company reports..."). Do not treat them as verified independent facts, but DO NOT omit them.

9. BALANCING CONTRADICTIONS & PROPORTIONALITY — CRITICAL: 
   - If a narrow-scope independent source contradicts a broad self-reported metric, briefly state both side-by-side (e.g., "Wärtsilä reports [X] company-wide users, though a departmental survey of HR found [Y]").
   - PROPORTIONALITY: You are summarizing a multi-billion euro enterprise. Keep narrow departmental statistics extremely brief. Do not let a single departmental survey dominate the technology or operational narrative of a global firm. Address the contradiction in a single, strictly factual sentence and move on.

10. RISK REPORTING: If the evidence documents negative trends or concrete vulnerabilities, report them factually. If the data reflects standard business practices (e.g., strategic outsourcing, white-labeling), do not manufacture artificial crises. Do not speculate about consequences — state the documented fact and let the reader draw conclusions.

11. PLAIN BUSINESS ENGLISH: No superlatives. No marketing language. No hedging phrases like "it appears that" or "it seems". State findings directly.

12. PARAGRAPHS: Structure the output as exactly 3-4 paragraphs, each covering a distinct dimension. Separate paragraphs using standard newline characters (\\n\\n). This is required for readability.

13. FORMAT: Plain prose only. No headers, no bullet points, no markdown, no links, no citations. CRITICAL: Do NOT use HTML tags like <br> to format line breaks.

Output only the prose text. Nothing else."""


def _word_count(text: str) -> int:
    return len(text.split())


def _strip_links(text: str) -> str:
    """Remove any [label](url) markdown links the model sneaks in despite instructions.
    Keeps the label text so readability is preserved; only the URL syntax is dropped."""
    return re.sub(r'\[([^\]]+)\]\(https?://[^)]+\)', r'\1', text)


def generate_general_summary(full_data: dict, company_name: str) -> tuple[str, str]:
    """
    Generate a 225-275 word General Summary from structured scan data.

    Returns (summary_text, model_used).
    Returns ("", "") on failure — caller should catch exceptions separately.
    """
    try:
        client = get_gemini_client()
    except ValueError:
        print("   ⚠️ [General Summary] No GEMINI_API_KEY — skipping")
        return "", ""

    payload = _build_input_payload(full_data, company_name)
    prompt = _build_prompt(payload, company_name)

    # Primary model: gemini-3.1-pro-preview (2 attempts)
    # gemini-3-pro-preview removed: shares 3.x rate limits with 3.1-pro (useless
    # as fallback during overload) and is being deprecated by Google.
    PRIMARY_MODEL = "gemini-3.1-pro-preview"

    FIRST_CONFIG = genai_types.GenerateContentConfig(
        temperature=1.0,
        max_output_tokens=6000,
        thinking_config=genai_types.ThinkingConfig(thinking_budget=2048),
    )
    RETRY_CONFIG = genai_types.GenerateContentConfig(
        temperature=1.0,
        max_output_tokens=6000,
        thinking_config=genai_types.ThinkingConfig(thinking_budget=512),
    )

    # Attempts 1 & 2: primary model
    for attempt, config in enumerate([FIRST_CONFIG, RETRY_CONFIG], start=1):
        try:
            t0 = time.time()
            response = call_with_503_backoff(
                lambda: client.models.generate_content(
                    model=PRIMARY_MODEL,
                    contents=prompt,
                    config=config,
                ),
                label=f"General Summary {PRIMARY_MODEL}",
                model=PRIMARY_MODEL,
                timeout=90,
            )
            text = _strip_links((response.text or "").strip())
            wc = _word_count(text)
            elapsed = int((time.time() - t0) * 1000)
            print(f"   [General Summary] {PRIMARY_MODEL} attempt {attempt}: {wc} words ({elapsed}ms)")

            if _WORD_MIN <= wc <= _WORD_MAX:
                return text, PRIMARY_MODEL

            if attempt == 1:
                print(f"   [General Summary] {wc} words outside [{_WORD_MIN}-{_WORD_MAX}] — retrying")
                continue

            # Attempt 2 result: use if non-empty (best-effort over silence),
            # but still fall through to backup if completely empty.
            if text:
                print(f"   [General Summary] Using attempt 2 result ({wc} words) — outside range but non-empty")
                return text, PRIMARY_MODEL

        except Exception as e:
            print(f"   ❌ [General Summary] {PRIMARY_MODEL} attempt {attempt} failed: {e}")
            if attempt == 1:
                continue

    # Attempt 3: Gemini 2.5 Pro backup (different model — survives primary model outages/quota)
    try:
        BACKUP_MODEL = "gemini-2.5-pro"
        print(f"   [General Summary] Primary model failed — trying backup {BACKUP_MODEL}")
        t0 = time.time()
        backup_config = genai_types.GenerateContentConfig(
            temperature=0.1,
            max_output_tokens=4000,
        )
        backup_resp = call_with_503_backoff(
            lambda: client.models.generate_content(
                model=BACKUP_MODEL,
                contents=prompt,
                config=backup_config,
            ),
            label=f"General Summary backup {BACKUP_MODEL}",
            model=BACKUP_MODEL,
            timeout=90,
        )
        text = _strip_links((backup_resp.text or "").strip())
        wc = _word_count(text)
        elapsed = int((time.time() - t0) * 1000)
        print(f"   [General Summary] {BACKUP_MODEL} backup: {wc} words ({elapsed}ms)")
        if text:
            return text, BACKUP_MODEL
    except Exception as e:
        print(f"   ❌ [General Summary] {BACKUP_MODEL} backup failed: {e}")

    # Attempt 4: Sonnet 4.6 (survives total Gemini outages)
    try:
        import anthropic
        anthropic_key = os.environ.get("ANTHROPIC_API_KEY")
        if not anthropic_key:
            print("   ⚠️ [General Summary] No ANTHROPIC_API_KEY — skipping Sonnet backup")
        else:
            SONNET_MODEL = "claude-sonnet-4-6"
            print(f"   [General Summary] Gemini models exhausted — trying {SONNET_MODEL}")
            t0 = time.time()
            claude_client = anthropic.Anthropic(api_key=anthropic_key)
            claude_resp = claude_client.messages.create(
                model=SONNET_MODEL,
                max_tokens=4000,
                messages=[{"role": "user", "content": prompt}],
            )
            text = _strip_links((claude_resp.content[0].text or "").strip())
            wc = _word_count(text)
            elapsed = int((time.time() - t0) * 1000)
            print(f"   [General Summary] {SONNET_MODEL} backup: {wc} words ({elapsed}ms)")
            if text:
                return text, SONNET_MODEL
    except Exception as e:
        print(f"   ❌ [General Summary] Sonnet backup failed: {e}")

    return "", ""


# ── Payload-based fact-check for General Summary ─────────────────────────────
# Unlike the AI Diagnosis fact-check (which uses Google Search grounding),
# this checks the General Summary against its OWN input payload. The Summary
# should contain ONLY claims traceable to the payload — anything else is
# hallucination or training-data leakage.

def _build_fact_check_prompt(summary_text: str, payload: dict, company_name: str) -> str:
    return f"""You are a precision fact-checker. Your job is to verify that EVERY factual claim in the General Summary below is directly supported by the provided evidence payload AND that narrow-scope findings are not silently elevated into company-wide claims. The Summary should contain ONLY information from the payload — nothing else.

COMPANY: {company_name}

GENERAL SUMMARY TO VERIFY:
{summary_text}

EVIDENCE PAYLOAD (the only data the summary was allowed to use):
{json.dumps(payload, ensure_ascii=False, indent=2)}

INSTRUCTIONS:
1. Extract every factual claim from the summary: numbers, dates, company names, revenue figures, percentages, product names, people names, strategic initiatives, regulatory references, market statistics.
2. For each claim, check if it can be traced to a specific item in the evidence payload above.
3. Flag a claim as UNSUPPORTED if:
   A. It states a specific fact (metric, event, figure, statistic) that does NOT appear anywhere in the payload.
   B. It significantly exaggerates or distorts something from the payload.
   C. It introduces market statistics, industry benchmarks, or regulatory details not present in the payload — these are likely hallucinated from the model's training data.
4. SCOPE INFLATION CHECK — Each evidence_links item has a "scope" field ("broad" or "narrow"). For each numeric or scale claim in the summary, identify which evidence item it traces back to. Flag the claim as UNSUPPORTED if:
   D. The claim is derived from a "narrow"-scope source AND is presented as a company-wide fact without a scope qualifier in the same sentence (qualifier examples: "in a survey of HR personnel", "in a 100-respondent study", "in a Vaasa-based pilot", "according to a master's thesis"). For example, "9% of personnel use Tool X" lifted from a narrow HR-only thesis without saying "in HR" is scope inflation.
   E. The payload has a top-level "contradictions" array, the summary cites one side but does NOT acknowledge the other side at all. Both sides of a listed contradiction must appear in the prose.
5. Do NOT flag:
   - Reasonable connecting language between evidence points (e.g., "meanwhile," "in parallel")
   - Direct restatements of payload data, even if paraphrased
   - The opening trend sentence if it accurately reflects the delta data
   - Claims from narrow-scope sources THAT ARE properly qualified in the same sentence

OUTPUT FORMAT — valid JSON only:

If all claims are supported:
{{"unsupported_claims": [], "verified": true}}

If unsupported claims found:
{{"unsupported_claims": [{{"claim": "The exact quote or close paraphrase from the summary", "reason": "Brief explanation of why this is not in the payload OR which scope-inflation rule (4D / 4E) it violates"}}], "verified": false}}"""


def _build_regen_prompt(payload: dict, company_name: str, original_text: str, false_claims: list) -> str:
    """Build a re-generation prompt that incorporates the false claims as explicit warnings."""
    claims_list = "\n".join(f"- {fc['claim']}: {fc['reason']}" for fc in false_claims)
    base_prompt = _build_prompt(payload, company_name)
    return f"""{base_prompt}

CRITICAL CORRECTION — THE PREVIOUS ATTEMPT CONTAINED THESE UNSUPPORTED CLAIMS:
{claims_list}

The previous attempt:
{original_text}

You MUST NOT include any of these claims or similar unsupported information. Write a completely new summary using ONLY the evidence payload. Every sentence must trace to a specific payload item."""


def fact_check_general_summary(
    summary_text: str,
    full_data: dict,
    company_name: str,
) -> dict:
    """
    Verify the General Summary against its own input payload.

    Stage 1: Gemini 2.5 Pro detects unsupported claims.
    Stage 2: If false claims found, re-generates with Gemini 3.1 Pro.

    Returns:
        dict with:
            - verified: bool
            - false_claims: list of {claim, reason} dicts
            - corrected_summary: str (re-generated summary if claims found, else "")
            - corrected_model: str (model used for re-generation, else "")
            - check_time_ms: int
    """
    if not summary_text:
        return {"verified": True, "false_claims": [], "corrected_summary": "",
                "corrected_model": "", "check_time_ms": 0}

    try:
        client = get_gemini_client()
    except ValueError:
        print("   ⚠️ [GS Fact-Check] No GEMINI_API_KEY — skipping")
        return {"verified": True, "false_claims": [], "corrected_summary": "",
                "corrected_model": "", "check_time_ms": 0}

    payload = _build_input_payload(full_data, company_name)
    check_prompt = _build_fact_check_prompt(summary_text, payload, company_name)

    t0 = time.time()

    # Stage 1: Detect unsupported claims
    # Primary: Gemini 2.5 Pro | Backup 1: Gemini 3.1 Pro | Backup 2: Sonnet 4.6
    CHECK_MODELS = [
        ("gemini-2.5-pro", "gemini"),
        ("gemini-3.1-pro-preview", "gemini"),
        ("claude-sonnet-4-6", "anthropic"),
    ]
    false_claims = []
    detection_succeeded = False

    for model_id, provider in CHECK_MODELS:
        if detection_succeeded:
            break

        try:
            print(f"   🔍 [GS Fact-Check] {model_id} checking claims against payload...")

            if provider == "gemini":
                resp = call_with_503_backoff(
                    lambda m=model_id: client.models.generate_content(
                        model=m,
                        contents=check_prompt,
                        config=genai_types.GenerateContentConfig(
                            temperature=0.0,
                            max_output_tokens=2000,
                        ),
                    ),
                    label="GS Fact-Check",
                    model=model_id,
                    timeout=60,
                )
                # Extract text robustly — thinking models (2.5 Pro) return
                # thinking parts that make resp.text empty or unreliable.
                raw = ""
                try:
                    raw = (resp.text or "").strip()
                except Exception:
                    pass
                if not raw:
                    candidates = getattr(resp, 'candidates', None)
                    if candidates:
                        content = getattr(candidates[0], 'content', None)
                        parts = getattr(content, 'parts', None) if content else None
                        if parts:
                            raw = "".join(
                                p.text for p in parts
                                if p and getattr(p, 'text', None) and not getattr(p, 'thought', False)
                            ).strip()

            elif provider == "anthropic":
                import anthropic
                anthropic_key = os.environ.get("ANTHROPIC_API_KEY")
                if not anthropic_key:
                    print(f"   ⚠️ [GS Fact-Check] No ANTHROPIC_API_KEY — skipping {model_id}")
                    continue
                claude_client = anthropic.Anthropic(api_key=anthropic_key)
                claude_resp = claude_client.messages.create(
                    model=model_id,
                    max_tokens=2000,
                    messages=[{"role": "user", "content": check_prompt}],
                )
                raw = (claude_resp.content[0].text or "").strip()

            if raw.startswith("```"):
                raw = raw.split("\n", 1)[-1].rsplit("```", 1)[0].strip()

            # Robust JSON extraction — models often wrap JSON in markdown,
            # add explanations before/after, or return thinking + JSON.
            # Find the outermost JSON object regardless of surrounding text.
            result = _extract_json(raw)
            if result is None:
                raise ValueError(f"No valid JSON object found in response ({len(raw)} chars)")
            false_claims = result.get("unsupported_claims", [])
            detection_succeeded = True

        except Exception as e:
            remaining = [m for m, _ in CHECK_MODELS if m != model_id and not detection_succeeded]
            next_info = f" — trying {remaining[0]}" if remaining else ""
            print(f"   ⚠️ [GS Fact-Check] {model_id} failed: {e}{next_info}")

    if not detection_succeeded:
        elapsed = int((time.time() - t0) * 1000)
        print(f"   ⚠️ [GS Fact-Check] All detection models failed — treating as unverified ({elapsed}ms)")
        return {"verified": False, "false_claims": [], "corrected_summary": "",
                "corrected_model": "", "check_time_ms": elapsed,
                "fact_check_skipped": True}

    if not false_claims:
        elapsed = int((time.time() - t0) * 1000)
        print(f"   ✅ [GS Fact-Check] All claims supported by payload ({elapsed}ms)")
        return {"verified": True, "false_claims": [], "corrected_summary": "",
                "corrected_model": "", "check_time_ms": elapsed}

    # Log the false claims
    print(f"   ⚠️ [GS Fact-Check] {len(false_claims)} unsupported claim(s) detected:")
    for fc in false_claims:
        print(f"      - {fc.get('claim', '?')[:100]}")
        print(f"        Reason: {fc.get('reason', '?')[:150]}")

    # Stage 2: Re-generate with false claims as explicit warnings
    regen_prompt = _build_regen_prompt(payload, company_name, summary_text, false_claims)

    REGEN_MODEL = "gemini-3.1-pro-preview"
    corrected_summary = ""
    corrected_model = ""

    try:
        regen_resp = call_with_503_backoff(
            lambda: client.models.generate_content(
                model=REGEN_MODEL,
                contents=regen_prompt,
                config=genai_types.GenerateContentConfig(
                    temperature=1.0,
                    max_output_tokens=6000,
                    thinking_config=genai_types.ThinkingConfig(thinking_budget=2048),
                ),
            ),
            label="GS Re-generation",
            model=REGEN_MODEL,
            timeout=90,
        )
        corrected_summary = _strip_links((regen_resp.text or "").strip())
        corrected_model = REGEN_MODEL
        wc = _word_count(corrected_summary)
        print(f"   ✅ [GS Fact-Check] Re-generated clean summary: {wc} words ({REGEN_MODEL})")

    except Exception as e:
        print(f"   ❌ [GS Fact-Check] Re-generation failed: {e} — keeping original (flagged)")

    elapsed = int((time.time() - t0) * 1000)
    return {
        "verified": False,
        "false_claims": false_claims,
        "corrected_summary": corrected_summary,
        "corrected_model": corrected_model,
        "check_time_ms": elapsed,
    }
