from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from typing import Optional
import os
import uuid
from datetime import datetime
import google.generativeai as genai

from scanner_one import analyze_intent
from scanner_two import x_ray_search
from storage import BlobStorage

app = FastAPI()

# Enable CORS for Vercel Frontend
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"], # In production, replace "*" with your Vercel URL
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

storage = BlobStorage()

class ScanRequest(BaseModel):
    companyName: str
    companyUrl: Optional[str] = None
    reportText: str

class AnalysisResponse(BaseModel):
    success: bool
    data: dict

@app.get("/")
def read_root():
    return {"status": "Adaptability Scanner API Online"}

@app.post("/scan")
async def run_scan(request: ScanRequest):
    try:
        # 1. Run Scanner 1 (Intent)
        intent_results = analyze_intent(request.reportText)
        
        # 2. Run Scanner 2 (Reality)
        reality_results = x_ray_search(request.companyName)
        
        # 3. Calculate Gap
        gap = intent_results["score"] - reality_results["score"]
        
        # 4. Generate AI Summary using Gemini
        summary = "AI Summary Unavailable (Config Error)"
        api_key = os.environ.get("GEMINI_API_KEY")
        
        if api_key:
            try:
                genai.configure(api_key=api_key)
                # Note: 'gemini-1.5-flash' is the model name.
                model = genai.GenerativeModel('gemini-1.5-flash')
                prompt = f"""
                Analyze {request.companyName}.
                Intent Score: {intent_results["score"]}/100.
                Reality Score: {reality_results["score"]}/100.
                Gap: {gap}.
                Write a 3 sentence executive summary diagnosing their adaptability.
                """
                ai_res = model.generate_content(prompt)
                if ai_res.text:
                    summary = ai_res.text
            except Exception as e:
                print(f"Gemini Error: {e}")
                summary = f"AI Analysis failed: {str(e)}"

        # 5. Construct Result Object
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
            "lastUpdated": datetime.utcnow().isoformat()
        }
        
        # 6. Update Master Dashboard JSON in Blob
        # For this prototype, we return data to frontend immediately
        
        return {
            "success": True,
            "data": result_data
        }

    except Exception as e:
        print(f"Server Error: {str(e)}")
        raise HTTPException(status_code=500, detail=str(e))
