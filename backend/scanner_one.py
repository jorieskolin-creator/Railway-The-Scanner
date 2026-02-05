
import re

# Keyword Definitions (Mirrors constants.ts and PDF Reference)
CATEGORIES = {
    "Operating Model": {
        "good": [
            "Service Areas", "Value Streams", "Stream-aligned", "Distributed Authority", "Sense and Respond",
            "Psychological Safety", "Cross-functional", "Flow Efficiency", "Platform Team", "Autonomy",
            "Outcome-driven", "Micro-enterprises", "Customer Intimacy", "Self-organization", "Experimentation",
            "Launch-and-Learn", "Fluid", "Network of Teams", "Enabling Leadership", "Cognitive Load",
            "Zero Distance", "Communities of Practice", "Transparency", "Human-centric", "End-to-End Responsibility"
        ],
        "bad": [
            "Functional Silos", "Command and Control", "Digital Taylorism", "Handovers", "Resource Efficiency",
            "Approval Chain", "Micromanagement", "Output-driven", "Rigid", "Bureaucracy",
            "Gatekeeping", "Waterfall", "Order-taker", "Compliance-driven", "Siloed Data",
            "HiPPO", "Standardization", "Big Bang", "Utilization Rate", "Legacy Thinking",
            "Spot Optimization", "Ticket-based", "Information Asymmetry", "Need to Know", "Predictability"
        ]
    },
    "Service Portfolio": {
        "good": [
            "SaaS", "Recurring Revenue", "API-First", "Platform", "Ecosystem",
            "Modular", "Scalability", "Continuous Delivery", "User-centric", "Product-Market Fit",
            "Subscription", "Cloud-native", "Data-driven", "Composable", "Service Design",
            "Value Proposition", "Feedback Loop", "MVP", "Consumption-based", "Self-service",
            "Interoperability", "Digital Twin", "Solution-oriented", "Lifetime Value", "Frictionless"
        ],
        "bad": [
            "Legacy", "Monolith", "On-premise", "Customization", "Project-based",
            "One-off Sales", "Maintenance-heavy", "Vendor Lock-in", "Siloed Data", "Hardware-centric",
            "Manual Updates", "Consulting-heavy", "End-of-Life", "Fragmented", "Feature Factory",
            "Complexity", "Static", "Commoditized", "Proprietary", "License-based",
            "Discontinued", "Cost-plus Pricing", "Bloatware", "Version 1.0", "Cannibalization"
        ]
    },
    "Values": {
        "good": [
            "Trust", "Transparency", "Psychological Safety", "Curiosity", "Courage",
            "Inclusion", "Empowerment", "Learning", "Openness", "Empathy",
            "Collaboration", "Purpose-driven", "Adaptability", "Resilience", "Accountability",
            "Diversity", "Innovation", "Feedback", "Growth Mindset", "Integrity",
            "Sustainability", "Experimentation", "Fun", "Human-centric", "Servant Leadership"
        ],
        "bad": [
            "Secrecy", "Fear", "Blame", "Control", "Compliance",
            "Hierarchy", "Silos", "Micromanagement", "Obedience", "Politics",
            "Status Quo", "Risk-averse", "Arrogance", "Bureaucracy", "Rigidity",
            "Greed", "Exclusion", "Punishment", "Competitiveness", "Opacity",
            "Command", "Ego", "Short-termism", "Exploitation", "Distrust"
        ]
    },
    "Strategy": {
        "good": [
            "North Star", "Adaptive", "Value Creation", "Digital Transformation", "Growth",
            "Vision", "Mission", "Customer-centric", "Agile Strategy", "Innovation",
            "Ecosystem", "Data-driven", "Future-proof", "Resilience", "Differentiation",
            "Outcome-based", "Sustainability", "Disruption", "Blue Ocean", "Pivot",
            "Horizon 1/2/3", "Focus", "Alignment", "Execution", "Emergent"
        ],
        "bad": [
            "Cost-cutting", "Defensive", "Short-term", "Static", "Reactive",
            "Generic", "Status Quo", "Siloed", "Unclear", "Budget-driven",
            "Top-down", "Rigid", "Risk-averse", "Legacy-focused", "Competitor-obsessed",
            "Shareholder-only", "Volume-based", "Efficiency-only", "Drifting", "Fragmented",
            "Bureaucratic", "Complacent", "Opaque", "Incremental", "Project-based"
        ]
    },
    "Business Model": {
        "good": [
            "Platform", "Ecosystem", "Recurring Revenue", "Network Effects", "Freemium",
            "SaaS", "Marketplace", "Data Monetization", "Servitization", "Outcome-based Pricing",
            "Direct-to-Consumer", "Sharing Economy", "API Economy", "Scalable", "Asset-light",
            "Community", "User-generated Content", "Open Source", "Partner Network", "Dynamic Pricing",
            "Lifetime Value", "Circular Economy", "Membership", "Flywheel", "Value Co-creation"
        ],
        "bad": [
            "Pipeline", "One-off Sales", "Middlemen", "Rent-seeking", "Asset-heavy",
            "Commodity", "Closed System", "Linear", "Transactional", "Cost-plus",
            "Ad-driven", "Brick-and-mortar", "Intermediary", "Manual", "High Friction",
            "Locked-in", "Inventory-heavy", "Time-based", "Project-based", "Hidden Fees",
            "Arbitrage", "Unsustainable", "Predatory", "Analog", "Dependent"
        ]
    },
    "AI Readiness": {
        "good": [
            "Cloud", "Data Lake", "Machine Learning", "API", "Microservices",
            "DevOps", "Data Mesh", "Automation", "Real-time", "Predictive",
            "Scalable", "Cybersecurity", "Data Quality", "Integration", "Open AI",
            "Python", "Algorithm", "Digital Twin", "Smart", "Self-healing",
            "Serverless", "Agile IT", "Citizen Developer", "Data Governance", "Augmented"
        ],
        "bad": [
            "Legacy", "On-premise", "Siloed Data", "Manual Process", "Spreadsheet",
            "Mainframe", "Monolith", "Security Breach", "Downtime", "Batch Processing",
            "Technical Debt", "Paper-based", "Disconnected", "Proprietary", "Fragmented",
            "Unstructured Data", "Shadow IT", "Waterfall", "Cost Center", "Outdated",
            "Manual Entry", "Black Box", "Vulnerable", "Rigid", "Offline"
        ]
    }
}

def analyze_intent(text: str):
    breakdown = []
    
    total_good_signals = 0
    total_bad_signals = 0
    
    # Calculation Logic based on Scanner 2.0 PDF (Page 10)
    # Threshold for "Good" Signal: 3 mentions
    # Threshold for "Bad" Signal: 1 mention
    GOOD_THRESHOLD = 3
    BAD_THRESHOLD = 1
    
    lower_text = text.lower()

    for category, keywords in CATEGORIES.items():
        unique_good_found = 0
        unique_bad_found = 0
        
        # 1. Count Good Signals (Must meet threshold)
        for word in keywords["good"]:
            pattern = r'\b' + re.escape(word.lower()) + r'\b'
            count = len(re.findall(pattern, lower_text))
            if count >= GOOD_THRESHOLD:
                unique_good_found += 1
            
        # 2. Count Bad Signals (Penalty)
        for word in keywords["bad"]:
            pattern = r'\b' + re.escape(word.lower()) + r'\b'
            count = len(re.findall(pattern, lower_text))
            if count >= BAD_THRESHOLD:
                unique_bad_found += 1
            
        # 3. Calculate "Net Modernization Score"
        # Formula: (Good - Bad) / Total_Possible (25)
        # We assume 25 is the max because that is the list size per category
        net_result = unique_good_found - unique_bad_found
        
        # Normalize to 0-100 scale
        # Max score is 25 (perfect alignment), Min is -25 (total legacy)
        # We map this to 0-100.
        # However, the PDF example: "16 / 25 = 64%". This implies simple division of Net Positive.
        # If net is negative, score is 0.
        
        if net_result < 0:
            score = 0
        else:
            score = (net_result / 25.0) * 100
            if score > 100: score = 100

        breakdown.append({
            "name": category,
            "score": int(score),
            "goodCount": unique_good_found,
            "badCount": unique_bad_found
        })
        
        total_good_signals += unique_good_found
        total_bad_signals += unique_bad_found

    # Global Score Calculation
    # We sum up the category scores and average them
    total_score = sum([c["score"] for c in breakdown])
    global_score = total_score / len(CATEGORIES)

    return {
        "score": int(global_score),
        "breakdown": breakdown
    }
