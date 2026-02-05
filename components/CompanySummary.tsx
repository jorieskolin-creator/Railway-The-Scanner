import React from 'react';
import { AnalysisResult } from '../types';
import { ResponsiveContainer, RadarChart, PolarGrid, PolarAngleAxis, PolarRadiusAxis, Radar, Tooltip } from 'recharts';
import { ExternalLink, CheckCircle, AlertTriangle, ShieldCheck, ServerCrash } from 'lucide-react';

interface CompanySummaryProps {
  data: AnalysisResult;
}

export const CompanySummary: React.FC<CompanySummaryProps> = ({ data }) => {
  const gapColor = data.reality_gap > 20 ? '#ef4444' : data.reality_gap < -20 ? '#f59e0b' : '#10b981';
  
  const radarData = data.intent_breakdown.map(item => ({
    subject: item.name,
    A: item.score,
    fullMark: 100,
  }));

  return (
    <div className="space-y-8">
      
      {/* Simulation Warning */}
      {data.is_simulated && (
        <div className="bg-amber-500/10 border border-amber-500/40 rounded-lg p-3 flex items-start gap-3">
            <ServerCrash className="text-amber-500 shrink-0 mt-0.5" size={18} />
            <div>
                <h4 className="text-amber-400 text-sm font-bold">Simulated Reality Data</h4>
                <p className="text-amber-200/70 text-xs mt-1">
                    The Reality Scanner could not access Google Search API (Config Restriction). 
                    Scores shown are projected estimates, not live X-Ray evidence.
                </p>
            </div>
        </div>
      )}

      {/* Top Cards: Scores */}
      <div className="grid grid-cols-1 md:grid-cols-3 gap-4">
        <div className="bg-white/5 backdrop-blur-md p-6 rounded-2xl border border-white/10 flex flex-col items-center justify-center relative group overflow-hidden">
          <div className="absolute inset-0 bg-indigo-500/10 opacity-0 group-hover:opacity-100 transition-opacity"></div>
          <span className="text-indigo-200 font-medium mb-1 text-sm tracking-widest uppercase">Intent Score</span>
          <span className="text-6xl font-black text-white tracking-tighter drop-shadow-[0_0_15px_rgba(99,102,241,0.5)]">{data.intent_score}</span>
          <span className="text-xs text-slate-400 mt-2 font-mono">Narrative Analysis</span>
        </div>
        
        <div className="bg-white/5 backdrop-blur-md p-6 rounded-2xl border border-white/10 flex flex-col items-center justify-center relative group overflow-hidden">
          <div className="absolute inset-0 bg-emerald-500/10 opacity-0 group-hover:opacity-100 transition-opacity"></div>
          <span className="text-emerald-200 font-medium mb-1 text-sm tracking-widest uppercase">Reality Score</span>
          <span className="text-6xl font-black text-white tracking-tighter drop-shadow-[0_0_15px_rgba(16,185,129,0.5)]">{data.reality_score}</span>
          <span className="text-xs text-slate-400 mt-2 font-mono">Evidence Check</span>
        </div>
        
        <div className="bg-white/5 backdrop-blur-md p-6 rounded-2xl border border-white/10 flex flex-col items-center justify-center relative overflow-hidden group">
          <div className="absolute inset-0 bg-gradient-to-br from-transparent to-white/5 opacity-0 group-hover:opacity-100 transition-opacity"></div>
          <span className="text-slate-300 font-medium mb-1 text-sm tracking-widest uppercase">Reality Gap</span>
          <span className="text-6xl font-black tracking-tighter" style={{ color: gapColor, textShadow: `0 0 20px ${gapColor}40` }}>
            {data.reality_gap > 0 ? `+${data.reality_gap}` : data.reality_gap}
          </span>
          <span className="text-xs text-slate-400 mt-2 font-mono">Intent minus Reality</span>
          {data.reality_gap > 30 && (
             <div className="absolute top-2 right-2 text-rose-500 animate-pulse" title="High Gap Warning">
               <AlertTriangle size={20} />
             </div>
          )}
        </div>
      </div>

      {/* AI Summary */}
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
        {/* Charts */}
        <div className="bg-white/5 p-6 rounded-2xl border border-white/10 backdrop-blur-md">
          <h3 className="text-lg font-bold text-white mb-6 flex items-center gap-2">
             <span className="w-1 h-6 bg-indigo-500 rounded-full"></span>
             Intent Breakdown
          </h3>
          <div className="h-[300px] w-full">
            <ResponsiveContainer width="100%" height="100%">
              <RadarChart cx="50%" cy="50%" outerRadius="80%" data={radarData}>
                <PolarGrid stroke="#334155" />
                <PolarAngleAxis dataKey="subject" tick={{ fill: '#94a3b8', fontSize: 11, fontWeight: 500 }} />
                <PolarRadiusAxis angle={30} domain={[0, 100]} tick={false} axisLine={false} />
                <Radar name="Intent" dataKey="A" stroke="#818cf8" strokeWidth={2} fill="#6366f1" fillOpacity={0.3} />
                <Tooltip 
                    contentStyle={{ backgroundColor: '#0f172a', borderColor: '#334155', color: '#f1f5f9', borderRadius: '8px' }}
                    itemStyle={{ color: '#818cf8' }}
                />
              </RadarChart>
            </ResponsiveContainer>
          </div>
        </div>

        {/* Evidence List */}
        <div className="bg-white/5 p-6 rounded-2xl border border-white/10 backdrop-blur-md flex flex-col">
          <h3 className="text-lg font-bold text-white mb-6 flex items-center gap-2">
            <span className="w-1 h-6 bg-emerald-500 rounded-full"></span>
            Verified Evidence
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
                 <p className="text-sm">No positive evidence found via X-Ray search.</p>
               </div>
             )}
          </div>
          
          <div className="mt-6 pt-4 border-t border-white/10">
            <h4 className="text-xs font-semibold text-slate-500 uppercase tracking-wider mb-3">X-Ray Checklist</h4>
            <div className="grid grid-cols-2 gap-3">
              {data.reality_checklist.slice(0, 6).map((item, idx) => (
                <div key={idx} className="flex items-center gap-2 text-xs text-slate-400">
                  {item.found ? (
                    <CheckCircle size={14} className="text-emerald-500 shrink-0 shadow-[0_0_8px_rgba(16,185,129,0.5)]" />
                  ) : (
                    <div className="w-3.5 h-3.5 rounded-full border border-slate-700 bg-slate-800/50 shrink-0" />
                  )}
                  <span className={item.found ? 'text-slate-200' : 'opacity-60'}>
                    {item.category.split(" ")[0]}...
                  </span>
                </div>
              ))}
            </div>
          </div>
        </div>
      </div>
    </div>
  );
};