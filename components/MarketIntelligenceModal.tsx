
const { useState, useMemo } = (window as any).React;

const LucideRaw = (window as any).Lucide || {};
const MIIcons = new Proxy(LucideRaw, {
  get: (target, prop) => {
    if (prop in target) return target[prop];
    return () => null;
  }
});
const { X, Loader2, TrendingUp, Target, Crosshair, ShieldAlert, Download, ChevronRight, Activity, BarChart3, Gauge, Radar, RefreshCw, Eye, AlertTriangle, BookOpen, Globe, FileText } = MIIcons;

const {
  BarChart, Bar, XAxis, YAxis, CartesianGrid, Tooltip, Legend, ResponsiveContainer,
  RadarChart, Radar: RechartsRadar, PolarGrid, PolarAngleAxis, PolarRadiusAxis,
  ScatterChart, Scatter, Cell, ComposedChart, Line, ZAxis, ReferenceLine,
} = (window as any).Recharts || {};

// ═══════════════════════════════════════════════════════════════════════════════
// COLORS
// ═══════════════════════════════════════════════════════════════════════════════
const CATEGORY_COLORS: Record<string, string> = {
  "Technology & Digital": "#06b6d4",
  "Economy & Business": "#8b5cf6",
  "Industry & Infrastructure": "#f59e0b",
  "Environment & Resources": "#10b981",
  "Society & Governance": "#ec4899",
  "People & Well-being": "#f97316",
  "Science & Demographics": "#6366f1",
};

const ARCHETYPE_COLORS = {
  thriving: "#10b981",
  modernizing: "#f59e0b",
  cash_cow: "#3b82f6",
  at_risk: "#f43f5e",
};

const HEALTH_COLOR = (score: number) => score >= 70 ? "#10b981" : score >= 40 ? "#f59e0b" : "#f43f5e";

const RADAR_COLORS = ["#06b6d4", "#8b5cf6", "#f59e0b", "#10b981", "#ec4899", "#f97316", "#6366f1", "#f43f5e"];

// Descriptions for KB Dimensions (used in Cross-Impact Matrix tooltips)
const DIMENSION_DESCRIPTIONS: Record<string, string> = {
  "Operating Model": "Evaluates how the company is structured and run: team autonomy vs. command-and-control, cross-functional collaboration, decision-making authority, flow efficiency, psychological safety, and whether the organization uses modern approaches (stream-aligned teams, platform teams) or legacy patterns (functional silos, waterfall, micromanagement).",
  "Service Portfolio": "Evaluates what the company offers and how it delivers value: SaaS vs. on-premise, recurring revenue vs. one-off sales, API-first and cloud-native architecture, product modularity, scalability, self-service capabilities, and whether offerings are modern and composable or legacy, monolithic and maintenance-heavy.",
  "Values": "Evaluates the organizational culture and guiding principles: trust, transparency, psychological safety, inclusion, learning orientation, growth mindset, purpose-driven leadership — versus fear-based culture, blame, secrecy, rigid hierarchy, risk-aversion, and short-termism.",
  "Strategy": "Evaluates the company's strategic direction and adaptability: clarity of vision, customer-centricity, data-driven decision-making, innovation focus, resilience planning, ecosystem thinking — versus cost-cutting focus, reactive posture, siloed planning, budget-driven priorities, and status quo orientation.",
  "AI Readiness": "Evaluates technological maturity and preparedness for AI adoption: cloud infrastructure, data governance, ML/AI capabilities, DevOps practices, automation, cybersecurity posture, modern tech stack — versus legacy systems, siloed data, manual processes, technical debt, and fragmented IT infrastructure.",
};

// Descriptions for Foresight Themes (Future Demand Pareto)
const FORESIGHT_THEME_DESCRIPTIONS: Record<string, string> = {
  "Artificial Intelligence": "Generative AI, machine learning, autonomous systems, AI governance, human-AI collaboration",
  "Data & Digitalisation": "Data governance, digital transformation, datafication, open data ecosystems, privacy and data rights",
  "Communication & Media": "Media transformation, platform governance, disinformation, social media dynamics, trust in information",
  "Business": "Business model transformation, corporate strategy, entrepreneurship, organizational resilience, platform disruption",
  "Finance & Ownership": "Banking transformation, fintech, financial inclusion, wealth distribution, alternative ownership models",
  "Global Economy": "Global trade, macroeconomics, supply chain transformation, geoeconomics, deglobalisation",
  "Digital & Sharing Economies": "Platform economy, gig labour rights, peer-to-peer models, collaborative digital economies",
  "Services": "Service economy transformation, AI-augmented services, public service innovation, service design futures",
  "Industry & Manufacturing": "Smart manufacturing, industrial automation, industrial decarbonisation, reshoring, green industrial policy",
  "Construction & Urbanisation": "Urban futures, climate-resilient cities, housing systems, smart urban infrastructure",
  "Energy": "Renewable energy transition, energy grids and storage, hydrogen economy, energy security, just transition",
  "Freight & Logistics": "Supply chain resilience, freight decarbonisation, autonomous logistics, nearshoring",
  "Private & Public Transport": "Electric vehicles, autonomous mobility, Mobility-as-a-Service, public transit futures",
  "Climate Change": "Climate mitigation and adaptation, carbon markets, tipping points, climate finance, geoengineering",
  "Sustainability & Recycling": "Circular economy, sustainable production, regenerative systems, ESG transformation",
  "Nature": "Biodiversity loss and recovery, ecosystem services, natural capital, nature-based solutions",
  "Food": "Food systems transformation, agricultural innovation, food security, alternative proteins, agroecology",
  "Water & Oceans": "Water security, ocean governance, marine economy, water-food-energy nexus",
  "Public Governance": "Democratic governance futures, public-sector innovation, digital governance, regulatory transformation",
  "Geopolitics": "Great power competition, multipolar world order, economic statecraft, tech nationalism",
  "Security & Safety": "Cybersecurity, hybrid warfare, critical infrastructure resilience, defence technology, biosecurity",
  "Values & Ethics": "Digital and AI ethics, bioethics, intergenerational justice, social trust, value pluralism",
  "Society": "Social inequality, polarisation, social capital, civil society, community futures",
  "Health & Wellbeing": "Healthcare transformation, mental health, biotech, precision medicine, pandemic preparedness",
  "Education & Knowledge": "Education futures, lifelong learning, AI in education, skills transformation",
  "Work & Income": "Future of work, labour market transformation, automation, income security models",
  "Management & HR": "Leadership futures, organisational design, talent management, hybrid work, DEI strategy",
  "Leisure & Lifestyles": "Consumer culture transformation, experience economy, wellness economy, sustainable tourism",
  "Sciences & Research": "Frontier sciences, convergent technologies, open science, quantum computing, synthetic biology",
  "Population & Demographics": "Population dynamics, ageing societies, migration, demographic dividends",
};

// Descriptions for Root Cause clusters (Current Demand Pareto)
const ROOT_CAUSE_DESCRIPTIONS: Record<string, string> = {
  "Digital Transformation": "Digital transformation, automation, and digitalization initiatives across business operations",
  "AI & Data Strategy": "Artificial intelligence, machine learning, data-driven platforms and data strategy development",
  "Platform & API Modernization": "Platform architecture, API-first approaches, SaaS migration, cloud and microservices adoption",
  "Customer-Centric Innovation": "Customer experience, user experience design, client-centric personalization and service innovation",
  "Operational Excellence": "Operating model optimization, operational efficiency, lean management, agile and DevOps practices",
  "Sustainability & ESG": "Sustainability strategy, ESG compliance, climate action, carbon reduction and circular economy",
  "Talent & Culture": "Talent acquisition, organizational culture, employee experience, workforce development and HR transformation",
  "Business Model Innovation": "Business model redesign, revenue model innovation, monetization strategies and recurring revenue",
  "Strategic Positioning": "Competitive strategy, market positioning, differentiation, strategic growth planning",
  "Supply Chain & Operations": "Supply chain management, logistics optimization, procurement and manufacturing operations",
};

// Short labels for radar axes
const CATEGORY_SHORT: Record<string, string> = {
  "Technology & Digital": "Tech",
  "Economy & Business": "Economy",
  "Industry & Infrastructure": "Industry",
  "Environment & Resources": "Environment",
  "Society & Governance": "Society",
  "People & Well-being": "People",
  "Science & Demographics": "Science",
};

// FAVI segment configuration (used in Executive Summary + FAVI tab)
const SEGMENT_CONFIG: Record<string, { color: string; bg: string; border: string; description: string }> = {
  "Critical": { color: "text-rose-300", bg: "bg-rose-500/15", border: "border-rose-500/30", description: "Struggling now AND blind to what's coming" },
  "Sleepwalking": { color: "text-amber-300", bg: "bg-amber-500/15", border: "border-amber-500/30", description: "Looks fine today but forces are building" },
  "Wounded but Sheltered": { color: "text-blue-300", bg: "bg-blue-500/15", border: "border-blue-500/30", description: "Problems are real but future isn't adding pressure" },
  "Safe Harbor": { color: "text-emerald-300", bg: "bg-emerald-500/15", border: "border-emerald-500/30", description: "Healthy and future-ready" },
};

const FAVI_COLOR = (score: number) => {
  if (score >= 60) return '#f43f5e';
  if (score >= 40) return '#f59e0b';
  if (score >= 20) return '#6366f1';
  return '#10b981';
};

// ═══════════════════════════════════════════════════════════════════════════════
// HTML EXPORT UTILITIES
// ═══════════════════════════════════════════════════════════════════════════════

const MI_HTML_CSS = `
:root { --bg: #0f172a; --surface: #1e293b; --border: #334155; --text: #e2e8f0; --text-muted: #94a3b8; --text-dim: #64748b; --cyan: #22d3ee; --violet: #a78bfa; --amber: #fbbf24; --emerald: #34d399; --rose: #fb7185; }
* { margin: 0; padding: 0; box-sizing: border-box; }
body { background: var(--bg); color: var(--text); font-family: 'Inter', -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif; line-height: 1.7; max-width: 960px; margin: 0 auto; padding: 40px 24px; }
h1 { font-size: 24px; margin-bottom: 4px; color: white; }
h2 { font-size: 16px; margin: 28px 0 12px; color: var(--cyan); text-transform: uppercase; letter-spacing: 0.05em; border-bottom: 1px solid var(--border); padding-bottom: 6px; }
h3 { font-size: 14px; margin: 20px 0 8px; color: white; }
.subtitle { color: var(--text-dim); font-size: 12px; font-family: monospace; margin-bottom: 32px; }
.section { background: var(--surface); border: 1px solid var(--border); border-radius: 12px; margin-bottom: 24px; padding: 24px 28px; overflow: hidden; }
.section h2 { margin-top: 0; }
table { width: 100%; border-collapse: collapse; font-size: 12px; }
th { text-align: left; padding: 8px 10px; color: var(--text-muted); font-weight: 500; border-bottom: 1px solid var(--border); }
td { padding: 6px 10px; border-bottom: 1px solid rgba(51,65,85,0.3); color: var(--text); }
table.matrix th { text-align: center; font-size: 11px; }
table.matrix td { font-family: monospace; font-size: 11px; color: var(--text-muted); text-align: center; }
.badge { display: inline-block; padding: 3px 10px; border-radius: 12px; font-size: 10px; font-family: monospace; background: var(--bg); border: 1px solid var(--border); color: var(--text-muted); margin: 2px; }
.badge-critical { color: var(--rose); border-color: rgba(251,113,133,0.3); }
.badge-sleepwalking { color: var(--amber); border-color: rgba(251,191,36,0.3); }
.badge-wounded { color: #60a5fa; border-color: rgba(96,165,250,0.3); }
.badge-safe { color: var(--emerald); border-color: rgba(52,211,153,0.3); }
.segment-grid { display: grid; grid-template-columns: 1fr 1fr; gap: 12px; margin: 16px 0; }
.segment-card { border-radius: 8px; padding: 16px; }
.segment-card h4 { font-size: 13px; margin-bottom: 4px; }
.segment-card .count { font-size: 28px; font-weight: bold; }
.segment-card .desc { font-size: 11px; color: var(--text-muted); margin-top: 6px; }
.bar-row { display: flex; align-items: center; gap: 8px; margin: 4px 0; }
.bar-label { font-size: 11px; color: var(--text-muted); min-width: 110px; text-align: right; }
.bar-track { flex: 1; height: 14px; background: var(--bg); border-radius: 4px; overflow: hidden; position: relative; }
.bar-fill { height: 100%; border-radius: 4px; }
.bar-val { font-size: 10px; font-family: monospace; color: var(--text-muted); min-width: 35px; }
.footer { text-align: center; color: var(--text-dim); font-size: 11px; font-family: monospace; margin-top: 48px; padding-top: 24px; border-top: 1px solid var(--border); }
@media print {
  body { background: white; color: #1a1a1a; max-width: 100%; padding: 20px; }
  .section { border-color: #ddd; background: #fafafa; }
  h2 { color: #1a1a1a; border-bottom-color: #ddd; }
  td, th { color: #333; }
  .badge { background: #f5f5f5; border-color: #ccc; color: #555; }
  .footer { color: #999; border-color: #ddd; }
}`;

const downloadHtml = (html: string, filename: string) => {
  const blob = new Blob([html], { type: 'text/html;charset=utf-8' });
  const url = URL.createObjectURL(blob);
  const a = document.createElement('a');
  a.href = url;
  a.download = filename;
  document.body.appendChild(a);
  a.click();
  document.body.removeChild(a);
  URL.revokeObjectURL(url);
};

// Adaptability x Future Vulnerability scatter for the static HTML export.
// Mirrors the in-app Recharts version: dots colored by segment, dashed
// reference lines at the calibrated FAVI cutoff and the FAC median, four
// quadrant labels, and a small caption listing what each quadrant means.
const buildSvgAdaptabilityScatter = (
  faviCompanies: any[],
  faviCutoff: number,
  facCutoff: number,
): string => {
  if (!faviCompanies?.length) return '';
  const chartW = 720, chartH = 360, mL = 56, mR = 36, mT = 28, mB = 50;
  const plotW = chartW - mL - mR, plotH = chartH - mT - mB;
  const xToPx = (x: number) => mL + (Math.max(0, Math.min(100, x)) / 100) * plotW;
  const yToPx = (y: number) => mT + plotH - (Math.max(0, Math.min(100, y)) / 100) * plotH;
  const segColor = (seg: string) =>
    seg === 'Critical' ? '#f43f5e' :
    seg === 'Sleepwalking' ? '#f59e0b' :
    seg === 'Wounded but Sheltered' ? '#3b82f6' : '#10b981';

  // Gridlines + tick labels at 0/25/50/75/100 on each axis.
  const ticks = [0, 25, 50, 75, 100];
  const grid = ticks.map(t => {
    const x = xToPx(t), y = yToPx(t);
    return (
      `<line x1="${x}" y1="${mT}" x2="${x}" y2="${mT + plotH}" stroke="#1e293b" stroke-dasharray="3,3"/>` +
      `<line x1="${mL}" y1="${y}" x2="${mL + plotW}" y2="${y}" stroke="#1e293b" stroke-dasharray="3,3"/>` +
      `<text x="${x}" y="${mT + plotH + 14}" text-anchor="middle" fill="#94a3b8" font-size="9" font-family="sans-serif">${t}</text>` +
      `<text x="${mL - 8}" y="${y + 3}" text-anchor="end" fill="#94a3b8" font-size="9" font-family="sans-serif">${t}</text>`
    );
  }).join('\n');

  // Reference lines: vertical at FAC median, horizontal at FAVI cutoff.
  const xRef = xToPx(facCutoff), yRef = yToPx(faviCutoff);
  const refs = [
    `<line x1="${xRef}" y1="${mT}" x2="${xRef}" y2="${mT + plotH}" stroke="#475569" stroke-dasharray="6,4"/>`,
    `<line x1="${mL}" y1="${yRef}" x2="${mL + plotW}" y2="${yRef}" stroke="#475569" stroke-dasharray="6,4"/>`,
    `<text x="${xRef + 4}" y="${mT + 10}" fill="#475569" font-size="9" font-family="sans-serif">FAC=${facCutoff.toFixed(0)}</text>`,
    `<text x="${mL + plotW - 4}" y="${yRef - 4}" text-anchor="end" fill="#475569" font-size="9" font-family="sans-serif">FAVI=${faviCutoff}</text>`,
  ].join('\n');

  // Quadrant labels in the four corners.
  const labels = [
    `<text x="${mL + 8}" y="${mT + 16}" fill="#fb7185" font-size="10" font-weight="700" font-family="sans-serif">TRAPPED</text>`,
    `<text x="${mL + plotW - 8}" y="${mT + 16}" text-anchor="end" fill="#fbbf24" font-size="10" font-weight="700" font-family="sans-serif">WEALTHY UNDER PRESSURE</text>`,
    `<text x="${mL + 8}" y="${mT + plotH - 8}" fill="#60a5fa" font-size="10" font-weight="700" font-family="sans-serif">MATURE &amp; STABLE</text>`,
    `<text x="${mL + plotW - 8}" y="${mT + plotH - 8}" text-anchor="end" fill="#34d399" font-size="10" font-weight="700" font-family="sans-serif">FUTURE-READY</text>`,
  ].join('\n');

  // Dots, one per company that has any signal on either axis.
  const dots = faviCompanies
    .filter((c: any) => (c.favi_score || 0) > 0 || (c.fac || 0) > 0)
    .map((c: any) => {
      const cx = xToPx(c.fac || 0), cy = yToPx(c.favi_score || 0);
      const fill = segColor(c.segment);
      const safeName = String(c.name || '').replace(/[<>&"']/g, ch => ({ '<': '&lt;', '>': '&gt;', '&': '&amp;', '"': '&quot;', "'": '&#39;' } as any)[ch] || ch);
      return `<circle cx="${cx}" cy="${cy}" r="4.5" fill="${fill}" fill-opacity="0.78" stroke="${fill}" stroke-opacity="0.95"><title>${safeName} (${c.domain || ''}) — FAC=${c.fac || 0}, FAVI=${c.favi_score || 0}, ${c.segment || ''}</title></circle>`;
    }).join('\n');

  // Axis titles.
  const xTitle = `<text x="${mL + plotW / 2}" y="${chartH - 8}" text-anchor="middle" fill="#64748b" font-size="9" font-family="sans-serif">Financial Adaptation Capacity (FAC)</text>`;
  const yTitle = `<text x="${14}" y="${mT + plotH / 2}" text-anchor="middle" transform="rotate(-90 14 ${mT + plotH / 2})" fill="#64748b" font-size="9" font-family="sans-serif">Foresight Vulnerability (FAVI)</text>`;

  const caption = `<div style="margin-top:8px;display:grid;grid-template-columns:1fr 1fr;gap:6px 16px;font-size:10px;color:#94a3b8">
    <div><span style="color:#fb7185;font-weight:700">TRAPPED</span> <span style="color:#64748b">(top-left)</span> — low cushion, future bad</div>
    <div><span style="color:#fbbf24;font-weight:700">WEALTHY UNDER PRESSURE</span> <span style="color:#64748b">(top-right)</span> — must invest</div>
    <div><span style="color:#60a5fa;font-weight:700">MATURE &amp; STABLE</span> <span style="color:#64748b">(bottom-left)</span> — financially fine, no future pressure</div>
    <div><span style="color:#34d399;font-weight:700">FUTURE-READY</span> <span style="color:#64748b">(bottom-right)</span> — best position</div>
  </div>`;

  return `<svg viewBox="0 0 ${chartW} ${chartH}" xmlns="http://www.w3.org/2000/svg" style="width:100%;max-width:${chartW}px;height:auto;display:block;margin:0 auto">${grid}\n${refs}\n${labels}\n${dots}\n${xTitle}\n${yTitle}</svg>${caption}`;
};

const buildSvgPareto = (foresights: any[]): string => {
  if (!foresights?.length) return '';
  const total = foresights.reduce((s: number, x: any) => s + x.count, 0) || 1;
  const maxCount = Math.max(...foresights.map((x: any) => x.count));
  const chartW = 820, chartH = 340, mL = 50, mR = 50, mT = 20, mB = 110;
  const plotW = chartW - mL - mR, plotH = chartH - mT - mB;
  const n = foresights.length, gap = 6;
  const bw = Math.floor((plotW - gap * (n - 1)) / n);

  let cum = 0;
  const bars: string[] = [], linePts: string[] = [];
  foresights.forEach((item: any, i: number) => {
    const x = mL + i * (bw + gap);
    const bh = (item.count / maxCount) * plotH;
    const y = mT + plotH - bh;
    const col = CATEGORY_COLORS[item.category] || '#6366f1';
    bars.push(`<rect x="${x}" y="${y}" width="${bw}" height="${bh}" rx="3" fill="${col}" opacity="0.85"/>`);
    bars.push(`<text x="${x + bw / 2}" y="${y - 5}" text-anchor="middle" fill="#cbd5e1" font-size="9" font-family="sans-serif">${item.count}</text>`);
    bars.push(`<text x="${x + bw / 2}" y="${mT + plotH + 14}" text-anchor="end" transform="rotate(-35 ${x + bw / 2} ${mT + plotH + 14})" fill="#94a3b8" font-size="9" font-family="sans-serif">${item.theme}</text>`);
    cum += item.count;
    const lx = x + bw / 2, ly = mT + plotH - (cum / total) * plotH;
    linePts.push(`${lx},${ly}`);
  });
  const ticks = [0, 0.25, 0.5, 0.75, 1];
  const grid = ticks.map(t => {
    const y = mT + plotH - t * plotH;
    return `<line x1="${mL}" y1="${y}" x2="${mL + plotW}" y2="${y}" stroke="#1e293b" stroke-dasharray="3,3"/>` +
      `<text x="${mL - 8}" y="${y + 3}" text-anchor="end" fill="#94a3b8" font-size="9" font-family="sans-serif">${Math.round(t * maxCount)}</text>` +
      `<text x="${mL + plotW + 8}" y="${y + 3}" text-anchor="start" fill="#94a3b8" font-size="9" font-family="sans-serif">${Math.round(t * 100)}%</text>`;
  }).join('\n');
  const dots = linePts.map(pt => { const [cx, cy] = pt.split(','); return `<circle cx="${cx}" cy="${cy}" r="3.5" fill="#f43f5e"/>`; }).join('\n');

  return `<svg viewBox="0 0 ${chartW} ${chartH}" xmlns="http://www.w3.org/2000/svg" style="width:100%;max-width:${chartW}px;height:auto;display:block;margin:0 auto">${grid}\n${bars.join('\n')}\n<polyline points="${linePts.join(' ')}" fill="none" stroke="#f43f5e" stroke-width="2" stroke-linejoin="round"/>\n${dots}</svg>`;
};

const buildSvgHorizBar = (items: {label: string; value: number; color: string}[], maxVal: number, width = 700, barH = 22): string => {
  if (!items.length) return '';
  const mL = 150, mR = 40, mT = 10, gap = 6;
  const plotW = width - mL - mR;
  const chartH = mT + items.length * (barH + gap);
  const bars = items.map((item, i) => {
    const y = mT + i * (barH + gap);
    const bw = Math.max(2, (item.value / maxVal) * plotW);
    return `<text x="${mL - 8}" y="${y + barH / 2 + 4}" text-anchor="end" fill="#94a3b8" font-size="10" font-family="sans-serif">${item.label}</text>` +
      `<rect x="${mL}" y="${y}" width="${bw}" height="${barH}" rx="3" fill="${item.color}" opacity="0.8"/>` +
      `<text x="${mL + bw + 6}" y="${y + barH / 2 + 4}" fill="#94a3b8" font-size="10" font-family="monospace">${Math.round(item.value)}</text>`;
  }).join('\n');
  return `<svg viewBox="0 0 ${width} ${chartH}" xmlns="http://www.w3.org/2000/svg" style="width:100%;max-width:${width}px;height:auto;display:block">${bars}</svg>`;
};

const buildSvgStackedBar = (items: {label: string; segments: {value: number; color: string; name: string}[]}[], width = 800, barH = 24): string => {
  if (!items.length) return '';
  const mL = 160, mR = 30, mT = 10, gap = 6;
  const plotW = width - mL - mR;
  const chartH = mT + items.length * (barH + gap) + 30;
  const maxTotal = Math.max(...items.map(it => it.segments.reduce((s, seg) => s + seg.value, 0)));

  const bars = items.map((item, i) => {
    const y = mT + i * (barH + gap);
    let xOff = mL;
    const rects = item.segments.map(seg => {
      const bw = maxTotal > 0 ? (seg.value / maxTotal) * plotW : 0;
      const r = bw > 0 ? `<rect x="${xOff}" y="${y}" width="${bw}" height="${barH}" fill="${seg.color}" opacity="0.8"/>` : '';
      xOff += bw;
      return r;
    }).join('');
    return `<text x="${mL - 8}" y="${y + barH / 2 + 4}" text-anchor="end" fill="#94a3b8" font-size="9" font-family="sans-serif">${item.label}</text>${rects}`;
  }).join('\n');

  // Legend
  const legendItems = items[0]?.segments.map((seg, i) => {
    const x = mL + i * 110;
    return `<rect x="${x}" y="${chartH - 20}" width="10" height="10" rx="2" fill="${seg.color}"/><text x="${x + 14}" y="${chartH - 11}" fill="#94a3b8" font-size="9" font-family="sans-serif">${seg.name}</text>`;
  }).join('\n') || '';

  return `<svg viewBox="0 0 ${width} ${chartH}" xmlns="http://www.w3.org/2000/svg" style="width:100%;max-width:${width}px;height:auto;display:block">${bars}\n${legendItems}</svg>`;
};

const buildSvgDemandBars = (demandData: {dimension: string; avg_gap: number}[], width = 700, barH = 20): string => {
  if (!demandData?.length) return '';
  const mL = 120, mR = 50, mT = 10, gap = 6;
  const plotW = width - mL - mR;
  const chartH = mT + demandData.length * (barH + gap) + 10;
  const maxAbs = Math.max(...demandData.map(d => Math.abs(d.avg_gap)), 1);
  const centerX = mL + plotW / 2;

  const bars = demandData.map((item, i) => {
    const y = mT + i * (barH + gap);
    const bw = (Math.abs(item.avg_gap) / maxAbs) * (plotW / 2);
    const color = item.avg_gap >= 0 ? '#10b981' : '#f43f5e';
    const x = item.avg_gap >= 0 ? centerX : centerX - bw;
    return `<text x="${mL - 8}" y="${y + barH / 2 + 4}" text-anchor="end" fill="#cbd5e1" font-size="10" font-family="sans-serif">${item.dimension}</text>` +
      `<rect x="${x}" y="${y}" width="${bw}" height="${barH}" rx="3" fill="${color}" opacity="0.8"/>` +
      `<text x="${item.avg_gap >= 0 ? x + bw + 6 : x - 6}" y="${y + barH / 2 + 4}" text-anchor="${item.avg_gap >= 0 ? 'start' : 'end'}" fill="#94a3b8" font-size="10" font-family="monospace">${item.avg_gap.toFixed(1)}</text>`;
  }).join('\n');

  const centerLine = `<line x1="${centerX}" y1="${mT - 5}" x2="${centerX}" y2="${mT + demandData.length * (barH + gap)}" stroke="#475569" stroke-dasharray="3,3"/>`;
  return `<svg viewBox="0 0 ${width} ${chartH}" xmlns="http://www.w3.org/2000/svg" style="width:100%;max-width:${width}px;height:auto;display:block">${centerLine}\n${bars}</svg>`;
};

const buildSvgHealthGauges = (healthData: any[]): string => {
  if (!healthData?.length) return '';
  const gaugeW = 140, gaugeH = 100, cols = Math.min(healthData.length, 5);
  const rows = Math.ceil(healthData.length / cols);
  const svgW = cols * gaugeW, svgH = rows * gaugeH;

  const gauges = healthData.map((h, i) => {
    const col = i % cols, row = Math.floor(i / cols);
    const ox = col * gaugeW + gaugeW / 2, oy = row * gaugeH + 50;
    const r = 35, score = h.score || 0;
    const color = HEALTH_COLOR(score);
    const angle = (score / 100) * 180;
    const rad = (angle * Math.PI) / 180;
    const x = ox - r * Math.cos(rad), y = oy - r * Math.sin(rad);
    const largeArc = angle > 180 ? 1 : 0;
    const bgArc = `<path d="M ${ox - r} ${oy} A ${r} ${r} 0 0 1 ${ox + r} ${oy}" fill="none" stroke="#334155" stroke-width="5"/>`;
    const fgArc = score > 0 ? `<path d="M ${ox - r} ${oy} A ${r} ${r} 0 ${largeArc} 1 ${x} ${y}" fill="none" stroke="${color}" stroke-width="5" stroke-linecap="round"/>` : '';
    return `${bgArc}${fgArc}` +
      `<text x="${ox}" y="${oy - 6}" text-anchor="middle" fill="${color}" font-size="13" font-weight="bold" font-family="sans-serif">${Math.round(score)}</text>` +
      `<text x="${ox}" y="${oy + 7}" text-anchor="middle" fill="#94a3b8" font-size="6" font-family="sans-serif">/100</text>` +
      `<text x="${ox}" y="${oy + 22}" text-anchor="middle" fill="#cbd5e1" font-size="9" font-weight="bold" font-family="sans-serif">${h.domain}</text>` +
      `<text x="${ox}" y="${oy + 33}" text-anchor="middle" fill="#64748b" font-size="7" font-family="sans-serif">${h.company_count} companies</text>`;
  }).join('\n');

  return `<svg viewBox="0 0 ${svgW} ${svgH}" xmlns="http://www.w3.org/2000/svg" style="width:100%;max-width:${svgW}px;height:auto;display:block;margin:0 auto">${gauges}</svg>`;
};

const buildSvgCoverageRing = (coverage: any): string => {
  if (!coverage) return '';
  const pct = coverage.pct || 0;
  const r = 36, cx = 50, cy = 50;
  const circumference = 2 * Math.PI * r;
  const offset = circumference - (pct / 100) * circumference;
  const color = pct >= 70 ? "#10b981" : pct >= 40 ? "#f59e0b" : "#f43f5e";
  return `<svg viewBox="0 0 100 100" xmlns="http://www.w3.org/2000/svg" style="width:120px;height:120px;display:block;margin:0 auto">` +
    `<circle cx="${cx}" cy="${cy}" r="${r}" fill="none" stroke="#334155" stroke-width="5"/>` +
    `<circle cx="${cx}" cy="${cy}" r="${r}" fill="none" stroke="${color}" stroke-width="5" stroke-linecap="round" stroke-dasharray="${circumference}" stroke-dashoffset="${offset}" transform="rotate(-90 ${cx} ${cy})"/>` +
    `<text x="${cx}" y="${cy - 4}" text-anchor="middle" fill="white" font-size="14" font-weight="bold" font-family="sans-serif">${coverage.covered}</text>` +
    `<text x="${cx}" y="${cy + 10}" text-anchor="middle" fill="#94a3b8" font-size="8" font-family="sans-serif">/${coverage.total}</text>` +
    `</svg>`;
};

const _DIMENSION_DESCRIPTIONS_HTML: Record<string, string> = {
  "Operating Model": "How the organization is structured, how teams work, governance, agility, and process design.",
  "Service Portfolio": "What the organization sells: products, services, platforms, monetization, offering mix.",
  "Values": "Culture, ethics, sustainability posture, inclusion, and the principles guiding decisions.",
  "Strategy": "Direction, market positioning, competitive thesis, growth ambition, transformation agenda.",
  "AI Readiness": "Capability to absorb AI-driven change: data foundations, talent, automation, technology stack.",
};

const buildCrossImpactHtml = (crossImpact: any[]): string => {
  if (!crossImpact?.length) return '';
  const themes = [...new Set(crossImpact.map((c: any) => c.theme))] as string[];
  const dims = ["Operating Model", "Service Portfolio", "Values", "Strategy", "AI Readiness"];
  const matrix: Record<string, Record<string, number>> = {};
  for (const item of crossImpact) { if (!matrix[item.theme]) matrix[item.theme] = {}; matrix[item.theme][item.dimension] = item.count; }
  const maxVal = Math.max(...crossImpact.map((c: any) => c.count), 1);

  // Top-3 rankings — same logic as the in-app render so the export matches 1:1.
  const themeTotals: Record<string, number> = {};
  for (const t of themes) themeTotals[t] = dims.reduce((s, d) => s + (matrix[t]?.[d] || 0), 0);
  const dimTotals: Record<string, number> = {};
  for (const d of dims) dimTotals[d] = themes.reduce((s, t) => s + (matrix[t]?.[d] || 0), 0);
  const matrixTotal = themes.reduce((s, t) => s + themeTotals[t], 0) || 1;

  const topThemes = [...themes].sort((a, b) => themeTotals[b] - themeTotals[a]).slice(0, 3);
  const topThemeRank: Record<string, number> = {};
  topThemes.forEach((t, i) => { topThemeRank[t] = i + 1; });
  const topDims = [...dims].sort((a, b) => dimTotals[b] - dimTotals[a]).slice(0, 3);
  const topDimRank: Record<string, number> = {};
  topDims.forEach((d, i) => { topDimRank[d] = i + 1; });
  const topCells = [...crossImpact].sort((a: any, b: any) => b.count - a.count).slice(0, 3);
  const topCellSet = new Set(topCells.map((c: any) => `${c.theme}|||${c.dimension}`));

  const subtitle = `<p style="font-size:11px;color:#94a3b8;margin:4px 0 12px;line-height:1.6">
    <strong style="color:#fbbf24">#1-#3 next to a theme</strong> = the three themes generating the most foresight pressure across all five dimensions.
    <strong style="color:#34d399"> #1-#3 next to a dimension</strong> = the three dimensions absorbing the most foresight pressure across all themes.
    Outlined cells = the three strongest individual theme x dimension intersections.
  </p>`;

  const tableHtml = `<table class="matrix"><thead><tr><th style="text-align:left">Theme</th>${dims.map(d => {
    const rank = topDimRank[d];
    const rankBadge = rank ? `<span style="margin-left:6px;padding:1px 4px;border:1px solid rgba(52,211,153,0.4);background:rgba(52,211,153,0.12);color:#34d399;font-size:9px;border-radius:3px;font-family:ui-monospace,monospace">#${rank}</span>` : '';
    const headerColor = rank ? '#34d399' : '#94a3b8';
    const headerWeight = rank ? '700' : '500';
    const titleAttr = `${d} — ${_DIMENSION_DESCRIPTIONS_HTML[d] || ''}${rank ? ` (#${rank} dimension absorbing the most foresight pressure across all themes; column total ${dimTotals[d]})` : ''}`;
    return `<th title="${titleAttr.replace(/"/g, '&quot;')}" style="color:${headerColor};font-weight:${headerWeight}">${d}${rankBadge}</th>`;
  }).join('')}</tr></thead><tbody>${themes.map(theme => {
    const tRank = topThemeRank[theme];
    const tRankBadge = tRank ? `<span style="margin-right:6px;padding:1px 4px;border:1px solid rgba(245,158,11,0.4);background:rgba(245,158,11,0.12);color:#fbbf24;font-size:9px;border-radius:3px;font-family:ui-monospace,monospace">#${tRank}</span>` : '';
    const themeColor = tRank ? '#fde68a' : '#cbd5e1';
    const themeWeight = tRank ? '700' : '400';
    const themeBorder = tRank ? 'border-left:2px solid rgba(251,191,36,0.6);padding-left:6px' : '';
    const themeBg = tRank ? 'background:rgba(245,158,11,0.04)' : '';
    return `<tr style="${themeBg}"><td style="text-align:left;color:${themeColor};font-weight:${themeWeight};${themeBorder}">${tRankBadge}${theme}</td>${dims.map(dim => {
      const val = matrix[theme]?.[dim] || 0;
      const isTopCell = topCellSet.has(`${theme}|||${dim}`);
      const ringStyle = isTopCell ? 'outline:1px solid rgba(251,191,36,0.7);outline-offset:-2px;font-weight:700;color:#fde68a;' : '';
      const titleAttr = val > 0
        ? `${theme} x ${dim}: ${val} foresight signal${val !== 1 ? 's' : ''} across scanned companies link the ${theme} megatrend to the ${dim} dimension.${isTopCell ? ' Top-3 highest-density cell in the matrix.' : ''}`
        : `${theme} x ${dim}: no signals link these in the scanned portfolio.`;
      const op = val > 0 ? (0.2 + (val / maxVal) * 0.6) : 0;
      return `<td title="${titleAttr.replace(/"/g, '&quot;')}" style="background:rgba(99,102,241,${op});${ringStyle}">${val > 0 ? val : '–'}</td>`;
    }).join('')}</tr>`;
  }).join('')}</tbody></table>`;

  // Legend strip + dimension descriptions footer (mirrors the in-app block).
  const legend = `<div style="margin-top:10px;display:flex;flex-wrap:wrap;gap:14px;font-size:10px;color:#94a3b8">
    <span><span style="display:inline-block;width:8px;height:8px;background:rgba(245,158,11,0.5);border:1px solid rgba(251,191,36,0.6);border-radius:2px;margin-right:5px;vertical-align:middle"></span>Top-3 themes — most pressure generated (row total)</span>
    <span><span style="display:inline-block;width:8px;height:8px;background:rgba(52,211,153,0.4);border:1px solid rgba(52,211,153,0.6);border-radius:2px;margin-right:5px;vertical-align:middle"></span>Top-3 dimensions — most pressure absorbed (column total)</span>
    <span><span style="display:inline-block;width:8px;height:8px;border-radius:2px;outline:1px solid rgba(251,191,36,0.7);outline-offset:-1px;margin-right:5px;vertical-align:middle"></span>Top-3 highest-density cells</span>
  </div>`;
  const dimDescriptions = `<div style="margin-top:14px;padding-top:10px;border-top:1px solid rgba(255,255,255,0.05);font-size:10px;color:#94a3b8;line-height:1.7">
    <div style="font-weight:600;color:#cbd5e1;margin-bottom:4px">Dimension definitions</div>
    ${dims.map(d => `<div style="margin:2px 0"><span style="color:#34d399;font-weight:600">${d}:</span> ${_DIMENSION_DESCRIPTIONS_HTML[d] || ''}</div>`).join('')}
  </div>`;

  return subtitle + tableHtml + legend + dimDescriptions;
};

// Parse and render the Cross-Impact Matrix Interpretation for HTML export.
// Mirrors the in-app rendering 1:1: section markers, theme chips with totals + %,
// per-theme distribution bars, cell chips with density + % of row, prose paragraphs.
const buildCrossImpactInterpretationHtml = (interpretation: any, crossImpact: any[]): string => {
  if (!interpretation?.text) return '';
  const dims = ["Operating Model", "Service Portfolio", "Values", "Strategy", "AI Readiness"];
  const matrix: Record<string, Record<string, number>> = {};
  for (const item of crossImpact || []) {
    if (!matrix[item.theme]) matrix[item.theme] = {};
    matrix[item.theme][item.dimension] = item.count;
  }
  const themeTotals: Record<string, number> = {};
  for (const t of Object.keys(matrix)) themeTotals[t] = dims.reduce((s, d) => s + (matrix[t]?.[d] || 0), 0);
  const matrixTotal = Object.values(themeTotals).reduce((s, v) => s + v, 0) || 1;
  const segColors: Record<string, string> = {
    "Operating Model": "#6366f1",
    "Service Portfolio": "#10b981",
    "Values": "#f59e0b",
    "Strategy": "#06b6d4",
    "AI Readiness": "#a855f7",
  };

  const lines = interpretation.text.split(/\n+/);
  const blocks: string[] = [];
  // Mirror the in-app rendering: meta sections (HOW TO USE, SOURCES) get a
  // muted style; the second paragraph after each [THEME:]/[CELL:] inside the
  // VERTICAL/HORIZONTAL sections is bolded as the interpretive payoff.
  let currentSection = '';
  let inAnalyticalSection = false;
  let inSubblock = false;
  let paraSinceSubblock = 0;
  for (const raw of lines) {
    const line = raw.trim();
    if (!line) continue;
    const sectionMatch = line.match(/^\[\s*SECTION\s*:\s*(.+?)\s*\]$/i);
    if (sectionMatch) {
      currentSection = sectionMatch[1];
      inAnalyticalSection = /VERTICAL|HORIZONTAL/i.test(currentSection);
      inSubblock = false;
      paraSinceSubblock = 0;
      const isMeta = /^\s*(how\s+to\s+use|sources)\b/i.test(currentSection);
      const headerStyle = isMeta
        ? 'color:#94a3b8;font-size:11px;font-weight:700;text-transform:uppercase;letter-spacing:0.06em;margin:24px 0 10px;padding-bottom:6px;border-bottom:1px solid rgba(148,163,184,0.18)'
        : 'color:#c4b5fd;font-size:13px;font-weight:700;margin:20px 0 10px;padding-bottom:6px;border-bottom:1px solid rgba(167,139,250,0.15)';
      blocks.push(`<h3 style="${headerStyle}">${currentSection}</h3>`);
      continue;
    }
    if (/^\[\s*THEME\s*:/i.test(line)) {
      const theme = line.match(/\[\s*THEME\s*:\s*([^\]]+?)\s*\]/i)?.[1] || '';
      const total = line.match(/\[\s*TOTAL\s*:\s*([^\]]+?)\s*\]/i)?.[1];
      const shape = line.match(/\[\s*SHAPE\s*:\s*([^\]]+?)\s*\]/i)?.[1];
      const totalNum = total ? Number(total) : NaN;
      const totalPct = Number.isFinite(totalNum) && matrixTotal > 0 ? Math.round((totalNum / matrixTotal) * 1000) / 10 : null;
      const totalChip = total
        ? `<span title="${totalPct !== null ? `${totalPct}% of total matrix density (${matrixTotal})` : ''}" style="margin-left:8px;padding:2px 8px;border-radius:4px;background:rgba(15,23,42,0.6);border:1px solid rgba(51,65,85,0.6);color:#cbd5e1;font-family:ui-monospace,monospace;font-size:11px">total ${total}${totalPct !== null ? ` <span style="color:#64748b">(${totalPct}%)</span>` : ''}</span>`
        : '';
      const shapeChip = shape
        ? `<span style="margin-left:8px;padding:2px 8px;border-radius:4px;background:rgba(99,102,241,0.15);border:1px solid rgba(99,102,241,0.3);color:#c4b5fd;font-family:ui-monospace,monospace;font-size:9px;text-transform:uppercase;letter-spacing:0.05em">${shape}</span>`
        : '';
      const themeRow = matrix[theme];
      const rowTotal = themeTotals[theme] || 0;
      let bar = '';
      if (themeRow && rowTotal > 0) {
        const segments = dims.map(d => {
          const v = themeRow[d] || 0;
          const w = (v / rowTotal) * 100;
          if (w <= 0) return '';
          const pct = Math.round(w * 10) / 10;
          return `<div title="${d}: density ${v} (${pct}% of row)" style="width:${w}%;background:${segColors[d]};height:100%"></div>`;
        }).join('');
        const legend = dims.map(d => {
          const v = themeRow[d] || 0;
          if (v <= 0) return '';
          const pct = Math.round((v / rowTotal) * 1000) / 10;
          return `<span style="margin-right:14px;font-size:9px;color:#94a3b8"><span style="display:inline-block;width:6px;height:6px;background:${segColors[d]};border-radius:1px;margin-right:4px;vertical-align:middle"></span>${d} ${pct}%</span>`;
        }).join('');
        bar = `<div style="margin-top:6px;max-width:480px"><div style="height:8px;display:flex;background:rgba(15,23,42,0.6);border:1px solid rgba(51,65,85,0.6);border-radius:3px;overflow:hidden">${segments}</div><div style="margin-top:4px">${legend}</div></div>`;
      }
      blocks.push(`<div style="margin:14px 0 6px"><div><strong style="color:#e0e7ff;font-size:12px">${theme}</strong>${totalChip}${shapeChip}</div>${bar}</div>`);
      inSubblock = inAnalyticalSection;
      paraSinceSubblock = 0;
      continue;
    }
    if (/^\[\s*CELL\s*:/i.test(line)) {
      const cellRaw = line.match(/\[\s*CELL\s*:\s*([^\]]+?)\s*\]/i)?.[1] || '';
      const density = line.match(/\[\s*DENSITY\s*:\s*([^\]]+?)\s*\]/i)?.[1];
      const rank = line.match(/\[\s*RANK\s*:\s*([^\]]+?)\s*\]/i)?.[1];
      const [theme, dimension] = cellRaw.split(/\s+[x×]\s+/i);
      const densityNum = density ? Number(density) : NaN;
      const rowTotal = theme ? (themeTotals[theme] || 0) : 0;
      const rowPct = Number.isFinite(densityNum) && rowTotal > 0 ? Math.round((densityNum / rowTotal) * 1000) / 10 : null;
      const rankChip = rank ? `<span style="margin-right:8px;padding:1px 6px;border-radius:3px;background:rgba(245,158,11,0.15);border:1px solid rgba(245,158,11,0.3);color:#fde68a;font-family:ui-monospace,monospace;font-size:9px">#${rank}</span>` : '';
      const densityChip = density ? `<span title="${rowPct !== null ? `${rowPct}% of ${theme} row total (${rowTotal})` : ''}" style="margin-left:8px;padding:2px 8px;border-radius:4px;background:rgba(15,23,42,0.6);border:1px solid rgba(51,65,85,0.6);color:#cbd5e1;font-family:ui-monospace,monospace;font-size:11px">density ${density}${rowPct !== null ? ` <span style="color:#64748b">(${rowPct}% of row)</span>` : ''}</span>` : '';
      blocks.push(`<div style="margin:14px 0 6px;font-size:12px">${rankChip}<strong style="color:#e0e7ff">${theme || cellRaw}</strong>${dimension ? ` <span style="color:#64748b">x</span> <strong style="color:#6ee7b7">${dimension}</strong>` : ''}${densityChip}</div>`);
      inSubblock = inAnalyticalSection;
      paraSinceSubblock = 0;
      continue;
    }
    if (inSubblock) paraSinceSubblock += 1;
    const emphasise = inSubblock && paraSinceSubblock === 2;
    const paraStyle = emphasise
      ? 'font-size:12px;line-height:1.7;color:#e0e7ff;font-weight:600;margin:8px 0'
      : 'font-size:12px;line-height:1.7;color:rgba(203,213,225,0.9);margin:8px 0';
    blocks.push(`<p style="${paraStyle}">${line}</p>`);
  }

  const meta = (interpretation.sources_used?.length || interpretation.model)
    ? `<div style="margin-top:14px;padding-top:10px;border-top:1px solid rgba(167,139,250,0.15);font-size:9px;color:#64748b;display:flex;flex-wrap:wrap;gap:14px">
        ${interpretation.sources_used?.length > 0 ? `<span>Sources: ${interpretation.sources_used.join(' · ')}</span>` : ''}
        ${interpretation.numeric_check ? `<span>Numeric check: ${interpretation.numeric_check.passed ? 'passed' : `${(interpretation.numeric_check.fabricated || []).length} flagged`}</span>` : ''}
        ${interpretation.model ? `<span>Model: ${interpretation.model}</span>` : ''}
      </div>` : '';
  return blocks.join('') + meta;
};

// C1 — Concentration vs Volume table for HTML export. Static replacement for the
// in-app Recharts scatter; same data, same per-theme shape classification.
const buildC1ConcentrationHtml = (crossImpact: any[]): string => {
  if (!crossImpact?.length) return '';
  const dims = ["Operating Model", "Service Portfolio", "Values", "Strategy", "AI Readiness"];
  const themes = [...new Set(crossImpact.map((c: any) => c.theme))] as string[];
  const matrix: Record<string, Record<string, number>> = {};
  for (const item of crossImpact) { if (!matrix[item.theme]) matrix[item.theme] = {}; matrix[item.theme][item.dimension] = item.count; }
  const SHAPE_COLORS: Record<string, string> = { CONCENTRATED: '#6366f1', BIMODAL: '#06b6d4', DIFFUSE: '#f59e0b', MIXED: '#64748b', SMALL_N: '#475569' };
  const rows = themes.map(theme => {
    const vals = dims.map(d => matrix[theme]?.[d] || 0);
    const total = vals.reduce((s, v) => s + v, 0);
    const sorted = [...vals].sort((a, b) => b - a);
    const topShare = total > 0 ? sorted[0] / total : 0;
    const top2Share = total > 0 ? (sorted[0] + sorted[1]) / total : 0;
    const dimCount = vals.filter(v => v > 0).length;
    const topDimIdx = vals.indexOf(sorted[0]);
    const topDim = dims[topDimIdx] || '';
    let shape = 'MIXED';
    if (total < 8) shape = 'SMALL_N';
    else if (topShare >= 0.35 && top2Share >= 0.60) shape = 'CONCENTRATED';
    else if (top2Share >= 0.60) shape = 'BIMODAL';
    else if (topShare < 0.30 && top2Share < 0.55) shape = 'DIFFUSE';
    return { theme, total, topShare: Math.round(topShare * 1000) / 10, dimCount, topDim, shape };
  }).sort((a, b) => b.total - a.total);
  const tableRows = rows.map(r => {
    const color = SHAPE_COLORS[r.shape];
    return `<tr><td style="text-align:left;color:#cbd5e1">${r.theme}</td><td style="text-align:right;font-family:ui-monospace,monospace">${r.total}</td><td style="text-align:right;font-family:ui-monospace,monospace">${r.topShare}%</td><td style="text-align:left;color:#6ee7b7">${r.topDim}</td><td style="text-align:right;font-family:ui-monospace,monospace">${r.dimCount} of 5</td><td style="text-align:center"><span style="padding:1px 8px;border-radius:3px;background:${color}33;border:1px solid ${color}66;color:${color};font-family:ui-monospace,monospace;font-size:10px">${r.shape}</span></td></tr>`;
  }).join('');
  return `<p style="font-size:10px;color:#64748b;margin-bottom:10px">X = total density (volume of pressure). Top-share % = share absorbed by the top dimension (concentration). Dimensions touched = number of dimensions with non-zero density. Shape labels follow the same thresholds the interpretation uses (CONCENTRATED if top dim ≥ 35% and top two ≥ 60%; BIMODAL if top two ≥ 60% but top single < 35%; DIFFUSE if top dim < 30% and top two < 55%; SMALL_N when total density < 8).</p>
  <table><thead><tr><th style="text-align:left">Theme</th><th style="text-align:right">Total</th><th style="text-align:right">Top-dim share</th><th style="text-align:left">Top dim</th><th style="text-align:right">Dimensions touched</th><th style="text-align:center">Shape</th></tr></thead><tbody>${tableRows}</tbody></table>`;
};

// C3 — Theme Momentum bars (reads observatoryData.theme_momentum).
const buildThemeMomentumHtml = (themeMomentum: any[]): string => {
  if (!themeMomentum?.length) return '';
  const top = themeMomentum.slice(0, 10);
  const rows = top.map((row: any) => {
    const tot = row.total || 1;
    const aPct = ((row.accelerating || 0) / tot) * 100;
    const sPct = ((row.stable || 0) / tot) * 100;
    const dPct = ((row.decelerating || 0) / tot) * 100;
    return `<tr>
      <td style="text-align:left;color:#cbd5e1;font-size:11px;width:180px">${row.theme}</td>
      <td><div style="height:12px;display:flex;background:rgba(15,23,42,0.6);border:1px solid rgba(51,65,85,0.6);border-radius:3px;overflow:hidden">
        ${aPct > 0 ? `<div title="Accelerating: ${row.accelerating}" style="width:${aPct}%;background:#10b981"></div>` : ''}
        ${sPct > 0 ? `<div title="Stable: ${row.stable}" style="width:${sPct}%;background:#64748b"></div>` : ''}
        ${dPct > 0 ? `<div title="Decelerating: ${row.decelerating}" style="width:${dPct}%;background:#f43f5e"></div>` : ''}
      </div></td>
      <td style="text-align:right;font-family:ui-monospace,monospace;color:#64748b;width:60px">${row.total}</td>
    </tr>`;
  }).join('');
  return `<p style="font-size:10px;color:#64748b;margin-bottom:10px">Accelerating vs. stable vs. decelerating signals per theme, drawn from the Foresight Knowledge Base.</p>
  <table style="width:100%"><tbody>${rows}</tbody></table>
  <div style="margin-top:10px;display:flex;flex-wrap:wrap;gap:14px;font-size:10px;color:#94a3b8">
    <span><span style="display:inline-block;width:8px;height:8px;background:#10b981;border-radius:2px;margin-right:5px;vertical-align:middle"></span>Accelerating</span>
    <span><span style="display:inline-block;width:8px;height:8px;background:#64748b;border-radius:2px;margin-right:5px;vertical-align:middle"></span>Stable</span>
    <span><span style="display:inline-block;width:8px;height:8px;background:#f43f5e;border-radius:2px;margin-right:5px;vertical-align:middle"></span>Decelerating</span>
  </div>`;
};

const exportMiTabHtml = (tabName: string, data: any, observatoryData?: any, crossImpactInterpretationData?: any) => {
  if (!data) return;
  const date = new Date().toLocaleDateString('en-FI', { day: 'numeric', month: 'long', year: 'numeric' });
  let content = '';

  if (tabName === 'executive') {
    // Market Health Gauges (SVG)
    if (data.market_health?.length) {
      content += `<div class="section"><h2>Market Health by Domain</h2><p style="font-size:10px;color:#64748b;margin-bottom:16px">Composite health score per industry domain based on average reality scores, intent-reality gaps, and archetype distribution.</p>${buildSvgHealthGauges(data.market_health)}</div>`;
    }

    // Foresight Coverage Ring + Demand Intensity side by side
    const coverageSvg = data.foresight_coverage ? buildSvgCoverageRing(data.foresight_coverage) : '';
    const demandSvg = data.demand_intensity?.length ? buildSvgDemandBars(data.demand_intensity) : '';
    if (coverageSvg || demandSvg) {
      content += `<div class="section" style="display:flex;gap:24px;align-items:flex-start;flex-wrap:wrap">`;
      if (coverageSvg) {
        const cov = data.foresight_coverage;
        content += `<div style="text-align:center;min-width:160px"><h3 style="color:#22d3ee;font-size:12px;text-transform:uppercase;margin-bottom:12px">Foresight Coverage</h3>${coverageSvg}<div style="font-size:10px;color:#94a3b8;margin-top:8px">themes with 3+ signals</div></div>`;
      }
      if (demandSvg) {
        content += `<div style="flex:1;min-width:300px"><h3 style="color:#fbbf24;font-size:12px;text-transform:uppercase;margin-bottom:12px">Demand Intensity (avg gap per dimension)</h3>${demandSvg}<div style="font-size:9px;color:#64748b;margin-top:8px">Negative values = market-wide gap where companies underperform their ambitions</div></div>`;
      }
      content += `</div>`;
    }

    // Quick stats
    content += `<div class="section"><h2>Key Metrics</h2><div style="display:grid;grid-template-columns:repeat(4,1fr);gap:16px;text-align:center">` +
      `<div><div style="font-size:28px;font-weight:bold;color:white">${data.company_count}</div><div style="font-size:10px;color:#94a3b8;text-transform:uppercase">Total Companies</div></div>` +
      `<div><div style="font-size:28px;font-weight:bold;color:#22d3ee">${data.detailed_count}</div><div style="font-size:10px;color:#94a3b8;text-transform:uppercase">With Full Data</div></div>` +
      `<div><div style="font-size:28px;font-weight:bold;color:#34d399">${(data.top_foresights || []).length}</div><div style="font-size:10px;color:#94a3b8;text-transform:uppercase">Top Foresight Themes</div></div>` +
      `<div><div style="font-size:28px;font-weight:bold;color:#fb7185">${(data.vulnerable_companies || []).length}</div><div style="font-size:10px;color:#94a3b8;text-transform:uppercase">Vulnerable Companies</div></div>` +
      `</div></div>`;

    // FAVI Segment Summary
    if (data.favi_segments) {
      const segs = [
        { key: 'Critical', color: '#f43f5e', bg: 'rgba(244,63,94,0.1)', border: 'rgba(244,63,94,0.3)', desc: 'Struggling now AND blind to what\'s coming' },
        { key: 'Sleepwalking', color: '#f59e0b', bg: 'rgba(245,158,11,0.1)', border: 'rgba(245,158,11,0.3)', desc: 'Looks fine today but forces are building' },
        { key: 'Wounded but Sheltered', color: '#3b82f6', bg: 'rgba(59,130,246,0.1)', border: 'rgba(59,130,246,0.3)', desc: 'Problems are real but future isn\'t adding pressure' },
        { key: 'Safe Harbor', color: '#10b981', bg: 'rgba(16,185,129,0.1)', border: 'rgba(16,185,129,0.3)', desc: 'Healthy and future-ready' },
      ];
      const cards = segs.map(s => {
        const count = data.favi_segments[s.key] || 0;
        return `<div class="segment-card" style="background:${s.bg};border:1px solid ${s.border}"><h4 style="color:${s.color}">${s.key}</h4><div class="count" style="color:${s.color}">${count}</div><div class="desc">${s.desc}</div></div>`;
      }).join('');
      content += `<div class="section"><h2>Foresight-Adjusted Vulnerability Index</h2><div class="segment-grid">${cards}</div></div>`;
    }

    // Observatory Layer 2 Sections 3 & 4 — Three Horizons + Contrarian Signal
    if (observatoryData?.finland_analysis?.text) {
      const fullText = observatoryData.finland_analysis.text;
      const mdToHtml = (t: string) => t.split(/\n\n+/).map((b: string) => {
        const s = b.trim(); if (!s) return '';
        if (/^-{3,}$/.test(s)) return '<hr style="border:none;border-top:1px solid #334155;margin:24px 0">';
        if (s.startsWith('### ')) return `<h3 style="font-size:14px;margin:20px 0 8px;color:#c4b5fd">${s.slice(4).replace(/\*\*([^*]+)\*\*/g, '<strong>$1</strong>')}</h3>`;
        if (s.startsWith('## ')) return `<h2>${s.slice(3).replace(/\*\*([^*]+)\*\*/g, '<strong>$1</strong>')}</h2>`;
        let h = s.replace(/\n/g, ' ').replace(/\*\*([^*]+)\*\*/g, '<strong>$1</strong>');
        if (!h.includes('<strong>')) h = h.replace(/^([^.]+\.)(\s|$)/, '<strong>$1</strong>$2');
        return `<p style="font-size:13px;line-height:1.8;margin-bottom:16px">${h}</p>`;
      }).join('\n');

      const s3Match = fullText.match(/##\s*Section\s*3[^\n]*\n([\s\S]*?)(?=##\s*Section\s*4|$)/i);
      if (s3Match) {
        const section3 = s3Match[1].trim().replace(/^-{3,}\s*/gm, '').trim();
        if (section3) {
          content += `<div class="section" style="border-color:rgba(167,139,250,0.3)"><h2 style="color:#a78bfa">Three Horizons for Finland</h2><p style="font-size:10px;color:#64748b;margin-bottom:16px">Strategic Foresight Observatory — Where Finland is heading across three timeframes</p>${mdToHtml(section3)}</div>`;
        }
      }
      const s4Match = fullText.match(/##\s*Section\s*4[^\n]*\n([\s\S]*?)(?=##\s*Section\s*5|$)/i);
      if (s4Match) {
        const section4 = s4Match[1].trim().replace(/^-{3,}\s*/gm, '').trim();
        if (section4) {
          content += `<div class="section" style="border-color:rgba(244,63,94,0.3)"><h2 style="color:#fb7185">The Contrarian Signal: Finland's Dangerous Assumption</h2><p style="font-size:10px;color:#64748b;margin-bottom:16px">Strategic Foresight Observatory — The uncomfortable truth the data reveals</p>${mdToHtml(section4)}</div>`;
        }
      }
    }
  }

  if (tabName === 'future') {
    // 1. Pareto chart
    content += `<div class="section"><h2>Top ${(data.top_foresights || []).length} Foresight Themes (Pareto)</h2>${buildSvgPareto(data.top_foresights || [])}</div>`;

    // 2. Theme descriptions
    if (data.top_foresights?.length) {
      const descs = data.top_foresights.map((f: any) => {
        const col = CATEGORY_COLORS[f.category] || '#6366f1';
        const desc = FORESIGHT_THEME_DESCRIPTIONS[f.theme] || f.category;
        return `<div style="display:flex;gap:8px;margin:4px 0"><span style="color:${col};font-weight:bold;font-size:11px;white-space:nowrap">${f.theme}:</span><span style="color:#94a3b8;font-size:11px">${desc}</span></div>`;
      }).join('');
      content += `<div class="section"><h2>Theme Descriptions</h2>${descs}</div>`;
    }

    // 3. Cross-Impact Matrix (with subtitle, rank chips, legend, dim descriptions)
    if (data.cross_impact?.length) {
      content += `<div class="section"><h2>Theme × Dimension Cross-Impact Matrix</h2>${buildCrossImpactHtml(data.cross_impact)}</div>`;
    }

    // 4. Cross-Impact Matrix Interpretation — placed directly under the matrix
    //    so the export mirrors the new in-app order.
    if (crossImpactInterpretationData?.text && data.cross_impact?.length) {
      const generatedAt = crossImpactInterpretationData.generated_at
        ? new Date(crossImpactInterpretationData.generated_at).toLocaleDateString('en-FI', { day: 'numeric', month: 'short', year: 'numeric' })
        : '';
      content += `<div class="section" style="border-color:rgba(99,102,241,0.3)">
        <h2 style="color:#a5b4fc">Cross-Impact Matrix Interpretation</h2>
        <p style="font-size:10px;color:#64748b;margin-bottom:14px">Where structural pressure consolidates across the Theme x Dimension matrix${generatedAt ? ` — ${generatedAt}` : ''}</p>
        ${buildCrossImpactInterpretationHtml(crossImpactInterpretationData, data.cross_impact)}
      </div>`;
    }

    // 5. C1 — Concentration vs Volume table (replaces the in-app scatter)
    if (data.cross_impact?.length) {
      content += `<div class="section"><h2 style="color:#a5b4fc">Concentration vs Volume — Top 15 Themes</h2>${buildC1ConcentrationHtml(data.cross_impact)}</div>`;
    }

    // 6. C2 (Domain x Dimension) is intentionally hidden in the app — also
    //    omitted from the export to keep them in sync.

    // 7. C3 — Theme Momentum bars (only present when Observatory has been generated)
    if (observatoryData?.theme_momentum?.length) {
      content += `<div class="section"><h2 style="color:#67e8f9">Theme Momentum — Top 10 themes by signal volume</h2>${buildThemeMomentumHtml(observatoryData.theme_momentum)}</div>`;
    }

    // 8. Finnish Economy Force Map (last)
    if (observatoryData?.finland_analysis?.text) {
      const mdToHtml = (t: string) => t.split(/\n\n+/).map(b => {
        const s = b.trim(); if (!s) return '';
        if (/^-{3,}$/.test(s)) return '<hr style="border:none;border-top:1px solid #334155;margin:24px 0">';
        if (s.startsWith('## ')) return `<h2>${s.slice(3).replace(/\*\*([^*]+)\*\*/g, '<strong>$1</strong>')}</h2>`;
        if (s.startsWith('# ')) return `<h1 style="font-size:20px;margin:24px 0 10px;color:white">${s.slice(2)}</h1>`;
        let h = s.replace(/\n/g, ' ').replace(/\*\*([^*]+)\*\*/g, '<strong>$1</strong>');
        if (!h.includes('<strong>')) h = h.replace(/^([^.]+\.)(\s|$)/, '<strong>$1</strong>$2');
        return `<p style="font-size:13px;line-height:1.8;margin-bottom:16px">${h}</p>`;
      }).join('\n');
      content += `<div class="section" style="border-color:rgba(167,139,250,0.3)"><h2 style="color:#a78bfa">Finnish Economy Force Map</h2><p style="font-size:10px;color:#64748b;margin-bottom:16px">Strategic Foresight Observatory — How global forces reshape Finnish business</p>${mdToHtml(observatoryData.finland_analysis.text)}</div>`;
    }
  }

  if (tabName === 'current') {
    // Root causes stacked bar chart
    if (data.top_causes?.length) {
      const causes = data.top_causes;
      const items = causes.map((c: any) => ({
        label: c.cluster_label,
        segments: [
          { value: c.why_count || 0, color: '#3b82f6', name: 'WHY (strategic)' },
          { value: c.what_count || 0, color: '#14b8a6', name: 'WHAT (operational)' },
        ]
      }));
      content += `<div class="section"><h2>Root Cause Analysis (WHY vs WHAT)</h2>${buildSvgStackedBar(items)}`;

      // Cluster descriptions
      const descs = causes.map((c: any) => {
        const desc = ROOT_CAUSE_DESCRIPTIONS[c.cluster_label] || '';
        return desc ? `<div style="display:flex;gap:8px;margin:4px 0"><span style="color:#cbd5e1;font-weight:bold;font-size:11px;white-space:nowrap">${c.cluster_label}:</span><span style="color:#94a3b8;font-size:11px">${desc}</span></div>` : '';
      }).filter(Boolean).join('');
      if (descs) content += `<div style="margin-top:16px">${descs}</div>`;

      // Examples
      causes.slice(0, 5).forEach((c: any) => {
        if (c.examples?.length) {
          content += `<div style="margin-top:16px"><h3 style="font-size:12px;color:#cbd5e1">${c.cluster_label} <span style="color:#64748b;font-weight:normal">(${c.count} matches)</span></h3>`;
          content += c.examples.slice(0, 5).map((ex: any) => `<div style="font-size:11px;color:#94a3b8;margin:4px 0 4px 12px">→ <span style="color:#22d3ee">${ex.company}</span>: ${ex.text}</div>`).join('');
          content += '</div>';
        }
      });
      content += '</div>';
    }

    // Archetype distribution stacked bar
    if (data.archetype_flow?.length) {
      const items = data.archetype_flow.map((a: any) => ({
        label: a.domain,
        segments: [
          { value: a.thriving || 0, color: '#10b981', name: 'Thriving' },
          { value: a.modernizing || 0, color: '#f59e0b', name: 'Modernizing' },
          { value: a.cash_cow || 0, color: '#3b82f6', name: 'Cash Cow' },
          { value: a.at_risk || 0, color: '#f43f5e', name: 'At Risk' },
        ]
      }));
      content += `<div class="section"><h2>Archetype Distribution by Domain</h2>${buildSvgStackedBar(items)}</div>`;
    }
  }

  if (tabName === 'favi') {
    // Segment matrix
    if (data.favi_segments) {
      const segs = [
        { key: 'Critical', color: '#f43f5e', bg: 'rgba(244,63,94,0.1)', border: 'rgba(244,63,94,0.3)', desc: 'Struggling now AND blind to what\'s coming' },
        { key: 'Sleepwalking', color: '#f59e0b', bg: 'rgba(245,158,11,0.1)', border: 'rgba(245,158,11,0.3)', desc: 'Looks fine today but forces are building' },
        { key: 'Wounded but Sheltered', color: '#3b82f6', bg: 'rgba(59,130,246,0.1)', border: 'rgba(59,130,246,0.3)', desc: 'Problems are real but future isn\'t adding pressure' },
        { key: 'Safe Harbor', color: '#10b981', bg: 'rgba(16,185,129,0.1)', border: 'rgba(16,185,129,0.3)', desc: 'Healthy and future-ready' },
      ];
      const totalCo = Object.values(data.favi_segments).reduce((a: number, b: any) => a + (b || 0), 0) as number;
      const cards = segs.map(s => {
        const count = data.favi_segments[s.key] || 0;
        const pct = totalCo > 0 ? Math.round((count / totalCo) * 100) : 0;
        return `<div class="segment-card" style="background:${s.bg};border:1px solid ${s.border}"><h4 style="color:${s.color}">${s.key}</h4><div class="count" style="color:${s.color}">${count}</div><div style="font-size:10px;color:#64748b">${pct}% of companies</div><div class="desc">${s.desc}</div></div>`;
      }).join('');
      // Cutoffs note — surfaces what split produced these counts so the
      // download is self-explanatory without needing the live page.
      let cutoffsHtml = '';
      if (data.favi_cutoffs) {
        const src: string = data.favi_cutoffs.source || '';
        const sourceLabel = src.startsWith('percentile')
          ? src.replace('percentile:', '').replace(/p(\d+(\.\d+)?)/g, '$1th percentile').replace(/,/g, ', ').replace('favi=', 'FAVI ').replace('current=', 'Current ')
          : 'fixed fallback';
        cutoffsHtml = `<p style="font-size:11px;color:#94a3b8;margin-top:8px">Cutoffs (${sourceLabel}): FAVI &ge; <span style="font-family:ui-monospace,monospace;color:#cbd5e1">${data.favi_cutoffs.favi}</span> &middot; Current Vulnerability &ge; <span style="font-family:ui-monospace,monospace;color:#cbd5e1">${data.favi_cutoffs.current_vulnerability}</span></p>`;
      }
      content += `<div class="section"><h2>Strategic Vulnerability Segments</h2><div class="segment-grid">${cards}</div>${cutoffsHtml}</div>`;
    }

    // Adaptability x Future Vulnerability scatter — replaces the old Domain
    // Foresight Pressure bar in the static export, mirroring the in-app view.
    if (data.favi_companies?.length) {
      const facValues = data.favi_companies.map((c: any) => c.fac || 0).filter((x: number) => x >= 0);
      let facMedian = 50;
      if (facValues.length) {
        const sortedFac = [...facValues].sort((a: number, b: number) => a - b);
        const mid = Math.floor(sortedFac.length / 2);
        facMedian = sortedFac.length % 2 === 0 ? (sortedFac[mid - 1] + sortedFac[mid]) / 2 : sortedFac[mid];
      }
      const faviCutoff = data.favi_cutoffs?.favi ?? 48;
      content += `<div class="section"><h2>Adaptability × Future Vulnerability</h2><p style="font-size:10px;color:#64748b;margin-bottom:10px">X = financial capacity to absorb change (FAC). Y = foresight-driven future pressure (FAVI). Reference lines at the calibrated FAVI cutoff (Y) and the portfolio's median FAC (X).</p>${buildSvgAdaptabilityScatter(data.favi_companies, faviCutoff, facMedian)}</div>`;
    }

    // FAVI company table
    if (data.favi_companies?.length) {
      const sorted = [...data.favi_companies].sort((a: any, b: any) => (b.favi_score || 0) - (a.favi_score || 0));
      const segBadge = (seg: string) => {
        const cls = seg === 'Critical' ? 'badge-critical' : seg === 'Sleepwalking' ? 'badge-sleepwalking' : seg?.includes('Wounded') ? 'badge-wounded' : 'badge-safe';
        return `<span class="badge ${cls}">${seg}</span>`;
      };
      const rows = sorted.map((c: any, i: number) => {
        const fc = FAVI_COLOR(c.favi_score || 0);
        return `<tr>
          <td style="font-family:monospace;color:#64748b">${i + 1}</td>
          <td style="color:#22d3ee;font-weight:600">${c.name}</td>
          <td style="font-size:11px">${c.domain || ''}</td>
          <td style="color:${fc};font-weight:bold;font-family:monospace">${Math.round(c.favi_score || 0)}</td>
          <td style="font-family:monospace">${Math.round(c.fps || 0)}</td>
          <td style="font-family:monospace">${Math.round(c.readiness_gap || 0)}</td>
          <td style="font-family:monospace">${Math.round(c.fac || 0)}</td>
          <td style="font-family:monospace">${c.megatrend_exposure ? Math.round(c.megatrend_exposure) + '%' : '–'}</td>
          <td style="font-family:monospace">${Math.round(c.current_vulnerability || 0)}</td>
          <td>${segBadge(c.segment)}</td>
        </tr>`;
      }).join('');
      content += `<div class="section"><h2>Foresight-Adjusted Vulnerability Index — Company Rankings</h2><div style="overflow-x:auto"><table><thead><tr><th>#</th><th>Company</th><th>Domain</th><th>Vulnerability</th><th>Pressure</th><th>Gap</th><th>Adapt.</th><th>Megatrend</th><th>Vuln.</th><th>Segment</th></tr></thead><tbody>${rows}</tbody></table></div></div>`;
    }
  }

  const titles: Record<string, string> = {
    executive: 'Executive Summary',
    future: 'Future Demand Analysis',
    current: 'Current Demand Analysis',
    favi: 'Foresight Vulnerability Analysis',
  };

  const html = `<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>${titles[tabName]} — ${date}</title>
  <style>${MI_HTML_CSS}</style>
</head>
<body>
  <h1>Market Intelligence: ${titles[tabName]}</h1>
  <div class="subtitle">${data.company_count || '?'} companies analyzed — ${date}</div>
  ${content}
  <div class="footer">Market Intelligence — ${date}<br>Generated by The Scanner</div>
</body>
</html>`;

  downloadHtml(html, `mi-${tabName}-${new Date().toISOString().slice(0, 10)}.html`);
};

// ═══════════════════════════════════════════════════════════════════════════════
// TAB 1: EXECUTIVE SUMMARY
// ═══════════════════════════════════════════════════════════════════════════════

const HealthGauge = ({ domain, score, companyCount, dossierSnippet }: { domain: string; score: number; companyCount: number; dossierSnippet?: string | null }) => {
  const [showTooltip, setShowTooltip] = useState(false);
  const color = HEALTH_COLOR(score);
  const angle = (score / 100) * 180;
  const rad = (angle * Math.PI) / 180;
  const r = 40;
  const cx = 50;
  const cy = 50;
  const x = cx - r * Math.cos(rad);
  const y = cy - r * Math.sin(rad);
  const largeArc = angle > 180 ? 1 : 0;

  return (
    <div
      className="bg-black/20 border border-white/10 rounded-lg p-3 text-center relative cursor-default"
      onMouseEnter={() => dossierSnippet && setShowTooltip(true)}
      onMouseLeave={() => setShowTooltip(false)}
    >
      <svg viewBox="0 0 100 60" className="w-full max-w-[120px] mx-auto">
        <path d={`M ${cx - r} ${cy} A ${r} ${r} 0 0 1 ${cx + r} ${cy}`} fill="none" stroke="#334155" strokeWidth="6" />
        {score > 0 && (
          <path d={`M ${cx - r} ${cy} A ${r} ${r} 0 ${largeArc} 1 ${x} ${y}`} fill="none" stroke={color} strokeWidth="6" strokeLinecap="round" />
        )}
        <text x={cx} y={cy - 5} textAnchor="middle" fill={color} fontSize="14" fontWeight="bold">{Math.round(score)}</text>
        <text x={cx} y={cy + 8} textAnchor="middle" fill="#94a3b8" fontSize="6">/100</text>
      </svg>
      <div className="text-[10px] font-bold text-slate-200 truncate mt-1" title={domain}>{domain}</div>
      <div className="text-[8px] text-slate-500">{companyCount} companies</div>
      {dossierSnippet && <div className="absolute top-1 right-1.5 text-[7px] text-amber-500/50">&#9670;</div>}

      {/* Observatory dossier tooltip — Section 1: Domain Reality Check */}
      {showTooltip && dossierSnippet && (
        <div className="fixed z-[200] left-1/2 top-1/2 -translate-x-1/2 -translate-y-1/2 w-[520px] max-h-[70vh] bg-[#0f172a] border border-amber-500/30 rounded-xl shadow-2xl overflow-hidden pointer-events-none">
          <div className="px-5 py-3 bg-amber-500/10 border-b border-amber-500/20 flex items-center gap-2">
            <FileText size={12} className="text-amber-400" />
            <span className="text-[10px] font-bold text-amber-300 uppercase tracking-wider">Domain Reality Check — {domain}</span>
          </div>
          <div className="px-5 py-4 overflow-y-auto max-h-[calc(70vh-48px)]">
            {dossierSnippet.split(/\n\n+/).map((block: string, bi: number) => {
              const trimmed = block.trim();
              if (!trimmed) return null;
              if (/^-{3,}$/.test(trimmed)) return null;
              if (trimmed.startsWith('### ')) return <h5 key={bi} className="text-xs font-bold text-amber-200/80 mt-4 mb-1">{trimmed.replace(/^###\s*/, '')}</h5>;
              const parts = trimmed.split(/(\*\*[^*]+\*\*)/);
              return (
                <p key={bi} className="text-[11px] text-slate-300 leading-relaxed mb-3">
                  {parts.map((seg: string, j: number) => {
                    if (seg.startsWith('**') && seg.endsWith('**')) {
                      return <strong key={j} className="text-slate-100 font-semibold">{seg.slice(2, -2)}</strong>;
                    }
                    return <span key={j}>{seg}</span>;
                  })}
                </p>
              );
            })}
          </div>
          <div className="px-5 py-2 border-t border-white/5 text-[8px] text-slate-600 italic">From Strategic Foresight Observatory — Layer 3</div>
        </div>
      )}
    </div>
  );
};

const CoverageRing = ({ coverage }: { coverage: any }) => {
  if (!coverage) return null;
  const pct = coverage.pct || 0;
  const circumference = 2 * Math.PI * 36;
  const offset = circumference - (pct / 100) * circumference;
  const color = pct >= 70 ? "#10b981" : pct >= 40 ? "#f59e0b" : "#f43f5e";

  return (
    <div className="bg-black/20 border border-white/10 rounded-lg p-4 flex flex-col items-center">
      <div className="text-[10px] font-bold text-cyan-300 uppercase tracking-wide mb-2">Foresight Coverage</div>
      <svg viewBox="0 0 80 80" className="w-24 h-24">
        <circle cx="40" cy="40" r="36" fill="none" stroke="#334155" strokeWidth="5" />
        <circle cx="40" cy="40" r="36" fill="none" stroke={color} strokeWidth="5" strokeLinecap="round"
          strokeDasharray={circumference} strokeDashoffset={offset}
          transform="rotate(-90 40 40)" />
        <text x="40" y="36" textAnchor="middle" fill="white" fontSize="14" fontWeight="bold">{coverage.covered}</text>
        <text x="40" y="48" textAnchor="middle" fill="#94a3b8" fontSize="8">/{coverage.total}</text>
      </svg>
      <div className="text-[9px] text-slate-400 mt-1">themes with 3+ signals</div>
      <div className="text-[8px] text-slate-500 mt-2 leading-relaxed text-left px-1">How many of the 30 strategic foresight themes have sufficient evidence (3+ validated signals). Higher coverage means broader market visibility.</div>
    </div>
  );
};

const ExecutiveSummaryTab = ({ data, observatoryData = null }: { data: any; observatoryData?: any }) => {
  // Build a map of domain → Section 1 (Domain Reality Check) full text from observatory data
  const dossierSnippets = useMemo(() => {
    if (!observatoryData?.domain_dossiers) return {};
    const map: Record<string, string> = {};
    for (const d of observatoryData.domain_dossiers) {
      if (!d.text || !d.domain) continue;
      // Extract Section 1 content — everything between "Section 1" header and "Section 2" header
      const section1Match = d.text.match(/##\s*Section\s*1[^\n]*\n([\s\S]*?)(?=##\s*Section\s*2|$)/i);
      if (section1Match) {
        // Clean up: remove markdown bold markers, trim, skip horizontal rules
        let section = section1Match[1].trim()
          .replace(/^-{3,}\s*/gm, '')
          .trim();
        if (section.length > 0) {
          map[d.domain] = section;
        }
      }
    }
    return map;
  }, [observatoryData]);

  return (
    <div className="space-y-6">
      {/* Market Health Gauges */}
      <div>
        <h4 className="text-[10px] font-bold text-emerald-300 uppercase tracking-wide mb-1 flex items-center gap-2">
          <Activity size={12} /> Market Health by Domain
        </h4>
        <div className="text-[8px] text-slate-500 mb-3 leading-relaxed">Composite health score per industry domain based on average reality scores, intent-reality gaps, and archetype distribution. Higher = healthier market segment. Low scores indicate domains with widespread modernization needs.</div>
        <div className="grid grid-cols-2 sm:grid-cols-3 md:grid-cols-4 lg:grid-cols-5 gap-3">
          {(data.market_health || []).map((h: any) => (
            <HealthGauge key={h.domain} domain={h.domain} score={h.score} companyCount={h.company_count} dossierSnippet={dossierSnippets[h.domain] || null} />
          ))}
        </div>
      </div>

      <div className="grid grid-cols-1 md:grid-cols-3 gap-4">
        {/* Foresight Coverage */}
        <CoverageRing coverage={data.foresight_coverage} />

        {/* Demand Intensity Bars */}
        <div className="md:col-span-2 bg-black/20 border border-white/10 rounded-lg p-4">
          <div className="text-[10px] font-bold text-amber-300 uppercase tracking-wide mb-3">Demand Intensity (avg gap per dimension)</div>
          {ResponsiveContainer && BarChart ? (
            <ResponsiveContainer width="100%" height={200}>
              <BarChart data={data.demand_intensity || []} layout="vertical" margin={{ left: 80, right: 20, top: 5, bottom: 5 }}>
                <CartesianGrid strokeDasharray="3 3" stroke="#1e293b" />
                <XAxis type="number" tick={{ fill: '#94a3b8', fontSize: 10 }} />
                <YAxis type="category" dataKey="dimension" tick={{ fill: '#cbd5e1', fontSize: 10 }} width={75} />
                <Tooltip contentStyle={{ background: '#0f172a', border: '1px solid #334155', borderRadius: 8, fontSize: 11, color: '#e2e8f0' }} labelStyle={{ color: '#f1f5f9', fontWeight: 600 }} itemStyle={{ color: '#e2e8f0' }} />
                <Bar dataKey="avg_gap" radius={[0, 4, 4, 0]} name="Avg Gap">
                  {(data.demand_intensity || []).map((entry: any, idx: number) => (
                    <Cell key={idx} fill={entry.avg_gap >= 0 ? '#10b981' : '#f43f5e'} />
                  ))}
                </Bar>
              </BarChart>
            </ResponsiveContainer>
          ) : (
            <div className="text-slate-500 text-xs">Charts unavailable</div>
          )}
          <div className="text-[8px] text-slate-500 mt-2 leading-relaxed">Average gap between what companies claim (intent) and what evidence shows (reality) per KB dimension. Negative values = market-wide gap where companies underperform their ambitions — the bigger the bar, the stronger the consulting demand signal.</div>
        </div>
      </div>

      {/* Quick stats row */}
      <div className="grid grid-cols-2 md:grid-cols-4 gap-3">
        <div className="bg-black/20 border border-white/10 rounded-lg p-3 text-center">
          <div className="text-2xl font-bold text-white">{data.company_count}</div>
          <div className="text-[9px] text-slate-400 uppercase">Total Companies</div>
          <div className="text-[8px] text-slate-500 mt-1 leading-relaxed">Companies scanned and included in this market intelligence analysis</div>
        </div>
        <div className="bg-black/20 border border-white/10 rounded-lg p-3 text-center">
          <div className="text-2xl font-bold text-cyan-400">{data.detailed_count}</div>
          <div className="text-[9px] text-slate-400 uppercase">With Full Data</div>
          <div className="text-[8px] text-slate-500 mt-1 leading-relaxed">Companies with complete scan data (foresight, financials, and KB scores)</div>
        </div>
        <div className="bg-black/20 border border-white/10 rounded-lg p-3 text-center">
          <div className="text-2xl font-bold text-emerald-400">{(data.top_foresights || []).length}</div>
          <div className="text-[9px] text-slate-400 uppercase">Top Foresight Themes</div>
          <div className="text-[8px] text-slate-500 mt-1 leading-relaxed">Most frequently detected megatrends creating strategic pressure across the market</div>
        </div>
        <div className="bg-black/20 border border-white/10 rounded-lg p-3 text-center">
          <div className="text-2xl font-bold text-rose-400">{(data.vulnerable_companies || []).length}</div>
          <div className="text-[9px] text-slate-400 uppercase">Vulnerable Companies</div>
          <div className="text-[8px] text-slate-500 mt-1 leading-relaxed">Companies with high intent-reality gap — strongest sales opportunities</div>
        </div>
      </div>

      {/* FAVI Segment Summary (mini preview) */}
      {data.favi_segments && (
        <div className="bg-black/20 border border-white/10 rounded-lg p-4">
          <h4 className="text-[10px] font-bold text-violet-300 uppercase tracking-wide mb-1 flex items-center gap-2">
            <Eye size={12} /> Foresight-Adjusted Vulnerability Index
          </h4>
          <div className="text-[8px] text-slate-500 mb-3 leading-relaxed">Companies segmented by current vulnerability (today's gaps) vs. foresight pressure (future forces). This 2×2 matrix reveals who needs help now, who will need it soon, and where the highest-value opportunities lie.</div>
          <div className="grid grid-cols-2 md:grid-cols-4 gap-3">
            {(["Critical", "Sleepwalking", "Wounded but Sheltered", "Safe Harbor"] as const).map(seg => {
              const cfg = SEGMENT_CONFIG[seg];
              const count = data.favi_segments[seg] || 0;
              return (
                <div key={seg} className={`${cfg.bg} border ${cfg.border} rounded-lg p-3 text-center`}>
                  <div className={`text-2xl font-bold ${cfg.color}`}>{count}</div>
                  <div className={`text-[9px] font-bold ${cfg.color} uppercase`}>{seg}</div>
                  <div className="text-[8px] text-slate-500 mt-1">{cfg.description}</div>
                </div>
              );
            })}
          </div>
        </div>
      )}

      {/* Observatory Layer 2 — Section 3: Three Horizons for Finland */}
      {(() => {
        if (!observatoryData?.finland_analysis?.text) return null;
        const fullText = observatoryData.finland_analysis.text;
        const s3Match = fullText.match(/##\s*Section\s*3[^\n]*\n([\s\S]*?)(?=##\s*Section\s*4|$)/i);
        if (!s3Match) return null;
        const section3 = s3Match[1].trim().replace(/^-{3,}\s*/gm, '').trim();
        if (!section3) return null;
        const renderBlocks = (text: string) => text.split(/\n\n+/).map((block: string, bi: number) => {
          const trimmed = block.trim();
          if (!trimmed) return null;
          if (/^-{3,}$/.test(trimmed)) return null;
          if (trimmed.startsWith('### ')) return <h5 key={bi} className="text-xs font-bold text-violet-200/80 mt-3 mb-1">{trimmed.replace(/^###\s*/, '')}</h5>;
          if (trimmed.startsWith('## ')) return <h5 key={bi} className="text-sm font-bold text-violet-200 mt-3 mb-1">{trimmed.replace(/^##\s*/, '')}</h5>;
          const parts = trimmed.replace(/\n/g, ' ').split(/(\*\*[^*]+\*\*)/);
          return (
            <p key={bi} className="text-[11px] text-slate-300 leading-relaxed">
              {parts.map((seg: string, j: number) => {
                if (seg.startsWith('**') && seg.endsWith('**')) {
                  return <strong key={j} className="text-slate-100 font-semibold">{seg.slice(2, -2)}</strong>;
                }
                return <span key={j}>{seg}</span>;
              })}
            </p>
          );
        });
        return (
          <div className="bg-black/20 border border-violet-500/20 rounded-lg p-5">
            <h4 className="text-[10px] font-bold text-violet-300 uppercase tracking-wide mb-1 flex items-center gap-2">
              <Globe size={12} /> Three Horizons for Finland
            </h4>
            <div className="text-[8px] text-slate-500 mb-4 leading-relaxed">From Strategic Foresight Observatory — Where Finland is heading across three timeframes</div>
            <div className="space-y-3">
              {renderBlocks(section3)}
            </div>
          </div>
        );
      })()}

      {/* Observatory Layer 2 — Section 4: The Contrarian Signal */}
      {(() => {
        if (!observatoryData?.finland_analysis?.text) return null;
        const fullText = observatoryData.finland_analysis.text;
        const s4Match = fullText.match(/##\s*Section\s*4[^\n]*\n([\s\S]*?)(?=##\s*Section\s*5|$)/i);
        if (!s4Match) return null;
        const section4 = s4Match[1].trim().replace(/^-{3,}\s*/gm, '').trim();
        if (!section4) return null;
        const renderBlocks = (text: string) => text.split(/\n\n+/).map((block: string, bi: number) => {
          const trimmed = block.trim();
          if (!trimmed) return null;
          if (/^-{3,}$/.test(trimmed)) return null;
          if (trimmed.startsWith('### ')) return <h5 key={bi} className="text-xs font-bold text-rose-200/80 mt-3 mb-1">{trimmed.replace(/^###\s*/, '')}</h5>;
          if (trimmed.startsWith('## ')) return <h5 key={bi} className="text-sm font-bold text-rose-200 mt-3 mb-1">{trimmed.replace(/^##\s*/, '')}</h5>;
          const parts = trimmed.replace(/\n/g, ' ').split(/(\*\*[^*]+\*\*)/);
          return (
            <p key={bi} className="text-[11px] text-slate-300 leading-relaxed">
              {parts.map((seg: string, j: number) => {
                if (seg.startsWith('**') && seg.endsWith('**')) {
                  return <strong key={j} className="text-rose-100 font-semibold">{seg.slice(2, -2)}</strong>;
                }
                return <span key={j}>{seg}</span>;
              })}
            </p>
          );
        });
        return (
          <div className="bg-black/20 border border-rose-500/20 rounded-lg p-5">
            <h4 className="text-[10px] font-bold text-rose-300 uppercase tracking-wide mb-1 flex items-center gap-2">
              <AlertTriangle size={12} /> The Contrarian Signal: Finland's Dangerous Assumption
            </h4>
            <div className="text-[8px] text-slate-500 mb-4 leading-relaxed">From Strategic Foresight Observatory — The uncomfortable truth the data reveals</div>
            <div className="space-y-3">
              {renderBlocks(section4)}
            </div>
          </div>
        );
      })()}
    </div>
  );
};

// ═══════════════════════════════════════════════════════════════════════════════
// TAB 2: FUTURE DEMAND
// ═══════════════════════════════════════════════════════════════════════════════

// Parse Cross-Impact Matrix Interpretation section markers into structured blocks
// Markers: [SECTION: ...], [THEME: ...] [TOTAL: ...] [SHAPE: ...], [CELL: ...] [DENSITY: ...] [RANK: ...]
type CIBlock =
  | { kind: 'section'; title: string }
  | { kind: 'theme'; name: string; total?: string; shape?: string }
  | { kind: 'cell'; theme: string; dimension: string; density?: string; rank?: string }
  | { kind: 'paragraph'; text: string };

// Section titles that are meta/instructional rather than analytical. Rendered
// in a muted style so the analytical sections stand out.
const isMetaSection = (title: string) =>
  /^\s*(how\s+to\s+use|sources)\b/i.test(title || '');

// Within VERTICAL / HORIZONTAL sections, the second paragraph that follows
// each [THEME:] or [CELL:] subsection carries the interpretive payoff and is
// rendered bold to make the key takeaway scannable.
const computeEmphasisParagraphs = (blocks: CIBlock[]): Set<number> => {
  const out = new Set<number>();
  let inAnalyticalSection = false;
  let paraSinceSubblock = 0;
  let inSubblock = false;
  blocks.forEach((b, i) => {
    if (b.kind === 'section') {
      inAnalyticalSection = /VERTICAL|HORIZONTAL/i.test(b.title || '');
      paraSinceSubblock = 0;
      inSubblock = false;
    } else if (b.kind === 'theme' || b.kind === 'cell') {
      paraSinceSubblock = 0;
      inSubblock = inAnalyticalSection;
    } else if (b.kind === 'paragraph') {
      if (inSubblock) {
        paraSinceSubblock += 1;
        if (paraSinceSubblock === 2) out.add(i);
      }
    }
  });
  return out;
};

const parseCrossImpactInterpretation = (text: string): CIBlock[] => {
  const blocks: CIBlock[] = [];
  if (!text) return blocks;
  const lines = text.split(/\n+/);
  for (const raw of lines) {
    const line = raw.trim();
    if (!line) continue;

    const sectionMatch = line.match(/^\[\s*SECTION\s*:\s*(.+?)\s*\]$/i);
    if (sectionMatch) {
      blocks.push({ kind: 'section', title: sectionMatch[1] });
      continue;
    }

    // [THEME: name] [TOTAL: n] [SHAPE: shape] — all on one line per the prompt
    if (/^\[\s*THEME\s*:/i.test(line)) {
      const theme = line.match(/\[\s*THEME\s*:\s*([^\]]+?)\s*\]/i)?.[1] || '';
      const total = line.match(/\[\s*TOTAL\s*:\s*([^\]]+?)\s*\]/i)?.[1];
      const shape = line.match(/\[\s*SHAPE\s*:\s*([^\]]+?)\s*\]/i)?.[1];
      blocks.push({ kind: 'theme', name: theme, total, shape });
      continue;
    }

    // [CELL: theme x dimension] [DENSITY: n] [RANK: 1|2|3]
    if (/^\[\s*CELL\s*:/i.test(line)) {
      const cellRaw = line.match(/\[\s*CELL\s*:\s*([^\]]+?)\s*\]/i)?.[1] || '';
      const density = line.match(/\[\s*DENSITY\s*:\s*([^\]]+?)\s*\]/i)?.[1];
      const rank = line.match(/\[\s*RANK\s*:\s*([^\]]+?)\s*\]/i)?.[1];
      const [theme, dimension] = cellRaw.split(/\s+[x×]\s+/i);
      blocks.push({ kind: 'cell', theme: theme || cellRaw, dimension: dimension || '', density, rank });
      continue;
    }

    blocks.push({ kind: 'paragraph', text: line });
  }
  return blocks;
};

const FutureDemandTab = ({ data, observatoryData = null, crossImpactInterpretationData = null, onRefreshCrossImpactInterpretation, crossImpactInterpretationLoading = false }: { data: any; observatoryData?: any; crossImpactInterpretationData?: any; onRefreshCrossImpactInterpretation?: () => void; crossImpactInterpretationLoading?: boolean }) => {
  const foresights = data.top_foresights || [];
  const crossImpact = data.cross_impact || [];
  const industryRadar = data.industry_radar || [];

  // Build cumulative % for Pareto, plus each bar's share of the grand total.
  const totalCount = foresights.reduce((s: number, f: any) => s + f.count, 0) || 1;
  let cumulative = 0;
  const paretoData = foresights.map((f: any) => {
    cumulative += f.count;
    return {
      ...f,
      cumPct: Math.round((cumulative / totalCount) * 100),
      pct: Math.round((f.count / totalCount) * 1000) / 10,
    };
  });

  // Build cross-impact matrix data
  const ciThemes = [...new Set(crossImpact.map((c: any) => c.theme))];
  const ciDimensions = ["Operating Model", "Service Portfolio", "Values", "Strategy", "AI Readiness"];
  const ciMatrix: Record<string, Record<string, number>> = {};
  for (const item of crossImpact) {
    if (!ciMatrix[item.theme]) ciMatrix[item.theme] = {};
    ciMatrix[item.theme][item.dimension] = item.count;
  }
  const maxCICount = Math.max(...crossImpact.map((c: any) => c.count), 1);

  // Per-row totals (theme density) and per-column totals (dimension density),
  // and the matrix-wide grand total. Used as denominators for the percentages
  // shown next to the [TOTAL: N] and [DENSITY: N] chips in the interpretation,
  // and as the basis for highlighting the top-3 rows / cols / cells in the matrix.
  const ciThemeTotals: Record<string, number> = {};
  for (const theme of ciThemes as string[]) {
    ciThemeTotals[theme] = ciDimensions.reduce((s, d) => s + (ciMatrix[theme]?.[d] || 0), 0);
  }
  const ciDimensionTotals: Record<string, number> = {};
  for (const dim of ciDimensions) {
    ciDimensionTotals[dim] = (ciThemes as string[]).reduce((s, t) => s + (ciMatrix[t]?.[dim] || 0), 0);
  }
  const ciMatrixTotal = (ciThemes as string[]).reduce((s, t) => s + (ciThemeTotals[t] || 0), 0) || 1;

  // Top-3 rankings for visual emphasis in the matrix table.
  const ciTopThemes = (ciThemes as string[])
    .slice() // already ordered by row total in ciThemes
    .sort((a, b) => (ciThemeTotals[b] || 0) - (ciThemeTotals[a] || 0))
    .slice(0, 3);
  const ciTopThemeRank: Record<string, number> = {};
  ciTopThemes.forEach((t, i) => { ciTopThemeRank[t] = i + 1; });

  const ciTopDimensions = ciDimensions
    .slice()
    .sort((a, b) => (ciDimensionTotals[b] || 0) - (ciDimensionTotals[a] || 0))
    .slice(0, 3);
  const ciTopDimensionRank: Record<string, number> = {};
  ciTopDimensions.forEach((d, i) => { ciTopDimensionRank[d] = i + 1; });

  const ciTopCells: Array<{ theme: string; dimension: string; count: number }> = [...crossImpact]
    .sort((a: any, b: any) => b.count - a.count)
    .slice(0, 3);
  const ciTopCellSet = new Set(ciTopCells.map(c => `${c.theme}|||${c.dimension}`));

  // Radar data: transform for Recharts
  const categories = Object.keys(CATEGORY_SHORT);
  const radarData = categories.map(cat => {
    const point: any = { category: CATEGORY_SHORT[cat] };
    industryRadar.forEach((ir: any) => {
      point[ir.domain] = ir.normalized_scores[cat] || 0;
    });
    return point;
  });

  return (
    <div className="space-y-6">
      {/* TOP10 Foresights Pareto */}
      <div className="bg-black/20 border border-white/10 rounded-lg p-4">
        <h4 className="text-[10px] font-bold text-cyan-300 uppercase tracking-wide mb-3">TOP {foresights.length} Foresight Themes (Pareto)</h4>
        {paretoData.length > 0 && ComposedChart ? (
          <ResponsiveContainer width="100%" height={300}>
            <ComposedChart data={paretoData} margin={{ left: 10, right: 30, top: 10, bottom: 60 }}>
              <CartesianGrid strokeDasharray="3 3" stroke="#1e293b" />
              <XAxis dataKey="theme" tick={{ fill: '#94a3b8', fontSize: 8 }} angle={-35} textAnchor="end" interval={0} height={80} />
              <YAxis yAxisId="count" tick={{ fill: '#94a3b8', fontSize: 10 }} />
              <YAxis yAxisId="pct" orientation="right" tick={{ fill: '#94a3b8', fontSize: 10 }} domain={[0, 100]} unit="%" />
              <Tooltip contentStyle={{ background: '#0f172a', border: '1px solid #334155', borderRadius: 8, fontSize: 11, color: '#e2e8f0' }} labelStyle={{ color: '#f1f5f9', fontWeight: 600 }} itemStyle={{ color: '#e2e8f0' }}
                formatter={(value: any, name: string, props: any) => {
                  if (name === 'Signals') {
                    const pct = props?.payload?.pct;
                    return [pct !== undefined ? `${value} (${pct}% of all signals)` : value, 'Signals'];
                  }
                  if (name === 'Cumulative %') {
                    return [`${value}%`, 'Cumulative'];
                  }
                  return [value, name];
                }} />
              <Bar yAxisId="count" dataKey="count" name="Signals" radius={[4, 4, 0, 0]}>
                {paretoData.map((entry: any, i: number) => (
                  <Cell key={i} fill={CATEGORY_COLORS[entry.category] || '#6366f1'} />
                ))}
              </Bar>
              <Line yAxisId="pct" dataKey="cumPct" stroke="#f43f5e" strokeWidth={2} dot={{ r: 3 }} name="Cumulative %" />
            </ComposedChart>
          </ResponsiveContainer>
        ) : (
          <div className="text-slate-500 text-xs py-8 text-center">No foresight data available</div>
        )}

        {/* Theme descriptions */}
        {paretoData.length > 0 && (
          <div className="mt-3 grid grid-cols-1 md:grid-cols-2 gap-1.5">
            {paretoData.map((f: any) => (
              <div key={f.theme} className="flex gap-2 text-[9px]">
                <span className="font-bold text-slate-300 whitespace-nowrap" style={{ color: CATEGORY_COLORS[f.category] || '#6366f1' }}>{f.theme}:</span>
                <span className="text-slate-500">{FORESIGHT_THEME_DESCRIPTIONS[f.theme] || f.category}</span>
              </div>
            ))}
          </div>
        )}
      </div>

      {/* Cross-Impact Matrix */}
      {ciThemes.length > 0 && (
        <div className="bg-black/20 border border-white/10 rounded-lg p-4 overflow-visible">
          <h4 className="text-[10px] font-bold text-violet-300 uppercase tracking-wide mb-1">Theme x Dimension Cross-Impact Matrix</h4>
          <p className="text-[10px] text-slate-500 mb-3">
            <span className="text-amber-300 font-semibold">#1-#3 next to a theme</span> = the three themes generating the most foresight pressure across all five dimensions.
            <span className="text-emerald-300 font-semibold"> #1-#3 next to a dimension</span> = the three dimensions absorbing the most foresight pressure across all themes.
            Ringed cells = the three strongest individual theme x dimension intersections.
          </p>
          <table className="w-full text-[10px]">
            <thead>
              <tr>
                <th className="text-left text-slate-400 p-1 font-medium">Theme</th>
                {ciDimensions.map(d => {
                  const dimRank = ciTopDimensionRank[d];
                  const isTopDim = !!dimRank;
                  return (
                    <th
                      key={d}
                      className={`text-center p-1 whitespace-nowrap relative group cursor-help ${isTopDim ? 'text-emerald-200 font-bold' : 'text-slate-400 font-medium'}`}
                    >
                      <span className={`border-b border-dashed ${isTopDim ? 'border-emerald-400/60' : 'border-slate-500'}`}>{d}</span>
                      {isTopDim && (
                        <span className="ml-1 align-middle inline-block px-1 py-0 rounded bg-emerald-500/15 border border-emerald-500/30 text-emerald-200 font-mono text-[8px]">
                          #{dimRank}
                        </span>
                      )}
                      {DIMENSION_DESCRIPTIONS[d] && (
                        <div className="absolute z-[100] hidden group-hover:block bg-slate-900 border border-slate-600 rounded-lg p-3 text-left text-[10px] text-slate-300 font-normal w-[280px] -translate-x-1/2 left-1/2 top-full mt-1 shadow-xl leading-relaxed whitespace-normal pointer-events-none">
                          <div className="font-bold text-cyan-300 mb-1">
                            {d}
                            {isTopDim && (
                              <span className="ml-2 text-emerald-300 font-normal">#{dimRank} dimension absorbing the most foresight pressure across all themes (column total {ciDimensionTotals[d]})</span>
                            )}
                          </div>
                          {DIMENSION_DESCRIPTIONS[d]}
                        </div>
                      )}
                    </th>
                  );
                })}
              </tr>
            </thead>
            <tbody>
              {(ciThemes as string[]).map((theme: string, themeIdx: number) => {
                const themeRank = ciTopThemeRank[theme];
                const isTopTheme = !!themeRank;
                return (
                  <tr key={theme} className={`border-t border-white/5 ${isTopTheme ? 'bg-amber-500/5' : ''}`}>
                    <td className={`p-1 whitespace-nowrap ${isTopTheme ? 'text-amber-100 font-bold border-l-2 border-amber-400/60 pl-2' : 'text-slate-300'}`}>
                      {isTopTheme && (
                        <span className="mr-1.5 inline-block px-1 py-0 rounded bg-amber-500/15 border border-amber-500/30 text-amber-200 font-mono text-[8px] align-middle">
                          #{themeRank}
                        </span>
                      )}
                      {theme}
                    </td>
                    {ciDimensions.map(dim => {
                      const val = ciMatrix[theme]?.[dim] || 0;
                      const intensity = val / maxCICount;
                      const bg = val > 0
                        ? `rgba(99, 102, 241, ${0.15 + intensity * 0.6})`
                        : 'transparent';
                      const showBelow = themeIdx < 3;
                      const isTopCell = ciTopCellSet.has(`${theme}|||${dim}`);
                      return (
                        <td
                          key={dim}
                          className={`p-1 text-center font-mono text-slate-200 relative group cursor-help ${isTopCell ? 'ring-1 ring-amber-400/70 ring-inset font-bold text-amber-100' : ''}`}
                          style={{ backgroundColor: bg }}
                        >
                          {val > 0 ? val : '-'}
                          {val > 0 && (
                            <div className={`absolute z-[100] hidden group-hover:block bg-slate-900 border border-slate-600 rounded-lg p-3 text-left text-[10px] text-slate-300 font-normal w-[260px] -translate-x-1/2 left-1/2 shadow-xl leading-relaxed whitespace-normal pointer-events-none ${showBelow ? 'top-full mt-1' : 'bottom-full mb-1'}`}>
                              <div className="font-bold text-cyan-300 mb-1">{theme} &times; {dim}</div>
                              <div className="text-slate-400">
                                <span className="text-white font-bold">{val}</span> foresight signal{val !== 1 ? 's' : ''} across scanned companies link the <span className="text-violet-300">{theme}</span> megatrend to the <span className="text-emerald-300">{dim}</span> dimension.
                              </div>
                              {isTopCell && (
                                <div className="mt-1.5 text-amber-300 text-[9px]">Top-3 highest-density cell in the matrix.</div>
                              )}
                              <div className="mt-1.5 text-slate-500 text-[9px]">Higher count = stronger cross-impact between this theme and dimension</div>
                            </div>
                          )}
                        </td>
                      );
                    })}
                  </tr>
                );
              })}
            </tbody>
          </table>
          <div className="mt-3 flex flex-wrap items-center gap-3 text-[9px] text-slate-500">
            <span className="flex items-center gap-1">
              <span className="inline-block w-2 h-2 rounded bg-amber-500/50 border border-amber-400/60"></span>
              Top-3 themes — most pressure generated (row total)
            </span>
            <span className="flex items-center gap-1">
              <span className="inline-block w-2 h-2 rounded bg-emerald-500/40 border border-emerald-400/60"></span>
              Top-3 dimensions — most pressure absorbed (column total)
            </span>
            <span className="flex items-center gap-1">
              <span className="inline-block w-2 h-2 rounded ring-1 ring-amber-400/70"></span>
              Top-3 highest-density cells
            </span>
          </div>
        </div>
      )}

      {/* CROSS-IMPACT MATRIX INTERPRETATION — placed directly under the matrix
          since it interprets the matrix above. Empty-state appears for admins
          when no interpretation has been generated yet. */}
      {!crossImpactInterpretationData?.text && onRefreshCrossImpactInterpretation && (
        <div className="mt-6 bg-black/20 border border-indigo-500/20 rounded-xl px-5 py-4 flex items-center justify-between gap-4">
          <div className="flex items-center gap-3">
            <div className="w-8 h-8 rounded-lg bg-indigo-500/20 flex items-center justify-center shrink-0">
              <BarChart3 size={16} className="text-indigo-400" />
            </div>
            <div>
              <h3 className="text-sm font-bold text-indigo-200">Cross-Impact Matrix Interpretation</h3>
              <p className="text-[10px] text-slate-500 mt-0.5">No interpretation generated yet. Run the analysis to produce a structured prose summary of the matrix.</p>
            </div>
          </div>
          <button
            onClick={onRefreshCrossImpactInterpretation}
            disabled={crossImpactInterpretationLoading}
            className="flex items-center gap-1.5 px-3 py-2 rounded-lg bg-indigo-500/20 hover:bg-indigo-500/30 border border-indigo-500/40 transition-colors text-[10px] font-mono text-indigo-200 hover:text-indigo-100 disabled:opacity-40 whitespace-nowrap"
            title="Generate the Cross-Impact Matrix Interpretation"
          >
            <RefreshCw size={12} className={crossImpactInterpretationLoading ? 'animate-spin' : ''} />
            {crossImpactInterpretationLoading ? 'Generating…' : 'Generate the Matrix Interpretation'}
          </button>
        </div>
      )}

      {crossImpactInterpretationData?.text && (() => {
        const blocks = parseCrossImpactInterpretation(crossImpactInterpretationData.text);
        const emphasisIdx = computeEmphasisParagraphs(blocks);
        return (
          <div className="mt-6 bg-black/20 rounded-xl border border-indigo-500/20 overflow-hidden">
            <div className="px-5 py-4 border-b border-indigo-500/10 flex items-center justify-between">
              <div className="flex items-center gap-3">
                <div className="w-8 h-8 rounded-lg bg-indigo-500/20 flex items-center justify-center">
                  <BarChart3 size={16} className="text-indigo-400" />
                </div>
                <div>
                  <h3 className="text-sm font-bold text-indigo-200">Cross-Impact Matrix Interpretation</h3>
                  <p className="text-[10px] text-slate-500 mt-0.5">Where structural pressure consolidates across the Theme x Dimension matrix</p>
                </div>
              </div>
              <div className="flex items-center gap-3">
                {crossImpactInterpretationData.generated_at && (
                  <span className="text-[9px] font-mono text-slate-600">
                    {new Date(crossImpactInterpretationData.generated_at).toLocaleDateString('en-FI', { day: 'numeric', month: 'short', year: 'numeric' })}
                  </span>
                )}
                {onRefreshCrossImpactInterpretation && (
                  <button
                    onClick={onRefreshCrossImpactInterpretation}
                    disabled={crossImpactInterpretationLoading}
                    className="flex items-center gap-1.5 px-2.5 py-1.5 rounded-lg bg-indigo-500/15 hover:bg-indigo-500/25 border border-indigo-500/30 transition-colors text-[9px] font-mono text-indigo-300 hover:text-indigo-100 disabled:opacity-40"
                    title="Regenerate the Cross-Impact Matrix Interpretation"
                  >
                    <RefreshCw size={11} className={crossImpactInterpretationLoading ? 'animate-spin' : ''} />
                    Refresh the Matrix Interpretation
                  </button>
                )}
              </div>
            </div>
            <div className="px-5 py-4">
              {blocks.map((b, i) => {
                if (b.kind === 'section') {
                  const meta = isMetaSection(b.title);
                  return (
                    <h4
                      key={i}
                      className={
                        meta
                          ? 'text-[11px] font-bold uppercase tracking-wider text-slate-400 mt-8 mb-3 border-b border-slate-700/40 pb-1.5'
                          : 'text-sm font-bold text-indigo-200 mt-6 mb-3 border-b border-indigo-500/10 pb-1.5 first:mt-0'
                      }
                    >
                      {b.title}
                    </h4>
                  );
                }
                if (b.kind === 'theme') {
                  const totalNum = b.total ? Number(b.total) : NaN;
                  const totalPct = Number.isFinite(totalNum) && ciMatrixTotal > 0
                    ? Math.round((totalNum / ciMatrixTotal) * 1000) / 10
                    : null;
                  // 5-segment distribution bar driven by the matrix data the model saw.
                  const themeRow = ciMatrix[b.name];
                  const rowTotal = ciThemeTotals[b.name] || 0;
                  const segmentColors: Record<string, string> = {
                    "Operating Model": "#6366f1",   // indigo
                    "Service Portfolio": "#10b981", // emerald
                    "Values": "#f59e0b",             // amber
                    "Strategy": "#06b6d4",           // cyan
                    "AI Readiness": "#a855f7",       // violet
                  };
                  return (
                    <div key={i} className="mt-4 mb-2">
                      <div className="flex flex-wrap items-center gap-2 text-[11px]">
                        <span className="font-bold text-indigo-100">{b.name}</span>
                        {b.total && (
                          <span
                            className="px-2 py-0.5 rounded bg-slate-800/60 border border-slate-700 text-slate-300 font-mono"
                            title={totalPct !== null ? `${totalPct}% of total matrix density (${ciMatrixTotal})` : undefined}
                          >
                            total {b.total}{totalPct !== null && <span className="text-slate-500"> ({totalPct}%)</span>}
                          </span>
                        )}
                        {b.shape && (
                          <span className="px-2 py-0.5 rounded bg-indigo-500/15 border border-indigo-500/30 text-indigo-200 font-mono uppercase tracking-wide text-[9px]">
                            {b.shape}
                          </span>
                        )}
                      </div>
                      {themeRow && rowTotal > 0 && (
                        <div className="mt-2 max-w-md">
                          <div className="h-2 flex rounded overflow-hidden bg-slate-800/60 border border-slate-700">
                            {ciDimensions.map(dim => {
                              const v = themeRow[dim] || 0;
                              const w = (v / rowTotal) * 100;
                              if (w <= 0) return null;
                              const pct = Math.round(w * 10) / 10;
                              return (
                                <div
                                  key={dim}
                                  className="h-full"
                                  style={{ width: `${w}%`, backgroundColor: segmentColors[dim] || '#64748b' }}
                                  title={`${dim}: density ${v} (${pct}% of row)`}
                                />
                              );
                            })}
                          </div>
                          <div className="mt-1 flex flex-wrap gap-x-3 gap-y-0.5 text-[9px] text-slate-500">
                            {ciDimensions.map(dim => {
                              const v = themeRow[dim] || 0;
                              if (v <= 0) return null;
                              const pct = Math.round((v / rowTotal) * 1000) / 10;
                              return (
                                <span key={dim} className="flex items-center gap-1">
                                  <span className="inline-block w-1.5 h-1.5 rounded-sm" style={{ backgroundColor: segmentColors[dim] || '#64748b' }} />
                                  {dim} {pct}%
                                </span>
                              );
                            })}
                          </div>
                        </div>
                      )}
                    </div>
                  );
                }
                if (b.kind === 'cell') {
                  const densityNum = b.density ? Number(b.density) : NaN;
                  const rowTotal = b.theme ? ciThemeTotals[b.theme] : 0;
                  const rowPct = Number.isFinite(densityNum) && rowTotal > 0
                    ? Math.round((densityNum / rowTotal) * 1000) / 10
                    : null;
                  return (
                    <div key={i} className="mt-4 mb-2 flex flex-wrap items-center gap-2 text-[11px]">
                      {b.rank && (
                        <span className="px-1.5 py-0.5 rounded bg-amber-500/15 border border-amber-500/30 text-amber-200 font-mono text-[9px]">
                          #{b.rank}
                        </span>
                      )}
                      <span className="font-bold text-indigo-100">{b.theme}</span>
                      {b.dimension && (
                        <>
                          <span className="text-slate-500">x</span>
                          <span className="font-bold text-emerald-200">{b.dimension}</span>
                        </>
                      )}
                      {b.density && (
                        <span
                          className="px-2 py-0.5 rounded bg-slate-800/60 border border-slate-700 text-slate-300 font-mono"
                          title={rowPct !== null ? `${rowPct}% of ${b.theme} row total (${rowTotal})` : undefined}
                        >
                          density {b.density}{rowPct !== null && <span className="text-slate-500"> ({rowPct}% of row)</span>}
                        </span>
                      )}
                    </div>
                  );
                }
                const isEmphasis = emphasisIdx.has(i);
                return (
                  <p
                    key={i}
                    className={
                      isEmphasis
                        ? 'text-[12px] text-indigo-100 font-semibold leading-relaxed mb-3'
                        : 'text-[12px] text-slate-300/90 leading-relaxed mb-3'
                    }
                  >
                    {b.text}
                  </p>
                );
              })}
            </div>
            {(crossImpactInterpretationData.sources_used?.length || crossImpactInterpretationData.model) && (
              <div className="px-5 py-2.5 border-t border-indigo-500/10 flex flex-wrap items-center gap-4 text-[9px] text-slate-600">
                {crossImpactInterpretationData.sources_used?.length > 0 && (
                  <span>Sources: {crossImpactInterpretationData.sources_used.join(' · ')}</span>
                )}
                {crossImpactInterpretationData.numeric_check && (
                  <span>
                    Numeric check: {crossImpactInterpretationData.numeric_check.passed ? 'passed' : `${(crossImpactInterpretationData.numeric_check.fabricated || []).length} flagged`}
                  </span>
                )}
                {crossImpactInterpretationData.model && <span>Model: {crossImpactInterpretationData.model}</span>}
              </div>
            )}
          </div>
        );
      })()}

      {/* C1 — Concentration vs Volume scatter (top-15 themes) */}
      {(ciThemes as string[]).length > 0 && ScatterChart && (() => {
        const SHAPE_COLORS: Record<string, string> = {
          CONCENTRATED: '#6366f1',
          BIMODAL: '#06b6d4',
          DIFFUSE: '#f59e0b',
          MIXED: '#64748b',
          SMALL_N: '#475569',
        };
        const points = (ciThemes as string[]).map((theme: string) => {
          const dims = ciDimensions.map(d => ciMatrix[theme]?.[d] || 0);
          const total = ciThemeTotals[theme] || 0;
          const sortedDesc = [...dims].sort((a, b) => b - a);
          const topShare = total > 0 ? sortedDesc[0] / total : 0;
          const top2Share = total > 0 ? (sortedDesc[0] + sortedDesc[1]) / total : 0;
          const dimCount = dims.filter(v => v > 0).length;
          const topDimIdx = dims.indexOf(sortedDesc[0]);
          const topDim = ciDimensions[topDimIdx] || '';
          let shape = 'MIXED';
          if (total < 8) shape = 'SMALL_N';
          else if (topShare >= 0.35 && top2Share >= 0.60) shape = 'CONCENTRATED';
          else if (top2Share >= 0.60) shape = 'BIMODAL';
          else if (topShare < 0.30 && top2Share < 0.55) shape = 'DIFFUSE';
          return {
            theme, total,
            topSharePct: Math.round(topShare * 1000) / 10,
            dimCount, topDim, shape,
          };
        });
        const grouped: Record<string, any[]> = {};
        for (const p of points) {
          (grouped[p.shape] ||= []).push(p);
        }
        return (
          <div className="bg-black/20 border border-white/10 rounded-lg p-4">
            <h4 className="text-[10px] font-bold text-indigo-300 uppercase tracking-wide mb-1">Concentration vs Volume — Top 15 Themes</h4>
            <p className="text-[10px] text-slate-500 mb-3">X = total density (volume of pressure). Y = share absorbed by the top dimension (concentration). Bubble size = number of dimensions touched.</p>
            <ResponsiveContainer width="100%" height={300}>
              <ScatterChart margin={{ top: 10, right: 20, bottom: 30, left: 10 }}>
                <CartesianGrid stroke="#334155" strokeDasharray="3 3" />
                <XAxis type="number" dataKey="total" name="total density" tick={{ fill: '#94a3b8', fontSize: 10 }} label={{ value: 'Total density', position: 'insideBottom', offset: -10, fill: '#94a3b8', fontSize: 10 }} />
                <YAxis type="number" dataKey="topSharePct" name="top-dim share %" domain={[0, 100]} tick={{ fill: '#94a3b8', fontSize: 10 }} unit="%" label={{ value: 'Top-dimension share', angle: -90, position: 'insideLeft', fill: '#94a3b8', fontSize: 10 }} />
                {ZAxis && <ZAxis type="number" dataKey="dimCount" range={[40, 220]} name="dimensions touched" />}
                <Tooltip
                  cursor={{ strokeDasharray: '3 3', stroke: '#475569' }}
                  contentStyle={{ background: '#0f172a', border: '1px solid #334155', borderRadius: 8, fontSize: 11, color: '#e2e8f0' }} labelStyle={{ color: '#f1f5f9', fontWeight: 600 }} itemStyle={{ color: '#e2e8f0' }}
                  content={({ active, payload }: any) => {
                    if (!active || !payload?.[0]) return null;
                    const p = payload[0].payload;
                    return (
                      <div className="bg-slate-900 border border-slate-600 rounded-lg p-2.5 text-[10px] text-slate-300 leading-relaxed">
                        <div className="font-bold text-indigo-200">{p.theme}</div>
                        <div>total density <span className="text-white font-mono">{p.total}</span></div>
                        <div>top dim: <span className="text-emerald-300">{p.topDim}</span> ({p.topSharePct}%)</div>
                        <div>dimensions touched: <span className="text-white font-mono">{p.dimCount}</span> of 5</div>
                        <div className="mt-1 inline-block px-1.5 py-0.5 rounded font-mono text-[9px]" style={{ backgroundColor: SHAPE_COLORS[p.shape] + '33', border: `1px solid ${SHAPE_COLORS[p.shape]}66`, color: SHAPE_COLORS[p.shape] }}>
                          {p.shape}
                        </div>
                      </div>
                    );
                  }}
                />
                {Object.entries(grouped).map(([shape, data]) => (
                  <Scatter key={shape} name={shape} data={data} fill={SHAPE_COLORS[shape] || '#64748b'} fillOpacity={0.75} stroke={SHAPE_COLORS[shape] || '#64748b'} />
                ))}
                <Legend wrapperStyle={{ fontSize: 9, color: '#94a3b8', paddingTop: 8 }} />
              </ScatterChart>
            </ResponsiveContainer>
          </div>
        );
      })()}

      {/* C2 — Domain x Dimension heatmap. Currently hidden: the Theme x Dimension
          matrix above offers stronger signal, and a domain-level lens is on hold
          until we revisit it. Code retained so we can re-enable without refactor. */}
      {false && (data.domain_dimension_matrix?.length || 0) > 0 && (() => {
        const ddRaw = data.domain_dimension_matrix as Array<{ domain: string; dimension: string; count: number }>;
        const ddDomains = [...new Set(ddRaw.map(c => c.domain))] as string[];
        const ddMatrix: Record<string, Record<string, number>> = {};
        for (const c of ddRaw) {
          if (!ddMatrix[c.domain]) ddMatrix[c.domain] = {};
          ddMatrix[c.domain][c.dimension] = c.count;
        }
        const ddMax = Math.max(...ddRaw.map(c => c.count), 1);
        const ddDomainTotals: Record<string, number> = {};
        for (const dom of ddDomains) {
          ddDomainTotals[dom] = ciDimensions.reduce((s, d) => s + (ddMatrix[dom]?.[d] || 0), 0);
        }
        return (
          <div className="bg-black/20 border border-white/10 rounded-lg p-4 overflow-visible">
            <h4 className="text-[10px] font-bold text-emerald-300 uppercase tracking-wide mb-1">Domain x Dimension — Where pressure lands by industry</h4>
            <p className="text-[10px] text-slate-500 mb-3">Same evidence base as the Theme x Dimension matrix above, grouped by industry domain instead of foresight theme.</p>
            <table className="w-full text-[10px]">
              <thead>
                <tr>
                  <th className="text-left text-slate-400 p-1 font-medium">Domain</th>
                  {ciDimensions.map(d => (
                    <th key={d} className="text-center text-slate-400 p-1 font-medium whitespace-nowrap">{d}</th>
                  ))}
                  <th className="text-center text-slate-500 p-1 font-medium">total</th>
                </tr>
              </thead>
              <tbody>
                {ddDomains.map(dom => (
                  <tr key={dom} className="border-t border-white/5">
                    <td className="p-1 text-slate-300 whitespace-nowrap">{dom}</td>
                    {ciDimensions.map(d => {
                      const v = ddMatrix[dom]?.[d] || 0;
                      const intensity = v / ddMax;
                      const bg = v > 0 ? `rgba(16, 185, 129, ${0.15 + intensity * 0.6})` : 'transparent';
                      return (
                        <td
                          key={d}
                          className="p-1 text-center font-mono text-slate-200"
                          style={{ backgroundColor: bg }}
                          title={v > 0 ? `${dom} x ${d}: density ${v}` : undefined}
                        >
                          {v > 0 ? v : '-'}
                        </td>
                      );
                    })}
                    <td className="p-1 text-center font-mono text-slate-500">{ddDomainTotals[dom]}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        );
      })()}

      {/* C3 — Theme Momentum bars */}
      {(observatoryData?.theme_momentum?.length || 0) > 0 && (() => {
        const tm = observatoryData.theme_momentum as Array<{ theme: string; accelerating: number; stable: number; decelerating: number; total: number }>;
        const top = tm.slice(0, 10);
        return (
          <div className="bg-black/20 border border-white/10 rounded-lg p-4">
            <h4 className="text-[10px] font-bold text-cyan-300 uppercase tracking-wide mb-1">Theme Momentum — Top 10 themes by signal volume</h4>
            <p className="text-[10px] text-slate-500 mb-3">Accelerating vs. stable vs. decelerating signals per theme, drawn from the Foresight Knowledge Base.</p>
            <div className="space-y-2">
              {top.map(row => {
                const tot = row.total || 1;
                const aPct = (row.accelerating / tot) * 100;
                const sPct = (row.stable / tot) * 100;
                const dPct = (row.decelerating / tot) * 100;
                return (
                  <div key={row.theme} className="grid grid-cols-[160px_1fr_64px] items-center gap-3 text-[10px]">
                    <div className="text-slate-300 truncate" title={row.theme}>{row.theme}</div>
                    <div className="h-3 flex rounded overflow-hidden bg-slate-800/60 border border-slate-700">
                      {aPct > 0 && <div style={{ width: `${aPct}%`, backgroundColor: '#10b981' }} title={`Accelerating: ${row.accelerating}`} />}
                      {sPct > 0 && <div style={{ width: `${sPct}%`, backgroundColor: '#64748b' }} title={`Stable: ${row.stable}`} />}
                      {dPct > 0 && <div style={{ width: `${dPct}%`, backgroundColor: '#f43f5e' }} title={`Decelerating: ${row.decelerating}`} />}
                    </div>
                    <div className="text-right font-mono text-slate-500">{row.total}</div>
                  </div>
                );
              })}
            </div>
            <div className="mt-3 flex flex-wrap items-center gap-3 text-[9px] text-slate-500">
              <span className="flex items-center gap-1">
                <span className="inline-block w-2 h-2 rounded" style={{ backgroundColor: '#10b981' }}></span> Accelerating
              </span>
              <span className="flex items-center gap-1">
                <span className="inline-block w-2 h-2 rounded" style={{ backgroundColor: '#64748b' }}></span> Stable
              </span>
              <span className="flex items-center gap-1">
                <span className="inline-block w-2 h-2 rounded" style={{ backgroundColor: '#f43f5e' }}></span> Decelerating
              </span>
            </div>
          </div>
        );
      })()}

      {/* FINNISH ECONOMY FORCE MAP — from Observatory */}
      {observatoryData?.finland_analysis?.text && (
        <div className="mt-6 bg-black/20 rounded-xl border border-violet-500/20 overflow-hidden">
          <div className="px-5 py-4 border-b border-violet-500/10 flex items-center justify-between">
            <div className="flex items-center gap-3">
              <div className="w-8 h-8 rounded-lg bg-violet-500/20 flex items-center justify-center">
                <Globe size={16} className="text-violet-400" />
              </div>
              <div>
                <h3 className="text-sm font-bold text-violet-200">Finnish Economy Force Map</h3>
                <p className="text-[10px] text-slate-500 mt-0.5">Strategic Foresight Observatory — How global forces reshape Finnish business</p>
              </div>
            </div>
            {observatoryData.generated_at && (
              <span className="text-[9px] font-mono text-slate-600">
                {new Date(observatoryData.generated_at).toLocaleDateString('en-FI', { day: 'numeric', month: 'short', year: 'numeric' })}
              </span>
            )}
          </div>
          <div className="px-5 py-4">
            {observatoryData.finland_analysis.text.split(/\n+/).map((part: string, i: number) => {
              const trimmed = part.trim();
              if (!trimmed) return null;
              if (trimmed === '---') return <hr key={i} className="border-violet-500/10 my-4" />;
              if (trimmed.startsWith("## ")) {
                return <h4 key={i} className="text-sm font-bold text-violet-200 mt-6 mb-3 border-b border-violet-500/10 pb-1.5">{trimmed.replace(/^##\s*/, "")}</h4>;
              }
              if (trimmed.startsWith("# ")) {
                return <h3 key={i} className="text-base font-bold text-violet-100 mt-6 mb-3">{trimmed.replace(/^#\s*/, "")}</h3>;
              }
              // Process markdown bold markers and auto-bold first sentence
              const processBold = (text: string) => {
                return text.split(/(\*\*[^*]+\*\*)/).map((seg: string, j: number) => {
                  if (seg.startsWith("**") && seg.endsWith("**")) {
                    return <strong key={j} className="text-violet-200 font-semibold">{seg.slice(2, -2)}</strong>;
                  }
                  return <span key={j}>{seg}</span>;
                });
              };
              // Auto-bold first sentence if no markdown bold exists
              const hasBold = /\*\*[^*]+\*\*/.test(trimmed);
              if (!hasBold) {
                const dotIdx = trimmed.indexOf('. ');
                if (dotIdx > 10 && dotIdx < 150) {
                  return (
                    <p key={i} className="text-[12px] text-slate-300/90 leading-relaxed mb-4">
                      <strong className="text-violet-200 font-semibold">{trimmed.slice(0, dotIdx + 1)}</strong>{' '}
                      {trimmed.slice(dotIdx + 2)}
                    </p>
                  );
                }
              }
              return <p key={i} className="text-[12px] text-slate-300/90 leading-relaxed mb-4">{processBold(trimmed)}</p>;
            })}
          </div>
          {/* Metadata footer */}
          <div className="px-5 py-2.5 border-t border-violet-500/10 flex items-center gap-4 text-[9px] text-slate-600">
            {observatoryData.finland_analysis.domains_analyzed && (
              <span>{observatoryData.finland_analysis.domains_analyzed} domains analyzed</span>
            )}
            {observatoryData.finland_analysis.favi_distribution && (
              <span>FAVI: {Object.entries(observatoryData.finland_analysis.favi_distribution).map(([k, v]: [string, any]) => `${k}: ${v}`).join(' · ')}</span>
            )}
            {observatoryData.model && <span>Model: {observatoryData.model}</span>}
          </div>
        </div>
      )}

    </div>
  );
};

// ═══════════════════════════════════════════════════════════════════════════════
// TAB 3: CURRENT DEMAND
// ═══════════════════════════════════════════════════════════════════════════════

const CurrentDemandTab = ({ data }: { data: any }) => {
  const causes = data.top_causes || [];
  const archetypeFlow = data.archetype_flow || [];

  return (
    <div className="space-y-6">
      {/* TOP10 Root Causes */}
      <div className="bg-black/20 border border-white/10 rounded-lg p-4">
        <h4 className="text-[10px] font-bold text-blue-300 uppercase tracking-wide mb-3">TOP {causes.length} Root Causes / Strategic Patterns</h4>
        {causes.length > 0 && BarChart ? (
          <ResponsiveContainer width="100%" height={300}>
            <BarChart data={causes} margin={{ left: 10, right: 20, top: 10, bottom: 60 }}>
              <CartesianGrid strokeDasharray="3 3" stroke="#1e293b" />
              <XAxis dataKey="cluster_label" tick={{ fill: '#94a3b8', fontSize: 8 }} angle={-30} textAnchor="end" interval={0} height={80} />
              <YAxis tick={{ fill: '#94a3b8', fontSize: 10 }} />
              <Tooltip cursor={false} active={false} contentStyle={{ display: 'none' }} />
              <Legend wrapperStyle={{ fontSize: 10 }} />
              <Bar dataKey="why_count" stackId="a" fill="#3b82f6" name="WHY (strategic)" radius={[0, 0, 0, 0]} />
              <Bar dataKey="what_count" stackId="a" fill="#14b8a6" name="WHAT (operational)" radius={[4, 4, 0, 0]} />
            </BarChart>
          </ResponsiveContainer>
        ) : (
          <div className="text-slate-500 text-xs py-8 text-center">No root cause data available</div>
        )}

        {/* Cluster descriptions */}
        {causes.length > 0 && (
          <div className="mt-3 grid grid-cols-1 md:grid-cols-2 gap-1.5">
            {causes.map((c: any) => (
              <div key={c.cluster_label} className="flex gap-2 text-[9px]">
                <span className="font-bold text-slate-300 whitespace-nowrap">{c.cluster_label}:</span>
                <span className="text-slate-500">{ROOT_CAUSE_DESCRIPTIONS[c.cluster_label] || ''}</span>
              </div>
            ))}
          </div>
        )}

        {/* Example proposals */}
        {causes.length > 0 && (
          <div className="mt-3 space-y-2">
            {causes.slice(0, 5).map((c: any) => (
              <div key={c.cluster_label} className="bg-black/30 rounded p-2">
                <div className="text-[10px] font-bold text-slate-200">{c.cluster_label} <span className="text-slate-500">({c.count} matches)</span></div>
                {(c.examples || []).slice(0, 5).map((ex: any, i: number) => (
                  <div key={i} className="text-[9px] text-slate-400 mt-1 flex gap-1">
                    <ChevronRight size={10} className="text-slate-600 shrink-0 mt-0.5" />
                    <span><span className="text-cyan-400">{ex.company}:</span> {ex.text}</span>
                  </div>
                ))}
              </div>
            ))}
          </div>
        )}
      </div>

      {/* Archetype Flow by Domain */}
      <div className="bg-black/20 border border-white/10 rounded-lg p-4">
        <h4 className="text-[10px] font-bold text-emerald-300 uppercase tracking-wide mb-3">Archetype Distribution by Domain</h4>
        {archetypeFlow.length > 0 && BarChart ? (
          <ResponsiveContainer width="100%" height={300}>
            <BarChart data={archetypeFlow} margin={{ left: 10, right: 20, top: 10, bottom: 60 }}>
              <CartesianGrid strokeDasharray="3 3" stroke="#1e293b" />
              <XAxis dataKey="domain" tick={{ fill: '#94a3b8', fontSize: 8 }} angle={-30} textAnchor="end" interval={0} height={80} />
              <YAxis tick={{ fill: '#94a3b8', fontSize: 10 }} />
              <Tooltip contentStyle={{ background: '#0f172a', border: '1px solid #334155', borderRadius: 8, fontSize: 11, color: '#e2e8f0' }} labelStyle={{ color: '#f1f5f9', fontWeight: 600 }} itemStyle={{ color: '#e2e8f0' }} />
              <Legend wrapperStyle={{ fontSize: 10 }} />
              <Bar dataKey="thriving" stackId="a" fill={ARCHETYPE_COLORS.thriving} name="Thriving" />
              <Bar dataKey="modernizing" stackId="a" fill={ARCHETYPE_COLORS.modernizing} name="Modernizing" />
              <Bar dataKey="cash_cow" stackId="a" fill={ARCHETYPE_COLORS.cash_cow} name="Cash Cow" />
              <Bar dataKey="at_risk" stackId="a" fill={ARCHETYPE_COLORS.at_risk} name="At Risk" radius={[4, 4, 0, 0]} />
            </BarChart>
          </ResponsiveContainer>
        ) : (
          <div className="text-slate-500 text-xs py-8 text-center">No archetype data available</div>
        )}
      </div>
    </div>
  );
};

// ═══════════════════════════════════════════════════════════════════════════════
// TAB 4: SALES PRIORITIES
// ═══════════════════════════════════════════════════════════════════════════════

const SalesPrioritiesTab = ({ data, onCompanyClick }: { data: any; onCompanyClick: (company: any) => void }) => {
  const [sortKey, setSortKey] = useState('vulnerability_score');
  const [sortDir, setSortDir] = useState('desc');
  const vulnerable = data.vulnerable_companies || [];

  const sorted = useMemo(() => {
    return [...vulnerable].sort((a: any, b: any) => {
      const va = a[sortKey] ?? 0;
      const vb = b[sortKey] ?? 0;
      return sortDir === 'desc' ? vb - va : va - vb;
    });
  }, [vulnerable, sortKey, sortDir]);

  const handleSort = (key: string) => {
    if (sortKey === key) {
      setSortDir(sortDir === 'desc' ? 'asc' : 'desc');
    } else {
      setSortKey(key);
      setSortDir('desc');
    }
  };

  const handleExportCSV = () => {
    if (sorted.length === 0) return;
    const headers = ['Rank', 'Company', 'Domain', 'Vulnerability Score', 'Residual', 'Gap', 'Archetype', 'Reasons'];
    const rows = sorted.map((v: any, i: number) => [
      i + 1, v.name, v.domain, v.vulnerability_score, v.residual, v.gap, v.archetype, (v.reasons || []).join('; ')
    ]);
    const csv = [headers, ...rows].map(r => r.map((c: any) => `"${String(c).replace(/"/g, '""')}"`).join(',')).join('\n');
    const blob = new Blob([csv], { type: 'text/csv' });
    const url = URL.createObjectURL(blob);
    const a = document.createElement('a');
    a.href = url;
    a.download = 'vulnerable_companies.csv';
    a.click();
    URL.revokeObjectURL(url);
  };

  const vulnColor = (score: number) => {
    if (score >= 60) return 'text-rose-400 bg-rose-500/10';
    if (score >= 30) return 'text-amber-400 bg-amber-500/10';
    return 'text-slate-400';
  };

  const SortHeader = ({ label, dataKey }: { label: string; dataKey: string }) => (
    <th className="p-2 text-left cursor-pointer hover:text-white select-none whitespace-nowrap" onClick={() => handleSort(dataKey)}>
      {label} {sortKey === dataKey ? (sortDir === 'desc' ? '\u2193' : '\u2191') : ''}
    </th>
  );

  return (
    <div className="space-y-6">
      {/* Vulnerability Table */}
      <div className="bg-black/20 border border-white/10 rounded-lg p-4">
        <div className="flex justify-between items-center mb-3">
          <h4 className="text-[10px] font-bold text-rose-300 uppercase tracking-wide flex items-center gap-2">
            <ShieldAlert size={12} /> Most Vulnerable Companies
          </h4>
          <button onClick={handleExportCSV} className="flex items-center gap-1 px-2 py-1 text-[10px] bg-white/5 hover:bg-white/10 border border-white/10 rounded text-slate-300">
            <Download size={10} /> CSV
          </button>
        </div>

        {sorted.length > 0 ? (
          <div className="overflow-x-auto">
            <table className="w-full text-[10px]">
              <thead>
                <tr className="text-slate-500 border-b border-white/10">
                  <th className="p-2 text-left">#</th>
                  <th className="p-2 text-left">Company</th>
                  <th className="p-2 text-left">Domain</th>
                  <SortHeader label="Vuln. Score" dataKey="vulnerability_score" />
                  <SortHeader label="Residual" dataKey="residual" />
                  <SortHeader label="Gap" dataKey="gap" />
                  <th className="p-2 text-left">Archetype</th>
                  <th className="p-2 text-left">Reason</th>
                </tr>
              </thead>
              <tbody>
                {sorted.map((v: any, i: number) => (
                  <tr key={v.name} className="border-t border-white/5 hover:bg-white/5 cursor-pointer transition-colors"
                    onClick={() => onCompanyClick({ companyName: v.name, domain: v.domain })}>
                    <td className="p-2 text-slate-500">{i + 1}</td>
                    <td className="p-2 text-cyan-300 font-medium">{v.name}</td>
                    <td className="p-2 text-slate-400">{v.domain}</td>
                    <td className={`p-2 font-mono font-bold ${vulnColor(v.vulnerability_score)}`}>
                      {v.vulnerability_score}
                    </td>
                    <td className={`p-2 font-mono ${v.residual < 0 ? 'text-rose-400' : 'text-emerald-400'}`}>
                      {v.residual > 0 ? '+' : ''}{v.residual}
                    </td>
                    <td className={`p-2 font-mono ${v.gap > 10 ? 'text-amber-400' : 'text-slate-400'}`}>
                      {v.gap}
                    </td>
                    <td className="p-2">
                      <span className={`px-1.5 py-0.5 rounded text-[9px] font-bold ${
                        v.archetype === 'At Risk' ? 'bg-rose-500/20 text-rose-300' :
                        v.archetype === 'Cash Cow' ? 'bg-blue-500/20 text-blue-300' :
                        v.archetype === 'Modernizing' ? 'bg-amber-500/20 text-amber-300' :
                        'bg-emerald-500/20 text-emerald-300'
                      }`}>{v.archetype}</span>
                    </td>
                    <td className="p-2 text-slate-500 max-w-[200px] truncate" title={(v.reasons || []).join(', ')}>
                      {(v.reasons || [])[0] || '-'}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        ) : (
          <div className="text-slate-500 text-xs py-8 text-center">No vulnerability data available</div>
        )}
      </div>

    </div>
  );
};

// ═══════════════════════════════════════════════════════════════════════════════
// TAB 5: FORESIGHT-ADJUSTED VULNERABILITY INDEX
// ═══════════════════════════════════════════════════════════════════════════════

const ForesightVulnerabilityTab = ({ data, onCompanyClick }: { data: any; onCompanyClick: (company: any) => void }) => {
  const [sortKey, setSortKey] = useState('favi_score');
  const [sortDir, setSortDir] = useState('desc');
  const [expandedCompany, setExpandedCompany] = useState<string | null>(null);

  const faviCompanies = data.favi_companies || [];
  const segments = data.favi_segments || {};
  const domainPressure = data.favi_domain_pressure || [];

  const sorted = useMemo(() => {
    return [...faviCompanies].sort((a: any, b: any) => {
      const va = a[sortKey] ?? 0;
      const vb = b[sortKey] ?? 0;
      return sortDir === 'desc' ? vb - va : va - vb;
    });
  }, [faviCompanies, sortKey, sortDir]);

  const handleSort = (key: string) => {
    if (sortKey === key) {
      setSortDir(sortDir === 'desc' ? 'asc' : 'desc');
    } else {
      setSortKey(key);
      setSortDir('desc');
    }
  };

  const handleExportCSV = () => {
    if (sorted.length === 0) return;
    const headers = ['Rank', 'Company', 'Domain', 'Foresight Vulnerability', 'Foresight Pressure', 'Readiness Gap', 'Adaptation Capacity', 'Current Vuln.', 'Segment', 'Formula'];
    const rows = sorted.map((v: any, i: number) => [
      i + 1, v.name, v.domain, v.favi_score, v.fps, v.readiness_gap, v.fac, v.current_vulnerability, v.segment, v.formula
    ]);
    const csv = [headers, ...rows].map(r => r.map((c: any) => `"${String(c).replace(/"/g, '""')}"`).join(',')).join('\n');
    const blob = new Blob([csv], { type: 'text/csv' });
    const url = URL.createObjectURL(blob);
    const a = document.createElement('a');
    a.href = url;
    a.download = 'favi_vulnerability_index.csv';
    a.click();
    URL.revokeObjectURL(url);
  };

  const SortHeader = ({ label, dataKey }: { label: string; dataKey: string }) => (
    <th className="p-2 text-left cursor-pointer hover:text-white select-none whitespace-nowrap" onClick={() => handleSort(dataKey)}>
      {label} {sortKey === dataKey ? (sortDir === 'desc' ? '\u2193' : '\u2191') : ''}
    </th>
  );

  const totalCompanies = Object.values(segments).reduce((s: number, v: any) => s + (v || 0), 0) || 1;

  return (
    <div className="space-y-6">
      {/* 2x2 Strategic Segment Matrix */}
      <div className="bg-black/20 border border-white/10 rounded-lg p-4">
        <h4 className="text-[10px] font-bold text-cyan-300 uppercase tracking-wide mb-4 flex items-center gap-2">
          <Eye size={12} /> Strategic Foresight Vulnerability Matrix (Current x Future)
        </h4>
        <div className="grid grid-cols-1 md:grid-cols-2 gap-px bg-white/10 rounded-lg overflow-hidden">
          {/* Top-left: Critical */}
          <div className={`p-4 ${SEGMENT_CONFIG["Critical"].bg}`}>
            <div className="flex items-center justify-between mb-2">
              <div className={`text-sm font-bold ${SEGMENT_CONFIG["Critical"].color}`}>Critical</div>
              <div className="text-2xl font-bold text-rose-400">{segments["Critical"] || 0}</div>
            </div>
            <div className="text-[9px] text-slate-400 mb-1">Scaled Current Vulnerability + High Foresight Vulnerability</div>
            <div className="text-[9px] text-rose-300/70">{SEGMENT_CONFIG["Critical"].description}</div>
            <div className="mt-2 h-1 rounded-full bg-white/5 overflow-hidden">
              <div className="h-full bg-rose-500 rounded-full" style={{ width: `${((segments["Critical"] || 0) / totalCompanies) * 100}%` }} />
            </div>
          </div>
          {/* Top-right: Wounded but Sheltered */}
          <div className={`p-4 ${SEGMENT_CONFIG["Wounded but Sheltered"].bg}`}>
            <div className="flex items-center justify-between mb-2">
              <div className={`text-sm font-bold ${SEGMENT_CONFIG["Wounded but Sheltered"].color}`}>Wounded but Sheltered</div>
              <div className="text-2xl font-bold text-blue-400">{segments["Wounded but Sheltered"] || 0}</div>
            </div>
            <div className="text-[9px] text-slate-400 mb-1">Scaled Current Vulnerability + Foresight Vulnerability</div>
            <div className="text-[9px] text-blue-300/70">{SEGMENT_CONFIG["Wounded but Sheltered"].description}</div>
            <div className="mt-2 h-1 rounded-full bg-white/5 overflow-hidden">
              <div className="h-full bg-blue-500 rounded-full" style={{ width: `${((segments["Wounded but Sheltered"] || 0) / totalCompanies) * 100}%` }} />
            </div>
          </div>
          {/* Bottom-left: Sleepwalking */}
          <div className={`p-4 ${SEGMENT_CONFIG["Sleepwalking"].bg}`}>
            <div className="flex items-center justify-between mb-2">
              <div className={`text-sm font-bold ${SEGMENT_CONFIG["Sleepwalking"].color}`}>Sleepwalking</div>
              <div className="text-2xl font-bold text-amber-400">{segments["Sleepwalking"] || 0}</div>
            </div>
            <div className="text-[9px] text-slate-400 mb-1">Scaled Current Vulnerability + High Foresight Vulnerability</div>
            <div className="text-[9px] text-amber-300/70">{SEGMENT_CONFIG["Sleepwalking"].description}</div>
            <div className="mt-2 h-1 rounded-full bg-white/5 overflow-hidden">
              <div className="h-full bg-amber-500 rounded-full" style={{ width: `${((segments["Sleepwalking"] || 0) / totalCompanies) * 100}%` }} />
            </div>
          </div>
          {/* Bottom-right: Safe Harbor */}
          <div className={`p-4 ${SEGMENT_CONFIG["Safe Harbor"].bg}`}>
            <div className="flex items-center justify-between mb-2">
              <div className={`text-sm font-bold ${SEGMENT_CONFIG["Safe Harbor"].color}`}>Safe Harbor</div>
              <div className="text-2xl font-bold text-emerald-400">{segments["Safe Harbor"] || 0}</div>
            </div>
            <div className="text-[9px] text-slate-400 mb-1">Scaled Current Vulnerability + Low Foresight Vulnerability</div>
            <div className="text-[9px] text-emerald-300/70">{SEGMENT_CONFIG["Safe Harbor"].description}</div>
            <div className="mt-2 h-1 rounded-full bg-white/5 overflow-hidden">
              <div className="h-full bg-emerald-500 rounded-full" style={{ width: `${((segments["Safe Harbor"] || 0) / totalCompanies) * 100}%` }} />
            </div>
          </div>
        </div>
        {/* Cutoffs surfaced from the backend so the user knows what split
            produced the four segment counts. Degrades silently when missing. */}
        {data.favi_cutoffs && (
          <div className="mt-3 text-[10px] text-slate-500 leading-relaxed">
            Cutoffs (
            {data.favi_cutoffs.source?.startsWith('percentile')
              ? data.favi_cutoffs.source.replace('percentile:', '').replace(/p(\d+(\.\d+)?)/g, '$1th percentile').replace(/,/g, ', ').replace('favi=', 'FAVI ').replace('current=', 'Current ')
              : 'fixed fallback'}
            ): FAVI &ge; <span className="font-mono text-slate-300">{data.favi_cutoffs.favi}</span>
            {' '}&middot; Current Vulnerability &ge; <span className="font-mono text-slate-300">{data.favi_cutoffs.current_vulnerability}</span>
          </div>
        )}
        {/* Matrix axis labels */}
        <div className="flex justify-between mt-3 text-[8px] text-slate-500 uppercase tracking-wider">
          <div className="flex items-center gap-1">
            <span className="inline-block w-8 h-px bg-slate-600" />
            Current Vulnerability (x-axis)
            <span className="inline-block w-8 h-px bg-slate-600" />
          </div>
          <div className="flex items-center gap-1">
            <span className="inline-block w-8 h-px bg-slate-600" />
            Foresight-Adjusted Vulnerability Index (y-axis)
            <span className="inline-block w-8 h-px bg-slate-600" />
          </div>
        </div>
      </div>

      {/* Domain Foresight Pressure + FAVI vs Current Vuln Scatter side by side */}
      <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
        {/* Domain Foresight Pressure — hidden in favor of the Adaptability x FAVI
            scatter below. Code retained so we can flip the flag back if needed. */}
        {false && (
        <div className="bg-black/20 border border-white/10 rounded-lg p-4">
          <h4 className="text-[10px] font-bold text-violet-300 uppercase tracking-wide mb-3">Domain Foresight Pressure</h4>
          {domainPressure.length > 0 && BarChart ? (
            <ResponsiveContainer width="100%" height={250}>
              <BarChart data={domainPressure} layout="vertical" margin={{ left: 90, right: 20, top: 5, bottom: 5 }}>
                <CartesianGrid strokeDasharray="3 3" stroke="#1e293b" />
                <XAxis type="number" tick={{ fill: '#94a3b8', fontSize: 10 }} domain={[0, 100]} />
                <YAxis type="category" dataKey="domain" tick={{ fill: '#cbd5e1', fontSize: 9 }} width={85} />
                <Tooltip contentStyle={{ background: '#0f172a', border: '1px solid #334155', borderRadius: 8, fontSize: 11, color: '#e2e8f0' }} labelStyle={{ color: '#f1f5f9', fontWeight: 600 }} itemStyle={{ color: '#e2e8f0' }}
                  formatter={(value: any, name: string) => [typeof value === 'number' ? value.toFixed(1) : value, name === 'avg_pressure' ? 'Avg Pressure' : 'Max Pressure']} />
                <Bar dataKey="avg_pressure" name="Avg Pressure" radius={[0, 4, 4, 0]}>
                  {domainPressure.map((entry: any, idx: number) => (
                    <Cell key={idx} fill={FAVI_COLOR(entry.avg_pressure)} fillOpacity={0.8} />
                  ))}
                </Bar>
              </BarChart>
            </ResponsiveContainer>
          ) : (
            <div className="text-slate-500 text-xs py-8 text-center">No foresight pressure data</div>
          )}
        </div>
        )}

        {/* Adaptability x Future Vulnerability — replaces the old Domain
            Foresight Pressure bar. X = Financial Adaptation Capacity (FAC).
            Y = Foresight Vulnerability (FAVI). Quadrants are anchored on the
            calibrated FAVI cutoff and the median FAC of this portfolio. */}
        {sorted.length > 0 && ScatterChart ? (() => {
          const facValues = sorted.map((v: any) => v.fac || 0).filter((x: number) => x >= 0);
          const facMedian = facValues.length
            ? (() => {
                const s = [...facValues].sort((a, b) => a - b);
                const mid = Math.floor(s.length / 2);
                return s.length % 2 === 0 ? (s[mid - 1] + s[mid]) / 2 : s[mid];
              })()
            : 50;
          const faviCutoff = data.favi_cutoffs?.favi ?? 48;
          return (
            <div className="bg-black/20 border border-white/10 rounded-lg p-4">
              <h4 className="text-[10px] font-bold text-cyan-300 uppercase tracking-wide mb-1">Adaptability x Future Vulnerability</h4>
              <p className="text-[10px] text-slate-500 mb-3">X = financial capacity to absorb change. Y = foresight-driven future pressure. Top-left = trapped; bottom-right = future-ready.</p>
              <ResponsiveContainer width="100%" height={250}>
                <ScatterChart margin={{ left: 10, right: 20, top: 10, bottom: 25 }}>
                  <CartesianGrid strokeDasharray="3 3" stroke="#1e293b" />
                  <XAxis type="number" dataKey="fac" name="FAC" tick={{ fill: '#94a3b8', fontSize: 10 }} domain={[0, 100]}
                    label={{ value: 'Financial Adaptation Capacity', fill: '#64748b', fontSize: 9, position: 'bottom', offset: 10 }} />
                  <YAxis type="number" dataKey="favi_score" name="FAVI" tick={{ fill: '#94a3b8', fontSize: 10 }} domain={[0, 100]}
                    label={{ value: 'Foresight Vulnerability', fill: '#64748b', fontSize: 9, angle: -90, position: 'insideLeft' }} />
                  <Tooltip cursor={{ strokeDasharray: '3 3' }}
                    contentStyle={{ background: '#0f172a', border: '1px solid #334155', borderRadius: 8, fontSize: 11, color: '#e2e8f0' }} labelStyle={{ color: '#f1f5f9', fontWeight: 600 }} itemStyle={{ color: '#e2e8f0' }}
                    content={({ active, payload }: any) => {
                      if (!active || !payload?.length) return null;
                      const d = payload[0]?.payload;
                      return (
                        <div className="bg-[#0f172a] border border-white/20 rounded-lg p-2 text-[10px]">
                          <div className="font-bold text-white">{d?.name}</div>
                          <div className="text-slate-400">{d?.domain}</div>
                          <div className="mt-1 text-cyan-300">Adaptability (FAC): {d?.fac}</div>
                          <div className="text-rose-300">Foresight Vulnerability: {d?.favi_score}</div>
                          <div className={`mt-1 font-bold ${SEGMENT_CONFIG[d?.segment]?.color || 'text-slate-300'}`}>{d?.segment}</div>
                        </div>
                      );
                    }} />
                  {/* Quadrant reference lines: vertical at FAC median, horizontal at calibrated FAVI cutoff */}
                  {ReferenceLine && <ReferenceLine x={facMedian} stroke="#475569" strokeDasharray="6 4" label={{ value: `FAC=${facMedian.toFixed(0)}`, fill: '#475569', fontSize: 8, position: 'top' }} />}
                  {ReferenceLine && <ReferenceLine y={faviCutoff} stroke="#475569" strokeDasharray="6 4" label={{ value: `FAVI=${faviCutoff}`, fill: '#475569', fontSize: 8, position: 'right' }} />}
                  <Scatter data={sorted.filter((v: any) => v.favi_score > 0 || v.fac > 0)} name="Companies">
                    {sorted.filter((v: any) => v.favi_score > 0 || v.fac > 0).map((v: any, i: number) => {
                      const segColor = v.segment === 'Critical' ? '#f43f5e' :
                        v.segment === 'Sleepwalking' ? '#f59e0b' :
                        v.segment === 'Wounded but Sheltered' ? '#3b82f6' : '#10b981';
                      return <Cell key={i} fill={segColor} fillOpacity={0.75} />;
                    })}
                  </Scatter>
                </ScatterChart>
              </ResponsiveContainer>
              {/* Quadrant labels overlaid as a small legend strip */}
              <div className="grid grid-cols-2 gap-2 mt-2 text-[9px] text-slate-400">
                <div><span className="text-rose-300 font-bold">TRAPPED</span> <span className="text-slate-500">(top-left)</span> — low cushion, future bad</div>
                <div><span className="text-amber-300 font-bold">WEALTHY UNDER PRESSURE</span> <span className="text-slate-500">(top-right)</span> — must invest</div>
                <div><span className="text-blue-300 font-bold">MATURE &amp; STABLE</span> <span className="text-slate-500">(bottom-left)</span> — financially fine, no future pressure</div>
                <div><span className="text-emerald-300 font-bold">FUTURE-READY</span> <span className="text-slate-500">(bottom-right)</span> — best position</div>
              </div>
            </div>
          );
        })() : null}

        {/* Foresight Vulnerability vs Current Vulnerability Scatter */}
        {sorted.length > 0 && ScatterChart ? (
          <div className="bg-black/20 border border-white/10 rounded-lg p-4">
            <h4 className="text-[10px] font-bold text-rose-300 uppercase tracking-wide mb-3">Foresight-Adjusted Vulnerability vs Current Vulnerability</h4>
            <ResponsiveContainer width="100%" height={250}>
              <ScatterChart margin={{ left: 10, right: 20, top: 10, bottom: 48 }}>
                <CartesianGrid strokeDasharray="3 3" stroke="#1e293b" />
                <XAxis type="number" dataKey="current_vulnerability" name="Current Vuln." tick={{ fill: '#94a3b8', fontSize: 10 }} domain={[0, 100]}
                  label={{ value: 'Current Vulnerability', fill: '#64748b', fontSize: 9, position: 'bottom', offset: 10 }} />
                <YAxis type="number" dataKey="favi_score" name="Foresight Vulnerability" tick={{ fill: '#94a3b8', fontSize: 10 }} domain={[0, 100]}
                  label={{ value: 'Foresight Vulnerability', fill: '#64748b', fontSize: 9, angle: -90, position: 'insideLeft' }} />
                <Tooltip cursor={{ strokeDasharray: '3 3' }}
                  contentStyle={{ background: '#0f172a', border: '1px solid #334155', borderRadius: 8, fontSize: 11, color: '#e2e8f0' }} labelStyle={{ color: '#f1f5f9', fontWeight: 600 }} itemStyle={{ color: '#e2e8f0' }}
                  content={({ active, payload }: any) => {
                    if (!active || !payload?.length) return null;
                    const d = payload[0]?.payload;
                    return (
                      <div className="bg-[#0f172a] border border-white/20 rounded-lg p-2 text-[10px]">
                        <div className="font-bold text-white">{d?.name}</div>
                        <div className="text-slate-400">{d?.domain}</div>
                        <div className="mt-1 text-rose-300">Foresight Vulnerability: {d?.favi_score}</div>
                        <div className="text-amber-300">Current Vuln: {d?.current_vulnerability}</div>
                        <div className={`mt-1 font-bold ${SEGMENT_CONFIG[d?.segment]?.color || 'text-slate-300'}`}>{d?.segment}</div>
                      </div>
                    );
                  }} />
                {/* Quadrant reference lines at calibrated segment cutoffs.
                    Falls back to the legacy fixed thresholds if the cutoffs
                    are absent on the API payload. */}
                {ReferenceLine && <ReferenceLine x={data.favi_cutoffs?.current_vulnerability ?? 14} stroke="#475569" strokeDasharray="6 4" label={{ value: `Vuln=${data.favi_cutoffs?.current_vulnerability ?? 14}`, fill: '#475569', fontSize: 8, position: 'top' }} />}
                {ReferenceLine && <ReferenceLine y={data.favi_cutoffs?.favi ?? 48} stroke="#475569" strokeDasharray="6 4" label={{ value: `FAVI=${data.favi_cutoffs?.favi ?? 48}`, fill: '#475569', fontSize: 8, position: 'right' }} />}
                <Scatter data={sorted.filter((v: any) => v.favi_score > 0 || v.current_vulnerability > 0)} name="Companies">
                  {sorted.filter((v: any) => v.favi_score > 0 || v.current_vulnerability > 0).map((v: any, i: number) => {
                    const segColor = v.segment === 'Critical' ? '#f43f5e' :
                      v.segment === 'Sleepwalking' ? '#f59e0b' :
                      v.segment === 'Wounded but Sheltered' ? '#3b82f6' : '#10b981';
                    return <Cell key={i} fill={segColor} fillOpacity={0.75} />;
                  })}
                </Scatter>
              </ScatterChart>
            </ResponsiveContainer>
          </div>
        ) : null}
      </div>

      {/* FAVI Company Table */}
      <div className="bg-black/20 border border-white/10 rounded-lg p-4">
        <div className="flex justify-between items-center mb-3">
          <h4 className="text-[10px] font-bold text-cyan-300 uppercase tracking-wide flex items-center gap-2">
            <AlertTriangle size={12} /> Foresight-Adjusted Vulnerability Index
          </h4>
          <button onClick={handleExportCSV} className="flex items-center gap-1 px-2 py-1 text-[10px] bg-white/5 hover:bg-white/10 border border-white/10 rounded text-slate-300">
            <Download size={10} /> CSV
          </button>
        </div>

        {sorted.length > 0 ? (
          <div className="overflow-x-auto">
            <table className="w-full text-[10px]">
              <thead>
                <tr className="text-slate-500 border-b border-white/10">
                  <th className="p-2 text-left">#</th>
                  <th className="p-2 text-left">Company</th>
                  <th className="p-2 text-left">Domain</th>
                  <SortHeader label="Vulnerability" dataKey="favi_score" />
                  <SortHeader label="Pressure" dataKey="fps" />
                  <SortHeader label="Gap" dataKey="readiness_gap" />
                  <SortHeader label="Adapt." dataKey="fac" />
                  <SortHeader label="Megatrend" dataKey="megatrend_exposure" />
                  <SortHeader label="Curr. Vuln" dataKey="current_vulnerability" />
                  <th className="p-2 text-left">Segment</th>
                  <th className="p-2 text-left"></th>
                </tr>
              </thead>
              <tbody>
                {sorted.map((v: any, i: number) => {
                  const segCfg = SEGMENT_CONFIG[v.segment] || SEGMENT_CONFIG["Safe Harbor"];
                  const isExpanded = expandedCompany === v.name;
                  return (
                    <>
                      <tr key={v.name} className="border-t border-white/5 hover:bg-white/5 transition-colors">
                        <td className="p-2 text-slate-500">{i + 1}</td>
                        <td className="p-2 text-cyan-300 font-medium cursor-pointer hover:underline"
                          onClick={() => onCompanyClick({ companyName: v.name, domain: v.domain })}>{v.name}</td>
                        <td className="p-2 text-slate-400">{v.domain}</td>
                        <td className="p-2 font-mono font-bold" style={{ color: FAVI_COLOR(v.favi_score) }}>
                          {v.favi_score}
                        </td>
                        <td className={`p-2 font-mono ${v.fps >= 50 ? 'text-rose-400' : v.fps >= 25 ? 'text-amber-400' : 'text-slate-400'}`}>
                          {v.fps}
                        </td>
                        <td className={`p-2 font-mono ${v.readiness_gap >= 50 ? 'text-rose-400' : v.readiness_gap >= 25 ? 'text-amber-400' : 'text-slate-400'}`}>
                          {v.readiness_gap}
                        </td>
                        <td className={`p-2 font-mono ${v.fac >= 60 ? 'text-emerald-400' : v.fac >= 30 ? 'text-amber-400' : 'text-rose-400'}`}>
                          {v.fac}
                        </td>
                        <td className={`p-2 font-mono ${v.megatrend_exposure >= 50 ? 'text-rose-400' : 'text-slate-400'}`}>
                          {v.megatrend_exposure}%
                        </td>
                        <td className={`p-2 font-mono ${v.current_vulnerability >= 40 ? 'text-rose-400' : 'text-slate-400'}`}>
                          {v.current_vulnerability}
                        </td>
                        <td className="p-2">
                          <span className={`px-1.5 py-0.5 rounded text-[9px] font-bold ${segCfg.bg} ${segCfg.color} border ${segCfg.border}`}>
                            {v.segment}
                          </span>
                        </td>
                        <td className="p-2">
                          <button onClick={() => setExpandedCompany(isExpanded ? null : v.name)}
                            className="text-slate-500 hover:text-cyan-300 transition-colors text-[9px]">
                            {isExpanded ? '[-]' : '[+]'}
                          </button>
                        </td>
                      </tr>
                      {isExpanded && v.gap_details && v.gap_details.length > 0 && (
                        <tr key={`${v.name}-detail`} className="bg-black/30">
                          <td colSpan={11} className="p-3">
                            <div className="text-[9px] text-slate-400 mb-2 font-bold uppercase tracking-wide">
                              Dimension Readiness Gaps — {v.formula === 'geometric' ? 'Full Model' : 'Additive (sparse data)'}
                              {' '}— {v.foresight_count} foresight signals across {v.dimensions_covered} dimensions
                            </div>
                            <div className="grid grid-cols-1 sm:grid-cols-2 md:grid-cols-3 lg:grid-cols-5 gap-2">
                              {v.gap_details.map((gd: any) => {
                                const gapPct = Math.min(100, gd.gap);
                                const pressurePct = Math.min(100, gd.foresight_pressure);
                                const realityPct = gd.reality_score != null ? Math.min(100, gd.reality_score) : 0;
                                return (
                                  <div key={gd.dimension} className="bg-black/40 rounded-lg p-2 border border-white/5">
                                    <div className="text-[9px] font-bold text-slate-200 mb-2 truncate" title={gd.dimension}>{gd.dimension}</div>
                                    {/* Pressure bar */}
                                    <div className="flex items-center gap-1 mb-1">
                                      <span className="text-[8px] text-violet-400 w-14 shrink-0">Pressure</span>
                                      <div className="flex-1 h-1.5 rounded-full bg-white/5 overflow-hidden">
                                        <div className="h-full bg-violet-500 rounded-full" style={{ width: `${pressurePct}%` }} />
                                      </div>
                                      <span className="text-[8px] text-violet-300 w-8 text-right font-mono">{gd.foresight_pressure}</span>
                                    </div>
                                    {/* Reality bar */}
                                    <div className="flex items-center gap-1 mb-1">
                                      <span className="text-[8px] text-emerald-400 w-14 shrink-0">Reality</span>
                                      <div className="flex-1 h-1.5 rounded-full bg-white/5 overflow-hidden">
                                        <div className="h-full bg-emerald-500 rounded-full" style={{ width: `${realityPct}%` }} />
                                      </div>
                                      <span className="text-[8px] text-emerald-300 w-8 text-right font-mono">{gd.reality_score ?? '—'}</span>
                                    </div>
                                    {/* Gap indicator */}
                                    <div className="flex items-center gap-1">
                                      <span className="text-[8px] text-rose-400 w-14 shrink-0">Gap</span>
                                      <div className="flex-1 h-1.5 rounded-full bg-white/5 overflow-hidden">
                                        <div className="h-full bg-rose-500 rounded-full" style={{ width: `${gapPct}%` }} />
                                      </div>
                                      <span className="text-[8px] text-rose-300 w-8 text-right font-mono font-bold">{gd.gap}</span>
                                    </div>
                                  </div>
                                );
                              })}
                            </div>
                          </td>
                        </tr>
                      )}
                    </>
                  );
                })}
              </tbody>
            </table>
          </div>
        ) : (
          <div className="text-slate-500 text-xs py-8 text-center">No foresight vulnerability data available — companies need foresight links for forward-looking vulnerability analysis</div>
        )}
      </div>
    </div>
  );
};

// ═══════════════════════════════════════════════════════════════════════════════
// MAIN MODAL
// ═══════════════════════════════════════════════════════════════════════════════

const TABS = [
  { id: 'executive', label: 'Executive Summary', icon: Gauge },
  { id: 'future', label: 'Future Demand', icon: TrendingUp },
  { id: 'current', label: 'Current Demand', icon: Target },
  { id: 'sales', label: 'Vulnerability Now', icon: Crosshair },
  { id: 'favi', label: 'Foresight Vulnerability', icon: Eye },
];

const MarketIntelligenceModal = ({ isOpen, data, loading, onClose, onRefresh, onCompanyClick, observatoryData = null, crossImpactInterpretationData = null, onRefreshCrossImpactInterpretation, crossImpactInterpretationLoading = false }: {
  isOpen: boolean;
  data: any;
  loading: boolean;
  onClose: () => void;
  onRefresh?: () => void;
  onCompanyClick: (company: any) => void;
  observatoryData?: any;
  crossImpactInterpretationData?: any;
  onRefreshCrossImpactInterpretation?: () => void;
  crossImpactInterpretationLoading?: boolean;
}) => {
  const [activeTab, setActiveTab] = useState('executive');

  if (!isOpen) return null;

  return (
    <div className="fixed inset-0 z-[60] flex items-center justify-center p-4">
      <div className="absolute inset-0 bg-black/90 backdrop-blur-sm" onClick={onClose} />
      <div className="relative w-full max-w-6xl bg-[#0f172a] border border-cyan-500/30 rounded-xl shadow-2xl z-10 flex flex-col max-h-[90vh] overflow-hidden">
        {/* Header */}
        <div className="p-4 border-b border-white/10 flex justify-between items-center bg-black/20 shrink-0">
          <h3 className="font-bold text-white flex items-center gap-2">
            <BarChart3 size={18} className="text-cyan-400" />
            Market Intelligence
            {data && <span className="text-[10px] text-slate-500 font-normal ml-2">{data.company_count} companies analyzed</span>}
          </h3>
          <div className="flex items-center gap-2">
            {data && ['executive', 'future', 'current', 'favi'].includes(activeTab) && (
              <button
                onClick={() => exportMiTabHtml(activeTab, data, observatoryData, crossImpactInterpretationData)}
                className="flex items-center gap-1.5 px-2.5 py-1.5 rounded-lg bg-slate-800/50 hover:bg-slate-700/50 border border-white/10 transition-colors text-[9px] font-mono text-slate-400 hover:text-slate-200"
                title="Download as HTML"
              >
                <Download size={12} />
                HTML
              </button>
            )}
            {onRefresh && (
              <button onClick={onRefresh} disabled={loading} className="text-slate-400 hover:text-cyan-300 disabled:opacity-30 transition-colors" title="Refresh data">
                <RefreshCw size={16} className={loading ? 'animate-spin' : ''} />
              </button>
            )}
            <button onClick={onClose} className="text-slate-400 hover:text-white">
              <X size={18} />
            </button>
          </div>
        </div>

        {/* Tab Navigation */}
        <div className="flex items-center gap-1 p-2 bg-black/30 border-b border-white/5 shrink-0 overflow-x-auto">
          <a href="https://presentations.static2.website/foresight-creation" target="_blank" rel="noopener noreferrer"
            className="flex items-center gap-1.5 px-3 py-1.5 rounded text-[10px] font-bold uppercase tracking-wide transition-all whitespace-nowrap text-violet-400 hover:text-violet-200 hover:bg-violet-500/10 border border-violet-500/25">
            <BookOpen size={12} />
            Introduction
          </a>
          <div className="w-px h-5 bg-white/10 mx-1" />
          {TABS.map(tab => {
            const Icon = tab.icon;
            return (
              <button key={tab.id} onClick={() => setActiveTab(tab.id)}
                className={`flex items-center gap-1.5 px-3 py-1.5 rounded text-[10px] font-bold uppercase tracking-wide transition-all whitespace-nowrap ${
                  activeTab === tab.id
                    ? 'bg-cyan-500/20 text-cyan-300 border border-cyan-500/30'
                    : 'text-slate-500 hover:text-slate-300 hover:bg-white/5 border border-transparent'
                }`}>
                <Icon size={12} />
                {tab.label}
              </button>
            );
          })}
        </div>

        {/* Content */}
        <div className="overflow-y-auto p-6 space-y-6">
          {loading ? (
            <div className="flex flex-col items-center justify-center py-16">
              <Loader2 className="animate-spin text-cyan-400 mb-4" size={32} />
              <p className="text-slate-400 text-sm">Computing market intelligence...</p>
            </div>
          ) : !data ? (
            <div className="text-center py-16 text-slate-400">No data available</div>
          ) : (
            <>
              {activeTab === 'executive' && <ExecutiveSummaryTab data={data} observatoryData={observatoryData} />}
              {activeTab === 'future' && <FutureDemandTab data={data} observatoryData={observatoryData} crossImpactInterpretationData={crossImpactInterpretationData} onRefreshCrossImpactInterpretation={onRefreshCrossImpactInterpretation} crossImpactInterpretationLoading={crossImpactInterpretationLoading} />}
              {activeTab === 'current' && <CurrentDemandTab data={data} />}
              {activeTab === 'sales' && <SalesPrioritiesTab data={data} onCompanyClick={onCompanyClick} />}
              {activeTab === 'favi' && <ForesightVulnerabilityTab data={data} onCompanyClick={onCompanyClick} />}
            </>
          )}
        </div>
      </div>
    </div>
  );
};

(window as any).MarketIntelligenceModal = MarketIntelligenceModal;
