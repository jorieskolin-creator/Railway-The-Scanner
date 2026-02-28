"""
general_summary.py — Plain-language General Summary generator.

Assembles a 275-325 word narrative from already-computed structured scan data
(scores, verified evidence links, jury findings, financial sub-scores, delta).

No Knowledge Base terminology. No interpretation beyond the data.
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

    # Verified evidence links only (max 14)
    verified_links = [
        {"title": l.get("title", "Source"), "url": l.get("url", ""), "snippet": (l.get("snippet") or "")[:180]}
        for l in full_data.get("evidence_links", [])
        if l.get("verified", True) and l.get("url")
    ][:14]

    # Key jury findings that have a URL (max 8)
    key_findings = [
        {
            "description": item.get("description") or item.get("category", ""),
            "title": item.get("title") or item.get("category", ""),
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

    return {
        "company_name": company_name,
        "reality_score": full_data.get("reality_score", 0),
        "intent_score": full_data.get("intent_score", 0),
        "financial_index": full_data.get("financial_index"),
        "financial_sub_scores": full_data.get("financial_sub_scores") or {},
        "confidence_score": full_data.get("confidence_score", 0),
        "delta": delta,
        "key_findings": key_findings,
        "evidence_links": verified_links,
        "financial_findings": fin_findings,
        "key_people": people,
    }


def _build_prompt(payload: dict, company_name: str, word_correction: str = "") -> str:
    forbidden = ", ".join(_FORBIDDEN_TERMS)
    correction_note = f"\n\nIMPORTANT CORRECTION FROM PREVIOUS ATTEMPT: {word_correction}" if word_correction else ""
    return f"""You are writing the General Summary section of a B2B intelligence report about {company_name}.

INPUT DATA — use ONLY these facts. Do not infer, assume, or add anything beyond what is provided:
{json.dumps(payload, ensure_ascii=False, indent=2)}
{correction_note}
RULES (all are mandatory):

1. WORD COUNT: The output MUST be between {_WORD_MIN} and {_WORD_MAX} words. Count every word before submitting. This is non-negotiable.

2. CITATIONS: Every factual claim about the company must end with [Title](url), using ONLY urls from evidence_links or key_findings in the input. If you cannot cite a url for a claim, do not make that claim. Scores (reality_score, intent_score, financial_index, confidence_score) do not need citations — they are computed internal metrics, not web claims.

3. FORBIDDEN TERMS: Do not use any of these: {forbidden}. Use plain descriptive language instead.

4. FACTS ONLY: Report what was observed. Do not interpret what findings mean strategically.

5. OPENING SENTENCE — MANDATORY:
   - If delta data is present: open with the trend/change story (e.g. "Since the previous assessment in [date], [company] has...").
   - If no delta: open with the single most significant observed fact from key_findings or evidence_links (e.g. "[Company] operates a ...").
   - NEVER open with a score number. Sentences like "X has a reality score of 45" or "The intent score is 37" must NOT appear anywhere near the opening. Scores may be mentioned briefly later, embedded in context, not as standalone statements.

6. PLAIN BUSINESS ENGLISH: No superlatives. No marketing language. No hedging phrases like "it appears that" or "it seems". State facts directly.

7. FORMAT: Plain prose only. No headers, no bullet points, no markdown formatting except the inline [Title](url) citation links.

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
    # gemini-2.5-pro:      thinking disabled (budget=0) so all tokens go to visible
    #                      output; temperature=0.3 for controlled prose.
    # gemini-3.1-pro-preview: temperature MUST be 1.0 (low temp causes output loops
    #                      on 3.x models); small thinking budget for grounding.
    models = [
        (
            "gemini-2.5-pro",
            genai_types.GenerateContentConfig(
                temperature=0.3,
                max_output_tokens=1200,
                thinking_config=genai_types.ThinkingConfig(thinking_budget=0),
            ),
            genai_types.GenerateContentConfig(
                temperature=0.2,
                max_output_tokens=1200,
                thinking_config=genai_types.ThinkingConfig(thinking_budget=0),
            ),
        ),
        (
            "gemini-3.1-pro-preview",
            genai_types.GenerateContentConfig(
                temperature=1.0,
                max_output_tokens=1200,
                thinking_config=genai_types.ThinkingConfig(thinking_budget=2048),
            ),
            genai_types.GenerateContentConfig(
                temperature=1.0,
                max_output_tokens=1200,
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
