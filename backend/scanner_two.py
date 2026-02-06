
import os
import requests
import re
import hashlib
from urllib.parse import urlparse

# --- FORENSIC METHODOLOGY CONFIGURATION ---

def get_forensic_queries(company_name, domain):
    return [
        {
            "category": "Operating Model",
            "sentiment": "positive", 
            "label": "Micro-Enterprise Structure",
            "q": f'"{company_name}" "P&L responsibility" AND "team level"'
        },
        {
            "category": "Operating Model",
            "sentiment": "positive",
            "label": "Stream-Aligned Teams",
            "q": f'"{company_name}" "stream-aligned" OR "value stream management" OR "end-to-end teams"'
        },
        {
            "category": "Operating Model",
            "sentiment": "positive",
            "label": "Platform Engineering (Golden Path)",
            "q": f'"{company_name}" "platform engineering" OR "internal developer platform" OR "golden path"'
        },
        {
            "category": "Strategy",
            "sentiment": "negative", # BAD SIGNAL
            "label": "Rigid Budget Compliance",
            "q": f'"{company_name}" "budget adherence" OR "budget compliance" OR "variance analysis"'
        },
        {
            "category": "Strategy",
            "sentiment": "positive",
            "label": "Rolling Forecasts",
            "q": f'"{company_name}" "rolling forecast" OR "beyond budgeting" OR "dynamic allocation"'
        },
        {
            "category": "Values",
            "sentiment": "negative", # BAD SIGNAL
            "label": "Digital Taylorism (Micromanagement)",
            "q": f'"{company_name}" "micromanagement" OR "surveillance" OR "time tracking software"'
        },
        {
            "category": "Values",
            "sentiment": "positive",
            "label": "High Trust Policies (PTO/Remote)",
            "q": f'"{company_name}" "unlimited PTO" OR "remote first" OR "flexible hours"'
        },
        {
            "category": "Values",
            "sentiment": "positive",
            "label": "Blameless Culture (Post-Mortems)",
            "q": f'"{company_name}" "blameless post-mortem" OR "psychological safety"'
        },
        {
            "category": "AI Readiness",
            "sentiment": "positive",
            "label": "Data Mesh (Domain Ownership)",
            "q": f'"{company_name}" "data mesh" OR "data product" OR "domain ownership"'
        },
        {
            "category": "AI Readiness",
            "sentiment": "negative", # BAD SIGNAL
            "label": "AI Efficiency Narrative (Cost Cutting)",
            "q": f'"{company_name}" "AI" AND ("headcount reduction" OR "cost savings" OR "layoffs")'
        },
         {
            "category": "AI Readiness",
            "sentiment": "positive",
            "label": "AI Growth Narrative (New Business)",
            "q": f'"{company_name}" "AI" AND ("new revenue" OR "new business model" OR "innovation")'
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
    Used when API keys are missing to create a realistic-looking distribution.
    """
    # Create a consistent integer from the name
    hash_val = int(hashlib.sha256(company_name.encode('utf-8')).hexdigest(), 16)
    
    # We want a distribution that is slightly weighted towards 40-70 (the "average" corp)
    # but with some outliers.
    base_score = 30 + (hash_val % 60) # 30 to 90 range
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
        
        # Fake checklist generation
        for item in queries:
            # Deterministic found status
            item_hash = int(hashlib.sha256((company_name + item['label']).encode('utf-8')).hexdigest(), 16)
            is_found = (item_hash % 100) < 40 # 40% chance to find any specific item
            
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
            "links": evidence_links[:5], # Limit links
            "is_simulated": True 
        }

    # --- REAL MODE ---
    current_score = 50 
    IMPACT_WEIGHT = 7 

    print(f"   [Forensic Scan] Target: {company_name}")

    for item in queries:
        raw_query = item['q']
        sentiment = item['sentiment']
        label = item['label']
        category = item['category']
        
        # USE PARAMS FOR SAFE ENCODING
        params = {
            'key': api_key,
            'cx': cx_id,
            'q': raw_query,
            'num': 1 # We only need to know if 1 exists
        }
        
        try:
            res = requests.get("https://www.googleapis.com/customsearch/v1", params=params, timeout=5)
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
                # Log specific error for debugging (e.g., 403 Quota, 400 Bad Request)
                print(f"      [API ERROR] {res.status_code}: {res.text[:100]}")
            
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
