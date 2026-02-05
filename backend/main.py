from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
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

app = FastAPI()

# Enable CORS for Vercel Frontend
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"], 
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

storage = BlobStorage()
BLOB_FILENAME = "dashboard_data.json"

class ScanRequest(BaseModel):
    companyName: str
    companyUrl: Optional[str] = None
    reportText: str
    saveToDatabase: Optional[bool] = False # Flag to trigger Blob persistence

@app.get("/")
def read_root():
    return {"status": "Adaptability Scanner API Online"}

@app.post("/scan")
async def run_scan(request: ScanRequest):
    try:
        # 1. Run Scanner 1 (Intent)
        intent_results = analyze_intent(request.reportText)
        
        # 2. Run Scanner 2 (Reality) - This is the "Live" check
        # Updated to pass the company URL for domain-specific X-Ray
        reality_results = x_ray_search(request.companyName, request.companyUrl)
        
        # 3. Calculate Gap
        gap = intent_results["score"] - reality_results["score"]
        
        # 4. Generate AI Summary using Gemini
        summary = "AI Summary Unavailable (Config Error)"
        api_key = os.environ.get("GEMINI_API_KEY")
        
        if api_key:
            try:
                genai.configure(api_key=api_key)
                # Attempt to use the preview model, fallback to flash if unavailable in region/tier
                try:
                    model = genai.GenerativeModel('gemini-1.5-flash')
                except:
                    model = genai.GenerativeModel('gemini-pro')

                prompt = f"""
                Analyze {request.companyName} (Finland Top 100 Context).
                Intent Score: {intent_results["score"]}/100.
                Reality Score: {reality_results["score"]}/100.
                Gap: {gap}.
                Verified Evidence: {', '.join([l['title'] for l in reality_results['links'][:3]])}
                
                Write a concise executive summary (max 3 sentences) diagnosing their adaptability.
                Mention if they are "Silent Executors" or practicing "Theater".
                """
                ai_res = model.generate_content(prompt)
                if ai_res.text:
                    summary = ai_res.text
            except Exception as e:
                print(f"Gemini Error: {e}")
                summary = f"AI Analysis failed: {str(e)}"

        # 5. Construct Result Object
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
            "lastUpdated": new_timestamp
        }
        
        # 6. Persistence Logic (The "Refresh Database" Feature)
        if request.saveToDatabase:
            print(f"Persisting update for {request.companyName} to Blob...")
            try:
                # A. Get current master list
                current_db = []
                blob_url = os.environ.get("VITE_BLOB_URL") 
                
                if not blob_url:
                    print("CRITICAL ERROR: VITE_BLOB_URL is missing in Railway Variables.")
                    print("The app cannot read the existing database to append the new record.")
                else:
                    try:
                        r = requests.get(blob_url)
                        if r.status_code == 200:
                            current_db = r.json()
                        else:
                            print(f"Failed to fetch existing DB from {blob_url}. Status: {r.status_code}")
                    except Exception as fetch_err:
                        print(f"Could not fetch existing DB: {fetch_err}")

                # B. Update or Append
                index = -1
                for i, item in enumerate(current_db):
                    if item.get("companyName").lower() == request.companyName.lower():
                        index = i
                        result_data["id"] = item.get("id", result_id) 
                        break
                
                if index > -1:
                    current_db[index] = result_data
                    print(f"Updated existing record for {request.companyName}")
                else:
                    current_db.append(result_data)
                    print(f"Appended new record for {request.companyName}")
                
                # C. Upload back to Blob
                new_url = storage.upload_json(BLOB_FILENAME, current_db)
                if new_url:
                    print(f"Database updated. URL: {new_url}")
                else:
                    print("Database update failed (Upload error).")

            except Exception as e:
                print(f"Persistence Failed: {e}")
        
        return {
            "success": True,
            "data": result_data
        }

    except Exception as e:
        print(f"Server Error: {str(e)}")
        raise HTTPException(status_code=500, detail=str(e))