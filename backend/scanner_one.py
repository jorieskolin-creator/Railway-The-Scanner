
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

# Negation terms based on PDF Page 10 ("Negative Look-around")
NEGATION_TERMS = ["eliminate", "remove", "avoid", "anti", "reduce", "less", "no", "not", "without", "free from", "versus", "vs"]

def is_negated(text_window):
    """
    Checks if any negation term appears in the text window preceding a keyword.
    """
    lower_window = text_window.lower()
    for term in NEGATION_TERMS:
        if term in lower_window:
            return True
    return False

def analyze_intent(text: str):
    breakdown = []
    
    total_good_matches = 0
    total_bad_matches = 0
    total_words = len(text.split())
    if total_words == 0: total_words = 1 # Avoid div by zero

    # Thresholds based on PDF logic
    GOOD_THRESHOLD = 3
    BAD_THRESHOLD = 1
    
    lower_text = text.lower()

    for category, keywords in CATEGORIES.items():
        unique_good_found = 0
        unique_bad_found = 0
        
        # 1. Count Good Signals
        for word in keywords["good"]:
            pattern = r'\b' + re.escape(word.lower()) + r'\b'
            matches = list(re.finditer(pattern, lower_text))
            if len(matches) >= GOOD_THRESHOLD:
                unique_good_found += 1
                total_good_matches += len(matches) # Add total volume
            elif len(matches) > 0:
                # Still add volume even if threshold not met for "unique" scoring
                total_good_matches += len(matches) 
            
        # 2. Count Bad Signals with NEGATIVE LOOK-AROUND (Page 10)
        for word in keywords["bad"]:
            pattern = r'\b' + re.escape(word.lower()) + r'\b'
            matches = list(re.finditer(pattern, lower_text))
            
            valid_bad_count = 0
            for match in matches:
                # Context Window: Check 50 characters before the match
                start_index = max(0, match.start() - 50)
                preceding_text = lower_text[start_index:match.start()]
                
                if not is_negated(preceding_text):
                    valid_bad_count += 1
                else:
                    # Debug: print(f"Ignored negated bad word: {word} (Context: {preceding_text})")
                    pass

            if valid_bad_count >= BAD_THRESHOLD:
                unique_bad_found += 1
            
            total_bad_matches += valid_bad_count

        # 3. Calculate "Net Modernization Score" (Page 10 Formula)
        # Score = (Good Matches - Bad Matches) / Total Items (25)
        net_result = unique_good_found - unique_bad_found
        
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

    # --- AGGREGATE METRICS ---

    # A. Global Adaptability Score
    total_score = sum([c["score"] for c in breakdown])
    global_score = total_score / len(CATEGORIES)

    # B. Friction Index (Page 14)
    # Formula: 1 - (|Good - Bad| / (Good + Bad))
    # Measures confusion/conflict in the organization.
    denominator = total_good_matches + total_bad_matches
    if denominator == 0:
        friction_index = 0.0 # No data, no friction
    else:
        # Calculate raw ratio
        ratio = abs(total_good_matches - total_bad_matches) / denominator
        friction_index = 1.0 - ratio 
        # Result: 0.0 (Coherent) to 1.0 (High Friction/Conflicted)
        
        # NOTE: PDF says "1.0 means perfect 50/50 friction". 
        # So Higher Number = Higher Friction (Bad).

    # C. Vibrancy Score (Page 15)
    # Formula: Total Matches / Total Word Count
    # Measures signal intensity.
    vibrancy_raw = denominator / total_words
    # Normalize: Assuming > 5% keyword density is very high (100)
    vibrancy_score = (vibrancy_raw / 0.05) * 100 
    if vibrancy_score > 100: vibrancy_score = 100

    return {
        "score": int(global_score),
        "breakdown": breakdown,
        "metrics": {
            "friction_index": round(friction_index, 2),
            "vibrancy_score": int(vibrancy_score),
            "total_good_mentions": total_good_matches,
            "total_bad_mentions": total_bad_matches
        }
    }
