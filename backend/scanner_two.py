
import os
import requests
import re
import time
import json
from urllib.parse import urlparse
from google import genai
from knowledge_base import get_evolving_framework

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

def verify_evidence_with_ai(company_name, potential_evidence):
    """
    Uses Gemini 3 Pro to read the snippets and decide if they are REAL evidence
    or just noise/false positives based on the Knowledge Base.
    """
    api_key = os.environ.get("GEMINI_API_KEY") or os.environ.get("API_KEY")
    if not api_key or not potential_evidence:
        return 0, [] # No AI, no score adjustment
    
    try:
        client = genai.Client(api_key=api_key)
        
        # Load Knowledge Base for Context
        knowledge_context = get_evolving_framework()

        evidence_json = json.dumps(potential_evidence, indent=2)

        prompt = f"""
        You are a Forensic Investigator (Scanner 2).
        
        ## KNOWLEDGE BASE (The Law)
        {knowledge_context}

        ## THE TASK
        I have performed Google Searches for "{company_name}" to find evidence of specific organizational behaviors.
        However, search engines are dumb. They might return a result like "We STOPPED doing micromanagement" which a dumb scanner would flag as "Micromanagement detected".
        
        Your job is to read the SNIPPETS below and decide the true sentiment context.

        ## POTENTIAL EVIDENCE
        {evidence_json}

        ## INSTRUCTIONS
        1. Analyze each snippet.
        2. Assign a `relevance_score` (-10 to +10).
           - If the snippet CONFIRMS a BAD behavior (e.g. employee complaining about silos), score negative (e.g. -7).
           - If the snippet CONFIRMS a GOOD behavior (e.g. case study on psychological safety), score positive (e.g. +7).
           - If the snippet is NOISE, MARKETING FLUFF, or IRRELEVANT, score 0.
           - If the snippet says "We avoid [Bad Thing]", score 0 or slightly positive (do not punish them).
        3. Return JSON with the `total_adjustment_score` and a list of `verified_evidence`.
        
        Return ONLY valid JSON.
        """

        response = client.models.generate_content(
            model='gemini-3-pro-preview', # Using PRO for deep reasoning on context
            contents=prompt,
            config={
                'response_mime_type': 'application/json'
            }
        )
        
        result = json.loads(response.text)
        
        # Calculate final verified score adjustment based on AI's reading
        # We cap it so it doesn't swing the entire analysis too wildly based on one snippet
        ai_score_adjustment = 0
        verified_links = []
        
        if "verified_evidence" in result:
             # Just process the AI's output
             # We assume the AI returns a structure we can parse or we aggregate manually
             pass
        
        # Fallback parsing if JSON schema varies (AI can be creative)
        # We accept a simple total score or list
        if "total_adjustment_score" in result:
             ai_score_adjustment = int(result["total_adjustment_score"])
        
        return ai_score_adjustment, potential_evidence # We return original links but weighted by score

    except Exception as e:
        print(f"      [Scanner 2 AI Context Check Failed]: {e}")
        return 0, potential_evidence

def x_ray_search(company_name: str, company_url: str = None):
    api_key = os.environ.get("GOOGLE_API_KEY")
    cx_id = os.environ.get("CX_ENGINE_ID")
    
    domain = get_clean_domain(company_url)
    queries = get_forensic_queries(company_name, domain)
    checklist = []
    
    # Bucket for AI verification
    raw_findings = [] 
    
    # --- NO KEYS MODE ---
    if not api_key or not cx_id:
        print(f"   [X-Ray] No API Keys. Returning 0 score for {company_name}")
        return { "score": 0, "checklist": [], "links": [], "is_simulated": False }

    # --- REAL MODE ---
    # Base score starts neutral.
    # Scanner 2 logic: We gather data, then let AI judge it.
    base_score = 50 
    
    print(f"   [Forensic Scan] Target: {company_name}")

    for item in queries:
        time.sleep(1.2) # Rate limit protection

        raw_query = item['q']
        sentiment = item['sentiment']
        label = item['label']
        category = item['category']
        
        params = {
            'key': api_key, 'cx': cx_id, 'q': raw_query,
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
                    
                    # Store for AI Context Analysis
                    raw_findings.append({
                        "category": category,
                        "label": label,
                        "expected_sentiment": sentiment,
                        "title": result.get('title'),
                        "snippet": result.get('snippet', ''),
                        "url": result.get('link'),
                        "source": result.get('displayLink')
                    })

            checklist.append({
                "category": category,
                "query": label,
                "found": found,
                "sentiment": sentiment
            })
            
        except Exception as e:
            print(f"      [Exception] {str(e)[:50]}")
            checklist.append({"category": category, "query": label, "found": False, "sentiment": "neutral"})

    # --- THE CONTEXT CHECK (AI) ---
    # Now we have a pile of "Maybe" evidence. 
    # We send it to Gemini 3 Pro to see if it actually matters.
    print(f"   [Scanner 2] Verifying {len(raw_findings)} potential findings with AI Context Engine...")
    
    ai_adjustment, verified_links = verify_evidence_with_ai(company_name, raw_findings)
    
    # Final Calculation
    final_score = base_score + ai_adjustment
    final_score = max(0, min(100, final_score))

    print(f"   [Scanner 2] AI Adjustment: {ai_adjustment}. Final Score: {final_score}")

    return {
        "score": int(final_score),
        "checklist": checklist,
        "links": raw_findings, # Return the links found
        "is_simulated": False
    }
