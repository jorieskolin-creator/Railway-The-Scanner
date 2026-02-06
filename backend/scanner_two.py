
import os
import requests
import re
import hashlib
import time
from urllib.parse import urlparse

# --- FORENSIC METHODOLOGY CONFIGURATION ---

def get_forensic_queries(company_name, domain):
    # UPGRADE: INTEGRATED "SMOKING GUN" TABLE + FINNISH NUANCE
    # Added "Kapulanvaihto" (Handoffs) and "Portinvartijuus" (Gatekeeping)
    return [
        # 1. OPERATING MODEL: Autonomy vs Control
        {
            "category": "Operating Model",
            "sentiment": "negative",
            "label": "Surveillance (Digital Taylorism)",
            # Table Source: "Micromanagement", "Tracking software", "Mouse movements"
            "q": f'"{company_name}" AND ("micromanagement" OR "mikromanagerointi" OR "tracking software" OR "mouse movers" OR "time doctor" OR "kyttääminen")'
        },
        {
            "category": "Operating Model",
            "sentiment": "negative",
            "label": "Silos (Handoffs)",
            # Added "kapulanvaihto" and "portinvartijuus"
            "q": f'"{company_name}" AND ("throwing over the wall" OR "turf war" OR "reviirikiista" OR "siiloutuminen" OR "kapulanvaihto" OR "portinvartijuus")'
        },

        # 2. STRATEGY: Agile vs Waterfall
        {
            "category": "Strategy",
            "sentiment": "negative",
            "label": "Fake Agile (Zombie Scrum)",
            # Table Source: "Fake Agile", "Zombie Scrum", "Mini-waterfall"
            "q": f'"{company_name}" AND ("fake agile" OR "zombie scrum" OR "agile in name only" OR "mini-waterfall" OR "vesiputous" OR "kertarysäys")'
        },
        {
            "category": "Strategy",
            "sentiment": "positive",
            "label": "Adaptability (Rolling Forecasts)",
            "q": f'"{company_name}" AND ("rolling forecast" OR "rullaava ennuste" OR "beyond budgeting" OR "jatkuva suunnittelu" OR "havainnoi ja reagoi")'
        },

        # 3. PORTFOLIO: Value vs Activity
        {
            "category": "Service Portfolio", # Mapped to Service/Product Portfolio
            "sentiment": "negative",
            "label": "Feature Factory",
            # Table Source: "Feature factory", "Vanity metrics", "Output over outcome"
            "q": f'"{company_name}" AND ("feature factory" OR "ominaisuustehdas" OR "vanity metrics" OR "output over outcome" OR "success theater")'
        },

        # 4. CULTURE: Safety vs Fear
        {
            "category": "Values",
            "sentiment": "negative",
            "label": "Blame Culture (Fear)",
            # Table Source: "Blame culture", "Bus factor", "Scapegoating"
            "q": f'"{company_name}" AND ("blame culture" OR "syyttelykulttuuri" OR "scapegoat" OR "syntipukki" OR "bus factor" OR "pelolla johtaminen")'
        },
        {
            "category": "Values",
            "sentiment": "positive",
            "label": "Psychological Safety",
            "q": f'"{company_name}" AND ("psychological safety" OR "psykologinen turvallisuus" OR "blameless post-mortem" OR "uskallus epäonnistua")'
        },

        # 5. DECISION MAKING: Data vs Ego
        {
            "category": "Strategy", # Mapped to Strategy/Decision Making
            "sentiment": "negative",
            "label": "HiPPO (Ego-Driven)",
            # Table Source: "HiPPO", "Executive whim", "Top-down"
            "q": f'"{company_name}" AND ("HiPPO" OR "highest paid person" OR "executive whim" OR "top-down" OR "ylhäältä saneltu")'
        },

        # 6. AI READINESS: Strategic vs Tactical
        {
            "category": "AI Readiness",
            "sentiment": "negative",
            "label": "Pilot Purgatory",
            # Table Source: "Pilot purgatory", "Shiny object", "Solution looking for a problem"
            "q": f'"{company_name}" AND ("pilot purgatory" OR "endless PoC" OR "shiny object" OR "solution looking for a problem" OR "ratkaisu etsii ongelmaa")'
        },
        {
            "category": "AI Readiness",
            "sentiment": "negative",
            "label": "Legacy Inertia (Excel Hell)",
            "q": f'"{company_name}" AND ("Excel hell" OR "Excel-helvetti" OR "manual reporting" OR "käsin raportointi" OR "legacy systems")'
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
    Generates a consistent Reality Score based on the company name to avoid
    the 'flat 50' issue when API keys are missing.
    """
    hash_val = int(hashlib.sha256(company_name.encode('utf-8')).hexdigest(), 16)
    # Range 20 to 80
    base_score = 20 + (hash_val % 61) 
    return base_score

def run_simulation(company_name, queries):
    """
    Helper to run the simulation logic, shared by both
    no-key mode.
    """
    checklist = []
    evidence_links = []
    simulated_score = get_simulated_score(company_name)
    
    for item in queries:
        # Deterministic simulation based on name + query label
        item_hash = int(hashlib.sha256((company_name + item['label']).encode('utf-8')).hexdigest(), 16)
        
        # Probability of finding evidence based on company score
        if item['sentiment'] == 'positive':
            is_found = (item_hash % 100) < simulated_score
        else:
            is_found = (item_hash % 100) > simulated_score
        
        checklist.append({
            "category": item['category'],
            "query": item['label'],
            "found": is_found,
            "sentiment": item['sentiment']
        })
        
        if is_found:
            evidence_links.append({
                "title": f"[{item['sentiment'].upper()}] {item['label']} (Simulated)",
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

def x_ray_search(company_name: str, company_url: str = None):
    api_key = os.environ.get("GOOGLE_API_KEY")
    cx_id = os.environ.get("CX_ENGINE_ID")
    
    domain = get_clean_domain(company_url)
    queries = get_forensic_queries(company_name, domain)
    checklist = []
    evidence_links = []
    
    # --- SIMULATION MODE (MISSING KEYS) ---
    if not api_key or not cx_id:
        print(f"   [X-Ray] No API Keys. Simulating for {company_name}")
        return run_simulation(company_name, queries)

    # --- REAL MODE (BILINGUAL) ---
    current_score = 50 
    IMPACT_WEIGHT = 7 

    print(f"   [Forensic Scan] Target: {company_name}")

    for item in queries:
        # Rate limiting logic for standard quotas (100 QPM)
        # Even with 10k/day, the per-minute limit is strict.
        # 1.0s delay guarantees max 60 requests/min, staying safely under 100.
        time.sleep(1.0) 

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
            elif res.status_code == 403:
                # NO SIMULATION FALLBACK as requested.
                print(f"      [API ERROR 403] Forbidden/Quota. Check QPM limits or Key permissions.")
                # We interpret this as "Not Found" for this query, but continue the batch
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
