import os
from scanner_one import analyze_intent
from storage import BlobStorage
from datetime import datetime
import uuid

# Mock Text for seeding
MOCK_TEXT_BAD = "We value Command and Control. Our Strategy is Cost-cutting. We use Functional Silos."
MOCK_TEXT_GOOD = "We use Stream-aligned teams and Distributed Authority. Our North Star is Value Creation."

def seed_data():
    print("Starting Seed Process...")
    storage = BlobStorage()
    
    companies = [
        {"name": "LegacyCorp", "text": MOCK_TEXT_BAD, "r_score": 20},
        {"name": "AdaptiveFlow", "text": MOCK_TEXT_GOOD, "r_score": 85},
        {"name": "GreenWash Inc", "text": MOCK_TEXT_GOOD, "r_score": 25}, # High Intent, Low Reality
        {"name": "SilentExecutors", "text": MOCK_TEXT_BAD, "r_score": 75}, # Low Intent, High Reality
        {"name": "AverageJoes", "text": "A mix of Cost-cutting and Growth.", "r_score": 50}
    ]
    
    dashboard_data = []
    
    for c in companies:
        print(f"Processing {c['name']}...")
        intent = analyze_intent(c['text'])
        
        # We Mock reality score here to ensure we get specific quadrants for the chart
        # instead of relying on live google searches during seed
        reality_score = c['r_score']
        gap = intent["score"] - reality_score
        
        entry = {
            "id": str(uuid.uuid4()),
            "companyName": c["name"],
            "intent_score": intent["score"],
            "reality_score": reality_score,
            "reality_gap": gap,
            "intent_breakdown": intent["breakdown"],
            "reality_checklist": [],
            "evidence_links": [],
            "summary_text": f"Seeded analysis for {c['name']}. Gap: {gap}",
            "lastUpdated": datetime.utcnow().isoformat()
        }
        dashboard_data.append(entry)
        
    print("Uploading dashboard_data.json to Blob...")
    url = storage.upload_json("dashboard_data.json", dashboard_data)
    
    if url:
        print(f"Success! Data available at: {url}")
        print("Set this URL as VITE_BLOB_URL in your frontend.")
    else:
        print("Upload failed. Check BLOB_READ_WRITE_TOKEN.")

if __name__ == "__main__":
    seed_data()
