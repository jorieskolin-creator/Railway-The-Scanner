import { AnalysisResult, ScannerInputData, ScannerCategoryScore, Scanner1Category, Scanner2Category } from '../types';
import { INTENT_KEYWORDS, REALITY_QUERIES } from '../constants';

/**
 * Simulates Scanner 1: Keyword counting in text
 */
const runScannerOne = (text: string): { score: number, breakdown: ScannerCategoryScore[] } => {
  const breakdown: ScannerCategoryScore[] = [];
  let totalGood = 0;
  let totalBad = 0;

  // Process each category
  (Object.keys(INTENT_KEYWORDS) as Scanner1Category[]).forEach(category => {
    const { good, bad } = INTENT_KEYWORDS[category];
    
    let goodCount = 0;
    let badCount = 0;

    const lowerText = text.toLowerCase();

    good.forEach(word => {
      // Simple occurrence check (in a real app, use regex for exact word boundary)
      const count = (lowerText.match(new RegExp(word.toLowerCase(), "g")) || []).length;
      goodCount += count;
    });

    bad.forEach(word => {
      const count = (lowerText.match(new RegExp(word.toLowerCase(), "g")) || []).length;
      badCount += count;
    });

    // Calculate category score (simple ratio normalized to 0-100)
    // Avoid division by zero. If no words found, neutral 50.
    let catScore = 50;
    const total = goodCount + badCount;
    if (total > 0) {
      catScore = (goodCount / total) * 100;
    }

    breakdown.push({
      name: category,
      score: Math.round(catScore),
      goodCount,
      badCount
    });

    totalGood += goodCount;
    totalBad += badCount;
  });

  // Calculate Global Intent Score
  let globalScore = 50;
  const globalTotal = totalGood + totalBad;
  if (globalTotal > 0) {
    globalScore = (totalGood / globalTotal) * 100;
  }

  return {
    score: Math.round(globalScore),
    breakdown
  };
};

/**
 * Simulates Scanner 2: Google Search X-Ray
 */
const runScannerTwo = async (companyName: string): Promise<{ score: number, checklist: any[], links: any[] }> => {
  const checklist: { category: string; found: boolean; query: string }[] = [];
  const links: { title: string; url: string; source: string }[] = [];
  
  let totalChecks = 0;
  let passedChecks = 0;

  (Object.keys(REALITY_QUERIES) as Scanner2Category[]).forEach(category => {
    const queries = REALITY_QUERIES[category];
    queries.forEach((query, index) => {
      totalChecks++;
      
      const isSimulatedHit = Math.random() > 0.4; 

      checklist.push({
        category,
        query,
        found: isSimulatedHit
      });

      if (isSimulatedHit) {
        passedChecks++;
        links.push({
          title: `${companyName} - ${query} | Careers`,
          url: `https://www.google.com/search?q=${encodeURIComponent(`site:linkedin.com OR site:indeed.com "${companyName}" "${query}"`)}`,
          source: "Simulated Search Result"
        });
      }
    });
  });

  const score = Math.round((passedChecks / totalChecks) * 100);

  return {
    score,
    checklist,
    links
  };
};

export const runMockScanner = async (input: ScannerInputData): Promise<AnalysisResult> => {
  const intentAnalysis = runScannerOne(input.reportText);
  const realityAnalysis = await runScannerTwo(input.companyName);

  return {
    id: Math.random().toString(36).substr(2, 9),
    companyName: input.companyName,
    intent_score: intentAnalysis.score,
    reality_score: realityAnalysis.score,
    reality_gap: intentAnalysis.score - realityAnalysis.score,
    intent_breakdown: intentAnalysis.breakdown,
    reality_checklist: realityAnalysis.checklist,
    evidence_links: realityAnalysis.links,
    summary_text: "Pending AI Synthesis...",
    lastUpdated: new Date().toISOString()
  };
};

/**
 * Generates bulk mock data for the dashboard
 */
export const generateMockDashboardData = (): AnalysisResult[] => {
  const companies = [
    "TechGlobal Inc", "FutureFlow", "Legacy Bank", "OldSchool Mfg", 
    "AgileStream", "DataMinds", "CloudNative Co", "Bureaucracy Ltd",
    "InnovateX", "GreenWash Energy", "TrueBuilders", "HyperScale",
    "SlowCorp", "FastTrack", "Mediocre Systems", "Visionary AI"
  ];

  return companies.map((name, idx) => {
    // Randomize scores to fill quadrants
    const intent = Math.floor(Math.random() * 80) + 10;
    const reality = Math.floor(Math.random() * 80) + 10;
    
    // Bias some data points for realism
    // Greenwasher
    if (name.includes("GreenWash") || name.includes("Future")) {
      return createMockResult(name, 85, 20);
    }
    // Silent Doer
    if (name.includes("True") || name.includes("Builders")) {
      return createMockResult(name, 30, 80);
    }
    // Frontrunner
    if (name.includes("Agile") || name.includes("Native")) {
      return createMockResult(name, 88, 85);
    }
    // Legacy
    if (name.includes("Legacy") || name.includes("Old")) {
      return createMockResult(name, 25, 20);
    }

    return createMockResult(name, intent, reality);
  });
};

const createMockResult = (name: string, intent: number, reality: number): AnalysisResult => {
  const gap = intent - reality;
  return {
    id: Math.random().toString(36).substr(2, 9),
    companyName: name,
    intent_score: intent,
    reality_score: reality,
    reality_gap: gap,
    evidence_links: reality > 40 ? [
      { title: `${name} - Senior Platform Engineer`, url: "#", source: "Careers Page" },
      { title: `${name} Tech Blog - Data Mesh`, url: "#", source: "Medium" }
    ] : [],
    summary_text: `Automated analysis for ${name} indicates a ${gap > 10 ? 'significant' : 'moderate'} gap between stated intent and operational reality.`,
    lastUpdated: new Date(Date.now() - Math.floor(Math.random() * 1000000000)).toISOString(),
    intent_breakdown: [
      { name: "Strategy", score: intent, goodCount: 5, badCount: 2 },
      { name: "Ops Model", score: intent - 5, goodCount: 4, badCount: 3 },
      { name: "Values", score: intent + 5, goodCount: 6, badCount: 1 },
      { name: "AI Readiness", score: Math.max(0, intent - 10), goodCount: 2, badCount: 2 },
      { name: "Service Portfolio", score: intent, goodCount: 5, badCount: 2 },
      { name: "Business Model", score: intent, goodCount: 5, badCount: 2 }
    ],
    reality_checklist: []
  };
};
