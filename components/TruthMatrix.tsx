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
      <div className="bg-[#020617]/90 backdrop-blur border border-slate-700/50 p-3 rounded-lg shadow-2xl text-xs text-white">
        <p className="font-bold text-sm mb-2 text-white">{data.companyName}</p>
        <div className="grid grid-cols-2 gap-x-4 gap-y-1 font-mono text-[10px] uppercase tracking-wider">
            <span className="text-slate-500">Intent</span>
            <span className="text-cyan-400 text-right">{data.intent_score}</span>
            
            <span className="text-slate-500">Reality</span>
            <span className="text-emerald-400 text-right">{data.reality_score}</span>
            
            <div className="col-span-2 h-px bg-white/10 my-1"></div>
            
            <span className="text-slate-500">Gap</span>
            <span className={`text-right ${data.reality_gap > 20 ? 'text-rose-400' : 'text-slate-300'}`}>{data.reality_gap}</span>
        </div>
      </div>
    );
  }
  return null;
};

export const TruthMatrix: React.FC<TruthMatrixProps> = ({ data, onSelectCompany }) => {
  return (
    <div className="w-full h-[350px] relative overflow-hidden">
      <ResponsiveContainer width="100%" height="100%">
        <ScatterChart margin={{ top: 20, right: 20, bottom: 20, left: 0 }}>
          <CartesianGrid strokeDasharray="3 3" stroke="#334155" opacity={0.2} />
          <XAxis 
            type="number" 
            dataKey="intent_score" 
            name="Intent" 
            domain={[0, 100]} 
            stroke="#475569"
            tickLine={false}
            axisLine={{ stroke: '#334155', opacity: 0.5 }}
            tick={{ fontSize: 10, fill: '#64748b' }}
            label={{ value: 'NARRATIVE INTENT', position: 'bottom', fill: '#475569', fontSize: 10, dy: 10, letterSpacing: '0.1em' }} 
          />
          <YAxis 
            type="number" 
            dataKey="reality_score" 
            name="Reality" 
            domain={[0, 100]} 
            stroke="#475569"
            tickLine={false}
            axisLine={{ stroke: '#334155', opacity: 0.5 }}
            tick={{ fontSize: 10, fill: '#64748b' }}
            label={{ value: 'OPERATIONAL REALITY', angle: -90, position: 'left', fill: '#475569', fontSize: 10, dx: -20, letterSpacing: '0.1em' }}
          />
          <Tooltip content={<CustomTooltip />} cursor={{ strokeDasharray: '3 3', stroke: '#cbd5e1' }} />
          
          {/* Quadrant Backgrounds (Very Subtle) */}
          <ReferenceArea x1={0} x2={50} y1={50} y2={100} fill="#3b82f6" fillOpacity={0.02} />
          <ReferenceArea x1={50} x2={100} y1={50} y2={100} fill="#10b981" fillOpacity={0.02} />
          <ReferenceArea x1={0} x2={50} y1={0} y2={50} fill="#64748b" fillOpacity={0.02} />
          <ReferenceArea x1={50} x2={100} y1={0} y2={50} fill="#f59e0b" fillOpacity={0.02} />

          {/* Labels */}
          <ReferenceArea x1={5} x2={45} y1={90} y2={98} stroke="none" fill="none">
             <Label value="SILENT DOERS" position="center" fill="#60a5fa" fontSize={12} fontWeight={800} opacity={0.3} letterSpacing="0.05em" />
          </ReferenceArea>
          <ReferenceArea x1={55} x2={95} y1={90} y2={98} stroke="none" fill="none">
             <Label value="ADAPTIVE" position="center" fill="#34d399" fontSize={12} fontWeight={800} opacity={0.3} letterSpacing="0.05em" />
          </ReferenceArea>
          <ReferenceArea x1={55} x2={95} y1={5} y2={15} stroke="none" fill="none">
             <Label value="THEATER" position="center" fill="#fbbf24" fontSize={12} fontWeight={800} opacity={0.3} letterSpacing="0.05em" />
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
                stroke="#0f172a"
                strokeWidth={1}
                fillOpacity={0.9}
              />
            ))}
          </Scatter>
        </ScatterChart>
      </ResponsiveContainer>
    </div>
  );
};