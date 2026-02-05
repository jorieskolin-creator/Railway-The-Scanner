import React from 'react';
import { AnalysisResult } from '../types';
import { BarChart, Bar, XAxis, YAxis, Tooltip, ResponsiveContainer, Cell, RadarChart, PolarGrid, PolarAngleAxis, PolarRadiusAxis, Radar } from 'recharts';
import { ExternalLink, CheckCircle, AlertTriangle, ShieldCheck } from 'lucide-react';

interface CompanySummaryProps {
  data: AnalysisResult;
}

export const CompanySummary: React.FC<CompanySummaryProps> = ({ data }) => {
  const gapColor = data.reality_gap > 20 ? '#ef4444' : data.reality_gap < -20 ? '#f59e0b' : '#10b981';
  
  const scoreData = [
    { name: 'Intent', value: data.intent_score, color: '#6366f1' }, // Indigo-500
    { name: 'Reality', value: data.reality_score, color: '#10b981' }, // Emerald-500
  ];

  const radarData = data.intent_breakdown.map(item => ({
    subject: item.name,
    A: item.score,
    fullMark: 100,
  }));

  return (
    <div className="space-y-8">
      
      {/* Top Cards: Scores */}
      <div className="grid grid-cols-1 md:grid-cols-3 gap-4">
        <div className="bg-white p-6 rounded-2xl shadow-sm border border-slate-100 flex flex-col items-center justify-center">
          <span className="text-slate-500 font-medium mb-1">Intent Score</span>
          <span className="text-5xl font-extrabold text-indigo-500">{data.intent_score}</span>
          <span className="text-xs text-slate-400 mt-2">Narrative Analysis</span>
        </div>
        <div className="bg-white p-6 rounded-2xl shadow-sm border border-slate-100 flex flex-col items-center justify-center">
          <span className="text-slate-500 font-medium mb-1">Reality Score</span>
          <span className="text-5xl font-extrabold text-emerald-500">{data.reality_score}</span>
          <span className="text-xs text-slate-400 mt-2">Evidence Check</span>
        </div>
        <div className="bg-white p-6 rounded-2xl shadow-sm border border-slate-100 flex flex-col items-center justify-center relative overflow-hidden">
          <span className="text-slate-500 font-medium mb-1">Reality Gap</span>
          <span className="text-5xl font-extrabold" style={{ color: gapColor }}>
            {data.reality_gap > 0 ? `+${data.reality_gap}` : data.reality_gap}
          </span>
          <span className="text-xs text-slate-400 mt-2">Intent minus Reality</span>
          {data.reality_gap > 30 && (
             <div className="absolute top-2 right-2 text-red-500" title="High Gap Warning">
               <AlertTriangle size={20} />
             </div>
          )}
        </div>
      </div>

      {/* AI Summary */}
      <div className="bg-white p-8 rounded-2xl shadow-md border-l-4 border-indigo-600">
        <h3 className="text-xl font-bold text-slate-800 mb-4 flex items-center gap-2">
          <ShieldCheck className="text-indigo-600" />
          AI Diagnosis
        </h3>
        <p className="text-slate-700 leading-relaxed whitespace-pre-line">
          {data.summary_text}
        </p>
      </div>

      <div className="grid grid-cols-1 lg:grid-cols-2 gap-8">
        {/* Charts */}
        <div className="bg-white p-6 rounded-2xl shadow-sm border border-slate-100">
          <h3 className="text-lg font-bold text-slate-800 mb-6">Intent Breakdown</h3>
          <div className="h-[300px] w-full">
            <ResponsiveContainer width="100%" height="100%">
              <RadarChart cx="50%" cy="50%" outerRadius="80%" data={radarData}>
                <PolarGrid />
                <PolarAngleAxis dataKey="subject" tick={{ fill: '#64748b', fontSize: 10 }} />
                <PolarRadiusAxis angle={30} domain={[0, 100]} />
                <Radar name="Intent" dataKey="A" stroke="#6366f1" fill="#6366f1" fillOpacity={0.5} />
                <Tooltip />
              </RadarChart>
            </ResponsiveContainer>
          </div>
        </div>

        {/* Evidence List */}
        <div className="bg-white p-6 rounded-2xl shadow-sm border border-slate-100 flex flex-col">
          <h3 className="text-lg font-bold text-slate-800 mb-6">Verified Evidence (Scanner 2)</h3>
          
          <div className="flex-grow overflow-y-auto max-h-[300px] pr-2 space-y-3">
             {data.evidence_links.length > 0 ? (
               data.evidence_links.map((link, idx) => (
                 <a 
                   key={idx}
                   href={link.url}
                   target="_blank"
                   rel="noopener noreferrer"
                   className="block p-4 rounded-xl bg-slate-50 border border-slate-200 hover:border-indigo-300 hover:shadow-md transition-all group"
                 >
                   <div className="flex items-start justify-between">
                     <div>
                       <h4 className="font-medium text-slate-800 group-hover:text-indigo-700 transition-colors text-sm line-clamp-1">
                         {link.title}
                       </h4>
                       <span className="text-xs text-slate-400 mt-1 inline-block">{link.source}</span>
                     </div>
                     <ExternalLink size={16} className="text-slate-400 group-hover:text-indigo-500 mt-1" />
                   </div>
                 </a>
               ))
             ) : (
               <div className="flex flex-col items-center justify-center h-full text-slate-400">
                 <AlertTriangle size={32} className="mb-2 opacity-50" />
                 <p className="text-sm">No positive evidence found via X-Ray search.</p>
               </div>
             )}
          </div>
          
          <div className="mt-6 pt-4 border-t border-slate-100">
            <h4 className="text-xs font-semibold text-slate-500 uppercase tracking-wider mb-3">X-Ray Checklist</h4>
            <div className="grid grid-cols-2 gap-2">
              {data.reality_checklist.slice(0, 6).map((item, idx) => (
                <div key={idx} className="flex items-center gap-2 text-xs text-slate-600">
                  {item.found ? (
                    <CheckCircle size={14} className="text-emerald-500 shrink-0" />
                  ) : (
                    <div className="w-3.5 h-3.5 rounded-full border border-slate-300 shrink-0" />
                  )}
                  <span className={item.found ? 'font-medium' : 'opacity-60'}>
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
