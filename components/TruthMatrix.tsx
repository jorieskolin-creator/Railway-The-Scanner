
const { ScatterChart, Scatter, XAxis, YAxis, CartesianGrid, Tooltip, ResponsiveContainer, ReferenceArea, Label, ReferenceLine, ReferenceDot } = (window as any).Recharts;
const { useState, useMemo, useCallback, useEffect, useRef } = (window as any).React;

const CustomTooltip = ({ active, payload }: { active?: boolean, payload?: any[] }) => {
  if (active && payload && payload.length) {
    const data = payload[0].payload;
    const companies = data.companies || [data];
    const isRef = companies.some(c => c.isReference);

    // Quadrant from first company's coordinates
    const financial = data.financial_index ?? data.intent_score ?? 0;
    const reality = data.reality_score;

    let gapLabel = "At Risk";
    let gapColor = "text-slate-400";
    if (reality >= 50) {
        gapLabel = financial >= 50 ? "Thriving" : "Modernizing";
        gapColor = financial >= 50 ? "text-emerald-400" : "text-cyan-400";
    } else {
        gapLabel = financial >= 50 ? "Cash Cow" : "At Risk";
        gapColor = financial >= 50 ? "text-amber-400" : "text-rose-400";
    }
    if (isRef) { gapLabel = "GLOBAL REFERENCE"; gapColor = "text-green-400"; }

    const DISPLAY_LIMIT = 12;

    return (
      <div
        className={`backdrop-blur-md border p-4 rounded-xl shadow-2xl text-xs text-white min-w-[240px] max-w-[320px] z-50 animate-in fade-in zoom-in-95 duration-200 ${isRef ? 'bg-green-950/90 border-green-500/50' : 'bg-[#020617]/95 border-slate-700/50'}`}
      >
        <div className="border-b border-white/10 pb-2 mb-2">
            <span className={`text-[10px] font-mono uppercase block mb-1 ${isRef ? 'text-green-300' : 'text-slate-500'}`}>
                {companies.length > 1 ? `${companies.length} Entities — click dot to select` : "Entity — click to open"}
            </span>
            {companies.slice(0, DISPLAY_LIMIT).map((c: any, i: number) => {
                const cFin = c.financial_index ?? c.intent_score ?? 0;
                return (
                    <div key={i} className="flex items-center justify-between gap-2 py-1 border-b border-white/5 last:border-0">
                        <span className="font-bold text-xs text-white truncate max-w-[140px] flex items-center gap-1">
                            {c.companyName}
                            {c.isReference && <span className="text-[8px] bg-green-500/30 text-green-200 px-1 rounded uppercase">(Ref)</span>}
                        </span>
                        <div className="flex gap-2 text-[10px] font-mono shrink-0">
                            <span className="text-cyan-300" title="Reality Score">R:{c.reality_score ?? '—'}</span>
                            <span className="text-emerald-300" title="Financial Health">{c.financial_index != null ? `F:${c.financial_index}` : 'F:—'}</span>
                        </div>
                    </div>
                );
            })}
        </div>

        <div className={`text-center py-1.5 rounded bg-white/5 ${gapColor} font-bold border border-white/5 font-mono text-[10px] uppercase tracking-wider`}>
            {gapLabel}
        </div>
      </div>
    );
  }
  return null;
};

// Revenue tier to radius multiplier
const getRevenueRadius = (companies: any[]) => {
    if (!companies || companies.length === 0) return 0;
    // Use max revenue in the group
    const maxRev = Math.max(...companies.map(c => c.revenue_avg_3yr || 0));
    if (maxRev >= 2000) return 4;   // XL
    if (maxRev >= 500) return 2;    // L
    if (maxRev >= 100) return 1;    // M
    return 0;                        // S
};

// CUSTOM DOT RENDERER with Reference Pulse + Revenue Sizing
const renderCustomDot = (props: any) => {
    const { cx, cy, payload } = props;
    const { reality_score: r, companies } = payload;
    const f = payload.financial_index ?? payload.intent_score ?? 0;
    const count = companies ? companies.length : 1;
    const isReference = companies ? companies.some(c => c.isReference) : payload.isReference;

    // Color Logic (X=Financial Health, Y=Reality)
    let fillColor = '#fb7185';
    if (r >= 50) {
        if (f >= 50) fillColor = '#10b981'; // Thriving (Emerald)
        else fillColor = '#22d3ee'; // Modernizing (Cyan)
    } else {
        if (f >= 50) fillColor = '#f59e0b'; // Cash Cow (Amber)
        else fillColor = '#fb7185'; // At Risk (Rose)
    }

    if (isReference) {
        fillColor = '#22c55e'; // Green for References (Green-500)
    }

    // Size Logic: base from cluster count + revenue boost
    const revenueBoost = getRevenueRadius(companies || [payload]);
    const radius = Math.min(22, 7 + (count - 1) * 3 + revenueBoost);

    return (
        <g>
            {isReference && (
                <>
                    <circle
                        cx={cx} cy={cy} r={radius + 2}
                        fill="none" stroke="#22c55e" strokeWidth={2}
                        style={{
                            transformOrigin: `${cx}px ${cy}px`,
                            animation: 'ref-pulse 2s ease-out infinite'
                        }}
                    />
                    <circle
                        cx={cx} cy={cy} r={radius + 2}
                        fill="none" stroke="#22c55e" strokeWidth={1.5}
                        style={{
                            transformOrigin: `${cx}px ${cy}px`,
                            animation: 'ref-pulse-delayed 2s ease-out infinite 0.6s'
                        }}
                    />
                </>
            )}

            <circle
                cx={cx}
                cy={cy}
                r={radius}
                fill={fillColor}
                stroke={isReference ? '#22c55e' : '#fff'}
                strokeWidth={isReference ? 2.5 : (count > 1 ? 2 : 1.5)}
                fillOpacity={0.9}
                style={{
                    filter: isReference ? 'drop-shadow(0 0 12px rgba(34, 197, 94, 0.7)) drop-shadow(0 0 4px rgba(34, 197, 94, 0.4))' : 'drop-shadow(0 0 4px rgba(0,0,0,0.5))',
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
  const [popover, setPopover] = useState<{ companies: any[], x: number, y: number } | null>(null);
  const [avgHovered, setAvgHovered] = useState(false);
  const [avgDotPos, setAvgDotPos] = useState<{ cx: number, cy: number } | null>(null);
  const popoverRef = useRef<HTMLDivElement>(null);
  const containerRef = useRef<HTMLDivElement>(null);

  // Close popover on click outside
  useEffect(() => {
      if (!popover) return;
      const handleClick = (e: MouseEvent) => {
          if (popoverRef.current && !popoverRef.current.contains(e.target as Node)) {
              setPopover(null);
          }
      };
      document.addEventListener('mousedown', handleClick);
      return () => document.removeEventListener('mousedown', handleClick);
  }, [popover]);

  const groupedData = useMemo(() => {
      const groups: { [key: string]: any } = {};

      data.forEach((item: any) => {
          const fi = item.financial_index ?? item.intent_score;
          const displayItem = { ...item, financial_index: fi };
          const key = `${fi}-${item.reality_score}`;
          if (!groups[key]) {
              groups[key] = {
                  ...displayItem,
                  companies: [item]
              };
          } else {
              groups[key].companies.push(item);
          }
      });
      return Object.values(groups);
  }, [data]);

  // Market average: center of gravity across all companies
  const marketAvg = useMemo(() => {
      if (!data || data.length === 0) return null;
      const nonRef = data.filter((d: any) => !d.isReference);
      if (nonRef.length === 0) return null;
      const avgReality = Math.round(nonRef.reduce((sum: number, d: any) => sum + (d.reality_score ?? 0), 0) / nonRef.length);
      const avgFinancial = Math.round(nonRef.reduce((sum: number, d: any) => sum + (d.financial_index ?? d.intent_score ?? 0), 0) / nonRef.length);
      return { x: avgFinancial, y: avgReality };
  }, [data]);

  const handleDotClick = useCallback((node: any, e?: any) => {
      const companies = node.payload?.companies || [node.payload];
      if (companies.length === 1) {
          // Single company — open directly
          onSelectCompany(companies[0]);
          setPopover(null);
      } else {
          // Multiple companies — show popover for selection
          const rect = containerRef.current?.getBoundingClientRect();
          const mouseX = e?.clientX ?? 0;
          const mouseY = e?.clientY ?? 0;
          const relX = rect ? mouseX - rect.left : 200;
          const relY = rect ? mouseY - rect.top : 200;
          setPopover({ companies, x: relX, y: relY });
      }
  }, [onSelectCompany]);

  return (
    <div ref={containerRef} className="w-full h-[400px] relative overflow-hidden bg-[#0f172a]/40 rounded-xl border border-white/5 group">
      <div className="absolute inset-0 bg-[linear-gradient(rgba(255,255,255,0.02)_1px,transparent_1px),linear-gradient(90deg,rgba(255,255,255,0.02)_1px,transparent_1px)] bg-[size:20px_20px] [mask-image:radial-gradient(ellipse_60%_60%_at_50%_50%,black,transparent)] pointer-events-none"></div>

      {/* Click-to-select popover for clustered dots */}
      {popover && (
          <div
              ref={popoverRef}
              className="absolute z-50 backdrop-blur-md bg-[#020617]/95 border border-slate-700/50 rounded-xl shadow-2xl p-3 min-w-[260px] max-w-[340px] animate-in fade-in zoom-in-95 duration-200"
              style={{
                  left: Math.min(popover.x, (containerRef.current?.clientWidth || 400) - 280),
                  top: Math.min(popover.y - 10, (containerRef.current?.clientHeight || 400) - 200),
              }}
          >
              <div className="text-[10px] font-mono text-slate-500 uppercase mb-2">
                  {popover.companies.length} Entities — select one
              </div>
              {popover.companies.map((c: any, i: number) => (
                  <button
                      key={i}
                      onClick={() => { onSelectCompany(c); setPopover(null); }}
                      className="w-full flex items-center justify-between gap-2 py-1.5 px-2 rounded-md hover:bg-white/10 transition-colors text-left border-b border-white/5 last:border-0"
                  >
                      <span className="font-bold text-xs text-white truncate max-w-[150px] flex items-center gap-1">
                          {c.companyName}
                          {c.isReference && <span className="text-[8px] bg-green-500/30 text-green-200 px-1 rounded uppercase">(Ref)</span>}
                      </span>
                      <div className="flex gap-2 text-[10px] font-mono shrink-0">
                          <span className="text-cyan-300">R:{c.reality_score ?? '—'}</span>
                          <span className="text-emerald-300">{c.financial_index != null ? `F:${c.financial_index}` : 'F:—'}</span>
                      </div>
                  </button>
              ))}
          </div>
      )}

      <ResponsiveContainer width="100%" height="100%">
        <ScatterChart margin={{ top: 20, right: 20, bottom: 40, left: 50 }}>
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
            dataKey="financial_index"
            name="Financial Health"
            domain={[0, 100]}
            stroke="#475569"
            tickLine={false}
            axisLine={{ stroke: '#334155', opacity: 0.5 }}
            tick={{ fontSize: 10, fill: '#64748b' }}
            label={{ value: 'FINANCIAL HEALTH INDEX', position: 'bottom', fill: '#475569', fontSize: 10, dy: 20, letterSpacing: '0.1em' }}
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
            label={{ value: 'OPERATIONAL REALITY (Evidence)', angle: -90, position: 'insideLeft', fill: '#475569', fontSize: 10, dx: -10, dy: 80, letterSpacing: '0.1em' }}
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
          <ReferenceLine y={25} stroke="#64748b" strokeDasharray="4 4" strokeOpacity={0.7} strokeWidth={1.5} />
          <ReferenceLine y={75} stroke="#64748b" strokeDasharray="4 4" strokeOpacity={0.7} strokeWidth={1.5} />
          <ReferenceLine y={50} stroke="#ffffff" strokeDasharray="4 4" strokeOpacity={0.5} strokeWidth={2} />
          <ReferenceLine x={25} stroke="#64748b" strokeDasharray="4 4" strokeOpacity={0.7} strokeWidth={1.5} />
          <ReferenceLine x={75} stroke="#64748b" strokeDasharray="4 4" strokeOpacity={0.7} strokeWidth={1.5} />
          <ReferenceLine x={50} stroke="#ffffff" strokeDasharray="4 4" strokeOpacity={0.5} strokeWidth={2} />

          {/* Archetype labels are rendered as HTML overlays below for hover support. */}

          <Scatter
            name="Companies"
            data={groupedData}
            shape={renderCustomDot}
            onClick={(node, _idx, e) => handleDotClick(node, e)}
            cursor="pointer"
          />

          {/* Market Average Dot — rendered AFTER Scatter so it paints on top */}
          {marketAvg && (
              <ReferenceDot
                  x={marketAvg.x}
                  y={marketAvg.y}
                  r={0}
                  shape={(props: any) => {
                      const { cx, cy } = props;
                      // Track pixel position for the HTML overlay label
                      if (!avgDotPos || avgDotPos.cx !== cx || avgDotPos.cy !== cy) {
                          setTimeout(() => setAvgDotPos({ cx, cy }), 0);
                      }
                      return (
                          <g
                              onMouseEnter={() => setAvgHovered(true)}
                              onMouseLeave={() => setAvgHovered(false)}
                              style={{ cursor: 'default' }}
                          >
                              {/* Invisible larger hit area for easier hover */}
                              <circle cx={cx} cy={cy} r={28} fill="transparent" />
                              <circle cx={cx} cy={cy} r={16} fill="white" fillOpacity={0.10} stroke="white" strokeWidth={1.5} strokeOpacity={0.3}>
                                  <animate attributeName="r" values="16;24;16" dur="3s" repeatCount="indefinite" />
                                  <animate attributeName="fillOpacity" values="0.10;0.03;0.10" dur="3s" repeatCount="indefinite" />
                                  <animate attributeName="strokeOpacity" values="0.3;0.1;0.3" dur="3s" repeatCount="indefinite" />
                              </circle>
                              <circle cx={cx} cy={cy} r={7} fill="white" fillOpacity={0.95} stroke="white" strokeWidth={2}
                                  style={{ filter: 'drop-shadow(0 0 8px rgba(255,255,255,0.7)) drop-shadow(0 0 3px rgba(255,255,255,0.4))' }}
                              >
                                  <animate attributeName="fillOpacity" values="0.95;0.75;0.95" dur="3s" repeatCount="indefinite" />
                              </circle>
                          </g>
                      );
                  }}
              />
          )}
        </ScatterChart>
      </ResponsiveContainer>

      {/* Archetype quadrant labels — HTML overlays so they support hover explanations.
          Positions are relative to the chart container's plot area (left margin 50,
          top 20, right 20, bottom 40 in the ScatterChart). */}
      {[
        {
          key: 'THRIVING',
          color: '#34d399',
          opacity: 0.85,
          weight: 900,
          pos: 'top-3 right-5',
          tooltipPos: 'top-full mt-1 right-0',
          desc: 'High Reality + Strong Financials. Modern operations AND healthy business.',
        },
        {
          key: 'MODERNIZING',
          color: '#22d3ee',
          opacity: 0.75,
          weight: 700,
          pos: 'top-3 left-[60px]',
          tooltipPos: 'top-full mt-1 left-0',
          desc: 'High Reality + Weak Financials. Strong operations but financial pressure.',
        },
        {
          key: 'CASH COW',
          color: '#fbbf24',
          opacity: 0.75,
          weight: 700,
          pos: 'bottom-12 right-5',
          tooltipPos: 'bottom-full mb-1 right-0',
          desc: 'Low Reality + Strong Financials. Profitable but operationally outdated.',
        },
        {
          key: 'AT RISK',
          color: '#fb7185',
          opacity: 0.85,
          weight: 900,
          pos: 'bottom-12 left-[60px]',
          tooltipPos: 'bottom-full mb-1 left-0',
          desc: 'Low Reality + Weak Financials. Both operations and finances under pressure.',
        },
      ].map(label => (
        <div
          key={label.key}
          className={`absolute ${label.pos} z-[40] group cursor-help select-none`}
          style={{ pointerEvents: 'auto' }}
        >
          <span
            className="text-[10px] font-mono uppercase tracking-wider"
            style={{ color: label.color, opacity: label.opacity, fontWeight: label.weight, letterSpacing: '0.05em' }}
          >
            {label.key}
          </span>
          <div
            className={`absolute ${label.tooltipPos} hidden group-hover:block bg-slate-900/95 border border-slate-600 rounded-lg px-3 py-2 text-[10px] text-slate-200 font-normal w-[240px] shadow-xl leading-relaxed whitespace-normal pointer-events-none z-[60]`}
          >
            <div className="font-bold mb-1" style={{ color: label.color }}>{label.key}</div>
            {label.desc}
          </div>
        </div>
      ))}

      {/* HTML overlay for MARKET AVG label — renders above all SVG/chart layers */}
      {avgHovered && avgDotPos && (
          <div
              className="absolute pointer-events-none z-[60]"
              style={{
                  left: avgDotPos.cx - 52,
                  top: avgDotPos.cy - 36,
              }}
          >
              <div className="bg-[#020617]/95 backdrop-blur-sm border border-white/30 rounded-md px-3 py-1 shadow-lg shadow-black/50">
                  <span className="text-[10px] font-mono font-bold text-white tracking-widest">MARKET AVG</span>
              </div>
          </div>
      )}
    </div>
  );
};

(window as any).TruthMatrix = TruthMatrix;
