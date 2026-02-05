
import os
import requests
import random
import re
import hashlib
from urllib.parse import urlparse

# REFINED QUERIES FOR FINNISH MARKET INTELLIGENCE
# These queries specifically target the high-authority domains you have whitelisted:
# Jobs: linkedin.com, duunitori.fi, oikotie.fi, thehub.io, indeed.com
# News: tivi.fi, kauppalehti.fi, talouselama.fi, tekniikkatalous.fi
# Tech: github.com, stackoverflow.com, medium.com

QUERIES = {
    "Operating Model": [
        # Look for agile/modern roles across all major job boards
        {'q': '(site:linkedin.com/jobs OR site:duunitori.fi OR site:oikotie.fi OR site:thehub.io) "Company Name" AND ("Scrum Master" OR "Product Owner" OR "Agile Coach")', 'intent': 'Agile Roles (Recruitment)'},
        # Look for evidence of team autonomy
        {'q': '(site:linkedin.com OR site:thehub.io OR site:medium.com) "Company Name" AND ("Stream-aligned" OR "Autonomous teams" OR "Micro-enterprises")', 'intent': 'Team Structure'},
        # Look for culture descriptions
        {'q': '(site:glassdoor.com OR site:indeed.com) "Company Name" AND ("Flat hierarchy" OR "Distributed authority" OR "Self-organization")', 'intent': 'Org Structure Evidence'}
    ],
    "Service Portfolio": [
        # Business model validation via financial news
        {'q': '(site:kauppalehti.fi OR site:talouselama.fi OR site:tivi.fi) "Company Name" AND ("SaaS" OR "Recurring Revenue" OR "Platform Economy")', 'intent': 'Business Model News'},
        # Tech ecosystem checks
        {'q': '(site:github.com OR site:thehub.io) "Company Name" AND ("API" OR "SDK" OR "Developer Portal")', 'intent': 'Developer Ecosystem'},
        # Investment news regarding ecosystems
        {'q': '(site:kauppalehti.fi OR site:talouselama.fi) "Company Name" AND "Investment" AND ("Startup" OR "Ecosystem")', 'intent': 'Ecosystem Investment'}
    ],
    "Values": [
        # Employee sentiment on global platforms
        {'q': 'site:glassdoor.com "Company Name" AND ("Micromanagement" OR "Bureaucracy" OR "Politics")', 'intent': 'Negative Sentiment Check'},
        # Modern work perks
        {'q': '(site:linkedin.com OR site:duunitori.fi OR site:oikotie.fi) "Company Name" AND ("Remote First" OR "Hybrid" OR "Flexible hours")', 'intent': 'Modern Work Policy'},
        # Culture blog posts
        {'q': '(site:medium.com OR site:linkedin.com) "Company Name" AND ("Post-mortem" OR "Psychological Safety" OR "Failure culture")', 'intent': 'Culture Blog Posts'}
    ],
    "Strategy": [
        # Strategic vision in financial press
        {'q': '(site:kauppalehti.fi OR site:talouselama.fi) "Company Name" AND ("Strategy 2025" OR "Transformation" OR "Vision")', 'intent': 'Strategic Vision'},
        # Tech strategy specific news
        {'q': '(site:tivi.fi OR site:tekniikkatalous.fi) "Company Name" AND ("Digital Transformation" OR "Modernization" OR "Cloud migration")', 'intent': 'Tech Strategy News'},
        # Negative signals (layoffs, cost cutting)
        {'q': '(site:kauppalehti.fi OR site:talouselama.fi) "Company Name" AND ("Cost cutting" OR "YT-neuvottelut" OR "Layoffs" OR "Savings")', 'intent': 'Defensive Strategy (Negative)'}
    ],
    "Business Model": [
        # Platform/Marketplace evidence
        {'q': '(site:tivi.fi OR site:linkedin.com) "Company Name" AND ("Marketplace" OR "Platform Business" OR "App Store")', 'intent': 'Platform Business'},
        # Shift to services
        {'q': '(site:kauppalehti.fi OR site:talouselama.fi) "Company Name" AND ("Service Business" OR "Recurring" OR "Subscription")', 'intent': 'Service Shift'},
        # Open source contribution
        {'q': 'site:github.com "Company Name"', 'intent': 'Open Innovation'}
    ],
    "AI Readiness": [
        # Hiring for AI/Data roles
        {'q': '(site:duunitori.fi OR site:oikotie.fi OR site:linkedin.com) "Company Name" AND ("Data Engineer" OR "MLOps" OR "AI Specialist" OR "Data Mesh")', 'intent': 'AI Hiring'},
        # Tech stack news
        {'q': '(site:tivi.fi OR site:tekniikkatalous.fi) "Company Name" AND ("Generative AI" OR "Cloud Native" OR "Azure" OR "AWS")', 'intent': 'Tech Stack News'},
        # Evidence of engineering output
        {'q': '(site:github.com OR site:stackoverflow.com OR site:medium.com) "Company Name" AND ("Python" OR "Kubernetes" OR "React")', 'intent': 'Modern Tech Stack'}
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
            
            # Insert Company Name
            search_query = raw_query.replace('"Company Name"', f'"{company_name}"')
            
            # Context Logic:
            # If the query is strictly looking at global sites (linkedin, glassdoor), add "Finland" to context.
            # If the query is already looking at .fi sites (kauppalehti.fi, duunitori.fi), do NOT add "Finland"
            # as it might limit results too much if the content is in Finnish.
            if ".fi" not in search_query:
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
    # If we get 0 results for a major company, the API might be blocking our requests
    # or the CSE configuration might be rejecting the specific 'site:' parameters.
    if passed_checks == 0 and total_checks > 0:
        print(f"CRITICAL: 0/{total_checks} Reality Checks passed. Fallback to simulation.")
        return run_simulation_mode(company_name)

    score = 0
    if total_checks > 0:
        score = (passed_checks / total_checks) * 100

    return {
        "score": int(score),
        "checklist": checklist,
        "links": evidence_links,
        "is_simulated": False
    }

def run_simulation_mode(company_name):
    """
    Generates deterministic simulation data based on company name.
    This ensures that if you re-scan the same company in simulation mode, 
    you get the same result (consistency), rather than random noise.
    """
    checklist = []
    evidence_links = []
    total_checks = 0
    passed_checks = 0

    print(f"Simulating reality check for {company_name}...")
    
    # Use Company Name to seed the random number generator
    # This makes the "simulation" consistent for the same input
    seed_val = int(hashlib.sha256(company_name.encode('utf-8')).hexdigest(), 16) % 10**8
    rng = random.Random(seed_val)

    for category, query_list in QUERIES.items():
        for item in query_list:
            total_checks += 1
            # Randomly "find" evidence to create score variance (biased slightly positive for realism)
            # 60% chance of finding evidence in simulation
            found = rng.choice([True, False, True, False, True]) 
            
            checklist.append({
                "category": category,
                "query": item['intent'],
                "found": found
            })

            if found:
                passed_checks += 1
                
                # Extract a domain from the query string to make the fake link look real
                # e.g. "site:kauppalehti.fi" -> "kauppalehti.fi"
                mock_source = "Google Search"
                domains = re.findall(r'site:([\w\.]+)', item['q'])
                if domains:
                    mock_source = rng.choice(domains)
                
                evidence_links.append({
                    "title": f"{item['intent']} found for {company_name}",
                    "url": "#",
                    "source": mock_source 
                })

    score = 0
    if total_checks > 0:
        score = (passed_checks / total_checks) * 100
        
    return {
        "score": int(score),
        "checklist": checklist,
        "links": evidence_links,
        "is_simulated": True
    }
