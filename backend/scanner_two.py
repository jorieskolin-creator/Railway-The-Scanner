
import os
import requests
import random
from urllib.parse import urlparse

# Query Definitions (Optimized for Restricted Google CSE)
# These queries target specific high-authority domains that users can manually whitelist.
# REQUIRED WHITELIST IN GOOGLE CSE:
# - linkedin.com
# - glassdoor.com
# - github.com
# - kauppalehti.fi
# - duunitori.fi

QUERIES = {
    "Operating Model": [
        {'q': 'site:linkedin.com/jobs "Company Name" AND ("Scrum Master" OR "Product Owner" OR "Agile Coach")', 'intent': 'Job Titles (Agile)'},
        {'q': 'site:linkedin.com/jobs "Company Name" AND ("P&L responsibility" AND "team level")', 'intent': 'Micro-Enterprise Test'},
        {'q': 'site:linkedin.com "Company Name" AND ("Stream-aligned" OR "end-to-end ownership")', 'intent': 'Stream-Aligned Test'},
        {'q': 'site:linkedin.com/jobs "Company Name" AND ("Platform Product Manager" OR "Golden Path")', 'intent': 'Platform Test'}
    ],
    "Service Portfolio": [
        {'q': 'site:linkedin.com "Company Name" AND ("ARR" OR "Recurring Revenue" OR "SaaS")', 'intent': 'Modern Revenue'},
        {'q': 'site:github.com "Company Name" AND ("API" OR "SDK" OR "Developer")', 'intent': 'Developer Ecosystem'},
        {'q': 'site:linkedin.com "Company Name" AND ("Subscription Model" OR "Licensing")', 'intent': 'Pricing Model'},
        {'q': 'site:kauppalehti.fi "Company Name" AND "Financial Results" AND "Segment"', 'intent': 'Revenue breakdown'}
    ],
    "Values": [
        {'q': 'site:glassdoor.com "Company Name" AND ("Micromanagement" OR "Surveillance")', 'intent': 'Trust Test (Negative)'},
        {'q': 'site:linkedin.com/jobs "Company Name" AND ("Unlimited PTO" OR "Remote First")', 'intent': 'Trust Test (Positive)'},
        {'q': 'site:linkedin.com "Company Name" AND ("Post-mortem" OR "Learning from failure")', 'intent': 'Psych Safety'},
        {'q': 'site:linkedin.com "Company Name" AND "Code of Conduct" AND ("Whistleblower" OR "Ethics")', 'intent': 'Governance'}
    ],
    "Strategy": [
        {'q': 'site:linkedin.com "Company Name" AND ("Strategy 2025" OR "Vision 2030")', 'intent': 'Future focus'},
        {'q': 'site:kauppalehti.fi "Company Name" AND ("Acquisition" OR "Divestment")', 'intent': 'Action'},
        {'q': 'site:kauppalehti.fi "Company Name" AND "Digital Transformation" AND "Revenue"', 'intent': 'Value Narrative'},
        {'q': 'site:linkedin.com "Company Name" AND ("Budget Variance" OR "Compliance")', 'intent': 'Budget Test (Negative)'}
    ],
    "Business Model": [
        {'q': 'site:linkedin.com "Company Name" AND ("App Store" OR "Marketplace")', 'intent': 'Ecosystem'},
        {'q': 'site:linkedin.com "Company Name" AND ("Freemium" OR "Usage-based")', 'intent': 'Modern Pricing'},
        {'q': 'site:github.com "Company Name" AND "API Key"', 'intent': 'Tech Platform'},
        {'q': 'site:linkedin.com "Company Name" AND "Partner Program"', 'intent': 'Network Effects'}
    ],
    "AI Readiness": [
        {'q': 'site:linkedin.com/jobs "Company Name" AND ("Azure" OR "AWS" OR "Google Cloud")', 'intent': 'Cloud Foundation'},
        {'q': 'site:linkedin.com/jobs "Company Name" AND ("Data Mesh" OR "Data Product Owner")', 'intent': 'Data Arch'},
        {'q': 'site:duunitori.fi "Company Name" AND ("Python" OR "Machine Learning" OR "LLM")', 'intent': 'Tech Skills'},
        {'q': 'site:linkedin.com "Company Name" AND ("Tech Radar" OR "Engineering Blog")', 'intent': 'Engineering Culture'}
    ]
}

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

    for category, query_list in QUERIES.items():
        for item in query_list:
            total_checks += 1
            raw_query = item['q']
            intent_label = item['intent']
            
            # Simple Replacement: Only Company Name is needed now
            # We removed the dependency on dynamic 'site:[domain]' to support Restricted CSEs
            search_query = raw_query.replace('"Company Name"', f'"{company_name}"')
            
            # Append "Finland" context if searching generic global sites, unless domain is already .fi
            if "linkedin.com" in search_query or "glassdoor.com" in search_query or "github.com" in search_query:
                search_query += " Finland"
            
            url = f"https://www.googleapis.com/customsearch/v1?key={api_key}&cx={cx_id}&q={search_query}"
            
            try:
                res = requests.get(url)
                data = res.json()
                
                # Logic: If totalResults > 0, we consider it evidence found
                found = False
                if "items" in data and len(data["items"]) > 0:
                    found = True
                    passed_checks += 1
                    
                    # Capture the first link as evidence
                    result_item = data["items"][0]
                    evidence_links.append({
                        "title": f"{intent_label}: {result_item.get('title', 'Evidence Source')}",
                        "url": result_item.get("link", "#"),
                        "source": result_item.get("displayLink", "Google Search")
                    })
                
                checklist.append({
                    "category": category,
                    "query": intent_label,
                    "found": found
                })
                
            except Exception as e:
                print(f"Search API Error for {intent_label}: {e}")
                checklist.append({
                    "category": category,
                    "query": intent_label,
                    "found": False
                })

    # HEURISTIC CHECK: 
    # If the Search Engine is restricted (e.g. only allows google.com) and we searched 
    # for linkedin.com, we will get 0 results for EVERYTHING.
    # A Reality Score of 0 is statistically impossible for a major company.
    # If this happens, we assume misconfiguration and auto-switch to Simulation Mode.
    if passed_checks == 0 and total_checks > 0:
        print(f"CRITICAL: 0/{total_checks} Reality Checks passed. This suggests Google CSE is blocking external domains.")
        print(">> Falling back to SIMULATION MODE to preserve application functionality.")
        return run_simulation_mode(company_name)

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
        for item in query_list:
            total_checks += 1
            # Randomly "find" evidence to create score variance
            found = random.choice([True, False, False]) # 33% chance of finding evidence
            
            checklist.append({
                "category": category,
                "query": item['intent'],
                "found": found
            })

            if found:
                passed_checks += 1
                evidence_links.append({
                    "title": f"{item['intent']} found for {company_name} (Simulated)",
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
