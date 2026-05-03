"""
foresight_guardrails.py — Shared safeguards to keep foresight as foresight.

Central enforcement for modal foresight language, company-specific corroboration,
and report-date-safe future deadlines.
"""
from __future__ import annotations

import re
from copy import deepcopy
from datetime import date
from typing import Any

FORESIGHT_CLAIM_TYPE = "foresight_context"
REGULATORY_CLAIM_TYPE = "regulatory_context"
FORESIGHT_SANITIZER_VERSION = "2026-05-03-v2-must-deadline-guardrails"

FORBIDDEN_FACT_PATTERNS = [
    r"\bmust\b", r"\brequires?\b", r"\brequired\s+to\b", r"\bforces?\b",
    r"\bhas\s+to\b", r"\bneeds\s+to\b", r"\bwill\s+need\s+to\b",
    r"\bis\s+obligated\s+to\b", r"\bfaces\s+penalties\b", r"\bis\s+non[-\s]?compliant\b",
    r"\bwill\s+fall\s+behind\b", r"\bwill\s+lose\b", r"\bhas\s+deployed\b",
    r"\bhave\s+deployed\b", r"\bis\s+deploying\b", r"\bare\s+deploying\b",
    r"\bcurrently\s+uses\b", r"\buses\s+multi-agent\b", r"\bbroad\s+deployment\b",
]

MODAL_REPLACEMENTS = [
    (r"\bwill\s+need\s+to\b", "may need to"),
    (r"\bis\s+obligated\s+to\b", "may be subject to obligations if in scope and should assess whether it needs to"),
    (r"\bfaces\s+penalties\b", "may face compliance risk if in scope and if requirements are not addressed"),
    (r"\bis\s+non[-\s]?compliant\b", "may require compliance assessment if in scope"),
    (r"\bwill\s+fall\s+behind\b", "may face a disadvantage if the trend becomes material"),
    (r"\bwill\s+lose\b", "could risk losing"),
    (r"\bhas\s+deployed\b", "is not proven by this foresight evidence alone to have deployed"),
    (r"\bhave\s+deployed\b", "are not proven by this foresight evidence alone to have deployed"),
    (r"\bis\s+deploying\b", "may be evaluating or could evaluate"),
    (r"\bare\s+deploying\b", "may be evaluating or could evaluate"),
    (r"\bcurrently\s+uses\b", "may need to evaluate use of"),
    (r"\buses\s+multi-agent\b", "could evaluate multi-agent"),
    (r"\bbroad\s+deployment\b", "potential broad relevance"),
    (r"\brequired\s+to\b", "potentially expected to if in scope"),
    (r"\brequires?\b", "may require"),
    (r"\bforces?\b", "may increase pressure on"),
    (r"\bhas\s+to\b", "should assess whether it needs to"),
    (r"\bneeds\s+to\b", "could evaluate whether it needs to"),
    (r"\bmust\b", "should evaluate whether it needs to"),
]

FORESIGHT_LANGUAGE_GUARDRAILS = """
FORESIGHT-MODALITY RULES — STRICT:
- Treat all foresight, trend, analyst, scenario and regulatory sources as context, not proof of current company deployment.
- Use modal language: "may", "could", "is becoming relevant", "should evaluate", "should assess whether", "if in scope", "if not already completed".
- Do NOT write deterministic language such as "must", "requires the company to", "forces", "has deployed", "is deploying", "currently uses", "broad deployment", "is non-compliant", or "will fall behind" unless a separate company-specific source proves it.
- Regulatory sources support what the regulation says and affected contexts; they do not alone prove that a named company must change a specific process.
- Do not generate deadlines earlier than the report date. If a date from a source is already in the past, treat it as historical context or write "if not already completed, in the next planning cycle".
- If company-specific evidence is missing, phrase recommendations as "validate whether...", "assess exposure to...", or "evaluate whether...".
"""
QUARTER_ENDS = {"Q1": (3,31), "Q2": (6,30), "Q3": (9,30), "Q4": (12,31)}

def build_foresight_guardrails(domain: str = "", applicability: dict | None = None) -> str:
    app_txt = ""
    if applicability:
        relevant = applicability.get("relevant_frameworks") or []
        conditional = applicability.get("conditional_frameworks") or []
        notes = applicability.get("notes") or []
        app_txt = "\nDOMAIN APPLICABILITY:\n" + f"- Domain: {domain or applicability.get('domain','unknown')}\n" + f"- Relevant frameworks: {', '.join(relevant) if relevant else 'none specified'}\n" + f"- Conditional frameworks: {', '.join(conditional) if conditional else 'none specified'}\n" + f"- Notes: {'; '.join(notes) if notes else 'Use company-specific evidence before asserting obligations.'}\n"
    return FORESIGHT_LANGUAGE_GUARDRAILS + app_txt

def _parse_quarter_deadline(text: str) -> date | None:
    m = re.match(r"\b(Q[1-4])\s+(20\d{2})\b", text.strip(), re.I)
    if not m: return None
    month, day = QUARTER_ENDS[m.group(1).upper()]
    return date(int(m.group(2)), month, day)

def validate_future_deadlines(text: str, report_date: date | None = None) -> list[dict[str, Any]]:
    report_date = report_date or date.today(); issues=[]
    if not text: return issues
    for match in re.finditer(r"\b(?:by|before|until|in)\s+(Q[1-4]\s+20\d{2})\b", text, re.I):
        d = _parse_quarter_deadline(match.group(1))
        if d and d < report_date:
            issues.append({"deadline": match.group(0), "deadline_date": d.isoformat(), "report_date": report_date.isoformat(), "issue": "past_deadline"})
    for match in re.finditer(r"\b(?:by|before|until|in)\s+(20\d{2})\b", text, re.I):
        d = date(int(match.group(1)), 12, 31)
        if d < report_date:
            issues.append({"deadline": match.group(0), "deadline_date": d.isoformat(), "report_date": report_date.isoformat(), "issue": "past_deadline"})
    return issues

def rewrite_past_deadlines(text: str, report_date: date | None = None) -> str:
    report_date = report_date or date.today()
    if not text: return text
    def repl_q(match):
        d = _parse_quarter_deadline(match.group(1))
        return "if not already completed, in the next planning cycle" if d and d < report_date else match.group(0)
    def repl_y(match):
        d = date(int(match.group(1)), 12, 31)
        return "if not already completed, in the next planning cycle" if d < report_date else match.group(0)
    out = re.sub(r"\b(?:by|before|until|in)\s+(Q[1-4]\s+20\d{2})\b", repl_q, text, flags=re.I)
    return re.sub(r"\b(?:by|before|until|in)\s+(20\d{2})\b", repl_y, out, flags=re.I)

def validate_foresight_text(text: str, report_date: date | None = None, *, allow_word_must_in_rules: bool = False) -> list[str]:
    warnings=[]
    if not text: return warnings
    for pattern in FORBIDDEN_FACT_PATTERNS:
        if allow_word_must_in_rules and pattern == r"\bmust\b": continue
        if re.search(pattern, text, re.I): warnings.append(f"Forbidden deterministic foresight wording matched: {pattern}")
    for issue in validate_future_deadlines(text, report_date):
        warnings.append(f"Past deadline found: {issue['deadline']} ({issue['deadline_date']} < report {issue['report_date']})")
    return warnings

def sanitize_foresight_modality(text: str) -> str:
    if not text: return text
    out=text
    for pat,repl in MODAL_REPLACEMENTS:
        out = re.sub(pat, repl, out, flags=re.I)
    return re.sub(r"\s{2,}", " ", out).strip()

def sanitize_foresight_text(text: str, report_date: date | None = None) -> str:
    return rewrite_past_deadlines(sanitize_foresight_modality(text), report_date)

def sanitize_future_insight(text: str, report_date: date | None = None) -> tuple[str, list[dict[str, Any]]]:
    report_date = report_date or date.today()
    issues = validate_future_deadlines(text, report_date)
    return sanitize_foresight_text(text, report_date), issues

def _split_source_and_relevance(text: str) -> tuple[str, str]:
    if not text: return "", ""
    m = re.search(r"source says:\s*(.*?)\s*(possible relevance:|relevance:)(.*)", text, re.I | re.S)
    if m: return m.group(1).strip(), m.group(3).strip()
    return text.strip(), ""

def sanitize_foresight_record(record: dict, *, report_date: date | None = None, domain: str = "") -> dict:
    if not isinstance(record, dict): return record
    rec = deepcopy(record); report_date = report_date or date.today()
    rec.setdefault("origin", "foresight"); rec.setdefault("claim_type", FORESIGHT_CLAIM_TYPE)
    rec.setdefault("allowed_claim_types", [FORESIGHT_CLAIM_TYPE]); rec.setdefault("company_specific", False)
    rec.setdefault("requires_company_specific_corroboration", True); rec.setdefault("modal_language_required", True)
    rec.setdefault("forbidden_as_current_company_fact", True); rec.setdefault("scope", "external-trend")
    rec["foresight_sanitizer_version"] = FORESIGHT_SANITIZER_VERSION
    if domain: rec.setdefault("domain_context", domain)
    description = rec.get("description") or rec.get("snippet") or rec.get("summary") or ""
    if description and not rec.get("source_says"):
        source_says, possible_relevance = _split_source_and_relevance(description)
        rec["source_says"] = sanitize_foresight_text(source_says, report_date)
        if possible_relevance: rec["possible_relevance"] = sanitize_foresight_text(possible_relevance, report_date)
    elif rec.get("source_says"):
        rec["source_says"] = sanitize_foresight_text(str(rec.get("source_says") or ""), report_date)
    if rec.get("possible_relevance"):
        rec["possible_relevance"] = sanitize_foresight_text(str(rec.get("possible_relevance") or ""), report_date)
    for key in ("description","snippet","summary","insight","impact","recommended_action"):
        if isinstance(rec.get(key), str): rec[key] = sanitize_foresight_text(rec[key], report_date)
    warnings=[]
    for key in ("description","snippet","summary","source_says","possible_relevance","insight","impact","recommended_action"):
        if isinstance(rec.get(key), str): warnings.extend(validate_foresight_text(rec[key], report_date))
    rec["foresight_language_warnings"] = sorted(set(warnings)); rec["foresight_sanitized"] = True
    return rec

def annotate_foresight_signal(signal: dict, *, theme: str = "", domain: str = "") -> dict:
    sig = sanitize_foresight_record(signal, domain=domain)
    if isinstance(sig, dict) and theme:
        sig.setdefault("fp_theme", theme); sig.setdefault("_kb_theme", theme)
    return sig

def annotate_foresight_link(link: dict, *, domain: str = "") -> dict:
    return sanitize_foresight_record(link, domain=domain)

def sanitize_foresight_collection(items: list, *, report_date: date | None = None, domain: str = "") -> list:
    return [sanitize_foresight_record(item, report_date=report_date, domain=domain) for item in (items or []) if isinstance(item, dict)]

def sanitize_report_foresight_fields(data: dict, *, report_date: date | None = None, domain: str = "") -> dict:
    if not isinstance(data, dict): return data
    out = deepcopy(data); report_date = report_date or date.today()
    for field in ["foresight_links","foresight_evidence","deep_dive_findings","source_cards","dimension_forces","future_insights","signals","evidence_links"]:
        if isinstance(out.get(field), list): out[field] = sanitize_foresight_collection(out[field], report_date=report_date, domain=domain)
    for field in ["future_insight","future_insights_text","domain_force_dossier","climate_exposure"]:
        if isinstance(out.get(field), str): out[field] = sanitize_foresight_text(out[field], report_date)
    out["foresight_sanitizer_version"] = FORESIGHT_SANITIZER_VERSION; out["foresight_sanitized"] = True
    return out
