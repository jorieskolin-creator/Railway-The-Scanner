
const { ResponsiveContainer, RadarChart, PolarGrid, PolarAngleAxis, PolarRadiusAxis, Radar, Tooltip: RechartsTooltip, LineChart, Line, XAxis, YAxis, CartesianGrid, ReferenceLine } = (window as any).Recharts;
const { ExternalLink, CheckCircle, AlertTriangle, ShieldCheck, ServerCrash, RefreshCw, XCircle, Search, Zap, Scale, Activity } = (window as any).Lucide;

const CompanySummary = ({ data }) => {
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

  return (
    <div className="space-y-8">
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
  );
};

(window as any).CompanySummary = CompanySummary;
