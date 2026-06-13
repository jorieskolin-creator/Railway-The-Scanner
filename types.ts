
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
  snippet?: string;
  verified?: boolean;
  source_credibility?: string; // "high" | "medium" | "low"
  source_type?: string;
  claim_tier?: string;
  claim_type?: string;
  scope?: string;
  entity_scope?: string;
  scope_warning?: string;
  canonical_company?: string;
  entity_match?: string;
  entity_match_status?: 'exact' | 'scoped' | 'weak' | 'wrong' | 'unknown' | string;
  entity_match_reason?: string;
  content_entity_match_status?: 'exact' | 'scoped' | 'weak' | 'wrong' | 'unknown' | string;
  content_entity_match_reason?: string;
  entity_verified?: boolean;
  entity_name_found?: string;
  canonical_source_host?: string;
  wrong_entity?: boolean;
  origin?: string;
  company_specific?: boolean;
  source_says?: string;
  possible_relevance?: string;
  source_says_was_genericized?: boolean;
  url_verified?: boolean;
  content_verified?: boolean;
  claim_support_status?: 'supported' | 'partial' | 'unsupported' | 'unchecked' | 'unavailable' | string;
  analysis_verified?: boolean;
  analysis_verified_at?: string;
  promotion_status?: 'generation_eligible' | 'display_only' | 'excluded' | string;
  generation_eligible?: boolean;
  content_relevance_score?: number;
  verification_model?: string;
  verification_conflict?: boolean;
  quote_mismatch?: boolean;
  verification_reason?: string;
  content_verification_reason?: string;
  source_quote?: string;
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
  entity_scope?: string;
  scope_warning?: string;
  entity_match_status?: 'exact' | 'scoped' | 'weak' | 'wrong' | 'unknown' | string;
  entity_match_reason?: string;
  content_entity_match_status?: 'exact' | 'scoped' | 'weak' | 'wrong' | 'unknown' | string;
  content_entity_match_reason?: string;
  entity_verified?: boolean;
  entity_name_found?: string;
  canonical_source_host?: string;
  wrong_entity?: boolean;
  url_verified?: boolean;
  content_verified?: boolean;
  claim_support_status?: 'supported' | 'partial' | 'unsupported' | 'unchecked' | 'unavailable' | string;
  analysis_verified?: boolean;
  analysis_verified_at?: string;
  promotion_status?: 'generation_eligible' | 'display_only' | 'excluded' | string;
  generation_eligible?: boolean;
  content_relevance_score?: number;
  verification_model?: string;
  verification_conflict?: boolean;
  quote_mismatch?: boolean;
  verification_reason?: string;
  content_verification_reason?: string;
  source_quote?: string;
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
  unsupported_claims?: string[];
  unsupported_claims_detailed?: any[];
  false_claims?: string[];
  false_claims_detailed?: any[];
  fact_check_skipped?: boolean;
  model?: string;
  time_ms?: number;
  note?: string;
}

interface ScanQuality {
  confidence_score?: number;
  confidence?: number;
  contradictions?: any[];
  contradiction_warnings?: any[];
  quality_notes?: string[];
}

interface EvidenceSufficiency {
  score?: number;
  status?: string;
  label?: string;
  promoted_company_sources?: number;
  promoted_nonfinancial_sources?: number;
  promoted_financial_sources?: number;
  promoted_total_sources?: number;
  [key: string]: any;
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
  excluded_evidence?: EvidenceLink[];
  key_people?: KeyPerson[];
  summary_text: string;
  pitch_text?: string; // NEW: Strategic Pitch
  evidence_based_risks?: string[]; // Neutral diagnostic risks derived from evidence
  why_proposals?: string[]; // Desired Impact (WHY) — 2 proposals
  what_proposals?: string[]; // Desired Effect (WHAT) — 2 proposals
  lastUpdated: string;
  intent_breakdown: ScannerCategoryScore[];
  metrics?: ScannerMetrics;
  reality_checklist: {
    category: string;
    found: boolean;
    query: string;
    source_type?: string;
    sentiment?: string;
    is_recent?: boolean;
  }[];
  is_simulated?: boolean;
  ai_verification?: AIVerification; // AI-powered claim verification
  ai_verified?: boolean; // Summary field for landing page badge
  hallucination_flags?: string[];
  quality_notes?: string[];
  hallucination_risk?: string;
  hallucination_score?: number;
  scan_quality?: ScanQuality;
  evidence_sufficiency?: EvidenceSufficiency | number;
  low_evidence_mode?: boolean;
  low_evidence_label?: string;
  ai_readiness_evidence_note?: string;
  dimension_evidence_notes?: {
    ai_readiness?: {
      dimension?: string;
      intent_score?: number | null;
      promoted_evidence_count?: number;
      status?: string;
      warning?: string;
    };
    [key: string]: any;
  };
  contradictions?: any[];
  contradiction_warnings?: any[];
  general_summary_false_claims?: string[];
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

interface FAVIGapDetail {
  dimension: string;
  foresight_pressure: number;
  reality_score: number | null;
  gap: number;
}

interface FAVICompany {
  name: string;
  domain: string;
  favi_score: number;
  fps: number;                    // Foresight Pressure Score (0-100)
  readiness_gap: number;          // Readiness Gap (0-100)
  fac: number;                    // Financial Adaptation Capacity (0-100)
  megatrend_exposure: number;     // Megatrend Exposure (0-100)
  current_vulnerability: number;  // Current vulnerability score
  intent_readiness_gap?: number;  // Raw intent-dimension readiness gap before current-risk adjustment
  current_vulnerability_signal?: number; // Current-risk floor used to prevent false zero FAVI
  readiness_gap_source?: string;  // "intent_dimensions" | "current_vulnerability_adjusted"
  segment: string;                // Legacy Current x FAVI segment; UI may derive FAC x FAVI segment
  formula: string;                // "geometric" | "additive"
  foresight_count: number;
  dimensions_covered: number;
  gap_details: FAVIGapDetail[];
}

interface FAVISegments {
  Critical: number;
  "Wounded but Sheltered": number;
  Sleepwalking: number;
  "Safe Harbor": number;
}

interface FAVIDomainPressure {
  domain: string;
  avg_pressure: number;
  max_pressure: number;
  company_count: number;
}

interface FAVICutoffs {
  favi: number;
  current_vulnerability: number;
  source: string;
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
  favi_companies: FAVICompany[];
  favi_segments: FAVISegments;
  favi_domain_pressure: FAVIDomainPressure[];
  favi_cutoffs?: FAVICutoffs;
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
