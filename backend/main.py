
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
from reality_check import validate_summary, validate_pitch, ai_verify_summary, clean_summary, purge_false_evidence

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

@app.post("/admin/recheck-company/{company_name}")
async def recheck_company(company_name: str, request: Request):
    """Run reality check on an already-stored company's summary without rescanning."""
    verify_admin(request)
    data = storage.get_company_data(company_name)
    if not data:
        raise HTTPException(status_code=404, detail=f"Company '{company_name}' not found in database.")

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

    # AI-powered cross-reference check (same as automatic scan pipeline)
    ai_result = ai_verify_summary(
        summary_text=data.get("summary_text", ""),
        evidence_links=data.get("evidence_links", []),
        company_name=company_name,
    )

    # Persist AI verification result back to stored data
    data["ai_verification"] = {
        "verified": ai_result["verified"],
        "unsupported_claims": ai_result["unsupported_claims"],
        "unsupported_claims_detailed": ai_result.get("unsupported_claims_detailed", []),
        "model": ai_result["verification_model"],
        "time_ms": ai_result["verification_time_ms"],
    }
    storage.save_company_data(company_name, data)
    # Update index with ai_verified flag
    storage.update_global_index_field(company_name, "ai_verified", ai_result["verified"])

    recommendation = "DELETE and rescan" if summary_result["risk"] == "high" else \
                     "Rescan — contradictions or fabrications found" if not ai_result["verified"] else \
                     "Review manually" if summary_result["risk"] == "medium" else "OK"

    return {
        "company": company_name,
        "summary_check": summary_result,
        "pitch_check": pitch_result,
        "ai_verification": ai_result,
        "recommendation": recommendation,
    }

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

        result = validate_summary(
            summary_text=data.get("summary_text", ""),
            company_name=name,
            key_people=data.get("key_people", []),
            evidence_links=data.get("evidence_links", []),
            confidence_score=data.get("confidence_score"),
        )

        # AI-powered cross-reference check (same as automatic scan pipeline)
        ai_result = ai_verify_summary(
            summary_text=data.get("summary_text", ""),
            evidence_links=data.get("evidence_links", []),
            company_name=name,
        )

        # Persist AI verification result back to stored data
        data["ai_verification"] = {
            "verified": ai_result["verified"],
            "unsupported_claims": ai_result["unsupported_claims"],
            "unsupported_claims_detailed": ai_result.get("unsupported_claims_detailed", []),
            "model": ai_result["verification_model"],
            "time_ms": ai_result["verification_time_ms"],
        }
        storage.save_company_data(name, data)
        storage.update_global_index_field(name, "ai_verified", ai_result["verified"])

        is_flagged = result["risk"] in ("medium", "high") or not ai_result["verified"]
        if is_flagged:
            flag_entry = {
                "companyName": name,
                "risk": result["risk"],
                "score": result["score"],
                "flags": result["flags"],
                "ai_verified": ai_result["verified"],
                "unsupported_claims": ai_result["unsupported_claims"],
                "summary_preview": (data.get("summary_text", "") or "")[:200],
            }

            # Auto-clean if AI found false claims
            if not ai_result["verified"] and ai_result["unsupported_claims"]:
                clean_result = _clean_company_data(name, data)
                flag_entry["clean_action"] = clean_result.get("action", "skipped")
                flag_entry["claims_removed"] = clean_result.get("claims_removed", 0)
                flag_entry["re_verified"] = clean_result.get("re_verified", False)
                if clean_result.get("action") == "cleaned":
                    cleaned_results.append({
                        "companyName": name,
                        "claims_removed": clean_result["claims_removed"],
                        "re_verified": clean_result["re_verified"],
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
    Shared cleaning logic: purge false evidence, rewrite summary, re-verify, save.
    Used by both single-company clean and recheck-all.

    Returns dict with action result (skipped/failed/cleaned).
    """
    ai_verification = data.get("ai_verification", {})
    unsupported_claims = ai_verification.get("unsupported_claims", [])
    detailed_claims = ai_verification.get("unsupported_claims_detailed", [])

    if ai_verification.get("verified", True) or not unsupported_claims:
        return {
            "company": company_name,
            "action": "skipped",
            "reason": "No false claims found — summary is already verified.",
        }

    print(f"   🧹 [Clean] Cleaning {len(unsupported_claims)} false claim(s) from {company_name}...")

    # Clean the summary first — only purge evidence if clean succeeds
    # (purge mutates data in-place, so a failed clean would leave evidence destroyed)
    clean_result = clean_summary(
        summary_text=data.get("summary_text", ""),
        pitch_text=data.get("pitch_text", ""),
        unsupported_claims=unsupported_claims,
        company_name=company_name,
        use_gemini=True,
    )

    if not clean_result["success"]:
        return {
            "company": company_name,
            "action": "failed",
            "reason": f"Cleaning failed: {clean_result['model']}",
            "original_claims": unsupported_claims,
        }

    # Only purge evidence if the clean actually removed claims.
    # If claims_removed == 0 the LLM found nothing to change — purging
    # would destroy legitimate evidence based on overly broad keyword matching.
    #
    # NEW: purge_false_evidence now uses AI-attributed source URLs instead of
    # keyword guessing. If a false claim has no traceable source, nothing gets
    # purged for that claim (safe mode).
    purge_result = {"evidence_removed": 0, "checklist_removed": 0, "purged_items": [], "skipped_claims": 0}
    if clean_result["claims_removed"] > 0:
        purge_result = purge_false_evidence(data, unsupported_claims, detailed_claims=detailed_claims)

        # Only replace summary/pitch when claims were actually removed.
        # If 0 claims removed, the LLM found nothing wrong — keep the original
        # to avoid Gemini subtly rewriting the text and introducing new issues.
        data["summary_text"] = clean_result["cleaned_summary"]
        data["pitch_text"] = clean_result["cleaned_pitch"]

        # Re-verify the cleaned summary against purged evidence
        print(f"   🔄 [Clean] Re-verifying cleaned summary...")
        re_verify = ai_verify_summary(
            summary_text=clean_result["cleaned_summary"],
            evidence_links=data.get("evidence_links", []),
            company_name=company_name,
        )

        data["ai_verification"] = {
            "verified": re_verify["verified"],
            "unsupported_claims": re_verify["unsupported_claims"],
            "unsupported_claims_detailed": re_verify.get("unsupported_claims_detailed", []),
            "model": re_verify["verification_model"],
            "time_ms": re_verify["verification_time_ms"],
        }
        data["summary_cleaned"] = {
            "was_cleaned": True,
            "claims_removed": clean_result["claims_removed"],
            "original_claims": unsupported_claims,
            "clean_model": clean_result["model"],
            "clean_time_ms": clean_result["time_ms"],
            "evidence_purged": purge_result,
        }

        storage.save_company_data(company_name, data)
        storage.update_global_index_field(company_name, "ai_verified", re_verify["verified"])

        print(f"   ✅ [Clean] {company_name}: Removed {clean_result['claims_removed']} false claims. Re-verified: {re_verify['verified']}")
    else:
        # Cleaning AI reviewed the summary and found nothing to remove.
        # This is a second-opinion confirmation: the flagged claims don't
        # actually appear in the text. Clear the UNVERIFIED status.
        print(f"   ⏭️ [Clean] 0 claims removed — cleaning AI confirmed summary is clean, marking as verified")
        re_verify = {"verified": True, "unsupported_claims": [], "unsupported_claims_detailed": [], "verification_time_ms": 0, "verification_model": f"clean_confirmed:{clean_result['model']}"}

        data["ai_verification"] = {
            "verified": True,
            "unsupported_claims": [],
            "unsupported_claims_detailed": [],
            "model": f"clean_confirmed:{clean_result['model']}",
            "time_ms": clean_result["time_ms"],
        }
        storage.save_company_data(company_name, data)
        storage.update_global_index_field(company_name, "ai_verified", True)

    return {
        "company": company_name,
        "action": "cleaned" if clean_result["claims_removed"] > 0 else "no_change",
        "claims_removed": clean_result["claims_removed"],
        "original_claims": unsupported_claims,
        "re_verified": re_verify["verified"],
        "remaining_issues": re_verify["unsupported_claims"],
        "clean_time_ms": clean_result["time_ms"],
        "verify_time_ms": re_verify.get("verification_time_ms", 0),
    }


@app.post("/admin/clean-summary/{company_name}")
async def clean_company_summary(company_name: str, request: Request):
    """
    Clean false claims from a company's summary without doing a full rescan.

    If AI verification found unsupported_claims (verified=false), this endpoint
    rewrites the summary to remove just the false parts, then re-verifies.
    Much cheaper than a full rescan (1 Haiku call vs 3 scanners + Opus synthesis).
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
        print(f"🚀 --- LIVE SCAN INITIATED FOR: {companyName} ---")
        
        # Model pipeline: Sonnet 4.6 + Web Search (forensic) | Gemini 2.5 Pro (financial) | Opus 4.6 (synthesis)
        # Forensic fallback: Gemini 3 Pro. Synthesis fallback: Gemini 3 Pro → Gemini 2.5 Pro
        search_model = "gemini-3-pro-preview"  # Used as forensic fallback if Sonnet fails

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
                financial_health_search, companyName, target_url
            )

            try:
                reality_results = reality_future.result(timeout=350)
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

                synthesis_prompt = f"""### PRIMARY FRAMEWORK (KNOWLEDGE BASE — MANDATORY)
{knowledge_context}

YOU MUST use the terminology, archetypes, and criteria defined in the Knowledge Base above.
Every claim in the summary MUST map to a Knowledge Base dimension or pattern.
Do NOT invent your own categories — use ONLY the vocabulary from the Knowledge Base.
NEVER use internal codes (TAC-xxx, DIM-xxx, etc.) in any output — always write the full concept name in plain language.

### SCAN DATA
Target: {companyName}
Financial Health Index: {fin_for_archetype} ({"actual financial index" if fin_score is not None else "fallback: using intent score"})
Archetype rules (computed from YOUR evaluated reality_score + Financial Health Index above):
  - THRIVING: reality_score >= 50 AND Financial Health Index >= 50. High Reality + Strong Financials.
  - MODERNIZING: reality_score >= 50 AND Financial Health Index < 50. High Reality + Weak Financials.
  - CASH COW: reality_score < 50 AND Financial Health Index >= 50. Low Reality + Strong Financials.
  - AT RISK: reality_score < 50 AND Financial Health Index < 50. Low Reality + Weak Financials.
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
Generate a JSON object with five fields: 'summary', 'pitch', 'reality_score', 'intent_score', and 'archetype'.

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

SECOND — DERIVE THE ARCHETYPE:
Apply the archetype rules using YOUR evaluated reality_score and the Financial Health Index ({fin_for_archetype}) provided above:
  - reality_score >= 50 AND Financial Health Index >= 50 → 'archetype': "Thriving"
  - reality_score >= 50 AND Financial Health Index < 50 → 'archetype': "Modernizing"
  - reality_score < 50 AND Financial Health Index >= 50 → 'archetype': "Cash Cow"
  - reality_score < 50 AND Financial Health Index < 50 → 'archetype': "At Risk"
This MUST be a mechanical derivation from the scores — do not override based on narrative interpretation.

THIRD — WRITE THE NARRATIVE:

1. 'summary' (180-220 words):
   - STATE THE ARCHETYPE you derived above explicitly in the first sentence.
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

Output valid JSON only. Must include all five fields: summary, pitch, reality_score, intent_score, archetype."""

                # --- Synthesis with fallback chain: Opus 4.6 → Gemini 3 Pro → Gemini 2.5 Pro ---
                # Opus is the judge — it does ONE thing: synthesize. Fallbacks degrade gracefully.
                response_text = ""
                synth_start = time.time()

                try:
                    print(f"   🔄 [Synthesis] Attempting claude-opus-4-6...")
                    response = claude_client.messages.create(
                        model="claude-opus-4-6",
                        max_tokens=1024,
                        messages=[{"role": "user", "content": synthesis_prompt}]
                    )
                    response_text = response.content[0].text if response.content else ""
                    print(f"   ✅ [Synthesis] claude-opus-4-6 succeeded in {int((time.time() - synth_start) * 1000)}ms")
                except Exception as opus_err:
                    print(f"   ❌ [Synthesis] Opus 4.6 failed: {opus_err}")

                    # Fallback 1: Gemini 3 Pro Preview (strongest Google model)
                    gemini_key = os.environ.get("GEMINI_API_KEY") or os.environ.get("API_KEY")
                    if gemini_key:
                        try:
                            print(f"   🔄 [Synthesis] Falling back to Gemini 3 Pro Preview...")
                            g_start = time.time()
                            gemini_client = genai.Client(api_key=gemini_key)
                            gemini_resp = gemini_client.models.generate_content(
                                model="gemini-3-pro-preview",
                                contents=synthesis_prompt,
                                config=types.GenerateContentConfig(
                                    temperature=0.7,
                                    max_output_tokens=2048
                                )
                            )
                            response_text = gemini_resp.text or ""
                            print(f"   ✅ [Synthesis] Gemini 3 Pro Preview succeeded in {int((time.time() - g_start) * 1000)}ms")
                        except Exception as gemini3_err:
                            print(f"   ❌ [Synthesis] Gemini 3 Pro Preview failed: {gemini3_err}")

                    # Fallback 2: Gemini 2.5 Pro
                    if not response_text and gemini_key:
                        try:
                            print(f"   🔄 [Synthesis] Falling back to Gemini 2.5 Pro...")
                            g2_start = time.time()
                            gemini_client = genai.Client(api_key=gemini_key)
                            gemini_resp = gemini_client.models.generate_content(
                                model="gemini-2.5-pro",
                                contents=synthesis_prompt,
                                config=types.GenerateContentConfig(
                                    temperature=0.7,
                                    max_output_tokens=2048
                                )
                            )
                            response_text = gemini_resp.text or ""
                            print(f"   ✅ [Synthesis] Gemini 2.5 Pro succeeded in {int((time.time() - g2_start) * 1000)}ms")
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
            print(f"   ⚠️ [Reality Check] Summary will be replaced with safety notice. Data saved with warning flag.")
            # Replace hallucinated summary with safe fallback, preserving scores
            full_data["summary_text"] = f"[FLAGGED] This summary was automatically blocked by the Reality Check system (hallucination score: {summary_check['score']}/100). Flags: {'; '.join(summary_check['flags'])}. Please rescan this company to generate a fresh analysis."
            full_data["pitch_text"] = "Pitch unavailable — summary flagged by Reality Check. Rescan required."
        elif summary_check["risk"] == "medium":
            print(f"   ⚠️ [Reality Check] MEDIUM hallucination risk (score {summary_check['score']}/100). Flags: {summary_check['flags']}")
        else:
            print(f"   ✅ [Reality Check] Summary OK (score {summary_check['score']}/100)")

        # --- AI VERIFICATION: Two-phase contradiction + fresh search check ---
        # Phase 1: Haiku checks if summary CONTRADICTS stored evidence snippets
        # Phase 2: Gemini + Google Search independently verifies summary against live web
        # Only runs if heuristic check passed (no point verifying an already-blocked summary).
        if summary_check["risk"] != "high":
            print(f"   🔄 [AI Verify] Two-phase verification (contradiction check + fresh web search)...")
            ai_verification = ai_verify_summary(
                summary_text=full_data["summary_text"],
                evidence_links=reality_results.get("links", []),
                company_name=companyName,
                anthropic_key=anthropic_key if has_synth_key else None,
            )
            full_data["ai_verification"] = {
                "verified": ai_verification["verified"],
                "unsupported_claims": ai_verification["unsupported_claims"],
                "unsupported_claims_detailed": ai_verification.get("unsupported_claims_detailed", []),
                "model": ai_verification["verification_model"],
                "time_ms": ai_verification["verification_time_ms"],
            }
            if ai_verification["verified"]:
                print(f"   ✅ [AI Verify] No contradictions or fabrications ({ai_verification['verification_time_ms']}ms)")
            else:
                print(f"   ⚠️ [AI Verify] {len(ai_verification['unsupported_claims'])} issue(s) found ({ai_verification['verification_time_ms']}ms):")
                for claim in ai_verification["unsupported_claims"]:
                    print(f"      - {claim}")

                # --- AUTO-CLEAN: Opus rewrites its own summary to remove false claims ---
                # Clean FIRST, then purge evidence only if clean succeeds.
                # (purge mutates full_data in-place — if clean fails, evidence would be destroyed
                #  while the uncleaned summary still references it)
                print(f"   🧹 [Auto-Clean] Opus rewriting summary to remove false claims...")
                auto_clean = clean_summary(
                    summary_text=full_data["summary_text"],
                    pitch_text=full_data["pitch_text"],
                    unsupported_claims=ai_verification["unsupported_claims"],
                    company_name=companyName,
                    anthropic_key=anthropic_key if has_synth_key else None,
                    use_opus=True,
                )

                if auto_clean["success"]:
                    # Only purge evidence if the clean actually removed claims.
                    # If claims_removed == 0 the LLM found nothing to change — purging
                    # would destroy legitimate evidence based on overly broad keyword matching.
                    #
                    # NEW: purge uses AI-attributed source URLs. If a false claim has no
                    # traceable source, nothing gets purged for that claim (safe mode).
                    purge_result = {"evidence_removed": 0, "checklist_removed": 0, "purged_items": [], "skipped_claims": 0}
                    if auto_clean["claims_removed"] > 0:
                        print(f"   🗑️ [Purge] Removing false evidence from stored data...")
                        detailed_claims = ai_verification.get("unsupported_claims_detailed", [])
                        purge_result = purge_false_evidence(
                            full_data,
                            ai_verification["unsupported_claims"],
                            detailed_claims=detailed_claims,
                        )

                        # Only replace summary/pitch when claims were actually removed.
                        # If 0 claims removed, the LLM found nothing wrong — keep the original
                        # to avoid subtly rewriting the text and introducing new issues.
                        full_data["summary_text"] = auto_clean["cleaned_summary"]
                        full_data["pitch_text"] = auto_clean["cleaned_pitch"]
                        full_data["summary_cleaned"] = {
                            "was_cleaned": True,
                            "claims_removed": auto_clean["claims_removed"],
                            "original_claims": ai_verification["unsupported_claims"],
                            "clean_model": auto_clean["model"],
                            "clean_time_ms": auto_clean["time_ms"],
                            "evidence_purged": purge_result,
                        }

                        # Re-verify the cleaned summary against the purged evidence
                        print(f"   🔄 [Auto-Clean] Re-verifying cleaned summary...")
                        re_verify = ai_verify_summary(
                            summary_text=auto_clean["cleaned_summary"],
                            evidence_links=full_data.get("evidence_links", []),
                            company_name=companyName,
                            anthropic_key=anthropic_key if has_synth_key else None,
                        )
                        full_data["ai_verification"] = {
                            "verified": re_verify["verified"],
                            "unsupported_claims": re_verify["unsupported_claims"],
                            "unsupported_claims_detailed": re_verify.get("unsupported_claims_detailed", []),
                            "model": re_verify["verification_model"],
                            "time_ms": re_verify["verification_time_ms"],
                        }
                        if re_verify["verified"]:
                            print(f"   ✅ [Auto-Clean] Cleaned summary passed verification!")
                        else:
                            print(f"   ⚠️ [Auto-Clean] Cleaned summary still has {len(re_verify['unsupported_claims'])} issue(s) — saving as unverified")
                    else:
                        print(f"   ⏭️ [Auto-Clean] 0 claims removed — keeping original summary untouched")
                else:
                    print(f"   ⚠️ [Auto-Clean] Could not clean summary: {auto_clean['model']}")
        else:
            full_data["ai_verification"] = {
                "verified": False,
                "unsupported_claims": ["Summary blocked by heuristic check"],
                "model": "skipped_heuristic_block",
                "time_ms": 0,
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
