"""
contradiction_detector.py — Surface contradicting claims across evidence items.

The General Summary and Detailed Forensic Report receive a list of evidence
snippets and decide which to weight. When two snippets disagree quantitatively
on the same topic — for example, a master's thesis reporting "9% of HR
personnel use WärtsiläGPT regularly" versus a company interview citing
"thousands of weekly users" — the model has historically silently picked one
side, usually the more dramatic narrow-scope finding.

This module runs a small Gemini Flash pass over the evidence list and returns
a structured list of contradictions. The summary builders then pass this list
into the prompt under a "contradictions" field; the SCOPE QUALIFICATION rule
already requires the model to address both sides explicitly when listed.

Failure-safe: any error in the Flash call returns an empty list so summary
generation is never blocked by a missing contradiction pass.
"""

import json
import os
import re

from gemini_backoff import call_with_503_backoff
from gemini_client import get_gemini_client


# Gemini Flash is plenty for this short, structured task. Stays cheap.
_MODEL = "gemini-2.5-flash"

# Hard caps to keep the prompt small and the latency low.
_MAX_EVIDENCE_ITEMS = 25
_MAX_SNIPPET_CHARS = 350
_MAX_CONTRADICTIONS = 6


def _extract_json(text: str) -> dict | None:
    """Pull the first valid JSON object out of a model response."""
    if not text:
        return None
    # Direct parse first.
    try:
        return json.loads(text)
    except (json.JSONDecodeError, TypeError):
        pass
    # Fall back to greedy outer-braces match.
    match = re.search(r"\{[\s\S]*\}", text)
    if not match:
        return None
    try:
        return json.loads(match.group(0))
    except (json.JSONDecodeError, TypeError):
        return None


def _build_prompt(evidence_items: list, company_name: str) -> str:
    """Compact prompt that asks Gemini to surface quantitative disagreements.

    The output schema is intentionally narrow: topic + the two competing
    claims + their source titles + their scope flags. The summary writer
    consumes this verbatim, so we don't ask the model for prose.
    """
    # Trim each snippet so the prompt stays small. Preserve scope so the
    # detector knows when one side is narrow and the other is broad.
    compact = []
    for i, ev in enumerate(evidence_items[:_MAX_EVIDENCE_ITEMS]):
        compact.append({
            "id": i,
            "title": (ev.get("title") or "")[:80],
            "snippet": (ev.get("snippet") or "")[:_MAX_SNIPPET_CHARS],
            "scope": ev.get("scope", "broad"),
        })

    return f"""You are scanning a list of evidence snippets about {company_name} for QUANTITATIVE CONTRADICTIONS — pairs of claims about the same topic that report different numbers, percentages, adoption rates, scale, or extent.

EVIDENCE ITEMS:
{json.dumps(compact, ensure_ascii=False, indent=2)}

Find pairs (or small groups) where two evidence items make conflicting numeric / scale claims about the SAME underlying topic. Examples of a contradiction worth flagging:
- One source says "9% of HR personnel use Tool X regularly", another says "Tool X has thousands of weekly users company-wide"
- One source says "pilot at one site", another says "deployed across all factories"
- One source says "5% revenue from segment Y", another says "segment Y is 30% of revenue"

Do NOT flag:
- Claims about different topics that merely sound similar
- Updates over time where the older one is clearly superseded (treat as evolution, not contradiction)
- Differences in framing or sentiment without a numeric or scale disagreement

For each real contradiction, output the topic, the two competing claims (paraphrased into one short sentence each), the title of each source, and the scope flag of each source ("narrow" or "broad").

Return at most {_MAX_CONTRADICTIONS} contradictions, ranked by importance (the ones a reader would most need to know about). Output VALID JSON only, no markdown, no commentary.

OUTPUT SCHEMA:
{{"contradictions": [
  {{
    "topic": "Short topic label (e.g. 'WärtsiläGPT adoption')",
    "claim_a": "First side (one sentence)",
    "source_a_title": "Source title for claim A",
    "scope_a": "narrow" | "broad",
    "claim_b": "Second side (one sentence)",
    "source_b_title": "Source title for claim B",
    "scope_b": "narrow" | "broad"
  }}
]}}

If no real contradictions exist, output: {{"contradictions": []}}"""


def detect_contradictions(evidence_items: list, company_name: str = "") -> list:
    """Return a list of contradiction objects, or [] when none / on failure.

    Each contradiction has the schema documented in _build_prompt(). The
    summary builders pass this list into their LLM prompt; the SCOPE
    QUALIFICATION rule already instructs the model to address both sides.

    Always safe to call: any failure (no API key, bad response, network)
    returns an empty list and lets summary generation proceed.
    """
    if not evidence_items:
        return []
    if not (os.environ.get("GEMINI_API_KEY") or os.environ.get("API_KEY")):
        return []

    try:
        client = get_gemini_client()
    except Exception as e:
        print(f"   [Contradictions] Gemini client unavailable: {e}")
        return []

    prompt = _build_prompt(evidence_items, company_name or "the company")

    try:
        response = call_with_503_backoff(
            lambda: client.models.generate_content(
                model=_MODEL,
                contents=prompt,
            ),
            max_retries=2,
        )
    except Exception as e:
        print(f"   [Contradictions] Detection call failed: {e}")
        return []

    text = (getattr(response, "text", None) or "").strip()
    parsed = _extract_json(text)
    if not parsed or not isinstance(parsed, dict):
        return []

    items = parsed.get("contradictions") or []
    if not isinstance(items, list):
        return []

    # Defensive shape-cleaning — only keep entries with all required fields,
    # so the prompt template can rely on them.
    cleaned = []
    for it in items[:_MAX_CONTRADICTIONS]:
        if not isinstance(it, dict):
            continue
        required = ("topic", "claim_a", "source_a_title", "claim_b", "source_b_title")
        if not all(it.get(k) for k in required):
            continue
        cleaned.append({
            "topic": str(it["topic"])[:120],
            "claim_a": str(it["claim_a"])[:280],
            "source_a_title": str(it["source_a_title"])[:120],
            "scope_a": "narrow" if str(it.get("scope_a", "")).lower() == "narrow" else "broad",
            "claim_b": str(it["claim_b"])[:280],
            "source_b_title": str(it["source_b_title"])[:120],
            "scope_b": "narrow" if str(it.get("scope_b", "")).lower() == "narrow" else "broad",
        })

    if cleaned:
        print(f"   [Contradictions] Surfaced {len(cleaned)} contradiction(s) for {company_name or 'the company'}")
    return cleaned
