
from fastapi import FastAPI, HTTPException, BackgroundTasks
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse
from pydantic import BaseModel
from typing import Optional, List
import os
import uuid
import json
import requests
from datetime import datetime
from google import genai
from google.genai import types

from scanner_one import analyze_intent
from scanner_two import x_ray_search
from storage import BlobStorage
from seed_manager import run_batch_seed, scrape_homepage_text
from finland_list import TOP_100_COMPANIES

print("Starting Adaptability Scanner Backend (v2.0)...", flush=True)

# Helper for cleaning env vars
def clean_env_var(value):
    if not value: return ""
    if "=" in value:
        try: value = value.split("=", 1)[1]
        except: pass
    return value.strip().strip('"').strip("'")

# Log Environment Config
blob_token = clean_env_var(os.environ.get("BLOB_READ_WRITE_TOKEN"))
blob_url_var = clean_env_var(os.environ.get("VITE_BLOB_URL"))
gemini_key = clean_env_var(os.environ.get("GEMINI_API_KEY") or os.environ.get("API_KEY"))
google_key = clean_env_var(os.environ.get("GOOGLE_API_KEY"))
cx_id = clean_env_var(os.environ.get("CX_ENGINE_ID"))

print(f"Config Check: Storage Token? {bool(blob_token)}")
print(f"Config Check: VITE_BLOB_URL? {blob_url_var if blob_url_var else 'Not Set'}")
print(f"Config Check: Gemini/AI Key? {bool(gemini_key)}")
print(f"Config Check: Google Search Key? {bool(google_key)}")
print(f"Config Check: Search Engine ID? {bool(cx_id)}")

if not google_key or not cx_id:
    print("WARNING: Google Search keys missing. Reality Scans will return 0 score.")

if not gemini_key:
    print("WARNING: Gemini API Key missing. Summaries will be unavailable.")

app = FastAPI()

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"], 
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

storage = BlobStorage()
BLOB_FILENAME = "dashboard_data.json"

# --- STATIC FILE SERVING FOR FRONTEND ---
app.mount("/components", StaticFiles(directory="../components"), name="components")
app.mount("/services", StaticFiles(directory="../services"), name="services")

@app.get("/App.tsx")
async def get_app_tsx(): return FileResponse("../App.tsx", media_type="application/javascript")

@app.get("/index.tsx")
async def get_index_tsx(): return FileResponse("../index.tsx", media_type="application/javascript")

@app.get("/types.ts")
async def get_types_ts(): return FileResponse("../types.ts", media_type="application/javascript")

@app.get("/constants.ts")
async def get_constants_ts(): return FileResponse("../constants.ts", media_type="application/javascript")

@app.get("/")
async def read_root():
    return FileResponse("../index.html")

@app.get("/api/health")
def health_check():
    # Re-read and clean to give accurate status
    token = clean_env_var(os.environ.get("BLOB_READ_WRITE_TOKEN"))
    return {
        "status": "Adaptability Scanner API Online", 
        "timestamp": datetime.utcnow().isoformat(),
        "config_status": {
            "has_blob_token": bool(token),
            "token_starts_with": token[:4] if token else None,
            "has_gemini_key": bool(clean_env_var(os.environ.get("GEMINI_API_KEY") or os.environ.get("API_KEY"))),
            "has_google_search_key": bool(clean_env_var(os.environ.get("GOOGLE_API_KEY")))
        }
    }

# --- DATA ROUTES ---

@app.get("/dashboard-data")
async def get_dashboard_data():
    """
    Retrieves the current database state from Vercel Blob.
    Used by Frontend to hydrate the dashboard.
    """
    try:
        data = storage.retrieve_latest_data(BLOB_FILENAME)
        return data
    except Exception as e:
        print(f"Error retrieving data: {e}")
        return []

@app.get("/admin/debug-storage")
async def debug_storage():
    """
    DEBUG ENDPOINT: Checks connection to Vercel Blob and lists files.
    Use this to verify if data is actually being stored.
    """
    return storage.debug_list_files()

# --- ACTION ROUTES ---

class ScanRequest(BaseModel):
    companyName: str
    companyUrl: Optional[str] = None
    reportText: str
    saveToDatabase: Optional[bool] = False

@app.post("/admin/seed-finland-100")
async def seed_database(background_tasks: BackgroundTasks):
    # Updated to real_xray_mode=True to ensure full scan
    background_tasks.add_task(run_batch_seed, real_xray_mode=True)
    return {"status": "Batch Seeding Initiated", "message": "Processing Finland Top 100 in background. Check back in 30 seconds."}

@app.post("/scan")
async def run_scan(request: ScanRequest):
    try:
        # 1. AUTO-SCRAPE LOGIC
        # If reportText is empty (from Refresh button) or too short, we scrape the site.
        target_text = request.reportText
        
        if not target_text or len(target_text) < 100:
            print(f"Report text missing/short for {request.companyName}. Attempting to scrape...")
            
            # Try to find URL
            target_url = request.companyUrl
            if not target_url:
                # Lookup in Top 100 List
                for c in TOP_100_COMPANIES:
                    if c['name'].lower() == request.companyName.lower():
                        target_url = c['url']
                        break
            
            if target_url:
                print(f"Scraping {target_url}...")
                target_text = scrape_homepage_text(target_url)
            else:
                # Fallback if we can't find a URL
                print("No URL found. Using generic fallback.")
                target_text = scrape_homepage_text(None) # Returns generic text

        # 2. Run Analysis
        intent_results = analyze_intent(target_text)
        reality_results = x_ray_search(request.companyName, request.companyUrl)
        gap = intent_results["score"] - reality_results["score"]
        
        # 3. AI Synthesis
        summary = "AI Summary Unavailable (Config Error)"
        api_key = clean_env_var(os.environ.get("API_KEY") or os.environ.get("GEMINI_API_KEY"))
        
        if api_key:
            try:
                client = genai.Client(api_key=api_key)
                prompt = f"""
                Analyze {request.companyName} (Finland Top 100 Context).
                Intent Score: {intent_results["score"]}/100.
                Reality Score: {reality_results["score"]}/100.
                Gap: {gap}.
                Verified Evidence: {', '.join([l['title'] for l in reality_results['links'][:3]])}
                
                Write a concise executive summary (max 3 sentences) diagnosing their adaptability.
                """
                
                try:
                    response = client.models.generate_content(
                        model='gemini-2.0-flash',
                        contents=prompt
                    )
                    if response.text: summary = response.text
                except Exception as e:
                    print(f"Gemini 2.0 Flash Error: {e}")

            except Exception as e:
                print(f"Gemini Client Error: {e}")

        # 4. Construct Result
        new_timestamp = datetime.utcnow().isoformat()
        result_id = str(uuid.uuid4())
        
        result_data = {
            "id": result_id,
            "companyName": request.companyName,
            "intent_score": intent_results["score"],
            "reality_score": reality_results["score"],
            "reality_gap": gap,
            "intent_breakdown": intent_results["breakdown"],
            "metrics": intent_results.get("metrics", {}), # Pass Friction/Vibrancy
            "reality_checklist": reality_results["checklist"],
            "evidence_links": reality_results["links"],
            "summary_text": summary,
            "lastUpdated": new_timestamp,
            "is_simulated": reality_results.get("is_simulated", False)
        }
        
        # 5. Save to Database
        if request.saveToDatabase:
            print(f"Persisting update for {request.companyName}...")
            try:
                current_db = storage.retrieve_latest_data(BLOB_FILENAME) or []
                
                index = -1
                for i, item in enumerate(current_db):
                    if item.get("companyName", "").lower() == request.companyName.lower():
                        index = i
                        result_data["id"] = item.get("id", result_id) 
                        break
                
                if index > -1: current_db[index] = result_data
                else: current_db.append(result_data)
                
                upload_url = storage.upload_json(BLOB_FILENAME, current_db)
                if not upload_url:
                    result_data["persistence_warning"] = "Warning: Data was analyzed but NOT saved to database (Missing BLOB_READ_WRITE_TOKEN)."

            except Exception as e:
                print(f"Persistence Failed: {e}")
                result_data["persistence_warning"] = f"Warning: Database error: {str(e)}"
        
        return {"success": True, "data": result_data}

    except Exception as e:
        print(f"Server Error: {str(e)}")
        raise HTTPException(status_code=500, detail=str(e))
