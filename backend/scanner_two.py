
import os
import requests
import re
from urllib.parse import urlparse

# --- FORENSIC METHODOLOGY CONFIGURATION ---

# Queries are now strictly typed:
# 'positive': Finding this INCREASES Reality Score (Evidence of Adaptability)
# 'negative': Finding this DECREASES Reality Score (Evidence of Legacy/Toxicity)

def get_forensic_queries(company_name, domain):
    return [
        # --- 1. OPERATING MODEL (RenDanHeYi & Team Topologies) ---
        {
            "category": "Operating Model",
            "sentiment": "positive", 
            "label": "Micro-Enterprise Structure",
            "q": f'site:linkedin.com/jobs "{company_name}" "P&L responsibility" "team level" -Director -VP'
        },
        {
            "category": "Operating Model",
            "sentiment": "positive",
            "label": "Stream-Aligned Teams",
            "q": f'site:{domain} "stream-aligned" OR "value stream" OR "end-to-end ownership"'
        },
        {
            "category": "Operating Model",
            "sentiment": "positive",
            "label": "Platform Engineering (Golden Path)",
            "q": f'site:linkedin.com/jobs "{company_name}" "platform product manager" OR "internal developer platform" OR "golden path"'
        },

        # --- 2. STRATEGY & FINANCE (Beyond Budgeting) ---
        {
            "category": "Strategy",
            "sentiment": "negative", # BAD SIGNAL
            "label": "Rigid Budget Compliance",
            "q": f'filetype:pdf site:{domain} "annual budget" AND ("variance analysis" OR "compliance" OR "enforcement")'
        },
        {
            "category": "Strategy",
            "sentiment": "positive",
            "label": "Rolling Forecasts",
            "q": f'site:linkedin.com/jobs "{company_name}" "rolling forecast" "driver-based" -budget'
        },

        # --- 3. VALUES (Culture & Agency) ---
        {
            "category": "Values",
            "sentiment": "negative", # BAD SIGNAL
            "label": "Digital Taylorism (Micromanagement)",
            "q": f'site:glassdoor.com/reviews "{company_name}" "micromanagement" OR "tracking" OR "surveillance"'
        },
        {
            "category": "Values",
            "sentiment": "positive",
            "label": "High Trust Policies (PTO/Remote)",
            "q": f'site:{domain} "unlimited PTO" OR "remote first" OR "asynchronous"'
        },
        {
            "category": "Values",
            "sentiment": "positive",
            "label": "Blameless Culture (Post-Mortems)",
            "q": f'site:{domain} "post-mortem" OR "incident report" OR "learning from failure"'
        },

        # --- 4. AI READINESS (Data Mesh & Narrative) ---
        {
            "category": "AI Readiness",
            "sentiment": "positive",
            "label": "Data Mesh (Domain Ownership)",
            "q": f'site:linkedin.com/jobs "{company_name}" "data product owner" OR "domain data" -centralized'
        },
        {
            "category": "AI Readiness",
            "sentiment": "negative", # BAD SIGNAL
            "label": "AI Efficiency Narrative (Cost Cutting)",
            "q": f'site:{domain} "AI" AND ("headcount" OR "cost savings" OR "efficiency")'
        },
         {
            "category": "AI Readiness",
            "sentiment": "positive",
            "label": "AI Growth Narrative (New Business)",
            "q": f'site:{domain} "AI" AND ("new business model" OR "revenue growth" OR "innovation")'
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
        return { "score": 0, "checklist": [], "links": [], "is_simulated": False }

    # 1. Prepare Domain context
    domain = get_clean_domain(company_url)
    if not domain:
        # Fallback if no URL provided: try to guess or use broad search
        domain = f"{company_name.replace(' ', '').lower()}.com"
        
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

    print(f"   [Forensic Scan] Target: {company_name} | Domain: {domain}")

    for item in queries:
        raw_query = item['q']
        sentiment = item['sentiment']
        label = item['label']
        category = item['category']
        
        # Build API URL
        # We append "Finland" context if searching generic sites, but respecting strict site: operators
        # Actually, the user's queries are very specific, so we run them AS IS.
        
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
                        "title": f"[{sentiment.upper()}] {label}: {result.get('title')}",
                        "url": result.get('link'),
                        "source": result.get('displayLink'),
                        "snippet": result.get('snippet', '')
                    }
                    evidence_links.append(link_data)
            
            # Update Score based on Sentiment
            if found:
                if sentiment == "positive":
                    current_score += IMPACT_WEIGHT
                    # print(f"      + Hit: {label} (Score +{IMPACT_WEIGHT})")
                elif sentiment == "negative":
                    current_score -= IMPACT_WEIGHT
                    # print(f"      - Hit: {label} (Score -{IMPACT_WEIGHT})")
            
            checklist.append({
                "category": category,
                "query": label, # Display the human-readable label, not the raw query string
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
