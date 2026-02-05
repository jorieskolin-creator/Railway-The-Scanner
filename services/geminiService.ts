
const getGeminiClient = () => {
    // Relies on global window.GoogleGenAI set in index.html
    const GoogleGenAI = (window as any).GoogleGenAI;
    
    // Safety check for process.env
    const apiKey = (window as any).process?.env?.API_KEY || ''; 
    
    if (!apiKey) {
      console.warn("API Key not found in process.env.API_KEY. Gemini features will be disabled.");
      return null;
    }
    return new GoogleGenAI({ apiKey });
  };
  
(window as any).generateSynthesis = async (data, companyName) => {
  const ai = getGeminiClient();
  if (!ai) return "Analysis complete. (AI Summary unavailable: API Key missing)";

  const prompt = `
    You are a Senior Market Analyst.
    Analyze the following data for company: "${companyName}".
    
    Intent Score (Narrative): ${data.intent_score}/100
    Reality Score (Evidence): ${data.reality_score}/100
    Reality Gap: ${data.reality_gap}

    Top Intent Categories:
    ${data.intent_breakdown.map(c => `- ${c.name}: ${c.score}/100`).join('\n')}

    Verified Evidence Found:
    ${data.evidence_links.slice(0, 5).map(l => `- ${l.title}`).join('\n')}

    Task:
    Write a concise, professional executive summary (max 150 words).
    1. Diagnose the company's adaptability.
    2. Highlight if there is "AI Washing" or "Agile Washing" (High Intent, Low Reality).
    3. Highlight if they are "Silent Executors" (Low Intent, High Reality).
    4. Provide one strategic recommendation.
    
    Use a direct, analytical tone.
  `;

  try {
    // UPGRADE: Using gemini-3-flash-preview for latest reasoning capabilities
    const response = await ai.models.generateContent({
      model: 'gemini-3-flash-preview',
      contents: prompt,
    });
    
    return response.text || "Analysis complete. (AI Summary generation returned empty)";
  } catch (error) {
    console.error("Gemini Synthesis Error:", error);
    return "Analysis complete. (AI Summary unavailable due to network or key error)";
  }
};
