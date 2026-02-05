
export interface ScannerInputData {
  companyName: string;
  companyUrl: string;
  reportText: string; 
}

export interface EvidenceLink {
  title: string;
  url: string;
  source: string;
}

export interface ScannerCategoryScore {
  name: string;
  score: number;
  goodCount: number;
  badCount: number;
}

export interface AnalysisResult {
  id: string; 
  companyName: string;
  intent_score: number;
  reality_score: number;
  reality_gap: number; 
  evidence_links: EvidenceLink[];
  summary_text: string;
  lastUpdated: string; 
  intent_breakdown: ScannerCategoryScore[];
  reality_checklist: {
    category: string;
    found: boolean;
    query: string;
  }[];
  is_simulated?: boolean;
}

export enum Scanner1Category {
  OperatingModel = "Operating Model",
  ServicePortfolio = "Service Portfolio",
  Values = "Values",
  Strategy = "Strategy",
  BusinessModel = "Business Model",
  AIReadiness = "AI Readiness"
}

export enum Scanner2Category {
  OperatingModel = "Operating Model",
  ServicePortfolio = "Service Portfolio",
  Values = "Values",
  Strategy = "Strategy",
  BusinessModel = "Business Model",
  AIReadiness = "AI Readiness"
}
