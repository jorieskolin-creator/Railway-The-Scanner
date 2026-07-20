"""
general_summary.py — Evidence-based business snapshot generator + payload fact-check.

Assembles a 250-320 word narrative from already-computed structured scan data
(jury findings and financial findings). Citations resolve through the shared
evidence manifest —
the General Summary is plain prose only; all evidence links are already shown
in the AI Diagnosis section of the UI.

After generation, fact_check_general_summary() verifies every claim in the
output traces back to the input payload (catches training-data hallucinations).
If unsupported claims are found, the summary is re-generated with explicit
warnings about the false claims.
"""

from __future__ import annotations

import json
import os
import re
import time
import hashlib

from source_quality import (
    assess_source_scope, infer_source_type, compute_claim_tier, describe_source,
    is_boilerplate_snippet, is_title_echo, tier_snippet_bounds,
    is_generation_eligible_source, is_analysis_premise_source,
)
from contradiction_detector import detect_contradictions
from prompts import PROMPT_VERSION, build_shared_guardrails
from entity_identity import apply_entity_scope, get_company_identity
from model_policy import CLAUDE_SONNET_MODEL, GPT_REASONING_MODEL, GPT_VERIFIER_MODEL, policy_for
from llm_clients import call_anthropic_text, call_openai_text, extract_anthropic_text


def _general_summary_guardrails() -> str:
    """Shared fact-discipline guardrails for plain General Summary mode."""
    return build_shared_guardrails(include_diagnostic=False, include_foresight=False, include_claim_tier=True)


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


def _call_openai_summary(prompt: str, model: str = GPT_REASONING_MODEL, timeout: int = 90) -> str:
    policy = policy_for("general_summary")
    return call_openai_text(
        prompt,
        model=model,
        max_output_tokens=4000,
        timeout=timeout,
        max_retries=0,
        reasoning_effort=str(policy.get("effort") or "medium"),
        text_verbosity=str(policy.get("verbosity") or "medium"),
        log_label="General Summary",
    )




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

_INTERNAL_PROCESS_PATTERNS = (
    "supplied payload", "current payload", "supplied delta", "payload identifies",
    "key_findings", "evidence_links", "generation-eligible", "generation eligible",
    "display-only", "display only", "promoted evidence", "promoted source",
    "source register", "scanner returned", "current scan returned",
    "zero promoted", "evidence pipeline",
)

_WORD_MIN = 250
_WORD_MAX = 320


def _build_input_payload(full_data: dict, company_name: str) -> dict:
    """Extract only the structured fields the model is allowed to reference."""
    full_data = _sanitize_general_summary_input(full_data or {}, f"{company_name}.general_summary_input")

    # Previous Scanner outputs are deliberately excluded. Model and pipeline
    # changes make scores, gaps and classifications incomparable across scans.

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
            "evidence_id": l.get("evidence_id", ""),
            "citation": l.get("citation", ""),
            "title": title,
            "snippet": snippet,
            "is_recent": l.get("is_recent", False),
            "scope": assess_source_scope(l.get("url", ""), title, snippet),
            "entity_scope": scoped_link.get("entity_scope", "unknown"),
            "scope_warning": scoped_link.get("scope_warning", ""),
            "claim_tier": tier,
            "source_label": describe_source(l),
        })

    # Attributed company context is not hard factual proof. It lets the General
    # Summary say what verified company material describes (domain, service
    # areas, ownership/context) without falling back to Scanner-status language
    # such as "limited promoted evidence". These items must be attributed in
    # prose and must not support diagnosis, readiness or risk claims here.
    attributed_company_context = []
    for l in _dict_items_only(full_data.get("evidence_links", []), "evidence_links"):
        if len(attributed_company_context) >= 8:
            break
        if l in _all_evidence:
            continue
        if l.get("verified") is not True and l.get("url_verified") is not True:
            continue
        if not l.get("url") or _is_legal_page(l.get("url", "")) or l.get("wrong_entity"):
            continue
        if not is_analysis_premise_source(l):
            continue
        snippet_raw = (l.get("semantic_snippet") or l.get("source_quote") or l.get("snippet") or "").strip()
        title = (l.get("title", "Company context source") or "Company context source")[:90]
        if (
            len(snippet_raw) < 50
            or is_title_echo(snippet_raw, title)
            or is_boilerplate_snippet(snippet_raw)
        ):
            continue
        scoped_link = apply_entity_scope(dict(l), company_name)
        attributed_company_context.append({
            "evidence_id": l.get("evidence_id", ""),
            "citation": l.get("citation", ""),
            "title": title,
            "context_snippet": snippet_raw[:650],
            "source_use_status": l.get("source_use_status") or l.get("promotion_status") or "",
            "source_context_status": l.get("source_context_status") or "",
            "entity_scope": scoped_link.get("entity_scope", "unknown"),
            "source_label": describe_source(l),
            "usage_rule": (
                "Use only as attributed company context, e.g. 'company material states/lists/describes'. "
                "Do not present as independent proof or as diagnosis."
            ),
        })

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
            if (
                not source_url
                or not evidence_snippet
                or f.get("generation_eligible") is False
                or str(f.get("entity_match_status") or "").lower() == "wrong"
                or f.get("wrong_entity") is True
            ):
                continue
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
                "source_label": source_label,
                "claim_tier": tier,
                "entity_scope": scoped_fin.get("entity_scope", "unknown"),
                "scope_warning": scoped_fin.get("scope_warning", ""),
                "evidence_snippet": evidence_snippet[:700],
            }
            if source_title:
                entry["source_title"] = source_title
            fin_findings.append(entry)
        else:
            # Legacy fallback: plain string
            fin_findings.append(str(f))

    # Only include scores the model genuinely needs for context.
    # intent_score, confidence_score, and financial_sub_scores are intentionally
    # excluded: when present as JSON fields the model treats them as "facts to
    # report" and dumps them as a score list instead of writing a narrative.
    # financial_index is kept (single number) as context for financial findings.
    # Detect quantitative disagreements between evidence items (e.g. one
    # source says "9% of HR personnel use Tool X", another says "thousands
    # of weekly users"). When present, the prompt instructs the model to
    # cite both sides with their scope rather than silently picking one.
    # Failure-safe: returns [] if verification is unavailable.
    contradictions = detect_contradictions(verified_links, company_name)

    result: dict = {
        "company_name": company_name,
        "prompt_guardrails_version": PROMPT_VERSION,
        "entity_identity": {
            "canonical_name": get_company_identity(company_name).get("canonical_name", company_name),
            "entity_scope_policy": get_company_identity(company_name).get("entity_scope_policy", ""),
        },
        "key_findings": key_findings,
        "evidence_links": verified_links,
        "attributed_company_context": attributed_company_context,
        "financial_findings": fin_findings,
        "financial_index": full_data.get("financial_index"),
        "evidence_sufficiency": full_data.get("evidence_sufficiency") or {},
        "low_evidence_mode": bool(full_data.get("low_evidence_mode")),
        "low_evidence_label": full_data.get("low_evidence_label", ""),
    }
    if contradictions:
        result["contradictions"] = contradictions
    return result


def _build_prompt(payload: dict, company_name: str) -> str:
    forbidden = ", ".join(_FORBIDDEN_TERMS)
    summary_guardrails = _general_summary_guardrails()
    return f"""You are writing the General Summary section of a forensic B2B intelligence report about {company_name}. Your role in this section is to write an evidence-based business snapshot: what the supplied evidence establishes about the company right now. This is a facts-only summary, not analysis. Do not analyze causes, infer implications, diagnose maturity, evaluate readiness, give advice, make recommendations, or project future outcomes.

This section differs from nearby report sections:
- AI Diagnosis owns deeper interpretation, risk framing, recommendations, and projected consequences.
- Stakeholder List owns people and role details.
- Structured Strategic Foresight Evidence owns key forces, scenarios, and cross-impact context.
- Evidence Trust owns source verification status.

The General Summary should organize verified facts into a concise executive snapshot without duplicating or performing the analysis owned by those sections.

SHARED FACT-DISCIPLINE / FORESIGHT GUARDRAILS:
{summary_guardrails}

EVIDENCE — the ONLY material you may reference. Every sentence you write must trace back to a specific item below. Do not supplement with outside knowledge, training data, or general industry assumptions:
{json.dumps(payload, ensure_ascii=False, indent=2)}

WRITING APPROACH:
Organize the evidence into a coherent narrative that gives a C-level reader an immediate factual snapshot. Start from verified company facts: what the company is, its domain, latest supported financial facts, documented ownership/entity scope, and concrete company-described services or capabilities. Stay in the present and past tense: report what has happened and what currently exists. You may group related facts for readability, but do not turn their combination into a new analytical conclusion. Do not project outcomes, consequences, trajectories, pressures, opportunities, or risks; those belong in the AI Diagnosis and foresight sections.

The payload may include attributed_company_context. This is valid only for compact company-description wording such as "company material states", "the company lists", or "public company material describes". It is not independent proof and it must not be used for readiness, maturity, risk, pressure, or recommendation claims.

Prefer a compact company snapshot over audit wording. Do not lead with or dwell on "limited evidence", "insufficient promoted evidence", empty fields, source-count language, or Scanner status. Mention evidence limitations only after you have stated the verified company facts and only if the payload gives a reader-facing limitation that matters; phrase it as an open evidence question, not as a diagnosis.

The summary should answer four questions, only where the payload supports them:
1. What is the company's current verified business context?
2. What are the latest supported financial facts and documented changes?
3. What operational reality is actually evidenced?
4. What explicit contradiction or evidence limitation is documented in the payload?

MANDATORY RULES:

1. WORD COUNT: The output MUST be between {_WORD_MIN} and {_WORD_MAX} words. Count carefully. This is non-negotiable.

2. FACTUAL DISCIPLINE — CRITICAL:
   - EVIDENCE-ONLY: Every factual claim must be directly traceable to a specific item in the EVIDENCE above. If you cannot point to the specific evidence item that supports a statement, do not write it. Never supplement with knowledge from outside the provided data.
   - LOW-EVIDENCE MODE: If low_evidence_mode=true or low_evidence_label is present, keep all claims cautious. Acknowledge limited public evidence only after presenting any supported company identity, financial, and company-described context. Do not let structured checklist findings substitute for source-backed company facts.
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

3. DATA HANDLING / META-METRICS: Do not reference the report's internal scoring metrics (e.g., "Reality Score," "Ambition Score," "Friction Index," "Integrity Delta," "Financial Health") by name in the generated prose. These scores should inform your overall tone only. The summary itself must focus strictly on the company's real-world financial, operational, and organizational data.
   Never mention the payload, scan, Scanner, source register, field names, promotion states, generation eligibility, display-only status, or counts of accepted/rejected evidence. If a business dimension lacks supported facts, omit it or describe the public evidence limitation in ordinary reader-facing language without exposing the analysis process.

3a. FINANCIAL FINDINGS ATTRIBUTION — CRITICAL:
   Each item in financial_findings carries:
     - "source_label": one of "Third-party financial database", "Company filing / investor relations", "Independent news / analyst", or "Other source"
     - "assessment_note": "model-interpreted summary, NOT a direct source quote" — meaning the assessment text was written by an analysis model, not lifted verbatim from the source
     - "claim_tier": A/B/C/D for calibration (internal — never write tier codes in output)
   When you cite a financial figure:
     - The exact value must appear in the evidence_snippet or source_title for that financial finding. If the value appears only in assessment, do not cite it as a fact.
     - If source_label = "Third-party financial database" → write "according to Finnish business database [source_title]" or "third-party financial data shows" — NEVER "according to the company's annual report"
     - If source_label = "Company filing / investor relations" → write "the company's annual report shows" or "filed accounts disclose"
     - If source_label = "Independent news / analyst" → write "[source_title] reported"
     - Do not write that a financial metric is unpublished, unavailable, or not yet reported if any financial_findings or evidence item contains that metric for the latest annual period. Use the latest annual value and source attribution instead.
   The "assessment" text is interpretation, not a quote. Use the underlying figure (revenue, margin, ratio) but rephrase in your own words; never present the model's interpretive language as if it were a source quote.
   Financial paragraph goal: report the latest supported figures, their periods, their scope, and documented changes. Do not infer resilience, pressure, investment capacity, liquidity quality, or ability to absorb change. If financial evidence is thin, state only that the available financial evidence is limited.

4. FORBIDDEN TERMS: Do not use any of these: {forbidden}. Use plain descriptive language instead.

5. NEUTRAL LANGUAGE — CRITICAL:
   - Do not use dramatic, editorial, or emotionally charged modifiers. Banned patterns include but are not limited to: "widening," "aggressively," "severe," "threatening," "forcing," "critical juncture," "operational friction," "at a crossroads."
   - Do not use "will," "threatens," "risks," or "forces" to describe future outcomes. Report only what has happened or is currently documented.
   - Use precise, neutral language. Prefer concrete descriptions over characterisations. Instead of "aggressively pivoting toward AI," write "has established a dedicated AI business unit and deployed Copilot to N users."

6. OPENING SENTENCE — MANDATORY:
   - Open with the single most significant current observed fact from key_findings, evidence_links, attributed_company_context, or financial_findings — prioritise company identity/domain, a strategic event, structural change, service/capability context, or operational signal over a financial figure.
   - Never open with a revenue figure, profit margin, or financial ratio.
   - Never open with a number. Never open with a narrative characterisation — anchor on a specific, verifiable data point.

7. COVERAGE: Address at least 3 distinct dimensions if the evidence supports them — business context or structural events, financial facts, operational or technology facts, and explicit contradictions or evidence limitations. Do not write the entire summary about a single event or topic. If the evidence does not support a dimension, omit it rather than filling the gap with analysis.

8. PARAGRAPH ALLOCATION — CRITICAL:
   Financial data (revenue, profit, margins, equity ratios) may occupy AT MOST one of the 3–4 paragraphs, and only when the financial evidence is concrete and material. Do not lead with financial data.
   Default priority order for paragraph topics:
     1. Current verified business context: what the company is, entity-scope caveats, and material strategic or structural changes (carve-out, acquisition, major contract win, certification, new business unit)
     2. Financial facts: latest supported values, periods, entity scope, and documented changes
     3. Operational reality: delivery model, technology signals, AI integration, platform, tools, certifications, partnerships, culture, or service portfolio evidence
     4. Explicit contradiction or limitation: only a discrepancy or evidence gap directly recorded in the payload, stated without diagnosing its cause or consequence
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
   - Do not infer a tension from internal scores or from absence of evidence. Mention a discrepancy only when the payload explicitly records contradictory facts or a defined evidence limitation.
   - PROPORTIONALITY: You are summarizing a multi-billion euro enterprise. Keep narrow departmental statistics extremely brief. Do not let a single departmental survey dominate the technology or operational narrative of a global firm. Address the contradiction in a single, strictly factual sentence and move on.

10. RISK REPORTING: If the evidence documents negative trends or concrete vulnerabilities, report them factually. If the data reflects standard business practices (e.g., strategic outsourcing, white-labeling), do not manufacture artificial crises. Do not speculate about consequences — state the documented fact and let the reader draw conclusions.

11. PLAIN BUSINESS ENGLISH: No superlatives. No marketing language. No hedging phrases like "it appears that" or "it seems". State findings directly.

11a. STAKEHOLDER NON-DUPLICATION — CRITICAL: Do not include a stakeholder inventory, named stakeholder list, or generic people/role paragraph. Named people belong in the Stakeholder List section, not here. You may mention leadership only when the payload documents a material leadership, ownership, acquisition, carve-out, or governance event and that event changes the business context. Do not mention stakeholder names merely because people data exists elsewhere in the report.

12. PARAGRAPHS: Structure the output as exactly 3-4 paragraphs, each covering a distinct dimension. Separate paragraphs using standard newline characters (\\n\\n). This is required for readability.

13. FORMAT: Plain prose only. No headers, bullets, links, or HTML. Add the supplied numeric citation (for example [3]) immediately after each externally sourced factual statement. Never invent a citation number.

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


def _internal_process_phrases(text: str) -> list[str]:
    """Return Scanner-internal language that must never reach a company summary."""
    lowered = str(text or "").lower()
    return [phrase for phrase in _INTERNAL_PROCESS_PATTERNS if phrase in lowered]


def generate_general_summary(full_data: dict, company_name: str, time_budget_seconds: int | None = None) -> tuple[str, str]:
    """
    Generate a 250-320 word General Summary from structured scan data.

    Returns (summary_text, model_used).
    Returns ("", "") on failure — caller should catch exceptions separately.
    """
    full_data = _sanitize_general_summary_input(full_data or {}, f"{company_name}.general_summary_factcheck_input")
    payload = _build_input_payload(full_data, company_name)
    prompt = _build_prompt(payload, company_name)
    deadline = time.time() + max(12, int(time_budget_seconds or 90))

    def remaining() -> int:
        return max(0, int(deadline - time.time()))

    # Primary: GPT frontier model (evidence-bound prose from already-built structured payload)
    try:
        PRIMARY_MODEL = GPT_REASONING_MODEL
        print(f"   [General Summary] Trying primary {PRIMARY_MODEL}")
        t0 = time.time()
        text = _strip_links(_call_openai_summary(prompt, model=PRIMARY_MODEL, timeout=min(35, max(10, remaining()))))
        wc = _word_count(text)
        elapsed = int((time.time() - t0) * 1000)
        print(f"   [General Summary] {PRIMARY_MODEL}: {wc} words ({elapsed}ms)")
        if text:
            process_phrases = _internal_process_phrases(text)
            if process_phrases:
                print(
                    f"   ⚠️ [General Summary] {PRIMARY_MODEL} exposed internal process language "
                    f"({', '.join(process_phrases[:4])}) — trying independent backup"
                )
                text = ""
        if text:
            if not (_WORD_MIN <= wc <= _WORD_MAX):
                print(f"   [General Summary] {PRIMARY_MODEL} word count outside [{_WORD_MIN}-{_WORD_MAX}] — using non-empty result")
            return text, PRIMARY_MODEL
    except Exception as e:
        print(f"   ❌ [General Summary] {GPT_REASONING_MODEL} primary failed: {e}")

    # Backup 1: Sonnet independent provider
    try:
        if remaining() < 10:
            return "", ""
        import anthropic
        anthropic_key = _clean_env_var(os.environ.get("ANTHROPIC_API_KEY", ""))
        if not anthropic_key:
            print("   ⚠️ [General Summary] No ANTHROPIC_API_KEY — skipping Sonnet backup")
        else:
            SONNET_MODEL = CLAUDE_SONNET_MODEL
            print(f"   [General Summary] {GPT_REASONING_MODEL} unavailable — trying {SONNET_MODEL}")
            t0 = time.time()
            text = _strip_links(call_anthropic_text(
                prompt,
                model=SONNET_MODEL,
                max_tokens=4000,
                timeout=min(20, max(8, remaining())),
                max_retries=0,
                reasoning_effort="medium",
                log_label="General Summary Fallback",
            ))
            wc = _word_count(text)
            elapsed = int((time.time() - t0) * 1000)
            print(f"   [General Summary] {SONNET_MODEL} backup: {wc} words ({elapsed}ms)")
            if text:
                process_phrases = _internal_process_phrases(text)
                if process_phrases:
                    print(
                        "   ⚠️ [General Summary] Sonnet exposed internal process language "
                        f"({', '.join(process_phrases[:4])}) — rejecting summary"
                    )
                    text = ""
            if text:
                return text, SONNET_MODEL
    except Exception as e:
        print(f"   ❌ [General Summary] Sonnet backup failed: {e}")

    return "", ""


def _build_fact_check_prompt(summary_text: str, payload: dict, company_name: str) -> str:
    """Build the payload-only claim verification prompt for General Summary."""
    return f"""You are verifying the General Summary for {company_name}.

Your task is narrow: compare the SUMMARY against the PAYLOAD and identify factual claims that are not supported by the payload.

Rules:
- Use ONLY the PAYLOAD as source material. Do not use outside knowledge.
- A claim is supported when it can be traced to a specific current payload item, financial finding, contradiction, or evidence snippet.
- attributed_company_context supports only attributed company-description statements, such as what company material states, lists, or describes. It does not support independent proof, diagnosis, readiness, risk, pressure, or recommendation claims.
- Never infer a company trend from previous Scanner scores, gaps, diagnoses, or classifications.
- Do not flag wording differences or factual grouping that introduces no new conclusion.
- General Summary is facts-only. Flag causal explanations, readiness judgments, pressure/resilience interpretations, risk/opportunity statements, recommendations, and projected consequences even when they sound plausible, unless the payload explicitly states that exact documented fact.
- Do flag invented events, invented people, invented figures, wrong entity claims, unqualified local-entity claims, and future claims presented as facts.
- People lists belong in the Stakeholder List section, not the General Summary. Flag named stakeholder inventories unless they document a material leadership, ownership, acquisition, carve-out, or governance event in the payload.
- If the summary says something broader than the payload supports, flag it and explain the scope problem.

PAYLOAD:
{json.dumps(payload, ensure_ascii=False, indent=2)}

SUMMARY:
{summary_text}

Return STRICT JSON only:
{{
  "unsupported_claims": [
    {{
      "claim": "exact unsupported or over-scoped claim from the summary",
      "reason": "brief explanation of why the payload does not support it"
    }}
  ]
}}

If all claims are supported, return exactly:
{{"unsupported_claims": []}}"""


def _build_regen_prompt(payload: dict, company_name: str, previous_summary: str, false_claims: list) -> str:
    """Build a corrective General Summary prompt after payload fact-checking."""
    false_claim_text = json.dumps(false_claims or [], ensure_ascii=False, indent=2)
    base_prompt = _build_prompt(payload, company_name)
    return f"""{base_prompt}

ADDITIONAL FACT-CHECK CORRECTION CONTEXT:
The previous General Summary was rejected by payload verification. Do not repeat, paraphrase, or imply these unsupported claims:
{false_claim_text}

Previous rejected summary for context only, not as evidence:
{previous_summary}

Regenerate the General Summary from the PAYLOAD in the prompt above. Use only payload-supported facts and keep the same output format: plain prose only."""


_GS_FACT_CHECK_SCHEMA = {
    "type": "object",
    "properties": {
        "unsupported_claims": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {"claim": {"type": "string"}, "reason": {"type": "string"}},
                "required": ["claim", "reason"],
                "additionalProperties": False,
            },
        },
    },
    "required": ["unsupported_claims"],
    "additionalProperties": False,
}

_GS_REPAIR_SCHEMA = {
    "type": "object",
    "properties": {
        "replacements": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "claim_id": {"type": "string"},
                    "original_claim": {"type": "string"},
                    "replacement": {"type": "string"},
                },
                "required": ["claim_id", "original_claim", "replacement"],
                "additionalProperties": False,
            },
        },
    },
    "required": ["replacements"],
    "additionalProperties": False,
}


def _gs_claim_id(claim: str) -> str:
    normalized = re.sub(r"\s+", " ", str(claim or "").strip().lower())
    return "GS-" + hashlib.sha256(normalized.encode("utf-8")).hexdigest()[:12].upper()


def _apply_general_summary_replacements(summary_text: str, replacements: list[dict]) -> tuple[str, list[str]]:
    corrected = summary_text
    applied = []
    for item in replacements or []:
        original = re.sub(r"\s+", " ", str(item.get("original_claim") or "")).strip()
        replacement = str(item.get("replacement") or "").strip()
        if not original:
            continue
        pattern = re.escape(original).replace(r"\ ", r"\s+")
        corrected, count = re.subn(pattern, replacement, corrected, flags=re.IGNORECASE)
        if count:
            applied.append(str(item.get("claim_id") or _gs_claim_id(original)))
    corrected = re.sub(r"\s+([,.;:!?])", r"\1", corrected)
    corrected = re.sub(r"[ \t]{2,}", " ", corrected)
    corrected = re.sub(r"\n[ \t]+", "\n", corrected)
    corrected = re.sub(r"\n{3,}", "\n\n", corrected)
    return corrected.strip(), list(dict.fromkeys(applied))


def fact_check_general_summary(summary_text: str, full_data: dict, company_name: str, time_budget_seconds: int | None = None) -> dict:
    """Verify, locally repair, and recheck the General Summary."""
    if not summary_text:
        return {"verified": True, "false_claims": [], "corrected_summary": "",
                "corrected_model": "", "check_time_ms": 0, "repaired_claim_ids": [],
                "unresolved_claims": [], "initial_verification_status": "verified",
                "final_verification_status": "verified"}

    t0 = time.time()
    deadline = t0 + max(15, int(time_budget_seconds or 80))

    def remaining() -> int:
        return max(0, int(deadline - time.time()))

    try:
        payload = _build_input_payload(full_data, company_name)
    except Exception as exc:
        return {"verified": False, "false_claims": [], "corrected_summary": "", "corrected_model": "",
                "check_time_ms": 0, "fact_check_skipped": True, "error": str(exc),
                "repaired_claim_ids": [], "unresolved_claims": [],
                "initial_verification_status": "unavailable", "final_verification_status": "unavailable"}

    def detect(text: str) -> tuple[list[dict] | None, str]:
        prompt = _build_fact_check_prompt(text, payload, company_name)
        for model_id, provider in ((CLAUDE_SONNET_MODEL, "anthropic"), (GPT_VERIFIER_MODEL, "openai")):
            if remaining() < 8:
                break
            try:
                timeout = min(30, max(7, remaining() - 2))
                print(f"   🔍 [GS Fact-Check] {model_id} checking claims against payload...")
                if provider == "anthropic":
                    raw = call_anthropic_text(
                        prompt, model=model_id, max_tokens=3000, timeout=timeout,
                        max_retries=0,
                        reasoning_effort="low", json_schema=_GS_FACT_CHECK_SCHEMA,
                        json_schema_name="general_summary_fact_check", log_label="GS Fact-Check",
                    )
                else:
                    raw = call_openai_text(
                        prompt, model=model_id, max_output_tokens=3000, timeout=timeout,
                        max_retries=0,
                        reasoning_effort="low", text_verbosity="low", json_schema=_GS_FACT_CHECK_SCHEMA,
                        json_schema_name="general_summary_fact_check", log_label="GS Fact-Check Fallback",
                    )
                parsed = _extract_json(raw)
                if not isinstance(parsed, dict):
                    raise ValueError("No schema-valid fact-check object")
                claims = [item for item in (parsed.get("unsupported_claims") or []) if isinstance(item, dict)]
                return claims, model_id
            except Exception as exc:
                print(f"   ⚠️ [GS Fact-Check] {model_id} failed: {exc}")
        return None, ""

    false_claims, detector_model = detect(summary_text)
    if false_claims is None:
        elapsed = int((time.time() - t0) * 1000)
        return {"verified": False, "false_claims": [], "corrected_summary": "", "corrected_model": "",
                "check_time_ms": elapsed, "fact_check_skipped": True, "repaired_claim_ids": [],
                "unresolved_claims": [], "initial_verification_status": "unavailable",
                "final_verification_status": "unavailable"}

    for phrase in _internal_process_phrases(summary_text):
        false_claims.append({
            "claim": phrase,
            "reason": "General Summary must describe the company, not Scanner process state.",
        })
    for item in false_claims:
        item["claim_id"] = _gs_claim_id(item.get("claim") or "")

    if not false_claims:
        elapsed = int((time.time() - t0) * 1000)
        return {"verified": True, "false_claims": [], "corrected_summary": "", "corrected_model": "",
                "check_time_ms": elapsed, "repaired_claim_ids": [], "unresolved_claims": [],
                "initial_verification_status": "verified", "final_verification_status": "verified",
                "verification_model": detector_model}

    corrected_summary = ""
    corrected_model = ""
    repaired_claim_ids = []
    repair_prompt = f"""You are making narrow factual corrections to a General Summary.
Use only the supplied payload. Return one replacement for every claim ID. A replacement may be empty when the unsupported sentence must be removed. Do not add analysis, recommendations, causes, or new facts.

PAYLOAD:
{json.dumps(payload, ensure_ascii=False)}

SUMMARY:
{summary_text}

UNSUPPORTED CLAIMS:
{json.dumps(false_claims, ensure_ascii=False)}
"""
    if remaining() >= 10:
        for model_id, provider in ((CLAUDE_SONNET_MODEL, "anthropic"), (GPT_VERIFIER_MODEL, "openai")):
            if remaining() < 8:
                break
            try:
                timeout = min(25, max(7, remaining() - 2))
                if provider == "anthropic":
                    raw = call_anthropic_text(
                        repair_prompt, model=model_id, max_tokens=3500, timeout=timeout,
                        max_retries=0,
                        reasoning_effort="medium", json_schema=_GS_REPAIR_SCHEMA,
                        json_schema_name="general_summary_sentence_repair", log_label="GS Sentence Repair",
                    )
                else:
                    raw = call_openai_text(
                        repair_prompt, model=model_id, max_output_tokens=3500, timeout=timeout,
                        max_retries=0,
                        reasoning_effort="medium", text_verbosity="low", json_schema=_GS_REPAIR_SCHEMA,
                        json_schema_name="general_summary_sentence_repair", log_label="GS Sentence Repair Fallback",
                    )
                parsed = _extract_json(raw)
                corrected_summary, repaired_claim_ids = _apply_general_summary_replacements(
                    summary_text, (parsed or {}).get("replacements") or []
                )
                if not repaired_claim_ids:
                    raise ValueError("No flagged sentence was repaired")
                corrected_model = model_id
                break
            except Exception as exc:
                print(f"   ⚠️ [GS Sentence Repair] {model_id} failed: {exc}")

    unresolved_claims = false_claims
    final_status = "failed"
    if corrected_summary and remaining() >= 7:
        recheck, recheck_model = detect(corrected_summary)
        if recheck is not None:
            unresolved_claims = recheck
            final_status = "verified_after_repair" if not recheck else "failed_after_repair"
            if recheck_model:
                corrected_model = f"{corrected_model}+{recheck_model}"

    if corrected_summary and unresolved_claims and (_word_count(corrected_summary) < 220 or corrected_summary.count("\n\n") < 2) and remaining() >= 12:
        try:
            regenerated = _strip_links(call_openai_text(
                _build_regen_prompt(payload, company_name, corrected_summary, unresolved_claims),
                model=GPT_REASONING_MODEL, max_output_tokens=5000, timeout=min(20, remaining()),
                max_retries=0,
                reasoning_effort="medium", text_verbosity="medium", log_label="GS Focused Reanalysis",
            ))
            if regenerated:
                corrected_summary = regenerated
                corrected_model = GPT_REASONING_MODEL
                recheck, _ = detect(corrected_summary) if remaining() >= 7 else (unresolved_claims, "")
                if recheck is not None:
                    unresolved_claims = recheck
                    final_status = "verified_after_repair" if not recheck else "failed_after_repair"
        except Exception as exc:
            print(f"   ⚠️ [GS Focused Reanalysis] Failed: {exc}")

    elapsed = int((time.time() - t0) * 1000)
    return {
        "verified": final_status == "verified_after_repair",
        "false_claims": false_claims,
        "corrected_summary": corrected_summary,
        "corrected_model": corrected_model,
        "check_time_ms": elapsed,
        "repaired_claim_ids": repaired_claim_ids,
        "unresolved_claims": unresolved_claims,
        "initial_verification_status": "failed",
        "final_verification_status": final_status,
        "verification_model": detector_model,
    }
