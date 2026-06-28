
const { useState, useEffect, useRef } = (window as any).React;

const LucideRaw = (window as any).Lucide || {};
const ObsIcons = new Proxy(LucideRaw, {
  get: (target: any, prop: string) => {
    if (prop in target) return target[prop];
    return () => null;
  }
});
const { X, Loader2, Globe, Map, FileText, ChevronDown, ChevronRight, RefreshCw, Eye, Clock, CheckCircle, AlertTriangle, Telescope, BarChart3, Download } = ObsIcons;

const {
  ComposedChart, Bar, Line, XAxis, YAxis, CartesianGrid, Tooltip, ResponsiveContainer, Cell,
} = (window as any).Recharts || {};

// Category colors for Pareto chart (matching MI modal)
const OBS_CATEGORY_COLORS: Record<string, string> = {
  "Technology & Digital": "#06b6d4",
  "Economy & Business": "#8b5cf6",
  "Industry & Infrastructure": "#f59e0b",
  "Environment & Resources": "#10b981",
  "Society & Governance": "#ec4899",
  "People & Well-being": "#f97316",
  "Science & Demographics": "#6366f1",
};

// ═══════════════════════════════════════════════════════════════════════════════
// HTML EXPORT
// ═══════════════════════════════════════════════════════════════════════════════

const OBSERVATORY_EVIDENCE_ID_SOURCE = String.raw`(?:L[123]-CLAIM-\d+|FINDING-[A-Z0-9]+|DOMAIN-[A-Z0-9-]+|PORTFOLIO-[A-Z0-9-]+|EVD-[A-Z0-9]+|FOR-[A-Z0-9]+|KB-[A-Z0-9-]+|CON-[A-Z0-9]+|(?=[A-F0-9]*[A-F])[A-F0-9]{8,12})`;
const OBSERVATORY_EVIDENCE_ID_RE = new RegExp(`\\b${OBSERVATORY_EVIDENCE_ID_SOURCE}\\b`, "g");
const OBSERVATORY_EVIDENCE_PAREN_RE = new RegExp(`\\s*\\((?:\\s*${OBSERVATORY_EVIDENCE_ID_SOURCE}\\s*(?:[;,]\\s*)?)+\\)`, "g");
const OBSERVATORY_EVIDENCE_MIXED_PAREN_RE = new RegExp(`\\s*\\((?:\\s*(?:${OBSERVATORY_EVIDENCE_ID_SOURCE}|versus|vs\\.?|and|[,;:])\\s*)+\\)`, "gi");

const extractObservatoryEvidenceRefs = (text: string): string[] => {
  if (!text) return [];
  const refs = text.match(OBSERVATORY_EVIDENCE_ID_RE) || [];
  return Array.from(new Set(refs));
};

const stripObservatoryInlineEvidenceRefs = (text: string): string => {
  if (!text) return "";
  return text
    .replace(OBSERVATORY_EVIDENCE_MIXED_PAREN_RE, "")
    .replace(OBSERVATORY_EVIDENCE_PAREN_RE, "")
    .replace(OBSERVATORY_EVIDENCE_ID_RE, "")
    .replace(/\(\s*(?:[,;:\s]|versus|vs\.?|and)*\)/gi, "")
    .replace(/\s+([,.;:])/g, "$1")
    .replace(/([(\[])\s+/g, "$1")
    .replace(/\s+([)\]])/g, "$1")
    .replace(/\s{2,}/g, " ")
    .trim();
};

const renderEvidenceRefsHtml = (refs: string[]): string => {
  if (!refs.length) return "";
  return `<div class="evidence-refs"><div class="evidence-refs-title">Evidence references</div><div class="evidence-ref-list">${refs.map(ref => `<span>${ref}</span>`).join("")}</div></div>`;
};

const stripMarkdownEmphasis = (text: string): string => (text || "").replace(/\*\*/g, "");

const isObservatoryHeadingBlock = (text: string): boolean => {
  const clean = stripMarkdownEmphasis(text || "").trim();
  if (clean.length > 120) return false;
  return !/[.!?]\s+\S/.test(clean);
};

const shouldRenderObservatoryEmphasis = (text: string): boolean => {
  const clean = (text || "").trim();
  if (!clean) return false;
  if (clean.length > 140) return false;
  if ((clean.match(/[.!?]/g) || []).length > 1) return false;
  return true;
};

const renderInlineMarkdown = (text: string) => {
  const segments = (text || "").split(/(\*\*[^*]+\*\*)/);
  return segments.map((seg: string, i: number) => {
    if (seg.startsWith("**") && seg.endsWith("**")) {
      const inner = seg.slice(2, -2);
      if (shouldRenderObservatoryEmphasis(inner)) {
        return <strong key={i} className="text-slate-100 font-semibold">{inner}</strong>;
      }
      return <span key={i}>{inner}</span>;
    }
    return <span key={i}>{seg}</span>;
  });
};

const markdownInlineToHtml = (text: string): string =>
  (text || "").replace(/\*\*([^*]+)\*\*/g, (_match, inner) =>
    shouldRenderObservatoryEmphasis(inner) ? `<strong>${inner}</strong>` : inner
  );

const escapeRegExp = (value: string): string => value.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");

const OBSERVATORY_PROSE_LEAD_LABELS = [
  "Strategic inference / scenario hypothesis",
  "Strategic inference",
  "Scenario hypothesis",
  "Anticipation",
  "Participation",
  "Futures Literacy",
  "The first convergence",
  "A second convergence",
  "The second convergence",
  "The third convergence",
  "The first asymmetry",
  "The second asymmetry",
  "The third asymmetry",
  "The fourth asymmetry",
  "The contrarian hypothesis",
  "The implication",
  "The urgent thinking shift",
];

const OBSERVATORY_PROSE_LEAD_LABEL_RE = new RegExp(
  `(^|\\n|[.!?]\\s+)(${OBSERVATORY_PROSE_LEAD_LABELS.map(escapeRegExp).join("|")})(?=\\s|:)`,
  "g"
);

const emphasizeObservatoryProseLabels = (text: string): string => {
  if (!text) return "";
  return text.replace(OBSERVATORY_PROSE_LEAD_LABEL_RE, (_match, prefix: string, label: string) => {
    const needsParagraphBreak = Boolean(prefix && prefix !== "\n" && prefix.trim().length > 0);
    const normalizedPrefix = prefix === "\n" ? "\n\n" : needsParagraphBreak ? `${prefix.trimEnd()}\n\n` : prefix;
    return `${normalizedPrefix}**${label}**`;
  });
};

const normalizeObservatoryEssayText = (text: string): string => {
  if (!text) return "";
  return emphasizeObservatoryProseLabels(stripObservatoryInlineEvidenceRefs(text))
    .replace(/\r\n?/g, "\n")
    .replace(/([^\n])\s+(#{1,3}\s+)/g, "$1\n\n$2")
    .replace(/\s+\*\*([^*]{3,120}?\.)\*\*/g, "\n\n**$1** ")
    .replace(/\s+\*\*([^*]{3,90}?)\*\*/g, "\n\n**$1** ")
    .replace(/[^\S\n]{2,}/g, " ")
    .replace(/\n{3,}/g, "\n\n")
    .trim();
};

const markdownToHtml = (text: string): string => {
  if (!text) return '';
  const refs = extractObservatoryEvidenceRefs(text);
  const cleanText = normalizeObservatoryEssayText(text);
  // Split into paragraph blocks (double newline or more)
  const blocks = cleanText.split(/\n\n+/);
  const body = blocks.map(block => {
    const t = block.trim();
    if (!t) return '';
    if (/^-{3,}$/.test(t)) return '<hr>';
    if (t.startsWith('### ') && isObservatoryHeadingBlock(t.slice(4))) return `<h3>${stripMarkdownEmphasis(t.slice(4))}</h3>`;
    if (t.startsWith('## ') && isObservatoryHeadingBlock(t.slice(3))) return `<h2>${stripMarkdownEmphasis(t.slice(3))}</h2>`;
    if (t.startsWith('# ') && isObservatoryHeadingBlock(t.slice(2))) return `<h1>${stripMarkdownEmphasis(t.slice(2))}</h1>`;
    const html = markdownInlineToHtml(t.replace(/^#{1,3}\s+/, '').replace(/\n/g, ' '));
    return `<p>${html}</p>`;
  }).join('\n');
  return `${body}${renderEvidenceRefsHtml(refs)}`;
};

const exportObservatoryHtml = (data: any, marketIntelData?: any) => {
  if (!data) return;

  const date = data.generated_at ? new Date(data.generated_at).toLocaleDateString('en-FI', { day: 'numeric', month: 'long', year: 'numeric' }) : 'Unknown date';
  const confidenceRows = [
    ['Macro-force evidence', data.confidence?.macro_force_evidence],
    ['Company portfolio pattern', data.confidence?.company_portfolio_pattern],
    ['Interpretation verification', data.confidence?.strategic_interpretation],
  ].filter(([label, value]) => value && !(label === 'Interpretation verification' && value.status === 'unavailable'));
  const kbManifest = data.input_manifest?.central_foresight_kb || {};
  const portfolioManifest = data.input_manifest?.company_portfolio || {};
  const governanceHtml = confidenceRows.length ? `
    <div class="section">
      <h2>Evidence &amp; Confidence</h2>
      <div class="meta">
        ${confidenceRows.map(([label, value]: any) => {
          const unavailable = value.status === "unavailable";
          const scoreText = unavailable ? "Unavailable" : `${value.score}/100`;
          const statusText = unavailable ? "not checked" : value.status;
          return `<div class="meta-item"><strong>${label}</strong>: ${scoreText} (${statusText})</div>`;
        }).join('')}
      </div>
      <p>Central KB eligible: ${kbManifest.records_eligible || 0}/${kbManifest.records_total || 0}; excluded: ${kbManifest.records_excluded || 0}. Company foresight eligible: ${portfolioManifest.foresight_records_eligible || 0}/${portfolioManifest.foresight_records_total || 0}.</p>
    </div>` : '';

  // Cross-impact matrix HTML
  let crossImpactHtml = '';
  if (marketIntelData?.cross_impact?.length > 0) {
    const ci = marketIntelData.cross_impact;
    const themes = [...new Set(ci.map((c: any) => c.theme))] as string[];
    const dims = ["Operating Model", "Service Portfolio", "Values", "Strategy", "AI Readiness"];
    const matrix: Record<string, Record<string, number>> = {};
    for (const item of ci) {
      if (!matrix[item.theme]) matrix[item.theme] = {};
      matrix[item.theme][item.dimension] = item.count;
    }
    const maxVal = Math.max(...ci.map((c: any) => c.count), 1);

    crossImpactHtml = `
      <div class="section">
        <h2>Theme × Dimension Cross-Impact Matrix</h2>
        <table class="matrix">
          <thead><tr><th>Theme</th>${dims.map(d => `<th>${d}</th>`).join('')}</tr></thead>
          <tbody>
            ${themes.map(theme => `<tr><td>${theme}</td>${dims.map(dim => {
              const val = matrix[theme]?.[dim] || 0;
              const opacity = val > 0 ? (0.2 + (val / maxVal) * 0.6) : 0;
              return `<td style="background: rgba(99,102,241,${opacity}); text-align:center">${val > 0 ? val : '–'}</td>`;
            }).join('')}</tr>`).join('\n')}
          </tbody>
        </table>
      </div>`;
  }

  // Top foresights — inline SVG Pareto chart
  let foresightsHtml = '';
  if (marketIntelData?.top_foresights?.length > 0) {
    const f = marketIntelData.top_foresights;
    const total = f.reduce((s: number, x: any) => s + x.count, 0) || 1;
    const maxCount = Math.max(...f.map((x: any) => x.count));

    // Chart dimensions
    const chartW = 820, chartH = 340;
    const mL = 50, mR = 50, mT = 20, mB = 110;
    const plotW = chartW - mL - mR;
    const plotH = chartH - mT - mB;
    const n = f.length;
    const gap = 6;
    const bw = Math.floor((plotW - gap * (n - 1)) / n);

    let cum = 0;
    const barsSvg: string[] = [];
    const linePts: string[] = [];

    f.forEach((item: any, i: number) => {
      const x = mL + i * (bw + gap);
      const bh = (item.count / maxCount) * plotH;
      const y = mT + plotH - bh;
      const col = OBS_CATEGORY_COLORS[item.category] || '#6366f1';
      barsSvg.push(`<rect x="${x}" y="${y}" width="${bw}" height="${bh}" rx="3" fill="${col}" opacity="0.85"/>`);
      barsSvg.push(`<text x="${x + bw / 2}" y="${y - 5}" text-anchor="middle" fill="#cbd5e1" font-size="9" font-family="sans-serif">${item.count}</text>`);
      barsSvg.push(`<text x="${x + bw / 2}" y="${mT + plotH + 14}" text-anchor="end" transform="rotate(-35 ${x + bw / 2} ${mT + plotH + 14})" fill="#94a3b8" font-size="9" font-family="sans-serif">${item.theme}</text>`);

      cum += item.count;
      const lx = x + bw / 2;
      const ly = mT + plotH - (cum / total) * plotH;
      linePts.push(`${lx},${ly}`);
    });

    // Y gridlines + labels
    const ticks = [0, 0.25, 0.5, 0.75, 1];
    const gridSvg = ticks.map(t => {
      const y = mT + plotH - t * plotH;
      const val = Math.round(t * maxCount);
      const pct = Math.round(t * 100);
      return `<line x1="${mL}" y1="${y}" x2="${mL + plotW}" y2="${y}" stroke="#1e293b" stroke-dasharray="3,3"/>` +
        `<text x="${mL - 8}" y="${y + 3}" text-anchor="end" fill="#94a3b8" font-size="9" font-family="sans-serif">${val}</text>` +
        `<text x="${mL + plotW + 8}" y="${y + 3}" text-anchor="start" fill="#94a3b8" font-size="9" font-family="sans-serif">${pct}%</text>`;
    }).join('\n');

    const dots = linePts.map(pt => {
      const [cx, cy] = pt.split(',');
      return `<circle cx="${cx}" cy="${cy}" r="3.5" fill="#f43f5e"/>`;
    }).join('\n');

    foresightsHtml = `
      <div class="section">
        <h2>Top ${n} Foresight Themes (Pareto)</h2>
        <svg viewBox="0 0 ${chartW} ${chartH}" xmlns="http://www.w3.org/2000/svg" style="width:100%;max-width:${chartW}px;height:auto;display:block;margin:0 auto">
          ${gridSvg}
          ${barsSvg.join('\n')}
          <polyline points="${linePts.join(' ')}" fill="none" stroke="#f43f5e" stroke-width="2" stroke-linejoin="round"/>
          ${dots}
        </svg>
      </div>`;
  }

  // Domain dossiers
  const dossiersHtml = (data.domain_dossiers || []).map((d: any) => {
    const archBadges = d.archetype_distribution
      ? Object.entries(d.archetype_distribution).map(([k, v]) => `<span class="badge badge-${k.toLowerCase().replace(/\s+/g, '-')}">${k}: ${v}</span>`).join(' ')
      : '';

    const forceBadges = d.forces_by_timescale ? [
      d.forces_by_timescale.act_now?.length > 0 ? `<span class="badge badge-act-now">Act Now: ${d.forces_by_timescale.act_now.length}</span>` : '',
      d.forces_by_timescale.prepare?.length > 0 ? `<span class="badge badge-prepare">Prepare: ${d.forces_by_timescale.prepare.length}</span>` : '',
      d.forces_by_timescale.position?.length > 0 ? `<span class="badge badge-position">Position: ${d.forces_by_timescale.position.length}</span>` : '',
    ].filter(Boolean).join(' ') : '';

    return `
      <div class="domain-dossier">
        <h2>${d.domain}</h2>
        <div class="badges">${archBadges} <span class="badge">${d.company_count} companies</span> ${forceBadges}</div>
        <div class="essay">${markdownToHtml(d.text)}</div>
      </div>`;
  }).join('\n');

  const html = `<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>Strategic Foresight Observatory — ${date}</title>
  <style>
    :root { --bg: #0f172a; --surface: #1e293b; --border: #334155; --text: #e2e8f0; --text-muted: #94a3b8; --text-dim: #64748b; --cyan: #22d3ee; --violet: #a78bfa; --amber: #fbbf24; --emerald: #34d399; --rose: #fb7185; }
    * { margin: 0; padding: 0; box-sizing: border-box; }
    body { background: var(--bg); color: var(--text); font-family: 'Inter', -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif; line-height: 1.7; max-width: 900px; margin: 0 auto; padding: 40px 24px; }
    h1 { font-size: 28px; margin-bottom: 4px; color: white; }
    .subtitle { color: var(--text-dim); font-size: 12px; font-family: monospace; margin-bottom: 32px; }
    .meta { display: flex; flex-wrap: wrap; gap: 12px; margin-bottom: 32px; }
    .meta-item { background: var(--surface); border: 1px solid var(--border); border-radius: 6px; padding: 6px 14px; font-size: 12px; font-family: monospace; color: var(--text-muted); }
    .meta-item.highlight { border-color: rgba(34,211,238,0.3); color: var(--cyan); }

    .layer { background: var(--surface); border: 1px solid var(--border); border-radius: 12px; margin-bottom: 28px; overflow: hidden; }
    .layer-header { padding: 20px 28px; border-bottom: 1px solid var(--border); }
    .layer-header h2 { font-size: 18px; margin-bottom: 4px; }
    .layer-header.global h2 { color: var(--cyan); }
    .layer-header.finland h2 { color: var(--violet); }
    .layer-header.domain h2 { color: var(--amber); }
    .layer-desc { color: var(--text-dim); font-size: 12px; line-height: 1.6; margin-top: 8px; }
    .layer-body { padding: 24px 28px; }

    .essay h1 { font-size: 20px; margin: 32px 0 14px; color: white; }
    .essay h2 { font-size: 16px; margin: 32px 0 14px; color: var(--cyan); text-transform: uppercase; letter-spacing: 0.05em; border-bottom: 1px solid var(--border); padding-bottom: 8px; }
    .essay h3 { font-size: 14px; margin: 24px 0 10px; color: white; font-weight: 600; }
    .essay p { font-size: 14px; color: var(--text); margin-bottom: 18px; line-height: 1.8; font-weight: 400; }
    .essay p strong { color: #e2e8f0; font-weight: 600; }
    .essay hr { border: none; border-top: 1px solid var(--border); margin: 28px 0; }
    .evidence-refs { margin-top: 20px; padding-top: 14px; border-top: 1px solid rgba(148,163,184,0.18); }
    .evidence-refs-title { color: var(--text-dim); font-size: 10px; text-transform: uppercase; letter-spacing: 0.08em; margin-bottom: 8px; }
    .evidence-ref-list { display: flex; flex-wrap: wrap; gap: 6px; }
    .evidence-ref-list span { color: var(--text-dim); border: 1px solid rgba(148,163,184,0.2); background: rgba(15,23,42,0.5); border-radius: 999px; padding: 2px 7px; font-size: 10px; font-family: monospace; }

    .domain-dossier { border-top: 1px solid var(--border); padding: 24px 28px; }
    .domain-dossier:first-child { border-top: none; }
    .domain-dossier h2 { font-size: 17px; color: var(--amber); margin-bottom: 8px; }

    .badges { display: flex; flex-wrap: wrap; gap: 6px; margin-bottom: 16px; }
    .badge { display: inline-block; padding: 3px 10px; border-radius: 12px; font-size: 10px; font-family: monospace; background: var(--bg); border: 1px solid var(--border); color: var(--text-muted); }
    .badge-thriving { color: var(--emerald); border-color: rgba(52,211,153,0.3); }
    .badge-modernizing { color: var(--amber); border-color: rgba(251,191,36,0.3); }
    .badge-cash-cow { color: #60a5fa; border-color: rgba(96,165,250,0.3); }
    .badge-at-risk { color: var(--rose); border-color: rgba(251,113,133,0.3); }
    .badge-act-now { color: var(--rose); border-color: rgba(251,113,133,0.3); }
    .badge-prepare { color: var(--amber); border-color: rgba(251,191,36,0.3); }
    .badge-position { color: var(--cyan); border-color: rgba(34,211,238,0.3); }

    .section { background: var(--surface); border: 1px solid var(--border); border-radius: 12px; margin-bottom: 28px; padding: 24px 28px; }
    .section h2 { font-size: 13px; text-transform: uppercase; letter-spacing: 0.05em; color: var(--violet); margin-bottom: 16px; }

    table { width: 100%; border-collapse: collapse; font-size: 12px; }
    th { text-align: left; padding: 8px 10px; color: var(--text-muted); font-weight: 500; border-bottom: 1px solid var(--border); }
    td { padding: 6px 10px; border-bottom: 1px solid rgba(51,65,85,0.3); color: var(--text); }
    table.matrix th { text-align: center; font-size: 11px; }
    table.matrix td { font-family: monospace; font-size: 11px; color: var(--text-muted); }

    .footer { text-align: center; color: var(--text-dim); font-size: 11px; font-family: monospace; margin-top: 48px; padding-top: 24px; border-top: 1px solid var(--border); }

    @media print {
      body { background: white; color: #1a1a1a; max-width: 100%; padding: 20px; }
      .layer, .section, .domain-dossier { border-color: #ddd; background: #fafafa; }
      .essay h2 { color: #1a1a1a; border-bottom-color: #ddd; }
      .layer-header.global h2, .layer-header.finland h2, .layer-header.domain h2 { color: #1a1a1a; }
      .essay p, td, th { color: #333; }
      .badge { background: #f5f5f5; border-color: #ccc; color: #555; }
      .footer { color: #999; border-color: #ddd; }
    }
  </style>
</head>
<body>
  <h1>Strategic Foresight Observatory</h1>
  <div class="subtitle">Three-layer force analysis — Generated ${date}</div>

  <div class="meta">
    <div class="meta-item">${data.company_count} companies</div>
    <div class="meta-item">${data.domain_count} domains</div>
    ${data.global_analysis?.force_count ? `<div class="meta-item highlight">${data.global_analysis.force_count} forces tracked</div>` : ''}
  </div>
  ${governanceHtml}

  ${foresightsHtml}
  ${crossImpactHtml}

  <!-- Layer 1: Global Force Landscape -->
  <div class="layer">
    <div class="layer-header global">
      <h2>Layer 1: Global Force Landscape</h2>
      <div class="layer-desc">A futurologist's briefing on the converging global forces reshaping business. Synthesizes validated signals from the Foresight Knowledge Base into macro-level patterns.</div>
    </div>
    <div class="layer-body">
      <div class="essay">${markdownToHtml(data.global_analysis?.text)}</div>
    </div>
  </div>

  <!-- Layer 2: Finnish Economy Force Map -->
  <div class="layer">
    <div class="layer-header finland">
      <h2>Layer 2: Finnish Economy Force Map</h2>
      <div class="layer-desc">Maps how global forces specifically land in the Finnish business landscape. Grounds every claim in actual company data — market health scores, archetype distributions, and gap patterns.</div>
    </div>
    <div class="layer-body">
      <div class="essay">${markdownToHtml(data.finland_analysis?.text)}</div>
    </div>
  </div>

  <!-- Layer 3: Domain Force Dossiers -->
  <div class="layer">
    <div class="layer-header domain">
      <h2>Layer 3: Domain Force Dossiers</h2>
      <div class="layer-desc">Per-domain strategic analysis with timescale mapping. Each dossier examines a specific industry's position, identifies forces by urgency horizon, and offers contrarian signals.</div>
    </div>
    <div class="layer-body" style="padding:0">
      ${dossiersHtml}
    </div>
  </div>

  <div class="footer">
    Strategic Foresight Observatory &mdash; ${date}<br>
    Generated by The Scanner
  </div>
</body>
</html>`;

  // Trigger download
  const blob = new Blob([html], { type: 'text/html;charset=utf-8' });
  const url = URL.createObjectURL(blob);
  const a = document.createElement('a');
  a.href = url;
  a.download = `observatory-${new Date(data.generated_at || Date.now()).toISOString().slice(0, 10)}.html`;
  document.body.appendChild(a);
  a.click();
  document.body.removeChild(a);
  URL.revokeObjectURL(url);
};

// ═══════════════════════════════════════════════════════════════════════════════
// HELPERS
// ═══════════════════════════════════════════════════════════════════════════════

const SECTION_COLORS = {
  global: { border: "border-cyan-500/30", bg: "bg-cyan-500/10", text: "text-cyan-300", icon: "text-cyan-400" },
  finland: { border: "border-violet-500/30", bg: "bg-violet-500/10", text: "text-violet-300", icon: "text-violet-400" },
  domain: { border: "border-amber-500/30", bg: "bg-amber-500/10", text: "text-amber-300", icon: "text-amber-400" },
};

const LAYER_DESCRIPTIONS: Record<string, string> = {
  global: "A futurologist's briefing on the converging global forces reshaping business. Synthesizes validated signals from the Foresight Knowledge Base into macro-level patterns — identifying which forces are accelerating, which are decelerating, and where unexpected cross-impacts create strategic inflection points. Frames the future through thinking capacity and adaptability, not technology adoption.",
  finland: "Maps how global forces specifically land in the Finnish business landscape. Grounds every claim in current company data — market health scores, archetype distributions, and gap patterns — to reveal where Finnish industry leads, where it lags, and where genuine strategic opportunities exist.",
  domain: "Per-domain strategic analysis with timescale mapping. Each dossier examines a specific industry's position using company-level data, identifies forces by urgency horizon (Act Now / Prepare / Position), explores 'Own Race' opportunities, and offers a contrarian signal that challenges conventional domain wisdom.",
};

const formatEssayText = (text: string) => {
  if (!text) return null;
  const refs = extractObservatoryEvidenceRefs(text);
  const cleanText = normalizeObservatoryEssayText(text);
  const parts = cleanText.split(/\n\n+|\n(?=#{1,3}\s)/);
  const renderedParts = parts.map((part: string, i: number) => {
    const trimmed = part.trim();
    if (!trimmed) return null;
    // Skip raw horizontal rules
    if (/^-{3,}$/.test(trimmed)) return <hr key={i} className="border-slate-700/40 my-6" />;
    // Headers
    if (trimmed.startsWith("## ") && isObservatoryHeadingBlock(trimmed.replace(/^##\s*/, ""))) {
      return (
        <h3 key={i} className="text-base font-bold text-slate-100 mt-6 mb-3 border-b border-white/10 pb-1">
          {trimmed.replace(/^##\s*/, "")}
        </h3>
      );
    }
    if (trimmed.startsWith("# ") && isObservatoryHeadingBlock(trimmed.replace(/^#\s*/, ""))) {
      return (
        <h2 key={i} className="text-lg font-bold text-slate-50 mt-6 mb-3">
          {trimmed.replace(/^#\s*/, "")}
        </h2>
      );
    }
    const rendered = renderInlineMarkdown(trimmed.replace(/^#{1,3}\s+/, ""));
    return (
      <p key={i} className="text-[13px] text-slate-300 leading-relaxed mb-3">
        {rendered}
      </p>
    );
  });
  if (refs.length === 0) return renderedParts;
  return [
    ...renderedParts,
    <div key="observatory-evidence-refs" className="mt-5 pt-3 border-t border-white/10">
      <div className="text-[9px] uppercase tracking-[0.12em] text-slate-500 mb-2">Evidence references</div>
      <div className="flex flex-wrap gap-1.5">
        {refs.map((ref) => (
          <span key={ref} className="px-2 py-0.5 rounded-full border border-white/10 bg-black/20 text-[9px] font-mono text-slate-500">
            {ref}
          </span>
        ))}
      </div>
    </div>
  ];
};

// ═══════════════════════════════════════════════════════════════════════════════
// SECTION COMPONENTS
// ═══════════════════════════════════════════════════════════════════════════════

const VerificationBadge = ({ verification }: { verification: any }) => {
  if (!verification) return null;
  const status = verification.status || (verification.passed === true ? "verified" : verification.passed === false ? "failed" : "unavailable");
  const passed = status === "verified";
  const unavailable = status === "unavailable";
  const issues = verification.issues || [];
  const violations = verification.tech_framing_violations || [];
  return (
    <div className={`inline-flex items-center gap-1 px-2 py-0.5 rounded text-[9px] font-mono ${
      passed ? "bg-emerald-500/15 text-emerald-300 border border-emerald-500/30"
             : unavailable
               ? "bg-slate-500/15 text-slate-300 border border-slate-500/30"
               : "bg-rose-500/15 text-rose-300 border border-rose-500/30"
    }`}>
      {passed ? <CheckCircle size={10} /> : <AlertTriangle size={10} />}
      {passed ? "Verified" : unavailable ? "Verification unavailable" : `Verification failed${issues.length + violations.length ? `: ${issues.length + violations.length} issues` : ""}`}
    </div>
  );
};

const ObservatoryGovernance = ({ data }: { data: any }) => {
  const confidence = data?.confidence || {};
  const manifest = data?.input_manifest || {};
  const kb = manifest.central_foresight_kb || {};
  const portfolio = manifest.company_portfolio || {};
  const rows = [
    ["Macro-force evidence", confidence.macro_force_evidence],
    ["Company portfolio pattern", confidence.company_portfolio_pattern],
    ["Interpretation verification", confidence.strategic_interpretation],
  ].filter(([label, value]) => value && !(label === "Interpretation verification" && value.status === "unavailable"));
  if (rows.length === 0 && !manifest.generated_at) return null;

  return (
    <div className="border border-white/10 rounded-lg bg-black/20 p-3 mb-4">
      <div className="grid grid-cols-1 md:grid-cols-3 gap-2">
        {rows.map(([label, value]: any) => (
          <div key={label} className="border border-white/5 rounded p-2 bg-black/20">
            <div className="text-[9px] uppercase tracking-wide text-slate-500">{label}</div>
            <div className="flex items-baseline gap-2 mt-1">
              <span className="text-sm font-bold text-slate-200">
                {value.status === "unavailable" ? "Unavailable" : `${value.score}/100`}
              </span>
              <span className="text-[9px] font-mono text-slate-400">
                {value.status === "unavailable" ? "not checked" : value.status}
              </span>
            </div>
            {value.status !== "unavailable" && (
              <div className="text-[9px] text-slate-500 mt-1">{value.basis}</div>
            )}
          </div>
        ))}
      </div>
      {(kb.records_total !== undefined || portfolio.summary_records !== undefined) && (
        <div className="flex flex-wrap gap-x-4 gap-y-1 mt-3 pt-2 border-t border-white/5 text-[9px] font-mono text-slate-500">
          <span>KB eligible: {kb.records_eligible || 0}/{kb.records_total || 0}</span>
          <span>KB excluded: {kb.records_excluded || 0}</span>
          <span>Company foresight eligible: {portfolio.foresight_records_eligible || 0}/{portfolio.foresight_records_total || 0}</span>
          <span>Snapshot: {portfolio.latest_snapshot_date ? new Date(portfolio.latest_snapshot_date).toLocaleDateString() : "unavailable"}</span>
        </div>
      )}
    </div>
  );
};

const EssaySection = ({ title, icon: Icon, colorKey, text, verification, metadata, description, defaultOpen = false }: any) => {
  const [isOpen, setIsOpen] = useState(defaultOpen);
  const colors = SECTION_COLORS[colorKey as keyof typeof SECTION_COLORS] || SECTION_COLORS.global;

  return (
    <div className={`border ${colors.border} rounded-lg overflow-hidden mb-4`}>
      <button
        onClick={() => setIsOpen(!isOpen)}
        className={`w-full flex items-center justify-between px-4 py-3 ${colors.bg} hover:brightness-110 transition-all`}
      >
        <div className="flex items-center gap-3">
          <Icon size={18} className={colors.icon} />
          <span className={`font-bold text-sm ${colors.text}`}>{title}</span>
          {verification && <VerificationBadge verification={verification} />}
        </div>
        <div className="flex items-center gap-2">
          {metadata && (
            <span className="text-[9px] text-slate-500 font-mono">{metadata}</span>
          )}
          {isOpen ? <ChevronDown size={16} className="text-slate-400" /> : <ChevronRight size={16} className="text-slate-400" />}
        </div>
      </button>
      {/* Layer description — always visible */}
      {description && (
        <div className="px-5 py-3 bg-black/10 border-b border-white/5">
          <p className="text-[11px] text-slate-400 leading-relaxed">{description}</p>
        </div>
      )}
      {isOpen && (
        <div className="px-5 py-4 bg-black/20 max-h-[600px] overflow-y-auto">
          {formatEssayText(text)}
        </div>
      )}
    </div>
  );
};

const DomainDossierTabs = ({ dossiers, defaultOpen = false }: { dossiers: any[]; defaultOpen?: boolean }) => {
  const [isOpen, setIsOpen] = useState(defaultOpen);
  const [activeDomain, setActiveDomain] = useState(0);
  if (!dossiers || dossiers.length === 0) return null;

  const active = dossiers[activeDomain];
  const colors = SECTION_COLORS.domain;

  return (
    <div className={`border ${colors.border} rounded-lg overflow-hidden mb-4`}>
      {/* Collapsible header — same style as EssaySection */}
      <button
        onClick={() => setIsOpen(!isOpen)}
        className={`w-full flex items-center justify-between px-4 py-3 ${colors.bg} hover:brightness-110 transition-all`}
      >
        <div className="flex items-center gap-3">
          <FileText size={18} className={colors.icon} />
          <span className={`font-bold text-sm ${colors.text}`}>Layer 3: Domain Force Dossiers</span>
        </div>
        <div className="flex items-center gap-2">
          <span className="text-[9px] text-slate-500 font-mono">{dossiers.length} domains</span>
          {isOpen ? <ChevronDown size={16} className="text-slate-400" /> : <ChevronRight size={16} className="text-slate-400" />}
        </div>
      </button>

      {/* Layer description — always visible */}
      <div className="px-5 py-3 bg-black/10 border-b border-white/5">
        <p className="text-[11px] text-slate-400 leading-relaxed">{LAYER_DESCRIPTIONS.domain}</p>
      </div>

      {isOpen && (
        <>
          {/* Domain tabs */}
          <div className="flex flex-wrap gap-1 px-3 py-2 bg-black/30 border-b border-white/5">
            {dossiers.map((d: any, i: number) => (
              <button
                key={i}
                onClick={() => setActiveDomain(i)}
                className={`px-2.5 py-1 rounded text-[10px] font-mono transition-all ${
                  i === activeDomain
                    ? "bg-amber-500/20 text-amber-200 border border-amber-500/40"
                    : "bg-black/20 text-slate-400 border border-white/5 hover:text-slate-200"
                }`}
              >
                {d.domain}
              </button>
            ))}
          </div>

          {/* Active dossier */}
          <div className="px-5 py-4 bg-black/20">
            {/* Domain stats bar */}
            <div className="flex flex-wrap gap-3 mb-4">
              <div className="px-2 py-1 bg-black/30 rounded border border-white/5 text-[9px] font-mono text-slate-400">
                {active.company_count} companies
              </div>
              {active.archetype_distribution && Object.entries(active.archetype_distribution).map(([k, v]: [string, any]) => (
                <div key={k} className={`px-2 py-1 rounded border text-[9px] font-mono ${
                  k === "Thriving" ? "bg-emerald-500/10 border-emerald-500/30 text-emerald-300" :
                  k === "Modernizing" ? "bg-amber-500/10 border-amber-500/30 text-amber-300" :
                  k === "Cash Cow" ? "bg-blue-500/10 border-blue-500/30 text-blue-300" :
                  "bg-rose-500/10 border-rose-500/30 text-rose-300"
                }`}>
                  {k}: {v}
                </div>
              ))}
              {active.verification && <VerificationBadge verification={active.verification} />}
            </div>

            {/* Forces by timescale */}
            {active.forces_by_timescale && (
              <div className="flex flex-wrap gap-2 mb-4">
                {[
                  { key: "act_now", label: "Act Now", color: "rose" },
                  { key: "prepare", label: "Prepare", color: "amber" },
                  { key: "position", label: "Position", color: "cyan" },
                ].map(({ key, label, color }) => {
                  const forces = active.forces_by_timescale[key] || [];
                  if (forces.length === 0) return null;
                  return (
                    <div key={key} className={`px-2 py-1 bg-${color}-500/10 border border-${color}-500/30 rounded text-[9px] font-mono text-${color}-300`}>
                      {label}: {forces.length} forces
                    </div>
                  );
                })}
              </div>
            )}

            {/* Essay text */}
            <div className="max-h-[500px] overflow-y-auto">
              {formatEssayText(active.text)}
            </div>
          </div>
        </>
      )}
    </div>
  );
};

// ═══════════════════════════════════════════════════════════════════════════════
// FORESIGHT LANDSCAPE CHARTS (from Market Intelligence data)
// ═══════════════════════════════════════════════════════════════════════════════

const ForesightCharts = ({ marketIntelData }: { marketIntelData: any }) => {
  const [isOpen, setIsOpen] = useState(false);
  const foresights = marketIntelData?.top_foresights || [];
  const crossImpact = marketIntelData?.cross_impact || [];

  if (foresights.length === 0 && crossImpact.length === 0) return null;

  // Pareto data
  const totalCount = foresights.reduce((s: number, f: any) => s + f.count, 0) || 1;
  let cumulative = 0;
  const paretoData = foresights.map((f: any) => {
    cumulative += f.count;
    return { ...f, cumPct: Math.round((cumulative / totalCount) * 100) };
  });

  // Cross-impact matrix
  const ciThemes = [...new Set(crossImpact.map((c: any) => c.theme))];
  const ciDimensions = ["Operating Model", "Service Portfolio", "Values", "Strategy", "AI Readiness"];
  const ciMatrix: Record<string, Record<string, number>> = {};
  for (const item of crossImpact) {
    if (!ciMatrix[item.theme]) ciMatrix[item.theme] = {};
    ciMatrix[item.theme][item.dimension] = item.count;
  }
  const maxCICount = Math.max(...crossImpact.map((c: any) => c.count), 1);

  return (
    <div className="border border-slate-700/40 rounded-lg overflow-hidden mb-4">
      <button
        onClick={() => setIsOpen(!isOpen)}
        className="w-full flex items-center justify-between px-4 py-3 bg-slate-800/30 hover:brightness-110 transition-all"
      >
        <div className="flex items-center gap-3">
          <BarChart3 size={18} className="text-slate-400" />
          <span className="font-bold text-sm text-slate-300">Foresight Landscape</span>
        </div>
        <div className="flex items-center gap-2">
          <span className="text-[9px] text-slate-500 font-mono">{foresights.length} themes &middot; {crossImpact.length} cross-impacts</span>
          {isOpen ? <ChevronDown size={16} className="text-slate-400" /> : <ChevronRight size={16} className="text-slate-400" />}
        </div>
      </button>
      <div className="px-5 py-3 bg-black/10 border-b border-white/5">
        <p className="text-[11px] text-slate-400 leading-relaxed">Quantitative foundation for the analysis layers below. The Pareto chart shows which foresight themes appear most frequently across scanned companies. The cross-impact matrix reveals how themes connect to organizational dimensions — where the bars show volume, the matrix shows structural relationships.</p>
      </div>
      {isOpen && (
        <div className="px-5 py-4 bg-black/20 space-y-6">
          {/* Pareto chart */}
          {paretoData.length > 0 && ComposedChart && (
            <div>
              <h4 className="text-[10px] font-bold text-cyan-300 uppercase tracking-wide mb-3">Top {foresights.length} Foresight Themes (Pareto)</h4>
              <ResponsiveContainer width="100%" height={280}>
                <ComposedChart data={paretoData} margin={{ left: 10, right: 30, top: 10, bottom: 60 }}>
                  <CartesianGrid strokeDasharray="3 3" stroke="#1e293b" />
                  <XAxis dataKey="theme" tick={{ fill: '#94a3b8', fontSize: 8 }} angle={-35} textAnchor="end" interval={0} height={80} />
                  <YAxis yAxisId="count" tick={{ fill: '#94a3b8', fontSize: 10 }} />
                  <YAxis yAxisId="pct" orientation="right" tick={{ fill: '#94a3b8', fontSize: 10 }} domain={[0, 100]} unit="%" />
                  <Tooltip contentStyle={{ background: '#0f172a', border: '1px solid #334155', borderRadius: 8, fontSize: 11 }}
                    formatter={(value: any, name: string) => [value, name === 'cumPct' ? 'Cumulative %' : 'Signals']} />
                  <Bar yAxisId="count" dataKey="count" name="Signals" radius={[4, 4, 0, 0]}>
                    {paretoData.map((entry: any, i: number) => (
                      <Cell key={i} fill={OBS_CATEGORY_COLORS[entry.category] || '#6366f1'} />
                    ))}
                  </Bar>
                  <Line yAxisId="pct" dataKey="cumPct" stroke="#f43f5e" strokeWidth={2} dot={{ r: 3 }} name="Cumulative %" />
                </ComposedChart>
              </ResponsiveContainer>
            </div>
          )}

          {/* Cross-Impact Matrix */}
          {ciThemes.length > 0 && (
            <div>
              <h4 className="text-[10px] font-bold text-violet-300 uppercase tracking-wide mb-3">Theme x Dimension Cross-Impact Matrix</h4>
              <div className="overflow-x-auto">
                <table className="w-full text-[10px]">
                  <thead>
                    <tr>
                      <th className="text-left text-slate-400 p-1 font-medium">Theme</th>
                      {ciDimensions.map(d => (
                        <th key={d} className="text-center text-slate-400 p-1 font-medium whitespace-nowrap">{d}</th>
                      ))}
                    </tr>
                  </thead>
                  <tbody>
                    {(ciThemes as string[]).map((theme: string) => (
                      <tr key={theme} className="border-t border-white/5">
                        <td className="p-1 text-slate-300 whitespace-nowrap">{theme}</td>
                        {ciDimensions.map(dim => {
                          const val = ciMatrix[theme]?.[dim] || 0;
                          const intensity = val / maxCICount;
                          const bg = val > 0
                            ? `rgba(99, 102, 241, ${0.15 + intensity * 0.6})`
                            : 'transparent';
                          return (
                            <td key={dim} className="p-1 text-center font-mono text-slate-200" style={{ backgroundColor: bg }}>
                              {val > 0 ? val : '\u2013'}
                            </td>
                          );
                        })}
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            </div>
          )}
        </div>
      )}
    </div>
  );
};

// ═══════════════════════════════════════════════════════════════════════════════
// GENERATION PANEL
// ═══════════════════════════════════════════════════════════════════════════════

const GeneratePanel = ({ onGenerate, generationStatus }: any) => {
  const isRunning = generationStatus?.is_running;
  const current = generationStatus?.current || 0;
  const total = generationStatus?.total || 1;
  const step = generationStatus?.current_step || "";
  const pct = total > 0 ? Math.round((current / total) * 100) : 0;

  return (
    <div className="border border-white/10 rounded-lg p-4 bg-black/20 mb-4">
      <div className="flex items-center justify-between mb-2">
        <div className="flex items-center gap-2">
          <Telescope size={16} className="text-cyan-400" />
          <span className="text-xs font-bold text-slate-200">Observatory Generation</span>
        </div>
        <button
          onClick={onGenerate}
          disabled={isRunning}
          className={`flex items-center gap-1.5 px-3 py-1.5 rounded text-[10px] font-bold font-mono transition-all ${
            isRunning
              ? "bg-slate-700/50 text-slate-500 cursor-not-allowed"
              : "bg-cyan-600/30 hover:bg-cyan-600/50 text-cyan-200 border border-cyan-500/40"
          }`}
        >
          {isRunning ? <Loader2 size={12} className="animate-spin" /> : <RefreshCw size={12} />}
          {isRunning ? "GENERATING..." : "GENERATE OBSERVATORY"}
        </button>
      </div>
      {isRunning && (
        <div className="mt-2">
          <div className="flex items-center justify-between text-[9px] font-mono text-slate-400 mb-1">
            <span>{step}</span>
            <span>{current}/{total} ({pct}%)</span>
          </div>
          <div className="w-full h-1.5 bg-black/40 rounded-full overflow-hidden">
            <div
              className="h-full bg-gradient-to-r from-cyan-500 to-violet-500 rounded-full transition-all duration-500"
              style={{ width: `${pct}%` }}
            />
          </div>
        </div>
      )}
      {generationStatus?.completed_at && !isRunning && (
        <div className="text-[9px] font-mono text-slate-500 mt-1">
          Last generated: {new Date(generationStatus.completed_at).toLocaleString()}
        </div>
      )}
    </div>
  );
};

// ═══════════════════════════════════════════════════════════════════════════════
// MAIN MODAL
// ═══════════════════════════════════════════════════════════════════════════════

const ObservatoryModal = ({ isOpen, data, loading, onClose, onRefresh, adminPassword, marketIntelData = null }: {
  isOpen: boolean;
  data: any;
  loading: boolean;
  onClose: () => void;
  onRefresh: () => void;
  adminPassword: string;
  marketIntelData?: any;
}) => {
  const [generationStatus, setGenerationStatus] = useState<any>(null);
  const pollRef = useRef<any>(null);
  const hasRefreshedRef = useRef<boolean>(false);

  // Poll for generation status — only while generation is running
  useEffect(() => {
    if (!isOpen) return;

    const poll = async () => {
      try {
        const res = await fetch(`/admin/observatory-status?t=${Date.now()}`);
        if (res.ok) {
          const status = await res.json();
          setGenerationStatus(status);

          // If just completed, refresh data ONCE and stop polling
          if (status.completed_at && !status.is_running && status.result && !status.result.error && !hasRefreshedRef.current) {
            hasRefreshedRef.current = true;
            onRefresh();
            // Stop polling — generation is done
            if (pollRef.current) {
              clearInterval(pollRef.current);
              pollRef.current = null;
            }
          }

          // Stop polling if not running and we already have status
          if (!status.is_running && pollRef.current) {
            clearInterval(pollRef.current);
            pollRef.current = null;
          }
        }
      } catch (e) { /* ignore */ }
    };

    // Initial fetch (one-time, no interval yet)
    poll();

    return () => {
      if (pollRef.current) {
        clearInterval(pollRef.current);
        pollRef.current = null;
      }
    };
  }, [isOpen]);

  const handleGenerate = async () => {
    if (!adminPassword) return;
    try {
      const res = await fetch("/admin/generate-observatory", {
        method: "POST",
        headers: { "Authorization": `Bearer ${adminPassword}` },
      });
      if (res.ok) {
        hasRefreshedRef.current = false;  // Reset so we refresh when complete
        setGenerationStatus({ ...generationStatus, is_running: true, current: 0, total: 0, current_step: "Starting..." });
        // Start polling for progress
        if (pollRef.current) clearInterval(pollRef.current);
        const poll = async () => {
          try {
            const r = await fetch(`/admin/observatory-status?t=${Date.now()}`);
            if (r.ok) {
              const status = await r.json();
              setGenerationStatus(status);
              if (status.completed_at && !status.is_running && !hasRefreshedRef.current) {
                hasRefreshedRef.current = true;
                onRefresh();
                if (pollRef.current) { clearInterval(pollRef.current); pollRef.current = null; }
              }
              if (!status.is_running && pollRef.current) {
                clearInterval(pollRef.current); pollRef.current = null;
              }
            }
          } catch (e) { /* ignore */ }
        };
        pollRef.current = setInterval(poll, 3000);
      } else {
        const err = await res.json();
        alert(err.detail || "Failed to start generation");
      }
    } catch (e: any) {
      alert("Error: " + e.message);
    }
  };

  if (!isOpen) return null;

  return (
    <div className="fixed inset-0 z-[60] flex items-start justify-center pt-8 pb-8">
      {/* Backdrop */}
      <div className="absolute inset-0 bg-black/70 backdrop-blur-sm" onClick={onClose} />

      {/* Modal */}
      <div className="relative z-10 w-full max-w-4xl max-h-[90vh] bg-gradient-to-br from-[#1e1b4b] to-[#0f172a] border border-white/10 rounded-xl shadow-2xl overflow-hidden flex flex-col">
        {/* Header */}
        <div className="flex items-center justify-between px-6 py-4 border-b border-white/10 bg-black/20 shrink-0">
          <div className="flex items-center gap-3">
            <div className="w-8 h-8 rounded-lg bg-gradient-to-br from-cyan-500/30 to-violet-500/30 flex items-center justify-center">
              <Telescope size={18} className="text-cyan-300" />
            </div>
            <div>
              <h2 className="text-base font-bold text-slate-100">Strategic Foresight Observatory</h2>
              <p className="text-[10px] text-slate-500 font-mono">Three-layer force analysis</p>
            </div>
          </div>
          <div className="flex items-center gap-3">
            {data?.generated_at && (
              <div className="flex items-center gap-1 text-[9px] text-slate-500 font-mono">
                <Clock size={10} />
                {new Date(data.generated_at).toLocaleDateString()}
              </div>
            )}
            {data && (
              <button
                onClick={() => exportObservatoryHtml(data, marketIntelData)}
                className="flex items-center gap-1.5 px-2.5 py-1.5 rounded-lg bg-slate-800/50 hover:bg-slate-700/50 border border-white/10 transition-colors text-[9px] font-mono text-slate-400 hover:text-slate-200"
                title="Download as HTML"
              >
                <Download size={12} />
                HTML
              </button>
            )}
            <button onClick={onClose} className="p-1.5 rounded-lg hover:bg-white/5 transition-colors">
              <X size={18} className="text-slate-400" />
            </button>
          </div>
        </div>

        {/* Content */}
        <div className="flex-1 overflow-y-auto px-6 py-4">
          {/* Generate panel (admin) */}
          {adminPassword && (
            <GeneratePanel onGenerate={handleGenerate} generationStatus={generationStatus} />
          )}

          {loading && (
            <div className="flex items-center justify-center py-20">
              <Loader2 size={24} className="animate-spin text-cyan-400" />
              <span className="ml-3 text-sm text-slate-400">Loading observatory data...</span>
            </div>
          )}

          {!loading && !data && (
            <div className="text-center py-20">
              <Telescope size={48} className="mx-auto text-slate-600 mb-4" />
              <p className="text-sm text-slate-400">No observatory data available yet.</p>
              <p className="text-xs text-slate-500 mt-1">Click "Generate Observatory" to create the first analysis.</p>
            </div>
          )}

          {!loading && data && (
            <>
              {/* Summary stats — without model name */}
              <div className="flex flex-wrap gap-3 mb-4">
                <div className="px-3 py-1.5 bg-black/30 rounded border border-white/10 text-[10px] font-mono text-slate-300">
                  {data.company_count} companies
                </div>
                <div className="px-3 py-1.5 bg-black/30 rounded border border-white/10 text-[10px] font-mono text-slate-300">
                  {data.domain_count} domains
                </div>
                {data.global_analysis?.force_count && (
                  <div className="px-3 py-1.5 bg-cyan-500/10 rounded border border-cyan-500/30 text-[10px] font-mono text-cyan-300">
                    {data.global_analysis.force_count} forces tracked
                  </div>
                )}
              </div>

              <ObservatoryGovernance data={data} />

              {/* Foresight Landscape Charts — from Market Intelligence */}
              {marketIntelData && (marketIntelData.top_foresights?.length > 0 || marketIntelData.cross_impact?.length > 0) && (
                <ForesightCharts marketIntelData={marketIntelData} />
              )}

              {/* Layer 1: Global — collapsed by default */}
              <EssaySection
                title="Layer 1: Global Force Landscape"
                icon={Globe}
                colorKey="global"
                text={data.global_analysis?.text}
                verification={data.global_analysis?.verification}
                metadata={`${data.global_analysis?.accelerating_forces || 0} accelerating`}
                description={LAYER_DESCRIPTIONS.global}
                defaultOpen={false}
              />

              {/* Layer 2: Finland — collapsed by default */}
              <EssaySection
                title="Layer 2: Finnish Economy Force Map"
                icon={Map}
                colorKey="finland"
                text={data.finland_analysis?.text}
                verification={data.finland_analysis?.verification}
                metadata={`${data.finland_analysis?.domains_analyzed || 0} domains`}
                description={LAYER_DESCRIPTIONS.finland}
                defaultOpen={false}
              />

              {/* Layer 3: Domain Dossiers — collapsed by default, same pattern */}
              {data.domain_dossiers && data.domain_dossiers.length > 0 && (
                <DomainDossierTabs dossiers={data.domain_dossiers} defaultOpen={false} />
              )}
            </>
          )}
        </div>
      </div>
    </div>
  );
};

(window as any).ObservatoryModal = ObservatoryModal;
