"""
Shared Company Source Ledger.

The ledger is reusable source memory for one company. It combines Scanner
evidence, financial source records, foresight links, Company Evidence DB records,
and Buying Insight source references without promoting anything to truth.
Scanner and Buying Insight may read it, but each still applies its own gates.
"""

from __future__ import annotations

import hashlib
import html
import re
from collections import Counter
from datetime import datetime, timezone
from typing import Any
from urllib.parse import urlparse

from source_quality import is_generation_eligible_source


STALE_AFTER_DAYS = 180
CONTEXT_ONLY_TYPES = {"foresight", "trend", "market_regulatory_pressure", "regulatory_context"}


class CompanySourceLedgerError(RuntimeError):
    def __init__(self, message: str, status_code: int = 400):
        super().__init__(message)
        self.status_code = status_code


def _list(value: Any) -> list:
    return value if isinstance(value, list) else []


def _dict(value: Any) -> dict:
    return value if isinstance(value, dict) else {}


def _clip(value: Any, limit: int = 900) -> str:
    return re.sub(r"\s+", " ", str(value or "")).strip()[:limit]


def _normalize_url(url: Any) -> str:
    value = str(url or "").strip()
    if not value:
        return ""
    if "://" not in value:
        value = "https://" + value
    parsed = urlparse(value)
    if not parsed.netloc:
        return ""
    return parsed._replace(fragment="", query="").geturl().rstrip("/")


def _host(url: Any) -> str:
    value = _normalize_url(url)
    if not value:
        return ""
    host = urlparse(value).netloc.lower()
    return host[4:] if host.startswith("www.") else host


def _content_hash(*parts: Any) -> str:
    text = "\n".join(str(p or "") for p in parts if p)
    if not text.strip():
        return ""
    return hashlib.sha256(text.encode("utf-8", errors="ignore")).hexdigest()[:24]


def _parse_date(value: Any):
    if not value:
        return None
    text = str(value).strip()
    try:
        if text.endswith("Z"):
            text = text[:-1] + "+00:00"
        dt = datetime.fromisoformat(text)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt
    except Exception:
        return None


def _freshness(record: dict, generated_at: str) -> dict:
    checked = (
        record.get("last_checked")
        or record.get("fetched_at")
        or record.get("verified_at")
        or record.get("updated_at")
        or record.get("generated_at")
        or generated_at
    )
    dt = _parse_date(checked)
    if not dt:
        return {"last_checked": checked, "source_freshness": "unknown", "is_stale": False, "age_days": None}
    age = max(0, (datetime.now(timezone.utc) - dt).days)
    if age <= 45:
        label = "fresh"
    elif age <= STALE_AFTER_DAYS:
        label = "current"
    else:
        label = "stale"
    return {"last_checked": checked, "source_freshness": label, "is_stale": age > STALE_AFTER_DAYS, "age_days": age}


def _truthy(value: Any) -> bool:
    return value is True or str(value).lower() in {"true", "yes", "1", "generation_eligible", "supported"}


def _base_source(raw: dict, origin: str, company_name: str, generated_at: str, idx: int) -> dict | None:
    if not isinstance(raw, dict):
        return None
    url = _normalize_url(raw.get("url") or raw.get("source_url") or raw.get("link"))
    quote = _clip(
        raw.get("source_quote")
        or raw.get("quote")
        or raw.get("snippet")
        or raw.get("evidence_snippet")
        or raw.get("description")
        or raw.get("text_excerpt"),
        700,
    )
    title = _clip(raw.get("title") or raw.get("source_title") or raw.get("source") or url or origin, 180)
    hash_value = raw.get("content_hash") or _content_hash(url, title, quote)
    if not url and not hash_value:
        return None

    source_type = raw.get("source_type") or raw.get("claim_type") or raw.get("scope") or raw.get("source_category")
    if origin == "foresight" and not source_type:
        source_type = "foresight"
    if origin == "financial" and not source_type:
        source_type = "financial"

    freshness = _freshness(raw, generated_at)
    context_only = bool(raw.get("context_only")) or str(source_type or "").lower() in CONTEXT_ONLY_TYPES or origin == "foresight"
    wrong_entity = raw.get("wrong_entity") is True or raw.get("entity_match_status") == "wrong"
    blocked = raw.get("blocked") is True or raw.get("promotion_status") == "excluded" or wrong_entity
    analysis_verified = raw.get("analysis_verified") is True
    generation_eligible = is_generation_eligible_source(raw) or (
        analysis_verified
        and raw.get("promotion_status") == "generation_eligible"
        and raw.get("claim_support_status") == "supported"
    )
    buying_eligible = raw.get("buying_evidence_eligible") is True or raw.get("allowed_as_fact") is True
    scanner_seed = bool(
        generation_eligible
        and analysis_verified
        and not freshness["is_stale"]
        and not wrong_entity
        and not blocked
        and not context_only
    )

    return {
        "id": f"LEDGER-{idx:03d}",
        "company_name": company_name,
        "title": title,
        "url": url,
        "canonical_source_host": raw.get("canonical_source_host") or _host(url),
        "source_type": source_type or "source",
        "source_category": raw.get("source_category") or raw.get("category"),
        "origins": [origin],
        "origin_records": [{"origin": origin, "id": raw.get("id") or raw.get("source_id") or raw.get("record_id")}],
        "provider": raw.get("provider"),
        "provider_record_type": raw.get("provider_record_type"),
        "source_quote": quote,
        "snippet": quote,
        "content_hash": hash_value,
        "url_verified": raw.get("url_verified") is True,
        "content_verified": raw.get("content_verified") is True,
        "entity_verified": raw.get("entity_verified") is True or raw.get("entity_match_status") in {"exact", "scoped"},
        "entity_match_status": raw.get("entity_match_status") or "unknown",
        "claim_support_status": raw.get("claim_support_status"),
        "analysis_verified": analysis_verified,
        "generation_eligible": bool(generation_eligible),
        "promotion_status": raw.get("promotion_status"),
        "verification_reason": raw.get("verification_reason") or raw.get("content_verification_reason"),
        "source_credibility": raw.get("source_credibility") or raw.get("credibility"),
        "wrong_entity": wrong_entity,
        "context_only": context_only,
        "display_only": raw.get("display_only") is True or raw.get("promotion_status") == "display_only",
        "blocked": blocked,
        "buying_evidence_eligible": bool(buying_eligible and not blocked and not wrong_entity and not freshness["is_stale"]),
        "allowed_as_fact": raw.get("allowed_as_fact") is True and not blocked and not wrong_entity and not freshness["is_stale"],
        "allowed_as_hypothesis": raw.get("allowed_as_hypothesis") is True and not blocked,
        "allowed_as_discovery_question": raw.get("allowed_as_discovery_question") is True and not blocked,
        "internal_only": raw.get("internal_only") is True,
        "sales_use": raw.get("sales_use"),
        "demand_signal_ids": raw.get("demand_signal_ids") or [],
        "linked_why_what_ids": raw.get("linked_why_what_ids") or [],
        "linked_service_ids": raw.get("linked_service_ids") or [],
        "stakeholder_role_match_reason": raw.get("stakeholder_role_match_reason"),
        "stakeholder": raw.get("stakeholder") if isinstance(raw.get("stakeholder"), dict) else None,
        "usable_as_scanner_seed": scanner_seed,
        "usable_for_buying_insight": bool((buying_eligible or raw.get("allowed_as_hypothesis") or raw.get("allowed_as_discovery_question")) and not blocked and not wrong_entity),
        **freshness,
    }


def _merge_records(existing: dict, incoming: dict) -> dict:
    merged = dict(existing)
    origins = list(dict.fromkeys(_list(existing.get("origins")) + _list(incoming.get("origins"))))
    merged["origins"] = origins
    merged["origin_records"] = _list(existing.get("origin_records")) + _list(incoming.get("origin_records"))
    for key in (
        "url_verified",
        "content_verified",
        "entity_verified",
        "analysis_verified",
        "generation_eligible",
        "buying_evidence_eligible",
        "allowed_as_fact",
        "allowed_as_hypothesis",
        "allowed_as_discovery_question",
        "usable_as_scanner_seed",
        "usable_for_buying_insight",
    ):
        merged[key] = bool(existing.get(key) or incoming.get(key))
    for key in ("wrong_entity", "blocked"):
        merged[key] = bool(existing.get(key) or incoming.get(key))
    for key in ("source_quote", "snippet", "verification_reason", "source_credibility", "claim_support_status", "promotion_status"):
        if len(str(incoming.get(key) or "")) > len(str(merged.get(key) or "")):
            merged[key] = incoming.get(key)
    if incoming.get("age_days") is not None and (merged.get("age_days") is None or incoming.get("age_days") < merged.get("age_days")):
        merged["last_checked"] = incoming.get("last_checked")
        merged["source_freshness"] = incoming.get("source_freshness")
        merged["is_stale"] = incoming.get("is_stale")
        merged["age_days"] = incoming.get("age_days")
    merged["usable_as_scanner_seed"] = bool(
        merged.get("generation_eligible")
        and merged.get("analysis_verified")
        and not merged.get("is_stale")
        and not merged.get("wrong_entity")
        and not merged.get("blocked")
        and not merged.get("context_only")
    )
    merged["buying_evidence_eligible"] = bool(
        merged.get("buying_evidence_eligible")
        and not merged.get("is_stale")
        and not merged.get("wrong_entity")
        and not merged.get("blocked")
    )
    return merged


def _financial_records(company_data: dict) -> list[dict]:
    records = []
    for key in ("financial_findings", "financial_health_findings", "financial_evidence"):
        for item in _list(company_data.get(key)):
            if isinstance(item, dict) and (item.get("source_url") or item.get("url")):
                records.append({**item, "source_type": item.get("source_type") or "financial"})
    return records


def _buying_report_records(report: dict) -> list[dict]:
    records = []
    evidence = _dict(report.get("evidence_confidence"))
    for key in ("strongest_evidence", "weak_or_missing_evidence", "claims_as_questions_only"):
        for item in _list(evidence.get(key)):
            if isinstance(item, dict) and (item.get("url") or item.get("source_url")):
                records.append(item)
    return records


def _collect_candidate_sources(company_data: dict, company_evidence: dict | None, buying_potential: dict | None) -> list[tuple[str, dict]]:
    candidates: list[tuple[str, dict]] = []
    for link in _list(company_data.get("evidence_links")):
        candidates.append(("scanner", link))
    for link in _financial_records(company_data):
        candidates.append(("financial", link))
    for link in _list(company_data.get("foresight_links")):
        candidates.append(("foresight", link))
    if isinstance(company_evidence, dict):
        for record in _list(company_evidence.get("records")):
            candidates.append(("company_evidence_db", record))
    if isinstance(buying_potential, dict):
        for record in _buying_report_records(buying_potential):
            candidates.append(("buying_insight", record))
    return candidates


def build_company_source_ledger(
    company_data: dict,
    company_evidence: dict | None = None,
    buying_potential: dict | None = None,
    *,
    stage_callback=None,
) -> tuple[dict, str]:
    if not isinstance(company_data, dict) or not (company_data.get("companyName") or company_data.get("company_name")):
        raise CompanySourceLedgerError("Company data missing or invalid.", 404)

    company_name = company_data.get("companyName") or company_data.get("company_name")
    generated_at = datetime.utcnow().isoformat()
    if stage_callback:
        stage_callback("combine_sources", 52, "Combining Scanner, evidence DB, financial, foresight, and buying-insight sources")

    by_key: dict[str, dict] = {}
    raw_count = 0
    for raw_count, (origin, raw) in enumerate(_collect_candidate_sources(company_data, company_evidence, buying_potential), start=1):
        source = _base_source(raw, origin, company_name, generated_at, raw_count)
        if not source:
            continue
        key = source.get("url") or f"hash:{source.get('content_hash')}"
        if key in by_key:
            by_key[key] = _merge_records(by_key[key], source)
        else:
            by_key[key] = source

    records = list(by_key.values())
    records.sort(key=lambda r: (
        not r.get("usable_as_scanner_seed"),
        not r.get("buying_evidence_eligible"),
        r.get("is_stale") is True,
        r.get("title") or "",
    ))
    for idx, record in enumerate(records, start=1):
        record["id"] = f"LEDGER-{idx:03d}"

    origins = Counter(origin for record in records for origin in _list(record.get("origins")))
    stats = {
        "candidate_sources": raw_count,
        "records": len(records),
        "deduplicated": max(0, raw_count - len(records)),
        "scanner_seed_eligible": sum(1 for r in records if r.get("usable_as_scanner_seed")),
        "buying_eligible": sum(1 for r in records if r.get("buying_evidence_eligible")),
        "generation_eligible": sum(1 for r in records if r.get("generation_eligible")),
        "stale": sum(1 for r in records if r.get("is_stale")),
        "wrong_entity": sum(1 for r in records if r.get("wrong_entity")),
        "display_only_or_blocked": sum(1 for r in records if r.get("display_only") or r.get("blocked")),
        "context_only": sum(1 for r in records if r.get("context_only")),
        "origins": dict(origins),
    }
    warnings = []
    if not records:
        warnings.append("No reusable source records were found for this company.")
    if stats["scanner_seed_eligible"] == 0:
        warnings.append("No fresh generation-eligible records are available as Scanner source seeds.")
    if stats["buying_eligible"] == 0:
        warnings.append("No buying-eligible ledger records are available; Buying Insight must rely on Scanner payload/service KB.")
    if stats["stale"] > max(2, len(records) // 3):
        warnings.append("A large share of ledger records are stale; refresh Company Evidence DB before relying on them.")

    scanner_seed_links = ledger_scanner_seed_links({"records": records}, limit=12)
    ledger = {
        "report_type": "company_source_ledger",
        "status": "ready",
        "company_name": company_name,
        "company_url": company_data.get("companyUrl") or company_data.get("url"),
        "generated_at": generated_at,
        "records": records,
        "scanner_seed_links": scanner_seed_links,
        "stats": stats,
        "warnings": warnings,
        "source_inputs": {
            "company_evidence_db_available": isinstance(company_evidence, dict),
            "company_evidence_db_generated_at": company_evidence.get("generated_at") if isinstance(company_evidence, dict) else None,
            "buying_insight_available": isinstance(buying_potential, dict),
            "buying_insight_generated_at": buying_potential.get("generated_at") if isinstance(buying_potential, dict) else None,
        },
        "trust_note": "This ledger is source memory, not truth. Scanner and Buying Insight must re-apply their own evidence gates before using a record.",
    }
    return ledger, render_company_source_ledger_html(ledger)


def ledger_scanner_seed_links(ledger: dict | None, limit: int = 10) -> list[dict]:
    if not isinstance(ledger, dict):
        return []
    records = _list(ledger.get("records"))
    seeds = []
    for record in records:
        if not isinstance(record, dict) or not record.get("usable_as_scanner_seed"):
            continue
        seeds.append({
            "title": record.get("title"),
            "url": record.get("url"),
            "snippet": record.get("snippet"),
            "source_quote": record.get("source_quote"),
            "source_type": record.get("source_type"),
            "source_credibility": record.get("source_credibility"),
            "url_verified": record.get("url_verified") is True,
            "content_verified": record.get("content_verified") is True,
            "entity_verified": record.get("entity_verified") is True,
            "analysis_verified": record.get("analysis_verified") is True,
            "claim_support_status": record.get("claim_support_status"),
            "promotion_status": record.get("promotion_status"),
            "generation_eligible": record.get("generation_eligible") is True,
            "origin": "company_source_ledger",
            "ledger_id": record.get("id"),
        })
        if len(seeds) >= limit:
            break
    return seeds


def render_company_source_ledger_html(ledger: dict) -> str:
    company = html.escape(str(ledger.get("company_name") or "Company"))
    stats = _dict(ledger.get("stats"))
    rows = []
    for record in _list(ledger.get("records"))[:120]:
        rows.append(
            "<tr>"
            f"<td>{html.escape(str(record.get('id') or ''))}</td>"
            f"<td><a href=\"{html.escape(str(record.get('url') or '#'))}\">{html.escape(str(record.get('title') or record.get('url') or 'Source'))}</a></td>"
            f"<td>{html.escape(', '.join(_list(record.get('origins'))))}</td>"
            f"<td>{html.escape(str(record.get('source_type') or ''))}</td>"
            f"<td>{'yes' if record.get('usable_as_scanner_seed') else 'no'}</td>"
            f"<td>{'yes' if record.get('buying_evidence_eligible') else 'no'}</td>"
            f"<td>{html.escape(str(record.get('source_freshness') or 'unknown'))}</td>"
            f"<td>{html.escape(_clip(record.get('source_quote') or record.get('snippet'), 220))}</td>"
            "</tr>"
        )
    warning_html = "".join(f"<li>{html.escape(str(w))}</li>" for w in _list(ledger.get("warnings")))
    return f"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1" />
  <title>{company} - Company Source Ledger</title>
  <style>
    body {{ font-family: Inter, -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif; margin: 0; padding: 32px; background: #f6f1e8; color: #172033; }}
    h1 {{ font-family: Georgia, serif; font-size: 42px; margin-bottom: 8px; }}
    .meta, .warn {{ border: 1px solid #d8cdbd; background: #fffaf2; padding: 16px; margin: 18px 0; }}
    .grid {{ display: grid; grid-template-columns: repeat(auto-fit, minmax(180px, 1fr)); gap: 12px; }}
    .metric {{ background: white; border: 1px solid #d8cdbd; padding: 14px; }}
    .metric strong {{ display: block; font-size: 28px; color: #9f3f24; }}
    table {{ width: 100%; border-collapse: collapse; background: white; border: 1px solid #d8cdbd; }}
    th, td {{ border-bottom: 1px solid #e5dacb; padding: 10px; text-align: left; vertical-align: top; font-size: 13px; }}
    th {{ font-size: 11px; text-transform: uppercase; letter-spacing: .08em; color: #596478; }}
    a {{ color: #184f6b; }}
  </style>
</head>
<body>
  <h1>{company} Source Ledger</h1>
  <p class="meta">{html.escape(str(ledger.get('trust_note') or ''))}</p>
  <div class="grid">
    <div class="metric"><span>Records</span><strong>{stats.get('records', 0)}</strong></div>
    <div class="metric"><span>Scanner Seeds</span><strong>{stats.get('scanner_seed_eligible', 0)}</strong></div>
    <div class="metric"><span>Buying Eligible</span><strong>{stats.get('buying_eligible', 0)}</strong></div>
    <div class="metric"><span>Deduplicated</span><strong>{stats.get('deduplicated', 0)}</strong></div>
  </div>
  {f'<div class="warn"><strong>Warnings</strong><ul>{warning_html}</ul></div>' if warning_html else ''}
  <table>
    <thead><tr><th>ID</th><th>Source</th><th>Origins</th><th>Type</th><th>Scanner seed</th><th>Buying</th><th>Freshness</th><th>Quote / snippet</th></tr></thead>
    <tbody>{''.join(rows)}</tbody>
  </table>
</body>
</html>"""
