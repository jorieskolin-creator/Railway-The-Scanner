
import os
import json
import time
import re
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

def extract_json_from_text(text):
    """
    Robustly extracts the first JSON object found in a string.
    Handles Markdown code blocks, conversational filler, etc.
    """
    if not text: 
        return None
    
    # 1. Try finding a markdown block first
    markdown_match = re.search(r"```json\s*(\{.*?\})\s*```", text, re.DOTALL)
    if markdown_match:
        return markdown_match.group(1)

    # 2. Fallback: Find the first outer curly braces
    match = re.search(r'\{.*\}', text, re.DOTALL)
    if match:
        return match.group(0)
        
    return None

def x_ray_search(company_name: str, company_url: str = None, model_id: str = None):
    """
    SCANNER 2: THE REALITY CHECK (External Evidence)
    
    This function performs the Forensic Scan.
    It uses the 'Standardized Tribunal' methodology with STRICT domain filtering.
    """
    
    api_key = os.environ.get("GEMINI_API_KEY") or os.environ.get("API_KEY")
    # Default to the chosen model, or fallback to the reliable 3-pro
    primary_model = model_id or "gemini-3-pro-preview"
    
    search_name = clean_company_name_for_search(company_name)
    domain_hint = get_clean_domain(company_url) if company_url else f"{search_name.lower().replace(' ', '')}.fi"
    
    if not api_key:
        print("   [Scanner 2] CRITICAL: No GEMINI_API_KEY found. Using simulation fallback.")
        return { "score": 60, "checklist": [], "links": [], "is_simulated": True }

    # --- DEFINING THE PROMPT ONCE ---
    prompt = f"""
    Perform a FORENSIC INVESTIGATION on "{search_name}" (Domain: {domain_hint}).
    
    SEARCH STRATEGY (STRICT - APPROVED DOMAINS ONLY):
    Do NOT read the company's own marketing pages.
    Look ONLY at evidence from these specific 'JURIES' defined below.
    
    CRITICAL: You must execute searches for ALL 4 JURIES. Do not skip any.

    1. THE ARCHITECTURAL JURY (Tech & Structure)
       - Public Developer Portals: `site:{domain_hint} "API documentation" OR "Developer Portal"`
       - System Status Pages: `site:{domain_hint} "System Status" OR "Häiriötiedotteet"`
       - Technical Communities: `site:stackoverflow.com "{search_name}"`

    2. THE STRATEGIC JURY (Money & Intent)
       - Capital Markets Day (CMD): `site:{domain_hint} filetype:pdf "Capital Markets Day" OR "Strategy"`
       - Risk Reports: `site:{domain_hint} filetype:pdf "Annual Report" AND "Risk Management"`

    3. THE COMMUNITY JURY (Culture & Reality)
       - TeamBlind & Reddit: `site:teamblind.com "{search_name}"` OR `site:reddit.com "{search_name}"`
       - Employee Voice: `site:glassdoor.com "{search_name}"` OR `site:linkedin.com "{search_name}"`

    4. THE ACADEMIC JURY (Deep Audits)
       - Theses: `site:theseus.fi "{search_name}"`

    VERIFY THESE 5 HYPOTHESES:
    1. "Surveillance": Micromanagement/tracking?
    2. "Silos": Bureaucracy complaints?
    3. "Fake Agile": "Waterfall" or "Zombie Scrum"?
    4. "Platform Thinking": Public API found?
    5. "Legacy Inertia": Failed transformations?

    TEMPORAL WEIGHTING (STATUTE OF LIMITATIONS) - CRITICAL:
    - Check the DATE of findings, especially in the Community Jury.
    - If a negative finding (e.g., toxic culture complaint) is OLDER than 18 months, IGNORE IT or weight it very low (20% impact). Companies change.
    - If a finding is RECENT (< 12 months), weight it at 100%.
    - Favor FRESH evidence over legacy complaints.

    RETURN STRICT JSON:
    {{
        "findings": [
            {{
                "category": "Operating Model",
                "label": "Brief Label",
                "found": boolean,
                "sentiment": "negative" or "positive",
                "snippet": "Quote...",
                "title": "Source Title",
                "url": "URL",
                "is_recent": boolean (True if < 18 months)
            }}
        ],
        "reality_score_adjustment": (integer -40 to +40)
    }}
    """

    def execute_with_model(target_model_name):
        print(f"   [Forensic Scan] Target: {company_name} | Model: {target_model_name}")
        try:
            client = genai.Client(api_key=api_key)
            response = client.models.generate_content(
                model=target_model_name,
                contents=prompt,
                config={
                    'tools': [{'google_search': {}}],
                    'temperature': 0.0,
                    'safety_settings': [
                        {'category': 'HARM_CATEGORY_HARASSMENT', 'threshold': 'BLOCK_NONE'},
                        {'category': 'HARM_CATEGORY_HATE_SPEECH', 'threshold': 'BLOCK_NONE'},
                        {'category': 'HARM_CATEGORY_SEXUALLY_EXPLICIT', 'threshold': 'BLOCK_NONE'},
                        {'category': 'HARM_CATEGORY_DANGEROUS_CONTENT', 'threshold': 'BLOCK_NONE'}
                    ]
                }
            )
            
            text_content = response.text if response.text else ""
            json_payload = extract_json_from_text(text_content)
            
            if json_payload:
                return json.loads(json_payload)
            else:
                return None # Signal failure
        except Exception as e:
            print(f"   [Scanner 2 Error] Model {target_model_name} failed: {e}")
            return None

    # --- ATTEMPT 1: User Selected Model ---
    data = execute_with_model(primary_model)

    # --- ATTEMPT 2: Smart Upgrade (if needed) ---
    # If the first attempt failed AND we haven't already tried the best model, try the best model.
    if not data and primary_model != "gemini-3-pro-preview":
        print("   [Scanner 2] Primary model failed to return JSON. AUTO-UPGRADING to Gemini 3 Pro Preview...")
        data = execute_with_model("gemini-3-pro-preview")

    # --- PROCESS RESULTS ---
    checklist = []
    raw_findings = []
    # BASE SCORE UPDATE: Raised from 50 to 60.
    # Assumption: Companies are slightly "Good" until proven "Bad".
    # This prevents neutral search results from dragging a good company into Stagnation.
    base_score = 60

    if data:
        findings = data.get("findings", [])
        for f in findings:
            checklist.append({
                "category": f.get("category", "General"),
                "query": f.get("label", "Signal"),
                "found": f.get("found", False),
                "sentiment": f.get("sentiment", "neutral")
            })
            
            if f.get("found") and f.get("url"):
                raw_findings.append({
                    "title": f.get("title", "Evidence Found"),
                    "snippet": f.get("snippet", ""),
                    "url": f.get("url", ""),
                    "source": get_clean_domain(f.get("url", ""))
                })
        
        adjustment = data.get("reality_score_adjustment", 0)
        final_score = max(0, min(100, base_score + adjustment))
        
        return {
            "score": int(final_score),
            "checklist": checklist,
            "links": raw_findings,
            "is_simulated": False
        }
    
    # --- FALLBACK: SIMULATION ---
    print(f"   [Scanner 2] All models failed. Engaging AI Simulation for {company_name} (Fallback)...")
    try:
        client = genai.Client(api_key=api_key)
        sim_prompt = f"Generate 3 REALISTIC forensic search findings for {company_name}. Return JSON with 'findings' and 'score'."
        response = client.models.generate_content(model="gemini-3-flash-preview", contents=sim_prompt)
        sim_data = json.loads(extract_json_from_text(response.text))
        return {
            "score": sim_data.get("score", 60),
            "checklist": [],
            "links": sim_data.get("findings", []),
            "is_simulated": True
        }
    except:
        return { "score": 60, "checklist": [], "links": [], "is_simulated": True }
