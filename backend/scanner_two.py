
import os
import requests
import re
import time
import json
from urllib.parse import urlparse
from google import genai
from knowledge_base import get_evolving_framework

# --- HELPER: IMPROVE SEARCH RECALL ---
def clean_company_name_for_search(name):
    """
    Removes legal suffixes to ensure Google Search finds natural language discussions.
    Example: "Nokia Oyj" -> "Nokia"
    Example: "Kesko Oyj" -> "Kesko"
    """
    # Case insensitive removal of common suffixes
    clean = name
    suffixes = [" Oyj", " Abp", " Oy", " Ltd", " Plc", " Corporation", " Group", " Inc"]
    
    for s in suffixes:
        if clean.lower().endswith(s.lower()):
            clean = clean[:-len(s)].strip()
            
    return clean

# --- FORENSIC METHODOLOGY CONFIGURATION ---

def get_forensic_queries(company_name, domain):
    # Use the conversational name for better search hits
    search_name = clean_company_name_for_search(company_name)
    
    return [
        # 1. OPERATING MODEL
        {
            "category": "Operating Model",
            "sentiment": "negative",
            "label": "Surveillance (Digital Taylorism)",
            "q": f'"{search_name}" AND ("micromanagement" OR "mikromanagerointi" OR "tracking software" OR "mouse movers" OR "time doctor" OR "kyttääminen")'
        },
        {
            "category": "Operating Model",
            "sentiment": "negative",
            "label": "Silos (Handoffs)",
            "q": f'"{search_name}" AND ("throwing over the wall" OR "turf war" OR "reviirikiista" OR "siiloutuminen" OR "kapulanvaihto" OR "portinvartijuus")'
        },

        # 2. STRATEGY
        {
            "category": "Strategy",
            "sentiment": "negative",
            "label": "Fake Agile (Zombie Scrum)",
            "q": f'"{search_name}" AND ("fake agile" OR "zombie scrum" OR "agile in name only" OR "mini-waterfall" OR "vesiputous" OR "kertarysäys")'
        },
        {
            "category": "Strategy",
            "sentiment": "positive",
            "label": "Adaptability (Rolling Forecasts)",
            "q": f'"{search_name}" AND ("rolling forecast" OR "rullaava ennuste" OR "beyond budgeting" OR "jatkuva suunnittelu" OR "havainnoi ja reagoi")'
        },

        # 3. SERVICE PORTFOLIO
        {
            "category": "Service Portfolio",
            "sentiment": "negative",
            "label": "Feature Factory",
            "q": f'"{search_name}" AND ("feature factory" OR "ominaisuustehdas" OR "vanity metrics" OR "output over outcome" OR "success theater")'
        },

        # 4. VALUES / CULTURE
        {
            "category": "Values",
            "sentiment": "negative",
            "label": "Blame Culture (Fear)",
            "q": f'"{search_name}" AND ("blame culture" OR "syyttelykulttuuri" OR "scapegoat" OR "syntipukki" OR "bus factor" OR "pelolla johtaminen")'
        },
        {
            "category": "Values",
            "sentiment": "positive",
            "label": "Psychological Safety",
            "q": f'"{search_name}" AND ("psychological safety" OR "psykologinen turvallisuus" OR "blameless post-mortem" OR "uskallus epäonnistua")'
        },

        # 5. STRATEGY / DECISION MAKING
        {
            "category": "Strategy", 
            "sentiment": "negative",
            "label": "HiPPO (Ego-Driven)",
            "q": f'"{search_name}" AND ("HiPPO" OR "highest paid person" OR "executive whim" OR "top-down" OR "ylhäältä saneltu")'
        },

        # 6. AI READINESS
        {
            "category": "AI Readiness",
            "sentiment": "negative",
            "label": "Pilot Purgatory",
            "q": f'"{search_name}" AND ("pilot purgatory" OR "endless PoC" OR "shiny object" OR "solution looking for a problem" OR "ratkaisu etsii ongelmaa")'
        },
        {
            "category": "AI Readiness",
            "sentiment": "negative",
            "label": "Legacy Inertia (Excel Hell)",
            "q": f'"{search_name}" AND ("Excel hell" OR "Excel-helvetti" OR "manual reporting" OR "käsin raportointi" OR "legacy systems")'
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

def verify_evidence_with_ai(company_name, potential_evidence):
    """
    Step 2 Intelligence Layer:
    Uses Gemini 3 Flash to read search snippets and determine if they are
    CONTEXTUALLY relevant based on the Knowledge Base.
    """
    api_key = os.environ.get("GEMINI_API_KEY") or os.environ.get("API_KEY")
    if not api_key or not potential_evidence:
        return 0, [] 
    
    try:
        client = genai.Client(api_key=api_key)
        
        # Load Knowledge Base
        knowledge_context = get_evolving_framework()

        evidence_json = json.dumps(potential_evidence, indent=2)

        prompt = f"""
        You are the Forensic Investigator (Scanner 2).
        
        ## THE RULEBOOK (Knowledge Base)
        Use these definitions to judge the evidence:
        {knowledge_context}

        ## THE EVIDENCE
        I searched for signals of "Bad" (Old Way) or "Good" (Modern Way) behavior for: {company_name}.
        Here are the search results (snippets).
        
        {evidence_json}

        ## YOUR TASK
        1. Read each snippet carefully.
        2. Decide if the context truly matches the behavior label.
           - Example Match: "Employees complain about micromanagement" -> Matches "Surveillance".
           - Example Mismatch: "We wrote a blog post about how to AVOID micromanagement" -> Does NOT match "Surveillance".
        3. Assign a score (-10 to +10) for the whole set.
           - Negative score = Evidence of "Bad/Old Way" found.
           - Positive score = Evidence of "Good/Modern Way" found.
           - Zero = No credible evidence found.
        
        Return JSON: {{ "total_adjustment_score": number }}
        """

        # FIX: Changed generateContent (JS) to generate_content (Python)
        response = client.models.generate_content(
            model='gemini-3-flash-preview', 
            contents=prompt,
            config={'response_mime_type': 'application/json'}
        )
        
        result = json.loads(response.text)
        score = int(result.get("total_adjustment_score", 0))
        
        return score, potential_evidence

    except Exception as e:
        print(f"      [Scanner 2 AI Context Check Failed]: {e}")
        return 0, potential_evidence

def x_ray_search(company_name: str, company_url: str = None):
    api_key = os.environ.get("GOOGLE_API_KEY")
    cx_id = os.environ.get("CX_ENGINE_ID")
    
    domain = get_clean_domain(company_url)
    queries = get_forensic_queries(company_name, domain)
    checklist = []
    
    raw_findings = [] 
    
    if not api_key or not cx_id:
        return { "score": 0, "checklist": [], "links": [], "is_simulated": False }

    base_score = 50 
    print(f"   [Forensic Scan] Target: {company_name}")

    # Rate limiting for Google Search API
    for item in queries:
        time.sleep(1.2) 

        params = {
            'key': api_key, 'cx': cx_id, 'q': item['q'],
            'num': 2, 'gl': 'fi', 'lr': 'lang_en|lang_fi', 'safe': 'active'
        }
        
        try:
            res = requests.get("https://www.googleapis.com/customsearch/v1", params=params, timeout=10)
            found = False
            
            if res.status_code == 200:
                data = res.json()
                if "items" in data and len(data["items"]) > 0:
                    found = True
                    result = data["items"][0]
                    
                    raw_findings.append({
                        "category": item['category'],
                        "label": item['label'],
                        "expected_sentiment": item['sentiment'],
                        "title": result.get('title'),
                        "snippet": result.get('snippet', ''),
                        "url": result.get('link'),
                        "source": result.get('displayLink')
                    })

            checklist.append({
                "category": item['category'],
                "query": item['label'],
                "found": found,
                "sentiment": item['sentiment']
            })
            
        except Exception as e:
            print(f"      [Exception] {str(e)[:50]}")
            checklist.append({"category": item['category'], "query": item['label'], "found": False, "sentiment": "neutral"})

    # --- INTELLIGENCE LAYER ---
    print(f"   [Scanner 2] Contextualizing {len(raw_findings)} findings via Knowledge Base...")
    
    ai_adjustment, verified_links = verify_evidence_with_ai(company_name, raw_findings)
    
    final_score = max(0, min(100, base_score + ai_adjustment))
    print(f"   [Scanner 2] AI Context Verdict: {ai_adjustment}. Final Reality Score: {final_score}")

    return {
        "score": int(final_score),
        "checklist": checklist,
        "links": raw_findings,
        "is_simulated": False
    }
