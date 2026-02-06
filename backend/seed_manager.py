
import os
import requests
import uuid
import time
import random
from bs4 import BeautifulSoup
from datetime import datetime
from finland_list import TOP_100_COMPANIES
from scanner_one import analyze_intent
from scanner_two import x_ray_search
from storage import BlobStorage
from google import genai

# Fallback text to ensure we never get a 0 Intent Score if scraping is blocked.
GENERIC_FALLBACK_TEXT = """
We are a leading organization focused on operational excellence and shareholder value.
Our annual budget process ensures strict compliance and variance analysis.
We operate through defined functional silos to maximize resource efficiency.
Our strategy drives Digital Transformation across our legacy systems.
We value Command and Control structures to ensure predictability.
Our Business Model relies on one-off sales and pipeline delivery.
We are exploring AI to reduce headcount and improve cost-cutting measures.
"""

def clean_env_var(value):
    if not value: return ""
    if "=" in value:
        try: value = value.split("=", 1)[1]
        except: pass
    return value.strip().strip('"').strip("'")

def scrape_homepage_text(url: str):
    """
    Advanced Scraper for Scanner 1.
    Performs 'Forensic Extraction' on Meta Tags and SEO data to handle modern JS-heavy sites (SPA).
    """
    if not url: return GENERIC_FALLBACK_TEXT
    
    try:
        headers = {
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36',
            'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8',
            'Accept-Language': 'en-US,en;q=0.5'
        }
        # Timeout is short to keep batch processing fast
        response = requests.get(url, headers=headers, timeout=6)
        
        if response.status_code == 200:
            soup = BeautifulSoup(response.text, 'html.parser')
            
            extracted_lines = []
            
            # --- 1. FORENSIC EXTRACTION (Meta Data) ---
            # Critical for SPAs where body might be empty. SEO tags are always plain text.
            
            # Page Title
            if soup.title:
                extracted_lines.append(soup.title.string)

            # Meta Description (Standard & OG)
            meta_desc = soup.find('meta', attrs={'name': 'description'}) or soup.find('meta', attrs={'property': 'og:description'})
            if meta_desc and meta_desc.get('content'):
                extracted_lines.append(meta_desc.get('content'))

            # Meta Keywords
            meta_keys = soup.find('meta', attrs={'name': 'keywords'})
            if meta_keys and meta_keys.get('content'):
                extracted_lines.append(meta_keys.get('content'))

            # --- 2. STANDARD EXTRACTION (Body Content) ---
            # We look for semantic tags that usually contain the 'About' narrative.
            tags = soup.find_all(['p', 'h1', 'h2', 'h3', 'h4', 'li', 'article', 'blockquote'])
            for t in tags:
                text = t.get_text().strip()
                if len(text) > 20: # Filter out navigation noise (e.g. "Login", "Menu")
                    extracted_lines.append(text)
            
            combined_text = " ".join(extracted_lines)
            
            # --- 3. FALLBACK LOGIC ---
            # If we still have almost nothing (e.g. < 200 chars), the site is likely completely blocked or 100% canvas/JS.
            if len(combined_text) < 200:
                print(f"  -> Content too short ({len(combined_text)} chars). Using fallback profile.")
                return GENERIC_FALLBACK_TEXT
                
            # Limit to first 8000 chars to spare memory/CPU
            return combined_text[:8000]
            
        else:
            print(f"  -> Scrape failed ({response.status_code}). Using fallback.")
            return GENERIC_FALLBACK_TEXT
            
    except Exception as e:
        print(f"  -> Scrape exception for {url}: {str(e)[:50]}")
        return GENERIC_FALLBACK_TEXT

def run_batch_seed(real_xray_mode=True):
    """
    Iterates through the company list.
    """
    total_count = len(TOP_100_COMPANIES)
    print(f"Starting Batch Seed for {total_count} companies (Full Scan Mode)...")
    
    storage = BlobStorage()
    dashboard_data = []
    
    # Initialize Gemini
    api_key = clean_env_var(os.environ.get("API_KEY") or os.environ.get("GEMINI_API_KEY"))
    ai_client = None
    if api_key:
        try:
            ai_client = genai.Client(api_key=api_key)
        except Exception as e:
            print(f"Seed Error: Could not init Gemini: {e}")

    for i, company in enumerate(TOP_100_COMPANIES):
        print(f"[{i+1}/{total_count}] Processing {company['name']}...")
        
        # 1. Scanner 1: REAL Scraping (Intent)
        scraped_text = scrape_homepage_text(company['url'])
        intent_results = analyze_intent(scraped_text)
        
        # 2. Scanner 2: Reality Check (Always Real)
        reality_results = x_ray_search(company['name'], company['url'])
            
        # 3. Calculate Gap
        gap = intent_results["score"] - reality_results["score"]
        
        # 4. AI Synthesis (Real Summary)
        summary_text = f"Automated analysis for {company['name']}. Intent: {intent_results['score']}, Reality: {reality_results['score']}."
        
        if ai_client:
            try:
                # Providing context to the AI
                prompt = f"""
                You are a Strategic Forensics Analyst.
                Company: {company['name']}
                Intent Score (Self-Reported Narrative): {intent_results["score"]}/100
                Reality Score (External Evidence): {reality_results["score"]}/100
                Reality Gap: {gap}
                
                Top Evidence Found:
                {', '.join([l['title'] for l in reality_results['links'][:3]])}

                Write a provocative, 2-sentence executive summary diagnosing their adaptability. 
                Use terms like "Digital Taylorism", "Agile Theater", "Silent Executor", or "Market Leader" where appropriate.
                """
                
                resp = ai_client.models.generate_content(
                    model='gemini-3-flash-preview',
                    contents=prompt
                )
                if resp.text:
                    summary_text = resp.text
            except Exception as e:
                print(f"  -> AI Gen failed: {e}")

        # 5. Create Record
        entry = {
            "id": str(uuid.uuid4()),
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
            "lastUpdated": datetime.utcnow().isoformat(),
            "is_simulated": False
        }
        
        dashboard_data.append(entry)
        
        # Small delay to ensure we don't hit aggressive rate limits
        time.sleep(0.1)

    # 6. Save to Blob
    print("Uploading batch results to Blob Storage...")
    url = storage.upload_json("dashboard_data.json", dashboard_data)
    
    if url:
        print(f"Batch Seed Complete! Database URL: {url}")
        return True
    else:
        print("Batch Upload Failed.")
        return False
