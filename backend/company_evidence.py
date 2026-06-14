"""
Company Evidence Database for Buying Insight.

This is a separate, BLOB2-backed acquisition layer. It gathers candidate
official-domain evidence for one company and applies conservative eligibility
rules before Buying Insight can use it.
"""

from __future__ import annotations

import hashlib
import html
import os
import re
from datetime import datetime
from typing import Any
from urllib.parse import urljoin, urlparse

import requests
from bs4 import BeautifulSoup


COMMON_TARGET_PATHS = (
    "/", "/about", "/about-us", "/company", "/news", "/newsroom", "/press",
    "/media", "/investors", "/investor", "/annual-report", "/reports",
    "/sustainability", "/responsibility", "/careers", "/jobs", "/references",
    "/case-studies", "/customers", "/services", "/solutions",
)
TARGET_KEYWORDS = (
    "about", "company", "news", "press", "media", "investor", "annual",
    "report", "sustainability", "responsibility", "career", "job",
    "reference", "case", "customer", "service", "solution",
)
FACT_SAFE_TYPES = {"homepage", "about", "investor", "sustainability"}
ELIGIBLE_TYPES = FACT_SAFE_TYPES | {"newsroom", "careers", "references", "services", "official_page"}


class CompanyEvidenceError(RuntimeError):
    def __init__(self, message: str, status_code: int = 400):
        super().__init__(message)
        self.status_code = status_code


def _clean_env(value: str | None) -> str:
    if not value:
        return ""
    if "=" in value:
        value = value.split("=", 1)[1]
    return value.strip().strip('"').strip("'")


def _clip(value: Any, limit: int = 900) -> str:
    return re.sub(r"\s+", " ", str(value or "")).strip()[:limit]


def _list(value: Any) -> list:
    return value if isinstance(value, list) else []


def _normalize_url(url: str | None, base: str | None = None) -> str:
    if not url:
        return ""
    value = str(url).strip()
    if base and value.startswith("/"):
        value = urljoin(base, value)
    if value and "://" not in value:
        value = "https://" + value
    parsed = urlparse(value)
    if not parsed.netloc:
        return ""
    return parsed._replace(fragment="", query="").geturl().rstrip("/")


def _host(url: str | None) -> str:
    value = _normalize_url(url)
    if not value:
        return ""
    host = urlparse(value).netloc.lower()
    return host[4:] if host.startswith("www.") else host


def _same_or_subdomain(host: str, official_host: str) -> bool:
    return bool(host and official_host and (host == official_host or host.endswith("." + official_host)))


def _company_tokens(company_name: str) -> list[str]:
    text = re.sub(r"\b(oyj|oy|abp|ab|plc|ltd|inc|corp|corporation|group|holding|holdings)\b", " ", (company_name or "").lower())
    return [t for t in re.findall(r"[a-zåäö0-9]{3,}", text) if t not in {"the", "and", "for"}][:6]


def _source_type(url: str, title: str = "") -> str:
    value = f"{url} {title}".lower()
    if url.rstrip("/").count("/") <= 2:
        return "homepage"
    if any(k in value for k in ("about", "company", "who-we-are")):
        return "about"
    if any(k in value for k in ("news", "press", "media", "release")):
        return "newsroom"
    if any(k in value for k in ("investor", "annual", "financial", "report")):
        return "investor"
    if any(k in value for k in ("sustainability", "responsibility", "esg", "csrd")):
        return "sustainability"
    if any(k in value for k in ("career", "jobs", "open-position", "rekry", "ura")):
        return "careers"
    if any(k in value for k in ("reference", "case", "customer", "asiakastarina")):
        return "references"
    if any(k in value for k in ("service", "solution", "palvelut", "ratkaisut")):
        return "services"
    return "official_page"


def _html_to_text(raw_html: str) -> tuple[str, str]:
    soup = BeautifulSoup(raw_html or "", "html.parser")
    for tag in soup(["script", "style", "noscript", "svg"]):
        tag.decompose()
    title = _clip(soup.title.string if soup.title else "", 180)
    return title, _clip(soup.get_text(" ", strip=True), 14000)


def _firecrawl_headers() -> dict:
    key = _clean_env(os.environ.get("FIRECRAWL_API_KEY"))
    return {"Authorization": f"Bearer {key}", "Content-Type": "application/json"} if key else {}


def _firecrawl_map(base_url: str, limit: int) -> list[str]:
    if not _firecrawl_headers():
        return []
    try:
        resp = requests.post(
            "https://api.firecrawl.dev/v1/map",
            headers=_firecrawl_headers(),
            json={"url": base_url, "limit": limit, "includeSubdomains": False},
            timeout=25,
        )
        if resp.status_code not in (200, 201):
            print(f"   ⚠️ [Company Evidence] Firecrawl map failed ({resp.status_code})", flush=True)
            return []
        data = resp.json()
        links = data.get("links") or data.get("data") or []
        if isinstance(links, dict):
            links = links.get("links") or links.get("urls") or []
        return [str(x.get("url") if isinstance(x, dict) else x) for x in links if x]
    except Exception as exc:
        print(f"   ⚠️ [Company Evidence] Firecrawl map exception: {exc}", flush=True)
        return []


def _firecrawl_scrape(url: str) -> dict | None:
    if not _firecrawl_headers():
        return None
    try:
        resp = requests.post(
            "https://api.firecrawl.dev/v1/scrape",
            headers=_firecrawl_headers(),
            json={"url": url, "formats": ["markdown", "html"]},
            timeout=35,
        )
        if resp.status_code not in (200, 201):
            return None
        data = resp.json().get("data") or resp.json()
        text = data.get("markdown") or data.get("content") or ""
        title = data.get("metadata", {}).get("title") or data.get("title") or ""
        raw_html = data.get("html") or ""
        if not text and raw_html:
            title, text = _html_to_text(raw_html)
        if text:
            return {"provider": "firecrawl", "title": title, "text": text, "status_code": 200}
    except Exception as exc:
        print(f"   ⚠️ [Company Evidence] Firecrawl scrape exception: {exc}", flush=True)
    return None


def _jina_fetch(url: str) -> dict | None:
    headers = {}
    key = _clean_env(os.environ.get("JINA_API_KEY"))
    if key:
        headers["Authorization"] = f"Bearer {key}"
    stripped = re.sub(r"^https?://", "", url)
    for reader_url in (f"https://r.jina.ai/http://{url}", f"https://r.jina.ai/http://{stripped}"):
        try:
            resp = requests.get(reader_url, headers=headers, timeout=25)
            if resp.status_code == 200 and len(resp.text.strip()) > 250:
                title = ""
                for line in resp.text.splitlines()[:8]:
                    if line.lower().startswith("title:"):
                        title = _clip(line.split(":", 1)[1], 180)
                        break
                return {"provider": "jina", "title": title, "text": resp.text, "status_code": 200}
        except Exception:
            continue
    return None


def _direct_fetch(url: str) -> dict | None:
    try:
        resp = requests.get(url, headers={"User-Agent": "The-Scanner Company Evidence Builder/1.0"}, timeout=15)
        if resp.status_code >= 400:
            return {"provider": "direct", "status_code": resp.status_code, "title": "", "text": ""}
        title, text = _html_to_text(resp.text)
        return {"provider": "direct", "status_code": resp.status_code, "title": title, "text": text}
    except Exception:
        return None


def _fetch_source(url: str) -> dict:
    fetched = _firecrawl_scrape(url) or _jina_fetch(url) or _direct_fetch(url)
    if not fetched:
        return {"provider": "none", "url_verified": False, "content_verified": False, "title": "", "text": "", "status_code": 0}
    text = _clip(fetched.get("text"), 14000)
    status_code = int(fetched.get("status_code") or 200)
    return {
        **fetched,
        "text": text,
        "url_verified": 200 <= status_code < 400,
        "content_verified": len(text) >= 250,
    }


def _discover_urls(company_url: str, max_urls: int) -> list[str]:
    base = _normalize_url(company_url)
    official_host = _host(base)
    discovered = [base]
    discovered.extend(_firecrawl_map(base, max_urls))
    discovered.extend(_normalize_url(urljoin(base.rstrip("/") + "/", path.lstrip("/"))) for path in COMMON_TARGET_PATHS)
    filtered = []
    seen = set()
    for url in discovered:
        clean = _normalize_url(url, base)
        if not clean or clean in seen:
            continue
        seen.add(clean)
        if not _same_or_subdomain(_host(clean), official_host):
            continue
        path = urlparse(clean).path.lower()
        if clean == base or any(keyword in path for keyword in TARGET_KEYWORDS):
            filtered.append(clean)
        if len(filtered) >= max_urls:
            break
    return filtered


def _quote_for_entity(text: str, tokens: list[str]) -> str:
    sentences = re.split(r"(?<=[.!?])\s+", text or "")
    for sentence in sentences[:80]:
        lowered = sentence.lower()
        if any(token in lowered for token in tokens):
            return _clip(sentence, 420)
    return _clip(" ".join(sentences[:2]), 420)


def _verify_record(company_name: str, company_url: str, url: str, fetched: dict) -> dict:
    official_host = _host(company_url)
    source_host = _host(url)
    tokens = _company_tokens(company_name)
    text = fetched.get("text") or ""
    title = fetched.get("title") or ""
    source_type = _source_type(url, title)
    same_domain = _same_or_subdomain(source_host, official_host)
    domain_token = official_host.split(".")[0] if official_host else ""
    text_l = text.lower()
    contains_entity = any(token in text_l for token in tokens) or (domain_token and domain_token in text_l)
    entity_verified = bool(same_domain and fetched.get("content_verified") and (contains_entity or source_type == "homepage"))
    eligible = bool(
        fetched.get("url_verified")
        and fetched.get("content_verified")
        and entity_verified
        and source_type in ELIGIBLE_TYPES
    )
    if not same_domain:
        match_status = "wrong"
        reason = "wrong_entity_domain_mismatch"
    elif not fetched.get("url_verified"):
        match_status = "unknown"
        reason = "url_not_verified"
    elif not fetched.get("content_verified"):
        match_status = "unknown"
        reason = "content_unreadable"
    elif not entity_verified:
        match_status = "weak"
        reason = "target_entity_not_confirmed_in_content"
    else:
        match_status = "exact"
        reason = "official_domain_entity_match"
    return {
        "source_type": source_type,
        "canonical_source_host": source_host,
        "source_quote": _quote_for_entity(text, tokens),
        "url_verified": bool(fetched.get("url_verified")),
        "content_verified": bool(fetched.get("content_verified")),
        "entity_verified": entity_verified,
        "entity_match_status": match_status,
        "claim_support_status": "supported" if eligible else "unchecked",
        "analysis_verified": eligible,
        "generation_eligible": eligible,
        "buying_evidence_eligible": eligible,
        "promotion_status": "generation_eligible" if eligible else "display_only",
        "verification_reason": reason,
        "allowed_as_fact": eligible and source_type in FACT_SAFE_TYPES,
        "allowed_as_hypothesis": eligible,
        "allowed_as_discovery_question": eligible or bool(fetched.get("content_verified")),
        "internal_only": not eligible,
        "blocked": match_status == "wrong",
        "sales_use": "allowed_as_fact" if eligible and source_type in FACT_SAFE_TYPES else ("allowed_as_discovery_question" if eligible else "internal_only"),
        "wrong_entity": match_status == "wrong",
    }


def _candidate_record(idx: int, company_name: str, company_url: str, url: str) -> dict:
    fetched = _fetch_source(url)
    verified = _verify_record(company_name, company_url, url, fetched)
    text = fetched.get("text") or ""
    content_hash = hashlib.sha256(text.encode("utf-8", errors="ignore")).hexdigest() if text else ""
    provider = fetched.get("provider") or "none"
    extraction_confidence = 80 if provider == "firecrawl" else (70 if provider == "jina" else (55 if fetched.get("content_verified") else 0))
    return {
        "id": f"CED-{idx:02d}",
        "company_name": company_name,
        "company_url": company_url,
        "url": url,
        "provider": provider,
        "title": fetched.get("title") or url,
        "fetched_at": datetime.utcnow().isoformat(),
        "content_hash": content_hash,
        "extraction_confidence": extraction_confidence,
        "source_freshness": "current_fetch",
        "text_excerpt": _clip(text, 1800),
        **verified,
    }


def _stats(records: list[dict]) -> dict:
    return {
        "candidate_sources": len(records),
        "url_verified": sum(1 for r in records if r.get("url_verified")),
        "content_verified": sum(1 for r in records if r.get("content_verified")),
        "entity_verified": sum(1 for r in records if r.get("entity_verified")),
        "buying_evidence_eligible": sum(1 for r in records if r.get("buying_evidence_eligible")),
        "display_only_or_blocked": sum(1 for r in records if not r.get("buying_evidence_eligible")),
        "wrong_entity": sum(1 for r in records if r.get("wrong_entity")),
        "providers": sorted({r.get("provider") for r in records if r.get("provider")}),
    }


def render_company_evidence_html(database: dict) -> str:
    company = database.get("company_name") or "Company"
    rows = []
    for rec in _list(database.get("records")):
        rows.append(
            "<tr>"
            f"<td>{html.escape(rec.get('id') or '')}</td>"
            f"<td>{html.escape(rec.get('source_type') or '')}</td>"
            f"<td><a href=\"{html.escape(rec.get('url') or '')}\">{html.escape(_clip(rec.get('title') or rec.get('url'), 90))}</a></td>"
            f"<td>{html.escape(rec.get('provider') or '')}</td>"
            f"<td>{'yes' if rec.get('buying_evidence_eligible') else 'no'}</td>"
            f"<td>{html.escape(rec.get('verification_reason') or '')}</td>"
            "</tr>"
        )
    stats = database.get("stats") or {}
    return f"""<!doctype html>
<html><head><meta charset="utf-8"><title>{html.escape(company)} — Company Evidence DB</title>
<style>
body{{background:#0f172a;color:#e5e7eb;font-family:Inter,system-ui,sans-serif;margin:0;padding:32px}}
a{{color:#67e8f9}} .meta{{display:flex;gap:12px;flex-wrap:wrap;margin:16px 0 28px}}
.pill{{border:1px solid #334155;border-radius:999px;padding:8px 12px;color:#cbd5e1;background:#111827}}
table{{width:100%;border-collapse:collapse;background:#111827;border:1px solid #334155}}
th,td{{text-align:left;border-bottom:1px solid #334155;padding:10px;vertical-align:top}}
th{{color:#93c5fd;text-transform:uppercase;font-size:12px;letter-spacing:.08em}}
</style></head><body>
<h1>{html.escape(company)} — Company Evidence Database</h1>
<p>Admin audit artifact for Buying Insight. Candidate sources are collected separately from normal Scanner data.</p>
<div class="meta">
<span class="pill">Candidates: {stats.get('candidate_sources', 0)}</span>
<span class="pill">Eligible: {stats.get('buying_evidence_eligible', 0)}</span>
<span class="pill">Entity verified: {stats.get('entity_verified', 0)}</span>
<span class="pill">Display-only / blocked: {stats.get('display_only_or_blocked', 0)}</span>
</div>
<table><thead><tr><th>ID</th><th>Type</th><th>Source</th><th>Provider</th><th>Eligible</th><th>Reason</th></tr></thead>
<tbody>{''.join(rows)}</tbody></table>
</body></html>"""


def build_company_evidence_database(company_data: dict, *, stage_callback=None) -> tuple[dict, str]:
    company_name = company_data.get("companyName") or company_data.get("company_name")
    company_url = company_data.get("companyUrl") or company_data.get("url")
    if not company_name:
        raise CompanyEvidenceError("Company name missing from stored data.", 404)
    if not company_url:
        raise CompanyEvidenceError("Company URL missing from stored data; cannot build official-domain evidence.", 409)
    max_urls = max(4, min(30, int(os.environ.get("COMPANY_EVIDENCE_MAX_URLS", "18"))))
    if stage_callback:
        stage_callback("discover_sources", 25, "Discovering official-domain source candidates")
    urls = _discover_urls(company_url, max_urls)
    if stage_callback:
        stage_callback("fetch_sources", 40, f"Fetching and extracting {len(urls)} candidate source(s)")
    records = []
    for idx, url in enumerate(urls, start=1):
        records.append(_candidate_record(idx, company_name, company_url, url))
    if stage_callback:
        stage_callback("verify_sources", 72, "Applying entity and sales-use gates")
    database = {
        "version": "company-evidence-v1",
        "company_name": company_name,
        "company_url": company_url,
        "company_slug": "",
        "generated_at": datetime.utcnow().isoformat(),
        "providers": {
            "firecrawl_enabled": bool(_firecrawl_headers()),
            "jina_enabled": True,
            "direct_fetch_enabled": True,
        },
        "acquisition_policy": {
            "mode": "official_domain_only",
            "max_urls": max_urls,
            "broad_web_search": False,
            "social_scraping": False,
            "contact_scraping": False,
        },
        "records": records,
        "stats": _stats(records),
        "warnings": [],
    }
    if not _firecrawl_headers():
        database["warnings"].append("FIRECRAWL_API_KEY not configured; used Jina/direct official-domain fallback only.")
    if not any(r.get("buying_evidence_eligible") for r in records):
        database["warnings"].append("No buying-evidence-eligible records found; Buying Insight should remain cautious.")
    return database, render_company_evidence_html(database)
