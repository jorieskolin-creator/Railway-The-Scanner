
// Dependencies from window
const { INTENT_KEYWORDS, REALITY_QUERIES } = (window as any);

const runScannerOne = (text) => {
  const breakdown = [];
  const GOOD_THRESHOLD = 3;
  const BAD_THRESHOLD = 1;

  Object.keys(INTENT_KEYWORDS).forEach(category => {
    const { good, bad } = INTENT_KEYWORDS[category];
    let uniqueGoodFound = 0;
    let uniqueBadFound = 0;
    const lowerText = text.toLowerCase();

    good.forEach((word) => {
      const escapedWord = word.replace(/[.*+?^${}()|[\]\\]/g, '\\$&'); 
      const count = (lowerText.match(new RegExp(`\\b${escapedWord.toLowerCase()}\\b`, "g")) || []).length;
      if (count >= GOOD_THRESHOLD) uniqueGoodFound++;
    });

    bad.forEach((word) => {
      const escapedWord = word.replace(/[.*+?^${}()|[\]\\]/g, '\\$&');
      const count = (lowerText.match(new RegExp(`\\b${escapedWord.toLowerCase()}\\b`, "g")) || []).length;
      if (count >= BAD_THRESHOLD) uniqueBadFound++;
    });

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

  const totalScore = breakdown.reduce((acc, curr) => acc + curr.score, 0);
  const globalScore = totalScore / breakdown.length;

  return {
    score: Math.round(globalScore),
    breakdown
  };
};

const runScannerTwo = async (companyName) => {
  const checklist = [];
  const links = [];
  
  let scoreAccumulator = 50; // Start Neutral
  
  Object.keys(REALITY_QUERIES).forEach(category => {
    const queries = REALITY_QUERIES[category];
    
    // Safety check if REALITY_QUERIES is structured differently
    if (!Array.isArray(queries)) return;

    queries.forEach((item) => {
      const { label, sentiment, q } = item;
      
      // Simulate Hit Chance
      const isSimulatedHit = Math.random() > 0.5; 

      checklist.push({
        category,
        query: label, // Use the human readable label
        found: isSimulatedHit,
        sentiment: sentiment
      });

      if (isSimulatedHit) {
        if (sentiment === 'positive') scoreAccumulator += 7;
        if (sentiment === 'negative') scoreAccumulator -= 7;

        links.push({
          title: `[${sentiment.toUpperCase()}] ${label}: Evidence Found`,
          url: `https://www.google.com/search?q=${encodeURIComponent(`site:linkedin.com "${companyName}" ${q}`)}`,
          source: "Simulated X-Ray"
        });
      }
    });
  });

  const finalScore = Math.max(0, Math.min(100, scoreAccumulator));

  return {
    score: finalScore,
    checklist,
    links
  };
};

(window as any).runMockScanner = async (input) => {
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
    lastUpdated: new Date().toISOString(),
    is_simulated: true
  };
};

const FINNISH_COMPANIES = [
  "Nokia Oyj", "Neste Oyj", "Kone Oyj", "UPM-Kymmene Oyj", "Stora Enso Oyj",
  "Kesko Oyj", "Sampo Oyj", "Fortum Oyj", "Metso Oyj", "Wärtsilä Oyj",
  "Valmet Oyj", "Huhtamäki Oyj", "Orion Oyj", "Elisa Oyj", "Kemira Oyj",
  "Cargotec Oyj", "Tietoevry Oyj", "YIT Oyj", "Outokumpu Oyj", "Sanoma Oyj",
  "Finnair Oyj", "Kojamo Oyj", "Tokmanni Group", "Caverion Oyj", "Rovio Entertainment",
  "Fiskars Group", "Vaisala Oyj", "Olvi Oyj", "Ponsse Oyj", "Revenio Group"
];

const createMockResult = (name, intent, reality) => {
  const gap = intent - reality;
  return {
    id: Math.random().toString(36).substr(2, 9),
    companyName: name,
    intent_score: intent,
    reality_score: reality,
    reality_gap: gap,
    evidence_links: reality > 40 ? [
      { title: `[POSITIVE] Platform Engineering Test: Jobs Found`, url: "#", source: "Simulated X-Ray" },
      { title: `[POSITIVE] Rolling Forecast Test: Annual Report`, url: "#", source: "Simulated X-Ray" }
    ] : [
       { title: `[NEGATIVE] Budget Rigidity Test: Variance Analysis Found`, url: "#", source: "Simulated X-Ray" } 
    ],
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
    reality_checklist: [
        { category: "Operating Model", query: "Micro-Enterprise Test", found: reality > 50, sentiment: 'positive' },
        { category: "Values", query: "Digital Taylorism Test", found: reality < 40, sentiment: 'negative' },
        { category: "Strategy", query: "Rolling Forecast Test", found: reality > 60, sentiment: 'positive' }
    ],
    is_simulated: true
  };
};

(window as any).generateMockDashboardData = () => {
  return FINNISH_COMPANIES.map((name, idx) => {
    let intent = Math.floor(Math.random() * 60) + 20; 
    let reality = Math.floor(Math.random() * 60) + 20;
    
    if (["Nokia Oyj", "Elisa Oyj", "Tietoevry Oyj", "Rovio Entertainment", "Vaisala Oyj"].includes(name)) {
        intent = Math.floor(Math.random() * 30) + 60;
        reality = Math.floor(Math.random() * 30) + 60;
    }

    if (["Stora Enso Oyj", "UPM-Kymmene Oyj", "Outokumpu Oyj", "Neste Oyj"].includes(name)) {
        intent = Math.floor(Math.random() * 20) + 70;
        reality = Math.floor(Math.random() * 40) + 30;
    }
    
    if (["Kesko Oyj", "Tokmanni Group", "Olvi Oyj"].includes(name)) {
        intent = Math.floor(Math.random() * 40) + 30;
        reality = Math.floor(Math.random() * 40) + 30;
    }

    return createMockResult(name, intent, reality);
  });
};
