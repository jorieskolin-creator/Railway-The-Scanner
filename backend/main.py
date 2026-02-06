
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

print("Starting Adaptability Scanner Backend (v2.2 Native Search)...", flush=True)

def clean_env_var(value):
    if not value: return ""
    if "=" in value:
        try: value = value.split("=", 1)[1]
        except: pass
    return value.strip().strip('"').strip("'")

# Log Environment Config
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
BLOB_FILENAME = "dashboard_data.json"

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
        "status": "Adaptability Scanner API Online", 
        "timestamp": datetime.utcnow().isoformat(),
        "config_status": {
            "has_blob_token": bool(token),
            "has_gemini_key": gemini_present,
            "has_google_search_key": gemini_present
        }
    }

@app.get("/dashboard-data")
async def get_dashboard_data(response: Response):
    # CRITICAL: Prevent caching of the JSON data to ensure live updates from Vercel are seen immediately.
    response.headers["Cache-Control"] = "no-cache, no-store, must-revalidate"
    response.headers["Pragma"] = "no-cache"
    response.headers["Expires"] = "0"
    return storage.retrieve_latest_data(BLOB_FILENAME)

class ScanRequest(BaseModel):
    companyName: str
    companyUrl: Optional[str] = None
    reportText: str
    saveToDatabase: Optional[bool] = False

@app.post("/admin/seed-finland-100")
async def seed_database(background_tasks: BackgroundTasks):
    background_tasks.add_task(run_batch_seed, real_xray_mode=True)
    return {"status": "Batch Seeding Initiated", "message": "Processing Finland Top 100 in background."}

@app.post("/scan")
async def run_scan(request: ScanRequest):
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
            if target_url: target_text = scrape_strategic_content(target_url, request.companyName)
            else: target_text = scrape_strategic_content(None, request.companyName)

        intent_results = analyze_intent(target_text)

        # --- STEP 2: SCANNER 2 (Reality + Context) ---
        reality_results = x_ray_search(request.companyName, target_url)
        gap = intent_results["score"] - reality_results["score"]
        
        # --- STEP 3: SYNTHESIS (The AI Judge) ---
        summary = "AI Summary Unavailable"
        api_key = clean_env_var(os.environ.get("API_KEY") or os.environ.get("GEMINI_API_KEY"))
        
        if api_key:
            try:
                client = genai.Client(api_key=api_key)
                
                intent_narrative = json.dumps(intent_results["breakdown"])
                evidence_list = ', '.join([f"{l['title']}: {l.get('snippet', '')}" for l in reality_results['links'][:4]])
                knowledge_context = get_evolving_framework()

                prompt = f"""
                ### STEP 3: SYNTHESIS & REPORTING
                
                You are the Chief Forensic Analyst.
                
                ## THE KNOWLEDGE BASE (Guideline)
                {knowledge_context}

                ## THE DATA
                Target: {request.companyName}
                Intent Score: {intent_results["score"]}/100
                Reality Score: {reality_results["score"]}/100
                Gap: {gap}

                Intent Breakdown: {intent_narrative}
                Evidence Context: {evidence_list}

                ## YOUR TASK
                Create a high-precision Executive Summary (max 100 words).
                1. Classify the company using the Knowledge Base archetypes.
                2. Explain the gap using specific terms (e.g. "Digital Taylorism", "Flow").
                3. Cite specific evidence found by Scanner 2.
                """
                
                # Use Gemini 3 Pro Preview for High Quality Synthesis as requested
                try:
                    response = client.models.generate_content(
                        model='gemini-3-pro-preview',
                        contents=prompt
                    )
                    if response.text: summary = response.text
                except Exception as e:
                    print(f"Gemini Pro failed, falling back to Flash: {e}")
                    response = client.models.generate_content(
                        model='gemini-3-flash-preview',
                        contents=prompt
                    )
                    if response.text: summary = response.text

            except Exception as e:
                print(f"Gemini Error: {e}")

        # 4. Result Construction
        new_timestamp = datetime.utcnow().isoformat()
        result_id = str(uuid.uuid4())
        
        result_data = {
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
            "is_simulated": reality_results.get("is_simulated", False)
        }
        
        # 5. Persist to Vercel Blob (Step 3 Requirement)
        if request.saveToDatabase:
            try:
                current_db = storage.retrieve_latest_data(BLOB_FILENAME) or []
                
                index = -1
                for i, item in enumerate(current_db):
                    if item.get("companyName", "").lower() == request.companyName.lower():
                        index = i
                        result_data["id"] = item.get("id", result_id) 
                        break
                
                if index > -1: 
                    existing_record = current_db[index]
                    history = existing_record.get("history", [])
                    snapshot = {
                        "date": existing_record.get("lastUpdated"),
                        "gap": existing_record.get("reality_gap", 0),
                        "reality_score": existing_record.get("reality_score", 0),
                        "intent_score": existing_record.get("intent_score", 0)
                    }
                    if snapshot["date"]: history.append(snapshot)
                    if len(history) > 10: history = history[-10:]
                    result_data["history"] = history
                    current_db[index] = result_data
                else: 
                    result_data["history"] = []
                    current_db.append(result_data)
                
                storage.upload_json(BLOB_FILENAME, current_db)

            except Exception as e:
                print(f"Persistence Failed: {e}")
        
        return {"success": True, "data": result_data}

    except Exception as e:
        print(f"Server Error: {str(e)}")
        raise HTTPException(status_code=500, detail=str(e))
