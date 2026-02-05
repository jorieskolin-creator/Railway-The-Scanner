import React from 'react';
import { AnalysisResult } from '../types';
import { ScatterChart, Scatter, XAxis, YAxis, CartesianGrid, Tooltip, ResponsiveContainer, ReferenceArea, Cell, Label } from 'recharts';

interface TruthMatrixProps {
  data: AnalysisResult[];
  onSelectCompany: (company: AnalysisResult) => void;
}

const CustomTooltip = ({ active, payload }: any) => {
  if (active && payload && payload.length) {
    const data = payload[0].payload as AnalysisResult;
    return (
      <div className="bg-[#0f172a]/95 backdrop-blur border border-white/10 p-4 rounded-xl shadow-2xl text-xs text-white ring-1 ring-white/10">
        <p className="font-bold text-base mb-2 text-white">{data.companyName}</p>
        <div className="space-y-1 font-mono">
            <div className="flex justify-between gap-4">
                <span className="text-indigo-400">Intent:</span>
                <span className="text-white">{data.intent_score}</span>
            </div>
            <div className="flex justify-between gap-4">
                <span className="text-emerald-400">Reality:</span>
                <span className="text-white">{data.reality_score}</span>
            </div>
            <div className="h-px bg-white/10 my-1"></div>
            <div className="flex justify-between gap-4">
                <span className="text-slate-400">Gap:</span>
                <span className={data.reality_gap > 20 ? 'text-rose-400' : 'text-amber-400'}>{data.reality_gap}</span>
            </div>
        </div>
      </div>
    );
  }
  return null;
};

export const TruthMatrix: React.FC<TruthMatrixProps> = ({ data, onSelectCompany }) => {
  return (
    <div className="w-full h-[450px] bg-[#0B0F19]/80 rounded-2xl border border-white/10 p-6 relative overflow-hidden backdrop-blur-sm shadow-xl">
      <div className="absolute top-0 right-0 p-6 z-10 pointer-events-none">
        <div className="flex items-center gap-2 text-xs font-mono text-slate-500 bg-black/40 px-3 py-1 rounded-full border border-white/5">
           <span className="w-2 h-2 rounded-full bg-indigo-500"></span> Intent (X)
           <span className="w-2 h-2 rounded-full bg-emerald-500 ml-2"></span> Reality (Y)
        </div>
      </div>

      <ResponsiveContainer width="100%" height="100%">
        <ScatterChart margin={{ top: 20, right: 20, bottom: 20, left: 20 }}>
          <CartesianGrid strokeDasharray="3 3" stroke="#1e293b" opacity={0.5} />
          <XAxis 
            type="number" 
            dataKey="intent_score" 
            name="Intent" 
            domain={[0, 100]} 
            stroke="#475569"
            tickLine={false}
            axisLine={{ stroke: '#334155' }}
            label={{ value: 'Narrative (Intent)', position: 'bottom', fill: '#64748b', fontSize: 12, dy: 10 }} 
            tick={{ fontSize: 10, fill: '#64748b' }}
          />
          <YAxis 
            type="number" 
            dataKey="reality_score" 
            name="Reality" 
            domain={[0, 100]} 
            stroke="#475569"
            tickLine={false}
            axisLine={{ stroke: '#334155' }}
            label={{ value: 'Evidence (Reality)', angle: -90, position: 'left', fill: '#64748b', fontSize: 12, dx: -10 }}
            tick={{ fontSize: 10, fill: '#64748b' }}
          />
          <Tooltip content={<CustomTooltip />} cursor={{ strokeDasharray: '3 3', stroke: '#cbd5e1' }} />
          
          {/* Quadrant Backgrounds */}
          {/* Silent Doers (Low Intent, High Reality) */}
          <ReferenceArea x1={0} x2={50} y1={50} y2={100} fill="#3b82f6" fillOpacity={0.03} />
          {/* Frontrunners (High Intent, High Reality) */}
          <ReferenceArea x1={50} x2={100} y1={50} y2={100} fill="#10b981" fillOpacity={0.03} />
          {/* Legacy (Low Intent, Low Reality) */}
          <ReferenceArea x1={0} x2={50} y1={0} y2={50} fill="#64748b" fillOpacity={0.03} />
          {/* Hype Trap (High Intent, Low Reality) */}
          <ReferenceArea x1={50} x2={100} y1={0} y2={50} fill="#f59e0b" fillOpacity={0.03} />

          {/* Quadrant Labels */}
          <ReferenceArea x1={5} x2={45} y1={90} y2={98} stroke="none" fill="none">
             <Label value="Silent Doers" position="center" fill="#60a5fa" fontSize={16} fontWeight={700} opacity={0.4} />
          </ReferenceArea>
          <ReferenceArea x1={55} x2={95} y1={90} y2={98} stroke="none" fill="none">
             <Label value="Frontrunners" position="center" fill="#34d399" fontSize={16} fontWeight={700} opacity={0.4} />
          </ReferenceArea>
          <ReferenceArea x1={5} x2={45} y1={5} y2={15} stroke="none" fill="none">
             <Label value="Laggards" position="center" fill="#94a3b8" fontSize={16} fontWeight={700} opacity={0.3} />
          </ReferenceArea>
          <ReferenceArea x1={55} x2={95} y1={5} y2={15} stroke="none" fill="none">
             <Label value="Hype Trap" position="center" fill="#fbbf24" fontSize={16} fontWeight={700} opacity={0.4} />
          </ReferenceArea>

          <Scatter name="Companies" data={data} onClick={(node) => onSelectCompany(node.payload as AnalysisResult)} cursor="pointer">
            {data.map((entry, index) => (
              <Cell 
                key={`cell-${index}`} 
                fill={
                  entry.reality_score >= 50 && entry.intent_score >= 50 ? '#10b981' : // Green
                  entry.reality_score < 50 && entry.intent_score >= 50 ? '#f59e0b' : // Yellow
                  entry.reality_score >= 50 && entry.intent_score < 50 ? '#3b82f6' : // Blue
                  '#94a3b8' // Slate
                }
                stroke="white"
                strokeWidth={2}
                fillOpacity={0.8}
              />
            ))}
          </Scatter>
        </ScatterChart>
      </ResponsiveContainer>
    </div>
  );
};