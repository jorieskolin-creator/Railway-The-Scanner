
from fastapi import FastAPI, HTTPException, BackgroundTasks, Response, Request, UploadFile, File, Form
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel
from typing import Optional, List
import os
import uuid
import json
import asyncio
import requests
import io
from datetime import datetime
from bs4 import BeautifulSoup
from pypdf import PdfReader
from google import genai
from google.genai import types

from scanner_one import analyze_intent
from scanner_two import x_ray_search
from storage import BlobStorage
from seed_manager import run_batch_seed, scrape_strategic_content
from finland_list import TOP_100_COMPANIES
from knowledge_base import get_evolving_framework

# VERIFICATION LOG: v4.4.0 - Strict Coordinate Logic
print("Starting Adaptability Scanner Backend (v4.4.0 - Strict Archetypes)...", flush=True)

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

app.mount("/components", StaticFiles(directory="../components"), name="components")
app.mount("/services", StaticFiles(directory="../services"), name="services")

# --- FILE PROCESSING HELPERS ---
async def extract_text_from_file(file: UploadFile) -> str:
    content = await file.read()
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
        "status": "Adaptability Scanner API Online (v4.2.0)", 
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
        # Using 3 Flash for basic grounded task (recommended in guidelines for basic text tasks)
        # and it supports google_search tool.
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
            # Simple cleanup for markdown links or quotes
            if "](" in url: url = url.split("](")[1].strip(")")
            url = url.strip("'").strip('"')
            if url.startswith("http"):
                return {"url": url}
                
        return {"url": ""}
    except Exception as e:
        print(f"   [API] Find URL failed: {e}")
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

@app.get("/company/{company_name}")
async def get_company_details(company_name: str, response: Response):
    response.headers["Cache-Control"] = "no-cache, no-store, must-revalidate"
    data = storage.get_company_data(company_name)
    if not data:
        raise HTTPException(status_code=404, detail="Company report not found in Vercel Blob.")
    return data

@app.get("/admin/company-list")
async def get_company_list():
    return {"companies": TOP_100_COMPANIES}

@app.post("/admin/reset-database")
async def reset_database():
    count = storage.nuke_everything()
    return {"status": "Database Wiped", "deleted_count": count}

# --- MAIN SCAN ENDPOINT (MULTIPART) ---
@app.post("/scan")
async def run_scan(
    companyName: str = Form(...),
    companyUrl: Optional[str] = Form(None),
    reportText: str = Form(""),
    saveToDatabase: bool = Form(True),
    modelId: Optional[str] = Form(None),
    files: List[UploadFile] = File(None)
):
    """
    Hybrid Forensic Scan: Accepts Text + URLs + Uploaded Documents (PDF/HTML).
    """
    try:
        print(f"--- LIVE SCAN INITIATED FOR: {companyName} ---")
        
        # --- MODEL SELECTION STRATEGY ---
        if modelId and modelId.strip():
            # USER FORCE OVERRIDE
            search_model = modelId
            synthesis_model = modelId
            print(f"   [Model Strategy] Manual Override Active: Using {modelId} for all steps.")
        else:
            # AUTO-PILOT (HYBRID)
            # Scanner 2 (Logic) -> Gemini 3 Pro
            # Synthesis (Writing) -> Gemini 2.5 Pro
            search_model = "gemini-3-pro-preview"
            synthesis_model = "gemini-2.5-pro"
            print(f"   [Model Strategy] Auto-Pilot Active: Search={search_model}, Synthesis={synthesis_model}")

        # --- PROCESS FILES ---
        combined_text = reportText if reportText else ""
        uploaded_doc_names = []
        
        if files:
            print(f"   [Upload] Processing {len(files)} uploaded documents...")
            for file in files:
                doc_text = await extract_text_from_file(file)
                if doc_text:
                    combined_text += f"\n\n--- INTERNAL DOCUMENT: {file.filename} ---\n{doc_text}"
                    uploaded_doc_names.append(file.filename)
        
        # --- STEP 1: SCANNER 1 (Intent) ---
        target_url = companyUrl
        
        if not target_url:
            for c in TOP_100_COMPANIES:
                if c['name'].lower() == companyName.lower():
                    target_url = c['url']
                    break

        if not combined_text or len(combined_text) < 100:
            if target_url: combined_text = scrape_strategic_content(target_url, companyName)
            else: combined_text = scrape_strategic_content(None, companyName)

        intent_results = analyze_intent(combined_text)

        # --- STEP 2: SCANNER 2 (Reality) ---
        # Pass intent_score so Scanner 2 can detect washing contradictions
        reality_results = x_ray_search(companyName, target_url, model_id=search_model, intent_score=intent_results["score"])
        gap = intent_results["score"] - reality_results["score"]
        confidence = reality_results.get("confidence_score", 50)
        washing_flag = reality_results.get("washing_flag", False)
        washing_type = reality_results.get("washing_type", None)
        key_people = reality_results.get("key_people", [])
        
        # --- LOGIC CONSISTENCY CHECK ---
        # CRITICAL FIX: "Washing" is impossible if Reality >= Intent.
        # It implies they are doing MORE than they say (Silent Doers).
        if reality_results["score"] >= intent_results["score"]:
            if washing_flag:
                print(f"   [Logic Override] Washing Flag set to FALSE. Reality ({reality_results['score']}) >= Intent ({intent_results['score']}). This is a Surplus/Silent Doer.")
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
        api_key = clean_env_var(os.environ.get("API_KEY") or os.environ.get("GEMINI_API_KEY"))
        
        if api_key:
            try:
                client = genai.Client(api_key=api_key)
                intent_narrative = json.dumps(intent_results["breakdown"])
                evidence_list = ', '.join([f"{l['title']}: {l.get('snippet', '')}" for l in reality_results['links'][:4]])
                knowledge_context = get_evolving_framework()
                
                doc_context = ""
                if uploaded_doc_names:
                    doc_context = f"NOTE: Analysis includes confidential internal documents: {', '.join(uploaded_doc_names)}. Treat this as high-fidelity intent data."
                
                # --- COORDINATE LOGIC (STRICT) ---
                # Lower-Left: Stagnation (Intent < 50, Reality < 50)
                # Lower-Right: Talk-not-Walk (Intent >= 50, Reality < 50)
                # Upper-Left: Silent Doers (Intent < 50, Reality >= 50)
                # Upper-Right: Walk-and-Talk (Intent >= 50, Reality >= 50)
                
                archetype = "Unknown"
                if reality_results["score"] >= 50:
                    if intent_results["score"] >= 50:
                        archetype = "Walk-and-Talk (Aligned)"
                    else:
                        archetype = "Silent Doers (Surplus)"
                else:
                    if intent_results["score"] >= 50:
                        archetype = "Talk-not-Walk (Hype)"
                    else:
                        archetype = "Stagnation (Low/Low)"

                # Logic-aware Context
                washing_context = ""
                if washing_flag:
                    washing_context = f"CRITICAL: The forensic scanner DETECTED {washing_type or 'WASHING'}. This confirms the 'Talk-not-Walk' diagnosis."
                
                prompt = f"""
                ### SYNTHESIS
                Target: {companyName}
                Intent: {intent_results["score"]}, Reality: {reality_results["score"]}, Gap: {gap}
                DIAGNOSIS ARCHETYPE: {archetype.upper()}
                Summary Confidence: {confidence}/100
                Knowledge Base: {knowledge_context}
                Evidence: {evidence_list}
                Intent Data: {intent_narrative}
                {doc_context}
                {washing_context}

                Task: Write a 175-word forensic executive summary.
                1. STATE THE ARCHETYPE ({archetype}) explicitly in the first sentence.
                2. Explicitly mention the 'Summary Confidence' level if it is low (below 50).
                3. If Washing is detected, use the term "{washing_type or 'Modernization Washing'}" explicitly.
                4. Focus on why they are in this specific quadrant.
                """
                
                response = client.models.generate_content(model=synthesis_model, contents=prompt)
                if response.text: summary = response.text
            except Exception as e:
                print(f"Gemini Synthesis Error: {e}")

        new_timestamp = datetime.utcnow().isoformat()
        result_id = str(uuid.uuid4())
        
        history = []
        existing_data = storage.get_company_data(companyName)
        if existing_data:
            history = existing_data.get("history", [])
            history.append({
                "date": existing_data.get("lastUpdated"),
                "gap": existing_data.get("reality_gap", 0)
            })
            if len(history) > 12: history = history[-12:] 

        full_data = {
            "id": result_id,
            "companyName": companyName,
            "companyUrl": target_url, 
            "intent_score": intent_results["score"],
            "reality_score": reality_results["score"],
            "reality_gap": gap,
            "confidence_score": confidence,
            "washing_flag": washing_flag,
            "washing_type": washing_type,
            "intent_breakdown": intent_results["breakdown"],
            "metrics": intent_results.get("metrics", {}),
            "reality_checklist": reality_results["checklist"],
            "evidence_links": reality_results["links"],
            "key_people": key_people,
            "summary_text": summary,
            "lastUpdated": new_timestamp,
            "history": history,
            "is_simulated": reality_results.get("is_simulated", False),
            "uploaded_docs": uploaded_doc_names 
        }
        
        # --- STEP 4: SAVE ---
        if saveToDatabase:
            saved_url = storage.save_company_data(companyName, full_data)
            print(f"   [Live Scan] Saved detailed report to: {saved_url}")
            
            summary_entry = {
                "id": result_id,
                "companyName": companyName,
                "intent_score": intent_results["score"],
                "reality_score": reality_results["score"],
                "reality_gap": gap,
                "lastUpdated": new_timestamp,
                "detailsUrl": saved_url,
                "has_docs": len(uploaded_doc_names) > 0 # FLAG FOR FRONTEND
            }
            # Update index
            storage.update_global_index(summary_entry)
        
        return {"success": True, "data": full_data}

    except Exception as e:
        print(f"Server Error during Live Scan: {str(e)}")
        raise HTTPException(status_code=500, detail=str(e))
