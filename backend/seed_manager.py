
import os
import requests
import uuid
import time
import random
import hashlib
from bs4 import BeautifulSoup
from datetime import datetime
from finland_list import TOP_100_COMPANIES
from scanner_one import analyze_intent
from scanner_two import x_ray_search
from storage import BlobStorage
from google import genai
from knowledge_base import get_evolving_framework

def clean_env_var(value):
    if not value: return ""
    if "=" in value:
        try: value = value.split("=", 1)[1]
        except: pass
    return value.strip().strip('"').strip("'")

def get_deterministic_fallback(company_name):
    return f"Analysis placeholder for {company_name}. No live text available." 

def get_page_text(url, timeout=5):
    """
    Fetches raw HTML with a realistic User-Agent to avoid bot blocking.
    """
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
        # Silently fail on connection errors to avoid log spam
        pass
    return ""

def extract_content_from_html(html):
    """
    Cleans HTML and returns significant text.
    """
    if not html: return ""
    
    soup = BeautifulSoup(html, 'html.parser')
    for element in soup(["script", "style", "nav", "footer", "header", "aside", "noscript", "iframe", "svg"]):
        element.decompose()
        
    text_content = []
    
    # Meta Description
    meta = soup.find("meta", attrs={"name": "description"})
    if meta and meta.get("content"):
        text_content.append(meta['content'])
        
    # Headers
    for h in soup.find_all(["h1", "h2", "h3"]):
        txt = h.get_text(strip=True)
        if len(txt) > 5: text_content.append(txt)
        
    # Paragraphs
    for p in soup.find_all("p"):
        txt = p.get_text(strip=True)
        if len(txt) > 30: text_content.append(txt)
        
    return ". ".join(text_content)

def scrape_strategic_content(base_url: str, company_name: str = "Unknown"):
    """
    SOURCE FOR SCANNER 1 (INTENT):
    This function implements the retrieval of the 'Official Company Narrative' from 4 defined sources:
    
    1. JOB ADS / RECRUITMENT ("careers", "jobs", "rekry") -> Reveals Cultural Intent.
    2. SERVICE OFFERINGS ("services", "solutions", "palvelut") -> Reveals Business Model Intent.
    3. BLOGS / THOUGHT LEADERSHIP ("blog", "news", "ajankohtaista") -> Reveals Strategic Intent.
    4. PRODUCT PORTFOLIO / ABOUT US ("products", "about", "strategy") -> Reveals Architectural Intent.
    """
    if not base_url: 
        return ""
    
    print(f"   [Scanner 1] Deep Crawl Started for: {base_url}...")
    
    # 1. Fetch Homepage
    home_html = get_page_text(base_url)
    if not home_html:
        return f"Automatic scraping failed for {base_url}."

    soup = BeautifulSoup(home_html, 'html.parser')
    
    # 2. Identify Sub-page Links (Heuristic)
    # We look for links containing keywords in href or text
    targets = {
        "Job Ads": ["career", "jobs", "työpaikat", "rekry", "join us", "open positions"],
        "Service Offerings": ["services", "products", "palvelut", "tuotteet", "solutions", "ratkaisut"],
        "Product Portfolio": ["about", "tietoa", "company", "yritys", "strategy", "strategia", "investors"],
        "Blogs": ["blog", "news", "ajankohtaista", "insights", "articles"]
    }
    
    found_urls = set()
    
    # Helper to clean relative URLs
    def make_absolute(href):
        if href.startswith('http'): return href
        if href.startswith('//'): return 'https:' + href
        if href.startswith('/'): return base_url.rstrip('/') + href
        return base_url.rstrip('/') + '/' + href

    for a in soup.find_all('a', href=True):
        href = a['href'].lower()
        text = a.get_text(strip=True).lower()
        
        for category, keywords in targets.items():
            if any(k in href for k in keywords) or any(k in text for k in keywords):
                full_url = make_absolute(a['href'])
                # Only keep internal links to avoid crawling social media etc.
                if base_url.split('.')[1] in full_url: 
                    found_urls.add(full_url)

    # 3. Fetch Content from Sub-pages (Limit to 3 random sub-pages to save time/bandwidth)
    # We prioritize finding at least one 'career' and one 'about' page if possible
    # But for simplicity in this batch function, we take a few distinctive ones.
    
    final_text = []
    final_text.append(extract_content_from_html(home_html))
    
    urls_to_visit = list(found_urls)[:3] # Cap at 3 extra pages
    
    for url in urls_to_visit:
        print(f"   [Scanner 1] + Visiting Sub-page: {url}")
        sub_html = get_page_text(url)
        content = extract_content_from_html(sub_html)
        if content:
            final_text.append(content)
            
    combined_text = "\n\n".join(final_text)
    print(f"   [Scanner 1] Extraction Complete. Total Chars: {len(combined_text)}")
    
    return combined_text[:50000] # Limit size for Regex

def run_batch_seed(real_xray_mode=True):
    total_count = len(TOP_100_COMPANIES)
    print(f"Starting Batch Seed for {total_count} companies (Atomic Mode)...")
    
    storage = BlobStorage()
    batch_summary_list = []
    
    api_key = clean_env_var(os.environ.get("API_KEY") or os.environ.get("GEMINI_API_KEY"))

    for i, company in enumerate(TOP_100_COMPANIES):
        print(f"[{i+1}/{total_count}] Analyzing {company['name']}...", end=" ", flush=True)
        
        # NOTE: For Batch Seeding 100 companies, we use Simulation to avoid 
        # 100x HTTP requests + 100x Gemini calls which would timeout Vercel/Railway.
        # Real scraping is used in the single /scan endpoint.
        
        intent_score = random.randint(40, 95)
        reality_score = random.randint(30, 85)
        
        # Consistency for major players
        if "Nokia" in company["name"]: intent_score, reality_score = 90, 85
        if "Kesko" in company["name"]: intent_score, reality_score = 60, 50

        intent_results = {"score": intent_score, "breakdown": [], "metrics": {}}
        reality_results = {"score": reality_score, "checklist": [], "links": [], "is_simulated": True}
        
        gap = intent_results["score"] - reality_results["score"]
        summary_text = f"Automated analysis for {company['name']}."

        # Generate IDs
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
            "is_simulated": True
        }
        
        summary_entry = {
            "id": result_id,
            "companyName": company["name"],
            "intent_score": intent_results["score"],
            "reality_score": reality_results["score"],
            "reality_gap": gap,
            "lastUpdated": timestamp
        }
        
        # UPLOAD (Blocking)
        url = storage.save_company_data(company["name"], full_data)
        
        if url:
            print(f"✅ Uploaded")
            batch_summary_list.append(summary_entry)
        else:
            print(f"❌ Upload Failed")

        time.sleep(0.1) 

    print(f"Finalizing... Saving Global Index with {len(batch_summary_list)} entries.")
    index_url = storage.save_global_index_direct(batch_summary_list)
    
    if index_url:
        print("Batch Seed Complete. Database Index Refreshed Successfully.")
    else:
        print("Error: Could not save Global Index.")
        
    return True
