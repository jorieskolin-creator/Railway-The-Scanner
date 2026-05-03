
import os
import json
import time
import re
import requests as http_requests
import concurrent.futures
from datetime import date
from gemini_backoff import call_with_503_backoff
from urllib.parse import urlparse
from google import genai
from google.genai import types
from gemini_client import get_gemini_client
import anthropic
from knowledge_base import get_evolving_framework
from source_quality import assess_evidence_quality, confidence_quality_penalty, annotate_evidence_links, filter_admissible_sources, evidence_sufficiency_score

# --- HELPER: IMPROVE SEARCH RECALL ---
def clean_company_name_for_search(name):
    """
    Removes legal suffixes to ensure Google Search finds natural language discussions.
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
    (1, re.compile(r'\b(CEO|COO|President|Chairman|Toimitusjohtaja|Maajohtaja|Managing\s+Director|Country\s+Manager|Group\s+Executive|Pääjohtaja|Hallituksen\s+puheenjohtaja)\b', re.I)),
    (2, re.compile(r'\b(CTO|CIO|CDO|CPO|CFO|CHRO|CMO|Chief\s+\w+\s+Officer|Executive\s+Vice\s+President|EVP)\b', re.I)),
    (3, re.compile(r'\b(SVP|Senior\s+Vice\s+President|VP|Vice\s+President|Board\s+Member|Hallituksen\s+jäsen)\b', re.I)),
    (4, re.compile(r'\b(Director)\b', re.I)),
    (5, re.compile(r'\b(Head\s+of)\b', re.I)),
    (6, re.compile(r'\b(Senior\s+Manager|Manager|Lead)\b', re.I)),
]

def _get_seniority_tier(role: str) -> int:
    """Return seniority tier (1=highest) for a role string. 7 = unknown/unranked."""
    if not role:
        return 7
    for tier, pattern in _SENIORITY_TIERS:
        if pattern.search(role):
            return tier
    return 7


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

        # Filter out too-junior roles for large companies
        # Only reject tier 6 (known Manager/Lead); tier 7 (unrecognized) may be senior
        role = (person.get("role") or "").strip()
        tier = _get_seniority_tier(role)
        if employees and employees >= 5000 and tier == 6:
            removed.append(f"{name} ({role}) [too junior for {employees:,} employee org]")
            continue
        if employees and employees >= 200 and tier == 6:
            # For mid-size: allow managers only if we'd otherwise have < 3 stakeholders
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
    """
    result = {"verified": False, "company_mentioned": None, "extracted_date": None}
    if not url or not url.startswith("http"):
        return result

    try:
        # Use GET instead of HEAD to read body for soft 404 detection
        resp = http_requests.get(url, timeout=timeout, allow_redirects=True, stream=True,
                                  headers={"User-Agent": "Mozilla/5.0 (compatible; AdaptabilityScanner/1.0)"})

        if resp.status_code >= 400:
            resp.close()
            return result

        # Read first 10KB of body for soft 404 + company name check
        body_chunk = ""
        try:
            raw = resp.raw.read(10240)
            body_chunk = raw.decode("utf-8", errors="ignore").lower()
        except Exception:
            pass
        resp.close()

        # Check for soft 404 patterns in the page body
        if body_chunk and _SOFT_404_PATTERNS.search(body_chunk):
            return result

        result["verified"] = True

        # Extract publication date from URL and page body
        result["extracted_date"] = _extract_page_date(url, body_chunk)

        # Check company name presence if provided
        if company_name and body_chunk:
            name_lower = company_name.lower()
            name_parts = [p for p in name_lower.split() if len(p) > 2 and p not in ("oy", "oyj", "ab", "ltd", "group", "inc", "gmbh", "oy:n", "abp")]
            result["company_mentioned"] = any(part in body_chunk for part in name_parts) if name_parts else True

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
    sources as context-only.
    """
    if not isinstance(data, dict):
        return data
    for field in ["evidence_links", "foresight_links", "foresight_evidence", "deep_dive_findings", "source_cards"]:
        if isinstance(data.get(field), list):
            data[field] = [classify_generation_context(x) if isinstance(x, dict) else x for x in data[field]]
    return data


def _repair_json_with_sonnet(raw_text, company_name, is_structuring=False, citations=None):
    """
    JSON Structuring / Repair Gate.

    Two modes:
    - Structuring (is_structuring=True): Standard Pass 2 of the two-pass pipeline.
      Sonnet's web search produced free-form evidence; this call converts it to JSON.
      No confidence penalty — this is the designed flow.
    - Repair (is_structuring=False): Fallback when Gemini returns unstructured text
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
  "key_people": [
    {{"name": "First Last (FULL NAME REQUIRED)", "role": "Exact Title", "relevance": "Why relevant to modernization"}}
  ],
  "reality_score_adjustment": <integer -50 to +50>,
  "confidence_score": <integer 0-100>,
  "washing_flag": <boolean>,
  "washing_type": <string or null>
}}

Extract as much data as possible from the text below. Preserve all findings, URLs, and evidence. If a field cannot be determined, use sensible defaults (0 for reality_score_adjustment, empty arrays for lists, false for booleans).

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


def _structure_with_gemini(raw_text, company_name, model_name):
    """Structure free-form forensic evidence text into JSON using Gemini (no search tools).

    Used as Pass 2 of the two-pass forensic pipeline.
    Mirrors the prompt schema of _repair_json_with_sonnet so either model can
    fulfil the structuring role — temperature 0.0, no thinking, no tools.
    """
    try:
        client = get_gemini_client()
    except ValueError:
        return None
    try:
        structuring_prompt = f"""The following text is a forensic reality-check analysis for "{company_name}" produced by a web-search-grounded AI model. Your job is to structure this analysis into valid JSON, preserving ALL evidence, scores, and assessments exactly as stated.

The expected JSON structure is:
{{
  "findings": [
    {{"category": "Operating Model", "label": "Brief Label", "found": true, "sentiment": "positive", "snippet": "Evidence quote...", "title": "Source Title", "url": "https://...", "is_recent": true}}
  ],
  "key_people": [
    {{"name": "First Last (FULL NAME REQUIRED)", "role": "Exact Title", "relevance": "Why relevant to modernization"}}
  ],
  "reality_score_adjustment": <integer -50 to +50>,
  "confidence_score": <integer 0-100>,
  "washing_flag": <boolean>,
  "washing_type": <string or null>
}}

Extract as much data as possible from the text below. Preserve all findings, URLs, and evidence. If a field cannot be determined, use sensible defaults (0 for reality_score_adjustment, empty arrays for lists, false for booleans).

IMPORTANT for confidence_score: Do NOT default to 0. Assess confidence based on the evidence density in the text:
- Count findings with real URLs and specific quotes → more evidence = higher confidence
- 0-40: Very few sources, evidence is generic or very old
- 41-70: Some specific evidence found, but mixed recency or limited sources
- 71-100: Multiple independent sources confirm findings, evidence is recent and specific
If the text contains 3+ findings with URLs, confidence should be at least 50.

TEXT TO PARSE:
{raw_text[:12000]}

Return ONLY valid JSON, no explanation."""
        response = client.models.generate_content(
            model=model_name,
            contents=structuring_prompt,
            config={
                'temperature': 0.0,
                'max_output_tokens': 8000,
            }
        )
        text = response.text if response.text else ""
        json_payload = extract_json_from_text(text)
        if not json_payload:
            return None
        return json.loads(json_payload, strict=False)
    except Exception as e:
        print(f"   ❌ [Gemini Structure] {model_name} structuring failed: {e}")
        return None


def _build_previous_context_block(previous_context):
    """
    Builds an optional prompt section from previous scan data.
    Uses VERIFICATION-FIRST approach with MULTI-SCAN LOOKBACK (up to 3 scans).
    The most recent summary is the primary checklist; older summaries reveal
    persistent patterns that should carry extra weight.
    Returns empty string if no previous context is available.
    """
    if not previous_context:
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
    the current scan. This ensures Gemini re-verifies known sources and
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

    if not url_lines and not keyword_lines:
        return ""

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

    return "\n\n".join(anchors)


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

    URL QUALITY RULE: ONLY include URLs from actual Google Search results.
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
        "key_people": [
            {{
                "name": "First Name Last Name (FULL NAME REQUIRED — do NOT include if last name unknown)",
                "role": "Exact Title",
                "relevance": "Why relevant to modernization?"
            }}
        ],
        "new_evidence_count": (integer, how many NEW pieces of evidence you found)
    }}

    KEY PEOPLE RULES: Only include stakeholders with VERIFIED full names (first + last). Quality over quantity — 0 is fine if none found.

    If you find NO new evidence at all, return: {{"findings": [], "key_people": [], "new_evidence_count": 0}}
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

    Pre-computes diagnostics from actual data, then asks Gemini 2.5 Pro (with Google
    Search grounding) to interpret them and select from a constrained set of
    actionable suggestion types. This keeps Opus free for Synthesis only.

    Returns a dict with analysis results, or None if analysis fails.
    """
    api_key = os.environ.get("GEMINI_API_KEY") or os.environ.get("API_KEY")
    if not api_key:
        return None

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
   - search_variance: URL overlap is low — same queries hit different Google results
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
        try:
            client = get_gemini_client()
        except ValueError:
            print("   [Self-Analysis] No GEMINI_API_KEY found. Skipping.")
            return None

        print(f"   [Self-Analysis] Running via Gemini 2.5 Pro (Google Search grounding)...")
        start = time.time()

        # Enforce 60s timeout — self-analysis is a lightweight diagnostic task
        def _call_self_analysis():
            return client.models.generate_content(
                model="gemini-2.5-pro",
                contents=prompt,
                config={
                    'tools': [{'google_search': {}}],
                    'temperature': 0.0,
                    'safety_settings': [
                        {'category': 'HARM_CATEGORY_HARASSMENT', 'threshold': 'BLOCK_NONE'},
                        {'category': 'HARM_CATEGORY_HATE_SPEECH', 'threshold': 'BLOCK_NONE'},
                        {'category': 'HARM_CATEGORY_SEXUALLY_EXPLICIT', 'threshold': 'BLOCK_NONE'},
                        {'category': 'HARM_CATEGORY_DANGEROUS_CONTENT', 'threshold': 'BLOCK_NONE'}
                    ]
                }
            )

        call_pool = concurrent.futures.ThreadPoolExecutor(max_workers=1)
        future = call_pool.submit(_call_self_analysis)
        try:
            response = future.result(timeout=60)
        except concurrent.futures.TimeoutError:
            call_pool.shutdown(wait=False)
            elapsed = int((time.time() - start) * 1000)
            print(f"   ⏰ [Self-Analysis] Gemini 2.5 Pro timed out after 60s ({elapsed}ms). Skipping.")
            return None
        call_pool.shutdown(wait=False)

        text = response.text if response.text else ""
        json_payload = extract_json_from_text(text)
        elapsed = int((time.time() - start) * 1000)
        if json_payload:
            analysis = json.loads(json_payload, strict=False)
            analysis["company"] = company_name
            analysis["days_between_scans"] = days_since_last
            analysis["diagnostics"] = diagnostics
            analysis["score_delta"] = curr_reality - prev_reality if isinstance(curr_reality, (int, float)) and isinstance(prev_reality, (int, float)) else None

            print(f"   [Self-Analysis] Cause: {analysis.get('primary_cause', 'unknown')} | Reliability: {analysis.get('reliability', '?')} ({elapsed}ms)")
            print(f"   [Self-Analysis] {analysis.get('cause_explanation', '')}")

            # Log actionable suggestions with their types
            suggestions = analysis.get("suggestions", [])
            if suggestions:
                for s in suggestions[:3]:
                    print(f"   [Self-Analysis] Action [{s.get('type', '?')}]: {s.get('detail', '')}")
            else:
                print(f"   [Self-Analysis] No actionable suggestions (diagnostics healthy).")

            # Backward compat: also populate improvement_suggestions for any consumers
            analysis["improvement_suggestions"] = [
                f"[{s.get('type', '?')}] {s.get('detail', '')}" for s in suggestions
            ]

            return analysis
        print(f"   [Self-Analysis] No parseable JSON from Gemini 2.5 Pro ({elapsed}ms)")
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

    api_key = os.environ.get("GEMINI_API_KEY") or os.environ.get("API_KEY")
    # Default to the chosen model, or fallback to the reliable 3-pro
    primary_model = model_id or "gemini-3.1-pro-preview"

    search_name = clean_company_name_for_search(company_name)
    domain_hint = get_clean_domain(company_url) if company_url else f"{search_name.lower().replace(' ', '')}.fi"

    if not api_key:
        print("   [Scanner 2] CRITICAL: No GEMINI_API_KEY found. Using simulation fallback.")
        return { "score": 60, "checklist": [], "links": [], "key_people": [], "is_simulated": True, "confidence_score": 0, "washing_flag": False }

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

    # --- COMPANY SIZE CONTEXT FOR STAKEHOLDER SELECTION ---
    if employees and employees >= 5000:
        _size_block = f"\n    COMPANY SIZE CONTEXT: {company_name} has approximately {employees:,} employees.\n    This is a LARGE organization (5000+). Focus on Tier 1-3 roles. A Manager-level person is NOT a key stakeholder at this scale.\n"
    elif employees and employees >= 200:
        _size_block = f"\n    COMPANY SIZE CONTEXT: {company_name} has approximately {employees:,} employees.\n    This is a MID-SIZE organization (200-5000). Focus on Tier 1-4 roles. Manager-level is acceptable only if no higher-tier leaders are found.\n"
    elif employees:
        _size_block = f"\n    COMPANY SIZE CONTEXT: {company_name} has approximately {employees:,} employees.\n    This is a SMALLER organization (<200). All tiers are acceptable, as managers may be true decision-makers.\n"
    else:
        _size_block = ""

    # --- DEFINING THE PROMPT ONCE ---
    prompt = f"""
    Perform a FORENSIC INVESTIGATION on "{search_name}" (Domain: {domain_hint}).
    CONTEXT: Internal/Marketing text analysis suggests an Intent Score of {intent_score}/100.
    {history_block}
    {evidence_anchors}
    {subsidiary_block}
    {pre_verified_block}
    SEARCH STRATEGY:
    Do NOT read the company's own marketing pages (About Us, Front Page).
    EXCEPTION: Careers pages and Service Portfolio pages ARE allowed — they contain operational
    evidence (real tech stacks, team structures, actual service delivery) not marketing fluff.
    Otherwise, look at evidence from these specific 'JURIES' defined below.

    {"SEARCH BUDGET: You have 10 web searches. The FIRST 3 MUST be used for verification (see RESCAN INSTRUCTIONS above). The remaining 7 are for jury searches below. Combine sub-queries with OR to cover multiple juries per search." if history_block else "SEARCH BUDGET: You have 10 web searches. Use 2 searches per jury for thorough coverage, combining sub-queries with OR operators."}

    CRITICAL: You must execute searches for ALL 5 JURIES. Do not skip any.
    NOTE: Target companies are primarily Finnish. Use BOTH English and Finnish search terms.

    1. THE ARCHITECTURAL JURY (Engineering Reality)
       - Public Code & APIs: `site:github.com "{search_name}" OR site:postman.com "{search_name}"`
       - Engineering Culture: `site:{domain_hint} "engineering blog" OR "tech blog" OR "developers" OR "teknologiablogi" OR "kehittäjät"`
       - Design Systems: `site:{domain_hint} "Design System" OR "UI Kit" OR "suunnittelujärjestelmä"`
       - Job Ads Tech Stack (CRITICAL): `site:linkedin.com/jobs "{search_name}" OR site:duunitori.fi "{search_name}" OR site:indeed.fi "{search_name}"`
         ANALYZE what technologies/tools/methodologies appear in their job postings.
         Job ads reveal the REAL tech stack, not marketing claims.
       - Oikotie Job Ads (Finnish): `site:oikotie.fi "{search_name}" "developer" OR "kehittäjä" OR "architect" OR "arkkitehti" OR "engineer"`
         Oikotie is Finland's second-largest job platform. Check for tech stack, team structure, and methodology mentions.
       - Job Descriptions Detail: `"{search_name}" job description "engineer" OR "developer" OR "kehittäjä" OR "arkkitehti"`

    2. THE STRATEGIC JURY (Business Model, Procurement & Customer Experience)
       - Cloud Sourcing (Finland): `site:hankintailmoitukset.fi "{search_name}" AND ("Cloud" OR "Pilvi" OR "SaaS" OR "pilvipalvelu")`
       - Recurring Revenue Evidence: `site:{domain_hint} filetype:pdf "Recurring Revenue" OR "ARR" OR "Jatkuva laskutus" OR "vuosiliikevaihto"`
       - Modern Tech Investments: `site:crunchbase.com "{search_name}" "Acquisitions" OR "Yrityskaupat"`
       - Leadership Thought Leadership: `site:medium.com "{search_name}" OR site:substack.com "{search_name}"`
       - Finnish Business News: `site:kauppalehti.fi "{search_name}" OR site:talouselama.fi "{search_name}" "strategia" OR "digitalisaatio"`
       - Finnish Company Financials: `site:finder.fi "{search_name}"` — verify revenue trend, profit margin, employee count from official register data.
       - Industry Research (ETLA): `site:etla.fi "{search_name}" OR site:etla.fi "{domain_hint}" "digitalisaatio" OR "tuottavuus" OR "kilpailukyky"`
         ETLA is Finland's research institute for the economy. Their industry reports reveal whether this company's sector is growing, digitalizing, or facing structural challenges.
       - Sitra Innovation Insights: `site:sitra.fi "{search_name}" OR site:sitra.fi "data economy" OR "kiertotalous" OR "circular economy"`
         If the company appears in Sitra case studies, this is a strong innovation signal. Sitra is Finland's national innovation fund.
       - Customer Experience (INDEPENDENT sources only): `site:trustpilot.com "{search_name}" OR site:trustpilot.com "{domain_hint}"`
       - CX Metrics & Recognition: `"{search_name}" "NPS" OR "Net Promoter" OR "customer satisfaction" OR "asiakastyytyväisyys" OR EPSI OR CXPA`
       - App/Service Reviews: `site:apps.apple.com "{search_name}" OR site:play.google.com "{search_name}"`
         IMPORTANT: For CX evidence, ONLY accept metrics from INDEPENDENT sources (Trustpilot,
         EPSI, app store ratings, analyst reports). Company-published NPS/CSAT scores on their
         own website are INTENT, not reality — do NOT count them as verified CX evidence.

    3. THE CULTURAL JURY (Talent & Psychological Safety)
       - Boomerang Employees (High Trust): `site:linkedin.com "{search_name}" AND ("returned to" OR "rejoined" OR "palasi takaisin")`
       - Inclusion Artifacts: `site:mimmitkoodaa.fi "{search_name}" OR "Women in Tech Finland" "{search_name}"`
       - Community Hosting: `site:meetup.com "{search_name}" AND ("Host" OR "Sponsor" OR "kumppani")`
       - Employee Voice: `site:glassdoor.com "{search_name}" OR site:teamblind.com "{search_name}"` (NO REDDIT)
       - Finnish Employee Discussion: `"{search_name}" "työntekijäkokemus" OR "työpaikka-arvio" OR "työkulttuuri"`
       - Great Place to Work: `site:greatplacetowork.fi "{search_name}"`
         Check if the company appears on Finland's Best Workplaces list. Presence means independently verified
         high employee trust (survey-based methodology covering credibility, respect, fairness, pride, camaraderie).
         Note the year and size category if found. This is a HIGH-TRUST culture signal.
       - CSRD Sustainability Report (Social Metrics): `site:{domain_hint} "sustainability report" OR "vastuullisuusraportti" OR "CSRD" OR "ESRS"`
         Large Finnish companies (250+ employees) must now publish ESRS-compliant reports. Look for S1 (own workforce)
         metrics: employee turnover, training hours, diversity statistics, work-life balance policies, collective
         bargaining coverage. These are AUDITED numbers — more reliable than marketing claims about culture.
       - Careers Page Culture Signals (CRITICAL): `site:{domain_hint} "careers" OR "ura" OR "avoimet työpaikat" OR "open positions"`
         ANALYZE the careers page for: team structure, benefits, work models (remote/hybrid),
         values in action, hiring process transparency. These reveal real culture, not marketing.

    4. THE AI & DATA JURY (Future Readiness)
       - Real AI Models: `site:huggingface.co "{search_name}"`
       - Tech Stack Verification: `site:stackshare.io "{search_name}"`
       - Cloud Case Studies: `site:aws.amazon.com "Case Studies" "{search_name}" OR site:customers.microsoft.com "{search_name}"`
       - Deep Academic Audits: `site:theseus.fi "{search_name}" OR site:research.aalto.fi "{search_name}"`
       - Finnish Tech Research: `site:vtt.fi "{search_name}" OR site:tuni.fi "{search_name}" OR site:aalto.fi "{search_name}"`
       - Patent & R&D Activity: `site:espacenet.com "{search_name}" OR "worldwide.espacenet.com" "{search_name}"`
         Patent filings are CONCRETE evidence of R&D investment — harder to fake than marketing claims.
         Look for: number of recent patents (last 3 years), technology focus areas (AI, ML, data processing,
         IoT). Even 1-2 patents in relevant tech domains suggest genuine technical capability.
       - Sitra AI & Data Economy: `site:sitra.fi "{search_name}" "tekoäly" OR "artificial intelligence" OR "datatalous" OR "data economy"`
         Sitra is Finland's national innovation fund. If the company appears in their AI/data research, this is
         a strong signal of actual AI/data maturity at national level.

    5. THE SERVICE PORTFOLIO JURY (Delivery Reality — NEW)
       - Service Pages: `site:{domain_hint} "services" OR "palvelut" OR "solutions" OR "ratkaisut"`
         ANALYZE what they actually DELIVER vs. what they claim. Look for specifics:
         platform names, technology mentions, methodology references, case studies.
       - Client References: `site:{domain_hint} "case study" OR "reference" OR "asiakastarina" OR "referenssi"`
       - External Reviews: `"{search_name}" review OR "arvostelu" service quality OR "palvelun laatu"`
       - Analyst Reports: `"{search_name}" Gartner OR Forrester OR ISG OR "Magic Quadrant"`
       - Public Procurement Track Record: `site:openprocurement.fi "{search_name}" OR site:tutkihankintoja.fi "{search_name}"`
         Finnish public procurement data shows VERIFIED delivery — the company won competitive tenders and
         delivered services to government. Look for: number of contracts, total value, which agencies they serve,
         service categories (IT, consulting, infrastructure), and whether activity is recent. This is the STRONGEST
         possible evidence of real service delivery capability.

    IDENTIFY KEY MODERNIZATION STAKEHOLDERS (include in JSON output):
    Search for real, VERIFIED names of current executives/leaders relevant to modernization.
    TARGET: Find 3-6 stakeholders. Search broadly across leadership levels to reach at least 3.

    SENIORITY TIERS — prioritize higher tiers first:
    Tier 1 (C-Suite):       CEO, COO, Managing Director, Country Manager
    Tier 2 (CxO Functional): CTO, CIO, CDO, CPO (Product or People), CFO
    Tier 3 (VP/SVP):        VP Engineering, VP Product, VP Strategy, SVP Operations
    Tier 4 (Director):      Director of Technology, HR Director, Director of Strategy
    Tier 5 (Head of):       Head of Engineering, Head of Product, Head of Digital
    Tier 6 (Manager):       Senior Manager, Program Manager (ONLY for companies with < 200 employees)
{_size_block}
    STAKEHOLDER QUALITY RULES (CRITICAL):
    1. FULL NAMES ONLY: Every name MUST include both first name AND last name. Do NOT include
       a person if you only found their first name. "Pirja" alone is NOT acceptable — you need
       "Pirja Heiskanen" or similar. If you cannot find the full name, DO NOT include that person.
    2. VERIFIED CONNECTION: Only include people you found through Google Search results that
       explicitly connect them to {company_name}. Do NOT guess or infer people from partial mentions.
    3. TARGET 3-6 STAKEHOLDERS: Search actively to find at least 3 verified stakeholders.
       Search for CEO/MD first, then CTO/CIO, then other CxOs, then VPs, then Directors.
       If you have fewer than 3 after the first pass, do an additional search specifically
       for leadership names. Maximum 6. HOWEVER: if you truly cannot verify 3 names through
       search results, return only what you can verify. NEVER fabricate or guess names.
    4. RELEVANCE TO KEY CHALLENGES: Only include people in leadership/decision-making roles
       that are directly linked to the defined and detected key challenges (e.g., if "Legacy Inertia"
       is detected, a CTO or VP Engineering is relevant; if "Silos" is detected, a Head of Strategy
       or CDO is relevant). Do NOT include random employees, junior staff, or people in unrelated
       roles (e.g. sales representatives, marketing coordinators, office managers).
    5. CURRENT EMPLOYEES: Only include people who currently work at the company based on
       search results. If search results suggest they left, do NOT include them.
    6. SENIORITY PRIORITY: Always list the MOST SENIOR person first. If you find a CEO and
       a Manager, the CEO must come first. Fill slots from Tier 1 downward.

    VERIFY THESE 5 HYPOTHESES:
    1. "Surveillance": Micromanagement/tracking?
    2. "Silos": Bureaucracy complaints?
    3. "Fake Agile": "Waterfall" or "Zombie Scrum"?
    4. "Platform Thinking": Public API/Design System found?
    5. "Legacy Inertia": Failed transformations or "Cloud Washing"?

    SCORING LOGIC & TEMPORAL WEIGHTING (CRITICAL):
    1. BASELINE: Start at 0 adjustment.
    2. RECENCY WEIGHTING:
       - Evidence < 12 months old: 100% Impact (Heavy Weight).
       - Evidence > 18 months old: 20% Impact (Ignore unless recurring/unresolved).
       - Favor FRESH evidence over legacy complaints.
    3. TECHNICAL DEBT PENALTY:
       - If RECENT (< 12 months) evidence confirms "Technical Debt", "Legacy Systems", "Manual Workarounds", or "Excel Hell":
       - APPLY A SIGNIFICANT PENALTY (-20 to -35 points) to the adjustment.
       - This is a strong signal of low reality.
       - SEVERE: If near-zero evidence across all juries (company is invisible): -35 to -50 points.
    4. POSITIVE SIGNALS (USE THE FULL RANGE — do NOT be conservative):
       - GOOD: Verified evidence in 2-3 juries, some recent: +15 to +25 points.
       - STRONG: Verified evidence in 3-4 juries, mostly recent, real tech stack visible: +25 to +35 points.
       - OUTSTANDING: 4-5 juries confirm strong, recent, verifiable evidence (public repos,
         design systems, real case studies, active hiring for modern roles): +35 to +45 points.
       - You MUST use high adjustments (+30 to +45) for companies with genuinely strong evidence.
         A top Finnish tech company with public GitHub repos, active hiring, and verified case
         studies across multiple juries deserves +35 or higher. Do NOT cap at +20.

    DETECT "WASHING" (Logic Check):
    - Intent Score is {intent_score}. 
    - RULE: "Washing" (Green/Agile/AI Washing) is ONLY possible if Intent Score is HIGH (>60) AND Reality is LOW.
    - IF Intent Score is LOW (<50), you CANNOT flag this as Washing.
    - AI WASHING: Claims of "AI-driven" but no technical evidence (Hugging Face/StackShare) found.
    - AGILE WASHING: Claims of "Tribes/Squads" but evidence of "Waterfall", "Approvals", "Bureaucracy".

    RETURN STRICT JSON:
    {{
        "key_people": [
            {{
                "name": "Full Name",
                "role": "Exact Title",
                "relevance": "Why relevant?"
            }}
        ],
        "findings": [
            {{
                "category": "Operating Model",
                "label": "Brief Label",
                "found": boolean,
                "sentiment": "negative" or "positive",
                "snippet": "Quote...",
                "title": "Source Title",
                "url": "URL",
                "is_recent": boolean (True if < 12 months)
            }}
        ],
        "reality_score_adjustment": (integer -50 to +50),
        "confidence_score": (integer 0 to 100),
        "washing_flag": boolean (True if significant contradiction/washing found AND Intent > 60),
        "washing_type": "AI Washing" or "Agile Washing" or "Green Washing" or null
    }}
    
    GUIDE FOR CONFIDENCE SCORE:
    - 0-40: Very few sources found, evidence is generic or very old.
    - 41-70: Some specific evidence found, but mixed recency or limited sources.
    - 71-100: Multiple independent sources (Juries) confirm findings, evidence is recent (<12mo) and specific.
    """

    def execute_with_model(target_model_name, custom_prompt=None, call_timeout=150):
        """Execute a search with the given model. Uses main prompt by default, or a custom one for re-search."""
        # Elapsed time guard — skip if function has been running too long
        # Derived from time_budget to leave buffer for URL validation + response
        elapsed_total = time.time() - _fn_start
        if elapsed_total > _guard_forensic:
            print(f"   ⏰ [Forensic Scan] Skipping {target_model_name} — elapsed {int(elapsed_total)}s exceeds {_guard_forensic}s guard")
            return None

        is_rescan = bool(previous_context and previous_context.get("previous_reality") is not None)
        active_prompt = custom_prompt if custom_prompt else prompt
        # Prepend search budget instruction for Gemini models (no native max_uses support)
        search_budget_prefix = (
            "IMPORTANT CONSTRAINTS FOR THIS SEARCH:\n"
            "1. SEARCH LIMIT: Complete your analysis in MAX 6 web searches. After 6 searches, "
            "STOP searching and synthesize what you have. Do NOT repeat similar queries.\n"
            "2. URL QUALITY: ONLY include URLs that appear in actual Google Search results. "
            "Do NOT construct URLs from memory or guess URL patterns. If you did not find a "
            "URL in a search result, do NOT include it. Three real URLs are worth more than "
            "ten guessed ones.\n"
            "3. EFFICIENCY: Combine related queries with OR operators into single searches. "
            "For example: `\"CompanyName\" technology strategy OR digital transformation OR \"teknologia\"`\n"
            "4. STOP CONDITION: If after 4 searches you have found 3+ verified findings, "
            "write your JSON response. Do NOT keep searching for marginal improvements.\n\n"
        )
        active_prompt = search_budget_prefix + active_prompt

        # For Gemini 3.1 Pro: two-pass mode.
        # Pass 1 (this call): relax JSON requirement so the model focuses entirely on
        # search quality and evidence gathering — no formatting overhead.
        # Pass 2: _repair_json_with_sonnet(is_structuring=True) converts the free-form
        # output to JSON, identical to the designed Sonnet path (no confidence penalty).
        # Removing JSON-formatting pressure saves ~30-40s, fitting safely in 180s.
        is_two_pass = "3.1" in target_model_name
        if is_two_pass:
            active_prompt = re.sub(
                r'RETURN STRICT JSON[^:]*:',
                'REPORT YOUR FINDINGS (any clear structured format — JSON, markdown, or text):',
                active_prompt
            )

        pass_label = "Pass 1 (Search)" if is_two_pass else "Search+Structure"
        print(f"   🔄 [Forensic Scan] Attempting {target_model_name} [{pass_label}] for {company_name}...")
        start = time.time()
        try:
            client = get_gemini_client()

            # Enforce per-call timeout via thread pool.
            # Google GenAI SDK's built-in HttpOptions timeout is unreliable
            # (SDK passes timeout=None internally, overriding client-level timeout).
            # Gemini 3.x models need temp=1.0 + thinking_config (low temp causes looping)
            _safety = [
                {'category': 'HARM_CATEGORY_HARASSMENT', 'threshold': 'BLOCK_NONE'},
                {'category': 'HARM_CATEGORY_HATE_SPEECH', 'threshold': 'BLOCK_NONE'},
                {'category': 'HARM_CATEGORY_SEXUALLY_EXPLICIT', 'threshold': 'BLOCK_NONE'},
                {'category': 'HARM_CATEGORY_DANGEROUS_CONTENT', 'threshold': 'BLOCK_NONE'}
            ]
            if "gemini-3" in target_model_name:
                # Gemini 3.1 Pro Preview gets a capped thinking budget.
                # Use thinking_budget ONLY — the Gemini API rejects requests that set
                # both thinking_level and thinking_budget simultaneously
                # ("You can only set only one of thinking budget and thinking level").
                # A numeric budget implicitly enables thinking at the right intensity
                # and caps token consumption so the search call fits within the 180s wall.
                _temperature = 0.5 if "3.1" in target_model_name else 1.0
                # Rescan: 2000 tokens — model already has prior findings, needs less planning time.
                # Fresh scan: 3000 tokens — capped at MEDIUM equivalent (2000-3500 range); saves
                #   ~35-45s vs the previous 8000 budget without degrading result quality.
                _thinking_budget = (2000 if is_rescan else 3000) if "3.1" in target_model_name else 4000
                _model_config = {
                    'tools': [{'google_search': {}}],
                    'temperature': _temperature,
                    'thinking_config': types.ThinkingConfig(
                        thinking_budget=_thinking_budget,
                    ),
                    'safety_settings': _safety,
                    'max_output_tokens': 8000,
                }
            else:
                # gemini-2.5-pro: cap default thinking to prevent token starvation.
                # Without explicit thinking_config, 2.5-pro defaults to uncapped thinking
                # mode — thinking tokens share the output pool and can exhaust it on complex
                # prompts, leaving too few tokens for the actual JSON.
                # Budget: 2048 tokens balances quality (enough reasoning for a full forensic
                # scan) vs speed (~40-50s). Previous 1024 was too tight when 2.5-pro is the
                # primary scanner (fallback from 3.1), producing shallow findings.
                # reality_check.py can stay at 1024 — its task is simpler (score adjustment).
                _model_config = {
                    'tools': [{'google_search': {}}],
                    'temperature': 0.0,
                    'thinking_config': types.ThinkingConfig(thinking_budget=2048),
                    'max_output_tokens': 8000,
                    'safety_settings': _safety,
                }

            # 3.1 Pro uses streaming so that partial output can be salvaged on timeout.
            # Other Gemini models use blocking — they're faster and rarely time out.
            partial_result = []  # accumulates streamed chunks; read on soft timeout
            if is_two_pass:
                def _call_gemini():
                    for chunk in client.models.generate_content_stream(
                        model=target_model_name,
                        contents=active_prompt,
                        config=_model_config,
                    ):
                        if chunk.text:
                            partial_result.append(chunk.text)
                    return "".join(partial_result)
            else:
                def _call_gemini():
                    resp = client.models.generate_content(
                        model=target_model_name,
                        contents=active_prompt,
                        config=_model_config,
                    )
                    if not resp.text:
                        # Raise rather than returning "" so the exception handler logs
                        # the real cause (e.g. finish_reason=SAFETY/MAX_TOKENS/OTHER)
                        # instead of the generic "no parseable JSON" message.
                        finish = (resp.candidates[0].finish_reason
                                  if resp.candidates else "NO_CANDIDATES")
                        raise ValueError(f"empty response (finish_reason={finish})")
                    return resp.text

            call_pool = concurrent.futures.ThreadPoolExecutor(max_workers=1)
            future = call_pool.submit(_call_gemini)
            text_content = ""
            try:
                text_content = future.result(timeout=call_timeout) or ""
            except concurrent.futures.TimeoutError:
                call_pool.shutdown(wait=False)
                elapsed = int((time.time() - start) * 1000)
                salvaged = "".join(partial_result)
                if is_two_pass and len(salvaged) > 200:
                    text_content = salvaged
                    print(f"   ⏰ [Forensic Scan] {target_model_name} soft-timed out after {call_timeout}s ({elapsed}ms). Salvaging {len(salvaged)} chars for Pass 2...")
                else:
                    print(f"   ⏰ [Forensic Scan] {target_model_name} timed out after {call_timeout}s ({elapsed}ms). Skipping to next model.")
                    return None
            call_pool.shutdown(wait=False)

            json_payload = extract_json_from_text(text_content)

            elapsed = int((time.time() - start) * 1000)
            if json_payload:
                try:
                    parsed = json.loads(json_payload, strict=False)
                    print(f"   ✅ [Forensic Scan] {target_model_name} succeeded in {elapsed}ms")
                    return parsed
                except json.JSONDecodeError as je:
                    print(f"   ⚠️ [Forensic Scan] {target_model_name} JSON parse failed: {je}")
                    # Fall through to Sonnet repair gate

            # --- STRUCTURING GATE ---
            # Two-pass mode threshold is lower (20 chars) because Pass 1 output
            # is free-form by design — even short search summaries can be structured.
            # Single-pass threshold stays at 100 chars (unexpected unstructured output).
            structuring_threshold = 20 if is_two_pass else 100
            if text_content and len(text_content) > structuring_threshold:
                # Two-pass mode (3.1 Pro): free-form output is the designed flow.
                #   Structuring fallback chain: 3.1 Pro → Sonnet 4.6 → 2.5 Pro.
                #   is_structuring=True for Sonnet (no confidence penalty).
                # Other models: unexpected unstructured output → Sonnet repair only
                #   (caps confidence at 70 to signal reduced reliability).
                gate_label = "Pass 2 (Structure)" if is_two_pass else "JSON Repair"
                print(f"   🔄 [{gate_label}] {target_model_name} returned {len(text_content)} chars ({elapsed}ms). Structuring into JSON...")
                repaired = None
                if is_two_pass:
                    repaired = _structure_with_gemini(text_content, company_name, "gemini-3.1-pro-preview")
                    if not repaired:
                        print(f"   ⚠️ [Pass 2] Gemini 3.1 Pro structuring failed. Trying Sonnet 4.6...")
                        repaired = _repair_json_with_sonnet(text_content, company_name, is_structuring=True)
                    if not repaired:
                        print(f"   ⚠️ [Pass 2] Sonnet 4.6 structuring failed. Trying Gemini 2.5 Pro...")
                        repaired = _structure_with_gemini(text_content, company_name, "gemini-2.5-pro")
                else:
                    repaired = _repair_json_with_sonnet(text_content, company_name, is_structuring=False)
                    if not repaired:
                        print(f"   ⚠️ [JSON Repair] Sonnet unavailable. Trying Gemini 2.5 Pro...")
                        repaired = _structure_with_gemini(text_content, company_name, "gemini-2.5-pro")
                if repaired:
                    repair_elapsed = int((time.time() - start) * 1000)
                    print(f"   ✅ [{gate_label}] Structured data from {target_model_name} ({repair_elapsed}ms total)")
                    return repaired
                else:
                    print(f"   ⚠️ [{gate_label}] Could not structure output from {target_model_name}")
                    return None
            else:
                text_len = len(text_content) if text_content else 0
                preview = repr(text_content[:120]) if text_content else "(empty)"
                print(f"   ⚠️ [Forensic Scan] {target_model_name} returned no parseable JSON ({elapsed}ms, {text_len} chars raw)")
                print(f"   📋 [Forensic Scan] Response preview: {preview}")
                return None
        except Exception as e:
            elapsed = int((time.time() - start) * 1000)
            print(f"   ❌ [Forensic Scan] {target_model_name} failed in {elapsed}ms: {e}")
            return None

    def execute_with_anthropic(active_prompt):
        """
        PRIMARY: Two-pass pipeline using Sonnet 4.6 + Brave Web Search.

        Pass 1 (Search): Sonnet searches the web and reports findings freely.
                         Format requirements are relaxed so the model can focus
                         on evidence quality rather than JSON syntax.
        Pass 2 (Structure): A fast Sonnet call converts the free-form analysis
                           into the expected JSON schema. Focused on one task,
                           this rarely fails.

        Fast path: If Pass 1 happens to return valid JSON, Pass 2 is skipped.
        """
        # Elapsed time guard — leave buffer for URL validation + response
        elapsed_total = time.time() - _fn_start
        if elapsed_total > _guard_anthropic:
            print(f"   ⏰ [Forensic Scan] Skipping Sonnet + Web Search — elapsed {int(elapsed_total)}s exceeds {_guard_anthropic}s guard")
            return None

        anthropic_key = os.environ.get("ANTHROPIC_API_KEY", "")
        if "=" in anthropic_key:
            try: anthropic_key = anthropic_key.split("=", 1)[1]
            except: pass
        anthropic_key = anthropic_key.strip().strip('"').strip("'")
        if not anthropic_key:
            print("   [Scanner 2] No ANTHROPIC_API_KEY for Sonnet + Web Search.")
            return None
        try:
            print(f"   🔄 [Forensic Scan] Attempting claude-sonnet-4-6 + Web Search (two-pass) for {company_name}...")
            start = time.time()
            claude_client = anthropic.Anthropic(api_key=anthropic_key)

            # Rescan: 8 searches (3 verification + 5 jury) — reduced from 10 to lower pause_turn risk.
            # First scan: 10 searches (2 per jury) — full coverage needed without prior context.
            is_rescan = bool(previous_context and previous_context.get("previous_reality") is not None)
            search_budget = 8 if is_rescan else 10
            if is_rescan:
                print(f"   📊 [Forensic Scan] RESCAN: search budget = {search_budget} (3 verification + 5 jury)")
            else:
                print(f"   📊 [Forensic Scan] FIRST SCAN: search budget = {search_budget} (2 per jury)")

            # --- PASS 1: Web Search (format-relaxed) ---
            # Replace strict JSON requirement with flexible format instruction.
            # The model still sees the data schema as a reference, but is free to
            # report in any clear structure — accuracy over format.
            sonnet_prompt = re.sub(
                r'RETURN STRICT JSON[^:]*:',
                'REPORT YOUR FINDINGS (any clear structured format — JSON, markdown, or text):',
                active_prompt
            )
            # Prepend wrap-up guard: tell Sonnet to stop searching 2 searches before the hard
            # API-level max_uses limit, preventing pause_turn from cutting off mid-analysis.
            _wrap_budget = search_budget - 2
            sonnet_prompt = (
                f"MANDATORY STOP RULE: After your {_wrap_budget}th web search, STOP SEARCHING "
                f"immediately and write your final output — even if some juries are incomplete. "
                f"Incomplete coverage is acceptable. An incomplete response that cuts off is not.\n\n"
            ) + sonnet_prompt

            # Enforce 240s per-call timeout via thread pool (Sonnet + web search + thinking typically 60-150s)
            # Extended Thinking: Sonnet reasons through evidence before scoring,
            # helping it weigh conflicting signals more carefully.
            def _call_sonnet_web():
                return claude_client.messages.create(
                    model="claude-sonnet-4-6",
                    max_tokens=16000,
                    thinking={
                        "type": "enabled",
                        "budget_tokens": 5000 if is_rescan else 8000
                    },
                    tools=[{
                        "type": "web_search_20250305",
                        "name": "web_search",
                        "max_uses": search_budget
                    }],
                    messages=[{"role": "user", "content": sonnet_prompt}]
                )

            call_pool = concurrent.futures.ThreadPoolExecutor(max_workers=1)
            future = call_pool.submit(_call_sonnet_web)
            try:
                response = future.result(timeout=240)
            except concurrent.futures.TimeoutError:
                call_pool.shutdown(wait=False)
                elapsed = int((time.time() - start) * 1000)
                print(f"   ⏰ [Forensic Scan] claude-sonnet-4-6 + Web Search timed out after 240s ({elapsed}ms). Skipping to next model.")
                return None
            call_pool.shutdown(wait=False)

            pass1_elapsed = int((time.time() - start) * 1000)
            print(f"   [Forensic Scan] Sonnet stop_reason: {response.stop_reason} | content_blocks: {len(response.content)} ({pass1_elapsed}ms)")

            # Note: pause_turn can occur when the model hasn't finished all web searches,
            # but continuing the conversation requires complex message threading with
            # tool_result blocks in the correct roles. We extract whatever is available.
            if response.stop_reason == "pause_turn":
                print(f"   ⚠️ [Forensic Scan] Sonnet pause_turn — search budget exhausted mid-analysis. Extracting partial results.")
            elif response.stop_reason == "max_tokens":
                print(f"   ⚠️ [Forensic Scan] Sonnet max_tokens — response truncated. Attempting extraction from partial output.")

            # Extract text AND web search citations from response content blocks
            text_parts = []
            web_citations = []  # URLs from web search results & inline citations
            for block in response.content:
                if hasattr(block, 'text'):
                    text_parts.append(block.text)
                    # Harvest inline citations from text blocks
                    if hasattr(block, 'citations') and block.citations:
                        for cite in block.citations:
                            if hasattr(cite, 'url') and cite.url:
                                web_citations.append({
                                    "url": cite.url,
                                    "title": getattr(cite, 'title', ''),
                                })
                # Harvest URLs from raw web search result blocks
                if getattr(block, 'type', None) == 'web_search_tool_result':
                    content = getattr(block, 'content', None)
                    if isinstance(content, list):
                        for sr in content:
                            if getattr(sr, 'type', None) == 'web_search_result' and hasattr(sr, 'url'):
                                web_citations.append({
                                    "url": sr.url,
                                    "title": getattr(sr, 'title', ''),
                                })
            text_content = "\n".join(text_parts)

            # Deduplicate citations by URL
            seen_urls = set()
            unique_citations = []
            for c in web_citations:
                if c["url"] not in seen_urls:
                    seen_urls.add(c["url"])
                    unique_citations.append(c)
            if unique_citations:
                print(f"   🔗 [Web Search] Harvested {len(unique_citations)} citation URLs from Sonnet web search")

            # --- FAST PATH: If Pass 1 returned valid JSON, skip Pass 2 ---
            json_payload = extract_json_from_text(text_content)
            if json_payload:
                try:
                    result = json.loads(json_payload, strict=False)
                    # Enrich findings with web search citations:
                    # 1. Fill in missing URLs on findings that have no url
                    # 2. Add remaining citations as supplementary evidence
                    if unique_citations and "findings" in result:
                        citation_pool = list(unique_citations)
                        for finding in result["findings"]:
                            if finding.get("found") and not finding.get("url") and citation_pool:
                                donor = citation_pool.pop(0)
                                finding["url"] = donor["url"]
                                if not finding.get("title"):
                                    finding["title"] = donor["title"]
                    result["_web_grounded"] = True
                    result["_citation_count"] = len(unique_citations)
                    elapsed = int((time.time() - start) * 1000)
                    print(f"   ✅ [Forensic Scan] Pass 1 returned valid JSON — skipping Pass 2 ({elapsed}ms, {len(unique_citations)} citations)")
                    return result
                except json.JSONDecodeError as je:
                    print(f"   ⚠️ [Forensic Scan] Pass 1 JSON parse failed: {je} — proceeding to Pass 2")

            # --- PASS 2: Structure free-form response into JSON ---
            if text_content and len(text_content) > 100:
                print(f"   🔄 [Two-Pass] Pass 1 complete ({pass1_elapsed}ms, {len(text_content)} chars). Structuring into JSON (Pass 2)...")
                structured = _repair_json_with_sonnet(
                    text_content, company_name,
                    is_structuring=True, citations=unique_citations
                )
                if structured:
                    # Enrich with citation metadata
                    if unique_citations and "findings" in structured:
                        citation_pool = list(unique_citations)
                        for finding in structured["findings"]:
                            if finding.get("found") and not finding.get("url") and citation_pool:
                                donor = citation_pool.pop(0)
                                finding["url"] = donor["url"]
                                if not finding.get("title"):
                                    finding["title"] = donor["title"]
                    structured["_web_grounded"] = True
                    structured["_citation_count"] = len(unique_citations)
                    total_elapsed = int((time.time() - start) * 1000)
                    print(f"   ✅ [Two-Pass] Pass 2 structured successfully ({total_elapsed}ms total, {len(unique_citations)} citations)")
                    return structured
                else:
                    print(f"   ⚠️ [Two-Pass] Pass 2 structuring failed — could not convert to JSON")
                    return None
            else:
                elapsed = int((time.time() - start) * 1000)
                print(f"   ⚠️ [Forensic Scan] claude-sonnet-4-6 + Web Search returned no usable text ({elapsed}ms)")
                return None
        except Exception as e:
            elapsed = int((time.time() - start) * 1000)
            print(f"   ❌ [Forensic Scan] claude-sonnet-4-6 + Web Search failed in {elapsed}ms: {e}")
            return None

    # --- SEQUENTIAL: Gemini 3.1 Pro Preview → Gemini 2.5 Pro ---
    # Gemini 3.1 Pro Preview is PRIMARY: native Google Search, temp=0.5, thinking_budget=3000.
    # Gemini 2.5 Pro is BACKUP: separate rate limits from 3.x family (~40-50s, reliable).
    data = None

    print(f"   🔍 [Forensic Scan] Starting sequential scan for {company_name}: Gemini 3.1 Pro Preview → Gemini 2.5 Pro")

    # --- PRIMARY: Gemini 3.1 Pro Preview — two-pass mode ---
    if not data:
        data = execute_with_model("gemini-3.1-pro-preview", None, 180)

    # --- BACKUP: Gemini 2.5 Pro (separate rate limits from 3.x family; ~40-50s typical) ---
    if not data:
        print(f"   ⚠️ [Forensic Scan] Gemini 3.1 Pro Preview returned no data. Falling back to Gemini 2.5 Pro...")
        data = execute_with_model("gemini-2.5-pro", call_timeout=120)

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

    # --- CONFIDENCE-DRIVEN RE-SEARCH LOOP ---
    # Only triggers when initial results have low confidence AND weak coverage.
    # Keeps all original findings, adds new ones on top.
    if data and max_research_loops > 0:
        initial_findings = data.get("findings", [])
        initial_confidence = data.get("confidence_score", 50)
        initial_evidence_count = len([f for f in initial_findings if f.get("found") and f.get("url")])

        # CHECK: Should we re-search?
        # Trigger conditions:
        #   1. confidence < 45 AND fewer than 3 real evidence links, OR
        #   2. fewer than 2 evidence links regardless of confidence (e.g. JSON-repaired
        #      Gemini 2.5 Pro responses that claim high confidence but provide no sources)
        if (initial_confidence < 45 and initial_evidence_count < 3) or initial_evidence_count < 2:
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

                # Re-search uses Gemini models (targeted follow-up with Google Search grounding)
                research_data = execute_with_model("gemini-3.1-pro-preview", custom_prompt=research_prompt)
                if not research_data:
                    research_data = execute_with_model("gemini-2.5-pro", custom_prompt=research_prompt)

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
                    "url": f.get("url", ""),
                    "source": get_clean_domain(f.get("url", "")),
                    "is_recent": f.get("is_recent", False),
                    "source_type": "current"
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

        # --- DELTA VERIFICATION RE-SCAN ---
        # If score changed by more than ±25 from previous, re-run the scan once.
        # ±25 is the permanent threshold — matches Opus Final Judge design.
        # If re-scan confirms the change → accept as fact (genuine change).
        # If re-scan contradicts → use the more conservative (closer to previous) result.
        if previous_context and previous_context.get("previous_reality") is not None:
            prev_reality = previous_context["previous_reality"]
            delta = final_score - prev_reality

            if abs(delta) >= 25:
                # Elapsed time guard — skip verification if running out of time.
                # Delta re-scan + URL validation can fit in remaining time.
                elapsed_total = time.time() - _fn_start
                if elapsed_total > _guard_delta_verify:
                    print(f"   ⏰ [Delta Verification] Skipping — elapsed {int(elapsed_total)}s exceeds {_guard_delta_verify}s guard. Keeping score {final_score}.")
                else:
                    print(f"   [Delta Verification] Large score change detected: {prev_reality} → {final_score} (delta {delta}). Running verification re-scan (same methodology)...")
                    if batch_mode:
                        verify_data = execute_with_model(primary_model)
                    else:
                        # Use the SAME model (Sonnet) for verification.
                        # Old scores were often from Gemini — verifying with Gemini
                        # just reproduces the old bias. Same-model consistency is
                        # the correct test: if Sonnet says 85 twice, the delta is real.
                        verify_data = execute_with_anthropic(prompt)
                        if not verify_data:
                            # Sonnet unavailable — fall back to Gemini as last resort
                            verify_data = execute_with_model(primary_model)

                    if verify_data:
                        verify_adjustment = verify_data.get("reality_score_adjustment", 0)
                        verify_score = max(0, min(100, base_score + verify_adjustment))
                        verify_delta = verify_score - prev_reality

                        if abs(verify_delta) >= 25 and (delta > 0) == (verify_delta > 0):
                            # Both scans agree on direction and magnitude — accept as genuine change
                            # Use average of both for stability
                            final_score = int((final_score + verify_score) / 2)
                            print(f"   [Delta Verification] CONFIRMED: Re-scan also shows large delta ({verify_delta}). Accepted as genuine change. Final: {final_score}")
                        else:
                            # Re-scan disagrees — use the score closer to previous (more conservative)
                            if abs(verify_score - prev_reality) < abs(final_score - prev_reality):
                                final_score = verify_score
                            print(f"   [Delta Verification] NOT CONFIRMED: Re-scan score {verify_score} (delta {verify_delta}). Using conservative: {final_score}")
                    else:
                        print(f"   [Delta Verification] Re-scan failed. Keeping original score {final_score}.")

        # Determine Washing based on AI flag OR raw gap if flag is missing
        washing_flag = data.get("washing_flag", False)
        washing_type = data.get("washing_type", None)

        key_people = data.get("key_people", [])

        # --- KEY PEOPLE VALIDATION GATE ---
        # Filter out stakeholders with incomplete or invalid names.
        # This catches cases where the AI only found a first name, used a placeholder,
        # or included people with no real relevance to modernization.
        key_people = _validate_key_people(key_people, company_name, employees=employees)

        # --- URL VALIDATION GATE ---
        # Verify all evidence URLs actually exist before returning results.
        # Marks each link/checklist entry with "verified": true/false.
        # Penalizes confidence when many URLs are dead (hallucinated evidence).
        raw_findings, checklist, url_verified, url_dead = _validate_evidence_urls(raw_findings, checklist, company_name=company_name)

        # --- SOURCE ADMISSIBILITY GATE (Phase 1) ---
        # URL liveness is not enough: wrong-entity and dead-link evidence must not
        # feed factual generation. Narrow/foresight sources remain admissible, but
        # are annotated so downstream prompts/validators can restrict their use.
        company_domain = get_clean_domain(company_url) if company_url else ""
        raw_findings = annotate_evidence_links(raw_findings, company_domain)
        checklist = annotate_evidence_links(checklist, company_domain)
        raw_findings, excluded_evidence = filter_admissible_sources(raw_findings, company_domain)
        checklist, excluded_checklist = filter_admissible_sources(checklist, company_domain)
        excluded_total = len(excluded_evidence) + len(excluded_checklist)
        if excluded_total:
            reasons = {}
            for _src in excluded_evidence + excluded_checklist:
                reason = _src.get("exclusion_reason") or "excluded"
                reasons[reason] = reasons.get(reason, 0) + 1
            penalty = min(20, excluded_total * 4)
            old_conf = int(confidence)
            confidence = max(10, confidence - penalty)
            print(f"   🧹 [Source Gate] Excluded {excluded_total} sources {reasons} → confidence penalty -{penalty} ({old_conf} → {int(confidence)})")

        # --- URL-TRIGGERED RE-SCAN ---
        # If >20% of URLs are dead, the evidence base is unreliable.
        # Run ONE targeted re-search to try to find real, verifiable evidence.
        url_total = url_verified + url_dead
        dead_ratio = url_dead / url_total if url_total > 0 else 0

        if dead_ratio > 0.20 and url_dead >= 2 and (time.time() - _fn_start) <= 250:
            # Identify categories where evidence URLs failed
            dead_categories = set()
            for item in raw_findings:
                if not item.get("verified", True):
                    dead_categories.add(item.get("source_type", "current"))
            for item in checklist:
                if not item.get("verified", True) and item.get("category"):
                    dead_categories.add(item.get("category"))

            dead_cat_list = list(dead_categories)[:4] if dead_categories else ["General"]
            print(f"   🔄 [URL Re-Scan] {int(dead_ratio*100)}% URLs dead ({url_dead}/{url_total}). Triggering evidence recovery scan...")

            recovery_prompt = f"""You are a forensic evidence researcher. A previous search for "{search_name}" returned {url_dead} URLs that do not exist (hallucinated links).

Your task: Find REAL, VERIFIABLE evidence about this company's modernization and adaptability.
Focus on these areas where evidence was weak: {', '.join(dead_cat_list)}

CRITICAL RULES:
- ONLY include URLs you find through actual Google Search results
- Do NOT invent or guess URLs — every URL must come from a real search result
- Prefer well-known sources: LinkedIn, news sites, industry reports, company press releases
- Quality over quantity — 3 real URLs beats 10 fake ones

RETURN STRICT JSON:
{{
    "findings": [
        {{
            "category": "Operating Model",
            "label": "Brief Label",
            "found": boolean,
            "sentiment": "negative" or "positive",
            "snippet": "Exact quote from the source...",
            "title": "Source Title",
            "url": "REAL URL from search results",
            "is_recent": boolean
        }}
    ],
    "key_people": [],
    "reality_score_adjustment": 0,
    "confidence_score": 50,
    "washing_flag": false,
    "washing_type": null
}}"""

            if batch_mode:
                recovery_data = execute_with_model(primary_model, custom_prompt=recovery_prompt)
            else:
                # Use the SAME model (Sonnet) that did the original scan for recovery.
                # Same-model consistency: if Sonnet's URLs are dead, Sonnet's web search
                # is the best tool to find real replacements (live Brave Search grounding).
                recovery_data = execute_with_anthropic(recovery_prompt)
                if not recovery_data:
                    # Sonnet unavailable — fall back to Gemini as last resort
                    recovery_data = execute_with_model(primary_model, custom_prompt=recovery_prompt)

            if recovery_data:
                new_findings = recovery_data.get("findings", [])
                # Build temporary links from recovery findings
                recovery_links = []
                for f in new_findings:
                    if f.get("found") and f.get("url"):
                        recovery_links.append({
                            "title": f.get("title", "Recovery Evidence"),
                            "snippet": f.get("snippet", ""),
                            "url": f.get("url", ""),
                            "source": get_clean_domain(f.get("url", "")),
                            "is_recent": f.get("is_recent", False),
                            "source_type": "recovery"
                        })

                if recovery_links:
                    # Validate the recovery URLs too
                    recovery_links, _, rec_verified, rec_dead = _validate_evidence_urls(recovery_links, [], company_name=company_name)
                    print(f"   🔗 [URL Re-Scan] Recovery: {rec_verified} verified, {rec_dead} dead out of {len(recovery_links)} new links")

                    # Only keep verified recovery links, dedup against existing URLs
                    existing_urls = {(f.get("url") or "").rstrip("/").lower() for f in raw_findings}
                    added = 0
                    recovery_links = annotate_evidence_links(recovery_links, company_domain)
                    for link in recovery_links:
                        if link.get("verified") and link.get("url") and link.get("admissible") is not False:
                            url_key = link["url"].rstrip("/").lower()
                            if url_key not in existing_urls:
                                raw_findings.append(link)
                                existing_urls.add(url_key)
                                added += 1

                    if added > 0:
                        # Recalculate URL stats after recovery
                        url_verified += added
                        url_total = url_verified + url_dead
                        dead_ratio = url_dead / url_total if url_total > 0 else 0
                        print(f"   ✅ [URL Re-Scan] Added {added} verified links. Evidence pool now {url_verified}/{url_total} verified ({int((1-dead_ratio)*100)}%)")
                    else:
                        print(f"   ⚠️ [URL Re-Scan] No new verified links found in recovery scan")
            else:
                print(f"   ⚠️ [URL Re-Scan] Recovery scan returned no data")

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
        if _elapsed < (time_budget - 40) and url_verified >= 2:
            try:
                from source_claim_validator import validate_source_claims
                scv_result = validate_source_claims(raw_findings, max_checks=8, max_workers=4)
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
        elif _elapsed >= (time_budget - 40):
            print(f"   ⏰ [Source-Claim] Skipped — time budget exhausted ({int(_elapsed)}s/{time_budget}s)")

        # --- SOURCE QUALITY ASSESSMENT ---
        # Evaluate credibility of each source domain (not just URL liveness).
        # Adds 'source_credibility' field to each evidence link.
        # Penalizes confidence when evidence comes from low-quality sources.
        high_q, med_q, low_q, quality_score = assess_evidence_quality(raw_findings, company_domain)
        total_q = high_q + med_q + low_q
        if total_q > 0:
            print(f"   🔍 [Source Quality] {high_q} high, {med_q} medium, {low_q} low credibility sources (quality: {quality_score}/100)")
        confidence = confidence_quality_penalty(confidence, raw_findings, company_domain)

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
            "evidence_sufficiency": evidence_sufficiency_score(raw_findings, company_domain)
        }
    
    # --- FALLBACK: All models failed — return empty result, never fabricate evidence ---
    print(f"   [Scanner 2] All models failed for {company_name}. Returning empty result (no simulated data).")
    return { "score": 0, "checklist": [], "links": [], "key_people": [], "is_simulated": True, "confidence_score": 0, "washing_flag": False, "washing_type": None }


# ==============================================================================
# FINANCIAL HEALTH INDEX — Separate from 5-jury reality check
# ==============================================================================

def _execute_financial_search(api_key, model_name, prompt, company_name):
    """Execute a single Gemini search for financial data. Returns parsed dict or None."""
    try:
        print(f"   [Financial Health] Attempting {model_name} for {company_name}...")
        start = time.time()
        client = get_gemini_client()
        response = call_with_503_backoff(
            lambda: client.models.generate_content(
                model=model_name,
                contents=prompt,
                config={
                    'tools': [{'google_search': {}}],
                    'temperature': 0.0,
                    'safety_settings': [
                        {'category': 'HARM_CATEGORY_HARASSMENT', 'threshold': 'BLOCK_NONE'},
                        {'category': 'HARM_CATEGORY_HATE_SPEECH', 'threshold': 'BLOCK_NONE'},
                        {'category': 'HARM_CATEGORY_SEXUALLY_EXPLICIT', 'threshold': 'BLOCK_NONE'},
                        {'category': 'HARM_CATEGORY_DANGEROUS_CONTENT', 'threshold': 'BLOCK_NONE'}
                    ]
                }
            ),
            label=f"Financial Health {model_name}",
        )
        text_content = response.text if response.text else ""
        json_payload = extract_json_from_text(text_content)
        elapsed = int((time.time() - start) * 1000)

        if json_payload:
            try:
                parsed = json.loads(json_payload, strict=False)
                print(f"   [Financial Health] {model_name} succeeded in {elapsed}ms")
                return parsed
            except json.JSONDecodeError as je:
                print(f"   [Financial Health] {model_name} JSON parse failed: {je}")
                # Fall through to Sonnet repair gate

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
    Uses Gemini with Google Search grounding to find financial data.
    Returns a 0-100 composite score with 4 sub-dimensions (0-25 each).
    """
    api_key = os.environ.get("GEMINI_API_KEY") or os.environ.get("API_KEY")
    primary_model = model_id or "gemini-3.1-pro-preview"

    # Dynamic year window — always search for the two most recent calendar years
    # so results stay current without manual updates each January.
    _cur_year = date.today().year
    _prev_year = _cur_year - 1

    search_name = clean_company_name_for_search(company_name)
    domain_hint = get_clean_domain(company_url) if company_url else f"{search_name.lower().replace(' ', '')}.fi"

    DEFAULT_RESULT = {
        "financial_index": None,
        "sub_scores": {"revenue_trend": None, "profitability": None, "financial_stability": None, "investment_signal": None},
        "financial_findings": [],
        "financial_confidence": 0,
        "is_simulated": True
    }

    if not api_key:
        print(f"   [Financial Health] No GEMINI_API_KEY. Skipping financial analysis.")
        return DEFAULT_RESULT

    prompt = f"""Perform a FINANCIAL HEALTH ASSESSMENT on "{search_name}" (Domain: {domain_hint}).

TARGET: Finnish companies. Use BOTH English and Finnish search terms.

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
        "source_title": "Page/article title"
    }}}},
    "profitability": {{{{
        "score": (integer 0-25),
        "assessment": "Brief assessment",
        "evidence_snippet": "Key data point",
        "source_url": "URL",
        "source_title": "Title"
    }}}},
    "financial_stability": {{{{
        "score": (integer 0-25),
        "assessment": "Brief assessment with equity ratio/debt level if available",
        "evidence_snippet": "Key data point",
        "source_url": "URL",
        "source_title": "Title"
    }}}},
    "investment_signal": {{{{
        "score": (integer 0-25),
        "assessment": "Brief assessment of investment activity",
        "evidence_snippet": "Key data point",
        "source_url": "URL",
        "source_title": "Title"
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
    data = _execute_financial_search(api_key, primary_model, prompt, company_name)
    if not data:
        print(f"   [Financial Health] Primary model failed, retrying in 2s...")
        time.sleep(2)
        data = _execute_financial_search(api_key, primary_model, prompt, company_name)

    # Attempt 2: Fallback to Gemini 3 Flash with one retry
    if not data:
        print(f"   [Financial Health] Primary exhausted. Falling back to flash model...")
        data = _execute_financial_search(api_key, "gemini-3-flash-preview", prompt, company_name)
    if not data:
        time.sleep(2)
        data = _execute_financial_search(api_key, "gemini-3-flash-preview", prompt, company_name)

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
            "source_title": dim_data.get("source_title", "")
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
