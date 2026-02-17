
from fastapi import FastAPI, HTTPException, BackgroundTasks, Response, Request, UploadFile, File, Form
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel
from typing import Optional, List
import os
import uuid
import json
import requests
import io
import time
import concurrent.futures
from datetime import datetime
from bs4 import BeautifulSoup
from pypdf import PdfReader
from google import genai
from google.genai import types
import anthropic

from scanner_one import analyze_intent
from scanner_two import x_ray_search, extract_json_from_text, run_self_analysis, financial_health_search
from storage import BlobStorage
from seed_manager import run_batch_seed, scrape_strategic_content
from finland_list import TOP_100_COMPANIES, get_company_metadata
from analyze_scores import run_analysis
from knowledge_base import get_evolving_framework

# VERIFICATION LOG: v5.2.0 - Performance Optimizations
print("Starting Adaptability Scanner Backend (v5.2.0 - Performance Optimizations)...", flush=True)

def clean_env_var(value):
    if not value: return ""
    if "=" in value:
        try: value = value.split("=", 1)[1]
        except: pass
    return value.strip().strip('"').strip("'")


# --- ADMIN AUTHENTICATION ---
def verify_admin(request: Request):
    """Validate Authorization: Bearer <secret> header against ADMIN_SECRET env var."""
    admin_secret = clean_env_var(os.environ.get("ADMIN_SECRET", ""))
    if not admin_secret:
        raise HTTPException(status_code=503, detail="Admin access not configured on server.")
    auth_header = request.headers.get("authorization", "")
    if not auth_header.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="Authorization required.")
    token = auth_header[7:]
    if token != admin_secret:
        raise HTTPException(status_code=401, detail="Invalid credentials.")

app = FastAPI()

# --- CORS: Restrict to known origins ---
_allowed_origins = os.environ.get(
    "ALLOWED_ORIGINS",
    "https://the-scanner.up.railway.app"
).split(",")
app.add_middleware(
    CORSMiddleware,
    allow_origins=[o.strip() for o in _allowed_origins],
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)

storage = BlobStorage()

app.mount("/components", StaticFiles(directory="../components"), name="components")
app.mount("/services", StaticFiles(directory="../services"), name="services")

# --- FILE PROCESSING HELPERS ---
def extract_text_from_file(file: UploadFile) -> str:
    content = file.file.read()
    filename = file.filename.lower()
    text = ""
    
    try:
        if filename.endswith(".pdf"):
            pdf = PdfReader(io.BytesIO(content))
            for page in pdf.pages:
                text += page.extract_text() + "\n"
            print(f"   [File Parser] Extracted {len(text)} chars from PDF: {filename}")
            
        elif filename.endswith(".html") or filename.endswith(".htm"):
            soup = BeautifulSoup(content, 'html.parser')
            # Remove scripts and styles
            for script in soup(["script", "style"]):
                script.decompose()
            text = soup.get_text(separator=' ')
            print(f"   [File Parser] Extracted {len(text)} chars from HTML: {filename}")
        
        else:
            # Fallback for text files
            text = content.decode('utf-8', errors='ignore')
            
    except Exception as e:
        print(f"   [File Parser Error] Could not parse {filename}: {e}")
        return ""
        
    return text

# --- ROUTES ---

@app.get("/App.tsx")
async def get_app_tsx(): return FileResponse("../App.tsx", media_type="application/javascript")
@app.get("/index.tsx")
async def get_index_tsx(): return FileResponse("../index.tsx", media_type="application/javascript")
@app.get("/types.ts")
async def get_types_ts(): return FileResponse("../types.ts", media_type="application/javascript")
@app.get("/constants.ts")
async def get_constants_ts(): return FileResponse("../constants.ts", media_type="application/javascript")
@app.get("/")
async def read_root(): return FileResponse("../index.html")

@app.get("/api/health")
def health_check():
    token = clean_env_var(os.environ.get("BLOB_READ_WRITE_TOKEN"))
    return {
        "status": "Adaptability Scanner API Online (v5.2.0)", 
        "timestamp": datetime.utcnow().isoformat(),
        "storage_connected": bool(token)
    }

@app.get("/api/find-url")
async def find_company_url(name: str):
    """
    Uses Gemini Flash + Google Search Grounding to find the official URL.
    """
    api_key = clean_env_var(os.environ.get("GEMINI_API_KEY") or os.environ.get("API_KEY"))
    if not api_key: return {"url": ""}
    
    try:
        client = genai.Client(api_key=api_key)
        response = client.models.generate_content(
            model='gemini-3-flash-preview',
            contents=f"What is the official website homepage URL for the organization '{name}'? Return ONLY the URL string (e.g. https://www.example.com). Do not include markdown or explanations.",
            config={
                'tools': [{'google_search': {}}],
                'temperature': 0.0
            }
        )

        if response.text:
            url = response.text.strip()
            if "](" in url: url = url.split("](")[1].strip(")")
            url = url.strip("'").strip('"')
            if url.startswith("http"):
                return {"url": url}

        return {"url": ""}
    except Exception as gemini_err:
        print(f"   [API] Find URL Gemini failed: {gemini_err}. Trying Sonnet 4.5...")
        try:
            anthropic_key = clean_env_var(os.environ.get("ANTHROPIC_API_KEY", ""))
            if anthropic_key:
                claude_client = anthropic.Anthropic(api_key=anthropic_key)
                resp = claude_client.messages.create(
                    model="claude-sonnet-4-5-20250929",
                    max_tokens=100,
                    messages=[{"role": "user", "content": f"What is the official website homepage URL for '{name}'? Return ONLY the URL, nothing else."}]
                )
                url = (resp.content[0].text or "").strip().strip("'").strip('"')
                if url.startswith("http"):
                    return {"url": url}
        except Exception as e2:
            print(f"   [API] Find URL Sonnet fallback also failed: {e2}")
        return {"url": ""}

@app.get("/api/models")
def list_models():
    api_key = clean_env_var(os.environ.get("GEMINI_API_KEY") or os.environ.get("API_KEY"))
    if not api_key: return {"error": "No API Key configured on server."}
    try:
        client = genai.Client(api_key=api_key)
        models = list(client.models.list())
        available = [m.name for m in models if "generateContent" in m.supported_generation_methods]
        return {"count": len(available), "models": sorted(available)}
    except Exception as e:
        return {"error": str(e)}

@app.get("/dashboard-data")
async def get_dashboard_data(response: Response):
    response.headers["Cache-Control"] = "no-cache, no-store, must-revalidate"
    data = storage.retrieve_latest_data("index/summary_list")
    return data or []

@app.get("/api/analyze")
async def analyze_scores(response: Response):
    """Run statistical analysis across all company scores (correlations, distributions, archetypes)."""
    response.headers["Cache-Control"] = "no-cache, no-store, must-revalidate"
    data = storage.retrieve_latest_data("index/summary_list")
    if not data:
        raise HTTPException(status_code=404, detail="No dashboard data found.")
    return run_analysis(data)

@app.get("/company/{company_name}")
async def get_company_details(company_name: str, response: Response):
    response.headers["Cache-Control"] = "no-cache, no-store, must-revalidate"
    data = storage.get_company_data(company_name)
    if not data:
        raise HTTPException(status_code=404, detail="Company report not found in Vercel Blob.")
    return data

@app.get("/admin/company-list")
async def get_company_list(request: Request):
    verify_admin(request)
    return {"companies": TOP_100_COMPANIES}

@app.post("/admin/reset-database")
async def reset_database(request: Request):
    verify_admin(request)
    count = storage.nuke_everything()
    return {"status": "Database Wiped", "deleted_count": count}

# --- MAIN SCAN ENDPOINT (MULTIPART) ---
@app.post("/scan")
def run_scan(
    request: Request,
    companyName: str = Form(...),
    companyUrl: Optional[str] = Form(None),
    reportText: str = Form(""),
    saveToDatabase: bool = Form(True),
    modelId: Optional[str] = Form(None),
    files: List[UploadFile] = File(None)
):
    """
    Hybrid Forensic Scan: Accepts Text + URLs + Uploaded Documents (PDF/HTML).
    If Authorization header is present, validates it (admin refresh/batch).
    If absent, scan runs freely (New Diagnosis — public feature).
    """
    auth_header = request.headers.get("authorization", "")
    if auth_header:
        verify_admin(request)

    try:
        scan_start = time.time()
        print(f"🚀 --- LIVE SCAN INITIATED FOR: {companyName} ---")
        
        # --- MODEL SELECTION STRATEGY ---
        # Scanner 2 (Search) always uses Gemini 3 Pro (requires Google Search grounding)
        # Synthesis model varies by strategy
        search_model = "gemini-3-pro-preview"
        synth_model = "claude-opus-4-6"
        synth_provider = "anthropic"
        strategy = (modelId or "").strip()

        if strategy == "gemini-only":
            synth_model = "gemini-3-pro-preview"
            synth_provider = "gemini"
            print(f"   [Model Strategy] Gemini Only: Search=Gemini 3 Pro, Synthesis=Gemini 3 Pro")
        elif strategy == "sonnet-opus":
            synth_model = "claude-sonnet-4-5-20250929"
            synth_provider = "anthropic"
            print(f"   [Model Strategy] Sonnet+Opus: Search=Gemini 3 Pro, Draft=Sonnet 4.5, Final=Opus 4.6")
        elif strategy == "gemini-hybrid":
            synth_model = "gemini-2.5-pro"
            synth_provider = "gemini"
            print(f"   [Model Strategy] Gemini Hybrid: Search=Gemini 3 Pro, Synthesis=Gemini 2.5 Pro")
        else:
            print(f"   [Model Strategy] Default: Search=Gemini 3 Pro, Synthesis=Claude Opus 4.6")

        # --- PROCESS FILES ---
        combined_text = reportText if reportText else ""
        uploaded_doc_names = []
        
        if files:
            print(f"   [Upload] Processing {len(files)} uploaded documents...")
            for file in files:
                doc_text = extract_text_from_file(file)
                if doc_text:
                    combined_text += f"\n\n--- INTERNAL DOCUMENT: {file.filename} ---\n{doc_text}"
                    uploaded_doc_names.append(file.filename)
        
        # --- STEP 1: SCANNER 1 (Intent) ---
        target_url = companyUrl
        company_subsidiaries = None

        if not target_url:
            for c in TOP_100_COMPANIES:
                if c['name'].lower() == companyName.lower():
                    target_url = c['url']
                    company_subsidiaries = c.get('subsidiaries')
                    break
        else:
            # Also check for subsidiaries even when URL is provided
            for c in TOP_100_COMPANIES:
                if c['name'].lower() == companyName.lower():
                    company_subsidiaries = c.get('subsidiaries')
                    break

        if not combined_text or len(combined_text) < 100:
            if target_url: combined_text = scrape_strategic_content(target_url, companyName, subsidiaries=company_subsidiaries)
            else: combined_text = scrape_strategic_content(None, companyName)

        print(f"   🔄 [Scanner 1] Analyzing intent for {companyName}...")
        s1_start = time.time()
        intent_results = analyze_intent(combined_text)
        print(f"   ✅ [Scanner 1] Intent analysis complete in {int((time.time() - s1_start) * 1000)}ms → Score: {intent_results['score']}/100")

        # --- LOAD PREVIOUS SCANS (multi-scan history lookback, up to 3) ---
        previous_context = None
        existing_data = None
        try:
            existing_data = storage.get_company_data(companyName)
            if existing_data and existing_data.get("lastUpdated"):
                # Most recent scan (full data available)
                previous_context = {
                    "last_scanned": existing_data.get("lastUpdated"),
                    "previous_intent": existing_data.get("intent_score"),
                    "previous_reality": existing_data.get("reality_score"),
                    "previous_confidence": existing_data.get("confidence_score"),
                    "previous_findings": existing_data.get("reality_checklist", [])[:10],
                    "previous_evidence": existing_data.get("evidence_links", [])[:8],
                    "previous_washing": existing_data.get("washing_type"),
                    "previous_summary": existing_data.get("summary_text", ""),
                }

                # Older scans from enriched history (up to 2 more, newest first)
                raw_history = existing_data.get("history", [])
                older_scans = []
                for entry in reversed(raw_history):
                    if entry.get("summary_text") and len(entry["summary_text"]) > 20:
                        older_scans.append(entry)
                    if len(older_scans) >= 2:
                        break
                if older_scans:
                    previous_context["scan_history"] = older_scans

                scan_count = 1 + len(older_scans)
                print(f"   [History] {scan_count} previous scan(s) found (latest: {existing_data.get('lastUpdated')}). Enabling rescan mode.")
            else:
                print(f"   [History] No previous scan found. Running first-time analysis.")
        except Exception as e:
            print(f"   [History] Could not load previous data (non-critical): {e}")

        # --- STEP 2: SCANNER 2 (Reality) + FINANCIAL HEALTH (Parallel) ---
        print(f"   🔄 [Scanner 2 + Financial] Starting parallel execution for {companyName}...")
        s2_start = time.time()

        with concurrent.futures.ThreadPoolExecutor(max_workers=2) as executor:
            reality_future = executor.submit(
                x_ray_search, companyName, target_url,
                model_id=search_model, intent_score=intent_results["score"],
                previous_context=previous_context, max_research_loops=2,
                subsidiaries=company_subsidiaries
            )
            financial_future = executor.submit(
                financial_health_search, companyName, target_url,
                model_id=search_model
            )

            reality_results = reality_future.result()

            # Financial search is non-blocking — if it fails, use defaults
            try:
                financial_results = financial_future.result(timeout=120)
            except Exception as e:
                print(f"   [Financial Health] Failed (non-critical): {e}")
                financial_results = {
                    "financial_index": None,
                    "sub_scores": {"revenue_trend": None, "profitability": None, "efficiency": None, "investment_signal": None},
                    "financial_findings": [],
                    "financial_confidence": 0,
                    "is_simulated": True
                }

        print(f"   ✅ [Scanner 2] Reality check complete in {int((time.time() - s2_start) * 1000)}ms → Score: {reality_results['score']}/100, Confidence: {reality_results.get('confidence_score', 0)}/100")
        if financial_results.get("financial_index") is not None:
            print(f"   ✅ [Financial Health] Index: {financial_results['financial_index']}/100, Confidence: {financial_results['financial_confidence']}/100")

        # --- SCORE DRIFT GUARD ---
        # Prevents unrealistic score swings between rescans done within a short timeframe.
        # Latest scan is always the baseline — it's the current "snapshot" in the index.
        if previous_context and previous_context.get("previous_reality") is not None:
            prev_reality = previous_context["previous_reality"]
            new_reality = reality_results["score"]
            max_drift = 15  # Maximum allowed score change per rescan within 7 days

            try:
                last_scanned = previous_context.get("last_scanned", "")
                days_since = (datetime.utcnow() - datetime.fromisoformat(last_scanned)).days
            except (ValueError, TypeError):
                days_since = 999  # If parsing fails, don't apply guard

            if days_since < 7 and isinstance(prev_reality, (int, float)):
                raw_delta = new_reality - prev_reality
                if abs(raw_delta) >= max_drift:
                    direction = 1 if raw_delta > 0 else -1
                    dampened = int(prev_reality + (direction * max_drift))
                    dampened = max(0, min(100, dampened))
                    print(f"   [Drift Guard] Score change too large for {days_since}d window: {prev_reality} → {new_reality} (delta {raw_delta}). Dampened to {dampened}.")
                    reality_results["score"] = dampened
                    reality_results["drift_guard_applied"] = True
                    reality_results["raw_score_before_dampening"] = new_reality

                    # --- SELF-ANALYSIS: Why did the scanner produce different results? ---
                    print(f"   🔄 [Self-Analysis] Triggered: score delta {raw_delta} within {days_since}d.")
                    sa_start = time.time()
                    self_analysis = run_self_analysis(
                        company_name=companyName,
                        previous_scan_data=previous_context,
                        current_scan_data=reality_results,
                        days_since_last=days_since
                    )
                    if self_analysis:
                        reality_results["self_analysis"] = self_analysis
                        print(f"   ✅ [Self-Analysis] Complete in {int((time.time() - sa_start) * 1000)}ms → Cause: {self_analysis.get('primary_cause', '?')}")
                    else:
                        print(f"   ⚠️ [Self-Analysis] No result ({int((time.time() - sa_start) * 1000)}ms)")

        gap = intent_results["score"] - reality_results["score"]
        confidence = reality_results.get("confidence_score", 50)
        washing_flag = reality_results.get("washing_flag", False)
        washing_type = reality_results.get("washing_type", None)
        key_people = reality_results.get("key_people", [])

        # --- LOGIC CONSISTENCY CHECK ---
        # CRITICAL FIX: "Washing" is impossible if Reality >= Intent.
        # It implies they are doing MORE than they say.
        if reality_results["score"] >= intent_results["score"]:
            if washing_flag:
                print(f"   [Logic Override] Washing Flag set to FALSE. Reality ({reality_results['score']}) >= Intent ({intent_results['score']}). Reality exceeds narrative.")
            washing_flag = False
            washing_type = None
        
        # Also impossible if Ambition is very low (e.g. < 40)
        if intent_results["score"] < 40:
             if washing_flag:
                print(f"   [Logic Override] Washing Flag set to FALSE. Intent ({intent_results['score']}) is too low to qualify as Washing.")
             washing_flag = False
             washing_type = None

        # --- STEP 3: SYNTHESIS ---
        summary = "AI Summary Unavailable"
        pitch = "Pitch Unavailable"

        anthropic_key = clean_env_var(os.environ.get("ANTHROPIC_API_KEY"))
        has_synth_key = anthropic_key if synth_provider == "anthropic" else (os.environ.get("GEMINI_API_KEY") or os.environ.get("API_KEY"))

        if has_synth_key:
            try:
                claude_client = anthropic.Anthropic(api_key=anthropic_key) if anthropic_key else None
                intent_narrative = json.dumps(intent_results["breakdown"])
                verified_links = [l for l in reality_results['links'] if l.get('verified', True)]
                evidence_list = ', '.join([f"{l['title']}: {l.get('snippet', '')}" for l in verified_links[:20]])
                knowledge_context = get_evolving_framework()

                doc_context = ""
                if uploaded_doc_names:
                    doc_context = f"NOTE: Analysis includes confidential internal documents: {', '.join(uploaded_doc_names)}. Treat this as high-fidelity intent data."

                # --- ARCHETYPE LOGIC (Reality vs Financial) ---
                # Uses Reality Score and Financial Index to classify companies.
                # Fallback: if Financial Index is unavailable, use Intent Score.
                fin_score = financial_results.get("financial_index")
                fin_for_archetype = fin_score if fin_score is not None else intent_results["score"]
                if reality_results["score"] >= 50:
                    archetype = "Thriving" if fin_for_archetype >= 50 else "Modernizing"
                else:
                    archetype = "Cash Cow" if fin_for_archetype >= 50 else "At Risk"

                # Logic-aware Context
                washing_context = ""
                if washing_flag:
                    washing_context = f"CRITICAL: The forensic scanner DETECTED {washing_type or 'WASHING'}. High ambition narrative but low operational evidence."

                # Financial context
                financial_context = ""
                if financial_results.get("financial_index") is not None:
                    fi = financial_results
                    findings = fi.get('financial_findings', [])
                    findings_text = '; '.join(
                        f.get('assessment', '') if isinstance(f, dict) else str(f)
                        for f in findings[:5]
                    ) if findings else 'No specific findings'
                    financial_context = f"FINANCIAL HEALTH CONTEXT (do NOT cite numeric scores — the index is displayed separately): {findings_text}. You may reference financial health qualitatively (e.g. 'declining revenue', 'strong profitability') but never mention specific index numbers."

                # Trend context (only when rescan — now multi-point)
                trend_context = ""
                if previous_context:
                    prev_i = previous_context.get("previous_intent", "N/A")
                    prev_r = previous_context.get("previous_reality", "N/A")
                    prev_date = previous_context.get("last_scanned", "unknown")
                    if isinstance(prev_r, (int, float)) and isinstance(reality_results["score"], (int, float)):
                        delta = reality_results["score"] - prev_r
                        if delta > 5:
                            direction = "IMPROVING"
                        elif delta < -5:
                            direction = "DECLINING"
                        else:
                            direction = "STABLE"
                    else:
                        direction = "UNKNOWN"

                    # Build multi-point trend line from scan_history
                    trend_points = []
                    scan_history = previous_context.get("scan_history", [])
                    for older in reversed(scan_history):  # oldest first
                        od = older.get("date", "?")
                        oi = older.get("intent_score", "?")
                        or_ = older.get("reality_score", "?")
                        trend_points.append(f"  {od}: Intent {oi}, Reality {or_}")
                    trend_points.append(f"  {prev_date}: Intent {prev_i}, Reality {prev_r}")
                    trend_points.append(f"  NOW: (current scan — scores determined by your independent evaluation)")
                    trend_line = "\n".join(trend_points)

                    trend_context = f"""TREND DATA (RESCAN — {len(trend_points)} data points):
Score trajectory (oldest → newest):
{trend_line}
Overall direction: {direction}
Include the trend trajectory in the summary. Note persistent patterns or turning points."""

                # Previous scan context for richer synthesis
                prev_synthesis_block = ""
                if previous_context:
                    prev_summary = previous_context.get("previous_summary", "")
                    prev_findings = previous_context.get("previous_findings", [])
                    prev_evidence = previous_context.get("previous_evidence", [])

                    parts = []
                    if prev_summary:
                        parts.append(f"Previous Summary:\n{prev_summary}")
                    if prev_findings:
                        findings_lines = []
                        for f in prev_findings[:10]:
                            sentiment = f.get("sentiment", "neutral")
                            query = f.get("query", f.get("label", "Unknown"))
                            found = f.get("found", False)
                            findings_lines.append(f"  [{sentiment.upper()}] {query} (found={found})")
                        parts.append(f"Previous Findings:\n" + "\n".join(findings_lines))
                    if prev_evidence:
                        ev_lines = [f"  - {e.get('title', '?')}: {e.get('snippet', '')[:100]}" for e in prev_evidence[:8]]
                        parts.append(f"Previous Evidence Sources:\n" + "\n".join(ev_lines))

                    if parts:
                        prev_synthesis_block = f"""
### PREVIOUS SCAN CONTEXT (for narrative continuity)
{chr(10).join(parts)}
Use this to maintain consistency. If issues from previous scan are still present, note them as persistent.
If new evidence shows improvement, highlight the positive trajectory."""

                synthesis_prompt = f"""### PRIMARY FRAMEWORK (KNOWLEDGE BASE — MANDATORY)
{knowledge_context}

YOU MUST use the terminology, archetypes, and criteria defined in the Knowledge Base above.
Every claim in the summary MUST map to a Knowledge Base dimension or pattern.
Do NOT invent your own categories — use ONLY the vocabulary from the Knowledge Base.
NEVER use internal codes (TAC-xxx, DIM-xxx, etc.) in any output — always write the full concept name in plain language.

### SCAN DATA
Target: {companyName}
DIAGNOSIS ARCHETYPE: {archetype.upper()}
Archetype definitions (Reality vs Financial health):
  - THRIVING: High Reality + Strong Financials. Modern operations AND healthy business.
  - MODERNIZING: High Reality + Weak Financials. Strong operations but financial pressure.
  - CASH COW: Low Reality + Strong Financials. Profitable but operationally outdated.
  - AT RISK: Low Reality + Weak Financials. Both operations and finances under pressure.
Summary Confidence: {confidence}/100
NOTE: Do NOT cite specific numeric scores in the summary text. The scores are displayed separately in the UI. Describe performance qualitatively only (e.g. "low reality evidence", "strong intent narrative").

Evidence Found: {evidence_list}
Intent Data: {intent_narrative}
{doc_context}
{washing_context}
{financial_context}
{trend_context}
{prev_synthesis_block}

### TASK
Generate a JSON object with four fields: 'summary', 'pitch', 'reality_score', and 'intent_score'.

FIRST — INDEPENDENT SCORE EVALUATION:
You have the Knowledge Base, the evidence, and the intent data above. Before writing the summary,
evaluate INDEPENDENTLY what the Reality Score and Intent Score should be on a 0-100 scale.

For 'reality_score' (0-100), evaluate across 5 Knowledge Base dimensions:
  - Operating Model (0-20): Is there evidence of modern practices? Or legacy/waterfall/silos?
  - Strategy (0-20): Clear digital/modernization strategy with execution? Or vague/absent?
  - Culture (0-20): Learning culture, psychological safety, talent signals? Or hierarchy/control?
  - Service Portfolio (0-20): Product-led, API-first, composable? Or monolithic/legacy?
  - AI Readiness (0-20): Concrete AI/ML adoption evidence? Or just buzzwords?
Sum the 5 dimensions (0-100). Score each dimension based on VERIFIED EVIDENCE ONLY.
If no evidence exists for a dimension, score it 10 (neutral, not generous).

For 'intent_score' (0-100), evaluate: How ambitious is their narrative vs. what's typical?
Score based on the Intent Data provided above.

THEN — WRITE THE NARRATIVE:

1. 'summary' (180-220 words):
   - STATE THE ARCHETYPE ({archetype}) explicitly in the first sentence.
   - Use ONLY the four archetype labels: Thriving, Modernizing, Cash Cow, At Risk.
   - MAP findings to Knowledge Base dimensions (Operating Model, Strategy, Culture, Service Portfolio, AI Readiness).
   - USE Knowledge Base terminology (e.g. "Digital Taylorism", "Flow Efficiency", "Pilot Purgatory").
   - NEVER use internal Knowledge Base codes (e.g. TAC-003, TAC-008, DIM-xx). Always write the full concept name instead.
   - Explicitly mention 'Summary Confidence' if low (<50).
   - If Washing detected, use term "{washing_type or 'Modernization Washing'}".
   - Focus on WHY they fit this archetype, grounded in evidence.

2. 'pitch' (90-110 words):
   - A direct, cold-outreach pitch to a C-level executive at {companyName}.
   - Reference specific challenges found using Knowledge Base terminology.
   - Derive exactly 2 CONCRETE PROPOSALS from the Knowledge Base dimensions where
     the company scored weakest. Each proposal must name the KB dimension and suggest
     a specific improvement direction (e.g. "Shift from Digital Taylorism toward
     Flow Efficiency by restructuring around stream-aligned teams").
   - Do NOT use the phrase "Forensic Modernization Audit" or any generic service names.
   - The proposals must feel specific to THIS company, not a template.
   - Tone: Professional, provocative, insight-led.

Output valid JSON only. Must include all four fields: summary, pitch, reality_score, intent_score."""

                response_text = ""

                synth_start = time.time()

                if synth_provider == "anthropic":
                    # --- Anthropic Synthesis (Opus or Sonnet) ---
                    if strategy == "sonnet-opus":
                        # Two-pass: Sonnet 4.5 drafts, Opus 4.6 refines
                        print(f"   🔄 [Synthesis] Attempting Sonnet 4.5 (draft pass)...")
                        p1_start = time.time()
                        draft_resp = claude_client.messages.create(
                            model="claude-sonnet-4-5-20250929",
                            max_tokens=1024,
                            messages=[{"role": "user", "content": synthesis_prompt}]
                        )
                        draft_text = draft_resp.content[0].text if draft_resp.content else ""
                        print(f"   ✅ [Synthesis] Sonnet 4.5 draft succeeded in {int((time.time() - p1_start) * 1000)}ms")

                        print(f"   🔄 [Synthesis] Attempting Opus 4.6 (refine pass)...")
                        p2_start = time.time()
                        refine_prompt = f"""Review and refine this company analysis draft. Keep the JSON structure (summary + pitch fields).
Improve clarity, sharpen the Knowledge Base terminology, and make the pitch more compelling.
Do NOT change the scores or archetype (Thriving/Modernizing/Cash Cow/At Risk) — only improve the prose quality.
NEVER use internal Knowledge Base codes (TAC-xxx, DIM-xxx) — always write the full concept name.

DRAFT:
{draft_text}

Output valid JSON only."""
                        refine_resp = claude_client.messages.create(
                            model="claude-opus-4-6",
                            max_tokens=1024,
                            messages=[{"role": "user", "content": refine_prompt}]
                        )
                        response_text = refine_resp.content[0].text if refine_resp.content else draft_text
                        print(f"   ✅ [Synthesis] Opus 4.6 refine succeeded in {int((time.time() - p2_start) * 1000)}ms")
                    else:
                        # Single-pass Anthropic (default = Opus 4.6)
                        print(f"   🔄 [Synthesis] Attempting {synth_model}...")
                        response = claude_client.messages.create(
                            model=synth_model,
                            max_tokens=1024,
                            messages=[{"role": "user", "content": synthesis_prompt}]
                        )
                        response_text = response.content[0].text if response.content else ""
                        print(f"   ✅ [Synthesis] {synth_model} succeeded in {int((time.time() - synth_start) * 1000)}ms")

                elif synth_provider == "gemini":
                    # --- Gemini Synthesis with fallback chain: selected model → 2.5 Pro → Sonnet 4.5 ---
                    print(f"   🔄 [Synthesis] Attempting {synth_model} (Gemini)...")
                    gemini_key = os.environ.get("GEMINI_API_KEY") or os.environ.get("API_KEY")
                    try:
                        g_start = time.time()
                        gemini_client = genai.Client(api_key=gemini_key)
                        gemini_resp = gemini_client.models.generate_content(
                            model=synth_model,
                            contents=synthesis_prompt,
                            config=types.GenerateContentConfig(
                                temperature=0.7,
                                max_output_tokens=1024
                            )
                        )
                        response_text = gemini_resp.text or ""
                        print(f"   ✅ [Synthesis] {synth_model} succeeded in {int((time.time() - g_start) * 1000)}ms")
                    except Exception as gemini_err:
                        print(f"   ❌ [Synthesis] {synth_model} failed: {gemini_err}")
                        # Try Gemini 2.5 Pro as backup (if not already the selected model)
                        if synth_model != "gemini-2.5-pro":
                            try:
                                print(f"   🔄 [Synthesis] Falling back to Gemini 2.5 Pro...")
                                g2_start = time.time()
                                gemini_client = genai.Client(api_key=gemini_key)
                                gemini_resp = gemini_client.models.generate_content(
                                    model="gemini-2.5-pro",
                                    contents=synthesis_prompt,
                                    config=types.GenerateContentConfig(
                                        temperature=0.7,
                                        max_output_tokens=1024
                                    )
                                )
                                response_text = gemini_resp.text or ""
                                print(f"   ✅ [Synthesis] Gemini 2.5 Pro succeeded in {int((time.time() - g2_start) * 1000)}ms")
                            except Exception as gemini_err2:
                                print(f"   ❌ [Synthesis] Gemini 2.5 Pro also failed: {gemini_err2}")
                                if claude_client:
                                    print(f"   🔄 [Synthesis] Falling back to Sonnet 4.5...")
                                    s_start = time.time()
                                    fallback_resp = claude_client.messages.create(
                                        model="claude-sonnet-4-5-20250929",
                                        max_tokens=1024,
                                        messages=[{"role": "user", "content": synthesis_prompt}]
                                    )
                                    response_text = fallback_resp.content[0].text if fallback_resp.content else ""
                                    print(f"   ✅ [Synthesis] Sonnet 4.5 fallback succeeded in {int((time.time() - s_start) * 1000)}ms")
                        else:
                            if claude_client:
                                print(f"   🔄 [Synthesis] Falling back to Sonnet 4.5...")
                                s_start = time.time()
                                fallback_resp = claude_client.messages.create(
                                    model="claude-sonnet-4-5-20250929",
                                    max_tokens=1024,
                                    messages=[{"role": "user", "content": synthesis_prompt}]
                                )
                                response_text = fallback_resp.content[0].text if fallback_resp.content else ""
                                print(f"   ✅ [Synthesis] Sonnet 4.5 fallback succeeded in {int((time.time() - s_start) * 1000)}ms")

                if response_text:
                    payload = extract_json_from_text(response_text)
                    if payload:
                         json_data = json.loads(payload)
                         summary = json_data.get("summary", summary)
                         pitch = json_data.get("pitch", pitch) or pitch

                         # --- OPUS SCORE VERIFICATION ---
                         opus_reality = json_data.get("reality_score")
                         opus_intent = json_data.get("intent_score")
                         gemini_reality = reality_results["score"]
                         scanner_intent = intent_results["score"]

                         if opus_reality is not None and isinstance(opus_reality, (int, float)):
                             opus_reality = max(0, min(100, int(opus_reality)))
                             delta_r = abs(opus_reality - gemini_reality)

                             if delta_r <= 5:
                                 # Agreement zone — keep Gemini's search-grounded score
                                 print(f"   ✅ [Score Verify] Reality: Gemini {gemini_reality} ≈ Opus {opus_reality} (delta {delta_r}). Keeping Gemini score.")
                             elif delta_r <= 10:
                                 # Mild disagreement — weighted average (60% Gemini, 40% Opus)
                                 blended = int(gemini_reality * 0.6 + opus_reality * 0.4)
                                 print(f"   🔄 [Score Verify] Reality: Gemini {gemini_reality} vs Opus {opus_reality} (delta {delta_r}). Blended → {blended}")
                                 reality_results["score"] = blended
                                 reality_results["opus_reality_score"] = opus_reality
                                 reality_results["score_verification"] = "blended"
                             else:
                                 # Strong disagreement — Opus has more context, use its score
                                 print(f"   ⚠️ [Score Verify] Reality: Gemini {gemini_reality} vs Opus {opus_reality} (delta {delta_r}). Opus override → {opus_reality}")
                                 reality_results["score"] = opus_reality
                                 reality_results["opus_reality_score"] = opus_reality
                                 reality_results["score_verification"] = "opus_override"

                             # Recalculate gap if score changed
                             if reality_results["score"] != gemini_reality:
                                 gap = intent_results["score"] - reality_results["score"]

                                 # Re-evaluate washing flag
                                 if reality_results["score"] >= intent_results["score"]:
                                     washing_flag = False
                                     washing_type = None

                         if opus_intent is not None and isinstance(opus_intent, (int, float)):
                             opus_intent = max(0, min(100, int(opus_intent)))
                             delta_i = abs(opus_intent - scanner_intent)
                             if delta_i > 10:
                                 print(f"   ⚠️ [Score Verify] Intent: Scanner 1 {scanner_intent} vs Opus {opus_intent} (delta {delta_i}). Opus override → {opus_intent}")
                                 intent_results["score"] = opus_intent
                                 intent_results["opus_intent_score"] = opus_intent
                                 gap = intent_results["score"] - reality_results["score"]
                             elif delta_i <= 5:
                                 print(f"   ✅ [Score Verify] Intent: Scanner 1 {scanner_intent} ≈ Opus {opus_intent} (delta {delta_i}). Keeping Scanner 1 score.")
                             else:
                                 blended_i = int(scanner_intent * 0.6 + opus_intent * 0.4)
                                 print(f"   🔄 [Score Verify] Intent: Scanner 1 {scanner_intent} vs Opus {opus_intent} (delta {delta_i}). Blended → {blended_i}")
                                 intent_results["score"] = blended_i
                                 gap = intent_results["score"] - reality_results["score"]

                    else:
                         summary = response_text

            except Exception as e:
                print(f"   [Synthesis Error] {e}")
        else:
            print(f"   [Synthesis] WARNING: No API key found for {synth_provider}. Summary unavailable.")

        new_timestamp = datetime.utcnow().isoformat()
        result_id = str(uuid.uuid4())

        # Build rolling history chart (reuse existing_data loaded earlier)
        # Enriched: each entry now carries summary + scores for multi-scan lookback
        history = []
        if existing_data:
            history = existing_data.get("history", [])
            history.append({
                "date": existing_data.get("lastUpdated"),
                "gap": existing_data.get("reality_gap", 0),
                "intent_score": existing_data.get("intent_score"),
                "reality_score": existing_data.get("reality_score"),
                "confidence_score": existing_data.get("confidence_score"),
                "washing_type": existing_data.get("washing_type"),
                "summary_text": (existing_data.get("summary_text", "") or "")[:2500],
                "findings": existing_data.get("reality_checklist", [])[:10],
                "evidence": existing_data.get("evidence_links", [])[:8],
            })
            if len(history) > 12: history = history[-12:]

        # --- COMPANY METADATA (from finland_list.py) ---
        meta = get_company_metadata(companyName) or {}
        company_domain = meta.get("domain", "Other")
        company_revenue = meta.get("revenue_avg_3yr", None)
        company_employees = meta.get("employees", None)
        company_ownership = meta.get("ownership", None)
        company_hq = meta.get("hq_country", None)

        # --- FINAL ARCHETYPE (computed from finalized scores) ---
        fin_score = financial_results.get("financial_index")
        fin_for_archetype = fin_score if fin_score is not None else intent_results["score"]
        if reality_results["score"] >= 50:
            archetype = "Thriving" if fin_for_archetype >= 50 else "Modernizing"
        else:
            archetype = "Cash Cow" if fin_for_archetype >= 50 else "At Risk"

        full_data = {
            "id": result_id,
            "companyName": companyName,
            "companyUrl": target_url,
            "domain": company_domain,
            "revenue_avg_3yr": company_revenue,
            "employees": company_employees,
            "ownership": company_ownership,
            "hq_country": company_hq,
            "intent_score": intent_results["score"],
            "reality_score": reality_results["score"],
            "reality_gap": gap,
            "confidence_score": confidence,
            "archetype": archetype,
            "washing_flag": washing_flag,
            "washing_type": washing_type,
            "intent_breakdown": intent_results["breakdown"],
            "metrics": intent_results.get("metrics", {}),
            "reality_checklist": reality_results["checklist"],
            "evidence_links": reality_results["links"],
            "key_people": key_people,
            "summary_text": summary,
            "pitch_text": pitch,
            "lastUpdated": new_timestamp,
            "history": history,
            "is_simulated": reality_results.get("is_simulated", False),
            "uploaded_docs": uploaded_doc_names,
            "drift_guard_applied": reality_results.get("drift_guard_applied", False),
            "raw_score_before_dampening": reality_results.get("raw_score_before_dampening"),
            "self_analysis": reality_results.get("self_analysis"),
            "financial_index": financial_results.get("financial_index"),
            "financial_sub_scores": financial_results.get("sub_scores", {}),
            "financial_findings": financial_results.get("financial_findings", []),
            "financial_confidence": financial_results.get("financial_confidence", 0)
        }

        # --- STEP 4: SAVE ---
        if saveToDatabase:
            print(f"   🔄 [Storage] Saving report to Vercel Blob...")
            save_start = time.time()
            saved_url = storage.save_company_data(companyName, full_data)
            print(f"   ✅ [Storage] Saved in {int((time.time() - save_start) * 1000)}ms → {saved_url}")

            summary_entry = {
                "id": result_id,
                "companyName": companyName,
                "domain": company_domain,
                "revenue_avg_3yr": company_revenue,
                "employees": company_employees,
                "ownership": company_ownership,
                "hq_country": company_hq,
                "confidence_score": confidence,
                "intent_score": intent_results["score"],
                "reality_score": reality_results["score"],
                "reality_gap": gap,
                "lastUpdated": new_timestamp,
                "detailsUrl": saved_url,
                "has_docs": len(uploaded_doc_names) > 0,
                "financial_index": financial_results.get("financial_index"),
                "archetype": archetype
            }
            # Update index
            storage.update_global_index(summary_entry)

        total_ms = int((time.time() - scan_start) * 1000)
        total_sec = round(total_ms / 1000, 1)
        print(f"✅ --- SCAN COMPLETE: {companyName} --- Total: {total_sec}s | Intent: {intent_results['score']}/100 | Reality: {reality_results['score']}/100 | Gap: {gap} | Archetype: {archetype} | Confidence: {confidence}/100 ---")

        return {"success": True, "data": full_data}

    except Exception as e:
        elapsed = round(time.time() - scan_start, 1) if 'scan_start' in locals() else 0
        print(f"❌ --- SCAN FAILED: {companyName} --- After {elapsed}s | Error: {str(e)} ---")
        raise HTTPException(status_code=500, detail=str(e))
