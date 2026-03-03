"""
general_summary.py — Plain-language General Summary generator.

Assembles a 225-275 word narrative from already-computed structured scan data
(jury findings, financial findings, delta). No inline links are included —
the General Summary is plain prose only; all evidence links are already shown
in the AI Diagnosis section of the UI.
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
    verified_links = [
        {
            "title": (l.get("title", "Source") or "Source")[:55],
            "snippet": (l.get("snippet") or "")[:180],
        }
        for l in full_data.get("evidence_links", [])
        if l.get("verified", True) and l.get("url") and not _is_legal_page(l.get("url", ""))
    ][:14]

    # Key jury findings — descriptions only, no URLs, no legal pages.
    key_findings = [
        {
            "description": item.get("description") or item.get("category", ""),
            "title": ((item.get("title") or item.get("category", "")) or "")[:55],
        }
        for item in full_data.get("reality_checklist", [])
        if item.get("found") and item.get("url") and not _is_legal_page(item.get("url", ""))
    ][:8]

    # Financial findings (plain strings, max 3)
    fin_findings = [str(f) for f in (full_data.get("financial_findings") or [])[:3]]

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
    result: dict = {
        "company_name": company_name,
        "delta": delta,
        "financial_index": full_data.get("financial_index"),
        "key_findings": key_findings,
        "evidence_links": verified_links,
        "financial_findings": fin_findings,
        "key_people": people,
    }
    if delta:
        result["reality_score"] = full_data.get("reality_score", 0)
    return result


def _build_prompt(payload: dict, company_name: str) -> str:
    forbidden = ", ".join(_FORBIDDEN_TERMS)
    return f"""You are an experienced analyst and executive advisor with deep expertise in how organisations and companies operate. You communicate findings clearly to C-level executives — objective, balanced, and strictly data-driven. You highlight what is working, and, if explicitly supported by the data, what is not. You highlight operational realities without sugarcoating, but you do not force a pessimistic narrative. Your analysis must be grounded in facts, not hyperbole.

You are writing the General Summary section of a forensic B2B intelligence report about {company_name}.

EVIDENCE — ground every claim you make in the data below. Synthesise and interpret — do not enumerate:
{json.dumps(payload, ensure_ascii=False, indent=2)}

WRITING APPROACH:
Weave the evidence into a coherent narrative that gives a C-level reader an immediate understanding of where this company stands, what the key signals are, and what is at stake. Connect findings — do not list them. For each significant observation, consider what it suggests about the company's trajectory and what happens if the current direction continues. Draw objective analytical conclusions — that is your job.

MANDATORY RULES:

1. WORD COUNT: The output MUST be between {_WORD_MIN} and {_WORD_MAX} words. Count carefully. This is non-negotiable.

2. FACTUAL DISCIPLINE — CRITICAL:
   - Only write about things that actually happened or currently exist. Do not present hypothetical, conditional, or future-tense scenarios as facts.
   - Terms of Service, legal disclaimers, and privacy policies describe hypothetical situations ("if X happens, Y occurs") — these are NOT evidence of real events. If a snippet sounds like a legal clause, ignore it entirely.
   - Do not infer events from the absence of evidence. If something is not clearly documented as having occurred, do not write that it occurred.
   - Never invent, combine, or rename financial metrics.

3. DATA HANDLING / META-METRICS: Do not reference the report's internal scoring metrics (e.g., "Reality Score," "Ambition Score," "Friction Index," "Integrity Delta") by name in the generated prose. These scores should inform your overall analytical tone, but the summary itself must focus strictly on the company's real-world financial, operational, and organizational data.

4. FORBIDDEN TERMS: Do not use any of these: {forbidden}. Use plain descriptive language instead.

5. OPENING SENTENCE — MANDATORY:
   - If delta data is present: open with the trend/change story (e.g. "Since the previous assessment in [date], [company] has...").
   - If no delta: open with the single most significant observed fact from key_findings or evidence_links.
   - Never open with a number.

6. COVERAGE: Address at least 3 distinct dimensions if the evidence supports them — for example: financial health, operational or technology signals, people and leadership. Do not write the entire summary about a single event.

7. OBJECTIVE RISK ASSESSMENT: If the data reveals negative trends or vulnerabilities, name the consequences directly. If the data reflects standard business practices (e.g., strategic outsourcing, white-labeling), do not manufacture artificial crises. Only highlight risks if they are explicitly supported by the evidence.

8. PLAIN BUSINESS ENGLISH: No superlatives. No marketing language. No hedging phrases like "it appears that" or "it seems". State findings and implications directly.

9. PARAGRAPHS: Structure the output as exactly 3-4 paragraphs, each covering a distinct dimension. Separate paragraphs with a blank line. This is required for readability.

10. FORMAT: Plain prose only. No headers, no bullet points, no markdown, no links, no citations. Blank lines between paragraphs only.

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
    gemini_key = os.environ.get("GEMINI_API_KEY") or os.environ.get("API_KEY")
    if not gemini_key:
        print("   ⚠️ [General Summary] No GEMINI_API_KEY — skipping")
        return "", ""

    payload = _build_input_payload(full_data, company_name)
    prompt = _build_prompt(payload, company_name)

    # Primary model: gemini-3.1-pro-preview (2 attempts)
    # gemini-2.5-pro was originally removed because thinking_budget=8192 caused
    # it to exhaust thinking and return response.text=None (0 visible words).
    # It is now reinstated as a backup-only model with thinking_budget=1024 +
    # max_output_tokens=8000 — the same fix that resolved the fact-check token
    # starvation issue. At 1024 thinking tokens, ~7000 tokens remain for output,
    # which is far more than the ~400 tokens needed for 225-275 words.
    # gemini-3-pro-preview removed: shares 3.x rate limits with 3.1-pro (useless
    # as fallback during overload) and is being deprecated by Google.
    PRIMARY_MODEL = "gemini-3.1-pro-preview"
    BACKUP_MODEL  = "gemini-2.5-pro"

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
    BACKUP_CONFIG = genai_types.GenerateContentConfig(
        temperature=0.5,
        max_output_tokens=8000,
        thinking_config=genai_types.ThinkingConfig(thinking_budget=1024),
    )

    client = genai.Client(api_key=gemini_key)

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

    # Attempt 3: backup model (gemini-2.5-pro) — separate rate limits from 3.x family
    print(f"   [General Summary] Primary model failed — trying backup {BACKUP_MODEL}")
    try:
        t0 = time.time()
        response = call_with_503_backoff(
            lambda: client.models.generate_content(
                model=BACKUP_MODEL,
                contents=prompt,
                config=BACKUP_CONFIG,
            ),
            label=f"General Summary {BACKUP_MODEL}",
            model=BACKUP_MODEL,
            timeout=90,
        )
        text = _strip_links((response.text or "").strip())
        wc = _word_count(text)
        elapsed = int((time.time() - t0) * 1000)
        print(f"   [General Summary] {BACKUP_MODEL} backup: {wc} words ({elapsed}ms)")
        if text:
            return text, BACKUP_MODEL
    except Exception as e:
        print(f"   ❌ [General Summary] {BACKUP_MODEL} backup failed: {e}")

    return "", ""
