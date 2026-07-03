"""
Foresight Observatory — Three-Layer Strategic Analysis Engine
=============================================================
Synthesizes existing Scanner data into macro-level strategic intelligence:
  1. Global Force Landscape — futurologist's briefing on converging forces
  2. Finnish Economy Force Map — how forces reshape Finnish business
  3. Domain Force Dossiers — per-domain analysis with timescale mapping

Model pipeline:
  - GPT-5.5 Pro: Layer 1 and Layer 2 strategic analysis
  - Claude Opus 4.8: Layer 3 domain dossiers
  - GPT-5.5 high / Sonnet 4.6: Independent verification

All data comes from existing DB — no new scanning or web search required.
"""

import json
import hashlib
import os
import re
import time
import statistics
from collections import Counter, defaultdict
from datetime import datetime

import anthropic

from llm_clients import (
    GPT_PRO_MODEL,
    GPT_REASONING_MODEL,
    call_json_model_chain,
    call_openai_text,
    parse_json_object,
)
from foresight_guardrails import build_foresight_guardrails, sanitize_foresight_text, validate_foresight_text, sanitize_future_insight
from storage import BlobStorage
from market_intelligence import (
    compute_market_intelligence, THEME_CATEGORIES, ALL_CATEGORIES, KB_DIMENSIONS,
)
from analyze_scores import run_analysis, classify_archetype


# ═══════════════════════════════════════════════════════════════════════════════
# SYSTEM PROMPT — Shared across all three outputs
# ═══════════════════════════════════════════════════════════════════════════════

SYSTEM_ROLE = """You are a synthesis of three expert perspectives: a futurologist who reads weak signals, a market analyst who grounds insight in evidence, and a strategic designer who frames actionable futures.

CRITICAL FRAMING RULE — Technology is context, never conclusion:
- Frame every force through the lens of organizational thinking, adaptability, and future-readiness.
- NEVER say "companies need AI/cloud/digital/technology" — instead describe what thinking capabilities, organizational structures, or strategic postures enable companies to create their own race.
- The ultimate strategic question is not "how do we respond to these forces?" but "how do we create conditions where we define the game rather than react to it?"
- Focus on: thinking and adaptability, future-drivenness, innovative thinking, organizational learning, what companies need to STOP doing, creating "Own Race" vs. participating reactively.

EVIDENCE RULE — Every claim must trace to the data provided. Include specific numbers when available (e.g., "60% of Finnish industrial companies sit in Cash Cow archetype"). Do not invent statistics.
PUBLIC OUTPUT RULE — Write for an executive reader. Do not expose internal method labels such as "[Strategic inference]" or fallback claim IDs such as "L2-FALLBACK-5" in the prose."""

OBSERVATORY_DOSSIER_VERSION = "observatory_dossier_v1"
OBSERVATORY_L1_MODEL = GPT_PRO_MODEL
OBSERVATORY_L2_MODEL = GPT_PRO_MODEL
OBSERVATORY_L3_MODEL = "claude-opus-4-8"
OBSERVATORY_INTERNAL_ID_RE = re.compile(r"\bL[123]-(?:CLAIM|FALLBACK)-\d+\b")
OBSERVATORY_FALLBACK_PAREN_RE = re.compile(r"\s*\(\s*L[123]-FALLBACK-\d+(?:\s*[/,;]\s*\d+)*\s*\)")
OBSERVATORY_METHOD_LABEL_RE = re.compile(
    r"(?:\*{1,2})?\[?\s*(?:strategic inference(?:\s*/\s*scenario hypothesis)?|scenario hypothesis)\s*\]?(?:\*{1,2})?\s*:?\s*",
    re.I,
)
OBSERVATORY_RAW_MARKDOWN_RE = re.compile(r"(^|[^*])\*(?!\*)([^*\n]{1,90})\*(?!\*)")
OBSERVATORY_DOMAIN_SECTION_TITLES = (
    "Domain Thesis",
    "Three Evidence Highlights",
    "What The Pattern Means",
    "Forces By Timing",
    "Strategic Implication",
    "Contrarian Signal",
)
OBSERVATORY_FINLAND_SECTION_TITLES = (
    "National Thesis",
    "Three Strategic Asymmetries",
    "What The Pattern Means",
    "Timing And Readiness Implications",
    "Contrarian Signal",
)
OBSERVATORY_ALL_SECTION_TITLES = tuple(dict.fromkeys(
    OBSERVATORY_DOMAIN_SECTION_TITLES + OBSERVATORY_FINLAND_SECTION_TITLES
))


def _observatory_section_re(titles: tuple[str, ...]) -> re.Pattern:
    return re.compile(
        r"(?im)^\s*(?:#{1,3}\s*)?(?:Section\s+\d+\s+[—-]\s*)?(?:\d+\s+[—-]\s*)?"
        r"("
        + "|".join(re.escape(title) for title in titles)
        + r")\s*:?\s*$"
    )


OBSERVATORY_DOMAIN_SECTION_RE = _observatory_section_re(OBSERVATORY_DOMAIN_SECTION_TITLES)
OBSERVATORY_FINLAND_SECTION_RE = _observatory_section_re(OBSERVATORY_FINLAND_SECTION_TITLES)
OBSERVATORY_ALL_SECTION_RE = _observatory_section_re(OBSERVATORY_ALL_SECTION_TITLES)
OBSERVATORY_FAKE_NUMERIC_HEADING_RE = re.compile(
    r"(?m)^\s*\d{1,3}\s+[—-]\s+(?!"
    + "|".join(re.escape(title) for title in OBSERVATORY_ALL_SECTION_TITLES)
    + r")"
)


# ═══════════════════════════════════════════════════════════════════════════════
# DATA AGGREGATION
# ═══════════════════════════════════════════════════════════════════════════════

def _aggregate_kb_signals(all_kb: dict) -> dict:
    """Aggregate all KB signals preserving full source context for LLM synthesis.

    Keeps full descriptions, lifecycle stages, momentum, confidence, and source
    credibility so the LLM can ground its analysis in actual source content
    rather than just metadata labels.
    """
    by_category = defaultdict(list)
    by_timeframe = defaultdict(int)
    by_force_type = defaultdict(int)
    by_lifecycle = defaultdict(int)
    by_momentum = defaultdict(int)
    by_credibility = defaultdict(int)
    by_theme_momentum = defaultdict(lambda: {"accelerating": 0, "stable": 0, "decelerating": 0, "total": 0})
    total = 0
    total_records = 0
    excluded_records = 0
    exclusion_reasons = Counter()

    for theme_slug, theme_data in all_kb.items():
        signals = theme_data.get("signals", [])
        for sig in signals:
            total_records += 1
            eligible, reason = _is_eligible_foresight_context(sig)
            if not eligible:
                excluded_records += 1
                exclusion_reasons[reason] += 1
                continue
            fp_theme = sig.get("fp_theme", "")
            category = THEME_CATEGORIES.get(fp_theme, "Unknown")
            evidence_id = _evidence_id("KB", sig.get("source_url"), sig.get("title"), fp_theme)
            by_category[category].append({
                "evidence_id": evidence_id,
                "theme": fp_theme,
                "dimension": sig.get("kb_dimension", ""),
                "dimensions_affected": sig.get("kb_dimensions_affected", []),
                "title": sig.get("title", ""),
                "description": sig.get("description", ""),  # Full description, not truncated
                "force_type": sig.get("force_type", ""),
                "timeframe": sig.get("timeframe", ""),
                "credibility": sig.get("source_credibility", "medium"),
                "confidence": sig.get("confidence", "medium"),
                "lifecycle_stage": sig.get("lifecycle_stage", ""),
                "trend_momentum": sig.get("trend_momentum", ""),
                "industries_affected": sig.get("industries_affected", []),
                "source_url": sig.get("source_url", ""),
                "source_count": sig.get("source_count", 0),
                "foresight_context_eligible": sig.get("foresight_context_eligible") is True,
                "theme_match": sig.get("theme_match") is True,
                "trend_supported": sig.get("trend_supported") is True,
                "source_readable": sig.get("source_readable") is not False,
                "source_registry_status": sig.get("source_registry_status", ""),
            })
            by_timeframe[sig.get("timeframe", "unknown")] += 1
            by_force_type[sig.get("force_type", "unknown")] += 1
            by_lifecycle[sig.get("lifecycle_stage", "unknown")] += 1
            momentum = sig.get("trend_momentum", "unknown")
            by_momentum[momentum] += 1
            by_credibility[sig.get("source_credibility", "unknown")] += 1
            total += 1

            # Per-theme momentum split (for the Theme Momentum chart in Future Demand).
            if fp_theme:
                if momentum == "accelerating":
                    by_theme_momentum[fp_theme]["accelerating"] += 1
                elif momentum == "decelerating":
                    by_theme_momentum[fp_theme]["decelerating"] += 1
                elif momentum == "stable":
                    by_theme_momentum[fp_theme]["stable"] += 1
                by_theme_momentum[fp_theme]["total"] += 1

    # Count accelerating vs decelerating
    accelerating = by_force_type.get("Probable/Emerging", 0) + by_force_type.get("Plausible/Maturing", 0)
    decelerating = by_force_type.get("Weakening/Legacy", 0) + by_force_type.get("Weakening/Declining", 0)

    # Top-15 themes by total signal count, with momentum breakdown.
    theme_momentum_list = sorted(
        (
            {"theme": theme, **counts}
            for theme, counts in by_theme_momentum.items()
        ),
        key=lambda x: x["total"],
        reverse=True,
    )[:15]

    return {
        "total_signals": total,
        "total_records": total_records,
        "eligible_records": total,
        "excluded_records": excluded_records,
        "exclusion_reasons": dict(exclusion_reasons),
        "by_category": dict(by_category),
        "by_timeframe": dict(by_timeframe),
        "by_force_type": dict(by_force_type),
        "by_lifecycle": dict(by_lifecycle),
        "by_momentum": dict(by_momentum),
        "by_credibility": dict(by_credibility),
        "by_theme_momentum": {t: dict(c) for t, c in by_theme_momentum.items()},
        "theme_momentum_top": theme_momentum_list,
        "accelerating": accelerating,
        "decelerating": decelerating,
        "theme_count": len(all_kb),
        "high_confidence_count": by_credibility.get("high", 0),
    }


def _evidence_id(prefix: str, *parts) -> str:
    raw = "|".join(str(part or "") for part in parts)
    return f"{prefix}-{hashlib.sha1(raw.encode('utf-8')).hexdigest()[:10].upper()}"


def _strip_internal_observatory_artifacts(text: str) -> str:
    """Remove audit/method artifacts that must never reach reader-facing prose."""
    if not text:
        return ""
    out = str(text)
    out = OBSERVATORY_FALLBACK_PAREN_RE.sub("", out)
    out = re.sub(r"\s*\(\s*L[123]-(?:CLAIM|FALLBACK)-\d+(?:\s*[,;/]\s*L[123]-(?:CLAIM|FALLBACK)-\d+)*\s*\)", "", out)
    out = OBSERVATORY_INTERNAL_ID_RE.sub("", out)
    out = OBSERVATORY_METHOD_LABEL_RE.sub("", out)
    out = re.sub(r"(?im)^\s*(?:#{1,3}\s*)?Section\s*$\n\s*(\d+\s+[—-])", r"\1", out)
    out = re.sub(r"(?im)^\s*(?:#{1,3}\s*)?Section\s*$", "", out)
    out = re.sub(r"(?<=\d)\.\s+(?=\d)", ".", out)
    out = re.sub(r"\(\s*(?:[,;:\s]|and|vs\.?|versus)*\)", "", out, flags=re.I)
    out = re.sub(r"\s+([,.;:])", r"\1", out)
    out = re.sub(r"([(\[])\s+", r"\1", out)
    out = re.sub(r"\s+([)\]])", r"\1", out)
    out = re.sub(r"[ \t]{2,}", " ", out)
    out = re.sub(r"\n{3,}", "\n\n", out)
    return out.strip()


def _public_claim_handoff(claims: list[dict]) -> list[dict]:
    """Create a prompt-safe claim handoff without internal IDs or evidence lists."""
    safe_claims = []
    for index, claim in enumerate(claims or []):
        if not isinstance(claim, dict):
            continue
        statement = _strip_internal_observatory_artifacts(str(claim.get("statement") or "").strip())
        if not statement:
            continue
        claim_type = str(claim.get("claim_type") or "strategic_interpretation").strip()
        if claim_type == "strategic_inference":
            claim_type = "strategic_interpretation"
        safe_claims.append({
            "handoff_index": index + 1,
            "statement": statement,
            "claim_type": claim_type,
            "confidence": max(0, min(100, int(claim.get("confidence") or 50))),
            "evidence_count": len(claim.get("evidence_ids") or []),
        })
    return safe_claims[:10]


def _prompt_safe_dossier(value):
    """Remove internal handoff identifiers from the dossier view shown to models."""
    if isinstance(value, dict):
        cleaned = {}
        for key, child in value.items():
            if key in {"claim_id", "extraction_model"}:
                continue
            if key == "limitations" and "deterministic handoff" in str(child).lower():
                continue
            if key == "strategic_inference_must_be_labelled":
                cleaned[key] = False
                continue
            cleaned[key] = _prompt_safe_dossier(child)
        return cleaned
    if isinstance(value, list):
        return [_prompt_safe_dossier(item) for item in value]
    if isinstance(value, str):
        return _strip_internal_observatory_artifacts(value)
    return value


def _sanitize_observatory_public_text(text: str) -> str:
    """Final public prose cleanup for Observatory app and HTML output."""
    if not text:
        return ""
    out = _strip_internal_observatory_artifacts(text)
    out = re.sub(r"\*{1,3}([^*\n]{1,160})\*{1,3}", r"\1", out)
    out = out.replace("*", "")
    out = re.sub(r"(?im)^\s*(?:#{1,3}\s*)?Section\s*$", "", out)
    out = re.sub(
        r"(?m)^\s*(\d{1,3})\s+[—-]\s+(?!"
        + "|".join(re.escape(title) for title in OBSERVATORY_ALL_SECTION_TITLES)
        + r")(.+)$",
        r"\2",
        out,
    )
    out = re.sub(r"(?i)\bidentifiedzero\b", "identified zero", out)
    out = re.sub(r"(?<=\d)\.\s+(?=\d)", ".", out)
    out = re.sub(r"\s+([,.;:])", r"\1", out)
    out = re.sub(r"[ \t]{2,}", " ", out)
    out = re.sub(r"\n{3,}", "\n\n", out)
    return out.strip()


def _extract_observatory_sections(text: str, titles: tuple[str, ...] = OBSERVATORY_DOMAIN_SECTION_TITLES) -> list[dict]:
    """Extract known public sections from generated Observatory prose."""
    if not text:
        return []
    title_lookup = {title.lower(): title for title in titles}
    if titles == OBSERVATORY_DOMAIN_SECTION_TITLES:
        section_re = OBSERVATORY_DOMAIN_SECTION_RE
    elif titles == OBSERVATORY_FINLAND_SECTION_TITLES:
        section_re = OBSERVATORY_FINLAND_SECTION_RE
    elif titles == OBSERVATORY_ALL_SECTION_TITLES:
        section_re = OBSERVATORY_ALL_SECTION_RE
    else:
        section_re = _observatory_section_re(titles)
    matches = list(section_re.finditer(text))
    sections: list[dict] = []
    for idx, match in enumerate(matches):
        raw_title = (match.group(1) or "").strip().lower()
        title = title_lookup.get(raw_title)
        if not title:
            continue
        start = match.end()
        end = matches[idx + 1].start() if idx + 1 < len(matches) else len(text)
        body = _sanitize_observatory_public_text(text[start:end].strip())
        if body:
            sections.append({"title": title, "text": body})
    seen = set()
    unique_sections = []
    for section in sections:
        title = section["title"]
        if title in seen:
            continue
        seen.add(title)
        unique_sections.append(section)
    return unique_sections


def _observatory_domain_metric_count(text: str) -> int:
    """Approximate reader-facing numeric highlights in one domain dossier."""
    if not text:
        return 0
    metric_patterns = (
        r"\bmarket health(?: score)?\b",
        r"\breality score\b",
        r"\bfinancial index\b",
        r"\bintent(?: score)?\b",
        r"\bThriving ratio\b",
        r"\baverage reality\b",
        r"\baverage financial\b",
    )
    return sum(len(re.findall(pattern, text, flags=re.I)) for pattern in metric_patterns)


def _observatory_public_quality(text: str, label: str) -> dict:
    fatal = []
    repairable = []
    warnings = []
    raw = text or ""
    word_count = len(raw.split())
    if not raw.strip():
        fatal.append("empty_output")
    elif word_count < 50:
        fatal.append("too_short_output")
    if OBSERVATORY_INTERNAL_ID_RE.search(raw):
        repairable.append("internal_claim_id_leak")
    if OBSERVATORY_METHOD_LABEL_RE.search(raw):
        repairable.append("method_label_leak")
    if re.search(r"(?im)^\s*(?:#{1,3}\s*)?Section\s*$", raw):
        repairable.append("orphan_section_label")
    if OBSERVATORY_RAW_MARKDOWN_RE.search(raw):
        repairable.append("raw_markdown_marker")
    paragraphs = [p.strip() for p in re.split(r"\n{2,}", raw) if p.strip()]
    max_paragraph_chars = max((len(p) for p in paragraphs), default=0)
    is_domain = label.startswith("domain:")
    if max_paragraph_chars > 1200:
        warnings.append("long_paragraph")
    if is_domain:
        if OBSERVATORY_FAKE_NUMERIC_HEADING_RE.search(raw):
            repairable.append("fake_numeric_heading")
        sections = _extract_observatory_sections(raw, OBSERVATORY_DOMAIN_SECTION_TITLES)
        if sections and len(sections) < 4:
            warnings.append("partial_domain_section_structure")
        if _observatory_domain_metric_count(raw) > 3:
            warnings.append("metric_heavy_domain_dossier")
    if label == "finland_analysis":
        if OBSERVATORY_FAKE_NUMERIC_HEADING_RE.search(raw):
            warnings.append("fake_numeric_heading")
        sections = _extract_observatory_sections(raw, OBSERVATORY_FINLAND_SECTION_TITLES)
        if sections and len(sections) < 4:
            warnings.append("partial_finland_section_structure")
        if _observatory_domain_metric_count(raw) > 5:
            warnings.append("metric_heavy_finland_analysis")
    if fatal:
        status = "fatal"
    elif repairable:
        status = "repairable"
    elif warnings:
        status = "warning"
    else:
        status = "clean"
    return {
        "label": label,
        "passed": status in {"clean", "warning"},
        "status": status,
        "fatal": fatal,
        "repairable": repairable,
        "issues": fatal + repairable,
        "warnings": warnings,
        "max_paragraph_chars": max_paragraph_chars,
        "word_count": word_count,
    }


def _rewrite_observatory_public_text_for_hygiene(text: str, label: str, titles: tuple[str, ...]) -> str:
    """Best-effort prose hygiene rewrite. Never required for saving."""
    if not text or not text.strip():
        return text or ""
    prompt = f"""Clean the following Observatory public prose for formatting/readability only.

Rules:
- Preserve the meaning, evidence boundaries, domain conclusions, and all factual claims.
- Do not add new claims, evidence, companies, scores, or recommendations.
- Remove orphan words like "Section".
- Remove raw markdown markers such as *, **, ***.
- Convert fake numeric headings such as "75 — ..." into normal prose.
- Keep or restore only these section headings when they are already present or naturally fit: {", ".join(titles)}.
- Return cleaned prose only, no commentary.

TEXT:
{text[:12000]}"""
    try:
        cleaned = call_openai_text(
            prompt,
            model=GPT_REASONING_MODEL,
            max_output_tokens=min(7000, max(1800, len(text.split()) * 2)),
            timeout=90,
            max_retries=1,
            reasoning_effort="low",
        )
        return _sanitize_observatory_public_text(cleaned or text)
    except Exception as exc:
        print(f"   [Observatory] QA rewrite unavailable for {label} (non-blocking): {type(exc).__name__}: {exc}")
        return text


def _finalize_observatory_output_text(
    text: str,
    label: str,
    titles: tuple[str, ...],
    *,
    allow_rewrite: bool = False,
) -> tuple[str, list[dict], dict]:
    """Sanitize, optionally rewrite, and return reader-facing sections + QA."""
    initial = _observatory_public_quality(text or "", label)
    cleaned = _sanitize_observatory_public_text(text or "")
    cleaned_check = _observatory_public_quality(cleaned, label)
    attempts = [
        {"stage": "initial", **initial},
        {"stage": "deterministic_sanitize", **cleaned_check},
    ]
    best_text = cleaned
    best_check = cleaned_check

    if allow_rewrite and best_check.get("repairable") and not best_check.get("fatal"):
        rewritten = _rewrite_observatory_public_text_for_hygiene(best_text, label, titles)
        rewritten_check = _observatory_public_quality(rewritten, label)
        attempts.append({"stage": "hygiene_rewrite", **rewritten_check})
        if len(rewritten_check.get("fatal") or []) <= len(best_check.get("fatal") or []) and (
            len(rewritten_check.get("repairable") or []) <= len(best_check.get("repairable") or [])
        ):
            best_text = rewritten
            best_check = rewritten_check

    repaired_issue_count = max(
        0,
        len(initial.get("repairable") or []) - len(best_check.get("repairable") or []),
    )
    qa = {
        **best_check,
        "attempts": attempts,
        "repaired_issue_count": repaired_issue_count,
        "saved_with_warnings": bool(best_check.get("repairable") or best_check.get("warnings")),
    }
    sections = _extract_observatory_sections(best_text, titles)
    return best_text, sections, qa


def _merge_observatory_public_qa(current: dict, prior=None) -> dict:
    """Preserve generation-time QA attempts when final payload sanitation rechecks text."""
    if not isinstance(prior, dict):
        return current
    merged = dict(current)
    if prior.get("attempts"):
        merged["attempts"] = prior.get("attempts")
    merged["repaired_issue_count"] = max(
        int(current.get("repaired_issue_count") or 0),
        int(prior.get("repaired_issue_count") or 0),
    )
    merged["saved_with_warnings"] = bool(
        current.get("saved_with_warnings")
        or prior.get("saved_with_warnings")
        or current.get("warnings")
        or current.get("repairable")
    )
    if prior.get("status") == "repairable" and current.get("status") == "clean":
        merged["status"] = "warning"
        merged.setdefault("warnings", [])
        if "repaired_public_output_issues" not in merged["warnings"]:
            merged["warnings"] = list(merged["warnings"]) + ["repaired_public_output_issues"]
    return merged


def _sanitize_observatory_payload(result: dict) -> dict:
    """Sanitize reader-facing Observatory text and attach non-fatal QA metadata."""
    checks = []
    global_text = (result.get("global_analysis") or {}).get("text") or ""
    if global_text or result.get("global_analysis"):
        prior_qa = result["global_analysis"].get("public_output_quality")
        text, sections, qa = _finalize_observatory_output_text(
            global_text,
            "global_analysis",
            OBSERVATORY_ALL_SECTION_TITLES,
            allow_rewrite=False,
        )
        qa = _merge_observatory_public_qa(qa, prior_qa)
        result["global_analysis"]["text"] = text
        result["global_analysis"]["sections"] = sections
        result["global_analysis"]["public_output_quality"] = qa
        checks.append(qa)

    finland_text = (result.get("finland_analysis") or {}).get("text") or ""
    if finland_text or result.get("finland_analysis"):
        prior_qa = result["finland_analysis"].get("public_output_quality")
        text, sections, qa = _finalize_observatory_output_text(
            finland_text,
            "finland_analysis",
            OBSERVATORY_FINLAND_SECTION_TITLES,
            allow_rewrite=False,
        )
        qa = _merge_observatory_public_qa(qa, prior_qa)
        result["finland_analysis"]["text"] = text
        result["finland_analysis"]["sections"] = sections
        result["finland_analysis"]["public_output_quality"] = qa
        checks.append(qa)

    filtered_domains = []
    kept_dossiers = []
    for dossier in result.get("domain_dossiers") or []:
        if not isinstance(dossier, dict):
            continue
        if str(dossier.get("domain") or "").strip().lower() == "other":
            filtered_domains.append(dossier.get("domain") or "Other")
            continue
        text = dossier.get("text") or ""
        label = f"domain:{dossier.get('domain') or 'unknown'}"
        prior_qa = dossier.get("public_output_quality")
        text, sections, qa = _finalize_observatory_output_text(
            text,
            label,
            OBSERVATORY_DOMAIN_SECTION_TITLES,
            allow_rewrite=True,
        )
        qa = _merge_observatory_public_qa(qa, prior_qa)
        dossier["text"] = text
        dossier["sections"] = sections
        dossier["public_output_quality"] = qa
        checks.append(qa)
        kept_dossiers.append(dossier)
    result["domain_dossiers"] = kept_dossiers

    fatal = [check for check in checks if check.get("fatal")]
    repairable = [check for check in checks if check.get("repairable")]
    warning = [check for check in checks if check.get("warnings")]
    status = "fatal_failed" if fatal else "saved_with_warnings" if (repairable or warning or filtered_domains) else "clean"
    result["public_output_quality"] = {
        "passed": not fatal and not repairable,
        "status": status,
        "checks": checks,
        "blocking_issue_count": sum(len(check.get("fatal") or []) for check in checks),
        "repairable_issue_count": sum(len(check.get("repairable") or []) for check in checks),
        "warning_count": sum(len(check.get("warnings") or []) for check in checks),
        "repaired_issue_count": sum(int(check.get("repaired_issue_count") or 0) for check in checks),
        "filtered": {"domain_dossiers": filtered_domains},
    }
    if fatal:
        labels = ", ".join(check["label"] for check in fatal[:5])
        raise ValueError(f"Observatory fatal public-output QA failed after sanitation: {labels}")
    return result


def _is_eligible_foresight_context(record: dict) -> tuple[bool, str]:
    """Use only explicit, readable foresight context; never infer eligibility."""
    if not isinstance(record, dict):
        return False, "invalid_record"
    if record.get("foresight_context_eligible") is not True:
        return False, "not_context_eligible"
    status = str(record.get("foresight_signal_status") or record.get("source_audit_status") or "").lower()
    if any(token in status for token in ("dead", "unreadable", "excluded", "rejected")):
        return False, status or "excluded"
    if record.get("source_readable") is False:
        return False, "source_unreadable"
    return True, "eligible"


def _filter_company_foresight(company_details: list) -> tuple[list, dict]:
    """Create an Observatory-only view with explicit company foresight context."""
    filtered = []
    total = eligible = 0
    reasons = Counter()
    for company in company_details:
        item = dict(company)
        kept = []
        for link in company.get("foresight_links") or []:
            total += 1
            ok, reason = _is_eligible_foresight_context(link)
            if ok:
                kept.append(link)
                eligible += 1
            else:
                reasons[reason] += 1
        item["foresight_links"] = kept
        filtered.append(item)
    return filtered, {
        "total_records": total,
        "eligible_records": eligible,
        "excluded_records": total - eligible,
        "exclusion_reasons": dict(reasons),
    }


def _load_all_data(storage: BlobStorage):
    """Load all data needed for observatory generation."""
    print("   [Observatory] Loading all data...")

    # 1. KB signals
    all_kb = storage.get_all_foresight_kb()
    kb_aggregate = _aggregate_kb_signals(all_kb)
    print(f"   [Observatory] KB: {kb_aggregate['total_signals']} signals across {kb_aggregate['theme_count']} themes")

    # 2. Dashboard + company details
    dashboard_data = storage.retrieve_latest_data("index/summary_list") or []
    company_details = []
    for item in dashboard_data:
        detail = storage.get_company_data(item.get("companyName", ""))
        if detail:
            company_details.append(detail)
    company_details, company_foresight_stats = _filter_company_foresight(company_details)
    print(
        f"   [Observatory] Companies: {len(dashboard_data)} summary, {len(company_details)} detailed; "
        f"company foresight {company_foresight_stats['eligible_records']}/{company_foresight_stats['total_records']} eligible"
    )

    # 3. Market intelligence
    analysis_result = run_analysis(dashboard_data) if dashboard_data else {}
    mi = compute_market_intelligence(dashboard_data, company_details, analysis_result) if dashboard_data else {}

    return {
        "all_kb": all_kb,
        "kb_aggregate": kb_aggregate,
        "dashboard_data": dashboard_data,
        "company_details": company_details,
        "company_foresight_stats": company_foresight_stats,
        "market_intel": mi,
    }


def _latest_snapshot_date(records: list) -> str | None:
    candidates = []
    for record in records:
        if not isinstance(record, dict):
            continue
        for key in ("updated_at", "scan_date", "scanned_at", "timestamp", "generated_at", "date"):
            value = record.get(key)
            if value:
                candidates.append(str(value))
    return max(candidates) if candidates else None


def _build_input_manifest(data: dict) -> dict:
    kb = data["kb_aggregate"]
    company_stats = data["company_foresight_stats"]
    kb_signals = [
        signal
        for theme_data in (data.get("all_kb") or {}).values()
        for signal in (theme_data.get("signals") or [])
        if isinstance(signal, dict)
    ]
    return {
        "generated_at": datetime.utcnow().isoformat(),
        "contract_version": "observatory_evidence_v1",
        "central_foresight_kb": {
            "themes_loaded": kb.get("theme_count", 0),
            "latest_signal_snapshot": _latest_snapshot_date(kb_signals),
            "records_total": kb.get("total_records", 0),
            "records_eligible": kb.get("eligible_records", 0),
            "records_excluded": kb.get("excluded_records", 0),
            "exclusion_reasons": kb.get("exclusion_reasons", {}),
            "eligibility_contract": "foresight_context_eligible=true",
        },
        "company_portfolio": {
            "summary_records": len(data.get("dashboard_data") or []),
            "detailed_records": len(data.get("company_details") or []),
            "latest_snapshot_date": _latest_snapshot_date(data.get("company_details") or []),
            "foresight_records_total": company_stats.get("total_records", 0),
            "foresight_records_eligible": company_stats.get("eligible_records", 0),
            "foresight_records_excluded": company_stats.get("excluded_records", 0),
            "foresight_exclusion_reasons": company_stats.get("exclusion_reasons", {}),
        },
        "market_intelligence": {"recomputed_from_current_company_snapshot": True},
        "source_boundaries": {
            "macro_context": "independent central Foresight KB",
            "portfolio_patterns": "stored Company Summary data",
            "company_evidence_db": "not read directly by Observatory",
            "company_source_ledger": "not read by Observatory",
            "people_and_influence": "not analyzed by Observatory; Buying Insight owns this layer",
        },
    }


def _build_structured_findings(kb_aggregate: dict, mi: dict) -> list:
    findings = []
    all_signals = [
        signal
        for signals in kb_aggregate.get("by_category", {}).values()
        for signal in signals
    ]
    all_signals.sort(
        key=lambda signal: (
            signal.get("credibility") == "high",
            signal.get("trend_momentum") == "accelerating",
            signal.get("source_count", 0),
        ),
        reverse=True,
    )
    for signal in all_signals[:6]:
        findings.append({
            "finding_id": _evidence_id("FINDING", signal.get("evidence_id"), signal.get("title")),
            "finding_type": "macro_force",
            "statement": signal.get("title") or "Unnamed macro force",
            "evidence_ids": [signal.get("evidence_id")],
            "confidence_basis": "eligible central KB signal",
        })

    for gap in (mi.get("gap_patterns") or [])[:5]:
        statement = (
            f"{gap.get('dimension', 'Unknown dimension')} has an average intent-reality gap "
            f"of {gap.get('avg_gap', 'n/a')} across {gap.get('company_count', 0)} companies."
        )
        findings.append({
            "finding_id": _evidence_id("FINDING", "portfolio_gap", statement),
            "finding_type": "portfolio_pattern",
            "statement": statement,
            "evidence_ids": [_evidence_id("PORTFOLIO", gap.get("dimension"), gap.get("company_count"))],
            "confidence_basis": "computed company-portfolio aggregate",
        })
    return findings


def _format_structured_findings(findings: list) -> str:
    if not findings:
        return "No pre-computed findings available."
    return "\n".join(
        f"- [{item['finding_id']}] {item['statement']} Evidence: {', '.join(item['evidence_ids'])}"
        for item in findings
    )


def _top_macro_evidence(kb_aggregate: dict, per_category: int = 5) -> list[dict]:
    """Select a bounded, evidence-ID-preserving macro packet for analysis."""
    selected = []
    for category, signals in (kb_aggregate.get("by_category") or {}).items():
        ranked = sorted(
            signals,
            key=lambda signal: (
                signal.get("credibility") == "high",
                signal.get("trend_momentum") == "accelerating",
                signal.get("source_count", 0),
            ),
            reverse=True,
        )[:per_category]
        for signal in ranked:
            selected.append({
                "evidence_id": signal.get("evidence_id"),
                "category": category,
                "theme": signal.get("theme"),
                "title": signal.get("title"),
                "description": signal.get("description"),
                "force_type": signal.get("force_type"),
                "timeframe": signal.get("timeframe"),
                "lifecycle_stage": signal.get("lifecycle_stage"),
                "trend_momentum": signal.get("trend_momentum"),
                "source_credibility": signal.get("credibility"),
                "source_url": signal.get("source_url"),
                "dimensions_affected": signal.get("dimensions_affected") or [],
                "industries_affected": signal.get("industries_affected") or [],
            })
    return selected


def _build_l1_evidence_dossier(
    kb_aggregate: dict,
    mi: dict,
    structured_findings: list,
    input_manifest: dict,
) -> dict:
    """Compile the locked macro-evidence input for Layer 1."""
    macro_evidence = _top_macro_evidence(kb_aggregate)
    macro_ids = {item.get("evidence_id") for item in macro_evidence if item.get("evidence_id")}
    macro_findings = [
        finding for finding in structured_findings
        if finding.get("finding_type") == "macro_force"
        and any(evidence_id in macro_ids for evidence_id in finding.get("evidence_ids") or [])
    ]
    macro_cross_impact = []
    for signal in macro_evidence:
        dimensions = signal.get("dimensions_affected") or []
        if len(dimensions) >= 2:
            macro_cross_impact.append({
                "theme": signal.get("theme"),
                "dimensions": dimensions,
                "evidence_ids": [signal.get("evidence_id")],
                "candidate_type": "macro_force_convergence",
            })

    return {
        "schema_version": OBSERVATORY_DOSSIER_VERSION,
        "layer": "L1_global_force_landscape",
        "locked_at": datetime.utcnow().isoformat(),
        "evidence_contract": {
            "allowed_claim_types": ["macro_force", "cross_impact", "strategic_inference"],
            "company_specific_claims_allowed": False,
            "material_claims_require_evidence_ids": True,
            "strategic_inference_must_be_labelled": True,
        },
        "input_manifest": input_manifest.get("central_foresight_kb", {}),
        "signal_summary": {
            "total_eligible": kb_aggregate.get("eligible_records", 0),
            "themes": kb_aggregate.get("theme_count", 0),
            "accelerating": kb_aggregate.get("accelerating", 0),
            "decelerating": kb_aggregate.get("decelerating", 0),
            "by_timeframe": kb_aggregate.get("by_timeframe", {}),
            "by_force_type": kb_aggregate.get("by_force_type", {}),
            "by_lifecycle": kb_aggregate.get("by_lifecycle", {}),
            "by_momentum": kb_aggregate.get("by_momentum", {}),
        },
        "macro_evidence": macro_evidence,
        "cross_impact_candidates": macro_cross_impact[:30],
        "precomputed_findings": macro_findings,
        "quality_notes": {
            "excluded_kb_records": kb_aggregate.get("excluded_records", 0),
            "exclusion_reasons": kb_aggregate.get("exclusion_reasons", {}),
            "freshness_gap": "Use source dates when present; absence of a date lowers certainty.",
        },
    }


def _allowed_evidence_ids(dossier: dict) -> set[str]:
    ids: set[str] = set()

    def collect(value):
        if isinstance(value, dict):
            if value.get("evidence_id"):
                ids.add(str(value["evidence_id"]))
            if value.get("finding_id"):
                ids.add(str(value["finding_id"]))
            ids.update(str(item) for item in value.get("evidence_ids") or [] if item)
            for child in value.values():
                collect(child)
        elif isinstance(value, list):
            for child in value:
                collect(child)

    collect(dossier)
    return ids


def _extract_layer_claims(analysis: str, dossier: dict, layer: str) -> list[dict]:
    """Convert narrative output into a bounded evidence-ID-backed handoff."""
    allowed_ids = _allowed_evidence_ids(dossier)
    prompt = f"""Extract the major claims from this {layer} strategic analysis for use by the next Observatory layer.

Return JSON only:
{{"claims": [{{"claim_id": "{layer}-CLAIM-1", "statement": "...", "claim_type": "macro_evidence|portfolio_evidence|strategic_inference", "evidence_ids": ["..."], "confidence": 0, "limitations": "..."}}]}}

Rules:
- Return at most 10 claims.
- Evidence IDs must come from this allowed list: {json.dumps(sorted(allowed_ids))}.
- A strategic inference must still cite the evidence IDs it interprets.
- Drop claims that cannot be tied to an allowed evidence ID.
- Confidence is 0-100.

ANALYSIS:
{analysis}
"""
    try:
        raw, model_used = call_json_model_chain(
            prompt,
            (GPT_REASONING_MODEL, "claude-sonnet-4-6"),
            max_tokens=2600,
            timeout=120,
            reasoning_effort="high",
        )
        parsed = parse_json_object(raw)
        claims = []
        for index, item in enumerate(parsed.get("claims") or []):
            if not isinstance(item, dict) or not str(item.get("statement") or "").strip():
                continue
            evidence_ids = [eid for eid in item.get("evidence_ids") or [] if eid in allowed_ids]
            if not evidence_ids:
                continue
            claims.append({
                "claim_id": item.get("claim_id") or f"{layer}-CLAIM-{index + 1}",
                "statement": str(item["statement"]).strip(),
                "claim_type": item.get("claim_type") or "strategic_inference",
                "evidence_ids": evidence_ids,
                "confidence": max(0, min(100, int(item.get("confidence") or 50))),
                "limitations": str(item.get("limitations") or "").strip(),
                "extraction_model": model_used,
            })
        if claims:
            return claims[:10]
    except Exception as exc:
        print(f"   [Observatory] {layer} claim extraction unavailable: {exc}")

    # Deterministic fallback keeps the layer handoff structured and auditable.
    fallback = []
    for index, finding in enumerate(dossier.get("precomputed_findings") or []):
        evidence_ids = [eid for eid in finding.get("evidence_ids") or [] if eid in allowed_ids]
        if evidence_ids:
            fallback.append({
                "claim_id": f"{layer}-FALLBACK-{index + 1}",
                "statement": finding.get("statement"),
                "claim_type": finding.get("finding_type") or "strategic_inference",
                "evidence_ids": evidence_ids,
                "confidence": 50,
                "limitations": "Deterministic handoff because model claim extraction was unavailable.",
                "extraction_model": "deterministic",
            })
    return fallback[:10]


def _build_l2_evidence_dossier(
    mi: dict,
    l1_claims: list[dict],
    structured_findings: list,
    input_manifest: dict,
) -> dict:
    """Compile Finnish portfolio evidence and validated L1 claims for Layer 2."""
    portfolio_findings = [
        finding for finding in structured_findings
        if finding.get("finding_type") == "portfolio_pattern"
    ]
    market_health = [
        {**item, "evidence_id": _evidence_id("PORTFOLIO-HEALTH", item.get("domain"))}
        for item in (mi.get("market_health") or [])
    ]
    archetype_flow = [
        {**item, "evidence_id": _evidence_id("PORTFOLIO-ARCHETYPE", item.get("domain"))}
        for item in (mi.get("archetype_flow") or [])
    ]
    gap_patterns = [
        {**item, "evidence_id": _evidence_id("PORTFOLIO-GAP", item.get("dimension"), item.get("company_count"))}
        for item in (mi.get("gap_patterns") or [])
    ]
    top_foresights = [
        {**item, "evidence_id": _evidence_id("PORTFOLIO-FORESIGHT", item.get("theme"), item.get("count"))}
        for item in (mi.get("top_foresights") or [])[:15]
    ]
    favi_segments = {
        segment: {
            "evidence_id": _evidence_id("PORTFOLIO-FAVI", segment),
            "count": count,
        }
        for segment, count in (mi.get("favi_segments") or {}).items()
    }
    cross_impact = [
        {**item, "evidence_id": _evidence_id(
            "PORTFOLIO-CROSS-IMPACT",
            item.get("theme") or item.get("source_theme"),
            item.get("dimension") or item.get("target_dimension"),
        )}
        for item in (mi.get("cross_impact") or [])[:30]
    ]
    return {
        "schema_version": OBSERVATORY_DOSSIER_VERSION,
        "layer": "L2_finnish_economy_force_map",
        "locked_at": datetime.utcnow().isoformat(),
        "evidence_contract": {
            "macro_input": "structured L1 claims only; the L1 essay is intentionally excluded",
            "portfolio_claims_require_computed_metrics": True,
            "material_claims_require_evidence_ids": True,
            "strategic_inference_must_be_labelled": False,
            "public_prose_excludes_internal_method_labels": True,
        },
        "input_manifest": input_manifest.get("company_portfolio", {}),
        "validated_l1_claims": l1_claims,
        "market_health": market_health,
        "archetype_flow": archetype_flow,
        "favi_segments": favi_segments,
        "gap_patterns": gap_patterns,
        "top_foresights": top_foresights,
        "cross_impact": cross_impact,
        "company_count": mi.get("company_count", 0),
        "detailed_count": mi.get("detailed_count", 0),
        "precomputed_findings": portfolio_findings,
    }


def _build_l3_evidence_dossier(
    domain: str,
    domain_config: dict,
    domain_companies: list[dict],
    kb_signals: list[dict],
    market_health: dict | None,
    l1_claims: list[dict],
    l2_claims: list[dict],
    structured_findings: list[dict],
) -> dict:
    """Compile one domain's evidence and upstream claim handoffs for Opus."""
    company_metrics = []
    for company in domain_companies:
        name = company.get("company_name") or company.get("companyName") or "Unknown"
        archetype = company.get("archetype")
        if not archetype and company.get("reality_score") is not None:
            comparison_score = company.get("financial_index")
            if comparison_score is None:
                comparison_score = company.get("intent_score")
            if comparison_score is not None:
                archetype = classify_archetype(
                    float(company["reality_score"]),
                    float(comparison_score),
                )
        company_metrics.append({
            "evidence_id": _evidence_id("DOMAIN-COMPANY", domain, name),
            "company_name": name,
            "reality_score": company.get("reality_score"),
            "intent_score": company.get("intent_score"),
            "financial_index": company.get("financial_index"),
            "archetype": archetype,
            "why": company.get("why"),
            "what": company.get("what"),
        })

    domain_signals = []
    for signal in kb_signals[:20]:
        domain_signals.append({
            "evidence_id": signal.get("evidence_id") or _evidence_id(
                "DOMAIN-FORESIGHT", domain, signal.get("source_url"), signal.get("title")
            ),
            "title": signal.get("title"),
            "description": signal.get("description"),
            "semantic_snippet": signal.get("semantic_snippet"),
            "theme": signal.get("fp_theme") or signal.get("theme"),
            "timeframe": signal.get("timeframe") or signal.get("time_horizon"),
            "force_type": signal.get("force_type"),
            "lifecycle_stage": signal.get("lifecycle_stage") or signal.get("s_curve_phase"),
            "trend_momentum": signal.get("trend_momentum") or signal.get("signal_direction"),
            "source_credibility": signal.get("source_credibility"),
            "source_url": signal.get("source_url"),
        })

    relevant_findings = [
        finding for finding in structured_findings
        if domain.lower() in json.dumps(finding, ensure_ascii=False).lower()
    ]
    return {
        "schema_version": OBSERVATORY_DOSSIER_VERSION,
        "layer": "L3_domain_force_dossier",
        "domain": domain,
        "locked_at": datetime.utcnow().isoformat(),
        "evidence_contract": {
            "upstream_context": "structured L1/L2 claims only; upstream essays are excluded",
            "company_metrics_are_portfolio_evidence": True,
            "foresight_signals_are_macro_context_not_company_facts": True,
            "material_claims_require_evidence_ids": True,
            "strategic_inference_must_be_labelled": False,
            "public_prose_excludes_internal_method_labels": True,
        },
        "domain_configuration": {
            "fp_themes": domain_config.get("fp_themes") or [],
            "priority_dimensions": domain_config.get("priority_dimensions") or [],
        },
        "validated_l1_claims": l1_claims,
        "validated_l2_claims": l2_claims,
        "market_health": {
            **(market_health or {}),
            "evidence_id": _evidence_id("DOMAIN-HEALTH", domain),
        },
        "company_metrics": company_metrics,
        "domain_foresight_signals": domain_signals,
        "precomputed_findings": relevant_findings,
    }


def _build_confidence_summary(data: dict, verifications: list) -> dict:
    kb = data["kb_aggregate"]
    total_kb = max(kb.get("total_records", 0), 1)
    macro_score = round(100 * kb.get("eligible_records", 0) / total_kb)

    summary_count = len(data.get("dashboard_data") or [])
    detail_count = len(data.get("company_details") or [])
    portfolio_score = round(100 * detail_count / max(summary_count, 1)) if summary_count else 0

    statuses = [v.get("status", "unavailable") for v in verifications if isinstance(v, dict)]
    verified = statuses.count("verified")
    failed = statuses.count("failed")
    unavailable = statuses.count("unavailable")
    interpretation_score = round(100 * verified / max(len(statuses), 1)) if statuses else 0
    interpretation_status = "verified" if statuses and failed == 0 and unavailable == 0 else "mixed"
    if not statuses or unavailable == len(statuses):
        interpretation_status = "unavailable"
    elif failed:
        interpretation_status = "failed"

    return {
        "macro_force_evidence": {
            "score": macro_score,
            "status": "strong" if macro_score >= 70 else "limited",
            "basis": f"{kb.get('eligible_records', 0)}/{kb.get('total_records', 0)} central KB records eligible",
        },
        "company_portfolio_pattern": {
            "score": portfolio_score,
            "status": "strong" if portfolio_score >= 70 else "limited",
            "basis": f"{detail_count}/{summary_count} portfolio companies have detailed records",
        },
        "strategic_interpretation": {
            "score": interpretation_score,
            "status": interpretation_status,
            "basis": (
                "Interpretation verification not available"
                if interpretation_status == "unavailable"
                else f"{verified} interpretation outputs verified, {failed} failed"
            ),
        },
    }


# ═══════════════════════════════════════════════════════════════════════════════
# PROMPT BUILDERS
# ═══════════════════════════════════════════════════════════════════════════════

def _format_top_signals_per_category(by_category: dict, per_cat: int = 5) -> str:
    """Format the top signals per category with full source context for the LLM.

    Selects the most important signals (high credibility + accelerating momentum)
    and includes full descriptions so the LLM can ground synthesis in actual content.
    """
    blocks = []
    for cat in ALL_CATEGORIES:
        signals = by_category.get(cat, [])
        if not signals:
            continue

        # Prioritize: high credibility → accelerating momentum → Probable/Emerging type
        def signal_priority(s):
            cred = {"high": 3, "medium": 2, "low": 1}.get(s.get("credibility", ""), 1)
            momentum = {"accelerating": 3, "stable": 2, "decelerating": 1}.get(s.get("trend_momentum", ""), 1)
            force = {"Probable/Emerging": 3, "Plausible/Maturing": 2, "Preposterous/Black Swan": 2}.get(s.get("force_type", ""), 1)
            return cred + momentum + force

        sorted_signals = sorted(signals, key=signal_priority, reverse=True)
        top = sorted_signals[:per_cat]

        lines = [f"\n### {cat} ({len(signals)} signals total, top {len(top)} shown):"]
        for s in top:
            lifecycle = s.get("lifecycle_stage", "")
            momentum = s.get("trend_momentum", "")
            confidence = s.get("confidence", "")
            source_count = s.get("source_count", 0)
            dims = s.get("dimensions_affected") or [s.get("dimension", "")]
            industries = s.get("industries_affected", [])

            line = f"- [{s.get('evidence_id', 'NO-ID')}] **{s['title']}** [{s.get('force_type', '?')} | {s.get('timeframe', '?')}]\n"
            line += f"  Description: {s.get('description', 'N/A')}\n"
            line += f"  Lifecycle: {lifecycle} | Momentum: {momentum} | Credibility: {s.get('credibility', '?')} ({source_count} verified sources)\n"
            if dims:
                line += f"  Dimensions affected: {', '.join(dims)}\n"
            if industries:
                line += f"  Industries affected: {', '.join(industries[:4])}"
            lines.append(line)

        blocks.append("\n".join(lines))

    return "\n\n".join(blocks)


def _build_global_prompt(
    kb_aggregate: dict,
    cross_impact: list,
    top_foresights: list,
    structured_findings: str = "",
    evidence_dossier=None,
) -> str:
    """Build prompt for Output 1: Global Force Landscape."""
    # Rich signal evidence — top signals per category with full descriptions
    signal_evidence = _format_top_signals_per_category(kb_aggregate["by_category"], per_cat=5)

    # Top themes by cross-impact density
    cross_summary = ""
    if evidence_dossier is not None:
        cross_summary = "\n".join(
            f"- {item.get('theme')}: {', '.join(item.get('dimensions') or [])} "
            f"(Evidence: {', '.join(item.get('evidence_ids') or [])})"
            for item in (evidence_dossier.get("cross_impact_candidates") or [])
        ) or "No evidence-linked macro convergence candidate met the dossier rule."
    elif cross_impact:
        theme_counts = Counter()
        for cell in cross_impact:
            theme_counts[cell["theme"]] += cell["count"]
        top_cross = theme_counts.most_common(8)
        cross_summary = "\n".join(f"- {t}: {c} cross-dimensional signals" for t, c in top_cross)

    # Top foresights
    foresight_summary = ""
    if evidence_dossier is not None:
        foresight_summary = "Excluded from Layer 1. Company-portfolio prevalence belongs to the Layer 2 dossier."
    elif top_foresights:
        foresight_summary = "\n".join(
            f"- {f['theme']} ({f['category']}): {f['count']} companies affected ({f['pct']}%)"
            for f in top_foresights[:10]
        )

    locked_dossier = json.dumps(_prompt_safe_dossier(evidence_dossier or {}), ensure_ascii=False, indent=2)

    return f"""{SYSTEM_ROLE}

TASK: Write a Global Force Landscape briefing — a futurologist's synthesis of the forces reshaping business globally.

LOCKED LAYER 1 EVIDENCE DOSSIER ({OBSERVATORY_DOSSIER_VERSION}):
{locked_dossier}

DOSSIER RULE:
- Treat the dossier above as the authoritative evidence boundary.
- Use the summaries below only as readable views of the same dossier, not as additional evidence.
- Cite evidence IDs after material claims.
- Keep interpretation visibly evidence-grounded, but write inference in plain executive language without method labels.

DATA PROVIDED:

Signal Overview:
- Total validated signals: {kb_aggregate['total_signals']}
- Themes covered: {kb_aggregate['theme_count']} of 30
- High-credibility signals (3+ verified sources): {kb_aggregate.get('high_confidence_count', 0)}
- Accelerating forces: {kb_aggregate['accelerating']}
- Decelerating forces: {kb_aggregate['decelerating']}

Timeframe distribution:
{json.dumps(kb_aggregate['by_timeframe'], indent=2)}

Force type distribution:
{json.dumps(kb_aggregate['by_force_type'], indent=2)}

Signal lifecycle distribution (adoption curve position):
{json.dumps(kb_aggregate.get('by_lifecycle', {}), indent=2)}

Signal momentum distribution:
{json.dumps(kb_aggregate.get('by_momentum', {}), indent=2)}

DETAILED SIGNAL EVIDENCE BY CATEGORY:
(Top signals per category with full source context — use these descriptions to ground your synthesis in actual evidence, not just signal titles)
{signal_evidence}

Cross-impact density (theme x dimension interactions):
{cross_summary}

Top foresight themes affecting companies:
{foresight_summary}

PRE-COMPUTED EVIDENCE-LINKED FINDINGS:
{structured_findings}

ANALYTICAL STRUCTURE (Popper's Foresight Framework):

Section 1 — ANTICIPATION: What forces are converging? Which weak signals are strengthening? What established patterns are decelerating? Synthesize across categories — don't list themes, weave them into a narrative about interconnected forces.

Section 2 — PARTICIPATION: Where are organizations already adapting? What patterns emerge from how companies engage with these forces? What does the cross-impact data reveal about which organizational dimensions are most under pressure?

Section 3 — FUTURES LITERACY (Scenario Pathways):

This section uses Summary/Scenario Writing methodology — the production of plausible future accounts based on a creative combination of the data above, factual patterns, and reasoned hypotheses. Do NOT simply list three separate scenarios. Instead, identify 2-3 FORCE CLUSTERS — groups of forces from the data that naturally co-occur and amplify each other — and build branching narratives from them.

For each force cluster:
1. Name the cluster and identify which specific forces from the data compose it (cite the themes/categories)
2. Describe the PLAUSIBILITY CONDITIONS — what specific signals would need to strengthen for this cluster to become dominant? Reference actual timeframe and force_type data.
3. Write a SHORT BRANCHING NARRATIVE: "If this cluster accelerates, organizations that [specific posture] will [outcome], while those that [opposite posture] will [different outcome]."
4. Identify CROSS-FORCE DEPENDENCIES — how does this cluster interact with or block other clusters? ("If cluster A accelerates, it makes cluster B more likely because...")

Frame the three clusters as:
- A convergence scenario (multiple Probable/Emerging forces compound)
- A drift scenario (Plausible/Maturing forces stall while weak signals continue building)
- A disruption scenario (Preposterous/Black Swan forces combine with one accelerating cluster)

End with: What would an organization with genuine futures literacy do differently from one that merely tracks trends? Frame this as a thinking capability, not a technology adoption question.

OUTPUT FORMAT:
- 900-1100 words
- Three sections with headers (## Anticipation, ## Participation, ## Futures Literacy)
- Analytical essay prose — NO bullet points
- Written as a futurologist would present to a strategy board
- End with implications for organizational thinking, never technology adoption"""


def _build_finland_prompt(
    global_analysis: str,
    mi: dict,
    kb_aggregate: dict = None,
    structured_findings: str = "",
    *,
    l1_claims=None,
    evidence_dossier=None,
) -> str:
    """Build prompt for Output 2: Finnish Economy Force Map."""
    # Market health summary
    health_summary = ""
    if mi.get("market_health"):
        health_summary = "\n".join(
            f"- {h['domain']}: health={h['score']}/100, reality={h['avg_reality']}, financial={h['avg_financial']}, gap={h['avg_gap']}, {h['company_count']} companies"
            for h in mi["market_health"]
        )

    # Archetype distribution
    arch_summary = ""
    if mi.get("archetype_flow"):
        arch_summary = "\n".join(
            f"- {a['domain']}: Thriving={a['thriving']}, Modernizing={a['modernizing']}, Cash Cow={a['cash_cow']}, At Risk={a['at_risk']} (total={a['total']})"
            for a in mi["archetype_flow"]
        )

    # FAVI segments
    favi_summary = json.dumps(mi.get("favi_segments", {}), indent=2)

    # Gap patterns
    gap_summary = ""
    if mi.get("gap_patterns"):
        gap_summary = "\n".join(
            f"- {g['dimension']}: avg_intent={g['avg_intent']}, avg_reality={g['avg_reality']}, gap={g['avg_gap']} ({g['company_count']} companies)"
            for g in mi["gap_patterns"]
        )

    # Top foresights
    foresight_summary = ""
    if mi.get("top_foresights"):
        foresight_summary = "\n".join(
            f"- {f['theme']} ({f['category']}): {f['count']} companies ({f['pct']}%)"
            for f in mi["top_foresights"][:10]
        )

    # KB signal evidence — top 5 per category with full descriptions
    signal_evidence = ""
    if kb_aggregate and kb_aggregate.get("by_category"):
        signal_evidence = _format_top_signals_per_category(kb_aggregate["by_category"], per_cat=5)
    if evidence_dossier is not None:
        signal_evidence = "Macro context is supplied through the validated_l1_claims in the locked dossier."

    if l1_claims is not None:
        global_context = json.dumps(_public_claim_handoff(l1_claims), ensure_ascii=False, indent=2)
        global_context_label = "STRUCTURED L1 CLAIM HANDOFF (the L1 essay is intentionally excluded)"
    else:
        # Compatibility path for older callers and tests.
        global_context = global_analysis
        global_context_label = "GLOBAL CONTEXT"
    locked_dossier = json.dumps(_prompt_safe_dossier(evidence_dossier or {}), ensure_ascii=False, indent=2)

    return f"""{SYSTEM_ROLE}

TASK: Write a Finnish Economy Force Map — a deep strategic analysis of how global forces specifically reshape the Finnish business landscape.

TONE CALIBRATION — Balanced Strategic Assessment:
- Maintain intellectual honesty: celebrate genuine strength as clearly as you identify genuine weakness.
- If the data shows domains performing well, say so with conviction. Do not default to pessimism or manufacture risks.
- Only flag risks and challenges that are clearly grounded in the actual data provided. If a domain is thriving, acknowledge it.
- The goal is accurate strategic assessment, not alarm. A balanced view earns more credibility than a uniformly critical one.
- Positive framing is valid: strong archetype distributions, healthy financial indices, and narrow gaps are genuine competitive advantages worth highlighting.

ANALYTICAL LENS — Strategic Asymmetry Mapping:
Your task is NOT to summarize the data. Your task is to find the ASYMMETRIES — the places where the data contradicts expectations, where strength in one dimension masks vulnerability in another, where forces that seem separate are actually the same force arriving through different channels. Think like a strategist reading the data for what it reveals that the data itself does not say out loud.

NATIONAL-LEVEL EDITORIAL CONTRACT:
- Scores and counts are background evidence, not the narrative spine.
- Use no more than 4-5 reader-facing metric highlights in the whole essay.
- Every metric you mention must answer: "so what for Finland's economic readiness, adaptation capacity, timing pressure, or strategic choice?"
- Do not write domain-by-domain score rollups or table narration.
- Prefer domain clusters when useful: industrial core, trust economy, digital/service domains, exposed domestic-demand domains, regulated infrastructure, or capital-intensive domains.
- Do not use company-level examples unless they are essential to explain a national pattern.
- Avoid raw markdown emphasis. Use plain section headings only.
- Never begin a paragraph or heading with a bare score such as "75 — ...".

LOCKED LAYER 2 EVIDENCE DOSSIER ({OBSERVATORY_DOSSIER_VERSION}):
{locked_dossier}

DOSSIER RULE:
- Treat this dossier as the authoritative Layer 2 evidence boundary.
- Use only the structured L1 claims below; do not infer from or reproduce the full Layer 1 essay.
- Cite evidence IDs after material claims when helpful for auditability, but do not write method labels such as "[Strategic inference]" in the prose.

{global_context_label}:
{global_context}

SOURCE SIGNAL EVIDENCE (top validated signals per category — use these to ground your analysis in actual source content, cite specific signals when making claims):
{signal_evidence}

FINNISH DATA:

Market Health by Domain:
{health_summary}

Archetype Distribution by Domain:
{arch_summary}

Foresight-Adjusted Vulnerability Index segment distribution (across all companies):
{favi_summary}

Gap Patterns (Intent vs Reality per KB Dimension):
{gap_summary}

Top Foresight Themes Affecting Finnish Companies:
{foresight_summary}

Total companies analyzed: {mi.get('company_count', 0)}
Companies with detailed data: {mi.get('detailed_count', 0)}

PRE-COMPUTED EVIDENCE-LINKED FINDINGS:
{structured_findings}

ANALYTICAL STRUCTURE:

Use these exact headings, one per line. Do not write the word "Section".

National Thesis
Write the national-level thesis: what the portfolio pattern says about Finland's current economic readiness and the central strategic tension. Use implication-led language, not domain ranking.

Three Strategic Asymmetries
Identify exactly three asymmetries. Each asymmetry should combine a domain-cluster pattern, a force pattern, and a strategic implication. Use only the most necessary metrics and interpret them immediately.

What The Pattern Means
Explain what the asymmetries reveal about Finnish organizational thinking, adaptation capacity, capability concentration, and exposure. Go beyond the intent/reality gap list.

Timing And Readiness Implications
Explain the 6-12, 12-24, and 24-36 month implications as one national readiness narrative. Name the capability, governance mechanism, or operating-model change that must carry each horizon.

Contrarian Signal
Identify the dangerous assumption, the contrarian hypothesis, and the implication in 2-3 paragraphs. Frame this as strategic foresight, not prediction.

OUTPUT FORMAT:
- 1300-1700 words
- Five sections with the exact headings above
- Analytical essay prose — NO bullet points
- Deep strategic analysis, not data reporting — every paragraph must contain an insight, not just a finding
- Grounded in actual company data patterns, but do not turn the essay into a score list
- Reference specific KB signals by name when supporting claims about forces"""


def _build_domain_prompt(
    domain: str, domain_config: dict, domain_companies: list,
    kb_signals: list, market_health: dict,
    global_analysis: str, finland_analysis: str, structured_findings: str = "",
    *, upstream_claims=None, evidence_dossier=None,
) -> str:
    """Build prompt for Output 3: Domain Force Dossier."""
    # Company stats
    n = len(domain_companies)
    archetypes = Counter()
    favi_segments = Counter()
    reality_scores = []
    financial_scores = []

    for c in domain_companies:
        reality = c.get("reality_score")
        financial = c.get("financial_index")
        if reality is not None:
            reality_scores.append(float(reality))
            arch = classify_archetype(
                float(reality),
                float(financial) if financial is not None else float(c.get("intent_score", 0))
            )
            archetypes[arch] += 1
        if financial is not None:
            financial_scores.append(float(financial))

    avg_reality = round(statistics.mean(reality_scores), 1) if reality_scores else 0
    avg_financial = round(statistics.mean(financial_scores), 1) if financial_scores else 0

    arch_str = ", ".join(f"{k}={v}" for k, v in sorted(archetypes.items()))

    # Foresight links from domain companies
    domain_forces = []
    for c in domain_companies:
        for link in (c.get("foresight_links") or []):
            domain_forces.append({
                "theme": link.get("fp_theme", ""),
                "dimension": link.get("kb_dimension", ""),
                "timeframe": link.get("timeframe", ""),
                "force_type": link.get("force_type", ""),
                "title": link.get("title", ""),
            })

    # KB signals for this domain's themes — full context
    kb_summary = []
    for sig in kb_signals[:20]:
        lifecycle = sig.get("lifecycle_stage", "")
        momentum = sig.get("trend_momentum", "")
        credibility = sig.get("source_credibility", "")
        desc = sig.get("description", "")
        line = f"- **{sig.get('title', '?')}** [{sig.get('force_type', '?')} | {sig.get('timeframe', '?')}]"
        if desc:
            line += f"\n  {desc}"
        if lifecycle or momentum:
            line += f"\n  Lifecycle: {lifecycle} | Momentum: {momentum} | Credibility: {credibility}"
        kb_summary.append(line)

    # Forces by timeframe
    forces_by_tf = defaultdict(list)
    for f in domain_forces:
        forces_by_tf[f["timeframe"]].append(f"{f['theme']}: {f['title']}")

    health_score = market_health.get("score", "N/A") if market_health else "N/A"
    if upstream_claims is not None:
        safe_upstream = {
            "layer_1": _public_claim_handoff((upstream_claims or {}).get("layer_1") or []),
            "layer_2": _public_claim_handoff((upstream_claims or {}).get("layer_2") or []),
        }
        broader_context = json.dumps(safe_upstream, ensure_ascii=False, indent=2)
        broader_context_rule = (
            "Use only the structured, evidence-linked L1/L2 claims in the locked dossier. "
            "The full upstream essays are intentionally excluded."
        )
    else:
        broader_context = f"Layer 1:\n{global_analysis}\n\nLayer 2:\n{finland_analysis}"
        broader_context_rule = "Use the supplied Layer 1 and Layer 2 analyses as broader context."
    locked_dossier = json.dumps(_prompt_safe_dossier(evidence_dossier or {}), ensure_ascii=False, indent=2)

    return f"""{SYSTEM_ROLE}

TASK: Write a Domain Force Dossier for {domain}.

TONE CALIBRATION — Let the Data Drive the Tone:
- Let the actual numbers set the tone. If the domain has strong reality scores, high Thriving ratios, or healthy financials, the analysis should reflect genuine confidence — not forced criticism.
- Only identify risks that are clearly evidenced in the data. Do not speculate about threats that the numbers do not support.
- Celebrate what is working: strong archetype distributions, narrow intent-reality gaps, and good market health are real competitive advantages. Say so clearly.
- The goal is an honest, data-grounded assessment — positive where the data warrants, concerned where the data warrants.
- A domain with 4 of 5 companies Thriving is genuinely strong. Do not undermine that finding with hypothetical future risks unless the signal evidence specifically supports those risks.

DOMAIN-LEVEL EDITORIAL CONTRACT:
- Write about the domain pattern, not individual company stories. Company-level detail belongs in Company Summary, not here.
- Use company data only as background evidence for the domain thesis.
- Do not list company-by-company scores or create a roll call of winners and laggards.
- Use individual company names only when a named outlier is necessary to explain a domain-level pattern.
- Include exactly three reader-facing evidence highlights in the "Three Evidence Highlights" section. Each highlight must be domain-level and must explain what the metric reveals.
- Outside the Three Evidence Highlights section, avoid raw score recitation. Interpret what the pattern means for domain readiness, pressure, capability, risk, or strategic choice.
- Never begin a paragraph or heading with a bare score such as "75 — ...".

BROADER CONTEXT — How to use prior analysis layers:
{broader_context_rule}
- From Layer 1, identify 1-2 global forces most directly relevant to {domain} and reference how they manifest in this specific domain.
- From Layer 2, use the Finnish positioning insights for {domain} specifically — do not repeat general Finnish findings.
- Build on their conclusions but add domain-specific depth that the broader analyses could not provide.

LOCKED LAYER 3 EVIDENCE DOSSIER ({OBSERVATORY_DOSSIER_VERSION}):
{locked_dossier}

STRUCTURED UPSTREAM CLAIM HANDOFF:
{broader_context}

DOSSIER RULE:
- Treat the locked dossier as the authoritative evidence boundary.
- Cite evidence IDs after material claims.
- Treat foresight signals as macro context, never proof that a company has acted.
- State important evidence gaps in plain language. Do not write method labels such as "[Strategic inference]" in the prose.

DOMAIN DATA:

Companies: {n}
Archetype distribution: {arch_str}
Average reality score: {avg_reality}/100
Average financial index: {avg_financial}/100
Market health score: {health_score}/100
FP themes mapped: {', '.join(domain_config.get('fp_themes', []))}
Priority dimensions: {', '.join(domain_config.get('priority_dimensions', []))}

Forces identified in domain companies ({len(domain_forces)} total):
- Near-term (6-12mo): {len(forces_by_tf.get('6-12mo', []))} forces
- Medium-term (12-24mo): {len(forces_by_tf.get('12-24mo', []))} forces
- Long-term (24-36mo): {len(forces_by_tf.get('24-36mo', []))} forces

KB SIGNAL EVIDENCE for this domain's themes (full descriptions — use these to ground your analysis in actual source content):
{chr(10).join(kb_summary)}

PRE-COMPUTED EVIDENCE-LINKED FINDINGS:
{structured_findings}

ANALYTICAL STRUCTURE:

Use these exact headings, one per line. Do not write the word "Section".

Domain Thesis
Write a concise synthesis of the domain's current position and central tension. Use domain-level language.

Three Evidence Highlights
Write exactly three short highlights. Each highlight must combine one metric or evidence pattern with its interpretation. These are the only reader-facing metric highlights in the dossier.

What The Pattern Means
Explain what the evidence pattern reveals about the domain's readiness, challenge profile, structural exposure, and ability to act. Avoid listing company scores.

Forces By Timing
Explain the 6-12, 12-36, and 24-60 month pressures as one integrated timing narrative. Include what organizations should stop assuming and what they should start preparing.

Strategic Implication
Describe what it would mean for this domain to define its own race rather than react. Make this about strategic choices, capability focus, and demand-shaping opportunities.

Contrarian Signal
Identify the underestimated force, the contrarian hypothesis, and the implication in 2-3 paragraphs. Frame this as strategic foresight, not prediction.


OUTPUT FORMAT:
- 650-850 words
- Six sections with the exact headings above
- Analytical essay prose — NO bullet points
- Reference actual data from above, but do not turn the dossier into a score list"""


# ═══════════════════════════════════════════════════════════════════════════════
# LLM GENERATION + VERIFICATION
# ═══════════════════════════════════════════════════════════════════════════════

def _generate_sonnet(prompt: str, max_tokens: int = 5000) -> tuple[str, int]:
    """Generate text via Sonnet 4.6. Returns (text, word_count)."""
    anthropic_key = os.environ.get("ANTHROPIC_API_KEY", "").strip()
    if not anthropic_key:
        raise ValueError("ANTHROPIC_API_KEY not configured")

    client = anthropic.Anthropic(api_key=anthropic_key)
    t0 = time.time()

    response = client.messages.create(
        model="claude-sonnet-4-6",
        max_tokens=max_tokens,
        thinking={"type": "adaptive"},
        extra_body={"output_config": {"effort": "medium"}},
        messages=[{"role": "user", "content": prompt}],
    )

    text_parts = []
    for block in response.content:
        if block.type == "text":
            text_parts.append(block.text)
    text = "\n".join(text_parts).strip()

    # Strip markdown links (keep text only)
    text = re.sub(r'\[([^\]]+)\]\(https?://[^)]+\)', r'\1', text)

    wc = len(text.split())
    elapsed = int((time.time() - t0) * 1000)
    print(f"   [Observatory] Sonnet 4.6: {wc} words ({elapsed}ms)")
    return text, wc


def _generate_opus(prompt: str, max_tokens: int = 16000) -> tuple[str, int]:
    """Generate text via Opus 4.8. Returns (text, word_count).
    Used for Domain Dossiers (Layer 3) where analytical depth matters most."""
    anthropic_key = os.environ.get("ANTHROPIC_API_KEY", "").strip()
    if not anthropic_key:
        raise ValueError("ANTHROPIC_API_KEY not configured")

    client = anthropic.Anthropic(api_key=anthropic_key)
    t0 = time.time()

    response = client.messages.create(
        model="claude-opus-4-8",
        max_tokens=max_tokens,
        thinking={"type": "adaptive"},
        extra_body={"output_config": {"effort": "xhigh"}},
        messages=[{"role": "user", "content": prompt}],
    )

    text_parts = []
    for block in response.content:
        if block.type == "text":
            text_parts.append(block.text)
    text = "\n".join(text_parts).strip()

    # Strip markdown links (keep text only)
    text = re.sub(r'\[([^\]]+)\]\(https?://[^)]+\)', r'\1', text)

    wc = len(text.split())
    elapsed = int((time.time() - t0) * 1000)
    print(f"   [Observatory] Opus 4.8: {wc} words ({elapsed}ms)")
    return text, wc


def _generate_gpt_pro(prompt: str, max_tokens: int = 16000) -> tuple[str, int, str]:
    """Generate L1/L2 with GPT-5.5 Pro and bounded quality fallbacks."""
    attempts = (
        (OBSERVATORY_L1_MODEL, None, 480),
        (GPT_REASONING_MODEL, "xhigh", 300),
    )
    errors = []
    for model, effort, timeout in attempts:
        try:
            t0 = time.time()
            text = call_openai_text(
                prompt,
                model=model,
                max_output_tokens=max_tokens,
                timeout=timeout,
                max_retries=1,
                reasoning_effort=effort,
            )
            text = re.sub(r'\[([^\]]+)\]\(https?://[^)]+\)', r'\1', text).strip()
            wc = len(text.split())
            elapsed = int((time.time() - t0) * 1000)
            print(f"   [Observatory] {model}{f' ({effort})' if effort else ''}: {wc} words ({elapsed}ms)")
            return text, wc, model
        except Exception as exc:
            errors.append(f"{model}: {exc}")
            print(f"   [Observatory] {model} generation unavailable: {exc}")

    try:
        text, wc = _generate_sonnet(prompt, max_tokens=min(max_tokens, 8000))
        return text, wc, "claude-sonnet-4-6"
    except Exception as exc:
        errors.append(f"claude-sonnet-4-6: {exc}")
        raise ValueError("All Observatory L1/L2 generation models failed: " + " | ".join(errors))


def _verify_with_models(essay: str, source_data_summary: str, output_type: str, signal_evidence: str = "") -> dict:
    """Verify essay via GPT/Claude. Returns verification result.

    When signal_evidence is provided, adds a claim-level cross-validation check:
    verifies that narrative claims trace back to actual KB signals rather than
    being fabricated by the generation model.
    """
    signal_check = ""
    signal_response_fields = ""
    if signal_evidence:
        signal_check = f"""6. CLAIM-SIGNAL CROSS-VALIDATION: Compare specific claims, trends, and force descriptions in the essay against the KB SIGNAL EVIDENCE below. Flag any claims that:
   a) Cite a signal title or trend that does NOT appear in the evidence
   b) Attribute statistics or data points not found in any signal description
   c) Describe a force's direction (accelerating/decelerating) opposite to what the signals show
   Only flag clear fabrications — reasonable synthesis and inference from multiple signals is expected and acceptable.

KB SIGNAL EVIDENCE (the signals that were provided to the generation model):
{signal_evidence[:8000]}

"""
        signal_response_fields = ', "unsupported_claims": ["claim 1", ...], "signal_cross_validation_passed": true/false'

    prompt = f"""You are a quality assurance reviewer for strategic foresight analysis.

TASK: Verify the following {output_type} essay against its source data.

CHECK LIST:
1. DATA ACCURACY: Do any numbers or statistics cited in the essay contradict the source data? List specific mismatches.
2. ANTI-TECHNOLOGY FRAMING: Does the essay frame technology as the key message anywhere? Flag any sentences that say "companies need AI/cloud/digital/technology" rather than focusing on thinking, adaptability, and organizational capability.
3. STRUCTURE: Are all required sections present? Estimate the word count.
4. EVIDENCE GROUNDING: Does the essay make claims not supported by the provided data?
5. TONE BALANCE (for Finnish Economy Force Map and Domain Dossiers only): Does the essay manufacture or exaggerate risks that the data does not support? If the data shows strong performance (high Thriving ratios, healthy scores), does the essay acknowledge that strength clearly? Flag any cases where the tone is unjustifiably negative relative to the actual data.
6. EVIDENCE-ID TRACE: Do material claims cite evidence IDs that actually exist in the locked dossier? Flag missing, unknown, or mismatched IDs. Distinguish direct evidence from strategic inference.
{signal_check}
LOCKED SOURCE DOSSIER:
{source_data_summary[:24000]}

ESSAY TO VERIFY:
{essay}

RESPOND IN JSON FORMAT ONLY:
{{"passed": true/false, "word_count_estimate": N, "issues": ["issue 1", ...], "tech_framing_violations": ["sentence 1", ...], "data_mismatches": ["mismatch 1", ...], "evidence_id_issues": ["issue 1", ...]{signal_response_fields}}}"""

    try:
        result, model_used = call_json_model_chain(
            prompt,
            (GPT_REASONING_MODEL, "claude-sonnet-4-6"),
            max_tokens=2600,
            timeout=120,
            reasoning_effort="high",
        )
        parsed = result if isinstance(result, dict) else parse_json_object(result)
        parsed.setdefault("verification_model", model_used)
        parsed["status"] = "verified" if parsed.get("passed") is True else "failed"
        return parsed
    except Exception as e:
        print(f"   [Observatory] Verification failed (non-blocking): {e}")
        return {
            "passed": False,
            "status": "unavailable",
            "issues": [],
            "error": str(e),
        }


def _generate_with_verification(
    prompt: str,
    source_summary: str,
    output_type: str,
    use_opus: bool = False,
    signal_evidence: str = "",
    use_gpt_pro: bool = False,
) -> tuple[str, dict]:
    """Generate via Claude, then verify via GPT/Claude. Returns (text, verification).
    use_opus=True uses Opus 4.8 for deeper analytical reasoning (Layer 3).
    signal_evidence: when provided, enables claim-level cross-validation against KB signals."""
    if use_gpt_pro:
        text, wc, generation_model = _generate_gpt_pro(prompt)
    else:
        generate_fn = _generate_opus if use_opus else _generate_sonnet
        text, wc = generate_fn(prompt)
        generation_model = OBSERVATORY_L3_MODEL if use_opus else "claude-sonnet-4-6"

    if wc < 50:
        raise ValueError(f"Generation too short: {wc} words")

    verification = _verify_with_models(text, source_summary, output_type, signal_evidence=signal_evidence)
    verification["generation_model"] = generation_model

    # Log cross-validation results if signal evidence was checked
    if signal_evidence and verification.get("unsupported_claims"):
        n_claims = len(verification["unsupported_claims"])
        print(f"   [Observatory] Cross-validation: {n_claims} unsupported claim(s) flagged in {output_type}")
        for claim in verification["unsupported_claims"][:3]:
            print(f"      ⚠️ {claim[:120]}")

    # If verification finds critical issues and text is regeneratable, try once more
    needs_regen = False
    correction_parts = []

    if not verification.get("passed", True) and verification.get("tech_framing_violations"):
        violations = verification["tech_framing_violations"]
        print(f"   [Observatory] Verification found {len(violations)} tech-framing violations — regenerating...")
        correction_parts.append(f"Your previous attempt contained technology-centric framing. These specific sentences violated the anti-technology rule: {json.dumps(violations)}. Rewrite to focus on organizational thinking, adaptability, and future-readiness instead.")
        needs_regen = True

    if verification.get("unsupported_claims") and not verification.get("signal_cross_validation_passed", True):
        unsupported = verification["unsupported_claims"]
        print(f"   [Observatory] Cross-validation failed: {len(unsupported)} fabricated claims — regenerating...")
        correction_parts.append(f"Your previous attempt contained claims not supported by the provided KB signal evidence. These claims could not be traced to any signal: {json.dumps(unsupported[:5])}. Remove or replace these with claims that are directly grounded in the signal evidence provided.")
        needs_regen = True

    if needs_regen:
        correction = "\n\nCRITICAL CORRECTION: " + " ".join(correction_parts)
        if use_gpt_pro:
            text, wc, generation_model = _generate_gpt_pro(prompt + correction)
        else:
            text, wc = generate_fn(prompt + correction)
        verification = _verify_with_models(text, source_summary, output_type, signal_evidence=signal_evidence)
        verification["generation_model"] = generation_model

    return text, verification


# ═══════════════════════════════════════════════════════════════════════════════
# MAIN ORCHESTRATOR
# ═══════════════════════════════════════════════════════════════════════════════

def generate_observatory(progress_callback=None):
    """
    Generate the full Foresight Observatory (three layers).
    Returns the complete observatory data structure.

    progress_callback(step_name, current_index, total_steps) is called after each step.
    """
    storage = BlobStorage()

    def progress(step, idx, total):
        print(f"   [Observatory] Step {idx+1}/{total}: {step}")
        if progress_callback:
            progress_callback(step, idx, total)

    # --- Load all data ---
    data = _load_all_data(storage)
    kb_agg = data["kb_aggregate"]
    mi = data["market_intel"]
    input_manifest = _build_input_manifest(data)
    structured_findings = _build_structured_findings(kb_agg, mi)
    findings_block = _format_structured_findings(structured_findings)
    l1_dossier = _build_l1_evidence_dossier(kb_agg, mi, structured_findings, input_manifest)

    # Import domain config
    from strategic_foresight import DOMAIN_TO_FORESIGHT

    # Count active domains (domains with companies). "Other" is useful as an
    # internal classification fallback, but it is not a reader-facing domain
    # for Observatory Layer 3.
    domain_companies = defaultdict(list)
    for c in data["company_details"]:
        domain = c.get("domain") or "Unknown"
        if domain not in {"Unknown", "Other"}:
            domain_companies[domain].append(c)

    active_domains = [d for d in sorted(domain_companies.keys()) if len(domain_companies[d]) >= 2]
    total_steps = 2 + len(active_domains)  # Global + Finland + N domains

    # --- Pre-compute signal evidence for cross-validation ---
    signal_evidence = _format_top_signals_per_category(kb_agg["by_category"], per_cat=5)

    # --- Output 1: Global Force Landscape ---
    progress("Global Force Landscape", 0, total_steps)
    global_prompt = _build_global_prompt(
        kb_agg,
        mi.get("cross_impact", []),
        mi.get("top_foresights", []),
        findings_block,
        evidence_dossier=l1_dossier,
    )
    global_source = json.dumps(l1_dossier, ensure_ascii=False)
    global_text, global_verify = _generate_with_verification(
        global_prompt,
        global_source,
        "Global Force Landscape",
        signal_evidence=signal_evidence,
        use_gpt_pro=True,
    )
    l1_claims = _extract_layer_claims(global_text, l1_dossier, "L1")

    # Extract key convergences from the text (first 3 sentences that mention convergence/force)
    key_convergences = []
    for sentence in re.split(r'[.!?]+', global_text):
        sentence = sentence.strip()
        if any(word in sentence.lower() for word in ["converg", "amplif", "intersect", "compound"]):
            if len(sentence) > 20:
                key_convergences.append(sentence[:150])
        if len(key_convergences) >= 3:
            break
    global_text, global_sections, global_public_qa = _finalize_observatory_output_text(
        global_text,
        "global_analysis",
        OBSERVATORY_ALL_SECTION_TITLES,
        allow_rewrite=False,
    )

    global_analysis = {
        "text": global_text,
        "sections": global_sections,
        "force_count": kb_agg["total_signals"],
        "accelerating_forces": kb_agg["accelerating"],
        "key_convergences": key_convergences,
        "structured_claims": l1_claims,
        "evidence_dossier_version": OBSERVATORY_DOSSIER_VERSION,
        "analysis_model": global_verify.get("generation_model", OBSERVATORY_L1_MODEL),
        "verification": global_verify,
        "public_output_quality": global_public_qa,
    }

    # --- Output 2: Finnish Economy Force Map ---
    progress("Finnish Economy Force Map", 1, total_steps)
    l2_dossier = _build_l2_evidence_dossier(mi, l1_claims, structured_findings, input_manifest)
    finland_prompt = _build_finland_prompt(
        "",
        mi,
        kb_agg,
        findings_block,
        l1_claims=l1_claims,
        evidence_dossier=l2_dossier,
    )
    finland_source = json.dumps(l2_dossier, ensure_ascii=False)
    finland_text, finland_verify = _generate_with_verification(
        finland_prompt,
        finland_source,
        "Finnish Economy Force Map",
        use_gpt_pro=True,
    )
    l2_claims = _extract_layer_claims(finland_text, l2_dossier, "L2")
    finland_text, finland_sections, finland_public_qa = _finalize_observatory_output_text(
        finland_text,
        "finland_analysis",
        OBSERVATORY_FINLAND_SECTION_TITLES,
        allow_rewrite=False,
    )

    # Extract top adaptability gaps
    top_gaps = []
    for g in mi.get("gap_patterns", [])[:3]:
        top_gaps.append(f"{g['dimension']}: gap={g['avg_gap']}")

    finland_analysis = {
        "text": finland_text,
        "sections": finland_sections,
        "domains_analyzed": len(mi.get("market_health", [])),
        "favi_distribution": mi.get("favi_segments", {}),
        "top_adaptability_gaps": top_gaps,
        "l1_claims_consumed": len(l1_claims),
        "structured_claims": l2_claims,
        "evidence_dossier_version": OBSERVATORY_DOSSIER_VERSION,
        "analysis_model": finland_verify.get("generation_model", OBSERVATORY_L2_MODEL),
        "verification": finland_verify,
        "public_output_quality": finland_public_qa,
    }

    # --- Output 3: Domain Force Dossiers ---
    domain_dossiers = []
    l3_dossiers = {}
    for i, domain in enumerate(active_domains):
        progress(f"Domain: {domain}", 2 + i, total_steps)

        config = DOMAIN_TO_FORESIGHT.get(domain, DOMAIN_TO_FORESIGHT.get("Other", {}))
        companies = domain_companies[domain]

        # Get KB signals for this domain's themes
        kb_signals = []
        for theme in config.get("fp_themes", []):
            for theme_slug, theme_data in data["all_kb"].items():
                for sig in theme_data.get("signals", []):
                    eligible, _ = _is_eligible_foresight_context(sig)
                    if sig.get("fp_theme") == theme and eligible:
                        kb_signals.append(sig)

        # Find market health for this domain
        mh = None
        for h in mi.get("market_health", []):
            if h["domain"] == domain:
                mh = h
                break

        l3_dossier = _build_l3_evidence_dossier(
            domain, config, companies, kb_signals, mh,
            l1_claims, l2_claims, structured_findings,
        )
        l3_dossiers[domain] = l3_dossier
        domain_prompt = _build_domain_prompt(
            domain, config, companies, kb_signals, mh,
            "", "", findings_block,
            upstream_claims={"layer_1": l1_claims, "layer_2": l2_claims},
            evidence_dossier=l3_dossier,
        )
        source = json.dumps(l3_dossier, ensure_ascii=False)
        # Build domain-specific signal evidence for cross-validation
        domain_signal_evidence = "\n".join(
            f"- **{s.get('title', 'N/A')}** [{s.get('force_type', '?')} | {s.get('timeframe', '?')}]\n  Description: {s.get('description', 'N/A')}"
            for s in kb_signals[:20]
        )
        text, verify = _generate_with_verification(domain_prompt, source, f"Domain Dossier: {domain}", use_opus=True, signal_evidence=domain_signal_evidence)
        text, sections, public_qa = _finalize_observatory_output_text(
            text,
            f"domain:{domain}",
            OBSERVATORY_DOMAIN_SECTION_TITLES,
            allow_rewrite=True,
        )

        # Extract forces by timescale
        forces_by_ts = {"act_now": [], "prepare": [], "position": []}
        for sig in kb_signals:
            tf = sig.get("timeframe", "")
            title = sig.get("title", "")[:80]
            if "6-12" in tf:
                forces_by_ts["act_now"].append(title)
            elif "12-24" in tf:
                forces_by_ts["prepare"].append(title)
            elif "24-36" in tf:
                forces_by_ts["position"].append(title)

        # Limit to top 5 per timescale
        for k in forces_by_ts:
            forces_by_ts[k] = forces_by_ts[k][:5]

        dossier = {
            "domain": domain,
            "text": text,
            "sections": sections,
            "company_count": len(companies),
            "archetype_distribution": dict(Counter(
                classify_archetype(
                    float(c["reality_score"]),
                    float(c.get("financial_index") or c.get("intent_score", 0))
                )
                for c in companies if c.get("reality_score") is not None
            )),
            "forces_by_timescale": forces_by_ts,
            "analysis_model": verify.get("generation_model", OBSERVATORY_L3_MODEL),
            "evidence_dossier_version": OBSERVATORY_DOSSIER_VERSION,
            "verification": verify,
            "public_output_quality": public_qa,
        }
        domain_dossiers.append(dossier)

    # --- Assemble final result ---
    all_verifications = [global_verify, finland_verify] + [item["verification"] for item in domain_dossiers]
    result = {
        "generated_at": datetime.utcnow().isoformat(),
        "model": "gpt-5.5-pro (Layers 1-2) + claude-opus-4-8 (Layer 3)",
        "verification_model": "gpt-5.5 high / claude-sonnet-4-6",
        "company_count": len(data["dashboard_data"]),
        "domain_count": len(active_domains),
        "input_manifest": input_manifest,
        "evidence_dossiers": {
            "schema_version": OBSERVATORY_DOSSIER_VERSION,
            "layer_1": l1_dossier,
            "layer_2": l2_dossier,
            "layer_3": l3_dossiers,
        },
        "layer_claims": {"layer_1": l1_claims, "layer_2": l2_claims},
        "structured_findings": structured_findings,
        "confidence": _build_confidence_summary(data, all_verifications),
        "global_analysis": global_analysis,
        "finland_analysis": finland_analysis,
        "domain_dossiers": domain_dossiers,
        # Top-15 themes with momentum breakdown — drives the Theme Momentum chart
        # in Market Intelligence > Future Demand. Additive; safe for old clients.
        "theme_momentum": kb_agg.get("theme_momentum_top", []),
    }
    result = _sanitize_observatory_payload(result)

    # Save to storage
    url = storage.save_observatory(result)
    qa_status = (result.get("public_output_quality") or {}).get("status", "unknown")
    qa_warning_count = (result.get("public_output_quality") or {}).get("warning_count", 0)
    qa_repairable_count = (result.get("public_output_quality") or {}).get("repairable_issue_count", 0)
    qa_repaired_count = (result.get("public_output_quality") or {}).get("repaired_issue_count", 0)
    print(
        f"   [Observatory] Saved observatory to storage: {url} "
        f"(qa_status={qa_status}, warnings={qa_warning_count}, "
        f"repairable_remaining={qa_repairable_count}, repaired={qa_repaired_count})"
    )

    return result


# FORESIGHT_OBSERVATORY_DEADLINE_GUARDRAIL: generated observatory text is passed through sanitize_foresight_text; prompts should avoid deterministic must/requires/forces wording and past deadlines.
