
import os
import requests
import uuid
import time
import json
import hashlib
import threading
import concurrent.futures
from bs4 import BeautifulSoup
from datetime import datetime
from finland_list import TOP_100_COMPANIES, get_company_metadata
from scanner_one import analyze_intent
from scanner_two import x_ray_search, extract_json_from_text, run_self_analysis, financial_health_search
from knowledge_base import get_evolving_framework
from storage import BlobStorage
from reality_check import validate_summary, validate_pitch
from google import genai
from google.genai import types
import anthropic

# --- CONFIGURATION ---
def clean_env_var(value):
    if not value: return ""
    if "=" in value:
        try: value = value.split("=", 1)[1]
        except: pass
    return value.strip().strip('"').strip("'")

def get_page_text(url, timeout=10):
    try:
        headers = {
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36'
        }
        if not url.startswith('http'):
            url = 'https://' + url
            
        response = requests.get(url, headers=headers, timeout=timeout)
        if response.status_code == 200:
            return response.text
        else:
            print(f"   [Scanner 1] ⚠️ HTTP Error {response.status_code} accessing {url}")
    except Exception as e:
        print(f"   [Scanner 1] ⚠️ Connection failed for {url}: {str(e)[:100]}...")
    return ""

def extract_content_from_html(html):
    if not html: return ""
    soup = BeautifulSoup(html, 'html.parser')
    for element in soup(["script", "style", "nav", "footer", "header", "aside", "noscript", "iframe", "svg"]):
        element.decompose()
    text_content = []
    meta = soup.find("meta", attrs={"name": "description"})
    if meta and meta.get("content"): text_content.append(meta['content'])
    for h in soup.find_all(["h1", "h2", "h3"]):
        txt = h.get_text(strip=True)
        if len(txt) > 5: text_content.append(txt)
    for p in soup.find_all("p"):
        txt = p.get_text(strip=True)
        if len(txt) > 30: text_content.append(txt)
    return ". ".join(text_content)

def scrape_strategic_content(base_url: str, company_name: str = "Unknown", subsidiaries: list = None):
    if not base_url: return ""
    print(f"   [Scanner 1] Deep Crawl Started for: {base_url}...")
    
    home_html = get_page_text(base_url)
    
    # --- FALLBACK MECHANISM ---
    if not home_html: 
        print(f"   [Scanner 1] ⛔ SCRAPING BLOCKED/FAILED for {base_url}.")
        print(f"   [Scanner 1] 🔄 Initiating AI Context Retrieval (Fallback) for '{company_name}'...")
        
        api_key = clean_env_var(os.environ.get("API_KEY") or os.environ.get("GEMINI_API_KEY"))
        fallback_prompt = f"""
                Write a detailed, factual summary of the corporate strategy, operating model, and core values for '{company_name}' (Domain: {base_url}).

                Simulate the content typically found on an 'About Us', 'Strategy', or 'Investor Relations' page.
                Include specific details on:
                1. Strategic Vision & Mission
                2. Organizational Structure (e.g. Teams, Hierarchies, Agile models)
                3. Business Model & Service Portfolio
                4. Technology Approach (AI, Cloud, Digital Transformation)

                Output plain text only.
                """
        if api_key:
             try:
                client = genai.Client(api_key=api_key)
                resp = client.models.generate_content(model='gemini-3-flash-preview', contents=fallback_prompt)
                if resp.text:
                    print(f"   [Scanner 1] ✅ AI Successfully retrieved strategic context for {company_name}.")
                    return f"--- AI GENERATED CONTEXT FOR {company_name} (SCRAPE FALLBACK) ---\n{resp.text}"
             except Exception as e:
                print(f"   [Scanner 1] ⚠️ Gemini fallback failed: {e}. Trying Sonnet 4.6...")

        # Sonnet 4.6 fallback for AI context retrieval
        try:
            anthropic_key = clean_env_var(os.environ.get("ANTHROPIC_API_KEY", ""))
            if anthropic_key:
                import anthropic
                claude_client = anthropic.Anthropic(api_key=anthropic_key)
                resp = claude_client.messages.create(
                    model="claude-sonnet-4-6",
                    max_tokens=2048,
                    messages=[{"role": "user", "content": fallback_prompt}]
                )
                text = resp.content[0].text if resp.content else ""
                if text:
                    print(f"   [Scanner 1] ✅ AI Context via Sonnet 4.6 for {company_name}.")
                    return f"--- AI GENERATED CONTEXT FOR {company_name} (SONNET FALLBACK) ---\n{text}"
        except Exception as e2:
            print(f"   [Scanner 1] ❌ Sonnet fallback also failed: {e2}")

        return f"Automatic scraping failed for {base_url}. Analyzing {company_name} based on general market knowledge."

    soup = BeautifulSoup(home_html, 'html.parser')
    
    # PRIORITY MAP: The exact list of categories to scan for Intent Analysis
    priority_map = {
        "Strategy": ["strategy", "strategia", "vision", "visio", "mission", "purpose", "tavoitteet", "goals"],
        "Sustainability": ["sustainability", "vastuullisuus", "esg", "environment", "ympäristö", "climate"],
        "Investors": ["investors", "sijoittajat", "governance", "hallinto", "reports", "financials", "annual"],
        "Careers": ["careers", "jobs", "työpaikat", "rekry", "open positions", "ura", "join us"],
        "About": ["about", "meistä", "company", "yritys", "story", "history", "who we are"],
        "Services": ["services", "palvelut", "solutions", "ratkaisut", "offering", "what we do"],
        "Products": ["products", "tuotteet", "technology", "teknologia"],
        "Blog": ["blog", "news", "ajankohtaista", "insights", "media", "press", "tiedotteet"]
    }
    
    found_targets = {} # Category -> URL
    
    def make_absolute(href):
        if href.startswith('http'): return href
        if href.startswith('//'): return 'https:' + href
        if href.startswith('/'): return base_url.rstrip('/') + href
        return base_url.rstrip('/') + '/' + href

    all_links = soup.find_all('a', href=True)

    for category, keywords in priority_map.items():
        for a in all_links:
            href = a['href'].lower()
            text = a.get_text(strip=True).lower()
            if any(k in href for k in keywords) or any(k in text for k in keywords):
                full_url = make_absolute(a['href'])
                try:
                    base_domain = base_url.split('/')[2].replace('www.', '')
                    if base_domain in full_url: 
                        if category not in found_targets:
                            found_targets[category] = full_url
                except:
                    pass

    final_text = []
    final_text.append(f"--- HOMEPAGE ({base_url}) ---\n" + extract_content_from_html(home_html))
    
    urls_to_visit = sorted(list(set(found_targets.values())))
    print(f"   [Scanner 1] Identified Strategic Targets: {list(found_targets.keys())}")

    # Fetch subpages in parallel (max 4 concurrent) for speed
    if urls_to_visit:
        with concurrent.futures.ThreadPoolExecutor(max_workers=4) as executor:
            future_map = {executor.submit(get_page_text, url): url for url in urls_to_visit}
            for future in concurrent.futures.as_completed(future_map):
                url = future_map[future]
                try:
                    sub_html = future.result()
                    content = extract_content_from_html(sub_html)
                    if content:
                        final_text.append(f"--- SUBPAGE ({url}) ---\n" + content)
                except Exception:
                    pass  # Individual page failure is non-critical
            
    # --- SUBSIDIARY CRAWLING ---
    # For holding companies (e.g. Sampo), also crawl key subsidiary websites
    if subsidiaries:
        print(f"   [Scanner 1] Crawling {len(subsidiaries)} subsidiary site(s)...")
        for sub in subsidiaries:
            sub_name = sub.get("name", "Unknown")
            sub_url = sub.get("url", "")
            if not sub_url:
                continue
            try:
                sub_html = get_page_text(sub_url)
                if sub_html:
                    sub_content = extract_content_from_html(sub_html)
                    if sub_content:
                        final_text.append(f"--- SUBSIDIARY: {sub_name} ({sub_url}) ---\n{sub_content}")
                        print(f"   [Scanner 1] ✅ Subsidiary '{sub_name}' crawled successfully.")
                    else:
                        print(f"   [Scanner 1] ⚠️ Subsidiary '{sub_name}' returned empty content.")
                else:
                    print(f"   [Scanner 1] ⚠️ Subsidiary '{sub_name}' scraping failed.")
            except Exception as e:
                print(f"   [Scanner 1] ⚠️ Subsidiary '{sub_name}' error: {e}")

    combined_text = "\n\n".join(final_text)
    return combined_text[:100000]

def run_batch_seed(real_xray_mode=True, limit=None, progress_callback=None, max_workers=5,
                    batch_progress=None, stop_event=None):
    """
    Runs the batch seed process with PARALLEL execution.
    max_workers controls concurrency (default 5).
    If progress_callback is provided, it calls it with the updated summary_list after every save.
    If batch_progress (dict) is provided, updates it in-place with current/total/current_name/failed.
    If stop_event (threading.Event) is provided, checks it before each company to allow graceful stop.
    """
    target_list = TOP_100_COMPANIES[:limit] if limit else TOP_100_COMPANIES
    total_count = len(target_list)

    if batch_progress is not None:
        batch_progress["total"] = total_count

    batch_start = time.time()
    batch_start_iso = datetime.utcnow().isoformat()
    print(f"🚀 === STARTING PARALLEL BATCH SCAN (Count: {total_count}, Workers: {max_workers}) ===")

    storage = BlobStorage()
    batch_summary_list = []

    try:
        existing_index = storage.retrieve_latest_data("index/summary_list")
        if existing_index:
            batch_summary_list = existing_index
            print(f"   [Info] Loaded existing index of {len(batch_summary_list)} items.")
    except Exception as e:
        print(f"   [Info] Could not load existing index: {e}")

    anthropic_key = clean_env_var(os.environ.get("ANTHROPIC_API_KEY"))
    claude_client = None
    if anthropic_key:
        try:
            claude_client = anthropic.Anthropic(api_key=anthropic_key)
        except:
            print("   [Error] Anthropic Client init failed.")

    # Thread safety: lock for shared index writes and progress callbacks
    index_lock = threading.Lock()
    completed_count = [0]  # Mutable list so nested function can modify

    def process_company(i, company):
        """Process a single company. Thread-safe — only index update needs lock."""
        if stop_event and stop_event.is_set():
            print(f"   [{company['name']}] Skipped (batch stop requested)")
            return

        company_start = time.time()
        company_name = company['name']
        if batch_progress is not None:
            batch_progress["current_name"] = company_name
        print(f"\n🚀 [{i+1}/{total_count}] Processing: {company_name} ...")

        try:
            # 1. REAL SCRAPING (with subsidiary crawling if defined)
            scraped_text = scrape_strategic_content(company["url"], company_name, subsidiaries=company.get("subsidiaries"))

            # 2. REAL INTENT ANALYSIS
            intent_results = analyze_intent(scraped_text)
            print(f"   [{company_name}] Intent Score: {intent_results['score']}")

            # 2b. LOAD PREVIOUS SCANS (multi-scan history lookback, up to 3)
            previous_context = None
            existing_company_data = None
            try:
                existing_company_data = storage.get_company_data(company_name)
                if existing_company_data and existing_company_data.get("lastUpdated"):
                    previous_context = {
                        "last_scanned": existing_company_data.get("lastUpdated"),
                        "previous_intent": existing_company_data.get("intent_score"),
                        "previous_reality": existing_company_data.get("reality_score"),
                        "previous_confidence": existing_company_data.get("confidence_score"),
                        "previous_findings": existing_company_data.get("reality_checklist", [])[:10],
                        "previous_evidence": existing_company_data.get("evidence_links", [])[:8],
                        "previous_washing": existing_company_data.get("washing_type"),
                        "previous_summary": existing_company_data.get("summary_text", ""),
                    }

                    raw_history = existing_company_data.get("history", [])
                    older_scans = []
                    for entry in reversed(raw_history):
                        if entry.get("summary_text") and len(entry["summary_text"]) > 20:
                            older_scans.append(entry)
                        if len(older_scans) >= 2:
                            break
                    if older_scans:
                        previous_context["scan_history"] = older_scans

                    scan_count = 1 + len(older_scans)
                    print(f"   [{company_name}] Rescan mode: {scan_count} previous scan(s)")
            except Exception:
                pass

            # 3. REAL REALITY CHECK + FINANCIAL HEALTH (parallel)
            with concurrent.futures.ThreadPoolExecutor(max_workers=2) as sub_executor:
                reality_future = sub_executor.submit(
                    x_ray_search, company_name, company["url"],
                    model_id="gemini-3.1-pro-preview", intent_score=intent_results["score"],
                    previous_context=previous_context, max_research_loops=2,
                    subsidiaries=company.get("subsidiaries")
                )
                financial_future = sub_executor.submit(
                    financial_health_search, company_name, company["url"]
                )
                try:
                    reality_results = reality_future.result(timeout=350)
                except Exception as e:
                    print(f"   [{company_name}] ❌ Reality search failed: {e}")
                    if existing_company_data and existing_company_data.get("reality_score") is not None:
                        print(f"   [{company_name}] 🔄 Reusing previous reality data (score: {existing_company_data['reality_score']})")
                        reality_results = {
                            "score": existing_company_data["reality_score"],
                            "checklist": existing_company_data.get("reality_checklist", []),
                            "links": existing_company_data.get("evidence_links", []),
                            "key_people": existing_company_data.get("key_people", []),
                            "is_simulated": False,
                            "confidence_score": existing_company_data.get("confidence_score", 0),
                            "washing_flag": existing_company_data.get("washing_flag", False),
                            "washing_type": existing_company_data.get("washing_type"),
                            "reused_from_previous": True,
                        }
                    else:
                        reality_results = {"score": 50, "checklist": [], "links": [], "key_people": [], "is_simulated": True, "confidence_score": 0, "washing_flag": False}

                try:
                    financial_results = financial_future.result(timeout=120)
                except Exception as e:
                    print(f"   [{company_name}] Financial Health failed (non-critical): {e}")
                    if existing_company_data and existing_company_data.get("financial_index") is not None:
                        print(f"   [{company_name}] 🔄 Reusing previous financial data (index: {existing_company_data['financial_index']})")
                        financial_results = {
                            "financial_index": existing_company_data["financial_index"],
                            "sub_scores": existing_company_data.get("financial_sub_scores", {}),
                            "financial_findings": existing_company_data.get("financial_findings", []),
                            "financial_confidence": existing_company_data.get("financial_confidence", 0),
                            "is_simulated": False,
                            "reused_from_previous": True,
                        }
                    else:
                        financial_results = {"financial_index": None, "sub_scores": {}, "financial_findings": [], "financial_confidence": 0, "is_simulated": True}

            print(f"   [{company_name}] Reality Score: {reality_results['score']}")
            if financial_results.get("financial_index") is not None:
                print(f"   [{company_name}] Financial Index: {financial_results['financial_index']}/100")

            # --- SCORE DRIFT DIAGNOSTIC ---
            # Detects large score swings between rescans for diagnostic purposes.
            # Does NOT dampen scores — Opus is the Final Judge and its score is authoritative.
            # When delta >= 25 within 7 days, triggers Self-Analysis to understand why.
            if previous_context and previous_context.get("previous_reality") is not None:
                prev_reality = previous_context["previous_reality"]
                new_reality = reality_results["score"]
                drift_threshold = 25  # Matches live scan threshold

                try:
                    last_scanned = previous_context.get("last_scanned", "")
                    days_since = (datetime.utcnow() - datetime.fromisoformat(last_scanned)).days
                except (ValueError, TypeError):
                    days_since = 999

                if days_since < 7 and isinstance(prev_reality, (int, float)):
                    raw_delta = new_reality - prev_reality
                    if abs(raw_delta) >= drift_threshold:
                        print(f"   [{company_name}] ⚠️ [Drift Diagnostic] Large score change in {days_since}d window: {prev_reality} → {new_reality} (delta {raw_delta}). Opus will be final judge.")
                        reality_results["large_drift_detected"] = True
                        reality_results["drift_delta"] = raw_delta

                        print(f"   [{company_name}] 🔄 [Self-Analysis] Triggered: score delta {raw_delta} within {days_since}d.")
                        sa_start = time.time()
                        self_analysis = run_self_analysis(
                            company_name=company_name,
                            previous_scan_data=previous_context,
                            current_scan_data=reality_results,
                            days_since_last=days_since
                        )
                        if self_analysis:
                            reality_results["self_analysis"] = self_analysis
                            print(f"   [{company_name}] ✅ [Self-Analysis] Complete in {int((time.time() - sa_start) * 1000)}ms → Cause: {self_analysis.get('primary_cause', '?')}")
                        else:
                            print(f"   [{company_name}] ⚠️ [Self-Analysis] No result ({int((time.time() - sa_start) * 1000)}ms)")

            gap = intent_results["score"] - reality_results["score"]
            confidence = reality_results.get("confidence_score", 50)
            washing_flag = reality_results.get("washing_flag", False)
            washing_type = reality_results.get("washing_type", None)
            key_people = reality_results.get("key_people", [])

            # --- LOGIC CONSISTENCY CHECK ---
            if reality_results["score"] >= intent_results["score"]:
                washing_flag = False
                washing_type = None
            if intent_results["score"] < 40:
                washing_flag = False
                washing_type = None

            # --- ARCHETYPE CALCULATION (Reality vs Financial) ---
            fin_score = financial_results.get("financial_index")
            fin_for_archetype = fin_score if fin_score is not None else intent_results["score"]
            if reality_results["score"] >= 50:
                archetype = "Thriving" if fin_for_archetype >= 50 else "Modernizing"
            else:
                archetype = "Cash Cow" if fin_for_archetype >= 50 else "At Risk"

            # 4. SYNTHESIS (Claude Opus 4.6)
            summary_text = "Automated Analysis Complete."
            pitch_text = "Pitch unavailable."

            if claude_client:
                try:
                    intent_narrative = json.dumps(intent_results["breakdown"])
                    verified_links = [l for l in reality_results['links'] if l.get('verified', True)]
                    evidence_list = ', '.join([f"{l.get('title', 'Unknown')}: {l.get('snippet', '')}" for l in verified_links[:20]])
                    knowledge_context = get_evolving_framework()

                    washing_context = ""
                    if washing_flag:
                        washing_context = f"CRITICAL: The forensic scanner DETECTED {washing_type or 'WASHING'}. High ambition narrative but low operational evidence."

                    financial_context = ""
                    if financial_results.get("financial_index") is not None:
                        fi = financial_results
                        findings = fi.get('financial_findings', [])
                        findings_text = '; '.join(
                            f.get('assessment', '') if isinstance(f, dict) else str(f)
                            for f in findings[:5]
                        ) if findings else 'No specific findings'
                        financial_context = f"FINANCIAL HEALTH CONTEXT (do NOT cite numeric scores — the index is displayed separately): {findings_text}. You may reference financial health qualitatively (e.g. 'declining revenue', 'strong profitability') but never mention specific index numbers."

                    prev_synthesis_block = ""
                    if previous_context:
                        prev_summary = previous_context.get("previous_summary", "")
                        prev_findings = previous_context.get("previous_findings", [])
                        prev_evidence = previous_context.get("previous_evidence", [])

                        parts = []
                        if prev_summary:
                            parts.append(f"Previous Summary:\n{prev_summary}")
                        if prev_findings:
                            findings_lines = []
                            for f in prev_findings[:10]:
                                sentiment = f.get("sentiment", "neutral")
                                query = f.get("query", f.get("label", "Unknown"))
                                found = f.get("found", False)
                                findings_lines.append(f"  [{sentiment.upper()}] {query} (found={found})")
                            parts.append(f"Previous Findings:\n" + "\n".join(findings_lines))
                        if prev_evidence:
                            ev_lines = [f"  - {e.get('title', '?')}: {e.get('snippet', '')[:100]}" for e in prev_evidence[:8]]
                            parts.append(f"Previous Evidence Sources:\n" + "\n".join(ev_lines))

                        if parts:
                            prev_synthesis_block = f"""
### PREVIOUS SCAN CONTEXT (for narrative continuity)
{chr(10).join(parts)}
Use this to maintain consistency. If issues from previous scan are still present, note them as persistent.
If new evidence shows improvement, highlight the positive trajectory."""

                    trend_block = ""
                    if previous_context:
                        prev_r = previous_context.get("previous_reality", "N/A")
                        prev_i = previous_context.get("previous_intent", "N/A")
                        prev_date = previous_context.get("last_scanned", "unknown")
                        if isinstance(prev_r, (int, float)):
                            delta = reality_results["score"] - prev_r
                            direction = "IMPROVING" if delta > 5 else ("DECLINING" if delta < -5 else "STABLE")
                        else:
                            direction = "UNKNOWN"

                        trend_points = []
                        scan_history = previous_context.get("scan_history", [])
                        for older in reversed(scan_history):
                            od = older.get("date", "?")
                            oi = older.get("intent_score", "?")
                            or_ = older.get("reality_score", "?")
                            trend_points.append(f"  {od}: Intent {oi}, Reality {or_}")
                        trend_points.append(f"  {prev_date}: Intent {prev_i}, Reality {prev_r}")
                        trend_points.append(f"  NOW: (current scan — scores determined by your independent evaluation)")
                        trend_line = "\n".join(trend_points)

                        trend_block = f"""TREND DATA (RESCAN — {len(trend_points)} data points):
Score trajectory (oldest → newest):
{trend_line}
Overall direction: {direction}
Include the trend trajectory in the summary. Note persistent patterns or turning points."""

                    synthesis_prompt = f"""You are a senior organizational analyst conducting a forensic modernization assessment.
Your task: synthesize scanner evidence into a precise, evidence-grounded diagnosis using the Knowledge Base framework below.

METHODOLOGY: Read ALL evidence first. Score ONLY what evidence supports. Write ONLY what scores justify.
If evidence is thin, say so — a short honest analysis beats a long fabricated one.

### PRIMARY FRAMEWORK (KNOWLEDGE BASE — MANDATORY)
{knowledge_context}

You MUST use the terminology, archetypes, and criteria defined in the Knowledge Base above.
Every claim in the summary MUST trace back to specific evidence AND map to a Knowledge Base dimension.
Do NOT invent categories — use ONLY Knowledge Base vocabulary.
NEVER use internal codes (TAC-xxx, DIM-xxx, etc.) — always write the full concept name.

### SCAN DATA
Target: {company_name}
DIAGNOSIS ARCHETYPE: {archetype.upper()}
Archetype definitions (Reality vs Financial health):
  - THRIVING: High Reality + Strong Financials. Modern operations AND healthy business.
  - MODERNIZING: High Reality + Weak Financials. Strong operations but financial pressure.
  - CASH COW: Low Reality + Strong Financials. Profitable but operationally outdated.
  - AT RISK: Low Reality + Weak Financials. Both operations and finances under pressure.
Summary Confidence: {confidence}/100

Evidence Found: {evidence_list}
Intent Data: {intent_narrative}
{washing_context}
{financial_context}
{trend_block}
{prev_synthesis_block}

### ANALYSIS PROTOCOL

STEP 1 — SCORE EVALUATION (evaluate before writing anything):
Score each Knowledge Base dimension (0-20) for reality_score, based on VERIFIED EVIDENCE ONLY:
  - Operating Model: Evidence of modern practices (DevOps, platform teams, stream-aligned)? Or legacy/waterfall/silos?
  - Strategy: Clear digital/modernization strategy with visible execution? Or vague/absent?
  - Culture: Learning culture, psychological safety, talent signals? Or hierarchy/control/fear?
  - Service Portfolio: Product-led, API-first, composable architecture? Or monolithic/legacy?
  - AI Readiness: Concrete AI/ML adoption with measurable outcomes? Or just buzzwords/pilot purgatory?

Scoring rules:
  - No evidence for a dimension = score 10 (neutral). Never score generously without evidence.
  - Evidence of aspiration (website claims, press releases) without execution evidence = score 8-12, not higher.
  - Sum the 5 dimensions → 'reality_score' (0-100).

For 'intent_score' (0-100): How ambitious is their narrative vs. what's typical?
Score based on the Intent Data provided above.

STEP 2 — SUMMARY (180-220 words):
Write a diagnostic narrative that reads like an expert assessment, not a template:
  - First sentence: State the archetype ({archetype}) and what defines this company's position.
  - Body: Map 3-4 key findings to specific KB dimensions. For each finding, connect the evidence
    to the KB pattern it reveals (e.g., "Job postings emphasize hierarchical reporting lines,
    characteristic of Digital Taylorism in the Operating Model dimension").
  - Use KB terminology naturally: "Digital Taylorism", "Flow Efficiency", "Pilot Purgatory",
    "Modernization Washing" — but only where the evidence supports the pattern.
  - If confidence < 50, state it: "Limited public evidence constrains this assessment."
  - If washing detected, name it: "{washing_type or 'Modernization Washing'}" with the specific gap.
  - Do NOT cite numeric scores in the text — scores display separately in the UI.
  - Do NOT fabricate findings. If evidence is sparse, acknowledge gaps rather than inventing patterns.
  - Use ONLY the four archetype labels: Thriving, Modernizing, Cash Cow, At Risk.

STEP 3 — PITCH (90-110 words):
A direct cold-outreach message to a C-level executive at {company_name}:
  - Open with a specific insight from the analysis that would make them stop and read.
  - Derive exactly 2 CONCRETE PROPOSALS targeting the company's weakest KB dimensions.
    Each proposal must name the dimension and suggest a specific transformation direction
    (e.g., "Shift from Digital Taylorism toward Flow Efficiency by restructuring around
    stream-aligned teams with end-to-end ownership").
  - The proposals must feel specific to THIS company's situation, not generic consulting templates.
  - Do NOT use "Forensic Modernization Audit" or any generic service names.
  - Tone: Professional, provocative, insight-led. The recipient should feel understood, not sold to.

### GUARDRAILS
- NEVER fabricate evidence, people, events, partnerships, or metrics not present in the scan data.
- NEVER invent temporal claims ("recently announced", "just launched", "this quarter").
- If a KB dimension has no supporting evidence, score it neutrally and acknowledge the gap — do not speculate.
- Every factual claim in the summary must be traceable to a specific piece of evidence above.

### OUTPUT
Respond with valid JSON only. Four fields required:
{{"summary": "...", "pitch": "...", "reality_score": <int 0-100>, "intent_score": <int 0-100>}}"""

                    # --- Synthesis with fallback chain: Opus 4.6 → Gemini 3.1 Pro → Gemini 3 Pro → Sonnet 4.6 ---
                    response_text = ""
                    try:
                        print(f"   [{company_name}] 🔄 [Synthesis] Attempting claude-opus-4-6...")
                        synth_start = time.time()
                        resp = claude_client.messages.create(
                            model="claude-opus-4-6",
                            max_tokens=1024,
                            messages=[{"role": "user", "content": synthesis_prompt}]
                        )
                        response_text = resp.content[0].text if resp.content else ""
                        print(f"   [{company_name}] ✅ [Synthesis] claude-opus-4-6 succeeded in {int((time.time() - synth_start) * 1000)}ms")
                    except Exception as opus_err:
                        print(f"   [{company_name}] ❌ [Synthesis] Opus 4.6 failed: {opus_err}")

                        # Fallback 1: Gemini 3.1 Pro Preview (strongest Google model, MEDIUM thinking)
                        gemini_key = os.environ.get("GEMINI_API_KEY") or os.environ.get("API_KEY")
                        if gemini_key:
                            try:
                                print(f"   [{company_name}] 🔄 [Synthesis] Falling back to Gemini 3.1 Pro Preview...")
                                g_start = time.time()
                                gemini_client = genai.Client(api_key=gemini_key)
                                gemini_resp = gemini_client.models.generate_content(
                                    model="gemini-3.1-pro-preview",
                                    contents=synthesis_prompt,
                                    config=types.GenerateContentConfig(
                                        temperature=0.5,
                                        thinking_config=types.ThinkingConfig(thinking_level="MEDIUM"),
                                        max_output_tokens=2048
                                    )
                                )
                                response_text = gemini_resp.text or ""
                                print(f"   [{company_name}] ✅ [Synthesis] Gemini 3.1 Pro Preview succeeded in {int((time.time() - g_start) * 1000)}ms")
                            except Exception as gemini31_err:
                                print(f"   [{company_name}] ❌ [Synthesis] Gemini 3.1 Pro Preview failed: {gemini31_err}")

                        # Fallback 2: Gemini 3 Pro Preview
                        if not response_text and gemini_key:
                            try:
                                print(f"   [{company_name}] 🔄 [Synthesis] Falling back to Gemini 3 Pro Preview...")
                                g2_start = time.time()
                                gemini_client = genai.Client(api_key=gemini_key)
                                gemini_resp = gemini_client.models.generate_content(
                                    model="gemini-3-pro-preview",
                                    contents=synthesis_prompt,
                                    config=types.GenerateContentConfig(
                                        temperature=1.0,  # Gemini 3 requires temp=1.0
                                        thinking_config=types.ThinkingConfig(thinking_level="LOW"),
                                        max_output_tokens=2048
                                    )
                                )
                                response_text = gemini_resp.text or ""
                                print(f"   [{company_name}] ✅ [Synthesis] Gemini 3 Pro Preview succeeded in {int((time.time() - g2_start) * 1000)}ms")
                            except Exception as gemini3_err:
                                print(f"   [{company_name}] ❌ [Synthesis] Gemini 3 Pro Preview failed: {gemini3_err}")

                        # Fallback 3: Gemini 2.5 Pro (separate rate limits from 3.x family)
                        if not response_text and gemini_key:
                            try:
                                print(f"   [{company_name}] 🔄 [Synthesis] Falling back to Gemini 2.5 Pro...")
                                g3_start = time.time()
                                gemini_client = genai.Client(api_key=gemini_key)
                                gemini_resp = gemini_client.models.generate_content(
                                    model="gemini-2.5-pro",
                                    contents=synthesis_prompt,
                                    config=types.GenerateContentConfig(
                                        temperature=0.0,
                                        max_output_tokens=2048
                                    )
                                )
                                response_text = gemini_resp.text or ""
                                print(f"   [{company_name}] ✅ [Synthesis] Gemini 2.5 Pro succeeded in {int((time.time() - g3_start) * 1000)}ms")
                            except Exception as gemini25_err:
                                print(f"   [{company_name}] ❌ [Synthesis] All models failed. Last error: {gemini25_err}")
                    if response_text:
                        payload = extract_json_from_text(response_text)
                        if payload:
                             json_data = json.loads(payload)
                             summary_text = json_data.get("summary", summary_text)
                             pitch_text = json_data.get("pitch", pitch_text) or pitch_text

                             # --- OPUS SCORE VERIFICATION ---
                             opus_reality = json_data.get("reality_score")
                             opus_intent = json_data.get("intent_score")
                             scanner2_reality = reality_results["score"]
                             scanner_intent = intent_results["score"]

                             if opus_reality is not None and isinstance(opus_reality, (int, float)):
                                 opus_reality = max(0, min(100, int(opus_reality)))
                                 delta_r = abs(opus_reality - scanner2_reality)
                                 reality_results["scanner2_reality_score"] = scanner2_reality
                                 reality_results["opus_reality_score"] = opus_reality

                                 if delta_r <= 5:
                                     print(f"   [{company_name}] ✅ [Score Verify] Reality: Scanner 2 {scanner2_reality} ≈ Opus {opus_reality} (delta {delta_r}). Final score: {opus_reality}")
                                 else:
                                     print(f"   [{company_name}] ⚖️ [Score Verify] Reality: Scanner 2 {scanner2_reality} vs Opus {opus_reality} (delta {delta_r}). Opus is final judge → {opus_reality}")

                                 reality_results["score"] = opus_reality
                                 reality_results["score_verification"] = "opus_final_judge"

                                 if reality_results["score"] != scanner2_reality:
                                     gap = intent_results["score"] - reality_results["score"]
                                     if reality_results["score"] >= intent_results["score"]:
                                         washing_flag = False
                                         washing_type = None

                             # Intent score is IMMUTABLE — Scanner 1's website analysis is the
                             # sole source of intent. Opus may disagree, but overriding intent
                             # would destroy the integrity delta (intent vs reality gap).
                             # Log Opus's opinion for diagnostics only.
                             if opus_intent is not None and isinstance(opus_intent, (int, float)):
                                 opus_intent = max(0, min(100, int(opus_intent)))
                                 delta_i = abs(opus_intent - scanner_intent)
                                 if delta_i <= 5:
                                     print(f"   [{company_name}] ✅ [Score Verify] Intent: Scanner 1 {scanner_intent} ≈ Opus {opus_intent} (delta {delta_i}). Keeping Scanner 1 score.")
                                 else:
                                     print(f"   [{company_name}] ℹ️ [Score Verify] Intent: Scanner 1 {scanner_intent} vs Opus {opus_intent} (delta {delta_i}). Keeping Scanner 1 score (immutable).")
                                     gap = intent_results["score"] - reality_results["score"]

                        else:
                             summary_text = response_text
                except Exception as e:
                    print(f"   [{company_name}] ❌ [Synthesis Error] {e}")

            # 5. ASSEMBLE DATA
            result_id = str(uuid.uuid4())
            timestamp = datetime.utcnow().isoformat()

            batch_history = []
            if existing_company_data:
                batch_history = existing_company_data.get("history", [])
                batch_history.append({
                    "date": existing_company_data.get("lastUpdated"),
                    "gap": existing_company_data.get("reality_gap", 0),
                    "intent_score": existing_company_data.get("intent_score"),
                    "reality_score": existing_company_data.get("reality_score"),
                    "confidence_score": existing_company_data.get("confidence_score"),
                    "washing_type": existing_company_data.get("washing_type"),
                    "summary_text": (existing_company_data.get("summary_text", "") or "")[:2500],
                    "findings": existing_company_data.get("reality_checklist", [])[:10],
                    "evidence": existing_company_data.get("evidence_links", [])[:8],
                })
                if len(batch_history) > 12: batch_history = batch_history[-12:]

            meta = get_company_metadata(company_name) or company
            company_domain = meta.get("domain", "Other")
            company_revenue = meta.get("revenue_avg_3yr", None)
            company_employees = meta.get("employees", None)
            company_ownership = meta.get("ownership", None)
            company_hq = meta.get("hq_country", None)

            # --- FINAL ARCHETYPE (computed from finalized scores) ---
            fin_score = financial_results.get("financial_index")
            fin_for_archetype = fin_score if fin_score is not None else intent_results["score"]
            if reality_results["score"] >= 50:
                archetype = "Thriving" if fin_for_archetype >= 50 else "Modernizing"
            else:
                archetype = "Cash Cow" if fin_for_archetype >= 50 else "At Risk"

            full_data = {
                "id": result_id,
                "companyName": company_name,
                "companyUrl": company["url"],
                "domain": company_domain,
                "revenue_avg_3yr": company_revenue,
                "employees": company_employees,
                "ownership": company_ownership,
                "hq_country": company_hq,
                "intent_score": intent_results["score"],
                "reality_score": reality_results["score"],
                "reality_gap": gap,
                "confidence_score": confidence,
                "archetype": archetype,
                "washing_flag": washing_flag,
                "washing_type": washing_type,
                "intent_breakdown": intent_results["breakdown"],
                "metrics": intent_results.get("metrics", {}),
                "reality_checklist": reality_results["checklist"],
                "evidence_links": reality_results["links"],
                "key_people": key_people,
                "summary_text": summary_text,
                "pitch_text": pitch_text,
                "lastUpdated": timestamp,
                "history": batch_history,
                "is_simulated": False,
                "drift_guard_applied": reality_results.get("drift_guard_applied", False),
                "raw_score_before_dampening": reality_results.get("raw_score_before_dampening"),
                "self_analysis": reality_results.get("self_analysis"),
                "financial_index": financial_results.get("financial_index"),
                "financial_sub_scores": financial_results.get("sub_scores", {}),
                "financial_findings": financial_results.get("financial_findings", []),
                "financial_confidence": financial_results.get("financial_confidence", 0)
            }

            # --- REALITY CHECK: Hallucination Detection ---
            from reality_check import validate_summary, validate_pitch
            summary_check = validate_summary(
                summary_text=summary_text,
                company_name=company_name,
                key_people=key_people,
                evidence_links=reality_results.get("links", []),
                confidence_score=confidence,
            )
            pitch_check = validate_pitch(pitch_text=pitch_text, company_name=company_name)

            full_data["hallucination_flags"] = summary_check["flags"] + pitch_check.get("flags", [])
            full_data["hallucination_risk"] = summary_check["risk"]
            full_data["hallucination_score"] = summary_check["score"]

            if summary_check["risk"] == "high":
                print(f"   [{company_name}] ⚠️ [Reality Check] HIGH risk (score {summary_check['score']}/100)! Flags: {summary_check['flags']}")
                full_data["summary_text"] = f"[FLAGGED] Summary blocked by Reality Check (score: {summary_check['score']}/100). Flags: {'; '.join(summary_check['flags'])}. Rescan required."
                full_data["pitch_text"] = "Pitch unavailable — summary flagged. Rescan required."
            elif summary_check["risk"] == "medium":
                print(f"   [{company_name}] ⚠️ [Reality Check] MEDIUM risk (score {summary_check['score']}/100). Flags: {summary_check['flags']}")
            else:
                print(f"   [{company_name}] ✅ [Reality Check] Summary OK (score {summary_check['score']}/100)")

            # 6. SAVE INDIVIDUAL COMPANY (thread-safe — unique path per company)
            # Use timestamp conflict resolution: skip save if a manual scan happened after batch started
            saved_url = storage.save_company_data(
                company_name, full_data,
                skip_if_newer_than=batch_start_iso if batch_progress is not None else None
            )

            summary_entry = {
                "id": result_id,
                "companyName": company_name,
                "domain": company_domain,
                "revenue_avg_3yr": company_revenue,
                "employees": company_employees,
                "ownership": company_ownership,
                "hq_country": company_hq,
                "confidence_score": confidence,
                "intent_score": intent_results["score"],
                "reality_score": reality_results["score"],
                "reality_gap": gap,
                "lastUpdated": timestamp,
                "detailsUrl": saved_url,
                "financial_index": financial_results.get("financial_index"),
                "archetype": archetype
            }

            # 7. UPDATE SHARED INDEX (needs lock — multiple threads write here)
            with index_lock:
                # Only update index entry if we actually saved (conflict resolution may skip)
                if saved_url is not None:
                    batch_summary_list[:] = [x for x in batch_summary_list if x['companyName'] != company_name]
                    batch_summary_list.append(summary_entry)
                    storage.save_global_index_direct(list(batch_summary_list))
                completed_count[0] += 1
                if batch_progress is not None:
                    batch_progress["current"] = completed_count[0]
                if progress_callback:
                    progress_callback(list(batch_summary_list))

            elapsed = round(time.time() - company_start, 1)
            print(f"✅ [{i+1}/{total_count}] {company_name} done in {elapsed}s | Intent: {intent_results['score']} | Reality: {reality_results['score']} | ({completed_count[0]}/{total_count} complete)")

        except Exception as e:
            print(f"   ❌ [CRITICAL BATCH ERROR] Failed to process {company_name}: {e}")
            with index_lock:
                completed_count[0] += 1
                if batch_progress is not None:
                    batch_progress["current"] = completed_count[0]
                    batch_progress["failed"].append(company_name)

    # --- PARALLEL EXECUTION ---
    with concurrent.futures.ThreadPoolExecutor(max_workers=max_workers) as executor:
        futures = {}
        for i, company in enumerate(target_list):
            if stop_event and stop_event.is_set():
                print(f"   [Batch] Stop requested — not submitting remaining companies.")
                break
            future = executor.submit(process_company, i, company)
            futures[future] = company['name']
            time.sleep(0.5)  # Stagger starts to avoid simultaneous Gemini hits

        # Wait for all to complete and catch any unhandled errors
        for future in concurrent.futures.as_completed(futures):
            try:
                future.result()
            except Exception as e:
                print(f"   ❌ [CRITICAL] Unhandled error for {futures[future]}: {e}")

    # Final save and summary
    total_elapsed = round((time.time() - batch_start) / 60, 1)
    storage.save_global_index_direct(batch_summary_list)
    if progress_callback: progress_callback(batch_summary_list)
    print(f"\n✅ === BATCH COMPLETE: {completed_count[0]}/{total_count} companies in {total_elapsed} minutes ({len(batch_summary_list)} index entries) ===")
