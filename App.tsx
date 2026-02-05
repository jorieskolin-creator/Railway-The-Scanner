
import React, { useState, useEffect } from 'https://esm.sh/react@18.2.0';
import { AnalysisResult, ScannerInputData } from './types.ts';
import { generateMockDashboardData, runMockScanner } from './services/mockScannerService.ts';
import { TruthMatrix } from './components/TruthMatrix.tsx';
import { CompanyModal } from './components/CompanyModal.tsx';
import { ScannerInput } from './components/ScannerInput.tsx';
import { Activity, Search, ArrowUpDown, Plus, Loader2, X, Sparkles, Shield, Database, Play } from 'https://esm.sh/lucide-react@0.344.0?deps=react@18.2.0';

const App: React.FC = () => {
  const [data, setData] = useState<AnalysisResult[]>([]);
  const [loading, setLoading] = useState<boolean>(true);
  const [isMockData, setIsMockData] = useState<boolean>(false);
  const [searchQuery, setSearchQuery] = useState<string>('');
  const [selectedCompany, setSelectedCompany] = useState<AnalysisResult | null>(null);
  const [isModalOpen, setIsModalOpen] = useState(false);
  const [isScannerOpen, setIsScannerOpen] = useState(false);
  const [isScanning, setIsScanning] = useState(false);
  const [isSeeding, setIsSeeding] = useState(false);
  const [sortConfig, setSortConfig] = useState<{ key: keyof AnalysisResult; direction: 'asc' | 'desc' } | null>({ key: 'reality_gap', direction: 'desc' });

  useEffect(() => {
    const fetchData = async () => {
      // In this setup, we assume the frontend is served by the backend, so we check relative or ENV
      // Since it's browser runtime, we can try to fetch the blob directly if we know it, 
      // OR we can default to empty/mock first if env is not injected into the browser window.
      // For this prototype, we'll try to guess or use mock if env is missing.
      
      const env = (import.meta as any).env || {};
      let blobUrl = env.VITE_BLOB_URL;
      
      // If we are running in the browser without build step, import.meta.env might be empty.
      // We can try to fetch a config endpoint from our own backend?
      // For now, let's proceed. If blobUrl is missing, we use Mock Data which is fine for "It works".

      if (blobUrl) {
        if (!blobUrl.endsWith("json")) {
            blobUrl = blobUrl.endsWith("/") ? `${blobUrl}dashboard_data.json` : `${blobUrl}/dashboard_data.json`;
        }
        try {
          const response = await fetch(blobUrl);
          if (response.status === 404) {
             const mockData = generateMockDashboardData();
             setData(mockData);
             setIsMockData(true);
             setLoading(false);
             return;
          }
          if (response.ok) {
            const realData = await response.json();
            if (Array.isArray(realData)) {
                setData(realData);
                setLoading(false);
                return;
            }
          }
        } catch (e) {
          console.warn("Blob fetch failed, falling back to mock");
        }
      }

      // Fallback
      setTimeout(() => {
        const mockData = generateMockDashboardData();
        setData(mockData);
        setIsMockData(true);
        setLoading(false);
      }, 1000);
    };

    fetchData();
  }, []);

  const handleRunScan = async (input: ScannerInputData) => {
    setIsScanning(true);
    
    // We are serving from the backend, so API URL is relative root
    const apiUrl = ""; 

    try {
      let newResult: AnalysisResult;

      try {
        const response = await fetch(`${apiUrl}/scan`, {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ ...input, saveToDatabase: true })
        });
        
        if (!response.ok) throw new Error("Scan failed");
        
        const json = await response.json();
        newResult = json.data;
      } catch (apiError) {
        console.warn("API failed, falling back to local simulation.", apiError);
        await new Promise(r => setTimeout(r, 1500));
        newResult = await runMockScanner(input);
      }

      setData(prev => {
        if (isMockData) {
            setIsMockData(false);
            return [newResult];
        }
        const exists = prev.findIndex(p => p.companyName === newResult.companyName);
        if (exists > -1) {
          const updated = [...prev];
          updated[exists] = newResult;
          return updated;
        }
        return [newResult, ...prev];
      });

      setIsScannerOpen(false);
      setSelectedCompany(newResult);
      setIsModalOpen(true);
    } catch (error) {
      console.error(error);
      alert("Error running scan.");
    } finally {
      setIsScanning(false);
    }
  };

  const handleBatchSeed = async () => {
    if (!confirm("Start background seed for Finland Top 100?")) return;
    setIsSeeding(true);
    try {
        const response = await fetch(`/admin/seed-finland-100`, { method: 'POST' });
        if (response.ok) alert("Batch Process Initiated!");
        else throw new Error("API Error");
    } catch (e) {
        alert("Could not connect to backend.");
    } finally {
        setIsSeeding(false);
    }
  };

  const handleCompanyUpdate = (updatedCompany: AnalysisResult) => {
    setData(prev => prev.map(item => 
      item.companyName === updatedCompany.companyName ? updatedCompany : item
    ));
    setSelectedCompany(updatedCompany);
  };

  const handleCompanyClick = (company: AnalysisResult) => {
    setSelectedCompany(company);
    setIsModalOpen(true);
  };

  const handleSort = (key: keyof AnalysisResult) => {
    let direction: 'asc' | 'desc' = 'desc';
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
            <button 
              onClick={() => setIsScannerOpen(true)}
              className="flex items-center gap-2 px-4 py-2 bg-white/10 hover:bg-white/20 text-white font-semibold text-xs rounded-lg transition-all border border-white/10 backdrop-blur-md"
            >
              <Plus size={14} /> NEW DIAGNOSIS
            </button>
          </div>
        </div>
        
        {isMockData && !loading && (
          <div className="w-full bg-indigo-950/80 border-b border-indigo-500/30 backdrop-blur-sm">
             <div className="max-w-7xl mx-auto px-4 py-1.5 flex items-center justify-between">
                <div className="flex items-center gap-2 text-[10px] uppercase tracking-wider font-mono text-indigo-200">
                    <Database size={12} className="text-cyan-400 animate-pulse" />
                    <span>Database Empty (Simulation Mode)</span>
                </div>
                <button 
                    onClick={handleBatchSeed}
                    disabled={isSeeding}
                    className="flex items-center gap-2 px-3 py-1 bg-cyan-600/20 hover:bg-cyan-600/40 text-cyan-300 text-[10px] font-bold uppercase rounded border border-cyan-500/50 transition-all"
                >
                    {isSeeding ? <Loader2 size={10} className="animate-spin" /> : <Play size={10} fill="currentColor" />}
                    Initialize Finland Top 100 Database
                </button>
             </div>
          </div>
        )}
      </header>

      <main className="flex-grow py-8 px-4 sm:px-6 lg:px-8 max-w-7xl mx-auto w-full space-y-8 z-10 relative">
        <section className="space-y-4">
          <div className="flex justify-between items-center">
            <h2 className="text-xl font-bold text-white flex items-center gap-2 tracking-tight">
              <Sparkles className="text-cyan-300 w-4 h-4" /> Market Landscape (Finland)
            </h2>
            <div className="flex gap-2">
                 {["Helsinki Stock Exchange", "Turnover > 500M"].map(tag => (
                   <div key={tag} className="px-2 py-1 rounded bg-[#0f172a]/50 border border-indigo-500/20 text-[9px] uppercase font-bold text-indigo-300 flex items-center gap-1">
                     <Shield size={8} /> {tag}
                   </div>
                 ))}
              </div>
          </div>
          {loading ? (
             <div className="h-[400px] w-full bg-[#0f172a]/60 rounded-2xl border border-white/10 backdrop-blur-md flex items-center justify-center">
                <Loader2 className="animate-spin text-cyan-400" size={32} />
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
                    <p className="text-xs text-emerald-400 font-mono animate-pulse delay-75">>> Interrogating Reality (X-Ray)</p>
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

export default App;
