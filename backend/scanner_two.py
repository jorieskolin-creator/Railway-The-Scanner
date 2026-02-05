import os
import requests
import random
from urllib.parse import urlparse

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

def get_domain(url):
    """Extracts domain from url (e.g., https://www.nokia.com -> nokia.com)"""
    if not url: return None
    try:
        parsed = urlparse(url)
        domain = parsed.netloc or parsed.path
        if domain.startswith("www."):
            return domain[4:]
        return domain
    except:
        return None

def x_ray_search(company_name: str, company_url: str = None):
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

    # Extract domain for site-specific search
    target_domain = get_domain(company_url)

    for category, query_list in QUERIES.items():
        for query in query_list:
            total_checks += 1
            
            # Smart Query Construction:
            # 1. Search the company name + query + Finland (Broad search on the sources defined in Console)
            # 2. OR Search directly on their own domain if provided (Deep search)
            
            if target_domain:
                # E.g. (site:nokia.com "Data Mesh") OR ("Nokia" "Data Mesh" Finland)
                search_term = f'(site:{target_domain} "{query}") OR ("{company_name}" "{query}" Finland)'
            else:
                search_term = f'"{company_name}" "{query}" Finland'
            
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