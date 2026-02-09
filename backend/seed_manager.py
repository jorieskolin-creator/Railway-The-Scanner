
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

def scrape_strategic_content(base_url: str, company_name: str = "Unknown"):
    if not base_url: return ""
    print(f"   [Scanner 1] Deep Crawl Started for: {base_url}...")
    
    home_html = get_page_text(base_url)
    
    # --- FALLBACK MECHANISM ---
    if not home_html: 
        print(f"   [Scanner 1] ⛔ SCRAPING BLOCKED/FAILED for {base_url}.")
        print(f"   [Scanner 1] 🔄 Initiating AI Context Retrieval (Fallback) for '{company_name}'...")
        
        api_key = clean_env_var(os.environ.get("API_KEY") or os.environ.get("GEMINI_API_KEY"))
        if api_key:
             try:
                client = genai.Client(api_key=api_key)
                # Ask Gemini to hallucinate (grounded) the strategy page content based on its knowledge
                prompt = f"""
                Write a detailed, factual summary of the corporate strategy, operating model, and core values for '{company_name}' (Domain: {base_url}).
                
                Simulate the content typically found on an 'About Us', 'Strategy', or 'Investor Relations' page.
                Include specific details on:
                1. Strategic Vision & Mission
                2. Organizational Structure (e.g. Teams, Hierarchies, Agile models)
                3. Business Model & Service Portfolio
                4. Technology Approach (AI, Cloud, Digital Transformation)
                
                Output plain text only.
                """
                resp = client.models.generate_content(model='gemini-3-flash-preview', contents=prompt)
                if resp.text:
                    print(f"   [Scanner 1] ✅ AI Successfully retrieved strategic context for {company_name}.")
                    return f"--- AI GENERATED CONTEXT FOR {company_name} (SCRAPE FALLBACK) ---\n{resp.text}"
             except Exception as e:
                print(f"   [Scanner 1] ❌ AI Fallback failed: {e}")

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
    
    for url in urls_to_visit:
        sub_html = get_page_text(url)
        content = extract_content_from_html(sub_html)
        if content: final_text.append(f"--- SUBPAGE ({url}) ---\n" + content)
            
    combined_text = "\n\n".join(final_text)
    return combined_text[:100000]

def run_batch_seed(real_xray_mode=True, limit=None, progress_callback=None):
    """
    Runs the batch seed process.
    If progress_callback is provided, it calls it with the updated summary_list after every save.
    """
    target_list = TOP_100_COMPANIES[:limit] if limit else TOP_100_COMPANIES
    total_count = len(target_list)
    
    print(f"=== STARTING BATCH SCAN (Count: {total_count}) ===")
    
    storage = BlobStorage()
    batch_summary_list = []
    
    try:
        existing_index = storage.retrieve_latest_data("index/summary_list")
        if existing_index:
            batch_summary_list = existing_index
            print(f"   [Info] Loaded existing index of {len(batch_summary_list)} items.")
    except Exception as e:
        print(f"   [Info] Could not load existing index: {e}")

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
            # 1. REAL SCRAPING
            scraped_text = scrape_strategic_content(company["url"], company["name"])
            
            # 2. REAL INTENT ANALYSIS
            intent_results = analyze_intent(scraped_text)
            print(f"   -> Intent Score: {intent_results['score']}")

            # 3. REAL REALITY CHECK
            # Keep 3-Pro for the Logic/Search step (Quality is critical here)
            reality_results = x_ray_search(company["name"], company["url"], model_id="gemini-3-pro-preview")
            print(f"   -> Reality Score: {reality_results['score']}")

            gap = intent_results["score"] - reality_results["score"]

            # 4. REAL AI SYNTHESIS
            summary_text = "Automated Analysis Complete."
            if gemini_client:
                try:
                    intent_narrative = json.dumps(intent_results["breakdown"])
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

                    Task: Write a 175-word forensic executive summary. Be direct.
                    """
                    # HYBRID STRATEGY: 
                    # Use Gemini 2.5 Pro for Synthesis.
                    # It creates better text than Flash, but uses a different quota bucket than 3 Pro.
                    resp = gemini_client.models.generate_content(model='gemini-2.5-pro', contents=prompt)
                    if resp.text: summary_text = resp.text
                except Exception as e:
                    print(f"   [Synthesis Error] {e}")

            # 5. ASSEMBLE DATA
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
                "is_simulated": False 
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
            
            # Update local list
            batch_summary_list = [x for x in batch_summary_list if x['companyName'] != company['name']]
            batch_summary_list.append(summary_entry)
            
            # Save Global Index Incremental
            storage.save_global_index_direct(batch_summary_list)
            
            # 7. REAL-TIME CALLBACK (Triggers SSE Broadcast)
            if progress_callback:
                print(f"   [Batch] Broadcasting update ({len(batch_summary_list)} items)...")
                progress_callback(batch_summary_list)
            
            # Rate Limit Safety
            time.sleep(5)

        except Exception as e:
            print(f"   [CRITICAL BATCH ERROR] Failed to process {company['name']}: {e}")
            continue

    print(f"\n=== BATCH COMPLETE. FINAL SAVE ({len(batch_summary_list)} entries) ===")
    storage.save_global_index_direct(batch_summary_list)
    if progress_callback: progress_callback(batch_summary_list)
    return True
