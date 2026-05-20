"""
general_summary.py — Plain-language General Summary generator + payload fact-check.

Assembles a 250-300 word narrative from already-computed structured scan data
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
from source_quality import (
    assess_source_scope, infer_source_type, compute_claim_tier, describe_source,
    is_boilerplate_snippet, is_title_echo, tier_snippet_bounds,
    is_generation_eligible_source,
)
from contradiction_detector import detect_contradictions
from prompts import PROMPT_VERSION, build_shared_guardrails
from entity_identity import apply_entity_scope, get_company_identity


def _general_summary_guardrails() -> str:
    """Shared fact-discipline guardrails for plain General Summary mode."""
    return build_shared_guardrails(include_diagnostic=False, include_foresight=True, include_claim_tier=True)


def _clean_env_var(value: str) -> str:
    if not value:
        return ""
    if "=" in value:
        try:
            value = value.split("=", 1)[1]
        except Exception:
            pass
    return value.strip().strip('"').strip("'")


def _get_openai_text(response) -> str:
    """Extract text from OpenAI Responses API output shapes."""
    text = getattr(response, "output_text", None)
    if text:
        return text.strip()

    parts = []
    for item in getattr(response, "output", []) or []:
        for content in getattr(item, "content", []) or []:
            content_text = getattr(content, "text", None)
            if content_text:
                parts.append(content_text)
    return "\n".join(parts).strip()


def _call_openai_summary(prompt: str, model: str = "gpt-5.5", timeout: int = 90) -> str:
    api_key = _clean_env_var(os.environ.get("GPT_API_KEY") or os.environ.get("OPENAI_API_KEY") or "")
    if not api_key:
        raise ValueError("No GPT_API_KEY configured")

    from openai import OpenAI
    client = OpenAI(api_key=api_key, timeout=timeout)
    response = client.responses.create(
        model=model,
        input=prompt,
        max_output_tokens=6000,
    )
    return _get_openai_text(response)




def _dict_items_only(items, label: str = "items") -> list:
    """Return only dict items before General Summary code calls .get()."""
    if not isinstance(items, list):
        return []
    clean = []
    dropped = 0
    for item in items:
        if isinstance(item, dict):
            clean.append(item)
        else:
            dropped += 1
    if dropped:
        print(f"   ⚠️ [General Summary Hygiene] Dropped {dropped} non-dict item(s) from {label}")
    return clean


_GS_DROPPED = object()  # sentinel: callable was removed, distinct from legitimate None

def _sanitize_general_summary_input(obj, label: str = "full_data"):
    """Remove callable/function objects before building/fact-checking payloads."""
    SOURCE_LIST_FIELDS = {
        "evidence_links", "foresight_links", "foresight_evidence", "source_cards",
        "deep_dive_findings", "dimension_forces", "future_insights", "history",
    }
    if callable(obj):
        print(f"   ⚠️ [General Summary Hygiene] Dropped callable/function at {label}")
        return _GS_DROPPED
    if isinstance(obj, dict):
        out = {}
        for key, value in obj.items():
            child = _sanitize_general_summary_input(value, f"{label}.{key}")
            if child is _GS_DROPPED:
                continue  # skip callable values only
            if key in SOURCE_LIST_FIELDS:
                child = _dict_items_only(child, f"{label}.{key}") if isinstance(child, list) else []
            out[key] = child  # preserve legitimate None values
        return out
    if isinstance(obj, (list, tuple)):
        out = []
        for idx, item in enumerate(obj):
            child = _sanitize_general_summary_input(item, f"{label}[{idx}]")
            if child is not _GS_DROPPED:
                out.append(child)
        return out
    return obj

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

_WORD_MIN = 250
_WORD_MAX = 300


def _build_input_payload(full_data: dict, company_name: str) -> dict:
    """Extract only the structured fields the model is allowed to reference."""
    full_data = _sanitize_general_summary_input(full_data or {}, f"{company_name}.general_summary_input")

    # Delta from history
    history = _dict_items_only(full_data.get("history") or [], "history")
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
        l for l in _dict_items_only(full_data.get("evidence_links", []), "evidence_links")
        if l.get("verified") is True and l.get("url") and not _is_legal_page(l.get("url", ""))
        and not l.get("wrong_entity")  # Exclude sources about a different company with same name
        and is_generation_eligible_source(l)
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
        title = (l.get("title", "Source") or "Source")[:80]
        snippet_raw = (l.get("source_quote") or l.get("snippet") or "").strip()
        tier = (l.get("claim_tier") or "D").upper()
        min_len, max_len = tier_snippet_bounds(tier)
        # Tighter ceiling on secondary evidence regardless of tier.
        if i >= 8:
            max_len = min(max_len, 350)
        # Tier-aware minimum lets dense tier-A facts ("Revenue €500M, 7,230
        # employees") through, while title-echo and boilerplate filters block
        # hollow / nav-stub content.  Padded tier-C self-report bloat is
        # trimmed by the tier-specific max_len truncation below.
        if (len(snippet_raw) < min_len
                or is_title_echo(snippet_raw, title)
                or is_boilerplate_snippet(snippet_raw)):
            continue
        snippet = snippet_raw[:max_len]
        scoped_link = apply_entity_scope(dict(l), company_name)
        verified_links.append({
            "title": title,
            "snippet": snippet,
            "is_recent": l.get("is_recent", False),
            "scope": assess_source_scope(l.get("url", ""), title, snippet),
            "entity_scope": scoped_link.get("entity_scope", "unknown"),
            "scope_warning": scoped_link.get("scope_warning", ""),
            "claim_tier": tier,
            "source_label": describe_source(l),
        })

    # Add validated foresight sources to the evidence pool (max 3, validated only)
    # Foresight sources always get deep snippets (500 chars) — forward-looking signals.
    foresight_sources = []
    fs_min, fs_max = tier_snippet_bounds("E")
    for l in full_data.get("foresight_links", []):
        if not is_generation_eligible_source(l):
            continue
        title = (l.get("title", "Foresight") or "Foresight")[:80]
        snippet_raw = (l.get("snippet") or "").strip()
        # If the raw snippet is hollow / title-echo, compose one from
        # source_says + possible_relevance.  possible_relevance is the
        # company-specific impact hypothesis, already modality-sanitized by
        # foresight_guardrails.py — safe to surface verbatim.  Joining them
        # gives the LLM both the trend and its application without inventing
        # claims; tier-E metadata still tells the model to hedge.
        if (len(snippet_raw) < fs_min
                or is_title_echo(snippet_raw, title)
                or is_boilerplate_snippet(snippet_raw)):
            source_says = (l.get("source_says") or "").strip()
            relevance = (l.get("possible_relevance") or "").strip()
            if is_title_echo(source_says, title):
                source_says = ""
            composed = " — ".join(p for p in (source_says, relevance) if p)
            if len(composed) < fs_min:
                continue
            snippet_raw = composed
        snippet = snippet_raw[:fs_max]
        scoped_link = apply_entity_scope(dict(l), company_name)
        foresight_sources.append({
            "title": title,
            "snippet": snippet,
            "is_recent": True,
            "scope": assess_source_scope(l.get("url", ""), title, snippet),
            "entity_scope": scoped_link.get("entity_scope", "unknown"),
            "scope_warning": scoped_link.get("scope_warning", ""),
            "claim_tier": "E",
            "source_label": "Forward-looking foresight",
        })
        if len(foresight_sources) >= 3:
            break
    verified_links = verified_links + foresight_sources

    # Key jury findings — descriptions only, no URLs, no legal pages.
    # Checklist items are Scanner 2 analysis findings; they do NOT carry a url field
    # (urls live on raw_findings / evidence_links, not the checklist).  Requiring
    # item.get("url") here silently drops all Scanner findings for companies whose
    # web evidence links failed verification.  Only exclude items whose url is a
    # legal/privacy page — URL-less findings are fine.
    key_findings = [
        {
            "description": item.get("description") or item.get("category", ""),
            "title": ((item.get("title") or item.get("category", "")) or "")[:80],
            "sentiment": item.get("sentiment", "neutral"),
            # Path B: classify checklist findings the same way as evidence_links.
            # Items with a URL get their computed claim_tier and source_label.
            # URL-less items default to tier D ("inferred / weak signal") and the
            # generic "Scanner analysis finding" label — these are model-grounded
            # observations without a citable source URL.
            "claim_tier": item.get("claim_tier") or ("D" if not item.get("url") else "C"),
            "source_label": describe_source(item) if item.get("url") else "Scanner analysis finding",
        }
        for item in full_data.get("reality_checklist", [])
        if item.get("found")
        and is_generation_eligible_source(item)
        and not (item.get("url") and _is_legal_page(item.get("url", "")))
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
            source_url = f.get("source_url", "")
            source_title = f.get("source_title", "")
            evidence_snippet = f.get("evidence_snippet", "")
            # Classify the financial source so the LLM can attribute correctly:
            # third-party database (Finder, Bloomberg) vs company filing (annual report, IR).
            # Without this, financial_findings reach the prompt with no provenance signal
            # and the model defaults to "according to the company's filings" for everything.
            if source_url:
                scoped_fin = apply_entity_scope({
                    "url": source_url,
                    "title": source_title,
                    "snippet": evidence_snippet,
                    "entity_scope": f.get("entity_scope") or "",
                }, company_name)
                stype = infer_source_type(source_url, source_title, evidence_snippet)
                independence = (
                    "third-party-or-independent"
                    if stype in {"financial_data_aggregator", "analyst", "news", "media", "regulation"}
                    else "company-reported"
                )
                tier = compute_claim_tier({
                    "source_type": stype,
                    "source_credibility": "high" if stype in {"financial_data_aggregator", "analyst", "news"} else "medium",
                    "independence": independence,
                    "scope": "broad",
                })
            else:
                stype, tier = "unknown", "D"
                scoped_fin = apply_entity_scope({
                    "title": source_title,
                    "snippet": evidence_snippet,
                    "entity_scope": f.get("entity_scope") or "",
                }, company_name)
            if stype == "financial_data_aggregator":
                source_label = "Third-party financial database"
            elif stype in {"company_financial_report", "annual_report", "investor_relations", "quarterly_report", "financial_report"}:
                source_label = "Company filing / investor relations"
            elif stype in {"news", "media", "analyst"}:
                source_label = "Independent news / analyst"
            else:
                source_label = "Other source"
            entry = {
                "dimension": f.get("dimension", ""),
                "assessment_note": "model-interpreted summary, NOT a direct source quote",
                "assessment": f.get("assessment", ""),
                "source_label": source_label,
                "claim_tier": tier,
                "entity_scope": scoped_fin.get("entity_scope", "unknown"),
                "scope_warning": scoped_fin.get("scope_warning", ""),
            }
            if evidence_snippet:
                entry["evidence_snippet"] = evidence_snippet
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
        "prompt_guardrails_version": PROMPT_VERSION,
        "entity_identity": {
            "canonical_name": get_company_identity(company_name).get("canonical_name", company_name),
            "entity_scope_policy": get_company_identity(company_name).get("entity_scope_policy", ""),
        },
        "delta": delta,
        "key_findings": key_findings,
        "evidence_links": verified_links,
        "key_people": people,
        "financial_findings": fin_findings,
        "financial_index": full_data.get("financial_index"),
    }
    if contradictions:
        result["contradictions"] = contradictions
    if delta:
        result["reality_score"] = full_data.get("reality_score", 0)
    return result


def _build_prompt(payload: dict, company_name: str) -> str:
    forbidden = ", ".join(_FORBIDDEN_TERMS)
    summary_guardrails = _general_summary_guardrails()
    return f"""You are writing the General Summary section of a forensic B2B intelligence report about {company_name}. Your role in this section is evidence reporter — not analyst, not advisor. You synthesise verified facts into readable prose, but you do not speculate, project, or editorialize.

This section differs from the AI Diagnosis section: the Diagnosis interprets and projects; this Summary strictly reports what the evidence shows.

SHARED FACT-DISCIPLINE / FORESIGHT GUARDRAILS:
{summary_guardrails}

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

3a. FINANCIAL FINDINGS ATTRIBUTION — CRITICAL:
   Each item in financial_findings carries:
     - "source_label": one of "Third-party financial database", "Company filing / investor relations", "Independent news / analyst", or "Other source"
     - "assessment_note": "model-interpreted summary, NOT a direct source quote" — meaning the assessment text was written by an analysis model, not lifted verbatim from the source
     - "claim_tier": A/B/C/D for calibration (internal — never write tier codes in output)
   When you cite a financial figure:
     - If source_label = "Third-party financial database" → write "according to Finnish business database [source_title]" or "third-party financial data shows" — NEVER "according to the company's annual report"
     - If source_label = "Company filing / investor relations" → write "the company's annual report shows" or "filed accounts disclose"
     - If source_label = "Independent news / analyst" → write "[source_title] reported"
   The "assessment" text is interpretation, not a quote. Use the underlying figure (revenue, margin, ratio) but rephrase in your own words; never present the model's interpretive language as if it were a source quote.

4. FORBIDDEN TERMS: Do not use any of these: {forbidden}. Use plain descriptive language instead.

5. NEUTRAL LANGUAGE — CRITICAL:
   - Do not use dramatic, editorial, or emotionally charged modifiers. Banned patterns include but are not limited to: "widening," "aggressively," "severe," "threatening," "forcing," "critical juncture," "operational friction," "at a crossroads."
   - Do not use "will," "threatens," "risks," or "forces" to describe future outcomes. Report only what has happened or is currently documented.
   - Use precise, neutral language. Prefer concrete descriptions over characterisations. Instead of "aggressively pivoting toward AI," write "has established a dedicated AI business unit and deployed Copilot to N users."

6. OPENING SENTENCE — MANDATORY:
   - If delta data is present: open with the most significant concrete STRATEGIC or OPERATIONAL change from the delta (e.g. "Since the previous assessment in [date], [company] has [structural event or operational change]"). If the delta shows both financial and non-financial changes, prefer the non-financial event — financial figures belong in the financial paragraph, not the opening.
   - If no delta: open with the single most significant observed fact from key_findings or evidence_links — prioritise a strategic event, structural change, or operational signal over a financial figure.
   - Never open with a revenue figure, profit margin, or financial ratio.
   - Never open with a number. Never open with a narrative characterisation — anchor on a specific, verifiable data point.

7. COVERAGE: Address at least 3 distinct dimensions if the evidence supports them — for example: strategic position or key structural events, operational or technology signals, financial performance, people and leadership. Do not write the entire summary about a single event or topic. If the evidence does not support a dimension, omit it rather than filling the gap with unsupported claims.

8. PARAGRAPH ALLOCATION — CRITICAL:
   Financial data (revenue, profit, margins, equity ratios) may occupy AT MOST one of the 3–4 paragraphs, and only when the financial evidence is concrete and material. Do not lead with financial data.
   Default priority order for paragraph topics:
     1. The most significant strategic event or structural change (carve-out, acquisition, major contract win, certification, new business unit)
     2. Operational and technology signals (delivery model, AI integration, platform, tools, certifications, partnerships)
     3. Financial performance — only if concrete figures exist and materially change the picture
     4. People and leadership — only if a key stakeholder change or team signal is documented
   If only financial evidence is strong and operational evidence is thin, one paragraph on financials is acceptable — but do not pad other paragraphs with weak signals to meet the 3-paragraph minimum. Acknowledge the evidential gap instead.

9. SCOPE & SOURCE QUALIFICATION — CRITICAL:
   - CLAIM TIER (INTERNAL ONLY — never write tier codes in output): Evidence items are prefixed [A]-[E] as reading aids. Use tier to choose your verb — for self-claims use "states"/"positions itself as", for weak signals use "suggests"/"may indicate", for independent verification use "reported"/"confirmed". NEVER write [A], [B], [C], [D], [E] or "tier" in the output prose.
   - NARROW SCOPE: When citing a finding from a narrow-scope source (job postings, employee reviews, single-department surveys), you MUST include a scope qualifier (e.g., "in a survey of HR personnel").
   - ANTI-EXTRAPOLATION: NEVER extrapolate a narrow-scope finding into a macro-trend. State the narrow fact exactly as it is, without macro-commentary.
   - SELF-REPORTED DATA: For company self-claims, qualify every concrete assertion ("The company states...", "According to the company..."). Do not omit specific numbers, but never present them as independently verified.
   - ENTITY SCOPE: Each evidence or financial item may carry entity_scope. Treat "canonical_group" as the target company. Treat "local_legal_entity", "subsidiary", "former_name", and "parent_owner" as scoped evidence only.
   - LOCAL LEGAL ENTITY RULE: If a financial figure is supported only by local_legal_entity evidence, qualify it in the same sentence as local/legal-entity evidence (for example, "the Finnish legal-entity data shows...", "{company_name}'s Finnish legal entity...", or the exact legal entity name if the payload provides it). Do not write it as unqualified company-wide performance.
   - FORMER NAME RULE: Former-name evidence may support history/continuity, but current claims must say the source relates to the predecessor/former name unless a current canonical_group source also supports it.
   - OWNER RULE: parent_owner evidence supports ownership/acquisition context only. Do not use owner/acquirer data as evidence of the target company's operations or finances.

9. BALANCING CONTRADICTIONS & PROPORTIONALITY — CRITICAL: 
   - If a narrow-scope independent source contradicts a broad self-reported metric, briefly state both side-by-side (e.g., "Wärtsilä reports [X] company-wide users, though a departmental survey of HR found [Y]").
   - PROPORTIONALITY: You are summarizing a multi-billion euro enterprise. Keep narrow departmental statistics extremely brief. Do not let a single departmental survey dominate the technology or operational narrative of a global firm. Address the contradiction in a single, strictly factual sentence and move on.

10. RISK REPORTING: If the evidence documents negative trends or concrete vulnerabilities, report them factually. If the data reflects standard business practices (e.g., strategic outsourcing, white-labeling), do not manufacture artificial crises. Do not speculate about consequences — state the documented fact and let the reader draw conclusions.

11. PLAIN BUSINESS ENGLISH: No superlatives. No marketing language. No hedging phrases like "it appears that" or "it seems". State findings directly.

12. PARAGRAPHS: Structure the output as exactly 3-4 paragraphs, each covering a distinct dimension. Separate paragraphs using standard newline characters (\\n\\n). This is required for readability.

13. FORMAT: Plain prose only. No headers, no bullet points, no markdown, no links, no citations. CRITICAL: Do NOT use HTML tags like <br> to format line breaks.

14. ENTITY NAMING CONSISTENCY — CRITICAL:
   - Usually refer to the target company by its PRIMARY BRAND NAME ({company_name}) throughout the summary. Do not surface individual country-specific legal entity registrations (e.g., "{company_name} Oy", "{company_name} Ltd", "{company_name} Limited", "{company_name} GmbH", "{company_name} s.r.o.", "{company_name} AB", "{company_name} AS", etc.) as if they were separate, noteworthy organisations.
   - Evidence sources may come from company registries (Companies House, Finnish Trade Register, etc.) that use formal legal names in ALL CAPS or with full legal suffixes. These are data sources only — do NOT reproduce the formal registered name in the summary prose.
   - If the company's corporate structure is genuinely relevant (e.g., a parent/subsidiary relationship with a DIFFERENT brand name), you may note it once briefly. Legal entity variants of the SAME brand name must not be named as separate organisations. They may be named only to qualify a sourced local legal-entity financial figure and must not be promoted into group-wide evidence.

Output only the prose text. Nothing else."""


def _word_count(text: str) -> int:
    return len(text.split())


def _strip_links(text: str) -> str:
    """Remove any [label](url) markdown links the model sneaks in despite instructions.
    Keeps the label text so readability is preserved; only the URL syntax is dropped."""
    return re.sub(r'\[([^\]]+)\]\(https?://[^)]+\)', r'\1', text)


def generate_general_summary(full_data: dict, company_name: str) -> tuple[str, str]:
    """
    Generate a 250-300 word General Summary from structured scan data.

    Returns (summary_text, model_used).
    Returns ("", "") on failure — caller should catch exceptions separately.
    """
    full_data = _sanitize_general_summary_input(full_data or {}, f"{company_name}.general_summary_factcheck_input")
    payload = _build_input_payload(full_data, company_name)
    prompt = _build_prompt(payload, company_name)

    # Primary: GPT-5.5 (evidence-bound prose from already-built structured payload)
    try:
        PRIMARY_MODEL = "gpt-5.5"
        print(f"   [General Summary] Trying primary {PRIMARY_MODEL}")
        t0 = time.time()
        text = _strip_links(_call_openai_summary(prompt, model=PRIMARY_MODEL, timeout=90))
        wc = _word_count(text)
        elapsed = int((time.time() - t0) * 1000)
        print(f"   [General Summary] {PRIMARY_MODEL}: {wc} words ({elapsed}ms)")
        if text:
            if not (_WORD_MIN <= wc <= _WORD_MAX):
                print(f"   [General Summary] {PRIMARY_MODEL} word count outside [{_WORD_MIN}-{_WORD_MAX}] — using non-empty result")
            return text, PRIMARY_MODEL
    except Exception as e:
        print(f"   ❌ [General Summary] GPT-5.5 primary failed: {e}")

    # Backup 1: Sonnet 4.6 (independent provider)
    try:
        import anthropic
        anthropic_key = _clean_env_var(os.environ.get("ANTHROPIC_API_KEY", ""))
        if not anthropic_key:
            print("   ⚠️ [General Summary] No ANTHROPIC_API_KEY — skipping Sonnet backup")
        else:
            SONNET_MODEL = "claude-sonnet-4-6"
            print(f"   [General Summary] GPT-5.5 unavailable — trying {SONNET_MODEL}")
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

    # Backup 2: Gemini 3.1 Pro (last resort for this test)
    try:
        client = get_gemini_client()
    except ValueError:
        print("   ⚠️ [General Summary] No GEMINI_API_KEY — skipping Gemini backup")
        return "", ""

    try:
        BACKUP_MODEL = "gemini-3.1-pro-preview"
        print(f"   [General Summary] GPT/Sonnet exhausted — trying backup {BACKUP_MODEL}")
        t0 = time.time()
        backup_config = genai_types.GenerateContentConfig(
            temperature=1.0,
            max_output_tokens=6000,
            thinking_config=genai_types.ThinkingConfig(thinking_budget=1024),
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

    return "", ""


# ── Payload-based fact-check for General Summary ─────────────────────────────
# Unlike the AI Diagnosis fact-check (which uses Google Search grounding),
# this checks the General Summary against its OWN input payload. The Summary
# should contain ONLY claims traceable to the payload — anything else is
# hallucination or training-data leakage.

def _build_fact_check_prompt(summary_text: str, payload: dict, company_name: str) -> str:
    fact_check_guardrails = _general_summary_guardrails()
    return f"""You are a precision fact-checker. Your job is to verify that EVERY factual claim in the General Summary below is directly supported by the provided evidence payload AND that narrow-scope findings are not silently elevated into company-wide claims. The Summary should contain ONLY information from the payload — nothing else.

SHARED FACT-DISCIPLINE / FORESIGHT GUARDRAILS:
{fact_check_guardrails}

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
   F. It refers to {company_name} using a country-specific legal entity suffix (e.g., "{company_name} Limited", "{company_name} Oy", "{company_name} GmbH", "{company_name} Ltd", "{company_name} s.r.o.", "{company_name} AB", "{company_name} AS") as if it were a distinct, separately noteworthy organisation. The summary must use the primary brand name "{company_name}" consistently.
4. SCOPE INFLATION CHECK — Each evidence_links item has a "scope" field ("broad" or "narrow"). For each numeric or scale claim in the summary, identify which evidence item it traces back to. Flag the claim as UNSUPPORTED if:
   D. The claim is derived from a "narrow"-scope source AND is presented as a company-wide fact without a scope qualifier in the same sentence (qualifier examples: "in a survey of HR personnel", "in a 100-respondent study", "in a Vaasa-based pilot", "according to a master's thesis"). For example, "9% of personnel use Tool X" lifted from a narrow HR-only thesis without saying "in HR" is scope inflation.
   E. The payload has a top-level "contradictions" array, the summary cites one side but does NOT acknowledge the other side at all. Both sides of a listed contradiction must appear in the prose.
   G. The claim is derived from an item with entity_scope="local_legal_entity", "subsidiary", "former_name", or "parent_owner" and is presented as an unqualified {company_name}-wide fact. Local legal-entity financial figures must be qualified as local/legal-entity data; parent_owner evidence must only support ownership/acquisition context.
5. Do NOT flag:
   - Reasonable connecting language between evidence points (e.g., "meanwhile," "in parallel")
   - Direct restatements of payload data, even if paraphrased
   - The opening trend sentence if it accurately reflects the delta data
   - Claims from narrow-scope sources THAT ARE properly qualified in the same sentence
   - A country-specific legal entity name when it is used only to qualify a local legal-entity financial figure supported by entity_scope="local_legal_entity"

OUTPUT FORMAT — valid JSON only:

If all claims are supported:
{{"unsupported_claims": [], "verified": true}}

If unsupported claims found:
{{"unsupported_claims": [{{"claim": "The exact quote or close paraphrase from the summary", "reason": "Brief explanation of why this is not in the payload OR which scope-inflation rule (4D / 4E) it violates"}}], "verified": false}}"""


def _build_regen_prompt(payload: dict, company_name: str, original_text: str, false_claims: list) -> str:
    """Build a re-generation prompt that incorporates the false claims as explicit warnings."""
    regen_guardrails = _general_summary_guardrails()
    claims_list = "\n".join(f"- {fc['claim']}: {fc['reason']}" for fc in false_claims)
    base_prompt = _build_prompt(payload, company_name)
    return f"""{base_prompt}

CRITICAL CORRECTION — THE PREVIOUS ATTEMPT CONTAINED THESE UNSUPPORTED CLAIMS:
{claims_list}

SHARED FACT-DISCIPLINE / FORESIGHT GUARDRAILS:
{regen_guardrails}

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
    # Primary: Gemini 3.1 Pro | Backup 1: Gemini 2.5 Pro (small thinking budget) | Backup 2: Sonnet 4.6
    # Gemini 2.5 Pro REQUIRES non-zero thinking_budget (the API rejects 0 with
    # "Budget 0 is invalid. This model only works in thinking mode."). We pass a
    # small budget (512) and rely on the part-extraction logic below to filter
    # out the thought parts so only text parts feed the JSON parser.
    CHECK_MODELS = [
        ("gemini-3.1-pro-preview", "gemini"),
        ("gemini-2.5-pro", "gemini"),
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
                # gemini-2.5-pro requires a non-zero thinking_budget — the API rejects 0
                # with INVALID_ARGUMENT. Use a small budget (512) so it thinks briefly;
                # the part-extraction logic below discards thought parts and keeps text.
                thinking_cfg = None
                if "2.5" in model_id:
                    thinking_cfg = genai_types.ThinkingConfig(thinking_budget=512)
                # max_output_tokens covers BOTH thinking tokens and text tokens for
                # thinking-capable models. 2000 was too tight: thinking consumed most
                # of it, leaving ~300 chars of text — not enough for a valid JSON
                # response. 4096 leaves comfortable headroom for both.
                resp = call_with_503_backoff(
                    lambda m=model_id, tc=thinking_cfg: client.models.generate_content(
                        model=m,
                        contents=check_prompt,
                        config=genai_types.GenerateContentConfig(
                            temperature=0.0,
                            max_output_tokens=4096,
                            thinking_config=tc,
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
