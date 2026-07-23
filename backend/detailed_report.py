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
import uuid

import anthropic
from llm_clients import call_anthropic_text, call_openai_text, parse_json_object
from model_policy import CLAUDE_OPUS_MODEL, CLAUDE_SONNET_MODEL, DETAILED_COMPLETION_MODEL, GPT_REASONING_MODEL, policy_for
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
_CONDENSED_WORD_MIN = 760
_COMPACT_WORD_MIN = 500
_COMPACT_WORD_MAX = 750
_REPAIR_REGEN_THRESHOLD = 0.20


def detailed_report_publication_status(
    quality: dict,
    verification: dict,
    recovery: dict | None = None,
) -> str:
    """Return a publication status that cannot overstate verification."""
    recovery = recovery or {}
    if not (quality or {}).get("complete"):
        return "incomplete"
    if (verification or {}).get("unresolved_blocking_claims"):
        return "withheld_material_claims"
    if not (verification or {}).get("verified"):
        publication_reasons = (verification or {}).get("publication_reasons") or {}
        # Verification coverage is a trust boundary. Semantic calibration and
        # language hygiene are separate quality dimensions and must not make a
        # factually verified, structurally complete report disappear.
        if not publication_reasons.get("incomplete_verification"):
            if publication_reasons.get("semantic_calibration"):
                return "published_with_semantic_calibration"
            if publication_reasons.get("language_hygiene"):
                return "published_with_language_warnings"
        return "internal_draft_verification_pending"
    recovery_mode = str(recovery.get("repair_mode") or "none")
    recovery_status = str(recovery.get("recovery_status") or "not_needed")
    had_actions = bool(
        (verification or {}).get("deterministic_removals")
        or (verification or {}).get("deterministic_replacements")
        or (verification or {}).get("wording_calibrations_applied")
        or (verification or {}).get("model_correction_applied")
    )
    if recovery_mode == "full_regeneration" and recovery_status == "succeeded":
        return "regenerated_verified"
    if recovery_mode == "safe_condensation" and recovery_status == "succeeded":
        return "verified_condensed_after_removals"
    if recovery_mode == "targeted_paragraph_repair" and recovery_status == "succeeded":
        return "verified_after_targeted_rewrite"
    if (quality or {}).get("condensed") and had_actions:
        return "verified_condensed_after_removals"
    if had_actions:
        return "verified_after_targeted_rewrite"
    return "generated_verified"

_TARGETED_REPAIR_SCHEMA = {
    "type": "object",
    "properties": {
        "sections": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "heading": {"type": "string"},
                    "content": {"type": "string"},
                },
                "required": ["heading", "content"],
                "additionalProperties": False,
            },
        },
    },
    "required": ["sections"],
    "additionalProperties": False,
}

_LANGUAGE_REPAIR_SCHEMA = {
    "type": "object",
    "properties": {
        "repairs": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "item_id": {"type": "string"},
                    "text": {"type": "string"},
                },
                "required": ["item_id", "text"],
                "additionalProperties": False,
            },
        },
    },
    "required": ["repairs"],
    "additionalProperties": False,
}


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


def _claim_terms(value: str) -> set[str]:
    """Normalize small wording variations for deterministic claim matching."""
    stop_words = {
        "a", "an", "the", "has", "have", "had", "is", "are", "was", "were",
        "be", "been", "being",
    }
    aliases = {"every": "all"}
    terms = set()
    for raw in re.findall(r"[a-z0-9]+", str(value or "").lower()):
        token = aliases.get(raw, raw)
        if len(token) > 4 and token.endswith("s") and not token.endswith("ss"):
            token = token[:-1]
        if token not in stop_words:
            terms.add(token)
    return terms


def polish_detailed_report_text(value: str) -> str:
    """Repair deterministic language seams without changing report meaning."""
    text = str(value or "")
    text = re.sub(
        r"\b([A-Za-zÅÄÖåäö][A-Za-zÅÄÖåäö-]{3,})\s+\1\b",
        r"\1",
        text,
        flags=re.IGNORECASE,
    )
    text = re.sub(r"\.\s*,", ",", text)
    text = re.sub(r",\s*\.", ".", text)
    text = re.sub(r"([.!?])\s*\1+", r"\1", text)
    text = re.sub(r"\s+([,.;:!?])", r"\1", text)
    text = re.sub(r"[ \t]{2,}", " ", text)
    text = re.sub(r"\n[ \t]+", "\n", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def _language_defects(text: str) -> list[str]:
    """Return deterministic public-prose defects without judging analysis."""
    value = str(text or "").strip()
    if not value:
        return ["empty_text"]
    defects = []
    checks = (
        (r"\b([A-Za-zÅÄÖåäö][A-Za-zÅÄÖåäö-]{2,})\s+\1\b", "repeated_word"),
        (
            r"\b((?:[A-Za-zÅÄÖåäö0-9'’/-]+\s+){1,3}[A-Za-zÅÄÖåäö0-9'’/-]+)\s*,?\s*\1\b",
            "repeated_short_phrase",
        ),
        (r",\s*['’]s\b", "broken_possessive"),
        (r"\.\s*[—–-]\s+(?:a|an|the)\b", "broken_transition"),
        (r"\bwhether\s+(?:if|because)\b", "malformed_whether"),
        (r"\b(?:and|or|but|because|while|with|to|of)\s*[.!?]?$", "trailing_connector"),
    )
    for pattern, label in checks:
        if re.search(pattern, value, flags=re.IGNORECASE):
            defects.append(label)
    if value.count("(") != value.count(")") or value.count("[") != value.count("]"):
        defects.append("unmatched_bracket")
    parentheticals = [
        re.sub(r"\s+", " ", item).strip().lower()
        for item in re.findall(r"\(([^()]{8,})\)", value)
    ]
    if len(parentheticals) != len(set(parentheticals)):
        defects.append("repeated_parenthetical")

    words = re.findall(r"[A-Za-zÅÄÖåäö0-9][A-Za-zÅÄÖåäö0-9'’/-]*", value.lower())
    # Repeated strategic vocabulary is normal in an analytical paragraph. Only
    # flag longer, nearby clauses; short four-word n-grams produced too many
    # false positives (for example repeated use of "product lifecycle").
    for size in range(10, 6, -1):
        phrases = {}
        for index in range(0, max(0, len(words) - size + 1)):
            phrase = tuple(words[index:index + size])
            if len(set(phrase)) < 3:
                continue
            previous = phrases.get(phrase)
            if previous is not None and index - previous <= 35:
                defects.append("repeated_phrase")
                break
            phrases[phrase] = index
        if "repeated_phrase" in defects:
            break
    return list(dict.fromkeys(defects))


def _language_invariants(text: str) -> dict:
    """Capture tokens a grammar-only repair is not allowed to alter."""
    value = str(text or "")
    return {
        "numbers": sorted(re.findall(r"(?<![A-Za-z])\d+(?:[.,]\d+)?%?", value)),
        "citations": sorted(re.findall(r"\[[A-Z]+-[^\]]+\]|\[\d{1,3}\]", value)),
        "urls": sorted(re.findall(r"https?://[^\s)\]]+", value)),
    }


def final_public_language_review(
    text: str,
    *,
    time_budget_seconds: int = 30,
    label: str = "Detailed Report",
) -> tuple[str, dict]:
    """Review the whole output, repairing only paragraphs with language defects.

    This is deliberately evidence-neutral: all numbers, citations and URLs must
    remain identical. The complete output is scanned, but the model receives
    only the passages that fail deterministic fluency checks.
    """
    source = polish_detailed_report_text(text)
    chunks = re.split(r"(\n\s*\n+)", source)
    flagged = []
    for index in range(0, len(chunks), 2):
        paragraph = chunks[index].strip()
        if not paragraph or paragraph.startswith("## "):
            continue
        defects = _language_defects(paragraph)
        if defects:
            flagged.append({
                "item_id": f"P{index // 2 + 1:03d}",
                "chunk_index": index,
                "text": paragraph,
                "defects": defects,
            })

    metadata = {
        "status": "clean" if not flagged else "defects_detected",
        "reviewed_paragraph_count": len([item for item in chunks[::2] if item.strip()]),
        "flagged_paragraph_count": len(flagged),
        "repaired_paragraph_count": 0,
        "unresolved_defects": [],
        "model": "",
    }
    if not flagged:
        return source, metadata
    if int(time_budget_seconds or 0) < 12:
        metadata["status"] = "unresolved_budget"
        metadata["unresolved_defects"] = [
            {"item_id": item["item_id"], "defects": item["defects"]} for item in flagged
        ]
        return source, metadata

    prompt_items = [
        {"item_id": item["item_id"], "text": item["text"], "defects": item["defects"]}
        for item in flagged[:8]
    ]
    prompt = f"""You are the final language editor for an evidence-governed {label}.

Repair grammar, duplication, punctuation, sentence completeness and paragraph fluency only. Preserve every factual proposition, analytical conclusion, number, date, entity, citation and URL. Do not add, remove, strengthen, weaken or reinterpret any claim. Return exactly one complete replacement for every item_id. Do not include headings or commentary.

FLAGGED PASSAGES:
{json.dumps(prompt_items, ensure_ascii=False)}
"""
    try:
        raw = call_anthropic_text(
            prompt,
            model=CLAUDE_SONNET_MODEL,
            max_tokens=5000,
            timeout=max(12, min(40, int(time_budget_seconds or 30))),
            max_retries=0,
            reasoning_effort="low",
            json_schema=_LANGUAGE_REPAIR_SCHEMA,
            json_schema_name="final_public_language_review",
            log_label=f"{label} Language QA",
        )
        parsed = parse_json_object(raw)
        repairs = {
            str(item.get("item_id") or ""): str(item.get("text") or "").strip()
            for item in (parsed.get("repairs") or [])
            if isinstance(item, dict)
        }
        expected = {item["item_id"] for item in prompt_items}
        if set(repairs) != expected:
            raise ValueError("Language QA did not return exactly the requested item IDs")
        for item in flagged[:8]:
            candidate = repairs[item["item_id"]]
            if (
                not candidate
                or _language_invariants(candidate) != _language_invariants(item["text"])
                or _language_defects(candidate)
            ):
                continue
            chunks[item["chunk_index"]] = candidate
            metadata["repaired_paragraph_count"] += 1
        metadata["model"] = CLAUDE_SONNET_MODEL
    except Exception as exc:
        metadata["error"] = str(exc)[:240]

    reviewed = polish_detailed_report_text("".join(chunks))
    unresolved = []
    for item in flagged:
        paragraph = chunks[item["chunk_index"]].strip()
        defects = _language_defects(paragraph)
        if defects:
            unresolved.append({"item_id": item["item_id"], "defects": defects})
    metadata["unresolved_defects"] = unresolved
    metadata["status"] = "repaired" if not unresolved else "unresolved"
    return reviewed, metadata


def detailed_report_quality(
    text: str,
    compact: bool = False,
    *,
    allow_condensed: bool = False,
) -> dict:
    words = _word_count(text)
    headings = _section_headings(text)
    normalized = [heading.strip().lower() for heading in headings]
    preferred_min = _COMPACT_WORD_MIN if compact else _WORD_MIN
    required_min = (
        preferred_min
        if compact or not allow_condensed
        else _CONDENSED_WORD_MIN
    )
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
        "preferred_min_words": preferred_min,
        "condensed_floor_words": _COMPACT_WORD_MIN if compact else _CONDENSED_WORD_MIN,
        "condensed": bool(not compact and allow_condensed and required_min <= words < preferred_min),
    }


def _split_report_sections(text: str) -> tuple[str, list[dict]]:
    """Split a Markdown report without allowing repaired sections to move."""
    source = str(text or "").strip()
    matches = list(re.finditer(r"(?m)^##\s+(.+?)\s*$", source))
    if not matches:
        return source, []
    preamble = source[:matches[0].start()].strip()
    sections = []
    for index, match in enumerate(matches):
        end = matches[index + 1].start() if index + 1 < len(matches) else len(source)
        sections.append({
            "heading": match.group(1).strip(),
            "content": source[match.end():end].strip(),
        })
    return preamble, sections


def _assemble_report_sections(preamble: str, sections: list[dict]) -> str:
    parts = [str(preamble or "").strip()] if str(preamble or "").strip() else []
    for section in sections:
        heading = str(section.get("heading") or "").strip()
        content = str(section.get("content") or "").strip()
        if heading and content:
            parts.append(f"## {heading}\n\n{content}")
    return polish_detailed_report_text("\n\n".join(parts))


def detailed_report_repair_impact(
    original_text: str,
    repaired_text: str,
    *,
    compact: bool = False,
) -> dict:
    """Choose local editing or full regeneration from measurable report damage."""
    original_words = _word_count(original_text)
    repaired_words = _word_count(repaired_text)
    retained_ratio = min(1.0, repaired_words / max(1, original_words))
    removed_word_ratio = max(0.0, 1.0 - retained_ratio)
    original_preamble, original_sections = _split_report_sections(original_text)
    repaired_preamble, repaired_sections = _split_report_sections(repaired_text)
    original_map = {
        str(section.get("heading") or "").strip().lower(): section
        for section in original_sections
    }
    repaired_map = {
        str(section.get("heading") or "").strip().lower(): section
        for section in repaired_sections
    }
    affected_sections = []
    for heading, original in original_map.items():
        current = repaired_map.get(heading)
        original_content = re.sub(r"\s+", " ", str(original.get("content") or "")).strip()
        current_content = re.sub(r"\s+", " ", str((current or {}).get("content") or "")).strip()
        if original_content != current_content:
            affected_sections.append(str(original.get("heading") or heading))
    for heading, current in repaired_map.items():
        if heading not in original_map:
            affected_sections.append(str(current.get("heading") or heading))
    if re.sub(r"\s+", " ", original_preamble) != re.sub(r"\s+", " ", repaired_preamble):
        affected_sections.insert(0, "Report introduction")

    quality = detailed_report_quality(repaired_text, compact=compact, allow_condensed=True)
    structural_missing = [
        issue for issue in quality.get("missing", [])
        if not str(issue).startswith("word_count_below_")
    ]
    core_argument_damaged = bool(structural_missing)
    for core_heading in ("key findings", "verdict"):
        original = original_map.get(core_heading)
        current = repaired_map.get(core_heading)
        if not original or not current:
            core_argument_damaged = True
            continue
        original_count = _word_count(str(original.get("content") or ""))
        current_count = _word_count(str(current.get("content") or ""))
        if original_count >= 20 and current_count / max(1, original_count) < 0.60:
            core_argument_damaged = True

    repair_mode = (
        "full_regeneration"
        if removed_word_ratio > _REPAIR_REGEN_THRESHOLD or core_argument_damaged
        else "targeted_paragraph_repair"
    )
    return {
        "original_word_count": original_words,
        "repaired_word_count": repaired_words,
        "retained_ratio": round(retained_ratio, 4),
        "removed_word_ratio": round(removed_word_ratio, 4),
        "affected_sections": list(dict.fromkeys(affected_sections)),
        "core_argument_damaged": core_argument_damaged,
        "repair_mode": repair_mode,
        "strict_quality": detailed_report_quality(repaired_text, compact=compact),
        "publishable_quality": quality,
    }


def build_detailed_report_claim_action_manifest(
    report_text: str,
    claim_issues: list[dict],
) -> list[dict]:
    """Freeze current-run claim boundaries for repair and regeneration."""
    _preamble, sections = _split_report_sections(report_text)
    manifest = []
    for raw in claim_issues or []:
        if not isinstance(raw, dict):
            continue
        claim = re.sub(r"\s+", " ", str(raw.get("claim") or "")).strip()
        if not claim:
            continue
        claim_id = str(raw.get("claim_id") or "").strip() or "DR-" + uuid.uuid5(
            uuid.NAMESPACE_URL,
            claim.lower(),
        ).hex[:12].upper()
        section_name = str(raw.get("section") or "").strip()
        if not section_name:
            claim_terms = _claim_terms(claim)
            best = (0.0, "")
            for section in sections:
                content_terms = _claim_terms(str(section.get("content") or ""))
                overlap = len(claim_terms & content_terms) / max(1, len(claim_terms))
                if overlap > best[0]:
                    best = (overlap, str(section.get("heading") or ""))
            if best[0] >= 0.35:
                section_name = best[1]
        raw_issue_type = str(
            raw.get("issue_type") or raw.get("error_type") or "blocking_false_claim"
        ).strip()
        issue_key = raw_issue_type.lower().replace("-", "_").replace(" ", "_")
        if "handoff" in issue_key:
            governed_issue_type = "handoff_failure"
        elif "causal" in issue_key or "attribution" in issue_key:
            governed_issue_type = "unsupported_causal_attribution"
        elif any(token in issue_key for token in ("scope", "entity", "misattribution")):
            governed_issue_type = "wrong_scope"
        elif (
            "calibration" in issue_key
            or "overstrong" in issue_key
            or str(raw.get("support_level") or "").lower() in {"partial", "context_only"}
        ):
            governed_issue_type = "overstrong_interpretation"
        else:
            governed_issue_type = "false_fact"
        manifest.append({
            "claim_id": claim_id,
            "section": section_name,
            "original_proposition": claim,
            "issue_type": governed_issue_type,
            "original_issue_type": raw_issue_type,
            "claim_class": raw.get("claim_class") or "",
            "support_level": raw.get("support_level") or "unsupported",
            "reason": raw.get("reasoning") or raw.get("reason") or "",
            "approved_narrowing": raw.get("correction") or "",
            "prohibited_fingerprint": sorted(_claim_terms(claim))[:24],
            "source_ids": list(dict.fromkeys(
                str(value or "").strip()
                for value in (raw.get("source_ids") or [])
                if str(value or "").strip()
            )),
            "current_run_only": True,
        })
    return manifest


def _section_context(sections: list[dict], index: int) -> dict:
    previous = sections[index - 1] if index > 0 else {}
    following = sections[index + 1] if index + 1 < len(sections) else {}
    return {
        "previous_heading": previous.get("heading", ""),
        "previous_tail": str(previous.get("content") or "")[-500:],
        "next_heading": following.get("heading", ""),
        "next_opening": str(following.get("content") or "")[:500],
    }


def repair_detailed_report_sections(
    repaired_text: str,
    *,
    affected_sections: list[str],
    claim_issues: list[dict],
    claim_action_manifest: list[dict] | None = None,
    time_budget_seconds: int = 40,
) -> tuple[str, str]:
    """Use Sonnet to repair only prose damaged by governed claim actions."""
    preamble, sections = _split_report_sections(repaired_text)
    requested = {str(value or "").strip().lower() for value in affected_sections or []}
    selected = []
    for index, section in enumerate(sections):
        if str(section.get("heading") or "").strip().lower() in requested:
            selected.append({**section, **_section_context(sections, index)})
    if not selected:
        return polish_detailed_report_text(repaired_text), "deterministic-language-qa"

    boundaries = list(claim_action_manifest or [])
    if not boundaries:
        boundaries = build_detailed_report_claim_action_manifest(repaired_text, claim_issues)
    prompt = f"""You are repairing fluency in selected sections of an evidence-governed Detailed Report.

Edit only the supplied current section prose. Preserve each heading and return exactly one section for every supplied heading. Restore paragraph transitions, sentence completeness and analytical flow after unsupported wording was narrowed or removed. Do not add facts, values, entities, services, timing, causal assertions or recommendations that are not already present in the current section or an allowed narrowing. Never reintroduce unsafe wording. The neighboring excerpts are continuity context only. Do not rewrite unaffected sections.

TARGET: Keep the full report substantive. If a section became slightly short, add only connective explanation or cautious synthesis already supported by its current prose. Do not pad.

SECTIONS TO REPAIR:
{json.dumps(selected, ensure_ascii=False)}

GOVERNED CLAIM BOUNDARIES. The original propositions and semantic equivalents are prohibited;
use approved_narrowing when it is available:
{json.dumps(boundaries, ensure_ascii=False)}
"""
    raw = call_anthropic_text(
        prompt,
        model=CLAUDE_SONNET_MODEL,
        max_tokens=5000,
        timeout=max(15, min(45, int(time_budget_seconds or 40))),
        max_retries=0,
        reasoning_effort="medium",
        json_schema=_TARGETED_REPAIR_SCHEMA,
        json_schema_name="detailed_report_section_repair",
        log_label="Detailed Report Targeted Repair",
    )
    parsed = parse_json_object(raw)
    returned = {
        str(item.get("heading") or "").strip().lower(): str(item.get("content") or "").strip()
        for item in (parsed.get("sections") or [])
        if isinstance(item, dict)
    }
    if set(returned) != {
        str(item.get("heading") or "").strip().lower() for item in selected
    }:
        raise ValueError("Targeted repair did not return exactly the requested report sections")
    for section in sections:
        key = str(section.get("heading") or "").strip().lower()
        if key in returned:
            if not returned[key] or returned[key].startswith("##"):
                raise ValueError(f"Targeted repair returned invalid prose for {section.get('heading')}")
            section["content"] = returned[key]
    candidate = _assemble_report_sections(preamble, sections)
    if flagged_report_claims_remaining(candidate, claim_issues):
        raise ValueError("Targeted repair reintroduced governed unsafe wording")
    return candidate, CLAUDE_SONNET_MODEL


def remove_flagged_report_sentences(text: str, claims: list) -> tuple[str, int]:
    """Remove verifier-flagged reader-facing sentences without generating new prose."""
    claim_texts = []
    for item in claims or []:
        value = item.get("claim") if isinstance(item, dict) else item
        value = re.sub(r"\s+", " ", str(value or "")).strip()
        if value:
            claim_texts.append(value)
    if not text or not claim_texts:
        return text or "", 0

    removed = 0
    output_paragraphs = []
    for paragraph in re.split(r"\n\s*\n", text):
        stripped = paragraph.strip()
        if not stripped:
            continue
        if stripped.startswith("#"):
            output_paragraphs.append(stripped)
            continue
        sentences = re.split(r"(?<=[.!?])\s+(?=[A-Z0-9])", stripped)
        kept = []
        for sentence in sentences:
            sentence_norm = re.sub(r"\s+", " ", sentence).strip()
            sentence_words = _claim_terms(sentence_norm)
            should_remove = False
            for claim in claim_texts:
                claim_words = _claim_terms(claim)
                overlap = len(sentence_words & claim_words) / max(1, len(claim_words))
                if claim.lower() in sentence_norm.lower() or sentence_norm.lower() in claim.lower() or (
                    len(claim_words) >= 5 and overlap >= 0.72
                ):
                    should_remove = True
                    break
            if should_remove:
                removed += 1
            else:
                kept.append(sentence.strip())
        if kept:
            output_paragraphs.append(" ".join(kept))
    return "\n\n".join(output_paragraphs).strip(), removed


def remove_flagged_report_paragraphs(text: str, claims: list) -> tuple[str, int]:
    """Remove complete prose paragraphs when sentence-level matching cannot clear a claim."""
    claim_terms = []
    for item in claims or []:
        value = item.get("claim") if isinstance(item, dict) else item
        terms = _claim_terms(str(value or ""))
        if terms:
            claim_terms.append(terms)
    if not text or not claim_terms:
        return text or "", 0

    removed = 0
    kept = []
    for paragraph in re.split(r"\n\s*\n", str(text or "")):
        stripped = paragraph.strip()
        if not stripped:
            continue
        if stripped.startswith("#"):
            kept.append(stripped)
            continue
        paragraph_terms = _claim_terms(stripped)
        matches = any(
            len(terms) >= 5
            and len(terms & paragraph_terms) / max(1, len(terms)) >= 0.55
            for terms in claim_terms
        )
        if matches:
            removed += 1
        else:
            kept.append(stripped)
    return polish_detailed_report_text("\n\n".join(kept)), removed


def flagged_report_claims_remaining(text: str, claims: list) -> list[str]:
    """Return material flagged claim wording that still appears after correction."""
    normalized_text = re.sub(r"\s+", " ", str(text or "")).lower()
    report_sentences = [
        re.sub(r"\s+", " ", sentence).strip()
        for sentence in re.split(r"(?<=[.!?])\s+(?=[A-Z0-9])", str(text or ""))
        if sentence.strip()
    ]
    remaining = []
    for item in claims or []:
        value = item.get("claim") if isinstance(item, dict) else item
        claim = re.sub(r"\s+", " ", str(value or "")).strip()
        claim_words = _claim_terms(claim)
        fuzzy_match = any(
            len(claim_words) >= 5
            and len(claim_words & _claim_terms(sentence)) / len(claim_words) >= 0.72
            for sentence in report_sentences
        )
        if claim and (claim.lower() in normalized_text or fuzzy_match):
            remaining.append(claim)
    return remaining


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

    # Historical Scanner outputs are source memory, not comparable company facts.
    delta = None

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

    # Financial facts only. Model-authored assessments are deliberately excluded.
    fin_findings = []
    for finding in (full_data.get("financial_findings") or [])[:8]:
        if not isinstance(finding, dict):
            continue
        source_url = str(finding.get("source_url") or "").strip()
        source_quote = str(finding.get("evidence_snippet") or finding.get("source_quote") or "").strip()
        if (
            not source_url
            or not source_quote
            or finding.get("generation_eligible") is False
            or finding.get("wrong_entity") is True
            or str(finding.get("entity_match_status") or "").lower() == "wrong"
        ):
            continue
        fin_findings.append({
            "dimension": str(finding.get("dimension") or ""),
            "source_title": str(finding.get("source_title") or "")[:160],
            "source_url": source_url,
            "source_quote": source_quote[:900],
            "entity_scope": str(finding.get("entity_scope") or "unknown"),
            "source_type": str(finding.get("source_type") or ""),
        })
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
    efforts: tuple[str, ...] | None = None,
) -> tuple[str, str, dict]:
    """Use GPT to expand/repair a short or truncated Detailed Report draft."""
    payload = _build_input_payload(full_data, company_name, foresight_data, prior_summaries)
    prompt = _build_completion_prompt(payload, company_name, draft, compact=compact)
    budget = max(15, int(time_budget_seconds or 80))
    deadline = time.time() + budget
    policy = policy_for("detailed_report")
    token_budget = 12000 if compact else int(policy.get("completion_max_output_tokens") or 16000)
    last_quality: dict = detailed_report_quality("", compact=compact)
    effort_chain = efforts or (str(policy.get("completion_effort") or "medium"), "high")
    for effort in tuple(dict.fromkeys(effort_chain)):
        remaining = max(0, int(deadline - time.time()))
        if remaining < 8:
            break
        t0 = time.time()
        try:
            text = call_openai_text(
                prompt,
                model=DETAILED_COMPLETION_MODEL,
                max_output_tokens=token_budget,
                timeout=min(70, remaining),
                max_retries=0,
                reasoning_effort=effort,
                text_verbosity=str(policy.get("completion_verbosity") or "high"),
                log_label="Detailed Report Completion",
            )
        except Exception as exc:
            print(f"   ❌ [Detailed Report] {DETAILED_COMPLETION_MODEL} completion failed at effort={effort}: {exc}")
            continue
        text = _strip_links((text or "").strip())
        last_quality = detailed_report_quality(text, compact=compact)
        elapsed = int((time.time() - t0) * 1000)
        print(
            "   📝 [Detailed Report] GPT completion/repair: "
            f"{last_quality['word_count']} words, complete={last_quality['complete']}, "
            f"model={DETAILED_COMPLETION_MODEL}, effort={effort} ({elapsed}ms)"
        )
        if last_quality["complete"]:
            return text, f"{DETAILED_COMPLETION_MODEL}-completion-{effort}", last_quality
    return "", f"{DETAILED_COMPLETION_MODEL}-completion-incomplete", last_quality


def regenerate_detailed_report_after_claim_actions(
    full_data: dict,
    company_name: str,
    foresight_data: dict | None,
    governed_draft: str,
    *,
    prior_summaries: dict | None = None,
    compact: bool = False,
    time_budget_seconds: int = 90,
    claim_action_manifest: list[dict] | None = None,
) -> tuple[str, str, dict]:
    """Rebuild a materially damaged report, with Sonnet as bounded fallback."""
    started = time.time()
    budget = max(30, int(time_budget_seconds or 90))
    gpt_budget = max(20, min(75, budget - 20))
    governed_recovery_draft = governed_draft
    if claim_action_manifest:
        governed_recovery_draft += (
            "\n\nCURRENT-RUN CLAIM ACTION MANIFEST. Do not repeat these original propositions "
            "or semantic equivalents; use only approved narrower wording:\n"
            + json.dumps(claim_action_manifest, ensure_ascii=False)
        )
    text, model, quality = complete_detailed_report_draft(
        full_data,
        company_name,
        foresight_data,
        governed_recovery_draft,
        prior_summaries=prior_summaries,
        compact=compact,
        time_budget_seconds=gpt_budget,
        efforts=("high",),
    )
    if text and quality.get("complete"):
        return polish_detailed_report_text(text), model, quality

    remaining = max(0, budget - int(time.time() - started))
    if remaining < 15:
        return "", model, quality
    payload = _build_input_payload(full_data, company_name, foresight_data, prior_summaries)
    prompt = _build_completion_prompt(payload, company_name, governed_recovery_draft, compact=compact)
    prompt += (
        "\n\nMATERIAL-DAMAGE RECOVERY: Rebuild the complete report because governed claim "
        "corrections removed too much of the prior argument. Use the structured payload as the "
        "only factual basis. Preserve supported conclusions, restore the required sections and "
        "produce a coherent new report rather than padding the damaged draft."
    )
    try:
        candidate = call_anthropic_text(
            prompt,
            model=CLAUDE_SONNET_MODEL,
            max_tokens=12000,
            timeout=min(60, remaining),
            max_retries=0,
            reasoning_effort="high",
            log_label="Detailed Report Regeneration Fallback",
        )
        candidate = polish_detailed_report_text(_strip_links(candidate))
        candidate_quality = detailed_report_quality(candidate, compact=compact)
        if candidate_quality.get("complete"):
            return candidate, f"{CLAUDE_SONNET_MODEL}-regeneration", candidate_quality
        quality = candidate_quality
    except Exception as exc:
        print(f"   ❌ [Detailed Report] {CLAUDE_SONNET_MODEL} regeneration fallback failed: {exc}")
    return "", f"{model}+{CLAUDE_SONNET_MODEL}-regeneration-incomplete", quality


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
    completion_policy = policy_for("detailed_report")
    token_budget = 12000 if compact else int(completion_policy.get("completion_max_output_tokens") or 16000)
    started = time.time()
    total_budget = max(20 if compact else 60, int(time_budget_seconds or (110 if compact else 180)))

    def remaining_budget() -> int:
        return max(0, total_budget - int(time.time() - started))

    def remaining_timeout(*, reserve: int = 8, cap: int = 120) -> int:
        remaining = remaining_budget() - reserve
        return max(0, min(cap, remaining))

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
        if remaining_budget() < 20:
            print(f"   ⏭ [Detailed Report] GPT completion skipped ({reason}); insufficient report budget")
            return "", ""
        try:
            print(f"   📝 [Detailed Report] Attempting {DETAILED_COMPLETION_MODEL} completion ({reason})...")
            t0 = time.time()
            completion_prompt = _build_completion_prompt(payload, company_name, draft, compact=compact)
            for effort in (str(completion_policy.get("completion_effort") or "medium"), "high"):
                call_timeout = remaining_timeout(reserve=3, cap=70)
                if call_timeout < 8:
                    break
                try:
                    repaired = call_openai_text(
                        completion_prompt,
                        model=DETAILED_COMPLETION_MODEL,
                        max_output_tokens=token_budget,
                        timeout=call_timeout,
                        max_retries=0,
                        reasoning_effort=effort,
                        text_verbosity=str(completion_policy.get("completion_verbosity") or "high"),
                        log_label="Detailed Report Completion",
                    )
                except Exception as effort_exc:
                    print(
                        f"   ❌ [Detailed Report] {DETAILED_COMPLETION_MODEL} completion "
                        f"failed at effort={effort}: {effort_exc}"
                    )
                    if remaining_budget() < 12:
                        break
                    continue
                repaired = _strip_links((repaired or "").strip())
                wc = _word_count(repaired)
                elapsed = int((time.time() - t0) * 1000)
                print(
                    f"   📝 [Detailed Report] {DETAILED_COMPLETION_MODEL} completion: "
                    f"{wc} words, effort={effort} ({elapsed}ms)"
                )
                if repaired and quality_ok(repaired):
                    return repaired, f"{DETAILED_COMPLETION_MODEL}-completion-{effort}"
                if remaining_budget() < 12:
                    break
            return "", ""
        except Exception as exc:
            elapsed = int((time.time() - t0) * 1000) if 't0' in dir() else 0
            print(f"   ❌ [Detailed Report] {DETAILED_COMPLETION_MODEL} completion failed ({elapsed}ms): {exc}")
            return "", ""

    anthropic_key = os.environ.get("ANTHROPIC_API_KEY", "").strip()
    if anthropic_key and remaining_budget() >= 10:
        # --- Attempt 1: Claude Sonnet 5 (standard long-form writer) ---
        SONNET_MODEL = CLAUDE_SONNET_MODEL
        try:
            print(f"   📝 [Detailed Report] Attempting {SONNET_MODEL}...")
            t0 = time.time()
            sonnet_timeout = remaining_timeout(reserve=20 if not compact else 8, cap=90)
            if sonnet_timeout < 8:
                raise TimeoutError("Detailed Report Sonnet budget unavailable")
            claude_client = anthropic.Anthropic(
                api_key=anthropic_key,
                timeout=sonnet_timeout,
                max_retries=0,
            )
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
        gpt_timeout = remaining_timeout(reserve=12 if not compact else 5, cap=80)
        if gpt_timeout < 8:
            raise TimeoutError("Detailed Report GPT fallback budget unavailable")
        print(f"   📝 [Detailed Report] Attempting {GPT_REASONING_MODEL} fallback...")
        t0 = time.time()
        text = call_openai_text(
            prompt,
            model=GPT_REASONING_MODEL,
            max_output_tokens=token_budget,
            timeout=gpt_timeout,
            max_retries=0,
            reasoning_effort="medium",
            text_verbosity="high",
            log_label="Detailed Report Generation",
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

    if anthropic_key and remaining_budget() >= 10:
        # --- Attempt 3: Claude Opus 4.8 premium fallback ---
        OPUS_MODEL = CLAUDE_OPUS_MODEL
        try:
            print(f"   📝 [Detailed Report] Attempting {OPUS_MODEL} premium fallback...")
            t0 = time.time()
            opus_timeout = remaining_timeout(reserve=3, cap=60)
            if opus_timeout < 8:
                raise TimeoutError("Detailed Report Opus fallback budget unavailable")
            claude_client = anthropic.Anthropic(
                api_key=anthropic_key,
                timeout=opus_timeout,
                max_retries=0,
            )
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
