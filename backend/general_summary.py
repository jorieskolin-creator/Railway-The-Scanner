"""
general_summary.py — Plain-language General Summary generator.

Assembles a 225-275 word narrative from already-computed structured scan data
(verified evidence links, jury findings, financial findings, delta).

Written from the perspective of an experienced analyst and executive advisor.
Key facts are anchored with citations; synthesis prose connecting evidence
does not require individual citations.
"""

import json
import os
import time

from google import genai
from google.genai import types as genai_types


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

    # Verified evidence links only (max 14).
    # Titles truncated to 55 chars to keep the payload compact and to prevent
    # the model from copying verbose article titles verbatim into citation labels.
    verified_links = [
        {
            "title": (l.get("title", "Source") or "Source")[:55],
            "url": l.get("url", ""),
            "snippet": (l.get("snippet") or "")[:180],
        }
        for l in full_data.get("evidence_links", [])
        if l.get("verified", True) and l.get("url")
    ][:14]

    # Key jury findings that have a URL (max 8).
    # Same 55-char title cap for consistency.
    key_findings = [
        {
            "description": item.get("description") or item.get("category", ""),
            "title": ((item.get("title") or item.get("category", "")) or "")[:55],
            "url": item.get("url", ""),
        }
        for item in full_data.get("reality_checklist", [])
        if item.get("found") and item.get("url")
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
    return f"""You are an experienced analyst and executive advisor with deep expertise in how organisations and companies operate. You know how to communicate findings clearly to C-level executives — direct, unvarnished, without sugarcoating. You highlight what is working, what is not, and what the consequences are if nothing changes. You do not flatter. You do not hedge.

You are writing the General Summary section of a forensic B2B intelligence report about {company_name}.

EVIDENCE — ground every claim you make in the data below. Synthesise and interpret — do not enumerate:
{json.dumps(payload, ensure_ascii=False, indent=2)}

WRITING APPROACH:
Weave the evidence into a coherent narrative that gives a C-level reader an immediate understanding of where this company stands, what the key signals are, and what is at stake. Connect findings — do not list them. For each significant observation, consider what it suggests about the company's trajectory and what happens if the current direction continues. Draw analytical conclusions — that is your job.

MANDATORY RULES:

1. WORD COUNT: The output MUST be between {_WORD_MIN} and {_WORD_MAX} words. Count carefully. This is non-negotiable.

2. CITATIONS: Anchor key specific facts — numbers, named events, named people, product names, financial figures — with an inline [label](url) citation using ONLY urls from evidence_links or key_findings. Write a short 2–4 word label of your own (e.g. [annual report](url), [press release](url)). Synthesis sentences that connect or interpret cited evidence do NOT need individual citations. Include between 3 and 8 citations total. Do not cite scores (financial_index, reality_score) — they are computed metrics, not web sources.

3. FORBIDDEN TERMS: Do not use any of these: {forbidden}. Use plain descriptive language instead.

4. OPENING SENTENCE — MANDATORY:
   - If delta data is present: open with the trend/change story (e.g. "Since the previous assessment in [date], [company] has...").
   - If no delta: open with the single most significant observed fact from key_findings or evidence_links.
   - NEVER open with a score number. Scores may appear briefly later as supporting context, never as the lead.

5. COVERAGE: Address at least 3 distinct dimensions if the evidence supports them — for example: financial health, operational or technology signals, people and leadership. Do not write the entire summary about a single event.

6. RISKS-IF-NOT-ACTING: Where the evidence points to a gap, stagnation, or warning signal, name what is likely to happen if the current trajectory continues. Be direct. Do not soften it.

7. PLAIN BUSINESS ENGLISH: No superlatives. No marketing language. No hedging phrases like "it appears that" or "it seems". State findings and implications directly.

8. PARAGRAPHS: Structure the output as exactly 3-4 paragraphs, each covering a distinct dimension. Separate paragraphs with a blank line. This is required for readability.

9. FORMAT: Plain prose only. No headers, no bullet points, no markdown except the inline [label](url) citation links and the blank lines between paragraphs.

Output only the prose text. Nothing else."""


def _word_count(text: str) -> int:
    return len(text.split())


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

    # gemini-3.1-pro-preview is the sole model for General Summary.
    # gemini-2.5-pro was removed: with thinking_budget=8192 it consistently
    # exhausts its thinking budget on the long evidence payload and returns
    # response.text = None (0 visible words) every time.
    #
    # gemini-3.1-pro-preview: thinking tokens and output tokens share the same
    # max_output_tokens pool. Budget: 6000 total, 2048 thinking → ~3952 visible
    # (~3000 words). The 225-275 word target needs ~400 visible tokens — ample.
    # temperature=1.0 is required on 3.x models (low temp causes output loops).
    #
    # Retry uses thinking_budget=512 to leave the maximum visible token room,
    # since on a retry the model has already failed once and over-thinking is
    # more likely to repeat the same mistake than fix it.
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
    MODEL = "gemini-3.1-pro-preview"

    client = genai.Client(api_key=gemini_key)

    for attempt, config in enumerate([FIRST_CONFIG, RETRY_CONFIG], start=1):
        try:
            t0 = time.time()
            response = client.models.generate_content(
                model=MODEL,
                contents=prompt,
                config=config,
            )
            text = (response.text or "").strip()
            wc = _word_count(text)
            elapsed = int((time.time() - t0) * 1000)
            print(f"   [General Summary] {MODEL} attempt {attempt}: {wc} words ({elapsed}ms)")

            if _WORD_MIN <= wc <= _WORD_MAX:
                return text, MODEL

            if attempt == 1:
                print(f"   [General Summary] {wc} words outside [{_WORD_MIN}-{_WORD_MAX}] — retrying")
                continue

            # Second attempt: use whatever we got if it's non-empty
            if text:
                print(f"   [General Summary] Using attempt 2 result ({wc} words) — outside range but non-empty")
                return text, MODEL

        except Exception as e:
            print(f"   ❌ [General Summary] {MODEL} attempt {attempt} failed: {e}")
            if attempt == 2:
                break
            continue

    return "", ""
