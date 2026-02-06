const { useState, useEffect } = (window as any).React;

// --- ICON SAFETY LAYER ---
// Wraps Lucide access so accessing an undefined icon returns a Null component instead of crashing
const LucideRaw = (window as any).Lucide || {};
const Icons = new Proxy(LucideRaw, {
    get: (target, prop) => {
        if (prop in target) return target[prop];
        return () => null; // Return generic empty component if icon missing
    }
});

const { Activity, Search, ArrowUpDown, Plus, Loader2, X, Sparkles, Shield, Database, Play, RefreshCw, CheckCircle2, AlertTriangle, Cloud, RotateCcw, Lock, HardDrive, Terminal } = Icons;

// Components & Services from Window
const TruthMatrix = (window as any).TruthMatrix;
const ScannerInput = (window as any).ScannerInput;
const CompanyModal = (window as any).CompanyModal;

// --- CONFIGURATION ---
const IS_ADMIN_MODE = true; 
// ---------------------

const App = () => {
  const [data, setData] = useState([]);
  const [loading, setLoading] = useState(true);
  const [connectionError, setConnectionError] = useState(null); 
  const [searchQuery, setSearchQuery] = useState('');
  const [selectedCompany, setSelectedCompany] = useState(null);
  const [isModalOpen, setIsModalOpen] = useState(false);
  const [isScannerOpen, setIsScannerOpen] = useState(false);
  const [isScanning, setIsScanning] = useState(false);
  const [isSeeding, setIsSeeding] = useState(false);
  const [sortConfig, setSortConfig] = useState({ key: 'reality_gap', direction: 'desc' });
  const [systemStatus, setSystemStatus] = useState({ online: false, storage: false, google: false, ai: false });

  // Check Backend Health & Config
  const checkHealth = async () => {
      try {
          const res = await fetch('/api/health');
          if (res.ok) {
              const json = await res.json();
              setSystemStatus({
                  online: true,
                  storage: json.config_status?.has_blob_token || false,
                  google: json.config_status?.has_google_search_key || false,
                  ai: json.config_status?.has_gemini_key || false
              });
              return json.config_status?.has_blob_token;
          }
      } catch (e) {
          console.warn("Backend health check failed", e);
      }
      return false;
  };

  useEffect(() => {
    checkHealth();
  }, []);

  // Function to load data from backend
  const fetchRealData = async () => {
    try {
        setLoading(true);
        setConnectionError(null);
        const response = await fetch('/dashboard-data');
        if (response.ok) {
            const json = await response.json();
            // If json is empty array, that's fine (empty DB)
            console.log("Loaded real data from backend:", json.length, "records");
            setData(json);
        } else {
            throw new Error("Failed to fetch data");
        }
    } catch (e) {
        console.error("Connection failed:", e);
        // We do NOT set connectionError here to prevent a banner on initial load.
        // The empty state UI will handle the "no data" case.
        setData([]); 
    } finally {
        setLoading(false);
    }
  };

  useEffect(() => {
    fetchRealData();
  }, []);

  const handleRunScan = async (input) => {
    setIsScanning(true);
    const apiUrl = ""; 

    try {
      const response = await fetch(`${apiUrl}/scan`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ ...input, saveToDatabase: true })
      });
      
      if (!response.ok) throw new Error("Scan failed");
      const json = await response.json();
      const newResult = json.data;

      setData(prev => {
        // Update existing or add new
        const existsIndex = prev.findIndex(p => p.companyName === newResult.companyName);
        if (existsIndex > -1) {
          const updated = [...prev];
          updated[existsIndex] = newResult;
          return updated;
        }
        return [newResult, ...prev];
      });

      setIsScannerOpen(false);
      setSelectedCompany(newResult);
      setIsModalOpen(true);
    } catch (error) {
      console.error(error);
      alert("Error running scan: Connection to backend failed.");
    } finally {
      setIsScanning(false);
    }
  };

  const handleBatchSeed = async () => {
    if (!IS_ADMIN_MODE) return;
    
    // Double check status before running
    const hasStorage = await checkHealth();
    
    if (!hasStorage) {
         if (!confirm("WARNING: The frontend thinks the Storage Token is missing. Force proceed?")) return;
    } else {
        if (!confirm("Refresh Database?\n\nThis will scrape the Top 100 Finnish companies, run X-Ray analysis, and update the Vercel Blob database.\n\nProcess runs in the background (~30 seconds).")) return;
    }
    
    setIsSeeding(true);
    try {
        const response = await fetch(`/admin/seed-finland-100`, { method: 'POST' });
        if (response.ok) {
            alert("✅ Database Refresh Started!\n\nThe server is now processing the Top 100 list.\n\nPlease wait ~30 seconds, then click the smaller 'Refresh Data' button to see results.");
        } else {
            throw new Error(`API returned error ${response.status}`);
        }
    } catch (e) {
        alert("❌ Failed to contact backend.\n\nIs the Python server running?");
        console.error(e);
    } finally {
        setIsSeeding(false);
    }
  };

  const handleCompanyUpdate = (updatedCompany) => {
    setData(prev => prev.map(item => 
      item.companyName === updatedCompany.companyName ? updatedCompany : item
    ));
    setSelectedCompany(updatedCompany);
  };

  const handleCompanyClick = (company) => {
    if (!company) return;
    setSelectedCompany(company);
    setIsModalOpen(true);
  };

  const handleSort = (key) => {
    let direction = 'desc';
    if (sortConfig && sortConfig.key === key && sortConfig.direction === 'desc') {
      direction = 'asc';
    }
    setSortConfig({ key, direction });
  };

  const filteredData = data.filter(item => 
    item.companyName.toLowerCase().includes(searchQuery.toLowerCase())
  );

  const sortedData = [...filteredData].sort((a, b) => {
    if (!sortConfig) return 0;
    const aVal = a[sortConfig.key];
    const bVal = b[sortConfig.key];
    if (typeof aVal === 'string' && typeof bVal === 'string') return sortConfig.direction === 'asc' ? aVal.localeCompare(bVal) : bVal.localeCompare(aVal);
    if (typeof aVal === 'number' && typeof bVal === 'number') return sortConfig.direction === 'asc' ? aVal - bVal : bVal - aVal;
    return 0;
  });

  return (
    <div className="min-h-screen flex flex-col font-sans relative overflow-x-hidden text-slate-100">
      <div className="fixed inset-0 z-0 bg-[#1e1b4b] overflow-hidden">
        <div className="absolute inset-0 bg-gradient-to-b from-[#2e1065] via-[#1e1b4b] to-[#0f172a] opacity-80"></div>
        <div className="absolute top-[-10%] left-[-10%] w-[50%] h-[50%] bg-rose-500/30 rounded-full blur-[100px] mix-blend-screen animate-pulse-glow"></div>
        <div className="absolute top-[-10%] right-[-10%] w-[60%] h-[60%] bg-cyan-500/30 rounded-full blur-[120px] mix-blend-screen animate-pulse-glow" style={{ animationDelay: '4s' }}></div>
        <div className="absolute bottom-[-20%] left-[20%] w-[70%] h-[70%] bg-violet-600/30 rounded-full blur-[150px] mix-blend-screen" style={{ animationDelay: '2s' }}></div>
        <div className="absolute inset-0 opacity-[0.05] pointer-events-none"></div>
      </div>

      <header className="sticky top-0 z-40 w-full border-b border-white/10 bg-[#1e1b4b]/50 backdrop-blur-xl">
        <div className="max-w-7xl mx-auto px-4 sm:px-6 lg:px-8 h-16 flex items-center justify-between">
          <div className="flex items-center gap-3">
            <div className="w-8 h-8 bg-gradient-to-br from-cyan-500 to-violet-600 rounded-lg flex items-center justify-center shadow-[0_0_15px_rgba(139,92,246,0.3)] ring-1 ring-white/20">
              <span className="text-white font-bold text-sm">FI</span>
            </div>
            <div className="flex flex-col">
              <h1 className="font-bold text-base tracking-tight text-white hidden sm:block">
                Adaptability<span className="text-transparent bg-clip-text bg-gradient-to-r from-cyan-300 to-violet-300">Scanner</span>
              </h1>
              <span className="text-[10px] text-indigo-200 tracking-[0.15em] uppercase font-medium">Finland Top 100 Index</span>
            </div>
          </div>
          
          <div className="flex items-center gap-4 w-full max-w-md ml-4 justify-end">
            
            {/* ADMIN BUTTONS */}
            {IS_ADMIN_MODE && (
                <button 
                    onClick={handleBatchSeed}
                    disabled={isSeeding}
                    title="Run Top 100 Scrape"
                    className="flex items-center gap-2 px-4 py-2 bg-indigo-600 hover:bg-indigo-500 text-white font-semibold text-xs rounded-lg transition-all shadow-lg shadow-indigo-500/20 border border-indigo-400/20"
                >
                    {isSeeding ? <Loader2 size={14} className="animate-spin" /> : <Database size={14} />}
                    {isSeeding ? "REFRESHING..." : "REFRESH DATABASE"}
                </button>
            )}

            <button 
                onClick={fetchRealData} 
                title="Refresh Data from Server"
                className="p-2 bg-white/5 hover:bg-white/10 rounded-full text-indigo-300 transition-all border border-transparent hover:border-indigo-500/30"
            >
                <RefreshCw size={14} className={loading ? "animate-spin" : ""} />
            </button>

            <div className="relative w-full max-w-[240px] group hidden sm:block">
              <div className="absolute inset-y-0 left-0 pl-3 flex items-center pointer-events-none text-indigo-300">
                <Search size={14} />
              </div>
              <input
                type="text"
                placeholder="Search Finland Top 100..."
                value={searchQuery}
                onChange={(e) => setSearchQuery(e.target.value)}
                className="w-full pl-9 pr-4 py-1.5 bg-[#0f172a]/50 border border-indigo-500/30 rounded-lg text-sm text-slate-100 placeholder-indigo-300/50 focus:ring-2 focus:ring-cyan-500/20 outline-none transition-all"
              />
            </div>
            
            {IS_ADMIN_MODE && (
              <button 
                onClick={() => setIsScannerOpen(true)}
                className="flex items-center gap-2 px-4 py-2 bg-white/10 hover:bg-white/20 text-white font-semibold text-xs rounded-lg transition-all border border-white/10 backdrop-blur-md"
              >
                <Plus size={14} /> NEW DIAGNOSIS
              </button>
            )}
          </div>
        </div>
      </header>

      {/* ERROR BANNER - Only shown if connectionError state is explicitly set (e.g. after a user action fails) */}
      {connectionError && (
        <div className="w-full bg-rose-500/20 border-b border-rose-500/30 backdrop-blur-sm">
             <div className="max-w-7xl mx-auto px-4 py-2 flex items-center justify-center gap-2 text-rose-200 text-xs font-medium">
                 <AlertTriangle size={14} />
                 <span>{connectionError}</span>
             </div>
        </div>
      )}

      <main className="flex-grow py-8 px-4 sm:px-6 lg:px-8 max-w-7xl mx-auto w-full space-y-8 z-10 relative">
        <section className="space-y-4">
          <div className="flex justify-between items-center">
            <h2 className="text-xl font-bold text-white flex items-center gap-2 tracking-tight">
              <Sparkles className="text-cyan-300 w-4 h-4" /> Market Landscape (Finland)
            </h2>
          </div>
          {loading ? (
             <div className="h-[400px] w-full bg-[#0f172a]/60 rounded-2xl border border-white/10 backdrop-blur-md flex items-center justify-center">
                <Loader2 className="animate-spin text-cyan-400" size={32} />
             </div>
          ) : data.length === 0 ? (
             <div className="h-[250px] w-full bg-[#0f172a]/60 rounded-2xl border border-white/10 backdrop-blur-md flex flex-col items-center justify-center text-slate-400 space-y-4">
                 <Database size={48} className="opacity-20" />
                 <p className="text-sm">Database is empty or unreachable.</p>
                 <button onClick={handleBatchSeed} className="text-indigo-400 hover:text-indigo-300 text-xs underline">
                    Run Initial Database Refresh
                 </button>
             </div>
          ) : (
             <div className="bg-[#0f172a]/60 border border-white/10 rounded-2xl p-1 shadow-2xl backdrop-blur-xl">
                <TruthMatrix data={data} onSelectCompany={handleCompanyClick} />
             </div>
          )}
        </section>

        <section className="space-y-4">
          <div className="flex justify-between items-center">
            <h2 className="text-lg font-bold text-white tracking-tight">Forensic Index</h2>
            <span className="text-[10px] font-mono text-indigo-200 uppercase tracking-wider bg-[#0f172a]/50 px-2 py-1 rounded border border-indigo-500/20">{filteredData.length} Entities</span>
          </div>

          <div className="bg-[#0f172a]/70 border border-white/10 rounded-2xl overflow-hidden shadow-2xl backdrop-blur-xl">
            <div className="overflow-x-auto">
              <table className="w-full text-sm text-left">
                <thead className="text-[10px] text-indigo-200 uppercase bg-black/30 border-b border-white/5 tracking-wider font-semibold">
                  <tr>
                    <th className="px-6 py-4 cursor-pointer hover:text-white" onClick={() => handleSort('companyName')}>
                      <div className="flex items-center gap-2">Entity <ArrowUpDown size={10} className="opacity-30" /></div>
                    </th>
                    <th className="px-6 py-4 cursor-pointer hover:text-white" onClick={() => handleSort('intent_score')}>
                       <div className="flex items-center gap-2">Intent <ArrowUpDown size={10} className="opacity-30" /></div>
                    </th>
                    <th className="px-6 py-4 cursor-pointer hover:text-white" onClick={() => handleSort('reality_score')}>
                       <div className="flex items-center gap-2">Reality <ArrowUpDown size={10} className="opacity-30" /></div>
                    </th>
                    <th className="px-6 py-4 cursor-pointer hover:text-white" onClick={() => handleSort('reality_gap')}>
                       <div className="flex items-center gap-2">Gap <ArrowUpDown size={10} className="opacity-30" /></div>
                    </th>
                    <th className="px-6 py-4 cursor-pointer hover:text-white" onClick={() => handleSort('lastUpdated')}>
                       <div className="flex items-center gap-2">Updated <ArrowUpDown size={10} className="opacity-30" /></div>
                    </th>
                    <th className="px-6 py-4 text-right">Action</th>
                  </tr>
                </thead>
                <tbody className="divide-y divide-white/5 text-slate-300">
                  {loading ? (
                    [...Array(5)].map((_, i) => (
                      <tr key={i} className="animate-pulse">
                        <td className="px-6 py-4"><div className="h-4 bg-white/5 rounded w-32" /></td>
                        <td className="px-6 py-4"></td>
                        <td className="px-6 py-4"></td>
                        <td className="px-6 py-4"></td>
                        <td className="px-6 py-4"></td>
                        <td className="px-6 py-4"></td>
                      </tr>
                    ))
                  ) : sortedData.length === 0 ? (
                      <tr>
                          <td colSpan={6} className="px-6 py-12 text-center text-slate-500">
                              {/* Silent error if no connection, just show text */}
                              No entities found. (Check connection or refresh database)
                          </td>
                      </tr>
                  ) : sortedData.map((row) => (
                    <tr key={row.id} className="hover:bg-white/5 transition-colors group">
                      <td className="px-6 py-4 font-medium text-slate-100 group-hover:text-white transition-colors">{row.companyName}</td>
                      <td className="px-6 py-4">
                        <div className="flex items-center gap-2">
                            <div className="w-16 h-1 bg-slate-700 rounded-full overflow-hidden">
                                <div className="h-full bg-cyan-400 rounded-full" style={{ width: `${row.intent_score}%` }}></div>
                            </div>
                            <span className="text-xs font-mono">{row.intent_score}</span>
                        </div>
                      </td>
                      <td className="px-6 py-4">
                        <div className="flex items-center gap-2">
                            <div className="w-16 h-1 bg-slate-700 rounded-full overflow-hidden">
                                <div className="h-full bg-emerald-400 rounded-full" style={{ width: `${row.reality_score}%` }}></div>
                            </div>
                            <span className="text-xs font-mono">{row.reality_score}</span>
                        </div>
                      </td>
                      <td className="px-6 py-4 font-mono text-xs">
                        <span className={`${row.reality_gap > 20 ? 'text-rose-400 font-bold' : row.reality_gap < -20 ? 'text-amber-400' : 'text-slate-400'}`}>
                          {row.reality_gap > 0 ? '+' : ''}{row.reality_gap}
                        </span>
                      </td>
                      <td className="px-6 py-4 text-xs font-mono text-slate-500">
                        {new Date(row.lastUpdated).toLocaleDateString()}
                      </td>
                      <td className="px-6 py-4 text-right">
                        <button 
                          onClick={() => handleCompanyClick(row)}
                          className="px-3 py-1.5 rounded-md hover:bg-cyan-500/10 hover:text-cyan-300 text-xs font-medium transition-all"
                        >
                          View Report
                        </button>
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </div>
        </section>
      </main>

      {isScannerOpen && (
        <div className="fixed inset-0 z-50 flex items-center justify-center p-4">
          <div className="absolute inset-0 bg-[#0f172a]/90 backdrop-blur-md" onClick={() => !isScanning && setIsScannerOpen(false)} />
          <div className="relative w-full max-w-2xl bg-[#0f172a] border border-white/10 rounded-2xl shadow-2xl overflow-hidden">
             <div className="absolute top-4 right-4 z-10">
               {!isScanning && (
                <button onClick={() => setIsScannerOpen(false)} className="text-slate-400 hover:text-white p-1 rounded-full">
                  <X size={20} />
                </button>
               )}
             </div>
             {isScanning ? (
               <div className="p-16 flex flex-col items-center justify-center space-y-8 text-center">
                 <div className="relative">
                    <div className="w-20 h-20 border-2 border-slate-700 border-t-cyan-500 rounded-full animate-spin"></div>
                    <div className="absolute inset-0 flex items-center justify-center">
                        <Activity className="text-cyan-400 animate-pulse" size={24} />
                    </div>
                 </div>
                 <div>
                   <h3 className="text-xl font-bold text-white tracking-tight">Diagnostic Engine Running</h3>
                   <div className="space-y-2 mt-4 text-left border-l-2 border-slate-700 pl-4 py-1">
                    <p className="text-xs text-cyan-400 font-mono animate-pulse">>> Parsing Narrative Vectors</p>
                    <p className="text-xs text-emerald-400 font-mono animate-pulse delay-75">
                        {systemStatus.google ? '>> Interrogating Global Grid (X-Ray API)' : '>> Interrogating Reality'}
                    </p>
                   </div>
                 </div>
               </div>
             ) : (
               <ScannerInput onScan={handleRunScan} />
             )}
          </div>
        </div>
      )}

      <CompanyModal 
        company={selectedCompany} 
        isOpen={isModalOpen} 
        onClose={() => setIsModalOpen(false)}
        onUpdate={handleCompanyUpdate}
      />
    </div>
  );
};

(window as any).App = App;