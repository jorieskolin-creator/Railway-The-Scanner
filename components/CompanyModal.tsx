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
      <div className="relative w-full max-w-2xl bg-slate-900 h-full shadow-2xl border-l border-slate-800 flex flex-col transform transition-transform animate-in slide-in-from-right duration-300">
        
        {/* Header */}
        <div className="p-6 border-b border-slate-800 flex items-start justify-between bg-slate-900 z-10">
          <div>
            <h2 className="text-2xl font-bold text-white">{company.companyName}</h2>
            <p className="text-slate-400 text-sm mt-1">Last Analysis: {new Date(company.lastUpdated).toLocaleDateString()}</p>
          </div>
          <button onClick={onClose} className="text-slate-400 hover:text-white transition-colors p-2 hover:bg-slate-800 rounded-full">
            <X size={24} />
          </button>
        </div>

        {/* Content - Scrollable */}
        <div className="flex-1 overflow-y-auto p-6 space-y-8">
          
          {/* Key Metrics */}
          <div className="grid grid-cols-3 gap-4">
            <div className="bg-slate-800/50 p-4 rounded-xl border border-slate-700 text-center">
              <span className="text-slate-400 text-xs uppercase tracking-wider">Intent</span>
              <div className="text-3xl font-bold text-indigo-400 mt-1">{company.intent_score}</div>
            </div>
            <div className="bg-slate-800/50 p-4 rounded-xl border border-slate-700 text-center">
              <span className="text-slate-400 text-xs uppercase tracking-wider">Reality</span>
              <div className="text-3xl font-bold text-emerald-400 mt-1">{company.reality_score}</div>
            </div>
            <div className="bg-slate-800/50 p-4 rounded-xl border border-slate-700 text-center relative overflow-hidden">
              <span className="text-slate-400 text-xs uppercase tracking-wider">Reality Gap</span>
              <div className={`text-3xl font-bold mt-1 ${company.reality_gap > 20 ? 'text-red-400' : 'text-slate-200'}`}>
                {company.reality_gap > 0 ? '+' : ''}{company.reality_gap}
              </div>
              {isGreenwashing && (
                <div className="absolute inset-0 bg-red-500/10 flex items-center justify-center backdrop-blur-[1px]">
                  <span className="text-xs font-bold text-red-400 flex flex-col items-center">
                    <AlertTriangle size={16} /> High Risk
                  </span>
                </div>
              )}
            </div>
          </div>

          {/* Warning Badge */}
          {isGreenwashing && (
            <div className="bg-red-500/10 border border-red-500/30 rounded-lg p-3 flex items-center gap-3 text-red-300 text-sm">
              <AlertTriangle className="shrink-0" size={18} />
              <span>
                <strong>Warning: High Reality Gap.</strong> Use of buzzwords significantly exceeds operational evidence. Potential Greenwashing detected.
              </span>
            </div>
          )}

          {/* AI Summary */}
          <div className="bg-slate-800 rounded-xl p-5 border border-slate-700">
             <h3 className="text-white font-semibold flex items-center gap-2 mb-3">
               <ShieldCheck className="text-indigo-400" size={18} />
               AI Diagnosis
             </h3>
             <p className="text-slate-300 text-sm leading-relaxed whitespace-pre-line">
               {company.summary_text}
             </p>
          </div>

          {/* Radar Chart */}
          <div className="bg-slate-800 rounded-xl p-4 border border-slate-700">
            <h3 className="text-white font-semibold mb-4 text-center">Narrative Breakdown</h3>
            <div className="h-[250px] w-full">
              <ResponsiveContainer width="100%" height="100%">
                <RadarChart cx="50%" cy="50%" outerRadius="70%" data={radarData}>
                  <PolarGrid stroke="#475569" />
                  <PolarAngleAxis dataKey="subject" tick={{ fill: '#94a3b8', fontSize: 10 }} />
                  <PolarRadiusAxis angle={30} domain={[0, 100]} tick={false} axisLine={false} />
                  <Radar name="Intent" dataKey="A" stroke="#6366f1" fill="#6366f1" fillOpacity={0.4} />
                  <Tooltip 
                    contentStyle={{ backgroundColor: '#1e293b', borderColor: '#334155', color: '#f1f5f9' }}
                    itemStyle={{ color: '#818cf8' }}
                  />
                </RadarChart>
              </ResponsiveContainer>
            </div>
          </div>

          {/* Evidence List */}
          <div>
            <h3 className="text-white font-semibold mb-4">Verified Evidence</h3>
            <div className="space-y-2">
              {company.evidence_links.length > 0 ? (
                company.evidence_links.map((link, idx) => (
                  <a 
                    key={idx}
                    href={link.url}
                    target="_blank"
                    rel="noopener noreferrer"
                    className="flex items-center justify-between p-3 rounded-lg bg-slate-800 border border-slate-700 hover:border-emerald-500/50 hover:bg-slate-750 transition-all group"
                  >
                    <div className="flex items-center gap-3 overflow-hidden">
                      <CheckCircle size={16} className="text-emerald-500 shrink-0" />
                      <div className="truncate">
                        <p className="text-slate-200 text-sm truncate">{link.title}</p>
                        <p className="text-slate-500 text-xs">{link.source}</p>
                      </div>
                    </div>
                    <ExternalLink size={14} className="text-slate-500 group-hover:text-emerald-400 shrink-0 ml-2" />
                  </a>
                ))
              ) : (
                <p className="text-slate-500 text-sm italic py-2">No direct operational evidence found for target queries.</p>
              )}
            </div>
          </div>
        </div>

        {/* Footer Actions */}
        <div className="p-6 border-t border-slate-800 bg-slate-900">
          <button 
            onClick={handleRefresh}
            disabled={isRefreshing}
            className="w-full py-3 bg-indigo-600 hover:bg-indigo-700 text-white font-medium rounded-lg transition-all flex items-center justify-center gap-2 disabled:opacity-70 disabled:cursor-not-allowed"
          >
            {isRefreshing ? (
              <>
                <div className="w-4 h-4 border-2 border-white/30 border-t-white rounded-full animate-spin" />
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
