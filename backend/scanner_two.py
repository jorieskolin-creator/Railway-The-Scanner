import os
import requests
import random

# Configuration: Define exactly where we look for evidence
# The code uses 'site:' operators to force Google to only look here.
SEARCH_SOURCES = [
    "linkedin.com/jobs",
    "indeed.com",
    "boards.greenhouse.io",
    "jobs.lever.co"
]

# Query Definitions
QUERIES = {
    "Adaptive Operating Model": [
        "Stream-aligned teams", "Micro-enterprises", "Platform Engineering", "Distributed Authority"
    ],
    "Strategy & Finance": [
        "Rolling Forecast", "Beyond Budgeting", "Dynamic Allocation", "Relative Targets"
    ],
    "Human-Centricity": [
        "Psychological Safety", "Blameless Post-Mortem", "Salary Transparency", "Self-Management"
    ],
    "AI & Data Readiness": [
        "Data Mesh", "Data Product Owner", "Automated Governance", "MLOps"
    ]
}

def x_ray_search(company_name: str):
    api_key = os.environ.get("GOOGLE_API_KEY")
    cx_id = os.environ.get("CX_ENGINE_ID")
    
    checklist = []
    evidence_links = []
    
    total_checks = 0
    passed_checks = 0
    
    # Fallback to Simulation Mode if keys are missing
    if not api_key or not cx_id:
        print("Warning: Google API credentials (GOOGLE_API_KEY, CX_ENGINE_ID) missing.")
        print("Running in SIMULATION MODE for Reality Scanner.")
        return run_simulation_mode(company_name)

    # Pre-calculate the site string: "site:linkedin.com OR site:indeed.com ..."
    # This acts as the strict filter for the Google Search Engine
    sites_query = " OR ".join([f"site:{s}" for s in SEARCH_SOURCES])

    for category, query_list in QUERIES.items():
        for query in query_list:
            total_checks += 1
            
            # Construct Search Term
            # Format: (site:A OR site:B) "CompanyName" "QueryTerm"
            search_term = f'({sites_query}) "{company_name}" "{query}"'
            
            url = f"https://www.googleapis.com/customsearch/v1?key={api_key}&cx={cx_id}&q={search_term}"
            
            try:
                res = requests.get(url)
                data = res.json()
                
                # Logic: If totalResults > 0, we consider it evidence found
                found = False
                if "items" in data and len(data["items"]) > 0:
                    found = True
                    passed_checks += 1
                    
                    # Capture the first link as evidence
                    item = data["items"][0]
                    evidence_links.append({
                        "title": item.get("title", "Evidence Source"),
                        "url": item.get("link", "#"),
                        "source": item.get("displayLink", "Google Search")
                    })
                
                checklist.append({
                    "category": category,
                    "query": query,
                    "found": found
                })
                
            except Exception as e:
                print(f"Search API Error for {query}: {e}")
                checklist.append({
                    "category": category,
                    "query": query,
                    "found": False
                })

    score = 0
    if total_checks > 0:
        score = (passed_checks / total_checks) * 100

    return {
        "score": int(score),
        "checklist": checklist,
        "links": evidence_links
    }

def run_simulation_mode(company_name):
    """
    Generates realistic looking data so the UI works without API keys.
    """
    checklist = []
    evidence_links = []
    total_checks = 0
    passed_checks = 0

    print(f"Simulating reality check for {company_name}...")

    for category, query_list in QUERIES.items():
        for query in query_list:
            total_checks += 1
            # Randomly "find" evidence to create score variance
            found = random.choice([True, False, False]) # 33% chance of finding evidence
            
            checklist.append({
                "category": category,
                "query": query,
                "found": found
            })

            if found:
                passed_checks += 1
                evidence_links.append({
                    "title": f"{company_name} - {query} (Simulated Result)",
                    "url": "#",
                    "source": "Simulated Search Match"
                })

    score = 0
    if total_checks > 0:
        score = (passed_checks / total_checks) * 100
        
    return {
        "score": int(score),
        "checklist": checklist,
        "links": evidence_links
    }