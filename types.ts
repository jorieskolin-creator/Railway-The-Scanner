
// Types are stripped by Babel, but we need to ensure the runtime code (assignments) exists.

interface ScannerInputData {
  companyName: string;
  companyUrl: string;
  reportText: string;
  modelId?: string; // Optional Model Override
}

interface EvidenceLink {
  title: string;
  url: string;
  source: string;
}

interface ScannerCategoryScore {
  name: string;
  score: number;
  goodCount: number;
  badCount: number;
}

interface ScannerMetrics {
  friction_index: number;
  vibrancy_score: number;
  total_good_mentions: number;
  total_bad_mentions: number;
}

interface AnalysisResult {
  id: string; 
  companyName: string;
  companyUrl?: string; // Optional field for persistence
  intent_score: number;
  reality_score: number;
  reality_gap: number; 
  evidence_links: EvidenceLink[];
  summary_text: string;
  lastUpdated: string; 
  intent_breakdown: ScannerCategoryScore[];
  metrics?: ScannerMetrics;
  reality_checklist: {
    category: string;
    found: boolean;
    query: string;
  }[];
  is_simulated?: boolean;
}

// Attach Enums to window so they are available globally
// We use a simple object check to prevent overwriting if somehow loaded twice
if (!(window as any).Scanner1Category) {
    (window as any).Scanner1Category = {
      OperatingModel: "Operating Model",
      ServicePortfolio: "Service Portfolio",
      Values: "Values",
      Strategy: "Strategy",
      BusinessModel: "Business Model",
      AIReadiness: "AI Readiness"
    };
}

if (!(window as any).Scanner2Category) {
    (window as any).Scanner2Category = {
      OperatingModel: "Operating Model",
      ServicePortfolio: "Service Portfolio",
      Values: "Values",
      Strategy: "Strategy",
      BusinessModel: "Business Model",
      AIReadiness: "AI Readiness"
    };
}
