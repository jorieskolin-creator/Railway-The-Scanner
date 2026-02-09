
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

def x_ray_search(company_name: str, company_url: str = None, model_id: str = None, intent_score: int = 0):
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
        return { "score": 60, "checklist": [], "links": [], "is_simulated": True, "confidence_score": 0, "washing_flag": False }

    # --- DEFINING THE PROMPT ONCE ---
    prompt = f"""
    Perform a FORENSIC INVESTIGATION on "{search_name}" (Domain: {domain_hint}).
    CONTEXT: Internal/Marketing text analysis suggests an Intent Score of {intent_score}/100.
    
    SEARCH STRATEGY (STRICT - APPROVED DOMAINS ONLY):
    Do NOT read the company's own marketing pages.
    Look ONLY at evidence from these specific 'JURIES' defined below.
    
    CRITICAL: You must execute searches for ALL 4 JURIES. Do not skip any.

    1. THE ARCHITECTURAL JURY (Engineering Reality)
       - Public Code & APIs: `site:github.com "{search_name}" OR site:postman.com "{search_name}"`
       - Engineering Culture: `site:{domain_hint} "engineering blog" OR "tech blog" OR "developers"`
       - Design Systems: `site:{domain_hint} "Design System" OR "UI Kit"`

    2. THE STRATEGIC JURY (Business Model & Procurement)
       - Cloud Sourcing (Finland): `site:hankintailmoitukset.fi "{search_name}" AND ("Cloud" OR "Pilvi" OR "SaaS")`
       - Recurring Revenue Evidence: `site:{domain_hint} filetype:pdf "Recurring Revenue" OR "ARR" OR "Jatkuva laskutus"`
       - Modern Tech Investments: `site:crunchbase.com "{search_name}" "Acquisitions"`

    3. THE CULTURAL JURY (Talent & Psychological Safety)
       - Boomerang Employees (High Trust): `site:linkedin.com "{search_name}" AND ("returned to" OR "rejoined")`
       - Inclusion Artifacts: `site:mimmitkoodaa.fi "{search_name}" OR "Women in Tech Finland" "{search_name}"`
       - Community Hosting: `site:meetup.com "{search_name}" AND ("Host" OR "Sponsor")`
       - Employee Voice: `site:glassdoor.com "{search_name}" OR site:teamblind.com "{search_name}"` (NO REDDIT)

    4. THE AI & DATA JURY (Future Readiness)
       - Real AI Models: `site:huggingface.co "{search_name}"`
       - Tech Stack Verification: `site:stackshare.io "{search_name}"`
       - Cloud Case Studies: `site:aws.amazon.com "Case Studies" "{search_name}" OR site:customers.microsoft.com "{search_name}"`
       - Deep Academic Audits: `site:theseus.fi "{search_name}" OR site:research.aalto.fi "{search_name}"`

    VERIFY THESE 5 HYPOTHESES:
    1. "Surveillance": Micromanagement/tracking?
    2. "Silos": Bureaucracy complaints?
    3. "Fake Agile": "Waterfall" or "Zombie Scrum"?
    4. "Platform Thinking": Public API/Design System found?
    5. "Legacy Inertia": Failed transformations or "Cloud Washing"?

    SCORING LOGIC & TEMPORAL WEIGHTING (CRITICAL):
    1. BASELINE: Start at 0 adjustment.
    2. RECENCY WEIGHTING: 
       - Evidence < 12 months old: 100% Impact (Heavy Weight).
       - Evidence > 18 months old: 20% Impact (Ignore unless recurring/unresolved).
       - Favor FRESH evidence over legacy complaints.
    3. TECHNICAL DEBT PENALTY:
       - If RECENT (< 12 months) evidence confirms "Technical Debt", "Legacy Systems", "Manual Workarounds", or "Excel Hell":
       - APPLY A SIGNIFICANT PENALTY (-15 to -25 points) to the adjustment.
       - This is a strong signal of low reality.
    4. POSITIVE SIGNALS:
       - Verified "Boomerang" hires, Hugging Face models, or Public Design Systems: +10 to +20 points.

    DETECT "WASHING" (Logic Check):
    - Intent Score is {intent_score}. 
    - RULE: "Washing" (Green/Agile/AI Washing) is ONLY possible if Intent Score is HIGH (>60) AND Reality is LOW.
    - IF Intent Score is LOW (<50), you CANNOT flag this as Washing.
    - AI WASHING: Claims of "AI-driven" but no technical evidence (Hugging Face/StackShare) found.
    - AGILE WASHING: Claims of "Tribes/Squads" but evidence of "Waterfall", "Approvals", "Bureaucracy".

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
                "is_recent": boolean (True if < 12 months)
            }}
        ],
        "reality_score_adjustment": (integer -40 to +40),
        "confidence_score": (integer 0 to 100),
        "washing_flag": boolean (True if significant contradiction/washing found AND Intent > 60),
        "washing_type": "AI Washing" or "Agile Washing" or "Green Washing" or null
    }}
    
    GUIDE FOR CONFIDENCE SCORE:
    - 0-40: Very few sources found, evidence is generic or very old.
    - 41-70: Some specific evidence found, but mixed recency or limited sources.
    - 71-100: Multiple independent sources (Juries) confirm findings, evidence is recent (<12mo) and specific.
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
                "sentiment": f.get("sentiment", "neutral"),
                "is_recent": f.get("is_recent", False)
            })
            
            if f.get("found") and f.get("url"):
                raw_findings.append({
                    "title": f.get("title", "Evidence Found"),
                    "snippet": f.get("snippet", ""),
                    "url": f.get("url", ""),
                    "source": get_clean_domain(f.get("url", "")),
                    "is_recent": f.get("is_recent", False)
                })
        
        adjustment = data.get("reality_score_adjustment", 0)
        confidence = data.get("confidence_score", 50) # Default to medium confidence if missing
        
        # Determine Washing based on AI flag OR raw gap if flag is missing
        washing_flag = data.get("washing_flag", False)
        washing_type = data.get("washing_type", None)
        
        final_score = max(0, min(100, base_score + adjustment))
        
        return {
            "score": int(final_score),
            "checklist": checklist,
            "links": raw_findings,
            "is_simulated": False,
            "confidence_score": int(confidence),
            "washing_flag": washing_flag,
            "washing_type": washing_type
        }
    
    # --- FALLBACK: SIMULATION ---
    print(f"   [Scanner 2] All models failed. Engaging AI Simulation for {company_name} (Fallback)...")
    try:
        client = genai.Client(api_key=api_key)
        sim_prompt = f"Generate 3 REALISTIC forensic search findings for {company_name}. Return JSON with 'findings', 'score' (0-100), 'confidence_score' (0-100), and 'washing_flag' (boolean)."
        response = client.models.generate_content(model="gemini-3-flash-preview", contents=sim_prompt)
        sim_data = json.loads(extract_json_from_text(response.text))
        return {
            "score": sim_data.get("score", 60),
            "checklist": [],
            "links": sim_data.get("findings", []),
            "is_simulated": True,
            "confidence_score": sim_data.get("confidence_score", 40),
            "washing_flag": sim_data.get("washing_flag", False),
            "washing_type": sim_data.get("washing_type", None)
        }
    except:
        return { "score": 60, "checklist": [], "links": [], "is_simulated": True, "confidence_score": 0, "washing_flag": False }
