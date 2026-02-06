
from fastapi import FastAPI, HTTPException, BackgroundTasks, Response
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
from seed_manager import run_batch_seed, scrape_strategic_content
from finland_list import TOP_100_COMPANIES
from knowledge_base import get_evolving_framework

print("Starting Adaptability Scanner Backend (v3.6 - Prefix Fix)...", flush=True)

def clean_env_var(value):
    if not value: return ""
    if "=" in value:
        try: value = value.split("=", 1)[1]
        except: pass
    return value.strip().strip('"').strip("'")

app = FastAPI()

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"], 
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

storage = BlobStorage()
print(f"   [Config] Effective Public Base URL: {storage.public_base_url or 'Auto-Discovery Active'}")

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
async def read_root(): return FileResponse("../index.html")

@app.get("/api/health")
def health_check():
    token = clean_env_var(os.environ.get("BLOB_READ_WRITE_TOKEN"))
    blob_url = clean_env_var(os.environ.get("VITE_BLOB_URL") or os.environ.get("BLOB_URL"))
    
    # Verify Storage Connectivity by trying to list 1 file
    storage_connected = False
    try:
        # Check if we can talk to Vercel API
        test_list = storage._list(prefix="index", limit=1)
        storage_connected = True
    except:
        storage_connected = False

    return {
        "status": "Adaptability Scanner API Online (v3.6)", 
        "timestamp": datetime.utcnow().isoformat(),
        "storage_connected": storage_connected,
        "config_status": {
            "has_blob_token": bool(token),
            "has_blob_url_env": bool(blob_url),
            "effective_base_url": storage.public_base_url
        }
    }

@app.get("/dashboard-data")
async def get_dashboard_data(response: Response):
    """
    Returns the LIGHTWEIGHT summary list.
    """
    response.headers["Cache-Control"] = "no-cache, no-store, must-revalidate"
    print("GET /dashboard-data requested...")
    
    # FIX: Remove .json extension to match Vercel's suffixing behavior
    # Searching for "index/summary_list" will match "index/summary_list-AbCd.json"
    data = storage.retrieve_latest_data("index/summary_list")
    
    if data:
        print(f" -> Success. Returning {len(data)} records.")
    else:
        print(" -> Warning: Storage returned empty list or None.")
        
    return data or []

@app.get("/company/{company_name}")
async def get_company_details(company_name: str, response: Response):
    """
    Returns the FULL DETAILED report.
    """
    response.headers["Cache-Control"] = "no-cache, no-store, must-revalidate"
    
    data = storage.get_company_data(company_name)
    if not data:
        raise HTTPException(status_code=404, detail="Company report not found in Vercel Blob.")
    
    return data

class ScanRequest(BaseModel):
    companyName: str
    companyUrl: Optional[str] = None
    reportText: str
    saveToDatabase: Optional[bool] = False

@app.post("/admin/seed-finland-100")
async def seed_database(background_tasks: BackgroundTasks):
    background_tasks.add_task(run_batch_seed, real_xray_mode=True, limit=None)
    return {"status": "Batch Seeding Initiated", "message": "Check server logs for progress."}

@app.post("/admin/seed-fast")
async def seed_fast(background_tasks: BackgroundTasks):
    """
    Runs a calibration scan for just the top 3 companies.
    """
    background_tasks.add_task(run_batch_seed, real_xray_mode=True, limit=3)
    return {"status": "Quick Calibration Initiated", "message": "Scanning top 3 companies (Nokia, Kone, Neste)..."}

@app.post("/scan")
async def run_scan(request: ScanRequest):
    """
    REAL FORENSIC SCAN (Live).
    """
    try:
        print(f"--- LIVE SCAN INITIATED FOR: {request.companyName} ---")
        
        # --- STEP 1: SCANNER 1 (Intent) ---
        target_text = request.reportText
        target_url = request.companyUrl
        
        if not target_url:
            for c in TOP_100_COMPANIES:
                if c['name'].lower() == request.companyName.lower():
                    target_url = c['url']
                    break

        if not target_text or len(target_text) < 100:
            if target_url: target_text = scrape_strategic_content(target_url, request.companyName)
            else: target_text = scrape_strategic_content(None, request.companyName)

        intent_results = analyze_intent(target_text)

        # --- STEP 2: SCANNER 2 (Reality) ---
        reality_results = x_ray_search(request.companyName, target_url)
        gap = intent_results["score"] - reality_results["score"]
        
        # --- STEP 3: SYNTHESIS ---
        summary = "AI Summary Unavailable"
        api_key = clean_env_var(os.environ.get("API_KEY") or os.environ.get("GEMINI_API_KEY"))
        
        if api_key:
            try:
                client = genai.Client(api_key=api_key)
                intent_narrative = json.dumps(intent_results["breakdown"])
                evidence_list = ', '.join([f"{l['title']}: {l.get('snippet', '')}" for l in reality_results['links'][:4]])
                knowledge_context = get_evolving_framework()

                prompt = f"""
                ### SYNTHESIS
                Target: {request.companyName}
                Intent: {intent_results["score"]}, Reality: {reality_results["score"]}, Gap: {gap}
                Knowledge Base: {knowledge_context}
                Evidence: {evidence_list}
                Intent Data: {intent_narrative}

                Task: Write a 100-word forensic executive summary.
                """
                response = client.models.generate_content(model='gemini-3-pro-preview', contents=prompt)
                if response.text: summary = response.text
            except Exception as e:
                print(f"Gemini Error: {e}")

        new_timestamp = datetime.utcnow().isoformat()
        result_id = str(uuid.uuid4())
        
        history = []
        existing_data = storage.get_company_data(request.companyName)
        if existing_data:
            history = existing_data.get("history", [])
            history.append({
                "date": existing_data.get("lastUpdated"),
                "gap": existing_data.get("reality_gap", 0)
            })
            if len(history) > 12: history = history[-12:] 

        full_data = {
            "id": result_id,
            "companyName": request.companyName,
            "companyUrl": target_url, 
            "intent_score": intent_results["score"],
            "reality_score": reality_results["score"],
            "reality_gap": gap,
            "intent_breakdown": intent_results["breakdown"],
            "metrics": intent_results.get("metrics", {}),
            "reality_checklist": reality_results["checklist"],
            "evidence_links": reality_results["links"],
            "summary_text": summary,
            "lastUpdated": new_timestamp,
            "history": history,
            "is_simulated": reality_results.get("is_simulated", False)
        }
        
        # --- STEP 4: SAVE ---
        saved_url = None
        if request.saveToDatabase:
            saved_url = storage.save_company_data(request.companyName, full_data)
            print(f"   [Live Scan] Saved detailed report to: {saved_url}")
            
            summary_entry = {
                "id": result_id,
                "companyName": request.companyName,
                "intent_score": intent_results["score"],
                "reality_score": reality_results["score"],
                "reality_gap": gap,
                "lastUpdated": new_timestamp,
                "detailsUrl": saved_url # Ensure we save the specific link
            }
            storage.update_global_index(summary_entry)
        
        return {"success": True, "data": full_data}

    except Exception as e:
        print(f"Server Error during Live Scan: {str(e)}")
        raise HTTPException(status_code=500, detail=str(e))
