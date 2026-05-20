"""
Curated market cohorts for Market Intelligence reporting.

These cohorts do not change scanning, scoring, or global aggregation. They only
filter already-scanned companies into dedicated reporting sheets.
"""

from __future__ import annotations

import re
from urllib.parse import urlparse


SWEDEN_SUMMARY_COMPANIES = [
    {"name": "Länsförsäkringar", "url": "https://www.lansforsakringar.se/"},
    {"name": "Swedbank", "url": "https://www.swedbank.se/"},
    {"name": "Handelsbanken", "url": "https://www.handelsbanken.se/"},
    {"name": "Sveriges Riksbank", "url": "https://www.riksbank.se/"},
    {"name": "Avtalat", "url": "https://www.avtalat.se/"},
    {"name": "SBAB", "url": "https://www.sbab.se/"},
    {"name": "Landshypotek Bank", "url": "https://www.landshypotek.se/"},
    {"name": "Resurs Bank", "url": "https://www.resursbank.se/"},
    {"name": "Svea Bank", "url": "https://www.svea.com/"},
    {"name": "TF Bank", "url": "https://www.tfbank.se/"},
    {"name": "Forex", "url": "https://www.forex.se/"},
    {"name": "AMF Pension", "url": "https://www.amf.se/"},
    {"name": "Dina Försäkringar", "url": "https://www.dina.se/"},
    {"name": "Skandia", "url": "https://www.skandia.se/"},
    {"name": "Almi Företagspartner", "url": "https://www.almi.se/"},
    {"name": "Svensk Exportkredit", "url": "https://www.sek.se/"},
    {"name": "SEB", "url": "https://seb.se/"},
    {"name": "Entity Bank Group", "url": "https://www.enity.com/"},
    {"name": "Norion Bank Group", "url": "https://www.norionbank.se/"},
    {"name": "Ica Banken", "url": "https://www.icabanken.se/"},
    {"name": "Ikano Bank", "url": "https://www.ikanobank.se/"},
    {"name": "Klarna", "url": "https://www.klarna.com/"},
    {"name": "Marginalen Bank", "url": "https://www.marginalen.se/"},
    {"name": "Nordax Bank", "url": "https://www.nordax.se/"},
    {"name": "Nordnet", "url": "https://www.nordnet.se/"},
    {"name": "Avanza", "url": "https://www.avanza.se/start"},
    {"name": "Bliwa", "url": "https://www.bliwa.se/"},
    {"name": "SPP", "url": "https://www.spp.se/"},
    {"name": "Nasdaq", "url": "https://www.nasdaq.com/"},
    {"name": "Intrum", "url": "https://www.intrum.se/"},
    {"name": "EQT", "url": "https://www.eqtgroup.com/"},
    {"name": "Flat Capital", "url": "https://www.flatcapital.com/"},
    {"name": "Industrivärden", "url": "https://www.industrivarden.se/"},
    {"name": "Investor", "url": "https://www.investorab.com/"},
    {"name": "Kinnevik", "url": "https://www.kinnevik.com/"},
    {"name": "Folksam", "url": "https://www.folksam.se/"},
    {"name": "IF", "url": "https://www.if.se/"},
    {"name": "Bankgirot", "url": "https://www.bankgirot.se/"},
    {"name": "Ziklo", "url": "https://www.ziklo.com/"},
    {"name": "ICS", "url": "https://www.icscards.nl/abnamrogb/abnamrogb-info/about-ics"},
    {"name": "MinPension", "url": "https://www.minpension.se/"},
    {"name": "Entercard", "url": "https://www.entercard.se/"},
    {"name": "Getswish", "url": "https://www.swish.nu/"},
]


def normalize_company_name(name: str | None) -> str:
    """Normalize a company name for exact cohort matching."""
    return re.sub(r"\s+", " ", str(name or "").strip()).casefold()


def normalize_url_host(url: str | None) -> str:
    """Normalize URL to lowercase host, ignoring path, trailing slash, and www."""
    raw = str(url or "").strip()
    if not raw:
        return ""
    if "://" not in raw:
        raw = f"https://{raw}"
    parsed = urlparse(raw)
    host = (parsed.netloc or parsed.path.split("/")[0] or "").casefold().strip(".")
    if "@" in host:
        host = host.rsplit("@", 1)[-1]
    if ":" in host:
        host = host.split(":", 1)[0]
    if host.startswith("www."):
        host = host[4:]
    return host


def _cohort_key(name: str | None, url: str | None) -> tuple[str, str]:
    return normalize_company_name(name), normalize_url_host(url)


def _stored_company_url(summary: dict, detail: dict | None) -> str:
    return (
        (detail or {}).get("companyUrl")
        or (detail or {}).get("url")
        or (summary or {}).get("companyUrl")
        or (summary or {}).get("url")
        or ""
    )


def compute_sweden_summary_cohort(
    dashboard_data: list[dict],
    company_details: list[dict],
    opportunity_map: dict,
    favi_companies: list[dict],
) -> dict:
    """Build Sweden-only Market Intelligence rows from already-computed data."""
    details_by_name = {
        normalize_company_name(d.get("companyName") or d.get("name")): d
        for d in (company_details or [])
        if isinstance(d, dict)
    }
    stored_by_key = {}
    for summary in dashboard_data or []:
        if not isinstance(summary, dict):
            continue
        name = summary.get("companyName") or summary.get("name")
        detail = details_by_name.get(normalize_company_name(name))
        url = _stored_company_url(summary, detail)
        key = _cohort_key(name, url)
        if key[0] and key[1]:
            stored_by_key[key] = {
                "companyName": name,
                "companyUrl": url,
                "domain": summary.get("domain") or (detail or {}).get("domain"),
                "lastUpdated": summary.get("lastUpdated") or (detail or {}).get("lastUpdated"),
            }

    matched = []
    missing = []
    matched_name_keys = set()
    for entry in SWEDEN_SUMMARY_COMPANIES:
        key = _cohort_key(entry["name"], entry["url"])
        stored = stored_by_key.get(key)
        if stored:
            matched_name_keys.add(normalize_company_name(stored["companyName"]))
            matched.append({
                "name": entry["name"],
                "url": entry["url"],
                "scanned_name": stored["companyName"],
                "scanned_url": stored["companyUrl"],
                "domain": stored.get("domain"),
                "lastUpdated": stored.get("lastUpdated"),
            })
        else:
            missing.append({"name": entry["name"], "url": entry["url"]})

    opportunities = [
        row for row in (opportunity_map or {}).get("company_rankings", [])
        if normalize_company_name(row.get("name")) in matched_name_keys
    ]
    favi_rows = [
        row for row in (favi_companies or [])
        if normalize_company_name(row.get("name")) in matched_name_keys
    ]

    return {
        "label": "SWEDEN - SUMMARY",
        "registry_count": len(SWEDEN_SUMMARY_COMPANIES),
        "matched_count": len(matched),
        "missing_count": len(missing),
        "matched_companies": matched,
        "missing_companies": missing,
        "opportunities": opportunities,
        "favi_companies": favi_rows,
    }


def compute_market_cohorts(
    dashboard_data: list[dict],
    company_details: list[dict],
    opportunity_map: dict,
    favi_companies: list[dict],
) -> dict:
    return {
        "sweden_summary": compute_sweden_summary_cohort(
            dashboard_data,
            company_details,
            opportunity_map,
            favi_companies,
        )
    }
