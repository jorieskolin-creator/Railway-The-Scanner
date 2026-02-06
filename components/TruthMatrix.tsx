
const { ScatterChart, Scatter, XAxis, YAxis, CartesianGrid, Tooltip, ResponsiveContainer, ReferenceArea, Cell, Label } = (window as any).Recharts;

const CustomTooltip = ({ active, payload }: { active?: boolean, payload?: any[] }) => {
  if (active && payload && payload.length) {
    const data = payload[0].payload;
    const gap = data.reality_gap;
    let gapLabel = "Aligned";
    let gapColor = "text-emerald-400";
    
    if (gap > 15) {
        gapLabel = "Talk-not-Walk (Hype)"; // Updated label
        gapColor = "text-amber-400";
    } else if (gap < -15) {
        gapLabel = "Silent Doers (Surplus)"; // Updated label
        gapColor = "text-cyan-400";
    }

    return (
      <div className="bg-[#020617]/95 backdrop-blur-md border border-slate-700/50 p-4 rounded-xl shadow-2xl text-xs text-white min-w-[200px]">
        <p className="font-bold text-sm mb-3 text-white border-b border-white/10 pb-2">{data.companyName}</p>
        <div className="grid grid-cols-2 gap-x-4 gap-y-2 font-mono text-[10px] uppercase tracking-wider">
            <span className="text-slate-500">Stated Ambition</span>
            <span className="text-indigo-400 text-right font-bold">{data.intent_score}/100</span>
            
            <span className="text-slate-500">Ops Reality</span>
            <span className="text-emerald-400 text-right font-bold">{data.reality_score}/100</span>
            
            <div className="col-span-2 h-px bg-white/10 my-1"></div>
            
            <span className="text-slate-500">Integrity Delta</span>
            <span className={`text-right font-bold ${gapColor}`}>{gap > 0 ? '+' : ''}{gap}</span>
            
            <span className={`col-span-2 text-center mt-1 py-1 rounded bg-white/5 ${gapColor}`}>{gapLabel}</span>
        </div>
      </div>
    );
  }
  return null;
};

const TruthMatrix = ({ data, onSelectCompany }) => {
  return (
    <div className="w-full h-[350px] relative overflow-hidden">
      <ResponsiveContainer width="100%" height="100%">
        <ScatterChart margin={{ top: 20, right: 20, bottom: 40, left: 0 }}>
          <CartesianGrid strokeDasharray="3 3" stroke="#334155" opacity={0.2} />
          <XAxis 
            type="number" 
            dataKey="intent_score" 
            name="Ambition" 
            domain={[0, 100]} 
            stroke="#475569"
            tickLine={false}
            axisLine={{ stroke: '#334155', opacity: 0.5 }}
            tick={{ fontSize: 10, fill: '#64748b' }}
            label={{ value: 'AMBITION INDEX (Adjusted for Friction)', position: 'bottom', fill: '#475569', fontSize: 10, dy: 20, letterSpacing: '0.1em' }} 
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
            label={{ value: 'OPERATIONAL REALITY (Evidence)', angle: -90, position: 'left', fill: '#475569', fontSize: 10, dx: -20, letterSpacing: '0.1em' }}
          />
          <Tooltip content={<CustomTooltip />} cursor={{ strokeDasharray: '3 3', stroke: '#cbd5e1' }} />
          
          {/* Quadrant Backgrounds */}
          <ReferenceArea x1={0} x2={50} y1={50} y2={100} fill="#22d3ee" fillOpacity={0.03} /> {/* Silent Doers */}
          <ReferenceArea x1={50} x2={100} y1={50} y2={100} fill="#10b981" fillOpacity={0.03} /> {/* Walk-and-Talk */}
          <ReferenceArea x1={0} x2={50} y1={0} y2={50} fill="#64748b" fillOpacity={0.03} /> {/* Stagnation */}
          <ReferenceArea x1={50} x2={100} y1={0} y2={50} fill="#f59e0b" fillOpacity={0.03} /> {/* Talk-not-Walk */}

          {/* Quadrant Labels */}
          <ReferenceArea x1={5} x2={45} y1={90} y2={98} stroke="none" fill="none">
             <Label value="SILENT DOERS" position="center" fill="#22d3ee" fontSize={11} fontWeight={700} opacity={0.6} letterSpacing="0.05em" />
          </ReferenceArea>
          <ReferenceArea x1={55} x2={95} y1={90} y2={98} stroke="none" fill="none">
             <Label value="WALK-AND-TALK" position="center" fill="#34d399" fontSize={11} fontWeight={700} opacity={0.6} letterSpacing="0.05em" />
          </ReferenceArea>
          <ReferenceArea x1={55} x2={95} y1={5} y2={15} stroke="none" fill="none">
             <Label value="TALK-NOT-WALK" position="center" fill="#fbbf24" fontSize={11} fontWeight={700} opacity={0.6} letterSpacing="0.05em" />
          </ReferenceArea>
           <ReferenceArea x1={5} x2={45} y1={5} y2={15} stroke="none" fill="none">
             <Label value="STAGNATION" position="center" fill="#94a3b8" fontSize={11} fontWeight={700} opacity={0.6} letterSpacing="0.05em" />
          </ReferenceArea>

          <Scatter name="Companies" data={data} onClick={(node) => onSelectCompany(node.payload)} cursor="pointer">
            {data.map((entry, index) => {
              // Color Logic based on Integrity Delta
              // Cyan = Performance Surplus (Good)
              // Amber = Hype Surplus (Bad)
              // Emerald = Aligned
              // Gray = Low/Low
              let fillColor = '#94a3b8';
              
              if (entry.reality_gap < -15) fillColor = '#22d3ee'; // Surplus
              else if (entry.reality_gap > 15) fillColor = '#f59e0b'; // Hype
              else if (entry.reality_score > 50) fillColor = '#10b981'; // Aligned High
              
              return (
                <Cell 
                  key={`cell-${index}`} 
                  fill={fillColor}
                  stroke="#0f172a"
                  strokeWidth={1}
                  fillOpacity={0.9}
                />
              );
            })}
          </Scatter>
        </ScatterChart>
      </ResponsiveContainer>
    </div>
  );
};

(window as any).TruthMatrix = TruthMatrix;
