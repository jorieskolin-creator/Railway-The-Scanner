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
    Performs a Forensic X-Ray Scan using Gemini with Google Search Grounding.
    
    ARCHITECTURAL CHANGE:
    - Removed 'CX_ENGINE_ID' (Legacy Custom Search).
    - Now uses Gemini's native 'google_search' tool.
    - This automatically searches the ENTIRE WEB (Glassdoor, Reddit, News, etc.).
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
        
        # We ask Gemini to do the heavy lifting: Search AND Categorize in one go.
        # This saves 10+ HTTP calls and uses the full Google Index.
        prompt = f"""
        INVESTIGATE the company "{search_name}" using Google Search.
        
        Look for recent employee reviews (Glassdoor, Reddit, Blind), news articles, and blogs.
        Specifically verify these 5 FORENSIC HYPOTHESES:

        1. "Surveillance": Is there evidence of micromanagement or time-tracking software?
        2. "Silos": Are there complaints about bureaucracy, handovers, or turf wars?
        3. "Fake Agile": Do people call it "Zombie Scrum", "Waterfall", or "Agile in Name Only"?
        4. "Fear Culture": Is there a "Blame Culture" or lack of psychological safety?
        5. "Legacy Inertia": Are there complaints about old systems, "Excel Hell", or manual reporting?

        Also look for POSITIVE signals (e.g., "Autonomy", "Psychological Safety", "Modern Tech Stack").

        RETURN STRICT JSON ONLY:
        {{
            "findings": [
                {{
                    "category": "Operating Model",
                    "label": "Surveillance",
                    "found": boolean,
                    "sentiment": "negative" or "positive" or "neutral",
                    "snippet": "Direct quote or summary of what was found...",
                    "title": "Title of source",
                    "url": "URL of source"
                }}
            ],
            "reality_score_adjustment": (integer between -30 and +30 based on evidence balance)
        }}
        """

        # Use Gemini 2.5 Flash as it robustly supports the Google Search tool
        response = client.models.generate_content(
            model='gemini-2.5-flash',
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
        
        response = client.models.generate_content(
            model='gemini-2.5-flash',
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