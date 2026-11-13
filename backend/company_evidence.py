"""
Company Evidence Database for Buying Insight.

This is a separate, BLOB2-backed acquisition layer. It gathers candidate
official-domain evidence for one company and applies conservative eligibility
rules before Buying Insight can use it.
"""

from __future__ import annotations

import hashlib
import html
import json
import os
import re
from datetime import datetime
from typing import Any
from urllib.parse import urlencode, urljoin, urlparse

import requests
from bs4 import BeautifulSoup
from stakeholder_policy import (
    demand_profile, is_historical_role, role_context, search_rounds,
    rank_stakeholders, employee_count, candidate_reason, person_key,
)


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
OUTBOUND_REFERENCE_SOURCE_TYPES = {
    "about", "services", "references", "newsroom", "sustainability",
    "investor", "careers", "procurement", "homepage", "official_page",
}
OUTBOUND_BLOCKED_HOST_FRAGMENTS = (
    "facebook.com", "instagram.com", "linkedin.com", "twitter.com", "x.com",
    "youtube.com", "youtu.be", "tiktok.com", "cookiebot.com", "onetrust.com",
    "google-analytics.com", "googletagmanager.com", "doubleclick.net",
    "addthis.com", "sharethis.com", "mailto:", "tel:",
)
OUTBOUND_BLOCKED_PATH_MARKERS = (
    "/login", "/signin", "/sign-in", "/register", "/search", "/tag/", "/tags/",
    "/cookie", "/privacy", "/terms", "/wp-json", "/feed", "/rss",
)
OUTBOUND_BLOCKED_EXTENSIONS = (
    ".png", ".jpg", ".jpeg", ".gif", ".svg", ".webp", ".ico", ".css", ".js",
    ".woff", ".woff2", ".ttf", ".eot", ".mp4", ".webm", ".mov", ".zip",
)
PR_DISTRIBUTION_HOSTS = (
    "cision.com", "news.cision.com", "prnewswire.com", "businesswire.com",
    "globenewswire.com", "cisionwire.com", "sttinfo.fi", "newsroom.cision.com",
)
INDEPENDENT_PUBLIC_HOSTS = (
    "europa.eu", "oecd.org", "worldbank.org", "weforum.org", "stat.fi",
    "prh.fi", "hankintailmoitukset.fi", "hilma.fi", "ted.europa.eu",
    "sec.gov", "ft.com", "reuters.com", "bloomberg.com", "yle.fi",
    "kauppalehti.fi", "talouselama.fi", "tekniikkatalous.fi",
)
PARTNER_VENDOR_MARKERS = (
    "partner", "partners", "vendor", "case", "customer", "client", "success",
    "reference", "marketplace", "technology-partner", "customer-story",
)
LEGAL_SUFFIXES = (
    "Oyj", "Oy", "Oy Ab", "Ab", "Abp", "Ltd", "Limited", "Plc", "Corporation", "Corp",
)
LEGAL_PREFIX_SUFFIXES = (
    ("Oy", "Ab"),
    ("Ab", ""),
)
IDENTIFIER_KEYS = (
    "business_id", "businessId", "businessID", "businessIdFi", "y_tunnus", "ytunnus",
    "y-tunnus", "registration_id", "registrationId", "registration_number",
    "registrationNumber", "company_registration_number", "companyRegistrationNumber",
    "vat", "vat_number", "vatNumber", "lei", "LEI",
)
LEGAL_NAME_KEYS = (
    "legal_name", "company_legal_name", "name_legal", "nameLegal", "legalName",
    "registered_name", "registeredName", "official_name", "officialName",
)
ALIAS_KEYS = (
    "aliases", "known_aliases", "name_aliases", "alternative_names", "nameAlts",
    "former_names", "formerNames", "search_aliases",
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


def _env_bool(name: str, default: bool = False) -> bool:
    value = os.environ.get(name)
    if value is None:
        return default
    return str(value).strip().lower() in {"1", "true", "yes", "on", "enabled"}


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


def _domain_like(value: str) -> bool:
    text = str(value or "").strip().lower()
    if not text or " " in text or "/" in text:
        return False
    return bool(re.match(r"^[a-z0-9][a-z0-9.-]*\.[a-z]{2,}$", text))


def _same_or_subdomain(host: str, official_host: str) -> bool:
    return bool(host and official_host and (host == official_host or host.endswith("." + official_host)))


def _host_matches_any(host: str, patterns: tuple[str, ...]) -> bool:
    host = (host or "").lower()
    return any(host == pattern or host.endswith("." + pattern) for pattern in patterns if pattern)


def _company_tokens(company_name: str) -> list[str]:
    text = re.sub(r"\b(oyj|oy|abp|ab|plc|ltd|inc|corp|corporation|group|holding|holdings)\b", " ", (company_name or "").lower())
    return [t for t in re.findall(r"[a-zåäö0-9]{3,}", text) if t not in {"the", "and", "for"}][:6]


def _broad_alias_reason(name: str) -> str:
    base = _strip_legal_suffixes(name)
    tokens = _company_tokens(base)
    if len(base) < 5:
        return "alias_too_short_for_fallback_search"
    if len(tokens) == 1 and len(tokens[0]) <= 4:
        return "alias_too_generic_for_fallback_search"
    return ""


def _dedupe_preserve(values: list[Any]) -> list[str]:
    seen = set()
    out = []
    for value in values:
        text = _clip(value, 180)
        if not text:
            continue
        key = re.sub(r"\s+", " ", text).strip().lower()
        if key and key not in seen:
            seen.add(key)
            out.append(text)
    return out


def _walk_identity_values(data: Any, keys: tuple[str, ...], *, max_depth: int = 3) -> list[str]:
    values: list[str] = []
    if max_depth < 0 or data is None:
        return values
    if isinstance(data, dict):
        for key, value in data.items():
            if key in keys:
                if isinstance(value, list):
                    for item in value:
                        if isinstance(item, dict):
                            extracted = _extract_identifier_value(item)
                            if extracted:
                                values.append(extracted)
                        elif item:
                            values.append(_clip(item, 180))
                elif isinstance(value, dict):
                    extracted = _extract_identifier_value(value)
                    if extracted:
                        values.append(extracted)
                    values.extend(_walk_identity_values(value, keys, max_depth=max_depth - 1))
                else:
                    values.append(_clip(value, 180))
            elif isinstance(value, (dict, list)):
                values.extend(_walk_identity_values(value, keys, max_depth=max_depth - 1))
    elif isinstance(data, list):
        for item in data[:40]:
            values.extend(_walk_identity_values(item, keys, max_depth=max_depth - 1))
    return values


def _extract_identifier_value(value: Any) -> str:
    if isinstance(value, dict):
        direct = _first_key(value, ("value", "id", "identifier", "businessId", "registrationNumber"))
        if direct:
            return _clip(direct, 80)
        for nested in value.values():
            extracted = _extract_identifier_value(nested)
            if extracted:
                return extracted
        return ""
    if isinstance(value, list):
        for item in value:
            extracted = _extract_identifier_value(item)
            if extracted:
                return extracted
        return ""
    text = _clip(value, 80)
    match = re.search(r"\b\d{7}-\d\b", text)
    return match.group(0) if match else text


def _strip_legal_suffixes(name: str) -> str:
    text = re.sub(r"\b(oyj|oy|abp|ab|plc|ltd|limited|inc|corp|corporation|group|holding|holdings)\b", " ", name or "", flags=re.I)
    return re.sub(r"\s+", " ", text).strip(" ,.-")


def _generated_legal_aliases(name: str, company_url: str = "") -> list[str]:
    base = _strip_legal_suffixes(name)
    if not base or len(base) < 3:
        return []
    host = _host(company_url)
    aliases = [base]
    if host.endswith(".fi"):
        aliases.extend([f"{base} {suffix}" for suffix in ("Oyj", "Oy", "Oy Ab", "Ab")])
        aliases.extend([f"{prefix} {base}{(' ' + suffix) if suffix else ''}" for prefix, suffix in LEGAL_PREFIX_SUFFIXES])
    else:
        aliases.extend([f"{base} {suffix}" for suffix in LEGAL_SUFFIXES])
    return _dedupe_preserve(aliases)


def _company_identity_bundle(company_data: dict, company_name: str, company_url: str) -> dict:
    explicit_names = [company_name]
    explicit_names.extend(_walk_identity_values(company_data, LEGAL_NAME_KEYS, max_depth=4))
    explicit_names.extend(_walk_identity_values(company_data, ALIAS_KEYS, max_depth=4))
    generated_names = []
    for name in explicit_names:
        generated_names.extend(_generated_legal_aliases(name, company_url))
    names = _dedupe_preserve([*explicit_names, *generated_names])[:18]
    identifiers = _dedupe_preserve(
        _extract_identifier_value(value)
        for value in _walk_identity_values(company_data, IDENTIFIER_KEYS, max_depth=4)
    )[:10]
    raw_domains = [_host(company_url)]
    for value in _walk_identity_values(company_data, ("companyUrl", "url", "website", "website_domain", "domain"), max_depth=3):
        host = _host(value)
        if _domain_like(host):
            raw_domains.append(host)
    domains = _dedupe_preserve(raw_domains)[:6]
    return {
        "names": names,
        "primary_name": company_name,
        "domains": domains,
        "primary_domain": _host(company_url),
        "identifiers": identifiers,
        "prh_aliases_used": _list((company_data.get("prh_identity") or {}).get("names"))[:8] if isinstance(company_data.get("prh_identity"), dict) else [],
        "prh_aliases_skipped": _list((company_data.get("prh_identity") or {}).get("skipped_names"))[:12] if isinstance(company_data.get("prh_identity"), dict) else [],
        "target_country": _target_country_from_url(company_url),
    }


def _prh_enabled(company_url: str) -> bool:
    if not _host(company_url).endswith(".fi"):
        return False
    return _env_bool("COMPANY_EVIDENCE_PRH_ENABLED", default=True)


def _prh_name_values(record: dict) -> list[str]:
    names = []
    for item in _list(record.get("names")):
        if isinstance(item, dict):
            names.append(item.get("name"))
        else:
            names.append(item)
    if record.get("name"):
        names.append(record.get("name"))
    return _dedupe_preserve(names)


def _prh_business_id(record: dict) -> str:
    return _extract_identifier_value(record.get("businessId"))


def _prh_alias_decision(company_name: str, name: str) -> tuple[bool, str]:
    target_base = _strip_legal_suffixes(company_name).lower()
    name_base = _strip_legal_suffixes(name).lower()
    target_tokens = set(_company_tokens(company_name))
    name_tokens = set(_company_tokens(name))
    if not name_base:
        return False, "empty_name"
    if target_base and name_base == target_base:
        return True, "base_name_exact"
    broad_reason = _broad_alias_reason(name)
    if broad_reason:
        return False, broad_reason
    if target_base and (target_base in name_base or name_base in target_base):
        return True, "base_name_contains_target"
    if target_tokens and target_tokens <= name_tokens:
        return True, "target_tokens_in_alias"
    return False, "alias_not_specific_to_scanned_company"


def _prh_filtered_aliases(company_name: str, names: list[str]) -> tuple[list[str], list[dict]]:
    accepted = []
    skipped = []
    for name in names:
        keep, reason = _prh_alias_decision(company_name, name)
        if keep:
            accepted.append(name)
        else:
            skipped.append({"name": _clip(name, 140), "reason": reason})
    return _dedupe_preserve(accepted), skipped[:12]


def _prh_record_matches(company_name: str, record: dict) -> bool:
    target_base = _strip_legal_suffixes(company_name).lower()
    target_tokens = set(_company_tokens(company_name))
    for name in _prh_name_values(record):
        name_base = _strip_legal_suffixes(name).lower()
        if target_base and name_base == target_base:
            return True
        name_tokens = set(_company_tokens(name))
        if target_tokens and target_tokens <= name_tokens:
            return True
    return False


def _fetch_prh_identity(company_name: str, company_url: str) -> dict:
    """Best-effort Finnish registry identity for matching only; never blocks the job."""
    result = {"enabled": _prh_enabled(company_url), "queried": False, "matched": False}
    if not result["enabled"]:
        return result
    try:
        resp = requests.get(
            "https://avoindata.prh.fi/opendata-ytj-api/v3/companies",
            params={"name": company_name, "maxResults": 5},
            headers={"User-Agent": "The-Scanner Company Evidence Builder/1.0", "Accept": "application/json"},
            timeout=8,
        )
        result.update({"queried": True, "status_code": resp.status_code})
        if resp.status_code != 200:
            return result
        data = resp.json()
        candidates = _list(data.get("companies"))
        result["candidate_count"] = len(candidates)
        best = next((candidate for candidate in candidates if isinstance(candidate, dict) and _prh_record_matches(company_name, candidate)), None)
        if not best:
            return result
        names = _prh_name_values(best)
        accepted_names, skipped_names = _prh_filtered_aliases(company_name, names)
        business_id = _prh_business_id(best)
        result.update({
            "matched": True,
            "business_id": business_id,
            "business_id_normalized": _norm_identifier(business_id),
            "legal_name": accepted_names[0] if accepted_names else (company_name or ""),
            "names": accepted_names[:8],
            "skipped_names": skipped_names,
            "raw_names": names[:12],
            "registration_date": _clip(best.get("registrationDate"), 40),
            "company_form": _clip(best.get("companyForm"), 80),
            "status": _clip(best.get("status"), 80),
        })
    except Exception as exc:
        result.update({"queried": True, "error": _clip(exc, 240)})
    return result


def _merge_identity_enrichment(company_data: dict, identity_enrichment: dict | None) -> dict:
    if not identity_enrichment or not identity_enrichment.get("matched"):
        return dict(company_data)
    merged = dict(company_data)
    merged["prh_identity"] = identity_enrichment
    if identity_enrichment.get("business_id"):
        merged.setdefault("business_id", identity_enrichment.get("business_id"))
    if identity_enrichment.get("business_id_normalized"):
        merged.setdefault("registration_id", identity_enrichment.get("business_id_normalized"))
    if identity_enrichment.get("legal_name"):
        merged.setdefault("legal_name", identity_enrichment.get("legal_name"))
    aliases = list(_list(merged.get("aliases")))
    aliases.extend(_list(identity_enrichment.get("names")))
    if aliases:
        merged["aliases"] = _dedupe_preserve(aliases)
    return merged


def _keyword_matches(text: str, keyword: str) -> bool:
    value = (text or "").lower()
    term = (keyword or "").lower().strip()
    if not term:
        return False
    if len(term) <= 3 and re.match(r"^[a-zåäö0-9]+$", term):
        return bool(re.search(rf"(?<![a-zåäö0-9]){re.escape(term)}(?![a-zåäö0-9])", value))
    if re.match(r"^[a-zåäö0-9]+$", term):
        return bool(re.search(rf"(?<![a-zåäö0-9]){re.escape(term)}", value))
    return term in value


def _any_keyword(text: str, keywords: tuple[str, ...] | list[str]) -> bool:
    return any(_keyword_matches(text, keyword) for keyword in keywords)


def _is_homepage_url(url: str) -> bool:
    path_parts = [p for p in urlparse(_normalize_url(url)).path.split("/") if p]
    return len(path_parts) == 0 or (len(path_parts) == 1 and path_parts[0].lower() in {"fi", "en", "sv", "se", "de", "fr"})


def _source_profile(url: str, title: str = "") -> dict:
    value = f"{url} {title}".lower()
    if _is_homepage_url(url):
        return dict(SOURCE_TARGET_REGISTRY[0])
    for target in SOURCE_TARGET_REGISTRY[1:]:
        if _any_keyword(value, target.get("keywords", ())):
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
        "oops! finavia",
        "oops! ",
    )
    if any(marker in sample for marker in markers):
        return True
    if title.strip().lower().startswith("oops") and any(nav in sample for nav in ("palaa etusivulle", "back to homepage", "return to front page")):
        return True
    return False


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


def _is_outbound_reference_candidate(url: str, official_host: str) -> bool:
    clean = _normalize_url(url)
    if not clean or not official_host:
        return False
    parsed = urlparse(clean)
    host = _host(clean)
    if not parsed.scheme.startswith("http") or not host:
        return False
    if _same_or_subdomain(host, official_host):
        return False
    value = clean.lower()
    if any(fragment in value for fragment in OUTBOUND_BLOCKED_HOST_FRAGMENTS):
        return False
    path = parsed.path.lower()
    if any(path.endswith(ext) for ext in OUTBOUND_BLOCKED_EXTENSIONS):
        return False
    if any(marker in path for marker in OUTBOUND_BLOCKED_PATH_MARKERS):
        return False
    return True


def _outbound_references_from_html(raw_html: str, base_url: str, official_host: str, limit: int = 80) -> list[dict]:
    if not raw_html:
        return []
    soup = BeautifulSoup(raw_html, "html.parser")
    refs = []
    seen = set()
    for tag in soup.find_all("a", href=True):
        href = str(tag.get("href") or "").strip()
        if not href or href.startswith(("#", "mailto:", "tel:", "javascript:")):
            continue
        clean = _normalize_url(href, base_url)
        if not _is_outbound_reference_candidate(clean, official_host):
            continue
        key = clean.rstrip("/").lower()
        if key in seen:
            continue
        seen.add(key)
        anchor = _clip(tag.get_text(" ", strip=True), 220)
        parent_text = _clip(tag.parent.get_text(" ", strip=True) if tag.parent else "", 500)
        refs.append({
            "external_url": clean,
            "anchor_text": anchor,
            "reference_context": parent_text or anchor,
            "extraction_provider": "direct_html",
        })
        if len(refs) >= limit:
            break
    return refs


def _outbound_references_from_text(text: str, base_url: str, official_host: str, limit: int = 80) -> list[dict]:
    """Extract external references from Firecrawl/Jina markdown or text snippets."""
    if not text or not official_host:
        return []
    refs = []
    seen = set()
    markdown_pattern = r"\[([^\]]{0,220})\]\((https?://[^)\s]+|/[^)\s]+)\)"
    for label, url in re.findall(markdown_pattern, text):
        clean = _normalize_url(url, base_url)
        if not _is_outbound_reference_candidate(clean, official_host):
            continue
        key = clean.rstrip("/").lower()
        if key in seen:
            continue
        seen.add(key)
        refs.append({
            "external_url": clean,
            "anchor_text": _clip(label, 220),
            "reference_context": _clip(label, 500),
            "extraction_provider": "firecrawl_markdown",
        })
        if len(refs) >= limit:
            return refs
    for match in re.findall(r"https?://[^\s)\"']+", text):
        clean = _normalize_url(str(match).strip().strip("<>").rstrip(".,;"), base_url)
        if not _is_outbound_reference_candidate(clean, official_host):
            continue
        key = clean.rstrip("/").lower()
        if key in seen:
            continue
        seen.add(key)
        refs.append({
            "external_url": clean,
            "anchor_text": "",
            "reference_context": "",
            "extraction_provider": "plain_text_url",
        })
        if len(refs) >= limit:
            break
    return refs


def _official_page_outbound_material(url: str) -> dict:
    """Best-effort official-page material for outbound link extraction only."""
    material = {"text": "", "html": "", "providers": []}
    if _firecrawl_headers():
        try:
            resp = requests.post(
                "https://api.firecrawl.dev/v1/scrape",
                headers=_firecrawl_headers(),
                json={"url": url, "formats": ["markdown", "html"]},
                timeout=35,
            )
            if resp.status_code in (200, 201):
                data = resp.json().get("data") or resp.json()
                material["text"] = data.get("markdown") or data.get("content") or ""
                material["html"] = data.get("html") or ""
                material["providers"].append("firecrawl")
        except Exception as exc:
            print(f"   ⚠️ [Company Evidence] Firecrawl outbound extraction exception: {exc}", flush=True)
    try:
        resp = requests.get(url, headers={"User-Agent": "The-Scanner Company Evidence Builder/1.0"}, timeout=15)
        if 200 <= resp.status_code < 400 and resp.text:
            material["html"] = material.get("html") or resp.text
            if not material.get("text"):
                _title, direct_text = _html_to_text(resp.text)
                material["text"] = direct_text
            material["providers"].append("direct")
    except Exception:
        pass
    return material


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
    elif origin_hint == "guess":
        score -= 28
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
    has_trigger_event = _any_keyword(text_l, TRIGGER_EVENT_KEYWORDS)
    has_technology_signal = _any_keyword(text_l, TECH_SIGNAL_KEYWORDS)
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


def _outbound_reference_enabled() -> bool:
    return _env_bool("COMPANY_EVIDENCE_OUTBOUND_REFERENCES", default=True)


def _outbound_publisher_classification(url: str, title: str, text: str, reference_context: str = "") -> dict:
    host = _host(url)
    value = f"{url} {title} {reference_context} {text[:1200]}".lower()
    if _host_matches_any(host, PR_DISTRIBUTION_HOSTS) or any(term in value for term in ("press release", "lehdistötiedote", "pörssitiedote", "cision")):
        return {
            "source_category": "company_distributed_material",
            "publisher_type": "press_release_distribution",
            "publisher_independence": "company_distributed",
            "boundary": "Company-distributed material can support what the company announced, but is not independent proof of the claim.",
        }
    if _host_matches_any(host, INDEPENDENT_PUBLIC_HOSTS):
        return {
            "source_category": "independent_external_evidence",
            "publisher_type": "regulator_registry_research_or_media",
            "publisher_independence": "independent",
            "boundary": "External source is independent of the company link, but still requires entity and claim validation before use.",
        }
    if any(marker in value for marker in PARTNER_VENDOR_MARKERS):
        return {
            "source_category": "partner_or_vendor_context",
            "publisher_type": "partner_vendor_or_customer_context",
            "publisher_independence": "partner_or_vendor",
            "boundary": "Partner/vendor/customer material is useful scoped context, not independent proof unless the claim is explicitly limited.",
        }
    if any(term in value for term in ("regulation", "standard", "directive", "procurement", "tender", "public report", "research report")):
        return {
            "source_category": "independent_external_evidence",
            "publisher_type": "public_or_research_context",
            "publisher_independence": "independent",
            "boundary": "External public/research material can support independent context after entity and claim validation.",
        }
    return {
        "source_category": "partner_or_vendor_context",
        "publisher_type": "unknown_external_reference",
        "publisher_independence": "unknown_or_contextual",
        "boundary": "External reference was found from an official company page; treat as a lead until independently validated.",
    }


def _outbound_reference_record(
    idx: int,
    company_name: str,
    company_url: str,
    originating_page: str,
    candidate: dict,
) -> dict:
    external_url = _normalize_url(candidate.get("external_url"))
    fetched = _fetch_source(external_url)
    text = fetched.get("text") or ""
    title = fetched.get("title") or candidate.get("anchor_text") or external_url
    tokens = _company_tokens(company_name)
    text_l = f"{title} {text}".lower()
    contains_entity = any(token in text_l for token in tokens)
    classification = _outbound_publisher_classification(
        external_url,
        title,
        text,
        candidate.get("reference_context") or candidate.get("anchor_text") or "",
    )
    source_category = classification["source_category"]
    content_verified = bool(fetched.get("content_verified"))
    page_not_found = bool(fetched.get("page_not_found"))
    url_verified = bool(fetched.get("url_verified"))
    entity_verified = bool(content_verified and contains_entity)
    unsafe = source_category == "irrelevant_or_unsafe"
    blocked = bool(page_not_found or not url_verified or unsafe)
    allowed_as_fact = bool(
        source_category == "independent_external_evidence"
        and url_verified
        and content_verified
        and entity_verified
        and not blocked
    )
    allowed_as_hypothesis = bool(
        not allowed_as_fact
        and source_category in {"partner_or_vendor_context", "company_distributed_material", "independent_external_evidence"}
        and url_verified
        and content_verified
        and entity_verified
        and not blocked
    )
    allowed_as_discovery_question = bool(
        not blocked
        and url_verified
        and content_verified
        and entity_verified
        and source_category in {"partner_or_vendor_context", "company_distributed_material", "independent_external_evidence"}
    )
    internal_only = bool(not (allowed_as_fact or allowed_as_hypothesis or allowed_as_discovery_question))
    if allowed_as_fact:
        sales_use = "allowed_as_fact"
        promotion_status = "generation_eligible"
    elif allowed_as_hypothesis:
        sales_use = "allowed_as_hypothesis"
        promotion_status = "scoped_context"
    elif allowed_as_discovery_question:
        sales_use = "allowed_as_discovery_question"
        promotion_status = "scoped_context"
    elif blocked:
        sales_use = "blocked"
        promotion_status = "blocked"
    else:
        sales_use = "internal_only"
        promotion_status = "audit_only"
    if blocked:
        reason = "external_reference_unreadable_or_blocked"
    elif not entity_verified:
        reason = "external_page_does_not_confirm_target_entity"
    elif allowed_as_fact:
        reason = "independent_external_entity_evidence"
    else:
        reason = "company_selected_external_context_requires_scoped_use"
    content_hash = hashlib.sha256(text.encode("utf-8", errors="ignore")).hexdigest() if text else ""
    return {
        "id": f"CED-{idx:02d}",
        "company_name": company_name,
        "company_url": company_url,
        "url": external_url,
        "external_url": external_url,
        "external_host": _host(external_url),
        "origin_relation": "official_page_outbound_reference",
        "originating_company_page": originating_page,
        "reference_context": _clip(candidate.get("reference_context") or candidate.get("anchor_text"), 700),
        "company_claim_vs_external_evidence_boundary": classification["boundary"],
        "provider": fetched.get("provider") or "none",
        "provider_record_type": "official_outbound_reference",
        "title": title,
        "fetched_at": datetime.utcnow().isoformat(),
        "content_hash": content_hash,
        "extraction_confidence": 80 if fetched.get("provider") == "firecrawl" else (70 if fetched.get("provider") == "jina" else (55 if content_verified else 0)),
        "source_freshness": "current_fetch",
        "text_excerpt": _clip(text, 1800),
        "source_quote": _quote_for_entity(text, tokens),
        "source_category": source_category,
        "source_type": "external_reference",
        "publisher_type": classification["publisher_type"],
        "publisher_independence": classification["publisher_independence"],
        "canonical_source_host": _host(external_url),
        "target_fields": ("external validation", "partner context", "public proof", "company-selected reference boundary"),
        "context_only": not allowed_as_fact,
        "url_verified": url_verified,
        "content_verified": content_verified,
        "page_not_found": page_not_found,
        "entity_verified": entity_verified,
        "entity_match_status": "exact" if entity_verified else ("unknown" if url_verified else "wrong"),
        "claim_support_status": "supported" if allowed_as_fact else ("contextual" if allowed_as_hypothesis or allowed_as_discovery_question else "unchecked"),
        "analysis_verified": bool(allowed_as_fact or allowed_as_hypothesis or allowed_as_discovery_question),
        "generation_eligible": allowed_as_fact,
        "buying_evidence_eligible": bool(allowed_as_fact or allowed_as_hypothesis or allowed_as_discovery_question),
        "promotion_status": promotion_status,
        "verification_reason": reason,
        "allowed_as_fact": allowed_as_fact,
        "allowed_as_hypothesis": allowed_as_hypothesis,
        "allowed_as_discovery_question": allowed_as_discovery_question,
        "internal_only": internal_only,
        "blocked": blocked,
        "sales_use": sales_use,
        "wrong_entity": False,
    }


def _discover_outbound_references(
    records: list[dict],
    company_name: str,
    company_url: str,
    *,
    start_idx: int,
) -> tuple[list[dict], dict]:
    diagnostics = {
        "enabled": _outbound_reference_enabled(),
        "official_pages_checked": 0,
        "outbound_candidates_found": 0,
        "records_added": 0,
        "independent_external_evidence": 0,
        "partner_or_vendor_context": 0,
        "company_distributed_material": 0,
        "excluded_candidates": 0,
        "providers": [],
    }
    if not diagnostics["enabled"]:
        return [], diagnostics
    official_host = _host(company_url)
    max_records = max(0, min(30, int(os.environ.get("COMPANY_EVIDENCE_MAX_OUTBOUND_REFERENCES", "12"))))
    max_pages = max(1, min(10, int(os.environ.get("COMPANY_EVIDENCE_MAX_OUTBOUND_PAGES", "6"))))
    if max_records <= 0:
        return [], diagnostics
    official_records = [
        record for record in records
        if record.get("url")
        and record.get("url_verified")
        and _same_or_subdomain(_host(record.get("url")), official_host)
        and str(record.get("source_type") or "").lower() in OUTBOUND_REFERENCE_SOURCE_TYPES
        and str(record.get("source_type") or "").lower() != "leadership"
    ][:max_pages]
    candidates: list[dict] = []
    seen = set()
    for record in official_records:
        page_url = record.get("url")
        diagnostics["official_pages_checked"] += 1
        material = _official_page_outbound_material(page_url)
        diagnostics["providers"].extend(material.get("providers") or [])
        page_refs = []
        page_refs.extend(_outbound_references_from_html(material.get("html") or "", page_url, official_host, limit=20))
        page_refs.extend(_outbound_references_from_text(material.get("text") or "", page_url, official_host, limit=20))
        for ref in page_refs:
            clean = _normalize_url(ref.get("external_url"))
            key = clean.rstrip("/").lower() if clean else ""
            if not key or key in seen:
                continue
            seen.add(key)
            ref["originating_company_page"] = page_url
            candidates.append(ref)
            if len(candidates) >= max_records * 2:
                break
        if len(candidates) >= max_records * 2:
            break
    diagnostics["outbound_candidates_found"] = len(candidates)
    outbound_records = []
    for ref in candidates:
        if len(outbound_records) >= max_records:
            break
        record = _outbound_reference_record(start_idx + len(outbound_records), company_name, company_url, ref.get("originating_company_page") or company_url, ref)
        if record.get("source_category") == "irrelevant_or_unsafe":
            diagnostics["excluded_candidates"] += 1
            continue
        outbound_records.append(record)
        category = record.get("source_category") or "unknown"
        diagnostics[category] = diagnostics.get(category, 0) + 1
    diagnostics["records_added"] = len(outbound_records)
    diagnostics["providers"] = sorted(set(diagnostics["providers"]))
    return outbound_records, diagnostics


def _coresignal_key() -> str:
    return _clean_env(os.environ.get("CORESIGNAL_API_KEY"))


def _coresignal_enabled() -> bool:
    key = _coresignal_key()
    if not key:
        return False
    explicit = os.environ.get("CORESIGNAL_ENABLED")
    if explicit is None:
        return True
    return _env_bool("CORESIGNAL_ENABLED", default=True)


def _coresignal_base_url() -> str:
    return _clean_env(os.environ.get("CORESIGNAL_BASE_URL")) or "https://api.coresignal.com"


def _coresignal_headers() -> dict:
    key = _coresignal_key()
    if not key:
        return {}
    return {
        "apikey": key,
        "Authorization": f"Bearer {key}",
        "Content-Type": "application/json",
        "Accept": "application/json",
    }


def _coresignal_url(path: str) -> str:
    base = _coresignal_base_url().rstrip("/")
    path = path if path.startswith("/") else "/" + path
    return base + path


def _coresignal_timeout() -> int:
    return max(8, min(45, int(os.environ.get("CORESIGNAL_TIMEOUT_SECONDS", "20"))))


def _coresignal_request(method: str, path: str, *, payload: dict | None = None, errors: list[dict] | None = None) -> Any:
    headers = _coresignal_headers()
    if not headers:
        return None
    url = _coresignal_url(path)
    try:
        if method.upper() == "POST":
            resp = requests.post(url, headers=headers, json=payload or {}, timeout=_coresignal_timeout())
        else:
            resp = requests.get(url, headers=headers, timeout=_coresignal_timeout())
        if resp.status_code not in (200, 201):
            body_excerpt = _clip(resp.text, 420)
            print(f"   ⚠️ [Company Evidence] Coresignal {method.upper()} {path} failed ({resp.status_code})", flush=True)
            if errors is not None:
                error_type = "payload_shape_error" if resp.status_code == 422 else (
                    "endpoint_config_error" if resp.status_code == 404 else "request_failed"
                )
                errors.append({
                    "method": method.upper(),
                    "path": path,
                    "status_code": resp.status_code,
                    "error_type": error_type,
                    "response_excerpt": body_excerpt,
                })
            return None
        if not resp.text.strip():
            return None
        return resp.json()
    except Exception as exc:
        print(f"   ⚠️ [Company Evidence] Coresignal {method.upper()} {path} exception: {exc}", flush=True)
        if errors is not None:
            errors.append({"method": method.upper(), "path": path, "exception": str(exc)})
        return None


def _result_values(data: Any) -> list[Any]:
    if data is None:
        return []
    if isinstance(data, list):
        return data
    if not isinstance(data, dict):
        return []
    for key in ("data", "results", "ids", "items", "records"):
        values = data.get(key)
        if isinstance(values, list):
            return values
        if isinstance(values, dict):
            nested = _result_values(values)
            if nested:
                return nested
    hits = data.get("hits")
    if isinstance(hits, dict) and isinstance(hits.get("hits"), list):
        return hits["hits"]
    return []


def _extract_ids(data: Any, limit: int) -> list[Any]:
    values = _result_values(data)
    ids = []
    for item in values:
        if isinstance(item, (str, int)):
            ids.append(item)
        elif isinstance(item, dict):
            source = item.get("_source") if isinstance(item.get("_source"), dict) else item
            item_id = (
                item.get("_id")
                or item.get("id")
                or source.get("id")
                or source.get("company_id")
                or source.get("member_id")
                or source.get("employee_id")
            )
            if item_id:
                ids.append(item_id)
        if len(ids) >= limit:
            break
    return ids


def _as_records(data: Any) -> list[dict]:
    values = _result_values(data)
    if values:
        records = []
        for item in values:
            if isinstance(item, dict):
                source_payload = item.get("_source")
                source = source_payload if isinstance(source_payload, dict) else item
                if source:
                    record = dict(source)
                    if item.get("_id") and not record.get("id"):
                        record["id"] = item["_id"]
                    records.append(record)
        return records
    if isinstance(data, dict):
        if isinstance(data.get("data"), dict):
            return [data["data"]]
        return [data]
    if isinstance(data, list):
        return [item for item in data if isinstance(item, dict)]
    return []


def _first_key(data: dict, keys: tuple[str, ...], default: Any = "") -> Any:
    for key in keys:
        if key in data and data.get(key) not in (None, "", []):
            return data.get(key)
    return default


def _flatten_text(value: Any, limit: int = 1000) -> str:
    if isinstance(value, dict):
        return _clip("; ".join(f"{k}: {_flatten_text(v, 160)}" for k, v in value.items() if v not in (None, "", [])), limit)
    if isinstance(value, list):
        return _clip("; ".join(_flatten_text(v, 160) for v in value[:12]), limit)
    return _clip(value, limit)


def _list_values(value: Any, limit: int = 12) -> list[str]:
    if isinstance(value, list):
        items = []
        for item in value:
            if isinstance(item, dict):
                extracted = _first_key(item, (
                    "technology", "name", "title", "category", "value", "url", "domain",
                ))
                if extracted:
                    items.append(_clip(extracted, 120))
            else:
                clipped = _clip(item, 120)
                if clipped:
                    items.append(clipped)
            if len(items) >= limit:
                break
        return items
    if isinstance(value, str):
        parts = [p.strip() for p in re.split(r"[,;|]", value) if p.strip()]
        return [_clip(part, 120) for part in parts[:limit]]
    if isinstance(value, dict):
        extracted = _first_key(value, ("technology", "name", "title", "category", "value", "domain"))
        return [_clip(extracted, 120)] if extracted else []
    return []


def _coresignal_domain(data: dict) -> str:
    domain_obj = data.get("domain") if isinstance(data.get("domain"), dict) else {}
    website = _first_key(data, (
        "website_domain", "website", "company_website", "homepage_url", "url", "website_url",
        "primary_domain", "canonical_domain",
    ))
    if not website:
        raw_domain = data.get("domain")
        website = _first_key(domain_obj, ("domain", "domainName", "url")) if isinstance(raw_domain, dict) else raw_domain
    return _host(str(website)) if website else ""


def _coresignal_country(data: dict) -> str:
    hq = data.get("headquarters") if isinstance(data.get("headquarters"), dict) else {}
    location = data.get("location") if isinstance(data.get("location"), dict) else {}
    return _clip(_first_key(data, (
        "country", "country_code", "headquarters_country", "hq_country", "hq_country_code",
    )) or _first_key(hq, ("country", "country_code", "country_name")) or _first_key(location, ("country", "country_code", "country_name")), 80).lower()


def _target_country_from_url(company_url: str) -> str:
    host = _host(company_url)
    tld = host.rsplit(".", 1)[-1] if "." in host else ""
    return {
        "fi": "finland",
        "se": "sweden",
        "no": "norway",
        "dk": "denmark",
        "ee": "estonia",
        "de": "germany",
        "fr": "france",
        "uk": "united kingdom",
    }.get(tld, "")


def _country_iso2(country: str) -> str:
    value = (country or "").strip().lower()
    return {
        "finland": "FI",
        "sweden": "SE",
        "norway": "NO",
        "denmark": "DK",
        "estonia": "EE",
        "germany": "DE",
        "france": "FR",
        "united kingdom": "GB",
    }.get(value, "")


def _country_matches(target_country: str, record_country: str) -> bool | None:
    target = (target_country or "").strip().lower()
    record = (record_country or "").strip().lower()
    if not target or not record:
        return None
    aliases = {
        "finland": {"finland", "fi", "suomi"},
        "sweden": {"sweden", "se", "sverige"},
        "norway": {"norway", "no", "norge"},
        "denmark": {"denmark", "dk", "danmark"},
        "estonia": {"estonia", "ee", "eesti"},
        "germany": {"germany", "de", "deutschland"},
        "united kingdom": {"united kingdom", "uk", "gb", "great britain"},
    }
    target_set = aliases.get(target, {target})
    record_set = aliases.get(record, {record})
    return bool(target_set & record_set or target in record or record in target)


def _norm_identifier(value: Any) -> str:
    return re.sub(r"[^a-z0-9]", "", str(value or "").lower())


def _coresignal_identifiers(data: dict) -> list[str]:
    return [_norm_identifier(value) for value in _walk_identity_values(data, IDENTIFIER_KEYS, max_depth=4) if _norm_identifier(value)]


def _coresignal_record_names(data: dict) -> list[str]:
    names = []
    for key in ("company_name", "name", *LEGAL_NAME_KEYS):
        value = data.get(key) if isinstance(data, dict) else None
        if isinstance(value, list):
            names.extend(value)
        elif value:
            names.append(value)
    return _dedupe_preserve(names)


def _public_profile_url(data: dict) -> str:
    return _normalize_url(_first_key(data, (
        "linkedin_url", "public_profile_url", "profile_url", "url", "professional_network_url",
        "company_linkedin_url", "website",
    )))


def _company_name_match(company_name: str, record_name: str, identity: dict | None = None) -> bool:
    if not record_name:
        return False
    record_l = record_name.lower()
    names = _list((identity or {}).get("names")) or [company_name]
    tokens = []
    for name in names:
        tokens.extend(_company_tokens(str(name)))
        if str(name).strip().lower() in record_l:
            return True
    tokens = list(dict.fromkeys(tokens))
    return any(token in record_l for token in tokens)


def _coresignal_search_payloads(company_name: str, domain: str, limit: int, target_country: str = "", identity: dict | None = None) -> list[tuple[str, dict]]:
    payloads: list[tuple[str, dict]] = []
    identity = identity or {}
    names = _list(identity.get("names")) or [company_name]
    domains = _list(identity.get("domains")) or ([domain] if domain else [])
    identifiers = _list(identity.get("identifiers"))
    target_iso2 = _country_iso2(target_country)
    if domain:
        payloads.append((
            "entity_domain_exact_query",
            {
                "query": {
                    "bool": {
                        "must": [
                            {"term": {"website_domain": domain}},
                        ],
                    }
                }
            },
        ))
    website_alias_should = []
    for candidate_domain in domains:
        if candidate_domain:
            website_alias_should.extend([
                {"match_phrase": {"website_alias": f"https://{candidate_domain}"}},
                {"match_phrase": {"website_alias": f"https://{candidate_domain}/"}},
                {"match_phrase": {"website_alias": f"https://www.{candidate_domain}"}},
                {"match_phrase": {"website_alias": f"https://www.{candidate_domain}/"}},
            ])
    if website_alias_should:
        payloads.append((
            "entity_website_alias_query",
            {
                "query": {
                    "bool": {
                        "should": website_alias_should,
                        "minimum_should_match": 1,
                    }
                }
            },
        ))
    if company_name:
        name_must = [{"match_phrase": {"company_name": company_name}}]
        if target_country:
            country_should = [{"match_phrase": {"hq_country": target_country}}]
            if target_iso2:
                country_should.append({"term": {"hq_country_iso2": target_iso2}})
            name_must.append({
                "bool": {
                    "should": country_should,
                    "minimum_should_match": 1,
                }
            })
        payloads.append((
            "entity_name_country_query" if target_country else "entity_name_query",
            {"query": {"bool": {"must": name_must}}},
        ))
    expanded_should = []
    for name in names:
        if name:
            expanded_should.append({"match_phrase": {"company_name": name}})
            expanded_should.append({"match_phrase": {"company_name_alias": name.lower()}})
    for identifier in identifiers:
        if identifier:
            expanded_should.extend([
                {"term": {"business_id": identifier}},
                {"term": {"registration_number": identifier}},
                {"term": {"company_registration_number": identifier}},
                {"term": {"vat_number": identifier}},
                {"term": {"lei": identifier}},
            ])
    expanded_should = _dedupe_preserve([json.dumps(item, sort_keys=True) for item in expanded_should])
    expanded_should = [json.loads(item) for item in expanded_should]
    if expanded_should:
        alias_bool = {
            "should": expanded_should,
            "minimum_should_match": 1,
        }
        if target_country:
            country_should = [{"match_phrase": {"hq_country": target_country}}]
            if target_iso2:
                country_should.append({"term": {"hq_country_iso2": target_iso2}})
            alias_bool["filter"] = [
                {
                    "bool": {
                        "should": country_should,
                        "minimum_should_match": 1,
                    }
                }
            ]
        payloads.append((
            "entity_alias_identifier_query",
            {"query": {"bool": alias_bool}},
        ))
    payloads.append((
        "entity_name_only_query",
        {
            "query": {
                "bool": {
                    "should": [
                        *[{"match_phrase": {"company_name": name}} for name in names if name],
                    ],
                    "minimum_should_match": 1,
                }
            }
        },
    ))
    return payloads


def _demand_profile(company_data: dict) -> dict:
    return demand_profile(company_data)


def _is_historical_role(title: str) -> bool:
    return is_historical_role(title)


def _stakeholder_role_context(title: str, demand_profile: dict) -> dict:
    return role_context(title, demand_profile)


def _coresignal_entity_decision(company_name: str, company_url: str, raw: dict, target_country: str = "", identity: dict | None = None) -> dict:
    identity = identity or {}
    official_host = _host(company_url)
    record_names = _coresignal_record_names(raw)
    record_name = record_names[0] if record_names else _clip(_first_key(raw, ("company_name", "name", "name_legal", "nameLegal", "legal_name")), 180)
    record_domain = _coresignal_domain(raw)
    record_country = _coresignal_country(raw)
    target_identifiers = {_norm_identifier(value) for value in _list(identity.get("identifiers")) if _norm_identifier(value)}
    record_identifiers = set(_coresignal_identifiers(raw))
    identifier_match = bool(target_identifiers and record_identifiers and target_identifiers & record_identifiers)
    domain_match = bool(record_domain and official_host and _same_or_subdomain(record_domain, official_host))
    domain_conflict = bool(record_domain and official_host and not _same_or_subdomain(record_domain, official_host))
    name_match = any(_company_name_match(company_name, name, identity) for name in ([record_name] + record_names))
    country_match = _country_matches(target_country, record_country)
    name_country_match = bool(name_match and not domain_conflict and (country_match is True or (not record_domain and country_match is None)))
    confirmed = bool(domain_match or identifier_match or name_country_match)
    if identifier_match:
        status = "exact"
        reason = "record_registration_identifier_matches_scanned_company"
    elif domain_conflict:
        status = "wrong"
        reason = f"record_domain_conflicts_with_scanned_domain:{record_domain}!={official_host}"
    elif domain_match:
        status = "exact"
        reason = "record_domain_matches_scanned_domain"
    elif name_country_match:
        status = "scoped"
        reason = "record_name_matches_scanned_company_and_country_scope_is_safe"
    elif name_match:
        status = "weak"
        reason = "record_name_matches_but_domain_or_country_confirmation_missing"
    else:
        status = "unknown"
        reason = "record_does_not_match_scanned_domain_or_company_name"
    return {
        "confirmed": confirmed,
        "status": status,
        "reason": reason,
        "record_name": record_name,
        "record_names": record_names[:8],
        "record_domain": record_domain,
        "record_country": record_country,
        "domain_match": domain_match,
        "domain_conflict": domain_conflict,
        "name_match": name_match,
        "country_match": country_match,
        "identifier_match": identifier_match,
        "record_identifiers": sorted(record_identifiers)[:8],
        "target_identifiers": sorted(target_identifiers)[:8],
    }


def _coresignal_enrich_path(domain: str) -> str:
    configured = _clean_env(os.environ.get("CORESIGNAL_COMPANY_ENRICH_PATH")) or "/cdapi/v2/company_multi_source/enrich"
    if "{website}" in configured:
        return configured.replace("{website}", domain)
    if "{domain}" in configured:
        return configured.replace("{domain}", domain)
    separator = "&" if "?" in configured else "?"
    return configured + separator + urlencode({"website": domain})


def _confirmed_coresignal_pairs(
    company_name: str,
    company_url: str,
    candidate_records: list[dict],
    target_country: str,
    identity: dict,
    mode: str,
    collected_pairs: list[tuple[Any, dict]] | None = None,
) -> tuple[list[tuple[Any, dict]], list[dict]]:
    confirmed_pairs = []
    rejection_samples = []
    collected_pairs = collected_pairs or []
    for idx, record in enumerate(candidate_records):
        decision = _coresignal_entity_decision(company_name, company_url, record, target_country, identity)
        if decision["confirmed"]:
            record_id = record.get("company_id") or (
                collected_pairs[idx][0]
                if idx < len(collected_pairs)
                else _first_key(record, ("id", "source_id"))
            )
            confirmed_pairs.append((record_id, record))
        elif len(rejection_samples) < 6:
            rejection_samples.append({
                "mode": mode,
                "record_name": decision.get("record_name"),
                "record_domain": decision.get("record_domain"),
                "record_country": decision.get("record_country"),
                "status": decision.get("status"),
                "reason": decision.get("reason"),
                "domain_match": decision.get("domain_match"),
                "name_match": decision.get("name_match"),
                "country_match": decision.get("country_match"),
                "identifier_match": decision.get("identifier_match"),
            })
    return confirmed_pairs, rejection_samples


def _coresignal_company_search(company_name: str, company_url: str, limit: int, company_data: dict | None = None) -> tuple[list[dict], list[Any], dict, list[str]]:
    warnings = []
    domain = _host(company_url)
    target_country = _target_country_from_url(company_url)
    identity = _company_identity_bundle(company_data or {}, company_name, company_url)
    enrich_path = _coresignal_enrich_path(domain) if domain else ""
    path = _clean_env(os.environ.get("CORESIGNAL_COMPANY_SEARCH_PATH")) or "/cdapi/v2/company_multi_source/search/es_dsl"
    collect_path = _clean_env(os.environ.get("CORESIGNAL_COMPANY_COLLECT_PATH")) or "/cdapi/v2/company_multi_source/collect/{id}"
    fallback_path = _clean_env(os.environ.get("CORESIGNAL_COMPANY_SEARCH_FALLBACK_PATH"))
    fallback_collect_path = _clean_env(os.environ.get("CORESIGNAL_COMPANY_COLLECT_FALLBACK_PATH")) or collect_path
    diagnostics = {
        "company_search_path": path,
        "company_enrich_path": enrich_path,
        "company_collect_path": collect_path,
        "company_search_mode": None,
        "company_enrich_queried": False,
        "company_ids_returned": 0,
        "company_records_returned": 0,
        "company_records_confirmed": 0,
        "company_records_rejected": 0,
        "company_records_collected": 0,
        "company_search_error_type": None,
        "company_request_errors": [],
        "identity_names_used": _list(identity.get("names"))[:18],
        "identity_domains_used": _list(identity.get("domains"))[:6],
        "identity_identifiers_used": _list(identity.get("identifiers"))[:10],
        "prh_aliases_used": _list(identity.get("prh_aliases_used"))[:8],
        "prh_aliases_skipped": _list(identity.get("prh_aliases_skipped"))[:12],
        "company_candidate_rejections": [],
    }
    records: list[dict] = []
    company_ids: list[Any] = []
    request_errors: list[dict] = []
    if enrich_path:
        raw_enrich = _coresignal_request("GET", enrich_path, errors=request_errors)
        enrich_records = _as_records(raw_enrich)[:limit]
        confirmed_pairs, rejection_samples = _confirmed_coresignal_pairs(
            company_name,
            company_url,
            enrich_records,
            target_country,
            identity,
            "domain_enrich",
        )
        diagnostics.update({
            "company_search_mode": "domain_enrich",
            "company_enrich_queried": True,
            "company_records_returned": len(enrich_records),
            "company_records_confirmed": len(confirmed_pairs),
            "company_records_rejected": max(0, len(enrich_records) - len(confirmed_pairs)),
            "company_records_collected": 0,
            "company_candidate_rejections": rejection_samples,
        })
        if confirmed_pairs:
            records = [record for _, record in confirmed_pairs][:limit]
            company_ids = [record_id for record_id, _record in confirmed_pairs if record_id][:limit]
            diagnostics["company_ids_returned"] = len(company_ids)
            diagnostics["company_request_errors"] = request_errors[:6]
            return records, company_ids, diagnostics, warnings

    search_attempts = [(path, collect_path)]
    if fallback_path:
        search_attempts.append((fallback_path, fallback_collect_path))
    seen_attempts = set()
    for search_path, current_collect_path in search_attempts:
        if (search_path, current_collect_path) in seen_attempts:
            continue
        seen_attempts.add((search_path, current_collect_path))
        for mode, payload in _coresignal_search_payloads(company_name, domain, limit, target_country, identity):
            raw = _coresignal_request("POST", search_path, payload=payload, errors=request_errors)
            candidate_ids = _extract_ids(raw, limit)
            candidate_records = _as_records(raw)
            collected_pairs = []
            for company_id in candidate_ids[:limit]:
                data = _coresignal_request("GET", current_collect_path.format(id=company_id), errors=request_errors)
                if isinstance(data, dict):
                    collected_pairs.append((company_id, data))
            if collected_pairs:
                candidate_records = [record for _, record in collected_pairs]
            elif candidate_ids and candidate_records:
                warnings.append("Coresignal collect returned no usable records; using search result records for normalization.")
            confirmed_pairs, rejection_samples = _confirmed_coresignal_pairs(
                company_name,
                company_url,
                candidate_records,
                target_country,
                identity,
                mode,
                collected_pairs,
            )
            confirmed = [record for _, record in confirmed_pairs]
            confirmed_ids = [record_id for record_id, _ in confirmed_pairs if record_id]
            diagnostics.update({
                "company_search_path": search_path,
                "company_collect_path": current_collect_path,
                "company_search_mode": mode,
                "company_ids_returned": len(candidate_ids),
                "company_records_returned": len(candidate_records),
                "company_records_confirmed": len(confirmed),
                "company_records_rejected": max(0, len(candidate_records) - len(confirmed)),
                "company_records_collected": len(collected_pairs),
                "company_candidate_rejections": rejection_samples,
            })
            if confirmed:
                records = confirmed[:limit]
                company_ids = confirmed_ids[:limit]
                break
        if records:
            break
    if not records:
        warnings.append("Coresignal enrichment unavailable/unconfirmed: no entity-confirmed Coresignal company record was found.")
        status_codes = {err.get("status_code") for err in request_errors if err.get("status_code")}
        error_types = [err.get("error_type") for err in request_errors if err.get("error_type")]
        diagnostics["company_search_error_type"] = error_types[0] if error_types else None
        if 422 in status_codes:
            warnings.append("Coresignal endpoint returned 422 payload_shape_error; verify the ES DSL request body against the Company API product.")
        if 404 in status_codes:
            warnings.append("Coresignal endpoint returned 404; verify CORESIGNAL_COMPANY_SEARCH_PATH/CORESIGNAL_COMPANY_COLLECT_PATH against the API product.")
        if diagnostics.get("company_records_returned"):
            warnings.append("Coresignal returned company candidates, but none could be safely matched to this company by domain, legal/name-country scope, or registration identifier.")
    diagnostics["company_request_errors"] = request_errors[:6]
    return records[:limit], company_ids[:limit], diagnostics, warnings


def _coresignal_employee_search(
    company_id: int, company_name: str, company_url: str, demand_profile: dict, max_people: int, start_idx: int,
) -> tuple[list[dict], dict, list[str]]:
    path = _clean_env(os.environ.get("CORESIGNAL_EMPLOYEE_SEARCH_PATH"))
    if not path or path == "/cdapi/v1/professional_network/employee/search/filter":
        path = "/cdapi/v2/employee_multi_source/search/es_dsl"
    collect_path = _clean_env(os.environ.get("CORESIGNAL_EMPLOYEE_COLLECT_PATH"))
    if not collect_path or collect_path == "/cdapi/v1/professional_network/employee/collect/{id}":
        collect_path = "/cdapi/v2/employee_multi_source/collect/{id}"
    # Reserve collection budget for every demand round. Never stop at display limit.
    rounds = search_rounds(demand_profile)
    budget = min(80, max(24, max_people * 4))
    diagnostics = {"employee_ids_returned": 0, "profiles_collected": 0, "collection_attempts": 0,
                   "collection_budget": budget, "search_rounds": [], "employee_request_errors": []}
    errors: list[dict] = []
    candidates, seen_ids, returned_unique_ids = [], set(), set()
    fields = urlencode({"fields": ("id", "full_name", "professional_network_url", "active_experience_title", "active_experience_company_id")}, doseq=True)
    separator = "&" if "?" in collect_path else "?"
    stop = False
    for round_idx, (round_name, role_query) in enumerate(rounds):
        # Role-family clauses are mandatory, not optional scoring suggestions.
        payload = {"query": {"bool": {
            "filter": [{"term": {"active_experience_company_id": company_id}}],
            "must": [role_query],
        }}, "sort": ["_score"]}
        raw = _coresignal_request("POST", path, payload=payload, errors=errors)
        returned_ids = _extract_ids(raw, max(1, len(_result_values(raw))))
        people_ids = list(dict.fromkeys(str(pid) for pid in returned_ids if re.fullmatch(r"[1-9][0-9]*", str(pid))))
        round_stats = {"round": round_name, "employee_ids_returned": len(returned_ids), "unique_ids_returned": len(people_ids),
                       "profiles_collected": 0, "collection_attempts": 0, "rejected_by_reason": {}}
        diagnostics["search_rounds"].append(round_stats)
        diagnostics["employee_ids_returned"] += len(returned_ids)
        returned_unique_ids.update(people_ids)
        remaining = budget - diagnostics["collection_attempts"]
        quota = max(1, remaining // (len(rounds) - round_idx))
        if round_idx == 0:
            quota = min(remaining, max(quota, min(8, budget // 2)))
        if errors and errors[-1].get("status_code") in {401, 402, 403, 429}:
            stop = True
        for pid in people_ids:
            if stop or round_stats["collection_attempts"] >= quota or diagnostics["collection_attempts"] >= budget:
                break
            if pid in seen_ids:
                continue
            seen_ids.add(pid)
            round_stats["collection_attempts"] += 1
            diagnostics["collection_attempts"] += 1
            data = _coresignal_request("GET", collect_path.format(id=pid) + separator + fields, errors=errors)
            if not isinstance(data, dict):
                reason = "collection_failed"
                if errors and errors[-1].get("status_code") in {401, 402, 403, 429}:
                    stop = True
            else:
                round_stats["profiles_collected"] += 1
                diagnostics["profiles_collected"] += 1
                person = _coresignal_person(data, verified=str(data.get("active_experience_company_id")) == str(company_id), basis="active_company_id")
                candidates.append(person)
                reason = candidate_reason(person, demand_profile)
            if reason:
                counts = round_stats["rejected_by_reason"]
                counts[reason] = counts.get(reason, 0) + 1
        round_stats["uncollected_ids"] = sum(pid not in seen_ids for pid in people_ids)
        if stop:
            diagnostics["stopped_on_provider_error"] = True
            break
    diagnostics["employee_unique_ids_returned"] = len(returned_unique_ids)
    diagnostics["collection_failed"] = diagnostics["collection_attempts"] - diagnostics["profiles_collected"]
    diagnostics["employee_request_errors"] = [{"method": e.get("method"), "status_code": e.get("status_code"), "error_type": e.get("error_type")} for e in errors]
    warnings = [f"Coresignal employee {method} failed ({status})." for method, status in dict.fromkeys(
        (e.get("method"), e.get("status_code") or "request error") for e in errors)]
    return candidates, diagnostics, warnings


def _coresignal_person(raw: dict, *, verified: bool, basis: str) -> dict:
    return {
        "name": _clip(_first_key(raw, ("name", "full_name", "member_full_name", "person_name")), 160),
        "role": _clip(_first_key(raw, ("active_experience_title", "title", "current_title", "job_title", "position", "role", "member_position_title")), 180),
        "public_profile_url": _public_profile_url(raw),
        "provider_person_id": _first_key(raw, ("provider_person_id", "parent_id", "id", "member_id", "person_id")),
        "provider": "coresignal",
        "current_affiliation_verified": verified and raw.get("is_current") is not False and raw.get("current_affiliation_verified") is not False,
        "affiliation_basis": basis,
        "allowed_use": "allowed_as_discovery_question",
    }


def _coresignal_company_record(idx: int, company_name: str, company_url: str, raw: dict, target_country: str = "", identity: dict | None = None) -> dict:
    official_host = _host(company_url)
    decision = _coresignal_entity_decision(company_name, company_url, raw, target_country, identity)
    record_name = decision["record_name"]
    record_domain = decision["record_domain"]
    raw_domain = raw.get("domain")
    public_source = _first_key(raw, ("website_domain", "website", "company_website", "homepage_url", "url"))
    if not public_source:
        public_source = _first_key(raw_domain, ("domain", "domainName", "url")) if isinstance(raw_domain, dict) else raw_domain
    public_url = _normalize_url(public_source or company_url)
    entity_verified = bool(decision["confirmed"])
    blocked = bool(decision["status"] == "wrong")
    industries = _list_values(_first_key(raw, ("industries", "industry", "categories", "category")))
    technologies = _list_values(_first_key(raw, (
        "technologies_used", "technologies", "technology", "tech_stack", "technographics",
    )))
    quote = _clip(
        f"Coresignal structured company record: name={record_name or company_name}; "
        f"domain={record_domain or official_host}; industry={', '.join(industries[:5])}; "
        f"employees={_first_key(raw, ('employees_count', 'totalEmployeesExact', 'employees', 'employee_count', 'total_employees', 'size_range', 'size'))}; "
        f"revenue={_first_key(raw, ('revenue', 'revenue_range', 'finances'))}.",
        620,
    )
    sales_use = "blocked" if blocked else ("allowed_as_hypothesis" if entity_verified else "internal_only")
    content_hash = hashlib.sha256(_flatten_text(raw, 6000).encode("utf-8", errors="ignore")).hexdigest()
    return {
        "id": f"CED-{idx:02d}",
        "company_name": company_name,
        "company_url": company_url,
        "url": public_url or company_url,
        "provider": "coresignal",
        "provider_record_type": "company_enrichment",
        "title": f"Coresignal company enrichment: {record_name or company_name}",
        "fetched_at": datetime.utcnow().isoformat(),
        "content_hash": content_hash,
        "extraction_confidence": 72 if entity_verified else 35,
        "source_freshness": "coresignal_snapshot",
        "text_excerpt": quote,
        "source_category": "company_enrichment",
        "source_type": "coresignal_company_enrichment",
        "target_fields": ["legal name", "domain", "country", "industry", "size band", "technology tags"],
        "context_only": False,
        "canonical_source_host": record_domain or official_host,
        "source_quote": quote,
        "url_verified": bool(public_url),
        "content_verified": True,
        "page_not_found": False,
        "entity_verified": entity_verified,
        "entity_match_status": decision["status"],
        "entity_match_reason": decision["reason"],
        "claim_support_status": "supported" if entity_verified and not blocked else "unchecked",
        "analysis_verified": False,
        "generation_eligible": False,
        "buying_evidence_eligible": bool(entity_verified and not blocked),
        "promotion_status": "display_only",
        "verification_reason": decision["reason"],
        "signal_markers": {
            "has_company_enrichment": True,
            "has_technology_service_language": bool(technologies),
            "has_trigger_event_language": bool(_first_key(raw, ("funding", "funding_events", "acquisitions", "company_updates"))),
        },
        "allowed_as_fact": False,
        "allowed_as_hypothesis": bool(entity_verified and not blocked),
        "allowed_as_discovery_question": bool(entity_verified and not blocked),
        "internal_only": not entity_verified and not blocked,
        "blocked": blocked,
        "sales_use": sales_use,
        "wrong_entity": blocked,
        "coresignal_fields": {
            "record_name": record_name,
            "legal_name": _clip(_first_key(raw, ("company_legal_name", "legal_name", "name_legal", "nameLegal")), 180),
            "record_domain": record_domain,
            "country": decision["record_country"] or _first_key(raw, ("country", "country_code", "headquarters_country")),
            "size_range": _clip(_first_key(raw, ("size_range", "size", "employees")), 120),
            "employees_count": _first_key(raw, ("employees_count", "totalEmployeesExact", "employee_count", "total_employees")),
            "target_country": target_country,
            "identity_names_used": _list((identity or {}).get("names"))[:18],
            "identity_identifiers_used": _list((identity or {}).get("identifiers"))[:10],
            "industry": industries[:8],
            "technologies": technologies[:12],
            "social_profiles": (
                _list_values(_first_key(raw, ("socials", "social_profiles", "social_media")), 8)
                + _list_values(_first_key(raw, ("linkedin_url", "canonical_linkedin_url", "crunchbase_url")), 4)
            )[:10],
            "company_updates": [
                {
                    "date": _clip(update.get("date"), 40),
                    "description": _clip(update.get("description"), 420),
                }
                for update in _list(raw.get("company_updates"))[:6]
                if isinstance(update, dict) and update.get("description")
            ],
            "key_executives": [
                {
                    "name": _clip(_first_key(executive, ("member_full_name", "name", "full_name", "person_name")), 160),
                    "title": _clip(_first_key(executive, ("member_position_title", "title", "current_title", "job_title", "position", "role")), 180),
                    "parent_id": _first_key(executive, ("parent_id", "id", "member_id", "person_id")),
                    "is_current": executive.get("is_current"),
                    "current_affiliation_verified": executive.get("current_affiliation_verified"),
                    "public_profile_url": _public_profile_url(executive),
                }
                for executive in _list(raw.get("key_executives"))
                if isinstance(executive, dict)
            ],
        },
    }


def _coresignal_update_records(idx: int, company_name: str, company_url: str, company_record: dict) -> list[dict]:
    fields = company_record.get("coresignal_fields") or {}
    updates = [update for update in _list(fields.get("company_updates")) if isinstance(update, dict) and update.get("description")]
    if not updates:
        return []
    records = []
    for update in updates[:5]:
        date = _clip(update.get("date"), 40)
        description = _clip(update.get("description"), 720)
        quote = _clip(
            f"Coresignal company update for {company_name}"
            f"{f' ({date})' if date else ''}: {description}. "
            "Treat as a sales-prep trigger candidate, not proof of a buying project.",
            760,
        )
        record = dict(company_record)
        record.update({
            "id": f"CED-{idx + len(records):02d}",
            "provider_record_type": "company_update_trigger",
            "title": f"Coresignal recent update: {company_name}{f' ({date})' if date else ''}",
            "source_category": "strategy_trigger_event",
            "source_type": "coresignal_company_update",
            "target_fields": ["recent company update", "public trigger", "timing signal"],
            "source_quote": quote,
            "text_excerpt": quote,
            "content_hash": hashlib.sha256(_flatten_text({"date": date, "description": description}, 1200).encode("utf-8", errors="ignore")).hexdigest(),
            "allowed_as_fact": False,
            "allowed_as_hypothesis": True,
            "allowed_as_discovery_question": True,
            "sales_use": "allowed_as_hypothesis",
            "verification_reason": "coresignal_company_update_entity_matched_sales_prep_signal",
            "signal_markers": {
                **(record.get("signal_markers") if isinstance(record.get("signal_markers"), dict) else {}),
                "has_trigger_event_language": True,
                "has_company_update": True,
            },
            "coresignal_update": {
                "date": date,
                "description": description,
            },
        })
        records.append(record)
    return records


def _coresignal_technology_record(idx: int, company_name: str, company_url: str, company_record: dict) -> dict | None:
    fields = company_record.get("coresignal_fields") or {}
    technologies = _list(fields.get("technologies"))
    if not technologies:
        return None
    record = dict(company_record)
    record.update({
        "id": f"CED-{idx:02d}",
        "provider_record_type": "technology_signal",
        "title": f"Coresignal technology signals: {company_name}",
        "source_category": "technology_service_fit",
        "source_type": "coresignal_technology_signal",
        "target_fields": ["cloud", "ERP", "AI", "cybersecurity", "workplace", "automation"],
        "source_quote": _clip(f"Coresignal technology tags for {company_name}: {', '.join(technologies[:12])}. Treat as sales-prep signal, not proof of an active project.", 620),
        "text_excerpt": _clip(f"Technology tags: {', '.join(technologies[:12])}", 620),
        "allowed_as_fact": False,
        "allowed_as_hypothesis": True,
        "allowed_as_discovery_question": True,
        "sales_use": "allowed_as_hypothesis",
        "verification_reason": "coresignal_technology_tags_entity_matched" if company_record.get("entity_verified") else "coresignal_technology_tags_entity_unconfirmed",
    })
    return record


def _coresignal_stakeholder_record(
    idx: int,
    company_name: str,
    company_url: str,
    raw: dict,
    demand_profile: dict,
) -> dict | None:
    name = _clip(_first_key(raw, ("name", "full_name", "member_full_name", "person_name")), 160)
    title = _clip(_first_key(raw, ("active_experience_title", "title", "current_title", "job_title", "position", "role", "member_position_title")), 180)
    if not name or not title:
        return None
    role_context = _stakeholder_role_context(title, demand_profile)
    if not role_context.get("matched"):
        return None
    matched_groups = _list(role_context.get("groups"))
    reason = role_context.get("reason") or "role_matches_detected_demand"
    is_historical = bool(role_context.get("historical"))
    profile_url = _public_profile_url(raw)
    public_url = profile_url or _normalize_url(company_url)
    quote = _clip(
        f"Coresignal public professional profile candidate: {name}, {title}. "
        f"Matched demand groups: {', '.join(matched_groups) if matched_groups else 'historical_context_only'}. "
        f"Use as a role-relevance hypothesis, not as proof of a buying project.",
        620,
    )
    content_hash = hashlib.sha256(_flatten_text({"name": name, "title": title, "profile_url": profile_url}, 1200).encode("utf-8", errors="ignore")).hexdigest()
    sales_use = "internal_only" if is_historical else "allowed_as_discovery_question"
    return {
        "id": f"CED-{idx:02d}",
        "company_name": company_name,
        "company_url": company_url,
        "url": public_url,
        "provider": "coresignal",
        "provider_record_type": "stakeholder_candidate",
        "title": f"Coresignal stakeholder candidate: {name}",
        "fetched_at": datetime.utcnow().isoformat(),
        "content_hash": content_hash,
        "extraction_confidence": 68,
        "source_freshness": "coresignal_snapshot",
        "text_excerpt": quote,
        "source_category": "stakeholder_candidate",
        "source_type": "coresignal_stakeholder_candidate",
        "target_fields": ["public professional profile", "role", "buying-committee relevance"],
        "context_only": False,
        "canonical_source_host": _host(public_url),
        "source_quote": quote,
        "url_verified": bool(public_url),
        "content_verified": True,
        "page_not_found": False,
        "entity_verified": True,
        "entity_match_status": "scoped",
        "claim_support_status": "supported",
        "analysis_verified": False,
        "generation_eligible": False,
        "buying_evidence_eligible": not is_historical,
        "promotion_status": "display_only",
        "verification_reason": "coresignal_public_profile_role_match",
        "signal_markers": {
            "has_stakeholder_candidate": True,
            "has_detected_demand_role_match": bool(matched_groups),
            "historical_or_retired_role": is_historical,
        },
        "allowed_as_fact": False,
        "allowed_as_hypothesis": False,
        "allowed_as_discovery_question": not is_historical,
        "internal_only": is_historical,
        "blocked": False,
        "sales_use": sales_use,
        "wrong_entity": False,
        "stakeholder": {
            "name": name,
            "role": title,
            "public_profile_url": profile_url,
            "provider_person_id": _first_key(raw, ("provider_person_id", "parent_id", "id", "member_id", "person_id")),
            "current_affiliation_verified": raw.get("current_affiliation_verified") is True,
            "affiliation_basis": raw.get("affiliation_basis"),
            "seniority_tier": role_context.get("seniority_tier"),
            "matched_demand_groups": matched_groups,
            "stakeholder_role_match_reason": reason,
            "demand_signal_ids": matched_groups,
            "linked_why_what_ids": [],
            "linked_service_ids": [],
            "allowed_use": sales_use,
        },
        "demand_signal_ids": matched_groups,
        "linked_why_what_ids": [],
        "linked_service_ids": [],
        "stakeholder_role_match_reason": reason,
    }


def _coresignal_records(company_data: dict, start_idx: int = 1, *, stage_callback=None) -> tuple[list[dict], dict, list[str]]:
    diagnostics = {
        "enabled": _coresignal_enabled(),
        "queried": False,
        "company_records": 0,
        "technology_records": 0,
        "stakeholder_candidates": 0,
        "employee_search_attempted": False,
        "blocked_or_unmatched": 0,
        "base_url": _coresignal_base_url() if _coresignal_enabled() else None,
        "entity_first": True,
        "company_search_mode": None,
        "company_search_path": None,
        "company_ids_returned": 0,
        "company_records_confirmed": 0,
        "company_records_rejected": 0,
    }
    warnings: list[str] = []
    if not _coresignal_enabled():
        diagnostics["reason"] = "CORESIGNAL_API_KEY missing or CORESIGNAL_ENABLED=false"
        return [], diagnostics, warnings

    company_name = company_data.get("companyName") or company_data.get("company_name")
    company_url = company_data.get("companyUrl") or company_data.get("url")
    max_companies = max(1, min(5, int(os.environ.get("CORESIGNAL_MAX_COMPANY_RESULTS", "2"))))
    max_stakeholders = max(0, min(20, int(os.environ.get("CORESIGNAL_MAX_STAKEHOLDERS", "8"))))
    if stage_callback:
        stage_callback("coresignal_enrichment", 62, "Querying Coresignal company enrichment and public stakeholder candidates")

    diagnostics["queried"] = True
    records: list[dict] = []
    identity = _company_identity_bundle(company_data, company_name, company_url)
    company_items, company_ids, company_search_diagnostics, company_warnings = _coresignal_company_search(company_name, company_url, max_companies, company_data)
    diagnostics.update(company_search_diagnostics)
    warnings.extend(company_warnings)
    target_country = _target_country_from_url(company_url)
    for raw in company_items:
        company_record = _coresignal_company_record(start_idx + len(records), company_name, company_url, raw, target_country, identity)
        records.append(company_record)
        update_records = _coresignal_update_records(start_idx + len(records), company_name, company_url, company_record)
        records.extend(update_records)
        tech_record = _coresignal_technology_record(start_idx + len(records), company_name, company_url, company_record)
        if tech_record:
            records.append(tech_record)

    profile = _demand_profile(company_data)
    candidates, stakeholders = [], []
    diagnostics.update({"executives_received": sum(len(_list(r.get("key_executives"))) for r in company_items),
                        "employee_ids_returned": 0, "profiles_collected": 0, "search_rounds": [],
                        "demand_groups": profile["groups"]})
    if max_stakeholders > 0:
        for company_record in [r for r in records if r.get("provider_record_type") == "company_enrichment"]:
            for raw in _list(company_record.get("coresignal_fields", {}).get("key_executives")):
                candidates.append(_coresignal_person(raw, verified=company_record.get("entity_verified") is True, basis="confirmed_company_executive_roster"))
        company_id = next((int(value) for value in company_ids if re.fullmatch(r"[1-9][0-9]*", str(value))), None)
        if company_id:
            diagnostics["employee_search_attempted"] = True
            employees, employee_diagnostics, employee_warnings = _coresignal_employee_search(
                company_id, company_name, company_url, profile, max_stakeholders, start_idx + len(records),
            )
            candidates.extend(employees)
            diagnostics.update(employee_diagnostics)
            warnings.extend(employee_warnings)
        else:
            warnings.append("Coresignal employee search skipped because no entity-confirmed numeric company ID was available.")
        size = employee_count(company_data) or next((employee_count(raw) for raw in company_items if employee_count(raw)), None)
        ranked, ranking = rank_stakeholders(candidates, profile, employees=size, limit=max_stakeholders)
        diagnostics.update(ranking)
        diagnostics["employees_for_ranking"] = size
        diagnostics["blocked_or_unmatched"] = sum(ranking["rejected_by_reason"].values())
        for person in ranked:
            record = _coresignal_stakeholder_record(start_idx + len(records) + len(stakeholders), company_name, company_url, person, profile)
            if record:
                stakeholders.append(record)
    print("[Coresignal stakeholders] " + json.dumps({"company": company_name, **{key: diagnostics.get(key) for key in (
        "executives_received", "employee_ids_returned", "employee_unique_ids_returned", "profiles_collected", "collection_attempts", "collection_failed", "collection_budget",
        "search_rounds", "rejected_by_reason", "candidates_retained", "candidates_selected", "display_limit_excluded",
    )}}, ensure_ascii=False))
    # Persist negative affiliation evidence so stale titles in another source
    # cannot resurrect a rejected person during report generation.
    diagnostics["stakeholder_conflicts"] = [
        {key: p.get(key) for key in ("name", "role", "public_profile_url", "provider_person_id", "provider", "current_affiliation_verified", "affiliation_basis")}
        for p in candidates if p.get("current_affiliation_verified") is False or is_historical_role(p.get("role", ""))
    ]
    for record in records:
        if record.get("provider_record_type") == "company_enrichment":
            record["stakeholder_selection_applied"] = True
    records.extend(stakeholders)
    diagnostics["company_records"] = sum(1 for r in records if r.get("provider_record_type") == "company_enrichment")
    diagnostics["technology_records"] = sum(1 for r in records if r.get("provider_record_type") == "technology_signal")
    diagnostics["company_update_triggers"] = sum(1 for r in records if r.get("provider_record_type") == "company_update_trigger")
    diagnostics["stakeholder_candidates"] = sum(1 for r in records if r.get("provider_record_type") == "stakeholder_candidate")
    if diagnostics["queried"] and not records:
        warnings.append("Coresignal was queried but produced no normalized evidence records.")
    return records, diagnostics, warnings


def _stats(records: list[dict]) -> dict:
    source_categories = {}
    sales_use_counts = {}
    provider_record_types = {}
    outbound_categories = {}
    for record in records:
        category = record.get("source_category") or "unknown"
        source_categories[category] = source_categories.get(category, 0) + 1
        sales_use = record.get("sales_use") or "unknown"
        sales_use_counts[sales_use] = sales_use_counts.get(sales_use, 0) + 1
        provider_record_type = record.get("provider_record_type")
        if provider_record_type:
            provider_record_types[provider_record_type] = provider_record_types.get(provider_record_type, 0) + 1
        if record.get("origin_relation") == "official_page_outbound_reference":
            outbound_categories[category] = outbound_categories.get(category, 0) + 1
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
        "provider_record_types": provider_record_types,
        "providers": sorted({r.get("provider") for r in records if r.get("provider")}),
        "coresignal_records": sum(1 for r in records if r.get("provider") == "coresignal"),
        "coresignal_company_records": sum(1 for r in records if r.get("provider_record_type") == "company_enrichment"),
        "coresignal_technology_records": sum(1 for r in records if r.get("provider_record_type") == "technology_signal"),
        "coresignal_stakeholder_candidates": sum(1 for r in records if r.get("provider_record_type") == "stakeholder_candidate"),
        "coresignal_sales_usable": sum(1 for r in records if r.get("provider") == "coresignal" and r.get("sales_use") in {"allowed_as_hypothesis", "allowed_as_discovery_question"}),
        "outbound_reference_records": sum(1 for r in records if r.get("origin_relation") == "official_page_outbound_reference"),
        "outbound_reference_categories": outbound_categories,
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
    official_triggers = categories.get("strategy_trigger_events", 0)
    coresignal_triggers = categories.get("strategy_trigger_event", 0)
    if not official_triggers and coresignal_triggers:
        warnings.append("No official-domain strategy/newsroom trigger sources found; Coresignal updates are available as weak trigger signals.")
    elif not official_triggers:
        warnings.append("No recent strategy/newsroom trigger sources found.")
    if not categories.get("technology_service_fit"):
        warnings.append("No technology or service-fit sources found.")
    if not categories.get("stakeholder_evidence") and not categories.get("stakeholder_candidate"):
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
            f"<td>{html.escape(rec.get('origin_relation') or '')}</td>"
            f"<td>{html.escape(rec.get('publisher_independence') or '')}</td>"
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
<span class="pill">Coresignal: {stats.get('coresignal_records', 0)}</span>
<span class="pill">Stakeholders: {stats.get('coresignal_stakeholder_candidates', 0)}</span>
<span class="pill">Outbound refs: {stats.get('outbound_reference_records', 0)}</span>
<span class="pill">404 / not found: {stats.get('page_not_found', 0)}</span>
<span class="pill">Display-only / blocked: {stats.get('display_only_or_blocked', 0)}</span>
</div>
{"<ul>" + warnings + "</ul>" if warnings else ""}
<table><thead><tr><th>ID</th><th>Category</th><th>Type</th><th>Source</th><th>Origin</th><th>Independence</th><th>Provider</th><th>Sales use</th><th>Reason</th></tr></thead>
<tbody>{''.join(rows)}</tbody></table>
</body></html>"""


def build_company_evidence_database(company_data: dict, *, stage_callback=None) -> tuple[dict, str]:
    company_name = company_data.get("companyName") or company_data.get("company_name")
    company_url = company_data.get("companyUrl") or company_data.get("url")
    if not company_name:
        raise CompanyEvidenceError("Company name missing from stored data.", 404)
    if not company_url:
        raise CompanyEvidenceError("Company URL missing from stored data; cannot build official-domain evidence.", 409)
    identity_enrichment = _fetch_prh_identity(company_name, company_url)
    enriched_company_data = _merge_identity_enrichment(company_data, identity_enrichment)
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
        stage_callback("discover_outbound_references", 55, "Discovering official-page outbound reference candidates")
    outbound_records, outbound_diagnostics = _discover_outbound_references(
        records,
        company_name,
        company_url,
        start_idx=len(records) + 1,
    )
    records.extend(outbound_records)
    coresignal_records, coresignal_diagnostics, coresignal_warnings = _coresignal_records(
        enriched_company_data,
        start_idx=len(records) + 1,
        stage_callback=stage_callback,
    )
    records.extend(coresignal_records)
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
            "official_outbound_reference_discovery_enabled": bool(outbound_diagnostics.get("enabled")),
            "coresignal_enabled": bool(coresignal_diagnostics.get("enabled")),
            "coresignal_queried": bool(coresignal_diagnostics.get("queried")),
        },
        "acquisition_policy": {
            "mode": "official_domain_source_target_registry_plus_outbound_references_plus_optional_coresignal",
            "max_urls": max_urls,
            "official_outbound_reference_discovery": bool(outbound_diagnostics.get("enabled")),
            "max_outbound_references": max(0, min(30, int(os.environ.get("COMPANY_EVIDENCE_MAX_OUTBOUND_REFERENCES", "12")))),
            "broad_web_search": False,
            "recursive_external_crawling": False,
            "social_scraping": False,
            "contact_scraping": False,
            "coresignal_enabled": bool(coresignal_diagnostics.get("enabled")),
            "coresignal_stakeholder_scope": "public_profiles_no_emails_or_phones",
            "source_target_categories": sorted({target["category"] for target in SOURCE_TARGET_REGISTRY}),
        },
        "provider_status": {
            "coresignal": coresignal_diagnostics,
            "prh": identity_enrichment,
            "official_outbound_references": outbound_diagnostics,
        },
        "records": records,
        "stats": stats,
        "warnings": [],
    }
    if not _firecrawl_headers():
        database["warnings"].append("FIRECRAWL_API_KEY not configured; used Jina/direct official-domain fallback only.")
    if not coresignal_diagnostics.get("enabled"):
        database["warnings"].append("CORESIGNAL_API_KEY not configured or Coresignal disabled; skipped structured enrichment.")
    database["warnings"].extend(coresignal_warnings)
    database["warnings"].extend(_warnings(records, stats))
    return database, render_company_evidence_html(database)
