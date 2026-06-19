
import os
import json
import time
import re
import html as html_lib
import unicodedata
import requests as http_requests
import concurrent.futures
from datetime import date, datetime
from urllib.parse import urlparse
import anthropic
from knowledge_base import get_evolving_framework
from source_quality import assess_evidence_quality, confidence_quality_penalty, annotate_evidence_links, filter_admissible_sources, evidence_sufficiency_score, classify_generation_context, apply_language_metadata
from entity_identity import build_entity_context_block, entity_search_terms
from llm_clients import call_anthropic_text, call_json_model_chain, call_openai_text
from gemini_client import call_gemini_forensic_text, gemini_api_key

# --- HELPER: IMPROVE SEARCH RECALL ---
def clean_company_name_for_search(name):
    """
    Removes legal suffixes to improve web-search recall.
    """
    clean = name
    suffixes = [" Oyj", " Abp", " Oy", " Ltd", " Plc", " Corporation", " Group", " Inc"]
    for s in suffixes:
        if clean.lower().endswith(s.lower()):
            clean = clean[:-len(s)].strip()
    return clean

def get_clean_domain(url):
    if not url: return ""
    try:
        parsed = urlparse(url)
        domain = parsed.netloc
        if domain.startswith("www."):
            domain = domain[4:]
        return domain
    except:
        return ""


# Candidate acquisition registry for Scanner 2. These are search targets, not
# promoted evidence. Promotion still requires URL, content, entity, and claim
# verification downstream.
JURY_SOURCE_TARGETS = {
    "ARCHITECTURAL JURY (Engineering Reality)": [
        {
            "label": "Public code, APIs, developer portals",
            "query": 'site:github.com "{search_name}" OR site:gitlab.com "{search_name}" OR site:postman.com "{search_name}" OR site:{domain_hint} ("developers" OR "API" OR "docs")',
            "source_type": "technical_artifact",
            "credibility": "medium/high when entity-matched",
            "company_claims": "yes, only when org/repo/domain clearly matches target",
            "confirmation": "requires entity match; public repos with same-name ambiguity are display-only",
        },
        {
            "label": "Engineering blog and design system",
            "query": 'site:{domain_hint} ("engineering blog" OR "tech blog" OR "developers" OR "teknologiablogi" OR "kehittäjät" OR "Design System" OR "UI Kit" OR "suunnittelujärjestelmä")',
            "source_type": "official_operating_evidence",
            "credibility": "medium",
            "company_claims": "yes, company-authored operational evidence",
            "confirmation": "do not treat generic marketing pages as engineering proof",
        },
        {
            "label": "Job ads and real tech stack",
            "query": 'site:linkedin.com/jobs "{search_name}" OR site:duunitori.fi "{search_name}" OR site:oikotie.fi "{search_name}" OR site:indeed.fi "{search_name}" OR site:thehub.io "{search_name}" OR "{search_name}" ("developer" OR "kehittäjä" OR "architect" OR "arkkitehti" OR "engineer" OR "job description")',
            "source_type": "job_ad_tech_stack",
            "credibility": "medium",
            "company_claims": "yes for hiring/stack signals, not proof of deployed production systems",
            "confirmation": "prefer current ads; record date/source and exact tools mentioned",
        },
    ],
    "STRATEGIC JURY (Business Model, Procurement & Customer Experience)": [
        {
            "label": "Legal, filings, ownership, investor facts",
            "query": 'site:prh.fi "{search_name}" OR site:virre.prh.fi "{search_name}" OR site:{domain_hint} ("annual report" OR "vuosikertomus" OR "investors" OR "sijoittajat" OR "stock exchange release" OR "pörssitiedote") OR site:nasdaqomxnordic.com "{search_name}"',
            "source_type": "legal_financial_filing",
            "credibility": "high",
            "company_claims": "yes for legal/ownership/filing facts",
            "confirmation": "official filing or company/investor page required for precise ownership or guidance claims",
        },
        {
            "label": "Financial databases",
            "query": 'site:finder.fi "{search_name}" OR site:asiakastieto.fi "{search_name}" OR site:kauppalehti.fi "{search_name}" liikevaihto OR site:talouselama.fi "{search_name}"',
            "source_type": "third_party_financial",
            "credibility": "high",
            "company_claims": "scoped financial facts only",
            "confirmation": "local legal-entity data cannot become unqualified group performance",
        },
        {
            "label": "Procurement and public delivery",
            "query": 'site:hankintailmoitukset.fi "{search_name}" OR site:ted.europa.eu "{search_name}" OR site:hansel.fi "{search_name}" OR site:cloudia.com "{search_name}" OR site:tutkihankintoja.fi "{search_name}"',
            "source_type": "procurement_record",
            "credibility": "high",
            "company_claims": "yes for awarded tenders/contracts when target entity is named",
            "confirmation": "contracting party and award/service category must be explicit",
        },
        {
            "label": "Independent business news and sector context",
            "query": 'site:reuters.com "{search_name}" OR site:kauppalehti.fi "{search_name}" OR site:talouselama.fi "{search_name}" OR site:etla.fi "{search_name}" OR site:etla.fi "{domain_hint}" OR site:sitra.fi "{search_name}"',
            "source_type": "editorial_or_research",
            "credibility": "medium/high",
            "company_claims": "company-specific only when company is explicitly discussed; otherwise context-only",
            "confirmation": "sector research may inform context, not company facts",
        },
        {
            "label": "Independent CX evidence",
            "query": 'site:trustpilot.com "{search_name}" OR site:apps.apple.com "{search_name}" OR site:play.google.com "{search_name}" OR "{search_name}" ("EPSI" OR "NPS" OR "Net Promoter" OR "customer satisfaction" OR "asiakastyytyväisyys" OR "CXPA")',
            "source_type": "independent_customer_experience",
            "credibility": "medium/high",
            "company_claims": "yes for independent ratings/metrics only",
            "confirmation": "company-published NPS/CSAT remains intent unless independently verified",
        },
    ],
    "CULTURAL JURY (Talent & Psychological Safety)": [
        {
            "label": "Careers and hiring transparency",
            "query": 'site:{domain_hint} ("careers" OR "ura" OR "avoimet työpaikat" OR "open positions" OR "hiring process" OR "rekrytointiprosessi")',
            "source_type": "official_careers",
            "credibility": "medium",
            "company_claims": "yes for stated work model/benefits/hiring process; culture claims are intent unless evidenced",
            "confirmation": "prefer concrete policies, process steps, and open role details",
        },
        {
            "label": "Certified employee trust and workforce metrics",
            "query": 'site:greatplacetowork.fi "{search_name}" OR site:{domain_hint} ("sustainability report" OR "vastuullisuusraportti" OR "CSRD" OR "ESRS" OR "S1 own workforce")',
            "source_type": "workforce_metric",
            "credibility": "high",
            "company_claims": "yes for survey/list presence and audited workforce metrics",
            "confirmation": "record year, size category, metric, and scope",
        },
        {
            "label": "Inclusion, community, employee voice",
            "query": 'site:mimmitkoodaa.fi "{search_name}" OR "Women in Tech Finland" "{search_name}" OR site:meetup.com "{search_name}" OR site:glassdoor.com "{search_name}" OR site:teamblind.com "{search_name}" OR "{search_name}" ("työntekijäkokemus" OR "työpaikka-arvio" OR "työkulttuuri" OR "returned to" OR "rejoined" OR "palasi takaisin")',
            "source_type": "culture_signal",
            "credibility": "low/medium",
            "company_claims": "display-only unless corroborated by stronger evidence",
            "confirmation": "do not use anonymous reviews as hard facts; no Reddit",
        },
    ],
    "AI & DATA JURY (Future Readiness)": [
        {
            "label": "Cloud/vendor customer stories and partner directories",
            "query": 'site:aws.amazon.com "{search_name}" ("case study" OR "case studies") OR site:customers.microsoft.com "{search_name}" OR site:cloud.google.com "{search_name}" OR site:sap.com "{search_name}" OR site:salesforce.com "{search_name}" OR site:servicenow.com "{search_name}" OR site:databricks.com "{search_name}" OR site:snowflake.com "{search_name}" OR site:atlassian.com "{search_name}"',
            "source_type": "vendor_case_study",
            "credibility": "high/medium",
            "company_claims": "yes for named implementation/vendor relationship",
            "confirmation": "source title/quote must name target and technology/vendor",
        },
        {
            "label": "Models, tech-stack, patents, research",
            "query": 'site:huggingface.co "{search_name}" OR site:stackshare.io "{search_name}" OR site:worldwide.espacenet.com "{search_name}" OR site:espacenet.com "{search_name}" OR site:theseus.fi "{search_name}" OR site:vtt.fi "{search_name}" OR site:tuni.fi "{search_name}" OR site:aalto.fi "{search_name}" OR site:research.aalto.fi "{search_name}"',
            "source_type": "ai_data_research_artifact",
            "credibility": "medium/high",
            "company_claims": "yes when target entity/inventor/applicant relationship is explicit",
            "confirmation": "patents and research must identify applicant/company or clear affiliation",
        },
        {
            "label": "AI/data economy context",
            "query": 'site:sitra.fi "{search_name}" ("tekoäly" OR "artificial intelligence" OR "datatalous" OR "data economy") OR site:sitra.fi ("tekoäly" OR "datatalous" OR "data economy")',
            "source_type": "innovation_context",
            "credibility": "high",
            "company_claims": "company-specific only if target is named; otherwise context-only",
            "confirmation": "do not promote national/sector context into company adoption claims",
        },
    ],
    "SERVICE PORTFOLIO JURY (Delivery Reality)": [
        {
            "label": "Official services, references, customer stories",
            "query": 'site:{domain_hint} ("services" OR "palvelut" OR "solutions" OR "ratkaisut" OR "case study" OR "reference" OR "asiakastarina" OR "referenssi" OR "customers")',
            "source_type": "official_service_evidence",
            "credibility": "medium",
            "company_claims": "yes for offered services and named references, but promotional tone requires caution",
            "confirmation": "look for concrete platform names, delivery methods, and named customer outcomes",
        },
        {
            "label": "Customer-side proof and analyst coverage",
            "query": '"{search_name}" ("case study" OR "customer story" OR "asiakastarina" OR "reference" OR "referenssi" OR Gartner OR Forrester OR ISG OR "Magic Quadrant")',
            "source_type": "third_party_service_evidence",
            "credibility": "medium/high",
            "company_claims": "yes when customer/analyst explicitly names target and service",
            "confirmation": "customer-side pages are stronger than supplier-authored references",
        },
        {
            "label": "Procurement delivery track record",
            "query": 'site:openprocurement.fi "{search_name}" OR site:tutkihankintoja.fi "{search_name}" OR site:hankintailmoitukset.fi "{search_name}"',
            "source_type": "procurement_record",
            "credibility": "high",
            "company_claims": "yes for public-sector delivery capability",
            "confirmation": "record buyer, category, value, and recency when available",
        },
    ],
}


FORENSIC_POSTPROCESS_RESERVE_SECONDS = 30
FORENSIC_ACQUISITION_CEILING_SECONDS = 135
FORENSIC_EXTRACTION_RECOVERY_CEILING_SECONDS = 35
FORENSIC_FIRECRAWL_MAX_URLS = 6
FORENSIC_JURY_ANALYSIS_CEILING_SECONDS = 95
FORENSIC_GEMINI_TIMEOUT_SECONDS = 100
FORENSIC_JURIES = tuple(JURY_SOURCE_TARGETS.keys())


def _canonical_evidence_url(url: str) -> str:
    if not url:
        return ""
    try:
        parsed = urlparse(str(url).strip())
        host = parsed.netloc.lower().removeprefix("www.")
        path = re.sub(r"/+", "/", parsed.path or "/").rstrip("/") or "/"
        return f"{host}{path}".lower()
    except Exception:
        return str(url).strip().lower().rstrip("/")


def _finding_jury(finding: dict) -> str:
    category = str((finding or {}).get("category") or "")
    if category in JURY_SOURCE_TARGETS:
        return category
    token = " ".join(
        str((finding or {}).get(key) or "")
        for key in (
            "category", "source_type", "source_category", "label", "title",
            "snippet", "cached_excerpt", "source_quote", "verification_reason",
        )
    ).lower()
    if any(word in token for word in ("architecture", "engineering", "developer", "api", "job ad", "tech stack")):
        return "ARCHITECTURAL JURY (Engineering Reality)"
    if any(word in token for word in ("service", "reference", "customer story", "case study", "delivery")):
        return "SERVICE PORTFOLIO JURY (Delivery Reality)"
    if any(word in token for word in ("ai", "data", "cloud", "cyber", "erp", "technology", "patent")):
        return "AI & DATA JURY (Future Readiness)"
    if any(word in token for word in ("culture", "career", "hiring", "employee", "workplace", "sustainability")):
        return "CULTURAL JURY (Talent & Psychological Safety)"
    if any(word in token for word in ("strategy", "financial", "annual", "investor", "procurement", "tender", "customer")):
        return "STRATEGIC JURY (Business Model, Procurement & Customer Experience)"
    return ""


def _compute_forensic_coverage(findings: list, official_domain: str = "") -> dict:
    findings = [item for item in (findings or []) if isinstance(item, dict)]
    linked = [item for item in findings if item.get("url")]
    readable = [
        item for item in linked
        if item.get("cached_excerpt") or item.get("snippet") or item.get("content_verified")
    ]
    verified = [
        item for item in linked
        if item.get("seed_url_verified") or item.get("url_verified") or item.get("verified")
    ]
    recent = [item for item in linked if item.get("is_recent")]
    fresh = [item for item in linked if item.get("source_status") == "fresh_discovery"]
    juries = sorted({jury for jury in (_finding_jury(item) for item in linked) if jury})
    official = 0
    independent = 0
    clean_domain = (official_domain or "").lower().removeprefix("www.")
    for item in linked:
        host = get_clean_domain(item.get("url") or "").lower()
        if clean_domain and (host == clean_domain or host.endswith(f".{clean_domain}")):
            official += 1
        elif host:
            independent += 1
    score = min(25, len(linked) * 4)
    score += min(20, len(readable) * 4)
    score += min(20, len(verified) * 4)
    score += min(20, len(juries) * 4)
    if official and independent:
        score += 10
    elif official or independent:
        score += 5
    score += min(5, len(recent))
    score = min(100, score)
    if not fresh:
        score = min(score, 55)
    if len(juries) < len(FORENSIC_JURIES):
        score = min(score, 75)
    return {
        "score": score,
        "usable_urls": len(linked),
        "cached_or_readable": len(readable),
        "verified_urls": len(verified),
        "jury_coverage": juries,
        "official_sources": official,
        "independent_sources": independent,
        "fresh_sources": len(fresh),
        "missing_juries": [jury for jury in FORENSIC_JURIES if jury not in juries],
    }


def _compute_promoted_evidence_coverage(findings: list, official_domain: str = "") -> dict:
    """Measure evidence that survived entity and exact claim-support gates."""
    promoted = [
        item for item in (findings or [])
        if isinstance(item, dict)
        and item.get("url")
        and item.get("verified") is True
        and item.get("wrong_entity") is not True
        and str(item.get("support_level") or "").lower() == "exact"
        and str(item.get("claim_support_status") or "").lower() == "supported"
        and item.get("analysis_verified") is True
    ]
    coverage = _compute_forensic_coverage(promoted, official_domain)
    coverage["promoted_findings"] = len(promoted)
    coverage["coverage_kind"] = "promoted_exact_claim_support"
    return coverage


def _is_generic_context_page(url: str) -> bool:
    """Identify landing/index pages that cannot prove detailed operational claims."""
    try:
        path = (urlparse(url).path or "/").lower().rstrip("/") or "/"
    except Exception:
        return False
    if path in {"/", "/fi", "/en", "/sv", "/careers", "/jobs", "/services", "/service"}:
        return True
    return bool(re.search(r"/(careers|jobs|services?|case-studies|references|customers)/?$", path))


def _normalize_passage(value: str) -> str:
    """Normalize presentation differences without turning paraphrases into quotes."""
    normalized = unicodedata.normalize("NFKC", html_lib.unescape(value or "")).casefold()
    normalized = normalized.replace("\u00ad", "").replace("\u00a0", " ")
    normalized = re.sub(r"(?<=\w)-\s+(?=\w)", "", normalized)
    normalized = re.sub(r"[\u2010-\u2015]", "-", normalized)
    normalized = normalized.replace("-", " ")
    normalized = re.sub(r"[\u2018\u2019]", "'", normalized)
    normalized = re.sub(r'[\u201c\u201d]', '"', normalized)
    normalized = re.sub(r"[^\w\d%€$+.#/\-]+", " ", normalized, flags=re.UNICODE)
    return re.sub(r"\s+", " ", normalized).strip()


def _quote_match_details(quote: str, packet_text: str, require_verbatim: bool = False) -> dict:
    """Match an acquired passage while keeping the standard close to verbatim."""
    normalized_quote = _normalize_passage(quote)
    normalized_packet = _normalize_passage(packet_text)
    details = {"matched": False, "method": "none", "score": 0.0}
    if len(normalized_quote) < 24 or not normalized_packet:
        return details
    if normalized_quote in normalized_packet:
        return {"matched": True, "method": "normalized_verbatim", "score": 1.0}
    if require_verbatim:
        return details
    quote_terms = {
        token for token in re.findall(r"[\w\d][\w\d+.#%€$/\-]{2,}", normalized_quote, flags=re.UNICODE)
    }
    packet_terms = {
        token for token in re.findall(r"[\w\d][\w\d+.#%€$/\-]{2,}", normalized_packet, flags=re.UNICODE)
    }
    if len(quote_terms) < 4 or not packet_terms:
        return details
    score = len(quote_terms & packet_terms) / len(quote_terms)
    # This tolerance handles punctuation, inflection and extraction noise. It is
    # intentionally too strict for a semantic paraphrase to count as a quote.
    matched = score >= 0.78 and len(quote_terms & packet_terms) >= min(6, len(quote_terms))
    return {"matched": matched, "method": "normalized_token_match" if matched else "none", "score": score}


def _quote_matches_packet(quote: str, packet_text: str, require_verbatim: bool = False) -> bool:
    return _quote_match_details(quote, packet_text, require_verbatim=require_verbatim)["matched"]


def _relevant_passage(quote: str, content: str, limit: int = 4200) -> str:
    """Return the part of extracted content most likely to contain the quote."""
    text = str(content or "").strip()
    if len(text) <= limit:
        return text
    lowered = text.casefold()
    words = [word for word in re.findall(r"[\w\d][\w\d\-]{4,}", quote or "", flags=re.UNICODE)]
    center = -1
    # Longer words are generally more discriminating than a company name or
    # generic terms near the start of a page.
    for word in sorted(set(words), key=len, reverse=True):
        center = lowered.find(word.casefold())
        if center >= 0:
            break
    if center < 0:
        return text[:limit]
    start = max(0, center - limit // 3)
    return text[start:start + limit]


def _firecrawl_forensic_extract(url: str, timeout: int = 25) -> dict:
    """Extract one valuable forensic page after direct passage matching fails."""
    key = str(os.environ.get("FIRECRAWL_API_KEY") or "").strip().strip("'\"")
    if not key:
        return {"ok": False, "reason": "not_configured", "text": ""}
    try:
        response = http_requests.post(
            "https://api.firecrawl.dev/v1/scrape",
            headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"},
            json={"url": url, "formats": ["markdown"], "onlyMainContent": True},
            timeout=timeout,
        )
        if response.status_code not in (200, 201):
            return {"ok": False, "reason": f"http_{response.status_code}", "text": ""}
        payload = response.json()
        data = payload.get("data") if isinstance(payload, dict) else None
        if not isinstance(data, dict):
            data = payload if isinstance(payload, dict) else {}
        text = str(data.get("markdown") or data.get("content") or "").strip()
        if len(text) < 120 or _SOFT_404_PATTERNS.search(text[:4000]):
            return {"ok": False, "reason": "unreadable_or_soft_404", "text": text}
        return {"ok": True, "reason": "", "text": text[:60000]}
    except Exception as error:
        return {"ok": False, "reason": type(error).__name__, "text": ""}


def _firecrawl_recovery_priority(source: dict) -> int:
    token = " ".join(str(source.get(key) or "") for key in ("source_type", "url", "title")).lower()
    score = 0
    if any(term in token for term in ("official", "filing", "legal", "procurement", "annual", "investor")):
        score += 5
    if any(term in token for term in ("case_study", "customer", "technical", "research", "workforce_metric")):
        score += 3
    if any(term in token for term in ("linkedin", "glassdoor", "teamblind", "search?", "/jobs")):
        score -= 4
    return score


def _claim_matches_quote(claim: str, quote: str, support_level: str) -> bool:
    """Reject claims that materially outrun their supporting quote."""
    stop_words = {
        "about", "after", "also", "and", "are", "for", "from", "has", "have", "into",
        "its", "that", "the", "their", "this", "through", "uses", "using", "with",
        "että", "ja", "joka", "kanssa", "kun", "myös", "on", "ovat", "sekä", "sen", "yritys",
    }

    def meaningful_terms(value: str) -> set:
        return {
            token.lower()
            for token in re.findall(r"[A-Za-zÅÄÖåäö0-9][A-Za-zÅÄÖåäö0-9+.#%€$\-/]{2,}", value or "")
            if token.lower() not in stop_words
        }

    claim_terms = meaningful_terms(claim)
    quote_terms = meaningful_terms(quote)
    if not claim_terms or not quote_terms:
        return False

    numbers = set(re.findall(r"(?<!\w)[€$]?\d[\d.,]*%?", claim or ""))
    if numbers and not all(number in (quote or "") for number in numbers):
        return False

    overlap = len(claim_terms & quote_terms) / len(claim_terms)
    threshold = 0.50 if support_level == "exact" else 0.30
    return overlap >= threshold and len(claim_terms & quote_terms) >= min(3, len(claim_terms))


def _prevalidate_acquisition_results(
    acquisition_results: list,
    company_name: str,
    recovery_seconds: int = FORENSIC_EXTRACTION_RECOVERY_CEILING_SECONDS,
) -> tuple[list, dict]:
    """Gate fresh packets before expensive jury analysis."""
    sources = [
        source for lane in (acquisition_results or [])
        for source in lane.get("sources", []) if isinstance(source, dict) and source.get("url")
    ]
    url_results = {}
    unique_urls = sorted({source["url"] for source in sources})
    if unique_urls:
        with concurrent.futures.ThreadPoolExecutor(max_workers=min(10, len(unique_urls))) as pool:
            futures = {pool.submit(_verify_url, url, 6, company_name): url for url in unique_urls}
            for future in concurrent.futures.as_completed(futures):
                url = futures[future]
                try:
                    url_results[url] = future.result() or {}
                except Exception:
                    url_results[url] = {}

    recovery_candidates = []
    for source in sources:
        checked = url_results.get(source.get("url"), {})
        match = _quote_match_details(
            str(source.get("snippet") or ""),
            str(checked.get("content_excerpt") or ""),
        )
        if (
            checked.get("verified") is True
            and checked.get("company_mentioned") is not False
            and checked.get("content_readable") is True
            and len(str(source.get("snippet") or "").strip()) >= 40
            and not match["matched"]
        ):
            recovery_candidates.append(source)

    firecrawl_results = {}
    selected_recovery = sorted(
        recovery_candidates,
        key=_firecrawl_recovery_priority,
        reverse=True,
    )[:FORENSIC_FIRECRAWL_MAX_URLS]
    firecrawl_configured = bool(str(os.environ.get("FIRECRAWL_API_KEY") or "").strip())
    if selected_recovery and firecrawl_configured and recovery_seconds > 0:
        pool = concurrent.futures.ThreadPoolExecutor(max_workers=min(4, len(selected_recovery)))
        futures = {
            pool.submit(_firecrawl_forensic_extract, source["url"], min(25, recovery_seconds)): source["url"]
            for source in selected_recovery
        }
        done, pending = concurrent.futures.wait(futures, timeout=recovery_seconds)
        for future in done:
            url = futures[future]
            try:
                firecrawl_results[url] = future.result() or {}
            except Exception as error:
                firecrawl_results[url] = {"ok": False, "reason": type(error).__name__, "text": ""}
        for future in pending:
            future.cancel()
            firecrawl_results[futures[future]] = {"ok": False, "reason": "shared_deadline", "text": ""}
        pool.shutdown(wait=False)

    stats = {
        "input": len(sources), "passed": 0, "dead_or_soft_404": 0,
        "wrong_entity": 0, "unreadable": 0, "quote_not_found": 0,
        "firecrawl_attempted": len(firecrawl_results), "firecrawl_recovered": 0,
        "firecrawl_failed": 0, "firecrawl_skipped_by_cap": max(0, len(recovery_candidates) - len(selected_recovery)),
        "firecrawl_unavailable": bool(recovery_candidates and not firecrawl_configured),
    }
    gated_results = []
    for lane in acquisition_results or []:
        kept = []
        rejected = list(lane.get("pre_jury_rejected", []) or [])
        for source in lane.get("sources", []) or []:
            checked = url_results.get(source.get("url"), {})
            source["url_verified"] = checked.get("verified") is True
            source["verified"] = source["url_verified"]
            source["content_readable"] = checked.get("content_readable") is True
            source["entity_mentioned"] = checked.get("company_mentioned")
            direct_content = str(checked.get("content_excerpt") or "")
            match = _quote_match_details(str(source.get("snippet") or ""), direct_content)
            source["quote_match_method"] = match["method"]
            source["quote_match_score"] = round(float(match["score"]), 3)
            source["extraction_provider"] = "direct"
            source["fetched_excerpt"] = _relevant_passage(str(source.get("snippet") or ""), direct_content)
            if checked.get("extracted_date"):
                source["extracted_date"] = str(checked["extracted_date"])
            reason = ""
            if not source["url_verified"]:
                reason = "dead_or_soft_404"
            elif checked.get("company_mentioned") is False:
                reason = "wrong_entity"
            elif not source["content_readable"] or len(str(source.get("snippet") or "").strip()) < 40:
                reason = "unreadable"
            elif not match["matched"]:
                recovered = firecrawl_results.get(source.get("url"), {})
                recovered_text = str(recovered.get("text") or "")
                recovered_match = _quote_match_details(str(source.get("snippet") or ""), recovered_text)
                if recovered.get("ok") and recovered_match["matched"]:
                    source["content_readable"] = True
                    source["extraction_provider"] = "firecrawl"
                    source["quote_match_method"] = f"firecrawl_{recovered_match['method']}"
                    source["quote_match_score"] = round(float(recovered_match["score"]), 3)
                    source["fetched_excerpt"] = _relevant_passage(
                        str(source.get("snippet") or ""), recovered_text
                    )
                    stats["firecrawl_recovered"] += 1
                else:
                    if source.get("url") in firecrawl_results:
                        stats["firecrawl_failed"] += 1
                        source["extraction_recovery_reason"] = recovered.get("reason") or "passage_not_found"
                    reason = "quote_not_found"
            if reason:
                stats.setdefault(reason, 0)
                stats[reason] += 1
                rejected.append({"url": source.get("url"), "reason": reason, "stage": "pre_jury_gate"})
                continue
            source["pre_jury_gate"] = "passed"
            source["generic_context_page"] = _is_generic_context_page(source.get("url") or "")
            kept.append(source)
            stats["passed"] += 1
        normalized_lane = dict(lane)
        normalized_lane["sources"] = kept
        normalized_lane["pre_jury_rejected"] = rejected
        gated_results.append(normalized_lane)
    return gated_results, stats


def _merge_forensic_jury_results(baseline: dict, jury_results: list, routed_seeds: dict) -> dict:
    """Merge judged findings; retain only revalidated seeds when a jury fails."""
    result = {key: value for key, value in (baseline or {}).items() if key != "findings"}
    findings = []
    statuses = []
    confidences = []
    adjustments = []
    rejected = []
    for lane in jury_results or []:
        jury = lane.get("jury")
        payload = lane.get("data") if isinstance(lane.get("data"), dict) else None
        packet_urls = {
            _canonical_evidence_url(url)
            for url in (lane.get("packet_urls") or [])
            if url
        }
        if not packet_urls:
            packet_urls = {
                _canonical_evidence_url(item.get("url"))
                for item in routed_seeds.get(jury, []) or []
                if item.get("url")
            }
        packet_by_url = {
            _canonical_evidence_url(item.get("url")): item
            for item in lane.get("packets", []) or [] if item.get("url")
        }
        accepted = []
        if payload:
            for item in payload.get("findings", []) or []:
                if not isinstance(item, dict) or not item.get("url"):
                    continue
                if _canonical_evidence_url(item.get("url")) not in packet_urls:
                    rejected.append({
                        "url": item.get("url"),
                        "reason": "Jury returned a URL that was not present in its supplied source packets.",
                        "jury": jury,
                    })
                    continue
                packet = packet_by_url.get(_canonical_evidence_url(item.get("url")), {})
                supported_claim = str(item.get("supported_claim") or item.get("label") or "").strip()
                source_quote = str(item.get("source_quote") or item.get("snippet") or "").strip()
                support_level = str(item.get("support_level") or "unsupported").lower()
                if support_level not in {"exact", "partial", "context_only", "unsupported"}:
                    support_level = "unsupported"
                packet_text = str(
                    packet.get("fetched_excerpt")
                    or packet.get("snippet")
                    or packet.get("cached_excerpt")
                    or ""
                )
                if not supported_claim or not _quote_matches_packet(
                    source_quote,
                    packet_text,
                    require_verbatim=support_level == "exact",
                ) or not _claim_matches_quote(supported_claim, source_quote, support_level):
                    rejected.append({
                        "url": item.get("url"),
                        "reason": "Jury claim lacked a source-bound quote or materially exceeded the quote.",
                        "jury": jury,
                    })
                    continue
                if packet.get("generic_context_page") and support_level == "exact":
                    support_level = "context_only"
                if support_level == "unsupported":
                    rejected.append({"url": item.get("url"), "reason": "Jury marked claim unsupported.", "jury": jury})
                    continue
                normalized = dict(item)
                normalized["category"] = jury
                normalized["label"] = supported_claim[:300]
                normalized["supported_claim"] = supported_claim
                normalized["snippet"] = source_quote[:1200]
                normalized["source_quote"] = source_quote[:1200]
                normalized["support_level"] = support_level
                normalized["source_status"] = "jury_accepted" if support_level == "exact" else f"jury_{support_level}"
                normalized["analysis_verified"] = support_level == "exact"
                normalized["claim_support_status"] = "supported" if support_level == "exact" else support_level
                normalized["generation_eligible"] = False
                normalized["claim_support_pending"] = False
                normalized["verified"] = packet.get("verified") is True
                normalized["url_verified"] = packet.get("url_verified") is True
                normalized["content_readable"] = packet.get("content_readable") is True
                normalized["generic_context_page"] = packet.get("generic_context_page") is True
                normalized["source_type"] = item.get("source_type") or packet.get("source_type")
                normalized["source_database"] = packet.get("source_database")
                normalized["forensic_seed_source"] = bool(packet.get("forensic_seed_source"))
                normalized["packet_source_status"] = packet.get("source_status")
                accepted.append(normalized)
            rejected.extend(payload.get("rejected_evidence", []) or [])
            if accepted:
                confidences.append(payload.get("confidence_score", 0) or 0)
                adjustments.append(payload.get("reality_score_adjustment", 0) or 0)
        else:
            for item in routed_seeds.get(jury, []) or []:
                if item.get("seed_url_verified") and item.get("seed_entity_mentioned") is not False:
                    fallback = dict(item)
                    fallback["category"] = jury
                    fallback["source_status"] = "seeded_analysis_pending"
                    fallback["analysis_verified"] = False
                    fallback["claim_support_pending"] = True
                    accepted.append(fallback)
        findings, _ = _merge_findings(findings, accepted)
        statuses.append({
            "jury": jury,
            "status": lane.get("status") or ("completed" if payload else "degraded"),
            "provider": lane.get("provider"),
            "accepted": len(accepted),
            "rejected": len((payload or {}).get("rejected_evidence", []) or []),
        })
    result["findings"] = findings
    result["key_people"] = []
    result["confidence_score"] = round(sum(confidences) / len(confidences)) if confidences else min(35, baseline.get("confidence_score", 20))
    result["reality_score_adjustment"] = round(sum(adjustments) / len(adjustments)) if adjustments else 0
    result["forensic_jury_status"] = statuses
    result["rejected_evidence"] = rejected
    result["_parallel_jury_forensic"] = True
    result["_seeded_forensic_fallback"] = not any(lane.get("data") for lane in jury_results or [])
    return result


def _build_jury_source_registry_block(search_name: str, domain_hint: str, jury_names=None) -> str:
    """Render source targets as a compact acquisition registry for the web-search prompt."""
    lines = [
        "SOURCE TARGET REGISTRY (candidate acquisition only — not automatic evidence):",
        "Search broadly, store more, promote narrowly. A source is allowed for analysis only after URL, content, target-entity, and claim-support verification pass.",
        "Do not increase search count because the registry is larger; combine adjacent targets and prioritize the highest-trust route for each jury.",
    ]
    selected = set(jury_names or JURY_SOURCE_TARGETS.keys())
    for jury_name, targets in JURY_SOURCE_TARGETS.items():
        if jury_name not in selected:
            continue
        lines.append(f"\n    {jury_name}")
        for target in targets:
            query = target["query"].format(search_name=search_name, domain_hint=domain_hint)
            lines.append(
                "       - {label}: `{query}` | type={source_type}; default trust={credibility}; "
                "company claims={company_claims}; confirmation={confirmation}".format(
                    label=target["label"],
                    query=query,
                    source_type=target["source_type"],
                    credibility=target["credibility"],
                    company_claims=target["company_claims"],
                    confirmation=target["confirmation"],
                )
            )
    return "\n".join(lines)


def _build_jury_acquisition_prompt(
    search_name: str,
    domain_hint: str,
    jury_name: str,
    entity_context: str = "",
    search_aliases: str = "",
) -> str:
    """Build a search-only prompt for one independent evidence-acquisition lane."""
    return f"""Acquire CURRENT SOURCE MATERIAL for \"{search_name}\" (official domain: {domain_hint}).
This lane covers only: {jury_name}

This is source acquisition, not analysis. Search the approved registry below in Finnish and English.
Prioritize sources published or updated within 24 months, while allowing current official registry,
legal, procurement, developer, careers, and living documentation pages without a visible date.
Do not search for people, stakeholders, profiles, emails, or contact details.

ENTITY:
{entity_context}
{search_aliases}

{_build_jury_source_registry_block(search_name, domain_hint, (jury_name,))}

Return only source packets that mention or clearly scope to the target company, group, or named subsidiary.
Do not infer a claim from a title. `verbatim_quote` is mandatory and must be copied word-for-word
from the linked page itself, not paraphrased from a search-result summary. Use a direct canonical
article, report, filing, documentation, or job URL rather than a search page or generic landing page.
The quote should be 30-120 words and contain the target entity or the exact narrow fact it supports.
If you cannot obtain a page-bound verbatim passage, omit that source.
Return ONLY valid JSON:
{{
  "jury": {json.dumps(jury_name)},
  "sources": [
    {{"url": "https://...", "title": "page title", "verbatim_quote": "exact page passage",
      "published_at": "YYYY-MM-DD or null", "source_type": "registry source type",
      "entity_scope_candidate": "company|group|subsidiary|context|unclear",
      "discovery_query": "query used", "freshness": "fresh|current|evergreen|unknown"}}
  ]
}}
"""


def _normalize_acquisition_payload(payload: dict, jury_name: str, provider: str) -> list:
    """Normalize one provider's source packets without promoting their trust."""
    sources = payload.get("sources") if isinstance(payload, dict) else None
    if not isinstance(sources, list):
        sources = payload.get("findings", []) if isinstance(payload, dict) else []
    normalized = []
    seen = set()
    for item in sources or []:
        if not isinstance(item, dict):
            continue
        url = str(item.get("url") or item.get("source_url") or "").strip()
        if not url.startswith("http"):
            continue
        key = _canonical_evidence_url(url)
        if not key or key in seen:
            continue
        seen.add(key)
        quote = str(
            item.get("verbatim_quote") or item.get("quote") or item.get("snippet") or item.get("source_quote") or ""
        ).strip()
        normalized.append({
            "category": jury_name,
            "label": str(item.get("label") or item.get("title") or "Fresh evidence candidate")[:180],
            "title": str(item.get("title") or url)[:300],
            "url": url,
            "snippet": quote[:1800],
            "published_at": item.get("published_at"),
            "source_type": item.get("source_type") or "web_source",
            "entity_scope": item.get("entity_scope_candidate") or item.get("entity_scope") or "unclear",
            "discovery_query": item.get("discovery_query") or "",
            "freshness": item.get("freshness") or "unknown",
            "is_recent": (item.get("freshness") in {"fresh", "current"}),
            "found": True,
            "sentiment": "neutral",
            "source_status": "fresh_discovery",
            "claim_support_pending": True,
            "acquisition_provider": provider,
            "forensic_provenance": "fresh_acquisition",
        })
    return normalized


def _route_seed_findings(findings: list) -> dict:
    """Route inherited candidates to one nearest jury; never inherit trust."""
    routed = {jury: [] for jury in FORENSIC_JURIES}
    for item in findings or []:
        if not isinstance(item, dict):
            continue
        jury = _finding_jury(item)
        if not jury:
            jury = "STRATEGIC JURY (Business Model, Procurement & Customer Experience)"
        candidate = dict(item)
        candidate["category"] = jury
        candidate["claim_support_pending"] = True
        candidate["claim_support_status"] = "pending_jury_verification"
        candidate["generation_eligible"] = False
        candidate["analysis_verified"] = False
        candidate["allowed_as_fact"] = False
        candidate["buying_evidence_eligible"] = False
        candidate["promotion_status"] = "candidate_revalidation_pending"
        candidate["forensic_provenance"] = "seed_candidate"
        routed[jury].append(candidate)
    return routed


def _build_jury_analysis_prompt(search_name: str, jury_name: str, packets: list) -> str:
    compact = []
    for item in packets[:18]:
        compact.append({
            "url": item.get("url"),
            "title": item.get("title"),
            "acquired_quote": str(item.get("snippet") or "")[:1200],
            "fetched_or_cached_content": str(
                item.get("fetched_excerpt") or item.get("cached_excerpt") or item.get("snippet") or ""
            )[:2600],
            "published_at": item.get("published_at"),
            "source_type": item.get("source_type"),
            "entity_scope_candidate": item.get("entity_scope"),
            "provenance": item.get("forensic_provenance") or item.get("source_database"),
            "url_revalidated": item.get("seed_url_verified") or item.get("url_verified"),
        })
    return f"""Act as {jury_name} for the forensic review of \"{search_name}\".
Do NOT browse the web. Judge only the supplied source packets.

For every promoted finding, verify from the supplied excerpt/content:
1. target entity and legal/group/subsidiary scope,
2. recency and whether an initiative is current, completed, or contradicted,
3. exact claim support rather than title similarity,
4. source authority and promotional limitations.

Seeded material is candidate evidence, never inherited truth. Reject wrong-entity, generic,
unsupported, stale, soft-404, or merely adjacent packets. Hiring is a weak signal, not deployment proof.
Write the SMALLEST claim the packet actually supports. `source_quote` must be copied verbatim from
`fetched_or_cached_content`, not invented, paraphrased, or copied only from an unconfirmed
`acquired_quote`. Grade support as:
- exact: quote directly supports the whole claim,
- partial: quote supports only part; phrase as a cautious signal,
- context_only: identity/general background only, not a company capability fact,
- unsupported: reject.
Precise figures, named systems/APIs/platforms, projects, outcomes, and specific hiring claims require
`exact` support. Generic home, careers, service, reference-index, and case-study-index pages are
context_only unless the supplied quote itself states the complete narrow fact.

SOURCE PACKETS:
{json.dumps(compact, ensure_ascii=False)}

Return ONLY valid JSON:
{{
  "jury": {json.dumps(jury_name)},
  "findings": [
    {{"category": {json.dumps(jury_name)}, "supported_claim": "minimal source-supported claim", "found": true,
      "sentiment": "positive|neutral|negative", "source_quote": "exact supplied quote", "title": "source title",
      "url": "https://...", "is_recent": true, "source_type": "type",
      "entity_scope": "company|group|subsidiary|context", "source_verdict": "accepted",
      "support_level": "exact|partial|context_only", "confidence": 0}}
  ],
  "rejected_evidence": [{{"url": "https://...", "reason": "specific rejection reason"}}],
  "contradictions": ["supported contradiction"],
  "missing_coverage": ["specific evidence gap"],
  "confidence_score": 0,
  "reality_score_adjustment": 0
}}
"""


# --- PRH OPEN DATA PRE-FETCH ---

def _fetch_prh_company_data(company_name: str, timeout: int = 8) -> dict | None:
    """
    Fetch official company data from PRH Open Data API (Finnish Trade Register).
    Returns structured data: business_id, registration date, business lines, status.
    Returns None on failure (non-blocking).
    """
    try:
        # Clean name for API search
        search = clean_company_name_for_search(company_name)
        url = f"https://avoindata.prh.fi/opendata-ytj-api/v3/companies?name={search}&maxResults=3"
        resp = http_requests.get(url, timeout=timeout, headers={
            "User-Agent": "AdaptabilityScanner/1.0",
            "Accept": "application/json",
        })
        if resp.status_code != 200:
            return None

        data = resp.json()
        companies = data.get("companies", [])
        if not companies:
            return None

        # Find best match (prefer exact or closest match)
        best = companies[0]
        for c in companies:
            names = c.get("names", [])
            for n in names:
                if n.get("name", "").lower().strip() == company_name.lower().strip():
                    best = c
                    break

        # Extract useful fields
        business_id = best.get("businessId", "")
        reg_date = best.get("registrationDate", "")
        status = best.get("status", "")
        company_form = best.get("companyForm", "")

        # Business lines
        lines = best.get("businessLines", [])
        active_lines = [bl.get("name", "") for bl in lines if bl.get("name")]

        # Registered offices
        addresses = best.get("registeredOffices", [])
        city = addresses[0].get("city", "") if addresses else ""

        return {
            "business_id": business_id,
            "registration_date": reg_date,
            "status": status,
            "company_form": company_form,
            "business_lines": active_lines[:3],
            "city": city,
        }
    except Exception as e:
        print(f"   [PRH] Pre-fetch failed (non-blocking): {e}")
        return None


# --- LIGHTWEIGHT TECH STACK DETECTION ---

# Known technology signatures in HTTP headers, HTML meta tags, and script sources
_TECH_SIGNATURES = {
    # CMS / Frameworks
    "WordPress": [("meta", "generator", "wordpress"), ("script", "wp-content"), ("script", "wp-includes")],
    "Drupal": [("meta", "generator", "drupal"), ("script", "drupal.js")],
    "Contentful": [("script", "contentful")],
    "Sitecore": [("meta", "generator", "sitecore"), ("script", "sitecore")],
    "HubSpot CMS": [("script", "js.hs-scripts.com"), ("script", "hubs.ly")],
    "Episerver/Optimizely": [("meta", "generator", "episerver"), ("script", "episerver"), ("script", "optimizely")],
    # Frontend frameworks
    "React": [("script", "react"), ("attr", "data-reactroot", "")],
    "Next.js": [("script", "_next/"), ("attr", "id", "__next")],
    "Vue.js": [("script", "vue."), ("attr", "data-v-", "")],
    "Angular": [("script", "angular"), ("attr", "ng-app", ""), ("attr", "ng-version", "")],
    # Analytics
    "Google Analytics 4": [("script", "gtag"), ("script", "googletagmanager")],
    "Hotjar": [("script", "hotjar.com")],
    "Cookiebot": [("script", "cookiebot.com"), ("script", "consentmanager")],
    # CDN / Hosting
    "Cloudflare": [("header", "server", "cloudflare"), ("header", "cf-ray", "")],
    "AWS CloudFront": [("header", "x-amz-cf-id", ""), ("header", "x-amz-cf-pop", "")],
    "Akamai": [("header", "x-akamai", ""), ("header", "server", "akamai")],
    "Azure": [("header", "x-azure", ""), ("header", "x-ms-", "")],
    "Vercel": [("header", "x-vercel", ""), ("header", "server", "vercel")],
    "Netlify": [("header", "server", "netlify"), ("header", "x-nf-", "")],
    # E-commerce
    "Shopify": [("script", "shopify"), ("meta", "generator", "shopify")],
    "WooCommerce": [("script", "woocommerce")],
}


def _detect_tech_stack(company_url: str, timeout: int = 6) -> dict | None:
    """
    Lightweight tech stack detection via HTTP headers and HTML inspection.
    No external API needed — just reads the company website's response.
    Returns dict with detected technologies and modernity assessment.
    """
    if not company_url:
        return None

    url = company_url if company_url.startswith("http") else f"https://{company_url}"

    try:
        resp = http_requests.get(url, timeout=timeout, headers={
            "User-Agent": "Mozilla/5.0 (compatible; AdaptabilityScanner/1.0)"
        }, allow_redirects=True)
        if resp.status_code >= 400:
            return None

        headers = {k.lower(): v.lower() for k, v in resp.headers.items()}
        html = resp.text[:80_000].lower()

        detected = []
        for tech, signatures in _TECH_SIGNATURES.items():
            for sig_type, sig_key, *sig_val in signatures:
                val = sig_val[0] if sig_val else ""
                matched = False
                if sig_type == "header":
                    header_val = headers.get(sig_key, "")
                    matched = val in header_val if val else sig_key in headers
                elif sig_type == "meta":
                    matched = f'name="{sig_key}"' in html and val in html
                elif sig_type == "script":
                    matched = sig_key in html
                elif sig_type == "attr":
                    matched = sig_key in html

                if matched:
                    detected.append(tech)
                    break

        # Deduplicate
        detected = list(dict.fromkeys(detected))

        # Assess modernity
        modern_signals = {"React", "Next.js", "Vue.js", "Angular", "Contentful",
                          "Cloudflare", "AWS CloudFront", "Vercel", "Netlify", "Google Analytics 4"}
        legacy_signals = {"WordPress", "Drupal", "Sitecore", "Episerver/Optimizely"}

        modern_count = sum(1 for t in detected if t in modern_signals)
        legacy_count = sum(1 for t in detected if t in legacy_signals)

        if modern_count >= 2:
            assessment = "modern"
        elif legacy_count >= 1 and modern_count == 0:
            assessment = "legacy"
        else:
            assessment = "mixed"

        # Check HTTPS and HTTP/2 support
        uses_https = resp.url.startswith("https://")
        uses_http2 = "h2" in headers.get("alt-svc", "")

        return {
            "technologies": detected,
            "assessment": assessment,
            "modern_count": modern_count,
            "legacy_count": legacy_count,
            "uses_https": uses_https,
        }
    except Exception as e:
        print(f"   [TechStack] Detection failed (non-blocking): {e}")
        return None


# --- STAKEHOLDER NAME VALIDATION ---
# Placeholder names that indicate no real person was found
_PLACEHOLDER_NAMES = {"unknown", "unnamed", "n/a", "tbd", "none", "not found",
                      "not available", "ei tiedossa", "tuntematon", "not specified",
                      "last name not specified", "name not found"}

# Generic role-only strings that are not person names
_ROLE_ONLY_PATTERNS = re.compile(
    r'^(?:cto|cio|cpo|cdo|cfo|ceo|coo|vp|svp|evp|director|head|manager|lead|chief)\b',
    re.IGNORECASE
)

# Parenthetical placeholder patterns like "Pirja (Last Name Not Specified)"
_PAREN_PLACEHOLDER_RE = re.compile(
    r'\(\s*(?:last\s+name\s+)?not\s+(?:specified|found|available|known)'
    r'|unknown\s+(?:last\s+name|surname)'
    r'|\(?\s*last\s+name\s+unknown\s*\)?',
    re.IGNORECASE
)


def _is_valid_full_name(name: str) -> bool:
    """
    Check if a name is a valid full name (first + last name minimum).
    Returns False for single names, placeholders, role-only strings, and
    names with placeholder markers like "(Last Name Not Specified)".
    """
    if not name:
        return False

    # Strip and check for placeholder patterns
    clean = name.strip()
    if clean.lower() in _PLACEHOLDER_NAMES:
        return False

    # Check for parenthetical placeholders like "(Last Name Not Specified)"
    if _PAREN_PLACEHOLDER_RE.search(clean):
        return False

    # Remove any parenthetical content for word count (e.g. "Pirja (something)")
    name_without_parens = re.sub(r'\([^)]*\)', '', clean).strip()

    # Check if it's a role-only string
    if _ROLE_ONLY_PATTERNS.match(name_without_parens):
        return False

    # Split into word parts — need at least 2 real name parts
    parts = [p for p in name_without_parens.split() if len(p) > 1]
    if len(parts) < 2:
        return False

    # All parts should look like name parts (start with letter, mostly alpha)
    for part in parts:
        # Allow hyphens and apostrophes in names (O'Brien, Heiskanen-Smith)
        alpha_chars = sum(1 for c in part if c.isalpha() or c in "-'")
        if alpha_chars < len(part) * 0.7:
            return False

    return True


_SENIORITY_TIERS = [
    (1, re.compile(r'\b(CEO|COO|President|Chairman|Founder|Co-Founder|Toimitusjohtaja|Maajohtaja|Managing\s+Director|Country\s+Manager|Group\s+Executive|General\s+Manager|Pääjohtaja|Hallituksen\s+puheenjohtaja)\b', re.I)),
    (2, re.compile(r'\b(CTO|CIO|CDO|CPO|CFO|CHRO|CMO|Chief\s+\w+\s+Officer|Executive\s+Vice\s+President|EVP)\b', re.I)),
    (3, re.compile(r'\b(SVP|Senior\s+Vice\s+President|VP|Vice\s+President|Board\s+Member|Hallituksen\s+jäsen)\b', re.I)),
    (4, re.compile(r'\b(Director)\b', re.I)),
    (5, re.compile(r'\b(Head\s+of)\b', re.I)),
    (6, re.compile(r'\b(Senior\s+Manager|Manager|Lead)\b', re.I)),
]

# Role descriptions that are NOT actual job titles — always invalid as stakeholders
_INVALID_ROLE_FRAGMENTS = {
    "unspecified", "role not", "no role", "returning employee",
    "not specified", "not found", "not available", "unknown role",
    "role unknown", "position unknown", "tbd", "n/a",
}

# Maps detected constraint keywords → functional roles most responsible for that challenge.
# Used both in the prompt (Rule #4 table) and for rescan constraint injection.
_CONSTRAINT_ROLE_MAP = [
    (["legacy", "technical debt", "cloud washing", "failed transformation",
      "monolith", "mainframe", "moderniz", "it debt"],
     ["CTO", "VP Engineering", "Head of Architecture"]),
    (["silo", "bureaucrac", "fragmentation", "cross-functional", "handoff",
      "coordination failure", "organizational friction"],
     ["CDO", "COO", "Head of Strategy & Transformation"]),
    (["agile", "waterfall", "zombie scrum", "fake agile", "delivery",
      "project-based", "sprint"],
     ["VP Engineering", "VP Product", "Head of Delivery"]),
    (["surveillance", "micromanagement", "control culture", "culture",
      "trust deficit", "employee satisfaction", "psychological safety"],
     ["CHRO", "Head of People & Culture", "COO"]),
    (["platform thinking", "platform gap", "no api", "no public api",
      "composable", "design system"],
     ["CTO", "CPO", "Head of Digital"]),
    (["ai readiness", "ai gap", "pilot purgatory", "machine learning",
      "data strategy", "analytics", "automation gap"],
     ["CDO", "Chief AI Officer", "Head of Data & Analytics"]),
    (["financial risk", "cost structure", "revenue decline", "profitab",
      "liquidity"],
     ["CFO", "COO"]),
]


def _get_seniority_tier(role: str) -> int:
    """Return seniority tier (1=highest) for a role string. 7 = unknown/unranked."""
    if not role:
        return 7
    for tier, pattern in _SENIORITY_TIERS:
        if pattern.search(role):
            return tier
    return 7


def _is_valid_role(role: str) -> bool:
    """Return False for placeholder / non-title role strings (e.g. 'Returning Employee (Role Unspecified)')."""
    if not role:
        return False
    role_lower = role.lower()
    return not any(fragment in role_lower for fragment in _INVALID_ROLE_FRAGMENTS)


def _validate_key_people(key_people: list, company_name: str, employees: int = None) -> list:
    """
    Filter key_people list to remove entries with invalid/incomplete names,
    low-relevance stakeholders, and too-junior roles for the company size.
    Results are sorted by seniority (most senior first).

    Removes:
    - People with only first name (no last name)
    - Placeholder names ("Unknown", "N/A", etc.)
    - Names with parenthetical placeholders like "(Last Name Not Specified)"
    - Role-only entries (no actual person name)
    - Manager-level roles at large companies (5000+ employees)
    """
    if not key_people:
        return key_people

    validated = []
    removed = []

    for person in key_people:
        name = (person.get("name") or "").strip()

        if not _is_valid_full_name(name):
            removed.append(name or "(empty)")
            continue

        role = (person.get("role") or "").strip()

        # Reject placeholder / non-title role strings regardless of company size
        if not _is_valid_role(role):
            removed.append(f"{name} ({role or 'no role'}) [invalid role description]")
            continue

        tier = _get_seniority_tier(role)

        # Large companies (5000+): reject known Manager/Lead tier (tier 6).
        # Tier 7 (unrecognised pattern) is kept — it may be a legitimate senior title
        # with an unusual or non-English phrasing. _is_valid_role() above already
        # blocks the genuinely invalid entries (e.g. "Returning Employee (Role Unspecified)").
        if employees and employees >= 5000 and tier == 6:
            removed.append(f"{name} ({role}) [Manager-level role not suitable for {employees:,} employee org]")
            continue

        if employees and employees >= 200 and tier == 6:
            # Mid-size: allow managers only if we'd otherwise have < 3 stakeholders
            person["_seniority_tier"] = tier
            person["_maybe_too_junior"] = True
            validated.append(person)
            continue

        person["_seniority_tier"] = tier
        validated.append(person)

    # For mid-size companies: drop junior roles if we have enough senior ones
    if employees and employees >= 200:
        senior = [p for p in validated if not p.get("_maybe_too_junior")]
        junior = [p for p in validated if p.get("_maybe_too_junior")]
        if len(senior) >= 3 and junior:
            for j in junior:
                removed.append(f"{j.get('name')} ({j.get('role')}) [enough senior stakeholders found]")
            validated = senior

    # Sort by seniority tier (most senior first)
    validated.sort(key=lambda p: p.get("_seniority_tier", 7))

    # Clean up internal fields
    for p in validated:
        p.pop("_seniority_tier", None)
        p.pop("_maybe_too_junior", None)

    # Cap at 6
    if len(validated) > 6:
        validated = validated[:6]

    if removed:
        print(f"   🚫 [Key People] Removed {len(removed)} stakeholder(s): {', '.join(removed)}")

    return validated

def _find_balanced_json_object(text, start=0):
    """
    Find the first balanced JSON object in text starting from position `start`.
    Properly handles nested braces and string literals (including escaped quotes).
    Returns the substring from opening { to its matching }, or None if not found.

    Why this exists: regex `\{.*?\}` (non-greedy) stops at the FIRST closing brace,
    truncating nested JSON like {"a": {"b": 1}, "c": 2} → {"a": {"b": 1}.
    Greedy `\{.*\}` can overshoot if there are multiple JSON objects in the text.
    Balanced-brace walking handles both cases correctly.
    """
    idx = text.find('{', start)
    if idx == -1:
        return None

    depth = 0
    in_string = False
    i = idx
    while i < len(text):
        ch = text[i]
        if in_string:
            if ch == '\\':
                i += 2  # skip escaped character
                continue
            if ch == '"':
                in_string = False
        else:
            if ch == '"':
                in_string = True
            elif ch == '{':
                depth += 1
            elif ch == '}':
                depth -= 1
                if depth == 0:
                    return text[idx:i + 1]
        i += 1

    return None


def extract_json_from_text(text):
    """
    Robustly extracts the first JSON object found in a string.
    Handles Markdown code blocks, conversational filler, trailing commas, etc.
    """
    if not text:
        return None

    # 1. Try finding a markdown block first — extract inner content, then balanced-brace match
    candidate = None
    block_match = re.search(r"```json\s*(.*?)\s*```", text, re.DOTALL)
    if block_match:
        block_content = block_match.group(1)
        candidate = _find_balanced_json_object(block_content)

    # 2. Fallback: balanced-brace match on the full text
    if not candidate:
        candidate = _find_balanced_json_object(text)

    if not candidate:
        return None

    # 3. Try parsing as-is first (fast path)
    try:
        json.loads(candidate, strict=False)
        return candidate
    except json.JSONDecodeError:
        pass

    # 4. Auto-repair common LLM JSON errors before giving up
    repaired = candidate
    # Remove trailing commas before } or ] (most common LLM error)
    repaired = re.sub(r',\s*([}\]])', r'\1', repaired)
    # Fix missing commas between } { or } " or ] { patterns (second most common)
    repaired = re.sub(r'(\})\s*(\{)', r'\1,\2', repaired)
    repaired = re.sub(r'(\})\s*(")', r'\1,\2', repaired)
    repaired = re.sub(r'(\])\s*(")', r'\1,\2', repaired)
    # Fix missing commas after true/false/null before next key
    repaired = re.sub(r'(true|false|null)\s*\n\s*(")', r'\1,\2', repaired)
    # Fix missing commas after numbers before next key (e.g. `50\n  "key"`)
    repaired = re.sub(r'(\d)\s*\n\s*(")', r'\1,\2', repaired)
    # Remove control characters that break JSON (newlines inside strings)
    repaired = re.sub(r'[\x00-\x08\x0b\x0c\x0e-\x1f]', ' ', repaired)

    try:
        json.loads(repaired, strict=False)
        return repaired
    except json.JSONDecodeError:
        pass

    # 5. Iterative position-based repair: use JSONDecodeError.pos to surgically
    #    insert missing commas/colons at the exact failure point. Handles deeply
    #    nested errors that regex patterns miss (e.g. char 10267 in a 12K payload).
    #    30 iterations handles large JSON (13K+) with many scattered comma errors.
    for _attempt in range(30):
        try:
            json.loads(repaired, strict=False)
            return repaired
        except json.JSONDecodeError as e:
            msg = str(e)
            pos = e.pos
            if pos is None or pos >= len(repaired):
                break
            if "Expecting ',' delimiter" in msg:
                repaired = repaired[:pos] + ',' + repaired[pos:]
            elif "Expecting ':' delimiter" in msg:
                repaired = repaired[:pos] + ':' + repaired[pos:]
            elif "Expecting value" in msg and pos > 0 and repaired[pos-1] == ',':
                # Trailing comma before unexpected token — remove it
                repaired = repaired[:pos-1] + repaired[pos:]
            else:
                break  # Unknown error type, stop

    try:
        json.loads(repaired, strict=False)
        return repaired
    except json.JSONDecodeError:
        pass

    # 6. Return best-effort repaired text for downstream handling
    #    (preserves partial repair progress rather than discarding it)
    return repaired

_SOFT_404_PATTERNS = re.compile(
    r'page\s*not\s*found|'
    r'404\s*(error|not\s*found)|'
    r'not\s*found\s*error|'
    r'could\s*n.?t\s*(find|locate)\s*(this|that|the)?\s*page|'
    r'no\s*longer\s*(available|exists)|'
    r'has\s*been\s*(removed|deleted|moved)|'
    r'this\s*page\s*(does\s*n.?t|doesn.?t)\s*exist|'
    r'requested\s*page\s*(was|is)\s*not\s*found',
    re.IGNORECASE
)


# --- Date extraction from URLs and page body ---
_MONTH_MAP = {
    "january": 1, "february": 2, "march": 3, "april": 4, "may": 5, "june": 6,
    "july": 7, "august": 8, "september": 9, "october": 10, "november": 11, "december": 12,
    "jan": 1, "feb": 2, "mar": 3, "apr": 4, "jun": 6,
    "jul": 7, "aug": 8, "sep": 9, "oct": 10, "nov": 11, "dec": 12,
}

# URL path patterns (highest reliability)
_URL_DATE_FULL = re.compile(r'/(\d{4})[/-](\d{1,2})[/-](\d{1,2})')
_URL_DATE_COMPACT = re.compile(r'/(\d{4})(\d{2})(\d{2})[/-]')
_URL_DATE_YEAR = re.compile(r'/(?:press[_-]?releases?|news|blog|articles?)/(\d{4})/', re.I)

# HTML meta tag patterns
_META_DATE_PATTERNS = [
    re.compile(r'article:published_time["\s]+content=["\'](\d{4}-\d{2}-\d{2})', re.I),
    re.compile(r'<time[^>]+datetime=["\'](\d{4}-\d{2}-\d{2})', re.I),
    re.compile(r'datepublished["\s:]+["\'](\d{4}-\d{2}-\d{2})', re.I),
]

# Body text patterns (require prefix keyword to avoid copyright-year false positives)
_BODY_DATE_ISO = re.compile(r'(?:published|posted|date|updated)\s*(?:on|:)?\s*(\d{4}-\d{2}-\d{2})', re.I)
_BODY_DATE_PROSE = re.compile(
    r'(?:published|posted|date|updated)\s*(?:on|:)?\s*'
    r'(\w+)\s+(\d{1,2}),?\s+(\d{4})', re.I
)

_DATE_FLOOR = date(2000, 1, 1)


def _extract_page_date(url, body_chunk):
    """
    Extract publication date from URL path and/or page body content.
    Returns datetime.date or None. Priority: URL > meta tags > body text.
    """
    from datetime import timedelta
    today = date.today()
    ceiling = today + timedelta(days=7)

    def _valid(d):
        return d and _DATE_FLOOR <= d <= ceiling

    # 1. URL path — full date (YYYY/MM/DD or YYYY-MM-DD)
    m = _URL_DATE_FULL.search(url)
    if m:
        try:
            d = date(int(m.group(1)), int(m.group(2)), int(m.group(3)))
            if _valid(d):
                return d
        except (ValueError, OverflowError):
            pass

    # 2. URL path — compact (YYYYMMDD-)
    m = _URL_DATE_COMPACT.search(url)
    if m:
        try:
            d = date(int(m.group(1)), int(m.group(2)), int(m.group(3)))
            if _valid(d):
                return d
        except (ValueError, OverflowError):
            pass

    # 3. URL path — year only (/news/2021/) → Jan 1 of that year (conservative)
    m = _URL_DATE_YEAR.search(url)
    if m:
        try:
            d = date(int(m.group(1)), 1, 1)
            if _valid(d):
                return d
        except (ValueError, OverflowError):
            pass

    if not body_chunk:
        return None

    # 4. HTML meta tags (high reliability)
    for pat in _META_DATE_PATTERNS:
        m = pat.search(body_chunk)
        if m:
            try:
                parts = m.group(1).split("-")
                d = date(int(parts[0]), int(parts[1]), int(parts[2]))
                if _valid(d):
                    return d
            except (ValueError, IndexError, OverflowError):
                pass

    # 5. Body text — ISO date with prefix
    m = _BODY_DATE_ISO.search(body_chunk)
    if m:
        try:
            parts = m.group(1).split("-")
            d = date(int(parts[0]), int(parts[1]), int(parts[2]))
            if _valid(d):
                return d
        except (ValueError, IndexError, OverflowError):
            pass

    # 6. Body text — prose date with prefix ("February 18, 2021")
    m = _BODY_DATE_PROSE.search(body_chunk)
    if m:
        month_str = m.group(1).lower()
        month_num = _MONTH_MAP.get(month_str)
        if month_num:
            try:
                d = date(int(m.group(3)), month_num, int(m.group(2)))
                if _valid(d):
                    return d
            except (ValueError, OverflowError):
                pass

    return None


def _verify_url(url, timeout=4, company_name=None):
    """
    Verify a single URL exists via HTTP request.
    Returns a dict with:
      - verified: True if URL is reachable and not a soft 404
      - company_mentioned: True if the company name appears on the page (None if not checked)
      - extracted_date: Publication date from URL/page content (None if not found)
      - content_readable: True when the response contains enough visible text
      - content_excerpt: Visible text used for pre-jury quote validation
    """
    result = {
        "verified": False,
        "company_mentioned": None,
        "extracted_date": None,
        "content_readable": False,
        "content_excerpt": "",
    }
    if not url or not url.startswith("http"):
        return result

    try:
        # Use GET instead of HEAD to read body for soft 404 detection
        resp = http_requests.get(url, timeout=timeout, allow_redirects=True, stream=True,
                                  headers={"User-Agent": "Mozilla/5.0 (compatible; AdaptabilityScanner/1.0)"})

        if resp.status_code >= 400:
            resp.close()
            return result

        # Read a bounded but materially fuller body. The previous 64 KB raw-HTML
        # cap often ended inside navigation/cookie markup and made valid quotes
        # appear absent before the jury ever saw the source.
        body_chunk = ""
        try:
            resp.raw.decode_content = True
            raw = resp.raw.read(524288)
            encoding = resp.encoding or "utf-8"
            body_chunk = raw.decode(encoding, errors="ignore").lower()
        except Exception:
            pass
        resp.close()

        # Check for soft 404 patterns in the page body
        if body_chunk and _SOFT_404_PATTERNS.search(body_chunk):
            return result

        result["verified"] = True
        body_for_text = re.sub(r"<(script|style|noscript)[^>]*>.*?</\1>", " ", body_chunk, flags=re.I | re.S)
        visible_text = html_lib.unescape(re.sub(r"<[^>]+>", " ", body_for_text))
        visible_text = re.sub(r"\s+", " ", visible_text).strip()
        result["content_readable"] = len(visible_text) >= 80
        result["content_excerpt"] = visible_text[:60000]

        # Extract publication date from URL and page body
        result["extracted_date"] = _extract_page_date(url, body_chunk)

        # Check company name presence if provided. A company token in the URL is
        # a weak positive identity hint for this URL-liveness stage; source-claim
        # verification still decides whether it can support analysis.
        if company_name:
            name_lower = company_name.lower()
            name_parts = [p for p in name_lower.split() if len(p) > 2 and p not in ("oy", "oyj", "ab", "ltd", "group", "inc", "gmbh", "oy:n", "abp")]
            url_lower = url.lower()
            if not name_parts:
                result["company_mentioned"] = True
            elif any(part in url_lower for part in name_parts):
                result["company_mentioned"] = True
            elif body_chunk:
                result["company_mentioned"] = any(part in body_chunk for part in name_parts)

        return result

    except Exception:
        return result


def _validate_evidence_urls(links, checklist, max_workers=6, company_name=None):
    """
    URL Validation Gate: Verifies evidence URLs actually exist and are relevant.
    Adds 'verified' field (true/false) to each link and checklist entry.
    Detects soft 404s (pages returning HTTP 200 with 'not found' content).
    Optionally checks company name presence on the page.
    Runs in parallel for speed. Logs summary.
    """
    start = time.time()

    # Collect all unique URLs to check
    url_set = set()
    for item in links + checklist:
        url = (item.get("url") or item.get("source_url") or "").strip()
        if url and url.startswith("http"):
            url_set.add(url)

    if not url_set:
        return links, checklist, 0, 0

    # Verify in parallel — returns dict with verified + company_mentioned
    url_results = {}  # url -> {"verified": bool, "company_mentioned": bool|None}
    with concurrent.futures.ThreadPoolExecutor(max_workers=max_workers) as executor:
        future_to_url = {executor.submit(_verify_url, url, 4, company_name): url for url in url_set}
        for future in concurrent.futures.as_completed(future_to_url):
            url = future_to_url[future]
            try:
                url_results[url] = future.result()
            except Exception:
                url_results[url] = {"verified": False, "company_mentioned": None}

    # Apply results to links
    verified_count = 0
    dead_count = 0
    wrong_entity_count = 0
    date_override_count = 0
    today = date.today()
    for item in links:
        url = (item.get("url") or "").strip()
        if url and url in url_results:
            r = url_results[url]
            item["verified"] = r["verified"]
            item["url_verified"] = r["verified"]
            # Mark sources about wrong entity — don't hard-reject but flag for downstream
            if r["verified"] and r.get("company_mentioned") is False:
                item["wrong_entity"] = True
                wrong_entity_count += 1
            if r["verified"]:
                verified_count += 1
                # Override is_recent with extracted date when available
                extracted = r.get("extracted_date")
                if extracted:
                    new_is_recent = (today - extracted).days < 365
                    old_is_recent = item.get("is_recent")
                    if old_is_recent != new_is_recent:
                        print(f"   📅 [Date Override] {url[:80]} — {extracted}, {'recent' if new_is_recent else 'old'} (was {'recent' if old_is_recent else 'old'} per LLM)")
                        date_override_count += 1
                    item["is_recent"] = new_is_recent
                    item["extracted_date"] = str(extracted)
            else:
                dead_count += 1
        else:
            item["verified"] = False

    # Apply results to checklist
    for item in checklist:
        url = (item.get("source_url") or item.get("url") or "").strip()
        if url and url in url_results:
            r = url_results[url]
            item["verified"] = r["verified"]
            item["url_verified"] = r["verified"]
            if r["verified"] and r.get("company_mentioned") is False:
                item["wrong_entity"] = True
            # Override is_recent with extracted date
            extracted = r.get("extracted_date")
            if r["verified"] and extracted:
                item["is_recent"] = (today - extracted).days < 365

    elapsed = int((time.time() - start) * 1000)
    total = verified_count + dead_count
    wrong_msg = f", {wrong_entity_count} wrong entity" if wrong_entity_count else ""
    date_msg = f", {date_override_count} date overrides" if date_override_count else ""
    print(f"   🔗 [URL Validation] {verified_count}/{total} URLs verified, {dead_count} dead/soft-404{wrong_msg}{date_msg} ({elapsed}ms)")

    return links, checklist, verified_count, dead_count


def _apply_scan_time_source_classification(data: dict) -> dict:
    """Ensure newly scanned records are clean-by-construction before save.

    The Sanitizer is only a legacy cleanup/audit tool. New scan outputs should
    already preserve company-specific evidence and mark generic foresight/regulatory
    sources as context-only. Non-dict/callable items are removed before later
    summary/rendering code calls `.get()`.
    """
    if not isinstance(data, dict):
        return data

    source_fields = [
        "evidence_links",
        "foresight_links",
        "foresight_evidence",
        "deep_dive_findings",
        "source_cards",
    ]
    for field in source_fields:
        if isinstance(data.get(field), list):
            cleaned = []
            dropped = 0
            for item in data[field]:
                if not isinstance(item, dict):
                    dropped += 1
                    continue
                item = classify_generation_context(item)
                item = apply_language_metadata(item)
                cleaned.append(item)
            if dropped:
                print(f"   ⚠️ [Source Hygiene] Dropped {dropped} non-dict item(s) from {field}")
            data[field] = cleaned
    return data


def _repair_json_with_sonnet(raw_text, company_name, is_structuring=False, citations=None):
    """
    JSON Structuring / Repair Gate.

    Two modes:
    - Structuring (is_structuring=True): Standard Pass 2 of the two-pass pipeline.
      Sonnet's web search produced free-form evidence; this call converts it to JSON.
      No confidence penalty — this is the designed flow.
    - Repair (is_structuring=False): Fallback when a model returns unstructured text
      instead of JSON. Caps confidence at 70 to signal reduced reliability.
    """
    anthropic_key = os.environ.get("ANTHROPIC_API_KEY", "")
    if "=" in anthropic_key:
        try: anthropic_key = anthropic_key.split("=", 1)[1]
        except: pass
    anthropic_key = anthropic_key.strip().strip('"').strip("'")
    if not anthropic_key:
        return None

    try:
        client = anthropic.Anthropic(api_key=anthropic_key)

        # Build citation context if available (helps structuring pass match URLs to findings)
        citations_section = ""
        if citations:
            citation_lines = [f"  - {c.get('title', 'Source')}: {c['url']}" for c in citations[:30]]
            citations_section = "\n\nVERIFIED SOURCE URLs harvested from web search (use these to match findings to real URLs):\n" + "\n".join(citation_lines)

        if is_structuring:
            intro = f"""The following text is a forensic reality-check analysis for "{company_name}" produced by a web-search-grounded AI model. Your job is to structure this analysis into valid JSON, preserving ALL evidence, scores, and assessments exactly as stated."""
        else:
            intro = f"""The following text is a forensic reality-check analysis for "{company_name}" that was returned by a search-grounded AI model, but it is NOT valid JSON. Your job is to extract the data and return it as valid JSON."""

        structuring_prompt = f"""{intro}

The expected JSON structure is:
{{
  "findings": [
    {{"category": "Operating Model", "label": "Brief Label", "found": true, "sentiment": "positive", "snippet": "Evidence quote...", "title": "Source Title", "url": "https://...", "is_recent": true}}
  ],
  "reality_score_adjustment": <integer -50 to +50>,
  "confidence_score": <integer 0-100>,
  "washing_flag": <boolean>,
  "washing_type": <string or null>
}}

Extract as much data as possible from the text below. Preserve all findings, URLs, and evidence. If a field cannot be determined, use sensible defaults (0 for reality_score_adjustment, empty arrays for lists, false for booleans).

CRITICAL — snippet vs title are DIFFERENT fields:
- "title" = the source page or article title
- "snippet" = an actual quote, data point, or substantive excerpt found IN the source
If you cannot extract a real snippet from the source text, leave snippet as an empty string "".
Do NOT copy the title into the snippet field. A finding with title-only and no snippet is
acceptable — the downstream pipeline filters incomplete findings correctly only when snippet
is honestly empty.

IMPORTANT for confidence_score: Do NOT default to 0. Assess confidence based on the evidence density in the text:
- Count findings with real URLs and specific quotes → more evidence = higher confidence
- 0-40: Very few sources, evidence is generic or very old
- 41-70: Some specific evidence found, but mixed recency or limited sources
- 71-100: Multiple independent sources confirm findings, evidence is recent and specific
If the text contains 3+ findings with URLs, confidence should be at least 50.
{citations_section}

TEXT TO PARSE:
{raw_text[:12000]}

Return ONLY valid JSON, no explanation."""

        response = client.messages.create(
            model="claude-sonnet-4-6",
            max_tokens=12000,
            messages=[{"role": "user", "content": structuring_prompt}]
        )
        response_text = response.content[0].text if response.content else ""
        json_payload = extract_json_from_text(response_text)
        if not json_payload:
            return None

        mode_label = "Structuring" if is_structuring else "JSON Repair"
        try:
            result = json.loads(json_payload, strict=False)
        except json.JSONDecodeError as je:
            # extract_json_from_text already tried 30 repair iterations.
            # Try one more aggressive pass: re-run regex + position repair
            # on the already-partially-repaired output.
            print(f"   ⚠️ [{mode_label}] Nearly valid JSON ({je}). Running extra repair pass...")
            re_repaired = extract_json_from_text(json_payload)
            if re_repaired:
                try:
                    result = json.loads(re_repaired, strict=False)
                except json.JSONDecodeError as je2:
                    print(f"   ❌ [{mode_label}] Extra repair pass also failed: {je2}")
                    return None
            else:
                return None

        # Only cap confidence for repair mode (unexpected format recovery)
        # Structuring mode is the designed flow — no penalty
        if not is_structuring and result.get("confidence_score", 0) > 70:
            result["confidence_score"] = 70
        return result
    except Exception as e:
        mode_label = "Structuring" if is_structuring else "JSON Repair"
        print(f"   ❌ [{mode_label}] Sonnet call failed: {e}")
        return None


def _structure_with_model(raw_text, company_name, model_name="gpt-5.4"):
    """Structure free-form forensic evidence text into JSON using GPT/Claude."""
    structuring_prompt = f"""The following text is a forensic reality-check analysis for "{company_name}" produced by a web-search-grounded AI model. Your job is to structure this analysis into valid JSON, preserving ALL evidence, scores, and assessments exactly as stated.

The expected JSON structure is:
{{
  "findings": [
    {{"category": "Operating Model", "label": "Brief Label", "found": true, "sentiment": "positive", "snippet": "Evidence quote...", "title": "Source Title", "url": "https://...", "is_recent": true}}
  ],
  "reality_score_adjustment": <integer -50 to +50>,
  "confidence_score": <integer 0-100>,
  "washing_flag": <boolean>,
  "washing_type": <string or null>
}}

Extract as much data as possible from the text below. Preserve all findings, URLs, and evidence. If a field cannot be determined, use sensible defaults.
Do not copy a page title into snippet. If no real source excerpt exists, leave snippet empty.

TEXT TO PARSE:
{raw_text[:12000]}

Return ONLY valid JSON, no explanation."""
    try:
        text, used_model = call_json_model_chain(
            structuring_prompt,
            (model_name, "claude-sonnet-4-6") if model_name.startswith("gpt-") else (model_name, "gpt-5.4"),
            max_tokens=8000,
            timeout=90,
        )
        json_payload = extract_json_from_text(text)
        if not json_payload:
            return None
        print(f"   ✅ [JSON Structure] Structured via {used_model}")
        return json.loads(json_payload, strict=False)
    except Exception as e:
        print(f"   ❌ [JSON Structure] Structuring failed: {e}")
        return None

def _build_previous_context_block(previous_context):
    """
    Builds an optional prompt section from previous scan data.
    Uses VERIFICATION-FIRST approach with MULTI-SCAN LOOKBACK (up to 3 scans).
    The most recent summary is the primary checklist; older summaries reveal
    persistent patterns that should carry extra weight.
    Returns empty string if no previous context is available.
    """
    if not previous_context or not previous_context.get("last_scanned"):
        return ""

    # --- MOST RECENT SCAN (full detail) ---
    prev_findings = previous_context.get("previous_findings", [])
    findings_summary = ""
    if prev_findings:
        items = []
        for f in prev_findings[:6]:
            sentiment = f.get("sentiment", "neutral")
            label = f.get("query", f.get("label", "Unknown"))
            items.append(f"  - [{sentiment.upper()}] {label}")
        findings_summary = "\n".join(items)

    prev_evidence = previous_context.get("previous_evidence", [])
    evidence_summary = ""
    if prev_evidence:
        items = [f"  - {e.get('title', 'Unknown')}: {e.get('snippet', '')[:80]}" for e in prev_evidence[:4]]
        evidence_summary = "\n".join(items)

    prev_summary = previous_context.get("previous_summary", "")
    summary_block = ""
    if prev_summary and len(prev_summary) > 20:
        truncated = prev_summary[:1500]
        summary_block = f"""
    === SCAN -1 SUMMARY (MOST RECENT — PRIMARY VERIFICATION CHECKLIST) ===
    Date: {previous_context.get('last_scanned', 'Unknown')}
    {truncated}
    === END SCAN -1 SUMMARY ===
    """

    # --- OLDER SCANS (summaries only, for persistent pattern detection) ---
    older_summaries_block = ""
    scan_history = previous_context.get("scan_history", [])
    if scan_history:
        older_parts = []
        for idx, older_scan in enumerate(scan_history, start=2):
            older_summary = older_scan.get("summary_text", "")
            if older_summary and len(older_summary) > 20:
                older_date = older_scan.get("date", "Unknown")
                older_reality = older_scan.get("reality_score", "N/A")
                older_intent = older_scan.get("intent_score", "N/A")
                older_washing = older_scan.get("washing_type", "None")
                older_parts.append(f"""
    === SCAN -{idx} SUMMARY (OLDER — PATTERN DETECTION) ===
    Date: {older_date} | Intent: {older_intent}/100 | Reality: {older_reality}/100 | Washing: {older_washing}
    {older_summary[:800]}
    === END SCAN -{idx} SUMMARY ===""")
        if older_parts:
            older_summaries_block = "\n".join(older_parts)

    # Count how many scans we have for the instructions
    total_scans = 1 + len(scan_history)

    # --- PERSISTENCE INSTRUCTION (only when we have 2+ scans) ---
    persistence_instruction = ""
    if scan_history:
        persistence_instruction = f"""
    PERSISTENCE ANALYSIS ({total_scans} previous scans available):
    You have summaries from {total_scans} previous scans above. Compare them:
      - PERSISTENT THEME: A topic/issue that appears in 2+ scans → HIGH CONFIDENCE.
        These are near-certain real patterns, not search variance. Weight them heavily.
      - ONE-TIME FINDING: A topic that appeared in only 1 scan and was never seen again →
        could be search variance. Still carry forward, but note lower certainty.
      - ESCALATING PATTERN: A topic that gets worse across scans → flag as urgent concern.
      - FADING PATTERN: A topic present in older scans but absent in the most recent →
        may be genuinely resolving. Still verify, but less weight than persistent themes.

    When a negative finding appears across ALL previous scans, it is almost certainly
    a REAL structural issue, not a one-off. Your score MUST reflect this."""

    return f"""

    --- PREVIOUS SCAN CONTEXT (RESCAN MODE — {total_scans} SCAN{'S' if total_scans > 1 else ''} AVAILABLE) ---
    Most Recent Scan: {previous_context.get('last_scanned', 'Unknown')}
    Previous Intent Score: {previous_context.get('previous_intent', 'N/A')}/100
    Previous Reality Score: {previous_context.get('previous_reality', 'N/A')}/100
    Previous Confidence: {previous_context.get('previous_confidence', 'N/A')}/100
    Previous Washing Flag: {previous_context.get('previous_washing', 'None')}
    {summary_block}
    {older_summaries_block}
    Previous Key Findings (from most recent scan):
{findings_summary if findings_summary else '    (No previous findings available)'}

    Previous Evidence Sources (from most recent scan):
{evidence_summary if evidence_summary else '    (No previous evidence available)'}

    RESCAN INSTRUCTIONS (VERIFICATION-FIRST — CRITICAL):

    SEARCH BUDGET ALLOCATION (YOU HAVE 10 WEB SEARCHES — USE THEM IN THIS ORDER):
    ┌─────────────────────────────────────────────────────────────────┐
    │ Searches 1-3:  VERIFICATION (Steps 1-3 below)                 │
    │   → Re-verify previous evidence URLs and key claims           │
    │   → Combine multiple verifications into single broad searches  │
    │     e.g. `"{previous_context.get('company_name', 'company')}" site:linkedin.com OR site:kauppalehti.fi` │
    │                                                                │
    │ Searches 4-10: NEW JURY EVIDENCE (Step 4 below)               │
    │   → One search per jury, combine sub-queries with OR           │
    │   → Prioritize juries where previous evidence was weakest      │
    └─────────────────────────────────────────────────────────────────┘
    YOU MUST USE YOUR FIRST SEARCHES FOR VERIFICATION. Do NOT skip to juries.
    If you run out of searches before covering all juries, that is acceptable —
    verification of existing evidence is MORE IMPORTANT than finding new evidence.

    STEP 1 - VERIFY THE MOST RECENT SUMMARY (YOUR #1 PRIORITY):
    Read SCAN -1 SUMMARY above carefully. It contains specific claims,
    themes, diagnoses, and terminology about this company. These are your verification targets.

    For EACH specific claim in the summary (e.g. "Digital Taylorism", "legacy silos",
    "AI Washing", "strong engineering culture", etc.):
      a) Search for the SAME themes using the SAME words and concepts.
      b) Try to find the SAME sources that were previously cited.
      c) Assign a status:
         - "CONFIRMED": You found the same or similar evidence again. The claim holds.
         - "RESOLVED": You found SPECIFIC NEW counter-evidence that DISPROVES the claim.
           (e.g. previous claim was "layoffs" → you found "major hiring campaign 2025")
         - "UNVERIFIED": You could NOT find evidence for or against the claim.
           CRITICAL: "UNVERIFIED" = CARRY FORWARD AS-IS. Do NOT flip it.
           Not finding something again is NOT proof of improvement — it is search variance.

    STEP 2 - CHECK FOR PERSISTENT PATTERNS ACROSS ALL SCANS:
    Compare ALL available scan summaries (Scan -1 through Scan -{total_scans}).
    Identify themes that RECUR across multiple scans — these are persistent patterns
    that carry higher certainty than any single finding.
    {persistence_instruction}

    STEP 3 - VERIFY PREVIOUS EVIDENCE SOURCES:
    For EACH evidence source listed above, try to access or re-find it.
    If the same URL/source still exists and says the same thing → CONFIRMED.
    If you cannot find it → UNVERIFIED (carry forward).

    STEP 4 - SEARCH FOR NEW EVIDENCE (USE REMAINING SEARCHES):
    ONLY AFTER completing Steps 1-3, run the normal jury searches for any
    NEW evidence not covered by the previous analyses.

    STEP 5 - SCORING (CRITICAL — READ CAREFULLY):

    FUNDAMENTAL RULE: Previous findings that are NOT proven "fixed" by new counter-evidence
    are STILL CURRENT FINDINGS. They are NOT historical — they are ACTIVE issues.
    You MUST include them in your scoring as if you found them yourself today.

    Your reality_score_adjustment must be calculated from the COMPLETE evidence set:
      - ALL confirmed claims from previous scans → ACTIVE (full weight)
      - ALL unverified previous claims → ACTIVE (full weight, not finding them again ≠ fixed)
      - PERSISTENT themes (found in 2+ scans) → HIGHEST WEIGHT
      - ALL new findings (positive or negative) → ACTIVE
      - RESOLVED claims → ONLY removed if you found SPECIFIC NEW counter-evidence
        that proves the issue was fixed (e.g. "layoffs" → "major hiring campaign 2025")

    SCORING CONSISTENCY: If evidence has not materially changed, the score MUST NOT
    materially change. A company cannot transform in days or weeks. If the previous
    Reality Score was {previous_context.get('previous_reality', 'N/A')}/100 and you found
    no significant new positive or negative evidence, your score adjustment should produce
    a result within ±10 of that score. A larger change requires EXPLICIT new evidence.
    --- END PREVIOUS CONTEXT ---
    """


def _build_evidence_anchors(previous_context):
    """
    Extracts concrete URLs and keywords from previous scan data to anchor
    the current scan. This ensures the scanner re-verifies known sources and
    searches for the same key terms, reducing URL overlap drift to near-zero.
    Returns empty string if no previous context or no usable anchors.
    """
    if not previous_context:
        return ""

    anchors = []

    # 1. Extract previous evidence URLs as mandatory re-check targets
    prev_evidence = previous_context.get("previous_evidence", [])
    url_lines = []
    for e in prev_evidence[:6]:
        url = e.get("url", "")
        title = e.get("title", "Unknown")
        if url and url.startswith("http"):
            url_lines.append(f'    - "{title}": {url}')

    # 2. Extract key finding labels/keywords from previous findings
    prev_findings = previous_context.get("previous_findings", [])
    keyword_set = set()
    for f in prev_findings[:8]:
        label = f.get("label", "")
        if label and len(label) > 3:
            keyword_set.add(label)
        snippet = f.get("snippet", "")
        if snippet and len(snippet) > 10:
            # Extract quoted terms or significant phrases (first 60 chars)
            keyword_set.add(snippet[:60].strip())

    keyword_lines = [f'    - "{kw}"' for kw in list(keyword_set)[:8]]

    if url_lines:
        anchors.append(f"""    EVIDENCE ANCHORING — MANDATORY URL RE-VERIFICATION:
    The following URLs were found in the previous scan. You MUST search for and
    re-verify each one BEFORE running fresh jury searches. For each URL:
    - If accessible and still relevant → include it in your findings as CONFIRMED
    - If the content has changed → note the change
    - If not accessible → mark as UNVERIFIED (carry forward the previous finding)

    Previous evidence URLs:
{chr(10).join(url_lines)}""")

    if keyword_lines:
        anchors.append(f"""    KEYWORD ANCHORING — SEARCH CONTINUITY:
    The following key findings/terms were identified in the previous scan.
    Include these as ADDITIONAL search terms alongside your standard jury queries.
    Example: If a keyword is "legacy migration project", add a search like
    `"{previous_context.get('company_name', 'company')}" "legacy migration"` to verify if it still holds.

    Previous key findings to re-verify:
{chr(10).join(keyword_lines)}""")

    company_evidence_candidates = previous_context.get("company_evidence_db_candidates", [])
    candidate_lines = []
    for item in company_evidence_candidates[:8]:
        url = item.get("url", "")
        title = item.get("title") or "Company Evidence DB source"
        source_type = item.get("source_type") or "source"
        provider = item.get("provider") or "stored acquisition"
        cached_excerpt = (item.get("cached_excerpt") or item.get("snippet") or "").strip()
        if url and url.startswith("http"):
            line = f'    - "{title}" ({source_type}, {provider}): {url}'
            if cached_excerpt:
                line += f"\n      Cached extract for orientation only: {cached_excerpt[:180]}"
            candidate_lines.append(line)
        elif cached_excerpt:
            candidate_lines.append(
                f'    - "{title}" ({source_type}, {provider}): cached extract only, no live URL. '
                f"Use as source-discovery context, not as a verified factual source.\n"
                f"      Cached extract: {cached_excerpt[:220]}"
            )

    if candidate_lines:
        anchors.append(f"""    COMPANY EVIDENCE DB CANDIDATES — SOURCE ACQUISITION TARGETS:
    The following URLs were collected by the Admin Company Evidence DB workflow
    using source-acquisition tools such as Firecrawl/Jina/Coresignal. Treat them
    as CANDIDATE SOURCES ONLY, not verified facts. You MUST re-check entity,
    content, and claim support before using them in findings, scoring, or prose.
    If a page cannot be fetched live but the cached extract is relevant, use it
    only as extraction context and still verify the claim/entity conservatively.

    Company Evidence DB candidate URLs:
{chr(10).join(candidate_lines)}""")

    if not anchors:
        return ""

    return "\n\n".join(anchors)


def _build_detected_constraints_block(previous_context: dict) -> str:
    """
    For rescans: extract previously detected negative findings and map them to
    the functional roles most responsible for each challenge. Injected directly
    into the STAKEHOLDER IDENTIFICATION section so the model searches for the
    right role types rather than whoever happens to be easiest to find.
    Returns empty string on first scans (no previous context).
    """
    if not previous_context:
        return ""
    prev_findings = previous_context.get("previous_findings", [])
    if not prev_findings:
        return ""

    negative_labels = [
        (f.get("query") or f.get("label") or "").strip()
        for f in prev_findings
        if f.get("sentiment") == "negative" and (f.get("query") or f.get("label"))
    ]
    if not negative_labels:
        return ""

    lines = []
    seen_roles = set()
    for label in negative_labels[:6]:
        label_lower = label.lower()
        for keywords, roles in _CONSTRAINT_ROLE_MAP:
            if any(kw in label_lower for kw in keywords):
                new_roles = [r for r in roles if r not in seen_roles]
                if new_roles:
                    lines.append(f"  ● {label} → prioritize: {', '.join(new_roles)}")
                    seen_roles.update(new_roles)
                break

    if not lines:
        return ""

    return (
        "\n    DETECTED KEY CHALLENGES FROM PREVIOUS SCAN (drive your stakeholder search):\n"
        + "\n".join(lines)
        + "\n    CEO/MD is always priority #1 regardless of which challenges are detected.\n"
    )


# --- RE-SEARCH LOOP HELPERS ---

# Maps each jury number to the category names that belong to it
JURY_CATEGORY_MAP = {
    1: ["Operating Model", "Engineering", "Architecture", "Code", "API", "Design System", "Job", "Tech Stack"],
    2: ["Strategy", "Business", "Procurement", "Revenue", "Investment", "Leadership"],
    3: ["Values", "Culture", "Talent", "Employee", "Inclusion", "Community", "HR", "People", "Careers"],
    4: ["AI", "Data", "Technology", "Cloud", "Academic", "Research"],
    5: ["Service", "Portfolio", "Delivery", "Platform", "Solution", "Client", "Reference"],
}

def _identify_weak_juries(findings):
    """
    Checks which juries (1-5) returned zero or very weak results.
    Returns a list of jury numbers that need re-searching.
    """
    jury_hit_count = {1: 0, 2: 0, 3: 0, 4: 0, 5: 0}

    for f in findings:
        cat = f.get("category", "").lower()
        matched = False
        for jury_num, keywords in JURY_CATEGORY_MAP.items():
            if any(k.lower() in cat for k in keywords):
                if f.get("found"):
                    jury_hit_count[jury_num] += 1
                matched = True
                break
        # If category didn't match any jury keyword, don't count it

    weak = [num for num, count in jury_hit_count.items() if count == 0]
    return weak

def _build_research_prompt(search_name, domain_hint, weak_juries, loop_number, intent_score):
    """
    Builds a FOCUSED re-search prompt targeting only the juries that came back empty.
    Each loop broadens the search strategy progressively.
    """
    jury_sections = []

    jury_templates_loop1 = {
        1: f"""1. ARCHITECTURAL JURY (RETRY - broader search, NO site: filter):
       - `"{search_name}" open source OR github OR API OR "avoin lähdekoodi"`
       - `"{search_name}" engineering culture OR tech stack OR "tekninen arkkitehtuuri"`
       - `"{search_name}" developer experience OR devops OR CI/CD`""",
        2: f"""2. STRATEGIC JURY (RETRY - broader search, Finnish news included):
       - `"{search_name}" strategy digital transformation OR "digitaalinen muutos"`
       - `"{search_name}" SaaS OR cloud migration OR "pilvipalvelu" OR "pilvisiirtymä"`
       - `"{search_name}" annual report {date.today().year} {date.today().year - 1} OR "vuosikertomus"`
       - `"{search_name}" kauppalehti OR talouselämä OR "strategia"`""",
        3: f"""3. CULTURAL JURY (RETRY - broader search, Finnish terms):
       - `"{search_name}" employer review OR workplace culture OR "työkulttuuri" OR "työnantaja"`
       - `"{search_name}" great place to work OR "paras työpaikka" Finland`
       - `"{search_name}" hiring OR layoffs OR "rekrytointi" OR "yt-neuvottelut"`
       - `"{search_name}" diversity inclusion OR "monimuotoisuus"`""",
        4: f"""4. AI & DATA JURY (RETRY - broader search):
       - `"{search_name}" artificial intelligence machine learning OR "tekoäly" OR "koneoppiminen"`
       - `"{search_name}" data platform OR data strategy OR "data-analytiikka"`
       - `"{search_name}" cloud AWS Azure Google Cloud OR "pilvipalvelu"`
       - `"{search_name}" innovation R&D OR "tutkimus ja kehitys" OR "innovaatio"`""",
        5: f"""5. SERVICE PORTFOLIO JURY (RETRY - broader search):
       - `"{search_name}" services portfolio OR "palvelut" OR "ratkaisut"`
       - `"{search_name}" case study client OR "asiakastarina" OR "referenssi"`
       - `"{search_name}" platform engineering OR consulting OR "konsultointi"`"""
    }

    jury_templates_loop2 = {
        1: f"""1. ARCHITECTURAL JURY (DEEP RETRY - industry context):
       - `"{search_name}" technology modernization legacy OR "järjestelmäuudistus"`
       - `"{search_name}" microservices OR monolith OR platform OR "alustatalous"`""",
        2: f"""2. STRATEGIC JURY (DEEP RETRY - industry context):
       - `"{search_name}" market position competitors Finland OR "markkinaosuus"`
       - `"{search_name}" growth acquisition partnership OR "kasvu" OR "kumppanuus"`""",
        3: f"""3. CULTURAL JURY (DEEP RETRY - industry context):
       - `"{search_name}" CEO interview culture OR "toimitusjohtaja" "kulttuuri"`
       - `"{search_name}" employee satisfaction Finland OR "henkilöstötyytyväisyys"`""",
        4: f"""4. AI & DATA JURY (DEEP RETRY - industry context):
       - `"{search_name}" digital maturity OR automation OR "automaatio" OR "digitaalinen kypsyys"`
       - `"{search_name}" technology investment Finland {date.today().year} {date.today().year - 1} OR "teknologiainvestointi"`""",
        5: f"""5. SERVICE PORTFOLIO JURY (DEEP RETRY - industry context):
       - `"{search_name}" Gartner OR Forrester OR ISG analyst report`
       - `"{search_name}" service delivery quality Finland OR "palvelun laatu"`"""
    }

    templates = jury_templates_loop1 if loop_number == 1 else jury_templates_loop2

    for jury_num in weak_juries:
        if jury_num in templates:
            jury_sections.append(templates[jury_num])

    if not jury_sections:
        return None

    juries_text = "\n\n    ".join(jury_sections)

    num_weak = len(weak_juries)
    return f"""
    TARGETED RE-SEARCH for "{search_name}" (Domain: {domain_hint}).
    CONTEXT: Intent Score is {intent_score}/100.

    IMPORTANT: A previous search found LIMITED evidence for certain areas.
    Your job is to find ADDITIONAL evidence using BROADER search terms.
    Do NOT use strict site: filters. Search openly across the web.
    Use BOTH English and Finnish search terms (target is a Finnish company).

    SEARCH LIMIT: Use MAX {min(num_weak + 1, 4)} web searches total for this re-search.
    Combine queries for related juries into a single search with OR operators.
    After {min(num_weak + 1, 4)} searches, STOP and return what you found.

    URL QUALITY RULE: ONLY include URLs from actual web search results.
    Do NOT construct or guess URLs. If you cannot find a real URL for a finding,
    set "url" to "" and "found" to false. Never fabricate links.

    SEARCH THESE AREAS ONLY (the ones that previously returned weak results):

    {juries_text}

    RETURN STRICT JSON (same format as before):
    {{
        "findings": [
            {{
                "category": "Operating Model",
                "label": "Brief Label",
                "found": boolean,
                "sentiment": "negative" or "positive",
                "snippet": "Quote...",
                "title": "Source Title",
                "url": "URL from search results ONLY (or empty string if none found)",
                "is_recent": boolean (True if < 12 months)
            }}
        ],
        "new_evidence_count": (integer, how many NEW pieces of evidence you found)
    }}

    If you find NO new evidence at all, return: {{"findings": [], "new_evidence_count": 0}}
    """

def _merge_findings(existing_findings, new_findings):
    """
    Merges new findings into existing list, avoiding duplicates by URL.
    Returns the merged list and count of truly new items added.
    """
    existing_urls = set()
    for f in existing_findings:
        url = f.get("url", "")
        if url:
            existing_urls.add(url.lower().rstrip("/"))

    new_unique = []
    for f in new_findings:
        url = f.get("url", "")
        url_key = url.lower().rstrip("/") if url else ""
        if url_key and url_key not in existing_urls:
            new_unique.append(f)
            existing_urls.add(url_key)
        elif not url_key and f.get("found"):
            # Found evidence without URL — still add if label is unique
            existing_labels = {ef.get("label", "").lower() for ef in existing_findings}
            if f.get("label", "").lower() not in existing_labels:
                new_unique.append(f)

    return existing_findings + new_unique, len(new_unique)

def _recalculate_confidence(original_confidence, new_unique_count, weak_juries_before, weak_juries_after):
    """
    Adjusts confidence score based on re-search results.
    Each new unique evidence: +5 confidence.
    Each previously-weak jury that now has findings: +10 confidence.
    Cap at 85 (full confidence requires manual verification).
    """
    boost = 0
    boost += new_unique_count * 5
    resolved_juries = len(weak_juries_before) - len(weak_juries_after)
    boost += resolved_juries * 10
    new_confidence = min(85, original_confidence + boost)
    return new_confidence


def _compute_scan_diagnostics(company_name, prev_findings, prev_evidence, curr_findings, curr_evidence):
    """
    Pre-computes concrete diagnostics from both scans BEFORE sending to LLM.
    This grounds the self-analysis in real data instead of hallucinated suggestions.
    """
    diagnostics = {}

    # 1. Source domain distribution — detect single-domain dominance
    curr_domains = {}
    for e in curr_evidence:
        domain = get_clean_domain(e.get("url", ""))
        if domain:
            curr_domains[domain] = curr_domains.get(domain, 0) + 1

    total_sources = sum(curr_domains.values())
    if total_sources > 0:
        dominant_domain = max(curr_domains, key=curr_domains.get)
        dominance_ratio = round(curr_domains[dominant_domain] / total_sources, 2)
        diagnostics["source_diversity"] = {
            "total_sources": total_sources,
            "unique_domains": len(curr_domains),
            "dominant_domain": dominant_domain,
            "dominance_ratio": dominance_ratio,
        }
    else:
        diagnostics["source_diversity"] = {"total_sources": 0, "unique_domains": 0}

    # 2. Jury coverage — detect single-source or empty juries
    jury_hit_count = {1: 0, 2: 0, 3: 0, 4: 0, 5: 0}
    jury_names = {1: "Architectural", 2: "Strategic", 3: "Cultural", 4: "AI & Data", 5: "Service Portfolio"}
    for f in curr_findings:
        cat = f.get("category", f.get("query", "")).lower()
        for jury_num, keywords in JURY_CATEGORY_MAP.items():
            if any(k.lower() in cat for k in keywords):
                if f.get("found", False):
                    jury_hit_count[jury_num] += 1
                break

    single_source_juries = [f"Jury {n} ({jury_names[n]})" for n, c in jury_hit_count.items() if c == 1]
    empty_juries = [f"Jury {n} ({jury_names[n]})" for n, c in jury_hit_count.items() if c == 0]
    diagnostics["jury_coverage"] = {
        "hits_per_jury": {f"{n} ({jury_names[n]})": c for n, c in jury_hit_count.items()},
        "single_source_juries": single_source_juries,
        "empty_juries": empty_juries,
    }

    # 3. Evidence recency
    recent_count = sum(1 for f in curr_findings if f.get("is_recent"))
    total_findings = len(curr_findings)
    diagnostics["recency"] = {
        "recent_findings": recent_count,
        "old_findings": total_findings - recent_count,
        "recency_ratio": round(recent_count / max(1, total_findings), 2),
    }

    # 4. URL overlap between scans — low overlap = search variance
    prev_urls = {e.get("url", "").lower().rstrip("/") for e in prev_evidence if e.get("url")}
    curr_urls = {e.get("url", "").lower().rstrip("/") for e in curr_evidence if e.get("url")}
    all_urls = prev_urls | curr_urls
    shared = prev_urls & curr_urls
    diagnostics["url_overlap"] = {
        "previous_urls": len(prev_urls),
        "current_urls": len(curr_urls),
        "shared_urls": len(shared),
        "overlap_ratio": round(len(shared) / max(1, len(all_urls)), 2),
    }

    # 5. Subsidiary indicator — name contains country/region suggesting regional entity
    subsidiary_keywords = ["finland", "suomi", "nordic", "nordics", "scandinavia",
                           "sweden", "sverige", "norway", "norge", "denmark", "danmark",
                           "baltics", "europe"]
    name_lower = (company_name or "").lower()
    diagnostics["subsidiary_indicator"] = any(kw in name_lower for kw in subsidiary_keywords)

    return diagnostics


def run_self_analysis(company_name, previous_scan_data, current_scan_data, days_since_last):
    """
    SELF-ANALYSIS: Triggered when scanner produces significantly different results
    for the same company within a short timeframe.

    Pre-computes diagnostics from actual data, then asks GPT/Claude to interpret
    them and select from a constrained set of actionable suggestion types.

    Returns a dict with analysis results, or None if analysis fails.
    """
    prev_reality = previous_scan_data.get("previous_reality", "N/A")
    prev_confidence = previous_scan_data.get("previous_confidence", "N/A")
    prev_findings_raw = previous_scan_data.get("previous_findings", [])
    prev_evidence = previous_scan_data.get("previous_evidence", [])

    curr_reality = current_scan_data.get("score", "N/A")
    curr_confidence = current_scan_data.get("confidence_score", "N/A")
    curr_findings = current_scan_data.get("checklist", [])
    curr_evidence = current_scan_data.get("links", [])

    # --- PRE-COMPUTE DIAGNOSTICS from actual data ---
    diagnostics = _compute_scan_diagnostics(
        company_name, prev_findings_raw, prev_evidence, curr_findings, curr_evidence
    )

    # Summarize previous findings
    prev_summary = []
    for f in prev_findings_raw[:6]:
        prev_summary.append(f"[{f.get('sentiment', '?').upper()}] {f.get('query', f.get('label', '?'))}")

    # Summarize current findings
    curr_summary = []
    for f in curr_findings[:8]:
        curr_summary.append(f"[{f.get('sentiment', '?').upper()}] {f.get('query', '?')} (found={f.get('found', '?')})")

    prev_evidence_summary = [f"{e.get('title', '?')}: {e.get('snippet', '')[:60]}" for e in prev_evidence[:4]]
    curr_evidence_summary = [f"{e.get('title', '?')}: {e.get('snippet', '')[:60]}" for e in curr_evidence[:4]]

    prompt = f"""SELF-ANALYSIS: SCANNER VARIANCE DETECTED

Company: {company_name}
Time between scans: {days_since_last} days

PREVIOUS SCAN:
  Reality Score: {prev_reality}/100
  Confidence: {prev_confidence}/100
  Findings: {json.dumps(prev_summary)}
  Evidence sources: {json.dumps(prev_evidence_summary)}

CURRENT SCAN:
  Reality Score: {curr_reality}/100
  Confidence: {curr_confidence}/100
  Findings: {json.dumps(curr_summary)}
  Evidence sources: {json.dumps(curr_evidence_summary)}

Score delta: {curr_reality - prev_reality if isinstance(curr_reality, (int, float)) and isinstance(prev_reality, (int, float)) else 'N/A'}

PRE-COMPUTED DIAGNOSTICS (from actual scan data — use these, do NOT invent your own metrics):
{json.dumps(diagnostics, indent=2)}

ANALYZE:
1. CAUSE: Why did the scanner produce different results? Pick the most likely:
   - search_variance: URL overlap is low — same queries hit different web results
   - evidence_gap: One scan found sources the other missed
   - prompt_sensitivity: Model interpreted context differently (same evidence, different conclusion)
   - genuine_change: Real company change (unlikely if < 7 days)

2. WHICH FINDINGS CHANGED: List specific findings that appeared or disappeared.

3. RELIABILITY: Based on this variance, how reliable is the current scan? (low/medium/high)

4. ACTIONABLE SUGGESTIONS: Select ONLY from the types below. Each suggestion MUST map to
   something the scanning system can programmatically act on. Do NOT write consulting advice.
   Do NOT suggest things that require human research or manual data gathering.

   AVAILABLE TYPES (pick all that apply based on the diagnostics above):
   - "widen_search_parent": The company appears to be a subsidiary. Include parent/group company
     in future searches. Provide the parent company name in "detail" if identifiable.
   - "source_diversity_low": Evidence dominated by a single domain (dominance_ratio > 0.5).
     The system should diversify search sources.
   - "jury_single_source": A jury relies on only 1 evidence source — flag it as low-confidence.
     Specify which jury in "detail".
   - "search_terms_narrow": URL overlap between scans is very low (< 0.2), meaning searches
     are hitting different results each time. Search terms should be broadened or pinned.
   - "recency_gap": Most evidence is older than 18 months. Freshness-targeted re-search needed.
   - "evidence_language_bias": Evidence is skewed to one language. Add more Finnish or English terms.

   RULES:
   - Maximum 3 suggestions
   - ONLY pick types where the diagnostics clearly support it
   - If diagnostics look healthy, return an empty suggestions array — do not force suggestions

Return STRICT JSON:
{{
    "primary_cause": "search_variance" or "evidence_gap" or "prompt_sensitivity" or "genuine_change",
    "cause_explanation": "One sentence explaining what happened",
    "changed_findings": ["List of specific findings that appeared or disappeared"],
    "reliability": "low" or "medium" or "high",
    "suggestions": [
        {{"type": "suggestion_type_from_list_above", "detail": "Brief specifics (e.g. which jury, which domain, parent company name)"}}
    ],
    "recommended_action": "One sentence: what should the system do differently next scan?"
}}
"""

    try:
        start = time.time()
        _sa_model_chain = [("gpt-5.4", 60), ("claude-sonnet-4-6", 60)]
        for _sa_model, _sa_timeout in _sa_model_chain:
            print(f"   [Self-Analysis] Trying {_sa_model} on stored diagnostics...")
            try:
                text, used_model = call_json_model_chain(prompt, (_sa_model,), max_tokens=2000, timeout=_sa_timeout)
            except Exception as model_err:
                elapsed = int((time.time() - start) * 1000)
                print(f"   ❌ [Self-Analysis] {_sa_model} failed after {elapsed}ms: {model_err}")
                continue

            json_payload = extract_json_from_text(text)
            elapsed = int((time.time() - start) * 1000)
            if json_payload:
                analysis = json.loads(json_payload, strict=False)
                analysis["company"] = company_name
                analysis["days_between_scans"] = days_since_last
                analysis["diagnostics"] = diagnostics
                analysis["score_delta"] = curr_reality - prev_reality if isinstance(curr_reality, (int, float)) and isinstance(prev_reality, (int, float)) else None
                analysis["model"] = used_model

                print(f"   [Self-Analysis] Cause: {analysis.get('primary_cause', 'unknown')} | Reliability: {analysis.get('reliability', '?')} ({elapsed}ms)")
                print(f"   [Self-Analysis] {analysis.get('cause_explanation', '')}")

                suggestions = analysis.get("suggestions", [])
                if suggestions:
                    for s in suggestions[:3]:
                        print(f"   [Self-Analysis] Action [{s.get('type', '?')}]: {s.get('detail', '')}")
                else:
                    print(f"   [Self-Analysis] No actionable suggestions (diagnostics healthy).")

                analysis["improvement_suggestions"] = [
                    f"[{s.get('type', '?')}] {s.get('detail', '')}" for s in suggestions
                ]
                return analysis
            print(f"   [Self-Analysis] No parseable JSON from {_sa_model} ({elapsed}ms). Trying next model...")
        return None
    except Exception as e:
        print(f"   [Self-Analysis] Failed: {e}")
        return None


def x_ray_search(company_name: str, company_url: str = None, model_id: str = None, intent_score: int = 0, previous_context: dict = None, max_research_loops: int = 2, subsidiaries: list = None, time_budget: int = 350, employees: int = None, batch_mode: bool = False):
    """
    SCANNER 2: THE REALITY CHECK (External Evidence)

    This function performs the Forensic Scan.
    It uses the 'Standardized Tribunal' methodology with STRICT domain filtering.

    Optional: previous_context dict enables history-aware rescan mode.
    Optional: max_research_loops (0-3) controls confidence-driven re-search.
              Default 2 for single scans, use 1 for batch scans.
              Set to 0 to disable re-search entirely.
    Optional: time_budget (seconds) — total time this function may run.
              Inner guards derive from this value instead of hardcoded constants.
    """

    _fn_start = time.time()  # Track total function elapsed time for guards
    # Derive inner guard thresholds from time_budget (instead of hardcoded values)
    _guard_forensic = time_budget - 20       # skip model calls (leave buffer for URL validation + response)
    _guard_research = time_budget - 50       # skip re-search loops
    _guard_anthropic = time_budget - 50      # skip Sonnet + Web Search
    _guard_delta_verify = time_budget - 130  # skip delta verification (leave room for URL validation)
    print(f"   [Scanner 2] Time budget: {time_budget}s (guards: forensic={_guard_forensic}s, research={_guard_research}s, delta={_guard_delta_verify}s)")

    is_rescan = bool(previous_context and previous_context.get("previous_reality") is not None)
    company_evidence_candidates = (previous_context or {}).get("company_evidence_db_candidates", []) if isinstance(previous_context, dict) else []
    # Parallel jury acquisition replaces the legacy confidence-driven full-search
    # loop. Coverage affects diagnostics, never whether a jury lane exists.
    max_research_loops = 0

    def _phase_remaining(buffer: int = 0) -> int:
        return max(0, int(time_budget - (time.time() - _fn_start) - buffer))

    def _hard_timeout_call(label: str, timeout_sec: int, fn):
        timeout_sec = max(1, int(timeout_sec))
        pool = concurrent.futures.ThreadPoolExecutor(max_workers=1)
        future = pool.submit(fn)
        try:
            return future.result(timeout=timeout_sec)
        except concurrent.futures.TimeoutError:
            future.cancel()
            print(f"   ⏰ [Forensic Scan] {label} hard deadline reached after {timeout_sec}s")
            return None
        finally:
            pool.shutdown(wait=False)

    def _seed_excerpt(value, limit=520):
        if not value:
            return ""
        text = re.sub(r"\s+", " ", str(value)).strip()
        return text[:limit]

    def _seed_category(source_type="", source_category=""):
        token = f"{source_type} {source_category}".lower()
        if any(k in token for k in ["financial", "annual", "investor", "filing"]):
            return "STRATEGIC JURY (Business Model, Procurement & Customer Experience)"
        if any(k in token for k in ["career", "job", "hiring", "people", "culture"]):
            return "CULTURAL JURY (Talent & Psychological Safety)"
        if any(k in token for k in ["service", "delivery", "reference", "case study", "customer story"]):
            return "SERVICE PORTFOLIO JURY (Delivery Reality)"
        if any(k in token for k in ["procurement", "contract", "tender"]):
            return "STRATEGIC JURY (Business Model, Procurement & Customer Experience)"
        if any(k in token for k in ["technology", "cloud", "ai", "data", "cyber", "erp"]):
            return "AI & DATA JURY (Future Readiness)"
        if any(k in token for k in ["architecture", "engineering", "developer", "api", "tech stack"]):
            return "ARCHITECTURAL JURY (Engineering Reality)"
        return "Company Evidence DB / Historical Evidence"

    def _is_previous_seedable(source):
        if not isinstance(source, dict):
            return False
        if not source.get("url"):
            return False
        if source.get("wrong_entity_true") or source.get("entity_match_status") == "wrong":
            return False
        if source.get("promotion_status") in {"excluded", "blocked"}:
            return False
        return bool(
            source.get("generation_eligible")
            or source.get("analysis_verified")
            or source.get("url_verified")
            or source.get("verified")
            or source.get("content_verified")
        )

    def _previous_scans_for_seed():
        scans = []
        if not isinstance(previous_context, dict):
            return scans
        scans.append({
            "date": previous_context.get("last_scanned", ""),
            "evidence": previous_context.get("previous_evidence", []),
        })
        for older in previous_context.get("scan_history", []) or []:
            scans.append({
                "date": older.get("date", ""),
                "evidence": older.get("evidence", []),
            })
        return scans

    def _scan_age_days(scan_date):
        try:
            if scan_date:
                return (datetime.utcnow() - datetime.fromisoformat(str(scan_date))).days
        except (ValueError, TypeError):
            pass
        return 999

    def _build_seeded_forensic_fallback(reason):
        """
        Build candidate evidence from cached Company Evidence DB and recent
        verified historical evidence when live web search fails. These are not
        promoted facts; they are revalidation-pending inputs for the normal
        Scanner gates.
        """
        seed_findings = []
        seen_urls = set()
        company_seed_count = 0
        historical_seed_count = 0

        def _candidate_rank(candidate):
            url = candidate.get("url") or candidate.get("source_url") or ""
            excerpt = (
                candidate.get("cached_excerpt") or candidate.get("source_quote")
                or candidate.get("snippet") or candidate.get("text_excerpt")
                or candidate.get("extracted_text") or candidate.get("markdown")
            )
            host = get_clean_domain(url)
            official = bool(domain_hint and host and (host == domain_hint or host.endswith(f".{domain_hint}")))
            return (
                1 if excerpt else 0,
                1 if official else 0,
                1 if candidate.get("url_verified") or candidate.get("content_verified") else 0,
                str(candidate.get("fetched_at") or candidate.get("last_verified_at") or ""),
            )

        def _company_seed_allowed(candidate):
            if not isinstance(candidate, dict):
                return False
            status_token = " ".join(str(candidate.get(key) or "") for key in (
                "promotion_status", "source_audit_status", "entity_match_status",
                "verification_status", "page_status", "source_category", "source_type",
            )).lower()
            if candidate.get("wrong_entity_true") or candidate.get("entity_match_status") == "wrong":
                return False
            if any(token in status_token for token in (
                "blocked", "excluded", "soft_404", "page_not_found", "source_dead",
                "stakeholder_candidate", "stakeholder_only", "sales_only", "context_only",
            )):
                return False
            if candidate.get("stale") is True or candidate.get("freshness_status") == "stale":
                return False
            return bool(candidate.get("url") or candidate.get("source_url") or _seed_excerpt(
                candidate.get("cached_excerpt") or candidate.get("source_quote")
                or candidate.get("snippet") or candidate.get("text_excerpt")
            ))

        eligible_company_candidates = [
            candidate for candidate in company_evidence_candidates
            if _company_seed_allowed(candidate)
        ]
        skipped_company_candidates = len(company_evidence_candidates) - len(eligible_company_candidates)
        ranked_candidates = sorted(eligible_company_candidates, key=_candidate_rank, reverse=True)
        for idx, candidate in enumerate(ranked_candidates[:16], start=1):
            url = candidate.get("url") or candidate.get("source_url") or ""
            excerpt = _seed_excerpt(
                candidate.get("cached_excerpt")
                or candidate.get("source_quote")
                or candidate.get("snippet")
                or candidate.get("text_excerpt")
                or candidate.get("extracted_text")
                or candidate.get("markdown")
            )
            if not url and not excerpt:
                continue
            key = url.rstrip("/").lower() if url else f"company-evidence-{idx}"
            if key in seen_urls:
                continue
            seen_urls.add(key)
            source_type = candidate.get("source_type") or candidate.get("source_category") or ""
            title = candidate.get("title") or candidate.get("source_title") or url or f"Company Evidence DB source {idx}"
            safe_label = f"Revalidate cached source: {str(title)[:90]}"
            seed_findings.append({
                "category": _seed_category(source_type, candidate.get("source_category") or ""),
                "label": safe_label,
                "found": True,
                "sentiment": "neutral",
                "snippet": (
                    f"Cached source text for revalidation: {excerpt}"
                    if excerpt else
                    "Company Evidence DB source available; live scan must revalidate content before use."
                ),
                "title": title,
                "url": url,
                "is_recent": False,
                "source_database": "company_evidence_db",
                "forensic_seed_source": True,
                "source_status": "candidate_revalidation_pending",
                "verification_reason": "Seeded from Company Evidence DB for Scanner revalidation before fresh discovery.",
                "cached_excerpt": excerpt,
            })
            company_seed_count += 1

        for scan in _previous_scans_for_seed():
            if _scan_age_days(scan.get("date")) >= 90:
                continue
            for prev_ev in scan.get("evidence", []) or []:
                if not _is_previous_seedable(prev_ev):
                    continue
                url = prev_ev.get("url") or ""
                key = url.rstrip("/").lower()
                if not key or key in seen_urls:
                    continue
                seen_urls.add(key)
                title = prev_ev.get("title") or "Historical verified evidence"
                safe_label = f"Revalidate historical source: {str(title)[:90]}"
                seed_findings.append({
                    "category": _seed_category(prev_ev.get("source_type") or "", prev_ev.get("category") or ""),
                    "label": safe_label,
                    "found": True,
                    "sentiment": "neutral",
                    "snippet": _seed_excerpt(prev_ev.get("snippet") or prev_ev.get("source_quote") or prev_ev.get("verification_reason"))
                    or "Previously verified evidence carried forward as historical revalidation-pending source.",
                    "title": title,
                    "url": url,
                    "is_recent": False,
                    "source_database": "previous_verified_evidence",
                    "forensic_seed_source": True,
                    "source_status": "historical_revalidation_pending",
                    "verification_reason": "Seeded from recent verified evidence for continuity and current revalidation.",
                })
                historical_seed_count += 1
                if len(seed_findings) >= 16:
                    break
            if len(seed_findings) >= 16:
                break

        # URL checks are deterministic and concurrent, outside the model budget.
        linked = [finding for finding in seed_findings if finding.get("url")]
        if linked:
            with concurrent.futures.ThreadPoolExecutor(max_workers=min(8, len(linked))) as pool:
                futures = {
                    pool.submit(_verify_url, finding["url"], 5, company_name): finding
                    for finding in linked
                }
                for future, finding in futures.items():
                    try:
                        check = future.result(timeout=7) or {}
                    except Exception:
                        check = {}
                    finding["seed_url_verified"] = bool(check.get("verified"))
                    finding["seed_entity_mentioned"] = check.get("company_mentioned")
                    if check.get("verified") and check.get("company_mentioned") is not False:
                        finding["source_status"] = "seed_revalidated"
                    else:
                        finding["source_status"] = "claim_support_pending"

        coverage = _compute_forensic_coverage(seed_findings, domain_hint)
        confidence = max(20, min(55, round(coverage["score"] * 0.65)))
        if historical_seed_count and not company_seed_count:
            confidence = min(confidence, 35)
        print(
            f"   🧭 [Forensic Seed] Baseline ready ({reason}): {company_seed_count} Company Evidence DB, "
            f"{historical_seed_count} historical, {coverage['verified_urls']} URL revalidated; "
            f"{skipped_company_candidates} unsafe/inapplicable candidates skipped; "
            f"coverage {coverage['score']}/100 across {len(coverage['jury_coverage'])}/5 juries."
        )
        return {
            "findings": seed_findings,
            "key_people": [],
            "reality_score_adjustment": 0,
            "confidence_score": confidence,
            "washing_flag": False,
            "washing_type": None,
            "_seeded_forensic_fallback": True,
            "_fallback_reason": reason,
            "_company_evidence_seed_count": company_seed_count,
            "_historical_seed_count": historical_seed_count,
            "_baseline_coverage": coverage,
        }

    search_name = clean_company_name_for_search(company_name)
    entity_terms = entity_search_terms(company_name)
    search_alias_block = ""
    if entity_terms and len(entity_terms) > 1:
        search_alias_block = (
            "    ENTITY SEARCH TERMS: Also search these names when they are relevant to the same target: "
            + ", ".join(f'"{term}"' for term in entity_terms[:8])
            + "\n"
        )
    domain_hint = get_clean_domain(company_url) if company_url else f"{search_name.lower().replace(' ', '')}.fi"

    # --- TIER 2: PRE-FETCH VERIFIED DATA (parallel, non-blocking) ---
    prh_data = None
    tech_stack = None
    with concurrent.futures.ThreadPoolExecutor(max_workers=2) as prefetch_pool:
        prh_future = prefetch_pool.submit(_fetch_prh_company_data, company_name)
        tech_future = prefetch_pool.submit(_detect_tech_stack, company_url)
        try:
            prh_data = prh_future.result(timeout=10)
        except Exception:
            pass
        try:
            tech_stack = tech_future.result(timeout=8)
        except Exception:
            pass

    pre_verified_block = ""
    if prh_data or tech_stack:
        parts = ["\n    PRE-VERIFIED DATA (fetched from official sources BEFORE your search — use as ground truth):"]
        if prh_data:
            print(f"   [Scanner 2] PRH data loaded: {prh_data.get('business_id', '?')}, registered {prh_data.get('registration_date', '?')}")
            parts.append(f"    Finnish Trade Register (PRH): Business ID {prh_data['business_id']}, "
                         f"registered {prh_data.get('registration_date', 'N/A')}, "
                         f"status: {prh_data.get('status', 'N/A')}, "
                         f"form: {prh_data.get('company_form', 'N/A')}, "
                         f"city: {prh_data.get('city', 'N/A')}")
            if prh_data.get("business_lines"):
                parts.append(f"    Registered business lines: {', '.join(prh_data['business_lines'])}")
        if tech_stack:
            techs = tech_stack.get("technologies", [])
            assessment = tech_stack.get("assessment", "unknown")
            print(f"   [Scanner 2] Tech stack detected: {', '.join(techs[:5]) if techs else 'none'} ({assessment})")
            if techs:
                parts.append(f"    Website tech stack (auto-detected from {domain_hint}): {', '.join(techs)}")
                parts.append(f"    Tech modernity assessment: {assessment.upper()} "
                             f"({tech_stack.get('modern_count', 0)} modern, {tech_stack.get('legacy_count', 0)} legacy signals)")
                parts.append(f"    HTTPS: {'Yes' if tech_stack.get('uses_https') else 'No'}")
                parts.append("    NOTE: This is what the website ACTUALLY runs — compare against their marketing claims.")
        pre_verified_block = "\n".join(parts) + "\n"

    # Build optional history context (empty string if first scan)
    history_block = _build_previous_context_block(previous_context)
    # Build evidence anchors for URL/keyword re-verification (empty on first scan)
    if previous_context:
        previous_context["company_name"] = search_name
    evidence_anchors = _build_evidence_anchors(previous_context)
    # Build constraint→role guidance for stakeholder targeting (rescan only)
    if history_block:
        print(f"   [Scanner 2] RESCAN MODE: Previous data from {previous_context.get('last_scanned', 'unknown')} loaded.")
        if evidence_anchors:
            print(f"   [Scanner 2] Evidence anchoring active: re-verifying previous URLs and keywords.")

    # --- SUBSIDIARY AWARENESS ---
    # Many Finnish companies are subsidiaries of Nordic parents (or vice versa).
    # Detect this from the company name and instruct the model to widen its search.
    subsidiary_keywords = ["finland", "suomi", "nordic", "nordics", "scandinavia",
                           "sweden", "sverige", "norway", "norge", "denmark", "danmark",
                           "baltics", "europe"]
    name_lower = search_name.lower()
    is_likely_subsidiary = any(kw in name_lower for kw in subsidiary_keywords)

    subsidiary_block = ""
    entity_context_block = build_entity_context_block(company_name)
    if is_likely_subsidiary:
        print(f"   [Scanner 2] Subsidiary indicator detected in '{search_name}'. Enabling parent company search.")
        subsidiary_block = f"""
    SUBSIDIARY AWARENESS (CRITICAL):
    "{search_name}" appears to be a REGIONAL SUBSIDIARY (name contains a country/region).
    You MUST:
    1. FIRST: Search for the PARENT/GROUP company: `"{search_name}" parent company OR "emoyhtiö" OR "konserni"`
    2. THEN: Include the parent company name in your jury searches alongside "{search_name}".
       Example: If parent is "Statkraft", also search `"Statkraft" technology strategy`.
    3. EVALUATE: Are engineering, culture, and strategy decisions made locally or inherited
       from the parent? This distinction is critical for scoring.
    4. In findings, NOTE which evidence applies to the local entity vs. the parent group.
    """
    elif subsidiaries:
        # Holding company with known subsidiaries — search for subsidiary tech activity
        sub_names = [s.get("name", "") for s in subsidiaries if s.get("name")]
        sub_list = ", ".join(sub_names)
        print(f"   [Scanner 2] Holding company with subsidiaries: {sub_list}. Enabling subsidiary search.")
        subsidiary_block = f"""
    HOLDING COMPANY — SUBSIDIARY AWARENESS (CRITICAL):
    "{search_name}" is a HOLDING/GROUP company. Its technology activity happens primarily in its subsidiaries:
    {sub_list}

    You MUST:
    1. Search for EACH subsidiary alongside the parent: e.g. `"{sub_names[0]}" technology strategy`, `"{sub_names[0]}" digital transformation`
    2. Include subsidiary-specific evidence in your findings — these count as evidence FOR the group.
    3. EVALUATE: What is the group-level tech strategy vs. what subsidiaries do independently?
    4. In findings, NOTE which subsidiary the evidence comes from.
    5. DO NOT penalize the group for lack of direct tech content on the holding company website — this is normal for holding structures.
    """

    # The forensic model no longer performs stakeholder discovery. Buying Insight
    # owns that work; Scanner 2 spends its discovery budget on source coverage.
    prompt = ""  # retained only for legacy helper call-shape compatibility

    def execute_with_model(target_model_name, custom_prompt=None, call_timeout=150):
        """Legacy executor slot retained for call-shape compatibility; inactive."""
        print(f"   ⏭ [Forensic Scan] Legacy model executor disabled for {target_model_name}")
        return None

    def execute_with_openai_web(
        active_prompt,
        timeout_cap: int | None = None,
        purpose: str = "primary",
        search_context_size: str = "medium",
        allow_repair: bool = False,
    ):
        """Primary web-search path using GPT-5.5 Responses web_search."""
        elapsed_total = time.time() - _fn_start
        if elapsed_total > _guard_anthropic:
            print(f"   ⏰ [Forensic Scan] Skipping GPT web search — elapsed {int(elapsed_total)}s exceeds {_guard_anthropic}s guard")
            return None
        ceiling = timeout_cap or 120
        local_timeout = min(ceiling, _phase_remaining(buffer=35))
        if local_timeout < 25:
            print(f"   ⏭️ [Forensic Scan] Skipping GPT web search — only {local_timeout}s available before phase guard")
            return None
        try:
            print(f"   🔄 [Forensic Scan] Attempting {purpose} gpt-5.5 + web_search for {company_name} (timeout={local_timeout}s)...")
            start = time.time()
            relaxed_prompt = re.sub(
                r'RETURN STRICT JSON[^:]*:',
                'REPORT YOUR FINDINGS (valid JSON preferred; clear structured text acceptable):',
                active_prompt,
            )
            text_content = _hard_timeout_call(
                "gpt-5.5 web search",
                local_timeout,
                lambda: call_openai_text(
                    relaxed_prompt,
                    model="gpt-5.5",
                    max_output_tokens=7000,
                    timeout=local_timeout,
                    web_search=True,
                    search_context_size=search_context_size,
                ),
            )
            if text_content is None:
                return None
            elapsed = int((time.time() - start) * 1000)
            json_payload = extract_json_from_text(text_content)
            if json_payload:
                try:
                    parsed = json.loads(json_payload, strict=False)
                    parsed["_web_grounded"] = True
                    parsed["_search_provider"] = "openai-web-search"
                    print(f"   ✅ [Forensic Scan] GPT web search returned JSON ({elapsed}ms)")
                    return parsed
                except json.JSONDecodeError as je:
                    print(f"   ⚠️ [Forensic Scan] GPT web JSON parse failed: {je}")
            if allow_repair and text_content and len(text_content) > 100:
                structured = _repair_json_with_sonnet(text_content, company_name, is_structuring=True)
                if structured:
                    structured["_web_grounded"] = True
                    structured["_search_provider"] = "openai-web-search"
                    print(f"   ✅ [Forensic Scan] GPT web output structured ({elapsed}ms)")
                    return structured
            print(f"   ⚠️ [Forensic Scan] GPT web search returned no usable evidence ({elapsed}ms)")
            return None
        except Exception as e:
            print(f"   ❌ [Forensic Scan] GPT web search failed: {e}")
            return None

    def execute_with_anthropic(active_prompt):
        """Legacy entry point retained for callers; web discovery is jury-lane only."""
        print("   ⏭️ [Forensic Scan] Full Sonnet web discovery disabled; Sonnet is jury-analysis fallback only")
        return None

    # --- PHASE 1: SEEDED TRIAGE + FIVE PARALLEL JURY ACQUISITIONS ---
    baseline = _build_seeded_forensic_fallback("pre_acquisition_baseline")
    coverage = baseline.get("_baseline_coverage") or _compute_forensic_coverage([], domain_hint)
    routed_seeds = _route_seed_findings(baseline.get("findings", []))
    acquisition_ceiling = min(
        FORENSIC_ACQUISITION_CEILING_SECONDS,
        _phase_remaining(
            buffer=(
                FORENSIC_EXTRACTION_RECOVERY_CEILING_SECONDS
                + FORENSIC_JURY_ANALYSIS_CEILING_SECONDS
                + FORENSIC_POSTPROCESS_RESERVE_SECONDS
            )
        ),
    )
    acquisition_started = time.time()
    acquisition_deadline = acquisition_started + acquisition_ceiling
    print(
        f"   🔍 [Forensic Acquisition] Five parallel jury lanes for {company_name}: "
        f"{acquisition_ceiling}s group ceiling; Gemini primary, GPT-5.5 web fallback; "
        f"baseline {coverage['score']}/100 across {len(coverage['jury_coverage'])}/5 juries."
    )

    def _parse_provider_payload(text):
        payload = extract_json_from_text(text or "")
        if not payload:
            return None
        try:
            return json.loads(payload, strict=False)
        except json.JSONDecodeError:
            return None

    def _acquire_one_jury(jury_name):
        lane_started = time.time()
        prompt = _build_jury_acquisition_prompt(
            search_name, domain_hint, jury_name, entity_context_block, search_alias_block
        )
        provider = "gemini-3.1-pro-preview"
        payload = None
        status = "provider_unavailable"
        if gemini_api_key():
            remaining = max(1, int(acquisition_deadline - time.time()))
            native_timeout = min(FORENSIC_GEMINI_TIMEOUT_SECONDS, remaining)
            try:
                text = call_gemini_forensic_text(prompt, timeout=native_timeout)
                payload = _parse_provider_payload(text)
                status = "completed" if payload else "invalid_json"
            except Exception as error:
                status = "provider_timeout" if "timeout" in type(error).__name__.lower() or "timeout" in str(error).lower() else "provider_error"
                print(f"   ⚠️ [Forensic Acquisition] {jury_name} Gemini {status}: {type(error).__name__}: {error}")
        if not payload:
            remaining = max(0, int(acquisition_deadline - time.time()))
            if remaining >= 20:
                provider = "gpt-5.5-web-fallback"
                payload = execute_with_openai_web(
                    prompt,
                    timeout_cap=min(55, remaining),
                    purpose=f"jury fallback: {jury_name}",
                    search_context_size="low",
                    allow_repair=False,
                )
                status = "fallback_completed" if payload else "fallback_failed"
        sources = _normalize_acquisition_payload(payload or {}, jury_name, provider)
        print(
            f"   {'✅' if sources else '⚠️'} [Forensic Acquisition] {jury_name}: {len(sources)} source packet(s), "
            f"provider={provider}, status={status}, elapsed={int(time.time() - lane_started)}s."
        )
        return {"jury": jury_name, "provider": provider, "status": status, "sources": sources}

    acquisition_results = []
    if acquisition_ceiling >= 20:
        pool = concurrent.futures.ThreadPoolExecutor(max_workers=5)
        futures = {pool.submit(_acquire_one_jury, jury): jury for jury in FORENSIC_JURIES}
        done, pending = concurrent.futures.wait(futures, timeout=acquisition_ceiling)
        for future in done:
            jury = futures[future]
            try:
                acquisition_results.append(future.result())
            except Exception as error:
                acquisition_results.append({"jury": jury, "provider": None, "status": "lane_error", "sources": [], "error": repr(error)})
        for future in pending:
            future.cancel()
            acquisition_results.append({"jury": futures[future], "provider": None, "status": "group_timeout", "sources": []})
        pool.shutdown(wait=False)
    else:
        acquisition_results = [
            {"jury": jury, "provider": None, "status": "budget_skipped", "sources": []}
            for jury in FORENSIC_JURIES
        ]
    acquisition_by_jury = {item["jury"]: item for item in acquisition_results}

    acquisition_results, pre_jury_stats = _prevalidate_acquisition_results(acquisition_results, company_name)
    acquisition_by_jury = {item["jury"]: item for item in acquisition_results}
    print(
        f"   🛡️ [Forensic Pre-Jury Gate] {pre_jury_stats['passed']}/{pre_jury_stats['input']} packets passed; "
        f"dead/soft-404={pre_jury_stats['dead_or_soft_404']}, wrong_entity={pre_jury_stats['wrong_entity']}, "
        f"unreadable={pre_jury_stats['unreadable']}, quote_not_found={pre_jury_stats.get('quote_not_found', 0)}; "
        f"Firecrawl recovered={pre_jury_stats.get('firecrawl_recovered', 0)}/"
        f"{pre_jury_stats.get('firecrawl_attempted', 0)}, skipped_by_cap="
        f"{pre_jury_stats.get('firecrawl_skipped_by_cap', 0)}."
    )

    # --- PHASE 2: FIVE PARALLEL NO-WEB JURY ANALYSES ---
    analysis_ceiling = min(
        FORENSIC_JURY_ANALYSIS_CEILING_SECONDS,
        _phase_remaining(buffer=FORENSIC_POSTPROCESS_RESERVE_SECONDS),
    )
    analysis_started = time.time()
    analysis_deadline = analysis_started + analysis_ceiling
    print(f"   ⚖️ [Forensic Analysis] Five parallel no-web jury calls; group ceiling {analysis_ceiling}s.")

    def _analyze_one_jury(jury_name):
        fresh = acquisition_by_jury.get(jury_name, {}).get("sources", [])
        seeds = routed_seeds.get(jury_name, [])
        packets = fresh + seeds
        if not packets:
            return {"jury": jury_name, "provider": None, "status": "no_evidence", "data": None}
        prompt = _build_jury_analysis_prompt(search_name, jury_name, packets)
        lane_started = time.time()
        payload = None
        provider = "gpt-5.5"
        remaining = max(1, int(analysis_deadline - time.time()))
        try:
            text = call_openai_text(
                prompt, model="gpt-5.5", max_output_tokens=6500,
                timeout=min(70, remaining), web_search=False,
            )
            payload = _parse_provider_payload(text)
        except Exception as error:
            print(f"   ⚠️ [Forensic Analysis] {jury_name} GPT failed: {type(error).__name__}: {error}")
        if not payload:
            remaining = max(0, int(analysis_deadline - time.time()))
            if remaining >= 15:
                provider = "claude-sonnet-4-6"
                try:
                    text = call_anthropic_text(
                        prompt, model="claude-sonnet-4-6", max_tokens=6500,
                        timeout=min(35, remaining), web_search=False,
                    )
                    payload = _parse_provider_payload(text)
                except Exception as error:
                    print(f"   ⚠️ [Forensic Analysis] {jury_name} Sonnet failed: {type(error).__name__}: {error}")
        accepted = len((payload or {}).get("findings", []) or [])
        print(
            f"   {'✅' if payload else '⚠️'} [Forensic Analysis] {jury_name}: {accepted} accepted finding(s), "
            f"provider={provider}, elapsed={int(time.time() - lane_started)}s."
        )
        return {
            "jury": jury_name,
            "provider": provider,
            "status": "completed" if payload else "analysis_failed",
            "data": payload,
            "packet_urls": [item.get("url") for item in packets if item.get("url")],
            "packets": packets,
        }

    jury_results = []
    if analysis_ceiling >= 15:
        pool = concurrent.futures.ThreadPoolExecutor(max_workers=5)
        futures = {pool.submit(_analyze_one_jury, jury): jury for jury in FORENSIC_JURIES}
        done, pending = concurrent.futures.wait(futures, timeout=analysis_ceiling)
        for future in done:
            jury = futures[future]
            try:
                jury_results.append(future.result())
            except Exception as error:
                jury_results.append({"jury": jury, "provider": None, "status": "lane_error", "data": None, "error": repr(error)})
        for future in pending:
            future.cancel()
            jury_results.append({"jury": futures[future], "provider": None, "status": "group_timeout", "data": None})
        pool.shutdown(wait=False)
    else:
        jury_results = [
            {"jury": jury, "provider": None, "status": "budget_skipped", "data": None}
            for jury in FORENSIC_JURIES
        ]

    # --- PHASE 3: DETERMINISTIC MERGE, NORMALIZATION, AND DIAGNOSTICS ---
    postprocess_started = time.time()
    postprocess_deadline = postprocess_started + FORENSIC_POSTPROCESS_RESERVE_SECONDS
    data = _merge_forensic_jury_results(baseline, jury_results, routed_seeds)
    data["_search_provider"] = "parallel-gemini-acquisition+parallel-jury-analysis"
    data["forensic_acquisition_status"] = [
        {
            "jury": item.get("jury"),
            "provider": item.get("provider"),
            "status": item.get("status"),
            "sources": len(item.get("sources", [])),
            "pre_jury_rejected": len(item.get("pre_jury_rejected", [])),
        }
        for item in acquisition_results
    ]
    fresh_url_provenance = {
        _canonical_evidence_url(source.get("url")): lane.get("provider")
        for lane in acquisition_results
        for source in lane.get("sources", [])
        if source.get("url")
    }
    for finding in data.get("findings", []):
        provider = fresh_url_provenance.get(_canonical_evidence_url(finding.get("url")))
        if provider:
            finding["source_status"] = "fresh_discovery"
            finding["acquisition_provider"] = provider
            finding["forensic_provenance"] = "fresh_acquisition_jury_accepted"
    fresh_packets = sum(len(item.get("sources", [])) for item in acquisition_results)
    completed_juries = len([item for item in jury_results if item.get("data")])
    accepted_fresh = len([
        finding for finding in data.get("findings", [])
        if finding.get("source_status") == "fresh_discovery"
    ])
    source_coverage = _compute_forensic_coverage(data.get("findings", []), domain_hint)
    data["_source_forensic_coverage"] = source_coverage
    jury_counts = {
        jury: len([finding for finding in data.get("findings", []) if _finding_jury(finding) == jury])
        for jury in FORENSIC_JURIES
    }
    missing_juries = [jury for jury, count in jury_counts.items() if count == 0]
    if completed_juries == 5 and accepted_fresh and not missing_juries:
        provisional_mode = "all_juries_fresh_pending_validation"
    elif completed_juries and accepted_fresh:
        provisional_mode = "partially_fresh_pending_validation"
    elif data.get("findings"):
        provisional_mode = "seeded_pending_validation"
    else:
        provisional_mode = "degraded"
    data["forensic_run_mode"] = "pending_claim_validation"
    data["forensic_provisional_mode"] = provisional_mode
    print(
        f"   📊 [Forensic Merge] provisional_mode={provisional_mode}; "
        f"{len(data.get('findings', []))} judged/preserved findings; "
        f"{fresh_packets} fresh packets acquired, {accepted_fresh} accepted; "
        f"source coverage {source_coverage['score']}/100 (promotion pending)."
    )
    print(f"   📚 [Forensic Juries] {jury_counts}")
    if missing_juries:
        print(f"   ⚠️ [Forensic Juries] Missing judged coverage: {missing_juries}")
    data["_forensic_timing"] = {
        "acquisition_seconds": round(time.time() - acquisition_started, 2),
        "extraction_recovery_ceiling_seconds": FORENSIC_EXTRACTION_RECOVERY_CEILING_SECONDS,
        "analysis_seconds": round(time.time() - analysis_started, 2),
        "postprocess_reserve_seconds": FORENSIC_POSTPROCESS_RESERVE_SECONDS,
        "merge_normalization_seconds": round(time.time() - postprocess_started, 2),
    }
    data["_forensic_pre_jury_gate"] = pre_jury_stats

    # --- CONFIDENCE FLOOR (two-pass structuring safety net) ---
    # When the structuring model (Pass 2) cannot determine confidence from
    # free-form text, it may default to 0 even when real evidence exists.
    # Apply a minimum confidence based on actual evidence count to prevent
    # false "Manual Review Rec." labels on well-evidenced scans.
    if data:
        model_confidence = data.get("confidence_score", 0)
        evidence_with_urls = len([f for f in data.get("findings", []) if f.get("found") and f.get("url")])
        if model_confidence < 10 and evidence_with_urls >= 1:
            # Evidence exists but confidence is near-zero → structuring model failed to assess
            floor = min(50, evidence_with_urls * 15)  # 15 per evidence link, cap at 50
            data["confidence_score"] = floor
            print(f"   📊 [Confidence Floor] Model returned {model_confidence}/100 but {evidence_with_urls} evidence links found. Adjusted to {floor}/100.")

    # The former confidence-driven re-search loop is intentionally disabled.
    # Fresh discovery now occurs in the five parallel jury lanes above; starting
    # another web-search cycle here would consume the protected normalization
    # and diagnostics slot.
    if data and max_research_loops > 0:
        initial_findings = data.get("findings", [])
        initial_confidence = data.get("confidence_score", 50)
        initial_evidence_count = len([f for f in initial_findings if f.get("found") and f.get("url")])

        # CHECK: Should we re-search?
        # Trigger conditions:
        #   1. confidence < 45 AND fewer than 3 real evidence links, OR
        #   2. fewer than 2 evidence links regardless of confidence (e.g. JSON-repaired
        #      model responses that claim high confidence but provide no sources)
        needs_research = (initial_confidence < 45 and initial_evidence_count < 3) or initial_evidence_count < 2
        if data.get("_seeded_forensic_fallback"):
            needs_research = False
            print(
                f"   [Re-Search] Skipped: using seeded forensic fallback "
                f"({initial_evidence_count} revalidation-pending links). Preserving phase budget for synthesis."
            )
        if needs_research:
            print(f"   [Re-Search] Low confidence detected: {initial_confidence}/100 with {initial_evidence_count} evidence links.")

            all_findings = list(initial_findings)
            all_key_people = list(data.get("key_people", []))
            current_confidence = initial_confidence

            for loop in range(1, max_research_loops + 1):
                # Elapsed time guard — return what we have rather than risk timeout
                elapsed_total = time.time() - _fn_start
                if elapsed_total > _guard_research:
                    print(f"   ⏰ [Re-Search] Skipping loop {loop} — elapsed {int(elapsed_total)}s exceeds {_guard_research}s guard. Returning current results.")
                    break

                weak_juries = _identify_weak_juries(all_findings)

                if not weak_juries:
                    print(f"   [Re-Search] All juries have findings. No re-search needed.")
                    break

                print(f"   [Re-Search] Loop {loop}/{max_research_loops}: Targeting weak juries {weak_juries}...")

                research_prompt = _build_research_prompt(
                    search_name, domain_hint, weak_juries, loop, intent_score
                )

                if not research_prompt:
                    break

                # Re-search follows the same model split: GPT scouts first,
                # Sonnet challenges only if GPT cannot return usable data.
                research_data = execute_with_openai_web(research_prompt)
                if not research_data:
                    research_data = execute_with_anthropic(research_prompt)

                if not research_data:
                    print(f"   [Re-Search] Loop {loop} returned no parseable data. Stopping.")
                    break

                new_findings = research_data.get("findings", [])
                all_findings, new_unique_count = _merge_findings(all_findings, new_findings)

                # Merge any new key people
                existing_names = {p.get("name", "").lower() for p in all_key_people}
                for person in research_data.get("key_people", []):
                    if person.get("name", "").lower() not in existing_names:
                        all_key_people.append(person)
                        existing_names.add(person.get("name", "").lower())

                if new_unique_count == 0:
                    print(f"   [Re-Search] Loop {loop} found nothing new. Stopping (diminishing returns).")
                    break

                # Recalculate confidence
                weak_juries_after = _identify_weak_juries(all_findings)
                current_confidence = _recalculate_confidence(
                    current_confidence, new_unique_count, weak_juries, weak_juries_after
                )
                print(f"   [Re-Search] Loop {loop} added {new_unique_count} new findings. Confidence: {current_confidence}/100.")

                # Stop if confidence is now acceptable
                if current_confidence >= 45:
                    print(f"   [Re-Search] Confidence now acceptable ({current_confidence}). Stopping.")
                    break

            # Update the data dict with merged results
            data["findings"] = all_findings
            data["key_people"] = all_key_people
            data["confidence_score"] = current_confidence
            data["research_loops_used"] = loop
            print(f"   [Re-Search] Complete. Final confidence: {current_confidence}/100, total findings: {len(all_findings)}.")
        else:
            print(f"   [Re-Search] Skipped: confidence {initial_confidence}/100, evidence links {initial_evidence_count} (thresholds not met).")

    # --- PROCESS RESULTS ---
    checklist = []
    raw_findings = []
    # BASE SCORE: Set to 50 for symmetric headroom (50 down, 50 up).
    # Combined with expanded adjustment range (±50), this allows the full
    # 0-100 scale to be utilized. Top performers can reach ~90, poor ones ~10.
    base_score = 50

    if data:
        findings = data.get("findings", [])
        for f in findings:
            checklist.append({
                "category": f.get("category", "General"),
                "query": f.get("label", "Signal"),
                "found": f.get("found", False),
                "sentiment": f.get("sentiment", "neutral"),
                "is_recent": f.get("is_recent", False),
                "source_type": "current"
            })

            if f.get("found") and f.get("url"):
                raw_findings.append({
                    "title": f.get("title", "Evidence Found"),
                    "snippet": f.get("snippet", ""),
                    "source_quote": f.get("source_quote", f.get("snippet", "")),
                    "supported_claim": f.get("supported_claim", f.get("label", "")),
                    "label": f.get("label", f.get("supported_claim", "")),
                    "category": f.get("category", "General"),
                    "url": f.get("url", ""),
                    "source": get_clean_domain(f.get("url", "")),
                    "is_recent": f.get("is_recent", False),
                    "source_type": f.get("source_type") or "current",
                    "support_level": f.get("support_level"),
                    "claim_support_status": f.get("claim_support_status"),
                    "analysis_verified": f.get("analysis_verified") is True,
                    "generation_eligible": f.get("generation_eligible") is True,
                    "entity_scope": f.get("entity_scope"),
                    "source_database": f.get("source_database"),
                    "forensic_seed_source": bool(f.get("forensic_seed_source")),
                    "source_status": f.get("source_status"),
                    "acquisition_provider": f.get("acquisition_provider"),
                    "forensic_provenance": f.get("forensic_provenance"),
                    "verification_reason": f.get("verification_reason"),
                    "cached_excerpt": f.get("cached_excerpt"),
                })

        # --- EVIDENCE PERSISTENCE ---
        # Collect evidence from ALL previous scans (up to 3 files in Vercel Blob).
        # Previous evidence is "innocent until proven fixed" — it doesn't vanish just
        # because Google returned different results this time.
        # Evidence expires after 90 days only.
        if previous_context:
            # Build set of current evidence URLs for dedup
            current_urls = {f.get("url", "").rstrip("/").lower() for f in raw_findings if f.get("url")}
            current_queries = {c.get("query", "").lower() for c in checklist}

            # Collect evidence from ALL previous scans (latest + history)
            all_prev_scans = []

            # 1. Latest scan (most recent file)
            all_prev_scans.append({
                "date": previous_context.get("last_scanned", ""),
                "evidence": previous_context.get("previous_evidence", []),
                "findings": previous_context.get("previous_findings", [])
            })

            # 2. Older scans from history (up to 2 more files)
            for older in previous_context.get("scan_history", []):
                all_prev_scans.append({
                    "date": older.get("date", ""),
                    "evidence": older.get("evidence", []),
                    "findings": older.get("findings", [])
                })

            historical_count = 0
            for scan in all_prev_scans:
                # Check age — expire after 90 days
                scan_days = 999
                try:
                    scan_date = scan.get("date", "")
                    if scan_date:
                        from datetime import datetime as dt
                        scan_days = (dt.utcnow() - dt.fromisoformat(scan_date)).days
                except (ValueError, TypeError):
                    pass

                if scan_days >= 90:
                    continue

                # Carry forward evidence URLs not in current results
                for prev_ev in scan.get("evidence", []):
                    prev_url = (prev_ev.get("url", "") or "").rstrip("/").lower()
                    if prev_url and prev_url not in current_urls:
                        raw_findings.append({
                            "title": prev_ev.get("title", "Historical Evidence"),
                            "snippet": prev_ev.get("snippet", ""),
                            "url": prev_ev.get("url", ""),
                            "source": prev_ev.get("source", get_clean_domain(prev_ev.get("url", ""))),
                            "is_recent": False,
                            "source_type": "historical"
                        })
                        current_urls.add(prev_url)
                        historical_count += 1

                # Carry forward checklist findings not in current results
                for prev_f in scan.get("findings", []):
                    prev_query = (prev_f.get("query", "") or prev_f.get("label", "") or "").lower()
                    if prev_query and prev_query not in current_queries and prev_f.get("found", False):
                        checklist.append({
                            "category": prev_f.get("category", "General"),
                            "query": prev_f.get("query", prev_f.get("label", "Signal")),
                            "found": True,
                            "sentiment": prev_f.get("sentiment", "neutral"),
                            "is_recent": False,
                            "source_type": "historical"
                        })
                        current_queries.add(prev_query)
                        historical_count += 1

            if historical_count > 0:
                print(f"   [Evidence Persistence] Carried forward {historical_count} historical finding(s) from {len(all_prev_scans)} previous scan(s).")

        adjustment = data.get("reality_score_adjustment", 0)
        confidence = data.get("confidence_score", 50)

        final_score = max(0, min(100, base_score + adjustment))

        # Large deltas are recorded for synthesis review. Starting another web
        # search here would violate the bounded acquisition/analysis ceilings.
        if previous_context and previous_context.get("previous_reality") is not None and not data.get("_seeded_forensic_fallback"):
            prev_reality = previous_context["previous_reality"]
            delta = final_score - prev_reality

            if abs(delta) >= 25:
                elapsed_total = time.time() - _fn_start
                print(
                    f"   📏 [Delta Verification] Large delta {delta} noted at {int(elapsed_total)}s. "
                    "No late web re-scan is started; source gates and synthesis will judge the change."
                )

        # Determine Washing based on AI flag OR raw gap if flag is missing
        washing_flag = data.get("washing_flag", False)
        washing_type = data.get("washing_type", None)

        # Compatibility field only. Stakeholder discovery belongs to Buying Insight.
        key_people = []

        # --- URL VALIDATION GATE ---
        # Verify all evidence URLs actually exist before returning results.
        # Marks each link/checklist entry with "verified": true/false.
        # Penalizes confidence when many URLs are dead (hallucinated evidence).
        raw_findings, checklist, url_verified, url_dead = _validate_evidence_urls(raw_findings, checklist, company_name=company_name)

        # --- SOURCE ADMISSIBILITY GATE (Phase 1) ---
        # URL liveness is not enough: wrong-entity and dead-link evidence must not
        # feed factual generation. Narrow/foresight sources remain admissible, but
        # are annotated so downstream prompts/validators can restrict their use.
        # NOTE: checklist items are Scanner 2's own analysis findings — they are NOT
        # external sources and must not be filtered by source admissibility rules.
        # Annotating them for metadata is fine; filtering them out is not.
        company_domain = get_clean_domain(company_url) if company_url else ""
        raw_findings = annotate_evidence_links(raw_findings, company_domain, company_name)
        checklist = annotate_evidence_links(checklist, company_domain, company_name)
        raw_findings, excluded_evidence = filter_admissible_sources(raw_findings, company_domain, company_name)
        excluded_total = len(excluded_evidence)
        if excluded_total:
            reasons = {}
            for _src in excluded_evidence:
                reason = _src.get("exclusion_reason") or "excluded"
                reasons[reason] = reasons.get(reason, 0) + 1
            penalty = min(20, excluded_total * 4)
            old_conf = int(confidence)
            confidence = max(10, confidence - penalty)
            print(f"   🧹 [Source Gate] Excluded {excluded_total} sources {reasons} → confidence penalty -{penalty} ({old_conf} → {int(confidence)})")

        # Report URL failures, but do not start a third discovery cycle.
        url_total = url_verified + url_dead
        dead_ratio = url_dead / url_total if url_total > 0 else 0

        if dead_ratio > 0.20 and url_dead >= 2:
            print(
                f"   ⚠️ [URL Diagnostics] {int(dead_ratio*100)}% URLs failed ({url_dead}/{url_total}). "
                "No late recovery search started; preserving the 30s post-processing slot."
            )

        # --- CONFIDENCE PENALTY (after recovery attempt) ---
        if url_total > 0:
            dead_ratio = url_dead / url_total
            if dead_ratio > 0.5:
                penalty = int(dead_ratio * 30)
                old_conf = int(confidence)
                confidence = max(10, confidence - penalty)
                print(f"   ⚠️ [URL Validation] {int(dead_ratio*100)}% URLs still dead → confidence penalty -{penalty} ({old_conf} → {int(confidence)})")
            elif dead_ratio > 0.25:
                penalty = int(dead_ratio * 15)
                old_conf = int(confidence)
                confidence = max(10, confidence - penalty)
                print(f"   ⚠️ [URL Validation] {int(dead_ratio*100)}% URLs still dead → confidence penalty -{penalty} ({old_conf} → {int(confidence)})")

        # --- SOURCE-CLAIM RELEVANCE VALIDATION ---
        # Check if verified sources actually support their claimed snippets.
        # Only runs when time permits (adds ~5-10s for page fetches + Flash calls).
        _elapsed = time.time() - _fn_start
        postprocess_remaining = max(0, int(postprocess_deadline - time.time()))
        if postprocess_remaining >= 8 and url_verified >= 1:
            try:
                from source_claim_validator import validate_source_claims
                max_claim_checks = 12 if postprocess_remaining >= 18 else 6
                scv_result = _hard_timeout_call(
                    "forensic source-claim validation",
                    postprocess_remaining,
                    lambda: validate_source_claims(
                        raw_findings,
                        max_checks=max_claim_checks,
                        max_workers=4,
                        company_name=company_name,
                        company_domain=company_domain,
                    ),
                )
                if not scv_result:
                    raise TimeoutError("source-claim validation exceeded post-processing deadline")
                raw_findings = annotate_evidence_links(raw_findings, company_domain, company_name)
                if scv_result["irrelevant"] > 0:
                    # Penalise confidence for irrelevant sources
                    irr_ratio = scv_result["irrelevant"] / max(scv_result["checked"], 1)
                    if irr_ratio > 0.3:
                        penalty = int(irr_ratio * 15)
                        old_conf = int(confidence)
                        confidence = max(10, confidence - penalty)
                        print(f"   ⚠️ [Source-Claim] {int(irr_ratio*100)}% sources irrelevant → confidence penalty -{penalty} ({old_conf} → {int(confidence)})")
            except Exception as scv_err:
                print(f"   ⚠️ [Source-Claim] Validation skipped: {scv_err}")
        elif postprocess_remaining < 8:
            print(
                f"   ⏰ [Source-Claim] Skipped — only {postprocess_remaining}s remain in the protected "
                f"{FORENSIC_POSTPROCESS_RESERVE_SECONDS}s post-processing slot"
            )

        # Ensure every source carries final promotion metadata, even when
        # source-claim validation was skipped or only covered part of the pool.
        raw_findings = annotate_evidence_links(raw_findings, company_domain, company_name)

        promoted_coverage = _compute_promoted_evidence_coverage(raw_findings, company_domain)
        source_coverage = data.get("_source_forensic_coverage") or _compute_forensic_coverage(raw_findings, company_domain)
        data["_final_forensic_coverage"] = {
            "source_coverage": source_coverage,
            "promoted_evidence_coverage": promoted_coverage,
        }
        exact_by_jury = {
            jury: len([
                item for item in raw_findings
                if _finding_jury(item) == jury
                and item.get("analysis_verified") is True
                and item.get("claim_support_status") == "supported"
                and item.get("support_level") == "exact"
            ])
            for jury in FORENSIC_JURIES
        }
        fresh_exact_juries = {
            _finding_jury(item) for item in raw_findings
            if item.get("source_status") == "fresh_discovery"
            and item.get("support_level") == "exact"
            and item.get("claim_support_status") == "supported"
        }
        completed_juries = len([item for item in data.get("forensic_jury_status", []) if item.get("status") == "completed"])
        if completed_juries == 5 and len(fresh_exact_juries) == 5:
            final_mode = "full"
        elif fresh_exact_juries:
            final_mode = "partially_fresh"
        elif promoted_coverage.get("promoted_findings"):
            final_mode = "seeded_only"
        else:
            final_mode = "degraded"
        data["forensic_run_mode"] = final_mode
        print(
            f"   📐 [Forensic Coverage] source={source_coverage.get('score', 0)}/100; "
            f"promoted={promoted_coverage.get('score', 0)}/100; exact findings={promoted_coverage.get('promoted_findings', 0)}; "
            f"exact by jury={exact_by_jury}; mode={final_mode}."
        )

        # --- SOURCE QUALITY ASSESSMENT ---
        # Evaluate credibility of each source domain (not just URL liveness).
        # Adds 'source_credibility' field to each evidence link.
        # Penalizes confidence when evidence comes from low-quality sources.
        high_q, med_q, low_q, quality_score = assess_evidence_quality(raw_findings, company_domain)
        total_q = high_q + med_q + low_q
        if total_q > 0:
            print(f"   🔍 [Source Quality] {high_q} high, {med_q} medium, {low_q} low credibility sources (quality: {quality_score}/100)")
        confidence = confidence_quality_penalty(confidence, raw_findings, company_domain)

        postprocess_elapsed = round(time.time() - postprocess_started, 2)
        data.setdefault("_forensic_timing", {})["postprocess_seconds"] = postprocess_elapsed
        data["_forensic_timing"]["postprocess_overrun"] = postprocess_elapsed > FORENSIC_POSTPROCESS_RESERVE_SECONDS
        print(
            f"   🧭 [Forensic Postprocess] merge, URL/entity/claim gates, normalization and diagnostics "
            f"completed in {postprocess_elapsed}s (reserved {FORENSIC_POSTPROCESS_RESERVE_SECONDS}s)"
        )

        return {
            "score": int(final_score),
            "checklist": checklist,
            "links": raw_findings,
            "key_people": key_people,
            "is_simulated": False,
            "confidence_score": int(confidence),
            "washing_flag": washing_flag,
            "washing_type": washing_type,
            "excluded_evidence": excluded_evidence if 'excluded_evidence' in locals() else [],
            "evidence_sufficiency": evidence_sufficiency_score(raw_findings, company_domain),
            "forensic_fallback": bool(data.get("_seeded_forensic_fallback")),
            "forensic_fallback_reason": data.get("_fallback_reason"),
            "company_evidence_seed_count": data.get("_company_evidence_seed_count", 0),
            "historical_seed_count": data.get("_historical_seed_count", 0),
            "forensic_timing": data.get("_forensic_timing", {}),
            "forensic_batch_status": data.get("forensic_batch_status", []),
            "forensic_coverage": data.get("_final_forensic_coverage", {}),
        }
    
    # --- FALLBACK: All models failed — return empty result, never fabricate evidence ---
    print(f"   [Scanner 2] All models failed for {company_name}. Returning empty result only because no cached or historical evidence was available.")
    return { "score": 0, "checklist": [], "links": [], "key_people": [], "is_simulated": True, "confidence_score": 0, "washing_flag": False, "washing_type": None }


# ==============================================================================
# FINANCIAL HEALTH INDEX — Separate from 5-jury reality check
# ==============================================================================

def _execute_financial_search(model_name, prompt, company_name, timeout=120):
    """Execute a GPT/Claude web-search financial data request. Returns parsed dict or None."""
    try:
        print(f"   [Financial Health] Attempting {model_name} for {company_name} (timeout={timeout}s)...")
        start = time.time()
        if model_name.startswith("claude-"):
            text_content = call_anthropic_text(
                prompt,
                model=model_name,
                max_tokens=10000,
                timeout=timeout,
                web_search=True,
                max_searches=8,
                thinking_budget=3000,
            )
        else:
            text_content = call_openai_text(
                prompt,
                model=model_name,
                max_output_tokens=10000,
                timeout=timeout,
                web_search=True,
                search_context_size="medium",
            )
        json_payload = extract_json_from_text(text_content)
        elapsed = int((time.time() - start) * 1000)
        if json_payload:
            try:
                parsed = json.loads(json_payload, strict=False)
                print(f"   [Financial Health] {model_name} succeeded in {elapsed}ms")
                return parsed
            except json.JSONDecodeError as je:
                print(f"   [Financial Health] {model_name} JSON parse failed: {je}")
        if text_content and len(text_content) > 100:
            repaired = _repair_json_with_sonnet(text_content, company_name)
            if repaired:
                print(f"   [Financial Health] JSON repair recovered data from {model_name}")
                return repaired
        print(f"   [Financial Health] {model_name} returned no parseable JSON ({elapsed}ms)")
        return None
    except Exception as e:
        print(f"   [Financial Health] {model_name} failed: {e}")
        return None

def migrate_financial_sub_scores(sub_scores: dict) -> dict:
    """Rename legacy 'efficiency' key to 'financial_stability' in stored sub_scores."""
    if not sub_scores or not isinstance(sub_scores, dict):
        return sub_scores
    if "efficiency" in sub_scores and "financial_stability" not in sub_scores:
        sub_scores["financial_stability"] = sub_scores.pop("efficiency")
    return sub_scores


def compute_financial_index(sub_scores: dict) -> int:
    """
    Weighted Financial Health Index — fundamentals-first weighting.

    Profitability is the dominant signal (60%). Financial Stability (15%)
    has low variance across companies but still anchors balance sheet
    health. Revenue Trend (15%) captures growth trajectory.

    Stage 1: Weighted linear base
      - Profitability:         60%  (dominant health signal)
      - Financial Stability:   15%  (balance sheet health)
      - Revenue Trend:         15%  (growth trajectory)
      - Investment Signal:     10%  (forward-looking tiebreaker)

    Stage 2: Softer core health gate
      Gate now includes Stability alongside Revenue+Profitability.
      Gate floor 0.35, threshold 0.60.

    Stage 3: Profitability penalty amplifier
      Unprofitable companies get an extra penalty on top of the gate:
      - P < 8 (losses):        ×0.85
      - P < 4 (severe losses): ×0.70
    """
    R = sub_scores.get("revenue_trend", 10)
    P = sub_scores.get("profitability", 10)
    E = sub_scores.get("financial_stability", 10)
    I = sub_scores.get("investment_signal", 10)

    # Stage 1: Weighted linear base (0-100)
    base = (0.15 * R + 0.60 * P + 0.15 * E + 0.10 * I) * 4

    # Stage 2: Softer core health gate — penalizes when all three fundamentals are weak
    core_pct = (R + P + E) / 75.0
    GATE_FLOOR = 0.35
    GATE_THRESHOLD = 0.60
    gate = min(1.0, GATE_FLOOR + (1.0 - GATE_FLOOR) * core_pct / GATE_THRESHOLD)

    # Stage 3: Profitability penalty amplifier — extra hit for actual losses
    if P < 4:
        profit_mult = 0.70
    elif P < 8:
        profit_mult = 0.85
    else:
        profit_mult = 1.0

    score = round(base * gate * profit_mult)
    return max(0, min(100, score))


def financial_health_search(company_name: str, company_url: str = None, model_id: str = None) -> dict:
    """
    FINANCIAL HEALTH INDEX: Separate from the 5-jury reality check.
    Uses Claude/OpenAI web search to find financial data.
    Returns a 0-100 composite score with 4 sub-dimensions (0-25 each).
    """
    primary_model = model_id or "claude-sonnet-4-6"
    _fh_start = time.time()
    _FH_BUDGET = 180  # seconds — abort remaining attempts if cumulative elapsed exceeds this

    # Dynamic year window — always search for the two most recent calendar years
    # so results stay current without manual updates each January.
    _cur_year = date.today().year
    _prev_year = _cur_year - 1

    search_name = clean_company_name_for_search(company_name)
    domain_hint = get_clean_domain(company_url) if company_url else f"{search_name.lower().replace(' ', '')}.fi"
    entity_context_block = build_entity_context_block(company_name)
    entity_terms = entity_search_terms(company_name)
    entity_search_block = ""
    if entity_terms and len(entity_terms) > 1:
        entity_search_block = "Also use these entity search terms where relevant: " + ", ".join(f'"{t}"' for t in entity_terms[:8]) + "."

    DEFAULT_RESULT = {
        "financial_index": None,
        "sub_scores": {"revenue_trend": None, "profitability": None, "financial_stability": None, "investment_signal": None},
        "financial_findings": [],
        "financial_confidence": 0,
        "is_simulated": True
    }

    prompt = f"""Perform a FINANCIAL HEALTH ASSESSMENT on "{search_name}" (Domain: {domain_hint}).

TARGET: Finnish companies. Use BOTH English and Finnish search terms.
{entity_context_block}
{entity_search_block}

ENTITY-SCOPE RULE — CRITICAL:
Financial data for a local legal entity is admissible only as local legal-entity evidence. If a source describes "{search_name} Oy", a country subsidiary, a former name, or an owner/acquirer, set entity_scope accordingly and do NOT treat it as unqualified group-wide financial performance unless a group-level source explicitly supports that.

DATA FRESHNESS — CRITICAL RULE:
Always use the MOST RECENT financial data available. Today is {date.today().isoformat()}.
Priority order: {_cur_year} full-year results > {_prev_year} full-year results > interim reports.
If {_cur_year} annual results have been published, use them. Do NOT use older data when newer is available.
State explicitly which year's results you are basing each score on.

SEARCH STRATEGY — Execute these searches:

1. REVENUE TREND (liikevaihto):
   - `"{search_name}" liikevaihto tulos {_prev_year} {_cur_year}`
   - `"{search_name}" annual report revenue profit {_prev_year} {_cur_year}`
   - `site:kauppalehti.fi "{search_name}" liikevaihto` OR `site:talouselama.fi "{search_name}"`
   - `"{search_name}" tilinpäätös vuosikertomus {_cur_year}`
   EVALUATE: Is revenue growing, stable, or declining over the last 2-3 years?

2. PROFITABILITY (kannattavuus):
   - `"{search_name}" operating profit EBITDA margin {_prev_year} {_cur_year}`
   - `"{search_name}" liikevoitto käyttökate tulos`
   - `site:finder.fi "{search_name}"` OR `site:asiakastieto.fi "{search_name}"`
   EVALUATE: Is the company profitable? Strong margins, moderate, weak, or negative?

3. FINANCIAL STABILITY (taloudellinen vakaus):
   - `site:finder.fi "{search_name}"` OR `site:asiakastieto.fi "{search_name}"`
   - `"{search_name}" omavaraisuusaste velkaantuneisuus tase`
   - `"{search_name}" equity ratio debt balance sheet {_prev_year} {_cur_year}`
   - `"{search_name}" luottoluokitus credit rating`
   EVALUATE: Balance sheet health — equity ratio, debt levels, liquidity. Finnish sources (Finder.fi, Asiakastieto) often publish equity ratio and credit ratings directly.

4. INVESTMENT SIGNAL (investoinnit):
   - `"{search_name}" R&D investment technology capex {_prev_year} {_cur_year}`
   - `"{search_name}" T&K investoinnit teknologia`
   - `"{search_name}" hiring engineers developers rekrytointi kehittäjä`
   - `"{search_name}" acquisition yrityskauppa technology`
   EVALUATE: Is the company investing in growth? R&D spend, tech hiring, acquisitions.

SCORING LOGIC:
Each dimension scores 0-25. Use the FULL range — most companies should land in the 8-17 zone, not cluster at 15-20.
- Revenue Trend: 0-7 (declining >10%), 8-13 (stable ±5%), 14-19 (growing 5-15%), 20-25 (growing >15%)
- Profitability: 0-7 (negative/losses), 8-13 (weak/breakeven <5% margin), 14-19 (moderate 5-15%), 20-25 (strong >15%)
- Financial Stability: 0-7 (high debt, weak equity ratio <20%, credit risk), 8-13 (moderate leverage, equity ratio 20-35%), 14-19 (solid balance sheet, equity ratio 35-50%), 20-25 (fortress balance sheet, equity ratio >50%, minimal debt)
- Investment Signal: 0-7 (no visible investment), 8-13 (maintenance level), 14-19 (moderate growth), 20-25 (aggressive investment/M&A)

CALIBRATION ANCHOR: A typical stable Finnish IT company with modest profitability should score around 10-13 per dimension, NOT 15-20. Reserve 14+ for companies with clearly above-average performance with evidence to back it up.

FINANCIAL FIGURE ACCURACY — CRITICAL:
When reading financial reports, distinguish between BASE FIGURES and YEAR-OVER-YEAR CHANGES:
- "Operating profit decreased by €153.7M" → the €153.7M is the CHANGE, NOT the previous profit
- "Revenue was €3.5B, down from €4.15B" → the actual figures are €3.5B (current) and €4.15B (previous)
- Always report the ACTUAL metric values in evidence_snippet, not the delta/change amount misidentified as a base figure
- If an interim report states both the current figure and the YoY change, use the ACTUAL FIGURE for scoring

If no financial data is found for a dimension, score it 10 (neutral — reflects uncertainty, not health).

RETURN STRICT JSON:
{{{{
    "revenue_trend": {{{{
        "score": (integer 0-25),
        "assessment": "Brief assessment (growing/stable/declining with % if available)",
        "evidence_snippet": "Key quote or data point found",
        "source_url": "URL where found",
        "source_title": "Page/article title",
        "entity_scope": "canonical_group" or "local_legal_entity" or "former_name" or "subsidiary" or "parent_owner" or "wrong_entity" or "unknown"
    }}}},
    "profitability": {{{{
        "score": (integer 0-25),
        "assessment": "Brief assessment",
        "evidence_snippet": "Key data point",
        "source_url": "URL",
        "source_title": "Title",
        "entity_scope": "canonical_group" or "local_legal_entity" or "former_name" or "subsidiary" or "parent_owner" or "wrong_entity" or "unknown"
    }}}},
    "financial_stability": {{{{
        "score": (integer 0-25),
        "assessment": "Brief assessment with equity ratio/debt level if available",
        "evidence_snippet": "Key data point",
        "source_url": "URL",
        "source_title": "Title",
        "entity_scope": "canonical_group" or "local_legal_entity" or "former_name" or "subsidiary" or "parent_owner" or "wrong_entity" or "unknown"
    }}}},
    "investment_signal": {{{{
        "score": (integer 0-25),
        "assessment": "Brief assessment of investment activity",
        "evidence_snippet": "Key data point",
        "source_url": "URL",
        "source_title": "Title",
        "entity_scope": "canonical_group" or "local_legal_entity" or "former_name" or "subsidiary" or "parent_owner" or "wrong_entity" or "unknown"
    }}}},
    "financial_confidence": (integer 0-100)
}}}}

CONFIDENCE GUIDE:
- 0-30: Almost no financial data found (private company, very small)
- 31-60: Some data found but incomplete (e.g., only revenue, no profit)
- 61-80: Good coverage (multiple years, multiple metrics)
- 81-100: Comprehensive (detailed financials, analyst coverage, multiple sources)
"""

    # Attempt 1: Primary model with one retry
    data = _execute_financial_search(primary_model, prompt, company_name, timeout=120)
    if not data:
        if (time.time() - _fh_start) > _FH_BUDGET:
            print(f"   ⏭ [Financial Health] Budget exceeded after attempt 1. Aborting.")
        else:
            print(f"   [Financial Health] Primary model failed, retrying in 2s...")
            time.sleep(2)
            data = _execute_financial_search(primary_model, prompt, company_name, timeout=120)

    # Attempt 2: Fallback to GPT-5.5 web search with one retry
    if not data:
        if (time.time() - _fh_start) > _FH_BUDGET:
            print(f"   ⏭ [Financial Health] Budget exceeded before fallback. Aborting.")
        else:
            print(f"   [Financial Health] Primary exhausted. Falling back to GPT-5.5 web search...")
            data = _execute_financial_search("gpt-5.5", prompt, company_name, timeout=120)
    if not data and (time.time() - _fh_start) <= _FH_BUDGET:
        time.sleep(2)
        data = _execute_financial_search("gpt-5.5", prompt, company_name, timeout=120)

    # Abort: all attempts exhausted — return explicit no-data result
    if not data:
        print(f"   ❌ [Financial Health] ABORT: All 4 attempts failed for {company_name}. No financial data available.")
        return DEFAULT_RESULT

    # Assemble result
    dimensions = ["revenue_trend", "profitability", "financial_stability", "investment_signal"]
    sub_scores = {}
    findings = []
    defaulted_count = 0

    for dim in dimensions:
        dim_data = data.get(dim, {})
        if isinstance(dim_data, dict) and "score" in dim_data:
            score = max(0, min(25, int(dim_data.get("score", 10))))
        else:
            score = 10
            defaulted_count += 1
            dim_data = dim_data if isinstance(dim_data, dict) else {}
        sub_scores[dim] = score
        findings.append({
            "dimension": dim.replace("_", " ").title(),
            "score": score,
            "assessment": dim_data.get("assessment", "No data found"),
            "evidence_snippet": dim_data.get("evidence_snippet", ""),
            "source_url": dim_data.get("source_url", ""),
            "source_title": dim_data.get("source_title", ""),
            "entity_scope": dim_data.get("entity_scope", "unknown")
        })

    # Quality gate: if all dimensions fell back to defaults, the API returned
    # no real financial data — treat the entire result as missing.
    if defaulted_count == len(dimensions):
        print(f"   ⚠️ [Financial Health] All dimensions defaulted for {company_name}. Treating as no data.")
        return DEFAULT_RESULT

    composite = compute_financial_index(sub_scores)
    confidence = max(0, min(100, int(data.get("financial_confidence", 40))))

    print(f"   ✅ [Financial Health] {company_name}: Index={composite}/100 (Rev={sub_scores['revenue_trend']}, Prof={sub_scores['profitability']}, Stab={sub_scores['financial_stability']}, Inv={sub_scores['investment_signal']}), Confidence={confidence}")

    return {
        "financial_index": composite,
        "sub_scores": sub_scores,
        "financial_findings": findings,
        "financial_confidence": confidence,
        "is_simulated": False
    }
