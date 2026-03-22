
// Types are stripped by Babel, but we need to ensure the runtime code (assignments) exists.

interface ScannerInputData {
  companyName: string;
  companyUrl: string;
  reportText: string;
}

interface EvidenceLink {
  title: string;
  url: string;
  source: string;
  verified?: boolean;
  source_credibility?: string; // "high" | "medium" | "low"
}

interface ForesightLink {
  title: string;
  url: string;
  snippet: string;
  source_type: string;
  verified: boolean;
  source_credibility: string;
  validated: boolean;
  kb_dimension: string;
  fp_theme: string;
  timeframe: string;
  force_type: string;
  origin: string; // always "foresight"
}

interface LinkedInInsights {
  found: boolean;
  headline?: string | null;
  verified_role?: string | null;
  still_at_company?: boolean | null;
  summary: string;
  career_moves?: string[];
  topics?: string[];
  notable_posts?: string[];
  model_used?: string | null;
}

interface KeyPerson {
  name: string;
  role: string;
  relevance: string;
  linkedin_insights?: LinkedInInsights;
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

interface AIVerification {
  verified: boolean;
  unsupported_claims: string[];
  model: string;
  time_ms: number;
}

interface AnalysisResult {
  id: string;
  companyName: string;
  companyUrl?: string; // Optional field for persistence
  intent_score: number;
  reality_score: number;
  reality_gap: number;
  confidence_score?: number;
  washing_flag?: boolean; // NEW: Washing Flag
  washing_type?: string | null; // NEW: AI Washing, Agile Washing, etc.
  evidence_links: EvidenceLink[];
  foresight_links?: ForesightLink[]; // Validated foresight source links
  key_people?: KeyPerson[];
  summary_text: string;
  pitch_text?: string; // NEW: Strategic Pitch
  why_proposals?: string[]; // Desired Impact (WHY) — 2 proposals
  what_proposals?: string[]; // Desired Effect (WHAT) — 2 proposals
  lastUpdated: string;
  intent_breakdown: ScannerCategoryScore[];
  metrics?: ScannerMetrics;
  reality_checklist: {
    category: string;
    found: boolean;
    query: string;
  }[];
  is_simulated?: boolean;
  ai_verification?: AIVerification; // AI-powered claim verification
  ai_verified?: boolean; // Summary field for landing page badge
}

// Market Intelligence Data (from /api/market-intelligence endpoint)
interface MarketIntelForesight {
  theme: string;
  category: string;
  count: number;
  pct: number;
  example_companies: string[];
}

interface MarketIntelCause {
  cluster_label: string;
  count: number;
  why_count: number;
  what_count: number;
  examples: { text: string; company: string }[];
}

interface MarketIntelGapPattern {
  dimension: string;
  avg_intent: number;
  avg_reality: number;
  avg_gap: number;
  company_count: number;
}

interface MarketIntelArchetypeFlow {
  domain: string;
  thriving: number;
  modernizing: number;
  cash_cow: number;
  at_risk: number;
  total: number;
}

interface MarketIntelCrossImpact {
  theme: string;
  dimension: string;
  count: number;
}

interface MarketIntelRadar {
  domain: string;
  raw_scores: Record<string, number>;
  normalized_scores: Record<string, number>;
  total_signals: number;
}

interface MarketIntelHealth {
  domain: string;
  score: number;
  avg_reality: number;
  avg_financial: number;
  avg_gap: number;
  thriving_pct: number;
  company_count: number;
}

interface MarketIntelCoverage {
  covered: number;
  total: number;
  pct: number;
  themes_detail: { theme: string; category: string; signal_count: number }[];
}

interface MarketIntelVulnerable {
  name: string;
  domain: string;
  vulnerability_score: number;
  residual: number;
  gap: number;
  archetype: string;
  reality_score: number;
  financial_index: number | null;
  reasons: string[];
}

interface MarketIntelDemandIntensity {
  dimension: string;
  avg_gap: number;
  max_gap: number;
  companies_with_gap: number;
  total_companies: number;
}

interface MarketIntelligenceData {
  company_count: number;
  detailed_count: number;
  top_foresights: MarketIntelForesight[];
  top_causes: MarketIntelCause[];
  gap_patterns: MarketIntelGapPattern[];
  archetype_flow: MarketIntelArchetypeFlow[];
  cross_impact: MarketIntelCrossImpact[];
  industry_radar: MarketIntelRadar[];
  market_health: MarketIntelHealth[];
  foresight_coverage: MarketIntelCoverage;
  vulnerable_companies: MarketIntelVulnerable[];
  demand_intensity: MarketIntelDemandIntensity[];
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
