import re

# Keyword Definitions (Mirrors constants.ts)
CATEGORIES = {
    "Operating Model": {
        "good": ["Service Areas", "Value Streams", "Stream-aligned", "Distributed Authority", "Sense and Respond"],
        "bad": ["Functional Silos", "Command and Control", "Digital Taylorism", "Handovers", "Approval Chain"]
    },
    "Service Portfolio": {
        "good": ["SaaS", "Recurring Revenue", "API-First", "Platform", "Ecosystem"],
        "bad": ["Monolith", "On-premise", "Project-based", "One-off Sales", "Vendor Lock-in"]
    },
    "Values": {
        "good": ["Trust", "Transparency", "Psychological Safety", "Curiosity", "Courage"],
        "bad": ["Secrecy", "Fear", "Blame", "Control", "Compliance"]
    },
    "Strategy": {
        "good": ["North Star", "Adaptive", "Value Creation", "Digital Transformation", "Growth"],
        "bad": ["Cost-cutting", "Defensive", "Short-term", "Static", "Reactive"]
    },
    "Business Model": {
        "good": ["Platform", "Ecosystem", "Recurring Revenue", "Network Effects", "Freemium"],
        "bad": ["Pipeline", "One-off Sales", "Middlemen", "Rent-seeking", "Asset-heavy"]
    },
    "AI Readiness": {
        "good": ["Cloud", "Data Lake", "Machine Learning", "API", "Microservices"],
        "bad": ["Legacy", "On-premise", "Siloed Data", "Manual Process", "Spreadsheet"]
    }
}

def analyze_intent(text: str):
    breakdown = []
    total_good = 0
    total_bad = 0
    
    lower_text = text.lower()

    for category, keywords in CATEGORIES.items():
        good_count = 0
        bad_count = 0
        
        for word in keywords["good"]:
            good_count += len(re.findall(re.escape(word.lower()), lower_text))
            
        for word in keywords["bad"]:
            bad_count += len(re.findall(re.escape(word.lower()), lower_text))
            
        # Calculate Category Score
        total_mentions = good_count + bad_count
        score = 50 # Default neutral
        if total_mentions > 0:
            score = (good_count / total_mentions) * 100
            
        breakdown.append({
            "name": category,
            "score": int(score),
            "goodCount": good_count,
            "badCount": bad_count
        })
        
        total_good += good_count
        total_bad += bad_count

    # Global Score
    global_total = total_good + total_bad
    global_score = 50
    if global_total > 0:
        global_score = (total_good / global_total) * 100

    return {
        "score": int(global_score),
        "breakdown": breakdown
    }
