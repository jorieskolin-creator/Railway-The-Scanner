import React, { useState, useEffect } from 'react';
import { AnalysisResult, ScannerInputData } from './types';
import { generateMockDashboardData, runMockScanner } from './services/mockScannerService';
import { TruthMatrix } from './components/TruthMatrix';
import { CompanyModal } from './components/CompanyModal';
import { ScannerInput } from './components/ScannerInput';
import { Activity, Search, ArrowUpDown, Plus, Loader2, X, Sparkles, Shield } from 'lucide-react';

const App: React.FC = () => {
  const [data, setData] = useState<AnalysisResult[]>([]);
  const [loading, setLoading] = useState<boolean>(true);
  const [searchQuery, setSearchQuery] = useState<string>('');
  const [selectedCompany, setSelectedCompany] = useState<AnalysisResult | null>(null);
  const [isModalOpen, setIsModalOpen] = useState(false);
  const [isScannerOpen, setIsScannerOpen] = useState(false);
  const [isScanning, setIsScanning] = useState(false);
  const [sortConfig, setSortConfig] = useState<{ key: keyof AnalysisResult; direction: 'asc' | 'desc' } | null>({ key: 'reality_gap', direction: 'desc' });

  useEffect(() => {
    const fetchData = async () => {
      // Safely access environment variables
      const env = (import.meta as any).env || {};
      const blobUrl = env.VITE_BLOB_URL; 
      
      if (blobUrl) {
        try {
          const response = await fetch(blobUrl);
          if (response.ok) {
            const realData = await response.json();
            setData(realData);
            setLoading(false);
            return;
          }
        } catch (e) {
          console.error("Failed to fetch Blob data, falling back to mock", e);
        }
      }

      // Fallback
      console.warn("Using Mock Data");
      setTimeout(() => {
        const mockData = generateMockDashboardData();
        setData(mockData);
        setLoading(false);
      }, 1000);
    };

    fetchData();
  }, []);

  const handleRunScan = async (input: ScannerInputData) => {
    setIsScanning(true);
    
    // Safely access API URL with Fallback to Railway Production
    const env = (import.meta as any).env || {};
    const apiUrl = env.VITE_API_URL || "https://the-scanner.up.railway.app"; 

    try {
      let newResult: AnalysisResult;

      if (apiUrl) {
        const response = await fetch(`${apiUrl}/scan`, {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ ...input, saveToDatabase: true }) // New scans also save to DB
        });
        
        if (!response.ok) throw new Error("Scan failed");
        
        const json = await response.json();
        newResult = json.data;
      } else {
        console.warn("VITE_API_URL not set. Running simulation in browser.");
        await new Promise(r => setTimeout(r, 2000)); 
        newResult = await runMockScanner(input);
      }

      // Update local state with new result
      // If company exists, replace it. If not, add it.
      setData(prev => {
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
      alert("Error running scan. Check console.");
    } finally {
      setIsScanning(false);
    }
  };

  const handleCompanyUpdate = (updatedCompany: AnalysisResult) => {
    // This function is called when the Modal performs a "Refresh Forensic Scan"
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
    
    if (typeof aVal === 'string' && typeof bVal === 'string') {
        return sortConfig.direction === 'asc' ? aVal.localeCompare(bVal) : bVal.localeCompare(aVal);
    }
    if (typeof aVal === 'number' && typeof bVal === 'number') {
        return sortConfig.direction === 'asc' ? aVal - bVal : bVal - aVal;
    }
    return 0;
  });

  return (
    <div className="min-h-screen flex flex-col font-sans relative overflow-x-hidden text-slate-100">
      
      {/* 1. Aurora Borealis Background */}
      <div className="fixed inset-0 z-0 bg-[#1e1b4b] overflow-hidden">
        {/* Main Twilight Gradient Base (Lighter than black) */}
        <div className="absolute inset-0 bg-gradient-to-b from-[#2e1065] via-[#1e1b4b] to-[#0f172a] opacity-80"></div>
        
        {/* Top Left: Pink/Red Aurora */}
        <div className="absolute top-[-10%] left-[-10%] w-[50%] h-[50%] bg-rose-500/30 rounded-full blur-[100px] mix-blend-screen animate-pulse-glow"></div>
        
        {/* Top Right: Cyan/Blue Aurora */}
        <div className="absolute top-[-10%] right-[-10%] w-[60%] h-[60%] bg-cyan-500/30 rounded-full blur-[120px] mix-blend-screen animate-pulse-glow" style={{ animationDelay: '4s' }}></div>
        
        {/* Bottom Center: Violet/Purple Aurora */}
        <div className="absolute bottom-[-20%] left-[20%] w-[70%] h-[70%] bg-violet-600/30 rounded-full blur-[150px] mix-blend-screen" style={{ animationDelay: '2s' }}></div>
        
        {/* Noise overlay for texture */}
        <div className="absolute inset-0 opacity-[0.05] bg-[url('https://grainy-gradients.vercel.app/noise.svg')] pointer-events-none"></div>
      </div>

      {/* Header */}
      <header className="sticky top-0 z-40 w-full border-b border-white/10 bg-[#1e1b4b]/50 backdrop-blur-xl supports-[backdrop-filter]:bg-[#1e1b4b]/30">
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
              <div className="absolute inset-y-0 left-0 pl-3 flex items-center pointer-events-none text-indigo-300 group-focus-within:text-cyan-300 transition-colors">
                <Search size={14} />
              </div>
              <input
                type="text"
                placeholder="Search Finland Top 100..."
                value={searchQuery}
                onChange={(e) => setSearchQuery(e.target.value)}
                className="w-full pl-9 pr-4 py-1.5 bg-[#0f172a]/50 border border-indigo-500/30 rounded-lg text-sm text-slate-100 placeholder-indigo-300/50 focus:ring-2 focus:ring-cyan-500/20 focus:border-cyan-500/50 focus:bg-[#0f172a]/80 outline-none transition-all shadow-inner"
              />
            </div>
            <button 
              onClick={() => setIsScannerOpen(true)}
              className="flex items-center gap-2 px-4 py-2 bg-white/10 hover:bg-white/20 text-white font-semibold text-xs rounded-lg transition-all shadow-[0_0_15px_rgba(255,255,255,0.1)] hover:shadow-[0_0_20px_rgba(255,255,255,0.2)] whitespace-nowrap tracking-wide border border-white/10 backdrop-blur-md"
            >
              <Plus size={14} /> NEW DIAGNOSIS
            </button>
          </div>
        </div>
      </header>

      {/* Main Content */}
      <main className="flex-grow py-8 px-4 sm:px-6 lg:px-8 max-w-7xl mx-auto w-full space-y-8 z-10 relative">
        
        {/* Top Row: Visual Overview */}
        <section className="space-y-4">
          <div className="flex justify-between items-center">
            <h2 className="text-xl font-bold text-white flex items-center gap-2 tracking-tight">
              <Sparkles className="text-cyan-300 w-4 h-4" /> Market Landscape (Finland)
            </h2>
            <div className="flex gap-2">
                 {["Helsinki Stock Exchange", "Turnover > 500M"].map(tag => (
                   <div key={tag} className="px-2 py-1 rounded bg-[#0f172a]/50 border border-indigo-500/20 text-[9px] uppercase font-bold text-indigo-300 flex items-center gap-1 cursor-default hover:bg-[#0f172a]/80 transition-colors">
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
                {/* Note: Background darkened to 60% opacity to contrast against lighter background */}
                <TruthMatrix data={data} onSelectCompany={handleCompanyClick} />
             </div>
          )}
        </section>

        {/* Bottom Row: Data Table */}
        <section className="space-y-4">
          <div className="flex justify-between items-center">
            <h2 className="text-lg font-bold text-white tracking-tight">Forensic Index</h2>
            <span className="text-[10px] font-mono text-indigo-200 uppercase tracking-wider bg-[#0f172a]/50 px-2 py-1 rounded border border-indigo-500/20">{filteredData.length} Entities</span>
          </div>

          <div className="bg-[#0f172a]/70 border border-white/10 rounded-2xl overflow-hidden shadow-2xl backdrop-blur-xl">
            {/* Note: Background darkened to 70% opacity for text readability */}
            <div className="overflow-x-auto">
              <table className="w-full text-sm text-left">
                <thead className="text-[10px] text-indigo-200 uppercase bg-black/30 border-b border-white/5 tracking-wider font-semibold">
                  <tr>
                    <th className="px-6 py-4 cursor-pointer hover:text-white transition-colors" onClick={() => handleSort('companyName')}>
                      <div className="flex items-center gap-2">Entity <ArrowUpDown size={10} className="opacity-30" /></div>
                    </th>
                    <th className="px-6 py-4 cursor-pointer hover:text-white transition-colors" onClick={() => handleSort('intent_score')}>
                       <div className="flex items-center gap-2">Intent <ArrowUpDown size={10} className="opacity-30" /></div>
                    </th>
                    <th className="px-6 py-4 cursor-pointer hover:text-white transition-colors" onClick={() => handleSort('reality_score')}>
                       <div className="flex items-center gap-2">Reality <ArrowUpDown size={10} className="opacity-30" /></div>
                    </th>
                    <th className="px-6 py-4 cursor-pointer hover:text-white transition-colors" onClick={() => handleSort('reality_gap')}>
                       <div className="flex items-center gap-2">Gap <ArrowUpDown size={10} className="opacity-30" /></div>
                    </th>
                    <th className="px-6 py-4 cursor-pointer hover:text-white transition-colors" onClick={() => handleSort('lastUpdated')}>
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
                        <td className="px-6 py-4"><div className="h-4 bg-white/5 rounded w-12" /></td>
                        <td className="px-6 py-4"><div className="h-4 bg-white/5 rounded w-12" /></td>
                        <td className="px-6 py-4"><div className="h-4 bg-white/5 rounded w-12" /></td>
                        <td className="px-6 py-4"><div className="h-4 bg-white/5 rounded w-24" /></td>
                        <td className="px-6 py-4"></td>
                      </tr>
                    ))
                  ) : sortedData.map((row) => (
                    <tr key={row.id} className="hover:bg-white/5 transition-colors group">
                      <td className="px-6 py-4 font-medium text-slate-100 group-hover:text-white transition-colors shadow-sm">
                        {row.companyName}
                      </td>
                      <td className="px-6 py-4">
                        <div className="flex items-center gap-2">
                            <div className="w-16 h-1 bg-slate-700 rounded-full overflow-hidden">
                                <div className="h-full bg-cyan-400 rounded-full shadow-[0_0_8px_rgba(34,211,238,0.5)]" style={{ width: `${row.intent_score}%` }}></div>
                            </div>
                            <span className="text-xs font-mono">{row.intent_score}</span>
                        </div>
                      </td>
                      <td className="px-6 py-4">
                        <div className="flex items-center gap-2">
                            <div className="w-16 h-1 bg-slate-700 rounded-full overflow-hidden">
                                <div className="h-full bg-emerald-400 rounded-full shadow-[0_0_8px_rgba(52,211,153,0.5)]" style={{ width: `${row.reality_score}%` }}></div>
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
                          className="px-3 py-1.5 rounded-md bg-transparent hover:bg-cyan-500/10 hover:text-cyan-300 border border-transparent hover:border-cyan-500/30 text-xs font-medium transition-all"
                        >
                          View Report
                        </button>
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
            {!loading && sortedData.length === 0 && (
              <div className="p-12 text-center text-slate-500 border-t border-white/5">
                No entities found matching "{searchQuery}"
              </div>
            )}
          </div>
        </section>

      </main>

      {/* Scanner Modal */}
      {isScannerOpen && (
        <div className="fixed inset-0 z-50 flex items-center justify-center p-4">
          <div className="absolute inset-0 bg-[#0f172a]/90 backdrop-blur-md" onClick={() => !isScanning && setIsScannerOpen(false)} />
          <div className="relative w-full max-w-2xl bg-[#0f172a] border border-white/10 rounded-2xl shadow-2xl overflow-hidden animate-in fade-in zoom-in duration-200 ring-1 ring-white/10">
             <div className="absolute top-4 right-4 z-10">
               {!isScanning && (
                <button onClick={() => setIsScannerOpen(false)} className="text-slate-400 hover:text-white transition-colors hover:bg-white/5 p-1 rounded-full">
                  <X size={20} />
                </button>
               )}
             </div>
             
             {isScanning ? (
               <div className="p-16 flex flex-col items-center justify-center space-y-8 text-center bg-gradient-to-b from-[#0f172a] to-black">
                 <div className="relative">
                    <div className="w-20 h-20 border-2 border-slate-700 border-t-cyan-500 rounded-full animate-spin"></div>
                    <div className="absolute inset-0 flex items-center justify-center">
                        <Activity className="text-cyan-400 animate-pulse" size={24} />
                    </div>
                 </div>
                 <div>
                   <h3 className="text-xl font-bold text-white tracking-tight">Diagnostic Engine Running</h3>
                   <div className="space-y-2 mt-4 text-left border-l-2 border-slate-700 pl-4 py-1">
                    <p className="text-xs text-cyan-400 font-mono animate-pulse">>> Parsing Narrative Vectors (NLP)</p>
                    <p className="text-xs text-emerald-400 font-mono animate-pulse delay-75">>> Interrogating Reality (X-Ray)</p>
                    <p className="text-xs text-purple-400 font-mono animate-pulse delay-150">>> Synthesizing Summary (LLM)</p>
                   </div>
                 </div>
               </div>
             ) : (
               <ScannerInput onScan={handleRunScan} />
             )}
          </div>
        </div>
      )}

      {/* Pop-out Summary Component */}
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