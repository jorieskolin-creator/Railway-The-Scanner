import { Scanner1Category, Scanner2Category } from './types';

// SCANNER 1: Narrative/Intent Keywords
export const INTENT_KEYWORDS: Record<Scanner1Category, { good: string[], bad: string[] }> = {
  [Scanner1Category.OperatingModel]: {
    good: ["Service Areas", "Value Streams", "Stream-aligned", "Distributed Authority", "Sense and Respond"],
    bad: ["Functional Silos", "Command and Control", "Digital Taylorism", "Handovers", "Approval Chain"]
  },
  [Scanner1Category.ServicePortfolio]: {
    good: ["SaaS", "Recurring Revenue", "API-First", "Platform", "Ecosystem"],
    bad: ["Monolith", "On-premise", "Project-based", "One-off Sales", "Vendor Lock-in"]
  },
  [Scanner1Category.Values]: {
    good: ["Trust", "Transparency", "Psychological Safety", "Curiosity", "Courage"],
    bad: ["Secrecy", "Fear", "Blame", "Control", "Compliance"]
  },
  [Scanner1Category.Strategy]: {
    good: ["North Star", "Adaptive", "Value Creation", "Digital Transformation", "Growth"],
    bad: ["Cost-cutting", "Defensive", "Short-term", "Static", "Reactive"]
  },
  [Scanner1Category.BusinessModel]: {
    good: ["Platform", "Ecosystem", "Recurring Revenue", "Network Effects", "Freemium"],
    bad: ["Pipeline", "One-off Sales", "Middlemen", "Rent-seeking", "Asset-heavy"]
  },
  [Scanner1Category.AIReadiness]: {
    good: ["Cloud", "Data Lake", "Machine Learning", "API", "Microservices"],
    bad: ["Legacy", "On-premise", "Siloed Data", "Manual Process", "Spreadsheet"]
  }
};

// SCANNER 2: Evidence/Reality Queries
export const REALITY_QUERIES: Record<Scanner2Category, string[]> = {
  [Scanner2Category.AdaptiveOperatingModel]: [
    "Stream-aligned teams", "Micro-enterprises", "Platform Engineering", "Distributed Authority"
  ],
  [Scanner2Category.StrategyFinance]: [
    "Rolling Forecast", "Beyond Budgeting", "Dynamic Allocation", "Relative Targets"
  ],
  [Scanner2Category.HumanCentricity]: [
    "Psychological Safety", "Blameless Post-Mortem", "Salary Transparency", "Self-Management"
  ],
  [Scanner2Category.AIDataReadiness]: [
    "Data Mesh", "Data Product Owner", "Automated Governance", "MLOps"
  ]
};
