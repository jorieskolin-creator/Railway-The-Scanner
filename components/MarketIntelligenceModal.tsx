
const { useState, useMemo } = (window as any).React;

const LucideRaw = (window as any).Lucide || {};
const MIIcons = new Proxy(LucideRaw, {
  get: (target, prop) => {
    if (prop in target) return target[prop];
    return () => null;
  }
});
const { X, Loader2, TrendingUp, Target, Crosshair, ShieldAlert, Download, ChevronRight, Activity, BarChart3, Gauge, Radar, RefreshCw } = MIIcons;

const {
  BarChart, Bar, XAxis, YAxis, CartesianGrid, Tooltip, Legend, ResponsiveContainer,
  RadarChart, Radar: RechartsRadar, PolarGrid, PolarAngleAxis, PolarRadiusAxis,
  ScatterChart, Scatter, Cell, ComposedChart, Line, ZAxis,
} = (window as any).Recharts || {};

// ═══════════════════════════════════════════════════════════════════════════════
// COLORS
// ═══════════════════════════════════════════════════════════════════════════════
const CATEGORY_COLORS: Record<string, string> = {
  "Technology & Digital": "#06b6d4",
  "Economy & Business": "#8b5cf6",
  "Industry & Infrastructure": "#f59e0b",
  "Environment & Resources": "#10b981",
  "Society & Governance": "#ec4899",
  "People & Well-being": "#f97316",
  "Science & Demographics": "#6366f1",
};

const ARCHETYPE_COLORS = {
  thriving: "#10b981",
  modernizing: "#f59e0b",
  cash_cow: "#3b82f6",
  at_risk: "#f43f5e",
};

const HEALTH_COLOR = (score: number) => score >= 70 ? "#10b981" : score >= 40 ? "#f59e0b" : "#f43f5e";

const RADAR_COLORS = ["#06b6d4", "#8b5cf6", "#f59e0b", "#10b981", "#ec4899", "#f97316", "#6366f1", "#f43f5e"];

// Short labels for radar axes
const CATEGORY_SHORT: Record<string, string> = {
  "Technology & Digital": "Tech",
  "Economy & Business": "Economy",
  "Industry & Infrastructure": "Industry",
  "Environment & Resources": "Environment",
  "Society & Governance": "Society",
  "People & Well-being": "People",
  "Science & Demographics": "Science",
};

// ═══════════════════════════════════════════════════════════════════════════════
// TAB 1: EXECUTIVE SUMMARY
// ═══════════════════════════════════════════════════════════════════════════════

const HealthGauge = ({ domain, score, companyCount }: { domain: string; score: number; companyCount: number }) => {
  const color = HEALTH_COLOR(score);
  const angle = (score / 100) * 180;
  const rad = (angle * Math.PI) / 180;
  const r = 40;
  const cx = 50;
  const cy = 50;
  const x = cx - r * Math.cos(rad);
  const y = cy - r * Math.sin(rad);
  const largeArc = angle > 180 ? 1 : 0;

  return (
    <div className="bg-black/20 border border-white/10 rounded-lg p-3 text-center">
      <svg viewBox="0 0 100 60" className="w-full max-w-[120px] mx-auto">
        <path d={`M ${cx - r} ${cy} A ${r} ${r} 0 0 1 ${cx + r} ${cy}`} fill="none" stroke="#334155" strokeWidth="6" />
        {score > 0 && (
          <path d={`M ${cx - r} ${cy} A ${r} ${r} 0 ${largeArc} 1 ${x} ${y}`} fill="none" stroke={color} strokeWidth="6" strokeLinecap="round" />
        )}
        <text x={cx} y={cy - 5} textAnchor="middle" fill={color} fontSize="14" fontWeight="bold">{Math.round(score)}</text>
        <text x={cx} y={cy + 8} textAnchor="middle" fill="#94a3b8" fontSize="6">/100</text>
      </svg>
      <div className="text-[10px] font-bold text-slate-200 truncate mt-1" title={domain}>{domain}</div>
      <div className="text-[8px] text-slate-500">{companyCount} companies</div>
    </div>
  );
};

const CoverageRing = ({ coverage }: { coverage: any }) => {
  if (!coverage) return null;
  const pct = coverage.pct || 0;
  const circumference = 2 * Math.PI * 36;
  const offset = circumference - (pct / 100) * circumference;
  const color = pct >= 70 ? "#10b981" : pct >= 40 ? "#f59e0b" : "#f43f5e";

  return (
    <div className="bg-black/20 border border-white/10 rounded-lg p-4 flex flex-col items-center">
      <div className="text-[10px] font-bold text-cyan-300 uppercase tracking-wide mb-2">Foresight Coverage</div>
      <svg viewBox="0 0 80 80" className="w-24 h-24">
        <circle cx="40" cy="40" r="36" fill="none" stroke="#334155" strokeWidth="5" />
        <circle cx="40" cy="40" r="36" fill="none" stroke={color} strokeWidth="5" strokeLinecap="round"
          strokeDasharray={circumference} strokeDashoffset={offset}
          transform="rotate(-90 40 40)" />
        <text x="40" y="36" textAnchor="middle" fill="white" fontSize="14" fontWeight="bold">{coverage.covered}</text>
        <text x="40" y="48" textAnchor="middle" fill="#94a3b8" fontSize="8">/{coverage.total}</text>
      </svg>
      <div className="text-[9px] text-slate-400 mt-1">themes with 3+ signals</div>
    </div>
  );
};

const ExecutiveSummaryTab = ({ data }: { data: any }) => {
  return (
    <div className="space-y-6">
      {/* Market Health Gauges */}
      <div>
        <h4 className="text-[10px] font-bold text-emerald-300 uppercase tracking-wide mb-3 flex items-center gap-2">
          <Activity size={12} /> Market Health by Domain
        </h4>
        <div className="grid grid-cols-2 sm:grid-cols-3 md:grid-cols-4 lg:grid-cols-5 gap-3">
          {(data.market_health || []).map((h: any) => (
            <HealthGauge key={h.domain} domain={h.domain} score={h.score} companyCount={h.company_count} />
          ))}
        </div>
      </div>

      <div className="grid grid-cols-1 md:grid-cols-3 gap-4">
        {/* Foresight Coverage */}
        <CoverageRing coverage={data.foresight_coverage} />

        {/* Demand Intensity Bars */}
        <div className="md:col-span-2 bg-black/20 border border-white/10 rounded-lg p-4">
          <div className="text-[10px] font-bold text-amber-300 uppercase tracking-wide mb-3">Demand Intensity (avg gap per dimension)</div>
          {ResponsiveContainer && BarChart ? (
            <ResponsiveContainer width="100%" height={200}>
              <BarChart data={data.demand_intensity || []} layout="vertical" margin={{ left: 80, right: 20, top: 5, bottom: 5 }}>
                <CartesianGrid strokeDasharray="3 3" stroke="#1e293b" />
                <XAxis type="number" tick={{ fill: '#94a3b8', fontSize: 10 }} />
                <YAxis type="category" dataKey="dimension" tick={{ fill: '#cbd5e1', fontSize: 10 }} width={75} />
                <Tooltip contentStyle={{ background: '#0f172a', border: '1px solid #334155', borderRadius: 8, fontSize: 11 }} />
                <Bar dataKey="avg_gap" radius={[0, 4, 4, 0]} name="Avg Gap">
                  {(data.demand_intensity || []).map((entry: any, idx: number) => (
                    <Cell key={idx} fill={entry.avg_gap >= 0 ? '#10b981' : '#f43f5e'} />
                  ))}
                </Bar>
              </BarChart>
            </ResponsiveContainer>
          ) : (
            <div className="text-slate-500 text-xs">Charts unavailable</div>
          )}
        </div>
      </div>

      {/* Quick stats row */}
      <div className="grid grid-cols-2 md:grid-cols-4 gap-3">
        <div className="bg-black/20 border border-white/10 rounded-lg p-3 text-center">
          <div className="text-2xl font-bold text-white">{data.company_count}</div>
          <div className="text-[9px] text-slate-400 uppercase">Total Companies</div>
        </div>
        <div className="bg-black/20 border border-white/10 rounded-lg p-3 text-center">
          <div className="text-2xl font-bold text-cyan-400">{data.detailed_count}</div>
          <div className="text-[9px] text-slate-400 uppercase">With Full Data</div>
        </div>
        <div className="bg-black/20 border border-white/10 rounded-lg p-3 text-center">
          <div className="text-2xl font-bold text-emerald-400">{(data.top_foresights || []).length}</div>
          <div className="text-[9px] text-slate-400 uppercase">Top Foresight Themes</div>
        </div>
        <div className="bg-black/20 border border-white/10 rounded-lg p-3 text-center">
          <div className="text-2xl font-bold text-rose-400">{(data.vulnerable_companies || []).length}</div>
          <div className="text-[9px] text-slate-400 uppercase">Vulnerable Companies</div>
        </div>
      </div>
    </div>
  );
};

// ═══════════════════════════════════════════════════════════════════════════════
// TAB 2: FUTURE DEMAND
// ═══════════════════════════════════════════════════════════════════════════════

const FutureDemandTab = ({ data }: { data: any }) => {
  const foresights = data.top_foresights || [];
  const crossImpact = data.cross_impact || [];
  const industryRadar = data.industry_radar || [];

  // Build cumulative % for Pareto
  const totalCount = foresights.reduce((s: number, f: any) => s + f.count, 0) || 1;
  let cumulative = 0;
  const paretoData = foresights.map((f: any) => {
    cumulative += f.count;
    return { ...f, cumPct: Math.round((cumulative / totalCount) * 100) };
  });

  // Build cross-impact matrix data
  const ciThemes = [...new Set(crossImpact.map((c: any) => c.theme))];
  const ciDimensions = ["Operating Model", "Service Portfolio", "Values", "Strategy", "AI Readiness"];
  const ciMatrix: Record<string, Record<string, number>> = {};
  for (const item of crossImpact) {
    if (!ciMatrix[item.theme]) ciMatrix[item.theme] = {};
    ciMatrix[item.theme][item.dimension] = item.count;
  }
  const maxCICount = Math.max(...crossImpact.map((c: any) => c.count), 1);

  // Radar data: transform for Recharts
  const categories = Object.keys(CATEGORY_SHORT);
  const radarData = categories.map(cat => {
    const point: any = { category: CATEGORY_SHORT[cat] };
    industryRadar.forEach((ir: any) => {
      point[ir.domain] = ir.normalized_scores[cat] || 0;
    });
    return point;
  });

  return (
    <div className="space-y-6">
      {/* TOP10 Foresights Pareto */}
      <div className="bg-black/20 border border-white/10 rounded-lg p-4">
        <h4 className="text-[10px] font-bold text-cyan-300 uppercase tracking-wide mb-3">TOP {foresights.length} Foresight Themes (Pareto)</h4>
        {paretoData.length > 0 && ComposedChart ? (
          <ResponsiveContainer width="100%" height={300}>
            <ComposedChart data={paretoData} margin={{ left: 10, right: 30, top: 10, bottom: 60 }}>
              <CartesianGrid strokeDasharray="3 3" stroke="#1e293b" />
              <XAxis dataKey="theme" tick={{ fill: '#94a3b8', fontSize: 8 }} angle={-35} textAnchor="end" interval={0} height={80} />
              <YAxis yAxisId="count" tick={{ fill: '#94a3b8', fontSize: 10 }} />
              <YAxis yAxisId="pct" orientation="right" tick={{ fill: '#94a3b8', fontSize: 10 }} domain={[0, 100]} unit="%" />
              <Tooltip contentStyle={{ background: '#0f172a', border: '1px solid #334155', borderRadius: 8, fontSize: 11 }}
                formatter={(value: any, name: string) => [value, name === 'cumPct' ? 'Cumulative %' : 'Signals']} />
              <Bar yAxisId="count" dataKey="count" name="Signals" radius={[4, 4, 0, 0]}>
                {paretoData.map((entry: any, i: number) => (
                  <Cell key={i} fill={CATEGORY_COLORS[entry.category] || '#6366f1'} />
                ))}
              </Bar>
              <Line yAxisId="pct" dataKey="cumPct" stroke="#f43f5e" strokeWidth={2} dot={{ r: 3 }} name="Cumulative %" />
            </ComposedChart>
          </ResponsiveContainer>
        ) : (
          <div className="text-slate-500 text-xs py-8 text-center">No foresight data available</div>
        )}
      </div>

      {/* Cross-Impact Matrix */}
      {ciThemes.length > 0 && (
        <div className="bg-black/20 border border-white/10 rounded-lg p-4 overflow-x-auto">
          <h4 className="text-[10px] font-bold text-violet-300 uppercase tracking-wide mb-3">Theme x Dimension Cross-Impact Matrix</h4>
          <table className="w-full text-[10px]">
            <thead>
              <tr>
                <th className="text-left text-slate-400 p-1 font-medium">Theme</th>
                {ciDimensions.map(d => (
                  <th key={d} className="text-center text-slate-400 p-1 font-medium whitespace-nowrap">{d}</th>
                ))}
              </tr>
            </thead>
            <tbody>
              {(ciThemes as string[]).map((theme: string) => (
                <tr key={theme} className="border-t border-white/5">
                  <td className="p-1 text-slate-300 whitespace-nowrap">{theme}</td>
                  {ciDimensions.map(dim => {
                    const val = ciMatrix[theme]?.[dim] || 0;
                    const intensity = val / maxCICount;
                    const bg = val > 0
                      ? `rgba(99, 102, 241, ${0.15 + intensity * 0.6})`
                      : 'transparent';
                    return (
                      <td key={dim} className="p-1 text-center font-mono text-slate-200" style={{ backgroundColor: bg }}>
                        {val > 0 ? val : '-'}
                      </td>
                    );
                  })}
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}

      {/* Industry Foresight Radar */}
      {industryRadar.length > 0 && RechartsRadar && (
        <div className="bg-black/20 border border-white/10 rounded-lg p-4">
          <h4 className="text-[10px] font-bold text-emerald-300 uppercase tracking-wide mb-3">Industry Foresight Radar (top domains)</h4>
          <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
            {industryRadar.slice(0, 4).map((ir: any, idx: number) => (
              <div key={ir.domain} className="bg-black/30 rounded-lg p-2">
                <div className="text-[10px] font-bold text-slate-200 text-center mb-1">{ir.domain} <span className="text-slate-500">({ir.total_signals} signals)</span></div>
                <ResponsiveContainer width="100%" height={200}>
                  <RadarChart data={radarData} cx="50%" cy="50%">
                    <PolarGrid stroke="#334155" />
                    <PolarAngleAxis dataKey="category" tick={{ fill: '#94a3b8', fontSize: 8 }} />
                    <PolarRadiusAxis tick={false} domain={[0, 100]} />
                    <RechartsRadar dataKey={ir.domain} stroke={RADAR_COLORS[idx]} fill={RADAR_COLORS[idx]} fillOpacity={0.25} strokeWidth={2} />
                  </RadarChart>
                </ResponsiveContainer>
              </div>
            ))}
          </div>
        </div>
      )}
    </div>
  );
};

// ═══════════════════════════════════════════════════════════════════════════════
// TAB 3: CURRENT DEMAND
// ═══════════════════════════════════════════════════════════════════════════════

const CurrentDemandTab = ({ data }: { data: any }) => {
  const causes = data.top_causes || [];
  const archetypeFlow = data.archetype_flow || [];

  return (
    <div className="space-y-6">
      {/* TOP10 Root Causes */}
      <div className="bg-black/20 border border-white/10 rounded-lg p-4">
        <h4 className="text-[10px] font-bold text-blue-300 uppercase tracking-wide mb-3">TOP {causes.length} Root Causes / Strategic Patterns</h4>
        {causes.length > 0 && BarChart ? (
          <ResponsiveContainer width="100%" height={300}>
            <BarChart data={causes} margin={{ left: 10, right: 20, top: 10, bottom: 60 }}>
              <CartesianGrid strokeDasharray="3 3" stroke="#1e293b" />
              <XAxis dataKey="cluster_label" tick={{ fill: '#94a3b8', fontSize: 8 }} angle={-30} textAnchor="end" interval={0} height={80} />
              <YAxis tick={{ fill: '#94a3b8', fontSize: 10 }} />
              <Tooltip cursor={false} active={false} contentStyle={{ display: 'none' }} />
              <Legend wrapperStyle={{ fontSize: 10 }} />
              <Bar dataKey="why_count" stackId="a" fill="#3b82f6" name="WHY (strategic)" radius={[0, 0, 0, 0]} />
              <Bar dataKey="what_count" stackId="a" fill="#14b8a6" name="WHAT (operational)" radius={[4, 4, 0, 0]} />
            </BarChart>
          </ResponsiveContainer>
        ) : (
          <div className="text-slate-500 text-xs py-8 text-center">No root cause data available</div>
        )}

        {/* Example proposals */}
        {causes.length > 0 && (
          <div className="mt-3 space-y-2">
            {causes.slice(0, 5).map((c: any) => (
              <div key={c.cluster_label} className="bg-black/30 rounded p-2">
                <div className="text-[10px] font-bold text-slate-200">{c.cluster_label} <span className="text-slate-500">({c.count} matches)</span></div>
                {(c.examples || []).slice(0, 5).map((ex: any, i: number) => (
                  <div key={i} className="text-[9px] text-slate-400 mt-1 flex gap-1">
                    <ChevronRight size={10} className="text-slate-600 shrink-0 mt-0.5" />
                    <span><span className="text-cyan-400">{ex.company}:</span> {ex.text}</span>
                  </div>
                ))}
              </div>
            ))}
          </div>
        )}
      </div>

      {/* Archetype Flow by Domain */}
      <div className="bg-black/20 border border-white/10 rounded-lg p-4">
        <h4 className="text-[10px] font-bold text-emerald-300 uppercase tracking-wide mb-3">Archetype Distribution by Domain</h4>
        {archetypeFlow.length > 0 && BarChart ? (
          <ResponsiveContainer width="100%" height={300}>
            <BarChart data={archetypeFlow} margin={{ left: 10, right: 20, top: 10, bottom: 60 }}>
              <CartesianGrid strokeDasharray="3 3" stroke="#1e293b" />
              <XAxis dataKey="domain" tick={{ fill: '#94a3b8', fontSize: 8 }} angle={-30} textAnchor="end" interval={0} height={80} />
              <YAxis tick={{ fill: '#94a3b8', fontSize: 10 }} />
              <Tooltip contentStyle={{ background: '#0f172a', border: '1px solid #334155', borderRadius: 8, fontSize: 11 }} />
              <Legend wrapperStyle={{ fontSize: 10 }} />
              <Bar dataKey="thriving" stackId="a" fill={ARCHETYPE_COLORS.thriving} name="Thriving" />
              <Bar dataKey="modernizing" stackId="a" fill={ARCHETYPE_COLORS.modernizing} name="Modernizing" />
              <Bar dataKey="cash_cow" stackId="a" fill={ARCHETYPE_COLORS.cash_cow} name="Cash Cow" />
              <Bar dataKey="at_risk" stackId="a" fill={ARCHETYPE_COLORS.at_risk} name="At Risk" radius={[4, 4, 0, 0]} />
            </BarChart>
          </ResponsiveContainer>
        ) : (
          <div className="text-slate-500 text-xs py-8 text-center">No archetype data available</div>
        )}
      </div>
    </div>
  );
};

// ═══════════════════════════════════════════════════════════════════════════════
// TAB 4: SALES PRIORITIES
// ═══════════════════════════════════════════════════════════════════════════════

const SalesPrioritiesTab = ({ data, onCompanyClick }: { data: any; onCompanyClick: (company: any) => void }) => {
  const [sortKey, setSortKey] = useState('vulnerability_score');
  const [sortDir, setSortDir] = useState('desc');
  const vulnerable = data.vulnerable_companies || [];

  const sorted = useMemo(() => {
    return [...vulnerable].sort((a: any, b: any) => {
      const va = a[sortKey] ?? 0;
      const vb = b[sortKey] ?? 0;
      return sortDir === 'desc' ? vb - va : va - vb;
    });
  }, [vulnerable, sortKey, sortDir]);

  const handleSort = (key: string) => {
    if (sortKey === key) {
      setSortDir(sortDir === 'desc' ? 'asc' : 'desc');
    } else {
      setSortKey(key);
      setSortDir('desc');
    }
  };

  const handleExportCSV = () => {
    if (sorted.length === 0) return;
    const headers = ['Rank', 'Company', 'Domain', 'Vulnerability Score', 'Residual', 'Gap', 'Archetype', 'Reasons'];
    const rows = sorted.map((v: any, i: number) => [
      i + 1, v.name, v.domain, v.vulnerability_score, v.residual, v.gap, v.archetype, (v.reasons || []).join('; ')
    ]);
    const csv = [headers, ...rows].map(r => r.map((c: any) => `"${String(c).replace(/"/g, '""')}"`).join(',')).join('\n');
    const blob = new Blob([csv], { type: 'text/csv' });
    const url = URL.createObjectURL(blob);
    const a = document.createElement('a');
    a.href = url;
    a.download = 'vulnerable_companies.csv';
    a.click();
    URL.revokeObjectURL(url);
  };

  const vulnColor = (score: number) => {
    if (score >= 60) return 'text-rose-400 bg-rose-500/10';
    if (score >= 30) return 'text-amber-400 bg-amber-500/10';
    return 'text-slate-400';
  };

  const SortHeader = ({ label, dataKey }: { label: string; dataKey: string }) => (
    <th className="p-2 text-left cursor-pointer hover:text-white select-none whitespace-nowrap" onClick={() => handleSort(dataKey)}>
      {label} {sortKey === dataKey ? (sortDir === 'desc' ? '\u2193' : '\u2191') : ''}
    </th>
  );

  return (
    <div className="space-y-6">
      {/* Vulnerability Table */}
      <div className="bg-black/20 border border-white/10 rounded-lg p-4">
        <div className="flex justify-between items-center mb-3">
          <h4 className="text-[10px] font-bold text-rose-300 uppercase tracking-wide flex items-center gap-2">
            <ShieldAlert size={12} /> Most Vulnerable Companies
          </h4>
          <button onClick={handleExportCSV} className="flex items-center gap-1 px-2 py-1 text-[10px] bg-white/5 hover:bg-white/10 border border-white/10 rounded text-slate-300">
            <Download size={10} /> CSV
          </button>
        </div>

        {sorted.length > 0 ? (
          <div className="overflow-x-auto">
            <table className="w-full text-[10px]">
              <thead>
                <tr className="text-slate-500 border-b border-white/10">
                  <th className="p-2 text-left">#</th>
                  <th className="p-2 text-left">Company</th>
                  <th className="p-2 text-left">Domain</th>
                  <SortHeader label="Vuln. Score" dataKey="vulnerability_score" />
                  <SortHeader label="Residual" dataKey="residual" />
                  <SortHeader label="Gap" dataKey="gap" />
                  <th className="p-2 text-left">Archetype</th>
                  <th className="p-2 text-left">Reason</th>
                </tr>
              </thead>
              <tbody>
                {sorted.map((v: any, i: number) => (
                  <tr key={v.name} className="border-t border-white/5 hover:bg-white/5 cursor-pointer transition-colors"
                    onClick={() => onCompanyClick({ companyName: v.name, domain: v.domain })}>
                    <td className="p-2 text-slate-500">{i + 1}</td>
                    <td className="p-2 text-cyan-300 font-medium">{v.name}</td>
                    <td className="p-2 text-slate-400">{v.domain}</td>
                    <td className={`p-2 font-mono font-bold ${vulnColor(v.vulnerability_score)}`}>
                      {v.vulnerability_score}
                    </td>
                    <td className={`p-2 font-mono ${v.residual < 0 ? 'text-rose-400' : 'text-emerald-400'}`}>
                      {v.residual > 0 ? '+' : ''}{v.residual}
                    </td>
                    <td className={`p-2 font-mono ${v.gap > 10 ? 'text-amber-400' : 'text-slate-400'}`}>
                      {v.gap}
                    </td>
                    <td className="p-2">
                      <span className={`px-1.5 py-0.5 rounded text-[9px] font-bold ${
                        v.archetype === 'At Risk' ? 'bg-rose-500/20 text-rose-300' :
                        v.archetype === 'Cash Cow' ? 'bg-blue-500/20 text-blue-300' :
                        v.archetype === 'Modernizing' ? 'bg-amber-500/20 text-amber-300' :
                        'bg-emerald-500/20 text-emerald-300'
                      }`}>{v.archetype}</span>
                    </td>
                    <td className="p-2 text-slate-500 max-w-[200px] truncate" title={(v.reasons || []).join(', ')}>
                      {(v.reasons || [])[0] || '-'}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        ) : (
          <div className="text-slate-500 text-xs py-8 text-center">No vulnerability data available</div>
        )}
      </div>

      {/* Vulnerability Scatter Chart */}
      {sorted.length > 0 && ScatterChart && (
        <div className="bg-black/20 border border-white/10 rounded-lg p-4">
          <h4 className="text-[10px] font-bold text-violet-300 uppercase tracking-wide mb-3">Reality Score vs Financial Index (bubble = vulnerability)</h4>
          <ResponsiveContainer width="100%" height={350}>
            <ScatterChart margin={{ left: 10, right: 20, top: 10, bottom: 10 }}>
              <CartesianGrid strokeDasharray="3 3" stroke="#1e293b" />
              <XAxis type="number" dataKey="reality_score" name="Reality Score" tick={{ fill: '#94a3b8', fontSize: 10 }} domain={[0, 100]} label={{ value: 'Reality Score', fill: '#64748b', fontSize: 10, position: 'bottom' }} />
              <YAxis type="number" dataKey="financial_index" name="Financial Index" tick={{ fill: '#94a3b8', fontSize: 10 }} domain={[0, 100]} label={{ value: 'Financial Index', fill: '#64748b', fontSize: 10, angle: -90, position: 'insideLeft' }} />
              <ZAxis type="number" dataKey="vulnerability_score" range={[30, 400]} />
              <Tooltip cursor={{ strokeDasharray: '3 3' }}
                contentStyle={{ background: '#0f172a', border: '1px solid #334155', borderRadius: 8, fontSize: 11 }}
                formatter={(value: any, name: string) => [typeof value === 'number' ? value.toFixed(1) : value, name]} />
              <Scatter data={sorted.filter((v: any) => v.financial_index !== null)} name="Companies">
                {sorted.filter((v: any) => v.financial_index !== null).map((v: any, i: number) => (
                  <Cell key={i} fill={v.vulnerability_score >= 60 ? '#f43f5e' : v.vulnerability_score >= 30 ? '#f59e0b' : '#6366f1'} fillOpacity={0.7} />
                ))}
              </Scatter>
            </ScatterChart>
          </ResponsiveContainer>
        </div>
      )}
    </div>
  );
};

// ═══════════════════════════════════════════════════════════════════════════════
// MAIN MODAL
// ═══════════════════════════════════════════════════════════════════════════════

const TABS = [
  { id: 'executive', label: 'Executive Summary', icon: Gauge },
  { id: 'future', label: 'Future Demand', icon: TrendingUp },
  { id: 'current', label: 'Current Demand', icon: Target },
  { id: 'sales', label: 'Sales Priorities', icon: Crosshair },
];

const MarketIntelligenceModal = ({ isOpen, data, loading, onClose, onRefresh, onCompanyClick }: {
  isOpen: boolean;
  data: any;
  loading: boolean;
  onClose: () => void;
  onRefresh?: () => void;
  onCompanyClick: (company: any) => void;
}) => {
  const [activeTab, setActiveTab] = useState('executive');

  if (!isOpen) return null;

  return (
    <div className="fixed inset-0 z-[60] flex items-center justify-center p-4">
      <div className="absolute inset-0 bg-black/90 backdrop-blur-sm" onClick={onClose} />
      <div className="relative w-full max-w-6xl bg-[#0f172a] border border-cyan-500/30 rounded-xl shadow-2xl z-10 flex flex-col max-h-[90vh] overflow-hidden">
        {/* Header */}
        <div className="p-4 border-b border-white/10 flex justify-between items-center bg-black/20 shrink-0">
          <h3 className="font-bold text-white flex items-center gap-2">
            <BarChart3 size={18} className="text-cyan-400" />
            Market Intelligence
            {data && <span className="text-[10px] text-slate-500 font-normal ml-2">{data.company_count} companies analyzed</span>}
          </h3>
          <div className="flex items-center gap-2">
            {onRefresh && (
              <button onClick={onRefresh} disabled={loading} className="text-slate-400 hover:text-cyan-300 disabled:opacity-30 transition-colors" title="Refresh data">
                <RefreshCw size={16} className={loading ? 'animate-spin' : ''} />
              </button>
            )}
            <button onClick={onClose} className="text-slate-400 hover:text-white">
              <X size={18} />
            </button>
          </div>
        </div>

        {/* Tab Navigation */}
        <div className="flex items-center gap-1 p-2 bg-black/30 border-b border-white/5 shrink-0 overflow-x-auto">
          {TABS.map(tab => {
            const Icon = tab.icon;
            return (
              <button key={tab.id} onClick={() => setActiveTab(tab.id)}
                className={`flex items-center gap-1.5 px-3 py-1.5 rounded text-[10px] font-bold uppercase tracking-wide transition-all whitespace-nowrap ${
                  activeTab === tab.id
                    ? 'bg-cyan-500/20 text-cyan-300 border border-cyan-500/30'
                    : 'text-slate-500 hover:text-slate-300 hover:bg-white/5 border border-transparent'
                }`}>
                <Icon size={12} />
                {tab.label}
              </button>
            );
          })}
        </div>

        {/* Content */}
        <div className="overflow-y-auto p-6 space-y-6">
          {loading ? (
            <div className="flex flex-col items-center justify-center py-16">
              <Loader2 className="animate-spin text-cyan-400 mb-4" size={32} />
              <p className="text-slate-400 text-sm">Computing market intelligence...</p>
            </div>
          ) : !data ? (
            <div className="text-center py-16 text-slate-400">No data available</div>
          ) : (
            <>
              {activeTab === 'executive' && <ExecutiveSummaryTab data={data} />}
              {activeTab === 'future' && <FutureDemandTab data={data} />}
              {activeTab === 'current' && <CurrentDemandTab data={data} />}
              {activeTab === 'sales' && <SalesPrioritiesTab data={data} onCompanyClick={onCompanyClick} />}
            </>
          )}
        </div>
      </div>
    </div>
  );
};

(window as any).MarketIntelligenceModal = MarketIntelligenceModal;
