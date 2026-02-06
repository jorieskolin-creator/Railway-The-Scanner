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

def get_page_text(url, timeout=4):
    """
    Helper to fetch and clean text from a single URL.
    """
    try:
        headers = {
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36',
            'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9',
            'Accept-Language': 'fi-FI,fi;q=0.9,en-US;q=0.8,en;q=0.7' 
        }
        response = requests.get(url, headers=headers, timeout=timeout)
        if response.status_code == 200:
            soup = BeautifulSoup(response.text, 'html.parser')
            
            # Remove scripts, styles, nav, footers to get core content
            for script in soup(["script", "style", "nav", "footer", "aside", "header", "noscript"]):
                script.decompose()
                
            text = soup.get_text(separator=' ')
            # Clean whitespace
            lines = [line.strip() for line in text.splitlines() if len(line.strip()) > 30] # Only keep substantial sentences
            return " ".join(lines)
    except:
        pass
    return ""

def scrape_strategic_content(base_url: str, company_name: str = "Unknown"):
    """
    SCANNER 1 SOURCE DEFINITION (The "Intent" Layer):
    
    We strictly limit the scraping to the 'Official Narrative' pages to capture 
    what the company *promises* or *thinks* it is.
    
    Expanded Sources:
    1. Homepage (Landing Page messaging)
    2. /Strategy (The Vision)
    3. /Offerings (Products, Services, Solutions - "What we are promising")
    4. /Investors (Governance & Metrics)
    5. /Careers & /Jobs (Job Ads reveal actual role expectations)
    6. /Insights (Blogs, News - Thought Leadership)
    7. /Sustainability (Operating Principles)
    """
    if not base_url: return get_deterministic_fallback(company_name)
    
    base_url = base_url.rstrip('/')
    collected_text = []
    
    # 1. Fetch Homepage
    print(f"   [Scanner 1] Source 1: Homepage ({base_url})")
    home_text = get_page_text(base_url)
    if home_text: collected_text.append(home_text)
    
    # 2. Strategic Targets (English & Finnish)
    # We probe these specific paths to build the "Intent" corpus.
    targets = [
        # Strategy & Vision
        "/strategy", "/strategia", "/vision", "/visio", "/our-story", "/tarinamme", "/about-us", "/meista",
        
        # Offerings (Products & Services - What they sell)
        "/services", "/palvelut", "/products", "/tuotteet", 
        "/solutions", "/ratkaisut", "/offering", "/tarjooma",
        
        # Investors & Governance
        "/investors", "/sijoittajat", "/governance", "/hallinnointi",
        
        # Talent & Job Ads (What they ask for)
        "/careers", "/tyopaikat", "/urapolku", "/working-at", "/meille-toihin",
        "/jobs", "/avoimet-tyopaikat", "/open-positions",
        
        # Thought Leadership & Blogs (What they preach)
        "/blog", "/blogi", "/insights", "/news", "/uutiset", "/ajankohtaista", "/media",
        
        # Sustainability
        "/sustainability", "/vastuullisuus", "/sustainability/approach"
    ]
    
    # Increased max_pages to 5 to accommodate the wider net (Jobs, Blogs, Services)
    pages_crawled = 0
    max_pages = 5
    
    for path in targets:
        if pages_crawled >= max_pages: break
        
        target_url = base_url + path
        print(f"   [Scanner 1] Probing Source: {target_url}")
        page_text = get_page_text(target_url, timeout=2.5)
        
        # Only add if we found substantial text (avoids 404 pages that just say "Not Found")
        if len(page_text) > 400: 
            collected_text.append(page_text)
            pages_crawled += 1
            
    full_corpus = " ".join(collected_text)
    
    if len(full_corpus) < 200:
        print("   [Scanner 1] Warning: Scrape yielded low data. Using deterministic fallback.")
        return get_deterministic_fallback(company_name)
        
    print(f"   [Scanner 1] Narrative Corpus Built. Size: {len(full_corpus)} chars.")
    return full_corpus[:25000] # Increased cap slightly to hold the extra context

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
        
        # UPGRADE: Use Strategic Scraper (Scanner 1)
        scraped_text = scrape_strategic_content(company['url'], company['name'])
        
        intent_results = analyze_intent(scraped_text)
        reality_results = x_ray_search(company['name'], company['url'])
            
        gap = intent_results["score"] - reality_results["score"]
        summary_text = f"Analysis for {company['name']}. Intent: {intent_results['score']}, Reality: {reality_results['score']}."
        
        if ai_client:
            try:
                knowledge_context = get_evolving_framework()
                prompt = f"""
                ### STEP 3: BATCH SYNTHESIS
                Analyze {company['name']}.
                
                ## KNOWLEDGE BASE
                {knowledge_context}

                ## DATA
                Gap: {gap} (Intent {intent_results["score"]} vs Reality {reality_results["score"]}).
                Evidence: {', '.join([l['title'] for l in reality_results['links'][:3]])}

                ## TASK
                Classify them (e.g., "Agile Theater") and explain why in 2 sentences.
                """
                
                resp = ai_client.models.generate_content(
                    model='gemini-3-pro-preview', # Upgraded to Pro for synthesis
                    contents=prompt
                )
                if resp.text: summary_text = resp.text
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
        time.sleep(0.5) 

    print("Uploading batch results to Blob Storage...")
    url = storage.upload_json("dashboard_data.json", dashboard_data)
    
    if url:
        print(f"Batch Seed Complete! Database URL: {url}")
        return True
    else:
        print("Batch Upload Failed.")
        return False