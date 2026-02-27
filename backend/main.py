
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
import threading
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
from reality_check import validate_summary, validate_pitch, ai_fact_check_summary, clean_summary, purge_false_evidence

# VERIFICATION LOG: v5.4.0 - Extended Thinking + Search Budget Upgrade
print("Starting Adaptability Scanner Backend (v5.4.0 - Extended Thinking + Search Budget Upgrade)...", flush=True)

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

# --- SERVER-SIDE BATCH STATE ---
_batch_state = {
    "is_running": False,
    "current": 0,
    "total": 0,
    "current_name": "",
    "started_at": None,
    "completed_at": None,
    "failed": [],
}
_batch_lock = threading.Lock()
_batch_stop_event = threading.Event()

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
        print(f"   [API] Find URL Gemini failed: {gemini_err}. Trying Sonnet 4.6...")
        try:
            anthropic_key = clean_env_var(os.environ.get("ANTHROPIC_API_KEY", ""))
            if anthropic_key:
                claude_client = anthropic.Anthropic(api_key=anthropic_key)
                resp = claude_client.messages.create(
                    model="claude-sonnet-4-6",
                    max_tokens=100,
                    messages=[{"role": "user", "content": f"What is the official website homepage URL for '{name}'? Return ONLY the URL, nothing else."}]
                )
                url = (resp.content[0].text or "").strip().strip("'").strip('"')
                if url.startswith("http"):
                    return {"url": url}
        except Exception as e2:
            print(f"   [API] Find URL Sonnet fallback also failed: {e2}")
        return {"url": ""}

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

@app.delete("/admin/company/{company_name}")
async def delete_company(company_name: str, request: Request):
    """Delete a single company's data from blob storage and global index."""
    verify_admin(request)
    result = storage.delete_company(company_name)
    return {
        "status": "deleted",
        "company": company_name,
        "deleted_files": result["deleted_files"],
        "removed_from_index": result["removed_from_index"]
    }

@app.get("/admin/review-data")
async def review_data(request: Request):
    """Returns all stored companies with summary previews and hallucination flags for admin review."""
    verify_admin(request)
    companies = storage.get_all_companies_summary()
    return {"companies": companies, "total": len(companies)}

def _recheck_company_data(company_name: str, data: dict) -> dict:
    """
    Shared per-company recheck logic: heuristic check → pitch check → AI fact-check → auto-clean.
    Used by both single-company recheck and recheck-all to guarantee identical procedure.

    Returns dict with summary_check, pitch_check, ai_verification, recommendation,
    and cleaning results if auto-clean was performed.
    """
    # Step 1: Heuristic validation (summary + pitch)
    summary_result = validate_summary(
        summary_text=data.get("summary_text", ""),
        company_name=company_name,
        key_people=data.get("key_people", []),
        evidence_links=data.get("evidence_links", []),
        confidence_score=data.get("confidence_score"),
    )
    pitch_result = validate_pitch(
        pitch_text=data.get("pitch_text", ""),
        company_name=company_name,
    )

    # Step 2: AI-powered fact-check (Gemini 3.1 Pro + Google Search grounding)
    ai_result = ai_fact_check_summary(
        summary_text=data.get("summary_text", ""),
        evidence_links=data.get("evidence_links", []),
        company_name=company_name,
        pitch_text=data.get("pitch_text", ""),
    )

    # Step 3: Persist AI verification result back to stored data
    data["ai_verification"] = {
        "verified": ai_result["verified"],
        "false_claims": ai_result["false_claims"],
        "false_claims_detailed": ai_result.get("false_claims_detailed", []),
        "model": ai_result["fact_check_model"],
        "time_ms": ai_result["fact_check_time_ms"],
    }
    storage.save_company_data(company_name, data)
    storage.update_global_index_field(company_name, "ai_verified", ai_result["verified"])

    # Step 4: Auto-clean if AI found false claims
    # Prefer corrected summary from fact-checker (inline correction) over separate clean
    clean_result = None
    if not ai_result["verified"] and ai_result["false_claims"]:
        corrected_summary = ai_result.get("corrected_summary", "")
        corrected_pitch = ai_result.get("corrected_pitch", "")
        if corrected_summary:
            # Fact-checker produced corrected summary — apply directly
            data["summary_text"] = corrected_summary
            if corrected_pitch:
                data["pitch_text"] = corrected_pitch
            data["ai_verification"]["verified"] = True
            data["ai_verification"]["false_claims"] = []
            data["ai_verification"]["note"] = "Corrected by Gemini 3.1 Pro fact-checker inline"
            data["summary_cleaned"] = True
            storage.save_company_data(company_name, data, post_clean=True)
            storage.update_global_index_field(company_name, "ai_verified", True)
            clean_result = {"action": "cleaned", "claims_removed": len(ai_result["false_claims"]), "re_verified": True, "remaining_issues": []}
            print(f"   ✅ [Admin Recheck] Applied corrected summary from fact-checker for {company_name}")
        else:
            # Fallback: separate clean
            clean_result = _clean_company_data(company_name, data)

    # Step 5: Determine recommendation
    if summary_result["risk"] == "high":
        recommendation = "Refresh database — high hallucination risk, full rescan needed"
    elif clean_result and clean_result.get("action") == "cleaned" and clean_result.get("re_verified"):
        recommendation = "OK — cleaned successfully"
    elif clean_result and clean_result.get("action") == "cleaned" and not clean_result.get("re_verified"):
        recommendation = "Deep scan recommended — cleaned but still has unverified claims"
    elif clean_result and clean_result.get("action") == "no_change":
        recommendation = "Deep scan recommended — cleaning could not resolve false claims"
    elif clean_result and clean_result.get("action") == "failed":
        recommendation = "Refresh database — cleaning failed, full rescan needed"
    elif not ai_result["verified"]:
        recommendation = "Deep scan recommended — false claims found by fact-check"
    elif summary_result["risk"] == "medium":
        recommendation = "Review manually"
    else:
        recommendation = "OK"

    result = {
        "company": company_name,
        "summary_check": summary_result,
        "pitch_check": pitch_result,
        "ai_verification": ai_result,
        "recommendation": recommendation,
    }

    if clean_result:
        result["clean_action"] = clean_result.get("action", "skipped")
        result["claims_removed"] = clean_result.get("claims_removed", 0)
        result["re_verified"] = clean_result.get("re_verified", False)
        result["remaining_issues"] = clean_result.get("remaining_issues", [])

    return result


@app.post("/admin/recheck-company/{company_name}")
async def recheck_company(company_name: str, request: Request):
    """
    Run reality check on an already-stored company's summary.
    Same procedure as recheck-all: heuristic check → AI fact-check → auto-clean if needed.
    Returns recommendation on whether deep scan or full refresh is needed.
    """
    verify_admin(request)
    data = storage.get_company_data(company_name)
    if not data:
        raise HTTPException(status_code=404, detail=f"Company '{company_name}' not found in database.")

    return _recheck_company_data(company_name, data)

@app.post("/admin/recheck-all")
async def recheck_all_companies(request: Request):
    """Run reality check on ALL stored companies. Flags issues and auto-cleans them."""
    verify_admin(request)
    companies = storage.get_all_companies_summary()
    flagged = []
    cleaned_results = []
    clean = 0

    for company in companies:
        name = company.get("companyName", "")
        if not name:
            continue
        data = storage.get_company_data(name)
        if not data:
            continue

        result = _recheck_company_data(name, data)

        summary_risk = result["summary_check"]["risk"]
        ai_verified = result["ai_verification"]["verified"]
        is_flagged = summary_risk in ("medium", "high") or not ai_verified

        if is_flagged:
            flag_entry = {
                "companyName": name,
                "risk": summary_risk,
                "score": result["summary_check"]["score"],
                "flags": result["summary_check"]["flags"],
                "ai_verified": ai_verified,
                "false_claims": result["ai_verification"]["false_claims"],
                "recommendation": result["recommendation"],
                "summary_preview": (data.get("summary_text", "") or "")[:200],
            }

            if result.get("clean_action"):
                flag_entry["clean_action"] = result["clean_action"]
                flag_entry["claims_removed"] = result.get("claims_removed", 0)
                flag_entry["re_verified"] = result.get("re_verified", False)
                if result["clean_action"] == "cleaned":
                    cleaned_results.append({
                        "companyName": name,
                        "claims_removed": result["claims_removed"],
                        "re_verified": result["re_verified"],
                    })

            flagged.append(flag_entry)
        else:
            clean += 1

    return {
        "total_checked": len(companies),
        "clean": clean,
        "flagged_count": len(flagged),
        "flagged": flagged,
        "auto_cleaned": len(cleaned_results),
        "cleaned": cleaned_results,
    }


def _clean_company_data(company_name: str, data: dict) -> dict:
    """
    Shared cleaning logic for admin recheck: purge false evidence, full re-synthesis, re-verify, save.
    Uses the SAME synthesis prompt, Knowledge Base, tone, and instructions as the Deep Scanning pipeline
    to ensure 100% consistency between scan-time and cleaning-time AI Diagnosis and Pitch generation.

    Returns dict with action result (skipped/failed/cleaned).
    """
    ai_verification = data.get("ai_verification", {})
    # Support both new field name (false_claims) and legacy (unsupported_claims)
    false_claims = ai_verification.get("false_claims") or ai_verification.get("unsupported_claims", [])
    detailed_claims = ai_verification.get("false_claims_detailed") or ai_verification.get("unsupported_claims_detailed", [])

    if ai_verification.get("verified", True) or not false_claims:
        return {
            "company": company_name,
            "action": "skipped",
            "reason": "No false claims found — summary is already verified.",
        }

    print(f"   🧹 [Clean] Cleaning {len(false_claims)} false claim(s) from {company_name} using full re-synthesis (same as Deep Scan)...")

    # Step 1: Purge contaminated evidence sources BEFORE re-synthesis
    purge_result = purge_false_evidence(data, false_claims, detailed_claims=detailed_claims)

    # Step 2: Build the SAME synthesis prompt used in Deep Scanning
    # This ensures 100% identical logic, tone, Knowledge Base terminology, and instructions.
    knowledge_context = get_evolving_framework()
    intent_narrative = json.dumps(data.get("intent_breakdown", {}))
    clean_evidence_links = data.get("evidence_links", [])
    clean_verified = [l for l in clean_evidence_links if l.get('verified', True)]
    evidence_list = ', '.join([f"{l.get('title', 'Unknown')}: {l.get('snippet', '')}" for l in clean_verified[:20]])

    confidence = data.get("confidence_score", 50)
    fin_score = data.get("financial_index")
    fin_for_arch = fin_score if fin_score is not None else data.get("intent_score", 0)
    washing_flag = data.get("washing_flag", False)
    washing_type = data.get("washing_type")

    washing_context = ""
    if washing_flag:
        washing_context = f"CRITICAL: The forensic scanner DETECTED {washing_type or 'WASHING'}. High ambition narrative but low operational evidence."

    financial_context = ""
    if fin_score is not None:
        findings = data.get("financial_findings", [])
        findings_text = '; '.join(
            f.get('assessment', '') if isinstance(f, dict) else str(f)
            for f in findings[:5]
        ) if findings else 'No specific findings'
        financial_context = f"FINANCIAL HEALTH CONTEXT (do NOT cite numeric scores — the index is displayed separately): {findings_text}. You may reference financial health qualitatively (e.g. 'declining revenue', 'strong profitability') but never mention specific index numbers."

    doc_context = ""
    uploaded_doc_names = data.get("uploaded_docs", [])
    if uploaded_doc_names:
        doc_context = f"NOTE: Analysis includes confidential internal documents: {', '.join(uploaded_doc_names)}. Treat this as high-fidelity intent data."

    # Same synthesis prompt as Deep Scanning (main.py scan pipeline)
    synthesis_prompt = f"""You are a senior organizational analyst conducting a forensic modernization assessment.
Your task: synthesize scanner evidence into a precise, evidence-grounded diagnosis using the Knowledge Base framework below.

METHODOLOGY: Read ALL evidence first. Score ONLY what evidence supports. Write ONLY what scores justify.
If evidence is thin, say so — a short honest analysis beats a long fabricated one.

### PRIMARY FRAMEWORK (KNOWLEDGE BASE — MANDATORY)
{knowledge_context}

You MUST use the terminology, archetypes, and criteria defined in the Knowledge Base above.
Every claim in the summary MUST trace back to specific evidence AND map to a Knowledge Base dimension.
Do NOT invent categories — use ONLY Knowledge Base vocabulary.
NEVER use internal codes (TAC-xxx, DIM-xxx, etc.) — always write the full concept name.

### SCAN DATA
Target: {company_name}
Financial Health Index: {fin_for_arch} ({"actual financial index" if fin_score is not None else "fallback: using intent score"})
Summary Confidence: {confidence}/100

Evidence Found: {evidence_list}
Intent Data: {intent_narrative}
{doc_context}
{washing_context}
{financial_context}

### ANALYSIS PROTOCOL

STEP 1 — REALITY SCORE (evaluate before writing anything):
Score each Knowledge Base dimension (0-20) based on VERIFIED EVIDENCE ONLY:
  - Operating Model: Evidence of modern practices (DevOps, platform teams, stream-aligned)? Or legacy/waterfall/silos?
  - Strategy: Clear digital/modernization strategy with visible execution? Or vague/absent?
  - Culture: Learning culture, psychological safety, talent signals? Or hierarchy/control/fear?
  - Service Portfolio: Product-led, API-first, composable architecture? Or monolithic/legacy?
  - AI Readiness: Concrete AI/ML adoption with measurable outcomes? Or just buzzwords/pilot purgatory?

Scoring rules:
  - No evidence for a dimension = score 10 (neutral). Never score generously without evidence.
  - Evidence of aspiration (website claims, press releases) without execution evidence = score 8-12, not higher.
  - Sum the 5 dimensions → 'reality_score' (0-100).
  - Do NOT evaluate intent_score or financial_index — those are immutable pipeline outputs.

STEP 2 — ARCHETYPE DERIVATION (mechanical, from scores):
Use YOUR reality_score + Financial Health Index ({fin_for_arch}):
  - reality_score >= 50 AND Financial Health Index >= 50 → "Thriving"
  - reality_score >= 50 AND Financial Health Index < 50 → "Modernizing"
  - reality_score < 50 AND Financial Health Index >= 50 → "Cash Cow"
  - reality_score < 50 AND Financial Health Index < 50 → "At Risk"
This is a mechanical derivation — do not override based on narrative interpretation.

STEP 3 — SUMMARY (180-220 words):
Write a diagnostic narrative that reads like an expert assessment, not a template:
  - First sentence: State the archetype and what defines this company's position.
  - Body: Map 3-4 key findings to specific KB dimensions. For each finding, connect the evidence
    to the KB pattern it reveals.
  - Use KB terminology naturally — but only where the evidence supports the pattern.
  - If confidence < 50, state it: "Limited public evidence constrains this assessment."
  - If washing detected, name it: "{washing_type or 'Modernization Washing'}" with the specific gap.
  - Do NOT cite numeric scores in the text — scores display separately in the UI.
  - Do NOT fabricate findings. If evidence is sparse, acknowledge gaps rather than inventing patterns.
  - Use ONLY the four archetype labels: Thriving, Modernizing, Cash Cow, At Risk.

STEP 4 — PITCH (90-110 words):
A direct cold-outreach message to a C-level executive at {company_name}:
  - Open with a specific insight from the analysis that would make them stop and read.
  - Derive exactly 2 CONCRETE PROPOSALS targeting the company's weakest KB dimensions.
  - The proposals must feel specific to THIS company's situation, not generic consulting templates.
  - Do NOT use "Forensic Modernization Audit" or any generic service names.
  - Tone: Professional, provocative, insight-led. The recipient should feel understood, not sold to.

### GUARDRAILS
- NEVER fabricate evidence, people, events, partnerships, or metrics not present in the scan data.
- NEVER invent temporal claims ("recently announced", "just launched", "this quarter").
- Every factual claim in the summary must be traceable to a specific piece of evidence above.

### OUTPUT
Respond with valid JSON only. Four fields required:
{{"summary": "...", "pitch": "...", "reality_score": <int 0-100>, "archetype": "<Thriving|Modernizing|Cash Cow|At Risk>"}}

### CRITICAL CONTEXT — FACT-CHECK CORRECTIONS
The previous synthesis contained false claims detected by an independent fact-checker (Gemini 3.1 Pro with Google Search).
The following claims were FALSE and must NOT appear in your output:
{chr(10).join(f"- {c}" for c in false_claims)}

The contaminated evidence sources have been removed. You are now working with CLEANED evidence only.
Generate a completely fresh synthesis. Do NOT reference or re-introduce any of the false claims listed above."""

    # Step 3: Full Gemini re-synthesis — same model chain as Deep Scanning re-synthesis
    resynth_start = time.time()
    resynth_text = ""
    resynth_model_used = ""

    gemini_key = os.environ.get("GEMINI_API_KEY") or os.environ.get("API_KEY")

    # Primary: Gemini 3.1 Pro Preview (same as Deep Scanning re-synthesis)
    if gemini_key:
        try:
            from google import genai
            from google.genai import types
            print(f"   🔄 [Clean Re-Synthesis] Attempting gemini-3.1-pro-preview...")
            gemini_client = genai.Client(api_key=gemini_key)
            resynth_response = gemini_client.models.generate_content(
                model="gemini-3.1-pro-preview",
                contents=synthesis_prompt,
                config=types.GenerateContentConfig(
                    temperature=0.5,
                    thinking_config=types.ThinkingConfig(thinking_level="MEDIUM"),
                    max_output_tokens=2048
                )
            )
            resynth_text = resynth_response.text or ""
            resynth_model_used = "gemini-3.1-pro-preview"
            resynth_ms = int((time.time() - resynth_start) * 1000)
            print(f"   ✅ [Clean Re-Synthesis] Gemini 3.1 Pro complete ({resynth_ms}ms)")
        except Exception as g31_err:
            print(f"   ❌ [Clean Re-Synthesis] Gemini 3.1 Pro failed: {g31_err}")

    # Fallback 1: Gemini 3.1 Pro Review (replaces Gemini 3 Pro)
    if not resynth_text and gemini_key:
        try:
            from google import genai
            from google.genai import types
            print(f"   🔄 [Clean Re-Synthesis] Falling back to gemini-3.1-pro-review...")
            gemini_client = genai.Client(api_key=gemini_key)
            resynth_response = gemini_client.models.generate_content(
                model="gemini-3.1-pro-review",
                contents=synthesis_prompt,
                config=types.GenerateContentConfig(
                    temperature=0.5,
                    thinking_config=types.ThinkingConfig(thinking_level="MEDIUM"),
                    max_output_tokens=2048
                )
            )
            resynth_text = resynth_response.text or ""
            resynth_model_used = "gemini-3.1-pro-review"
            resynth_ms = int((time.time() - resynth_start) * 1000)
            print(f"   ✅ [Clean Re-Synthesis] Gemini 3.1 Pro Review complete ({resynth_ms}ms)")
        except Exception as g3_err:
            print(f"   ❌ [Clean Re-Synthesis] Gemini 3.1 Pro Review failed: {g3_err}")

    # Fallback 2: Gemini 2.5 Pro (separate rate limits from 3.x family)
    if not resynth_text and gemini_key:
        try:
            from google import genai
            from google.genai import types
            print(f"   🔄 [Clean Re-Synthesis] Falling back to gemini-2.5-pro...")
            gemini_client = genai.Client(api_key=gemini_key)
            resynth_response = gemini_client.models.generate_content(
                model="gemini-2.5-pro",
                contents=synthesis_prompt,
                config=types.GenerateContentConfig(
                    temperature=0.0,
                    max_output_tokens=2048
                )
            )
            resynth_text = resynth_response.text or ""
            resynth_model_used = "gemini-2.5-pro"
            resynth_ms = int((time.time() - resynth_start) * 1000)
            print(f"   ✅ [Clean Re-Synthesis] Gemini 2.5 Pro complete ({resynth_ms}ms)")
        except Exception as g25_err:
            resynth_ms = int((time.time() - resynth_start) * 1000)
            print(f"   ❌ [Clean Re-Synthesis] All re-synthesis models failed. Last error: {g25_err} ({resynth_ms}ms)")

    # Step 4: Parse re-synthesis result and apply
    if resynth_text:
        resynth_payload = extract_json_from_text(resynth_text)
        if resynth_payload:
            resynth_json = json.loads(resynth_payload)
            new_summary = resynth_json.get("summary", "")
            new_pitch = resynth_json.get("pitch", "")

            if new_summary:
                data["summary_text"] = new_summary
            if new_pitch:
                data["pitch_text"] = new_pitch

            # Update reality score if Gemini re-evaluated it
            resynth_reality = resynth_json.get("reality_score")
            if resynth_reality is not None and isinstance(resynth_reality, (int, float)):
                resynth_reality = max(0, min(100, int(resynth_reality)))
                data["reality_score"] = resynth_reality
                data["reality_gap"] = data.get("intent_score", 0) - resynth_reality

            # --- ARCHETYPE RECALCULATION (same as scan pipeline) ---
            reality = data.get("reality_score", 0)
            if reality >= 50:
                expected_archetype = "Thriving" if fin_for_arch >= 50 else "Modernizing"
            else:
                expected_archetype = "Cash Cow" if fin_for_arch >= 50 else "At Risk"

            old_archetype = data.get("archetype")
            data["archetype"] = expected_archetype

            # Verify archetype from re-synthesis matches deterministic rule
            resynth_archetype = resynth_json.get("archetype")
            if resynth_archetype and resynth_archetype != expected_archetype:
                print(f"   ⚖️ [Clean Re-Synthesis] Archetype '{resynth_archetype}' → corrected to '{expected_archetype}'")
                valid_archetypes = ["Thriving", "Modernizing", "Cash Cow", "At Risk"]
                for wrong_label in valid_archetypes:
                    if wrong_label == expected_archetype:
                        continue
                    data["summary_text"] = data["summary_text"].replace(wrong_label, expected_archetype)

            if old_archetype and old_archetype != expected_archetype:
                print(f"   ⚖️ [Clean] Archetype '{old_archetype}' → corrected to '{expected_archetype}' (scores: Reality={reality}, Financial={fin_for_arch})")

            # Re-evaluate washing flag
            if reality >= data.get("intent_score", 0):
                data["washing_flag"] = False
                data["washing_type"] = None

            # Re-verify the re-synthesized summary with Gemini fact-check
            print(f"   🔄 [Clean] Re-verifying re-synthesized summary with Gemini fact-check...")
            re_verify = ai_fact_check_summary(
                summary_text=data["summary_text"],
                evidence_links=data.get("evidence_links", []),
                company_name=company_name,
            )

            data["ai_verification"] = {
                "verified": re_verify["verified"],
                "false_claims": re_verify["false_claims"],
                "false_claims_detailed": re_verify.get("false_claims_detailed", []),
                "model": re_verify["fact_check_model"],
                "time_ms": re_verify["fact_check_time_ms"],
            }
            data["summary_cleaned"] = {
                "was_cleaned": True,
                "method": "full_re_synthesis",
                "claims_removed": len(false_claims),
                "original_claims": false_claims,
                "clean_model": resynth_model_used,
                "clean_time_ms": resynth_ms,
                "evidence_purged": purge_result,
            }

            # Clear washing flags if re-verification passes
            if re_verify["verified"]:
                data["washing_flag"] = False
                data["washing_type"] = None
                print(f"   ✅ [Clean] Cleared washing flags (false claims removed, fresh synthesis)")

            saved_url = storage.save_company_data(company_name, data, post_clean=True)
            storage.update_global_index_fields(company_name, {
                "ai_verified": re_verify["verified"],
                "archetype": expected_archetype,
                "reality_score": data.get("reality_score", 0),
                "reality_gap": data.get("reality_gap", 0),
                "detailsUrl": saved_url,
            })

            print(f"   ✅ [Clean] {company_name}: Full re-synthesis from cleaned evidence. Re-verified: {re_verify['verified']}")

            return {
                "company": company_name,
                "action": "cleaned",
                "claims_removed": len(false_claims),
                "original_claims": false_claims,
                "re_verified": re_verify["verified"],
                "remaining_issues": re_verify["false_claims"],
                "clean_time_ms": resynth_ms,
                "verify_time_ms": re_verify.get("fact_check_time_ms", 0),
            }

    # Re-synthesis failed — save with false claims flagged
    resynth_ms = int((time.time() - resynth_start) * 1000)
    print(f"   ⚠️ [Clean Re-Synthesis] Could not re-synthesize — saving with false claims flagged")
    data["summary_cleaned"] = {
        "was_cleaned": False,
        "method": "full_re_synthesis",
        "claims_removed": 0,
        "original_claims": false_claims,
        "clean_model": resynth_model_used or "none",
        "clean_time_ms": resynth_ms,
        "reason": "Re-synthesis failed — all Gemini models unavailable",
    }
    storage.save_company_data(company_name, data)

    return {
        "company": company_name,
        "action": "failed",
        "reason": "Re-synthesis failed — all Gemini models unavailable",
        "original_claims": false_claims,
        "clean_time_ms": resynth_ms,
        "verify_time_ms": 0,
    }


@app.post("/admin/clean-summary/{company_name}")
async def clean_company_summary(company_name: str, request: Request):
    """
    Clean false claims from a company's summary using full re-synthesis.

    If AI fact-check found false_claims (verified=false), this endpoint
    purges contaminated evidence and performs a full Gemini re-synthesis
    using the same prompt, Knowledge Base, tone, and instructions as the
    Deep Scanning pipeline. Then re-verifies with fact-check.
    """
    verify_admin(request)
    data = storage.get_company_data(company_name)
    if not data:
        raise HTTPException(status_code=404, detail=f"Company '{company_name}' not found in database.")

    return _clean_company_data(company_name, data)


@app.post("/admin/clean-all-unverified")
async def clean_all_unverified(request: Request):
    """
    Clean false claims from ALL unverified companies in the database.
    Iterates through all companies, finds those with ai_verified=false,
    and attempts to clean their summaries using the shared cleaning logic.
    """
    verify_admin(request)
    companies = storage.get_all_companies_summary()
    cleaned = []
    skipped = 0
    failed = []

    for company in companies:
        name = company.get("companyName", "")
        if not name:
            continue

        # Only process unverified companies
        if company.get("ai_verified", True):
            skipped += 1
            continue

        data = storage.get_company_data(name)
        if not data:
            continue

        print(f"   🧹 [Clean All] Processing {name}...")
        result = _clean_company_data(name, data)

        if result["action"] == "skipped":
            skipped += 1
        elif result["action"] == "failed":
            failed.append({"companyName": name, "reason": result.get("reason", "unknown")})
        elif result["action"] == "cleaned":
            cleaned.append({
                "companyName": name,
                "claims_removed": result["claims_removed"],
                "re_verified": result["re_verified"],
            })

    return {
        "total_checked": len(companies),
        "cleaned": len(cleaned),
        "skipped": skipped,
        "failed": len(failed),
        "results": cleaned,
        "failures": failed,
    }


# --- SERVER-SIDE BATCH REFRESH ---

def _run_batch_background():
    """Background thread: runs the full batch scan, updating _batch_state in-place."""
    batch_progress = {
        "current": 0,
        "total": 0,
        "current_name": "",
        "failed": [],
    }

    def on_progress(summary_list):
        """Called by run_batch_seed after each company save."""
        with _batch_lock:
            _batch_state["current"] = batch_progress["current"]
            _batch_state["current_name"] = batch_progress["current_name"]
            _batch_state["failed"] = batch_progress["failed"]

    try:
        run_batch_seed(
            real_xray_mode=True,
            limit=None,
            progress_callback=on_progress,
            max_workers=5,
            batch_progress=batch_progress,
            stop_event=_batch_stop_event,
        )
    except Exception as e:
        print(f"   [Batch Background] Fatal error: {e}")
    finally:
        with _batch_lock:
            _batch_state["is_running"] = False
            _batch_state["completed_at"] = datetime.utcnow().isoformat()
            _batch_state["current"] = batch_progress["current"]
            _batch_state["current_name"] = ""
            _batch_state["failed"] = batch_progress["failed"]


@app.post("/batch-refresh")
def start_batch_refresh(request: Request):
    """Starts a full database refresh in a background thread. Returns immediately."""
    verify_admin(request)

    with _batch_lock:
        if _batch_state["is_running"]:
            raise HTTPException(status_code=409, detail="Batch refresh already in progress.")
        _batch_stop_event.clear()
        _batch_state["is_running"] = True
        _batch_state["current"] = 0
        _batch_state["total"] = len(TOP_100_COMPANIES)
        _batch_state["current_name"] = "Starting..."
        _batch_state["started_at"] = datetime.utcnow().isoformat()
        _batch_state["completed_at"] = None
        _batch_state["failed"] = []

    thread = threading.Thread(target=_run_batch_background, daemon=True)
    thread.start()

    return {"status": "started", "total": len(TOP_100_COMPANIES)}


@app.post("/batch-refresh/stop")
def stop_batch_refresh(request: Request):
    """Signals the running batch to stop after the current company finishes."""
    verify_admin(request)
    _batch_stop_event.set()
    return {"status": "stop_requested"}


@app.get("/batch-status")
def get_batch_status():
    """Returns current batch refresh progress. No auth required (read-only)."""
    with _batch_lock:
        return dict(_batch_state)


# --- MAIN SCAN ENDPOINT (MULTIPART) ---
@app.post("/scan")
def run_scan(
    request: Request,
    companyName: str = Form(...),
    companyUrl: Optional[str] = Form(None),
    reportText: str = Form(""),
    saveToDatabase: bool = Form(True),
    files: List[UploadFile] = File(None)
):
    """
    Hybrid Forensic Scan: Accepts Text + URLs + Uploaded Documents (PDF/HTML).
    Requires admin authentication.
    """
    verify_admin(request)

    try:
        scan_start = time.time()
        # Global scan time budget (seconds). All phases must fit within this window.
        # Railway proxy timeout is typically 300-600s; 540s (9 min) keeps us safely under.
        # Budget allocation:
        #   Reality check: budget - 150s (reserves time for synthesis + post-processing)
        #   Financial:     120s (parallel with reality, non-blocking)
        #   Self-analysis: only if remaining > 120s
        #   Synthesis:     60s per model call (with fallback chain)
        SCAN_TOTAL_BUDGET = 540
        print(f"🚀 --- LIVE SCAN INITIATED FOR: {companyName} (budget: {SCAN_TOTAL_BUDGET}s) ---")

        # Model pipeline: Sonnet 4.6 + Web Search (forensic) | Gemini 2.5 Pro (financial) | Opus 4.6 (synthesis)
        # Forensic fallback: Gemini 3.1 Pro. Synthesis fallback: Gemini 3.1 Pro → Gemini 3 Pro
        search_model = "gemini-3.1-pro-preview"  # Used as forensic fallback if Sonnet fails

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
        # Allocate time: reserve 150s for synthesis + self-analysis + post-processing
        elapsed_so_far = time.time() - scan_start
        reality_budget = max(180, int(SCAN_TOTAL_BUDGET - elapsed_so_far - 150))
        reality_outer_timeout = reality_budget + 10  # small buffer over internal budget
        print(f"   🔄 [Scanner 2 + Financial] Starting parallel execution for {companyName} (reality budget: {reality_budget}s)...")
        s2_start = time.time()

        with concurrent.futures.ThreadPoolExecutor(max_workers=2) as executor:
            reality_future = executor.submit(
                x_ray_search, companyName, target_url,
                model_id=search_model, intent_score=intent_results["score"],
                previous_context=previous_context, max_research_loops=2,
                subsidiaries=company_subsidiaries,
                time_budget=reality_budget
            )
            financial_future = executor.submit(
                financial_health_search, companyName, target_url
            )

            try:
                reality_results = reality_future.result(timeout=reality_outer_timeout)
            except (concurrent.futures.TimeoutError, Exception) as e:
                print(f"   ❌ [Scanner 2] Reality search failed for {companyName}: {e}")
                # PARTIAL UPDATE: reuse existing reality data if available
                if existing_data and existing_data.get("reality_score") is not None:
                    print(f"   🔄 [Scanner 2] Reusing previous reality data (score: {existing_data['reality_score']}, confidence: {existing_data.get('confidence_score', 0)})")
                    reality_results = {
                        "score": existing_data["reality_score"],
                        "checklist": existing_data.get("reality_checklist", []),
                        "links": existing_data.get("evidence_links", []),
                        "key_people": existing_data.get("key_people", []),
                        "is_simulated": False,
                        "confidence_score": existing_data.get("confidence_score", 0),
                        "washing_flag": existing_data.get("washing_flag", False),
                        "washing_type": existing_data.get("washing_type"),
                        "reused_from_previous": True,
                    }
                else:
                    reality_results = { "score": 50, "checklist": [], "links": [], "key_people": [], "is_simulated": True, "confidence_score": 0, "washing_flag": False }

            # Financial search is non-blocking — if it fails, use existing data or defaults
            try:
                financial_results = financial_future.result(timeout=120)
            except Exception as e:
                print(f"   [Financial Health] Failed (non-critical): {e}")
                if existing_data and existing_data.get("financial_index") is not None:
                    print(f"   🔄 [Financial Health] Reusing previous financial data (index: {existing_data['financial_index']})")
                    financial_results = {
                        "financial_index": existing_data["financial_index"],
                        "sub_scores": existing_data.get("financial_sub_scores", {}),
                        "financial_findings": existing_data.get("financial_findings", []),
                        "financial_confidence": existing_data.get("financial_confidence", 0),
                        "is_simulated": False,
                        "reused_from_previous": True,
                    }
                else:
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

        # --- SCORE DRIFT DIAGNOSTIC ---
        # Detects large score swings between rescans for diagnostic purposes.
        # Does NOT dampen scores — Opus is the Final Judge and its score is authoritative.
        # When delta >= 25 within 7 days, triggers Self-Analysis to understand why.
        if previous_context and previous_context.get("previous_reality") is not None:
            prev_reality = previous_context["previous_reality"]
            new_reality = reality_results["score"]
            drift_threshold = 25  # Matches the rescan trigger threshold in scanner_two

            try:
                last_scanned = previous_context.get("last_scanned", "")
                days_since = (datetime.utcnow() - datetime.fromisoformat(last_scanned)).days
            except (ValueError, TypeError):
                days_since = 999  # If parsing fails, don't apply guard

            if days_since < 7 and isinstance(prev_reality, (int, float)):
                raw_delta = new_reality - prev_reality
                if abs(raw_delta) >= drift_threshold:
                    print(f"   ⚠️ [Drift Diagnostic] Large score change in {days_since}d window: {prev_reality} → {new_reality} (delta {raw_delta}). Opus will be final judge.")
                    reality_results["large_drift_detected"] = True
                    reality_results["drift_delta"] = raw_delta

                    # --- SELF-ANALYSIS: Why did the scanner produce different results? ---
                    # Only run if we have enough time remaining (need ~120s for synthesis)
                    sa_remaining = SCAN_TOTAL_BUDGET - (time.time() - scan_start)
                    if sa_remaining > 120:
                        print(f"   🔄 [Self-Analysis] Triggered: score delta {raw_delta} within {days_since}d. ({int(sa_remaining)}s remaining)")
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
                    else:
                        print(f"   ⏰ [Self-Analysis] Skipped — only {int(sa_remaining)}s remaining (need 120s for synthesis)")

        gap = intent_results["score"] - reality_results["score"]
        confidence = reality_results.get("confidence_score", 50)
        washing_flag = reality_results.get("washing_flag", False)
        washing_type = reality_results.get("washing_type", None)
        key_people = reality_results.get("key_people", [])
        # Preserve previous key_people if new scan returned empty
        if not key_people and existing_data:
            prev_kp = existing_data.get("key_people", [])
            if prev_kp:
                key_people = prev_kp
                print(f"   [Key People] Preserved {len(prev_kp)} stakeholder(s) from previous scan (new scan returned none)")

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
        has_synth_key = anthropic_key
        if has_synth_key:
            try:
                claude_client = anthropic.Anthropic(api_key=anthropic_key) if anthropic_key else None
                intent_narrative = json.dumps(intent_results["breakdown"])
                verified_links = [l for l in reality_results['links'] if l.get('verified', True)]
                evidence_list = ', '.join([f"{l.get('title', 'Unknown')}: {l.get('snippet', '')}" for l in verified_links[:20]])
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

                synthesis_prompt = f"""You are a senior organizational analyst conducting a forensic modernization assessment.
Your task: synthesize scanner evidence into a precise, evidence-grounded diagnosis using the Knowledge Base framework below.

METHODOLOGY: Read ALL evidence first. Score ONLY what evidence supports. Write ONLY what scores justify.
If evidence is thin, say so — a short honest analysis beats a long fabricated one.

### PRIMARY FRAMEWORK (KNOWLEDGE BASE — MANDATORY)
{knowledge_context}

You MUST use the terminology, archetypes, and criteria defined in the Knowledge Base above.
Every claim in the summary MUST trace back to specific evidence AND map to a Knowledge Base dimension.
Do NOT invent categories — use ONLY Knowledge Base vocabulary.
NEVER use internal codes (TAC-xxx, DIM-xxx, etc.) — always write the full concept name.

### SCAN DATA
Target: {companyName}
Financial Health Index: {fin_for_archetype} ({"actual financial index" if fin_score is not None else "fallback: using intent score"})
Summary Confidence: {confidence}/100

Evidence Found: {evidence_list}
Intent Data: {intent_narrative}
{doc_context}
{washing_context}
{financial_context}
{trend_context}
{prev_synthesis_block}

### ANALYSIS PROTOCOL

STEP 1 — REALITY SCORE (evaluate before writing anything):
Score each Knowledge Base dimension (0-20) based on VERIFIED EVIDENCE ONLY:
  - Operating Model: Evidence of modern practices (DevOps, platform teams, stream-aligned)? Or legacy/waterfall/silos?
  - Strategy: Clear digital/modernization strategy with visible execution? Or vague/absent?
  - Culture: Learning culture, psychological safety, talent signals? Or hierarchy/control/fear?
  - Service Portfolio: Product-led, API-first, composable architecture? Or monolithic/legacy?
  - AI Readiness: Concrete AI/ML adoption with measurable outcomes? Or just buzzwords/pilot purgatory?

Scoring rules:
  - No evidence for a dimension = score 10 (neutral). Never score generously without evidence.
  - Evidence of aspiration (website claims, press releases) without execution evidence = score 8-12, not higher.
  - Sum the 5 dimensions → 'reality_score' (0-100).
  - Do NOT evaluate intent_score or financial_index — those are immutable pipeline outputs.

STEP 2 — ARCHETYPE DERIVATION (mechanical, from scores):
Use YOUR reality_score + Financial Health Index ({fin_for_archetype}):
  - reality_score >= 50 AND Financial Health Index >= 50 → "Thriving"
  - reality_score >= 50 AND Financial Health Index < 50 → "Modernizing"
  - reality_score < 50 AND Financial Health Index >= 50 → "Cash Cow"
  - reality_score < 50 AND Financial Health Index < 50 → "At Risk"
This is a mechanical derivation — do not override based on narrative interpretation.

STEP 3 — SUMMARY (180-220 words):
Write a diagnostic narrative that reads like an expert assessment, not a template:
  - First sentence: State the archetype and what defines this company's position.
  - Body: Map 3-4 key findings to specific KB dimensions. For each finding, connect the evidence
    to the KB pattern it reveals (e.g., "Job postings emphasize hierarchical reporting lines,
    characteristic of Digital Taylorism in the Operating Model dimension").
  - Use KB terminology naturally: "Digital Taylorism", "Flow Efficiency", "Pilot Purgatory",
    "Modernization Washing" — but only where the evidence supports the pattern.
  - If confidence < 50, state it: "Limited public evidence constrains this assessment."
  - If washing detected, name it: "{washing_type or 'Modernization Washing'}" with the specific gap.
  - Do NOT cite numeric scores in the text — scores display separately in the UI.
  - Do NOT fabricate findings. If evidence is sparse, acknowledge gaps rather than inventing patterns.
  - Use ONLY the four archetype labels: Thriving, Modernizing, Cash Cow, At Risk.

STEP 4 — PITCH (90-110 words):
A direct cold-outreach message to a C-level executive at {companyName}:
  - Open with a specific insight from the analysis that would make them stop and read.
  - Derive exactly 2 CONCRETE PROPOSALS targeting the company's weakest KB dimensions.
    Each proposal must name the dimension and suggest a specific transformation direction
    (e.g., "Shift from Digital Taylorism toward Flow Efficiency by restructuring around
    stream-aligned teams with end-to-end ownership").
  - The proposals must feel specific to THIS company's situation, not generic consulting templates.
  - Do NOT use "Forensic Modernization Audit" or any generic service names.
  - Tone: Professional, provocative, insight-led. The recipient should feel understood, not sold to.

### GUARDRAILS
- NEVER fabricate evidence, people, events, partnerships, or metrics not present in the scan data.
- NEVER invent temporal claims ("recently announced", "just launched", "this quarter").
- If a KB dimension has no supporting evidence, score it neutrally and acknowledge the gap — do not speculate.
- Every factual claim in the summary must be traceable to a specific piece of evidence above.

### OUTPUT
Respond with valid JSON only. Four fields required:
{{"summary": "...", "pitch": "...", "reality_score": <int 0-100>, "archetype": "<Thriving|Modernizing|Cash Cow|At Risk>"}}"""

                # --- Synthesis with fallback chain: Opus 4.6 → Gemini 3.1 Pro → Gemini 3 Pro → Gemini 2.5 Pro ---
                # Opus is the judge — it does ONE thing: synthesize. Fallbacks degrade gracefully.
                # Opus uses Extended Thinking to reason through conflicting intent vs. reality
                # evidence before generating the synthesis — exactly what a "judge" model should do.
                SYNTH_TIMEOUT = 120  # seconds per synthesis model call (extended thinking needs more time)
                response_text = ""
                synth_start = time.time()
                synth_remaining = SCAN_TOTAL_BUDGET - (time.time() - scan_start)
                print(f"   [Synthesis] Time remaining in scan budget: {int(synth_remaining)}s")

                def _synth_opus():
                    return claude_client.messages.create(
                        model="claude-opus-4-6",
                        max_tokens=16000,
                        thinking={
                            "type": "enabled",
                            "budget_tokens": 10000
                        },
                        messages=[{"role": "user", "content": synthesis_prompt}]
                    )

                try:
                    print(f"   🔄 [Synthesis] Attempting claude-opus-4-6 with Extended Thinking (timeout: {SYNTH_TIMEOUT}s)...")
                    synth_pool = concurrent.futures.ThreadPoolExecutor(max_workers=1)
                    synth_future = synth_pool.submit(_synth_opus)
                    try:
                        response = synth_future.result(timeout=SYNTH_TIMEOUT)
                        # Extended thinking response: [ThinkingBlock, TextBlock]
                        # Extract the text block (skip thinking blocks)
                        response_text = ""
                        if response.content:
                            for block in response.content:
                                if getattr(block, 'type', None) == 'text':
                                    response_text = block.text
                                    break
                        print(f"   ✅ [Synthesis] claude-opus-4-6 + Extended Thinking succeeded in {int((time.time() - synth_start) * 1000)}ms")
                    except concurrent.futures.TimeoutError:
                        synth_pool.shutdown(wait=False)
                        print(f"   ⏰ [Synthesis] claude-opus-4-6 timed out after {SYNTH_TIMEOUT}s")
                        raise Exception(f"Opus timeout after {SYNTH_TIMEOUT}s")
                    synth_pool.shutdown(wait=False)
                except Exception as opus_err:
                    print(f"   ❌ [Synthesis] Opus 4.6 failed: {opus_err}")

                    # Fallback 1: Gemini 3.1 Pro Preview (strongest Google model, MEDIUM thinking)
                    gemini_key = os.environ.get("GEMINI_API_KEY") or os.environ.get("API_KEY")
                    if gemini_key and (SCAN_TOTAL_BUDGET - (time.time() - scan_start)) > 30:
                        try:
                            print(f"   🔄 [Synthesis] Falling back to Gemini 3.1 Pro Preview (timeout: {SYNTH_TIMEOUT}s)...")
                            g_start = time.time()
                            gemini_client = genai.Client(api_key=gemini_key)

                            def _synth_gemini31():
                                return gemini_client.models.generate_content(
                                    model="gemini-3.1-pro-preview",
                                    contents=synthesis_prompt,
                                    config=types.GenerateContentConfig(
                                        temperature=0.5,
                                        thinking_config=types.ThinkingConfig(thinking_level="MEDIUM"),
                                        max_output_tokens=2048
                                    )
                                )

                            g_pool = concurrent.futures.ThreadPoolExecutor(max_workers=1)
                            g_future = g_pool.submit(_synth_gemini31)
                            try:
                                gemini_resp = g_future.result(timeout=SYNTH_TIMEOUT)
                                response_text = gemini_resp.text or ""
                                print(f"   ✅ [Synthesis] Gemini 3.1 Pro Preview succeeded in {int((time.time() - g_start) * 1000)}ms")
                            except concurrent.futures.TimeoutError:
                                g_pool.shutdown(wait=False)
                                print(f"   ⏰ [Synthesis] Gemini 3.1 Pro Preview timed out after {SYNTH_TIMEOUT}s")
                            g_pool.shutdown(wait=False)
                        except Exception as gemini31_err:
                            print(f"   ❌ [Synthesis] Gemini 3.1 Pro Preview failed: {gemini31_err}")

                    # Fallback 2: Gemini 3 Pro Preview
                    if not response_text and gemini_key and (SCAN_TOTAL_BUDGET - (time.time() - scan_start)) > 30:
                        try:
                            print(f"   🔄 [Synthesis] Falling back to Gemini 3.1 Pro Review (timeout: {SYNTH_TIMEOUT}s)...")
                            g2_start = time.time()
                            gemini_client = genai.Client(api_key=gemini_key)

                            def _synth_gemini31review():
                                return gemini_client.models.generate_content(
                                    model="gemini-3.1-pro-review",
                                    contents=synthesis_prompt,
                                    config=types.GenerateContentConfig(
                                        temperature=0.5,
                                        thinking_config=types.ThinkingConfig(thinking_level="MEDIUM"),
                                        max_output_tokens=2048
                                    )
                                )

                            g2_pool = concurrent.futures.ThreadPoolExecutor(max_workers=1)
                            g2_future = g2_pool.submit(_synth_gemini31review)
                            try:
                                gemini_resp = g2_future.result(timeout=SYNTH_TIMEOUT)
                                response_text = gemini_resp.text or ""
                                print(f"   ✅ [Synthesis] Gemini 3.1 Pro Review succeeded in {int((time.time() - g2_start) * 1000)}ms")
                            except concurrent.futures.TimeoutError:
                                g2_pool.shutdown(wait=False)
                                print(f"   ⏰ [Synthesis] Gemini 3.1 Pro Review timed out after {SYNTH_TIMEOUT}s")
                            g2_pool.shutdown(wait=False)
                        except Exception as gemini3_err:
                            print(f"   ❌ [Synthesis] Gemini 3.1 Pro Review failed: {gemini3_err}")

                    # Fallback 3: Gemini 2.5 Pro (separate rate limits from 3.x family)
                    if not response_text and gemini_key and (SCAN_TOTAL_BUDGET - (time.time() - scan_start)) > 30:
                        try:
                            print(f"   🔄 [Synthesis] Falling back to Gemini 2.5 Pro (timeout: {SYNTH_TIMEOUT}s)...")
                            g3_start = time.time()
                            gemini_client = genai.Client(api_key=gemini_key)

                            def _synth_gemini25():
                                return gemini_client.models.generate_content(
                                    model="gemini-2.5-pro",
                                    contents=synthesis_prompt,
                                    config=types.GenerateContentConfig(
                                        temperature=0.0,
                                        max_output_tokens=2048
                                    )
                                )

                            g3_pool = concurrent.futures.ThreadPoolExecutor(max_workers=1)
                            g3_future = g3_pool.submit(_synth_gemini25)
                            try:
                                gemini_resp = g3_future.result(timeout=SYNTH_TIMEOUT)
                                response_text = gemini_resp.text or ""
                                print(f"   ✅ [Synthesis] Gemini 2.5 Pro succeeded in {int((time.time() - g3_start) * 1000)}ms")
                            except concurrent.futures.TimeoutError:
                                g3_pool.shutdown(wait=False)
                                print(f"   ⏰ [Synthesis] Gemini 2.5 Pro timed out after {SYNTH_TIMEOUT}s")
                            g3_pool.shutdown(wait=False)
                        except Exception as gemini25_err:
                            print(f"   ❌ [Synthesis] All models failed. Last error: {gemini25_err}")

                if response_text:
                    payload = extract_json_from_text(response_text)
                    if payload:
                         json_data = json.loads(payload)
                         summary = json_data.get("summary", summary)
                         pitch = json_data.get("pitch", pitch) or pitch

                         # --- OPUS SCORE VERIFICATION ---
                         # Opus is the Final Judge for Reality Score. It has the most
                         # complete picture: Scanner 1 intent + Scanner 2 evidence +
                         # its own synthesis. Its score is always authoritative.
                         # (The 25-point delta threshold in the rescan trigger is a
                         # separate concern — it flags companies for re-evaluation,
                         # not score override.)
                         opus_reality = json_data.get("reality_score")
                         opus_intent = json_data.get("intent_score")
                         scanner2_reality = reality_results["score"]
                         scanner_intent = intent_results["score"]

                         if opus_reality is not None and isinstance(opus_reality, (int, float)):
                             opus_reality = max(0, min(100, int(opus_reality)))
                             delta_r = abs(opus_reality - scanner2_reality)
                             reality_results["scanner2_reality_score"] = scanner2_reality
                             reality_results["opus_reality_score"] = opus_reality

                             if delta_r <= 5:
                                 # Agreement — Opus confirms Scanner 2's finding
                                 print(f"   ✅ [Score Verify] Reality: Scanner 2 {scanner2_reality} ≈ Opus {opus_reality} (delta {delta_r}). Final score: {opus_reality}")
                             else:
                                 # Divergence — Opus is the final judge, log the delta for diagnostics
                                 print(f"   ⚖️ [Score Verify] Reality: Scanner 2 {scanner2_reality} vs Opus {opus_reality} (delta {delta_r}). Opus is final judge → {opus_reality}")

                             reality_results["score"] = opus_reality
                             reality_results["score_verification"] = "opus_final_judge"

                             # Recalculate gap if score changed
                             if reality_results["score"] != scanner2_reality:
                                 gap = intent_results["score"] - reality_results["score"]

                                 # Re-evaluate washing flag
                                 if reality_results["score"] >= intent_results["score"]:
                                     washing_flag = False
                                     washing_type = None

                         # Intent score is IMMUTABLE — Scanner 1's website analysis is the
                         # sole source of intent. Opus may disagree, but overriding intent
                         # would destroy the integrity delta (intent vs reality gap).
                         # Log Opus's opinion for diagnostics only.
                         if opus_intent is not None and isinstance(opus_intent, (int, float)):
                             opus_intent = max(0, min(100, int(opus_intent)))
                             delta_i = abs(opus_intent - scanner_intent)
                             intent_results["opus_intent_score"] = opus_intent
                             if delta_i <= 5:
                                 print(f"   ✅ [Score Verify] Intent: Scanner 1 {scanner_intent} ≈ Opus {opus_intent} (delta {delta_i}). Keeping Scanner 1 score.")
                             else:
                                 print(f"   ℹ️ [Score Verify] Intent: Scanner 1 {scanner_intent} vs Opus {opus_intent} (delta {delta_i}). Keeping Scanner 1 score (immutable).")
                                 gap = intent_results["score"] - reality_results["score"]

                         # --- ARCHETYPE VERIFICATION ---
                         # Opus now derives the archetype from its own scores.
                         # Verify it matches the deterministic rule; fix summary if not.
                         opus_archetype = json_data.get("archetype")
                         final_reality = reality_results["score"]
                         expected_archetype = ("Thriving" if fin_for_archetype >= 50 else "Modernizing") if final_reality >= 50 else ("Cash Cow" if fin_for_archetype >= 50 else "At Risk")

                         if opus_archetype and opus_archetype != expected_archetype:
                             print(f"   ⚖️ [Archetype Verify] Opus wrote '{opus_archetype}' but scores dictate '{expected_archetype}' (reality={final_reality}, financial={fin_for_archetype}). Correcting summary.")
                             valid_archetypes = ["Thriving", "Modernizing", "Cash Cow", "At Risk"]
                             for wrong_label in valid_archetypes:
                                 if wrong_label == expected_archetype:
                                     continue
                                 summary = summary.replace(wrong_label, expected_archetype)
                                 summary = summary.replace(wrong_label.upper(), expected_archetype.upper())
                         elif opus_archetype:
                             print(f"   ✅ [Archetype Verify] Opus archetype '{opus_archetype}' matches score-derived '{expected_archetype}'")

                    else:
                         summary = response_text

            except Exception as e:
                print(f"   [Synthesis Error] {e}")
        else:
            print(f"   [Synthesis] WARNING: No ANTHROPIC_API_KEY found. Summary unavailable.")

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

        # --- REALITY CHECK: Hallucination Detection ---
        summary_check = validate_summary(
            summary_text=summary,
            company_name=companyName,
            key_people=key_people,
            evidence_links=reality_results.get("links", []),
            confidence_score=confidence,
        )
        pitch_check = validate_pitch(pitch_text=pitch, company_name=companyName)

        full_data["hallucination_flags"] = summary_check["flags"] + pitch_check.get("flags", [])
        full_data["hallucination_risk"] = summary_check["risk"]
        full_data["hallucination_score"] = summary_check["score"]

        if summary_check["risk"] == "high":
            print(f"   ⚠️ [Reality Check] HIGH hallucination risk (score {summary_check['score']}/100)! Flags: {summary_check['flags']}")
            print(f"   ℹ️ [Reality Check] Heuristic is diagnostic only — proceeding to AI fact-check for independent verification.")
        elif summary_check["risk"] == "medium":
            print(f"   ⚠️ [Reality Check] MEDIUM hallucination risk (score {summary_check['score']}/100). Flags: {summary_check['flags']}")
        else:
            print(f"   ✅ [Reality Check] Summary OK (score {summary_check['score']}/100)")

        # --- AI FACT-CHECK: Always runs in Deep Scan (heuristic is diagnostic only) ---
        # Core insight: the problem is EVIDENCE CONTAMINATION, not hallucination.
        # Checking summary against stored evidence is circular when evidence itself may be wrong.
        # Gemini 3.1 Pro with Google Search independently verifies claims against live web data.
        #
        # Single-pass pipeline (no loops, no eternal fact-checking):
        #   1. Fact-check once → if clean, DONE (original synthesis stands)
        #   2. If false claims → Gemini 3.1 Pro produces corrected summary in same call
        #      → purge contaminated evidence → apply corrected summary → DONE
        #   No second fact-check after correction — fact-checker fixes what it finds.
        print(f"   🔄 [Fact-Check] Gemini 3.1 Pro fact-check against live web data...")
        fact_check = ai_fact_check_summary(
            summary_text=full_data["summary_text"],
            evidence_links=reality_results.get("links", []),
            company_name=companyName,
            pitch_text=full_data.get("pitch_text", ""),
        )
        full_data["ai_verification"] = {
            "verified": fact_check["verified"],
            "false_claims": fact_check["false_claims"],
            "false_claims_detailed": fact_check.get("false_claims_detailed", []),
            "model": fact_check["fact_check_model"],
            "time_ms": fact_check["fact_check_time_ms"],
        }

        if fact_check["verified"]:
            # --- CLEAN: No false claims found. Original synthesis stands. Done. ---
            print(f"   ✅ [Fact-Check] Summary verified — no false claims ({fact_check['fact_check_time_ms']}ms)")
        else:
            # --- FALSE CLAIMS FOUND: Apply corrected summary from fact-check + purge evidence ---
            # Gemini 3.1 Pro produced the corrected summary in the same fact-check call.
            # If fact-checker didn't produce a corrected summary, fall back to separate re-synthesis.
            print(f"   ⚠️ [Fact-Check] {len(fact_check['false_claims'])} false claim(s) found ({fact_check['fact_check_time_ms']}ms):")
            for claim in fact_check["false_claims"]:
                print(f"      - {claim}")

            # Step 1: Purge contaminated evidence sources
            print(f"   🗑️ [Purge] Removing contaminated evidence from stored data...")
            detailed_claims = fact_check.get("false_claims_detailed", [])
            purge_result = purge_false_evidence(
                full_data,
                fact_check["false_claims"],
                detailed_claims=detailed_claims,
            )

            # Step 2: Apply corrected summary from fact-check (or fall back to separate re-synthesis)
            corrected_summary = fact_check.get("corrected_summary", "")
            corrected_pitch = fact_check.get("corrected_pitch", "")
            resynth_ms = 0
            resynth_model_used = fact_check.get("fact_check_model", "")

            if corrected_summary:
                # --- Fact-checker produced corrected summary — use it directly ---
                print(f"   ✅ [Fact-Check] Using corrected summary from Gemini 3.1 Pro fact-checker ({len(corrected_summary.split())} words)")
                full_data["summary_text"] = corrected_summary
                if corrected_pitch:
                    full_data["pitch_text"] = corrected_pitch
                    print(f"   ✅ [Fact-Check] Also applied corrected pitch ({len(corrected_pitch.split())} words)")

                full_data["re_synthesis"] = {
                    "was_resynthesized": True,
                    "false_claims_found": fact_check["false_claims"],
                    "evidence_purged": purge_result,
                    "resynth_model": resynth_model_used,
                    "resynth_time_ms": fact_check.get("fact_check_time_ms", 0),
                    "method": "fact_check_inline_correction",
                }
                # Mark as verified — fact-checker fixed what it found.
                # SINGLE-PASS GUARANTEE: No second fact-check runs here.
                full_data["ai_verification"]["verified"] = True
                full_data["ai_verification"]["false_claims"] = []
                full_data["ai_verification"]["note"] = "Corrected by Gemini 3.1 Pro fact-checker in single pass (fact-check + fix)"
                # Clear stale washing flags
                full_data["washing_flag"] = False
                full_data["washing_type"] = None
                print(f"   ✅ [Fact-Check] Cleared washing flags (false claims corrected by fact-checker)")

            else:
                # --- Fallback: Fact-checker didn't produce corrected summary → separate re-synthesis ---
                print(f"   ⚠️ [Fact-Check] No corrected summary from fact-checker — falling back to separate re-synthesis")
                print(f"   🔄 [Re-Synthesis] Gemini full re-synthesis from cleaned evidence...")
                resynth_start = time.time()

                # Rebuild evidence list from purged data
                clean_evidence_links = full_data.get("evidence_links", [])
                clean_verified = [l for l in clean_evidence_links if l.get('verified', True)]
                clean_evidence_list = ', '.join([f"{l.get('title', 'Unknown')}: {l.get('snippet', '')}" for l in clean_verified[:20]])

                # Note which claims were removed so the model knows what to avoid
                removed_claims_note = "\n".join(f"- {c}" for c in fact_check["false_claims"])

                resynth_prompt = f"""{synthesis_prompt}

CRITICAL CONTEXT — FACT-CHECK CORRECTIONS:
The previous synthesis contained false claims that were detected by an independent fact-checker
(Gemini 3.1 Pro with Google Search). The following claims were FALSE and must NOT appear in your output:
{removed_claims_note}

The contaminated evidence sources have been removed. You are now working with CLEANED evidence only:
Evidence Found: {clean_evidence_list}

Generate a completely fresh synthesis from this cleaned evidence set. Do NOT reference or
re-introduce any of the false claims listed above. Build your narrative only from the
verified evidence provided."""

                resynth_text = ""
                resynth_model_used = ""
                gemini_key = os.environ.get("GEMINI_API_KEY") or os.environ.get("API_KEY")

                # Primary: Gemini 3.1 Pro Preview
                if gemini_key:
                    try:
                        print(f"   🔄 [Re-Synthesis] Attempting gemini-3.1-pro-preview...")
                        gemini_client = genai.Client(api_key=gemini_key)
                        resynth_response = gemini_client.models.generate_content(
                            model="gemini-3.1-pro-preview",
                            contents=resynth_prompt,
                            config=types.GenerateContentConfig(
                                temperature=0.5,
                                thinking_config=types.ThinkingConfig(thinking_level="MEDIUM"),
                                max_output_tokens=2048
                            )
                        )
                        resynth_text = resynth_response.text or ""
                        resynth_model_used = "gemini-3.1-pro-preview"
                        resynth_ms = int((time.time() - resynth_start) * 1000)
                        print(f"   ✅ [Re-Synthesis] Gemini 3.1 Pro re-synthesis complete ({resynth_ms}ms)")
                    except Exception as g31_err:
                        print(f"   ❌ [Re-Synthesis] Gemini 3.1 Pro failed: {g31_err}")

                # Fallback 1: Gemini 3.1 Pro Review (replaces Gemini 3 Pro)
                if not resynth_text and gemini_key:
                    try:
                        print(f"   🔄 [Re-Synthesis] Falling back to gemini-3.1-pro-review...")
                        gemini_client = genai.Client(api_key=gemini_key)
                        resynth_response = gemini_client.models.generate_content(
                            model="gemini-3.1-pro-review",
                            contents=resynth_prompt,
                            config=types.GenerateContentConfig(
                                temperature=0.5,
                                thinking_config=types.ThinkingConfig(thinking_level="MEDIUM"),
                                max_output_tokens=2048
                            )
                        )
                        resynth_text = resynth_response.text or ""
                        resynth_model_used = "gemini-3.1-pro-review"
                        resynth_ms = int((time.time() - resynth_start) * 1000)
                        print(f"   ✅ [Re-Synthesis] Gemini 3.1 Pro Review re-synthesis complete ({resynth_ms}ms)")
                    except Exception as g3_err:
                        print(f"   ❌ [Re-Synthesis] Gemini 3.1 Pro Review failed: {g3_err}")

                # Fallback 2: Gemini 2.5 Pro (separate rate limits from 3.x family)
                if not resynth_text and gemini_key:
                    try:
                        print(f"   🔄 [Re-Synthesis] Falling back to gemini-2.5-pro...")
                        gemini_client = genai.Client(api_key=gemini_key)
                        resynth_response = gemini_client.models.generate_content(
                            model="gemini-2.5-pro",
                            contents=resynth_prompt,
                            config=types.GenerateContentConfig(
                                temperature=0.0,
                                max_output_tokens=2048
                            )
                        )
                        resynth_text = resynth_response.text or ""
                        resynth_model_used = "gemini-2.5-pro"
                        resynth_ms = int((time.time() - resynth_start) * 1000)
                        print(f"   ✅ [Re-Synthesis] Gemini 2.5 Pro re-synthesis complete ({resynth_ms}ms)")
                    except Exception as g25_err:
                        resynth_ms = int((time.time() - resynth_start) * 1000)
                        print(f"   ❌ [Re-Synthesis] All re-synthesis models failed. Last error: {g25_err} ({resynth_ms}ms)")

                if resynth_text:
                    resynth_payload = extract_json_from_text(resynth_text)
                    if resynth_payload:
                        resynth_json = json.loads(resynth_payload)
                        new_summary = resynth_json.get("summary", "")
                        new_pitch = resynth_json.get("pitch", "")

                        if new_summary:
                            full_data["summary_text"] = new_summary
                        if new_pitch:
                            full_data["pitch_text"] = new_pitch

                        # Update reality score if Gemini re-evaluated it
                        resynth_reality = resynth_json.get("reality_score")
                        if resynth_reality is not None and isinstance(resynth_reality, (int, float)):
                            resynth_reality = max(0, min(100, int(resynth_reality)))
                            reality_results["score"] = resynth_reality
                            full_data["reality_score"] = resynth_reality
                            # Recalculate gap and archetype
                            gap = intent_results["score"] - resynth_reality
                            full_data["reality_gap"] = gap
                            if resynth_reality >= 50:
                                archetype = "Thriving" if fin_for_archetype >= 50 else "Modernizing"
                            else:
                                archetype = "Cash Cow" if fin_for_archetype >= 50 else "At Risk"
                            full_data["archetype"] = archetype
                            # Re-evaluate washing flag
                            if resynth_reality >= intent_results["score"]:
                                full_data["washing_flag"] = False
                                full_data["washing_type"] = None

                        # Verify archetype from re-synthesis
                        resynth_archetype = resynth_json.get("archetype")
                        final_reality = full_data["reality_score"]
                        expected_archetype = ("Thriving" if fin_for_archetype >= 50 else "Modernizing") if final_reality >= 50 else ("Cash Cow" if fin_for_archetype >= 50 else "At Risk")
                        if resynth_archetype and resynth_archetype != expected_archetype:
                            print(f"   ⚖️ [Re-Synthesis] Archetype '{resynth_archetype}' → corrected to '{expected_archetype}'")
                            valid_archetypes = ["Thriving", "Modernizing", "Cash Cow", "At Risk"]
                            for wrong_label in valid_archetypes:
                                if wrong_label == expected_archetype:
                                    continue
                                full_data["summary_text"] = full_data["summary_text"].replace(wrong_label, expected_archetype)

                        print(f"   ✅ [Re-Synthesis] Full re-synthesis applied from cleaned evidence")

                    full_data["re_synthesis"] = {
                        "was_resynthesized": True,
                        "false_claims_found": fact_check["false_claims"],
                        "evidence_purged": purge_result,
                        "resynth_model": resynth_model_used or "gemini-3.1-pro-preview",
                        "resynth_time_ms": resynth_ms,
                        "method": "separate_re_synthesis_fallback",
                    }
                    # Mark as verified after re-synthesis — built from clean evidence.
                    # SINGLE-PASS GUARANTEE: No second fact-check runs here.
                    full_data["ai_verification"]["verified"] = True
                    full_data["ai_verification"]["false_claims"] = []
                    full_data["ai_verification"]["note"] = "Re-synthesized from cleaned evidence after fact-check (single-pass fallback)"
                    # Clear stale washing flags
                    full_data["washing_flag"] = False
                    full_data["washing_type"] = None
                    print(f"   ✅ [Re-Synthesis] Cleared washing flags (false claims removed, fresh synthesis)")
                else:
                    print(f"   ⚠️ [Re-Synthesis] Could not re-synthesize — saving with false claims flagged")
                    full_data["re_synthesis"] = {
                        "was_resynthesized": False,
                        "false_claims_found": fact_check["false_claims"],
                        "evidence_purged": purge_result,
                        "reason": "Gemini re-synthesis failed",
                    }

        # --- STEP 4: SAVE ---
        if saveToDatabase:
            print(f"   🔄 [Storage] Saving report to Vercel Blob...")
            save_start = time.time()
            # If data was cleaned via re-synthesis, use strict pruning (keep=1)
            # to ensure false data versions are truly gone from the database
            was_cleaned = full_data.get("re_synthesis", {}).get("was_resynthesized", False)
            saved_url = storage.save_company_data(companyName, full_data, post_clean=was_cleaned)
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
                "archetype": archetype,
                "ai_verified": full_data.get("ai_verification", {}).get("verified", True)
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
