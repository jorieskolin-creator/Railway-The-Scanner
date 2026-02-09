
const { ScatterChart, Scatter, XAxis, YAxis, CartesianGrid, Tooltip, ResponsiveContainer, ReferenceArea, Label, ReferenceLine } = (window as any).Recharts;

const CustomTooltip = ({ active, payload }: { active?: boolean, payload?: any[] }) => {
  if (active && payload && payload.length) {
    const data = payload[0].payload; 
    const companies = data.companies || [data]; 
    
    // Check if any in this group is a reference (though usually refs should be unique points)
    const isRef = companies.some(c => c.isReference);
    
    // Use coordinates from the first company
    const gap = data.reality_gap;
    const intent = data.intent_score;
    const reality = data.reality_score;

    // Strict Quadrant Classification
    let gapLabel = "Stagnation";
    let gapColor = "text-slate-400";
    
    if (reality >= 50) {
        if (intent >= 50) {
            gapLabel = "Walk-and-Talk";
            gapColor = "text-emerald-400";
        } else {
            gapLabel = "Silent Doers";
            gapColor = "text-cyan-400";
        }
    } else {
        if (intent >= 50) {
            gapLabel = "Talk-not-Walk";
            gapColor = "text-amber-400";
        } else {
            gapLabel = "Stagnation";
            gapColor = "text-rose-400";
        }
    }

    if (isRef) {
        gapLabel = "GLOBAL REFERENCE";
        gapColor = "text-blue-400";
    }

    const DISPLAY_LIMIT = 12;

    return (
      <div 
        className={`backdrop-blur-md border p-4 rounded-xl shadow-2xl text-xs text-white min-w-[220px] max-w-[300px] z-50 animate-in fade-in zoom-in-95 duration-200 ${isRef ? 'bg-blue-950/90 border-blue-500/50' : 'bg-[#020617]/95 border-slate-700/50'}`}
      >
        <div className="border-b border-white/10 pb-2 mb-2">
            <span className={`text-[10px] font-mono uppercase block mb-1 ${isRef ? 'text-blue-300' : 'text-slate-500'}`}>
                {companies.length > 1 ? `${companies.length} Entities at this position` : "Entity"}
            </span>
            <ul className="space-y-1">
                {companies.slice(0, DISPLAY_LIMIT).map((c: any, i: number) => (
                    <li key={i} className="font-bold text-xs text-white truncate border-b border-white/5 last:border-0 pb-1 mb-1 last:mb-0 last:pb-0 flex items-center gap-2">
                        {c.companyName}
                        {c.isReference && <span className="text-[9px] bg-blue-500/30 text-blue-200 px-1 rounded uppercase">(Reference)</span>}
                    </li>
                ))}
            </ul>
        </div>

        <div className="grid grid-cols-2 gap-x-4 gap-y-1 font-mono text-[10px] uppercase tracking-wider">
            <span className="text-slate-500">Integrity Delta</span>
            <span className={`text-right font-bold ${gap > 0 ? 'text-emerald-400' : gap < 0 ? 'text-rose-400' : 'text-slate-400'}`}>
                {gap > 0 ? '+' : ''}{gap}
            </span>
            
            <div className={`col-span-2 text-center mt-2 py-1.5 rounded bg-white/5 ${gapColor} font-bold border border-white/5`}>
                {gapLabel}
            </div>
        </div>
      </div>
    );
  }
  return null;
};

// CUSTOM DOT RENDERER with Reference Pulse Support
const renderCustomDot = (props: any) => {
    const { cx, cy, payload } = props;
    const { intent_score: i, reality_score: r, companies } = payload;
    const count = companies ? companies.length : 1;
    const isReference = companies ? companies.some(c => c.isReference) : payload.isReference;

    // Color Logic
    let fillColor = '#fb7185'; 
    if (r >= 50) {
        if (i >= 50) fillColor = '#10b981'; // Walk-and-Talk (Emerald)
        else fillColor = '#22d3ee'; // Silent Doers (Cyan)
    } else {
        if (i >= 50) fillColor = '#f59e0b'; // Talk-not-Walk (Amber)
        else fillColor = '#fb7185'; // Stagnation (Rose)
    }

    if (isReference) {
        fillColor = '#3b82f6'; // Blue for References
    }

    // Size Logic
    const radius = Math.min(18, 8 + (count - 1) * 3);

    return (
        <g>
            {isReference && (
                <circle cx={cx} cy={cy} r={radius + 8} fill={fillColor} opacity="0.3">
                    <animate attributeName="r" from={radius} to={radius + 15} dur="2s" repeatCount="indefinite" />
                    <animate attributeName="opacity" from="0.6" to="0" dur="2s" repeatCount="indefinite" />
                </circle>
            )}
            
            <circle 
                cx={cx} 
                cy={cy} 
                r={radius} 
                fill={fillColor} 
                stroke="#fff" 
                strokeWidth={count > 1 || isReference ? 2 : 1.5} 
                fillOpacity={0.9}
                style={{ 
                    filter: isReference ? 'drop-shadow(0 0 10px rgba(59, 130, 246, 0.6))' : 'drop-shadow(0 0 4px rgba(0,0,0,0.5))',
                    cursor: 'pointer',
                    transition: 'all 0.3s ease'
                }}
            />
            {count > 1 && (
                <text x={cx} y={cy} dy={3} textAnchor="middle" fill="#fff" fontSize={10} fontWeight="bold" style={{ pointerEvents: 'none' }}>
                    {count}
                </text>
            )}
        </g>
    );
};

const TruthMatrix = ({ data, onSelectCompany }) => {
  
  const groupedData = (window as any).React.useMemo(() => {
      const groups: { [key: string]: any } = {};
      
      data.forEach((item: any) => {
          const key = `${item.intent_score}-${item.reality_score}`;
          if (!groups[key]) {
              groups[key] = {
                  ...item, 
                  companies: [item]
              };
          } else {
              groups[key].companies.push(item);
          }
      });
      return Object.values(groups);
  }, [data]);

  return (
    <div className="w-full h-[400px] relative overflow-hidden bg-[#0f172a]/40 rounded-xl border border-white/5 group">
      <div className="absolute inset-0 bg-[linear-gradient(rgba(255,255,255,0.02)_1px,transparent_1px),linear-gradient(90deg,rgba(255,255,255,0.02)_1px,transparent_1px)] bg-[size:20px_20px] [mask-image:radial-gradient(ellipse_60%_60%_at_50%_50%,black,transparent)] pointer-events-none"></div>

      <ResponsiveContainer width="100%" height="100%">
        <ScatterChart margin={{ top: 20, right: 20, bottom: 40, left: 0 }}>
          <defs>
            <linearGradient id="greenZone" x1="0" y1="0" x2="0" y2="1">
                <stop offset="5%" stopColor="#10b981" stopOpacity={0.15}/>
                <stop offset="95%" stopColor="#10b981" stopOpacity={0.02}/>
            </linearGradient>
            <linearGradient id="redZone" x1="0" y1="0" x2="0" y2="1">
                <stop offset="5%" stopColor="#f43f5e" stopOpacity={0.02}/>
                <stop offset="95%" stopColor="#f43f5e" stopOpacity={0.15}/>
            </linearGradient>
            <radialGradient id="sweetSpot" cx="50%" cy="50%" r="50%">
                <stop offset="0%" stopColor="#34d399" stopOpacity={0.2}>
                    <animate attributeName="stopOpacity" values="0.2;0.35;0.2" dur="4s" repeatCount="indefinite" />
                </stop>
                <stop offset="100%" stopColor="#34d399" stopOpacity={0}/>
            </radialGradient>
            <radialGradient id="dangerSpot" cx="50%" cy="50%" r="50%">
                <stop offset="0%" stopColor="#f43f5e" stopOpacity={0.2}>
                     <animate attributeName="stopOpacity" values="0.2;0.35;0.2" dur="5s" repeatCount="indefinite" />
                </stop>
                <stop offset="100%" stopColor="#f43f5e" stopOpacity={0}/>
            </radialGradient>
          </defs>

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
            label={{ value: 'AMBITION INDEX (Narrative)', position: 'bottom', fill: '#475569', fontSize: 10, dy: 20, letterSpacing: '0.1em' }} 
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
          
          <Tooltip 
            content={<CustomTooltip />} 
            cursor={{ strokeDasharray: '3 3', stroke: '#cbd5e1' }}
            isAnimationActive={false}
            offset={25}
            wrapperStyle={{ pointerEvents: 'none', zIndex: 100 }}
          />
          
          <ReferenceArea x1={0} x2={100} y1={50} y2={100} fill="url(#greenZone)" stroke="none" />
          <ReferenceArea x1={0} x2={100} y1={0} y2={50} fill="url(#redZone)" stroke="none" />
          <ReferenceArea x1={50} x2={100} y1={50} y2={100} fill="url(#sweetSpot)" stroke="none" />
          <ReferenceArea x1={0} x2={50} y1={0} y2={50} fill="url(#dangerSpot)" stroke="none" />
          <ReferenceLine y={50} stroke="#22d3ee" strokeDasharray="4 4" strokeOpacity={0.6} />
          <ReferenceLine x={50} stroke="#6366f1" strokeDasharray="4 4" strokeOpacity={0.3} />

          <ReferenceArea x1={80} x2={98} y1={92} y2={98} stroke="none" fill="none">
             <Label value="WALK-AND-TALK" position="insideTopRight" fill="#34d399" fontSize={10} fontWeight={900} opacity={0.8} letterSpacing="0.05em" />
          </ReferenceArea>
          <ReferenceArea x1={2} x2={20} y1={92} y2={98} stroke="none" fill="none">
             <Label value="SILENT DOERS" position="insideTopLeft" fill="#22d3ee" fontSize={10} fontWeight={700} opacity={0.7} letterSpacing="0.05em" />
          </ReferenceArea>
          <ReferenceArea x1={80} x2={98} y1={2} y2={8} stroke="none" fill="none">
             <Label value="TALK-NOT-WALK" position="insideBottomRight" fill="#fbbf24" fontSize={10} fontWeight={700} opacity={0.7} letterSpacing="0.05em" />
          </ReferenceArea>
           <ReferenceArea x1={2} x2={20} y1={2} y2={8} stroke="none" fill="none">
             <Label value="STAGNATION" position="insideBottomLeft" fill="#fb7185" fontSize={10} fontWeight={900} opacity={0.8} letterSpacing="0.05em" />
          </ReferenceArea>

          <Scatter 
            name="Companies" 
            data={groupedData} 
            shape={renderCustomDot}
            onClick={(node) => {
                if (node.payload.companies && node.payload.companies.length > 0) {
                    onSelectCompany(node.payload.companies[0]);
                }
            }} 
            cursor="pointer"
          />
        </ScatterChart>
      </ResponsiveContainer>
    </div>
  );
};

(window as any).TruthMatrix = TruthMatrix;
