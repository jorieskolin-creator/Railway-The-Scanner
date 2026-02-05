
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
import google.generativeai as genai

from scanner_one import analyze_intent
from scanner_two import x_ray_search
from storage import BlobStorage
from seed_manager import run_batch_seed

print("Starting Adaptability Scanner Backend...", flush=True)

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
# Serve specific directories for React components and logic
app.mount("/components", StaticFiles(directory="../components"), name="components")
app.mount("/services", StaticFiles(directory="../services"), name="services")

# Serve individual source files required by the browser
@app.get("/App.tsx")
async def get_app_tsx(): return FileResponse("../App.tsx")

@app.get("/index.tsx")
async def get_index_tsx(): return FileResponse("../index.tsx")

@app.get("/types.ts")
async def get_types_ts(): return FileResponse("../types.ts")

@app.get("/constants.ts")
async def get_constants_ts(): return FileResponse("../constants.ts")

# Serve the main HTML entry point at root
@app.get("/")
async def read_root():
    return FileResponse("../index.html")

# Move the API Status check to a dedicated endpoint
@app.get("/api/health")
def health_check():
    return {"status": "Adaptability Scanner API Online", "timestamp": datetime.utcnow().isoformat()}

# --- API ROUTES ---

class ScanRequest(BaseModel):
    companyName: str
    companyUrl: Optional[str] = None
    reportText: str
    saveToDatabase: Optional[bool] = False

@app.post("/admin/seed-finland-100")
async def seed_database(background_tasks: BackgroundTasks):
    background_tasks.add_task(run_batch_seed, real_xray_mode=False)
    return {"status": "Batch Seeding Initiated", "message": "Processing Finland Top 100 in background."}

@app.post("/scan")
async def run_scan(request: ScanRequest):
    try:
        intent_results = analyze_intent(request.reportText)
        reality_results = x_ray_search(request.companyName, request.companyUrl)
        gap = intent_results["score"] - reality_results["score"]
        
        summary = "AI Summary Unavailable (Config Error)"
        api_key = os.environ.get("API_KEY") or os.environ.get("GEMINI_API_KEY")
        
        if api_key:
            try:
                genai.configure(api_key=api_key)
                prompt = f"""
                Analyze {request.companyName} (Finland Top 100 Context).
                Intent Score: {intent_results["score"]}/100.
                Reality Score: {reality_results["score"]}/100.
                Gap: {gap}.
                Verified Evidence: {', '.join([l['title'] for l in reality_results['links'][:3]])}
                
                Write a concise executive summary (max 3 sentences) diagnosing their adaptability.
                Mention if they are "Silent Executors" or practicing "Theater".
                """
                try:
                    model = genai.GenerativeModel('gemini-3-flash-preview')
                    ai_res = model.generate_content(prompt)
                    if ai_res.text: summary = ai_res.text
                except Exception as e:
                    print(f"Gemini 3 Flash unavailable. Fallback to 1.5 Flash. Error: {e}")
                    try:
                        model = genai.GenerativeModel('gemini-1.5-flash')
                        ai_res = model.generate_content(prompt)
                        if ai_res.text: summary = ai_res.text
                    except Exception as fb_err:
                         summary = f"AI Analysis failed: {str(fb_err)}"

            except Exception as e:
                print(f"Gemini Error: {e}")
                summary = f"AI Analysis failed: {str(e)}"

        new_timestamp = datetime.utcnow().isoformat()
        result_id = str(uuid.uuid4())
        
        result_data = {
            "id": result_id,
            "companyName": request.companyName,
            "intent_score": intent_results["score"],
            "reality_score": reality_results["score"],
            "reality_gap": gap,
            "intent_breakdown": intent_results["breakdown"],
            "reality_checklist": reality_results["checklist"],
            "evidence_links": reality_results["links"],
            "summary_text": summary,
            "lastUpdated": new_timestamp,
            "is_simulated": reality_results.get("is_simulated", False)
        }
        
        if request.saveToDatabase:
            print(f"Persisting update for {request.companyName}...")
            try:
                current_db = []
                blob_url = os.environ.get("VITE_BLOB_URL") 
                if blob_url:
                    if not blob_url.endswith("json"):
                         blob_url = f"{blob_url}{BLOB_FILENAME}" if blob_url.endswith("/") else f"{blob_url}/{BLOB_FILENAME}"
                    try:
                        r = requests.get(blob_url)
                        if r.status_code == 200: current_db = r.json()
                        elif r.status_code == 404: current_db = [] 
                    except Exception: pass 

                index = -1
                for i, item in enumerate(current_db):
                    if item.get("companyName", "").lower() == request.companyName.lower():
                        index = i
                        result_data["id"] = item.get("id", result_id) 
                        break
                
                if index > -1: current_db[index] = result_data
                else: current_db.append(result_data)
                
                storage.upload_json(BLOB_FILENAME, current_db)

            except Exception as e:
                print(f"Persistence Failed: {e}")
        
        return {"success": True, "data": result_data}

    except Exception as e:
        print(f"Server Error: {str(e)}")
        raise HTTPException(status_code=500, detail=str(e))
