


const { useState, useEffect, useRef, useMemo } = (window as any).React;

const LucideRaw = (window as any).Lucide || {};
const Icons = new Proxy(LucideRaw, {
    get: (target, prop) => {
        if (prop in target) return target[prop];
        return () => null;
    }
});

const { Activity, Search, ArrowUpDown, Plus, Loader2, X, Sparkles, Shield, Database, RefreshCw, CheckCircle2, AlertTriangle, Cloud, RotateCcw, Lock, HardDrive, Terminal, Radio, Play, Pause, StopCircle, TestTube, Cpu, BarChart3, TrendingUp, TrendingDown, Minus } = Icons;

const TruthMatrix = (window as any).TruthMatrix;
const ScannerInput = (window as any).ScannerInput;
const CompanyModal = (window as any).CompanyModal;

const IS_ADMIN_MODE = true; 

// DYNAMIC REFERENCE CONFIG
// These names will be highlighted as References in the UI, 
// but their Data (Scores/Summary) will come from the Backend (Live Scan).
const REFERENCE_NAMES = [
    "Tesla",
    "Buurtzorg",
    "Supercell",
    "DNA",
    "Neste",
    "Kone",
    "Rebase Consulting",
    "Oura"
];

const App = () => {
  const [data, setData] = useState([]); // Lightweight Summary Data
  const [loading, setLoading] = useState(true);
  const [connectionError, setConnectionError] = useState(null); 
  const [searchQuery, setSearchQuery] = useState('');
  const [selectedCompany, setSelectedCompany] = useState(null); // Full Detailed Data
  const [isLoadingDetails, setIsLoadingDetails] = useState(false);
  const [isModalOpen, setIsModalOpen] = useState(false);
  const [isScannerOpen, setIsScannerOpen] = useState(false);
  const [isScanning, setIsScanning] = useState(false);
  const [lastSynced, setLastSynced] = useState(null);
  const [sortConfig, setSortConfig] = useState({ key: 'reality_score', direction: 'desc' });
  const [systemStatus, setSystemStatus] = useState({ online: false, storage: false, google: false, ai: false });
  const [selectedDomain, setSelectedDomain] = useState('All');
  
  // ADMIN SETTINGS
  const [adminModel, setAdminModel] = useState('');

  // ANALYTICS STATE
  const [isAnalyticsOpen, setIsAnalyticsOpen] = useState(false);
  const [analyticsData, setAnalyticsData] = useState(null);
  const [analyticsLoading, setAnalyticsLoading] = useState(false);

  // BATCH STATE
  const [isBatchOpen, setIsBatchOpen] = useState(false);
  const [batchStatus, setBatchStatus] = useState({ current: 0, total: 0, isRunning: false, currentName: '', logs: [] });
  const stopBatchRef = useRef(false);
  const adminTokenRef = useRef('');

  const prevDataLengthRef = useRef(0);

  const checkHealth = async () => {
      try {
          const res = await fetch('/api/health');
          if (res.ok) {
              const json = await res.json();
              setSystemStatus({
                  online: true,
                  storage: json.storage_connected || false,
                  google: true, // Assumed if online
                  ai: true
              });
              return json.storage_connected;
          }
      } catch (e) {
          console.warn("Backend health check failed", e);
          setSystemStatus(prev => ({ ...prev, online: false }));
      }
      return false;
  };

  useEffect(() => {
    checkHealth();
  }, []);

  const fetchSummaryData = async (silent = false) => {
    try {
        if (!silent) setLoading(true);
        setConnectionError(null);
        const response = await fetch(`/dashboard-data?t=${Date.now()}`);
        if (response.ok) {
            const json = await response.json();
            const newData = json || [];
            prevDataLengthRef.current = newData.length;
            
            // PROCESS REFERENCES: Flag them if they exist in the live data
            const processed = newData.map(item => {
                const isRef = REFERENCE_NAMES.some(refName => 
                    item.companyName.toLowerCase().includes(refName.toLowerCase())
                );
                return isRef ? { ...item, isReference: true } : item;
            });

            setData(processed);
            setLastSynced(new Date().toLocaleTimeString('en-US', { hour12: false, hour: "2-digit", minute: "2-digit", second: "2-digit" }));
        } else {
            throw new Error("Failed to fetch summary data");
        }
    } catch (e) {
        console.error(e);
        if (!silent) setData([]); 
    } finally {
        if (!silent) setLoading(false);
    }
  };

  useEffect(() => {
    fetchSummaryData();
  }, []);

  useEffect(() => {
    const handleBeforeUnload = (e) => {
        if (batchStatus.isRunning) {
            e.preventDefault();
            e.returnValue = "Diagnosis in progress. Leaving will stop the scanner.";
            return e.returnValue;
        }
    };
    window.addEventListener('beforeunload', handleBeforeUnload);
    return () => window.removeEventListener('beforeunload', handleBeforeUnload);
  }, [batchStatus.isRunning]);


  const handleCompanyClick = async (summaryItem) => {
    if (!summaryItem) return;
    
    setIsLoadingDetails(true);
    setSelectedCompany({ ...summaryItem, partial: true });
    setIsModalOpen(true);

    try {
        let fullData = null;
        if (summaryItem.detailsUrl) {
            const res = await fetch(`${summaryItem.detailsUrl}?t=${Date.now()}`);
            if (res.ok) fullData = await res.json();
        }
        
        if (!fullData) {
            const res = await fetch(`/company/${encodeURIComponent(summaryItem.companyName)}?t=${Date.now()}`);
            if (res.ok) fullData = await res.json();
        }

        if (fullData) {
            // Apply Reference Flag to details as well to keep UI consistent (e.g. if we add badges later)
            const isRef = REFERENCE_NAMES.some(refName => 
                fullData.companyName.toLowerCase().includes(refName.toLowerCase())
            );
            setSelectedCompany({ ...fullData, isReference: isRef });
        } else {
            setSelectedCompany(prev => ({ ...prev, partial: false, summary_text: "Detailed forensic report unavailable. Displaying summary index data." }));
        }
    } catch (e) {
        console.error("Error fetching details", e);
        setSelectedCompany(prev => ({ ...prev, partial: false }));
    } finally {
        setIsLoadingDetails(false);
    }
  };

  const handleRunScan = async (input, isFormData = false) => {
    setIsScanning(true);
    try {
      let body;
      let headers = {};
      
      if (isFormData) {
          body = input;
      } else {
          body = JSON.stringify({ ...input, saveToDatabase: true });
          headers = { 'Content-Type': 'application/json' };
      }

      const response = await fetch(`/scan`, { method: 'POST', headers: headers, body: body });
      if (!response.ok) throw new Error("Scan failed");
      const json = await response.json();
      const newResult = json.data;

      setIsScannerOpen(false);
      setSelectedCompany(newResult); 
      setIsModalOpen(true);
      fetchSummaryData(true); 
      
    } catch (error) {
      console.error(error);
      alert("Error running scan.");
    } finally {
      setIsScanning(false);
    }
  };

  // --- BATCH LOGIC (PARALLEL — 5 concurrent scans) ---
  const executeBatch = async (targets, mode = "FULL") => {
      setIsBatchOpen(true);
      stopBatchRef.current = false;

      let modelLabel = "Default (Gemini 3 Pro + Opus 4.6)";
      if (adminModel === 'gemini-only') modelLabel = 'Gemini 3 Pro Only';
      if (adminModel === 'sonnet-opus') modelLabel = 'Sonnet 4.5 + Opus 4.6';
      if (adminModel === 'gemini-hybrid') modelLabel = 'Gemini 3 Pro + Gemini 2.5 Pro';

      const MAX_PARALLEL = 5;
      setBatchStatus({ current: 0, total: 0, isRunning: true, currentName: 'Initializing...', logs: [`Starting ${mode} Batch using ${modelLabel} (${MAX_PARALLEL} parallel)...`] });

      try {
        setBatchStatus(prev => ({ ...prev, total: targets.length, logs: [...prev.logs, `Loaded ${targets.length} targets.`] }));

        let completed = 0;
        let activeNames: string[] = [];

        const scanOne = async (target) => {
            if (stopBatchRef.current) return;

            activeNames = [...activeNames, target.name];
            setBatchStatus(prev => ({ ...prev, currentName: activeNames.join(', '), logs: [...prev.logs, `🔄 Starting: ${target.name}...`] }));

            try {
                const formData = new FormData();
                formData.append('companyName', target.name);
                formData.append('companyUrl', target.url);
                formData.append('reportText', "");
                formData.append('saveToDatabase', 'true');
                if (adminModel) formData.append('modelId', adminModel);

                const res = await fetch('/scan', {
                    method: 'POST',
                    body: formData,
                    headers: { 'Authorization': `Bearer ${adminTokenRef.current}` }
                });
                completed++;
                activeNames = activeNames.filter(n => n !== target.name);

                if (res.ok) {
                    setBatchStatus(prev => ({ ...prev, current: completed, currentName: activeNames.join(', ') || 'waiting...', logs: [...prev.logs, `✅ Success: ${target.name} (${completed}/${targets.length})`] }));
                } else {
                    setBatchStatus(prev => ({ ...prev, current: completed, currentName: activeNames.join(', ') || 'waiting...', logs: [...prev.logs, `❌ Failed: ${target.name} (${completed}/${targets.length})`] }));
                }
            } catch (err) {
                completed++;
                activeNames = activeNames.filter(n => n !== target.name);
                setBatchStatus(prev => ({ ...prev, current: completed, currentName: activeNames.join(', ') || 'waiting...', logs: [...prev.logs, `⚠️ Network Error: ${target.name} (${completed}/${targets.length})`] }));
            }
        };

        // Parallel execution with concurrency limit
        const queue = [...targets];
        const running: Promise<void>[] = [];

        while (queue.length > 0 || running.length > 0) {
            if (stopBatchRef.current) {
                setBatchStatus(prev => ({ ...prev, isRunning: false, logs: [...prev.logs, '⛔ Batch Stopped by User.'] }));
                break;
            }

            // Fill up to MAX_PARALLEL
            while (running.length < MAX_PARALLEL && queue.length > 0) {
                const target = queue.shift()!;
                const promise = scanOne(target).then(() => {
                    running.splice(running.indexOf(promise), 1);
                });
                running.push(promise);
            }

            // Wait for at least one to finish before continuing
            if (running.length > 0) {
                await Promise.race(running);
            }
        }

        fetchSummaryData(true);
        setBatchStatus(prev => ({ ...prev, isRunning: false, currentName: 'Complete', logs: [...prev.logs, `✨ BATCH COMPLETE: ${completed}/${targets.length} companies processed.`] }));

    } catch (e) {
        alert("Error starting batch.");
        console.error(e);
        setBatchStatus(prev => ({ ...prev, isRunning: false }));
    }
  };

  const handleStartBatch = async () => {
    if (!IS_ADMIN_MODE) return;
    const password = prompt("AUTHENTICATION REQUIRED\n\nEnter Admin Password to initiate FULL Batch Scan:");
    if (!password) return;

    const modelLabel = adminModel === '' ? 'Default (Gemini 3 Pro + Opus 4.6)' : getAdminModelLabel();
    if (!confirm(`⚠️ START FULL BATCH SCAN (100+ Companies)?\n\nModel: ${modelLabel}\n\nThis will take a significant amount of time.\n\nDO NOT CLOSE THIS TAB until finished.`)) return;

    try {
        const listRes = await fetch('/admin/company-list', {
            headers: { 'Authorization': `Bearer ${password}` }
        });
        if (listRes.status === 401) { alert("ACCESS DENIED: Incorrect Password."); return; }
        if (!listRes.ok) throw new Error("Failed to fetch company list");
        adminTokenRef.current = password;
        const listJson = await listRes.json();
        executeBatch(listJson.companies || [], "FULL");
    } catch(e) { alert("Failed to fetch company list"); }
  };
  
  const handleTestBatch = async () => {
    if (!IS_ADMIN_MODE) return;
    const password = prompt("AUTHENTICATION REQUIRED\n\nEnter Admin Password for TEST SCAN (5 Random):");
    if (!password) return;

    try {
        const listRes = await fetch('/admin/company-list', {
            headers: { 'Authorization': `Bearer ${password}` }
        });
        if (listRes.status === 401) { alert("ACCESS DENIED: Incorrect Password."); return; }
        if (!listRes.ok) throw new Error("Failed to fetch company list");
        adminTokenRef.current = password;
        const listJson = await listRes.json();
        const allCompanies = listJson.companies || [];
        const shuffled = [...allCompanies].sort(() => 0.5 - Math.random());
        executeBatch(shuffled.slice(0, 5), "TEST (5)");
    } catch(e) { alert("Failed to fetch company list"); }
  };

  const cycleAdminModel = () => {
      const models = ['', 'gemini-only', 'sonnet-opus', 'gemini-hybrid'];
      const idx = models.indexOf(adminModel);
      setAdminModel(models[(idx + 1) % models.length]);
  };

  const getAdminModelLabel = () => {
      if (adminModel === '') return 'GEMINI 3 PRO + OPUS 4.6';
      if (adminModel === 'gemini-only') return 'GEMINI 3 PRO ONLY';
      if (adminModel === 'sonnet-opus') return 'SONNET 4.5 + OPUS 4.6';
      if (adminModel === 'gemini-hybrid') return 'GEMINI 3 PRO + 2.5 PRO';
      return adminModel;
  };

  const handleStopBatch = () => { stopBatchRef.current = true; };

  const handleOpenAnalytics = async () => {
    setIsAnalyticsOpen(true);
    if (analyticsData) return; // already loaded
    setAnalyticsLoading(true);
    try {
      const res = await fetch(`/api/analyze?t=${Date.now()}`);
      if (res.ok) {
        const result = await res.json();
        setAnalyticsData(result);
      }
    } catch (e) {
      console.error('Analytics fetch failed:', e);
    } finally {
      setAnalyticsLoading(false);
    }
  };

  const handleSort = (key) => {
    let direction = 'desc';
    if (sortConfig && sortConfig.key === key && sortConfig.direction === 'desc') direction = 'asc';
    setSortConfig({ key, direction });
  };

  // Revenue tier helper
  const getRevenueTier = (revenue) => {
    if (!revenue) return { label: '-', size: 'S' };
    if (revenue >= 2000) return { label: 'XL', size: 'XL' };
    if (revenue >= 500) return { label: 'L', size: 'L' };
    if (revenue >= 100) return { label: 'M', size: 'M' };
    return { label: 'S', size: 'S' };
  };

  // Collect unique domains from data
  const availableDomains = useMemo(() => {
    const domains = new Set();
    data.forEach(item => { if (item.domain) domains.add(item.domain); });
    return ['All', ...Array.from(domains).sort()];
  }, [data]);

  // Domain-level stats for gauge cards
  const domainStats = useMemo(() => {
    const items = selectedDomain === 'All' ? data : data.filter(d => d.domain === selectedDomain);
    if (items.length === 0) return null;
    const avgIntent = Math.round(items.reduce((s, i) => s + (i.intent_score || 0), 0) / items.length);
    const avgReality = Math.round(items.reduce((s, i) => s + (i.reality_score || 0), 0) / items.length);
    const financialItems = items.filter(i => i.financial_index != null);
    const avgFinancial = financialItems.length > 0 ? Math.round(financialItems.reduce((s, i) => s + i.financial_index, 0) / financialItems.length) : null;
    let archetypes = { 'Thriving': 0, 'Modernizing': 0, 'Cash Cow': 0, 'At Risk': 0 };
    items.forEach(i => {
      const r = i.reality_score || 0;
      const f = i.financial_index ?? i.intent_score ?? 0;
      if (r >= 50) { f >= 50 ? archetypes['Thriving']++ : archetypes['Modernizing']++; }
      else { f >= 50 ? archetypes['Cash Cow']++ : archetypes['At Risk']++; }
    });
    return { count: items.length, avgIntent, avgReality, avgFinancial, archetypes };
  }, [data, selectedDomain]);

  const filteredData = data.filter(item => {
    const matchesSearch = item.companyName.toLowerCase().includes(searchQuery.toLowerCase());
    const matchesDomain = selectedDomain === 'All' || item.domain === selectedDomain;
    return matchesSearch && matchesDomain;
  });

  const getArchetype = (reality: number, financial: number) => {
      if (reality >= 50) {
          return financial >= 50
            ? { label: "Thriving", color: "text-emerald-400", bg: "bg-emerald-400/10", border: "border-emerald-400/20" }
            : { label: "Modernizing", color: "text-cyan-400", bg: "bg-cyan-400/10", border: "border-cyan-400/20" };
      } else {
          return financial >= 50
            ? { label: "Cash Cow", color: "text-amber-400", bg: "bg-amber-400/10", border: "border-amber-400/20" }
            : { label: "At Risk", color: "text-rose-400", bg: "bg-rose-400/10", border: "border-rose-400/20" };
      }
  };

  const sortedData = [...filteredData].sort((a, b) => {
    if (!sortConfig) return 0;
    if (sortConfig.key === 'archetype') {
         const archA = getArchetype(a.reality_score, a.financial_index ?? a.intent_score ?? 0).label;
         const archB = getArchetype(b.reality_score, b.financial_index ?? b.intent_score ?? 0).label;
         return sortConfig.direction === 'asc' ? archA.localeCompare(archB) : archB.localeCompare(archA);
    }
    const aVal = a[sortConfig.key];
    const bVal = b[sortConfig.key];
    const aNull = aVal == null;
    const bNull = bVal == null;
    if (aNull && bNull) return 0;
    if (aNull) return 1;
    if (bNull) return -1;
    if (typeof aVal === 'string' && typeof bVal === 'string') return sortConfig.direction === 'asc' ? aVal.localeCompare(bVal) : bVal.localeCompare(aVal);
    if (typeof aVal === 'number' && typeof bVal === 'number') return sortConfig.direction === 'asc' ? aVal - bVal : bVal - aVal;
    return 0;
  });

  return (
    <div className="min-h-screen flex flex-col font-sans relative overflow-x-hidden text-slate-100">
      <div className="fixed inset-0 z-0 bg-[#1e1b4b] overflow-hidden">
        <div className="absolute inset-0 bg-gradient-to-b from-[#2e1065] via-[#1e1b4b] to-[#0f172a] opacity-80"></div>
        <div className="absolute inset-0 opacity-[0.05] pointer-events-none"></div>
      </div>

      <header className="sticky top-0 z-40 w-full border-b border-white/10 bg-[#1e1b4b]/50 backdrop-blur-xl">
        <div className="max-w-7xl mx-auto px-4 sm:px-6 lg:px-8 h-16 flex items-center justify-between">
          <div className="flex items-center gap-3">
            <div className="w-8 h-8 bg-gradient-to-br from-cyan-500 to-violet-600 rounded-lg flex items-center justify-center">
              <span className="text-white font-bold text-sm">FI</span>
            </div>
            <div className="flex flex-col">
              <h1 className="font-bold text-base text-white hidden sm:block">
                Adaptability<span className="text-transparent bg-clip-text bg-gradient-to-r from-cyan-300 to-violet-300">Scanner</span>
              </h1>
              <span className="text-[10px] text-indigo-200 tracking-[0.15em] uppercase font-medium">Finland Top 100 Index</span>
            </div>
          </div>
          
          <div className="flex items-center gap-4 w-full max-w-md ml-4 justify-end">
            <div className="flex items-center gap-3 mr-2">
                 <div className="hidden sm:flex items-center gap-1.5">
                     <div className={`w-1.5 h-1.5 rounded-full ${systemStatus.storage ? 'bg-cyan-400' : 'bg-rose-500'}`}></div>
                     <span className="text-[9px] font-mono uppercase text-slate-500">
                        Storage
                     </span>
                 </div>
            </div>
            {IS_ADMIN_MODE && (
                <>
                <button 
                  onClick={cycleAdminModel}
                  className={`hidden md:flex items-center gap-1.5 px-3 py-1.5 border rounded transition-all text-[10px] font-mono font-bold tracking-tight ${adminModel === '' ? 'bg-emerald-500/20 text-emerald-300 border-emerald-500/40 shadow-[0_0_10px_rgba(16,185,129,0.2)]' : 'bg-amber-500/10 text-amber-300 border-amber-500/30'}`}
                >
                    {adminModel === '' ? <Sparkles size={12} className="text-emerald-300" /> : <Cpu size={12} className="text-amber-400" />}
                    <span>{getAdminModelLabel()}</span>
                </button>

                <button onClick={handleOpenAnalytics} className="hidden md:flex items-center gap-2 px-3 py-1.5 bg-violet-900/40 hover:bg-violet-800/60 border border-violet-700/50 text-violet-200 font-mono font-bold text-[10px] rounded transition-all">
                    <BarChart3 size={12} /> ANALYZE
                </button>
                <button onClick={handleTestBatch} className="hidden md:flex items-center gap-2 px-3 py-1.5 bg-cyan-900/40 hover:bg-cyan-800/60 border border-cyan-700/50 text-cyan-200 font-mono font-bold text-[10px] rounded transition-all">
                    <TestTube size={12} /> TEST(5)
                </button>
                <button onClick={handleStartBatch} className="flex items-center gap-2 px-4 py-2 bg-indigo-600 hover:bg-indigo-500 text-white font-semibold text-xs rounded-lg transition-all">
                    {batchStatus.isRunning ? <Loader2 size={14} className="animate-spin" /> : <Database size={14} />}
                    {batchStatus.isRunning ? "SCANNING..." : "REFRESH DB"}
                </button>
                </>
            )}
            <div className="flex items-center gap-2">
                {lastSynced && <span className="text-[10px] text-cyan-300/70 font-mono hidden sm:block">{lastSynced}</span>}
                <button onClick={() => fetchSummaryData(false)} className="p-2 bg-white/5 rounded-full hover:bg-white/10">
                    <RefreshCw size={14} className={loading ? "animate-spin" : ""} />
                </button>
            </div>
            <div className="relative w-full max-w-[240px] hidden sm:block">
              <input type="text" placeholder="Search..." value={searchQuery} onChange={(e) => setSearchQuery(e.target.value)} className="w-full pl-4 pr-4 py-1.5 bg-[#0f172a]/50 border border-indigo-500/30 rounded-lg text-sm text-slate-100" />
            </div>
            {IS_ADMIN_MODE && (
              <button onClick={() => setIsScannerOpen(true)} className="flex items-center gap-2 px-4 py-2 bg-white/10 rounded-lg text-xs font-bold">
                <Plus size={14} /> NEW DIAGNOSIS
              </button>
            )}
          </div>
        </div>
      </header>

      <main className="flex-grow py-8 px-4 sm:px-6 lg:px-8 max-w-7xl mx-auto w-full space-y-8 z-10 relative">
        <section className="space-y-4">
          <div className="flex justify-between items-center">
            <h2 className="text-xl font-bold text-white flex items-center gap-2">
              <Sparkles className="text-cyan-300 w-4 h-4" /> Market Landscape
            </h2>
          </div>
          {loading && data.length === 0 ? (
             <div className="h-[400px] w-full bg-[#0f172a]/60 rounded-2xl flex items-center justify-center">
                <Loader2 className="animate-spin text-cyan-400" size={32} />
             </div>
          ) : data.length === 0 ? (
             <div className="h-[400px] w-full bg-[#0f172a]/60 rounded-2xl flex flex-col items-center justify-center border border-white/5 text-slate-400 gap-4">
                {systemStatus.storage ? (
                    <Database size={48} className="text-cyan-500/50" />
                ) : (
                    <AlertTriangle size={48} className="text-amber-500/50" />
                )}
                <div className="text-center">
                    <h3 className="text-white font-bold text-lg">{systemStatus.storage ? "Database Ready (Empty)" : "Storage Disconnected"}</h3>
                    <p className="text-sm max-w-md mt-2">
                        {systemStatus.storage 
                           ? "Vercel Blob Storage is connected. Initialize via Admin Batch." 
                           : "Could not connect to Vercel Blob."}
                    </p>
                </div>
             </div>
          ) : (
             <div className="bg-[#0f172a]/60 border border-white/10 rounded-2xl p-1 shadow-2xl backdrop-blur-xl">
                <TruthMatrix data={filteredData} onSelectCompany={handleCompanyClick} />
             </div>
          )}

          {/* --- DOMAIN FILTER TABS --- */}
          {data.length > 0 && (
            <div className="flex flex-wrap gap-1.5 mt-3">
              {availableDomains.map(domain => (
                <button
                  key={domain}
                  onClick={() => setSelectedDomain(domain)}
                  className={`px-3 py-1 rounded-full text-[10px] font-mono font-semibold uppercase tracking-wide transition-all border ${
                    selectedDomain === domain
                      ? 'bg-cyan-500/20 text-cyan-200 border-cyan-500/50 shadow-[0_0_8px_rgba(34,211,238,0.15)]'
                      : 'bg-white/5 text-slate-400 border-white/10 hover:bg-white/10 hover:text-slate-200'
                  }`}
                >
                  {domain}
                </button>
              ))}
            </div>
          )}

          {/* --- DOMAIN GAUGE CARDS --- */}
          {domainStats && data.length > 0 && (
            <div className="grid grid-cols-2 md:grid-cols-4 gap-3 mt-2">
              <div className="bg-[#0f172a]/70 border border-white/10 rounded-xl p-4">
                <div className="text-[9px] text-slate-500 uppercase tracking-wider font-mono mb-2">Avg Ambition</div>
                <div className="text-2xl font-bold text-indigo-300">{domainStats.avgIntent}</div>
                <div className="w-full h-1.5 bg-slate-800 rounded-full mt-2 overflow-hidden">
                  <div className="h-full bg-indigo-500 rounded-full transition-all" style={{ width: `${domainStats.avgIntent}%` }}></div>
                </div>
              </div>
              <div className="bg-[#0f172a]/70 border border-white/10 rounded-xl p-4">
                <div className="text-[9px] text-slate-500 uppercase tracking-wider font-mono mb-2">Avg Reality</div>
                <div className="text-2xl font-bold text-cyan-300">{domainStats.avgReality}</div>
                <div className="w-full h-1.5 bg-slate-800 rounded-full mt-2 overflow-hidden">
                  <div className="h-full bg-cyan-500 rounded-full transition-all" style={{ width: `${domainStats.avgReality}%` }}></div>
                </div>
              </div>
              <div className="bg-[#0f172a]/70 border border-white/10 rounded-xl p-4">
                <div className="text-[9px] text-slate-500 uppercase tracking-wider font-mono mb-2">Companies</div>
                <div className="text-2xl font-bold text-white">{domainStats.count}</div>
                <div className="text-[10px] text-slate-500 mt-2 font-mono">
                  {selectedDomain === 'All' ? 'All domains' : selectedDomain}
                </div>
              </div>
              <div className="bg-[#0f172a]/70 border border-white/10 rounded-xl p-4">
                <div className="text-[9px] text-slate-500 uppercase tracking-wider font-mono mb-2">Archetype Split</div>
                <div className="flex gap-1 mt-1">
                  {Object.entries(domainStats.archetypes).map(([arch, count]) => {
                    const total = domainStats.count;
                    const pct = total > 0 ? Math.round(((count as number) / total) * 100) : 0;
                    const colors = {
                      'Thriving': 'bg-emerald-500',
                      'Modernizing': 'bg-cyan-500',
                      'Cash Cow': 'bg-amber-500',
                      'At Risk': 'bg-rose-500'
                    };
                    if (pct === 0) return null;
                    return <div key={arch} className={`h-6 rounded ${colors[arch]}`} style={{ width: `${pct}%`, minWidth: pct > 0 ? '8px' : '0' }} title={`${arch}: ${count} (${pct}%)`}></div>;
                  })}
                </div>
                <div className="flex justify-between text-[8px] text-slate-600 mt-1.5 font-mono">
                  <span className="text-emerald-500">{domainStats.archetypes['Thriving']} Thr</span>
                  <span className="text-cyan-500">{domainStats.archetypes['Modernizing']} Mod</span>
                  <span className="text-amber-500">{domainStats.archetypes['Cash Cow']} CC</span>
                  <span className="text-rose-500">{domainStats.archetypes['At Risk']} AR</span>
                </div>
              </div>
            </div>
          )}
        </section>

        <section className="space-y-4">
          <div className="flex flex-col md:flex-row justify-between md:items-center gap-2">
            <h2 className="text-lg font-bold text-white">Forensic Index</h2>
            <div className="flex gap-3 text-[10px] font-mono text-slate-500">
              <span><span className="text-slate-300 font-bold">S</span> &lt;100M</span>
              <span><span className="text-slate-300 font-bold">M</span> 100-499M</span>
              <span><span className="text-slate-300 font-bold">L</span> 500M-2B</span>
              <span><span className="text-slate-300 font-bold">XL</span> &gt;2B revenue (EUR)</span>
            </div>
          </div>
          <div className="bg-[#0f172a]/70 border border-white/10 rounded-2xl overflow-hidden min-h-[200px]">
              <table className="w-full text-sm text-left">
                <thead className="text-[10px] text-indigo-200 uppercase bg-black/30 border-b border-white/5">
                  <tr>
                    <th className="px-4 py-4 cursor-pointer" onClick={() => handleSort('companyName')}>Entity</th>
                    <th className="px-3 py-4 cursor-pointer hidden md:table-cell" onClick={() => handleSort('domain')}>Domain</th>
                    <th className="px-3 py-4 cursor-pointer" onClick={() => handleSort('intent_score')}>Ambition</th>
                    <th className="px-3 py-4 cursor-pointer" onClick={() => handleSort('reality_score')}>Reality</th>
                    <th className="px-3 py-4 cursor-pointer hidden md:table-cell" onClick={() => handleSort('financial_index')}>Financial</th>
                    <th className="px-3 py-4 cursor-pointer hidden lg:table-cell" onClick={() => handleSort('revenue_avg_3yr')}>Size</th>
                    <th className="px-3 py-4 cursor-pointer" onClick={() => handleSort('archetype')}>Archetype</th>
                    <th className="px-3 py-4">Action</th>
                  </tr>
                </thead>
                <tbody className="divide-y divide-white/5 text-slate-300">
                  {sortedData.map((row) => {
                    const archetype = getArchetype(row.reality_score, row.financial_index ?? row.intent_score ?? 0);
                    const revTier = getRevenueTier(row.revenue_avg_3yr);
                    const lowConfidence = row.confidence_score != null && row.confidence_score < 50;
                    const tierColors = { XL: 'text-violet-300 bg-violet-500/15 border-violet-500/30', L: 'text-blue-300 bg-blue-500/15 border-blue-500/30', M: 'text-slate-300 bg-slate-500/15 border-slate-500/30', S: 'text-slate-500 bg-slate-500/10 border-slate-500/20' };
                    return (
                    <tr key={row.id} className="hover:bg-white/5 transition-colors">
                      <td className="px-4 py-3 font-medium text-white">
                        <div className="flex items-center gap-2">
                          {row.companyName}
                          {row.isReference && <span className="text-[9px] bg-green-500/30 text-green-200 px-1 rounded uppercase font-bold">(Ref)</span>}
                          {row.has_docs && <Cpu size={12} className="text-indigo-400" title="Deep Dive Docs Used" />}
                          {lowConfidence && <span className="text-[9px] bg-amber-500/20 text-amber-300 px-1 rounded font-mono" title={`Confidence: ${row.confidence_score}/100`}>?</span>}
                        </div>
                      </td>
                      <td className="px-3 py-3 hidden md:table-cell">
                        <span className="text-[10px] font-mono text-slate-500 truncate block max-w-[120px]">{row.domain || '-'}</span>
                      </td>
                      <td className="px-3 py-3">{row.intent_score}</td>
                      <td className="px-3 py-3">{row.reality_score}</td>
                      <td className="px-3 py-3 hidden md:table-cell">
                        {row.financial_index != null ? <span className="font-mono">{row.financial_index}</span> : <span className="text-slate-600">-</span>}
                      </td>
                      <td className="px-3 py-3 hidden lg:table-cell">
                        <span className={`text-[9px] font-mono font-bold px-1.5 py-0.5 rounded border ${tierColors[revTier.size]}`}>
                          {revTier.label}
                        </span>
                      </td>
                      <td className="px-3 py-3 font-mono text-xs">
                         <span className={`px-2.5 py-1 rounded-md border ${archetype.bg} ${archetype.border} ${archetype.color} font-semibold uppercase tracking-wider text-[10px]`}>
                            {archetype.label}
                         </span>
                      </td>
                      <td className="px-3 py-3">
                        <button onClick={() => handleCompanyClick(row)} className="text-cyan-400 hover:text-cyan-300 text-xs font-medium">View Report</button>
                      </td>
                    </tr>
                    );
                  })}
                </tbody>
              </table>
          </div>
        </section>
      </main>

      {isScannerOpen && (
        <div className="fixed inset-0 z-50 flex items-center justify-center p-4">
           <div className="absolute inset-0 bg-black/90 backdrop-blur-sm" onClick={() => !isScanning && setIsScannerOpen(false)} />
           <div className="relative w-full max-w-2xl bg-[#0f172a] border border-white/10 rounded-2xl shadow-2xl overflow-hidden z-10">
               {isScanning ? (
                   <div className="p-16 flex flex-col items-center justify-center">
                       <Loader2 className="animate-spin text-cyan-400 mb-4" size={32} />
                       <p className="text-white">Scanning...</p>
                   </div>
               ) : (
                    <>
                        <div className="absolute top-2 right-2">
                            <button onClick={() => setIsScannerOpen(false)}><X className="text-white" /></button>
                        </div>
                        <ScannerInput onScan={handleRunScan} />
                    </>
               )}
           </div>
        </div>
      )}

      {isBatchOpen && (
        <div className="fixed inset-0 z-[60] flex items-center justify-center p-4">
            <div className="absolute inset-0 bg-black/95 backdrop-blur-md" />
            <div className="relative w-full max-w-lg bg-[#0f172a] border border-cyan-500/30 rounded-xl shadow-2xl z-10 flex flex-col max-h-[80vh]">
                <div className="p-4 border-b border-white/10 flex justify-between items-center bg-black/20">
                    <h3 className="font-bold text-white flex items-center gap-2">
                        <Terminal size={18} className="text-cyan-400" />
                        Batch Diagnosis Terminal
                    </h3>
                    <div className="flex gap-2">
                        {batchStatus.isRunning ? (
                             <button onClick={handleStopBatch} className="text-rose-400 hover:text-white px-2 py-1 text-xs border border-rose-500/30 rounded">
                                STOP
                             </button>
                        ) : (
                             <button onClick={() => setIsBatchOpen(false)} className="text-slate-400 hover:text-white">
                                <X size={18} />
                             </button>
                        )}
                    </div>
                </div>
                
                <div className="p-6 space-y-4">
                    <div className="flex justify-between text-xs font-mono text-cyan-300 uppercase">
                        <span>Progress</span>
                        <span>{batchStatus.current} / {batchStatus.total}</span>
                    </div>
                    <div className="w-full h-2 bg-slate-800 rounded-full overflow-hidden">
                        <div 
                            className="h-full bg-cyan-500 transition-all duration-300 ease-out"
                            style={{ width: `${(batchStatus.current / (batchStatus.total || 1)) * 100}%` }}
                        ></div>
                    </div>
                    <div className="text-center py-4">
                        <h4 className="text-lg font-bold text-white animate-pulse">
                            {batchStatus.isRunning ? `Analyzing: ${batchStatus.currentName}` : "Execution Halted"}
                        </h4>
                        <p className="text-xs text-slate-500 mt-1">
                            {batchStatus.isRunning ? "Please keep this tab open to prevent timeouts." : "Process completed or stopped manually."}
                        </p>
                    </div>
                </div>

                <div className="flex-grow bg-black/50 p-4 font-mono text-[10px] text-slate-400 overflow-y-auto border-t border-white/10 space-y-1">
                    {batchStatus.logs.map((log, i) => (
                        <div key={i} className="border-b border-white/5 pb-0.5">{log}</div>
                    ))}
                    <div ref={(el) => el && el.scrollIntoView({ behavior: "smooth" })} />
                </div>
            </div>
        </div>
      )}

      {/* --- ANALYTICS PANEL --- */}
      {isAnalyticsOpen && (
        <div className="fixed inset-0 z-[60] flex items-center justify-center p-4">
          <div className="absolute inset-0 bg-black/90 backdrop-blur-sm" onClick={() => setIsAnalyticsOpen(false)} />
          <div className="relative w-full max-w-4xl bg-[#0f172a] border border-violet-500/30 rounded-xl shadow-2xl z-10 flex flex-col max-h-[90vh] overflow-hidden">
            <div className="p-4 border-b border-white/10 flex justify-between items-center bg-black/20 shrink-0">
              <h3 className="font-bold text-white flex items-center gap-2">
                <BarChart3 size={18} className="text-violet-400" />
                Statistical Analysis
              </h3>
              <button onClick={() => setIsAnalyticsOpen(false)} className="text-slate-400 hover:text-white">
                <X size={18} />
              </button>
            </div>

            <div className="overflow-y-auto p-6 space-y-6">
              {analyticsLoading ? (
                <div className="flex flex-col items-center justify-center py-16">
                  <Loader2 className="animate-spin text-violet-400 mb-4" size={32} />
                  <p className="text-slate-400 text-sm">Running analysis...</p>
                </div>
              ) : analyticsData?.error ? (
                <div className="text-center py-16 text-slate-400">{analyticsData.error}</div>
              ) : analyticsData ? (
                <>
                  {/* HEADER */}
                  <div className="text-center">
                    <div className="text-[10px] text-violet-400 uppercase tracking-widest font-mono mb-1">Correlation & Distribution Report</div>
                    <div className="text-2xl font-bold text-white">{analyticsData.n} Companies Analyzed</div>
                  </div>

                  {/* CORE CORRELATION: Reality vs Financial */}
                  <div className="bg-violet-500/5 border border-violet-500/20 rounded-xl p-5">
                    <h4 className="text-sm font-bold text-violet-300 uppercase tracking-wide mb-3 flex items-center gap-2">
                      <TrendingUp size={14} /> Reality Score vs Financial Index
                    </h4>
                    {analyticsData.reality_vs_financial?.pearson_r != null ? (
                      <div className="space-y-3">
                        <div className="grid grid-cols-2 md:grid-cols-4 gap-3">
                          <div className="bg-black/30 rounded-lg p-3 text-center">
                            <div className="text-[9px] text-slate-500 uppercase tracking-wider font-mono">Paired n</div>
                            <div className="text-xl font-bold text-white">{analyticsData.reality_vs_financial.n}</div>
                          </div>
                          <div className="bg-black/30 rounded-lg p-3 text-center">
                            <div className="text-[9px] text-slate-500 uppercase tracking-wider font-mono">Pearson r</div>
                            <div className={`text-xl font-bold ${analyticsData.reality_vs_financial.pearson_r > 0.3 ? 'text-emerald-400' : analyticsData.reality_vs_financial.pearson_r < -0.3 ? 'text-rose-400' : 'text-amber-400'}`}>
                              {analyticsData.reality_vs_financial.pearson_r > 0 ? '+' : ''}{analyticsData.reality_vs_financial.pearson_r}
                            </div>
                          </div>
                          <div className="bg-black/30 rounded-lg p-3 text-center">
                            <div className="text-[9px] text-slate-500 uppercase tracking-wider font-mono">Spearman rho</div>
                            <div className="text-xl font-bold text-slate-200">
                              {analyticsData.reality_vs_financial.spearman_rho > 0 ? '+' : ''}{analyticsData.reality_vs_financial.spearman_rho}
                            </div>
                          </div>
                          <div className="bg-black/30 rounded-lg p-3 text-center">
                            <div className="text-[9px] text-slate-500 uppercase tracking-wider font-mono">Interpretation</div>
                            <div className="text-sm font-bold text-violet-300 uppercase">{analyticsData.reality_vs_financial.interpretation}</div>
                          </div>
                        </div>
                        {analyticsData.reality_vs_financial.pearson_p != null && (
                          <div className="text-[10px] text-slate-500 font-mono text-center">
                            p-value = {analyticsData.reality_vs_financial.pearson_p} {analyticsData.reality_vs_financial.pearson_p < 0.05 ? '(statistically significant)' : '(not significant)'}
                          </div>
                        )}
                        {/* Outliers */}
                        {analyticsData.reality_vs_financial.outliers?.length > 0 && (
                          <div className="mt-3">
                            <div className="text-[10px] text-slate-500 uppercase tracking-wider font-mono mb-2">Largest Outliers from Regression</div>
                            <div className="space-y-1">
                              {analyticsData.reality_vs_financial.outliers.map((o, i) => (
                                <div key={i} className="flex items-center justify-between text-xs font-mono bg-black/20 rounded px-3 py-1.5">
                                  <span className="text-slate-300 truncate max-w-[200px]">{o.name}</span>
                                  <div className="flex gap-4 text-[10px]">
                                    <span className="text-cyan-400">R:{o.reality}</span>
                                    <span className="text-emerald-400">F:{o.financial}</span>
                                    <span className={o.residual > 0 ? 'text-emerald-400' : 'text-rose-400'}>
                                      {o.residual > 0 ? '+' : ''}{o.residual}
                                    </span>
                                  </div>
                                </div>
                              ))}
                            </div>
                          </div>
                        )}
                      </div>
                    ) : (
                      <div className="text-slate-500 text-sm">Not enough paired data (need 3+ companies with both scores)</div>
                    )}
                  </div>

                  {/* CORRELATION MATRIX */}
                  {analyticsData.correlation_matrix && (
                    <div className="bg-black/30 border border-white/10 rounded-xl p-5">
                      <h4 className="text-sm font-bold text-slate-300 uppercase tracking-wide mb-3">Correlation Matrix (Pearson)</h4>
                      <div className="overflow-x-auto">
                        <table className="w-full text-xs font-mono">
                          <thead>
                            <tr className="text-slate-500">
                              <th className="text-left py-1 px-2 w-24"></th>
                              {['Intent', 'Reality', 'Financial', 'Confidence'].map(h => (
                                <th key={h} className="text-center py-1 px-2">{h}</th>
                              ))}
                            </tr>
                          </thead>
                          <tbody>
                            {['intent', 'reality', 'financial', 'confidence'].map((ki, i) => (
                              <tr key={ki}>
                                <td className="text-slate-400 py-1 px-2 capitalize">{ki}</td>
                                {['intent', 'reality', 'financial', 'confidence'].map((kj) => {
                                  const v = analyticsData.correlation_matrix[ki]?.[kj];
                                  const isIdentity = ki === kj;
                                  const color = v == null ? 'text-slate-600' : isIdentity ? 'text-slate-500' : v > 0.3 ? 'text-emerald-400' : v < -0.3 ? 'text-rose-400' : 'text-slate-300';
                                  return (
                                    <td key={kj} className={`text-center py-1 px-2 ${color}`}>
                                      {v != null ? (v > 0 ? '+' : '') + v.toFixed(3) : 'n/a'}
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

                  {/* DESCRIPTIVE STATS */}
                  {analyticsData.descriptive && (
                    <div className="bg-black/30 border border-white/10 rounded-xl p-5">
                      <h4 className="text-sm font-bold text-slate-300 uppercase tracking-wide mb-3">Descriptive Statistics</h4>
                      <div className="overflow-x-auto">
                        <table className="w-full text-xs font-mono">
                          <thead>
                            <tr className="text-slate-500 text-[10px]">
                              <th className="text-left py-1 px-2"></th>
                              <th className="text-center py-1 px-1">n</th>
                              <th className="text-center py-1 px-1">Mean</th>
                              <th className="text-center py-1 px-1">Median</th>
                              <th className="text-center py-1 px-1">Std</th>
                              <th className="text-center py-1 px-1">Min</th>
                              <th className="text-center py-1 px-1">Max</th>
                              <th className="text-center py-1 px-1">Skew</th>
                            </tr>
                          </thead>
                          <tbody>
                            {[['Intent', 'intent'], ['Reality', 'reality'], ['Financial', 'financial'], ['Confidence', 'confidence'], ['Gap (I-R)', 'gap']].map(([label, key]) => {
                              const d = analyticsData.descriptive[key];
                              if (!d) return null;
                              return (
                                <tr key={key} className="border-t border-white/5">
                                  <td className="text-slate-400 py-1.5 px-2">{label}</td>
                                  <td className="text-center text-slate-500 py-1.5 px-1">{d.n}</td>
                                  <td className="text-center text-white py-1.5 px-1">{d.mean}</td>
                                  <td className="text-center text-slate-300 py-1.5 px-1">{d.median}</td>
                                  <td className="text-center text-slate-400 py-1.5 px-1">{d.std}</td>
                                  <td className="text-center text-slate-400 py-1.5 px-1">{d.min}</td>
                                  <td className="text-center text-slate-400 py-1.5 px-1">{d.max}</td>
                                  <td className={`text-center py-1.5 px-1 ${d.skew > 0.5 ? 'text-amber-400' : d.skew < -0.5 ? 'text-cyan-400' : 'text-slate-400'}`}>{d.skew > 0 ? '+' : ''}{d.skew}</td>
                                </tr>
                              );
                            })}
                          </tbody>
                        </table>
                      </div>
                    </div>
                  )}

                  {/* ARCHETYPE DISTRIBUTION */}
                  {analyticsData.archetypes && (
                    <div className="bg-black/30 border border-white/10 rounded-xl p-5">
                      <h4 className="text-sm font-bold text-slate-300 uppercase tracking-wide mb-3">Archetype Distribution</h4>
                      <div className="space-y-2">
                        {[
                          { key: 'Thriving', color: 'bg-emerald-500', text: 'text-emerald-400' },
                          { key: 'Modernizing', color: 'bg-cyan-500', text: 'text-cyan-400' },
                          { key: 'Cash Cow', color: 'bg-amber-500', text: 'text-amber-400' },
                          { key: 'At Risk', color: 'bg-rose-500', text: 'text-rose-400' },
                        ].map(({ key, color, text }) => {
                          const a = analyticsData.archetypes[key];
                          if (!a) return null;
                          return (
                            <div key={key} className="flex items-center gap-3">
                              <span className={`${text} text-xs font-mono w-28 text-right`}>{key}</span>
                              <div className="flex-grow h-5 bg-slate-800 rounded overflow-hidden">
                                <div className={`h-full ${color} rounded transition-all`} style={{ width: `${a.pct}%` }}></div>
                              </div>
                              <span className="text-xs font-mono text-slate-400 w-20">{a.count} ({a.pct}%)</span>
                            </div>
                          );
                        })}
                      </div>
                    </div>
                  )}

                  {/* DOMAIN BREAKDOWN */}
                  {analyticsData.by_domain && (
                    <div className="bg-black/30 border border-white/10 rounded-xl p-5">
                      <h4 className="text-sm font-bold text-slate-300 uppercase tracking-wide mb-3">Average Scores by Industry</h4>
                      <div className="overflow-x-auto">
                        <table className="w-full text-xs font-mono">
                          <thead>
                            <tr className="text-slate-500 text-[10px]">
                              <th className="text-left py-1 px-2">Domain</th>
                              <th className="text-center py-1 px-1">n</th>
                              <th className="text-center py-1 px-1">Intent</th>
                              <th className="text-center py-1 px-1">Reality</th>
                              <th className="text-center py-1 px-1">Financial</th>
                              <th className="text-center py-1 px-1">Gap</th>
                            </tr>
                          </thead>
                          <tbody>
                            {Object.entries(analyticsData.by_domain).map(([domain, s]: [string, any]) => (
                              <tr key={domain} className="border-t border-white/5">
                                <td className="text-slate-300 py-1.5 px-2 max-w-[180px] truncate">{domain}</td>
                                <td className="text-center text-slate-500 py-1.5 px-1">{s.n}</td>
                                <td className="text-center text-indigo-300 py-1.5 px-1">{s.avg_intent}</td>
                                <td className="text-center text-cyan-300 py-1.5 px-1">{s.avg_reality}</td>
                                <td className="text-center text-emerald-300 py-1.5 px-1">{s.avg_financial ?? '-'}</td>
                                <td className={`text-center py-1.5 px-1 ${s.avg_gap > 10 ? 'text-amber-400' : s.avg_gap < -10 ? 'text-cyan-400' : 'text-slate-400'}`}>
                                  {s.avg_gap != null ? (s.avg_gap > 0 ? '+' : '') + s.avg_gap : '-'}
                                </td>
                              </tr>
                            ))}
                          </tbody>
                        </table>
                      </div>
                    </div>
                  )}

                  {/* OWNERSHIP BREAKDOWN */}
                  {analyticsData.by_ownership && (
                    <div className="bg-black/30 border border-white/10 rounded-xl p-5">
                      <h4 className="text-sm font-bold text-slate-300 uppercase tracking-wide mb-3">Average Scores by Ownership</h4>
                      <div className="overflow-x-auto">
                        <table className="w-full text-xs font-mono">
                          <thead>
                            <tr className="text-slate-500 text-[10px]">
                              <th className="text-left py-1 px-2">Ownership</th>
                              <th className="text-center py-1 px-1">n</th>
                              <th className="text-center py-1 px-1">Intent</th>
                              <th className="text-center py-1 px-1">Reality</th>
                              <th className="text-center py-1 px-1">Financial</th>
                            </tr>
                          </thead>
                          <tbody>
                            {Object.entries(analyticsData.by_ownership).map(([owner, s]: [string, any]) => (
                              <tr key={owner} className="border-t border-white/5">
                                <td className="text-slate-300 py-1.5 px-2">{owner}</td>
                                <td className="text-center text-slate-500 py-1.5 px-1">{s.n}</td>
                                <td className="text-center text-indigo-300 py-1.5 px-1">{s.avg_intent}</td>
                                <td className="text-center text-cyan-300 py-1.5 px-1">{s.avg_reality}</td>
                                <td className="text-center text-emerald-300 py-1.5 px-1">{s.avg_financial ?? '-'}</td>
                              </tr>
                            ))}
                          </tbody>
                        </table>
                      </div>
                    </div>
                  )}

                  {/* TOP 10 / BOTTOM 10 */}
                  <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
                    {analyticsData.top_10_reality && (
                      <div className="bg-emerald-500/5 border border-emerald-500/20 rounded-xl p-4">
                        <h4 className="text-[10px] font-bold text-emerald-400 uppercase tracking-wide mb-2 flex items-center gap-1">
                          <TrendingUp size={12} /> Top 10 Reality Score
                        </h4>
                        <div className="space-y-1">
                          {analyticsData.top_10_reality.map((c, i) => (
                            <div key={i} className="flex justify-between text-[11px] font-mono py-0.5">
                              <span className="text-slate-300 truncate max-w-[160px]">{i+1}. {c.name}</span>
                              <div className="flex gap-2 text-[10px]">
                                <span className="text-cyan-400">{c.reality}</span>
                                <span className="text-emerald-400">{c.financial ?? '-'}</span>
                              </div>
                            </div>
                          ))}
                        </div>
                      </div>
                    )}
                    {analyticsData.bottom_10_reality && (
                      <div className="bg-rose-500/5 border border-rose-500/20 rounded-xl p-4">
                        <h4 className="text-[10px] font-bold text-rose-400 uppercase tracking-wide mb-2 flex items-center gap-1">
                          <TrendingDown size={12} /> Bottom 10 Reality Score
                        </h4>
                        <div className="space-y-1">
                          {analyticsData.bottom_10_reality.map((c, i) => (
                            <div key={i} className="flex justify-between text-[11px] font-mono py-0.5">
                              <span className="text-slate-300 truncate max-w-[160px]">{i+1}. {c.name}</span>
                              <div className="flex gap-2 text-[10px]">
                                <span className="text-cyan-400">{c.reality}</span>
                                <span className="text-rose-400">{c.financial ?? '-'}</span>
                              </div>
                            </div>
                          ))}
                        </div>
                      </div>
                    )}
                  </div>

                  {/* DISTRIBUTIONS */}
                  {analyticsData.distributions && (
                    <div className="bg-black/30 border border-white/10 rounded-xl p-5">
                      <h4 className="text-sm font-bold text-slate-300 uppercase tracking-wide mb-3">Score Distributions</h4>
                      <div className="grid grid-cols-1 md:grid-cols-3 gap-4">
                        {[
                          { key: 'reality', label: 'Reality', color: 'bg-cyan-500' },
                          { key: 'financial', label: 'Financial', color: 'bg-emerald-500' },
                          { key: 'intent', label: 'Intent', color: 'bg-indigo-500' },
                        ].map(({ key, label, color }) => {
                          const dist = analyticsData.distributions[key];
                          if (!dist) return null;
                          const maxCount = Math.max(...dist.bins.map(b => b.count));
                          return (
                            <div key={key}>
                              <div className="text-[10px] text-slate-500 font-mono uppercase mb-1">{label} (n={dist.n})</div>
                              <div className="space-y-0.5">
                                {dist.bins.map((b, i) => (
                                  <div key={i} className="flex items-center gap-1.5 text-[9px] font-mono">
                                    <span className="text-slate-600 w-12 text-right">{b.range}</span>
                                    <div className="flex-grow h-3 bg-slate-800 rounded overflow-hidden">
                                      <div className={`h-full ${color} rounded`} style={{ width: `${maxCount > 0 ? (b.count / maxCount) * 100 : 0}%` }}></div>
                                    </div>
                                    <span className="text-slate-500 w-4">{b.count}</span>
                                  </div>
                                ))}
                              </div>
                            </div>
                          );
                        })}
                      </div>
                    </div>
                  )}
                </>
              ) : null}
            </div>
          </div>
        </div>
      )}

      <CompanyModal
        company={selectedCompany}
        isOpen={isModalOpen} 
        isLoading={isLoadingDetails}
        onClose={() => setIsModalOpen(false)}
        onUpdate={(updated) => {
            setSelectedCompany(updated);
            setData(prev => {
                const idx = prev.findIndex(p => p.companyName === updated.companyName);
                if (idx > -1) {
                    const newArr = [...prev];
                    newArr[idx] = { 
                        ...newArr[idx], 
                        intent_score: updated.intent_score,
                        reality_score: updated.reality_score,
                        reality_gap: updated.reality_gap,
                        lastUpdated: updated.lastUpdated,
                        has_docs: updated.uploaded_docs && updated.uploaded_docs.length > 0
                    }; 
                    return newArr;
                }
                return [...prev, updated];
            });
            fetchSummaryData(true); 
        }}
      />
    </div>
  );
};

(window as any).App = App;
