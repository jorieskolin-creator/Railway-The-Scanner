
import os
import json
import time
from urllib.parse import urlparse
from google import genai
from google.genai import types
from knowledge_base import get_evolving_framework

# --- HELPER: IMPROVE SEARCH RECALL ---
def clean_company_name_for_search(name):
    """
    Removes legal suffixes to ensure Google Search finds natural language discussions.
    """
    clean = name
    suffixes = [" Oyj", " Abp", " Oy", " Ltd", " Plc", " Corporation", " Group", " Inc"]
    for s in suffixes:
        if clean.lower().endswith(s.lower()):
            clean = clean[:-len(s)].strip()
    return clean

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

def x_ray_search(company_name: str, company_url: str = None):
    """
    SCANNER 2: THE REALITY CHECK (External Evidence)
    
    This function performs the Forensic Scan.
    It uses the '4 Juries' methodology with STRICT domain filtering.
    """
    
    # Only GEMINI_API_KEY is required now.
    api_key = os.environ.get("GEMINI_API_KEY") or os.environ.get("API_KEY")
    
    checklist = []
    raw_findings = []
    base_score = 50
    is_simulated = False
    
    search_name = clean_company_name_for_search(company_name)
    domain_hint = get_clean_domain(company_url) if company_url else f"{search_name.lower().replace(' ', '')}.fi"
    
    print(f"   [Forensic Scan] Target: {company_name} (Search Term: {search_name})")

    if not api_key:
        print("   [Scanner 2] CRITICAL: No GEMINI_API_KEY found. Using simulation fallback.")
        return { "score": 50, "checklist": [], "links": [], "is_simulated": True }

    try:
        client = genai.Client(api_key=api_key)
        
        # WE DEFINE THE SOURCES HERE (MERGED TRIBUNAL)
        prompt = f"""
        Perform a FORENSIC INVESTIGATION on "{search_name}" (Domain: {domain_hint}).
        
        SEARCH STRATEGY (STRICT - COMBINED SOURCES):
        Do NOT read the company's own marketing pages (except for developer portals/investor PDFs).
        Look ONLY at evidence from these specific 'JURIES' (The Standardized Tribunal):

        1. THE ARCHITECTURAL JURY (Tech & Structure)
           - Evidence: Service Portfolio, Platform Thinking, API-First, DevOps maturity.
           - Developer Portals (Platform Thinking): `site:{domain_hint} "API documentation" OR "Developer Portal" OR "SDK"` OR `site:{domain_hint} "Rajapinnat" OR "Kehittäjille" OR "Avoin data"`
           - System Status (Transparency): `site:{domain_hint} "System Status" OR "Operational" OR "Incident History"` OR `site:{domain_hint} "Palveluiden tila" OR "Häiriötiedotteet"`
           - Technical Communities (The Tech Jury): `site:stackoverflow.com "{search_name}"` OR `site:github.com "{search_name}"`

        2. THE STRATEGIC JURY (Money, Press & Intent)
           - Evidence: Strategy (Growth vs. Efficiency), RenDanHeYi.
           - Capital Markets Day (CMD): `site:{domain_hint} filetype:pdf "Capital Markets Day" OR "Strategy Presentation"` OR `site:{domain_hint} filetype:pdf "Sijoittajaesitys" OR "Pääomamarkkinapäivä"`
           - Risk Management Reports: `site:{domain_hint} filetype:pdf "Annual Report" AND "Risk Management"` OR `site:{domain_hint} filetype:pdf "Vuosikertomus" AND "Riskienhallinta"`
           - Finnish Financial & Tech News (The Press Jury): `site:kauppalehti.fi "{search_name}"` OR `site:talouselama.fi "{search_name}"` OR `site:tekniikkatalous.fi "{search_name}"` OR `site:tivi.fi "{search_name}"`

        3. THE COMMUNITY JURY (Culture, People & Reality)
           - Evidence: Operating Model, Psychological Safety, Learning Organization.
           - Finnish Tech Events: `"{search_name}" AND ("ScanAgile" OR "DevOps Finland" OR "Disobey" OR "Mimmit koodaa")`
           - The Unfiltered Voice (Anonymous): `site:teamblind.com "{search_name}"` OR `site:reddit.com "{search_name}"`
           - Employee Voice (The People Jury): `site:glassdoor.com "{search_name}"`
           - Recruitment Channels: `site:thehub.io "{search_name}"` OR `site:indeed.com "{search_name}"` OR `site:oikotie.fi "{search_name}"` OR `site:duunitori.fi "{search_name}"` OR `site:linkedin.com "{search_name}"`

        4. THE ACADEMIC JURY (Deep Audits & Thought Leadership)
           - Evidence: Internal Operating Model failures/successes.
           - Master's Theses (Deep Audits): `site:theseus.fi "{search_name}" AND ("Agile" OR "Process" OR "Johtaminen" OR "Organisaatio")`
           - Aalto University: `site:aalto.fi "{search_name}" AND ("Thesis" OR "Diplomityö")`
           - Thought Leadership: `site:medium.com "{search_name}"`

        VERIFY THESE 5 HYPOTHESES:
        1. "Surveillance": Is there micromanagement, time-tracking, or low trust?
        2. "Silos": Are there complaints about bureaucracy, "turf wars", or heavy handovers?
        3. "Fake Agile": Do employees call it "Waterfall", "Zombie Scrum", or "Agile in Name Only"?
        4. "Platform Thinking": Do they have a public API/Dev portal? (If yes -> High Reality Score).
        5. "Legacy Inertia": Are there academic theses describing failed transformations?

        RETURN STRICT JSON:
        {{
            "findings": [
                {{
                    "category": "Operating Model" | "Strategy" | "Technology" | "Culture",
                    "label": "Brief Label (e.g. 'Public Dev Portal Found')",
                    "found": boolean,
                    "sentiment": "negative" or "positive",
                    "snippet": "Quote finding...",
                    "title": "Source Title (e.g. 'Kauppalehti: Layoff News')",
                    "url": "URL of the source"
                }}
            ],
            "reality_score_adjustment": (integer between -40 and +40 based on evidence balance)
        }}
        """

        # Use Gemini 3 Flash Preview as requested for Scanner 2
        # It supports the google_search tool.
        response = client.models.generate_content(
            model='gemini-3-flash-preview',
            contents=prompt,
            config={
                'tools': [{'google_search': {}}],
                'response_mime_type': 'application/json'
            }
        )
        
        # Parse the result
        if response.text:
            data = json.loads(response.text)
            
            # Extract Findings
            findings = data.get("findings", [])
            for f in findings:
                # Add to checklist for UI
                checklist.append({
                    "category": f.get("category", "General"),
                    "query": f.get("label", "Signal"),
                    "found": f.get("found", False),
                    "sentiment": f.get("sentiment", "neutral")
                })
                
                # Add to links if found
                if f.get("found") and f.get("url"):
                    raw_findings.append({
                        "title": f.get("title", "Evidence Found"),
                        "snippet": f.get("snippet", ""),
                        "url": f.get("url", ""),
                        "source": get_clean_domain(f.get("url", ""))
                    })
            
            # Calculate Score
            adjustment = data.get("reality_score_adjustment", 0)
            final_score = max(0, min(100, base_score + adjustment))
            
            print(f"   [Scanner 2] Google Search Successful. Found {len(raw_findings)} items. Adj: {adjustment}")
            
            return {
                "score": int(final_score),
                "checklist": checklist,
                "links": raw_findings,
                "is_simulated": False # This was a REAL search
            }

    except Exception as e:
        print(f"   [Scanner 2] Google Search Tool Failed: {e}")
        # Fallthrough to Simulation

    # --- FALLBACK: AI SIMULATION ---
    # If the Search Tool fails (e.g. model overloaded), we fall back to knowledge-base simulation
    print(f"   [Scanner 2] Engaging AI Simulation for {company_name} (Fallback)...")
    is_simulated = True
    
    try:
        client = genai.Client(api_key=api_key)
        sim_prompt = f"""
        You are a Forensic Data Generator.
        I cannot access live search results for {company_name}.
        
        Based on your internal knowledge base about {company_name} (industry reputation, typical operating model):
        Generate 3 REALISTIC 'Search Snippets' that represent the likely operational reality.
        
        Return JSON:
        {{
            "findings": [
                {{
                    "title": "Realistic Forum Post Title",
                    "snippet": "Realistic text...",
                    "url": "https://glassdoor.com/...",
                    "sentiment": "negative" or "positive"
                }}
            ],
            "score": (Estimated Reality Score 0-100)
        }}
        """
        
        # Fallback can also use flash
        response = client.models.generate_content(
            model='gemini-3-flash-preview',
            contents=sim_prompt,
            config={'response_mime_type': 'application/json'}
        )
        
        sim_data = json.loads(response.text)
        
        return {
            "score": sim_data.get("score", 50),
            "checklist": [
                {"category": "Simulation", "query": "Historical Data", "found": True, "sentiment": "neutral"}
            ],
            "links": sim_data.get("findings", []),
            "is_simulated": True
        }

    except Exception as e:
        print(f"   [Scanner 2] Simulation Failed: {e}")
        return { "score": 50, "checklist": [], "links": [], "is_simulated": True }
