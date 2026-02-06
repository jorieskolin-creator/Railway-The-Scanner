
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
    return "Company strategy text placeholder." 

def get_page_text(url, timeout=4):
    try:
        headers = {'User-Agent': 'Bot'}
        response = requests.get(url, headers=headers, timeout=timeout)
        if response.status_code == 200:
            return response.text
    except:
        pass
    return ""

def scrape_strategic_content(base_url: str, company_name: str = "Unknown"):
    return "Scraped content placeholder"

def run_batch_seed(real_xray_mode=True):
    total_count = len(TOP_100_COMPANIES)
    print(f"Starting Batch Seed for {total_count} companies (Atomic Mode)...")
    
    storage = BlobStorage()
    
    # List to hold the summary data for the index file
    batch_summary_list = []
    
    api_key = clean_env_var(os.environ.get("API_KEY") or os.environ.get("GEMINI_API_KEY"))

    for i, company in enumerate(TOP_100_COMPANIES):
        print(f"[{i+1}/{total_count}] Analyzing {company['name']}...", end=" ", flush=True)
        
        # 1. Scrape & Analyze (Simulation for speed in this architecture demo)
        intent_score = random.randint(40, 95)
        reality_score = random.randint(30, 85)
        
        # Add some consistency for famous companies
        if "Nokia" in company["name"]: intent_score, reality_score = 90, 85
        if "Kesko" in company["name"]: intent_score, reality_score = 60, 50

        intent_results = {"score": intent_score, "breakdown": [], "metrics": {}}
        reality_results = {"score": reality_score, "checklist": [], "links": [], "is_simulated": True}
        
        gap = intent_results["score"] - reality_results["score"]
        summary_text = f"Automated analysis for {company['name']}."

        # 2. Generate Full Data Object
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
        
        # 3. Generate Summary Object
        summary_entry = {
            "id": result_id,
            "companyName": company["name"],
            "intent_score": intent_results["score"],
            "reality_score": reality_results["score"],
            "reality_gap": gap,
            "lastUpdated": timestamp
        }
        
        # 4. UPLOAD VERIFICATION
        # This function call is BLOCKING. It waits for Vercel to respond.
        url = storage.save_company_data(company["name"], full_data)
        
        if url:
            print(f"✅ Uploaded")
            # Only add to index if upload succeeded
            batch_summary_list.append(summary_entry)
        else:
            print(f"❌ Upload Failed")

        # 5. Tiny sleep to ensure Vercel rate limits are respected
        time.sleep(0.1) 

    # 6. Save the Global Index ONCE at the end
    print(f"Finalizing... Saving Global Index with {len(batch_summary_list)} entries.")
    index_url = storage.save_global_index_direct(batch_summary_list)
    
    if index_url:
        print("Batch Seed Complete. Database Index Refreshed Successfully.")
    else:
        print("Error: Could not save Global Index.")
        
    return True
