
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

print("Starting Adaptability Scanner Backend (v3.1 Final)...", flush=True)

def clean_env_var(value):
    if not value: return ""
    if "=" in value:
        try: value = value.split("=", 1)[1]
        except: pass
    return value.strip().strip('"').strip("'")

blob_token = clean_env_var(os.environ.get("BLOB_READ_WRITE_TOKEN"))
gemini_key = clean_env_var(os.environ.get("GEMINI_API_KEY") or os.environ.get("API_KEY"))

app = FastAPI()

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"], 
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

storage = BlobStorage()

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
    gemini_present = bool(clean_env_var(os.environ.get("GEMINI_API_KEY") or os.environ.get("API_KEY")))
    
    return {
        "status": "Adaptability Scanner API Online (v3.1)", 
        "timestamp": datetime.utcnow().isoformat(),
        "config_status": {
            "has_blob_token": bool(token),
            "has_gemini_key": gemini_present,
            "has_google_search_key": gemini_present
        }
    }

@app.get("/dashboard-data")
async def get_dashboard_data(response: Response):
    """
    Returns the LIGHTWEIGHT summary list for the landing page table/graph.
    """
    response.headers["Cache-Control"] = "no-cache, no-store, must-revalidate"
    response.headers["Pragma"] = "no-cache"
    response.headers["Expires"] = "0"
    
    data = storage.retrieve_latest_data("index/summary_list.json")
    return data or []

@app.get("/company/{company_name}")
async def get_company_details(company_name: str, response: Response):
    """
    Returns the FULL DETAILED report for a specific company from Vercel Blob.
    """
    response.headers["Cache-Control"] = "no-cache, no-store, must-revalidate"
    data = storage.get_company_data(company_name)
    if not data:
        raise HTTPException(status_code=404, detail="Company report not found")
    return data

class ScanRequest(BaseModel):
    companyName: str
    companyUrl: Optional[str] = None
    reportText: str
    saveToDatabase: Optional[bool] = False

@app.post("/admin/seed-finland-100")
async def seed_database(background_tasks: BackgroundTasks):
    """
    BATCH REFRESH: Updates all 100 companies in the index + individual files.
    """
    background_tasks.add_task(run_batch_seed, real_xray_mode=True)
    return {"status": "Batch Seeding Initiated", "message": "Processing Finland Top 100 in background."}

@app.post("/scan")
async def run_scan(request: ScanRequest):
    """
    REAL FORENSIC SCAN:
    1. Scrapes the defined sources (Scanner 1).
    2. Runs the Tribunal Search (Scanner 2).
    3. Synthesizes via Gemini.
    4. Saves to Database (if requested), adding new companies to the index AND saving their individual file.
    """
    try:
        # --- STEP 1: SCANNER 1 (Intent) ---
        target_text = request.reportText
        target_url = request.companyUrl
        
        if not target_url:
            for c in TOP_100_COMPANIES:
                if c['name'].lower() == request.companyName.lower():
                    target_url = c['url']
                    break

        if not target_text or len(target_text) < 100:
            # THIS CALLS THE UPDATED SCRAPER WITH "JOB ADS", "SERVICES", ETC.
            if target_url: target_text = scrape_strategic_content(target_url, request.companyName)
            else: target_text = scrape_strategic_content(None, request.companyName)

        intent_results = analyze_intent(target_text)

        # --- STEP 2: SCANNER 2 (Reality + Context) ---
        # THIS CALLS THE UPDATED TRIBUNAL LOGIC
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
                
                try:
                    response = client.models.generate_content(model='gemini-3-pro-preview', contents=prompt)
                    if response.text: summary = response.text
                except:
                    response = client.models.generate_content(model='gemini-3-flash-preview', contents=prompt)
                    if response.text: summary = response.text

            except Exception as e:
                print(f"Gemini Error: {e}")

        new_timestamp = datetime.utcnow().isoformat()
        result_id = str(uuid.uuid4())
        
        # HISTORY PRESERVATION
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
        
        summary_entry = {
            "id": result_id,
            "companyName": request.companyName,
            "intent_score": intent_results["score"],
            "reality_score": reality_results["score"],
            "reality_gap": gap,
            "lastUpdated": new_timestamp
        }
        
        # --- STEP 4: DATABASE UPDATE ---
        if request.saveToDatabase:
            # 1. Save the Heavy Full Data (used by Modal)
            storage.save_company_data(request.companyName, full_data)
            # 2. Update the Lightweight Index (used by Landing Page)
            # This ensures new companies appear in the list immediately.
            storage.update_global_index(summary_entry)
        
        return {"success": True, "data": full_data}

    except Exception as e:
        print(f"Server Error: {str(e)}")
        raise HTTPException(status_code=500, detail=str(e))
