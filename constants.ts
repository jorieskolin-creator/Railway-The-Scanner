
import { Scanner1Category, Scanner2Category } from './types';

export const INTENT_KEYWORDS = {
  [Scanner1Category.OperatingModel]: {
    good: [
      "Service Areas", "Value Streams", "Stream-aligned", "Distributed Authority", "Sense and Respond",
      "Psychological Safety", "Cross-functional", "Flow Efficiency", "Platform Team", "Autonomy",
      "Outcome-driven", "Micro-enterprises", "Customer Intimacy", "Self-organization", "Experimentation",
      "Launch-and-Learn", "Fluid", "Network of Teams", "Enabling Leadership", "Cognitive Load",
      "Zero Distance", "Communities of Practice", "Transparency", "Human-centric", "End-to-End Responsibility"
    ],
    bad: [
      "Functional Silos", "Command and Control", "Digital Taylorism", "Handovers", "Resource Efficiency",
      "Approval Chain", "Micromanagement", "Output-driven", "Rigid", "Bureaucracy",
      "Gatekeeping", "Waterfall", "Order-taker", "Compliance-driven", "Siloed Data",
      "HiPPO", "Standardization", "Big Bang", "Utilization Rate", "Legacy Thinking",
      "Spot Optimization", "Ticket-based", "Information Asymmetry", "Need to Know", "Predictability"
    ]
  },
  [Scanner1Category.ServicePortfolio]: {
    good: [
      "SaaS", "Recurring Revenue", "API-First", "Platform", "Ecosystem",
      "Modular", "Scalability", "Continuous Delivery", "User-centric", "Product-Market Fit",
      "Subscription", "Cloud-native", "Data-driven", "Composable", "Service Design",
      "Value Proposition", "Feedback Loop", "MVP", "Consumption-based", "Self-service",
      "Interoperability", "Digital Twin", "Solution-oriented", "Lifetime Value", "Frictionless"
    ],
    bad: [
      "Legacy", "Monolith", "On-premise", "Customization", "Project-based",
      "One-off Sales", "Maintenance-heavy", "Vendor Lock-in", "Siloed Data", "Hardware-centric",
      "Manual Updates", "Consulting-heavy", "End-of-Life", "Fragmented", "Feature Factory",
      "Complexity", "Static", "Commoditized", "Proprietary", "License-based",
      "Discontinued", "Cost-plus Pricing", "Bloatware", "Version 1.0", "Cannibalization"
    ]
  },
  [Scanner1Category.Values]: {
    good: [
      "Trust", "Transparency", "Psychological Safety", "Curiosity", "Courage",
      "Inclusion", "Empowerment", "Learning", "Openness", "Empathy",
      "Collaboration", "Purpose-driven", "Adaptability", "Resilience", "Accountability",
      "Diversity", "Innovation", "Feedback", "Growth Mindset", "Integrity",
      "Sustainability", "Experimentation", "Fun", "Human-centric", "Servant Leadership"
    ],
    bad: [
      "Secrecy", "Fear", "Blame", "Control", "Compliance",
      "Hierarchy", "Silos", "Micromanagement", "Obedience", "Politics",
      "Status Quo", "Risk-averse", "Arrogance", "Bureaucracy", "Rigidity",
      "Greed", "Exclusion", "Punishment", "Competitiveness", "Opacity",
      "Command", "Ego", "Short-termism", "Exploitation", "Distrust"
    ]
  },
  [Scanner1Category.Strategy]: {
    good: [
      "North Star", "Adaptive", "Value Creation", "Digital Transformation", "Growth",
      "Vision", "Mission", "Customer-centric", "Agile Strategy", "Innovation",
      "Ecosystem", "Data-driven", "Future-proof", "Resilience", "Differentiation",
      "Outcome-based", "Sustainability", "Disruption", "Blue Ocean", "Pivot",
      "Horizon 1/2/3", "Focus", "Alignment", "Execution", "Emergent"
    ],
    bad: [
      "Cost-cutting", "Defensive", "Short-term", "Static", "Reactive",
      "Generic", "Status Quo", "Siloed", "Unclear", "Budget-driven",
      "Top-down", "Rigid", "Risk-averse", "Legacy-focused", "Competitor-obsessed",
      "Shareholder-only", "Volume-based", "Efficiency-only", "Drifting", "Fragmented",
      "Bureaucratic", "Complacent", "Opaque", "Incremental", "Project-based"
    ]
  },
  [Scanner1Category.BusinessModel]: {
    good: [
      "Platform", "Ecosystem", "Recurring Revenue", "Network Effects", "Freemium",
      "SaaS", "Marketplace", "Data Monetization", "Servitization", "Outcome-based Pricing",
      "Direct-to-Consumer", "Sharing Economy", "API Economy", "Scalable", "Asset-light",
      "Community", "User-generated Content", "Open Source", "Partner Network", "Dynamic Pricing",
      "Lifetime Value", "Circular Economy", "Membership", "Flywheel", "Value Co-creation"
    ],
    bad: [
      "Pipeline", "One-off Sales", "Middlemen", "Rent-seeking", "Asset-heavy",
      "Commodity", "Closed System", "Linear", "Transactional", "Cost-plus",
      "Ad-driven", "Brick-and-mortar", "Intermediary", "Manual", "High Friction",
      "Locked-in", "Inventory-heavy", "Time-based", "Project-based", "Hidden Fees",
      "Arbitrage", "Unsustainable", "Predatory", "Analog", "Dependent"
    ]
  },
  [Scanner1Category.AIReadiness]: {
    good: [
      "Cloud", "Data Lake", "Machine Learning", "API", "Microservices",
      "DevOps", "Data Mesh", "Automation", "Real-time", "Predictive",
      "Scalable", "Cybersecurity", "Data Quality", "Integration", "Open AI",
      "Python", "Algorithm", "Digital Twin", "Smart", "Self-healing",
      "Serverless", "Agile IT", "Citizen Developer", "Data Governance", "Augmented"
    ],
    bad: [
      "Legacy", "On-premise", "Siloed Data", "Manual Process", "Spreadsheet",
      "Mainframe", "Monolith", "Security Breach", "Downtime", "Batch Processing",
      "Technical Debt", "Paper-based", "Disconnected", "Proprietary", "Fragmented",
      "Unstructured Data", "Shadow IT", "Waterfall", "Cost Center", "Outdated",
      "Manual Entry", "Black Box", "Vulnerable", "Rigid", "Offline"
    ]
  }
};

export const REALITY_QUERIES = {
  [Scanner2Category.OperatingModel]: [
    '("Scrum Master" OR "Product Owner" OR "Agile Coach")', 
    '("P&L responsibility" AND "team level")', 
    '"Stream-aligned" OR "end-to-end ownership"', 
    '"Platform Product Manager" OR "Golden Path"'
  ],
  [Scanner2Category.ServicePortfolio]: [
    '"ARR" OR "Recurring Revenue" OR "SaaS"', 
    '"API Documentation" OR "Developer Portal"', 
    '"Subscription" OR "License"', 
    '"Financial Results" AND "Segment Reporting"'
  ],
  [Scanner2Category.Values]: [
    '"Micromanagement" OR "Surveillance"', 
    '"Unlimited PTO" OR "Remote First"', 
    '"Post-mortem" OR "Learning from failure"', 
    '"Whistleblower" OR "Code of Conduct"'
  ],
  [Scanner2Category.Strategy]: [
    '"Strategy 2025" OR "Vision 2030"', 
    '"Acquisition" OR "Divestment"', 
    '"Digital Transformation" OR "AI" AND "Revenue"', 
    '"Budget Variance" OR "Compliance"'
  ],
  [Scanner2Category.BusinessModel]: [
    '"App Store" OR "Marketplace"', 
    '"Freemium" OR "Usage-based"', 
    '"API Key" OR "SDK"', 
    '"Partner Program" OR "Ecosystem"'
  ],
  [Scanner2Category.AIReadiness]: [
    '"Azure" OR "AWS" OR "Google Cloud"', 
    '"Data Mesh" OR "Data Product Owner"', 
    '"Python" OR "Machine Learning" OR "LLM"', 
    '"Tech Radar" OR "Engineering Blog"'
  ]
};
