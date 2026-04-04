
const { useState, useEffect, useRef } = (window as any).React;

const LucideRaw = (window as any).Lucide || {};
const ObsIcons = new Proxy(LucideRaw, {
  get: (target: any, prop: string) => {
    if (prop in target) return target[prop];
    return () => null;
  }
});
const { X, Loader2, Globe, Map, FileText, ChevronDown, ChevronRight, RefreshCw, Eye, Clock, CheckCircle, AlertTriangle, Telescope } = ObsIcons;

// ═══════════════════════════════════════════════════════════════════════════════
// HELPERS
// ═══════════════════════════════════════════════════════════════════════════════

const SECTION_COLORS = {
  global: { border: "border-cyan-500/30", bg: "bg-cyan-500/10", text: "text-cyan-300", icon: "text-cyan-400" },
  finland: { border: "border-violet-500/30", bg: "bg-violet-500/10", text: "text-violet-300", icon: "text-violet-400" },
  domain: { border: "border-amber-500/30", bg: "bg-amber-500/10", text: "text-amber-300", icon: "text-amber-400" },
};

const formatEssayText = (text: string) => {
  if (!text) return null;
  const parts = text.split(/\n+/);
  return parts.map((part: string, i: number) => {
    const trimmed = part.trim();
    if (!trimmed) return null;
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

const EssaySection = ({ title, icon: Icon, colorKey, text, verification, metadata, defaultOpen = false }: any) => {
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
      {isOpen && (
        <div className="px-5 py-4 bg-black/20 max-h-[600px] overflow-y-auto">
          {formatEssayText(text)}
        </div>
      )}
    </div>
  );
};

const DomainDossierTabs = ({ dossiers }: { dossiers: any[] }) => {
  const [activeDomain, setActiveDomain] = useState(0);
  if (!dossiers || dossiers.length === 0) return null;

  const active = dossiers[activeDomain];

  return (
    <div className="border border-amber-500/30 rounded-lg overflow-hidden">
      {/* Header */}
      <div className="px-4 py-3 bg-amber-500/10 flex items-center gap-3">
        <FileText size={18} className="text-amber-400" />
        <span className="font-bold text-sm text-amber-300">Domain Force Dossiers</span>
        <span className="text-[9px] text-slate-500 font-mono ml-auto">{dossiers.length} domains</span>
      </div>

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

const ObservatoryModal = ({ isOpen, data, loading, onClose, onRefresh, adminPassword }: {
  isOpen: boolean;
  data: any;
  loading: boolean;
  onClose: () => void;
  onRefresh: () => void;
  adminPassword: string;
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
              {/* Summary stats */}
              <div className="flex flex-wrap gap-3 mb-4">
                <div className="px-3 py-1.5 bg-black/30 rounded border border-white/10 text-[10px] font-mono text-slate-300">
                  {data.company_count} companies
                </div>
                <div className="px-3 py-1.5 bg-black/30 rounded border border-white/10 text-[10px] font-mono text-slate-300">
                  {data.domain_count} domains
                </div>
                <div className="px-3 py-1.5 bg-black/30 rounded border border-white/10 text-[10px] font-mono text-slate-300">
                  Model: {data.model}
                </div>
                {data.global_analysis?.force_count && (
                  <div className="px-3 py-1.5 bg-cyan-500/10 rounded border border-cyan-500/30 text-[10px] font-mono text-cyan-300">
                    {data.global_analysis.force_count} forces tracked
                  </div>
                )}
              </div>

              {/* Layer 1: Global */}
              <EssaySection
                title="Layer 1: Global Force Landscape"
                icon={Globe}
                colorKey="global"
                text={data.global_analysis?.text}
                verification={data.global_analysis?.verification}
                metadata={`${data.global_analysis?.accelerating_forces || 0} accelerating`}
                defaultOpen={true}
              />

              {/* Layer 2: Finland */}
              <EssaySection
                title="Layer 2: Finnish Economy Force Map"
                icon={Map}
                colorKey="finland"
                text={data.finland_analysis?.text}
                verification={data.finland_analysis?.verification}
                metadata={`${data.finland_analysis?.domains_analyzed || 0} domains`}
              />

              {/* Layer 3: Domain Dossiers */}
              {data.domain_dossiers && data.domain_dossiers.length > 0 && (
                <DomainDossierTabs dossiers={data.domain_dossiers} />
              )}
            </>
          )}
        </div>
      </div>
    </div>
  );
};

(window as any).ObservatoryModal = ObservatoryModal;
