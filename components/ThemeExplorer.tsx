

const { useState, useEffect } = (window as any).React;
const { BookOpen, ChevronDown, ChevronRight, Globe, Layers, ExternalLink, Loader2: LoaderIcon } = (window as any).Lucide;

const CATEGORY_ICONS = {
  "Technology & Digital": "cpu",
  "Economy & Business": "trending-up",
  "Industry & Infrastructure": "hard-drive",
  "Environment & Resources": "cloud",
  "Society & Governance": "shield",
  "People & Well-being": "heart",
  "Science & Demographics": "atom",
};

const CATEGORY_COLORS = {
  "Technology & Digital": { bg: "from-blue-900/40 to-cyan-900/30", border: "border-blue-500/40", badge: "bg-blue-500/20 text-blue-300" },
  "Economy & Business": { bg: "from-amber-900/40 to-yellow-900/30", border: "border-amber-500/40", badge: "bg-amber-500/20 text-amber-300" },
  "Industry & Infrastructure": { bg: "from-slate-800/40 to-zinc-900/30", border: "border-slate-500/40", badge: "bg-slate-500/20 text-slate-300" },
  "Environment & Resources": { bg: "from-emerald-900/40 to-green-900/30", border: "border-emerald-500/40", badge: "bg-emerald-500/20 text-emerald-300" },
  "Society & Governance": { bg: "from-purple-900/40 to-violet-900/30", border: "border-purple-500/40", badge: "bg-purple-500/20 text-purple-300" },
  "People & Well-being": { bg: "from-rose-900/40 to-pink-900/30", border: "border-rose-500/40", badge: "bg-rose-500/20 text-rose-300" },
  "Science & Demographics": { bg: "from-teal-900/40 to-cyan-900/30", border: "border-teal-500/40", badge: "bg-teal-500/20 text-teal-300" },
};

const ThemeExplorer = ({ isOpen, onClose, authToken = '' }) => {
  const [data, setData] = useState(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState(null);
  const [expandedCategories, setExpandedCategories] = useState<Record<string, boolean>>({});
  const [expandedThemes, setExpandedThemes] = useState<Record<string, boolean>>({});

  useEffect(() => {
    if (isOpen && !data && authToken) {
      setLoading(true);
      setError(null);
      fetch('/api/fp-themes', { headers: { 'Authorization': `Bearer ${authToken}` } })
        .then(r => { if (r.status === 401) throw new Error('Invalid password'); if (!r.ok) throw new Error('Failed to load themes'); return r.json(); })
        .then(d => { setData(d); setLoading(false); })
        .catch(e => { setError(e.message); setLoading(false); });
    }
    if (!isOpen) { setData(null); setError(null); }
  }, [isOpen, authToken]);

  if (!isOpen) return null;

  const toggleCategory = (cat: string) => {
    setExpandedCategories(prev => ({ ...prev, [cat]: !prev[cat] }));
  };

  const toggleTheme = (theme: string) => {
    setExpandedThemes(prev => ({ ...prev, [theme]: !prev[theme] }));
  };

  const categories = data?.categories || [];
  const themes = data?.themes || [];

  const totalSources = themes.reduce((sum, t) => sum + (t.source_count || 0), 0);

  return (
    <div className="fixed inset-0 bg-black/70 backdrop-blur-sm z-50 flex items-center justify-center p-4" onClick={onClose}>
      <div className="bg-[#0a0f1a] border border-slate-700/50 rounded-2xl shadow-2xl w-full max-w-4xl max-h-[85vh] overflow-hidden flex flex-col" onClick={e => e.stopPropagation()}>

        {/* Header */}
        <div className="px-6 py-4 border-b border-slate-700/50 bg-gradient-to-r from-[#0a0f1a] to-slate-900/50 flex items-center justify-between flex-shrink-0">
          <div className="flex items-center gap-3">
            <div className="p-2 bg-teal-500/10 rounded-lg">
              <BookOpen className="w-5 h-5 text-teal-400" />
            </div>
            <div>
              <h2 className="text-lg font-semibold text-white">Strategic Foresight Themes</h2>
              <p className="text-xs text-slate-400 mt-0.5">
                {loading ? 'Loading...' : `${themes.length} themes · ${totalSources} curated sources · 7 categories`}
              </p>
            </div>
          </div>
          <button onClick={onClose} className="text-slate-400 hover:text-white transition-colors p-1">
            <span className="text-xl leading-none">&times;</span>
          </button>
        </div>

        {/* Content */}
        <div className="overflow-y-auto flex-1 p-4 space-y-3">
          {loading && (
            <div className="flex items-center justify-center py-16">
              <LoaderIcon className="w-6 h-6 text-teal-400 animate-spin" />
              <span className="ml-3 text-slate-400 text-sm">Loading theme data...</span>
            </div>
          )}

          {error && (
            <div className="text-center py-16 text-red-400 text-sm">{error}</div>
          )}

          {!loading && !error && categories.map((cat: string) => {
            const catThemes = themes.filter(t => t.category === cat);
            const isExpanded = expandedCategories[cat];
            const colors = CATEGORY_COLORS[cat] || CATEGORY_COLORS["Technology & Digital"];
            const catSources = catThemes.reduce((sum, t) => sum + t.source_count, 0);

            return (
              <div key={cat} className={`rounded-xl border ${colors.border} overflow-hidden`}>
                {/* Category Header */}
                <button
                  onClick={() => toggleCategory(cat)}
                  className={`w-full px-4 py-3 flex items-center justify-between bg-gradient-to-r ${colors.bg} hover:brightness-110 transition-all`}
                >
                  <div className="flex items-center gap-3">
                    {isExpanded
                      ? <ChevronDown className="w-4 h-4 text-slate-400" />
                      : <ChevronRight className="w-4 h-4 text-slate-400" />
                    }
                    <Layers className="w-4 h-4 text-slate-300" />
                    <span className="text-sm font-semibold text-white">{cat}</span>
                  </div>
                  <div className="flex items-center gap-2">
                    <span className={`text-[10px] px-2 py-0.5 rounded-full font-mono ${colors.badge}`}>
                      {catThemes.length} themes
                    </span>
                    <span className="text-[10px] px-2 py-0.5 rounded-full bg-slate-700/50 text-slate-300 font-mono">
                      {catSources} sources
                    </span>
                  </div>
                </button>

                {/* Themes within category */}
                {isExpanded && (
                  <div className="border-t border-slate-700/30">
                    {catThemes.map((theme, ti) => {
                      const isThemeExpanded = expandedThemes[theme.name];
                      return (
                        <div key={theme.name} className={`${ti > 0 ? 'border-t border-slate-800/50' : ''}`}>
                          {/* Theme row */}
                          <button
                            onClick={() => toggleTheme(theme.name)}
                            className="w-full px-5 py-2.5 flex items-center justify-between hover:bg-slate-800/30 transition-colors"
                          >
                            <div className="flex items-center gap-2 min-w-0">
                              {isThemeExpanded
                                ? <ChevronDown className="w-3.5 h-3.5 text-slate-500 flex-shrink-0" />
                                : <ChevronRight className="w-3.5 h-3.5 text-slate-500 flex-shrink-0" />
                              }
                              <span className="text-sm text-slate-200 font-medium truncate">{theme.name}</span>
                            </div>
                            <span className="text-[10px] text-slate-500 font-mono flex-shrink-0 ml-2">
                              {theme.source_count} sources
                            </span>
                          </button>

                          {/* Expanded theme details */}
                          {isThemeExpanded && (
                            <div className="px-5 pb-3 ml-6">
                              {/* Description */}
                              <p className="text-xs text-slate-400 leading-relaxed mb-3 border-l-2 border-teal-500/30 pl-3">
                                {theme.description}
                              </p>

                              {/* Source domains grid */}
                              <div className="flex flex-wrap gap-1.5">
                                {(theme.source_domains || []).map((domain: string, di: number) => (
                                  <span
                                    key={di}
                                    className="inline-flex items-center gap-1 text-[10px] px-2 py-1 rounded-md bg-slate-800/60 text-slate-300 border border-slate-700/40 font-mono hover:bg-slate-700/50 transition-colors"
                                  >
                                    <Globe className="w-2.5 h-2.5 text-slate-500" />
                                    {domain}
                                  </span>
                                ))}
                              </div>
                            </div>
                          )}
                        </div>
                      );
                    })}
                  </div>
                )}
              </div>
            );
          })}
        </div>

        {/* Footer */}
        <div className="px-6 py-3 border-t border-slate-700/50 bg-slate-900/30 flex-shrink-0 overflow-y-auto max-h-[40vh]">
          <p className="text-[10px] text-slate-500 text-center mb-3">
            Sources curated using 4-tier academic quality framework · Prioritises intergovernmental, academic, and independent research institutions
          </p>
          <div className="text-[10px] text-slate-400 space-y-2 px-2">
            <h4 className="text-[11px] font-semibold text-slate-300">1.3 Source Quality Framework</h4>
            <p className="text-slate-500">The following quality tiers were applied in evaluating and adding sources:</p>
            <div>
              <p className="font-semibold text-slate-300">TIER 1 — Highest Authority:</p>
              <p className="text-slate-500">Intergovernmental bodies and UN agencies (IPCC, WHO, IEA, IRENA, IMF, World Bank, OECD, UN agencies); peer-reviewed flagship journals (Nature, Science, Lancet); national academies (Royal Society, AAAS).</p>
            </div>
            <div>
              <p className="font-semibold text-slate-300">TIER 2 — High Authority:</p>
              <p className="text-slate-500">Leading independent think tanks (Chatham House, RAND, Brookings, Carnegie, CSIS, IISS, SIPRI); major academic institutions (Oxford, Cambridge, MIT, Harvard, Stanford, LSE); European institutions (JRC, EEA, ECFR, Bruegel).</p>
            </div>
            <div>
              <p className="font-semibold text-slate-300">TIER 3 — Solid Specialist:</p>
              <p className="text-slate-500">Specialist research bodies (ILO, FAO, UNEP, ITU, WTO, UNCTAD); futures organisations (IFTF, CIFS, SOIF, Hawaii Research Center); well-established NGOs with research credibility (Ellen MacArthur Foundation, WRI, IUCN, Fairwork).</p>
            </div>
            <div>
              <p className="font-semibold text-slate-300">TIER 4 — Supplementary (use selectively):</p>
              <p className="text-slate-500">Commercial analysts (McKinsey, Deloitte, Gartner, Forrester, BCG); government-funded bodies (Dubai Future Foundation); professional associations (WEF). These sources are retained where specifically authoritative but are not the primary bases for analysis.</p>
            </div>
          </div>
        </div>
      </div>
    </div>
  );
};

(window as any).ThemeExplorer = ThemeExplorer;
