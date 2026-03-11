
const { ResponsiveContainer, RadarChart, PolarGrid, PolarAngleAxis, PolarRadiusAxis, Radar, Tooltip: RechartsTooltip, LineChart, Line, XAxis, YAxis, CartesianGrid, ReferenceLine } = (window as any).Recharts;
const { ExternalLink, CheckCircle, AlertTriangle, ShieldCheck, ServerCrash, RefreshCw, XCircle, Search, Zap, Scale, Activity, Download, Copy, FileJson, FileCode, Check, Eye, AlertOctagon, Info, Users, Sparkles, TrendingUp } = (window as any).Lucide;
const { useRef, useState } = (window as any).React;

const HighlightText = ({ text }) => {
  if (!text) return null;
  
  const negativeTerms = [
    "Digital Taylorism", "Feature Factory", "Command and Control", "Zombie Scrum", 
    "Fake Agile", "Silos", "Micromanagement", "Project-based", "Fear", "Bureaucracy", 
    "Legacy", "Cash Cow", "At Risk", "Surveillance", "Turf Wars", "Water-scrum-fall",
    "Ticket-based", "Output-driven", "Gatekeeping", "Compliance-driven", "Blame Culture",
    "Risk-averse", "Siloed", "Waterfall", "Consulting-heavy", "Vendor Lock-in", "Burnout",
    "Change Control Board", "HiPPO", "Modernization Washing", "AI Washing", "Agile Washing"
  ];

  const positiveTerms = [
    "Psychological Safety", "Platform Engineering", "Flow Efficiency", "Autonomy", 
    "Value Streams", "Outcome-based", "Trust", "Innovation", "Modernization", 
    "Thriving", "Modernizing", "Distributed Authority", "Sense and Respond",
    "Stream-aligned", "Zero Distance", "Customer Intimacy", "Continuous Delivery",
    "DevOps", "Data Mesh", "Empowerment", "Learning Organization", "Servant Leadership",
    "Cloud-native", "Ecosystem", "Open Source", "Self-organization", "Cross-functional",
    "Adaptive Strategy", "Generative AI", "AI-Augmented"
  ];

  const sortTerms = (a, b) => b.length - a.length;
  negativeTerms.sort(sortTerms);
  positiveTerms.sort(sortTerms);
  
  const allTerms = [...negativeTerms, ...positiveTerms];
  const escapedTerms = allTerms.map(t => t.replace(/[.*+?^${}()|[\]\\]/g, '\\$&'));
  
  const regex = new RegExp(`(${escapedTerms.join('|')})`, 'gi');
  const parts = text.split(regex);

  return (
    <>
      {parts.map((part, i) => {
        const lowerPart = part.toLowerCase();
        const isNegative = negativeTerms.some(term => term.toLowerCase() === lowerPart);
        const isPositive = positiveTerms.some(term => term.toLowerCase() === lowerPart);

        if (isNegative) {
             return <span key={i} className="font-bold text-rose-300 bg-rose-500/20 px-1 py-0.5 rounded mx-0.5 border border-rose-500/30 shadow-[0_0_10px_rgba(244,63,94,0.2)]" title="Negative Signal">{part}</span>;
        }
        
        if (isPositive) {
             return <span key={i} className="font-bold text-emerald-300 bg-emerald-500/20 px-1 py-0.5 rounded mx-0.5 border border-emerald-500/30 shadow-[0_0_10px_rgba(16,185,129,0.2)]" title="Positive Signal">{part}</span>;
        }

        return part;
      })}
    </>
  );
};

const CompanySummary = ({ data }) => {
  const summaryRef = useRef(null);
  const [copied, setCopied] = useState(false);
  const [pitchCopied, setPitchCopied] = useState(false);
  const [whyWhatCopied, setWhyWhatCopied] = useState(false);

  // ARCHETYPE LOGIC (Reality vs Financial — unified with dashboard)
  // Prefer the stored archetype from the backend (single source of truth).
  // Fall back to score-based computation for backward compatibility with old data.
  const ARCHETYPE_COLORS = {
      "Thriving": '#10b981',    // Emerald
      "Modernizing": '#22d3ee', // Cyan
      "Cash Cow": '#f59e0b',    // Amber
      "At Risk": '#fb7185',     // Rose/Red
  };

  let gapKey: string;
  if (data.archetype && ARCHETYPE_COLORS[data.archetype]) {
      gapKey = data.archetype;
  } else {
      const fin = data.financial_index ?? data.intent_score ?? 0;
      if (data.reality_score >= 50) {
          gapKey = fin >= 50 ? "Thriving" : "Modernizing";
      } else {
          gapKey = fin >= 50 ? "Cash Cow" : "At Risk";
      }
  }
  const gapLabel = gapKey;
  const gapColor = ARCHETYPE_COLORS[gapKey] || '#94a3b8';

  const ARCHETYPE_DESCRIPTIONS = {
    "Thriving": "High Reality + Strong Financials. Modern operations AND healthy business.",
    "Modernizing": "High Reality + Weak Financials. Strong operations but financial pressure.",
    "Cash Cow": "Low Reality + Strong Financials. Profitable but operationally outdated.",
    "At Risk": "Low Reality + Weak Financials. Both operations and finances under pressure."
  };
  
  let gapValueDisplay = data.reality_gap > 0 ? `+${data.reality_gap}` : `${data.reality_gap}`;

  const radarData = data.intent_breakdown.map(item => ({
    subject: item.name,
    A: item.score,
    fullMark: 100,
  }));

  // Safely access metrics (might be missing in old seed data)
  const metrics = data.metrics || { friction_index: 0, vibrancy_score: 0, total_good_mentions: 0, total_bad_mentions: 0 };
  
  // Friction Logic: 
  const frictionPercentage = Math.round(metrics.friction_index * 100);
  const frictionColor = frictionPercentage > 60 ? 'text-rose-400' : frictionPercentage > 30 ? 'text-amber-400' : 'text-emerald-400';
  const frictionLabel = frictionPercentage > 60 ? 'Battleground' : frictionPercentage > 30 ? 'Conflicted' : 'Coherent';
  
  // Confidence Logic
  const confidence = data.confidence_score !== undefined ? data.confidence_score : 0;
  let confidenceColor = 'text-slate-400';
  let confidenceLabel = 'Unknown';
  let confidenceBg = 'bg-slate-700';

  if (confidence > 80) {
      confidenceColor = 'text-emerald-400';
      confidenceLabel = 'High Fidelity';
      confidenceBg = 'bg-emerald-500';
  } else if (confidence > 60) {
      confidenceColor = 'text-cyan-400';
      confidenceLabel = 'Verified';
      confidenceBg = 'bg-cyan-500';
  } else if (confidence > 40) {
      confidenceColor = 'text-amber-400';
      confidenceLabel = 'Partial Data';
      confidenceBg = 'bg-amber-500';
  } else {
      confidenceColor = 'text-rose-400';
      confidenceLabel = 'Manual Review Rec.';
      confidenceBg = 'bg-rose-500';
  }

  // --- CHART DATA PREPARATION ---
  const historyRaw = data.history || [];
  let chartData = [];

  if (historyRaw.length > 0) {
      chartData = historyRaw.map(h => ({
          date: new Date(h.date).toLocaleDateString('en-US', { month: 'short', year: '2-digit' }),
          gap: h.gap
      }));
      chartData.push({
          date: new Date(data.lastUpdated).toLocaleDateString('en-US', { month: 'short', year: '2-digit' }),
          gap: data.reality_gap
      });
  } else {
      const current = new Date(data.lastUpdated);
      const currentGap = data.reality_gap;
      chartData = [];
      for(let i=5; i>=1; i--) {
          const d = new Date(current);
          d.setMonth(d.getMonth() - i);
          const variance = Math.floor(Math.random() * 16) - 8; 
          chartData.push({
              date: d.toLocaleDateString('en-US', { month: 'short', year: '2-digit' }),
              gap: Math.max(-100, Math.min(100, currentGap + variance))
          });
      }
      chartData.push({
          date: current.toLocaleDateString('en-US', { month: 'short', year: '2-digit' }),
          gap: currentGap
      });
  }

  // --- EXPORT LOGIC ---
  const handleCopyJSON = () => {
    const rawData = JSON.stringify(data, null, 2);
    navigator.clipboard.writeText(rawData).then(() => {
      setCopied(true);
      setTimeout(() => setCopied(false), 2000);
    });
  };

  const handleCopyPitch = () => {
    if (data.pitch_text) {
        navigator.clipboard.writeText(data.pitch_text).then(() => {
            setPitchCopied(true);
            setTimeout(() => setPitchCopied(false), 2000);
        });
    }
  };

  const handleCopyWhyWhat = () => {
    const why = data.why_proposals || [];
    const what = data.what_proposals || [];
    const text = `WHY (Desired Impact):\n1. ${why[0] || ''}\n2. ${why[1] || ''}\n\nWHAT (Desired Effect):\n1. ${what[0] || ''}\n2. ${what[1] || ''}`;
    navigator.clipboard.writeText(text).then(() => {
        setWhyWhatCopied(true);
        setTimeout(() => setWhyWhatCopied(false), 2000);
    });
  };

  const handleDownloadHTML = () => {
    if (!summaryRef.current) return;

    // 1. Get the visual DOM (Snapshot of current view)
    const visuals = summaryRef.current.innerHTML;
    // 2. Prepare raw data for Deep Dive Tool integration
    const rawData = JSON.stringify(data, null, 2);

    // 3. Construct Standalone HTML
    const htmlContent = `
<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>${data.companyName} - Forensic Report</title>
    <script src="https://cdn.tailwindcss.com"></script>
    <link href="https://fonts.googleapis.com/css2?family=Inter:wght@300;400;500;600;700;800&display=swap" rel="stylesheet">
    <style>
      body { font-family: 'Inter', sans-serif; background-color: #0f172a; color: #f8fafc; }
      ::-webkit-scrollbar { width: 8px; height: 8px; }
      ::-webkit-scrollbar-track { background: #1e1b4b; }
      ::-webkit-scrollbar-thumb { background: #4f46e5; border-radius: 4px; border: 2px solid #1e1b4b; }
      /* Show report-only sections in downloaded HTML */
      .report-only-section { display: block !important; }
    </style>
</head>
<body class="p-8 max-w-7xl mx-auto">
    <div class="mb-6 border-b border-slate-700 pb-6">
        <div class="flex justify-between items-start">
            <div>
                <div class="flex items-center gap-3 flex-wrap">
                    <h1 class="text-3xl font-bold text-white tracking-tight">${data.companyName}</h1>
                    ${data.id ? `<span class="px-2 py-0.5 rounded text-[10px] font-mono bg-white/5 text-slate-400 border border-white/5">ID: ${data.id.substring(0,8)}</span>` : ''}
                    ${data.has_docs ? '<span class="flex items-center gap-1 px-2 py-0.5 rounded text-[10px] font-bold bg-indigo-500/20 text-indigo-300 border border-indigo-500/30">DEEP DIVE</span>' : ''}
                    ${data.washing_flag && data.intent_score > data.reality_score ? `<span class="flex items-center gap-1 px-2 py-0.5 rounded text-[10px] font-bold bg-rose-500/20 text-rose-300 border border-rose-500/30">${data.washing_type ? data.washing_type.toUpperCase() : 'WASHING DETECTED'}</span>` : ''}
                </div>
                <p class="text-slate-400 text-sm mt-1">Final Forensic Report</p>
            </div>
            <div class="text-right text-xs text-slate-500 font-mono space-y-1">
                <div>Report: ${new Date().toISOString().split('T')[0]}</div>
                ${data.lastUpdated ? `<div>Scanned: ${new Date(data.lastUpdated).toLocaleDateString('en-FI', { day: 'numeric', month: 'long', year: 'numeric' })}</div>` : ''}
            </div>
        </div>
        <div class="flex items-center gap-3 mt-3 flex-wrap">
            ${data.domain ? `<span class="px-2.5 py-1 rounded-full text-[10px] font-bold uppercase tracking-wider bg-slate-800 text-slate-300 border border-white/10">${data.domain}</span>` : ''}
            ${data.archetype ? `<span class="px-2.5 py-1 rounded-full text-[10px] font-bold uppercase tracking-wider bg-indigo-500/15 text-indigo-300 border border-indigo-500/20">${data.archetype}</span>` : ''}
            ${data.financial_index != null ? `<span class="px-2.5 py-1 rounded-full text-[10px] font-bold font-mono bg-slate-800 text-slate-300 border border-white/10">FHI: ${data.financial_index}/100</span>` : ''}
        </div>
    </div>

    <!-- VISUAL REPORT SNAPSHOT -->
    <div id="visual-report" class="space-y-8">
        ${visuals}
    </div>

    <!-- DETAILED FORENSIC REPORT (rendered from data, not DOM) -->
    ${data.detailed_report ? `
    <div class="mt-8 space-y-8">
        <div class="bg-gradient-to-r from-violet-900/30 to-indigo-900/30 p-8 rounded-2xl border-l-4 border-violet-500 backdrop-blur-sm">
            <h3 class="text-xl font-bold text-white mb-2 flex items-center gap-2">
                <svg xmlns="http://www.w3.org/2000/svg" width="22" height="22" viewBox="0 0 24 24" fill="none" stroke="#a78bfa" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M12 22s8-4 8-10V5l-8-3-8 3v7c0 6 8 10 8 10z"/><path d="m9 12 2 2 4-4"/></svg>
                Detailed Forensic Report
            </h3>
            <p class="text-[10px] text-slate-500 mb-4 uppercase tracking-wider font-mono">
                Deep diagnostic with evidence and stakeholder context${data.detailed_report_model ? ` — ${data.detailed_report_model}` : ''}
            </p>
            <div>
                ${data.detailed_report.split(/\n\n+/).map((para, pi) => `<p class="text-slate-200 leading-relaxed text-sm font-light${pi > 0 ? ' mt-4' : ''}">${para.replace(/</g, '&lt;').replace(/>/g, '&gt;')}</p>`).join('\n                ')}
            </div>
        </div>
    </div>` : ''}

    <!-- FUTURE INSIGHT (rendered from data, not DOM) -->
    ${data.foresight_summary ? `
    <div class="mt-8 space-y-8">
        <div class="bg-gradient-to-r from-teal-900/30 to-cyan-900/30 p-8 rounded-2xl border-l-4 border-teal-500 backdrop-blur-sm">
            <h3 class="text-xl font-bold text-white mb-2 flex items-center gap-2">
                <svg xmlns="http://www.w3.org/2000/svg" width="22" height="22" viewBox="0 0 24 24" fill="none" stroke="#2dd4bf" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><polyline points="22 7 13.5 15.5 8.5 10.5 2 17"/><polyline points="16 7 22 7 22 13"/></svg>
                Future Insight
            </h3>
            <p class="text-[10px] text-slate-500 mb-4 uppercase tracking-wider font-mono">
                Forward-looking narrative based on strategic foresight analysis${data.foresight_summary_model ? ` — ${data.foresight_summary_model}` : ''}
            </p>
            <div>
                ${data.foresight_summary.split(/\n\n+/).map((para, pi) => `<p class="text-slate-200 leading-relaxed text-sm font-light${pi > 0 ? ' mt-4' : ''}">${para.replace(/</g, '&lt;').replace(/>/g, '&gt;')}</p>`).join('\n                ')}
            </div>
        </div>
    </div>` : ''}

    <!-- RAW DATA CAPSULE (Hidden, for Deep Dive Tool Parsing) -->
    <script id="forensic-data" type="application/json">
        ${rawData}
    </script>

    <div class="mt-8 pt-8 border-t border-slate-700 text-center text-slate-500 text-xs font-mono opacity-50">
        AdaptabilityScanner | Forensic Suite | Data contained in #forensic-data tag
    </div>
</body>
</html>`;

    // 4. Download
    const blob = new Blob([htmlContent], { type: 'text/html' });
    const url = URL.createObjectURL(blob);
    const a = document.createElement('a');
    a.href = url;
    a.download = `Forensic_Report_${data.companyName.replace(/\s+/g, '_')}_${new Date().toISOString().split('T')[0]}.html`;
    a.click();
    URL.revokeObjectURL(url);
  };

  return (
    <div className="space-y-4">
      {/* TOOLBAR */}
      <div className="flex justify-end items-center gap-2 mb-2">
        <button 
            onClick={handleCopyJSON}
            className="flex items-center gap-2 px-3 py-1.5 rounded bg-white/5 hover:bg-white/10 border border-white/5 hover:border-cyan-500/30 text-xs font-mono text-cyan-400 transition-all"
            title="Copy Raw JSON to Clipboard for Deep Dive Tool"
        >
            {copied ? <Check size={14} /> : <FileCode size={14} />}
            {copied ? "COPIED" : "COPY JSON"}
        </button>
        <button 
            onClick={handleDownloadHTML}
            className="flex items-center gap-2 px-3 py-1.5 rounded bg-indigo-600/80 hover:bg-indigo-500 border border-indigo-400/30 text-xs font-bold text-white transition-all shadow-lg shadow-indigo-900/20"
            title="Download Offline HTML Report (Visuals + Data)"
        >
            <Download size={14} />
            DOWNLOAD REPORT
        </button>
      </div>

      {/* CONTENT WRAPPER FOR SNAPSHOT */}
      <div ref={summaryRef} className="space-y-8">
        <div className="grid grid-cols-1 md:grid-cols-3 gap-4">
            <div className="bg-white/5 backdrop-blur-md p-6 rounded-2xl border border-white/10 flex flex-col items-center justify-center relative group overflow-hidden">
            <div className="absolute inset-0 bg-indigo-500/10 opacity-0 group-hover:opacity-100 transition-opacity"></div>
            <span className="text-indigo-200 font-medium mb-1 text-sm tracking-widest uppercase">Ambition Score</span>
            <span className="text-6xl font-black text-white tracking-tighter drop-shadow-[0_0_15px_rgba(99,102,241,0.5)]">{data.intent_score}</span>
            <span className="text-xs text-slate-400 mt-2 font-mono">Narrative Clarity</span>
            </div>
            
            <div className="bg-white/5 backdrop-blur-md p-6 rounded-2xl border border-white/10 flex flex-col items-center justify-center relative group overflow-hidden">
            <div className="absolute inset-0 bg-emerald-500/10 opacity-0 group-hover:opacity-100 transition-opacity"></div>
            <span className="text-emerald-200 font-medium mb-1 text-sm tracking-widest uppercase">Reality Score</span>
            <span className="text-6xl font-black text-white tracking-tighter drop-shadow-[0_0_15px_rgba(16,185,129,0.5)]">{data.reality_score}</span>
            <span className="text-xs text-slate-400 mt-2 font-mono">Evidence Check</span>
            </div>
            
            <div className="bg-white/5 backdrop-blur-md p-6 rounded-2xl border border-white/10 flex flex-col items-center justify-center relative overflow-hidden group/tooltip">
                <div className="absolute inset-0 bg-gradient-to-br from-transparent to-white/5 opacity-0 group-hover/tooltip:opacity-100 transition-opacity"></div>
                <span className="text-slate-300 font-medium mb-1 text-sm tracking-widest uppercase">Integrity Delta</span>
                <span className="text-6xl font-black tracking-tighter" style={{ color: gapColor, textShadow: `0 0 20px ${gapColor}40` }}>
                    {gapValueDisplay}
                </span>
                
                {/* TOOLTIP CONTAINER */}
                <div className="flex items-center gap-1.5 mt-2 z-10 relative cursor-help">
                    <span className="text-xs font-mono" style={{ color: gapColor }}>{gapLabel}</span>
                    <Info size={12} className="text-slate-500 hover:text-white transition-colors" />
                    
                    {/* HOVER CONTENT */}
                    <div className="absolute bottom-full left-1/2 -translate-x-1/2 mb-2 w-48 p-3 bg-slate-900 border border-white/20 rounded-lg shadow-xl text-[10px] text-slate-300 pointer-events-none opacity-0 group-hover/tooltip:opacity-100 transition-opacity z-50 text-center leading-relaxed backdrop-blur-xl">
                        {ARCHETYPE_DESCRIPTIONS[gapKey] || "No definition available."}
                        <div className="absolute -bottom-1 left-1/2 -translate-x-1/2 w-2 h-2 bg-slate-900 border-b border-r border-white/20 rotate-45"></div>
                    </div>
                </div>
            </div>
        </div>

        {/* WASHING WARNING BANNER */}
        {/* LOGIC FIX: Ensure Washing only displays if Intent > Reality. */}
        {data.washing_flag && data.intent_score > data.reality_score && (
            <div className="animate-in slide-in-from-top-4 bg-rose-500/10 border-l-4 border-rose-500 p-4 rounded-r-xl flex items-start gap-4 shadow-lg shadow-rose-900/20">
                <AlertOctagon className="text-rose-500 shrink-0 mt-0.5" size={24} />
                <div>
                    <h4 className="text-rose-400 font-bold text-sm uppercase tracking-wide">
                        {data.washing_type ? data.washing_type.toUpperCase() : "MODERNIZATION WASHING DETECTED"}
                    </h4>
                    <p className="text-slate-300 text-sm mt-1">
                        Forensic analysis confirms a significant contradiction between marketing intent and operational reality. 
                        High-level claims ({data.intent_score} Ambition) are unsupported by technical evidence.
                    </p>
                </div>
            </div>
        )}

        {/* METRICS & TRENDS ROW */}
        <div className="grid grid-cols-1 lg:grid-cols-2 gap-4">
            <div className="space-y-4">
                <div className="bg-white/5 p-4 rounded-xl border border-white/10 flex items-center justify-between">
                    <div className="flex items-center gap-3">
                        <div className="p-2 bg-slate-800 rounded-lg text-slate-400">
                            <Scale size={20} />
                        </div>
                        <div>
                            <h4 className="text-xs text-slate-400 font-bold uppercase tracking-wider">Friction Index</h4>
                            <p className={`text-lg font-bold ${frictionColor}`}>{frictionLabel} ({frictionPercentage}%)</p>
                        </div>
                    </div>
                    <div className="text-right">
                        <span className="text-[10px] text-slate-500 block">Good Signals: {metrics.total_good_mentions}</span>
                        <span className="text-[10px] text-slate-500 block">Bad Signals: {metrics.total_bad_mentions}</span>
                    </div>
                </div>

                <div className="bg-white/5 p-4 rounded-xl border border-white/10 flex items-center justify-between">
                    <div className="flex items-center gap-3">
                        <div className="p-2 bg-slate-800 rounded-lg text-slate-400">
                            <Eye size={20} />
                        </div>
                        <div>
                            <h4 className="text-xs text-slate-400 font-bold uppercase tracking-wider">Summary Confidence</h4>
                            <p className={`text-lg font-bold ${confidenceColor}`}>{confidenceLabel} ({confidence}%)</p>
                        </div>
                    </div>
                    <div className="text-right">
                         <span className="text-[10px] text-slate-500 block">Based on evidence density</span>
                         <div className="w-16 h-1.5 bg-slate-700 rounded-full mt-1">
                            <div className={`h-full ${confidenceBg} rounded-full`} style={{ width: `${confidence}%` }}></div>
                         </div>
                    </div>
                </div>
            </div>

            {/* TREND CHART */}
            <div className="bg-white/5 p-5 rounded-2xl border border-white/10 backdrop-blur-md flex flex-col">
                <h3 className="text-xs font-bold text-slate-400 uppercase tracking-widest mb-4 flex items-center gap-2">
                <Activity className="text-cyan-400" size={14} />
                Integrity Delta Trend (6 Mo)
                </h3>
                <div className="flex-grow min-h-[120px]">
                    <ResponsiveContainer width="100%" height="100%">
                        <LineChart data={chartData}>
                            <CartesianGrid strokeDasharray="3 3" stroke="#334155" opacity={0.2} vertical={false} />
                            <XAxis 
                                dataKey="date" 
                                stroke="#64748b" 
                                fontSize={10} 
                                tickLine={false} 
                                axisLine={false}
                                dy={5} 
                            />
                            <YAxis 
                                stroke="#64748b" 
                                fontSize={10} 
                                tickLine={false} 
                                axisLine={false}
                                domain={[-100, 100]} 
                                hide={true} 
                            />
                            <ReferenceLine y={0} stroke="#475569" strokeDasharray="3 3" />
                            <RechartsTooltip 
                                contentStyle={{ backgroundColor: '#0f172a', borderColor: '#334155', color: '#f1f5f9', borderRadius: '8px', fontSize: '12px', padding: '8px' }}
                                itemStyle={{ color: gapColor }}
                            />
                            <Line 
                                type="monotone" 
                                dataKey="gap" 
                                stroke={gapColor} 
                                strokeWidth={3} 
                                dot={{ fill: '#0f172a', stroke: gapColor, strokeWidth: 2, r: 4 }}
                                activeDot={{ r: 6, fill: gapColor, stroke: '#fff' }}
                                animationDuration={1500}
                            />
                        </LineChart>
                    </ResponsiveContainer>
                </div>
            </div>
        </div>

        {/* FINANCIAL HEALTH INDEX */}
        {data.financial_index != null && (
            <div className="bg-white/5 p-6 rounded-2xl border border-white/10 backdrop-blur-md">
                <h3 className="text-lg font-bold text-white mb-4 flex items-center gap-2">
                    <TrendingUp className="text-cyan-400" size={20} />
                    Financial Health Index
                </h3>
                <div className="flex flex-col md:flex-row items-center gap-6">
                    {/* Big score */}
                    <div className="flex flex-col items-center justify-center min-w-[120px]">
                        <span className={`text-5xl font-black tracking-tighter ${
                            data.financial_index >= 70 ? 'text-emerald-400 drop-shadow-[0_0_15px_rgba(16,185,129,0.5)]' :
                            data.financial_index >= 45 ? 'text-amber-400 drop-shadow-[0_0_15px_rgba(245,158,11,0.5)]' :
                            'text-rose-400 drop-shadow-[0_0_15px_rgba(244,63,94,0.5)]'
                        }`}>{data.financial_index}</span>
                        <span className="text-xs text-slate-400 mt-1 font-mono">/ 100</span>
                    </div>

                    {/* Sub-score bars */}
                    <div className="flex-grow w-full grid grid-cols-1 sm:grid-cols-2 gap-3">
                        {[
                            { key: 'revenue_trend', label: 'Revenue Trend', icon: '📈' },
                            { key: 'profitability', label: 'Profitability', icon: '💰' },
                            { key: 'financial_stability', label: 'Financial Stability', icon: '🏦' },
                            { key: 'investment_signal', label: 'Investment Signal', icon: '🔬' }
                        ].map(dim => {
                            const score = data.financial_sub_scores?.[dim.key] ?? 0;
                            const pct = Math.round((score / 25) * 100);
                            const barColor = score >= 18 ? 'bg-emerald-500' : score >= 12 ? 'bg-amber-500' : 'bg-rose-500';
                            return (
                                <div key={dim.key} className="bg-black/20 p-3 rounded-lg border border-white/5">
                                    <div className="flex items-center justify-between mb-1.5">
                                        <span className="text-[10px] text-slate-400 uppercase tracking-wider font-bold">{dim.icon} {dim.label}</span>
                                        <span className="text-xs font-mono font-bold text-slate-300">{score}/25</span>
                                    </div>
                                    <div className="w-full h-1.5 bg-slate-700 rounded-full">
                                        <div className={`h-full ${barColor} rounded-full transition-all duration-500`} style={{ width: `${pct}%` }}></div>
                                    </div>
                                </div>
                            );
                        })}
                    </div>
                </div>
                {data.financial_confidence != null && (
                    <div className="mt-3 text-[10px] text-slate-500 font-mono flex items-center gap-1.5">
                        <Eye size={10} /> Data Confidence: {data.financial_confidence}%
                    </div>
                )}
            </div>
        )}

        {/* HALLUCINATION WARNING BANNER */}
        {data.hallucination_risk && data.hallucination_risk !== 'low' && (
            <div className={`p-4 rounded-xl border-l-4 ${
                data.hallucination_risk === 'high'
                    ? 'bg-rose-500/10 border-rose-500 text-rose-200'
                    : 'bg-amber-500/10 border-amber-500 text-amber-200'
            }`}>
                <div className="flex items-center gap-2 mb-1">
                    <AlertOctagon size={16} className={data.hallucination_risk === 'high' ? 'text-rose-400' : 'text-amber-400'} />
                    <span className="font-bold text-sm uppercase">
                        Reality Check: {data.hallucination_risk} hallucination risk
                        {data.hallucination_score != null && ` (score: ${data.hallucination_score}/100)`}
                    </span>
                </div>
                {data.hallucination_flags && data.hallucination_flags.length > 0 && (
                    <div className="space-y-0.5 mt-2">
                        {data.hallucination_flags.map((flag, i) => (
                            <div key={i} className="text-[11px] font-mono opacity-80">- {flag}</div>
                        ))}
                    </div>
                )}
                {data.hallucination_risk === 'high' && (
                    <div className="mt-2 text-[11px] font-mono opacity-60">
                        This summary was automatically blocked. Rescan the company to generate fresh data.
                    </div>
                )}
            </div>
        )}

        {/* GENERAL SUMMARY — plain prose, no inline links (links shown in AI Diagnosis) */}
        {data.general_summary && (
            <div className="bg-gradient-to-r from-slate-800/60 to-slate-700/40 p-8 rounded-2xl border-l-4 border-slate-500 backdrop-blur-sm">
                <h3 className="text-xl font-bold text-white mb-4 flex items-center gap-2">
                    <Search className="text-slate-400" size={22} />
                    General Summary
                    {data.general_summary_verified === false && (
                        <span className="ml-2 text-xs font-normal text-amber-400 bg-amber-400/10 px-2 py-0.5 rounded-full border border-amber-400/20">
                            unverified claims removed
                        </span>
                    )}
                </h3>
                <div>
                    {data.general_summary.split(/\n\n+/).map((para, pi) => (
                        <p key={pi} className={`text-slate-200 leading-relaxed text-base font-light${pi > 0 ? " mt-4" : ""}`}>
                            {para}
                        </p>
                    ))}
                </div>
            </div>
        )}

        <div className="bg-gradient-to-r from-indigo-900/30 to-purple-900/30 p-8 rounded-2xl border-l-4 border-indigo-500 backdrop-blur-sm">
            <h3 className="text-xl font-bold text-white mb-4 flex items-center gap-2">
            <ShieldCheck className="text-indigo-400" />
            AI Diagnosis
            </h3>
            <p className="text-slate-200 leading-relaxed whitespace-pre-line text-lg font-light">
                <HighlightText text={data.summary_text} />
            </p>
        </div>

        {/* REALITY CHECKLIST SECTION */}
        {data.reality_checklist && data.reality_checklist.length > 0 && (
            <div className="bg-white/5 p-6 rounded-2xl border border-white/10 backdrop-blur-md">
                <h3 className="text-lg font-bold text-white mb-4 flex items-center gap-2">
                    <CheckCircle className="text-emerald-400" size={20} />
                    Forensic Reality Checklist
                </h3>
                <div className="grid grid-cols-1 md:grid-cols-2 gap-3">
                    {data.reality_checklist.map((item, idx) => {
                        let borderColor = "border-white/5";
                        let bgColor = "bg-black/20";
                        let icon = <div className="w-2 h-2 rounded-full bg-slate-600" />;
                        let textColor = "text-slate-400";
                        
                        if (item.found) {
                            if (item.sentiment === 'negative') {
                                borderColor = "border-rose-500/30";
                                bgColor = "bg-rose-500/10";
                                icon = <AlertTriangle size={14} className="text-rose-400" />;
                                textColor = "text-rose-200";
                            } else {
                                borderColor = "border-emerald-500/30";
                                bgColor = "bg-emerald-500/10";
                                icon = <CheckCircle size={14} className="text-emerald-400" />;
                                textColor = "text-emerald-200";
                            }
                        }

                        return (
                            <div key={idx} className={`flex items-center justify-between p-3 rounded-lg border ${borderColor} ${bgColor}`}>
                                <div className="flex items-center gap-3 overflow-hidden">
                                    <div className="shrink-0">{icon}</div>
                                    <div className="flex flex-col min-w-0">
                                        <span className={`text-xs font-medium ${textColor} truncate`}>
                                            {item.query || item.category}
                                        </span>
                                        <span className="text-[10px] text-slate-500 uppercase tracking-wider">{item.category}</span>
                                    </div>
                                </div>
                                {item.found && item.is_recent && (
                                    <span className="shrink-0 text-[9px] font-bold bg-cyan-500/20 text-cyan-300 border border-cyan-500/30 px-1.5 py-0.5 rounded ml-2">
                                        RECENT
                                    </span>
                                )}
                            </div>
                        );
                    })}
                </div>
            </div>
        )}

        <div className="grid grid-cols-1 lg:grid-cols-2 gap-8">
            <div className="bg-white/5 p-6 rounded-2xl border border-white/10 backdrop-blur-md">
            <h3 className="text-lg font-bold text-white mb-6 flex items-center gap-2">
                <span className="w-1 h-6 bg-indigo-500 rounded-full"></span>
                Ambition Breakdown
            </h3>
            <div className="h-[300px] w-full">
                <ResponsiveContainer width="100%" height="100%">
                <RadarChart cx="50%" cy="50%" outerRadius="80%" data={radarData}>
                    <PolarGrid stroke="#334155" />
                    <PolarAngleAxis dataKey="subject" tick={{ fill: '#94a3b8', fontSize: 11, fontWeight: 500 }} />
                    <PolarRadiusAxis angle={30} domain={[0, 100]} tick={false} axisLine={false} />
                    <Radar name="Ambition" dataKey="A" stroke="#818cf8" strokeWidth={2} fill="#6366f1" fillOpacity={0.3} />
                    <RechartsTooltip 
                        contentStyle={{ backgroundColor: '#0f172a', borderColor: '#334155', color: '#f1f5f9', borderRadius: '8px' }}
                        itemStyle={{ color: '#818cf8' }}
                    />
                </RadarChart>
                </ResponsiveContainer>
            </div>
            </div>

            <div className="bg-white/5 p-6 rounded-2xl border border-white/10 backdrop-blur-md flex flex-col h-full">
            <h3 className="text-lg font-bold text-white mb-6 flex items-center gap-2">
                <span className="w-1 h-6 bg-emerald-500 rounded-full"></span>
                Deep Dive Findings
            </h3>

            <div className="flex-grow overflow-y-auto max-h-[450px] pr-2 space-y-3 custom-scrollbar">
                {(() => {
                    // Merge current + historical evidence, de-duped by URL
                    const currentLinks = (data.evidence_links || []).map(l => ({ ...l, _scan: 'current' }));
                    const seenUrls = new Set(currentLinks.map(l => l.url).filter(Boolean));
                    const historyLinks = [];
                    if (data.history && data.history.length > 0) {
                        for (const h of [...data.history].reverse()) {
                            const scanDate = h.date ? new Date(h.date).toLocaleDateString('en-US', { month: 'short', year: '2-digit' }) : '?';
                            for (const ev of (h.evidence || [])) {
                                if (ev.url && !seenUrls.has(ev.url)) {
                                    seenUrls.add(ev.url);
                                    historyLinks.push({ ...ev, _scan: scanDate });
                                }
                            }
                        }
                    }
                    const allLinks = [...currentLinks, ...historyLinks];
                    const extractDomain = (url) => { try { return new URL(url).hostname.replace('www.', ''); } catch { return url; } };

                    if (allLinks.length === 0) {
                        return (
                            <div className="flex flex-col items-center justify-center h-full text-slate-600 border-2 border-dashed border-white/5 rounded-xl">
                                <AlertTriangle size={32} className="mb-2 opacity-50" />
                                <p className="text-sm">No specific evidence returned.</p>
                            </div>
                        );
                    }

                    return allLinks.map((link, idx) => (
                        <a
                            key={idx}
                            href={link.url}
                            target="_blank"
                            rel="noopener noreferrer"
                            className="block p-4 rounded-xl bg-black/20 border border-white/5 hover:border-emerald-500/50 hover:bg-emerald-500/5 transition-all group"
                        >
                            <div className="flex items-start justify-between gap-2">
                                <div className="min-w-0 flex-grow">
                                    <h4 className="font-medium text-slate-200 group-hover:text-emerald-400 transition-colors text-sm line-clamp-1">
                                        <HighlightText text={link.title || extractDomain(link.url)} />
                                    </h4>
                                    {link.snippet && (
                                        <p className="text-[11px] text-slate-400 mt-1 line-clamp-2 leading-relaxed">{link.snippet}</p>
                                    )}
                                    <div className="flex items-center gap-2 mt-1">
                                        <span className="text-[10px] text-slate-500 font-mono truncate">{link.source || extractDomain(link.url)}</span>
                                        {link._scan !== 'current' && (
                                            <span className="text-[9px] bg-slate-700/60 text-slate-400 px-1.5 py-0.5 rounded font-mono shrink-0">
                                                {link._scan}
                                            </span>
                                        )}
                                        {link.verified === false && (
                                            <span className="text-[9px] bg-rose-500/20 text-rose-400 px-1.5 py-0.5 rounded font-mono shrink-0">
                                                unverified
                                            </span>
                                        )}
                                    </div>
                                </div>
                                <ExternalLink size={16} className="text-slate-600 group-hover:text-emerald-400 mt-1 shrink-0" />
                            </div>
                        </a>
                    ));
                })()}
            </div>
            </div>
        </div>

        {/* KEY STAKEHOLDERS SECTION */}
        {data.key_people && data.key_people.length > 0 && (
            <div className="bg-white/5 p-6 rounded-2xl border border-white/10 backdrop-blur-md">
                <h3 className="text-lg font-bold text-white mb-4 flex items-center gap-2">
                    <Users className="text-indigo-400" size={20} />
                    Key Modernization Stakeholders
                </h3>
                <div className="grid grid-cols-1 gap-3">
                    {data.key_people.map((person, idx) => {
                        const insights = person.linkedin_insights;
                        const hasInsights = insights && insights.found;
                        return (
                            <div key={idx} className="rounded-lg border border-white/5 bg-black/20 hover:border-indigo-500/30 transition-colors">
                                <div className="flex items-center justify-between p-3">
                                    <div className="flex flex-col min-w-0 mr-2">
                                        <span className="font-bold text-sm text-slate-200 truncate">{person.name}</span>
                                        <span className="text-xs text-indigo-300 truncate">{person.role}</span>
                                        {person.relevance && <span className="text-[10px] text-slate-500 mt-1 line-clamp-1">{person.relevance}</span>}
                                    </div>
                                    <a
                                        href={`https://www.linkedin.com/search/results/people/?keywords=${encodeURIComponent(person.name + " " + data.companyName)}`}
                                        target="_blank"
                                        rel="noopener noreferrer"
                                        className="shrink-0 p-2 rounded-full bg-white/5 hover:bg-[#0077b5] hover:text-white text-slate-500 transition-all"
                                        title="Find on LinkedIn"
                                    >
                                        <ExternalLink size={16} />
                                    </a>
                                </div>
                                {insights && (
                                    <div className={`px-3 pb-3 border-t ${hasInsights ? 'border-indigo-500/15' : 'border-white/5'}`}>
                                        {hasInsights ? (
                                            <div className="pt-2 space-y-1.5">
                                                {insights.headline && (
                                                    <p className="text-[11px] text-indigo-300 font-medium">{insights.headline}</p>
                                                )}
                                                <p className="text-[11px] text-slate-400 leading-relaxed">{insights.summary}</p>
                                                {insights.topics && insights.topics.length > 0 && (
                                                    <div className="flex flex-wrap gap-1 mt-1">
                                                        {insights.topics.slice(0, 5).map((topic, ti) => (
                                                            <span key={ti} className="text-[9px] px-1.5 py-0.5 rounded-full bg-indigo-500/15 text-indigo-300 border border-indigo-500/20">{topic}</span>
                                                        ))}
                                                    </div>
                                                )}
                                                {insights.career_moves && insights.career_moves.length > 0 && (
                                                    <div className="mt-1">
                                                        <span className="text-[9px] text-slate-600 uppercase tracking-wider">Previous:</span>
                                                        <span className="text-[10px] text-slate-500 ml-1">{insights.career_moves.slice(0, 2).join(" → ")}</span>
                                                    </div>
                                                )}
                                                {insights.notable_posts && insights.notable_posts.length > 0 && (
                                                    <div className="mt-1">
                                                        <span className="text-[9px] text-slate-600 uppercase tracking-wider">Shared publicly:</span>
                                                        {insights.notable_posts.slice(0, 2).map((post, pi) => (
                                                            <p key={pi} className="text-[10px] text-slate-500 ml-2 mt-0.5">— {post}</p>
                                                        ))}
                                                    </div>
                                                )}
                                            </div>
                                        ) : (
                                            <p className="text-[10px] text-slate-600 pt-2 italic">{insights.summary || "No public LinkedIn insights found."}</p>
                                        )}
                                    </div>
                                )}
                            </div>
                        );
                    })}
                </div>
            </div>
        )}

        {/* STRATEGIC PITCH SECTION */}
        {data.pitch_text && (
            <div className="bg-gradient-to-r from-cyan-900/30 to-blue-900/30 p-6 rounded-2xl border border-cyan-500/30 backdrop-blur-md shadow-lg shadow-cyan-900/10">
                <h3 className="text-lg font-bold text-white mb-3 flex items-center gap-2">
                    <Sparkles className="text-cyan-400" size={20} />
                    Strategic Outreach Pitch
                </h3>
                <div className="relative group">
                    <div className="bg-black/40 p-5 rounded-xl border border-white/10 font-mono text-xs text-slate-300 leading-relaxed">
                        {data.pitch_text.split(/\n\n+/).map((para, pi, arr) => (
                            <p key={pi} className={pi > 0 ? "mt-3" : ""}>
                                {pi === 0 ? "\u201c" : ""}{para}{pi === arr.length - 1 ? "\u201d" : ""}
                            </p>
                        ))}
                    </div>
                    <button
                        onClick={handleCopyPitch}
                        className={`absolute top-2 right-2 p-2 rounded transition-all shadow-md flex items-center gap-2 ${pitchCopied ? 'bg-emerald-600 text-white' : 'bg-white/10 hover:bg-cyan-600 hover:text-white text-slate-400'}`}
                        title="Copy Pitch to Clipboard"
                    >
                        {pitchCopied ? <Check size={14} /> : <Copy size={14} />}
                        {pitchCopied && <span className="text-[10px] font-bold">COPIED</span>}
                    </button>
                </div>
                <p className="text-[10px] text-slate-500 mt-3 flex items-center gap-1.5 opacity-70">
                     <Info size={12} />
                     Generated based on detected constraints: {data.reality_checklist ? data.reality_checklist.filter(i => i.sentiment === 'negative').slice(0, 3).map(i => i.query).join(", ") : "General constraints"}
                </p>
            </div>
        )}

        {/* WHY & WHAT PROPOSALS */}
        {data.why_proposals && data.why_proposals.length > 0 && data.what_proposals && data.what_proposals.length > 0 && (
            <div className="bg-gradient-to-r from-amber-900/20 to-orange-900/20 p-6 rounded-2xl border border-amber-500/30 backdrop-blur-md shadow-lg shadow-amber-900/10">
                <h3 className="text-lg font-bold text-white mb-4 flex items-center gap-2">
                    <Zap className="text-amber-400" size={20} />
                    Proposed WHY & WHAT
                </h3>
                <div className="relative group">
                    <div className="space-y-4">
                        <div>
                            <p className="text-[11px] font-bold text-amber-400 uppercase tracking-wider mb-2">WHY — Desired Impact</p>
                            <div className="space-y-2">
                                {data.why_proposals.map((why, i) => (
                                    <div key={i} className="bg-black/30 p-3 rounded-lg border border-amber-500/15">
                                        <p className="text-xs text-slate-300 leading-relaxed">
                                            <span className="text-amber-400 font-bold mr-2">{i + 1}.</span>{why}
                                        </p>
                                    </div>
                                ))}
                            </div>
                        </div>
                        <div>
                            <p className="text-[11px] font-bold text-orange-400 uppercase tracking-wider mb-2">WHAT — Desired Effect</p>
                            <div className="space-y-2">
                                {data.what_proposals.map((what, i) => (
                                    <div key={i} className="bg-black/30 p-3 rounded-lg border border-orange-500/15">
                                        <p className="text-xs text-slate-300 leading-relaxed">
                                            <span className="text-orange-400 font-bold mr-2">{i + 1}.</span>{what}
                                        </p>
                                    </div>
                                ))}
                            </div>
                        </div>
                    </div>
                    <button
                        onClick={handleCopyWhyWhat}
                        className={`absolute top-0 right-0 p-2 rounded transition-all shadow-md flex items-center gap-2 ${whyWhatCopied ? 'bg-emerald-600 text-white' : 'bg-white/10 hover:bg-amber-600 hover:text-white text-slate-400'}`}
                        title="Copy WHY & WHAT to Clipboard"
                    >
                        {whyWhatCopied ? <Check size={14} /> : <Copy size={14} />}
                        {whyWhatCopied && <span className="text-[10px] font-bold">COPIED</span>}
                    </button>
                </div>
                <p className="text-[10px] text-slate-500 mt-3 flex items-center gap-1.5 opacity-70">
                    <Info size={12} />
                    Proposals derived from the weakest Knowledge Base dimensions — intended as conversation starters, not promises.
                </p>
            </div>
        )}

        {/* === REPORT-ONLY SECTIONS (hidden in modal, visible in downloaded HTML) === */}
        {/* These sections are wrapped with report-only class which is display:none in the app
            but the downloaded HTML overrides it to display:block */}

        {data.detailed_report && (
            <div className="report-only-section" style={{ display: 'none' }}>
                <div className="bg-gradient-to-r from-violet-900/30 to-indigo-900/30 p-8 rounded-2xl border-l-4 border-violet-500 backdrop-blur-sm">
                    <h3 className="text-xl font-bold text-white mb-2 flex items-center gap-2">
                        <ShieldCheck className="text-violet-400" size={22} />
                        Detailed Forensic Report
                    </h3>
                    <p className="text-[10px] text-slate-500 mb-4 uppercase tracking-wider font-mono">
                        Deep diagnostic with evidence and stakeholder context
                        {data.detailed_report_model && ` — ${data.detailed_report_model}`}
                    </p>
                    <div>
                        {data.detailed_report.split(/\n\n+/).map((para, pi) => (
                            <p key={pi} className={`text-slate-200 leading-relaxed text-sm font-light${pi > 0 ? " mt-4" : ""}`}>
                                {para}
                            </p>
                        ))}
                    </div>
                </div>
            </div>
        )}

        {data.foresight_summary && (
            <div className="report-only-section" style={{ display: 'none' }}>
                <div className="bg-gradient-to-r from-teal-900/30 to-cyan-900/30 p-8 rounded-2xl border-l-4 border-teal-500 backdrop-blur-sm">
                    <h3 className="text-xl font-bold text-white mb-2 flex items-center gap-2">
                        <TrendingUp className="text-teal-400" size={22} />
                        Future Insight
                    </h3>
                    <p className="text-[10px] text-slate-500 mb-4 uppercase tracking-wider font-mono">
                        Forward-looking narrative based on strategic foresight analysis
                        {data.foresight_summary_model && ` — ${data.foresight_summary_model}`}
                    </p>
                    <div>
                        {data.foresight_summary.split(/\n\n+/).map((para, pi) => (
                            <p key={pi} className={`text-slate-200 leading-relaxed text-sm font-light${pi > 0 ? " mt-4" : ""}`}>
                                {para}
                            </p>
                        ))}
                    </div>
                </div>
            </div>
        )}
      </div>
    </div>
  );
};

(window as any).CompanySummary = CompanySummary;
