
import { AnalysisResult, ScannerInputData, ScannerCategoryScore, Scanner1Category, Scanner2Category } from '../types';
import { INTENT_KEYWORDS, REALITY_QUERIES } from '../constants';

/**
 * Simulates Scanner 1: Keyword counting in text
 */
const runScannerOne = (text: string): { score: number, breakdown: ScannerCategoryScore[] } => {
  const breakdown: ScannerCategoryScore[] = [];
  
  // PDF Thresholds
  const GOOD_THRESHOLD = 3;
  const BAD_THRESHOLD = 1;

  // Process each category
  (Object.keys(INTENT_KEYWORDS) as Scanner1Category[]).forEach(category => {
    const { good, bad } = INTENT_KEYWORDS[category];
    
    let uniqueGoodFound = 0;
    let uniqueBadFound = 0;

    const lowerText = text.toLowerCase();

    good.forEach(word => {
      // Escape special characters for regex
      const escapedWord = word.replace(/[.*+?^${}()|[\]\\]/g, '\\$&'); 
      const count = (lowerText.match(new RegExp(`\\b${escapedWord.toLowerCase()}\\b`, "g")) || []).length;
      if (count >= GOOD_THRESHOLD) {
        uniqueGoodFound++;
      }
    });

    bad.forEach(word => {
      const escapedWord = word.replace(/[.*+?^${}()|[\]\\]/g, '\\$&');
      const count = (lowerText.match(new RegExp(`\\b${escapedWord.toLowerCase()}\\b`, "g")) || []).length;
      if (count >= BAD_THRESHOLD) {
        uniqueBadFound++;
      }
    });

    // Net Modernization Score Logic: (Good Matches - Bad Matches) / 25
    const netResult = uniqueGoodFound - uniqueBadFound;
    let catScore = 0;
    
    if (netResult > 0) {
        catScore = (netResult / 25) * 100;
        if (catScore > 100) catScore = 100;
    }

    breakdown.push({
      name: category,
      score: Math.round(catScore),
      goodCount: uniqueGoodFound,
      badCount: uniqueBadFound
    });
  });

  // Global score is average of category scores
  const totalScore = breakdown.reduce((acc, curr) => acc + curr.score, 0);
  const globalScore = totalScore / breakdown.length;

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
          title: `${companyName} - ${query} | Careers Finland`,
          url: `https://www.google.com/search?q=${encodeURIComponent(`site:linkedin.com "${companyName}" "${query}" Finland`)}`,
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
 * FINLAND TOP 100 DATASET (Turnover based)
 */
const FINNISH_COMPANIES = [
  "Nokia Oyj", "Neste Oyj", "Kone Oyj", "UPM-Kymmene Oyj", "Stora Enso Oyj",
  "Kesko Oyj", "Sampo Oyj", "Fortum Oyj", "Metso Oyj", "Wärtsilä Oyj",
  "Valmet Oyj", "Huhtamäki Oyj", "Orion Oyj", "Elisa Oyj", "Kemira Oyj",
  "Cargotec Oyj", "Tietoevry Oyj", "YIT Oyj", "Outokumpu Oyj", "Sanoma Oyj",
  "Finnair Oyj", "Kojamo Oyj", "Tokmanni Group", "Caverion Oyj", "Rovio Entertainment",
  "Fiskars Group", "Vaisala Oyj", "Olvi Oyj", "Ponsse Oyj", "Revenio Group"
];

export const generateMockDashboardData = (): AnalysisResult[] => {
  return FINNISH_COMPANIES.map((name, idx) => {
    // Randomize scores to fill quadrants based on company archetype stereotypes (Simulated)
    let intent = Math.floor(Math.random() * 60) + 20; 
    let reality = Math.floor(Math.random() * 60) + 20;
    
    // Tech/Modern Bias (Nokia, Elisa, Tietoevry, Rovio)
    if (["Nokia Oyj", "Elisa Oyj", "Tietoevry Oyj", "Rovio Entertainment", "Vaisala Oyj"].includes(name)) {
        intent = Math.floor(Math.random() * 30) + 60; // 60-90
        reality = Math.floor(Math.random() * 30) + 60; // 60-90
    }

    // Heavy Industry Bias (Stora Enso, UPM, Outokumpu) - Often High Intent (Sustainability), Lower Agility Reality
    if (["Stora Enso Oyj", "UPM-Kymmene Oyj", "Outokumpu Oyj", "Neste Oyj"].includes(name)) {
        intent = Math.floor(Math.random() * 20) + 70; // High Intent
        reality = Math.floor(Math.random() * 40) + 30; // Mixed Reality
    }
    
    // Traditional/Retail Bias (Kesko, Tokmanni)
    if (["Kesko Oyj", "Tokmanni Group", "Olvi Oyj"].includes(name)) {
        intent = Math.floor(Math.random() * 40) + 30;
        reality = Math.floor(Math.random() * 40) + 30;
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
      { title: `${name} - Senior Platform Engineer`, url: "#", source: "Careers Finland" },
      { title: `${name} Annual Report 2023`, url: "#", source: "Investor Relations" }
    ] : [],
    summary_text: `Automated analysis for ${name} indicates a ${gap > 10 ? 'significant' : 'moderate'} gap between stated intent and operational reality within the Finnish market context.`,
    lastUpdated: new Date(Date.now() - Math.floor(Math.random() * 1000000000)).toISOString(),
    intent_breakdown: [
      { name: "Strategy", score: intent, goodCount: 5, badCount: 2 },
      { name: "Ops Model", score: Math.max(0, intent - 10), goodCount: 4, badCount: 3 },
      { name: "Values", score: intent + 5, goodCount: 6, badCount: 1 },
      { name: "AI Readiness", score: Math.max(0, intent - 15), goodCount: 2, badCount: 2 },
      { name: "Service Portfolio", score: intent, goodCount: 5, badCount: 2 },
      { name: "Business Model", score: intent, goodCount: 5, badCount: 2 }
    ],
    reality_checklist: []
  };
};
