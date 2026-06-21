
import os
import re
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
from scanner_two import x_ray_search, extract_json_from_text, run_self_analysis, financial_health_search, migrate_financial_sub_scores, _validate_key_people
from knowledge_base import get_evolving_framework
from storage import BlobStorage
from reality_check import validate_summary, validate_pitch, ai_fact_check_summary, purge_false_evidence, _call_openai_text
from general_summary import generate_general_summary, fact_check_general_summary
from source_quality import manage_evidence_sources, is_foresight_context_eligible_source
from prompts import PROMPT_VERSION, build_shared_guardrails
import anthropic


def _strip_tac_codes(text: str) -> str:
    """Remove legacy TAC-xxx / DIM-xxx internal codes from summary text."""
    if not text:
        return text
    text = re.sub(r'\s*\(\s*(?:TAC|DIM)-\d{3,4}\s*\)', '', text)
    text = re.sub(r'\b(?:TAC|DIM)-\d{3,4}\b', '', text)
    text = re.sub(r'\s*\(\s*\)', '', text)
    return text.strip()


def _shared_prompt_guardrails(include_diagnostic: bool = True, include_foresight: bool = True, include_claim_tier: bool = False) -> str:
    """Shared evidence/diagnosis/foresight guardrails injected into seed_manager prompts."""
    return build_shared_guardrails(include_diagnostic=include_diagnostic, include_foresight=include_foresight, include_claim_tier=include_claim_tier)

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
        
        fallback_guardrails = _shared_prompt_guardrails(include_diagnostic=False, include_foresight=False)
        fallback_prompt = f"""
                Write a cautious, clearly labelled fallback context summary for '{company_name}' (Domain: {base_url}).

                IMPORTANT: This is fallback context because scraping failed. Do not invent facts. Use only generally phrased, non-specific context unless you are certain. Clearly avoid presenting simulated content as verified evidence.

                SHARED FACT-DISCIPLINE GUARDRAILS:
                {fallback_guardrails}

                Cover the types of information typically found on an 'About Us', 'Strategy', or 'Investor Relations' page, but avoid unsupported specifics.
                Include specific details on:
                1. Strategic Vision & Mission
                2. Organizational Structure (e.g. Teams, Hierarchies, Agile models)
                3. Business Model & Service Portfolio
                4. Technology Approach (AI, Cloud, Digital Transformation)

                Output plain text only.
                """
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
            print(f"   [Scanner 1] ❌ Sonnet fallback failed: {e2}")
        try:
            text = _call_openai_text(fallback_prompt, model="gpt-5.5", max_output_tokens=2048, timeout=45)
            if text:
                print(f"   [Scanner 1] ✅ AI Context via GPT 5.5 for {company_name}.")
                return f"--- AI GENERATED CONTEXT FOR {company_name} (GPT FALLBACK — NOT VERIFIED EVIDENCE) ---\n{text}"
        except Exception as e3:
            print(f"   [Scanner 1] ❌ GPT fallback also failed: {e3}")

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
        COMPANY_BUDGET = 660  # seconds per company (scrape + reality + financial + synthesis + GS + save)
        company_scan_quality = "complete"
        company_name = company['name']
        if batch_progress is not None:
            batch_progress["current_name"] = company_name
        print(f"\n🚀 [{i+1}/{total_count}] Processing: {company_name} (budget: {COMPANY_BUDGET}s)...")

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
                    model_id=None, intent_score=intent_results["score"],
                    previous_context=previous_context, max_research_loops=2,
                    subsidiaries=company.get("subsidiaries"),
                    time_budget=340,
                    employees=company.get("employees"),
                    batch_mode=True
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
                            "sub_scores": migrate_financial_sub_scores(existing_company_data.get("financial_sub_scores", {})),
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

            # --- KEY PEOPLE NAME VALIDATION ---
            key_people = _validate_key_people(key_people, company_name, employees=company.get("employees"))

            stakeholder_enrichment_skipped = "moved_to_buying_insight"
            if key_people:
                print(f"   [{company_name}] ⏭ [Stakeholder Intel] Live enrichment skipped for {len(key_people)} stakeholder(s) — moved to Buying Insight")
                for person in key_people:
                    if isinstance(person, dict):
                        person.setdefault("stakeholder_intelligence_mode", "lightweight_scan_record")

                # Keep a cheap compatibility filter for old enriched records.
                departed = [p for p in key_people if p.get("linkedin_insights", {}).get("still_at_company") is False]
                if departed:
                    departed_names = ", ".join(p.get("name", "?") for p in departed)
                    print(f"   [{company_name}] 🚫 [Stakeholder Filter] Removing {len(departed)} departed stakeholder(s): {departed_names}")
                    key_people = [p for p in key_people if p.get("linkedin_insights", {}).get("still_at_company") is not False]

            # --- LOGIC CONSISTENCY CHECK ---
            if reality_results["score"] >= intent_results["score"]:
                washing_flag = False
                washing_type = None
            if intent_results["score"] < 40:
                washing_flag = False
                washing_type = None

            # --- EARLY DOMAIN RESOLUTION (needed for foresight before synthesis) ---
            meta = get_company_metadata(company_name) or company
            company_domain = meta.get("domain", "Other")
            if company_domain == "Other":
                from domain_classifier import infer_domain
                _cached = existing_company_data.get("domain") if existing_company_data else None
                company_domain = infer_domain(company_name, company.get("url", ""), reality_results, financial_results, cached_domain=_cached)

            # --- ARCHETYPE CALCULATION (Reality vs Financial) ---
            fin_score = financial_results.get("financial_index")
            fin_for_archetype = fin_score if fin_score is not None else intent_results["score"]
            if reality_results["score"] >= 50:
                archetype = "Thriving" if fin_for_archetype >= 50 else "Modernizing"
            else:
                archetype = "Cash Cow" if fin_for_archetype >= 50 else "At Risk"

            # --- STEP 2.7: STRATEGIC FORESIGHT ---
            foresight_data = None
            foresight_links = []
            try:
                from strategic_foresight import (
                    generate_foresight, validate_foresight_urls,
                    extract_foresight_links, retry_failed_sources, enforce_source_quality_gate,
                )
                foresight_data = generate_foresight(
                    company_name=company_name,
                    domain=company_domain,
                    archetype=archetype,
                    reality_score=reality_results["score"],
                    financial_index=financial_results.get("financial_index"),
                    top_findings=reality_results.get("checklist", [])[:8],
                )
                if foresight_data:
                    n_forces = len(foresight_data.get('dimension_forces', []))
                    is_kb_first = foresight_data.get("kb_first", False)
                    path_label = "KB-first" if is_kb_first else "search"
                    print(f"   [{company_name}] 🔮 [Foresight] Complete: {n_forces} forces via {path_label} path")

                    if is_kb_first:
                        foresight_data["insufficient_evidence"] = False
                        foresight_links = extract_foresight_links(foresight_data, company_name=company_name, domain=company_domain)
                        print(f"   [{company_name}] 📚 [Foresight KB-First] Skipped URL verify + source retry (pre-validated)")
                    else:
                        foresight_data, fs_verified, fs_dead = validate_foresight_urls(foresight_data)
                        if fs_verified == 0 or (fs_dead > 0 and fs_verified < n_forces):
                            foresight_data, newly_verified = retry_failed_sources(foresight_data, domain=company_domain)
                            fs_verified += newly_verified
                        try:
                            from source_claim_validator import validate_foresight_claims
                            validate_foresight_claims(
                                foresight_data,
                                max_checks=6,
                                company_name=company_name,
                                company_domain=company_domain,
                            )
                        except Exception as scv_err:
                            print(f"   [{company_name}] ⚠️ [Foresight Source-Claim] Validation skipped: {scv_err}")
                        foresight_data, passes_gate = enforce_source_quality_gate(foresight_data)
                        foresight_links = extract_foresight_links(foresight_data, company_name=company_name, domain=company_domain)
                        if not passes_gate:
                            print(f"   [{company_name}] ⚠️ [Foresight] Insufficient evidence — foresight flagged as low-trust")

                    # Preserve only links satisfying the current foresight contract.
                    if existing_company_data and existing_company_data.get("foresight_links"):
                        db_fl = existing_company_data["foresight_links"]
                        new_urls = {l.get("url") for l in foresight_links if l.get("url")}
                        for db_link in db_fl:
                            if db_link.get("url") and db_link["url"] not in new_urls:
                                if is_foresight_context_eligible_source(db_link):
                                    foresight_links.append(db_link)
                                    new_urls.add(db_link["url"])
                        print(f"   [{company_name}] 📎 [Foresight Links] Merged: {len(foresight_links)} total ({len(db_fl)} from DB)")
                else:
                    print(f"   [{company_name}] ⚠️ [Foresight] Returned no data")
            except Exception as foresight_err:
                print(f"   [{company_name}] ⚠️ [Foresight] Failed (non-fatal): {foresight_err}")

            # Defensive guard: ensure foresight_links is always a plain list of dicts
            # (handles legacy corrupt strings, tuples, or partial merge failures)
            if not isinstance(foresight_links, list):
                foresight_links = []
            else:
                foresight_links = [l for l in foresight_links if isinstance(l, dict)]

            # 4. SYNTHESIS (Claude Opus 4.8 / GPT / Sonnet)
            summary_text = "Automated Analysis Complete."
            pitch_text = "Pitch unavailable."
            why_proposals = []
            what_proposals = []

            if claude_client:
                try:
                    intent_narrative = json.dumps(intent_results["breakdown"])
                    verified_links = [l for l in reality_results['links'] if l.get('verified', True)]
                    evidence_list = '\n'.join([
                        f"[{l.get('claim_tier','D')}]{'['+l.get('entity_scope','')+']' if l.get('entity_scope','unknown') != 'unknown' else ''} {l.get('title','Unknown')}: {l.get('snippet','')}"
                        for l in verified_links[:20]
                    ])
                    knowledge_context = get_evolving_framework()

                    washing_context = ""
                    if washing_flag:
                        washing_context = f"CRITICAL: The forensic scanner DETECTED {washing_type or 'WASHING'}. High ambition narrative but low operational evidence."

                    financial_context = ""
                    if financial_results.get("financial_index") is not None:
                        fi = financial_results
                        findings = fi.get('financial_findings', [])
                        findings_text = '; '.join(
                            f.get('assessment', '') if isinstance(f, dict) else ''
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

                    # Foresight context (only when foresight data was generated)
                    foresight_context = ""
                    if foresight_data:
                        from strategic_foresight import format_foresight_for_synthesis
                        foresight_context = format_foresight_for_synthesis(foresight_data, company_name, company_domain)

                    # Build constraint-driven context for pitch and proposals
                    _neg_items = [item for item in reality_results.get("checklist", []) if item.get("sentiment") == "negative"]
                    negative_summary = "\n".join([f"- {item.get('query', '')}: {item.get('snippet', 'No detail')}" for item in _neg_items[:6]])
                    negative_findings_context = f"\nKey Negative Findings (Scanner 2):\n{negative_summary}" if negative_summary else ""

                    seed_synthesis_guardrails = _shared_prompt_guardrails(include_diagnostic=True, include_foresight=True, include_claim_tier=True)
                    synthesis_prompt = f"""You are a senior organizational analyst conducting a forensic modernization assessment.
You are conservative and evidence-bound. You would rather say "insufficient evidence" than risk a wrong claim. Your credibility depends on never overstating.
Your task: synthesize scanner evidence into a precise, evidence-grounded diagnosis using the Knowledge Base framework below.

### SHARED EVIDENCE / DIAGNOSIS / FORESIGHT GUARDRAILS
{seed_synthesis_guardrails}

METHODOLOGY: Read ALL evidence first. Score ONLY what evidence supports. Write ONLY what scores justify.
If evidence is thin, say so — a short honest analysis beats a long fabricated one.

### ABSOLUTE RULES (repeated throughout for emphasis)
1. NEVER fabricate findings — if evidence is thin, SAY SO.
2. NEVER invent organizational or people problems (leadership gaps, succession issues, morale claims, unfilled roles).
3. Every claim must trace to specific evidence in the SCAN DATA below. No evidence = no claim.
4. Do NOT extrapolate small signals into sweeping assessments (e.g., a Jira mention in a job ad does NOT reveal corporate strategy).

### PRIMARY FRAMEWORK (KNOWLEDGE BASE — MANDATORY)
{knowledge_context}

You MUST use the terminology, archetypes, and criteria defined in the Knowledge Base above.
Every claim in the summary MUST trace back to specific evidence AND map to a Knowledge Base dimension.
Do NOT invent categories — use ONLY Knowledge Base vocabulary.
NEVER use internal codes (TAC-xxx, DIM-xxx, etc.) — always write the full concept name.

### SCAN DATA
Target: {company_name}
Financial Health Index: {fin_for_archetype} ({"actual financial index" if fin_score is not None else "fallback: using intent score"})
Summary Confidence: {confidence}/100

Evidence Found: {evidence_list}
Intent Data: {intent_narrative}
{washing_context}
{financial_context}
{trend_block}
{foresight_context}
{negative_findings_context}
{prev_synthesis_block}

### ANALYSIS PROTOCOL

STEP 1 — REALITY SCORE (evaluate before writing anything):
Score each Knowledge Base dimension (0-20) based on VERIFIED EVIDENCE ONLY:
  - Operating Model: Evidence of modern practices (DevOps, platform teams, stream-aligned)? Or legacy/waterfall/silos?
  - Strategy: Clear digital/modernization strategy with visible execution? Or vague/absent?
  - Culture: Learning culture, psychological safety? Evidence of open communication or blameless post-mortems? Or hierarchy/control/fear?
  - Service Portfolio: Product-led, API-first, composable architecture? Or monolithic/legacy?
  - AI Readiness: Concrete AI/ML adoption with measurable outcomes? Or just buzzwords/pilot purgatory?

Scoring rules:
  - No evidence for a dimension = score 10 (neutral). Never score generously without evidence.
  - Evidence of aspiration (website claims, press releases) without execution evidence = score 8-12, not higher.
  - Sum the 5 dimensions → 'reality_score' (0-100).
  - Do NOT evaluate intent_score or financial_index — those are immutable pipeline outputs.

STEP 2 — ARCHETYPE DERIVATION (mechanical, from scores):
Use YOUR reality_score + Financial Health Index ({fin_for_archetype}):
  - reality_score >= 50 AND Financial Health Index >= 50 → "Thriving"
  - reality_score >= 50 AND Financial Health Index < 50 → "Modernizing"
  - reality_score < 50 AND Financial Health Index >= 50 → "Cash Cow"
  - reality_score < 50 AND Financial Health Index < 50 → "At Risk"
This is a mechanical derivation — do not override based on narrative interpretation.

STEP 3 — AI DIAGNOSIS SUMMARY:
Write a diagnostic narrative that reads like an expert assessment, not a template:
  - Structure: exactly 3-4 core paragraphs, each covering a distinct KB dimension (e.g. overall position
    & operating model / strategy & culture / service portfolio & AI readiness / financial context).
    Separate paragraphs with \\n\\n in the JSON string — this is required for rendering.
  - If SCAN DATA includes "AI DIAGNOSIS FORESIGHT INPUTS", add exactly one final foresight implication paragraph of 3-5 sentences after the core diagnosis.
  - The final foresight paragraph must phrase forces as external pressure or market context, not as company facts, unless company-specific evidence above supports the fact.
  - If no qualified foresight inputs exist, do not force a future paragraph.
  - Target length: 250-375 words without foresight inputs; 325-475 words when the optional foresight paragraph is present.
  - First sentence: State the archetype and what defines this company's position.
  - Body: Map 3-4 key findings to specific KB dimensions. For each finding, connect the evidence
    to the KB pattern it reveals (e.g., "Job postings emphasize hierarchical reporting lines,
    characteristic of Digital Taylorism in the Operating Model dimension").
  - Use KB terminology naturally: "Digital Taylorism", "Flow Efficiency", "Pilot Purgatory",
    "Modernization Washing" — but only where the evidence supports the pattern.
  - If confidence < 50, state it: "Limited public evidence constrains this assessment."
  - If washing detected, name it: "{washing_type or 'Modernization Washing'}" with the specific gap.
  - Do NOT cite numeric scores in the text — scores display separately in the UI.
  - Do NOT fabricate findings. If evidence is sparse, acknowledge gaps rather than inventing patterns.
  - TEMPORAL PRECISION: When evidence contains the same metric for different periods (e.g. Q1 vs full-year, 2024 vs 2025), always use the most recent FULL-YEAR / ANNUAL figure — not interim or quarterly snapshots. If only a quarterly figure is available, label it explicitly (e.g. "as of Q1 2025"). Never present a quarterly number as if it were the annual result.
  - ORGANIZATIONAL CLAIMS PROHIBITION: NEVER claim or imply leadership gaps, unfilled executive roles, succession planning issues, management vacancies, or organizational restructuring unless the evidence EXPLICITLY states this. The absence of a person in scanner results does NOT mean the role is vacant — it means the scanner didn't find them. Do NOT invent risk factors about people, roles, or organizational structure to add analytical depth. If the evidence doesn't mention an organizational problem, the problem does not exist for this analysis.
  - Use ONLY the four archetype labels: Thriving, Modernizing, Cash Cow, At Risk.

STEP 4 — PITCH (90-110 words):
A direct cold-outreach message to a C-level executive at {company_name}:
  - Open with a specific insight from the analysis that would make them stop and read.
  - Derive 1-2 CONCRETE PROPOSALS targeting the company's weakest KB dimensions.
    Each proposal must name the dimension and suggest a specific transformation direction
    (e.g., "Shift from Digital Taylorism toward Flow Efficiency by restructuring around
    stream-aligned teams with end-to-end ownership").
  - The proposals must feel specific to THIS company's situation, not generic consulting templates.
  - Do NOT use "Forensic Modernization Audit" or any generic service names.
  - Tone: Professional, direct, evidence-led. Every claim in the pitch must be verifiable from the scan data. The recipient should feel understood, not sold to.
  - Structure as exactly 3 short paragraphs separated by \\n\\n (opening hook / proposals / closing CTA).

STEP 5 — IMPACT PROPOSALS (derived from Steps 1-4):
Generate 1-2 WHY statements and 1-2 WHAT statements (matching count).
If evidence strongly supports 2 distinct weaknesses, produce 2 pairs. If evidence is limited, produce 1 pair. One strong evidence-based proposal beats two fabricated ones.

WHY (Desired Impact) — the strategic reason to act:
  - Each WHY must connect a specific weakness found in Step 1 to a business-level consequence the C-suite cares about.
  - Frame as: "What happens if this stays unfixed?" → strategic risk or missed opportunity.
  - Tone: Executive-level, outcome-oriented. Not technical jargon.
  - Length: 2-3 sentences each.
  - Example: "Reduce time-to-market for digital services from months to weeks by dissolving project-based handoffs that currently bottleneck every release."

WHAT (Desired Effect) — the measurable business outcome:
  - Each WHAT must propose a concrete, observable change that would prove progress on the corresponding WHY.
  - Frame as: "What would be different in 12 months if we succeed?"
  - Must be specific enough to verify, but not a made-up KPI number.
  - Tone: Tangible, verifiable, grounded in this company's context.
  - Length: 2-3 sentences each.
  - Example: "Cross-functional teams own services end-to-end, eliminating the current approval chain that delays every deployment."

Rules:
  - WHY1 pairs with WHAT1 (same theme). If 2 pairs: WHY2 pairs with WHAT2 (different theme).
  - Draw ONLY from the weakest KB dimensions scored in Step 1 that have ACTUAL EVIDENCE.
  - Never generic — each must reference something specific from the evidence or diagnosis.
  - These are proposals, not promises — frame as "potential impact" and "potential effect".

### GUARDRAILS
- NEVER fabricate evidence, people, events, partnerships, or metrics not present in the scan data.
- NEVER invent temporal claims ("recently announced", "just launched", "this quarter").
- NEVER extrapolate small signals into sweeping assessments (e.g., a tool mentioned in a job ad does NOT reveal corporate strategy).
- If a KB dimension has no supporting evidence, score it neutrally and acknowledge the gap — do not speculate.
- Every factual claim in the summary must be traceable to a specific piece of evidence above.

### OUTPUT
Respond with valid JSON only. Six fields required:
{{"summary": "...", "pitch": "...", "reality_score": <int 0-100>, "archetype": "<Thriving|Modernizing|Cash Cow|At Risk>", "why_proposals": ["...", "..."], "what_proposals": ["...", "..."]}}
Note: the summary value must contain \\n\\n between paragraphs (3-4 core paragraphs plus one optional final foresight implication paragraph). The pitch value must also contain \\n\\n between its 3 paragraphs.
Note: why_proposals and what_proposals are arrays of 1-2 strings each. WHY1 pairs with WHAT1. If 2 pairs: WHY2 pairs with WHAT2."""

                    # --- Synthesis with fallback chain: Opus 4.8 → GPT 5.5 → Sonnet 4.6 ---
                    response_text = ""
                    _seed_elapsed = time.time() - company_start
                    _seed_synth_remaining = COMPANY_BUDGET - _seed_elapsed
                    _seed_chain_budget = min(180, max(60, int(_seed_synth_remaining) - 60))
                    _SEED_SYNTH_TIMEOUT = max(30, _seed_chain_budget // 3)
                    print(f"   [{company_name}] [Synthesis] Elapsed: {int(_seed_elapsed)}s | chain budget: {_seed_chain_budget}s | per-attempt: {_SEED_SYNTH_TIMEOUT}s")

                    if _seed_synth_remaining < 90:
                        company_scan_quality = "partial_synthesis"
                        print(f"   [{company_name}] ⏭ [Synthesis] Budget < 90s — skipping synthesis chain")
                    else:
                        # Primary: Opus 4.8 with Extended Thinking
                        if not response_text and (COMPANY_BUDGET - (time.time() - company_start)) > 60:
                            try:
                                print(f"   [{company_name}] 🔄 [Synthesis] Attempting claude-opus-4-8 with Extended Thinking...")
                                synth_start = time.time()
                                resp = claude_client.messages.create(
                                    model="claude-opus-4-8",
                                    max_tokens=16000,
                                    thinking={"type": "adaptive"},
                                    extra_body={"output_config": {"effort": "xhigh"}},
                                    messages=[{"role": "user", "content": synthesis_prompt}]
                                )
                                if resp.content:
                                    for block in resp.content:
                                        if getattr(block, 'type', None) == 'text':
                                            response_text = block.text
                                            break
                                print(f"   [{company_name}] ✅ [Synthesis] claude-opus-4-8 + Extended Thinking succeeded in {int((time.time() - synth_start) * 1000)}ms")
                            except Exception as opus_err:
                                print(f"   [{company_name}] ❌ [Synthesis] Opus 4.8 failed: {opus_err}")

                        # Fallback 1: GPT 5.5
                        if not response_text and (COMPANY_BUDGET - (time.time() - company_start)) > 60:
                            try:
                                print(f"   [{company_name}] 🔄 [Synthesis] Falling back to GPT 5.5...")
                                m_start = time.time()
                                response_text = _call_openai_text(
                                    synthesis_prompt,
                                    model="gpt-5.5",
                                    max_output_tokens=4000,
                                    timeout=_SEED_SYNTH_TIMEOUT,
                                )
                                print(f"   [{company_name}] ✅ [Synthesis] GPT 5.5 succeeded in {int((time.time() - m_start) * 1000)}ms")
                            except Exception as gpt_err:
                                print(f"   [{company_name}] ❌ [Synthesis] GPT 5.5 failed: {gpt_err}")

                        # Fallback 2: Claude Sonnet 4.6
                        if not response_text and (COMPANY_BUDGET - (time.time() - company_start)) > 60:
                            try:
                                print(f"   [{company_name}] 🔄 [Synthesis] Falling back to Claude Sonnet 4.6...")
                                m_start = time.time()
                                sonnet_resp = claude_client.messages.create(
                                    model="claude-sonnet-4-6",
                                    max_tokens=4000,
                                    messages=[{"role": "user", "content": synthesis_prompt}],
                                )
                                for block in sonnet_resp.content:
                                    if getattr(block, 'type', None) == 'text':
                                        response_text = block.text
                                        break
                                print(f"   [{company_name}] ✅ [Synthesis] Claude Sonnet 4.6 succeeded in {int((time.time() - m_start) * 1000)}ms")
                            except Exception as sonnet_err:
                                print(f"   [{company_name}] ❌ [Synthesis] All models failed. Last error: {sonnet_err}")
                    if response_text:
                        payload = extract_json_from_text(response_text)
                        if payload:
                             json_data = json.loads(payload, strict=False)
                             summary_text = json_data.get("summary", summary_text)
                             pitch_text = json_data.get("pitch", pitch_text) or pitch_text
                             why_proposals = json_data.get("why_proposals", why_proposals) or why_proposals
                             what_proposals = json_data.get("what_proposals", what_proposals) or what_proposals

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
                                 intent_results["opus_intent_score"] = opus_intent
                                 if delta_i <= 5:
                                     print(f"   [{company_name}] ✅ [Score Verify] Intent: Scanner 1 {scanner_intent} ≈ Opus {opus_intent} (delta {delta_i}). Keeping Scanner 1 score.")
                                 else:
                                     print(f"   [{company_name}] ℹ️ [Score Verify] Intent: Scanner 1 {scanner_intent} vs Opus {opus_intent} (delta {delta_i}). Keeping Scanner 1 score (immutable).")
                                     gap = intent_results["score"] - reality_results["score"]

                             # --- ARCHETYPE VERIFICATION ---
                             opus_archetype = json_data.get("archetype")
                             final_reality = reality_results["score"]
                             expected_archetype = ("Thriving" if fin_for_archetype >= 50 else "Modernizing") if final_reality >= 50 else ("Cash Cow" if fin_for_archetype >= 50 else "At Risk")

                             if opus_archetype and opus_archetype != expected_archetype:
                                 print(f"   [{company_name}] ⚖️ [Archetype Verify] Opus wrote '{opus_archetype}' but scores dictate '{expected_archetype}' (reality={final_reality}, financial={fin_for_archetype}). Correcting summary.")
                                 valid_archetypes = ["Thriving", "Modernizing", "Cash Cow", "At Risk"]
                                 for wrong_label in valid_archetypes:
                                     if wrong_label == expected_archetype:
                                         continue
                                     summary_text = summary_text.replace(wrong_label, expected_archetype)
                                     summary_text = summary_text.replace(wrong_label.upper(), expected_archetype.upper())
                             elif opus_archetype:
                                 print(f"   [{company_name}] ✅ [Archetype Verify] Opus archetype '{opus_archetype}' matches score-derived '{expected_archetype}'")

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
                    "foresight_evidence": existing_company_data.get("foresight_links", [])[:5],
                })
                if len(batch_history) > 12: batch_history = batch_history[-12:]

            # Domain already resolved before foresight — use metadata for remaining fields
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

            # --- SOURCE MANAGEMENT: Merge new evidence with existing, cap at 15 ---
            existing_evidence = existing_company_data.get("evidence_links", []) if existing_company_data else []
            from scanner_two import get_clean_domain as _get_clean_domain
            _company_site_domain = _get_clean_domain(company["url"]) if company.get("url") else ""
            source_mgmt = manage_evidence_sources(
                existing_links=existing_evidence,
                new_links=reality_results["links"],
                company_domain=_company_site_domain,
            )
            managed_evidence_links = source_mgmt["links"]

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
                "evidence_links": managed_evidence_links,
                "evidence_gold_standard": source_mgmt["gold_standard"],
                "key_people": key_people,
                "stakeholder_enrichment_skipped": stakeholder_enrichment_skipped,
                "stakeholder_intelligence_mode": "buying_insight_only",
                "summary_text": _strip_tac_codes(summary_text),
                "pitch_text": pitch_text,
                "why_proposals": why_proposals,
                "what_proposals": what_proposals,
                "lastUpdated": timestamp,
                "history": batch_history,
                "is_simulated": False,
                "drift_guard_applied": reality_results.get("drift_guard_applied", False),
                "raw_score_before_dampening": reality_results.get("raw_score_before_dampening"),
                "self_analysis": reality_results.get("self_analysis"),
                "financial_index": financial_results.get("financial_index"),
                "financial_sub_scores": financial_results.get("sub_scores", {}),
                "financial_findings": financial_results.get("financial_findings", []),
                "financial_confidence": financial_results.get("financial_confidence", 0),
                "strategic_foresight": foresight_data,
                "foresight_links": foresight_links,
                "scan_quality": company_scan_quality,
            }

            # --- REALITY CHECK: Hallucination Detection (diagnostic only) ---
            summary_check = validate_summary(
                summary_text=summary_text,
                company_name=company_name,
                key_people=key_people,
                evidence_links=reality_results.get("links", []),
                confidence_score=confidence,
            )
            pitch_check = validate_pitch(pitch_text=pitch_text, company_name=company_name)

            full_data["hallucination_flags"] = summary_check["flags"] + pitch_check.get("flags", [])
            full_data["quality_notes"] = summary_check.get("quality_notes", [])
            full_data["hallucination_risk"] = summary_check["risk"]
            full_data["hallucination_score"] = summary_check["score"]

            if summary_check["risk"] == "high":
                print(f"   [{company_name}] ⚠️ [Reality Check] HIGH risk (score {summary_check['score']}/100)! Flags: {summary_check['flags']}")
                print(f"   [{company_name}] ℹ️ [Reality Check] Heuristic is diagnostic only — proceeding to AI fact-check for independent verification.")
            elif summary_check["risk"] == "medium":
                print(f"   [{company_name}] ⚠️ [Reality Check] MEDIUM risk (score {summary_check['score']}/100). Flags: {summary_check['flags']}")
            else:
                print(f"   [{company_name}] ✅ [Reality Check] Summary OK (score {summary_check['score']}/100)")

            # --- AI FACT-CHECK: Same single-pass pipeline as Deep Scanning ---
            # Independent GPT/Claude verification checks claims against promoted evidence.
            # Single-pass: fact-check once → if false claims → apply correction → DONE.
            # Wrapped in try/except: fact-check failure must NOT crash the pipeline —
            # if it fails, the original synthesis is saved as UNVERIFIED and pipeline
            # continues to General Summary + save.
            _seed_fc_remaining = COMPANY_BUDGET - (time.time() - company_start)
            if _seed_fc_remaining < 60:
                print(f"   [{company_name}] ⏭ [Fact-Check] Budget < 60s ({int(_seed_fc_remaining)}s) — skipping, stored as unverified")
                fact_check = {
                    "verified": False,
                    "fact_check_skipped": True,
                    "false_claims": [],
                    "false_claims_detailed": [],
                    "corrected_summary": "",
                    "corrected_pitch": "",
                    "fact_check_model": "skipped_budget",
                    "fact_check_time_ms": 0,
                }
                if company_scan_quality == "complete":
                    company_scan_quality = "unverified"
            else:
                print(f"   [{company_name}] 🔄 [Fact-Check] Independent GPT/Claude fact-check ({int(_seed_fc_remaining)}s remaining)...")
                try:
                    fact_check = ai_fact_check_summary(
                        summary_text=full_data["summary_text"],
                        evidence_links=reality_results.get("links", []),
                        foresight_links=full_data.get("foresight_links", []),
                        company_name=company_name,
                        pitch_text=full_data.get("pitch_text", ""),
                    )
                except Exception as fc_err:
                    print(f"   [{company_name}] ❌ [Fact-Check] Exception — summary stored as UNVERIFIED: {fc_err}")
                    fact_check = {
                        "verified": False,
                        "fact_check_skipped": True,
                        "false_claims": [],
                        "false_claims_detailed": [],
                        "corrected_summary": "",
                        "corrected_pitch": "",
                        "fact_check_model": "error",
                        "fact_check_time_ms": 0,
                    }

            full_data["ai_verification"] = {
                "verified": fact_check["verified"],
                "fact_check_skipped": fact_check.get("fact_check_skipped", False),
                "false_claims": fact_check["false_claims"],
                "false_claims_detailed": fact_check.get("false_claims_detailed", []),
                "model": fact_check["fact_check_model"],
                "time_ms": fact_check["fact_check_time_ms"],
            }

            if fact_check.get("fact_check_skipped"):
                _seed_fc_skip_reason = "budget exhausted" if fact_check.get("fact_check_model") == "skipped_budget" else "all models failed"
                print(f"   [{company_name}] ⚠️ [Fact-Check] Skipped ({_seed_fc_skip_reason}) — summary stored as UNVERIFIED ({fact_check['fact_check_time_ms']}ms)")
                if company_scan_quality == "complete":
                    company_scan_quality = "unverified"
            elif fact_check["verified"]:
                print(f"   [{company_name}] ✅ [Fact-Check] Summary verified — no false claims ({fact_check['fact_check_time_ms']}ms)")
            else:
                # --- FALSE CLAIMS FOUND: Apply corrected summary or re-synthesize ---
                print(f"   [{company_name}] ⚠️ [Fact-Check] {len(fact_check['false_claims'])} false claim(s) found ({fact_check['fact_check_time_ms']}ms):")
                for claim in fact_check["false_claims"]:
                    print(f"      - {claim}")

                # Step 1: Purge contaminated evidence sources
                print(f"   [{company_name}] 🗑️ [Purge] Removing contaminated evidence from stored data...")
                detailed_claims = fact_check.get("false_claims_detailed", [])
                purge_result = purge_false_evidence(
                    full_data, fact_check["false_claims"], detailed_claims=detailed_claims,
                )

                # Step 2: Apply corrected summary or fall back to separate re-synthesis
                corrected_summary = fact_check.get("corrected_summary", "")
                corrected_pitch = fact_check.get("corrected_pitch", "")
                resynth_model_used = fact_check.get("fact_check_model", "")
                _kp_before_fc = len(full_data.get("key_people") or [])

                if corrected_summary:
                    # --- Inline correction from fact-checker ---
                    print(f"   [{company_name}] ✅ [Fact-Check] Using corrected summary from fact-checker ({len(corrected_summary.split())} words)")
                    full_data["summary_text"] = _strip_tac_codes(corrected_summary)
                    if corrected_pitch:
                        full_data["pitch_text"] = corrected_pitch
                        print(f"   [{company_name}] ✅ [Fact-Check] Also applied corrected pitch ({len(corrected_pitch.split())} words)")

                    full_data["re_synthesis"] = {
                        "was_resynthesized": True,
                        "false_claims_found": fact_check["false_claims"],
                        "evidence_purged": purge_result,
                        "resynth_model": resynth_model_used,
                        "resynth_time_ms": fact_check.get("fact_check_time_ms", 0),
                        "method": "fact_check_inline_correction",
                    }
                    full_data["ai_verification"]["verified"] = True
                    full_data["ai_verification"]["false_claims"] = []
                    full_data["ai_verification"]["note"] = "Corrected by independent GPT/Claude fact-checker in single pass (fact-check + fix)"
                    full_data["washing_flag"] = False
                    full_data["washing_type"] = None
                    washing_flag = False
                    washing_type = None
                    print(f"   [{company_name}] ✅ [Fact-Check] Cleared washing flags (false claims corrected)")

                    # Verify key_people survived the correction as compatibility data.
                    _kp_after_fc = len(full_data.get("key_people") or [])
                    if _kp_after_fc != _kp_before_fc:
                        print(f"   [{company_name}] ⚠️ [Fact-Check] key_people compatibility count changed: {_kp_before_fc} → {_kp_after_fc} stakeholder(s)")
                    elif _kp_after_fc == 0:
                        print(f"   [{company_name}] ℹ️ [Fact-Check] key_people compatibility field is empty after correction")
                    else:
                        print(f"   [{company_name}] ✅ [Fact-Check] key_people compatibility field preserved: {_kp_after_fc} stakeholder(s)")

                else:
                    # --- Separate re-synthesis fallback ---
                    print(f"   [{company_name}] ⚠️ [Fact-Check] No inline correction — falling back to separate re-synthesis")
                    print(f"   [{company_name}] 🔄 [Re-Synthesis] Full re-synthesis from cleaned evidence...")
                    resynth_start = time.time()

                    # Build re-synthesis prompt from full_data (same approach as _clean_company_data)
                    knowledge_ctx = get_evolving_framework()
                    clean_evidence_links = full_data.get("evidence_links", [])
                    clean_verified = [l for l in clean_evidence_links if l.get('verified', True)]
                    clean_evidence_list = '\n'.join([
                        f"[{l.get('claim_tier','D')}]{'['+l.get('entity_scope','')+']' if l.get('entity_scope','unknown') != 'unknown' else ''} {l.get('title','Unknown')}: {l.get('snippet','')}"
                        for l in clean_verified[:20]
                    ])
                    removed_claims_note = "\n".join(f"- {c}" for c in fact_check["false_claims"])

                    fc_confidence = full_data.get("confidence_score", 50)
                    fc_fin_score = full_data.get("financial_index")
                    fc_fin_for_arch = fc_fin_score if fc_fin_score is not None else full_data.get("intent_score", 0)

                    fc_washing_context = ""
                    if full_data.get("washing_flag"):
                        fc_washing_context = f"CRITICAL: The forensic scanner DETECTED {full_data.get('washing_type') or 'WASHING'}. High ambition narrative but low operational evidence."

                    fc_financial_context = ""
                    if fc_fin_score is not None:
                        fc_findings = full_data.get("financial_findings", [])
                        fc_findings_text = '; '.join(
                            f.get('assessment', '') if isinstance(f, dict) else ''
                            for f in fc_findings[:5]
                        ) if fc_findings else 'No specific findings'
                        fc_financial_context = f"FINANCIAL HEALTH CONTEXT (do NOT cite numeric scores — the index is displayed separately): {fc_findings_text}. You may reference financial health qualitatively but never mention specific index numbers."

                    clean_seed_synthesis_guardrails = _shared_prompt_guardrails(include_diagnostic=True, include_foresight=True, include_claim_tier=True)
                    resynth_prompt = f"""You are a senior organizational analyst conducting a forensic modernization assessment.
Your task: synthesize scanner evidence into a precise, evidence-grounded diagnosis using the Knowledge Base framework below.

### SHARED EVIDENCE / DIAGNOSIS / FORESIGHT GUARDRAILS
{clean_seed_synthesis_guardrails}

METHODOLOGY: Read ALL evidence first. Score ONLY what evidence supports. Write ONLY what scores justify.
If evidence is thin, say so — a short honest analysis beats a long fabricated one.

### PRIMARY FRAMEWORK (KNOWLEDGE BASE — MANDATORY)
{knowledge_ctx}

You MUST use the terminology, archetypes, and criteria defined in the Knowledge Base above.
Every claim in the summary MUST trace back to specific evidence AND map to a Knowledge Base dimension.
Do NOT invent categories — use ONLY Knowledge Base vocabulary.
NEVER use internal codes (TAC-xxx, DIM-xxx, etc.) — always write the full concept name.

### SCAN DATA
Target: {company_name}
Financial Health Index: {fc_fin_for_arch} ({"actual financial index" if fc_fin_score is not None else "fallback: using intent score"})
Summary Confidence: {fc_confidence}/100

Evidence Found: {clean_evidence_list}
Intent Data: {json.dumps(full_data.get("intent_breakdown", {{}}))}
{fc_washing_context}
{fc_financial_context}

### ANALYSIS PROTOCOL

STEP 1 — SCORE EVALUATION (evaluate before writing anything):
Score each Knowledge Base dimension (0-20) for reality_score, based on VERIFIED EVIDENCE ONLY:
  - Operating Model: Evidence of modern practices? Or legacy/waterfall/silos?
  - Strategy: Clear digital/modernization strategy with visible execution? Or vague/absent?
  - Culture: Learning culture, psychological safety, talent signals? Or hierarchy/control/fear?
  - Service Portfolio: Product-led, API-first, composable architecture? Or monolithic/legacy?
  - AI Readiness: Concrete AI/ML adoption with measurable outcomes? Or just buzzwords/pilot purgatory?

Scoring rules:
  - No evidence for a dimension = score 10 (neutral). Never score generously without evidence.
  - Evidence of aspiration without execution evidence = score 8-12, not higher.
  - Sum the 5 dimensions → 'reality_score' (0-100).

For 'intent_score' (0-100): Score based on the Intent Data provided above.

STEP 2 — SUMMARY (180-220 words):
Write a diagnostic narrative that reads like an expert assessment, not a template:
  - First sentence: State the archetype and what defines this company's position.
  - Body: Map 3-4 key findings to specific KB dimensions.
  - Use KB terminology naturally — but only where evidence supports it.
  - Do NOT cite numeric scores in the text — scores display separately in the UI.
  - Do NOT fabricate findings.
  - Use ONLY the four archetype labels: Thriving, Modernizing, Cash Cow, At Risk.

STEP 3 — PITCH (90-110 words):
A direct cold-outreach message to a C-level executive at {company_name}:
  - Open with a specific insight from the analysis.
  - Derive exactly 2 CONCRETE PROPOSALS targeting the company's weakest KB dimensions.
  - Tone: Professional, provocative, insight-led.

STEP 4 — IMPACT PROPOSALS (derived from Steps 1-3):
Generate exactly 2 WHY statements and 2 WHAT statements.

### GUARDRAILS
- NEVER fabricate evidence, people, events, partnerships, or metrics not present in the scan data.
- NEVER invent temporal claims ("recently announced", "just launched", "this quarter").
- Every factual claim in the summary must be traceable to a specific piece of evidence above.

### OUTPUT
Respond with valid JSON only. Six fields required:
{{"summary": "...", "pitch": "...", "reality_score": <int 0-100>, "intent_score": <int 0-100>, "why_proposals": ["...", "..."], "what_proposals": ["...", "..."]}}

### CRITICAL CONTEXT — FACT-CHECK CORRECTIONS
The previous synthesis contained false claims detected by an independent fact-checker.
The following claims were FALSE and must NOT appear in your output:
{removed_claims_note}

The contaminated evidence sources have been removed. You are now working with CLEANED evidence only.
Generate a completely fresh synthesis from this cleaned evidence set. Do NOT reference or
re-introduce any of the false claims listed above."""

                    resynth_text = ""
                    resynth_model_used = ""
                    resynth_ms = 0

                    # Primary: GPT 5.5
                    try:
                        print(f"   [{company_name}] 🔄 [Re-Synthesis] Attempting gpt-5.5...")
                        resynth_text = _call_openai_text(resynth_prompt, model="gpt-5.5", max_output_tokens=12000, timeout=120)
                        resynth_model_used = "gpt-5.5"
                        resynth_ms = int((time.time() - resynth_start) * 1000)
                        print(f"   [{company_name}] ✅ [Re-Synthesis] GPT 5.5 complete ({resynth_ms}ms)")
                    except Exception as gpt_err:
                        print(f"   [{company_name}] ❌ [Re-Synthesis] GPT 5.5 failed: {gpt_err}")

                    # Fallback: Claude Sonnet 4.6
                    if not resynth_text:
                        try:
                            print(f"   [{company_name}] 🔄 [Re-Synthesis] Falling back to Claude Sonnet 4.6...")
                            m_start = time.time()
                            sonnet_resp = claude_client.messages.create(
                                model="claude-sonnet-4-6",
                                max_tokens=4000,
                                messages=[{"role": "user", "content": resynth_prompt}],
                            )
                            for block in sonnet_resp.content:
                                if getattr(block, 'type', None) == 'text':
                                    resynth_text = block.text
                                    break
                            resynth_model_used = "claude-sonnet-4-6"
                            resynth_ms = int((time.time() - m_start) * 1000)
                            print(f"   [{company_name}] ✅ [Re-Synthesis] Claude Sonnet 4.6 complete ({resynth_ms}ms)")
                        except Exception as sonnet_err:
                            resynth_ms = int((time.time() - resynth_start) * 1000)
                            print(f"   [{company_name}] ❌ [Re-Synthesis] Claude Sonnet 4.6 failed: {sonnet_err} ({resynth_ms}ms)")

                    if not resynth_text:
                        resynth_ms = int((time.time() - resynth_start) * 1000)
                        print(f"   [{company_name}] ❌ [Re-Synthesis] All models failed ({resynth_ms}ms)")

                    if resynth_text:
                        resynth_payload = extract_json_from_text(resynth_text)
                        if resynth_payload:
                            resynth_json = json.loads(resynth_payload, strict=False)
                            new_summary = resynth_json.get("summary", "")
                            new_pitch = resynth_json.get("pitch", "")

                            if new_summary:
                                full_data["summary_text"] = _strip_tac_codes(new_summary)
                            if new_pitch:
                                full_data["pitch_text"] = new_pitch
                            new_why = resynth_json.get("why_proposals", [])
                            new_what = resynth_json.get("what_proposals", [])
                            if new_why and isinstance(new_why, list):
                                full_data["why_proposals"] = new_why
                            if new_what and isinstance(new_what, list):
                                full_data["what_proposals"] = new_what

                            # Update reality score if re-synthesis re-evaluated it
                            resynth_reality = resynth_json.get("reality_score")
                            if resynth_reality is not None and isinstance(resynth_reality, (int, float)):
                                resynth_reality = max(0, min(100, int(resynth_reality)))
                                reality_results["score"] = resynth_reality
                                full_data["reality_score"] = resynth_reality
                                gap = intent_results["score"] - resynth_reality
                                full_data["reality_gap"] = gap
                                if resynth_reality >= 50:
                                    archetype = "Thriving" if fin_for_archetype >= 50 else "Modernizing"
                                else:
                                    archetype = "Cash Cow" if fin_for_archetype >= 50 else "At Risk"
                                full_data["archetype"] = archetype
                                if resynth_reality >= intent_results["score"]:
                                    full_data["washing_flag"] = False
                                    full_data["washing_type"] = None

                            # Verify archetype from re-synthesis
                            resynth_archetype = resynth_json.get("archetype")
                            final_reality = full_data["reality_score"]
                            expected_archetype = ("Thriving" if fin_for_archetype >= 50 else "Modernizing") if final_reality >= 50 else ("Cash Cow" if fin_for_archetype >= 50 else "At Risk")
                            if resynth_archetype and resynth_archetype != expected_archetype:
                                print(f"   [{company_name}] ⚖️ [Re-Synthesis] Archetype '{resynth_archetype}' → corrected to '{expected_archetype}'")
                                valid_archetypes = ["Thriving", "Modernizing", "Cash Cow", "At Risk"]
                                for wrong_label in valid_archetypes:
                                    if wrong_label == expected_archetype:
                                        continue
                                    full_data["summary_text"] = full_data["summary_text"].replace(wrong_label, expected_archetype)

                            print(f"   [{company_name}] ✅ [Re-Synthesis] Full re-synthesis applied from cleaned evidence")

                        full_data["re_synthesis"] = {
                            "was_resynthesized": True,
                            "false_claims_found": fact_check["false_claims"],
                            "evidence_purged": purge_result,
                            "resynth_model": resynth_model_used or "gpt-5.5",
                            "resynth_time_ms": resynth_ms,
                            "method": "separate_re_synthesis_fallback",
                        }
                        full_data["ai_verification"]["verified"] = True
                        full_data["ai_verification"]["false_claims"] = []
                        full_data["ai_verification"]["note"] = "Re-synthesized from cleaned evidence after fact-check (single-pass fallback)"
                        full_data["washing_flag"] = False
                        full_data["washing_type"] = None
                        print(f"   [{company_name}] ✅ [Re-Synthesis] Cleared washing flags (false claims removed)")
                    else:
                        print(f"   [{company_name}] ⚠️ [Re-Synthesis] Could not re-synthesize — saving with false claims flagged")
                        full_data["re_synthesis"] = {
                            "was_resynthesized": False,
                            "false_claims_found": fact_check["false_claims"],
                            "evidence_purged": purge_result,
                            "reason": "re-synthesis failed",
                        }

            # --- GENERAL SUMMARY ---
            # Plain-language narrative (225-275 words) assembled from already-computed
            # structured data (scores, verified links, jury findings, delta).
            # Runs after fact-check so evidence_links are already purged/verified.
            # Failure is non-fatal — old records without this field are handled gracefully.
            _seed_gs_remaining = COMPANY_BUDGET - (time.time() - company_start)
            if _seed_gs_remaining < 45:
                print(f"   [{company_name}] ⏭ [General Summary] Budget < 45s ({int(_seed_gs_remaining)}s) — skipping")
                if company_scan_quality == "complete":
                    company_scan_quality = "partial_summary"
            else:
                try:
                    gs_start = time.time()
                    gs_text, gs_model = generate_general_summary(full_data, company_name)
                    if gs_text:
                        wc = len(gs_text.split())
                        print(f"   [{company_name}] ✅ [General Summary] {wc} words, model={gs_model} ({int((time.time()-gs_start)*1000)}ms)")

                        # Fact-check General Summary against its own input payload
                        gs_check = fact_check_general_summary(gs_text, full_data, company_name)
                        if gs_check.get("corrected_summary"):
                            gs_text = gs_check["corrected_summary"]
                            gs_model = gs_check["corrected_model"] or gs_model
                            print(f"   [{company_name}] 🔄 [GS Fact-Check] Replaced with corrected summary ({gs_check['check_time_ms']}ms)")

                        full_data["general_summary"] = gs_text
                        full_data["general_summary_model"] = gs_model
                        full_data["general_summary_verified"] = gs_check.get("verified", True)
                        full_data["general_summary_false_claims"] = [
                            fc.get("claim", "") for fc in gs_check.get("false_claims", [])
                        ]
                    else:
                        print(f"   [{company_name}] ⚠️ [General Summary] Generation returned empty — skipping field")
                except Exception as gs_err:
                    print(f"   [{company_name}] ⚠️ [General Summary] Failed: {gs_err} — skipping, scan continues")

            # Sync final scan_quality back into full_data before saving. It may have
            # changed during optional General Summary generation.
            full_data["scan_quality"] = company_scan_quality

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
                "archetype": archetype,
                "scan_quality": company_scan_quality,
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
            time.sleep(0.5)  # Stagger starts to avoid simultaneous provider hits

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
