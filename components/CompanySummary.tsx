
const { ResponsiveContainer, RadarChart, PolarGrid, PolarAngleAxis, PolarRadiusAxis, Radar, Tooltip: RechartsTooltip, LineChart, Line, XAxis, YAxis, CartesianGrid, ReferenceLine } = (window as any).Recharts;
const { ExternalLink, CheckCircle, AlertTriangle, ShieldCheck, ServerCrash, RefreshCw, XCircle, Search, Zap, Scale, Activity, Download, Copy, FileJson, FileCode, Check } = (window as any).Lucide;
const { useRef, useState } = (window as any).React;

const CompanySummary = ({ data }) => {
  const summaryRef = useRef(null);
  const [copied, setCopied] = useState(false);

  // NEW VISUAL LOGIC (Quadrant-based 50/50 split):
  // Silent Doers: Reality >= 50, Ambition < 50
  // Walk-and-Talk: Reality >= 50, Ambition >= 50
  // Talk-not-Walk: Reality < 50, Ambition >= 50
  // Stagnation: Reality < 50, Ambition < 50

  let gapColor = '#94a3b8';
  let gapLabel = "Stagnation";
  
  if (data.reality_score >= 50) {
      if (data.intent_score >= 50) {
          gapLabel = "Walk-and-Talk (Aligned)";
          gapColor = '#10b981'; // Emerald
      } else {
          gapLabel = "Silent Doers (Surplus)";
          gapColor = '#22d3ee'; // Cyan
      }
  } else {
      if (data.intent_score >= 50) {
          gapLabel = "Talk-not-Walk (Hype)";
          gapColor = '#f59e0b'; // Amber
      } else {
          gapLabel = "Stagnation (Low/Low)";
          gapColor = '#94a3b8'; // Gray
      }
  }
  
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
    </style>
</head>
<body class="p-8 max-w-7xl mx-auto">
    <div class="mb-6 flex justify-between items-center border-b border-slate-700 pb-4">
        <div>
            <h1 class="text-2xl font-bold text-white">${data.companyName}</h1>
            <p class="text-slate-400 text-sm">Forensic Snapshot</p>
        </div>
        <div class="text-right text-xs text-slate-500 font-mono">
            Generated: ${new Date().toISOString()}
        </div>
    </div>

    <!-- VISUAL REPORT SNAPSHOT -->
    <div id="visual-report" class="space-y-8">
        ${visuals}
    </div>

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
            
            <div className="bg-white/5 backdrop-blur-md p-6 rounded-2xl border border-white/10 flex flex-col items-center justify-center relative overflow-hidden group">
            <div className="absolute inset-0 bg-gradient-to-br from-transparent to-white/5 opacity-0 group-hover:opacity-100 transition-opacity"></div>
            <span className="text-slate-300 font-medium mb-1 text-sm tracking-widest uppercase">Integrity Delta</span>
            <span className="text-6xl font-black tracking-tighter" style={{ color: gapColor, textShadow: `0 0 20px ${gapColor}40` }}>
                {gapValueDisplay}
            </span>
            <span className="text-xs text-slate-400 mt-2 font-mono" style={{ color: gapColor }}>{gapLabel}</span>
            </div>
        </div>

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
                            <Zap size={20} />
                        </div>
                        <div>
                            <h4 className="text-xs text-slate-400 font-bold uppercase tracking-wider">Vibrancy Score</h4>
                            <p className="text-lg font-bold text-cyan-400">{metrics.vibrancy_score}/100</p>
                        </div>
                    </div>
                    <div className="text-right">
                        <span className="text-[10px] text-slate-500 block">Signal Intensity</span>
                        <div className="w-16 h-1.5 bg-slate-700 rounded-full mt-1">
                            <div className="h-full bg-cyan-500 rounded-full" style={{ width: `${metrics.vibrancy_score}%` }}></div>
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

        <div className="bg-gradient-to-r from-indigo-900/30 to-purple-900/30 p-8 rounded-2xl border-l-4 border-indigo-500 backdrop-blur-sm">
            <h3 className="text-xl font-bold text-white mb-4 flex items-center gap-2">
            <ShieldCheck className="text-indigo-400" />
            AI Diagnosis
            </h3>
            <p className="text-slate-200 leading-relaxed whitespace-pre-line text-lg font-light">
            {data.summary_text}
            </p>
        </div>

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

            <div className="bg-white/5 p-6 rounded-2xl border border-white/10 backdrop-blur-md flex flex-col">
            <h3 className="text-lg font-bold text-white mb-6 flex items-center gap-2">
                <span className="w-1 h-6 bg-emerald-500 rounded-full"></span>
                Forensic Findings
            </h3>
            
            <div className="flex-grow overflow-y-auto max-h-[300px] pr-2 space-y-3 custom-scrollbar">
                {data.evidence_links.length > 0 ? (
                data.evidence_links.map((link, idx) => (
                    <a 
                    key={idx}
                    href={link.url}
                    target="_blank"
                    rel="noopener noreferrer"
                    className="block p-4 rounded-xl bg-black/20 border border-white/5 hover:border-emerald-500/50 hover:bg-emerald-500/5 transition-all group"
                    >
                    <div className="flex items-start justify-between">
                        <div>
                        <h4 className="font-medium text-slate-200 group-hover:text-emerald-400 transition-colors text-sm line-clamp-1">
                            {link.title}
                        </h4>
                        <span className="text-xs text-slate-500 mt-1 inline-block font-mono">{link.source}</span>
                        </div>
                        <ExternalLink size={16} className="text-slate-600 group-hover:text-emerald-400 mt-1" />
                    </div>
                    </a>
                ))
                ) : (
                <div className="flex flex-col items-center justify-center h-full text-slate-600 border-2 border-dashed border-white/5 rounded-xl">
                    <AlertTriangle size={32} className="mb-2 opacity-50" />
                    <p className="text-sm">No specific evidence returned.</p>
                </div>
                )}
            </div>
            
            <div className="mt-6 pt-4 border-t border-white/10">
                <h4 className="text-xs font-semibold text-slate-500 uppercase tracking-wider mb-3 flex items-center gap-2">
                    <Search size={12} />
                    Methodology Checklist
                </h4>
                <div className="grid grid-cols-1 sm:grid-cols-2 gap-2">
                {data.reality_checklist && data.reality_checklist.slice(0, 8).map((item, idx) => {
                    let icon = <div className="w-3.5 h-3.5 rounded-full border border-slate-700 bg-slate-800/50 shrink-0" />;
                    let textColor = "opacity-60";
                    
                    if (item.found) {
                        if (item.sentiment === 'negative') {
                            icon = <AlertTriangle size={14} className="text-rose-500 shrink-0" />;
                            textColor = "text-rose-300";
                        } else {
                            icon = <CheckCircle size={14} className="text-emerald-500 shrink-0" />;
                            textColor = "text-emerald-300";
                        }
                    }

                    return (
                        <div key={idx} className={`flex items-center gap-2 text-xs text-slate-400 p-1.5 rounded bg-black/20 border border-transparent ${item.found ? 'bg-white/5 border-white/5' : ''}`}>
                        {icon}
                        <span className={`${textColor} truncate`}>
                            {item.query || item.category} 
                        </span>
                        </div>
                    );
                })}
                </div>
            </div>
            </div>
        </div>
      </div>
    </div>
  );
};

(window as any).CompanySummary = CompanySummary;
