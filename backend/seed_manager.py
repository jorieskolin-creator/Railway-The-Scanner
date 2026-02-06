
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

# --- DETERMINISTIC FALLBACK PROFILES ---
FALLBACK_PROFILES = [
    {
        "type": "LEGACY_INDUSTRIAL",
        "text": """
        We prioritize operational excellence and shareholder value through strict budget compliance. 
        Our functional silos ensure resource efficiency and command and control stability.
        We focus on cost-cutting measures and maintaining legacy systems to drive predictability.
        Our strategy is defensive, focusing on barriers to entry and supply chain dominance.
        """
    },
    {
        "type": "MODERN_INNOVATOR",
        "text": """
        Our North Star is value creation through autonomous, stream-aligned teams.
        We embrace psychological safety, experimentation, and a fail-fast culture.
        Our platform engineering group provides a golden path for rapid delivery.
        We utilize a data mesh architecture to democratize insights and drive AI readiness.
        Continuous delivery and customer intimacy are at the heart of our operating model.
        """
    },
    {
        "type": "AGILE_THEATER",
        "text": """
        We are undergoing a digital transformation to become more customer-centric.
        While we value agility, we must ensure strict governance and approval chains.
        Our squads work in sprints, but deployment is managed by a central release board.
        We call it the "Hybrid Model" – blending waterfall predictability with agile ceremonies.
        We leverage AI for efficiency and headcount reduction while talking about innovation.
        """
    },
    {
        "type": "BALANCED_CORP",
        "text": """
        We balance stability with innovation. Our strategy focuses on sustainable growth.
        We are moving towards cross-functional collaboration while respecting established hierarchies.
        Cloud-native adoption is a priority, though we maintain significant on-premise assets.
        We value transparency and trust, backed by robust compliance frameworks.
        """
    }
]

def clean_env_var(value):
    if not value: return ""
    if "=" in value:
        try: value = value.split("=", 1)[1]
        except: pass
    return value.strip().strip('"').strip("'")

def get_deterministic_fallback(company_name):
    hash_val = int(hashlib.sha256(company_name.encode('utf-8')).hexdigest(), 16)
    profile = FALLBACK_PROFILES[hash_val % len(FALLBACK_PROFILES)]
    return profile["text"]

def scrape_homepage_text(url: str, company_name: str = "Unknown"):
    """
    Advanced Scraper with Bilingual Support and Deterministic Fallback.
    """
    if not url: return get_deterministic_fallback(company_name)
    
    try:
        # UPDATED HEADERS: explicitly ask for English AND Finnish
        headers = {
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36',
            'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8',
            'Accept-Language': 'en-US,en;q=0.9,fi;q=0.8' 
        }
        
        response = requests.get(url, headers=headers, timeout=6)
        
        if response.status_code == 200:
            soup = BeautifulSoup(response.text, 'html.parser')
            
            extracted_lines = []
            
            # --- 1. FORENSIC EXTRACTION (Meta Data) ---
            if soup.title: extracted_lines.append(soup.title.string)
            
            meta_desc = soup.find('meta', attrs={'name': 'description'}) or soup.find('meta', attrs={'property': 'og:description'})
            if meta_desc and meta_desc.get('content'): extracted_lines.append(meta_desc.get('content'))

            meta_keys = soup.find('meta', attrs={'name': 'keywords'})
            if meta_keys and meta_keys.get('content'): extracted_lines.append(meta_keys.get('content'))

            # --- 2. STANDARD EXTRACTION ---
            tags = soup.find_all(['p', 'h1', 'h2', 'h3', 'h4', 'li', 'article', 'blockquote'])
            for t in tags:
                text = t.get_text().strip()
                if len(text) > 20: 
                    extracted_lines.append(text)
            
            combined_text = " ".join(extracted_lines)
            
            if len(combined_text) < 200:
                print(f"  -> Content too short ({len(combined_text)} chars). Using deterministic fallback.")
                return get_deterministic_fallback(company_name)
                
            return combined_text[:8000]
            
        else:
            print(f"  -> Scrape failed ({response.status_code}). Using fallback.")
            return get_deterministic_fallback(company_name)
            
    except Exception as e:
        print(f"  -> Scrape exception for {url}: {str(e)[:50]}")
        return get_deterministic_fallback(company_name)

def run_batch_seed(real_xray_mode=True):
    total_count = len(TOP_100_COMPANIES)
    print(f"Starting Batch Seed for {total_count} companies...")
    
    storage = BlobStorage()
    dashboard_data = []
    
    api_key = clean_env_var(os.environ.get("API_KEY") or os.environ.get("GEMINI_API_KEY"))
    ai_client = None
    if api_key:
        try:
            ai_client = genai.Client(api_key=api_key)
        except Exception as e:
            print(f"Seed Error: Could not init Gemini: {e}")

    for i, company in enumerate(TOP_100_COMPANIES):
        print(f"[{i+1}/{total_count}] Processing {company['name']}...")
        
        scraped_text = scrape_homepage_text(company['url'], company['name'])
        intent_results = analyze_intent(scraped_text)
        reality_results = x_ray_search(company['name'], company['url'])
            
        gap = intent_results["score"] - reality_results["score"]
        
        summary_text = f"Analysis for {company['name']}. Intent: {intent_results['score']}, Reality: {reality_results['score']}."
        
        if ai_client:
            try:
                # Providing context to the AI
                prompt = f"""
                You are a Strategic Forensics Analyst.
                Company: {company['name']}
                Intent Score: {intent_results["score"]}/100
                Reality Score: {reality_results["score"]}/100
                Gap: {gap}
                
                Evidence: {', '.join([l['title'] for l in reality_results['links'][:3]])}

                Write a 2-sentence summary using terms like "Digital Taylorism", "Agile Theater", or "Silent Executor".
                """
                
                resp = ai_client.models.generate_content(
                    model='gemini-3-flash-preview',
                    contents=prompt
                )
                if resp.text:
                    summary_text = resp.text
            except Exception as e:
                print(f"  -> AI Gen failed: {e}")

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
            "is_simulated": reality_results.get("is_simulated", False)
        }
        
        dashboard_data.append(entry)
        time.sleep(0.1)

    print("Uploading batch results to Blob Storage...")
    url = storage.upload_json("dashboard_data.json", dashboard_data)
    
    if url:
        print(f"Batch Seed Complete! Database URL: {url}")
        return True
    else:
        print("Batch Upload Failed.")
        return False
