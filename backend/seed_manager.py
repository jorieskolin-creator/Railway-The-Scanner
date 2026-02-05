
import requests
import uuid
import time
from bs4 import BeautifulSoup
from datetime import datetime
from finland_list import TOP_100_COMPANIES
from scanner_one import analyze_intent
from scanner_two import x_ray_search, run_simulation_mode
from storage import BlobStorage

def scrape_homepage_text(url: str):
    """
    Simple scraper to fetch the 'About' or homepage text for Scanner 1 analysis.
    """
    if not url: return ""
    try:
        headers = {
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36'
        }
        # Timeout is short to keep batch processing fast
        response = requests.get(url, headers=headers, timeout=5)
        if response.status_code == 200:
            soup = BeautifulSoup(response.text, 'html.parser')
            # Extract paragraphs to get narrative
            paragraphs = soup.find_all('p')
            text = " ".join([p.get_text() for p in paragraphs])
            # Limit to first 5000 chars to avoid overloading
            return text[:5000]
    except Exception as e:
        print(f"Scraping failed for {url}: {e}")
    
    # Fallback text if scraping fails
    return "Company strategy and annual report analysis pending."

def run_batch_seed(real_xray_mode=False):
    """
    Iterates through the Top 100 list.
    real_xray_mode: If True, uses Google API (Costs $$). If False, uses Simulation for Reality check.
    Note: Scanner 1 (Intent) ALWAYS attempts real scraping.
    """
    print(f"Starting Batch Seed for {len(TOP_100_COMPANIES)} companies...")
    print(f"X-Ray Mode: {'REAL API' if real_xray_mode else 'SIMULATION'}")
    
    storage = BlobStorage()
    dashboard_data = []
    
    for i, company in enumerate(TOP_100_COMPANIES):
        print(f"[{i+1}/{len(TOP_100_COMPANIES)}] Processing {company['name']}...")
        
        # 1. Scanner 1: REAL Scraping (Intent)
        scraped_text = scrape_homepage_text(company['url'])
        intent_results = analyze_intent(scraped_text)
        
        # 2. Scanner 2: Reality Check
        if real_xray_mode:
            reality_results = x_ray_search(company['name'], company['url'])
        else:
            # Use simulation for batch to save API quota, but based on company name
            # This generates realistic but fake "Reality" data
            reality_results = run_simulation_mode(company['name'])
            
        # 3. Calculate Gap
        gap = intent_results["score"] - reality_results["score"]
        
        # 4. Create Record
        entry = {
            "id": str(uuid.uuid4()),
            "companyName": company["name"],
            "intent_score": intent_results["score"],
            "reality_score": reality_results["score"],
            "reality_gap": gap,
            "intent_breakdown": intent_results["breakdown"],
            "reality_checklist": reality_results["checklist"],
            "evidence_links": reality_results["links"],
            "summary_text": f"Batch analysis completed for {company['name']}. Intent derived from homepage crawl. Reality index calculated via {'X-Ray' if real_xray_mode else 'Model Projection'}.",
            "lastUpdated": datetime.utcnow().isoformat()
        }
        
        dashboard_data.append(entry)
        
        # Be nice to the servers
        time.sleep(0.5)

    # 5. Save to Blob
    print("Uploading batch results to Blob Storage...")
    url = storage.upload_json("dashboard_data.json", dashboard_data)
    
    if url:
        print(f"Batch Seed Complete! Database URL: {url}")
        return True
    else:
        print("Batch Upload Failed.")
        return False
