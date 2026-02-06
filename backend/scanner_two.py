
import os
import requests
import re
import hashlib
from urllib.parse import urlparse

# --- FORENSIC METHODOLOGY CONFIGURATION ---

def get_forensic_queries(company_name, domain):
    # UPDATED: Included Finnish terms (e.g., 'tulosvastuu', 'mikromanagerointi', 'yt-neuvottelut')
    return [
        {
            "category": "Operating Model",
            "sentiment": "positive", 
            "label": "Micro-Enterprise Structure",
            "q": f'"{company_name}" AND ("P&L responsibility" OR "tulosvastuu" OR "profit center")'
        },
        {
            "category": "Operating Model",
            "sentiment": "positive",
            "label": "Stream-Aligned Teams",
            "q": f'"{company_name}" AND ("stream-aligned" OR "arvovirta" OR "value stream")'
        },
        {
            "category": "Operating Model",
            "sentiment": "positive",
            "label": "Platform Engineering",
            "q": f'"{company_name}" AND ("platform engineering" OR "internal developer platform" OR "kehitysalusta")'
        },
        {
            "category": "Strategy",
            "sentiment": "negative", # BAD SIGNAL
            "label": "Rigid Budget Compliance",
            "q": f'"{company_name}" AND ("budget adherence" OR "budget compliance" OR "budjettikuri" OR "variance analysis")'
        },
        {
            "category": "Strategy",
            "sentiment": "positive",
            "label": "Rolling Forecasts",
            "q": f'"{company_name}" AND ("rolling forecast" OR "rullaava ennuste" OR "beyond budgeting")'
        },
        {
            "category": "Values",
            "sentiment": "negative", # BAD SIGNAL
            "label": "Digital Taylorism (Micromanagement)",
            "q": f'"{company_name}" AND ("micromanagement" OR "mikromanagerointi" OR "kyttääminen" OR "surveillance")'
        },
        {
            "category": "Values",
            "sentiment": "positive",
            "label": "High Trust Policies",
            "q": f'"{company_name}" AND ("unlimited PTO" OR "joustava työaika" OR "remote first" OR "etätyö")'
        },
        {
            "category": "Values",
            "sentiment": "positive",
            "label": "Blameless Culture",
            "q": f'"{company_name}" AND ("blameless" OR "post-mortem" OR "psykologinen turvallisuus" OR "psychological safety")'
        },
        {
            "category": "AI Readiness",
            "sentiment": "positive",
            "label": "Data Mesh (Domain Ownership)",
            "q": f'"{company_name}" AND ("data mesh" OR "data product" OR "datatuote")'
        },
        {
            "category": "AI Readiness",
            "sentiment": "negative", # BAD SIGNAL
            "label": "Cost Cutting Narrative",
            "q": f'"{company_name}" AND ("AI" OR "tekoäly") AND ("headcount reduction" OR "layoffs" OR "muutosneuvottelut" OR "yt-neuvottelut" OR "säästöt")'
        },
         {
            "category": "AI Readiness",
            "sentiment": "positive",
            "label": "Growth Narrative",
            "q": f'"{company_name}" AND ("AI" OR "tekoäly") AND ("new revenue" OR "uutta liiketoimintaa" OR "innovation" OR "innovaatio")'
        }
    ]

def get_clean_domain(url):
    if not url: return ""
    try:
        parsed = urlparse(url)
        domain = parsed.netloc
        if domain.startswith("www."):
            domain = domain[4:]
        return domain
    except:
        return ""

def get_simulated_score(company_name):
    """
    Generates a consistent Reality Score (0-100) based on the company name.
    """
    hash_val = int(hashlib.sha256(company_name.encode('utf-8')).hexdigest(), 16)
    base_score = 30 + (hash_val % 60) 
    return base_score

def x_ray_search(company_name: str, company_url: str = None):
    api_key = os.environ.get("GOOGLE_API_KEY")
    cx_id = os.environ.get("CX_ENGINE_ID")
    
    domain = get_clean_domain(company_url)
    queries = get_forensic_queries(company_name, domain)
    checklist = []
    evidence_links = []
    
    # --- SIMULATION MODE ---
    if not api_key or not cx_id:
        print(f"   [X-Ray] No API Keys. Simulating for {company_name}")
        simulated_score = get_simulated_score(company_name)
        
        for item in queries:
            item_hash = int(hashlib.sha256((company_name + item['label']).encode('utf-8')).hexdigest(), 16)
            is_found = (item_hash % 100) < 40 
            
            checklist.append({
                "category": item['category'],
                "query": item['label'],
                "found": is_found,
                "sentiment": item['sentiment']
            })
            
            if is_found:
                evidence_links.append({
                    "title": f"[{item['sentiment'].upper()}] Evidence of {item['label']}",
                    "url": "#",
                    "source": "Simulated Forensic Search",
                    "snippet": f"Simulated detection of {item['label']} for {company_name}."
                })

        return { 
            "score": simulated_score, 
            "checklist": checklist, 
            "links": evidence_links[:5], 
            "is_simulated": True 
        }

    # --- REAL MODE (BILINGUAL) ---
    current_score = 50 
    IMPACT_WEIGHT = 7 

    print(f"   [Forensic Scan] Target: {company_name}")

    for item in queries:
        raw_query = item['q']
        sentiment = item['sentiment']
        label = item['label']
        category = item['category']
        
        params = {
            'key': api_key,
            'cx': cx_id,
            'q': raw_query,
            'num': 3,
            'gl': 'fi',               # Geolocation: Finland
            'lr': 'lang_en|lang_fi',  # Language: English OR Finnish
            'safe': 'active'
        }
        
        try:
            res = requests.get("https://www.googleapis.com/customsearch/v1", params=params, timeout=10)
            found = False
            
            if res.status_code == 200:
                data = res.json()
                if "items" in data and len(data["items"]) > 0:
                    found = True
                    result = data["items"][0]
                    evidence_links.append({
                        "title": f"{label}",
                        "url": result.get('link'),
                        "source": result.get('displayLink'),
                        "snippet": result.get('snippet', '')
                    })
            else:
                print(f"      [API ERROR] {res.status_code}")
            
            if found:
                if sentiment == "positive":
                    current_score += IMPACT_WEIGHT
                elif sentiment == "negative":
                    current_score -= IMPACT_WEIGHT
            
            checklist.append({
                "category": category,
                "query": label,
                "found": found,
                "sentiment": sentiment
            })
            
        except Exception as e:
            print(f"      [Exception] {str(e)[:50]}")
            checklist.append({"category": category, "query": label, "found": False, "sentiment": "neutral"})

    final_score = max(0, min(100, current_score))

    return {
        "score": int(final_score),
        "checklist": checklist,
        "links": evidence_links,
        "is_simulated": False
    }
