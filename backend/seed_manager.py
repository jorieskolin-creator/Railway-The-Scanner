
import os
import requests
import uuid
import time
import json
import hashlib
from bs4 import BeautifulSoup
from datetime import datetime
from finland_list import TOP_100_COMPANIES
from scanner_one import analyze_intent
from scanner_two import x_ray_search
from knowledge_base import get_evolving_framework
from storage import BlobStorage
from google import genai

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
    except Exception as e:
        pass
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

def scrape_strategic_content(base_url: str, company_name: str = "Unknown"):
    if not base_url: return ""
    print(f"   [Scanner 1] Deep Crawl Started for: {base_url}...")
    home_html = get_page_text(base_url)
    if not home_html: return f"Automatic scraping failed for {base_url}."

    soup = BeautifulSoup(home_html, 'html.parser')
    
    # PRIORITY MAP: The exact list of categories to scan for Intent Analysis
    # We look for the first valid link for each category to ensure we get the main landing page
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

    # 1. Fill buckets based on priority
    for category, keywords in priority_map.items():
        for a in all_links:
            href = a['href'].lower()
            text = a.get_text(strip=True).lower()
            
            # Check if this link matches the category keywords
            if any(k in href for k in keywords) or any(k in text for k in keywords):
                full_url = make_absolute(a['href'])
                
                # Internal Domain Check (Safety)
                try:
                    base_domain = base_url.split('/')[2].replace('www.', '')
                    if base_domain in full_url: 
                        # Only take the first match per category to avoid scanning 10 different blog pages
                        if category not in found_targets:
                            found_targets[category] = full_url
                except:
                    pass

    final_text = []
    final_text.append(f"--- HOMEPAGE ({base_url}) ---\n" + extract_content_from_html(home_html))
    
    # 2. Compile unique URLs 
    # Sorting ensures DETERMINISTIC behavior (The Butterfly Effect fix)
    urls_to_visit = sorted(list(set(found_targets.values())))
    
    print(f"   [Scanner 1] Identified Strategic Targets: {list(found_targets.keys())}")
    print(f"   [Scanner 1] Visiting {len(urls_to_visit)} subpages...")

    # 3. Visit pages
    for url in urls_to_visit:
        # print(f"   [Scanner 1] + Visiting: {url}")
        sub_html = get_page_text(url)
        content = extract_content_from_html(sub_html)
        if content: final_text.append(f"--- SUBPAGE ({url}) ---\n" + content)
            
    combined_text = "\n\n".join(final_text)
    # Return up to 100k chars to ensure we capture the depth of these ~8 pages
    return combined_text[:100000]

def run_batch_seed(real_xray_mode=True, limit=None):
    # Select companies based on limit
    target_list = TOP_100_COMPANIES[:limit] if limit else TOP_100_COMPANIES
    total_count = len(target_list)
    
    print(f"=== STARTING BATCH SCAN (Count: {total_count}) ===")
    
    storage = BlobStorage()
    batch_summary_list = []
    
    # CRITICAL: Always try to load existing data first.
    # This enables "Resume" capability and prevents wiping the dashboard on start.
    # If files were deleted (Fresh Start), this simply returns [] which is correct.
    try:
        existing_index = storage.retrieve_latest_data("index/summary_list")
        if existing_index:
            batch_summary_list = existing_index
            print(f"   [Info] Loaded existing index of {len(batch_summary_list)} items. Updates will occur in-place.")
    except Exception as e:
        print(f"   [Info] Could not load existing index (Starting Fresh): {e}")

    api_key = clean_env_var(os.environ.get("API_KEY") or os.environ.get("GEMINI_API_KEY"))
    gemini_client = None
    if api_key:
        try:
            gemini_client = genai.Client(api_key=api_key)
        except:
            print("   [Error] Gemini Client init failed.")

    for i, company in enumerate(target_list):
        print(f"\n[{i+1}/{total_count}] Processing: {company['name']} ...")
        
        try:
            # 1. REAL SCRAPING (Intent)
            # We use the URL from the list
            scraped_text = scrape_strategic_content(company["url"], company["name"])
            
            # 2. REAL INTENT ANALYSIS
            intent_results = analyze_intent(scraped_text)
            print(f"   -> Intent Score: {intent_results['score']}")

            # 3. REAL REALITY CHECK (Google Search via Gemini)
            # This uses the imported x_ray_search which does the actual external API call
            reality_results = x_ray_search(company["name"], company["url"])
            print(f"   -> Reality Score: {reality_results['score']} (Found {len(reality_results['links'])} evidence links)")

            gap = intent_results["score"] - reality_results["score"]

            # 4. REAL AI SYNTHESIS
            summary_text = "Automated Analysis Complete."
            if gemini_client:
                try:
                    intent_narrative = json.dumps(intent_results["breakdown"])
                    # Take top 3 links for context
                    evidence_list = ', '.join([f"{l['title']}: {l.get('snippet', '')}" for l in reality_results['links'][:3]])
                    knowledge_context = get_evolving_framework()

                    prompt = f"""
                    Target: {company['name']}
                    Intent Score: {intent_results["score"]}
                    Reality Score: {reality_results["score"]}
                    Gap: {gap}

                    Intent Data: {intent_narrative}
                    Real Evidence Found: {evidence_list}
                    
                    Knowledge Base Context: {knowledge_context}

                    Task: Write a 100-word forensic executive summary. Be direct.
                    """
                    # UPGRADE: Use Pro model for higher quality summaries in batch
                    resp = gemini_client.models.generate_content(model='gemini-3-pro-preview', contents=prompt)
                    if resp.text: summary_text = resp.text
                except Exception as e:
                    print(f"   [Synthesis Error] {e}")

            # 5. ASSEMBLE REAL DATA
            result_id = str(uuid.uuid4())
            timestamp = datetime.utcnow().isoformat()

            full_data = {
                "id": result_id,
                "companyName": company["name"],
                "companyUrl": company["url"],
                "intent_score": intent_results["score"],
                "reality_score": reality_results["score"],
                "reality_gap": gap,
                "intent_breakdown": intent_results["breakdown"],
                "metrics": intent_results.get("metrics", {}),
                "reality_checklist": reality_results["checklist"],
                "evidence_links": reality_results["links"],
                "summary_text": summary_text,
                "lastUpdated": timestamp,
                "history": [],
                "is_simulated": False # FLAG AS REAL
            }

            # 6. SAVE TO BLOB
            saved_url = storage.save_company_data(company["name"], full_data)
            
            summary_entry = {
                "id": result_id,
                "companyName": company["name"],
                "intent_score": intent_results["score"],
                "reality_score": reality_results["score"],
                "reality_gap": gap,
                "lastUpdated": timestamp,
                "detailsUrl": saved_url
            }
            
            # Update local list (Remove old entry if exists, add new)
            batch_summary_list = [x for x in batch_summary_list if x['companyName'] != company['name']]
            batch_summary_list.append(summary_entry)
            
            # CRITICAL UPDATE: Save incrementally so the user sees progress immediately
            storage.save_global_index_direct(batch_summary_list)
            
            print(f"   -> Saved & Index Updated. URL: {saved_url}")

            # 7. RATE LIMITING SAFETY
            # Sleep 5 seconds between companies to avoid hammering Google Search API
            time.sleep(5)

        except Exception as e:
            print(f"   [CRITICAL BATCH ERROR] Failed to process {company['name']}: {e}")
            # Continue to next company, do not crash the whole batch
            continue

    print(f"\n=== BATCH COMPLETE. FINAL SAVE ({len(batch_summary_list)} entries) ===")
    storage.save_global_index_direct(batch_summary_list)
    print("Database Refresh Successful.")
    return True
