
import os
import json
import time
import re
import requests as http_requests
import concurrent.futures
from urllib.parse import urlparse
from google import genai
from google.genai import types
import anthropic
from knowledge_base import get_evolving_framework

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

def extract_json_from_text(text):
    """
    Robustly extracts the first JSON object found in a string.
    Handles Markdown code blocks, conversational filler, etc.
    """
    if not text: 
        return None
    
    # 1. Try finding a markdown block first
    markdown_match = re.search(r"```json\s*(\{.*?\})\s*```", text, re.DOTALL)
    if markdown_match:
        return markdown_match.group(1)

    # 2. Fallback: Find the first outer curly braces
    match = re.search(r'\{.*\}', text, re.DOTALL)
    if match:
        return match.group(0)
        
    return None

def _verify_url(url, timeout=4):
    """
    Verify a single URL exists via HTTP HEAD request.
    Returns True if the URL responds with a success/redirect status (2xx, 3xx).
    Returns False for 4xx, 5xx, timeouts, or connection errors.
    """
    if not url or not url.startswith("http"):
        return False
    try:
        resp = http_requests.head(url, timeout=timeout, allow_redirects=True,
                                   headers={"User-Agent": "Mozilla/5.0 (compatible; AdaptabilityScanner/1.0)"})
        return resp.status_code < 400
    except Exception:
        # Timeout, connection error, SSL error, etc.
        # Try GET as fallback — some servers reject HEAD
        try:
            resp = http_requests.get(url, timeout=timeout, allow_redirects=True, stream=True,
                                      headers={"User-Agent": "Mozilla/5.0 (compatible; AdaptabilityScanner/1.0)"})
            resp.close()
            return resp.status_code < 400
        except Exception:
            return False


def _validate_evidence_urls(links, checklist, max_workers=6):
    """
    URL Validation Gate: Verifies evidence URLs actually exist.
    Adds 'verified' field (true/false) to each link and checklist entry.
    Runs in parallel for speed. Logs summary.
    """
    start = time.time()

    # Collect all unique URLs to check
    url_map = {}  # url -> verified (bool)
    for item in links + checklist:
        url = (item.get("url") or item.get("source_url") or "").strip()
        if url and url.startswith("http") and url not in url_map:
            url_map[url] = None  # pending

    if not url_map:
        return links, checklist, 0, 0

    # Verify in parallel
    with concurrent.futures.ThreadPoolExecutor(max_workers=max_workers) as executor:
        future_to_url = {executor.submit(_verify_url, url): url for url in url_map}
        for future in concurrent.futures.as_completed(future_to_url):
            url = future_to_url[future]
            try:
                url_map[url] = future.result()
            except Exception:
                url_map[url] = False

    # Apply results to links
    verified_count = 0
    dead_count = 0
    for item in links:
        url = (item.get("url") or "").strip()
        if url and url in url_map:
            item["verified"] = url_map[url]
            if url_map[url]:
                verified_count += 1
            else:
                dead_count += 1
        else:
            item["verified"] = False

    # Apply results to checklist
    for item in checklist:
        url = (item.get("source_url") or item.get("url") or "").strip()
        if url and url in url_map:
            item["verified"] = url_map[url]
        # No URL = no verification needed

    elapsed = int((time.time() - start) * 1000)
    total = verified_count + dead_count
    print(f"   🔗 [URL Validation] {verified_count}/{total} URLs verified, {dead_count} dead/unreachable ({elapsed}ms)")

    return links, checklist, verified_count, dead_count


def _repair_json_with_sonnet(raw_text, company_name):
    """
    JSON Repair Gate: When a Gemini model returns grounded search data as
    unstructured text, use a fast Sonnet call to extract and restructure it
    into the expected JSON format. This preserves evidence from Google Search
    grounding that would otherwise be discarded.
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
        repair_prompt = f"""The following text is a forensic reality-check analysis for "{company_name}" that was returned by a search-grounded AI model, but it is NOT valid JSON. Your job is to extract the data and return it as valid JSON.

The expected JSON structure is:
{{
  "reality_score_adjustment": <integer 0-100>,
  "confidence_score": <integer 0-100>,
  "washing_flag": <boolean>,
  "washing_type": <string or null>,
  "checklist": [
    {{"query": "<search query>", "jury": "<jury name>", "found": <boolean>, "sentiment": "positive|negative|neutral", "snippet": "<evidence snippet>", "source_url": "<url>"}}
  ],
  "links": [
    {{"title": "<source title>", "url": "<source url>", "snippet": "<brief description>"}}
  ],
  "key_people": [
    {{"name": "<person name>", "role": "<role>", "context": "<relevance>"}}
  ]
}}

Extract as much data as possible from the text below. If a field cannot be determined, use sensible defaults (0 for scores, empty arrays for lists, false for booleans).

TEXT TO PARSE:
{raw_text[:8000]}

Return ONLY valid JSON, no explanation."""

        response = client.messages.create(
            model="claude-sonnet-4-5-20250929",
            max_tokens=2048,
            messages=[{"role": "user", "content": repair_prompt}]
        )
        response_text = response.content[0].text if response.content else ""
        json_payload = extract_json_from_text(response_text)
        if json_payload:
            result = json.loads(json_payload, strict=False)
            # Cap confidence since this went through a repair step
            if result.get("confidence_score", 0) > 70:
                result["confidence_score"] = 70
            return result
        return None
    except Exception as e:
        print(f"   ❌ [JSON Repair] Sonnet repair failed: {e}")
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

    STEP 4 - SEARCH FOR NEW EVIDENCE:
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
    3: ["Culture", "Talent", "Employee", "Inclusion", "Community", "HR", "People", "Careers"],
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
       - `"{search_name}" annual report 2024 2025 OR "vuosikertomus"`
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
       - `"{search_name}" technology investment Finland 2024 2025 OR "teknologiainvestointi"`""",
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

    return f"""
    TARGETED RE-SEARCH for "{search_name}" (Domain: {domain_hint}).
    CONTEXT: Intent Score is {intent_score}/100.

    IMPORTANT: A previous search found LIMITED evidence for certain areas.
    Your job is to find ADDITIONAL evidence using BROADER search terms.
    Do NOT use strict site: filters. Search openly across the web.
    Use BOTH English and Finnish search terms (target is a Finnish company).

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
                "url": "URL",
                "is_recent": boolean (True if < 12 months)
            }}
        ],
        "key_people": [
            {{
                "name": "Full Name",
                "role": "Exact Title",
                "relevance": "Why relevant?"
            }}
        ],
        "new_evidence_count": (integer, how many NEW pieces of evidence you found)
    }}

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

    Pre-computes diagnostics from actual data, then asks Gemini Flash to interpret
    them and select from a constrained set of actionable suggestion types.

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
        anthropic_key = os.environ.get("ANTHROPIC_API_KEY", "")
        # Clean the key (same logic as main.py)
        if "=" in anthropic_key:
            try: anthropic_key = anthropic_key.split("=", 1)[1]
            except: pass
        anthropic_key = anthropic_key.strip().strip('"').strip("'")

        if not anthropic_key:
            print("   [Self-Analysis] No ANTHROPIC_API_KEY found. Skipping.")
            return None

        claude_client = anthropic.Anthropic(api_key=anthropic_key)
        print(f"   [Self-Analysis] Running via Claude Sonnet 4.5...")
        response = claude_client.messages.create(
            model="claude-sonnet-4-5-20250929",
            max_tokens=1024,
            messages=[{"role": "user", "content": prompt}]
        )

        text = response.content[0].text if response.content else ""
        json_payload = extract_json_from_text(text)
        if json_payload:
            analysis = json.loads(json_payload, strict=False)
            analysis["company"] = company_name
            analysis["days_between_scans"] = days_since_last
            analysis["diagnostics"] = diagnostics
            analysis["score_delta"] = curr_reality - prev_reality if isinstance(curr_reality, (int, float)) and isinstance(prev_reality, (int, float)) else None

            print(f"   [Self-Analysis] Cause: {analysis.get('primary_cause', 'unknown')} | Reliability: {analysis.get('reliability', '?')}")
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
        return None
    except Exception as e:
        print(f"   [Self-Analysis] Failed: {e}")
        return None


def x_ray_search(company_name: str, company_url: str = None, model_id: str = None, intent_score: int = 0, previous_context: dict = None, max_research_loops: int = 2, subsidiaries: list = None):
    """
    SCANNER 2: THE REALITY CHECK (External Evidence)

    This function performs the Forensic Scan.
    It uses the 'Standardized Tribunal' methodology with STRICT domain filtering.

    Optional: previous_context dict enables history-aware rescan mode.
    Optional: max_research_loops (0-3) controls confidence-driven re-search.
              Default 2 for single scans, use 1 for batch scans.
              Set to 0 to disable re-search entirely.
    """

    api_key = os.environ.get("GEMINI_API_KEY") or os.environ.get("API_KEY")
    # Default to the chosen model, or fallback to the reliable 3-pro
    primary_model = model_id or "gemini-3-pro-preview"

    search_name = clean_company_name_for_search(company_name)
    domain_hint = get_clean_domain(company_url) if company_url else f"{search_name.lower().replace(' ', '')}.fi"

    if not api_key:
        print("   [Scanner 2] CRITICAL: No GEMINI_API_KEY found. Using simulation fallback.")
        return { "score": 60, "checklist": [], "links": [], "key_people": [], "is_simulated": True, "confidence_score": 0, "washing_flag": False }

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

    # --- DEFINING THE PROMPT ONCE ---
    prompt = f"""
    Perform a FORENSIC INVESTIGATION on "{search_name}" (Domain: {domain_hint}).
    CONTEXT: Internal/Marketing text analysis suggests an Intent Score of {intent_score}/100.
    {history_block}
    {evidence_anchors}
    {subsidiary_block}
    SEARCH STRATEGY:
    Do NOT read the company's own marketing pages (About Us, Front Page).
    EXCEPTION: Careers pages and Service Portfolio pages ARE allowed — they contain operational
    evidence (real tech stacks, team structures, actual service delivery) not marketing fluff.
    Otherwise, look at evidence from these specific 'JURIES' defined below.

    CRITICAL: You must execute searches for ALL 5 JURIES. Do not skip any.
    NOTE: Target companies are primarily Finnish. Use BOTH English and Finnish search terms.

    1. THE ARCHITECTURAL JURY (Engineering Reality)
       - Public Code & APIs: `site:github.com "{search_name}" OR site:postman.com "{search_name}"`
       - Engineering Culture: `site:{domain_hint} "engineering blog" OR "tech blog" OR "developers" OR "teknologiablogi" OR "kehittäjät"`
       - Design Systems: `site:{domain_hint} "Design System" OR "UI Kit" OR "suunnittelujärjestelmä"`
       - Job Ads Tech Stack (CRITICAL): `site:linkedin.com/jobs "{search_name}" OR site:duunitori.fi "{search_name}" OR site:indeed.fi "{search_name}"`
         ANALYZE what technologies/tools/methodologies appear in their job postings.
         Job ads reveal the REAL tech stack, not marketing claims.
       - Job Descriptions Detail: `"{search_name}" job description "engineer" OR "developer" OR "kehittäjä" OR "arkkitehti"`

    2. THE STRATEGIC JURY (Business Model & Procurement)
       - Cloud Sourcing (Finland): `site:hankintailmoitukset.fi "{search_name}" AND ("Cloud" OR "Pilvi" OR "SaaS" OR "pilvipalvelu")`
       - Recurring Revenue Evidence: `site:{domain_hint} filetype:pdf "Recurring Revenue" OR "ARR" OR "Jatkuva laskutus" OR "vuosiliikevaihto"`
       - Modern Tech Investments: `site:crunchbase.com "{search_name}" "Acquisitions" OR "Yrityskaupat"`
       - Leadership Thought Leadership: `site:medium.com "{search_name}" OR site:substack.com "{search_name}"`
       - Finnish Business News: `site:kauppalehti.fi "{search_name}" OR site:talouselama.fi "{search_name}" "strategia" OR "digitalisaatio"`

    3. THE CULTURAL JURY (Talent & Psychological Safety)
       - Boomerang Employees (High Trust): `site:linkedin.com "{search_name}" AND ("returned to" OR "rejoined" OR "palasi takaisin")`
       - Inclusion Artifacts: `site:mimmitkoodaa.fi "{search_name}" OR "Women in Tech Finland" "{search_name}"`
       - Community Hosting: `site:meetup.com "{search_name}" AND ("Host" OR "Sponsor" OR "kumppani")`
       - Employee Voice: `site:glassdoor.com "{search_name}" OR site:teamblind.com "{search_name}"` (NO REDDIT)
       - Finnish Employee Discussion: `"{search_name}" "työntekijäkokemus" OR "työpaikka-arvio" OR "työkulttuuri"`
       - Careers Page Culture Signals (CRITICAL): `site:{domain_hint} "careers" OR "ura" OR "avoimet työpaikat" OR "open positions"`
         ANALYZE the careers page for: team structure, benefits, work models (remote/hybrid),
         values in action, hiring process transparency. These reveal real culture, not marketing.

    4. THE AI & DATA JURY (Future Readiness)
       - Real AI Models: `site:huggingface.co "{search_name}"`
       - Tech Stack Verification: `site:stackshare.io "{search_name}"`
       - Cloud Case Studies: `site:aws.amazon.com "Case Studies" "{search_name}" OR site:customers.microsoft.com "{search_name}"`
       - Deep Academic Audits: `site:theseus.fi "{search_name}" OR site:research.aalto.fi "{search_name}"`
       - Finnish Tech Research: `site:vtt.fi "{search_name}" OR site:tuni.fi "{search_name}" OR site:aalto.fi "{search_name}"`

    5. THE SERVICE PORTFOLIO JURY (Delivery Reality — NEW)
       - Service Pages: `site:{domain_hint} "services" OR "palvelut" OR "solutions" OR "ratkaisut"`
         ANALYZE what they actually DELIVER vs. what they claim. Look for specifics:
         platform names, technology mentions, methodology references, case studies.
       - Client References: `site:{domain_hint} "case study" OR "reference" OR "asiakastarina" OR "referenssi"`
       - External Reviews: `"{search_name}" review OR "arvostelu" service quality OR "palvelun laatu"`
       - Analyst Reports: `"{search_name}" Gartner OR Forrester OR ISG OR "Magic Quadrant"`

    ALSO: IDENTIFY 4 KEY MODERNIZATION STAKEHOLDERS:
    Search for real names of current executives in these roles:
    1. Technology (CTO, CIO, VP Engineering)
    2. People/Culture (CPO, HR Director)
    3. Transformation (CDO, Head of Strategy)
    4. Product/Innovation (CPO, Head of Product)

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
       - APPLY A SIGNIFICANT PENALTY (-15 to -25 points) to the adjustment.
       - This is a strong signal of low reality.
    4. POSITIVE SIGNALS:
       - Verified "Boomerang" hires, Hugging Face models, or Public Design Systems: +10 to +20 points.

    DETECT "WASHING" (Logic Check):
    - Intent Score is {intent_score}. 
    - RULE: "Washing" (Green/Agile/AI Washing) is ONLY possible if Intent Score is HIGH (>60) AND Reality is LOW.
    - IF Intent Score is LOW (<50), you CANNOT flag this as Washing.
    - AI WASHING: Claims of "AI-driven" but no technical evidence (Hugging Face/StackShare) found.
    - AGILE WASHING: Claims of "Tribes/Squads" but evidence of "Waterfall", "Approvals", "Bureaucracy".

    RETURN STRICT JSON:
    {{
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
        "key_people": [
            {{
                "name": "Full Name",
                "role": "Exact Title",
                "relevance": "Why relevant?"
            }}
        ],
        "reality_score_adjustment": (integer -40 to +40),
        "confidence_score": (integer 0 to 100),
        "washing_flag": boolean (True if significant contradiction/washing found AND Intent > 60),
        "washing_type": "AI Washing" or "Agile Washing" or "Green Washing" or null
    }}
    
    GUIDE FOR CONFIDENCE SCORE:
    - 0-40: Very few sources found, evidence is generic or very old.
    - 41-70: Some specific evidence found, but mixed recency or limited sources.
    - 71-100: Multiple independent sources (Juries) confirm findings, evidence is recent (<12mo) and specific.
    """

    def execute_with_model(target_model_name, custom_prompt=None):
        """Execute a search with the given model. Uses main prompt by default, or a custom one for re-search."""
        active_prompt = custom_prompt if custom_prompt else prompt
        print(f"   🔄 [Forensic Scan] Attempting {target_model_name} for {company_name}...")
        start = time.time()
        try:
            client = genai.Client(api_key=api_key)
            response = client.models.generate_content(
                model=target_model_name,
                contents=active_prompt,
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

            text_content = response.text if response.text else ""
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

            if text_content and len(text_content) > 100:
                # --- JSON REPAIR GATE ---
                # Model returned grounded search data but not as valid JSON.
                # Use Sonnet to extract/restructure it rather than discarding.
                print(f"   ⚠️ [Forensic Scan] {target_model_name} returned unstructured text ({elapsed}ms, {len(text_content)} chars). Attempting JSON repair...")
                repaired = _repair_json_with_sonnet(text_content, company_name)
                if repaired:
                    repair_elapsed = int((time.time() - start) * 1000)
                    print(f"   ✅ [JSON Repair] Recovered structured data from {target_model_name} response ({repair_elapsed}ms total)")
                    return repaired
                else:
                    print(f"   ⚠️ [JSON Repair] Could not recover JSON from {target_model_name} response")
                    return None
            else:
                print(f"   ⚠️ [Forensic Scan] {target_model_name} returned no parseable JSON ({elapsed}ms)")
                return None
        except Exception as e:
            elapsed = int((time.time() - start) * 1000)
            print(f"   ❌ [Forensic Scan] {target_model_name} failed in {elapsed}ms: {e}")
            return None

    def execute_with_anthropic(active_prompt):
        """Fallback: Use Sonnet 4.5 when Gemini is rate-limited or unavailable."""
        anthropic_key = os.environ.get("ANTHROPIC_API_KEY", "")
        if "=" in anthropic_key:
            try: anthropic_key = anthropic_key.split("=", 1)[1]
            except: pass
        anthropic_key = anthropic_key.strip().strip('"').strip("'")
        if not anthropic_key:
            print("   [Scanner 2] No ANTHROPIC_API_KEY for Sonnet fallback.")
            return None
        try:
            print(f"   🔄 [Forensic Scan] Attempting claude-sonnet-4-5 (knowledge-based fallback)...")
            start = time.time()
            fallback_prefix = f"""You are a forensic company analyst. Answer based on your training knowledge.
NOTE: You do NOT have live web search. Provide the best analysis you can from your existing knowledge of {company_name}.
For any URLs, use real URLs you know from training data. Mark confidence_score lower (max 60) since this is knowledge-based, not search-verified.

"""
            claude_client = anthropic.Anthropic(api_key=anthropic_key)
            response = claude_client.messages.create(
                model="claude-sonnet-4-5-20250929",
                max_tokens=4096,
                messages=[{"role": "user", "content": fallback_prefix + active_prompt}]
            )
            text_content = response.content[0].text if response.content else ""
            json_payload = extract_json_from_text(text_content)
            elapsed = int((time.time() - start) * 1000)
            if json_payload:
                result = json.loads(json_payload, strict=False)
                if result.get("confidence_score", 0) > 60:
                    result["confidence_score"] = 60
                print(f"   ✅ [Forensic Scan] claude-sonnet-4-5 succeeded in {elapsed}ms (knowledge-based)")
                return result
            else:
                print(f"   ⚠️ [Forensic Scan] claude-sonnet-4-5 returned no parseable JSON ({elapsed}ms)")
                return None
        except Exception as e:
            print(f"   ❌ [Forensic Scan] claude-sonnet-4-5 fallback failed: {e}")
            return None

    # --- ATTEMPT 1: User Selected Model (UNCHANGED) ---
    data = execute_with_model(primary_model)

    # --- ATTEMPT 2: Smart Upgrade (if initial model failed completely) ---
    if not data and primary_model != "gemini-3-pro-preview":
        print("   [Scanner 2] Primary model failed to return JSON. AUTO-UPGRADING to Gemini 3 Pro Preview...")
        data = execute_with_model("gemini-3-pro-preview")

    # --- ATTEMPT 3: Gemini 2.5 Pro Fallback (preserves Google Search grounding) ---
    if not data:
        print("   [Scanner 2] Gemini 3 Pro exhausted. FALLING BACK to Gemini 2.5 Pro (grounding preserved)...")
        data = execute_with_model("gemini-2.5-pro")

    # --- ATTEMPT 4: Sonnet 4.5 Fallback (no grounding — knowledge-based only) ---
    if not data:
        print("   [Scanner 2] All Gemini models exhausted. FALLING BACK to Sonnet 4.5 (knowledge-based)...")
        data = execute_with_anthropic(prompt)

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

                # Use the same model for re-search, with 2.5 Pro fallback
                research_data = execute_with_model(primary_model, custom_prompt=research_prompt)
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
    # BASE SCORE UPDATE: Raised from 50 to 60.
    # Assumption: Companies are slightly "Good" until proven "Bad".
    # This prevents neutral search results from dragging a good company into Stagnation.
    base_score = 60

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
        # If score changed by more than ±15 from previous, re-run the scan once.
        # If re-scan confirms the change → accept as fact (genuine change).
        # If re-scan contradicts → use the more conservative (closer to previous) result.
        if previous_context and previous_context.get("previous_reality") is not None:
            prev_reality = previous_context["previous_reality"]
            delta = final_score - prev_reality

            if abs(delta) >= 15:
                print(f"   [Delta Verification] Large score change detected: {prev_reality} → {final_score} (delta {delta}). Running verification re-scan...")
                verify_data = execute_with_model(primary_model)
                if not verify_data:
                    verify_data = execute_with_model("gemini-2.5-pro")

                if verify_data:
                    verify_adjustment = verify_data.get("reality_score_adjustment", 0)
                    verify_score = max(0, min(100, base_score + verify_adjustment))
                    verify_delta = verify_score - prev_reality

                    if abs(verify_delta) >= 15 and (delta > 0) == (verify_delta > 0):
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

        # --- URL VALIDATION GATE ---
        # Verify all evidence URLs actually exist before returning results.
        # Marks each link/checklist entry with "verified": true/false.
        # Penalizes confidence when many URLs are dead (hallucinated evidence).
        raw_findings, checklist, url_verified, url_dead = _validate_evidence_urls(raw_findings, checklist)

        # --- URL-TRIGGERED RE-SCAN ---
        # If >20% of URLs are dead, the evidence base is unreliable.
        # Run ONE targeted re-search to try to find real, verifiable evidence.
        url_total = url_verified + url_dead
        dead_ratio = url_dead / url_total if url_total > 0 else 0

        if dead_ratio > 0.20 and url_dead >= 2:
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

            recovery_data = execute_with_model(primary_model, custom_prompt=recovery_prompt)
            if not recovery_data:
                recovery_data = execute_with_model("gemini-2.5-pro", custom_prompt=recovery_prompt)

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
                    recovery_links, _, rec_verified, rec_dead = _validate_evidence_urls(recovery_links, [])
                    print(f"   🔗 [URL Re-Scan] Recovery: {rec_verified} verified, {rec_dead} dead out of {len(recovery_links)} new links")

                    # Only keep verified recovery links, dedup against existing URLs
                    existing_urls = {(f.get("url") or "").rstrip("/").lower() for f in raw_findings}
                    added = 0
                    for link in recovery_links:
                        if link.get("verified") and link.get("url"):
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

        return {
            "score": int(final_score),
            "checklist": checklist,
            "links": raw_findings,
            "key_people": key_people,
            "is_simulated": False,
            "confidence_score": int(confidence),
            "washing_flag": washing_flag,
            "washing_type": washing_type
        }
    
    # --- FALLBACK: SIMULATION ---
    print(f"   [Scanner 2] All models failed. Engaging AI Simulation for {company_name} (Fallback)...")
    try:
        # Try Sonnet 4.5 first for simulation (Gemini may be rate-limited)
        anthropic_key = os.environ.get("ANTHROPIC_API_KEY", "")
        if "=" in anthropic_key:
            try: anthropic_key = anthropic_key.split("=", 1)[1]
            except: pass
        anthropic_key = anthropic_key.strip().strip('"').strip("'")

        if anthropic_key:
            claude_client = anthropic.Anthropic(api_key=anthropic_key)
            sim_prompt = f"Generate 3 REALISTIC forensic search findings for {company_name}. Return JSON with 'findings' (array of objects with label, found, sentiment, category, snippet, url, is_recent), 'reality_score_adjustment' (-40 to +40), 'confidence_score' (0-100), 'washing_flag' (boolean), and 'key_people' (array)."
            response = claude_client.messages.create(
                model="claude-sonnet-4-5-20250929",
                max_tokens=2048,
                messages=[{"role": "user", "content": sim_prompt}]
            )
            sim_text = response.content[0].text if response.content else ""
            sim_data = json.loads(extract_json_from_text(sim_text), strict=False)
        else:
            # Last resort: try Gemini Flash
            client = genai.Client(api_key=api_key)
            sim_prompt = f"Generate 3 REALISTIC forensic search findings for {company_name}. Return JSON with 'findings', 'score' (0-100), 'confidence_score' (0-100), 'washing_flag' (boolean), and 'key_people' (array)."
            response = client.models.generate_content(model="gemini-3-flash-preview", contents=sim_prompt)
            sim_data = json.loads(extract_json_from_text(response.text), strict=False)

        return {
            "score": sim_data.get("score", 60),
            "checklist": [],
            "links": sim_data.get("findings", []),
            "key_people": sim_data.get("key_people", []),
            "is_simulated": True,
            "confidence_score": min(sim_data.get("confidence_score", 40), 50),
            "washing_flag": sim_data.get("washing_flag", False),
            "washing_type": sim_data.get("washing_type", None)
        }
    except:
        return { "score": 60, "checklist": [], "links": [], "key_people": [], "is_simulated": True, "confidence_score": 0, "washing_flag": False }


# ==============================================================================
# FINANCIAL HEALTH INDEX — Separate from 5-jury reality check
# ==============================================================================

def _execute_financial_search(api_key, model_name, prompt, company_name):
    """Execute a single Gemini search for financial data. Returns parsed dict or None."""
    try:
        print(f"   [Financial Health] Attempting {model_name} for {company_name}...")
        start = time.time()
        client = genai.Client(api_key=api_key)
        response = client.models.generate_content(
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


def compute_financial_index(sub_scores: dict) -> int:
    """
    Weighted Financial Health Index with core health gate.
    Revenue/Profitability dominate (70% combined); Efficiency/Investment are supporting (30%).

    Stage 1: Weighted linear base
      - Revenue Trend:    30%
      - Profitability:    40%  (strongest health signal)
      - Efficiency:       15%
      - Investment Signal: 15%

    Stage 2: Core health gate
      When both fundamentals (R+P) are weak, a multiplier (floor 0.25) crushes the score.
      Gate reaches 1.0 (no penalty) when R+P >= 60% of max (30 out of 50).
    """
    R = sub_scores.get("revenue_trend", 12)
    P = sub_scores.get("profitability", 12)
    E = sub_scores.get("efficiency", 12)
    I = sub_scores.get("investment_signal", 12)

    # Stage 1: Weighted linear base (0-100)
    base = (0.30 * R + 0.40 * P + 0.15 * E + 0.15 * I) * 4

    # Stage 2: Core health gate — penalizes when both fundamentals are weak
    core_pct = (R + P) / 50.0
    GATE_FLOOR = 0.25
    GATE_THRESHOLD = 0.60
    gate = min(1.0, GATE_FLOOR + (1.0 - GATE_FLOOR) * core_pct / GATE_THRESHOLD)

    score = round(base * gate)
    return max(0, min(100, score))


def financial_health_search(company_name: str, company_url: str = None, model_id: str = None) -> dict:
    """
    FINANCIAL HEALTH INDEX: Separate from the 5-jury reality check.
    Uses Gemini with Google Search grounding to find financial data.
    Returns a 0-100 composite score with 4 sub-dimensions (0-25 each).
    """
    api_key = os.environ.get("GEMINI_API_KEY") or os.environ.get("API_KEY")
    primary_model = model_id or "gemini-3-pro-preview"

    search_name = clean_company_name_for_search(company_name)
    domain_hint = get_clean_domain(company_url) if company_url else f"{search_name.lower().replace(' ', '')}.fi"

    DEFAULT_RESULT = {
        "financial_index": None,
        "sub_scores": {"revenue_trend": None, "profitability": None, "efficiency": None, "investment_signal": None},
        "financial_findings": [],
        "financial_confidence": 0,
        "is_simulated": True
    }

    if not api_key:
        print(f"   [Financial Health] No GEMINI_API_KEY. Skipping financial analysis.")
        return DEFAULT_RESULT

    prompt = f"""Perform a FINANCIAL HEALTH ASSESSMENT on "{search_name}" (Domain: {domain_hint}).

TARGET: Finnish companies. Use BOTH English and Finnish search terms.

SEARCH STRATEGY — Execute these searches:

1. REVENUE TREND (liikevaihto):
   - `"{search_name}" liikevaihto tulos 2024 2025`
   - `"{search_name}" annual report revenue profit 2024 2025`
   - `site:kauppalehti.fi "{search_name}" liikevaihto` OR `site:talouselama.fi "{search_name}"`
   - `"{search_name}" tilinpäätös vuosikertomus`
   EVALUATE: Is revenue growing, stable, or declining over the last 2-3 years?

2. PROFITABILITY (kannattavuus):
   - `"{search_name}" operating profit EBITDA margin 2024`
   - `"{search_name}" liikevoitto käyttökate tulos`
   - `site:finder.fi "{search_name}"` OR `site:asiakastieto.fi "{search_name}"`
   EVALUATE: Is the company profitable? Strong margins, moderate, weak, or negative?

3. EFFICIENCY (tehokkuus):
   - `"{search_name}" henkilöstö liikevaihto per employee`
   - `"{search_name}" revenue employees headcount`
   - `"{search_name}" tuottavuus henkilöstömäärä`
   EVALUATE: Revenue per employee ratio. Compare to industry peers if possible.

4. INVESTMENT SIGNAL (investoinnit):
   - `"{search_name}" R&D investment technology capex 2024 2025`
   - `"{search_name}" T&K investoinnit teknologia`
   - `"{search_name}" hiring engineers developers rekrytointi kehittäjä`
   - `"{search_name}" acquisition yrityskauppa technology`
   EVALUATE: Is the company investing in growth? R&D spend, tech hiring, acquisitions.

SCORING LOGIC:
Each dimension scores 0-25:
- Revenue Trend: 0-8 (declining >10%), 9-15 (stable ±5%), 16-20 (growing 5-15%), 21-25 (growing >15%)
- Profitability: 0-8 (negative/losses), 9-15 (weak <5% margin), 16-20 (moderate 5-15%), 21-25 (strong >15%)
- Efficiency: 0-8 (very low for sector), 9-15 (below average), 16-20 (average), 21-25 (top quartile)
- Investment Signal: 0-8 (no visible investment), 9-15 (maintenance level), 16-20 (moderate growth), 21-25 (aggressive investment/M&A)

If no financial data is found for a dimension, score it 12 (neutral).

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
    "efficiency": {{{{
        "score": (integer 0-25),
        "assessment": "Brief assessment with revenue/employee if available",
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

    # Attempt 1: Primary model
    data = _execute_financial_search(api_key, primary_model, prompt, company_name)

    # Attempt 2: Fallback to Gemini 2.5 Pro
    if not data and primary_model != "gemini-2.5-pro":
        data = _execute_financial_search(api_key, "gemini-2.5-pro", prompt, company_name)

    if not data:
        print(f"   [Financial Health] All models failed for {company_name}. Using defaults.")
        return DEFAULT_RESULT

    # Assemble result
    dimensions = ["revenue_trend", "profitability", "efficiency", "investment_signal"]
    sub_scores = {}
    findings = []

    for dim in dimensions:
        dim_data = data.get(dim, {})
        if isinstance(dim_data, dict):
            score = max(0, min(25, int(dim_data.get("score", 12))))
        else:
            score = 12
            dim_data = {}
        sub_scores[dim] = score
        findings.append({
            "dimension": dim.replace("_", " ").title(),
            "score": score,
            "assessment": dim_data.get("assessment", "No data found"),
            "evidence_snippet": dim_data.get("evidence_snippet", ""),
            "source_url": dim_data.get("source_url", ""),
            "source_title": dim_data.get("source_title", "")
        })

    composite = compute_financial_index(sub_scores)
    confidence = max(0, min(100, int(data.get("financial_confidence", 40))))

    print(f"   ✅ [Financial Health] {company_name}: Index={composite}/100 (Rev={sub_scores['revenue_trend']}, Prof={sub_scores['profitability']}, Eff={sub_scores['efficiency']}, Inv={sub_scores['investment_signal']}), Confidence={confidence}")

    return {
        "financial_index": composite,
        "sub_scores": sub_scores,
        "financial_findings": findings,
        "financial_confidence": confidence,
        "is_simulated": False
    }
