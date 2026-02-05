import React, { useState } from 'react';
import { AnalysisResult } from '../types';
import { ResponsiveContainer, RadarChart, PolarGrid, PolarAngleAxis, PolarRadiusAxis, Radar, Tooltip } from 'recharts';
import { X, ExternalLink, RefreshCw, AlertTriangle, ShieldCheck, CheckCircle } from 'lucide-react';

interface CompanyModalProps {
  company: AnalysisResult | null;
  isOpen: boolean;
  onClose: () => void;
}

export const CompanyModal: React.FC<CompanyModalProps> = ({ company, isOpen, onClose }) => {
  const [isRefreshing, setIsRefreshing] = useState(false);

  if (!isOpen || !company) return null;

  const handleRefresh = () => {
    setIsRefreshing(true);
    setTimeout(() => {
      setIsRefreshing(false);
      alert("Analysis refreshed (Simulation)");
    }, 2000);
  };

  const radarData = company.intent_breakdown.map(item => ({
    subject: item.name,
    A: item.score,
    fullMark: 100,
  }));

  const isGreenwashing = company.reality_gap > 30;

  return (
    <div className="fixed inset-0 z-50 flex justify-end">
      {/* Backdrop */}
      <div className="absolute inset-0 bg-black/60 backdrop-blur-sm transition-opacity" onClick={onClose} />

      {/* Slide-over Panel */}
      <div className="relative w-full max-w-2xl bg-[#0B0F19] h-full shadow-2xl border-l border-white/10 flex flex-col transform transition-transform animate-in slide-in-from-right duration-300">
        
        {/* Header */}
        <div className="p-6 border-b border-white/10 flex items-start justify-between bg-[#0B0F19] z-10">
          <div>
            <h2 className="text-2xl font-bold text-white tracking-tight">{company.companyName}</h2>
            <div className="flex items-center gap-2 mt-1">
                <span className={`w-2 h-2 rounded-full ${isGreenwashing ? 'bg-rose-500 animate-pulse' : 'bg-emerald-500'}`}></span>
                <p className="text-slate-400 text-sm font-mono">ID: {company.id.substring(0,8)}</p>
            </div>
          </div>
          <button onClick={onClose} className="text-slate-500 hover:text-white transition-colors p-2 hover:bg-white/10 rounded-full">
            <X size={24} />
          </button>
        </div>

        {/* Content - Scrollable */}
        <div className="flex-1 overflow-y-auto p-6 space-y-8 custom-scrollbar">
          
          {/* Key Metrics */}
          <div className="grid grid-cols-3 gap-4">
            <div className="bg-white/5 p-4 rounded-xl border border-white/10 text-center">
              <span className="text-slate-500 text-xs uppercase tracking-wider font-semibold">Intent</span>
              <div className="text-3xl font-black text-indigo-400 mt-1">{company.intent_score}</div>
            </div>
            <div className="bg-white/5 p-4 rounded-xl border border-white/10 text-center">
              <span className="text-slate-500 text-xs uppercase tracking-wider font-semibold">Reality</span>
              <div className="text-3xl font-black text-emerald-400 mt-1">{company.reality_score}</div>
            </div>
            <div className="bg-white/5 p-4 rounded-xl border border-white/10 text-center relative overflow-hidden">
              <span className="text-slate-500 text-xs uppercase tracking-wider font-semibold">Gap</span>
              <div className={`text-3xl font-black mt-1 ${company.reality_gap > 20 ? 'text-rose-400' : 'text-slate-200'}`}>
                {company.reality_gap > 0 ? '+' : ''}{company.reality_gap}
              </div>
              {isGreenwashing && (
                <div className="absolute inset-0 bg-rose-500/10 flex items-center justify-center backdrop-blur-[1px] border border-rose-500/20 rounded-xl">
                  <span className="text-xs font-bold text-rose-400 flex flex-col items-center uppercase tracking-widest">
                    High Risk
                  </span>
                </div>
              )}
            </div>
          </div>

          {/* Warning Badge */}
          {isGreenwashing && (
            <div className="bg-rose-500/10 border border-rose-500/30 rounded-xl p-4 flex items-start gap-3 text-rose-200 text-sm shadow-[0_0_20px_-5px_rgba(244,63,94,0.2)]">
              <AlertTriangle className="shrink-0 mt-0.5" size={18} />
              <span>
                <strong className="block text-rose-100 mb-1">Warning: Reality Gap Detected</strong>
                Use of "Adaptive" buzzwords significantly exceeds operational evidence found in public domain. Potential Greenwashing.
              </span>
            </div>
          )}

          {/* AI Summary */}
          <div className="bg-indigo-900/20 rounded-xl p-5 border border-indigo-500/30">
             <h3 className="text-indigo-100 font-bold flex items-center gap-2 mb-3">
               <ShieldCheck className="text-indigo-400" size={18} />
               AI Diagnosis
             </h3>
             <p className="text-indigo-200 text-sm leading-relaxed whitespace-pre-line font-light">
               {company.summary_text}
             </p>
          </div>

          {/* Radar Chart */}
          <div className="bg-white/5 rounded-xl p-6 border border-white/10">
            <h3 className="text-white font-semibold mb-4 text-center text-sm uppercase tracking-wider text-slate-400">Narrative Breakdown</h3>
            <div className="h-[250px] w-full">
              <ResponsiveContainer width="100%" height="100%">
                <RadarChart cx="50%" cy="50%" outerRadius="70%" data={radarData}>
                  <PolarGrid stroke="#334155" />
                  <PolarAngleAxis dataKey="subject" tick={{ fill: '#94a3b8', fontSize: 10 }} />
                  <PolarRadiusAxis angle={30} domain={[0, 100]} tick={false} axisLine={false} />
                  <Radar name="Intent" dataKey="A" stroke="#6366f1" strokeWidth={2} fill="#6366f1" fillOpacity={0.4} />
                  <Tooltip 
                    contentStyle={{ backgroundColor: '#0f172a', borderColor: '#334155', color: '#f1f5f9' }}
                    itemStyle={{ color: '#818cf8' }}
                  />
                </RadarChart>
              </ResponsiveContainer>
            </div>
          </div>

          {/* Evidence List */}
          <div>
            <h3 className="text-white font-semibold mb-4 text-sm uppercase tracking-wider text-slate-400">Verified Evidence</h3>
            <div className="space-y-2">
              {company.evidence_links.length > 0 ? (
                company.evidence_links.map((link, idx) => (
                  <a 
                    key={idx}
                    href={link.url}
                    target="_blank"
                    rel="noopener noreferrer"
                    className="flex items-center justify-between p-3 rounded-lg bg-white/5 border border-white/10 hover:border-emerald-500/50 hover:bg-emerald-500/10 transition-all group"
                  >
                    <div className="flex items-center gap-3 overflow-hidden">
                      <CheckCircle size={16} className="text-emerald-500 shrink-0" />
                      <div className="truncate">
                        <p className="text-slate-200 text-sm truncate group-hover:text-emerald-300 transition-colors">{link.title}</p>
                        <p className="text-slate-500 text-xs font-mono">{link.source}</p>
                      </div>
                    </div>
                    <ExternalLink size={14} className="text-slate-500 group-hover:text-emerald-400 shrink-0 ml-2" />
                  </a>
                ))
              ) : (
                <div className="p-4 border border-dashed border-slate-700 rounded-lg text-center">
                    <p className="text-slate-500 text-sm italic">No direct operational evidence found for target queries.</p>
                </div>
              )}
            </div>
          </div>
        </div>

        {/* Footer Actions */}
        <div className="p-6 border-t border-white/10 bg-[#0B0F19]">
          <button 
            onClick={handleRefresh}
            disabled={isRefreshing}
            className="w-full py-3 bg-white text-black hover:bg-indigo-400 font-bold rounded-lg transition-all flex items-center justify-center gap-2 disabled:opacity-70 disabled:cursor-not-allowed shadow-[0_0_15px_rgba(255,255,255,0.2)] hover:shadow-[0_0_20px_rgba(99,102,241,0.4)]"
          >
            {isRefreshing ? (
              <>
                <div className="w-4 h-4 border-2 border-black/30 border-t-black rounded-full animate-spin" />
                Updating Analysis...
              </>
            ) : (
              <>
                <RefreshCw size={18} />
                Refresh Analysis
              </>
            )}
          </button>
        </div>

      </div>
    </div>
  );
};