
export interface ScannerInputData {
  companyName: string;
  companyUrl: string;
  reportText: string; // Text to be analyzed by Scanner 1
}

export interface EvidenceLink {
  title: string;
  url: string;
  source: string;
}

export interface ScannerCategoryScore {
  name: string;
  score: number; // 0-100 (normalized)
  goodCount: number;
  badCount: number;
}

export interface AnalysisResult {
  id: string; // Unique ID for list keys
  companyName: string;
  intent_score: number; // 0-100
  reality_score: number; // 0-100
  reality_gap: number; // Intent - Reality
  evidence_links: EvidenceLink[];
  summary_text: string;
  lastUpdated: string; // ISO Date string
  // Detailed breakdown for visualization
  intent_breakdown: ScannerCategoryScore[];
  reality_checklist: {
    category: string;
    found: boolean;
    query: string;
  }[];
}

export enum Scanner1Category {
  OperatingModel = "Operating Model",
  ServicePortfolio = "Service Portfolio",
  Values = "Values",
  Strategy = "Strategy",
  BusinessModel = "Business Model",
  AIReadiness = "AI Readiness"
}

// Updated to mirror Scanner 1 for "Reality Gap" calculation (Page 14 of PDF)
export enum Scanner2Category {
  OperatingModel = "Operating Model",
  ServicePortfolio = "Service Portfolio",
  Values = "Values",
  Strategy = "Strategy",
  BusinessModel = "Business Model",
  AIReadiness = "AI Readiness"
}
