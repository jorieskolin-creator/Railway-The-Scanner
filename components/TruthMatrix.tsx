

const { ScatterChart, Scatter, XAxis, YAxis, CartesianGrid, Tooltip, ResponsiveContainer, ReferenceArea, Cell, Label, ReferenceLine } = (window as any).Recharts;

const CustomTooltip = ({ active, payload }: { active?: boolean, payload?: any[] }) => {
  if (active && payload && payload.length) {
    const data = payload[0].payload; // This is now our aggregated object
    const companies = data.companies || [data]; // Fallback if not aggregated
    
    // Use coordinates from the first company (since they are all identical in this cluster)
    const intent = data.intent_score;
    const reality = data.reality_score;
    const gap = data.reality_gap; // Use the gap of the representative (or calculating avg if needed, but usually identical here)

    // Strict Quadrant Classification (50/50 Split)
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

    return (
      <div 
        className="bg-[#020617]/95 backdrop-blur-md border border-slate-700/50 p-4 rounded-xl shadow-2xl text-xs text-white min-w-[200px] z-50 animate-in fade-in zoom-in-95 duration-200"
        style={{ pointerEvents: 'none' }} // CRITICAL FIX: Prevents tooltip from capturing mouse and causing flickering
      >
        <div className="border-b border-white/10 pb-2 mb-2">
            <span className="text-[10px] font-mono uppercase text-slate-500 block mb-1">
                {companies.length > 1 ? `${companies.length} Entities at this position` : "Entity"}
            </span>
            <ul className="space-y-1 max-h-[150px] overflow-hidden">
                {companies.slice(0, 5).map((c: any, i: number) => (
                    <li key={i} className="font-bold text-sm text-white truncate">
                        {c.companyName}
                    </li>
                ))}
                {companies.length > 5 && (
                    <li className="text-[10px] text-slate-400 italic">...and {companies.length - 5} more</li>
                )}
            </ul>
        </div>

        <div className="grid grid-cols-2 gap-x-4 gap-y-1 font-mono text-[10px] uppercase tracking-wider">
            {/* Values removed as requested, only showing Archetype and Gap Direction */}
            
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

const TruthMatrix = ({ data, onSelectCompany }) => {
  
  // 1. GROUP DATA LOGIC
  // Combine companies that have the exact same X (intent) and Y (reality) coordinates
  const groupedData = (window as any).React.useMemo(() => {
      const groups: { [key: string]: any } = {};
      
      data.forEach((item: any) => {
          const key = `${item.intent_score}-${item.reality_score}`;
          if (!groups[key]) {
              groups[key] = {
                  ...item, // Keep the first item's properties as representative (for colors/rendering)
                  companies: [item]
              };
          } else {
              groups[key].companies.push(item);
          }
      });
      
      // Convert map back to array
      return Object.values(groups);
  }, [data]);

  return (
    <div className="w-full h-[400px] relative overflow-hidden bg-[#0f172a]/40 rounded-xl border border-white/5 group">
      {/* Subtle Background Grid Animation */}
      <div className="absolute inset-0 bg-[linear-gradient(rgba(255,255,255,0.02)_1px,transparent_1px),linear-gradient(90deg,rgba(255,255,255,0.02)_1px,transparent_1px)] bg-[size:20px_20px] [mask-image:radial-gradient(ellipse_60%_60%_at_50%_50%,black,transparent)] pointer-events-none"></div>

      <ResponsiveContainer width="100%" height="100%">
        <ScatterChart margin={{ top: 20, right: 20, bottom: 40, left: 0 }}>
          <defs>
            {/* 1. Global Upper Half (Green Tint) - Static but clean */}
            <linearGradient id="greenZone" x1="0" y1="0" x2="0" y2="1">
                <stop offset="5%" stopColor="#10b981" stopOpacity={0.15}/>
                <stop offset="95%" stopColor="#10b981" stopOpacity={0.02}/>
            </linearGradient>
            
            {/* 2. Global Lower Half (Red Tint) - Static but clean */}
            <linearGradient id="redZone" x1="0" y1="0" x2="0" y2="1">
                <stop offset="5%" stopColor="#f43f5e" stopOpacity={0.02}/>
                <stop offset="95%" stopColor="#f43f5e" stopOpacity={0.15}/>
            </linearGradient>

            {/* 3. High Performance Glow (Top Right Quadrant) - PULSING ANIMATION */}
            <radialGradient id="sweetSpot" cx="50%" cy="50%" r="50%">
                <stop offset="0%" stopColor="#34d399" stopOpacity={0.2}>
                    <animate attributeName="stopOpacity" values="0.2;0.35;0.2" dur="4s" repeatCount="indefinite" />
                </stop>
                <stop offset="100%" stopColor="#34d399" stopOpacity={0}/>
            </radialGradient>

            {/* 4. Critical Risk Glow (Bottom Left Quadrant) - PULSING ANIMATION */}
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
            isAnimationActive={false} // Helps stability
            offset={20} // Distance from cursor
          />
          
          {/* --- ZONING LAYERS --- */}

          {/* LAYER 1: The Great Divide (50 line) Backgrounds */}
          <ReferenceArea x1={0} x2={100} y1={50} y2={100} fill="url(#greenZone)" stroke="none" />
          <ReferenceArea x1={0} x2={100} y1={0} y2={50} fill="url(#redZone)" stroke="none" />

          {/* LAYER 2: Quadrant Spotlights (Pulsing) */}
          {/* Walk-and-Talk (Top Right) */}
          <ReferenceArea x1={50} x2={100} y1={50} y2={100} fill="url(#sweetSpot)" stroke="none" />
          
          {/* Stagnation (Bottom Left) */}
          <ReferenceArea x1={0} x2={50} y1={0} y2={50} fill="url(#dangerSpot)" stroke="none" />

          {/* MID LINE Separator */}
          <ReferenceLine y={50} stroke="#22d3ee" strokeDasharray="4 4" strokeOpacity={0.6} />
          <ReferenceLine x={50} stroke="#6366f1" strokeDasharray="4 4" strokeOpacity={0.3} />

          {/* LABELS */}
          
          {/* Top Right: Walk-and-Talk */}
          <ReferenceArea x1={80} x2={98} y1={92} y2={98} stroke="none" fill="none">
             <Label value="WALK-AND-TALK" position="insideTopRight" fill="#34d399" fontSize={10} fontWeight={900} opacity={0.8} letterSpacing="0.05em" />
          </ReferenceArea>
          
          {/* Top Left: Silent Doers */}
          <ReferenceArea x1={2} x2={20} y1={92} y2={98} stroke="none" fill="none">
             <Label value="SILENT DOERS" position="insideTopLeft" fill="#22d3ee" fontSize={10} fontWeight={700} opacity={0.7} letterSpacing="0.05em" />
          </ReferenceArea>

          {/* Bottom Right: Talk-not-Walk */}
          <ReferenceArea x1={80} x2={98} y1={2} y2={8} stroke="none" fill="none">
             <Label value="TALK-NOT-WALK" position="insideBottomRight" fill="#fbbf24" fontSize={10} fontWeight={700} opacity={0.7} letterSpacing="0.05em" />
          </ReferenceArea>
          
          {/* Bottom Left: Stagnation */}
           <ReferenceArea x1={2} x2={20} y1={2} y2={8} stroke="none" fill="none">
             <Label value="STAGNATION" position="insideBottomLeft" fill="#fb7185" fontSize={10} fontWeight={900} opacity={0.8} letterSpacing="0.05em" />
          </ReferenceArea>

          <Scatter 
            name="Companies" 
            data={groupedData} 
            onClick={(node) => {
                // Handle click: If cluster has only 1, open it. 
                // If cluster has multiple, we default to the first one for now 
                // (or you could open a small menu, but here we keep the interaction simple)
                if (node.payload.companies && node.payload.companies.length > 0) {
                    onSelectCompany(node.payload.companies[0]);
                }
            }} 
            cursor="pointer"
          >
            {groupedData.map((entry: any, index: number) => {
              // Color Logic
              let fillColor = '#fb7185'; 
              const r = entry.reality_score;
              const i = entry.intent_score;
              const count = entry.companies.length;
              
              if (r >= 50) {
                  if (i >= 50) fillColor = '#10b981'; // Walk-and-Talk (Emerald)
                  else fillColor = '#22d3ee'; // Silent Doers (Cyan)
              } else {
                  if (i >= 50) fillColor = '#f59e0b'; // Talk-not-Walk (Amber)
                  else fillColor = '#fb7185'; // Stagnation (Rose)
              }
              
              // If multiple companies are at this spot, we make the dot slightly larger and add a glow
              const size = count > 1 ? 120 : 80; 
              
              return (
                <Cell 
                  key={`cell-${index}`} 
                  fill={fillColor}
                  stroke="#fff"
                  strokeWidth={count > 1 ? 2 : 1}
                  fillOpacity={count > 1 ? 1 : 0.8}
                  className="hover:scale-110 transition-transform duration-200 origin-center"
                  style={{ filter: count > 1 ? 'drop-shadow(0px 0px 8px rgba(255,255,255,0.6))' : 'drop-shadow(0px 0px 4px rgba(255,255,255,0.3))' }}
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
