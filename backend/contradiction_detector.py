"""
contradiction_detector.py — Surface contradicting claims across evidence items.

Phase 2 update: returns richer contradiction objects with resolution_rule,
preferred_wording, confidence and severity. It also includes a deterministic
fallback for narrow-vs-broad scale conflicts so the most dangerous
scope-inflation risks are caught even when model verification is unavailable.
"""

from __future__ import annotations

import json
import re
from typing import Any

from llm_clients import call_json_model_chain
from model_policy import CLAUDE_SONNET_MODEL, GPT_VERIFIER_MODEL

_MAX_EVIDENCE_ITEMS = 25
_MAX_SNIPPET_CHARS = 350
_MAX_CONTRADICTIONS = 6


def _extract_json(text: str) -> dict | None:
    """Pull the first valid JSON object out of a model response."""
    if not text:
        return None
    try:
        return json.loads(text)
    except (json.JSONDecodeError, TypeError):
        pass
    match = re.search(r"\{[\s\S]*\}", text)
    if not match:
        return None
    try:
        return json.loads(match.group(0))
    except (json.JSONDecodeError, TypeError):
        return None


def _normalize_scope(scope: str | None) -> str:
    scope = (scope or "broad").strip().lower()
    return "narrow" if scope in {"narrow", "department", "project", "pilot", "region", "team"} else "broad"


def _safe_title(ev: dict) -> str:
    return (ev.get("title") or ev.get("source_title") or ev.get("url") or "Unknown source")[:120]


def _text_for_matching(ev: dict) -> str:
    return f"{ev.get('title','')} {ev.get('snippet','')} {ev.get('evidence_snippet','')}".lower()


def _looks_like_scale_claim(text: str) -> bool:
    return bool(re.search(
        r"\b(\d+\s*%|\d+[,.]?\d*\s*(?:users|employees|factories|sites|countries|locations)|company[-\s]?wide|enterprise[-\s]?wide|broad deployment|all factories|pilot|one team|department|hr personnel|weekly users|thousands)\b",
        text,
        re.I,
    ))


def _topic_keywords(text: str) -> set[str]:
    topics = {
        "gen_ai_adoption": ["generative ai", "generative-ai", "gpt", "copilot", "ai adoption", "ai readiness", "weekly users"],
        "multi_agent_deployment": ["multi-agent", "agentic", "autonomous agents", "agent systems", "supply chain workflow"],
        "advanced_computing_scope": ["lumi", "supercomputer", "openfoam", "cfd", "thermofluids", "simulation"],
        "operating_model": ["safe", "agile", "silo", "operating model", "scrum", "waterfall"],
        "financial_scale": ["net sales", "order book", "order intake", "operating result", "margin", "gearing"],
    }
    return {topic for topic, words in topics.items() if any(w in text for w in words)}


def _preferred_wording(topic: str, a: dict, b: dict) -> str:
    title_a = _safe_title(a)
    title_b = _safe_title(b)
    scope_a = _normalize_scope(a.get("scope"))
    scope_b = _normalize_scope(b.get("scope"))
    topic_label = topic.replace("_", " ")
    if {scope_a, scope_b} == {"narrow", "broad"}:
        return (
            f"Evidence on {topic_label} should be stated with scope: {title_a} is {scope_a}-scope, "
            f"while {title_b} is {scope_b}-scope. Do not turn the narrow finding into a company-wide conclusion; state both sides explicitly."
        )
    return (
        f"Evidence on {topic_label} contains competing scale claims from {title_a} and {title_b}. "
        "State both claims with dates and source scope instead of choosing only one side."
    )


def _normalize_contradiction(item: dict) -> dict | None:
    """Backwards-compatible enrichment of LLM or deterministic contradiction objects."""
    if not isinstance(item, dict):
        return None
    topic = str(item.get("topic") or "Unspecified contradiction")[:120]
    scope_a = _normalize_scope(item.get("scope_a"))
    scope_b = _normalize_scope(item.get("scope_b"))
    return {
        "topic": topic,
        "claim_a": str(item.get("claim_a") or "")[:350],
        "source_a_title": str(item.get("source_a_title") or "Unknown source")[:160],
        "scope_a": scope_a,
        "claim_b": str(item.get("claim_b") or "")[:350],
        "source_b_title": str(item.get("source_b_title") or "Unknown source")[:160],
        "scope_b": scope_b,
        "resolution_rule": item.get("resolution_rule") or "state_both_with_scope",
        "preferred_wording": item.get("preferred_wording") or "State both sides with source scope; do not generalize narrow evidence.",
        "confidence": item.get("confidence") or ("high" if scope_a != scope_b else "medium"),
        "severity": item.get("severity") or ("high" if scope_a != scope_b else "medium"),
    }


def _detect_scope_tensions_deterministic(evidence_items: list) -> list[dict]:
    """Cheap deterministic fallback for narrow-vs-broad scale conflicts."""
    candidates: list[dict] = []
    items = evidence_items[:_MAX_EVIDENCE_ITEMS]
    for i, a in enumerate(items):
        if not isinstance(a, dict):
            continue
        ta = _text_for_matching(a)
        topics_a = _topic_keywords(ta)
        if not topics_a or not _looks_like_scale_claim(ta):
            continue
        for b in items[i + 1:]:
            if not isinstance(b, dict):
                continue
            tb = _text_for_matching(b)
            topics = topics_a & _topic_keywords(tb)
            if not topics or not _looks_like_scale_claim(tb):
                continue
            scope_a = _normalize_scope(a.get("scope"))
            scope_b = _normalize_scope(b.get("scope"))
            narrow_broad = scope_a != scope_b
            scale_words_conflict = (
                (re.search(r"\b(pilot|one team|department|hr personnel|survey|case study)\b", ta) and re.search(r"\b(company[-\s]?wide|enterprise[-\s]?wide|broad deployment|all factories|thousands)\b", tb))
                or (re.search(r"\b(pilot|one team|department|hr personnel|survey|case study)\b", tb) and re.search(r"\b(company[-\s]?wide|enterprise[-\s]?wide|broad deployment|all factories|thousands)\b", ta))
            )
            if not (narrow_broad or scale_words_conflict):
                continue
            topic = sorted(topics)[0]
            candidates.append({
                "topic": topic,
                "claim_a": (a.get("snippet") or a.get("evidence_snippet") or a.get("title") or "")[:260],
                "source_a_title": _safe_title(a),
                "scope_a": scope_a,
                "claim_b": (b.get("snippet") or b.get("evidence_snippet") or b.get("title") or "")[:260],
                "source_b_title": _safe_title(b),
                "scope_b": scope_b,
                "resolution_rule": "state_both_with_scope",
                "preferred_wording": _preferred_wording(topic, a, b),
                "confidence": "medium",
                "severity": "high" if narrow_broad else "medium",
            })
            if len(candidates) >= _MAX_CONTRADICTIONS:
                return candidates
    return candidates


def _build_prompt(evidence_items: list, company_name: str) -> str:
    """Compact prompt that asks a verifier to surface quantitative disagreements."""
    compact = []
    for i, ev in enumerate(evidence_items[:_MAX_EVIDENCE_ITEMS]):
        if not isinstance(ev, dict):
            continue
        compact.append({
            "id": i,
            "title": _safe_title(ev)[:80],
            "snippet": (ev.get("snippet") or ev.get("evidence_snippet") or "")[:_MAX_SNIPPET_CHARS],
            "scope": _normalize_scope(ev.get("scope")),
            "source_type": ev.get("source_type", "unknown"),
            "time_validity": ev.get("time_validity", "unknown"),
            "allowed_claim_types": ev.get("allowed_claim_types", []),
        })

    return f"""You are scanning a list of evidence snippets about {company_name} for QUANTITATIVE OR SCALE CONTRADICTIONS — pairs of claims about the same topic that report different numbers, percentages, adoption rates, scale, or extent.

EVIDENCE ITEMS:
{json.dumps(compact, ensure_ascii=False, indent=2)}

Find pairs or small groups where two evidence items make conflicting numeric / scale claims about the SAME underlying topic.

Examples worth flagging:
- One source says "9% of HR personnel use Tool X regularly", another says "Tool X has thousands of weekly users company-wide"
- One source says "pilot at one site", another says "deployed across all factories"
- One source says "specific R&D team tested supercomputing", another says "enterprise-wide advanced computing deployment"

Do NOT flag:
- Claims about different topics that merely sound similar
- Updates over time where the older one is clearly superseded
- Differences in sentiment without a numeric or scale disagreement

For narrow-vs-broad conflicts, resolution_rule MUST be "state_both_with_scope" and preferred_wording MUST warn not to generalize the narrow evidence company-wide.

Return at most {_MAX_CONTRADICTIONS} contradictions, ranked by importance. Output VALID JSON only.

OUTPUT SCHEMA:
{{"contradictions": [
  {{
    "topic": "Short topic label",
    "claim_a": "First side",
    "source_a_title": "Source title for claim A",
    "scope_a": "narrow" | "broad",
    "claim_b": "Second side",
    "source_b_title": "Source title for claim B",
    "scope_b": "narrow" | "broad",
    "resolution_rule": "state_both_with_scope",
    "preferred_wording": "One sentence telling the summary writer how to state both sides without overgeneralising.",
    "confidence": "low" | "medium" | "high",
    "severity": "low" | "medium" | "high"
  }}
]}}

If no real contradictions exist, output: {{"contradictions": []}}"""


def detect_contradictions(evidence_items: list, company_name: str = "") -> list:
    """Return a list of contradiction objects, or [] when none / on failure."""
    if not evidence_items:
        return []

    deterministic = _detect_scope_tensions_deterministic(evidence_items)

    try:
        prompt = _build_prompt(evidence_items, company_name or "the company")
        parsed, _model_used = call_json_model_chain(
            prompt,
            (GPT_VERIFIER_MODEL, CLAUDE_SONNET_MODEL),
            max_tokens=1800,
            timeout=45,
        )
        contradictions = parsed.get("contradictions") if isinstance(parsed, dict) else []
        if not isinstance(contradictions, list):
            contradictions = []

        clean: list[dict[str, Any]] = []
        seen = set()
        for item in list(contradictions) + list(deterministic):
            normalized = _normalize_contradiction(item)
            if not normalized:
                continue
            key = (
                normalized["topic"].lower(),
                normalized["source_a_title"].lower(),
                normalized["source_b_title"].lower(),
            )
            if key in seen:
                continue
            seen.add(key)
            clean.append(normalized)
            if len(clean) >= _MAX_CONTRADICTIONS:
                break
        return clean
    except Exception as e:
        print(f"   [Contradictions] Detection failed: {e}")
        return [_normalize_contradiction(c) for c in deterministic if _normalize_contradiction(c)][:_MAX_CONTRADICTIONS]
