
import { GoogleGenAI } from "https://esm.sh/@google/genai@0.1.1";
import { AnalysisResult } from '../types.ts';

const getGeminiClient = () => {
    const apiKey = process.env.API_KEY;
    if (!apiKey) {
      console.error("API Key not found. Please ensure process.env.API_KEY is set.");
      // In a real app, handle this gracefully. For this demo, it might crash if called.
      return null;
    }
    return new GoogleGenAI({ apiKey });
  };
  
export const generateSynthesis = async (data: AnalysisResult, companyName: string): Promise<string> => {
  const ai = getGeminiClient();
  if (!ai) return "Error: API Key missing. Unable to generate summary.";

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
