"""
database_sanitizer.py — Dry-run-first sanitation for stored foresight/source-card text.

This script preserves source-registry/checklist memory while cleaning stored
foresight/context snippets and future-facing text. It is intentionally dry-run
first and returns debug information if no company records are selected.
"""

from __future__ import annotations

import argparse
import copy
import json
import re
import unicodedata
from datetime import date, datetime, timezone
from typing import Any

try:
    from storage import BlobStorage
except Exception:
    BlobStorage = None

try:
    from foresight_guardrails import (  # type: ignore
        FORESIGHT_SANITIZER_VERSION,
        sanitize_report_foresight_fields,
        sanitize_foresight_text,
        validate_foresight_text,
        validate_future_deadlines,
    )
    _GUARDRAILS_IMPORTED = True
except Exception:
    _GUARDRAILS_IMPORTED = False
    FORESIGHT_SANITIZER_VERSION = "2026-08-28-v4-idempotent-standalone"

    # Narrowed patterns — no bare \brequires?\b (flags "may require")
    # and no bare \bforces?\b (flags noun "forces are building").
    _FORBIDDEN = [
        r"\bmust\b",
        r"\brequires?\s+(?:the\s+company|the\s+firm|[A-Z][a-zA-Z\xc0-\xff\-]+)\s+to\b",
        r"\brequired\s+to\b",
        r"\bforces?\s+(?:the\s+company|the\s+firm|[A-Z][a-zA-Z\xc0-\xff\-]+)\b",
        r"\bhas\s+to\b",
        r"\bwill\s+need\s+to\b",
        r"\bis\s+obligated\s+to\b",
        r"\bfaces\s+penalties\b",
        r"\bis\s+non[-\s]?compliant\b",
        r"\bwill\s+fall\s+behind\b",
        r"\bhas\s+deployed\b",
        r"\bis\s+deploying\b",
        r"\bcurrently\s+uses\b",
        r"\bbroad\s+deployment\b",
    ]
    # Lookbehinds prevent re-application on second pass (idempotency).
    _REPL = [
        (r"\bwill\s+need\s+to\b", "may need to"),
        (r"\bis\s+obligated\s+to\b",
         "may be subject to obligations if in scope and should assess whether it needs to"),
        (r"\bfaces\s+penalties\b",
         "may face compliance risk if in scope and if requirements are not addressed"),
        (r"\bis\s+non[-\s]?compliant\b", "may require compliance assessment if in scope"),
        (r"\bwill\s+fall\s+behind\b", "may face a disadvantage if the trend becomes material"),
        (r"\bhas\s+deployed\b",
         "is not proven by this foresight evidence alone to have deployed"),
        (r"\bis\s+deploying\b", "may be evaluating or could evaluate"),
        (r"\bcurrently\s+uses\b", "may need to evaluate use of"),
        (r"\bbroad\s+deployment\b", "potential broad relevance"),
        (r"\brequired\s+to\b", "potentially expected to if in scope"),
        (r"(?<!may )(?<!could )(?<!should )(?<!might )(?<!would )\brequires?\b", "may require"),
        (r"\bforces?\b", "may increase pressure on"),
        (r"\bhas\s+to\b", "should assess whether it needs to"),
        (r"(?<!whether it )(?<!whether )\bneeds\s+to\b", "could evaluate whether it needs to"),
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
        issues: list[dict[str, Any]] = []
        if not text:
            return issues
        for match in re.finditer(r"\b(?:by|before|until|in)\s+(Q[1-4]\s+20\d{2})\b", text, re.I):
            d = _parse_q(match.group(1))
            if d and d < report_date:
                issues.append({
                    "deadline": match.group(0),
                    "deadline_date": d.isoformat(),
                    "report_date": report_date.isoformat(),
                    "issue": "past_deadline",
                })
        for match in re.finditer(r"\b(?:by|before|until|in)\s+(20\d{2})\b", text, re.I):
            d = date(int(match.group(1)), 12, 31)
            if d < report_date:
                issues.append({
                    "deadline": match.group(0),
                    "deadline_date": d.isoformat(),
                    "report_date": report_date.isoformat(),
                    "issue": "past_deadline",
                })
        return issues

    def _collapse_repeated_modals(text: str) -> str:
        if not text:
            return text
        _M = r"(?:may|could|should|might|would)"
        prev = None
        while prev != text:
            prev = text
            text = re.sub(rf"\b({_M})\s+\1\b", r"\1", text, flags=re.I)
            text = re.sub(
                rf"\b({_M})\s+evaluate whether(?:\s+it)?\s+(?:{_M})\s+evaluate whether\b",
                r"\1 evaluate whether", text, flags=re.I,
            )
            text = re.sub(
                rf"\bevaluate whether(?:\s+it)?\s+(?:{_M})\s+evaluate whether\b",
                "evaluate whether", text, flags=re.I,
            )
        return re.sub(r"\s{2,}", " ", text).strip()

    def _rewrite_past_deadlines(text: str, report_date: date | None = None) -> str:
        report_date = report_date or date.today()

        def repl_q(match: re.Match) -> str:
            d = _parse_q(match.group(1))
            return (
                "if not already completed, in the next planning cycle"
                if d and d < report_date
                else match.group(0)
            )

        def repl_y(match: re.Match) -> str:
            d = date(int(match.group(1)), 12, 31)
            return (
                "if not already completed, in the next planning cycle"
                if d < report_date
                else match.group(0)
            )

        out = re.sub(r"\b(?:by|before|until|in)\s+(Q[1-4]\s+20\d{2})\b", repl_q, text or "", flags=re.I)
        return re.sub(r"\b(?:by|before|until|in)\s+(20\d{2})\b", repl_y, out, flags=re.I)

    _DEPLOYMENT_REPL = [
        (r"\bwill\s+fall\s+behind\b", "may face a disadvantage if the trend becomes material"),
        (r"\bhas\s+deployed\b",
         "is not proven by this foresight evidence alone to have deployed"),
        (r"\bis\s+deploying\b", "may be evaluating or could evaluate"),
        (r"\bcurrently\s+uses\b", "may need to evaluate use of"),
        (r"\bbroad\s+deployment\b", "potential broad relevance"),
    ]

    def sanitize_source_description(text: str, report_date: date | None = None) -> str:
        out = text or ""
        for pat, repl in _DEPLOYMENT_REPL:
            out = re.sub(pat, repl, out, flags=re.I)
        out = _collapse_repeated_modals(out)
        out = _rewrite_past_deadlines(out, report_date)
        return re.sub(r"\s{2,}", " ", out).strip()

    def sanitize_foresight_text(text: str, report_date: date | None = None) -> str:
        out = text or ""
        for pat, repl in _REPL:
            out = re.sub(pat, repl, out, flags=re.I)
        out = _collapse_repeated_modals(out)
        out = _rewrite_past_deadlines(out, report_date)
        return re.sub(r"\s{2,}", " ", out).strip()

    def validate_foresight_text(text: str, report_date: date | None = None) -> list[str]:
        warnings: list[str] = []
        if not text:
            return warnings
        for pat in _FORBIDDEN:
            if re.search(pat, text, re.I):
                warnings.append(f"Forbidden deterministic foresight wording matched: {pat}")
        for issue in validate_future_deadlines(text, report_date):
            warnings.append(
                f"Past deadline found: {issue['deadline']} "
                f"({issue['deadline_date']} < report {issue['report_date']})"
            )
        return warnings

    def sanitize_report_foresight_fields(
        data: dict,
        *,
        report_date: date | None = None,
        domain: str = "",
        company_name: str = "",
    ) -> dict:
        out = copy.deepcopy(data or {})
        list_fields = [
            "foresight_links", "foresight_evidence", "deep_dive_findings",
            "source_cards", "dimension_forces", "future_insights", "signals", "evidence_links",
        ]
        text_keys = [
            "description", "snippet", "summary", "source_says", "possible_relevance",
            "insight", "impact", "recommended_action", "assessment", "evidence_snippet",
        ]
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
                    item.setdefault("not_evidence_of_deployment", True)
                    description = (
                        item.get("description") or item.get("snippet") or item.get("summary") or ""
                    )
                    if description and not item.get("source_says"):
                        item["source_says"] = sanitize_source_description(description, report_date)
                    _desc_keys = {"description", "snippet", "summary", "source_says", "assessment", "evidence_snippet"}
                    for key in text_keys:
                        if isinstance(item.get(key), str):
                            if key in _desc_keys:
                                item[key] = sanitize_source_description(item[key], report_date)
                            else:
                                item[key] = sanitize_foresight_text(item[key], report_date)
        for field in ["future_insight", "future_insights_text", "domain_force_dossier", "climate_exposure"]:
            if isinstance(out.get(field), str):
                out[field] = sanitize_foresight_text(out[field], report_date)
        out["foresight_sanitized"] = True
        out["foresight_sanitizer_version"] = FORESIGHT_SANITIZER_VERSION
        return out


# Source types that identify direct company evidence — never classify as context-only.
COMPANY_SPECIFIC_SOURCE_TYPES = frozenset({
    "company_report", "annual_report", "company_press_release", "press_release",
    "investor_report", "case_study", "company_website", "company_filing",
    "earnings_report", "sustainability_report",
})


def _is_company_specific_evidence(item: dict) -> bool:
    """Return True when this item is direct company evidence, not generic foresight/trend context."""
    source_type = str(item.get("source_type") or "").lower()
    if source_type in COMPANY_SPECIFIC_SOURCE_TYPES:
        return True
    entity_match = str(item.get("entity_match") or "").lower()
    company_mentioned = item.get("company_mentioned")
    if entity_match == "exact" and company_mentioned is True:
        return True
    if item.get("company_specific") is True:
        return True
    return False


def _norm_company_name(value: str) -> str:
    value = value or ""
    value = unicodedata.normalize("NFKD", value)
    value = "".join(ch for ch in value if not unicodedata.combining(ch))
    value = value.lower()
    value = re.sub(r"\b(oyj|abp|plc|ltd|inc|corp|corporation|group)\b", "", value)
    value = re.sub(r"[^a-z0-9]+", " ", value)
    return re.sub(r"\s+", " ", value).strip()


def _company_matches_filter(company_filter: str | None, name: str) -> bool:
    if not company_filter:
        return True
    wanted = _norm_company_name(company_filter)
    actual = _norm_company_name(name)
    if not wanted:
        return True
    return wanted in actual or actual in wanted


def _company_name_from_summary(summary: Any) -> str:
    if isinstance(summary, str):
        return summary.strip()
    if not isinstance(summary, dict):
        return ""
    for key in ["companyName", "company_name", "company", "name", "displayName", "display_name", "title", "id", "key"]:
        value = summary.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return ""


def _is_probably_foresight_record(item: dict) -> bool:
    if not isinstance(item, dict):
        return False
    claim_type = str(item.get("claim_type") or "").lower()
    source_type = str(item.get("source_type") or item.get("category") or "").lower()
    allowed = [str(x).lower() for x in (item.get("allowed_claim_types") or [])]
    origin = str(item.get("origin") or "").lower()
    scope = str(item.get("scope") or "").lower()
    text = " ".join(
        str(item.get(k) or "") for k in ["title", "snippet", "description", "summary"]
    ).lower()
    return (
        claim_type in {"foresight_context", "regulatory_context"}
        or origin == "foresight"
        or scope == "external-trend"
        or source_type in {
            "foresight", "foresight_report", "trend_report",
            "regulation", "regulatory_context", "analyst",
        }
        or "foresight_context" in allowed
        or "regulatory_context" in allowed
        or any(
            t in text
            for t in [
                "foresight", "trend", "agentic ai", "digital product passport",
                "net-zero industry act", "nzia", "eu ai act",
            ]
        )
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
            # Skip runtime-only metadata — timestamp changes are not content changes.
            if not path and key in {"sanitation_metadata"}:
                continue
            changes.extend(
                _count_changed_strings(
                    before.get(key), after.get(key), f"{path}.{key}" if path else key
                )
            )
    elif isinstance(before, list) and isinstance(after, list):
        for idx, (b, a) in enumerate(zip(before, after)):
            changes.extend(_count_changed_strings(b, a, f"{path}[{idx}]"))
    elif isinstance(before, str) and isinstance(after, str) and before != after:
        changes.append({"path": path, "before": before[:240], "after": after[:240]})
    return changes


def classify_source_registry_items(record: dict) -> dict:
    """Keep source/checklist memory without implying source verification.

    Company-specific evidence is preserved as company-specific, but it is not
    generation-eligible unless Clean Evidence has already promoted it through
    analysis verification.
    """
    out = copy.deepcopy(record or {})
    for field in [
        "evidence_links", "foresight_links", "foresight_evidence",
        "deep_dive_findings", "source_cards",
    ]:
        values = out.get(field)
        if not isinstance(values, list):
            continue
        for item in values:
            if not isinstance(item, dict):
                continue
            item.setdefault("search_skip", True)
            if _is_probably_foresight_record(item) and not _is_company_specific_evidence(item):
                # Macro foresight is not expected to match the company entity.
                # Repair it under the separate foresight-context contract first.
                _mark_sanitized_foresight_context(item)
            elif item.get("wrong_entity") is True or item.get("verified") is False:
                item["generation_eligible"] = False
                item.setdefault("analysis_verified", False)
                item["promotion_status"] = "excluded"
                item.setdefault("claim_support_status", "unavailable")
                item.setdefault("verification_reason", "wrong_entity_or_unverified")
                item["excluded_from_generation"] = True
                item["source_registry_status"] = "checked_excluded"
                item["exclusion_reason"] = "wrong_entity_or_unverified"
            else:
                # Company-specific evidence: preserve scope, but do not imply
                # analysis verification from sanitation alone.
                # If previously mis-classified as context-only, clear that flag.
                if _has_analysis_verified_promotion(item):
                    item["generation_eligible"] = True
                else:
                    item.setdefault("analysis_verified", False)
                    item["generation_eligible"] = False
                    item.setdefault("promotion_status", "display_only")
                    item.setdefault("claim_support_status", "unchecked")
                    item.setdefault("verification_reason", "company_specific_not_analysis_verified")
                if _is_company_specific_evidence(item) and item.get("excluded_from_company_fact_generation"):
                    item.pop("excluded_from_company_fact_generation", None)
                    item["source_registry_status"] = "checked_company_specific"
                    item.setdefault("exclusion_reason", "company_specific_evidence_preserved")
                else:
                    item.setdefault("source_registry_status", "checked_company_specific")
    return out


def _has_analysis_verified_promotion(item: dict) -> bool:
    """True only when Clean Evidence has promoted this source for generated prose."""
    return (
        isinstance(item, dict)
        and item.get("analysis_verified") is True
        and item.get("promotion_status") == "generation_eligible"
    )


def _mark_sanitized_foresight_context(item: dict) -> None:
    """Repair generic foresight metadata without implying company-fact evidence."""
    item["claim_type"] = "foresight_context"
    item["company_specific"] = False
    item["requires_company_specific_corroboration"] = True
    item["not_evidence_of_deployment"] = True
    item.setdefault("modal_language_required", True)
    item.setdefault("generation_scope", "foresight_synthesis_only")
    item["excluded_from_company_fact_generation"] = True
    item["exclusion_reason"] = "context_only_not_company_fact"
    item.setdefault("stored_metadata_repair_version", FORESIGHT_SANITIZER_VERSION)

    legacy_supported = (
        item.get("verified") is True
        and item.get("validated") is True
        and item.get("source_readable", item.get("content_verified")) is True
        and item.get("theme_match", True) is not False
        and item.get("trend_supported", True) is not False
        and item.get("verification_conflict") is not True
        and str(item.get("source_audit_status") or "").lower()
        not in {"source_dead", "source_unreadable", "excluded"}
    )
    context_eligible = item.get("foresight_context_eligible") is True or legacy_supported
    item["foresight_context_eligible"] = context_eligible
    item["foresight_signal_status"] = (
        "context_eligible" if context_eligible else "context_only_unverified"
    )
    item["source_registry_status"] = item["foresight_signal_status"]
    item["source_audit_status"] = "accepted" if context_eligible else "requires_revalidation"
    item["verification_reason"] = (
        "foresight_context_contract_satisfied"
        if context_eligible
        else "foresight_context_requires_revalidation"
    )
    for legacy_field in (
        "analysis_verified", "generation_eligible", "promotion_status",
        "claim_support_status", "entity_match_status", "content_entity_match_status",
    ):
        item.pop(legacy_field, None)


def _company_name_from_record(record: dict) -> str:
    """Extract company name from a stored company record."""
    return (
        record.get("name")
        or record.get("companyName")
        or record.get("company_name")
        or record.get("company")
        or record.get("key")
        or ""
    )


def sanitize_company_record(
    record: dict, domain: str = "", report_date: date | None = None
) -> tuple[dict, dict]:
    report_date = report_date or date.today()
    original = copy.deepcopy(record or {})
    company_name = _company_name_from_record(original)

    classified = classify_source_registry_items(original)

    # Thread company_name so source_says neutrality is enforced per-link.
    if _GUARDRAILS_IMPORTED:
        cleaned = sanitize_report_foresight_fields(
            classified, report_date=report_date, domain=domain, company_name=company_name
        )
    else:
        cleaned = sanitize_report_foresight_fields(
            classified, report_date=report_date, domain=domain
        )

    removed_legacy_summary = False
    for legacy_field in (
        "foresight_summary", "foresight_summary_model",
        "foresight_summary_status", "foresight_summary_skip_reason",
    ):
        if legacy_field in cleaned:
            cleaned.pop(legacy_field, None)
            removed_legacy_summary = True

    # Count content changes BEFORE adding runtime metadata (timestamp excluded from diff).
    changes = _count_changed_strings(original, cleaned)
    deterministic_count = 0
    past_deadline_count = 0
    for ch in changes:
        issues_before = _collect_text_issues(ch["before"], report_date)
        deterministic_count += len(issues_before["deterministic_warnings"])
        past_deadline_count += len(issues_before["past_deadline_issues"])

    # Add sanitation metadata — preserve existing timestamp when version matches
    # so that a second identical run produces zero string changes (idempotency).
    meta = cleaned.setdefault("sanitation_metadata", {})
    existing_version = record.get("sanitation_metadata", {}).get("foresight_sanitizer_version")
    existing_ts = record.get("sanitation_metadata", {}).get("foresight_sanitized_at")
    if existing_version == FORESIGHT_SANITIZER_VERSION and existing_ts and not changes:
        meta["foresight_sanitized_at"] = existing_ts
    else:
        meta["foresight_sanitized_at"] = datetime.now(timezone.utc).isoformat()
    meta["foresight_sanitized"] = True
    meta["foresight_sanitizer_version"] = FORESIGHT_SANITIZER_VERSION
    meta["report_date_used"] = report_date.isoformat()
    meta["changed_string_fields"] = len(changes)
    meta["deterministic_language_issues_found"] = deterministic_count
    meta["past_deadline_issues_found"] = past_deadline_count
    if removed_legacy_summary:
        meta["legacy_foresight_summary_removed"] = True

    audit = {
        "changed": bool(changes),
        "changed_string_fields": len(changes),
        "deterministic_language_issues_found": deterministic_count,
        "past_deadline_issues_found": past_deadline_count,
        "sample_changes": changes[:20],
        "sanitizer_version": FORESIGHT_SANITIZER_VERSION,
        "legacy_foresight_summary_removed": removed_legacy_summary,
    }
    return cleaned, audit


def _build_summary_debug(summaries: Any, selected: list[str], company: str | None) -> dict[str, Any]:
    sample = None
    sample_keys: list[str] = []
    if isinstance(summaries, list) and summaries:
        sample = summaries[0]
        if isinstance(sample, dict):
            sample_keys = list(sample.keys())
    return {
        "company_filter": company,
        "normalized_company_filter": _norm_company_name(company or ""),
        "summary_type": type(summaries).__name__,
        "summary_count": len(summaries) if isinstance(summaries, list) else 0,
        "sample_summary_keys": sample_keys,
        "sample_summary": sample,
        "selected_companies_preview": selected[:20],
    }


def sanitize_database(
    *,
    company: str | None = None,
    dry_run: bool = True,
    write: bool = False,
    post_clean: bool = False,
    report_date: date | None = None,
) -> dict:
    if BlobStorage is None:
        raise RuntimeError(
            "BlobStorage could not be imported. Check storage.py and environment variables."
        )
    storage = BlobStorage()
    report_date = report_date or date.today()
    summaries = storage.get_all_companies_summary()

    selected: list[str] = []
    if isinstance(summaries, list):
        for summary in summaries:
            name = _company_name_from_summary(summary)
            if not name:
                continue
            if not _company_matches_filter(company, name):
                continue
            selected.append(name)

    summary_debug = _build_summary_debug(summaries, selected, company)
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
        "storage_summary_debug": summary_debug,
        "selected_companies": selected[:50],
        "results": results,
    }


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Sanitize stored foresight/source-card text in company records."
    )
    parser.add_argument("--company", help="Optional company name substring to sanitize")
    parser.add_argument(
        "--dry-run", action="store_true", help="Do not write changes; produce audit only"
    )
    parser.add_argument("--write", action="store_true", help="Write sanitized records")
    parser.add_argument(
        "--post-clean",
        action="store_true",
        help="When writing, prune old versions and keep only cleaned record",
    )
    parser.add_argument(
        "--report-date",
        help="YYYY-MM-DD report date for past-deadline checks; defaults to today",
    )
    parser.add_argument(
        "--out", default="database_sanitizer_report.json", help="Output audit JSON path"
    )
    args = parser.parse_args()

    rd = date.fromisoformat(args.report_date) if args.report_date else date.today()
    dry_run = args.dry_run or not args.write
    report = sanitize_database(
        company=args.company,
        dry_run=dry_run,
        write=args.write,
        post_clean=args.post_clean,
        report_date=rd,
    )
    with open(args.out, "w", encoding="utf-8") as f:
        json.dump(report, f, indent=2, ensure_ascii=False)
    print(
        json.dumps(
            {k: report[k] for k in ["dry_run", "write", "post_clean", "companies_checked", "companies_changed"]},
            indent=2,
        )
    )
    if report.get("companies_checked") == 0:
        print("WARNING: No company records were selected. Inspect storage_summary_debug in the audit JSON.")
    print(f"Audit written to {args.out}")


if __name__ == "__main__":
    main()
