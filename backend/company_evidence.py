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


SOURCE_TARGET_REGISTRY = (
    {
        "category": "identity_entity",
        "source_type": "homepage",
        "keywords": ("",),
        "localized_paths": (),
        "target_fields": ("company name", "domain", "country", "legal identity"),
        "default_sales_use": "allowed_as_fact",
        "fact_safe": True,
        "context_only": False,
    },
    {
        "category": "identity_entity",
        "source_type": "about",
        "keywords": ("about", "company", "who-we-are", "contact", "yritys", "meista", "meistä", "yhteystiedot", "om-oss", "kontakt"),
        "localized_paths": ("about", "about-us", "company", "contact", "yritys", "meista", "meistä", "yhteystiedot", "om-oss", "kontakt"),
        "target_fields": ("legal name", "company description", "locations", "group relationship"),
        "default_sales_use": "allowed_as_fact",
        "fact_safe": True,
        "context_only": False,
    },
    {
        "category": "strategy_trigger_events",
        "source_type": "newsroom",
        "keywords": ("news", "newsroom", "press", "media", "release", "uutiset", "tiedotteet", "ajankohtaista", "nyheter", "pressmeddelanden"),
        "localized_paths": ("news", "newsroom", "press", "media", "uutiset", "tiedotteet", "ajankohtaista", "nyheter", "press"),
        "target_fields": ("recent events", "partnerships", "leadership changes", "expansion", "new business units"),
        "default_sales_use": "allowed_as_hypothesis",
        "fact_safe": False,
        "context_only": False,
    },
    {
        "category": "strategy_trigger_events",
        "source_type": "investor",
        "keywords": ("investor", "annual", "financial", "report", "governance", "strategy", "sijoittajat", "vuosikertomus", "tilinpäätös", "hallinnointi"),
        "localized_paths": ("investors", "investor", "annual-report", "reports", "strategy", "sijoittajat", "vuosikertomus", "raportit", "strategia"),
        "target_fields": ("strategy", "financial updates", "ownership", "governance", "investment priorities"),
        "default_sales_use": "allowed_as_fact",
        "fact_safe": True,
        "context_only": False,
    },
    {
        "category": "market_regulatory_pressure",
        "source_type": "sustainability",
        "keywords": ("sustainability", "responsibility", "esg", "csrd", "esrs", "climate", "vastuullisuus", "kestävyys", "kestavyys", "hållbarhet"),
        "localized_paths": ("sustainability", "responsibility", "esg", "vastuullisuus", "kestavyys", "kestävyys", "hållbarhet"),
        "target_fields": ("CSRD", "ESRS", "climate", "workforce", "regulatory exposure"),
        "default_sales_use": "allowed_as_hypothesis",
        "fact_safe": True,
        "context_only": True,
    },
    {
        "category": "hiring_capability",
        "source_type": "careers",
        "keywords": ("career", "jobs", "open-position", "rekry", "ura", "avoimet", "tyopaikat", "työpaikat", "join-us", "work-with-us"),
        "localized_paths": ("careers", "jobs", "open-positions", "join-us", "ura", "rekry", "avoimet-tyopaikat", "avoimet-työpaikat", "tyopaikat", "työpaikat"),
        "target_fields": ("hiring themes", "AI roles", "cloud roles", "ERP roles", "cybersecurity roles", "platform engineering"),
        "default_sales_use": "allowed_as_discovery_question",
        "fact_safe": False,
        "context_only": False,
    },
    {
        "category": "technology_service_fit",
        "source_type": "references",
        "keywords": ("reference", "case", "customer", "client", "asiakastarina", "referenssi", "customers", "success-story"),
        "localized_paths": ("references", "case-studies", "customers", "customer-stories", "referenssit", "asiakastarinat"),
        "target_fields": ("customer cases", "delivery evidence", "technology partners", "proof points"),
        "default_sales_use": "allowed_as_hypothesis",
        "fact_safe": False,
        "context_only": False,
    },
    {
        "category": "technology_service_fit",
        "source_type": "services",
        "keywords": ("service", "solution", "product", "platform", "cloud", "ai", "data", "cyber", "security", "erp", "sap", "azure", "aws", "palvelut", "ratkaisut", "tuotteet"),
        "localized_paths": ("services", "solutions", "products", "platform", "palvelut", "ratkaisut", "tuotteet"),
        "target_fields": ("AI", "data", "cloud", "ERP", "cybersecurity", "digital workplace", "automation"),
        "default_sales_use": "allowed_as_hypothesis",
        "fact_safe": False,
        "context_only": False,
    },
    {
        "category": "stakeholder_evidence",
        "source_type": "leadership",
        "keywords": ("management", "leadership", "board", "executive", "team", "johto", "hallitus", "johtoryhma", "johtoryhmä", "ledning", "styrelse"),
        "localized_paths": ("management", "leadership", "board", "team", "johto", "hallitus", "johtoryhma", "johtoryhmä", "ledning", "styrelse"),
        "target_fields": ("leadership roles", "public statements", "buying committee relevance"),
        "default_sales_use": "internal_only",
        "fact_safe": False,
        "context_only": False,
    },
    {
        "category": "procurement_public_activity",
        "source_type": "procurement",
        "keywords": ("procurement", "tender", "contract", "hankinta", "kilpailutus", "tarjouspyynto", "tarjouspyyntö"),
        "localized_paths": ("procurement", "tenders", "hankinnat", "kilpailutukset"),
        "target_fields": ("public tenders", "contract activity", "service categories", "procurement contacts"),
        "default_sales_use": "allowed_as_fact",
        "fact_safe": True,
        "context_only": False,
    },
)

COMMON_TARGET_PATHS = tuple(
    "/" + path
    for target in SOURCE_TARGET_REGISTRY
    for path in target.get("localized_paths", ())
    if path
)
TARGET_KEYWORDS = tuple(
    keyword
    for target in SOURCE_TARGET_REGISTRY
    for keyword in target.get("keywords", ())
    if keyword
)
FACT_SAFE_TYPES = {target["source_type"] for target in SOURCE_TARGET_REGISTRY if target.get("fact_safe")}
ELIGIBLE_TYPES = {target["source_type"] for target in SOURCE_TARGET_REGISTRY} | {"official_page"}
TECH_SIGNAL_KEYWORDS = (
    "ai", "tekoäly", "genai", "copilot", "agent", "data", "analytics", "cloud",
    "azure", "aws", "gcp", "google cloud", "sap", "erp", "dynamics",
    "salesforce", "oracle", "workday", "cyber", "security", "nis2", "dora",
    "iam", "soc", "m365", "devops", "platform engineering", "automation",
    "rpa", "process mining",
)
TRIGGER_EVENT_KEYWORDS = (
    "acquisition", "partnership", "agreement", "investment", "funding",
    "expansion", "launch", "appointment", "appointed", "strategy", "transform",
    "yrityskauppa", "kumppanuus", "sopimus", "investointi", "laajent", "nimitys",
)


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


def _is_homepage_url(url: str) -> bool:
    path_parts = [p for p in urlparse(_normalize_url(url)).path.split("/") if p]
    return len(path_parts) == 0 or (len(path_parts) == 1 and path_parts[0].lower() in {"fi", "en", "sv", "se", "de", "fr"})


def _source_profile(url: str, title: str = "") -> dict:
    value = f"{url} {title}".lower()
    if _is_homepage_url(url):
        return dict(SOURCE_TARGET_REGISTRY[0])
    for target in SOURCE_TARGET_REGISTRY[1:]:
        if any(k and k in value for k in target.get("keywords", ())):
            return dict(target)
    return {
        "category": "other_official",
        "source_type": "official_page",
        "target_fields": ("official-domain context",),
        "default_sales_use": "allowed_as_discovery_question",
        "fact_safe": False,
        "context_only": False,
    }


def _source_type(url: str, title: str = "") -> str:
    return _source_profile(url, title).get("source_type", "official_page")


def _is_not_found_content(title: str, text: str, status_code: int = 200) -> bool:
    if status_code == 404:
        return True
    sample = f"{title} {text[:600]}".lower()
    markers = (
        "404: this page could not be found",
        "404 - page not found",
        "404 page not found",
        "page could not be found",
        "page not found",
        "sivua ei löytynyt",
        "sidan hittades inte",
    )
    return any(marker in sample for marker in markers)


def _html_to_text(raw_html: str) -> tuple[str, str]:
    soup = BeautifulSoup(raw_html or "", "html.parser")
    for tag in soup(["script", "style", "noscript", "svg"]):
        tag.decompose()
    title = _clip(soup.title.string if soup.title else "", 180)
    return title, _clip(soup.get_text(" ", strip=True), 14000)


def _homepage_links(base_url: str, limit: int = 80) -> list[str]:
    """Extract official-domain links from the homepage for smarter targeting."""
    base = _normalize_url(base_url)
    official_host = _host(base)
    if not base or not official_host:
        return []
    try:
        resp = requests.get(base, headers={"User-Agent": "The-Scanner Company Evidence Builder/1.0"}, timeout=15)
        if resp.status_code >= 400:
            return []
        soup = BeautifulSoup(resp.text, "html.parser")
        links = []
        for tag in soup.find_all("a", href=True):
            href = str(tag.get("href") or "").strip()
            if not href or href.startswith(("#", "mailto:", "tel:", "javascript:")):
                continue
            clean = _normalize_url(href, base)
            if not clean or not _same_or_subdomain(_host(clean), official_host):
                continue
            links.append(clean)
            if len(links) >= limit:
                break
        return links
    except Exception:
        return []


def _links_from_text(text: str, base_url: str, limit: int = 80) -> list[str]:
    """Extract links from Firecrawl/Jina markdown or text snippets."""
    base = _normalize_url(base_url)
    official_host = _host(base)
    if not text or not official_host:
        return []
    candidates = []
    patterns = (
        r"\[[^\]]+\]\((https?://[^)\s]+|/[^)\s]+)\)",
        r"https?://[^\s)\"']+",
    )
    for pattern in patterns:
        for match in re.findall(pattern, text):
            url = str(match).strip().strip("<>").rstrip(".,;")
            clean = _normalize_url(url, base)
            if clean and _same_or_subdomain(_host(clean), official_host):
                candidates.append(clean)
            if len(candidates) >= limit:
                break
        if len(candidates) >= limit:
            break
    return candidates


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
    normalized = _normalize_url(url)
    stripped = re.sub(r"^https?://", "", normalized)
    for reader_url in (f"https://r.jina.ai/http://{stripped}", f"https://r.jina.ai/http://{normalized}"):
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
    title = fetched.get("title") or ""
    status_code = int(fetched.get("status_code") or 200)
    not_found = _is_not_found_content(title, text, status_code)
    return {
        **fetched,
        "title": title,
        "text": text,
        "page_not_found": not_found,
        "url_verified": (200 <= status_code < 400) and not not_found,
        "content_verified": len(text) >= 250 and not not_found,
    }


def _language_prefixes(base_url: str) -> list[str]:
    path_parts = [p for p in urlparse(_normalize_url(base_url)).path.split("/") if p]
    if path_parts and path_parts[0].lower() in {"fi", "en", "sv", "se", "de", "fr"}:
        return [path_parts[0].lower()]
    return [""]


def _localized_candidate_paths(base_url: str) -> list[str]:
    paths = []
    for prefix in _language_prefixes(base_url):
        for path in COMMON_TARGET_PATHS:
            clean_path = path.strip("/")
            if not clean_path:
                continue
            full_path = f"/{prefix}/{clean_path}" if prefix else f"/{clean_path}"
            paths.append(full_path)
    return paths


def _base_origin(base_url: str) -> str:
    parsed = urlparse(_normalize_url(base_url))
    if not parsed.scheme or not parsed.netloc:
        return _normalize_url(base_url)
    return f"{parsed.scheme}://{parsed.netloc}"


def _target_score(url: str, base_url: str, origin_hint: str = "guess") -> int:
    path = urlparse(url).path.lower()
    if _is_homepage_url(url) and _host(url) == _host(base_url):
        return 100
    profile = _source_profile(url)
    category_order = {
        "identity_entity": 90,
        "strategy_trigger_events": 82,
        "technology_service_fit": 78,
        "hiring_capability": 70,
        "stakeholder_evidence": 66,
        "procurement_public_activity": 64,
        "market_regulatory_pressure": 58,
        "other_official": 40,
    }
    score = category_order.get(profile.get("category"), 35)
    if any(k in path for k in ("privacy", "cookie", "terms", "login", "cart", "search", "tag", "author")):
        score -= 50
    if len(path.strip("/").split("/")) > 4:
        score -= 12
    if origin_hint == "discovered":
        score += 10
    elif origin_hint == "homepage_markdown":
        score += 14
    return score


def _discover_urls(company_url: str, max_urls: int) -> list[str]:
    base = _normalize_url(company_url)
    official_host = _host(base)
    origin = _base_origin(base)
    discovered = [(base, "discovered")]
    discovered.extend((url, "discovered") for url in _firecrawl_map(base, max(max_urls * 3, 40)))
    discovered.extend((url, "discovered") for url in _homepage_links(base, max(max_urls * 3, 60)))
    homepage_fetch = _firecrawl_scrape(base)
    if homepage_fetch and homepage_fetch.get("text"):
        discovered.extend((url, "homepage_markdown") for url in _links_from_text(homepage_fetch.get("text") or "", base, max(max_urls * 4, 80)))
    discovered.extend(
        (_normalize_url(urljoin(origin.rstrip("/") + "/", path.lstrip("/"))), "guess")
        for path in _localized_candidate_paths(base)
    )
    filtered = []
    seen = set()
    for url, origin_hint in discovered:
        clean = _normalize_url(url, base)
        if not clean or clean in seen:
            continue
        seen.add(clean)
        if not _same_or_subdomain(_host(clean), official_host):
            continue
        path = urlparse(clean).path.lower()
        if clean == base or any(keyword in path for keyword in TARGET_KEYWORDS):
            filtered.append((clean, origin_hint))
    filtered.sort(key=lambda item: _target_score(item[0], base, item[1]), reverse=True)
    return [url for url, _origin_hint in filtered[:max_urls]]


def _quote_for_entity(text: str, tokens: list[str]) -> str:
    sentences = [s.strip() for s in re.split(r"(?<=[.!?])\s+", text or "") if s.strip()]
    evidence_terms = (
        "company", "business", "service", "solution", "customer", "strategy",
        "annual", "revenue", "employee", "founded", "develop", "manufactur",
        "tuottaa", "tuotanto", "tuotekehitys", "palvelu", "ratkaisu",
        "asiakas", "strategia", "liikevaihto", "henkilöstö", "henkilosto",
        "ydinosaamista", "vastuullisuus", "ura", "rekry",
    )
    scored = []
    for idx, sentence in enumerate(sentences[:140]):
        lowered = sentence.lower()
        if len(sentence) < 45 or "base64-image-removed" in lowered:
            continue
        score = 0
        if any(token in lowered for token in tokens):
            score += 8
        if any(term in lowered for term in evidence_terms):
            score += 4
        if idx < 40:
            score += 1
        if score:
            scored.append((score, idx, sentence))
    if scored:
        scored.sort(key=lambda item: (-item[0], item[1]))
        return _clip(scored[0][2], 420)
    return _clip(" ".join(sentences[:2]), 420)


def _verify_record(company_name: str, company_url: str, url: str, fetched: dict) -> dict:
    official_host = _host(company_url)
    source_host = _host(url)
    tokens = _company_tokens(company_name)
    text = fetched.get("text") or ""
    title = fetched.get("title") or ""
    profile = _source_profile(url, title)
    source_type = profile.get("source_type", "official_page")
    same_domain = _same_or_subdomain(source_host, official_host)
    domain_token = official_host.split(".")[0] if official_host else ""
    text_l = text.lower()
    contains_entity = any(token in text_l for token in tokens) or (domain_token and domain_token in text_l)
    entity_verified = bool(same_domain and fetched.get("content_verified") and (contains_entity or source_type == "homepage"))
    has_date = bool(re.search(r"\b(20[1-3][0-9]|19[8-9][0-9])\b", text_l))
    has_trigger_event = any(term in text_l for term in TRIGGER_EVENT_KEYWORDS)
    has_technology_signal = any(term in text_l for term in TECH_SIGNAL_KEYWORDS)
    eligible = bool(
        fetched.get("url_verified")
        and fetched.get("content_verified")
        and entity_verified
        and source_type in ELIGIBLE_TYPES
    )
    if not same_domain:
        match_status = "wrong"
        reason = "wrong_entity_domain_mismatch"
    elif fetched.get("page_not_found"):
        match_status = "unknown"
        reason = "page_not_found"
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
    allowed_as_fact = bool(
        eligible
        and (
            source_type in FACT_SAFE_TYPES
            or (source_type == "newsroom" and has_date and has_trigger_event)
            or source_type == "procurement"
        )
        and source_type not in {"careers", "leadership"}
    )
    allowed_as_hypothesis = bool(
        eligible
        and not allowed_as_fact
        and source_type not in {"leadership"}
    )
    allowed_as_discovery_question = bool(
        (eligible or fetched.get("content_verified"))
        and source_type in {"careers", "services", "references", "newsroom", "sustainability", "official_page", "leadership"}
    )
    internal_only = bool(
        not allowed_as_fact
        and not allowed_as_hypothesis
        and not allowed_as_discovery_question
    )
    if source_type == "leadership" and eligible:
        internal_only = True
    if fetched.get("page_not_found"):
        allowed_as_fact = allowed_as_hypothesis = allowed_as_discovery_question = False
        internal_only = False
    blocked = bool(match_status == "wrong" or fetched.get("page_not_found"))
    if allowed_as_fact:
        sales_use = "allowed_as_fact"
    elif allowed_as_hypothesis:
        sales_use = "allowed_as_hypothesis"
    elif allowed_as_discovery_question:
        sales_use = "allowed_as_discovery_question"
    elif internal_only:
        sales_use = "internal_only"
    else:
        sales_use = "blocked"
    return {
        "source_category": profile.get("category"),
        "source_type": source_type,
        "target_fields": list(profile.get("target_fields", ())),
        "context_only": bool(profile.get("context_only")),
        "canonical_source_host": source_host,
        "source_quote": _quote_for_entity(text, tokens),
        "url_verified": bool(fetched.get("url_verified")),
        "content_verified": bool(fetched.get("content_verified")),
        "page_not_found": bool(fetched.get("page_not_found")),
        "entity_verified": entity_verified,
        "entity_match_status": match_status,
        "claim_support_status": "supported" if eligible else "unchecked",
        "analysis_verified": eligible,
        "generation_eligible": eligible,
        "buying_evidence_eligible": eligible,
        "promotion_status": "generation_eligible" if eligible else "display_only",
        "verification_reason": reason,
        "signal_markers": {
            "has_recent_or_dated_content": has_date,
            "has_trigger_event_language": has_trigger_event,
            "has_technology_service_language": has_technology_signal,
        },
        "allowed_as_fact": allowed_as_fact,
        "allowed_as_hypothesis": allowed_as_hypothesis,
        "allowed_as_discovery_question": allowed_as_discovery_question,
        "internal_only": internal_only,
        "blocked": blocked,
        "sales_use": sales_use,
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
    source_categories = {}
    sales_use_counts = {}
    for record in records:
        category = record.get("source_category") or "unknown"
        source_categories[category] = source_categories.get(category, 0) + 1
        sales_use = record.get("sales_use") or "unknown"
        sales_use_counts[sales_use] = sales_use_counts.get(sales_use, 0) + 1
    return {
        "candidate_sources": len(records),
        "url_verified": sum(1 for r in records if r.get("url_verified")),
        "content_verified": sum(1 for r in records if r.get("content_verified")),
        "entity_verified": sum(1 for r in records if r.get("entity_verified")),
        "buying_evidence_eligible": sum(1 for r in records if r.get("buying_evidence_eligible")),
        "allowed_as_fact": sum(1 for r in records if r.get("allowed_as_fact")),
        "allowed_as_hypothesis": sum(1 for r in records if r.get("allowed_as_hypothesis")),
        "allowed_as_discovery_question": sum(1 for r in records if r.get("allowed_as_discovery_question")),
        "internal_only": sum(1 for r in records if r.get("internal_only")),
        "blocked": sum(1 for r in records if r.get("blocked")),
        "page_not_found": sum(1 for r in records if r.get("page_not_found")),
        "display_only_or_blocked": sum(1 for r in records if not r.get("buying_evidence_eligible")),
        "wrong_entity": sum(1 for r in records if r.get("wrong_entity")),
        "source_categories": source_categories,
        "sales_use_counts": sales_use_counts,
        "providers": sorted({r.get("provider") for r in records if r.get("provider")}),
    }


def _warnings(records: list[dict], stats: dict) -> list[str]:
    warnings = []
    total = max(1, stats.get("candidate_sources", 0))
    if stats.get("page_not_found", 0) / total >= 0.35:
        warnings.append("High page-not-found rate; official-domain discovery may need more site-specific links.")
    if stats.get("content_verified", 0) / total < 0.35:
        warnings.append("Low readable-content rate; extraction coverage is thin.")
    if stats.get("buying_evidence_eligible", 0) < 3:
        warnings.append("Few buying-evidence-eligible records found; Buying Insight should remain cautious.")
    categories = stats.get("source_categories") or {}
    if not categories.get("strategy_trigger_events"):
        warnings.append("No recent strategy/newsroom trigger sources found.")
    if not categories.get("technology_service_fit"):
        warnings.append("No technology or service-fit sources found.")
    if not categories.get("stakeholder_evidence"):
        warnings.append("No stakeholder/leadership source pages found.")
    if stats.get("allowed_as_fact", 0) == 0:
        warnings.append("No sources are currently safe for customer-facing factual sales wording.")
    return warnings


def render_company_evidence_html(database: dict) -> str:
    company = database.get("company_name") or "Company"
    rows = []
    for rec in _list(database.get("records")):
        rows.append(
            "<tr>"
            f"<td>{html.escape(rec.get('id') or '')}</td>"
            f"<td>{html.escape(rec.get('source_category') or '')}</td>"
            f"<td>{html.escape(rec.get('source_type') or '')}</td>"
            f"<td><a href=\"{html.escape(rec.get('url') or '')}\">{html.escape(_clip(rec.get('title') or rec.get('url'), 90))}</a></td>"
            f"<td>{html.escape(rec.get('provider') or '')}</td>"
            f"<td>{html.escape(rec.get('sales_use') or '')}</td>"
            f"<td>{html.escape(rec.get('verification_reason') or '')}</td>"
            "</tr>"
        )
    stats = database.get("stats") or {}
    warnings = "".join(f"<li>{html.escape(str(w))}</li>" for w in _list(database.get("warnings")))
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
<span class="pill">Facts: {stats.get('allowed_as_fact', 0)}</span>
<span class="pill">Hypotheses: {stats.get('allowed_as_hypothesis', 0)}</span>
<span class="pill">Questions: {stats.get('allowed_as_discovery_question', 0)}</span>
<span class="pill">Entity verified: {stats.get('entity_verified', 0)}</span>
<span class="pill">404 / not found: {stats.get('page_not_found', 0)}</span>
<span class="pill">Display-only / blocked: {stats.get('display_only_or_blocked', 0)}</span>
</div>
{"<ul>" + warnings + "</ul>" if warnings else ""}
<table><thead><tr><th>ID</th><th>Category</th><th>Type</th><th>Source</th><th>Provider</th><th>Sales use</th><th>Reason</th></tr></thead>
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
    stats = _stats(records)
    database = {
        "version": "company-evidence-v1.1",
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
            "mode": "official_domain_source_target_registry",
            "max_urls": max_urls,
            "broad_web_search": False,
            "social_scraping": False,
            "contact_scraping": False,
            "coresignal_enabled": False,
            "source_target_categories": sorted({target["category"] for target in SOURCE_TARGET_REGISTRY}),
        },
        "records": records,
        "stats": stats,
        "warnings": [],
    }
    if not _firecrawl_headers():
        database["warnings"].append("FIRECRAWL_API_KEY not configured; used Jina/direct official-domain fallback only.")
    database["warnings"].extend(_warnings(records, stats))
    return database, render_company_evidence_html(database)
