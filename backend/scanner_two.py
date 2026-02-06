
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
    CRITICAL: It is strictly prohibited from using the company's own website as a source.
    
    APPROVED SOURCES (STRICT ALLOWLIST):
    1. Tech/Code: StackOverflow, GitHub.
    2. Thought Leadership: Medium.
    3. FI Financial News: Tekniikka&Talous, Talouselämä, Kauppalehti, Tivi.
    4. Employee Voice: Glassdoor, Indeed, The Hub, Oikotie, Duunitori, LinkedIn.
    """
    
    # Only GEMINI_API_KEY is required now.
    api_key = os.environ.get("GEMINI_API_KEY") or os.environ.get("API_KEY")
    
    checklist = []
    raw_findings = []
    base_score = 50
    is_simulated = False
    
    search_name = clean_company_name_for_search(company_name)
    print(f"   [Forensic Scan] Target: {company_name} (Search Term: {search_name})")

    if not api_key:
        print("   [Scanner 2] CRITICAL: No GEMINI_API_KEY found. Using simulation fallback.")
        return { "score": 50, "checklist": [], "links": [], "is_simulated": True }

    try:
        client = genai.Client(api_key=api_key)
        
        # We craft a prompt that forces the model to look at the "Jury" of sources
        # DOMAIN ALLOWLIST ENFORCED HERE
        prompt = f"""
        Perform a FORENSIC INVESTIGATION on "{search_name}".
        
        SEARCH STRATEGY (STRICT - APPROVED SOURCES ONLY):
        Do NOT read the company's own website.
        Look ONLY at evidence from these specific domain groups (The "Standardized Tribunal"):

        1. TECHNICAL COMMUNITIES (The Tech Jury):
           - *.stackoverflow.com
           - *.github.com

        2. FINNISH FINANCIAL & TECH NEWS (The Press Jury):
           - *.tekniikkatalous.fi
           - *.talouselama.fi
           - *.kauppalehti.fi
           - *.tivi.fi

        3. EMPLOYEE VOICE & RECRUITMENT (The People Jury):
           - *.glassdoor.com
           - *.thehub.io
           - *.indeed.com
           - *.oikotie.fi
           - *.duunitori.fi
           - *.linkedin.com
           
        4. THOUGHT LEADERSHIP:
           - *.medium.com

        VERIFY THESE 5 HYPOTHESES:
        1. "Surveillance": Is there micromanagement, time-tracking, or low trust?
        2. "Silos": Are there complaints about bureaucracy, "turf wars", or heavy handovers?
        3. "Fake Agile": Do employees call it "Waterfall", "Zombie Scrum", or "Agile in Name Only"?
        4. "Fear Culture": Is there a blame culture, fear of failure, or layoffs handled poorly?
        5. "Legacy Inertia": Are there complaints about "Excel Hell", old systems, or manual work?

        Also look for POSITIVE signals (e.g. "Autonomy", "Great Culture", "Modern Tech", "Open Source contributions").

        RETURN STRICT JSON:
        {{
            "findings": [
                {{
                    "category": "Operating Model",
                    "label": "Surveillance",
                    "found": boolean,
                    "sentiment": "negative" or "positive" or "neutral",
                    "snippet": "Quote finding...",
                    "title": "Source Title (e.g. 'Talouselämä Article')",
                    "url": "URL of the source"
                }}
            ],
            "reality_score_adjustment": (integer between -30 and +30 based on evidence balance)
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
