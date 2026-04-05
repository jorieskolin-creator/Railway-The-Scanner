
const { useState, useEffect, useRef } = (window as any).React;

const LucideRaw = (window as any).Lucide || {};
const ObsIcons = new Proxy(LucideRaw, {
  get: (target: any, prop: string) => {
    if (prop in target) return target[prop];
    return () => null;
  }
});
const { X, Loader2, Globe, Map, FileText, ChevronDown, ChevronRight, RefreshCw, Eye, Clock, CheckCircle, AlertTriangle, Telescope, BarChart3 } = ObsIcons;

const {
  ComposedChart, Bar, Line, XAxis, YAxis, CartesianGrid, Tooltip, ResponsiveContainer, Cell,
} = (window as any).Recharts || {};

// Category colors for Pareto chart (matching MI modal)
const OBS_CATEGORY_COLORS: Record<string, string> = {
  "Technology & Digital": "#06b6d4",
  "Economy & Business": "#8b5cf6",
  "Industry & Infrastructure": "#f59e0b",
  "Environment & Resources": "#10b981",
  "Society & Governance": "#ec4899",
  "People & Well-being": "#f97316",
  "Science & Demographics": "#6366f1",
};

// ═══════════════════════════════════════════════════════════════════════════════
// HELPERS
// ═══════════════════════════════════════════════════════════════════════════════

const SECTION_COLORS = {
  global: { border: "border-cyan-500/30", bg: "bg-cyan-500/10", text: "text-cyan-300", icon: "text-cyan-400" },
  finland: { border: "border-violet-500/30", bg: "bg-violet-500/10", text: "text-violet-300", icon: "text-violet-400" },
  domain: { border: "border-amber-500/30", bg: "bg-amber-500/10", text: "text-amber-300", icon: "text-amber-400" },
};

const LAYER_DESCRIPTIONS: Record<string, string> = {
  global: "A futurologist's briefing on the converging global forces reshaping business. Synthesizes validated signals from the Foresight Knowledge Base into macro-level patterns — identifying which forces are accelerating, which are decelerating, and where unexpected cross-impacts create strategic inflection points. Frames the future through thinking capacity and adaptability, not technology adoption.",
  finland: "Maps how global forces specifically land in the Finnish business landscape. Grounds every claim in actual company data — market health scores, archetype distributions, and gap patterns — to reveal where Finnish industry leads, where it lags, and where genuine strategic opportunities exist. Includes stakeholder dynamics analysis across organizational roles.",
  domain: "Per-domain strategic analysis with timescale mapping. Each dossier examines a specific industry's position using company-level data, identifies forces by urgency horizon (Act Now / Prepare / Position), explores 'Own Race' opportunities, and offers a contrarian signal that challenges conventional domain wisdom.",
};

const formatEssayText = (text: string) => {
  if (!text) return null;
  const parts = text.split(/\n+/);
  return parts.map((part: string, i: number) => {
    const trimmed = part.trim();
    if (!trimmed) return null;
    // Skip raw horizontal rules
    if (/^-{3,}$/.test(trimmed)) return <hr key={i} className="border-slate-700/40 my-6" />;
    // Headers
    if (trimmed.startsWith("## ")) {
      return (
        <h3 key={i} className="text-base font-bold text-slate-100 mt-6 mb-3 border-b border-white/10 pb-1">
          {trimmed.replace(/^##\s*/, "")}
        </h3>
      );
    }
    if (trimmed.startsWith("# ")) {
      return (
        <h2 key={i} className="text-lg font-bold text-slate-50 mt-6 mb-3">
          {trimmed.replace(/^#\s*/, "")}
        </h2>
      );
    }
    // Bold text within paragraphs
    const rendered = trimmed.split(/(\*\*[^*]+\*\*)/).map((seg: string, j: number) => {
      if (seg.startsWith("**") && seg.endsWith("**")) {
        return <strong key={j} className="text-slate-100 font-semibold">{seg.slice(2, -2)}</strong>;
      }
      return <span key={j}>{seg}</span>;
    });
    return (
      <p key={i} className="text-[13px] text-slate-300 leading-relaxed mb-3">
        {rendered}
      </p>
    );
  });
};

// ═══════════════════════════════════════════════════════════════════════════════
// SECTION COMPONENTS
// ═══════════════════════════════════════════════════════════════════════════════

const VerificationBadge = ({ verification }: { verification: any }) => {
  if (!verification) return null;
  const passed = verification.passed !== false;
  const issues = verification.issues || [];
  const violations = verification.tech_framing_violations || [];
  return (
    <div className={`inline-flex items-center gap-1 px-2 py-0.5 rounded text-[9px] font-mono ${
      passed ? "bg-emerald-500/15 text-emerald-300 border border-emerald-500/30"
             : "bg-amber-500/15 text-amber-300 border border-amber-500/30"
    }`}>
      {passed ? <CheckCircle size={10} /> : <AlertTriangle size={10} />}
      {passed ? "Verified" : `${issues.length + violations.length} issues`}
    </div>
  );
};

const EssaySection = ({ title, icon: Icon, colorKey, text, verification, metadata, description, defaultOpen = false }: any) => {
  const [isOpen, setIsOpen] = useState(defaultOpen);
  const colors = SECTION_COLORS[colorKey as keyof typeof SECTION_COLORS] || SECTION_COLORS.global;

  return (
    <div className={`border ${colors.border} rounded-lg overflow-hidden mb-4`}>
      <button
        onClick={() => setIsOpen(!isOpen)}
        className={`w-full flex items-center justify-between px-4 py-3 ${colors.bg} hover:brightness-110 transition-all`}
      >
        <div className="flex items-center gap-3">
          <Icon size={18} className={colors.icon} />
          <span className={`font-bold text-sm ${colors.text}`}>{title}</span>
          {verification && <VerificationBadge verification={verification} />}
        </div>
        <div className="flex items-center gap-2">
          {metadata && (
            <span className="text-[9px] text-slate-500 font-mono">{metadata}</span>
          )}
          {isOpen ? <ChevronDown size={16} className="text-slate-400" /> : <ChevronRight size={16} className="text-slate-400" />}
        </div>
      </button>
      {/* Layer description — always visible */}
      {description && (
        <div className="px-5 py-3 bg-black/10 border-b border-white/5">
          <p className="text-[11px] text-slate-400 leading-relaxed">{description}</p>
        </div>
      )}
      {isOpen && (
        <div className="px-5 py-4 bg-black/20 max-h-[600px] overflow-y-auto">
          {formatEssayText(text)}
        </div>
      )}
    </div>
  );
};

const DomainDossierTabs = ({ dossiers, defaultOpen = false }: { dossiers: any[]; defaultOpen?: boolean }) => {
  const [isOpen, setIsOpen] = useState(defaultOpen);
  const [activeDomain, setActiveDomain] = useState(0);
  if (!dossiers || dossiers.length === 0) return null;

  const active = dossiers[activeDomain];
  const colors = SECTION_COLORS.domain;

  return (
    <div className={`border ${colors.border} rounded-lg overflow-hidden mb-4`}>
      {/* Collapsible header — same style as EssaySection */}
      <button
        onClick={() => setIsOpen(!isOpen)}
        className={`w-full flex items-center justify-between px-4 py-3 ${colors.bg} hover:brightness-110 transition-all`}
      >
        <div className="flex items-center gap-3">
          <FileText size={18} className={colors.icon} />
          <span className={`font-bold text-sm ${colors.text}`}>Layer 3: Domain Force Dossiers</span>
        </div>
        <div className="flex items-center gap-2">
          <span className="text-[9px] text-slate-500 font-mono">{dossiers.length} domains</span>
          {isOpen ? <ChevronDown size={16} className="text-slate-400" /> : <ChevronRight size={16} className="text-slate-400" />}
        </div>
      </button>

      {/* Layer description — always visible */}
      <div className="px-5 py-3 bg-black/10 border-b border-white/5">
        <p className="text-[11px] text-slate-400 leading-relaxed">{LAYER_DESCRIPTIONS.domain}</p>
      </div>

      {isOpen && (
        <>
          {/* Domain tabs */}
          <div className="flex flex-wrap gap-1 px-3 py-2 bg-black/30 border-b border-white/5">
            {dossiers.map((d: any, i: number) => (
              <button
                key={i}
                onClick={() => setActiveDomain(i)}
                className={`px-2.5 py-1 rounded text-[10px] font-mono transition-all ${
                  i === activeDomain
                    ? "bg-amber-500/20 text-amber-200 border border-amber-500/40"
                    : "bg-black/20 text-slate-400 border border-white/5 hover:text-slate-200"
                }`}
              >
                {d.domain}
              </button>
            ))}
          </div>

          {/* Active dossier */}
          <div className="px-5 py-4 bg-black/20">
            {/* Domain stats bar */}
            <div className="flex flex-wrap gap-3 mb-4">
              <div className="px-2 py-1 bg-black/30 rounded border border-white/5 text-[9px] font-mono text-slate-400">
                {active.company_count} companies
              </div>
              {active.archetype_distribution && Object.entries(active.archetype_distribution).map(([k, v]: [string, any]) => (
                <div key={k} className={`px-2 py-1 rounded border text-[9px] font-mono ${
                  k === "Thriving" ? "bg-emerald-500/10 border-emerald-500/30 text-emerald-300" :
                  k === "Modernizing" ? "bg-amber-500/10 border-amber-500/30 text-amber-300" :
                  k === "Cash Cow" ? "bg-blue-500/10 border-blue-500/30 text-blue-300" :
                  "bg-rose-500/10 border-rose-500/30 text-rose-300"
                }`}>
                  {k}: {v}
                </div>
              ))}
              {active.verification && <VerificationBadge verification={active.verification} />}
            </div>

            {/* Forces by timescale */}
            {active.forces_by_timescale && (
              <div className="flex flex-wrap gap-2 mb-4">
                {[
                  { key: "act_now", label: "Act Now", color: "rose" },
                  { key: "prepare", label: "Prepare", color: "amber" },
                  { key: "position", label: "Position", color: "cyan" },
                ].map(({ key, label, color }) => {
                  const forces = active.forces_by_timescale[key] || [];
                  if (forces.length === 0) return null;
                  return (
                    <div key={key} className={`px-2 py-1 bg-${color}-500/10 border border-${color}-500/30 rounded text-[9px] font-mono text-${color}-300`}>
                      {label}: {forces.length} forces
                    </div>
                  );
                })}
              </div>
            )}

            {/* Essay text */}
            <div className="max-h-[500px] overflow-y-auto">
              {formatEssayText(active.text)}
            </div>
          </div>
        </>
      )}
    </div>
  );
};

// ═══════════════════════════════════════════════════════════════════════════════
// FORESIGHT LANDSCAPE CHARTS (from Market Intelligence data)
// ═══════════════════════════════════════════════════════════════════════════════

const ForesightCharts = ({ marketIntelData }: { marketIntelData: any }) => {
  const [isOpen, setIsOpen] = useState(false);
  const foresights = marketIntelData?.top_foresights || [];
  const crossImpact = marketIntelData?.cross_impact || [];

  if (foresights.length === 0 && crossImpact.length === 0) return null;

  // Pareto data
  const totalCount = foresights.reduce((s: number, f: any) => s + f.count, 0) || 1;
  let cumulative = 0;
  const paretoData = foresights.map((f: any) => {
    cumulative += f.count;
    return { ...f, cumPct: Math.round((cumulative / totalCount) * 100) };
  });

  // Cross-impact matrix
  const ciThemes = [...new Set(crossImpact.map((c: any) => c.theme))];
  const ciDimensions = ["Operating Model", "Service Portfolio", "Values", "Strategy", "AI Readiness"];
  const ciMatrix: Record<string, Record<string, number>> = {};
  for (const item of crossImpact) {
    if (!ciMatrix[item.theme]) ciMatrix[item.theme] = {};
    ciMatrix[item.theme][item.dimension] = item.count;
  }
  const maxCICount = Math.max(...crossImpact.map((c: any) => c.count), 1);

  return (
    <div className="border border-slate-700/40 rounded-lg overflow-hidden mb-4">
      <button
        onClick={() => setIsOpen(!isOpen)}
        className="w-full flex items-center justify-between px-4 py-3 bg-slate-800/30 hover:brightness-110 transition-all"
      >
        <div className="flex items-center gap-3">
          <BarChart3 size={18} className="text-slate-400" />
          <span className="font-bold text-sm text-slate-300">Foresight Landscape</span>
        </div>
        <div className="flex items-center gap-2">
          <span className="text-[9px] text-slate-500 font-mono">{foresights.length} themes &middot; {crossImpact.length} cross-impacts</span>
          {isOpen ? <ChevronDown size={16} className="text-slate-400" /> : <ChevronRight size={16} className="text-slate-400" />}
        </div>
      </button>
      <div className="px-5 py-3 bg-black/10 border-b border-white/5">
        <p className="text-[11px] text-slate-400 leading-relaxed">Quantitative foundation for the analysis layers below. The Pareto chart shows which foresight themes appear most frequently across scanned companies. The cross-impact matrix reveals how themes connect to organizational dimensions — where the bars show volume, the matrix shows structural relationships.</p>
      </div>
      {isOpen && (
        <div className="px-5 py-4 bg-black/20 space-y-6">
          {/* Pareto chart */}
          {paretoData.length > 0 && ComposedChart && (
            <div>
              <h4 className="text-[10px] font-bold text-cyan-300 uppercase tracking-wide mb-3">Top {foresights.length} Foresight Themes (Pareto)</h4>
              <ResponsiveContainer width="100%" height={280}>
                <ComposedChart data={paretoData} margin={{ left: 10, right: 30, top: 10, bottom: 60 }}>
                  <CartesianGrid strokeDasharray="3 3" stroke="#1e293b" />
                  <XAxis dataKey="theme" tick={{ fill: '#94a3b8', fontSize: 8 }} angle={-35} textAnchor="end" interval={0} height={80} />
                  <YAxis yAxisId="count" tick={{ fill: '#94a3b8', fontSize: 10 }} />
                  <YAxis yAxisId="pct" orientation="right" tick={{ fill: '#94a3b8', fontSize: 10 }} domain={[0, 100]} unit="%" />
                  <Tooltip contentStyle={{ background: '#0f172a', border: '1px solid #334155', borderRadius: 8, fontSize: 11 }}
                    formatter={(value: any, name: string) => [value, name === 'cumPct' ? 'Cumulative %' : 'Signals']} />
                  <Bar yAxisId="count" dataKey="count" name="Signals" radius={[4, 4, 0, 0]}>
                    {paretoData.map((entry: any, i: number) => (
                      <Cell key={i} fill={OBS_CATEGORY_COLORS[entry.category] || '#6366f1'} />
                    ))}
                  </Bar>
                  <Line yAxisId="pct" dataKey="cumPct" stroke="#f43f5e" strokeWidth={2} dot={{ r: 3 }} name="Cumulative %" />
                </ComposedChart>
              </ResponsiveContainer>
            </div>
          )}

          {/* Cross-Impact Matrix */}
          {ciThemes.length > 0 && (
            <div>
              <h4 className="text-[10px] font-bold text-violet-300 uppercase tracking-wide mb-3">Theme x Dimension Cross-Impact Matrix</h4>
              <div className="overflow-x-auto">
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
                              {val > 0 ? val : '\u2013'}
                            </td>
                          );
                        })}
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            </div>
          )}
        </div>
      )}
    </div>
  );
};

// ═══════════════════════════════════════════════════════════════════════════════
// GENERATION PANEL
// ═══════════════════════════════════════════════════════════════════════════════

const GeneratePanel = ({ onGenerate, generationStatus }: any) => {
  const isRunning = generationStatus?.is_running;
  const current = generationStatus?.current || 0;
  const total = generationStatus?.total || 1;
  const step = generationStatus?.current_step || "";
  const pct = total > 0 ? Math.round((current / total) * 100) : 0;

  return (
    <div className="border border-white/10 rounded-lg p-4 bg-black/20 mb-4">
      <div className="flex items-center justify-between mb-2">
        <div className="flex items-center gap-2">
          <Telescope size={16} className="text-cyan-400" />
          <span className="text-xs font-bold text-slate-200">Observatory Generation</span>
        </div>
        <button
          onClick={onGenerate}
          disabled={isRunning}
          className={`flex items-center gap-1.5 px-3 py-1.5 rounded text-[10px] font-bold font-mono transition-all ${
            isRunning
              ? "bg-slate-700/50 text-slate-500 cursor-not-allowed"
              : "bg-cyan-600/30 hover:bg-cyan-600/50 text-cyan-200 border border-cyan-500/40"
          }`}
        >
          {isRunning ? <Loader2 size={12} className="animate-spin" /> : <RefreshCw size={12} />}
          {isRunning ? "GENERATING..." : "GENERATE OBSERVATORY"}
        </button>
      </div>
      {isRunning && (
        <div className="mt-2">
          <div className="flex items-center justify-between text-[9px] font-mono text-slate-400 mb-1">
            <span>{step}</span>
            <span>{current}/{total} ({pct}%)</span>
          </div>
          <div className="w-full h-1.5 bg-black/40 rounded-full overflow-hidden">
            <div
              className="h-full bg-gradient-to-r from-cyan-500 to-violet-500 rounded-full transition-all duration-500"
              style={{ width: `${pct}%` }}
            />
          </div>
        </div>
      )}
      {generationStatus?.completed_at && !isRunning && (
        <div className="text-[9px] font-mono text-slate-500 mt-1">
          Last generated: {new Date(generationStatus.completed_at).toLocaleString()}
        </div>
      )}
    </div>
  );
};

// ═══════════════════════════════════════════════════════════════════════════════
// MAIN MODAL
// ═══════════════════════════════════════════════════════════════════════════════

const ObservatoryModal = ({ isOpen, data, loading, onClose, onRefresh, adminPassword, marketIntelData = null }: {
  isOpen: boolean;
  data: any;
  loading: boolean;
  onClose: () => void;
  onRefresh: () => void;
  adminPassword: string;
  marketIntelData?: any;
}) => {
  const [generationStatus, setGenerationStatus] = useState<any>(null);
  const pollRef = useRef<any>(null);
  const hasRefreshedRef = useRef<boolean>(false);

  // Poll for generation status — only while generation is running
  useEffect(() => {
    if (!isOpen) return;

    const poll = async () => {
      try {
        const res = await fetch(`/admin/observatory-status?t=${Date.now()}`);
        if (res.ok) {
          const status = await res.json();
          setGenerationStatus(status);

          // If just completed, refresh data ONCE and stop polling
          if (status.completed_at && !status.is_running && status.result && !status.result.error && !hasRefreshedRef.current) {
            hasRefreshedRef.current = true;
            onRefresh();
            // Stop polling — generation is done
            if (pollRef.current) {
              clearInterval(pollRef.current);
              pollRef.current = null;
            }
          }

          // Stop polling if not running and we already have status
          if (!status.is_running && pollRef.current) {
            clearInterval(pollRef.current);
            pollRef.current = null;
          }
        }
      } catch (e) { /* ignore */ }
    };

    // Initial fetch (one-time, no interval yet)
    poll();

    return () => {
      if (pollRef.current) {
        clearInterval(pollRef.current);
        pollRef.current = null;
      }
    };
  }, [isOpen]);

  const handleGenerate = async () => {
    if (!adminPassword) return;
    try {
      const res = await fetch("/admin/generate-observatory", {
        method: "POST",
        headers: { "Authorization": `Bearer ${adminPassword}` },
      });
      if (res.ok) {
        hasRefreshedRef.current = false;  // Reset so we refresh when complete
        setGenerationStatus({ ...generationStatus, is_running: true, current: 0, total: 0, current_step: "Starting..." });
        // Start polling for progress
        if (pollRef.current) clearInterval(pollRef.current);
        const poll = async () => {
          try {
            const r = await fetch(`/admin/observatory-status?t=${Date.now()}`);
            if (r.ok) {
              const status = await r.json();
              setGenerationStatus(status);
              if (status.completed_at && !status.is_running && !hasRefreshedRef.current) {
                hasRefreshedRef.current = true;
                onRefresh();
                if (pollRef.current) { clearInterval(pollRef.current); pollRef.current = null; }
              }
              if (!status.is_running && pollRef.current) {
                clearInterval(pollRef.current); pollRef.current = null;
              }
            }
          } catch (e) { /* ignore */ }
        };
        pollRef.current = setInterval(poll, 3000);
      } else {
        const err = await res.json();
        alert(err.detail || "Failed to start generation");
      }
    } catch (e: any) {
      alert("Error: " + e.message);
    }
  };

  if (!isOpen) return null;

  return (
    <div className="fixed inset-0 z-[60] flex items-start justify-center pt-8 pb-8">
      {/* Backdrop */}
      <div className="absolute inset-0 bg-black/70 backdrop-blur-sm" onClick={onClose} />

      {/* Modal */}
      <div className="relative z-10 w-full max-w-4xl max-h-[90vh] bg-gradient-to-br from-[#1e1b4b] to-[#0f172a] border border-white/10 rounded-xl shadow-2xl overflow-hidden flex flex-col">
        {/* Header */}
        <div className="flex items-center justify-between px-6 py-4 border-b border-white/10 bg-black/20 shrink-0">
          <div className="flex items-center gap-3">
            <div className="w-8 h-8 rounded-lg bg-gradient-to-br from-cyan-500/30 to-violet-500/30 flex items-center justify-center">
              <Telescope size={18} className="text-cyan-300" />
            </div>
            <div>
              <h2 className="text-base font-bold text-slate-100">Strategic Foresight Observatory</h2>
              <p className="text-[10px] text-slate-500 font-mono">Three-layer force analysis</p>
            </div>
          </div>
          <div className="flex items-center gap-3">
            {data?.generated_at && (
              <div className="flex items-center gap-1 text-[9px] text-slate-500 font-mono">
                <Clock size={10} />
                {new Date(data.generated_at).toLocaleDateString()}
              </div>
            )}
            <button onClick={onClose} className="p-1.5 rounded-lg hover:bg-white/5 transition-colors">
              <X size={18} className="text-slate-400" />
            </button>
          </div>
        </div>

        {/* Content */}
        <div className="flex-1 overflow-y-auto px-6 py-4">
          {/* Generate panel (admin) */}
          {adminPassword && (
            <GeneratePanel onGenerate={handleGenerate} generationStatus={generationStatus} />
          )}

          {loading && (
            <div className="flex items-center justify-center py-20">
              <Loader2 size={24} className="animate-spin text-cyan-400" />
              <span className="ml-3 text-sm text-slate-400">Loading observatory data...</span>
            </div>
          )}

          {!loading && !data && (
            <div className="text-center py-20">
              <Telescope size={48} className="mx-auto text-slate-600 mb-4" />
              <p className="text-sm text-slate-400">No observatory data available yet.</p>
              <p className="text-xs text-slate-500 mt-1">Click "Generate Observatory" to create the first analysis.</p>
            </div>
          )}

          {!loading && data && (
            <>
              {/* Summary stats — without model name */}
              <div className="flex flex-wrap gap-3 mb-4">
                <div className="px-3 py-1.5 bg-black/30 rounded border border-white/10 text-[10px] font-mono text-slate-300">
                  {data.company_count} companies
                </div>
                <div className="px-3 py-1.5 bg-black/30 rounded border border-white/10 text-[10px] font-mono text-slate-300">
                  {data.domain_count} domains
                </div>
                {data.global_analysis?.force_count && (
                  <div className="px-3 py-1.5 bg-cyan-500/10 rounded border border-cyan-500/30 text-[10px] font-mono text-cyan-300">
                    {data.global_analysis.force_count} forces tracked
                  </div>
                )}
              </div>

              {/* Foresight Landscape Charts — from Market Intelligence */}
              {marketIntelData && (marketIntelData.top_foresights?.length > 0 || marketIntelData.cross_impact?.length > 0) && (
                <ForesightCharts marketIntelData={marketIntelData} />
              )}

              {/* Layer 1: Global — collapsed by default */}
              <EssaySection
                title="Layer 1: Global Force Landscape"
                icon={Globe}
                colorKey="global"
                text={data.global_analysis?.text}
                verification={data.global_analysis?.verification}
                metadata={`${data.global_analysis?.accelerating_forces || 0} accelerating`}
                description={LAYER_DESCRIPTIONS.global}
                defaultOpen={false}
              />

              {/* Layer 2: Finland — collapsed by default */}
              <EssaySection
                title="Layer 2: Finnish Economy Force Map"
                icon={Map}
                colorKey="finland"
                text={data.finland_analysis?.text}
                verification={data.finland_analysis?.verification}
                metadata={`${data.finland_analysis?.domains_analyzed || 0} domains`}
                description={LAYER_DESCRIPTIONS.finland}
                defaultOpen={false}
              />

              {/* Layer 3: Domain Dossiers — collapsed by default, same pattern */}
              {data.domain_dossiers && data.domain_dossiers.length > 0 && (
                <DomainDossierTabs dossiers={data.domain_dossiers} defaultOpen={false} />
              )}
            </>
          )}
        </div>
      </div>
    </div>
  );
};

(window as any).ObservatoryModal = ObservatoryModal;
