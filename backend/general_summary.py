"""
general_summary.py — Plain-language General Summary generator.

Assembles a 275-325 word narrative from already-computed structured scan data
(verified evidence links, jury findings, financial findings, delta).

Written from the perspective of an experienced analyst and executive advisor.
Every factual claim must be backed by an inline citation from the evidence pool.
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

_WORD_MIN = 275
_WORD_MAX = 325


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


def _build_prompt(payload: dict, company_name: str, word_correction: str = "") -> str:
    forbidden = ", ".join(_FORBIDDEN_TERMS)
    correction_note = f"\n\nIMPORTANT CORRECTION FROM PREVIOUS ATTEMPT: {word_correction}" if word_correction else ""
    return f"""You are an experienced analyst and executive advisor with deep expertise in how organisations and companies operate. You know how to communicate findings clearly to C-level executives — direct, unvarnished, without sugarcoating. You highlight what is working, what is not, and what the consequences are if nothing changes. You do not flatter. You do not hedge.

You are writing the General Summary section of a forensic B2B intelligence report about {company_name}.

EVIDENCE — ground every claim you make in the data below. Synthesise and interpret — do not enumerate:
{json.dumps(payload, ensure_ascii=False, indent=2)}
{correction_note}
WRITING APPROACH:
Weave the evidence into a coherent narrative that gives a C-level reader an immediate understanding of where this company stands, what the key signals are, and what is at stake. Connect findings — do not list them. For each significant observation, consider: what does this suggest about the company's trajectory, and what happens if the current direction continues? You are allowed to draw analytical conclusions as long as every factual claim cites a source URL.

MANDATORY RULES:

1. WORD COUNT: The output MUST be between {_WORD_MIN} and {_WORD_MAX} words. Count every word before submitting. This is non-negotiable.

2. CITATIONS: Every factual claim about the company must be followed by an inline citation in the form [label](url). Use ONLY urls from evidence_links or key_findings in the input. Write a short 2–4 word label of your own — do NOT copy the article title. Good examples: [press release](url), [annual report](url), [company news](url), [strategy page](url). If you cannot cite a url for a claim, do not make that claim. Scores (financial_index, reality_score) do not need citations — they are computed internal metrics.

3. FORBIDDEN TERMS: Do not use any of these: {forbidden}. Use plain descriptive language instead.

4. OPENING SENTENCE — MANDATORY:
   - If delta data is present: open with the trend/change story (e.g. "Since the previous assessment in [date], [company] has...").
   - If no delta: open with the single most significant observed fact from key_findings or evidence_links.
   - NEVER open with a score number. Scores may appear briefly later as supporting context, never as the lead.

5. COVERAGE: If the evidence supports it, address at least 3 distinct dimensions — for example: financial health, operational or technology signals, people and leadership. Do not write the entire summary about a single event.

6. RISKS-IF-NOT-ACTING: Where the evidence points to a gap, a stagnation, or a warning signal, name what is likely to happen if the current trajectory continues. Be direct. Do not soften it.

7. PLAIN BUSINESS ENGLISH: No superlatives. No marketing language. No hedging phrases like "it appears that" or "it seems". State findings and implications directly.

8. FORMAT: Plain prose only. No headers, no bullet points, no markdown except the inline [label](url) citation links.

Output only the prose text. Nothing else."""


def _word_count(text: str) -> int:
    return len(text.split())


def generate_general_summary(full_data: dict, company_name: str) -> tuple[str, str]:
    """
    Generate a 275-325 word General Summary from structured scan data.

    Returns (summary_text, model_used).
    Returns ("", "") on failure — caller should catch exceptions separately.
    """
    gemini_key = os.environ.get("GEMINI_API_KEY") or os.environ.get("API_KEY")
    if not gemini_key:
        print("   ⚠️ [General Summary] No GEMINI_API_KEY — skipping")
        return "", ""

    payload = _build_input_payload(full_data, company_name)

    # Per-model configs.
    # gemini-2.5-pro: mandatory thinking model — thinking_budget (128-32768).
    #   thinking tokens are separate from max_output_tokens for this model, so
    #   1200 output tokens (~900 words) is ample for the 275-325 word target.
    #   8192 thinking budget is appropriate for multi-rule prose generation.
    # gemini-3.1-pro-preview: thinking tokens are drawn from the same
    #   max_output_tokens pool, so the budget must comfortably exceed
    #   thinking_budget + target_output (2048 thinking + ~500 visible = 2548).
    #   4096 gives a safe margin. temperature MUST be 1.0 on 3.x models.
    models = [
        (
            "gemini-2.5-pro",
            genai_types.GenerateContentConfig(
                temperature=0.5,
                max_output_tokens=2000,
                thinking_config=genai_types.ThinkingConfig(thinking_budget=8192),
            ),
            genai_types.GenerateContentConfig(
                temperature=0.5,
                max_output_tokens=2000,
                thinking_config=genai_types.ThinkingConfig(thinking_budget=8192),
            ),
        ),
        (
            "gemini-3.1-pro-preview",
            genai_types.GenerateContentConfig(
                temperature=1.0,
                max_output_tokens=4096,
                thinking_config=genai_types.ThinkingConfig(thinking_budget=2048),
            ),
            genai_types.GenerateContentConfig(
                temperature=1.0,
                max_output_tokens=4096,
                thinking_config=genai_types.ThinkingConfig(thinking_budget=1024),
            ),
        ),
    ]

    for model_id, first_config, retry_config in models:
        try:
            client = genai.Client(api_key=gemini_key)
            t0 = time.time()

            prompt = _build_prompt(payload, company_name)
            response = client.models.generate_content(
                model=model_id,
                contents=prompt,
                config=first_config,
            )
            text = (response.text or "").strip()
            wc = _word_count(text)
            elapsed = int((time.time() - t0) * 1000)
            print(f"   [General Summary] {model_id}: {wc} words ({elapsed}ms)")

            # If outside range, one retry with explicit correction note
            if not (_WORD_MIN <= wc <= _WORD_MAX):
                if wc < _WORD_MIN:
                    correction = f"Your previous output was {wc} words — too short. You must reach at least {_WORD_MIN} words. Expand with additional cited evidence from the input."
                else:
                    correction = f"Your previous output was {wc} words — too long. You must cut down to at most {_WORD_MAX} words. Remove the least important sentences."

                retry_prompt = _build_prompt(payload, company_name, word_correction=correction)
                retry_response = client.models.generate_content(
                    model=model_id,
                    contents=retry_prompt,
                    config=retry_config,
                )
                retry_text = (retry_response.text or "").strip()
                retry_wc = _word_count(retry_text)
                print(f"   [General Summary] {model_id} retry: {retry_wc} words")
                # Use retry result if it's at least closer to range (or inside it)
                if abs(retry_wc - 300) < abs(wc - 300):
                    text = retry_text
                    wc = retry_wc

            if text:
                return text, model_id

        except Exception as e:
            print(f"   ❌ [General Summary] {model_id} failed: {e}")
            continue

    return "", ""
