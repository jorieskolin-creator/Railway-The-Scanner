
const { useState, useEffect } = (window as any).React;

const LucideRaw = (window as any).Lucide || {};
const Icons = new Proxy(LucideRaw, {
    get: (target, prop) => {
        if (prop in target) return target[prop];
        return () => null;
    }
});

const { Activity, Search, ArrowUpDown, Plus, Loader2, X, Sparkles, Shield, Database, Play, RefreshCw, CheckCircle2, AlertTriangle, Cloud, RotateCcw, Lock, HardDrive, Terminal } = Icons;

const TruthMatrix = (window as any).TruthMatrix;
const ScannerInput = (window as any).ScannerInput;
const CompanyModal = (window as any).CompanyModal;

const IS_ADMIN_MODE = true; 

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
  const [isSeeding, setIsSeeding] = useState(false);
  const [lastSynced, setLastSynced] = useState(null);
  const [sortConfig, setSortConfig] = useState({ key: 'reality_gap', direction: 'desc' });
  const [systemStatus, setSystemStatus] = useState({ online: false, storage: false, google: false, ai: false });

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

  // Fetch LIGHTWEIGHT Summary Data
  const fetchSummaryData = async () => {
    try {
        setLoading(true);
        setConnectionError(null);
        const response = await fetch(`/dashboard-data?t=${Date.now()}`);
        if (response.ok) {
            const json = await response.json();
            console.log("Loaded summary data:", json.length, "records");
            setData(json);
            setLastSynced(new Date().toLocaleTimeString('en-US', { hour12: false, hour: "2-digit", minute: "2-digit", second: "2-digit" }));
        } else {
            throw new Error("Failed to fetch summary data");
        }
    } catch (e) {
        console.error(e);
        setData([]); 
    } finally {
        setLoading(false);
    }
  };

  useEffect(() => {
    fetchSummaryData();
  }, []);

  // Fetch DETAILED Data for a specific company
  const handleCompanyClick = async (summaryItem) => {
    if (!summaryItem) return;
    
    setIsLoadingDetails(true);
    // Optimistic UI: Open modal with summary data first
    setSelectedCompany({ ...summaryItem, partial: true });
    setIsModalOpen(true);

    try {
        let fullData = null;
        
        // METHOD 1: Use the explicit URL if available (Best for Vercel Blob with suffixes)
        if (summaryItem.detailsUrl) {
            console.log("Fetching details via direct URL:", summaryItem.detailsUrl);
            const res = await fetch(`${summaryItem.detailsUrl}?t=${Date.now()}`);
            if (res.ok) {
                fullData = await res.json();
            }
        }
        
        // METHOD 2: Fallback to Backend Proxy
        if (!fullData) {
            console.log("Direct fetch failed, trying backend proxy...");
            const res = await fetch(`/company/${encodeURIComponent(summaryItem.companyName)}?t=${Date.now()}`);
            if (res.ok) {
                fullData = await res.json();
            }
        }

        if (fullData) {
            setSelectedCompany(fullData);
        } else {
            console.warn("Full detailed report not found.");
            setSelectedCompany(prev => ({ 
                ...prev, 
                partial: false, 
                summary_text: "Detailed forensic report unavailable. Displaying summary index data." 
            }));
        }
    } catch (e) {
        console.error("Error fetching details", e);
        setSelectedCompany(prev => ({ ...prev, partial: false }));
    } finally {
        setIsLoadingDetails(false);
    }
  };

  const handleRunScan = async (input) => {
    setIsScanning(true);
    try {
      const response = await fetch(`/scan`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ ...input, saveToDatabase: true })
      });
      
      if (!response.ok) throw new Error("Scan failed");
      const json = await response.json();
      const newResult = json.data;

      // Update Summary List Local State so the table updates immediately
      fetchSummaryData(); 
      
      setIsScannerOpen(false);
      setSelectedCompany(newResult); // The scan result IS the full data
      setIsModalOpen(true);
    } catch (error) {
      console.error(error);
      alert("Error running scan.");
    } finally {
      setIsScanning(false);
    }
  };

  const handleBatchSeed = async () => {
    if (!IS_ADMIN_MODE) return;
    if (!confirm("Start Batch Refresh? This will reset the Index and create 100+ individual files.")) return;
    
    setIsSeeding(true);
    try {
        const response = await fetch(`/admin/seed-finland-100`, { method: 'POST' });
        if (response.ok) {
            alert("✅ Batch Refresh Initiated. Check Railway Logs for progress.");
        }
    } catch (e) {
        alert("❌ Error starting batch seed");
    } finally {
        setIsSeeding(false);
    }
  };

  const handleSort = (key) => {
    let direction = 'desc';
    if (sortConfig && sortConfig.key === key && sortConfig.direction === 'desc') direction = 'asc';
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
            <div className="flex items-center gap-2 mr-2">
                 {/* SYSTEM STATUS INDICATOR */}
                 <div className={`w-2 h-2 rounded-full ${systemStatus.storage ? 'bg-emerald-400 shadow-[0_0_8px_rgba(52,211,153,0.6)]' : 'bg-rose-500'}`}></div>
                 <span className="text-[10px] font-mono uppercase text-slate-400 hidden sm:block">
                    {systemStatus.storage ? "System Online" : "Storage Disconnected"}
                 </span>
            </div>
            {IS_ADMIN_MODE && (
                <button onClick={handleBatchSeed} disabled={isSeeding} className="flex items-center gap-2 px-4 py-2 bg-indigo-600 hover:bg-indigo-500 text-white font-semibold text-xs rounded-lg transition-all">
                    {isSeeding ? <Loader2 size={14} className="animate-spin" /> : <Database size={14} />}
                    {isSeeding ? "REFRESHING..." : "REFRESH DATABASE"}
                </button>
            )}
            <div className="flex items-center gap-2">
                {lastSynced && <span className="text-[10px] text-cyan-300/70 font-mono hidden sm:block">{lastSynced}</span>}
                <button onClick={fetchSummaryData} className="p-2 bg-white/5 rounded-full hover:bg-white/10">
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
          {loading ? (
             <div className="h-[400px] w-full bg-[#0f172a]/60 rounded-2xl flex items-center justify-center">
                <Loader2 className="animate-spin text-cyan-400" size={32} />
             </div>
          ) : data.length === 0 ? (
             <div className="h-[400px] w-full bg-[#0f172a]/60 rounded-2xl flex flex-col items-center justify-center border border-white/5 text-slate-400 gap-4">
                <AlertTriangle size={48} className="text-amber-500/50" />
                <div className="text-center">
                    <h3 className="text-white font-bold text-lg">Database Connection Pending</h3>
                    <p className="text-sm max-w-md mt-2">No records found. The database might be empty, or the backend is unable to read from Vercel Blob Storage.</p>
                </div>
                {IS_ADMIN_MODE && (
                    <button onClick={handleBatchSeed} className="px-6 py-2 bg-indigo-600 hover:bg-indigo-500 text-white text-sm font-bold rounded-lg transition-all">
                        Initialize Batch Scan
                    </button>
                )}
             </div>
          ) : (
             <div className="bg-[#0f172a]/60 border border-white/10 rounded-2xl p-1 shadow-2xl backdrop-blur-xl">
                <TruthMatrix data={data} onSelectCompany={handleCompanyClick} />
             </div>
          )}
        </section>

        <section className="space-y-4">
          <div className="flex justify-between items-center">
            <h2 className="text-lg font-bold text-white">Forensic Index</h2>
          </div>
          <div className="bg-[#0f172a]/70 border border-white/10 rounded-2xl overflow-hidden min-h-[200px]">
              {data.length === 0 && !loading ? (
                  <div className="flex items-center justify-center h-48 text-slate-500 text-sm italic">
                      No Index Data Available.
                  </div>
              ) : (
                  <table className="w-full text-sm text-left">
                    <thead className="text-[10px] text-indigo-200 uppercase bg-black/30 border-b border-white/5">
                      <tr>
                        <th className="px-6 py-4 cursor-pointer" onClick={() => handleSort('companyName')}>Entity</th>
                        <th className="px-6 py-4 cursor-pointer" onClick={() => handleSort('intent_score')}>Intent</th>
                        <th className="px-6 py-4 cursor-pointer" onClick={() => handleSort('reality_score')}>Reality</th>
                        <th className="px-6 py-4 cursor-pointer" onClick={() => handleSort('reality_gap')}>Gap</th>
                        <th className="px-6 py-4">Action</th>
                      </tr>
                    </thead>
                    <tbody className="divide-y divide-white/5 text-slate-300">
                      {sortedData.map((row) => (
                        <tr key={row.id} className="hover:bg-white/5 transition-colors">
                          <td className="px-6 py-4 font-medium text-white">{row.companyName}</td>
                          <td className="px-6 py-4">{row.intent_score}</td>
                          <td className="px-6 py-4">{row.reality_score}</td>
                          <td className="px-6 py-4 font-mono text-xs">
                             <span className={row.reality_gap > 20 ? 'text-rose-400' : 'text-slate-400'}>{row.reality_gap}</span>
                          </td>
                          <td className="px-6 py-4">
                            <button onClick={() => handleCompanyClick(row)} className="text-cyan-400 hover:text-cyan-300 text-xs font-medium">View Report</button>
                          </td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
              )}
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

      <CompanyModal 
        company={selectedCompany} 
        isOpen={isModalOpen} 
        isLoading={isLoadingDetails}
        onClose={() => setIsModalOpen(false)}
        onUpdate={(updated) => {
            setSelectedCompany(updated);
            fetchSummaryData();
        }}
      />
    </div>
  );
};

(window as any).App = App;
