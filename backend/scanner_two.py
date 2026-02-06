
import os
import requests
import re
from urllib.parse import urlparse

# --- FORENSIC METHODOLOGY CONFIGURATION ---

# Queries are now strictly typed:
# 'positive': Finding this INCREASES Reality Score (Evidence of Adaptability)
# 'negative': Finding this DECREASES Reality Score (Evidence of Legacy/Toxicity)

def get_forensic_queries(company_name, domain):
    # FALLBACK: If domain is empty, use company name
    site_operator = f'site:{domain}' if domain else ""
    
    return [
        # --- 1. OPERATING MODEL (RenDanHeYi & Team Topologies) ---
        {
            "category": "Operating Model",
            "sentiment": "positive", 
            "label": "Micro-Enterprise Structure",
            # Broadened: Removed site:linkedin constraint to find blogs/articles
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

        # --- 2. STRATEGY & FINANCE (Beyond Budgeting) ---
        {
            "category": "Strategy",
            "sentiment": "negative", # BAD SIGNAL
            "label": "Rigid Budget Compliance",
            # PDF search remains, but broadened terms
            "q": f'"{company_name}" "budget adherence" OR "budget compliance" OR "variance analysis"'
        },
        {
            "category": "Strategy",
            "sentiment": "positive",
            "label": "Rolling Forecasts",
            "q": f'"{company_name}" "rolling forecast" OR "beyond budgeting" OR "dynamic allocation"'
        },

        # --- 3. VALUES (Culture & Agency) ---
        {
            "category": "Values",
            "sentiment": "negative", # BAD SIGNAL
            "label": "Digital Taylorism (Micromanagement)",
            # Loosened to find forums/reddit/glassdoor
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

        # --- 4. AI READINESS (Data Mesh & Narrative) ---
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
    """
    Extracts 'nokia.com' from 'https://www.nokia.com/about-us'
    """
    if not url: return ""
    try:
        parsed = urlparse(url)
        domain = parsed.netloc
        # Remove www.
        if domain.startswith("www."):
            domain = domain[4:]
        return domain
    except:
        return ""

def x_ray_search(company_name: str, company_url: str = None):
    api_key = os.environ.get("GOOGLE_API_KEY")
    cx_id = os.environ.get("CX_ENGINE_ID")
    
    # Validation
    if not api_key or not cx_id:
        print("Warning: Google API credentials missing. Returning 0 score.")
        return { "score": 50, "checklist": [], "links": [], "is_simulated": True }

    # 1. Prepare Domain context
    domain = get_clean_domain(company_url)
    
    queries = get_forensic_queries(company_name, domain)
    
    checklist = []
    evidence_links = []
    
    # SCORING MODEL
    # Start at Neutral (50).
    # Positive hits pull score UP.
    # Negative hits pull score DOWN.
    current_score = 50 
    
    # Weighting: How much does a "Smoking Gun" move the needle?
    IMPACT_WEIGHT = 7 

    print(f"   [Forensic Scan] Target: {company_name}")

    for item in queries:
        raw_query = item['q']
        sentiment = item['sentiment']
        label = item['label']
        category = item['category']
        
        url = f"https://www.googleapis.com/customsearch/v1?key={api_key}&cx={cx_id}&q={raw_query}"
        
        try:
            res = requests.get(url)
            found = False
            link_data = None
            
            if res.status_code == 200:
                data = res.json()
                if "items" in data and len(data["items"]) > 0:
                    found = True
                    result = data["items"][0]
                    link_data = {
                        "title": f"{label}",
                        "url": result.get('link'),
                        "source": result.get('displayLink'),
                        "snippet": result.get('snippet', '')
                    }
                    evidence_links.append(link_data)
            
            # Update Score based on Sentiment
            if found:
                if sentiment == "positive":
                    current_score += IMPACT_WEIGHT
                elif sentiment == "negative":
                    current_score -= IMPACT_WEIGHT
            
            checklist.append({
                "category": category,
                "query": label, # Display the human-readable label
                "found": found,
                "sentiment": sentiment
            })
            
        except Exception as e:
            print(f"      Err: {str(e)[:50]}")
            checklist.append({"category": category, "query": label, "found": False, "sentiment": "neutral"})

    # Clamp Score 0-100
    final_score = max(0, min(100, current_score))

    return {
        "score": int(final_score),
        "checklist": checklist,
        "links": evidence_links,
        "is_simulated": False
    }
