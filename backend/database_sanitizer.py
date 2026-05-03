"""
database_sanitizer.py — Dry-run-first sanitation for stored foresight/source-card text.

This script cleans existing company records without deleting useful source-registry
/checklist memory. It rewrites deterministic foresight wording ("must", "requires",
"has deployed", etc.) and past deadlines (e.g. "by Q2 2025" in a 2026 report),
while preserving sources as registry/checklist items.

Recommended flow:
    1) python database_sanitizer.py --dry-run --company "Wärtsilä Oyj"
    2) Review database_sanitizer_report.json
    3) python database_sanitizer.py --write --company "Wärtsilä Oyj" --post-clean

All-company dry-run:
    python database_sanitizer.py --dry-run

All-company write:
    python database_sanitizer.py --write --post-clean
"""

from __future__ import annotations

import argparse
import copy
import json
import re
from datetime import date, datetime, timezone
from typing import Any

try:
    from storage import BlobStorage
except Exception:  # Allows unit testing sanitize_company_record without storage env vars.
    BlobStorage = None

# Prefer central guardrails when present, but keep this script self-contained so
# it can be copied independently and still run safely.
try:
    from foresight_guardrails import (  # type: ignore
        FORESIGHT_SANITIZER_VERSION,
        sanitize_report_foresight_fields,
        sanitize_foresight_text,
        validate_foresight_text,
        validate_future_deadlines,
    )
except Exception:
    FORESIGHT_SANITIZER_VERSION = "2026-05-03-v2-must-deadline-guardrails-standalone"

    _FORBIDDEN = [
        r"\bmust\b", r"\brequires?\b", r"\brequired\s+to\b", r"\bforces?\b",
        r"\bhas\s+to\b", r"\bneeds\s+to\b", r"\bwill\s+need\s+to\b",
        r"\bis\s+obligated\s+to\b", r"\bfaces\s+penalties\b", r"\bis\s+non[-\s]?compliant\b",
        r"\bwill\s+fall\s+behind\b", r"\bhas\s+deployed\b", r"\bis\s+deploying\b",
        r"\bcurrently\s+uses\b", r"\bbroad\s+deployment\b",
    ]
    _REPL = [
        (r"\bwill\s+need\s+to\b", "may need to"),
        (r"\bis\s+obligated\s+to\b", "may be subject to obligations if in scope and should assess whether it needs to"),
        (r"\bfaces\s+penalties\b", "may face compliance risk if in scope and if requirements are not addressed"),
        (r"\bis\s+non[-\s]?compliant\b", "may require compliance assessment if in scope"),
        (r"\bwill\s+fall\s+behind\b", "may face a disadvantage if the trend becomes material"),
        (r"\bhas\s+deployed\b", "is not proven by this foresight evidence alone to have deployed"),
        (r"\bis\s+deploying\b", "may be evaluating or could evaluate"),
        (r"\bcurrently\s+uses\b", "may need to evaluate use of"),
        (r"\bbroad\s+deployment\b", "potential broad relevance"),
        (r"\brequired\s+to\b", "potentially expected to if in scope"),
        (r"\brequires?\b", "may require"),
        (r"\bforces?\b", "may increase pressure on"),
        (r"\bhas\s+to\b", "should assess whether it needs to"),
        (r"\bneeds\s+to\b", "could evaluate whether it needs to"),
        (r"\bmust\b", "should evaluate whether it needs to"),
    ]
    _Q_ENDS = {"Q1": (3, 31), "Q2": (6, 30), "Q3": (9, 30), "Q4": (12, 31)}

    def _parse_q(qtxt: str) -> date | None:
        m = re.match(r"\b(Q[1-4])\s+(20\d{2})\b", qtxt.strip(), re.I)
        if not m:
            return None
        month, day = _Q_ENDS[m.group(1).upper()]
        return date(int(m.group(2)), month, day)

    def validate_future_deadlines(text: str, report_date: date | None = None) -> list[dict[str, Any]]:
        report_date = report_date or date.today()
        issues = []
        if not text:
            return issues
        for match in re.finditer(r"\b(?:by|before|until|in)\s+(Q[1-4]\s+20\d{2})\b", text, re.I):
            d = _parse_q(match.group(1))
            if d and d < report_date:
                issues.append({"deadline": match.group(0), "deadline_date": d.isoformat(), "report_date": report_date.isoformat(), "issue": "past_deadline"})
        for match in re.finditer(r"\b(?:by|before|until|in)\s+(20\d{2})\b", text, re.I):
            d = date(int(match.group(1)), 12, 31)
            if d < report_date:
                issues.append({"deadline": match.group(0), "deadline_date": d.isoformat(), "report_date": report_date.isoformat(), "issue": "past_deadline"})
        return issues

    def _rewrite_past_deadlines(text: str, report_date: date | None = None) -> str:
        report_date = report_date or date.today()
        def repl_q(match: re.Match) -> str:
            d = _parse_q(match.group(1))
            return "if not already completed, in the next planning cycle" if d and d < report_date else match.group(0)
        def repl_y(match: re.Match) -> str:
            d = date(int(match.group(1)), 12, 31)
            return "if not already completed, in the next planning cycle" if d < report_date else match.group(0)
        out = re.sub(r"\b(?:by|before|until|in)\s+(Q[1-4]\s+20\d{2})\b", repl_q, text or "", flags=re.I)
        return re.sub(r"\b(?:by|before|until|in)\s+(20\d{2})\b", repl_y, out, flags=re.I)

    def sanitize_foresight_text(text: str, report_date: date | None = None) -> str:
        out = text or ""
        for pat, repl in _REPL:
            out = re.sub(pat, repl, out, flags=re.I)
        out = _rewrite_past_deadlines(out, report_date)
        return re.sub(r"\s{2,}", " ", out).strip()

    def validate_foresight_text(text: str, report_date: date | None = None) -> list[str]:
        warnings = []
        if not text:
            return warnings
        for pat in _FORBIDDEN:
            if re.search(pat, text, re.I):
                warnings.append(f"Forbidden deterministic foresight wording matched: {pat}")
        for issue in validate_future_deadlines(text, report_date):
            warnings.append(f"Past deadline found: {issue['deadline']} ({issue['deadline_date']} < report {issue['report_date']})")
        return warnings

    def sanitize_report_foresight_fields(data: dict, *, report_date: date | None = None, domain: str = "") -> dict:
        out = copy.deepcopy(data or {})
        list_fields = ["foresight_links", "foresight_evidence", "deep_dive_findings", "source_cards", "dimension_forces", "future_insights", "signals", "evidence_links"]
        text_keys = ["description", "snippet", "summary", "source_says", "possible_relevance", "insight", "impact", "recommended_action", "assessment", "evidence_snippet"]
        for field in list_fields:
            if isinstance(out.get(field), list):
                for item in out[field]:
                    if not isinstance(item, dict):
                        continue
                    item.setdefault("foresight_sanitized", True)
                    item.setdefault("foresight_sanitizer_version", FORESIGHT_SANITIZER_VERSION)
                    item.setdefault("claim_type", "foresight_context")
                    item.setdefault("company_specific", False)
                    item.setdefault("requires_company_specific_corroboration", True)
                    description = item.get("description") or item.get("snippet") or item.get("summary") or ""
                    if description and not item.get("source_says"):
                        item["source_says"] = sanitize_foresight_text(description, report_date)
                    for key in text_keys:
                        if isinstance(item.get(key), str):
                            item[key] = sanitize_foresight_text(item[key], report_date)
        for field in ["future_insight", "future_insights_text", "domain_force_dossier", "climate_exposure"]:
            if isinstance(out.get(field), str):
                out[field] = sanitize_foresight_text(out[field], report_date)
        out["foresight_sanitized"] = True
        out["foresight_sanitizer_version"] = FORESIGHT_SANITIZER_VERSION
        return out


def _is_probably_foresight_record(item: dict) -> bool:
    if not isinstance(item, dict):
        return False
    claim_type = str(item.get("claim_type") or "").lower()
    source_type = str(item.get("source_type") or item.get("category") or "").lower()
    allowed = [str(x).lower() for x in (item.get("allowed_claim_types") or [])]
    origin = str(item.get("origin") or "").lower()
    scope = str(item.get("scope") or "").lower()
    text = " ".join(str(item.get(k) or "") for k in ["title", "snippet", "description", "summary"]).lower()
    return (
        claim_type in {"foresight_context", "regulatory_context"}
        or origin == "foresight"
        or scope == "external-trend"
        or source_type in {"foresight", "foresight_report", "trend_report", "regulation", "regulatory_context", "analyst"}
        or "foresight_context" in allowed
        or "regulatory_context" in allowed
        or any(t in text for t in ["foresight", "trend", "agentic ai", "digital product passport", "net-zero industry act", "nzia", "eu ai act"])
    )


def _collect_text_issues(text: str, report_date: date | None = None) -> dict[str, Any]:
    if not text:
        return {"deterministic_warnings": [], "past_deadline_issues": []}
    return {
        "deterministic_warnings": validate_foresight_text(text, report_date),
        "past_deadline_issues": validate_future_deadlines(text, report_date),
    }


def _count_changed_strings(before: Any, after: Any, path: str = "") -> list[dict[str, str]]:
    changes: list[dict[str, str]] = []
    if isinstance(before, dict) and isinstance(after, dict):
        for key in sorted(set(before) | set(after)):
            changes.extend(_count_changed_strings(before.get(key), after.get(key), f"{path}.{key}" if path else key))
    elif isinstance(before, list) and isinstance(after, list):
        for idx, (b, a) in enumerate(zip(before, after)):
            changes.extend(_count_changed_strings(b, a, f"{path}[{idx}]"))
    elif isinstance(before, str) and isinstance(after, str) and before != after:
        changes.append({"path": path, "before": before[:240], "after": after[:240]})
    return changes


def classify_source_registry_items(record: dict) -> dict:
    """Keep source/checklist memory but add generation eligibility metadata."""
    out = copy.deepcopy(record or {})
    for field in ["evidence_links", "foresight_links", "foresight_evidence", "deep_dive_findings", "source_cards"]:
        values = out.get(field)
        if not isinstance(values, list):
            continue
        for item in values:
            if not isinstance(item, dict):
                continue
            item.setdefault("search_skip", True)
            if item.get("wrong_entity") is True or item.get("verified") is False:
                item["generation_eligible"] = False
                item["excluded_from_generation"] = True
                item["source_registry_status"] = "checked_excluded"
                item["exclusion_reason"] = "wrong_entity_or_unverified"
            elif _is_probably_foresight_record(item):
                item.setdefault("generation_eligible", True)
                item.setdefault("claim_type", "foresight_context")
                item.setdefault("company_specific", False)
                item.setdefault("requires_company_specific_corroboration", True)
                item["excluded_from_company_fact_generation"] = True
                item.setdefault("source_registry_status", "checked_context_only")
                item.setdefault("exclusion_reason", "context_only_not_company_fact")
            else:
                item.setdefault("generation_eligible", True)
                item.setdefault("source_registry_status", "checked_generation_eligible")
    return out


def sanitize_company_record(record: dict, domain: str = "", report_date: date | None = None) -> tuple[dict, dict]:
    report_date = report_date or date.today()
    original = copy.deepcopy(record or {})
    classified = classify_source_registry_items(original)
    cleaned = sanitize_report_foresight_fields(classified, report_date=report_date, domain=domain)

    meta = cleaned.setdefault("sanitation_metadata", {})
    meta["foresight_sanitized"] = True
    meta["foresight_sanitizer_version"] = FORESIGHT_SANITIZER_VERSION
    meta["foresight_sanitized_at"] = datetime.now(timezone.utc).isoformat()
    meta["report_date_used"] = report_date.isoformat()

    changes = _count_changed_strings(original, cleaned)
    deterministic_count = 0
    past_deadline_count = 0
    for ch in changes:
        issues_before = _collect_text_issues(ch["before"], report_date)
        deterministic_count += len(issues_before["deterministic_warnings"])
        past_deadline_count += len(issues_before["past_deadline_issues"])

    meta["changed_string_fields"] = len(changes)
    meta["deterministic_language_issues_found"] = deterministic_count
    meta["past_deadline_issues_found"] = past_deadline_count

    audit = {
        "changed": cleaned != original,
        "changed_string_fields": len(changes),
        "deterministic_language_issues_found": deterministic_count,
        "past_deadline_issues_found": past_deadline_count,
        "sample_changes": changes[:20],
        "sanitizer_version": FORESIGHT_SANITIZER_VERSION,
    }
    return cleaned, audit


def _company_name_from_summary(summary: dict) -> str:
    return summary.get("name") or summary.get("company_name") or summary.get("company") or summary.get("key") or ""


def sanitize_database(
    *,
    company: str | None = None,
    dry_run: bool = True,
    write: bool = False,
    post_clean: bool = False,
    report_date: date | None = None,
) -> dict:
    if BlobStorage is None:
        raise RuntimeError("BlobStorage could not be imported. Check storage.py and environment variables.")
    storage = BlobStorage()
    report_date = report_date or date.today()
    summaries = storage.get_all_companies_summary()
    selected = []
    for s in summaries:
        name = _company_name_from_summary(s)
        if company and company.lower() not in name.lower():
            continue
        if name:
            selected.append(name)

    results = []
    for name in selected:
        record = storage.get_company_data(name)
        if not record:
            results.append({"company": name, "status": "not_found"})
            continue
        domain = record.get("domain") or record.get("industry_domain") or ""
        cleaned, audit = sanitize_company_record(record, domain=domain, report_date=report_date)
        status = "unchanged"
        if audit["changed"]:
            status = "changed_dry_run" if dry_run or not write else "changed_written"
            if write and not dry_run:
                storage.save_company_data(name, cleaned, post_clean=post_clean)
        results.append({"company": name, "status": status, **audit})

    return {
        "dry_run": dry_run,
        "write": write,
        "post_clean": post_clean,
        "report_date": report_date.isoformat(),
        "companies_checked": len(selected),
        "companies_changed": sum(1 for r in results if r.get("changed")),
        "results": results,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Sanitize stored foresight/source-card text in company records.")
    parser.add_argument("--company", help="Optional company name substring to sanitize")
    parser.add_argument("--dry-run", action="store_true", help="Do not write changes; produce audit only")
    parser.add_argument("--write", action="store_true", help="Write sanitized records")
    parser.add_argument("--post-clean", action="store_true", help="When writing, prune old versions and keep only cleaned record")
    parser.add_argument("--report-date", help="YYYY-MM-DD report date for past-deadline checks; defaults to today")
    parser.add_argument("--out", default="database_sanitizer_report.json", help="Output audit JSON path")
    args = parser.parse_args()
    rd = date.fromisoformat(args.report_date) if args.report_date else date.today()
    dry_run = args.dry_run or not args.write
    report = sanitize_database(company=args.company, dry_run=dry_run, write=args.write, post_clean=args.post_clean, report_date=rd)
    with open(args.out, "w", encoding="utf-8") as f:
        json.dump(report, f, indent=2, ensure_ascii=False)
    print(json.dumps({k: report[k] for k in ["dry_run", "write", "post_clean", "companies_checked", "companies_changed"]}, indent=2))
    print(f"Audit written to {args.out}")


if __name__ == "__main__":
    main()
