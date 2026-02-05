
import os
import requests
import random
from urllib.parse import urlparse

# Query Definitions (Mirrors constants.ts and PDF X-Ray Logic)
QUERIES = {
    "Operating Model": [
        {'q': 'site:linkedin.com/jobs "Company Name" AND ("Scrum Master" OR "Product Owner" OR "Agile Coach")', 'intent': 'Job Titles (Agile)'},
        {'q': 'site:linkedin.com/jobs "Company Name" AND ("P&L responsibility" AND "team level")', 'intent': 'Micro-Enterprise Test'},
        {'q': '"Company Name" AND ("Stream-aligned" OR "end-to-end ownership")', 'intent': 'Stream-Aligned Test'},
        {'q': 'site:linkedin.com/jobs "Company Name" AND ("Platform Product Manager" OR "Golden Path")', 'intent': 'Platform Test'}
    ],
    "Service Portfolio": [
        {'q': '"Company Name" AND ("ARR" OR "Recurring Revenue" OR "SaaS") filetype:pdf', 'intent': 'Modern Revenue'},
        {'q': 'site:[domain] "API Documentation" OR "Developer Portal"', 'intent': 'Platform Indicator'},
        {'q': 'site:[domain] "Pricing" AND ("Subscription" OR "License")', 'intent': 'Pricing Model'},
        {'q': '"Company Name" "Financial Results" AND "Segment Reporting" filetype:pdf', 'intent': 'Revenue breakdown'}
    ],
    "Values": [
        {'q': 'site:glassdoor.com "Company Name" AND ("Micromanagement" OR "Surveillance")', 'intent': 'Trust Test (Negative)'},
        {'q': 'site:[domain] "Unlimited PTO" OR "Remote First"', 'intent': 'Trust Test (Positive)'},
        {'q': 'site:[domain] "Post-mortem" OR "Learning from failure"', 'intent': 'Psych Safety'},
        {'q': 'site:[domain] "Code of Conduct" AND ("Whistleblower" OR "Ethics")', 'intent': 'Governance'}
    ],
    "Strategy": [
        {'q': '"Company Name" AND ("Strategy 2025" OR "Vision 2030")', 'intent': 'Future focus'},
        {'q': '"Company Name" AND ("Acquisition" OR "Divestment") site:kauppalehti.fi', 'intent': 'Action'},
        {'q': '"Company Name" AND "Digital Transformation" AND "Revenue"', 'intent': 'Value Narrative'},
        {'q': '"Company Name" AND ("Budget Variance" OR "Compliance")', 'intent': 'Budget Test (Negative)'}
    ],
    "Business Model": [
        {'q': 'site:[domain] "App Store" OR "Marketplace"', 'intent': 'Ecosystem'},
        {'q': 'site:[domain] "Pricing" AND ("Freemium" OR "Usage-based")', 'intent': 'Modern Pricing'},
        {'q': 'site:[domain] "API Key" OR "SDK"', 'intent': 'Tech Platform'},
        {'q': '"Company Name" AND ("Partner Program" OR "Ecosystem")', 'intent': 'Network Effects'}
    ],
    "AI Readiness": [
        {'q': 'site:linkedin.com/jobs "Company Name" AND ("Azure" OR "AWS" OR "Google Cloud")', 'intent': 'Cloud Foundation'},
        {'q': 'site:linkedin.com/jobs "Company Name" AND ("Data Mesh" OR "Data Product Owner")', 'intent': 'Data Arch'},
        {'q': 'site:duunitori.fi "Company Name" AND ("Python" OR "Machine Learning" OR "LLM")', 'intent': 'Tech Skills'},
        {'q': 'site:[domain] "Tech Radar" OR "Engineering Blog"', 'intent': 'Engineering Culture'}
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
    target_domain = get_domain(company_url) if company_url else "linkedin.com" # Fallback domain

    for category, query_list in QUERIES.items():
        for item in query_list:
            total_checks += 1
            raw_query = item['q']
            intent_label = item['intent']
            
            # Dynamic String Replacement for X-Ray
            # We replace "Company Name" and [domain] placeholders with actual values
            search_query = raw_query.replace('"Company Name"', f'"{company_name}"')
            if target_domain:
                search_query = search_query.replace('site:[domain]', f'site:{target_domain}')
            else:
                # If no domain provided, remove the site: operator logic or fallback to broad search
                search_query = search_query.replace('site:[domain]', f'"{company_name}"')

            # Append "Finland" for geo-specificity if it looks like a broad search
            if "site:" not in search_query and "Finland" not in search_query:
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
                    "query": intent_label, # Display the intent (e.g., "Job Titles") not the raw query
                    "found": found
                })
                
            except Exception as e:
                print(f"Search API Error for {intent_label}: {e}")
                checklist.append({
                    "category": category,
                    "query": intent_label,
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
