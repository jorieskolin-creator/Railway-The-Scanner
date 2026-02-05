import React from 'react';
import { AnalysisResult } from '../types';
import { ScatterChart, Scatter, XAxis, YAxis, CartesianGrid, Tooltip, ResponsiveContainer, ReferenceArea, Cell } from 'recharts';

interface TruthMatrixProps {
  data: AnalysisResult[];
  onSelectCompany: (company: AnalysisResult) => void;
}

const CustomTooltip = ({ active, payload }: any) => {
  if (active && payload && payload.length) {
    const data = payload[0].payload as AnalysisResult;
    return (
      <div className="bg-slate-800 border border-slate-700 p-3 rounded-lg shadow-xl text-xs text-white">
        <p className="font-bold text-sm mb-1">{data.companyName}</p>
        <p className="text-indigo-400">Intent: {data.intent_score}</p>
        <p className="text-emerald-400">Reality: {data.reality_score}</p>
        <p className="text-slate-400 mt-1">Gap: {data.reality_gap}</p>
      </div>
    );
  }
  return null;
};

export const TruthMatrix: React.FC<TruthMatrixProps> = ({ data, onSelectCompany }) => {
  return (
    <div className="w-full h-[400px] bg-slate-900 rounded-xl border border-slate-800 p-4 relative overflow-hidden">
      <div className="absolute top-4 left-4 z-10">
        <h3 className="text-slate-100 font-bold text-lg">Truth Matrix</h3>
        <p className="text-slate-500 text-xs">Intent (X) vs. Reality (Y)</p>
      </div>

      <ResponsiveContainer width="100%" height="100%">
        <ScatterChart margin={{ top: 20, right: 20, bottom: 20, left: 20 }}>
          <CartesianGrid strokeDasharray="3 3" stroke="#334155" opacity={0.5} />
          <XAxis 
            type="number" 
            dataKey="intent_score" 
            name="Intent" 
            domain={[0, 100]} 
            stroke="#94a3b8"
            label={{ value: 'Narrative (What they say)', position: 'bottom', fill: '#94a3b8', fontSize: 12 }} 
            tick={{ fontSize: 10 }}
          />
          <YAxis 
            type="number" 
            dataKey="reality_score" 
            name="Reality" 
            domain={[0, 100]} 
            stroke="#94a3b8"
            label={{ value: 'Evidence (What they do)', angle: -90, position: 'left', fill: '#94a3b8', fontSize: 12 }}
            tick={{ fontSize: 10 }}
          />
          <Tooltip content={<CustomTooltip />} cursor={{ strokeDasharray: '3 3' }} />
          
          {/* Quadrant Backgrounds */}
          {/* Top Left: Silent Doers (Low Intent, High Reality) - Blue */}
          <ReferenceArea x1={0} x2={50} y1={50} y2={100} fill="#3b82f6" fillOpacity={0.05} />
          {/* Top Right: Frontrunners (High Intent, High Reality) - Green */}
          <ReferenceArea x1={50} x2={100} y1={50} y2={100} fill="#10b981" fillOpacity={0.05} />
          {/* Bottom Left: Legacy (Low Intent, Low Reality) - Slate */}
          <ReferenceArea x1={0} x2={50} y1={0} y2={50} fill="#64748b" fillOpacity={0.05} />
          {/* Bottom Right: Hype Trap (High Intent, Low Reality) - Yellow/Orange */}
          <ReferenceArea x1={50} x2={100} y1={0} y2={50} fill="#f59e0b" fillOpacity={0.05} />

          {/* Quadrant Labels */}
          <ReferenceArea x1={10} x2={40} y1={90} y2={95} stroke="none" fill="none" label={{ value: "Silent Doers", fill: "#60a5fa", fontSize: 14, fontWeight: 'bold' }} />
          <ReferenceArea x1={60} x2={90} y1={90} y2={95} stroke="none" fill="none" label={{ value: "Frontrunners", fill: "#34d399", fontSize: 14, fontWeight: 'bold' }} />
          <ReferenceArea x1={10} x2={40} y1={10} y2={15} stroke="none" fill="none" label={{ value: "Legacy", fill: "#94a3b8", fontSize: 14, fontWeight: 'bold' }} />
          <ReferenceArea x1={60} x2={90} y1={10} y2={15} stroke="none" fill="none" label={{ value: "Hype Trap", fill: "#fbbf24", fontSize: 14, fontWeight: 'bold' }} />

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
              />
            ))}
          </Scatter>
        </ScatterChart>
      </ResponsiveContainer>
    </div>
  );
};